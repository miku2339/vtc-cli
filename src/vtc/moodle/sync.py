from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from vtc.moodle.client import MoodleClient
from vtc.moodle.extract import extract_file
from vtc.moodle.html import filename_from_headers, is_syncable_modtype, pluginfile_links, safe_filename
from vtc.paths import ensure_private_file
from vtc.provenance import utc_now


def sync_course(
    client: MoodleClient,
    course_ref: str,
    output: Path,
    *,
    dry_run: bool = False,
    extract: bool = True,
) -> dict[str, Any]:
    course = client.find_course(course_ref)
    source, parsed = client.course_page(course)
    output = output.expanduser().resolve()
    if not dry_run:
        output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / ".vtc-sync.json"
    manifest = _read_manifest(manifest_path)

    planned: list[dict[str, Any]] = []
    downloaded: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []

    for activity in parsed.get("activities") or []:
        modtype = activity.get("modtype") or ""
        if not is_syncable_modtype(modtype):
            continue
        files = list(activity.get("files") or [])
        if not files and activity.get("url") and modtype in {"resource", "folder", "assign"}:
            files.extend(_discover_files(client.http, activity))
        if modtype == "assign":
            assignments.append(
                {
                    "title": activity.get("title"),
                    "url": activity.get("url"),
                    "source": source,
                }
            )
        for file_info in files:
            url = file_info.get("url")
            if not url:
                continue
            name = safe_filename(str(file_info.get("title") or activity.get("title") or "file"))
            record = {
                "activity_title": activity.get("title"),
                "modtype": modtype,
                "url": _strip_token_query(url) if source == "moodle_rest" else url,
                "filename": name,
                "source": source,
            }
            planned.append(record)
            if dry_run:
                continue
            result = _download_if_changed(client.http, url, output, name, manifest)
            result.update(record)
            if extract:
                _attach_extract(result, write_sidecar=not dry_run)
            if result.get("action") == "skipped":
                skipped.append(result)
            else:
                downloaded.append(result)

    if not dry_run:
        manifest["captured_at"] = utc_now()
        manifest["site"] = client.site.key
        manifest["course"] = {
            "id": course.get("id"),
            "code": course.get("code") or parsed.get("course_code"),
            "title": course.get("title"),
            "url": course.get("url"),
        }
        manifest["files"] = {
            item["filename"]: {"url": item.get("url"), "size": item.get("size")}
            for item in downloaded + skipped
            if item.get("filename")
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        ensure_private_file(manifest_path)

    return {
        "course": {
            "id": course.get("id"),
            "code": course.get("code") or parsed.get("course_code"),
            "title": course.get("title") or parsed.get("raw_title"),
            "url": course.get("url"),
            "source": source,
        },
        "source": source,
        "dry_run": dry_run,
        "planned": planned,
        "downloaded": downloaded,
        "skipped": skipped,
        "assignments": assignments,
        "output": str(output),
        "extract": extract,
    }


def _discover_files(http: httpx.Client, activity: dict[str, Any]) -> list[dict[str, str]]:
    url = activity.get("url")
    if not url:
        return []
    response = http.get(url, follow_redirects=True)
    content_type = response.headers.get("content-type", "")
    disposition = response.headers.get("content-disposition", "")
    if "pluginfile.php" in str(response.url) or "attachment" in disposition.lower() or "text/html" not in content_type:
        name = filename_from_headers(disposition, str(response.url), str(activity.get("title") or "file"))
        return [{"title": name, "url": str(response.url)}]
    return pluginfile_links(response.text)


def _download_if_changed(
    http: httpx.Client,
    url: str,
    output: Path,
    filename: str,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    target = output / filename
    response = http.get(url, follow_redirects=True)
    response.raise_for_status()
    if "text/html" in response.headers.get("content-type", "") and "pluginfile.php" not in str(response.url):
        return {"action": "skipped", "reason": "html_not_file", "filename": filename, "size": None}
    body = response.content
    size = len(body)
    previous = (manifest.get("files") or {}).get(filename) or {}
    if target.exists() and target.stat().st_size == size and previous.get("size") == size:
        return {"action": "skipped", "reason": "unchanged", "filename": filename, "size": size, "path": str(target)}
    target.write_bytes(body)
    return {"action": "downloaded", "filename": filename, "size": size, "path": str(target)}


def _attach_extract(result: dict[str, Any], *, write_sidecar: bool) -> None:
    path_value = result.get("path")
    if not path_value:
        return
    path = Path(path_value)
    extracted = extract_file(path)
    result["extract_status"] = extracted.status
    result["extractor"] = extracted.extractor
    result["excerpt"] = extracted.excerpt
    result["key_lines"] = extracted.key_lines
    if extracted.error:
        result["extract_error"] = extracted.error
    if write_sidecar and extracted.text:
        sidecar = path.with_name(f"{path.name}.extracted.txt")
        sidecar.write_text(extracted.text, encoding="utf-8")
        result["extracted_text_path"] = str(sidecar)


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"files": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"files": {}}
    if not isinstance(payload, dict):
        return {"files": {}}
    payload.setdefault("files", {})
    return payload


def _strip_token_query(url: str) -> str:
    parsed = urlparse(url)
    if "token=" in (parsed.query or ""):
        return parsed._replace(query="").geturl()
    return url
