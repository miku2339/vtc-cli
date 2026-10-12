from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag


LIBRARY_HOST = "library.vtc.edu.hk"
LIBGUIDES_HOST = "libguides.vtc.edu.hk"
OFFICIAL_HOSTS = frozenset({LIBRARY_HOST, LIBGUIDES_HOST})
LIBRARY_HOME = f"https://{LIBRARY_HOST}/web/"
SOURCE = "library_html"

RESOURCE_CATEGORIES = (
    "all",
    "databases",
    "ebooks",
    "ejournals",
    "videos",
    "repositories",
    "guides",
)

INFO_TOPICS = (
    "hours",
    "locations",
    "news",
    "services",
    "regulations",
    "membership",
    "remote-access",
    "room-booking",
    "laptop-loans",
    "forms",
    "staff",
    "alumni",
)

_RESOURCE_PAGES = {
    "all": "/web/english/eres/eres.php",
    "databases": "/web/english/eres/",
    "ebooks": "/web/english/eres/ebk.php",
    "ejournals": "/web/english/eres/eres.php",
    "videos": "/web/english/eres/evideo.php",
    "repositories": "/web/english/eres/eres.php",
    "guides": "/web/english/guides/",
}

_RESOURCE_HEADINGS = {
    "all": ("Electronic Resources",),
    "databases": ("E-Databases",),
    "ebooks": ("E-Books",),
    "ejournals": ("E-Journals",),
    "videos": ("eVideo Portal",),
    "repositories": ("Repository",),
    "guides": ("Subject Guide",),
}

_INFO_PAGES = {
    "hours": ("/web/english/info/open.php", "Opening Hours"),
    "locations": ("/web/english/info/loc.php", "Locations"),
    "news": ("/web/english/news.php", "News & Announcements"),
    "services": ("/web/english/info/serv.php", "Library Services & Facilities"),
    "regulations": ("/web/english/guides/regulation.php", "Library Regulations"),
    "membership": ("/web/english/info/ticket.php", "Library Membership"),
    "remote-access": ("/web/english/guides/gremote.php", "Remote Access Guide"),
    "room-booking": ("/web/english/roombook.php", "Book a Room"),
    "laptop-loans": ("/web/english/info/laptop-loan.php", "Laptop Loans"),
    "forms": ("/web/english/forms/forms.php", "Forms"),
    "staff": ("/web/english/info/staff.php", "Staff Directory"),
    "alumni": ("/web/english/info/as.php", "Alumni Services"),
}

_RESOURCE_KINDS = {
    "all": "directory",
    "databases": "collection",
    "ebooks": "collection",
    "ejournals": "directory",
    "videos": "collection",
    "repositories": "directory",
    "guides": "collection",
}

_MAINTENANCE_MARKERS = (
    "under maintenance",
    "temporarily unavailable",
    "service unavailable",
    "website is currently unavailable",
)


