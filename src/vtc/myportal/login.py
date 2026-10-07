"""MyPortal form login using terminal input or a private credential file."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from vtc.errors import LoginFailed, SecretInputError, UsageError
from vtc.myportal.constants import LOGIN_BUTTON, LOGOUT_MARKERS, MYPORTAL_URL
from vtc.myportal.parse import looks_like_myportal_login
from vtc.paths import myportal_session_path, write_private_json
from vtc.pw import type_like_user
from vtc.secrets import (
    PASSWORD_ITEM,
    STUDENT_ID_ITEM,
    SecretStore,
    default_store,
    current_totp,
    pop_secret_env,
    read_tty_secret,
    read_credentials_file,
    resolve_student_id,
    sanitized_environ,
    stdin_is_tty,
)

OTP_SELECTOR = (
    "#oathCodeInput, #oneTimePasscodeInput, "
    "input[placeholder*='驗證碼'], input[placeholder*='verification code']"
)


@dataclass
class LoginResult:
    authenticated: bool
    session: bool
    details: str


def _resolve_password(store: SecretStore) -> str:
    from_env = pop_secret_env("VTC_PASSWORD")
    if from_env:
        return from_env
    stored = store.get(PASSWORD_ITEM)
    if stored:
        return stored
    return read_tty_secret("VTC MyPortal password: ")


def login_myportal(
    *,
    store: SecretStore | None = None,
    store_password: bool = False,
    headed: bool = False,
    timeout_ms: int = 30000,
    credentials_file: Path | None = None,
) -> LoginResult:
    store = store or default_store()
    credentials = read_credentials_file(credentials_file) if credentials_file else None
    student_id = credentials.account if credentials else resolve_student_id(store)
    if student_id and student_id.lower().endswith("@stu.vtc.edu.hk"):
        student_id = student_id.rsplit("@", 1)[0]
    if not student_id:
        if not stdin_is_tty():
            raise SecretInputError(
                "No student id. Set VTC_STUDENT_ID or run `vtc login myportal` in a local terminal."
            )
        student_id = input("VTC student id: ").strip()
        if not student_id:
            raise UsageError("Student id is required.")
    password = credentials.password if credentials else _resolve_password(store)
    totp_seed = credentials.totp_secret if credentials else None

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise LoginFailed("Playwright is not installed. Run: python -m playwright install chromium") from exc

    session_file = myportal_session_path()
    session_file.parent.mkdir(parents=True, exist_ok=True)
    browser_env = sanitized_environ()

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not headed, env=browser_env)
            context = browser.new_context()
            page = context.new_page()
            page.goto(MYPORTAL_URL, wait_until="domcontentloaded", timeout=timeout_ms)
            type_like_user(page, page.locator("input[name='userid']"), student_id)
            type_like_user(page, page.locator("input[name='password']"), password)
            page.get_by_role("button", name=re.compile(LOGIN_BUTTON, re.IGNORECASE)).click()
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                pass

            code_locator = page.locator(OTP_SELECTOR).first
            try:
                code_locator.wait_for(state="visible", timeout=8000)
            except PlaywrightTimeoutError:
                code_locator = None
            if code_locator is not None:
                if totp_seed:
                    otp = current_totp(totp_seed)
                else:
                    otp = read_tty_secret("MyPortal one-time code: ")
                type_like_user(page, code_locator, otp)
                page.get_by_role("button", name=re.compile(LOGIN_BUTTON, re.IGNORECASE)).click()
                try:
                    page.wait_for_load_state("networkidle", timeout=timeout_ms)
                except PlaywrightTimeoutError:
                    pass

            body = ""
            try:
                body = page.locator("body").inner_text(timeout=5000)
            except Exception:
                body = page.content()
            if re.search(
                r"Please input the correct CNA and password|請輸入正確的CNA和密碼",
                body,
                re.IGNORECASE,
            ):
                raise LoginFailed("MyPortal rejected the CNA or password.")
            html = page.content()
            final_url = urlparse(page.url)
            if (
                final_url.scheme != "https"
                or final_url.netloc != urlparse(MYPORTAL_URL).netloc
                or looks_like_myportal_login(html, page.url)
                or not any(marker in f"{body} {html}".lower() for marker in LOGOUT_MARKERS)
            ):
                raise LoginFailed("MyPortal login did not succeed.")

            write_private_json(session_file, context.storage_state())
            context.close()
            browser.close()
    except Exception:
        password = ""
        raise
    else:
        try:
            store.set(STUDENT_ID_ITEM, student_id)
        except Exception:
            pass
        if store_password:
            store.set(PASSWORD_ITEM, password)
    finally:
        password = ""
        totp_seed = None
        credentials = None

    return LoginResult(
        authenticated=True,
        session=session_file.exists(),
        details="MyPortal login stored a local session.",
    )
