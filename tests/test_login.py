from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from vtc.errors import LoginFailed
from vtc.login_moodle import login_moodle, parse_mobile_token_url
from vtc.myportal.login import login_myportal
from vtc.paths import myportal_session_path, session_path
from vtc.secrets import MemoryStore


class LoginPage:
    def __init__(self, target, authenticated=True):
        self.target = target
        self.authenticated = authenticated
        self.url = ""

    @property
    def first(self):
        return self

    def locator(self, _selector):
        return self

    def get_by_role(self, *_args, **_kwargs):
        return self

    def on(self, *_args):
        pass

    def goto(self, url, **_kwargs):
        self.url = url
        if "/my/courses.php" in url and not self.authenticated:
            self.url = "https://sso.example.invalid/auth-failed"
        return SimpleNamespace(status=200, ok=True)

    def click(self, **_kwargs):
        if self.target == "myportal" and self.authenticated:
            self.url = "https://myportal.vtc.edu.hk/wps/myportal/sp/"

    def wait_for(self, **_kwargs):
        raise PlaywrightTimeoutError("Synthetic missing MFA")

    def wait_for_url(self, *_args, **_kwargs):
        pass

    def wait_for_load_state(self, *_args, **_kwargs):
        pass

    def inner_text(self, **_kwargs):
        return "Log Out" if self.authenticated else "Maintenance"

    def content(self):
        return '<a href="/login/logout.php">Log Out</a>' if self.authenticated else "<h1>Maintenance</h1>"


def fake_browser(monkeypatch, page):
    contexts = []

    class Context:
        def new_page(self):
            return page

        def storage_state(self, path=None):
            state = {"cookies": [], "origins": []}
            if path:
                Path(path).write_text('{"cookies": [], "origins": []}', encoding="utf-8")
            return state

        def close(self):
            pass

    class Browser:
        def new_context(self, **kwargs):
            contexts.append(kwargs)
            return Context()

        def close(self):
            pass

    class Playwright:
        chromium = SimpleNamespace(launch=lambda **_kwargs: Browser())

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr("playwright.sync_api.sync_playwright", Playwright)
    monkeypatch.setattr("vtc.login_moodle.type_like_user", lambda *_args: None)
    monkeypatch.setattr("vtc.myportal.login.type_like_user", lambda *_args: None)
    return contexts


@pytest.mark.parametrize("target", ["moodle", "myportal"])
def test_login_keeps_certificate_validation_and_accepts_no_keyring(target, monkeypatch):
    class NoBackend(MemoryStore):
        def set(self, *_args):
            raise RuntimeError("Synthetic missing backend")

    store = NoBackend({"student-id": "testuser", "password": "test-password"})
    contexts = fake_browser(monkeypatch, LoginPage(target))
    result = login_moodle("ay2627", store=store) if target == "moodle" else login_myportal(store=store)
    assert result.authenticated is True
    assert all(not context.get("ignore_https_errors", False) for context in contexts)


@pytest.mark.parametrize("target", ["moodle", "myportal"])
def test_login_rejects_error_page_without_saving_session(target, monkeypatch):
    store = MemoryStore({"student-id": "testuser", "password": "test-password"})
    fake_browser(monkeypatch, LoginPage(target, authenticated=False))
    with pytest.raises(LoginFailed):
        if target == "moodle":
            login_moodle("ay2627", store=store)
        else:
            login_myportal(store=store)
    path = session_path("ay2627") if target == "moodle" else myportal_session_path()
    assert not path.exists()


def test_token_parser_rejects_unrelated_urls():
    assert parse_mobile_token_url("https://example.invalid/?token=abcdefghijklmnop") is None


@pytest.mark.parametrize("target", ["moodle", "myportal"])
def test_private_file_login_generates_mfa_without_tty(target, monkeypatch, tmp_path):
    class MFAPage(LoginPage):
        def wait_for(self, **_kwargs):
            pass

    path = tmp_path / "credentials.md"
    path.write_text("account: testuser@stu.vtc.edu.hk\npassword: local-test-password\ntotp_secret: JBSWY3DPEHPK3PXP\n", encoding="utf-8")
    fake_browser(monkeypatch, MFAPage(target))
    typed = []
    module = "vtc.login_moodle" if target == "moodle" else "vtc.myportal.login"
    monkeypatch.setattr(f"{module}.type_like_user", lambda _page, _locator, value: typed.append(value))
    monkeypatch.setattr(f"{module}.stdin_is_tty", lambda: False)
    monkeypatch.setattr(f"{module}.read_tty_secret", lambda *_args: pytest.fail("TTY prompt attempted"))
    monkeypatch.setattr(f"{module}.current_totp", lambda _seed: "123456")
    store = MemoryStore()
    result = login_moodle("ay2627", store=store, credentials_file=path) if target == "moodle" else login_myportal(store=store, credentials_file=path)
    assert result.authenticated is True
    assert typed[0] == ("testuser@stu.vtc.edu.hk" if target == "moodle" else "testuser")
    assert typed[-2:] == ["local-test-password", "123456"]
    assert store.get("password") is None
    assert store.get("totp") is None


def test_moodle_login_accepts_loggedin_body_without_logout_link(monkeypatch):
    class LoggedInPage(LoginPage):
        def content(self):
            return '<body class="loggedin roleshortname-student"><h1>My courses</h1></body>'

    fake_browser(monkeypatch, LoggedInPage("moodle"))
    result = login_moodle("ay2627", store=MemoryStore({"student-id": "testuser", "password": "test-password"}))
    assert result.authenticated is True
    assert session_path("ay2627").exists()