class LibraryPublicClient:
    def __init__(
        self,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.http = httpx.Client(
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "vtc-cli library public reader",
            },
            follow_redirects=False,
            timeout=timeout,
            transport=transport,
        )

    def __enter__(self) -> LibraryPublicClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self.http.close()

    def links(self) -> dict[str, Any]:
        response, error = self._get_official(LIBRARY_HOME)
        if response is None:
            return self._links_result(url=LIBRARY_HOME, error=error)

        soup = BeautifulSoup(response.text, "html.parser")
        if self._is_bad_page(soup):
            return self._links_result(url=str(response.url), error="unexpected_page")
        nav = soup.select_one("#nav")
        shortcuts = soup.select_one("#main_buttons")
        if nav is None or shortcuts is None:
            return self._links_result(url=str(response.url), error="unexpected_page")

        records: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        categorized: set[int] = set()

        for group in nav.select(":scope > li.top"):
            top = group.select_one(":scope > a.top_link")
            category = _link_title(top) if top is not None else "Navigation"
            for anchor in group.select("a[href]"):
                categorized.add(id(anchor))
                self._append_link(records, seen, anchor, str(response.url), category)

        for anchor in soup.select("a[href]"):
            if id(anchor) in categorized:
                continue
            if anchor.find_parent(id="main_buttons") is not None:
                category = "Home shortcuts"
            elif anchor.find_parent("footer") is not None:
                category = "Footer"
            else:
                category = "Utility"
            self._append_link(records, seen, anchor, str(response.url), category)

        if not records:
            return self._links_result(url=str(response.url), error="unexpected_page")
        return {
            "verified": True,
            "source": SOURCE,
            "url": str(response.url),
            "links": records,
        }

    def resources(self, category: str = "all", query: str = "") -> dict[str, Any]:
        if category not in RESOURCE_CATEGORIES:
            return self._resources_result(category, query, LIBRARY_HOME, "invalid_category")

        url = urljoin(LIBRARY_HOME, _RESOURCE_PAGES[category])
        response, error = self._get_official(url)
        if response is None:
            return self._resources_result(category, query, url, error)

        soup = BeautifulSoup(response.text, "html.parser")
        content = soup.select_one("#mainContent")
        if content is None or self._is_bad_page(soup):
            return self._resources_result(category, query, str(response.url), "unexpected_page")
        if not _has_heading(content, _RESOURCE_HEADINGS[category]):
            return self._resources_result(category, query, str(response.url), "unexpected_page")

        result_url = str(response.url)
        directory_url = result_url
        listed = True
        result_metadata: dict[str, Any] = {}
        if category in {"databases", "ebooks", "videos"}:
            frame = content.select_one("iframe[src]")
            frame_url = _normalize_href(frame.get("src"), result_url) if frame else None
            if frame_url is None or not _is_official_url(frame_url):
                return self._resources_result(category, query, result_url, "unexpected_page")
            nested_url = frame_url
            if category == "videos" and query.strip():
                nested_url = _replace_query(
                    frame_url,
                    {"keyword_search": query.strip(), "submit_search": "Search"},
                )
            nested, error = self._get_official(nested_url)
            if nested is None:
                return self._resources_result(category, query, frame_url, error)
            nested_soup = BeautifulSoup(nested.text, "html.parser")
            if self._is_bad_page(nested_soup):
                return self._resources_result(category, query, str(nested.url), "unexpected_page")
            result_url = str(nested.url)
            if category == "videos":
                if query.strip():
                    records, listed, result_metadata = _video_search_resources(
                        nested_soup, result_url
                    )
                else:
                    records, listed, result_metadata = _video_resources(
                        nested_soup, result_url
                    )
            else:
                records, listed, error = self._load_az_listing(
                    nested_soup, result_url, category, query
                )
                if error:
                    return self._resources_result(category, query, result_url, error)
        elif category == "guides":
            records, listed = _guide_resources(content, result_url)
        else:
            records = _heading_resources(content, result_url, category)
            listed = bool(records)

        if not listed:
            return self._resources_result(category, query, result_url, "unexpected_page")
        if category in {"databases", "ebooks"} or (
            category == "videos" and query.strip()
        ):
            filtered = records
        else:
            filtered = _filter_resources(records, query)
        return {
            "verified": True,
            "source": SOURCE,
            "url": result_url,
            "directory_url": directory_url,
            "category": category,
            "kind": _RESOURCE_KINDS[category],
            "query": query.strip(),
            "result_count": len(filtered),
            "resources": filtered,
            **result_metadata,
        }

    def info(self, topic: str) -> dict[str, Any]:
        page = _INFO_PAGES.get(topic)
        if page is None:
            return self._info_result(topic, LIBRARY_HOME, "invalid_topic")
        path, expected_heading = page
        url = urljoin(LIBRARY_HOME, path)
        response, error = self._get_official(url)
        if response is None:
            return self._info_result(topic, url, error)

        soup = BeautifulSoup(response.text, "html.parser")
        content = soup.select_one("#mainContent")
        if content is None or self._is_bad_page(soup):
            return self._info_result(topic, str(response.url), "unexpected_page")
        heading = content.find("h1")
        title = _clean_text(heading)
        if not title or expected_heading.casefold() not in title.casefold():
            return self._info_result(topic, str(response.url), "unexpected_page")

        _resolve_document_writes(content)
        for element in content.select("script, style, noscript"):
            element.decompose()
        text = _clean_text(content)
        if len(text) <= len(title):
            return self._info_result(topic, str(response.url), "unexpected_page")

        return {
            "verified": True,
            "source": SOURCE,
            "url": str(response.url),
            "topic": topic,
            "title": title,
            "text": text,
            "tables": _extract_tables(content),
            "links": _extract_links(content, str(response.url)),
        }

    def _load_az_listing(
        self,
        soup: BeautifulSoup,
        page_url: str,
        category: str,
        query: str,
    ) -> tuple[list[dict[str, Any]], bool, str | None]:
        site_id = _az_site_id(soup)
        if site_id is None:
            return [], False, "unexpected_page"
        params = {
            "search": query.strip(),
            "subject_id": "",
            "type_id": "36194" if category == "ebooks" else "",
            "vendor_id": "",
            "access_mode_id": "",
            "page": "1",
            "site_id": site_id,
            "content_id": "0",
            "is_widget": "0",
            "bootstrap5": "true",
            "page_size": "0",
            "preview": "",
            "sublist_id": "0",
            "alpha": "",
        }
        endpoint = urljoin(page_url, "/process/az/render-list")
        response, error = self._get_official(
            f"{endpoint}?{urlencode(params)}", expected_content="json"
        )
        if response is None:
            return [], False, error
        try:
            payload = response.json()
            data = payload["data"]
            html = data["html"]
            count = int(data["count"])
        except (KeyError, TypeError, ValueError):
            return [], False, "unexpected_page"
        if payload.get("errText") != "OK" or not isinstance(html, str):
            return [], False, "unexpected_page"
        records, listed = _az_resources(
            BeautifulSoup(html, "html.parser"), page_url, expected_count=count
        )
        return records, listed, None

    def _get_official(
        self, url: str, *, expected_content: str = "html"
    ) -> tuple[httpx.Response | None, str | None]:
        current = url
        for _ in range(6):
            if not _is_official_url(current):
                return None, "unexpected_redirect"
            try:
                response = self.http.get(current)
            except httpx.HTTPError:
                return None, "request_failed"
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    return None, "unexpected_redirect"
                current = urljoin(str(response.url), location)
                continue
            if response.status_code < 200 or response.status_code >= 300:
                return None, "http_error"
            if not _is_official_url(str(response.url)):
                return None, "unexpected_redirect"
            content_type = response.headers.get("content-type", "").casefold()
            if expected_content not in content_type:
                return None, "unexpected_content_type"
            return response, None
        return None, "too_many_redirects"

    @staticmethod
    def _append_link(
        records: list[dict[str, Any]],
        seen: set[tuple[str, str, str]],
        anchor: Tag,
        base_url: str,
        category: str,
    ) -> None:
        title = _link_title(anchor)
        target = _normalize_href(anchor.get("href"), base_url)
        if not title or target is None:
            return
        key = (title, target, category)
        if key in seen:
            return
        seen.add(key)
        records.append(
            {
                "title": title,
                "url": target,
                "category": category,
                "external": not _is_official_host(target),
            }
        )

    @staticmethod
    def _is_bad_page(soup: BeautifulSoup) -> bool:
        text = _clean_text(soup).casefold()
        return any(marker in text for marker in _MAINTENANCE_MARKERS)

    @staticmethod
    def _links_result(*, url: str, error: str | None) -> dict[str, Any]:
        return {
            "verified": False,
            "source": SOURCE,
            "url": url,
            "links": [],
            "error": error or "unverified",
        }

    @staticmethod
    def _resources_result(
        category: str, query: str, url: str, error: str | None
    ) -> dict[str, Any]:
        return {
            "verified": False,
            "source": SOURCE,
            "url": url,
            "category": category,
            "kind": _RESOURCE_KINDS.get(category),
            "query": query.strip(),
            "result_count": 0,
            "resources": [],
            "error": error or "unverified",
        }

    @staticmethod
    def _info_result(topic: str, url: str, error: str | None) -> dict[str, Any]:
        return {
            "verified": False,
            "source": SOURCE,
            "url": url,
            "topic": topic,
            "title": None,
            "text": "",
            "tables": [],
            "links": [],
            "error": error or "unverified",
        }


