"""Library SSO login using terminal input or a private credential file."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from vtc.errors import LoginFailed, SecretInputError, UsageError
from vtc.library.constants import (
    CATALOGUE_ACCOUNT_URL,
    CATALOGUE_LOGIN_URL,
    CATALOGUE_ORIGIN,
    CATALOGUE_VID,
    PAPERS_URL,
    SERVICES,
    SYLLABUSES_URL,
)
from vtc.paths import library_session_path, write_private_json
from vtc.pw import type_like_user
from vtc.secrets import current_totp, read_credentials_file, read_tty_secret, sanitized_environ, stdin_is_tty

USER_AGENT = "vtc-cli library reader"
IDP_HOSTS = {"fs.vtc.edu.hk", "login.vtc.edu.hk"}
OTP_SELECTOR = "#oathCodeInput, #oneTimePasscodeInput"
SIGN_IN_BUTTON = re.compile(r"Sign in|登入", re.IGNORECASE)


@dataclass
class LoginResult:
    authenticated: bool
    service: str
    session: bool
    details: str


def login_library(
    *,
    service: str = "catalogue",
    credentials_file: Path | None = None,
    headed: bool = False,
    timeout_ms: int = 30000,
) -> LoginResult:
    if service not in SERVICES:
        raise UsageError("Unknown Library service.")
    credentials = read_credentials_file(credentials_file) if credentials_file else None
    if credentials is None:
        if not stdin_is_tty():
            raise SecretInputError("Interactive Library login requires a local terminal or --credentials-file.")
        account = input("VTC CNA email address: ").strip()
        password = read_tty_secret("VTC Library password: ")
        seed = None
    else:
        account, password, seed = credentials.account, credentials.password, credentials.totp_secret
    if not account:
        raise UsageError("VTC CNA account is required.")
    if "@" not in account:
        account += "@stu.vtc.edu.hk"
    student = account.lower().endswith("@stu.vtc.edu.hk")

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not headed, env=sanitized_environ())
            try:
                context = browser.new_context(user_agent=USER_AGENT)
                page = context.new_page()
                page.set_default_timeout(timeout_ms)
                entry = CATALOGUE_LOGIN_URL if service == "catalogue" else PAPERS_URL if service == "papers" else SYLLABUSES_URL
                response = page.goto(entry, wait_until="domcontentloaded", timeout=timeout_ms)
                if response is None or not response.ok:
                    raise LoginFailed("Library sign-in page was not read.")
                if service == "catalogue":
                    label = r"Student CNA" if student else r"Staff CNA"
                    page.get_by_role("button", name=re.compile(label, re.IGNORECASE)).click()
                else:
                    choice = "vtc-student-bridge" if student else "vtc-staff-bridge"
                    page.locator(f"input[name='sourceChoice[{choice}]']").click()
                _sign_in(page, account, password, seed, timeout_ms)

                if service == "catalogue":
                    state = _catalogue_state(context, page, timeout_ms)
                else:
                    from vtc.library.documents import verified_database_page

                    expected = PAPERS_URL if service == "papers" else SYLLABUSES_URL
                    page.goto(expected, wait_until="domcontentloaded", timeout=timeout_ms)
                    if not verified_database_page(page, service):
                        raise LoginFailed("Library database login was not verified.")
                    state = context.storage_state()
                write_private_json(library_session_path(service), state)
                context.close()
            finally:
                browser.close()
    except (LoginFailed, SecretInputError, UsageError):
        raise
    except Exception:
        raise LoginFailed("Library login could not be verified. Check connectivity or use --headed in a local terminal.") from None
    finally:
        password = ""
        seed = None
        credentials = None
    return LoginResult(True, service, True, "Library login stored a local session.")


def _sign_in(page: Any, account: str, password: str, seed: str | None, timeout_ms: int) -> None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    username = page.locator("input[name='UserName']:not([type='hidden'])")
    username.wait_for(state="visible", timeout=timeout_ms)
    _require_idp(page)
    type_like_user(page, username, account)
    password_input = page.locator("input[name='Password']:not([type='hidden'])")
    password_input.wait_for(state="visible", timeout=timeout_ms)
    _require_idp(page)
    type_like_user(page, password_input, password)
    page.get_by_role("button", name=SIGN_IN_BUTTON).click()
    otp = page.locator(OTP_SELECTOR).first
    try:
        otp.wait_for(state="visible", timeout=12000)
    except PlaywrightTimeoutError:
        otp = None
    if otp is not None:
        _require_idp(page)
        code = current_totp(seed) if seed else read_tty_secret("Library one-time code: ")
        type_like_user(page, otp, code)
        page.get_by_role("button", name=SIGN_IN_BUTTON).click()
    page.wait_for_url(re.compile(r"https://(?:vtc\.primo\.exlibrisgroup\.com/nde/(?:home|account(?:/.*)?)|vtclib23\.vtc\.edu\.hk/(?:exam|syllabus)/search(?:/list)?)(?:\?.*)?$"), timeout=timeout_ms)


def _require_idp(page: Any) -> None:
    origin = urlsplit(page.url)
    if origin.scheme != "https" or origin.hostname not in IDP_HOSTS:
        raise LoginFailed("Library login reached an unexpected identity provider.")


def _catalogue_state(context: Any, page: Any, timeout_ms: int) -> dict[str, Any]:
    from vtc.library.catalogue import _authenticated_claims, _decode_jwt, _parse_counters

    response = page.goto(CATALOGUE_ACCOUNT_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    if response is None or not response.ok or urlsplit(page.url).hostname != urlsplit(CATALOGUE_ORIGIN).hostname:
        raise LoginFailed("Library account page was not read.")
    page.wait_for_function("""() => {
        try {
            const token = sessionStorage.getItem('primoExploreJwt');
            const encoded = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
            const signedIn = JSON.parse(atob(encoded)).signedIn;
            return signedIn === true || signedIn === 'true';
        } catch { return false; }
    }""", timeout=timeout_ms)
    token = page.evaluate("sessionStorage.getItem('primoExploreJwt')")
    if not isinstance(token, str) or not _authenticated_claims(_decode_jwt(token)):
        raise LoginFailed("Library account is not authenticated.")
    response = context.request.get(
        f"{CATALOGUE_ORIGIN}/primaws/rest/priv/myaccount/counters",
        params={"lang": "en", "vid": CATALOGUE_VID},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "is-nde": "true"},
        max_redirects=0,
        timeout=timeout_ms,
    )
    if response.status != 200 or "json" not in response.headers.get("content-type", "").lower() or _parse_counters(response.json()) is None:
        raise LoginFailed("Library account access was not verified.")
    state = context.storage_state()
    state["sessionStorage"] = [{"origin": CATALOGUE_ORIGIN, "name": "primoExploreJwt", "value": token}]
    return state
