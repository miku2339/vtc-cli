from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from vtc.myportal.constants import TRANSCRIPT_MARKERS, TUITION_MARKERS

HK = ZoneInfo("Asia/Hong_Kong")
WEEK_OPTION_RE = re.compile(
    r"\((\d+)\)\s+(\d{2}-[A-Za-z]{3}-\d{4})\s+-\s+(\d{2}-[A-Za-z]{3}-\d{4})"
)
TIME_RANGE_RE = re.compile(r"\((\d{2}:\d{2}\s*-\s*\d{2}:\d{2})\)")
WEEK_NO_RE = re.compile(r"Wk:(\d+)")
WEEKDAY_TO_EN = {
    "monday": "Monday",
    "tuesday": "Tuesday",
    "wednesday": "Wednesday",
    "thursday": "Thursday",
    "friday": "Friday",
    "saturday": "Saturday",
    "sunday": "Sunday",
    "星期一": "Monday",
    "星期二": "Tuesday",
    "星期三": "Wednesday",
    "星期四": "Thursday",
    "星期五": "Friday",
    "星期六": "Saturday",
    "星期日": "Sunday",
    "星期天": "Sunday",
}


def today_hk() -> date:
    return datetime.now(HK).date()


def weekday_en(value: date | None = None) -> str:
    day = value or today_hk()
    return (
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    )[day.weekday()]


def normalize_weekday(value: str | None) -> str:
    text = (value or "").strip()
    return WEEKDAY_TO_EN.get(text.lower(), WEEKDAY_TO_EN.get(text, text))


def clean_text(value: str | None) -> str:
    return " ".join((value or "").replace("\xa0", " ").split())


def looks_like_myportal_login(html_text: str, url: str | None = None) -> bool:
    text = (html_text or "").lower()
    has_userid = 'name="userid"' in text or "name='userid'" in text
    has_password = 'name="password"' in text or "name='password'" in text
    if url and "/wps/myportal/sp/" in url.lower() and "log out" in text:
        return False
    return has_userid and has_password


def item_id(*parts: str) -> str:
    joined = " ".join(part for part in parts if part)
    slug = re.sub(r"[^a-z0-9]+", "-", joined.lower()).strip("-")
    return slug[:48] or "item"


