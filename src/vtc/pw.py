"""Shared Playwright helpers. Never log typed values."""

from __future__ import annotations

from typing import Any


def type_like_user(page: Any, locator: Any, text: str) -> None:
    locator.fill(text, timeout=10000)
