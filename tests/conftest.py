from __future__ import annotations

import pytest

from vtc.secrets import MemoryStore


@pytest.fixture(autouse=True)
def isolate_vtc_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VTC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VTC_CONFIG_DIR", str(tmp_path / "config"))
    for key in (
        "VTC_PASSWORD",
        "VTC_TOTP_SECRET",
        "VTC_MOODLE_WSTOKEN",
        "VTC_WSTOKEN",
        "VTC_STUDENT_ID",
        "VTC_SITE",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def memory_secret_store(monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr("vtc.secrets.default_store", lambda: store)
    monkeypatch.setattr("vtc.login_moodle.default_store", lambda: store)
    monkeypatch.setattr("vtc.myportal.login.default_store", lambda: store)
    monkeypatch.setattr("vtc.moodle.client.default_store", lambda: store)
    return store