def select_week_option(
    options: list[dict[str, str]],
    *,
    today: date | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    today = today or today_hk()
    normalized: list[dict[str, Any]] = []
    for opt in options:
        label = opt.get("label", "")
        value = opt.get("value", "")
        match = WEEK_OPTION_RE.search(label)
        if not match:
            continue
        start = datetime.strptime(match.group(2), "%d-%b-%Y").date()
        end = datetime.strptime(match.group(3), "%d-%b-%Y").date()
        normalized.append(
            {
                "label": label,
                "value": value,
                "week_no": match.group(1),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "_start": start,
                "_end": end,
            }
        )
    if not normalized:
        return None, None
    for opt in normalized:
        if opt["_start"] <= today <= opt["_end"]:
            return opt, opt
    nearest = min(
        normalized,
        key=lambda opt: min(abs((today - opt["_start"]).days), abs((today - opt["_end"]).days)),
    )
    return nearest, nearest


def public_week(option: dict[str, Any] | None) -> dict[str, Any] | None:
    if not option:
        return None
    return {key: value for key, value in option.items() if not str(key).startswith("_")}


def parse_timetable_cell(text: str, weekday: str, week_label: str) -> dict[str, Any] | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 4:
        return None
    time_match = TIME_RANGE_RE.search(lines[1])
    week_match = WEEK_NO_RE.search(text)
    return {
        "weekday": normalize_weekday(weekday),
        "course": clean_text(lines[0]),
        "time_range": time_match.group(1).replace(" ", "") if time_match else None,
        "room": clean_text(lines[2]),
        "instructor": clean_text(lines[3]),
        "week_label": week_label,
        "week_no": week_match.group(1) if week_match else None,
        "raw": clean_text(text),
    }


def parse_timetable_grid(
    grid_rows: list[list[dict[str, Any]]],
    week_label: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not grid_rows or not grid_rows[0]:
        return [], []
    weekdays = [
        normalize_weekday(cell.get("text", "").strip())
        for cell in grid_rows[0][1:]
        if cell.get("text", "").strip()
    ]
    if not weekdays:
        return [], []
    carry_spans = [0] * len(weekdays)
    entries: list[dict[str, Any]] = []
    for row in grid_rows[1:]:
        if not row:
            continue
        iterator = iter(row[1:])
        for day_idx, weekday in enumerate(weekdays):
            if carry_spans[day_idx] > 0:
                carry_spans[day_idx] -= 1
                continue
            cell = next(iterator, None)
            if not cell:
                continue
            rowspan = int(cell.get("rowspan", 1) or 1)
            if rowspan > 1:
                carry_spans[day_idx] = rowspan - 1
            text = cell.get("text", "").strip()
            if not text:
                continue
            parsed = parse_timetable_cell(text, weekday, week_label)
            if parsed:
                entries.append(parsed)
    return entries, weekdays


def header_key(value: str) -> str:
    text = clean_text(value).lower()
    mapping = {
        "activity": "title",
        "activity name": "title",
        "event": "title",
        "module code": "code",
        "module": "code",
        "module title": "title",
        "title": "title",
        "document": "title",
        "document type": "title",
        "file": "title",
        "date": "date",
        "period": "period",
        "academic year": "period",
        "quota": "quota",
        "status": "status",
        "venue": "venue",
        "deadline": "deadline",
    }
    return mapping.get(text, re.sub(r"[^a-z0-9]+", "_", text).strip("_") or "value")


def parse_html_tables(html_text: str, *, base_url: str = "") -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text or "", "html.parser")
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        headers = [clean_text(cell.get_text(" ", strip=True)) for cell in rows[0].find_all(["th", "td"])]
        if not any(headers):
            continue
        keys = [header_key(header) for header in headers]
        if len(set(keys)) < 2 and "title" not in keys and "code" not in keys:
            continue
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            values = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
            if not any(values):
                continue
            fingerprint = tuple(values[:6])
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            record: dict[str, Any] = {"cells": {}}
            href = None
            for cell in cells:
                anchor = cell.find("a", href=True)
                if anchor and not href:
                    href = urljoin(base_url, anchor["href"])
            for key, header, value in zip(keys, headers, values, strict=False):
                record["cells"][header] = value
                if key not in record or key in {"title", "code"}:
                    record[key] = value
            title = record.get("title") or values[0]
            code = record.get("code") or ""
            record["title"] = title
            record["id"] = item_id(code, title, record.get("date") or record.get("period") or "")
            if href:
                record["url"] = href
            records.append(record)
    return records


def parse_link_records(html_text: str, *, base_url: str = "") -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text or "", "html.parser")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base_url, anchor["href"])
        title = clean_text(anchor.get_text(" ", strip=True))
        if not title or href in seen:
            continue
        if href.startswith("javascript:"):
            continue
        lower = f"{title} {href}".lower()
        kind = classify_document(title, href)
        if kind or any(token in lower for token in (".pdf", "download", "成績", "學費", "transcript", "tuition")):
            seen.add(href)
            records.append(
                {
                    "id": item_id(kind or "doc", title),
                    "title": title,
                    "url": href,
                    "kind": kind,
                }
            )
    return records


def classify_document(title: str, url: str = "") -> str | None:
    blob = f"{title} {url}".lower()
    if any(marker.lower() in blob for marker in TRANSCRIPT_MARKERS):
        return "transcript"
    if any(marker.lower() in blob for marker in TUITION_MARKERS):
        return "tuition"
    return None


def filter_documents(records: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for record in records:
        guessed = record.get("kind") or classify_document(
            str(record.get("title") or ""),
            str(record.get("url") or ""),
        )
        if guessed == kind:
            item = dict(record)
            item["kind"] = kind
            matched.append(item)
    return matched


def match_activity(records: list[dict[str, Any]], activity_id: str) -> dict[str, Any] | None:
    wanted = activity_id.strip().lower()
    for record in records:
        if str(record.get("id") or "").lower() == wanted:
            return record
        if wanted and wanted in str(record.get("title") or "").lower():
            return record
    return None


def match_module(records: list[dict[str, Any]], code: str) -> dict[str, Any] | None:
    wanted = code.strip().lower()
    for record in records:
        if str(record.get("code") or "").lower() == wanted:
            return record
        if str(record.get("id") or "").lower() == wanted:
            return record
        if wanted and wanted in str(record.get("title") or "").lower():
            return record
    return None
