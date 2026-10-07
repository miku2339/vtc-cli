"""Credential access. Never log or return secret values."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from getpass import getpass
from pathlib import Path
from typing import Protocol

from vtc.errors import SecretInputError

KEYCHAIN_SERVICE = "vtc.cli"


def default_store() -> SecretStore:
    return KeyringStore()


PASSWORD_ITEM = "password"
TOTP_ITEM = "totp"
STUDENT_ID_ITEM = "student-id"

SECRET_ENV_NAMES = frozenset(
    {
        "VTC_PASSWORD",
        "VTC_TOTP_SECRET",
        "VTC_MOODLE_WSTOKEN",
        "VTC_WSTOKEN",
    }
)

SAFE_VTC_ENV = frozenset({"VTC_STUDENT_ID", "VTC_SITE", "VTC_DATA_DIR", "VTC_CONFIG_DIR"})


@dataclass(frozen=True)
class FileCredentials:
    account: str
    password: str = field(repr=False)
    totp_secret: str | None = field(default=None, repr=False)


def read_credentials_file(path: Path) -> FileCredentials:
    target = Path(path).expanduser()
    try:
        if not target.is_file() or target.stat().st_size > 65536:
            raise SecretInputError("Credential file is missing or too large.")
        target.chmod(0o600)
        lines = target.read_text(encoding="utf-8").splitlines()
    except SecretInputError:
        raise
    except Exception:
        raise SecretInputError("Credential file could not be read.") from None
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "```")) or stripped == "---":
            continue
        key, separator, value = stripped.partition(":")
        if not separator or key not in {"account", "password", "totp_secret"} or key in values:
            raise SecretInputError("Credential file must contain account, password and optional totp_secret fields.")
        values[key] = value.strip()
    if not values.get("account") or not values.get("password"):
        raise SecretInputError("Credential file requires a non-empty account and password.")
    seed = values.get("totp_secret") or None
    if seed:
        import pyotp

        try:
            generator = pyotp.parse_uri(seed) if seed.startswith("otpauth://") else pyotp.TOTP(seed)
            if not isinstance(generator, pyotp.TOTP):
                raise ValueError("TOTP required")
            generator.byte_secret()
        except Exception:
            raise SecretInputError("Credential file contains an invalid TOTP seed.") from None
    return FileCredentials(account=values["account"], password=values["password"], totp_secret=seed)


def current_totp(seed: str) -> str:
    import pyotp

    generator = pyotp.parse_uri(seed) if seed.startswith("otpauth://") else pyotp.TOTP(seed)
    return generator.now()


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class MemoryStore:
    def __init__(self, initial: Mapping[str, str] | None = None) -> None:
        self._data = dict(initial or {})

    def get(self, name: str) -> str | None:
        value = self._data.get(name)
        return value if value else None

    def set(self, name: str, value: str) -> None:
        self._data[name] = value

    def delete(self, name: str) -> None:
        self._data.pop(name, None)


class KeyringStore:
    def get(self, name: str) -> str | None:
        try:
            import keyring
        except ImportError:
            return None
        try:
            value = keyring.get_password(KEYCHAIN_SERVICE, name)
        except Exception:
            return None
        return value or None

    def set(self, name: str, value: str) -> None:
        import keyring

        keyring.set_password(KEYCHAIN_SERVICE, name, value)

    def delete(self, name: str) -> None:
        try:
            import keyring

            keyring.delete_password(KEYCHAIN_SERVICE, name)
        except Exception:
            return


def wstoken_item(site_key: str) -> str:
    return f"moodle-wstoken-{site_key}"


def pop_secret_env(
    name: str, env: MutableMapping[str, str] | None = None
) -> str | None:
    target = env if env is not None else os.environ
    value = target.pop(name, None)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def sanitized_environ(base: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key in SECRET_ENV_NAMES:
            env.pop(key, None)
            continue
        if key.startswith("VTC_") and key not in SAFE_VTC_ENV:
            env.pop(key, None)
            continue
        upper = key.upper()
        if key.startswith("VTC_") and any(
            marker in upper for marker in ("PASSWORD", "SECRET", "TOKEN", "TOTP", "OTP")
        ):
            env.pop(key, None)
    return env


def stdin_is_tty() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


def read_tty_secret(prompt: str) -> str:
    if not stdin_is_tty():
        raise SecretInputError(
            "Refusing to read a secret: stdin is not a TTY. "
            "Run this in a local terminal, or pass a short-lived env var into the login process only."
        )
    try:
        value = getpass(prompt)
    except Exception as exc:
        raise SecretInputError(
            "Refusing to fall back to visible password input."
        ) from exc
    if value is None:
        raise SecretInputError("No secret was entered.")
    secret = value.strip()
    if not secret:
        raise SecretInputError("No secret was entered.")
    return secret


def resolve_student_id(store: SecretStore, env: MutableMapping[str, str] | None = None) -> str | None:
    target = env if env is not None else os.environ
    from_env = (target.get("VTC_STUDENT_ID") or "").strip()
    if from_env:
        return from_env
    stored = store.get(STUDENT_ID_ITEM)
    return stored.strip() if stored else None


def resolve_password(store: SecretStore, env: MutableMapping[str, str] | None = None) -> str:
    target = env if env is not None else os.environ
    from_env = pop_secret_env("VTC_PASSWORD", target)
    if from_env:
        return from_env
    stored = store.get(PASSWORD_ITEM)
    if stored:
        return stored
    return read_tty_secret("VTC Moodle password: ")


def resolve_totp_seed(store: SecretStore, env: MutableMapping[str, str] | None = None) -> str | None:
    target = env if env is not None else os.environ
    from_env = pop_secret_env("VTC_TOTP_SECRET", target)
    if from_env:
        return from_env
    stored = store.get(TOTP_ITEM)
    return stored.strip() if stored else None


def resolve_wstoken(
    store: SecretStore, site_key: str, env: MutableMapping[str, str] | None = None
) -> str | None:
    target = env if env is not None else os.environ
    from_env = pop_secret_env("VTC_MOODLE_WSTOKEN", target) or pop_secret_env(
        "VTC_WSTOKEN", target
    )
    if from_env:
        return from_env
    stored = store.get(wstoken_item(site_key))
    return stored.strip() if stored else None