def _clean_text(value: Tag | BeautifulSoup | None) -> str:
    if value is None:
        return ""
    return " ".join(value.get_text(" ", strip=True).split())


def _link_title(anchor: Tag | None) -> str:
    if anchor is None:
        return ""
    text = _clean_text(anchor)
    if text:
        return text
    for attribute in ("aria-label", "title"):
        value = anchor.get(attribute)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    image = anchor.find("img", alt=True)
    if image is not None:
        return " ".join(str(image.get("alt") or "").split())
    return ""


def _normalize_href(value: Any, base_url: str) -> str | None:
    if not isinstance(value, str):
        return None
    href = value.strip()
    if not href or href.startswith(("#", "javascript:")):
        return None
    absolute = urljoin(base_url, href)
    parsed = urlsplit(absolute)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.hostname and parsed.hostname.casefold() in OFFICIAL_HOSTS and parsed.scheme == "http":
        parsed = parsed._replace(scheme="https")
        absolute = urlunsplit(parsed)
    return absolute


def _replace_query(url: str, params: dict[str, str]) -> str:
    return urlsplit(url)._replace(query=urlencode(params), fragment="").geturl()


def _is_official_host(url: str) -> bool:
    return (urlsplit(url).hostname or "").casefold() in OFFICIAL_HOSTS


