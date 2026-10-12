from pathlib import Path
from types import SimpleNamespace

import pytest

from vtc.errors import LoginFailed, SecretInputError
from vtc.library.login import _sign_in, login_library
from vtc.paths import library_session_path


class IdentityPage:
    def __init__(self, url, *, move_after_username=False):
        self.url = url
        self.move_after_username = move_after_username
        self.fills = []

    def locator(self, selector):
        page = self

        class Locator:
            def wait_for(self, **_kwargs):
                pass

            def fill(self, value, **_kwargs):
                page.fills.append((selector, value))
                if "UserName" in selector and page.move_after_username:
                    page.url = "https://attacker.example/login"

        return Locator()


@pytest.mark.parametrize("url", ["http://fs.vtc.edu.hk/adfs/ls/", "https://fs.vtc.edu.hk.attacker.example/login"])
def test_library_rejects_untrusted_idp_before_account_input(url):
    page = IdentityPage(url)
    with pytest.raises(LoginFailed):
        _sign_in(page, "testuser@stu.vtc.edu.hk", "test-password", None, 1000)
    assert page.fills == []


def test_library_rechecks_idp_after_username_navigation():
    page = IdentityPage("https://fs.vtc.edu.hk/adfs/ls/", move_after_username=True)
    with pytest.raises(LoginFailed):
        _sign_in(page, "testuser@stu.vtc.edu.hk", "test-password", None, 1000)
    assert len(page.fills) == 1
    assert "UserName" in page.fills[0][0]


def test_library_interactive_login_requires_human_tty(monkeypatch):
    monkeypatch.setattr("vtc.library.login.stdin_is_tty", lambda: False)
    with pytest.raises(SecretInputError):
        login_library()


def fake_login_browser(monkeypatch):
    contexts = []

    class Page:
        def set_default_timeout(self, _timeout):
            pass

        def goto(self, _url, **_kwargs):
            return SimpleNamespace(ok=True)

        def get_by_role(self, *_args, **_kwargs):
            return SimpleNamespace(click=lambda: None)

    class Context:
        def new_page(self):
            return Page()

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
    monkeypatch.setattr("vtc.library.login._sign_in", lambda *_args: None)
    return contexts


def credential_file(tmp_path):
    path = tmp_path / "credentials.md"
    path.write_text("account: testuser@stu.vtc.edu.hk\npassword: test-password\n", encoding="utf-8")
    return path


def test_library_only_saves_verified_private_session(monkeypatch, tmp_path):
    contexts = fake_login_browser(monkeypatch)
    monkeypatch.setattr("vtc.library.login.stdin_is_tty", lambda: False)
    state = {"cookies": [], "origins": [], "sessionStorage": []}
    monkeypatch.setattr("vtc.library.login._catalogue_state", lambda *_args: state)
    result = login_library(credentials_file=credential_file(tmp_path))
    assert result.authenticated is True
    assert library_session_path().is_file()
    assert library_session_path().stat().st_mode & 0o777 == 0o600
    assert all(not kwargs.get("ignore_https_errors") for kwargs in contexts)
    assert not library_session_path("papers").exists()


def test_library_failed_verification_preserves_previous_session(monkeypatch, tmp_path):
    fake_login_browser(monkeypatch)
    path = library_session_path()
    path.parent.mkdir(parents=True)
    previous = b'{"previous": "test-session"}'
    path.write_bytes(previous)

    def reject(*_args):
        raise LoginFailed("Test rejected account")

    monkeypatch.setattr("vtc.library.login._catalogue_state", reject)
    with pytest.raises(LoginFailed):
        login_library(credentials_file=credential_file(tmp_path))
    assert path.read_bytes() == previous
