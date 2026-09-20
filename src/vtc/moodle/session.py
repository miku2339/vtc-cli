from __future__ import annotations

import json
from pathlib import Path

import httpx

from vtc.moodle.rest import USER_AGENT


def load_storage_cookies(path: Path) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cookies = payload.get("cookies") or []
    if not isinstance(cookies, list):
        return []
    return [cookie for cookie in cookies if isinstance(cookie, dict) and cookie.get("name")]


def http_client_from_storage(path: Path) -> httpx.Client:
    client = httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=60.0,
    )
    for cookie in load_storage_cookies(path):
        client.cookies.set(
            cookie["name"],
            cookie.get("value") or "",
            domain=cookie.get("domain") or None,
            path=cookie.get("path") or "/",
        )
    return client
