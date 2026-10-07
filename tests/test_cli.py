import base64
import json

import pytest

from vtc.cli import main, redact_public_payload
from vtc.login_moodle import parse_mobile_token_url
from vtc.provenance import envelope
from vtc.sites import parse_site


def test_help_lists_login_moodle_and_myportal(capsys):
    code = main(["--help"])
    out = capsys.readouterr().out
    assert code == 0
    assert "login" in out
    assert "moodle" in out
    assert "myportal" in out


def test_moodle_commands_require_site(capsys):
    code = main(["moodle", "courses", "--json"])
    err = capsys.readouterr().err
    assert code != 0
    assert "site" in err.lower() or "required" in err.lower()


def test_login_requires_site():
    code = main(["login", "moodle", "--json"])
    assert code != 0


def test_courses_without_session_is_unverified_json(capsys):
    code = main(["moodle", "courses", "--site", "ay2627", "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert code == 2
    assert payload["ok"] is False
    assert payload["status"] == "unverified"
    assert payload["source"] == "unverified"
    assert payload["site"] == "ay2627"
    assert "captured_at" in payload
    blob = json.dumps(payload).lower()
    assert "password" not in blob
    assert "wstoken" not in blob
    assert "cookie" not in blob


def test_login_without_tty_does_not_prompt_visibly(monkeypatch, capsys):
    monkeypatch.setattr("vtc.secrets.stdin_is_tty", lambda: False)
    monkeypatch.setenv("VTC_STUDENT_ID", "testuser")
    code = main(["login", "moodle", "--site", "ay2526", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 4
    assert payload["status"] == "secret_input_blocked"
    assert "should-not-leak" not in captured.out
    assert "password" not in json.dumps(payload).lower() or "TTY" in payload.get("error", "")


def test_myportal_without_session_is_unverified_json(capsys):
    code = main(["myportal", "timetable", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["ok"] is False
    assert payload["status"] == "unverified"
    assert payload["command"] == "myportal.timetable"
    assert payload["site"] is None
    blob = json.dumps(payload).lower()
    assert "password" not in blob
    assert "cookie" not in blob


def test_myportal_does_not_require_moodle_site(capsys):
    code = main(["myportal", "activities", "--json"])
    err = capsys.readouterr()
    payload = json.loads(err.out)
    assert code == 2
    assert "site" not in (err.err or "").lower()
    assert payload["status"] == "unverified"


def test_myportal_apply_without_confirm_is_write_blocked(capsys):
    code = main(["myportal", "apply", "--id", "orientation-day", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 4
    assert payload["status"] == "write_blocked"
    assert payload["ok"] is False


def test_myportal_select_confirm_without_tty_is_write_blocked(monkeypatch, capsys):
    monkeypatch.setattr("vtc.cli.stdin_is_tty", lambda: False)
    code = main(["myportal", "select", "--code", "ITP3902", "--confirm", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 4
    assert payload["status"] == "write_blocked"


def test_login_myportal_without_tty_does_not_prompt(monkeypatch, capsys):
    monkeypatch.setattr("vtc.secrets.stdin_is_tty", lambda: False)
    monkeypatch.setenv("VTC_STUDENT_ID", "testuser")
    code = main(["login", "myportal", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 4
    assert payload["status"] == "secret_input_blocked"
    assert "password" not in json.dumps(payload).lower() or "TTY" in payload.get("error", "")


@pytest.mark.parametrize("scheme", ["moodlevtc", "moodlemobile"])
def test_parse_mobile_token_url_reads_moodle_redirect(scheme):
    raw = base64.b64encode(b"signature:::0123456789abcdef:::privatetoken").decode("ascii")
    token = parse_mobile_token_url(f"{scheme}://token={raw}")
    assert token == "0123456789abcdef"


def test_redact_strips_token_query_and_secret_keys():
    payload = envelope(
        command="moodle.sync",
        site="ay2627",
        source="moodle_rest",
        ok=True,
        status="ok",
        token="must-not-appear",
        url="https://moodle2627.vtc.edu.hk/webservice/pluginfile.php/1/file.pdf?token=abc",
    )
    clean = redact_public_payload(payload)
    assert "token" not in clean
    assert "token=abc" not in json.dumps(clean)


def test_site_mapping():
    assert parse_site("ay2526").host == "moodle2526.vtc.edu.hk"
    assert parse_site("ay2627").base_url.endswith("moodle2627.vtc.edu.hk")


def test_sync_command_returns_success_after_reading_course(monkeypatch, tmp_path, capsys):
    class CourseClient:
        site = parse_site("ay2627")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def find_course(self, _reference):
            return {"id": 1, "code": "ITP3902", "title": "Test course"}

        def course_page(self, _course):
            return "moodle_html", {"activities": []}

    monkeypatch.setattr("vtc.moodle.client.MoodleClient", lambda *_args: CourseClient())
    code = main([
        "moodle", "sync", "--site", "ay2627", "--course", "ITP3902",
        "--output", str(tmp_path / "course"), "--dry-run", "--json",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["source"] == "moodle_html"
    assert payload["dry_run"] is True
    assert not (tmp_path / "course").exists()


def test_redaction_removes_case_variants_and_preserves_safe_query():
    clean = redact_public_payload({
        "url": "https://example.test/file?WSTOKEN=private-value&forcedownload=1",
        "nested": ["https://example.test/file?sesskey=private-value&id=2"],
    })
    assert "private-value" not in json.dumps(clean)
    assert "forcedownload=1" in clean["url"]
    assert "id=2" in clean["nested"][0]


@pytest.mark.parametrize("command", ["activities", "modules", "transcript", "tuition"])
def test_myportal_unread_records_fail_even_if_partial_rows_exist(command, monkeypatch, capsys):
    class UnreadClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def activities(self):
            return {"found": False, "activities": [{"title": "Unverified"}]}

        def modules(self):
            return {"found": False, "modules": [{"code": "TEST"}]}

        def documents(self, _kind):
            return {"found": False, "documents": [{"title": "Unverified"}]}

    monkeypatch.setattr("vtc.myportal.session.MyPortalSession", UnreadClient)
    code = main(["myportal", command, "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["ok"] is False
    assert payload["status"] == "unverified"


def test_myportal_failed_download_is_unverified(monkeypatch, tmp_path, capsys):
    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def download_documents(self, _kind, _output):
            return {"found": True, "documents": [{"title": "Transcript", "path": None}]}

    monkeypatch.setattr("vtc.myportal.session.MyPortalSession", Client)
    code = main(["myportal", "transcript", "--output", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["status"] == "unverified"


def test_redaction_covers_credential_file_and_oauth_field_names():
    payload = {"totp_secret": "seed-value", "VTC_PASSWORD": "password-value", "VTC_TOTP_SECRET": "seed-env-value", "access_token": "access-value", "url": "https://example.invalid/cb?access_token=access-query&totp_secret=seed-query&id=2", "message": "password = inline-value"}
    clean = redact_public_payload(payload)
    blob = json.dumps(clean)
    assert clean["url"] == "https://example.invalid/cb?id=2"
    for value in ["seed-value", "password-value", "seed-env-value", "access-value", "access-query", "seed-query", "inline-value"]:
        assert value not in blob


def test_unread_moodle_assignments_do_not_report_success(monkeypatch, capsys):
    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def assignments(self, _course):
            return "moodle_html", []

    monkeypatch.setattr("vtc.moodle.client.MoodleClient", lambda *_args: Client())
    code = main(["moodle", "assignments", "--site", "ay2627", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["ok"] is False
    assert payload["status"] == "unverified"
