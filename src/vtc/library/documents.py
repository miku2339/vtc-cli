from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from vtc.library.constants import PAPERS_URL, SYLLABUSES_URL
from vtc.paths import library_session_path
from vtc.secrets import sanitized_environ


SOURCE = "library_html"
HOST = "vtclib23.vtc.edu.hk"
USER_AGENT = "vtc-cli library reader"

_SERVICE_CONFIG = {
    "papers": {
        "base_url": PAPERS_URL,
        "search_url": f"{PAPERS_URL.rstrip('/')}/search",
        "heading": "Search Exams",
        "results_heading": "Exams",
        "results_path": "/exam/search/list",
    },
    "syllabuses": {
        "base_url": SYLLABUSES_URL,
        "search_url": f"{SYLLABUSES_URL.rstrip('/')}/search",
        "heading": "Search Syllabi",
        "results_heading": "Syllabi",
        "results_path": "/syllabus/search/list",
    },
}

_PAPER_HEADERS = (
    "Full Text",
    "Academic Year",
    "Session",
    "Semester / Level / Year",
    "Department",
    "Course",
    "Subject Code",
    "Subject Title",
    "Remarks",
)
_PAPER_FIELDS = (
    "full_text",
    "academic_year",
    "session",
    "semester_level_year",
    "department",
    "course",
    "subject_code",
    "subject_title",
    "remarks",
)
_SYLLABUS_HEADERS = (
    "Full Text",
    "Issuing Year",
    "Course Code",
    "Course Title",
    "Subject Code",
    "Subject Title",
    "Discipline",
    "Department",
    "Remarks",
)
_SYLLABUS_FIELDS = (
    "full_text",
    "issuing_year",
    "course_code",
    "course_title",
    "subject_code",
    "subject_title",
    "discipline",
    "department",
    "remarks",
)
_RESULT_SCHEMAS = {
    "papers": (_PAPER_HEADERS, _PAPER_FIELDS, "No Exams"),
    "syllabuses": (_SYLLABUS_HEADERS, _SYLLABUS_FIELDS, "No Syllabi"),
}
_PER_PAGE_OPTIONS = (10, 20, 50, 100)
_FILTER_FIELDS = {
    "papers": (
        "discipline_uuid",
        "department_uuid",
        "course_code",
        "year",
        "session",
        "class",
    ),
    "syllabuses": (
        "discipline_uuid",
        "department_uuid",
        "course_code",
        "year",
    ),
}
_DYNAMIC_FILTERS = frozenset({"department_uuid", "course_code"})
_PAGINATION_RE = re.compile(
    r"Showing\s+([\d,]+)\s+to\s+([\d,]+)\s+of\s+([\d,]+)\s+results",
    re.IGNORECASE,
)
_RECORD_KEY_RE = re.compile(r"\.table\.records\.(\d+)$")
_FILE_ACTION_RE = re.compile(
    r"^callTableColumnAction\('file_path',\s*'(\d+)'\)$"
)


def verified_database_page(page: Any, service: str) -> bool:
    config = _SERVICE_CONFIG.get(service)
    if config is None:
        return False
    try:
        parsed = urlsplit(page.url)
        if parsed.scheme != "https" or parsed.hostname != HOST:
            return False
        expected_prefix = f"/{'exam' if service == 'papers' else 'syllabus'}/search"
        if not parsed.path.rstrip("/").startswith(expected_prefix):
            return False

        soup = BeautifulSoup(page.content(), "html.parser")
        title = _text(soup.title)
        heading = _text(soup.select_one("main h1"))
        if title.casefold().startswith("login -") or heading == "Sign in":
            return False
        if soup.select_one("input[type='password'], input[name='AuthState']") is not None:
            return False
        if soup.select_one("input[name^='sourceChoice[']") is not None:
            return False
        if heading != config["heading"]:
            return False

        keyword = soup.select_one("main input#keyword[type='text']")
        if keyword is None or keyword.get("wire:model") != "keyword":
            return False
        form = keyword.find_parent("form")
        if form is None or not _has_submit(form):
            return False
        return True
    except Exception:
        return False


