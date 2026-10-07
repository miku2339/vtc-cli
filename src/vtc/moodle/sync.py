from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlparse

import httpx

from vtc.errors import MissingSession, VtcError
from vtc.moodle import html as moodle_html
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
    reserved_filenames: set[str] = set()

    for activity in parsed.get("activities") or []:
        modtype = activity.get("modtype") or ""
        if not is_syncable_modtype(modtype):
            continue
        files = list(activity.get("files") or [])
        if not files and activity.get("url") and modtype in {"resource", "folder", "assign"}:
            files.extend(_discover_files(client, activity))
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
            public_url = _strip_token_query(url)
            name = _choose_filename(
                safe_filename(str(file_info.get("title") or activity.get("title") or "file")),
                public_url,
                output,
                manifest,
                reserved_filenames,
            )
            reserved_filenames.add(name)
            record = {
                "activity_title": activity.get("title"),
                "modtype": modtype,
                "url": public_url,
                "filename": name,
                "source": source,
            }
            planned.append(record)
            if dry_run:
                continue
            result = _download_if_changed(
                client.http,
                url,
                output,
                name,
                manifest,
                request=client.get_file,
            )
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


def _discover_files(client: MoodleClient, activity: dict[str, Any]) -> list[dict[str, str]]:
    url = activity.get("url")
    if not url:
        return []
    response = client._get(url, allow_file=True)
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
    *,
    request: Callable[[str], httpx.Response] | None = None,
) -> dict[str, Any]:
    target = output / filename
    response = request(url) if request else http.get(url, follow_redirects=True)
    response.raise_for_status()
    if moodle_html.looks_like_html_document(
        response.headers.get("content-type", ""), response.content
    ):
        if moodle_html.looks_like_login_page(response.text, str(response.url)):
            raise MissingSession("Moodle session expired while downloading a file.")
        if Path(filename).suffix.lower() not in {".htm", ".html"}:
            raise VtcError(
                f"Moodle returned HTML instead of the expected file: {filename}",
                status="unverified",
            )
    body = response.content
    size = len(body)
    if target.exists() and target.read_bytes() == body:
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


def _choose_filename(
    filename: str,
    url: str,
    output: Path,
    manifest: dict[str, Any],
    reserved: set[str],
) -> str:
    files = manifest.get("files") or {}
    for existing_name, details in files.items():
        if (
            existing_name not in reserved
            and isinstance(details, dict)
            and _strip_token_query(str(details.get("url") or "")) == url
        ):
            return existing_name

    path = Path(filename)
    stem = path.stem or "moodle_file"
    suffix = path.suffix
    index = 1
    while True:
        candidate = filename if index == 1 else f"{stem} ({index}){suffix}"
        if candidate not in reserved and candidate not in files and not (output / candidate).exists():
            return candidate
        index += 1


def _strip_token_query(url: str) -> str:
    parsed = urlparse(url)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key.lower() != "token"]
    return parsed._replace(query=urlencode(query, doseq=True)).geturl()