def _is_official_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme == "https" and (parsed.hostname or "").casefold() in OFFICIAL_HOSTS


def _has_heading(content: Tag, expected: tuple[str, ...]) -> bool:
    headings = [_clean_text(node).casefold() for node in content.select("h1, h2, h3")]
    return any(term.casefold() in heading for term in expected for heading in headings)


def _heading_resources(content: Tag, base_url: str, category: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for heading in content.find_all("h2"):
        title = _clean_text(heading)
        folded = title.casefold()
        if category == "ejournals" and "e-journals" not in folded:
            continue
        if category == "repositories" and "repository" not in folded:
            continue
        anchor = _first_link_before_next_heading(heading)
        if anchor is None:
            continue
        target = _normalize_href(anchor.get("href"), base_url)
        if target is None:
            continue
        container = anchor.find_parent("p")
        records.append(
            {
                "title": title or _link_title(anchor),
                "url": target,
                "description": _clean_text(container),
                "external": not _is_official_host(target),
            }
        )
    return records


def _first_link_before_next_heading(heading: Tag) -> Tag | None:
    for element in heading.next_elements:
        if element is heading:
            continue
        if isinstance(element, Tag) and element.name in {"h1", "h2"}:
            return None
        if isinstance(element, Tag) and element.name == "a" and element.get("href"):
            return element
    return None


def _az_site_id(soup: BeautifulSoup) -> str | None:
    field = soup.select_one("#site_id_default[value], input[name='site_id'][value]")
    if field is not None:
        value = str(field.get("value") or "").strip()
        if value.isdigit():
            return value
    match = re.search(r"\bsite_id\s*:\s*['\"]?(\d+)", str(soup))
    return match.group(1) if match else None


def _az_resources(
    soup: BeautifulSoup,
    base_url: str,
    *,
    expected_count: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    cards = soup.select(
        ".az-item, .s-lg-az-result, [data-az-record], .s-lg-db-panel[data-az-record]"
    )
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for card in cards:
        anchor = card.select_one(
            "a.az-title[href], .s-lg-az-result-title a[href], "
            ".s-lg-db-panel-title a[href], "
            "h2 a[href], h3 a[href], h4 a[href]"
        )
        if anchor is None:
            continue
        title = _link_title(anchor)
        target = _normalize_href(anchor.get("href"), base_url)
        if not title or target is None or (title, target) in seen:
            continue
        seen.add((title, target))
        description = _first_text(
            card,
            (
                ".s-lg-az-result-description",
                ".s-lg-db-panel-description",
                ".az-description-short",
                ".az-description",
                ".description",
            ),
        )
        records.append(
            {
                "title": title,
                "url": target,
                "description": description,
                "subjects": _metadata_values(card, "subject"),
                "types": _metadata_values(card, "type"),
                "vendors": _metadata_values(card, "vendor"),
                "external": not _is_official_host(target),
            }
        )
    no_results = soup.select_one(".s-lg-az-no-results, .s-lg-no-results") is not None
    if expected_count is not None:
        listed = len(records) == expected_count
    else:
        listed = bool(records) or no_results
    return records, listed


def _guide_resources(content: Tag, base_url: str) -> tuple[list[dict[str, Any]], bool]:
    table = content.select_one("#subjecttable")
    if table is None:
        return [], False
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for anchor in table.select("a[href]"):
        title = _link_title(anchor)
        target = _normalize_href(anchor.get("href"), base_url)
        if not title or target is None or (title, target) in seen:
            continue
        seen.add((title, target))
        records.append(
            {
                "title": title,
                "url": target,
                "description": "VTC Library subject guide",
                "subjects": [title],
                "types": ["Guide"],
                "vendors": [],
                "external": not _is_official_host(target),
            }
        )
    return records, bool(records) or "no subject" in _clean_text(table).casefold()


def _video_resources(
    soup: BeautifulSoup, base_url: str
) -> tuple[list[dict[str, Any]], bool, dict[str, Any]]:
    channel_select = soup.select_one("select#cid")
    programme_select = soup.select_one("select#pid")
    year_select = soup.select_one("select#eYear")
    episode_select = soup.select_one("select#eid")
    if not all((channel_select, programme_select, year_select, episode_select)):
        return [], False, {}

    channel_value, channel = _selected_option(channel_select)
    programme_value, programme = _selected_option(programme_select)
    year_value, year = _selected_option(year_select)
    records: list[dict[str, Any]] = []
    for option in episode_select.select("option[value]"):
        episode_id = str(option.get("value") or "").strip()
        title = _clean_text(option)
        if not episode_id or not title:
            continue
        target = _replace_query(
            base_url,
            {
                "cid": channel_value,
                "pid": programme_value,
                "eYear": year_value,
                "eid": episode_id,
            },
        )
        records.append(
            {
                "title": title,
                "url": target,
                "description": " · ".join(value for value in (programme, channel, year) if value),
                "subjects": [value for value in (channel, programme) if value],
                "types": ["Video"],
                "vendors": ["VTC Library eVideo"],
                "year": year,
                "id": episode_id,
                "external": False,
            }
        )
    metadata = {
        "scope": "selected_facet",
        "facet": {
            "channel": {"value": channel_value, "title": channel},
            "programme": {"value": programme_value, "title": programme},
            "year": {"value": year_value, "title": year},
        },
        "facets": {
            "channels": _option_records(channel_select),
            "programmes": _option_records(programme_select),
            "years": _option_records(year_select),
        },
    }
    listed = bool(records) or "no episode" in _clean_text(episode_select).casefold()
    return records, listed, metadata


def _video_search_resources(
    soup: BeautifulSoup, base_url: str
) -> tuple[list[dict[str, Any]], bool, dict[str, Any]]:
    result = soup.select_one("#search_result")
    if result is None:
        return [], False, {}
    status = _clean_text(result)
    match = re.search(
        r"Found\s+(\d+)\s+results?\.\s+Showing\s+(\d+)\s+to\s+(\d+)\s+records?\.",
        status,
        flags=re.IGNORECASE,
    )
    table = result.find("table")
    if match is None or table is None:
        return [], False, {}
    matched_count, start, end = (int(value) for value in match.groups())
    shown_count = 0 if matched_count == 0 else end - start + 1
    if shown_count < 0:
        return [], False, {}

    records: list[dict[str, Any]] = []
    for row in table.select("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 3:
            return [], False, {}
        anchor = cells[1].select_one("a[href]")
        target = _normalize_href(anchor.get("href"), base_url) if anchor else None
        if anchor is None or target is None or not _is_official_url(target):
            return [], False, {}
        params = parse_qs(urlsplit(target).query)
        required = {name: values[0] for name, values in params.items() if values}
        if not all(required.get(name) for name in ("cid", "pid", "eYear", "eid")):
            return [], False, {}
        programme = _clean_text(cells[0])
        title = _link_title(anchor)
        date = _clean_text(cells[2])
        if not programme or not title or not date:
            return [], False, {}
        records.append(
            {
                "title": title,
                "url": target,
                "description": f"{programme} · {date}",
                "subjects": [programme],
                "types": ["Video"],
                "vendors": ["VTC Library eVideo"],
                "year": required["eYear"],
                "date": date,
                "id": required["eid"],
                "channel_id": required["cid"],
                "programme_id": required["pid"],
                "external": False,
            }
        )
    listed = len(records) == shown_count and (
        matched_count == 0 or 1 <= start <= end <= matched_count
    )
    return records, listed, {
        "scope": "search_results",
        "search": {"matched_count": matched_count, "shown_count": shown_count},
    }


def _selected_option(select: Tag) -> tuple[str, str]:
    option = select.select_one("option[selected]") or select.find("option")
    if option is None:
        return "", ""
    return str(option.get("value") or "").strip(), _clean_text(option)


def _option_records(select: Tag) -> list[dict[str, str]]:
    return [
        {"value": str(option.get("value") or "").strip(), "title": _clean_text(option)}
        for option in select.select("option[value]")
        if _clean_text(option)
    ]


def _first_text(container: Tag, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        node = container.select_one(selector)
        text = _clean_text(node)
        if text:
            return text
    return ""


def _metadata_values(container: Tag, name: str) -> list[str]:
    selectors = (
        f".s-lg-az-result-{name}s a",
        f".s-lg-az-result-{name}s",
        f".s-lg-db-panel-{name}s a",
        f".s-lg-db-panel-{name}s",
        f"[data-filter='{name}']",
    )
    values: list[str] = []
    for selector in selectors:
        for node in container.select(selector):
            text = _clean_text(node)
            text = re.sub(rf"^{name}s?\s*:\s*", "", text, flags=re.IGNORECASE)
            for value in re.split(r"\s*[;,|]\s*", text):
                if value and value not in values:
                    values.append(value)
        if values:
            break
    return values


def _filter_resources(records: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    needle = query.strip().casefold()
    if not needle:
        return records
    filtered: list[dict[str, Any]] = []
    for record in records:
        values: list[str] = []
        for key in ("title", "description", "subjects", "types", "vendors", "year"):
            value = record.get(key)
            if isinstance(value, list):
                values.extend(str(item) for item in value)
            elif value is not None:
                values.append(str(value))
        if needle in " ".join(values).casefold():
            filtered.append(record)
    return filtered


def _extract_links(content: Tag, base_url: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for anchor in content.select("a[href]"):
        title = _link_title(anchor)
        target = _normalize_href(anchor.get("href"), base_url)
        if not title or target is None or (title, target) in seen:
            continue
        seen.add((title, target))
        records.append(
            {
                "title": title,
                "url": target,
                "external": not _is_official_host(target),
            }
        )
    return records


def _resolve_document_writes(content: Tag) -> None:
    assignments: dict[str, str] = {}
    for script in content.find_all("script"):
        script_text = script.string or script.get_text()
        for name, _quote, value in re.findall(
            r"\$([A-Za-z0-9_]+)\s*=\s*([\"'])(.*?)\2\s*;?",
            script_text,
            flags=re.DOTALL,
        ):
            assignments[name] = value

    for script in content.find_all("script"):
        script_text = script.string or script.get_text()
        match = re.search(r"document\.write\(\s*\$([A-Za-z0-9_]+)\s*\)", script_text)
        if not match:
            continue
        value = assignments.get(match.group(1))
        if value is not None:
            replacement = _clean_text(BeautifulSoup(value, "html.parser"))
            script.replace_with(replacement)


def _extract_tables(content: Tag) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for table in content.find_all("table"):
        caption = _clean_text(table.find("caption"))
        if not caption:
            previous = table.find_previous(["h1", "h2", "h3"])
            caption = _clean_text(previous)
        headers: list[str] = []
        rows: list[list[str]] = []
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"], recursive=False)
            if not cells:
                cells = row.find_all(["th", "td"])
            values = [_clean_text(cell) for cell in cells]
            if not any(values):
                continue
            if not headers and any(cell.name == "th" for cell in cells):
                headers = values
            else:
                rows.append(values)
        if headers or rows:
            tables.append({"caption": caption, "headers": headers, "rows": rows})
    return tables