class LibraryDocumentsClient:
    def __init__(
        self,
        service: str,
        *,
        session_file: Path | None = None,
        timeout_ms: int = 30000,
        page: Any | None = None,
    ) -> None:
        self.service = service
        self.session_file = session_file
        self.timeout_ms = timeout_ms
        self._page = page
        self._owns_page = page is None
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None

    def __enter__(self) -> LibraryDocumentsClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._owns_page:
            return
        if self._context is not None:
            self._context.close()
            self._context = None
            self._page = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def status(self) -> dict[str, Any]:
        config = _SERVICE_CONFIG.get(self.service)
        if config is None:
            return _result(self.service, error="invalid_service")
        page, error = self._authenticated_page(config["search_url"])
        if page is None:
            return _result(self.service, url=config["search_url"], error=error)
        return _result(
            self.service,
            verified=True,
            url=page.url,
            authenticated=True,
        )

    def search(
        self,
        query: str = "",
        *,
        page: int = 1,
        per_page: int = 10,
        filters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        config = _SERVICE_CONFIG.get(self.service)
        if config is None:
            return _search_result(self.service, query, page, per_page, "invalid_service")
        if (
            not isinstance(query, str)
            or page < 1
            or per_page not in _PER_PAGE_OPTIONS
            or not _valid_filter_request(self.service, filters)
        ):
            return _search_result(self.service, query, page, per_page, "invalid_parameters")

        browser_page, error = self._authenticated_page(config["search_url"])
        if browser_page is None:
            return _search_result(
                self.service,
                query,
                page,
                per_page,
                error,
                url=config["search_url"],
            )
        try:
            _, filter_error = self._filter_options(browser_page, filters or {})
            if filter_error:
                return _search_result(
                    self.service,
                    query,
                    page,
                    per_page,
                    filter_error,
                    url=browser_page.url,
                )
            browser_page.locator("main input#keyword").fill(query.strip())
            browser_page.get_by_role("button", name="Submit", exact=True).click()
            browser_page.wait_for_url(
                re.compile(re.escape(config["results_path"]) + r"(?:\?.*)?$"),
                timeout=self.timeout_ms,
            )
            browser_page.locator("main h1").filter(
                has_text=config["results_heading"]
            ).wait_for(state="visible", timeout=self.timeout_ms)
            browser_page.locator(
                "main table, main .fi-ta-empty-state"
            ).first.wait_for(state="visible", timeout=self.timeout_ms)
            self._wait_for_update(browser_page)
            if not verified_results_page(browser_page, self.service):
                return _search_result(
                    self.service,
                    query,
                    page,
                    per_page,
                    "unexpected_page",
                    url=browser_page.url,
                )
            initial_per_page = self._current_per_page(browser_page)
            if initial_per_page is None:
                initial = _parse_results(
                    browser_page.content(),
                    self.service,
                    1,
                    per_page,
                )
                if initial is not None and initial["total"] != 0:
                    initial = None
            else:
                initial = _parse_results(
                    browser_page.content(),
                    self.service,
                    1,
                    initial_per_page,
                )
            if initial is None:
                return _search_result(
                    self.service,
                    query,
                    page,
                    per_page,
                    "unexpected_page",
                    url=browser_page.url,
                )
            if initial["total"] == 0:
                if page != 1:
                    return _search_result(
                        self.service,
                        query,
                        page,
                        per_page,
                        "invalid_page",
                        url=browser_page.url,
                    )
                return _result(
                    self.service,
                    verified=True,
                    url=browser_page.url,
                    query=query.strip(),
                    page=1,
                    per_page=per_page,
                    **initial,
                )
            if per_page != initial_per_page and not self._set_per_page(
                browser_page,
                per_page,
            ):
                return _search_result(
                    self.service,
                    query,
                    page,
                    per_page,
                    "unexpected_page",
                    url=browser_page.url,
                )
            total = initial["total"]
            last_page = (total + per_page - 1) // per_page
            if page > last_page:
                return _search_result(
                    self.service,
                    query,
                    page,
                    per_page,
                    "invalid_page",
                    url=browser_page.url,
                )
            if page > 1:
                if not self._go_to_page(browser_page, page, per_page):
                    return _search_result(
                        self.service,
                        query,
                        page,
                        per_page,
                        "unexpected_page",
                        url=browser_page.url,
                    )
            parsed = _parse_results(browser_page.content(), self.service, page, per_page)
            if parsed is None:
                return _search_result(
                    self.service,
                    query,
                    page,
                    per_page,
                    "unexpected_page",
                    url=browser_page.url,
                )
            return _result(
                self.service,
                verified=True,
                url=browser_page.url,
                query=query.strip(),
                page=page,
                per_page=per_page,
                **parsed,
            )
        except Exception:
            return _search_result(
                self.service,
                query,
                page,
                per_page,
                "unavailable",
                url=getattr(browser_page, "url", config["search_url"]),
            )

    def download(
        self,
        record_id: str,
        output: Path,
        *,
        query: str = "",
    ) -> dict[str, Any]:
        if (
            self.service not in _SERVICE_CONFIG
            or not isinstance(record_id, str)
            or re.fullmatch(r"[1-9]\d*", record_id) is None
            or not isinstance(query, str)
        ):
            return _result(self.service, error="invalid_parameters")
        output_error = _validate_output_path(output)
        if output_error:
            return _result(self.service, error=output_error, record_id=record_id)

        current_page = 1
        result = self.search(query, page=1, per_page=100)
        while True:
            if not result.get("verified"):
                return _result(
                    self.service,
                    error=result.get("error") or "unavailable",
                    record_id=record_id,
                )
            item = next(
                (
                    row
                    for row in result.get("items", [])
                    if row.get("record_id") == record_id
                ),
                None,
            )
            if item is not None:
                break
            total = result.get("total")
            if not isinstance(total, int) or current_page * 100 >= total:
                return _result(
                    self.service,
                    error="record_not_found",
                    record_id=record_id,
                )
            current_page += 1
            browser_page = self._page
            if browser_page is None or not self._click_next_page(
                browser_page,
                current_page,
                100,
            ):
                return _result(
                    self.service,
                    error="unexpected_page",
                    record_id=record_id,
                )
            parsed = _parse_results(
                browser_page.content(),
                self.service,
                current_page,
                100,
            )
            if parsed is None:
                return _result(
                    self.service,
                    error="unexpected_page",
                    record_id=record_id,
                )
            result = {"verified": True, **parsed}

        browser_page = self._page
        if browser_page is None or not verified_results_page(browser_page, self.service):
            return _result(self.service, error="unexpected_page", record_id=record_id)
        button = _record_download_button(browser_page, record_id)
        if button is None:
            return _result(self.service, error="unexpected_page", record_id=record_id)

        temporary: Path | None = None
        try:
            with browser_page.expect_download(timeout=self.timeout_ms) as download_info:
                button.click()
            download = download_info.value
            failure = download.failure()
            if failure:
                return _result(self.service, error="download_failed", record_id=record_id)
            suggested = str(download.suggested_filename or "")
            if not _safe_pdf_filename(suggested):
                return _result(self.service, error="invalid_file", record_id=record_id)
            if not os.path.lexists(output) and output.suffix.casefold() != ".pdf":
                output.mkdir()
            destination = output / suggested if output.is_dir() else output
            if os.path.lexists(destination):
                return _result(self.service, error="output_exists", record_id=record_id)
            source_path = download.path()
            if not source_path:
                return _result(self.service, error="invalid_file", record_id=record_id)
            source = Path(source_path)
            if not _valid_pdf(source):
                return _result(self.service, error="invalid_file", record_id=record_id)

            handle, name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
            )
            os.close(handle)
            temporary = Path(name)
            download.save_as(temporary)
            if not _valid_pdf(temporary):
                return _result(self.service, error="invalid_file", record_id=record_id)
            try:
                os.link(temporary, destination)
            except FileExistsError:
                return _result(self.service, error="output_exists", record_id=record_id)
            size = destination.stat().st_size
            return _result(
                self.service,
                verified=True,
                record_id=record_id,
                path=str(destination),
                bytes=size,
            )
        except Exception:
            return _result(self.service, error="download_failed", record_id=record_id)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def filters(self, filters: dict[str, str] | None = None) -> dict[str, Any]:
        config = _SERVICE_CONFIG.get(self.service)
        if config is None:
            return _result(self.service, error="invalid_service", filters={})
        if not _valid_filter_request(self.service, filters):
            return _result(self.service, error="invalid_parameters", filters={})
        page, error = self._authenticated_page(config["search_url"])
        if page is None:
            return _result(
                self.service,
                url=config["search_url"],
                error=error,
                filters={},
            )
        options, error = self._filter_options(page, filters or {})
        if options is None:
            return _result(
                self.service,
                url=page.url,
                error=error,
                filters={},
            )
        return _result(
            self.service,
            verified=True,
            url=page.url,
            filters=options,
            selected={key: value for key, value in (filters or {}).items() if value},
        )

    def _authenticated_page(self, url: str) -> tuple[Any | None, str | None]:
        page, error = self._ensure_page()
        if page is None:
            return None, error
        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.timeout_ms,
            )
            if response is None or not response.ok:
                return None, "unavailable"
            page.locator("main h1").wait_for(
                state="visible",
                timeout=self.timeout_ms,
            )
            parsed = urlsplit(page.url)
            if parsed.scheme != "https" or parsed.hostname != HOST:
                return None, "authentication_unverified"
            if _login_page(BeautifulSoup(page.content(), "html.parser")):
                return None, "authentication_unverified"
            page.locator("main input#keyword[type='text']").wait_for(
                state="visible",
                timeout=self.timeout_ms,
            )
            if not verified_database_page(page, self.service):
                return None, "authentication_unverified"
            return page, None
        except Exception:
            return None, "unavailable"

    def _ensure_page(self) -> tuple[Any | None, str | None]:
        if self._page is not None:
            return self._page, None
        if self.service not in _SERVICE_CONFIG:
            return None, "invalid_service"
        session_file = self.session_file or library_session_path(self.service)
        if not session_file.exists():
            return None, "missing_session"
        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=True,
                env=sanitized_environ(),
            )
            self._context = self._browser.new_context(
                storage_state=str(session_file),
                user_agent=USER_AGENT,
            )
            self._page = self._context.new_page()
            self._page.set_default_timeout(self.timeout_ms)
            return self._page, None
        except Exception:
            self.close()
            return None, "invalid_session"

    def _set_per_page(self, page: Any, per_page: int) -> bool:
        selectors = page.locator(".fi-pagination select")
        for index in range(selectors.count()):
            selector = selectors.nth(index)
            if selector.is_visible():
                selector.select_option(str(per_page))
                self._wait_for_update(page)
                return (
                    selector.input_value() == str(per_page)
                    and self._wait_for_results_window(page, 1, per_page)
                )
        return False

    def _current_per_page(self, page: Any) -> int | None:
        selectors = page.locator(".fi-pagination select")
        for index in range(selectors.count()):
            selector = selectors.nth(index)
            if not selector.is_visible():
                continue
            try:
                value = int(selector.input_value())
            except (TypeError, ValueError):
                return None
            return value if value in _PER_PAGE_OPTIONS else None
        return None

    def _go_to_page(self, page: Any, number: int, per_page: int) -> bool:
        controls = page.get_by_role(
            "button",
            name=f"Go to page {number}",
            exact=True,
        )
        direct = next(
            (
                controls.nth(index)
                for index in range(controls.count())
                if controls.nth(index).is_visible()
            ),
            None,
        )
        if direct is not None:
            direct.click()
            self._wait_for_update(page)
            return self._wait_for_results_window(page, number, per_page)
        for next_number in range(2, number + 1):
            if not self._click_next_page(page, next_number, per_page):
                return False
        return True

    def _click_next_page(self, page: Any, number: int, per_page: int) -> bool:
        expected_action = "nextPage('page')"
        desktop = page.locator(".fi-pagination button[aria-label='Next']")
        button = next(
            (
                desktop.nth(index)
                for index in range(desktop.count())
                if desktop.nth(index).is_visible()
                and not desktop.nth(index).is_disabled()
            ),
            None,
        )
        if button is None:
            controls = page.locator(".fi-pagination button[wire\\:click]")
            button = next(
                (
                    controls.nth(index)
                    for index in range(controls.count())
                    if controls.nth(index).get_attribute("wire:click")
                    == expected_action
                    and controls.nth(index).is_visible()
                    and not controls.nth(index).is_disabled()
                ),
                None,
            )
        if button is None:
            return False
        button.click()
        self._wait_for_update(page)
        return self._wait_for_results_window(page, number, per_page)

    def _wait_for_results_window(self, page: Any, number: int, per_page: int) -> bool:
        try:
            return bool(
                page.wait_for_function(
                    r"""args => {
                        const text = Array.from(document.querySelectorAll('.fi-pagination'))
                            .map((node) => node.textContent || '')
                            .find((value) => /Showing\s+[\d,]+\s+to\s+[\d,]+\s+of\s+[\d,]+\s+results/i.test(value))
                        if (!text) return false
                        const match = text.match(/Showing\s+([\d,]+)\s+to\s+([\d,]+)\s+of\s+([\d,]+)\s+results/i)
                        const start = Number(match[1].replaceAll(',', ''))
                        const end = Number(match[2].replaceAll(',', ''))
                        const total = Number(match[3].replaceAll(',', ''))
                        const expectedEnd = Math.min(total, start + args.perPage - 1)
                        const rows = Array.from(document.querySelectorAll('main table tbody tr'))
                            .filter((row) => row.hasAttribute('wire:key')).length
                        return start === ((args.page - 1) * args.perPage) + 1
                            && end === expectedEnd
                            && rows === end - start + 1
                    }""",
                    arg={"page": number, "perPage": per_page},
                    timeout=self.timeout_ms,
                )
            )
        except Exception:
            return False

    def _filter_options(
        self,
        page: Any,
        selected: dict[str, str],
    ) -> tuple[dict[str, Any] | None, str | None]:
        result: dict[str, Any] = {}
        page.wait_for_function(
            "() => Boolean(window.Livewire?.find)",
            timeout=self.timeout_ms,
        )
        for field in _FILTER_FIELDS[self.service]:
            control = page.locator(f"select#{field}")
            if control.count() != 1:
                return None, "unexpected_page"
            x_data = control.evaluate(
                "element => element.closest('[x-data]')?.getAttribute('x-data') || ''"
            )
            if not isinstance(x_data, str) or not _verified_select_component(x_data, field):
                return None, "unexpected_page"
            options = control.evaluate(
                """async (element, field) => {
                    let root = element
                    while (root && !root.hasAttribute('wire:id')) root = root.parentElement
                    const component = root && window.Livewire?.find(root.getAttribute('wire:id'))
                    if (!component?.$call) return null
                    return await component.$call('getFormSelectOptions', field)
                }""",
                field,
            )
            normalized = _normalize_options(options)
            if normalized is None:
                return None, "unexpected_schema"

            requested = selected.get(field, "")
            if requested:
                option = next(
                    (
                        item
                        for item in normalized
                        if item["value"] == requested and not item["disabled"]
                    ),
                    None,
                )
                if option is None and field in _DYNAMIC_FILTERS:
                    searched = control.evaluate(
                        """async (element, args) => {
                            let root = element
                            while (root && !root.hasAttribute('wire:id')) root = root.parentElement
                            const component = root && window.Livewire?.find(root.getAttribute('wire:id'))
                            if (!component?.$call) return null
                            return await component.$call(
                                'getFormSelectSearchResults', args.field, args.search
                            )
                        }""",
                        {"field": field, "search": requested},
                    )
                    searched_options = _normalize_options(searched)
                    if searched_options is None:
                        return None, "unexpected_schema"
                    option = next(
                        (
                            item
                            for item in searched_options
                            if item["value"] == requested and not item["disabled"]
                        ),
                        None,
                    )
                    if option is not None:
                        normalized = searched_options
                if option is None:
                    return None, "invalid_filter"
                updated = control.evaluate(
                    """async (element, args) => {
                        let root = element
                        while (root && !root.hasAttribute('wire:id')) root = root.parentElement
                        const component = root && window.Livewire?.find(root.getAttribute('wire:id'))
                        if (!component?.$set) return false
                        await component.$set(args.field, args.value)
                        return true
                    }""",
                    {"field": field, "value": requested},
                )
                if updated is not True:
                    return None, "unexpected_page"
                self._wait_for_update(page)
            result[field] = {
                "dynamic": field in _DYNAMIC_FILTERS,
                "options": normalized,
            }
        return result, None

    def _wait_for_update(self, page: Any) -> None:
        try:
            page.locator(".fi-loading-indicator").first.wait_for(
                state="hidden",
                timeout=self.timeout_ms,
            )
        except Exception:
            pass


