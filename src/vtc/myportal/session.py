from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from vtc.errors import MissingSession
from vtc.myportal.constants import (
    APPLY_BUTTON,
    DOCUMENT_DOWNLOAD_URL,
    LOGOUT_MARKERS,
    MYPORTAL_HOME,
    MYPORTAL_URL,
    PARENT_LABELS,
    SEARCH_BUTTON,
    SECTION_LABELS,
    SECTION_PATHS,
    SELECT_BUTTON,
)
from vtc.myportal.parse import (
    looks_like_myportal_login,
    match_activity,
    match_module,
    parse_html_tables,
    parse_link_records,
    parse_timetable_grid,
    public_week,
    select_week_option,
    today_hk,
    weekday_en,
)
from vtc.paths import myportal_session_path
from vtc.secrets import sanitized_environ

TABLE_JS = """els => els.map(el => ({
    rows: Array.from(el.rows || []).map(r =>
        Array.from(r.cells || []).map(c => ({
            text: (c.innerText || '').trim(),
            colspan: c.colSpan,
            rowspan: c.rowSpan
        }))
    )
}))"""
OPTION_JS = "els => els.map(el => ({value: el.value, label: (el.innerText || '').trim()}))"
NAV_JS = "els => els.map(a => (a.innerText || a.textContent || '').trim()).filter(Boolean).slice(0, 80)"


