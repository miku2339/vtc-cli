"""Shared Playwright helpers. Never log typed values."""

from __future__ import annotations

from typing import Any


def type_like_user(page: Any, locator: Any, text: str, *, delay_ms: int = 45) -> None:
    locator.wait_for(state="visible", timeout=10000)
    locator.click(timeout=10000)
    try:
        locator.press("Meta+A")
    except Exception:
        locator.press("Control+A")
    locator.press("Backspace")
    page.keyboard.type(text, delay=delay_ms)