def verified_results_page(page: Any, service: str) -> bool:
    config = _SERVICE_CONFIG.get(service)
    schema = _RESULT_SCHEMAS.get(service)
    if config is None or schema is None:
        return False
    try:
        parsed = urlsplit(page.url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != HOST
            or parsed.path.rstrip("/") != config["results_path"]
        ):
            return False
        soup = BeautifulSoup(page.content(), "html.parser")
        if _login_page(soup):
            return False
        if _text(soup.select_one("main h1")) != config["results_heading"]:
            return False
        if _text(soup.select_one("main h2")) != "Filters":
            return False
        headers = tuple(_text(cell) for cell in soup.select("main table thead th"))
        if headers:
            return headers == schema[0]
        return _verified_empty_state(soup, service)
    except Exception:
        return False


def _parse_results(
    html: str,
    service: str,
    page: int,
    per_page: int,
) -> dict[str, Any] | None:
    schema = _RESULT_SCHEMAS.get(service)
    config = _SERVICE_CONFIG.get(service)
    if schema is None or config is None:
        return None
    expected_headers, fields, _ = schema
    soup = BeautifulSoup(html, "html.parser")
    if _text(soup.select_one("main h1")) != config["results_heading"]:
        return None
    if _text(soup.select_one("main h2")) != "Filters":
        return None
    headers = tuple(_text(cell) for cell in soup.select("main table thead th"))
    if not headers and _verified_empty_state(soup, service):
        return {"items": [], "documents": [], "total": 0}
    if headers != expected_headers:
        return None
    pagination = _PAGINATION_RE.search(_text(soup.select_one(".fi-pagination")))
    if pagination is None:
        return None
    start, end, total = (
        int(value.replace(",", "")) for value in pagination.groups()
    )
    if total < 1 or start < 1 or end < start or end > total:
        return None
    expected_end = min(total, start + per_page - 1)
    if (
        end != expected_end
        or start != ((page - 1) * per_page) + 1
    ):
        return None

    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in soup.select("main table tbody tr[wire\\:key]"):
        match = _RECORD_KEY_RE.search(str(row.get("wire:key", "")))
        cells = row.select(":scope > td")
        if match is None or len(cells) != len(fields):
            return None
        record_id = match.group(1)
        if record_id in seen_ids:
            return None
        seen_ids.add(record_id)
        action = cells[0].select_one("button[wire\\:click]")
        action_match = (
            _FILE_ACTION_RE.fullmatch(str(action.get("wire:click", "")))
            if action is not None
            else None
        )
        if action_match is None or action_match.group(1) != record_id:
            return None
        item = {
            field: _text(cell)
            for field, cell in zip(fields[1:], cells[1:], strict=True)
        }
        item.update({"record_id": record_id, "full_text": True})
        items.append(item)

    expected_count = end - start + 1
    if len(items) != expected_count:
        return None
    return {"items": items, "documents": items, "total": total}


