import json

import httpx
import pytest

from vtc.errors import VtcError
from vtc.moodle.client import MoodleClient
from vtc.moodle.sync import _download_if_changed, _strip_token_query, sync_course
from vtc.sites import parse_site


class _Site:
    key = "ay2627"


class _SyncClient:
    site = _Site()

    def __init__(self, transport):
        self.http = httpx.Client(transport=transport)

    def find_course(self, course_ref):
        return {"id": 7, "code": course_ref, "title": "Test course", "url": "https://moodle2627.vtc.edu.hk/course/view.php?id=7"}

    def get_file(self, url):
        return self.http.get(url)

    def course_page(self, course):
        return (
            "moodle_rest",
            {
                "activities": [
                    {
                        "title": "Week 1",
                        "modtype": "resource",
                        "files": [
                            {"title": "notes.pdf", "url": "https://moodle2627.vtc.edu.hk/webservice/pluginfile.php/1/notes.pdf"},
                            {"title": "notes.pdf", "url": "https://moodle2627.vtc.edu.hk/webservice/pluginfile.php/2/notes.pdf"},
                        ],
                    }
                ]
            },
        )


def test_sync_skips_unchanged_file(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    target = output / "week1.pdf"
    target.write_bytes(b"hello")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"hello", headers={"content-type": "application/pdf"})
        )
    )
    result = _download_if_changed(
        client,
        "https://example.test/week1.pdf",
        output,
        "week1.pdf",
        {"files": {"week1.pdf": {"size": 5}}},
    )
    assert result["action"] == "skipped"
    assert result["reason"] == "unchanged"


def test_sync_downloads_when_size_changes(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    (output / "week1.pdf").write_bytes(b"old")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"newer", headers={"content-type": "application/pdf"})
        )
    )
    result = _download_if_changed(
        client,
        "https://example.test/week1.pdf",
        output,
        "week1.pdf",
        {"files": {"week1.pdf": {"size": 3}}},
    )
    assert result["action"] == "downloaded"
    assert (output / "week1.pdf").read_bytes() == b"newer"


def test_sync_downloads_when_contents_change_without_size_change(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    (output / "week1.pdf").write_bytes(b"old!!")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"new!!", headers={"content-type": "application/pdf"})
        )
    )

    result = _download_if_changed(
        client,
        "https://example.test/week1.pdf",
        output,
        "week1.pdf",
        {"files": {"week1.pdf": {"size": 5}}},
    )

    assert result["action"] == "downloaded"
    assert (output / "week1.pdf").read_bytes() == b"new!!"


def test_sync_rejects_html_error_body_for_non_html_attachment(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    target = output / "notes.pdf"
    target.write_bytes(b"original pdf")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text="<html><h1>Scheduled maintenance</h1></html>",
            )
        )
    )

    with pytest.raises(VtcError):
        _download_if_changed(
            client,
            "https://moodle2627.vtc.edu.hk/webservice/pluginfile.php/1/notes.pdf",
            output,
            "notes.pdf",
            {"files": {"notes.pdf": {"size": 12}}},
        )

    assert target.read_bytes() == b"original pdf"


def test_sync_allows_html_attachment(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    body = b"<html><h1>Lesson notes</h1></html>"
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=body,
                headers={"content-type": "text/html; charset=utf-8"},
            )
        )
    )

    result = _download_if_changed(
        client,
        "https://moodle2627.vtc.edu.hk/webservice/pluginfile.php/1/lesson.html",
        output,
        "lesson.html",
        {"files": {}},
    )

    assert result["action"] == "downloaded"
    assert (output / "lesson.html").read_bytes() == body


