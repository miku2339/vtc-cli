"""Moodle SSO login. This module is the only path allowed to read passwords."""

from __future__ import annotations

import base64
import json
import os
import random
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from vtc.errors import LoginFailed, SecretInputError, UsageError
from vtc.paths import ensure_private_file, rest_status_path, session_path
from vtc.provenance import utc_now
from vtc.secrets import (
    PASSWORD_ITEM,
    STUDENT_ID_ITEM,
    TOTP_ITEM,
    SecretStore,
    default_store,
    read_tty_secret,
    resolve_password,
    resolve_student_id,
    resolve_totp_seed,
    sanitized_environ,
    stdin_is_tty,
    wstoken_item,
)
from vtc.pw import type_like_user
from vtc.sites import parse_site

OTP_SELECTOR = (
    "#oathCodeInput, #oneTimePasscodeInput, "
    "input[placeholder*='驗證碼'], input[placeholder*='verification code']"
)
SIGN_IN_BUTTON = re.compile(r"登入|Sign in", re.IGNORECASE)


@dataclass
class LoginResult:
    authenticated: bool
    site: str
    rest: str
    session: bool
    details: str


def parse_mobile_token_url(url: str) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    token_param = None
    query = parse_qs(parsed.query)
    if "token" in query:
        token_param = query["token"][0]
    if parsed.fragment and "token=" in parsed.fragment:
        token_param = parse_qs(parsed.fragment).get("token", [None])[0]
    if token_param is None and "token=" in url:
        token_param = unquote(url.split("token=", 1)[1].split("&", 1)[0])
    if not token_param:
        return None
    raw = token_param.replace("%3A", ":")
    try:
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.b64decode(padded).decode("utf-8", errors="ignore")
    except Exception:
        decoded = raw
    parts = [part for part in decoded.replace(":::", ":").split(":") if part]
    # Moodle uses ':::' separators; keep original split too.
    triple = decoded.split(":::")
    if len(triple) >= 2 and len(triple[1]) >= 16:
        return triple[1]
    if len(parts) >= 2 and len(parts[1]) >= 16:
        return parts[1]
    if len(raw) >= 16 and all(ch.isalnum() for ch in raw):
        return raw
    return None


def _current_otp(seed: str) -> str:
    import pyotp

    return pyotp.TOTP(seed).now()


def login_moodle(
    site_key: str,
    *,
    store: SecretStore | None = None,
    store_password: bool = False,
    store_totp: bool = False,
    totp_auto: bool = False,
    headed: bool = False,
    timeout_ms: int = 30000,
) -> LoginResult:
    site = parse_site(site_key)
    store = store or default_store()
    student_id = resolve_student_id(store)
    if not student_id:
        if not stdin_is_tty():
            raise SecretInputError(
                "No student id. Set VTC_STUDENT_ID or run `vtc login moodle --site ...` in a local terminal."
            )
        student_id = input("VTC student id: ").strip()
        if not student_id:
            raise UsageError("Student id is required.")
    password = resolve_password(store)
    totp_seed = resolve_totp_seed(store) if totp_auto else None

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise LoginFailed("Playwright is not installed. Run: python -m playwright install chromium") from exc

    session_file = session_path(site.key)
    session_file.parent.mkdir(parents=True, exist_ok=True)
    captured_token: str | None = None
    rest_state = "unavailable"

    browser_env = sanitized_environ()
    # Playwright still needs a complete OS env; secrets were stripped above.

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not headed, env=browser_env)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()

            def capture_request(request: Any) -> None:
                nonlocal captured_token
                token = parse_mobile_token_url(request.url)
                if token:
                    captured_token = token

            page.on("request", capture_request)
            page.on("requestfailed", capture_request)

            page.goto(site.saml_login_url(), wait_until="domcontentloaded", timeout=timeout_ms)
            type_like_user(
                page,
                page.locator("input[name='UserName']:not([type='hidden'])"),
                site.student_email(student_id),
            )
            type_like_user(
                page,
                page.locator("input[name='Password']:not([type='hidden'])"),
                password,
            )
            page.get_by_role("button", name=SIGN_IN_BUTTON).click()

            code_locator = page.locator(OTP_SELECTOR).first
            try:
                code_locator.wait_for(state="visible", timeout=12000)
            except PlaywrightTimeoutError:
                code_locator = None

            if code_locator is not None:
                if totp_auto and totp_seed:
                    otp = _current_otp(totp_seed)
                else:
                    otp = read_tty_secret("Moodle one-time code: ")
                type_like_user(page, code_locator, otp)
                page.get_by_role("button", name=SIGN_IN_BUTTON).click()

            try:
                page.wait_for_url(
                    re.compile(rf"https://{re.escape(site.host)}/.*"),
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )
            except PlaywrightTimeoutError as exc:
                if not page.url.startswith(site.base_url):
                    raise LoginFailed("Moodle login did not return to the Moodle site.") from exc

            page.goto(site.url("/my/courses.php"), wait_until="domcontentloaded", timeout=timeout_ms)
            if "/login/" in page.url:
                raise LoginFailed("Moodle login did not succeed.")

            context.storage_state(path=str(session_file))
            os.chmod(session_file, 0o600)
            ensure_private_file(session_file)

            passport = str(random.randint(10**8, 10**9 - 1))
            launch = (
                f"{site.url('/admin/tool/mobile/launch.php')}"
                f"?service=moodle_mobile_app&passport={passport}&urlscheme=moodlevtc"
            )
            try:
                page.goto(launch, wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass

            context.close()
            browser.close()
    except Exception:
        password = ""
        totp_seed = None
        raise
    else:
        store.set(STUDENT_ID_ITEM, student_id)
        if store_password:
            store.set(PASSWORD_ITEM, password)
        if store_totp and totp_seed:
            store.set(TOTP_ITEM, totp_seed)
    finally:
        password = ""
        totp_seed = None

    if captured_token:
        try:
            store.set(wstoken_item(site.key), captured_token)
            rest_state = "available"
        except Exception:
            rest_state = "unavailable"
        captured_token = None

    status_path = rest_status_path(site.key)
    status_path.write_text(
        json.dumps(
            {
                "site": site.key,
                "rest": rest_state,
                "session": True,
                "captured_at": utc_now(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    ensure_private_file(status_path)

    return LoginResult(
        authenticated=True,
        site=site.key,
        rest=rest_state,
        session=session_file.exists(),
        details="Moodle login stored a local session. REST token capture is separate from login success.",
    )
