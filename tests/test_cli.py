import base64
import json

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


def test_parse_mobile_token_url_reads_moodle_redirect():
    raw = base64.b64encode(b"signature:::0123456789abcdef:::privatetoken").decode("ascii")
    token = parse_mobile_token_url(f"moodlevtc://token={raw}")
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