def test_sync_discovers_and_downloads_html_resource(tmp_path):
    body = b"<html><h1>Lesson notes</h1></html>"

    def respond(request):
        if "/mod/resource/" in request.url.path:
            return httpx.Response(
                302,
                headers={
                    "location": "https://moodle2627.vtc.edu.hk/pluginfile.php/1/lesson.html"
                },
            )
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/html; charset=utf-8"},
        )

    client = object.__new__(MoodleClient)
    client.site = parse_site("ay2627")
    client._wstoken = None
    client.http = httpx.Client(
        transport=httpx.MockTransport(respond),
        follow_redirects=True,
    )
    client.find_course = lambda course_ref: {
        "id": 7,
        "code": course_ref,
        "title": "Test course",
        "url": "https://moodle2627.vtc.edu.hk/course/view.php?id=7",
    }
    client.course_page = lambda course: (
        "moodle_html",
        {
            "activities": [
                {
                    "title": "Lesson notes",
                    "modtype": "resource",
                    "url": "https://moodle2627.vtc.edu.hk/mod/resource/view.php?id=1",
                    "files": [],
                }
            ]
        },
    )

    result = sync_course(client, "ITP3902", tmp_path / "out", extract=False)

    assert [item["filename"] for item in result["downloaded"]] == ["lesson.html"]
    assert (tmp_path / "out" / "lesson.html").read_bytes() == body


def test_sync_keeps_same_named_attachments_distinct_and_stable(tmp_path):
    def respond(request):
        return httpx.Response(
            200,
            content=b"first" if "/1/" in request.url.path else b"second",
            headers={"content-type": "application/pdf"},
        )

    output = tmp_path / "out"
    client = _SyncClient(httpx.MockTransport(respond))

    first = sync_course(client, "ITP3902", output, extract=False)
    second = sync_course(client, "ITP3902", output, extract=False)

    assert [item["filename"] for item in first["downloaded"]] == ["notes.pdf", "notes (2).pdf"]
    assert (output / "notes.pdf").read_bytes() == b"first"
    assert (output / "notes (2).pdf").read_bytes() == b"second"
    assert [item["filename"] for item in second["skipped"]] == ["notes.pdf", "notes (2).pdf"]


def test_sync_does_not_overwrite_untracked_existing_file(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    (output / "notes.pdf").write_bytes(b"personal copy")
    client = _SyncClient(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=b"moodle copy",
                headers={"content-type": "application/pdf"},
            )
        )
    )

    result = sync_course(client, "ITP3902", output, extract=False)

    assert (output / "notes.pdf").read_bytes() == b"personal copy"
    assert [item["filename"] for item in result["downloaded"]] == ["notes (2).pdf", "notes (3).pdf"]


def test_sync_discovery_http_failure_does_not_rewrite_manifest(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    manifest_path = output / ".vtc-sync.json"
    original_manifest = {
        "files": {
            "notes.pdf": {
                "url": "https://moodle2627.vtc.edu.hk/pluginfile.php/1/notes.pdf",
                "size": 5,
            }
        }
    }
    manifest_path.write_text(json.dumps(original_manifest), encoding="utf-8")
    client = object.__new__(MoodleClient)
    client.site = parse_site("ay2627")
    client.http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                503,
                text="<html><h1>Maintenance</h1></html>",
                headers={"content-type": "text/html"},
            )
        ),
        follow_redirects=True,
    )
    client.find_course = lambda course_ref: {
        "id": 7,
        "code": course_ref,
        "title": "Test course",
        "url": "https://moodle2627.vtc.edu.hk/course/view.php?id=7",
    }
    client.course_page = lambda course: (
        "moodle_html",
        {
            "activities": [
                {
                    "title": "Week 1",
                    "modtype": "resource",
                    "url": "https://moodle2627.vtc.edu.hk/mod/resource/view.php?id=1",
                    "files": [],
                }
            ]
        },
    )

    with pytest.raises(VtcError):
        sync_course(client, "ITP3902", output, extract=False)

    assert json.loads(manifest_path.read_text(encoding="utf-8")) == original_manifest


def test_strip_token_query_does_not_keep_wstoken():
    url = "https://moodle2627.vtc.edu.hk/webservice/pluginfile.php/1/mod_resource/content/0/a.pdf?token=secret&forcedownload=1"
    assert "token=" not in _strip_token_query(url)
    assert _strip_token_query(url).endswith("/a.pdf?forcedownload=1")
