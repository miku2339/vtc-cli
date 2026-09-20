from __future__ import annotations

import json
from typing import Any

import httpx

from vtc.sites import MoodleSite

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class RestUnavailable(Exception):
    def __init__(self, message: str = "Moodle REST is unavailable") -> None:
        super().__init__(message)


def rest_call(
    client: httpx.Client,
    site: MoodleSite,
    token: str,
    function: str,
    params: dict[str, Any] | None = None,
) -> Any:
    data: dict[str, Any] = {
        "wstoken": token,
        "wsfunction": function,
        "moodlewsrestformat": "json",
    }
    if params:
        data.update(_flatten_params(params))
    response = client.post(site.url("/webservice/rest/server.php"), data=data)
    response.raise_for_status()
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise RestUnavailable("Moodle REST returned non-JSON") from exc
    if isinstance(payload, dict) and payload.get("exception"):
        raise RestUnavailable(str(payload.get("errorcode") or "rest_exception"))
    return payload


def _flatten_params(params: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in params.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            flat.update(_flatten_params(value, name))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                item_name = f"{name}[{index}]"
                if isinstance(item, dict):
                    flat.update(_flatten_params(item, item_name))
                else:
                    flat[item_name] = item
        else:
            flat[name] = value
    return flat


def site_info(client: httpx.Client, site: MoodleSite, token: str) -> dict[str, Any]:
    payload = rest_call(client, site, token, "core_webservice_get_site_info")
    if not isinstance(payload, dict) or not payload.get("userid"):
        raise RestUnavailable("site_info_missing")
    return payload


def enrolled_courses(client: httpx.Client, site: MoodleSite, token: str, userid: int) -> list[dict[str, Any]]:
    payload = rest_call(
        client,
        site,
        token,
        "core_enrol_get_users_courses",
        {"userid": userid},
    )
    if not isinstance(payload, list):
        raise RestUnavailable("courses_missing")
    courses = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        course_id = item.get("id")
        fullname = str(item.get("fullname") or item.get("shortname") or "")
        if not course_id:
            continue
        courses.append(
            {
                "id": int(course_id),
                "code": None,
                "title": fullname,
                "shortname": item.get("shortname"),
                "url": site.url(f"/course/view.php?id={course_id}"),
            }
        )
    return courses


def course_contents(client: httpx.Client, site: MoodleSite, token: str, course_id: int) -> list[dict[str, Any]]:
    payload = rest_call(
        client,
        site,
        token,
        "core_course_get_contents",
        {"courseid": course_id},
    )
    if not isinstance(payload, list):
        raise RestUnavailable("contents_missing")
    return payload


def assignments(client: httpx.Client, site: MoodleSite, token: str, course_ids: list[int]) -> list[dict[str, Any]]:
    payload = rest_call(
        client,
        site,
        token,
        "mod_assign_get_assignments",
        {"courseids": course_ids},
    )
    if not isinstance(payload, dict):
        raise RestUnavailable("assignments_missing")
    rows: list[dict[str, Any]] = []
    for course in payload.get("courses") or []:
        if not isinstance(course, dict):
            continue
        for item in course.get("assignments") or []:
            if not isinstance(item, dict):
                continue
            duedate = item.get("duedate") or None
            rows.append(
                {
                    "course_id": course.get("id"),
                    "course_title": course.get("fullname"),
                    "id": item.get("id"),
                    "title": item.get("name"),
                    "due_timestamp": duedate if duedate else None,
                    "url": site.url(f"/mod/assign/view.php?id={item.get('cmid')}")
                    if item.get("cmid")
                    else None,
                }
            )
    return rows
