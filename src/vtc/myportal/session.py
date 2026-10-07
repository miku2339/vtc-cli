from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from vtc.errors import MissingSession
from vtc.myportal.constants import (
    APPLY_BUTTON,
    LOGOUT_MARKERS,
    MYPORTAL_HOME,
    MYPORTAL_URL,
    PARENT_LABELS,
    SEARCH_BUTTON,
    SECTION_LABELS,
    SECTION_PATHS,
    SECTION_URL_MARKERS,
    SELECT_BUTTON,
)
from vtc.myportal.parse import (
    clean_text,
    filter_documents,
    item_id,
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
DOCUMENT_CARD_SELECTORS = {
    "transcript": "#transcript button.downloadBtn",
    "tuition": "#paymentAdvice button.downloadBtn",
}
DOCUMENT_FILE_SELECTOR = "#download_div.showDialog a.fileDownloadBtn.modal_box_btn.primary"


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
        self._portal_page: Any = None

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
            accept_downloads=True,
        )
        self.page = self._context.new_page()
        self._portal_page = self.page
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
        portal_page = self._portal_page
        if portal_page is not None:
            try:
                if not portal_page.is_closed():
                    self.page = portal_page
            except Exception:
                self.page = portal_page
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
        evidence: dict[str, Any] = {
            "section": section,
            "clicked": False,
            "verified": False,
            "path": None,
        }
        href = self._click_exact_section_link(section)
        if href:
            evidence.update(clicked=True, path=href, final_url=self.page.url)
            evidence["verified"] = self._section_page_loaded(section)
            return evidence
        self._click_pattern(PARENT_LABELS.get(section, ()))
        href = self._click_exact_section_link(section)
        if href:
            evidence.update(clicked=True, path=href, final_url=self.page.url)
            evidence["verified"] = self._section_page_loaded(section)
            return evidence
        for url in SECTION_PATHS.get(section, ()):
            try:
                assert self.page is not None
                response = self.page.goto(
                    url, wait_until="domcontentloaded", timeout=self.timeout_ms
                )
                self._wait_quiet()
                if self._session_expired():
                    raise MissingSession(
                        "MyPortal session expired. Run `vtc login myportal` in a local terminal."
                    )
                if self._section_route_loaded(section, url, response):
                    evidence["path"] = url
                    evidence["verified"] = True
                    evidence["final_url"] = self.page.url
                    return evidence
            except MissingSession:
                raise
            except Exception:
                continue
        evidence["final_url"] = self.page.url if self.page else None
        return evidence

    def _click_exact_section_link(self, section: str) -> str | None:
        assert self.page is not None
        patterns = SECTION_LABELS.get(section, ())
        if not patterns:
            return None
        label_re = re.compile(f"(?:{'|'.join(patterns)})", re.IGNORECASE)
        targets = [self.page]
        main_frame = getattr(self.page, "main_frame", None)
        targets.extend(
            frame
            for frame in self.page.frames
            if main_frame is None or frame is not main_frame
        )
        candidates: list[tuple[bool, str, Any]] = []
        for target in targets:
            try:
                links = target.locator("a[href]")
                base_url = str(getattr(target, "url", "") or self.page.url)
                for index in range(links.count()):
                    link = links.nth(index)
                    if not label_re.fullmatch(clean_text(link.inner_text())):
                        continue
                    raw_href = link.get_attribute("href")
                    if not raw_href:
                        continue
                    href = urljoin(base_url, raw_href)
                    if self._section_target_matches(section, href):
                        try:
                            visible = bool(link.is_visible())
                        except Exception:
                            visible = True
                        candidates.append((visible, href, link))
            except Exception:
                continue
        candidates.sort(key=lambda candidate: candidate[0], reverse=True)
        for visible, href, link in candidates:
            if not visible:
                continue
            try:
                if section == "documents":
                    with self.page.expect_popup(timeout=8000) as pending:
                        link.click(timeout=8000)
                    popup = pending.value
                    try:
                        popup.wait_for_load_state(
                            "domcontentloaded", timeout=self.timeout_ms
                        )
                    except Exception:
                        pass
                    if self._section_target_matches(section, popup.url):
                        self.page = popup
                else:
                    link.click(timeout=8000)
                self._wait_quiet()
                return href
            except Exception:
                continue
        return None

    def _section_target_matches(self, section: str, url: str) -> bool:
        parsed = urlsplit(url)
        if parsed.scheme.lower() != "https":
            return False
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        if section == "documents":
            if host == "swsdownload.vtc.edu.hk":
                return path.startswith("/swsdownload/")
            if host != "myportal.vtc.edu.hk":
                return False
        elif host != "myportal.vtc.edu.hk":
            return False
        lower_url = f"{host}{path}"
        return any(
            marker in lower_url for marker in SECTION_URL_MARKERS.get(section, ())
        )

    def _section_page_loaded(self, section: str) -> bool:
        assert self.page is not None
        if not self._section_target_matches(section, self.page.url):
            return False
        html = self._all_html()
        if looks_like_myportal_login(html, self.page.url):
            return False
        text = clean_text(html).lower()
        return not any(
            marker in text
            for marker in ("404 not found", "page not found", "server error")
        )

    def _section_route_loaded(
        self, section: str, requested_url: str, response: Any
    ) -> bool:
        assert self.page is not None
        status = getattr(response, "status", None)
        if not isinstance(status, int) or status >= 400:
            return False
        requested = urlsplit(requested_url)
        final = urlsplit(self.page.url)
        requested_path = requested.path.rstrip("/")
        final_path = final.path.rstrip("/")
        if (requested.scheme, requested.netloc) != (final.scheme, final.netloc):
            return False
        if requested_path and not final_path.startswith(requested_path):
            return False
        return self._section_page_loaded(section)

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
        targets = [self.page]
        main_frame = getattr(self.page, "main_frame", None)
        targets.extend(
            frame
            for frame in self.page.frames
            if main_frame is None or frame is not main_frame
        )
        records: list[dict[str, Any]] = []
        seen: set[tuple[str, ...]] = set()
        for target in targets:
            try:
                html = target.content()
            except Exception:
                continue
            base_url = str(getattr(target, "url", "") or self.page.url)
            parsed = parse_html_tables(html, base_url=base_url)
            parsed.extend(parse_link_records(html, base_url=base_url))
            for record in parsed:
                url = str(record.get("url") or "")
                key = ("url", url) if url else (
                    "record",
                    str(record.get("id") or ""),
                    str(record.get("code") or ""),
                    str(record.get("title") or ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                records.append(record)
        return records

    def timetable(self, *, today_only: bool = False) -> dict[str, Any]:
        evidence = self.open_section("timetable")
        assert self.page is not None
        week_select = self._first_locator(
            "select[name='j_id_e:beanDateFrom'], select[name$='beanDateFrom'], select[name*='DateFrom']"
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
                "select[name='j_id_e:beanDateFrom'], select[name$='beanDateFrom'], select[name*='DateFrom']"
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
        search_clicked = False
        search = self._first_locator("input[name='j_id_e:search'], input[type='submit'][value*='Search']")
        if search is not None:
            try:
                search.first.click()
                search_clicked = True
            except Exception:
                search_clicked = False
        else:
            search_clicked = self._click_pattern((SEARCH_BUTTON,))
        evidence["search_clicked"] = search_clicked
        if not search_clicked:
            return {
                "found": False,
                "week": public_week(from_opt),
                "weekdays": [],
                "courses": [],
                "today_courses": [],
                "remarks": [],
                "evidence": evidence,
                "nav": self.nav_labels(),
                "details": "Opened the timetable but did not click its Search control; results are unverified.",
            }
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
        if not weekdays:
            evidence["dom_table_count"] = len(tables)
            return {
                "found": False,
                "week": week,
                "weekdays": [],
                "courses": [],
                "today_courses": [],
                "remarks": [],
                "evidence": evidence,
                "nav": self.nav_labels(),
                "details": "Search completed but no readable timetable grid was found; results are unverified.",
            }
        today_label = weekday_en(today_hk())
        today_courses = [item for item in entries if item.get("weekday") == today_label]
        remarks: list[str] = []
        for row in remarks_rows[1:]:
            if row and row[0].get("text"):
                remarks.extend(line.strip() for line in str(row[0]["text"]).splitlines() if line.strip())

        courses = today_courses if today_only else entries
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
        if self.page and self._section_target_matches("activities", self.page.url):
            self._click_pattern(
                (r"^Activity Enrolment$", r"^活動報名$", r"^活动报名$")
            )
        records = self._section_records("activities")
        return {
            "found": bool(records),
            "activities": records,
            "evidence": evidence,
            "nav": self.nav_labels(),
            "final_url": self.page.url if self.page else None,
        }

    def modules(self) -> dict[str, Any]:
        evidence = self.open_section("modules")
        records = self._section_records("modules")
        return {
            "found": bool(records),
            "modules": records,
            "evidence": evidence,
            "nav": self.nav_labels(),
            "final_url": self.page.url if self.page else None,
        }

    def _section_records(self, section: str) -> list[dict[str, Any]]:
        if not self.page or not self._section_target_matches(section, self.page.url):
            return []
        records: list[dict[str, Any]] = []
        for record in self._page_records():
            if not record.get("cells") or record.get("kind"):
                continue
            title = clean_text(str(record.get("title") or ""))
            url = str(record.get("url") or "")
            blob = f"{title} {url}".lower()
            if any(
                marker in blob
                for marker in ("guide", ".pdf", "document download", "eapplication")
            ):
                continue
            if section == "activities" and title:
                records.append(record)
            elif section == "modules" and record.get("code"):
                records.append(record)
        return records

    def documents(self, kind: str) -> dict[str, Any]:
        evidence = self.open_section("documents")
        current = urlsplit(self.page.url if self.page else "")
        verified_download_page = (
            evidence.get("verified") is True
            and current.scheme.lower() == "https"
            and (current.hostname or "").lower() == "swsdownload.vtc.edu.hk"
            and current.path.rstrip("/").lower() == "/swsdownload"
        )
        records: list[dict[str, Any]] = []
        if verified_download_page:
            records.extend(self._document_modal_records(kind))
            legacy = [
                record
                for record in self._page_records()
                if urlsplit(str(record.get("url") or "")).path.lower().endswith(".pdf")
            ]
            known = {str(record.get("id") or "") for record in records}
            records.extend(
                record
                for record in filter_documents(legacy, kind)
                if str(record.get("id") or "") not in known
            )
        matched = filter_documents(records, kind)
        return {
            "found": bool(matched),
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
        reserved: set[Path] = set()
        for item in result["documents"]:
            href = item.get("url")
            saved = dict(item)
            if not self.page:
                saved["path"] = None
                downloaded.append(saved)
                continue
            if not href:
                control = self._exact_modal_download_control(
                    str(item.get("title") or "")
                )
                if control is None:
                    saved["path"] = None
                    downloaded.append(saved)
                    continue
            else:
                control = self._exact_download_anchor(str(href))
            try:
                if control is None:
                    raise LookupError("Document link was not found in its owning frame.")
                with self.page.expect_download(timeout=15000) as pending:
                    control.click()
                download = pending.value
                filename = download.suggested_filename or f"{item.get('id') or kind}.pdf"
                dest = self._download_destination(output, filename, item, reserved)
                reserved.add(dest)
                download.save_as(str(dest))
                saved["path"] = str(dest)
            except Exception:
                saved["path"] = None
            downloaded.append(saved)
        result["documents"] = downloaded
        return result

    def _document_modal_records(self, kind: str) -> list[dict[str, Any]]:
        assert self.page is not None
        card_selector = DOCUMENT_CARD_SELECTORS.get(kind)
        if not card_selector:
            return []
        cards: list[Any] = []
        targets = [self.page]
        main_frame = getattr(self.page, "main_frame", None)
        targets.extend(
            frame
            for frame in self.page.frames
            if main_frame is None or frame is not main_frame
        )
        for target in targets:
            try:
                locator = target.locator(card_selector)
                for index in range(locator.count()):
                    card = locator.nth(index)
                    if card.is_visible():
                        cards.append(card)
            except Exception:
                continue
        if len(cards) != 1:
            return []
        try:
            cards[0].click(timeout=8000)
        except Exception:
            return []

        records: list[dict[str, Any]] = []
        seen_titles: set[str] = set()
        for target in targets:
            try:
                files = target.locator(DOCUMENT_FILE_SELECTOR)
                files.first.wait_for(state="visible", timeout=self.timeout_ms)
                for index in range(files.count()):
                    control = files.nth(index)
                    if not control.is_visible():
                        continue
                    title = clean_text(
                        control.get_attribute("title") or control.inner_text()
                    )
                    if not title or title in seen_titles:
                        continue
                    seen_titles.add(title)
                    records.append(
                        {
                            "id": item_id(kind, title),
                            "title": title,
                            "kind": kind,
                        }
                    )
            except Exception:
                continue
        return records

    def _exact_modal_download_control(self, title: str) -> Any | None:
        assert self.page is not None
        wanted = clean_text(title)
        if not wanted:
            return None
        targets = [self.page]
        main_frame = getattr(self.page, "main_frame", None)
        targets.extend(
            frame
            for frame in self.page.frames
            if main_frame is None or frame is not main_frame
        )
        matches: list[Any] = []
        for target in targets:
            try:
                controls = target.locator(DOCUMENT_FILE_SELECTOR)
                for index in range(controls.count()):
                    control = controls.nth(index)
                    control_title = clean_text(
                        control.get_attribute("title") or control.inner_text()
                    )
                    if control.is_visible() and control_title == wanted:
                        matches.append(control)
            except Exception:
                continue
        return matches[0] if len(matches) == 1 else None

    def _exact_download_anchor(self, href: str) -> Any | None:
        assert self.page is not None
        targets = [self.page]
        main_frame = getattr(self.page, "main_frame", None)
        targets.extend(
            frame
            for frame in self.page.frames
            if main_frame is None or frame is not main_frame
        )
        matches: list[Any] = []
        for target in targets:
            try:
                anchors = target.locator("a[href]")
                base_url = str(getattr(target, "url", "") or self.page.url)
                for index in range(anchors.count()):
                    anchor = anchors.nth(index)
                    raw_href = anchor.get_attribute("href")
                    if raw_href and urljoin(base_url, raw_href) == href:
                        matches.append(anchor)
            except Exception:
                continue
        return matches[0] if len(matches) == 1 else None

    def _download_destination(
        self,
        output: Path,
        suggested_filename: str,
        item: dict[str, Any],
        reserved: set[Path],
    ) -> Path:
        filename = Path(suggested_filename).name or "document.pdf"
        candidate = output / filename
        if candidate not in reserved and not candidate.exists():
            return candidate
        suffix_id = re.sub(r"[^a-z0-9-]+", "-", str(item.get("id") or "").lower()).strip("-")
        if not suffix_id:
            suffix_id = item_id(
                str(item.get("kind") or "document"),
                str(item.get("title") or ""),
                str(item.get("url") or ""),
            )
        stem = Path(filename).stem
        extension = Path(filename).suffix
        candidate = output / f"{stem}-{suffix_id}{extension}"
        counter = 2
        while candidate in reserved or candidate.exists():
            candidate = output / f"{stem}-{suffix_id}-{counter}{extension}"
            counter += 1
        return candidate

    def _click_row_action(self, record: dict[str, Any], button_re: str) -> bool:
        assert self.page is not None
        identifiers = {
            clean_text(str(record.get(key) or "")).casefold()
            for key in ("title", "code")
            if record.get(key)
        }
        if not identifiers:
            return False
        combined = re.compile(button_re, re.IGNORECASE)
        targets = [self.page]
        main_frame = getattr(self.page, "main_frame", None)
        targets.extend(
            frame
            for frame in self.page.frames
            if main_frame is None or frame is not main_frame
        )
        matched_rows: list[Any] = []
        for target in targets:
            try:
                rows = target.locator("tr")
                for index in range(rows.count()):
                    row = rows.nth(index)
                    cells = {
                        clean_text(text).casefold()
                        for text in row.locator("th, td").all_inner_texts()
                    }
                    if identifiers & cells:
                        matched_rows.append(row)
            except Exception:
                continue
        if len(matched_rows) != 1:
            return False
        controls: list[Any] = []
        for role in ("button", "link"):
            try:
                locator = matched_rows[0].get_by_role(role, name=combined)
                controls.extend(locator.nth(index) for index in range(locator.count()))
            except Exception:
                continue
        if len(controls) != 1:
            return False
        try:
            controls[0].click(timeout=5000)
            self._wait_quiet()
            return True
        except Exception:
            return False

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
            "applied": False,
            "action_clicked": clicked,
            "matched": record,
            "details": (
                "Registration control was clicked, but no reliable success marker was read; the result is unverified."
                if clicked
                else "Found the activity but did not click a registration control."
            ),
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
            "selected": False,
            "action_clicked": clicked,
            "matched": record,
            "details": (
                "Selection control was clicked, but no reliable success marker was read; the result is unverified."
                if clicked
                else "Found the module but did not click a selection control."
            ),
            **listed,
        }


def with_session(fn: Callable[[MyPortalSession], dict[str, Any]]) -> dict[str, Any]:
    with MyPortalSession() as session:
        return fn(session)
