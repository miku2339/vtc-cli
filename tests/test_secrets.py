import os

import pytest

from vtc.errors import SecretInputError
from vtc.secrets import (
    MemoryStore,
    pop_secret_env,
    resolve_password,
    sanitized_environ,
)


def test_sanitized_environ_drops_secrets_and_keeps_safe_keys():
    env = {
        "PATH": "/usr/bin",
        "HOME": "/tmp",
        "VTC_PASSWORD": "should-not-leak",
        "VTC_TOTP_SECRET": "SHOULDNOTLEAK",
        "VTC_MOODLE_WSTOKEN": "token-leak",
        "VTC_STUDENT_ID": "testuser",
        "VTC_DATA_DIR": "/tmp/vtc-data",
        "UNRELATED": "ok",
    }
    cleaned = sanitized_environ(env)
    assert "VTC_PASSWORD" not in cleaned
    assert "VTC_TOTP_SECRET" not in cleaned
    assert "VTC_MOODLE_WSTOKEN" not in cleaned
    assert cleaned["VTC_STUDENT_ID"] == "testuser"
    assert cleaned["PATH"] == "/usr/bin"
    assert cleaned["UNRELATED"] == "ok"


def test_resolve_password_uses_env_then_removes_it():
    env = {"VTC_PASSWORD": "once-only"}
    store = MemoryStore()
    secret = resolve_password(store, env)
    assert secret == "once-only"
    assert "VTC_PASSWORD" not in env
    assert pop_secret_env("VTC_PASSWORD", env) is None


def test_resolve_password_refuses_non_tty_without_secret(monkeypatch):
    monkeypatch.setattr("vtc.secrets.stdin_is_tty", lambda: False)

    def fake_getpass(_prompt: str) -> str:
        raise AssertionError("getpass must not run when stdin is not a TTY")

    monkeypatch.setattr("vtc.secrets.getpass", fake_getpass)
    with pytest.raises(SecretInputError, match="not a TTY"):
        resolve_password(MemoryStore(), {})


def test_markdown_credentials_keep_secrets_private(tmp_path):
    from vtc.secrets import read_credentials_file

    path = tmp_path / "credentials.md"
    path.write_text(
        "# VTC\naccount: testuser\npassword: test:password\ntotp_secret: JBSWY3DPEHPK3PXP\n",
        encoding="utf-8",
    )
    credentials = read_credentials_file(path)
    assert credentials.account == "testuser"
    assert credentials.password == "test:password"
    assert credentials.totp_secret == "JBSWY3DPEHPK3PXP"
    assert "test:password" not in repr(credentials)
    assert "JBSWY3DPEHPK3PXP" not in repr(credentials)
    assert path.stat().st_mode & 0o777 == 0o600


def test_markdown_credentials_errors_do_not_expose_contents(tmp_path):
    from vtc.secrets import read_credentials_file

    path = tmp_path / "credentials.md"
    path.write_text("password: private-test-value\n", encoding="utf-8")
    with pytest.raises(SecretInputError) as caught:
        read_credentials_file(path)
    assert "private-test-value" not in str(caught.value)


def test_markdown_credentials_accept_totp_uri(tmp_path):
    from vtc.secrets import read_credentials_file

    path = tmp_path / "credentials.md"
    uri = "otpauth://totp?secret=JBSWY3DPEHPK3PXP&algorithm=SHA1&digits=6&period=30"
    path.write_text(f"account: testuser\npassword: test-password\ntotp_secret: {uri}\n", encoding="utf-8")
    assert read_credentials_file(path).totp_secret == uri