def _has_submit(form: Any) -> bool:
    for control in form.select("button[type='submit'], input[type='submit']"):
        label = control.get("value") if control.name == "input" else _visible_text(control)
        if str(label or "").strip() == "Submit":
            return True
    return False


def _visible_text(element: Any) -> str:
    fragment = BeautifulSoup(str(element), "html.parser")
    control = fragment.find()
    if control is None:
        return ""
    for hidden in control.select(
        "[hidden], [aria-hidden='true'], [wire\\:loading], .fi-loading-indicator"
    ):
        hidden.decompose()
    for hidden in control.select("[style]"):
        style = str(hidden.get("style", "")).replace(" ", "").casefold()
        if "display:none" in style or "visibility:hidden" in style:
            hidden.decompose()
    return _text(control)


def _verified_empty_state(soup: BeautifulSoup, service: str) -> bool:
    schema = _RESULT_SCHEMAS.get(service)
    if schema is None:
        return False
    empty_states = soup.select("main .fi-ta-empty-state")
    if len(empty_states) != 1 or _text(empty_states[0]) != schema[2]:
        return False
    return (
        soup.select_one("main table") is None
        and soup.select_one("main .fi-pagination") is None
    )


def _record_download_button(page: Any, record_id: str) -> Any | None:
    expected_key = re.compile(rf"\.table\.records\.{re.escape(record_id)}$")
    expected_action = f"callTableColumnAction('file_path', '{record_id}')"
    rows = page.locator("main table tbody tr[wire\\:key]")
    matched = None
    for index in range(rows.count()):
        row = rows.nth(index)
        if expected_key.search(str(row.get_attribute("wire:key") or "")) is None:
            continue
        buttons = row.locator("button[wire\\:click]")
        for button_index in range(buttons.count()):
            button = buttons.nth(button_index)
            if button.get_attribute("wire:click") != expected_action:
                continue
            if not button.is_visible():
                continue
            if matched is not None:
                return None
            matched = button
    return matched


