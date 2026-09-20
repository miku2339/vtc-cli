from __future__ import annotations

import os
from pathlib import Path


def config_dir() -> Path:
    override = os.environ.get("VTC_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / "vtc"
    return Path.home() / ".config" / "vtc"


def data_dir() -> Path:
    override = os.environ.get("VTC_DATA_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / "vtc"
    return Path.home() / ".local" / "share" / "vtc"


def session_path(site_key: str) -> Path:
    return data_dir() / f"moodle-{site_key}.storage.json"


def myportal_session_path() -> Path:
    return data_dir() / "myportal.storage.json"


def rest_status_path(site_key: str) -> Path:
    return data_dir() / f"moodle-{site_key}.rest-status.json"


def config_path() -> Path:
    return config_dir() / "config.toml"


def ensure_private_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.chmod(0o600)