class MyPortalSession:
    def __init__(self, *, headed: bool = False, timeout_ms: int = 30000) -> None:
        self.session_file = myportal_session_path()
        if not self.session_file.exists():
            raise MissingSession(
                "No MyPortal session. Run `vtc login myportal` in a local terminal."
            )
        self.headed = headed
        self.timeout_ms = timeout_ms
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self.page: Any = None

    def __enter__(self) -> MyPortalSession:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise MissingSession(
                "Playwright is not installed. Run: python -m playwright install chromium"
            ) from exc
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=not self.headed,
            env=sanitized_environ(),
        )
        self._context = self._browser.new_context(
            storage_state=str(self.session_file),
            ignore_https_errors=True,
            accept_downloads=True,
        )
        self.page = self._context.new_page()
        self._open_home()
        return self

    def __exit__(self, *args: object) -> None:
        if self._context is not None:
            self._context.close()
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        self.page = None

    def _open_home(self) -> None:
        assert self.page is not None
        self.page.goto(MYPORTAL_HOME, wait_until="domcontentloaded", timeout=self.timeout_ms)
        self._wait_quiet()
        if self._session_expired():
            self.page.goto(MYPORTAL_URL, wait_until="domcontentloaded", timeout=self.timeout_ms)
            self._wait_quiet()
        if self._session_expired():
            raise MissingSession(
                "MyPortal session expired. Run `vtc login myportal` in a local terminal."
            )

    def _wait_quiet(self) -> None:
        assert self.page is not None
        try:
            self.page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass

    def _session_expired(self) -> bool:
        assert self.page is not None
        html = self._all_html()
        if looks_like_myportal_login(html, self.page.url):
            body = self._body_text().lower()
            if any(marker in body for marker in LOGOUT_MARKERS):
                return False
            return True
        return False

    def _body_text(self) -> str:
        assert self.page is not None
        try:
            return self.page.locator("body").inner_text(timeout=4000)
        except Exception:
            return ""

    def _all_html(self) -> str:
        assert self.page is not None
        parts = [self.page.content()]
        for frame in self.page.frames:
            try:
                parts.append(frame.content())
            except Exception:
                continue
        return "\n".join(parts)

    def nav_labels(self) -> list[str]:
        assert self.page is not None
        try:
            labels = self.page.locator("a, button").evaluate_all(NAV_JS)
        except Exception:
            labels = []
        out: list[str] = []
        for label in labels:
            text = str(label).strip()
            if not text or "welcome" in text.lower():
                continue
            if text not in out:
                out.append(text)
        return out[:40]

    def status(self) -> dict[str, Any]:
        body = self._body_text().lower()
        labels = self.nav_labels()
        authenticated = any(marker in body for marker in LOGOUT_MARKERS) or any(
            "timetable" in item.lower() or "時間表" in item for item in labels
        )
        return {
            "authenticated": authenticated and not self._session_expired(),
            "final_url": self.page.url if self.page else None,
            "nav": labels,
        }

    def _click_pattern(self, patterns: tuple[str, ...], *, timeout_ms: int = 8000) -> bool:
        assert self.page is not None
        if not patterns:
            return False
        combined = re.compile("|".join(patterns), re.IGNORECASE)
        locators: list[Any] = [
            self.page.get_by_role("link", name=combined),
            self.page.get_by_role("button", name=combined),
            self.page.get_by_text(combined),
        ]
        for frame in self.page.frames:
            locators.extend(
                [
                    frame.get_by_role("link", name=combined),
                    frame.get_by_role("button", name=combined),
                ]
            )
        for locator in locators:
            try:
                target = locator.first
                if target.count() == 0:
                    continue
                target.click(timeout=timeout_ms)
                self._wait_quiet()
                return True
            except Exception:
                continue
        return False

    def open_section(self, section: str) -> dict[str, Any]:
        evidence: dict[str, Any] = {"section": section, "clicked": False, "path": None}
        for url in SECTION_PATHS.get(section, ()):
            try:
                assert self.page is not None
                self.page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                self._wait_quiet()
                if self._session_expired():
                    raise MissingSession(
                        "MyPortal session expired. Run `vtc login myportal` in a local terminal."
                    )
                if not looks_like_myportal_login(self._all_html(), self.page.url):
                    evidence["path"] = url
                    evidence["final_url"] = self.page.url
                    return evidence
            except MissingSession:
                raise
            except Exception:
                continue
        self._click_pattern(PARENT_LABELS.get(section, ()))
        evidence["clicked"] = self._click_pattern(SECTION_LABELS.get(section, ()))
        evidence["final_url"] = self.page.url if self.page else None
        if section == "documents" and not evidence["clicked"]:
            try:
                assert self.page is not None
                self.page.goto(
                    DOCUMENT_DOWNLOAD_URL,
                    wait_until="domcontentloaded",
                    timeout=self.timeout_ms,
                )
                self._wait_quiet()
                evidence["path"] = DOCUMENT_DOWNLOAD_URL
                evidence["final_url"] = self.page.url
            except Exception:
                pass
        return evidence

    def _first_locator(self, selector: str) -> Any | None:
        assert self.page is not None
        for target in [self.page, *list(self.page.frames)]:
            try:
                locator = target.locator(selector)
                if locator.count() > 0:
                    return locator
            except Exception:
                continue
        return None

    def _page_records(self) -> list[dict[str, Any]]:
        assert self.page is not None
        html = self._all_html()
        records = parse_html_tables(html, base_url=self.page.url)
        if records:
            return records
        return parse_link_records(html, base_url=self.page.url)

    def timetable(self, *, today_only: bool = False) -> dict[str, Any]:
        evidence = self.open_section("timetable")
        assert self.page is not None
        week_select = self._first_locator(
            "select[name='j_id_e:beanDateFrom'], select[name*='DateFrom'], select[name*='beanDate']"
        )
        if week_select is None:
            try:
                clicked_action = bool(
                    self.page.evaluate(
                        """() => {
                            if (typeof clickBtn !== 'function') return false;
                            clickBtn('getTimetableAction');
                            return true;
                        }"""
                    )
                )
            except Exception:
                clicked_action = False
            if clicked_action:
                self._wait_quiet()
                self.page.wait_for_timeout(1500)
            week_select = self._first_locator(
                "select[name='j_id_e:beanDateFrom'], select[name*='DateFrom'], select[name*='beanDate']"
            )
        if week_select is None:
            return {
                "found": False,
                "week": None,
                "weekdays": [],
                "courses": [],
                "today_courses": [],
                "remarks": [],
                "evidence": evidence,
                "nav": self.nav_labels(),
                "details": "Opened MyPortal but did not find the timetable week selector.",
            }

        options = week_select.locator("option").evaluate_all(OPTION_JS)
        from_opt, to_opt = select_week_option(options)
        if not from_opt or not to_opt:
            return {
                "found": False,
                "week": None,
                "weekdays": [],
                "courses": [],
                "today_courses": [],
                "remarks": [],
                "evidence": evidence,
                "nav": self.nav_labels(),
                "details": "Timetable week selector had no readable week options.",
            }

        week_select.select_option(from_opt["value"])
        to_select = self._first_locator("select[name='j_id_e:beanDateTo'], select[name*='DateTo']")
        if to_select is not None:
            to_select.select_option(datetime.fromisoformat(to_opt["end"]).strftime("%d-%b-%Y"))
        search = self._first_locator("input[name='j_id_e:search'], input[type='submit'][value*='Search']")
        if search is not None:
            search.first.click()
        else:
            self._click_pattern((SEARCH_BUTTON,))
        self._wait_quiet()
        self.page.wait_for_timeout(1500)

        tables: list[dict[str, Any]] = []
        print_area = self._first_locator("#printContent table")
        if print_area is not None:
            tables = print_area.evaluate_all(TABLE_JS)
        if not tables:
            any_tables = self._first_locator("table")
            if any_tables is not None:
                tables = any_tables.evaluate_all(TABLE_JS)

        grid_rows = tables[0]["rows"] if tables else []
        remarks_rows = tables[1]["rows"] if len(tables) > 1 else []
        week = public_week(from_opt) or {}
        entries, weekdays = parse_timetable_grid(grid_rows, str(week.get("label") or ""))
        today_label = weekday_en(today_hk())
        today_courses = [item for item in entries if item.get("weekday") == today_label]
        remarks: list[str] = []
        for row in remarks_rows[1:]:
            if row and row[0].get("text"):
                remarks.extend(line.strip() for line in str(row[0]["text"]).splitlines() if line.strip())

        courses = today_courses if today_only else entries
        evidence["search_clicked"] = True
        evidence["dom_table_count"] = len(tables)
        return {
            "found": True,
            "week": week,
            "weekdays": weekdays,
            "courses": courses,
            "today_weekday": today_label,
            "today_courses": today_courses,
            "remarks": remarks,
            "evidence": evidence,
            "nav": self.nav_labels(),
            "details": None,
        }

    def activities(self) -> dict[str, Any]:
        evidence = self.open_section("activities")
        self._click_pattern((r"Activity Enrolment", r"活動報名", r"活动报名"))
        records = self._page_records()
        return {
            "found": bool(records) or evidence.get("clicked") or bool(evidence.get("path")),
            "activities": records,
            "evidence": evidence,
            "nav": self.nav_labels(),
            "final_url": self.page.url if self.page else None,
        }

    def modules(self) -> dict[str, Any]:
        evidence = self.open_section("modules")
        records = self._page_records()
        return {
            "found": bool(records) or evidence.get("clicked") or bool(evidence.get("path")),
            "modules": records,
            "evidence": evidence,
            "nav": self.nav_labels(),
            "final_url": self.page.url if self.page else None,
        }

    def documents(self, kind: str) -> dict[str, Any]:
        evidence = self.open_section("documents")
        records = self._page_records()
        if not filter_documents(records, kind):
            records = records + parse_link_records(
                self._all_html(),
                base_url=self.page.url if self.page else "",
            )
        matched = filter_documents(records, kind)
        return {
            "found": evidence.get("clicked")
            or bool(evidence.get("path"))
            or "swsdownload" in ((self.page.url if self.page else "") or ""),
            "documents": matched,
            "all_count": len(records),
            "evidence": evidence,
            "nav": self.nav_labels(),
            "final_url": self.page.url if self.page else None,
        }

    def download_documents(self, kind: str, output: Path) -> dict[str, Any]:
        result = self.documents(kind)
        output.mkdir(parents=True, exist_ok=True)
        downloaded: list[dict[str, Any]] = []
        for item in result["documents"]:
            href = item.get("url")
            saved = dict(item)
            if not href or not self.page:
                saved["path"] = None
                downloaded.append(saved)
                continue
            try:
                with self.page.expect_download(timeout=15000) as pending:
                    self.page.evaluate(
                        """(url) => {
                            const link = [...document.querySelectorAll('a')].find(a => a.href === url);
                            if (link) { link.click(); return true; }
                            window.location.href = url;
                            return false;
                        }""",
                        href,
                    )
                download = pending.value
                filename = download.suggested_filename or f"{item.get('id') or kind}.pdf"
                dest = output / filename
                download.save_as(str(dest))
                saved["path"] = str(dest)
            except Exception:
                saved["path"] = None
            downloaded.append(saved)
        result["documents"] = downloaded
        return result

    def _click_row_action(self, record: dict[str, Any], button_re: str) -> bool:
        assert self.page is not None
        title = str(record.get("title") or record.get("code") or "")
        combined = re.compile(button_re, re.IGNORECASE)
        if title:
            row = self.page.get_by_role("row", name=re.compile(re.escape(title)[:40], re.IGNORECASE))
            try:
                if row.count() > 0:
                    row.first.get_by_role("button", name=combined).click(timeout=5000)
                    self._wait_quiet()
                    return True
            except Exception:
                try:
                    row.first.get_by_role("link", name=combined).click(timeout=5000)
                    self._wait_quiet()
                    return True
                except Exception:
                    pass
        return self._click_pattern(tuple(part for part in button_re.split("|") if part))

    def apply_activity(self, activity_id: str) -> dict[str, Any]:
        listed = self.activities()
        record = match_activity(listed.get("activities") or [], activity_id)
        if not record:
            return {
                "applied": False,
                "matched": None,
                "details": "No matching activity was read. That is unverified, not proof the activity is missing.",
                **listed,
            }
        clicked = self._click_row_action(record, APPLY_BUTTON)
        return {
            "applied": clicked,
            "matched": record,
            "details": None if clicked else "Found the activity but did not click a registration control.",
            **listed,
        }

    def select_module(self, code: str) -> dict[str, Any]:
        listed = self.modules()
        record = match_module(listed.get("modules") or [], code)
        if not record:
            return {
                "selected": False,
                "matched": None,
                "details": "No matching module was read. That is unverified, not proof the module is missing.",
                **listed,
            }
        clicked = self._click_row_action(record, SELECT_BUTTON)
        return {
            "selected": clicked,
            "matched": record,
            "details": None if clicked else "Found the module but did not click a selection control.",
            **listed,
        }


def with_session(fn: Callable[[MyPortalSession], dict[str, Any]]) -> dict[str, Any]:
    with MyPortalSession() as session:
        return fn(session)