def _validate_output_path(output: Any) -> str | None:
    if not isinstance(output, Path) or output.name in {"", ".", ".."}:
        return "invalid_output"
    if os.path.lexists(output):
        return None if output.is_dir() else "output_exists"
    if not output.parent.is_dir():
        return "invalid_output"
    if output.suffix.casefold() == ".pdf" or not output.suffix:
        return None
    return "invalid_output"


def _safe_pdf_filename(filename: str) -> bool:
    return (
        bool(filename)
        and "/" not in filename
        and "\\" not in filename
        and "\x00" not in filename
        and filename == Path(filename).name
        and filename not in {".", ".."}
        and Path(filename).suffix.casefold() == ".pdf"
    )


def _valid_pdf(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size < 5:
            return False
        with path.open("rb") as handle:
            return handle.read(5) == b"%PDF-"
    except OSError:
        return False


def _login_page(soup: BeautifulSoup) -> bool:
    if _text(soup.title).casefold().startswith("login -"):
        return True
    if _text(soup.select_one("main h1")) == "Sign in":
        return True
    return soup.select_one(
        "input[type='password'], input[name='AuthState'], input[name^='sourceChoice[']"
    ) is not None


def _valid_filter_request(service: str, filters: dict[str, str] | None) -> bool:
    if filters is None:
        return True
    if not isinstance(filters, dict):
        return False
    allowed = set(_FILTER_FIELDS.get(service, ()))
    return all(
        key in allowed and isinstance(value, str) and len(value) <= 200
        for key, value in filters.items()
    )


def _verified_select_component(x_data: str, field: str) -> bool:
    quoted = re.escape(field)
    checks = (
        rf"getFormSelectOptions\(['\"]{quoted}['\"]\)",
        rf"getFormSelectSearchResults\(['\"]{quoted}['\"],\s*search\)",
        rf"statePath\s*:\s*['\"]{quoted}['\"]",
    )
    return all(re.search(pattern, x_data) is not None for pattern in checks)


def _normalize_options(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    options: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        label = item.get("label")
        option_value = item.get("value")
        disabled = item.get("disabled", False)
        if (
            not isinstance(label, str)
            or not isinstance(option_value, (str, int))
            or not isinstance(disabled, bool)
        ):
            return None
        options.append(
            {
                "label": " ".join(label.split()),
                "value": str(option_value),
                "disabled": disabled,
            }
        )
    return options


def _text(element: Any) -> str:
    if element is None:
        return ""
    return " ".join(element.get_text(" ", strip=True).split())


def _result(
    service: str,
    *,
    verified: bool = False,
    url: str | None = None,
    error: str | None = None,
    **payload: Any,
) -> dict[str, Any]:
    return {
        "verified": verified,
        "source": SOURCE if verified else "unverified",
        "service": service,
        "url": url,
        "error": error,
        **payload,
    }


def _search_result(
    service: str,
    query: Any,
    page: int,
    per_page: int,
    error: str | None,
    *,
    url: str | None = None,
) -> dict[str, Any]:
    return _result(
        service,
        url=url,
        error=error,
        query=query.strip() if isinstance(query, str) else "",
        page=page,
        per_page=per_page,
        items=[],
        documents=[],
        total=None,
    )
