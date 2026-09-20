from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

COURSE_CODE_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{2,}\d{4}[A-Z]?)(?![A-Z0-9])")
SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")
SESSION_EXPIRED_MARKERS = (
    "log in to the site",
    "name=\"username\"",
    "name=\"password\"",
    'name="userid"',
    "forgot password",
)


def clean_text(value: str | None) -> str:
    return " ".join((value or "").replace("\xa0", " ").split())


def extract_course_code(value: str | None) -> str | None:
    match = COURSE_CODE_RE.search(value or "")
    return match.group(1).upper() if match else None


def parse_course_id(url: str | None) -> int | None:
    if not url:
        return None
    match = re.search(r"[?&]id=(\d+)", url)
    return int(match.group(1)) if match else None


def looks_like_login_page(html_text: str, url: str | None = None) -> bool:
    text = (html_text or "").lower()
    if url and "/login/" in url.lower():
        return True
    return any(marker in text for marker in SESSION_EXPIRED_MARKERS)


def extract_sesskey(html_text: str) -> str | None:
    match = re.search(r'["\']sesskey["\']\s*[:=]\s*["\']([^"\']+)["\']', html_text or "")
    return match.group(1) if match else None


def normalize_activity_title(value: str) -> str:
    text = clean_text(value)
    text = re.sub(r"^Select activity\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+(File|Folder|Assignment|Forum|URL|SCORM package)$", "", text)
    return text


def choose_activity_link(activity_tag: Any) -> str | None:
    anchors = activity_tag.select("a[href]")
    if not anchors:
        return None
    hrefs = []
    for anchor in anchors:
        href = anchor.get("href")
        if not href or href.startswith("#"):
            continue
        hrefs.append(href)
    for href in hrefs:
        if "/mod/" in href or "pluginfile.php" in href:
            return href
    return hrefs[0] if hrefs else None


def parse_course_list(html_text: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html_text, "html.parser")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href*='/course/view.php']"):
        href = anchor.get("href") or ""
        title = clean_text(anchor.get_text(" ", strip=True))
        course_id = parse_course_id(href)
        if not href or not title or course_id is None:
            continue
        if href in seen:
            continue
        seen.add(href)
        rows.append(
            {
                "id": course_id,
                "code": extract_course_code(title),
                "title": title,
                "url": href,
            }
        )
    return rows


def parse_course_page(html_text: str, course_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html_text, "html.parser")
    page_title = clean_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
    raw_title = page_title
    title_match = re.search(r"Course:\s*(.*?)\s*\|\s*VTC Moodle", page_title)
    if title_match:
        raw_title = title_match.group(1).strip()

    teacher = None
    if " by " in raw_title:
        raw_without_teacher, teacher = raw_title.rsplit(" by ", 1)
    else:
        raw_without_teacher = raw_title

    sections: list[dict[str, Any]] = []
    activities: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()

    for section_index, section_tag in enumerate(soup.select("li.section.course-section"), start=1):
        heading = section_tag.select_one(".sectionname, h3, h4")
        section_name = clean_text(heading.get_text(" ", strip=True)) if heading else f"Section {section_index}"
        for activity_tag in section_tag.select("li.activity"):
            classes = activity_tag.get("class", [])
            modtype = next(
                (part.replace("modtype_", "") for part in classes if part.startswith("modtype_")),
                "unknown",
            )
            counts[modtype] += 1
            title_tag = activity_tag.select_one(".instancename")
            raw_activity_title = (
                title_tag.get_text(" ", strip=True)
                if title_tag
                else activity_tag.get_text(" ", strip=True)
            )
            title = normalize_activity_title(raw_activity_title)
            url = choose_activity_link(activity_tag)
            activity = {
                "section": section_name,
                "modtype": modtype,
                "title": title,
                "url": url,
            }
            activities.append(activity)
        sections.append({"name": section_name, "activities": []})

    # Keep activities as a flat list; sections retain names for grouping.
    grouped: dict[str, list[dict[str, Any]]] = {section["name"]: [] for section in sections}
    for activity in activities:
        grouped.setdefault(activity["section"], []).append(activity)
    for section in sections:
        section["activities"] = grouped.get(section["name"], [])

    return {
        "page_title": page_title,
        "raw_title": raw_title,
        "raw_title_without_teacher": raw_without_teacher,
        "teacher_from_title": teacher,
        "course_code": extract_course_code(raw_without_teacher),
        "course_url": course_url,
        "course_id": parse_course_id(course_url),
        "section_count": len(sections),
        "activity_count": len(activities),
        "activity_types": dict(sorted(counts.items())),
        "sections": sections,
        "activities": activities,
    }


def parse_assignment_page(html_text: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html_text, "html.parser")
    title = clean_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
    fields: list[dict[str, str]] = []
    due = None
    opened = None
    for row in soup.select("table tr"):
        cells = row.select("th, td")
        if len(cells) < 2:
            continue
        label = clean_text(cells[0].get_text(" ", strip=True))
        value = clean_text(cells[1].get_text(" ", strip=True))
        if not label or not value:
            continue
        lowered = label.lower()
        if any(
            marker in lowered
            for marker in (
                "opened",
                "due",
                "allow submissions",
                "cut-off",
                "remaining",
                "submission status",
                "grading status",
            )
        ):
            fields.append({"label": label, "value": value})
            if "due" in lowered and due is None:
                due = value
            if "opened" in lowered and opened is None:
                opened = value
    intro_tag = soup.select_one(".intro, #intro, .activity-description")
    intro_text = clean_text(intro_tag.get_text(" ", strip=True)) if intro_tag else None
    return {
        "title": title,
        "url": url,
        "due": due,
        "opened": opened,
        "fields": fields,
        "intro_excerpt": intro_text[:1000] if intro_text else None,
    }


def pluginfile_links(html_text: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html_text, "html.parser")
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.select('a[href*="pluginfile.php"]'):
        href = anchor.get("href")
        if not href or href in seen:
            continue
        seen.add(href)
        links.append({"title": clean_text(anchor.get_text(" ", strip=True)), "url": href})
    return links


def safe_filename(value: str) -> str:
    cleaned = SAFE_FILENAME_RE.sub("_", value.strip())
    cleaned = cleaned.strip(" ._")
    return cleaned or "moodle_file"


def filename_from_headers(content_disposition: str | None, url: str, fallback: str) -> str:
    if content_disposition:
        match = re.search(
            r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', content_disposition, re.IGNORECASE
        )
        if match:
            return safe_filename(unquote(match.group(1)))
    name = PathName(url)
    if name:
        return safe_filename(name)
    return safe_filename(fallback)


def PathName(url: str) -> str:
    return unquote(urlparse(url).path.rsplit("/", 1)[-1])


def is_syncable_modtype(modtype: str) -> bool:
    return modtype in {"resource", "folder", "assign"}
