from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx

from vtc.errors import MissingSession
from vtc.moodle import html as moodle_html
from vtc.moodle import rest as moodle_rest
from vtc.moodle.session import http_client_from_storage
from vtc.paths import rest_status_path, session_path
from vtc.secrets import SecretStore, default_store, resolve_wstoken
from vtc.sites import MoodleSite, parse_site


class MoodleClient:
    def __init__(self, site: MoodleSite | str, *, store: SecretStore | None = None) -> None:
        self.site = site if isinstance(site, MoodleSite) else parse_site(site)
        self.session_file = session_path(self.site.key)
        if not self.session_file.exists():
            raise MissingSession(
                f"No Moodle session for {self.site.key}. "
                f"Run `vtc login moodle --site {self.site.key}` in a local terminal."
            )
        self.store = store or default_store()
        self.http = http_client_from_storage(self.session_file)
        self._wstoken = resolve_wstoken(self.store, self.site.key)
        self._rest_ok: bool | None = None
        self._userid: int | None = None

    def close(self) -> None:
        self.http.close()
        self._wstoken = None

    def __enter__(self) -> MoodleClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def rest_status(self) -> str:
        path = rest_status_path(self.site.key)
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("rest") == "available" and self._wstoken:
                    return "available"
            except Exception:
                pass
        if self._probe_rest():
            return "available"
        return "unavailable"

    def _probe_rest(self) -> bool:
        if self._rest_ok is not None:
            return self._rest_ok
        if not self._wstoken:
            self._rest_ok = False
            return False
        try:
            info = moodle_rest.site_info(self.http, self.site, self._wstoken)
            self._userid = int(info["userid"])
            self._rest_ok = True
            return True
        except Exception:
            self._rest_ok = False
            return False

    def _get(self, path: str) -> httpx.Response:
        response = self.http.get(self.site.url(path))
        if moodle_html.looks_like_login_page(response.text, str(response.url)):
            raise MissingSession(
                f"Moodle session for {self.site.key} expired. "
                f"Run `vtc login moodle --site {self.site.key}` in a local terminal."
            )
        return response

    def courses(self) -> tuple[str, list[dict[str, Any]]]:
        if self._probe_rest() and self._wstoken and self._userid is not None:
            rows = moodle_rest.enrolled_courses(self.http, self.site, self._wstoken, self._userid)
            for row in rows:
                row["code"] = moodle_html.extract_course_code(row.get("title") or "") or moodle_html.extract_course_code(
                    str(row.get("shortname") or "")
                )
                row["source"] = "moodle_rest"
            return "moodle_rest", rows

        ajax_rows = self._courses_via_sesskey()
        if ajax_rows:
            for row in ajax_rows:
                row["source"] = "moodle_html"
            return "moodle_html", ajax_rows

        response = self._get("/my/courses.php")
        rows = moodle_html.parse_course_list(response.text)
        for row in rows:
            row["source"] = "moodle_html"
        return "moodle_html", rows

    def _courses_via_sesskey(self) -> list[dict[str, Any]]:
        home = self._get("/my/courses.php")
        sesskey = moodle_html.extract_sesskey(home.text)
        if not sesskey:
            return []
        payload = [
            {
                "index": 0,
                "methodname": "core_course_get_enrolled_courses_by_timeline_classification",
                "args": {
                    "classification": "inprogress",
                    "limit": 0,
                    "offset": 0,
                    "sort": "fullname",
                },
            }
        ]
        try:
            response = self.http.post(
                self.site.url(f"/lib/ajax/service.php?sesskey={sesskey}"),
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            body = response.json()
        except Exception:
            return []
        if not isinstance(body, list) or not body:
            return []
        first = body[0]
        if not isinstance(first, dict) or first.get("error"):
            return []
        data = first.get("data") or {}
        courses = data.get("courses") if isinstance(data, dict) else None
        if not isinstance(courses, list):
            return []
        rows = []
        for item in courses:
            if not isinstance(item, dict):
                continue
            course_id = item.get("id")
            title = str(item.get("fullname") or item.get("shortname") or "")
            if not course_id or not title:
                continue
            rows.append(
                {
                    "id": int(course_id),
                    "code": moodle_html.extract_course_code(title)
                    or moodle_html.extract_course_code(str(item.get("shortname") or "")),
                    "title": title,
                    "url": self.site.url(f"/course/view.php?id={course_id}"),
                }
            )
        return rows

    def find_course(self, course_ref: str) -> dict[str, Any]:
        source, courses = self.courses()
        query = (course_ref or "").strip()
        if not query:
            raise ValueError("course is required")
        query_upper = query.upper()
        fallback = None
        for row in courses:
            if query.isdigit() and int(row.get("id") or 0) == int(query):
                row = dict(row)
                row["source"] = source
                return row
            code = (row.get("code") or "").upper()
            title = str(row.get("title") or "")
            if code == query_upper:
                row = dict(row)
                row["source"] = source
                return row
            if query.lower() in title.lower():
                fallback = dict(row)
                fallback["source"] = source
        if fallback:
            return fallback
        raise ValueError(f"course not found: {course_ref}")

    def course_page(self, course: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if self._probe_rest() and self._wstoken:
            contents = moodle_rest.course_contents(self.http, self.site, self._wstoken, int(course["id"]))
            parsed = _contents_to_activities(contents, self.site, int(course["id"]))
            parsed["course_url"] = course.get("url")
            parsed["course_code"] = course.get("code") or parsed.get("course_code")
            parsed["raw_title"] = course.get("title")
            return "moodle_rest", parsed
        response = self.http.get(course["url"])
        if moodle_html.looks_like_login_page(response.text, str(response.url)):
            raise MissingSession(
                f"Moodle session for {self.site.key} expired. "
                f"Run `vtc login moodle --site {self.site.key}` in a local terminal."
            )
        parsed = moodle_html.parse_course_page(response.text, course["url"])
        return "moodle_html", parsed

    def assignments(self, course_ref: str | None = None) -> tuple[str, list[dict[str, Any]]]:
        source, courses = self.courses()
        if course_ref:
            selected = [self.find_course(course_ref)]
            source = selected[0].get("source") or source
        else:
            selected = courses
        if self._probe_rest() and self._wstoken:
            ids = [int(row["id"]) for row in selected if row.get("id")]
            rows = moodle_rest.assignments(self.http, self.site, self._wstoken, ids)
            for row in rows:
                row["due"] = _format_timestamp(row.get("due_timestamp"))
                row["source"] = "moodle_rest"
                row["code"] = moodle_html.extract_course_code(str(row.get("course_title") or ""))
            return "moodle_rest", rows

        results: list[dict[str, Any]] = []
        for course in selected:
            page_source, parsed = self.course_page(course)
            for activity in parsed.get("activities") or []:
                if activity.get("modtype") != "assign" or not activity.get("url"):
                    continue
                response = self.http.get(activity["url"])
                detail = moodle_html.parse_assignment_page(response.text, activity["url"])
                results.append(
                    {
                        "course_id": course.get("id"),
                        "course_title": course.get("title"),
                        "code": course.get("code") or parsed.get("course_code"),
                        "title": activity.get("title") or detail.get("title"),
                        "url": activity.get("url"),
                        "due": detail.get("due"),
                        "opened": detail.get("opened"),
                        "fields": detail.get("fields"),
                        "source": page_source,
                    }
                )
        return "moodle_html", results


def _format_timestamp(value: Any) -> str | None:
    if not value:
        return None
    try:
        stamp = int(value)
    except (TypeError, ValueError):
        return None
    if stamp <= 0:
        return None
    return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat()


def _contents_to_activities(contents: list[dict[str, Any]], site: MoodleSite, course_id: int) -> dict[str, Any]:
    activities: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    for section in contents:
        if not isinstance(section, dict):
            continue
        name = str(section.get("name") or "Section")
        section_activities = []
        for module in section.get("modules") or []:
            if not isinstance(module, dict):
                continue
            modtype = str(module.get("modname") or "unknown")
            url = module.get("url")
            files = []
            for item in module.get("contents") or []:
                if isinstance(item, dict) and item.get("fileurl"):
                    files.append(
                        {
                            "title": item.get("filename"),
                            "url": item.get("fileurl"),
                            "size": item.get("filesize"),
                            "timemodified": item.get("timemodified"),
                        }
                    )
            activity = {
                "section": name,
                "modtype": modtype,
                "title": module.get("name"),
                "url": url,
                "files": files,
            }
            section_activities.append(activity)
            activities.append(activity)
        sections.append({"name": name, "activities": section_activities})
    return {
        "course_id": course_id,
        "course_url": site.url(f"/course/view.php?id={course_id}"),
        "section_count": len(sections),
        "activity_count": len(activities),
        "sections": sections,
        "activities": activities,
    }
