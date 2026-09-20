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
