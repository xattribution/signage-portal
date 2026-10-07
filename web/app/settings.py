"""
Settings an administrator can change in the portal, stored in SQLite.

Each setting has an environment-variable equivalent. When that variable is set, it wins
and the Settings page shows the field as locked, so existing .env deployments behave
exactly as before. Values are applied to the config module, which every request reads.
"""
import json
import os
import re

from fastapi import HTTPException

from . import config, db

RENDER_SIZES = {"1080p": (1920, 1080), "4k": (3840, 2160)}
_HOST = re.compile(r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*")

# Defaults captured at import: the environment value if set, otherwise the built-in default.
DEFAULTS = {
    "site_name": config.SITE_NAME,
    "clock_24h": config.CLOCK_24H,
    "default_slide_seconds": config.DEFAULT_SLIDE_SECONDS,
    "render_size": "4k" if (config.TARGET_WIDTH, config.TARGET_HEIGHT) == (3840, 2160) else "1080p",
    "access_names": [],
}
ENV_VARS = {
    "site_name": ["SITE_NAME"],
    "clock_24h": ["CLOCK_24H"],
    "default_slide_seconds": ["DEFAULT_SLIDE_SECONDS"],
    "render_size": ["TARGET_WIDTH", "TARGET_HEIGHT"],
    "access_names": ["ALLOWED_HOSTS"],
}


def locked(key: str) -> bool:
    return any(os.getenv(name, "").strip() for name in ENV_VARS[key])


def _clean(key: str, value):
    """Validate one value; raise HTTPException(400) with a plain message when it is invalid."""
    if key == "site_name":
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80:
            raise HTTPException(400, "Workspace name must be 1 to 80 characters.")
        return value.strip()
    if key == "clock_24h":
        if not isinstance(value, bool):
            raise HTTPException(400, "Clock format must be true or false.")
        return value
    if key == "default_slide_seconds":
        if not isinstance(value, int) or isinstance(value, bool) or not 2 <= value <= 3600:
            raise HTTPException(400, "Seconds per slide must be between 2 and 3600.")
        return value
    if key == "render_size":
        if value not in RENDER_SIZES:
            raise HTTPException(400, "Render size must be 1080p or 4k.")
        return value
    if key == "access_names":
        if not isinstance(value, list) or len(value) > 20:
            raise HTTPException(400, "List up to 20 access names.")
        names = []
        for raw in value:
            name = str(raw).strip().lower().rstrip(".")
            if not name:
                continue
            if not _HOST.fullmatch(name):
                raise HTTPException(400, f"'{name[:60]}' is not a valid hostname. Use a name like signage.office.lan, "
                                         "without http:// or a port.")
            if name not in names:
                names.append(name)
        return names
    raise HTTPException(400, "Unknown setting.")


def _apply(key: str, value) -> None:
    if key == "site_name":
        config.SITE_NAME = value
    elif key == "clock_24h":
        config.CLOCK_24H = value
    elif key == "default_slide_seconds":
        config.DEFAULT_SLIDE_SECONDS = value
    elif key == "render_size":
        config.TARGET_WIDTH, config.TARGET_HEIGHT = RENDER_SIZES[value]
    elif key == "access_names":
        config.EXTRA_HOSTS = list(value)


def load() -> None:
    """Apply stored values (or defaults) for every unlocked setting. Called by db.init()."""
    stored = {r["key"]: r["value"] for r in db.rows("SELECT key, value FROM settings")}
    for key, default in DEFAULTS.items():
        if locked(key):
            continue
        value = default
        if key in stored:
            try:
                value = _clean(key, json.loads(stored[key]))
            except (ValueError, HTTPException):
                value = default
        _apply(key, value)


def current() -> dict:
    values = {
        "site_name": config.SITE_NAME,
        "clock_24h": config.CLOCK_24H,
        "default_slide_seconds": config.DEFAULT_SLIDE_SECONDS,
        "render_size": "4k" if (config.TARGET_WIDTH, config.TARGET_HEIGHT) == (3840, 2160) else "1080p",
        "access_names": list(config.EXTRA_HOSTS) if config.HOSTS_AUTO else list(config.ALLOWED_HOSTS),
    }
    return {
        "values": values,
        "locked": {key: ENV_VARS[key] for key in DEFAULTS if locked(key)},
        "hosts_auto": config.HOSTS_AUTO,
    }


def save(changes: dict) -> list[str]:
    """Validate everything first, then store and apply. Returns the keys that changed."""
    if not isinstance(changes, dict) or not changes:
        raise HTTPException(400, "Nothing to save.")
    cleaned = {}
    for key, value in changes.items():
        if key not in DEFAULTS:
            raise HTTPException(400, f"Unknown setting '{str(key)[:40]}'.")
        if locked(key):
            raise HTTPException(409, f"{', '.join(ENV_VARS[key])} is set on the server, so this setting is locked.")
        cleaned[key] = _clean(key, value)
    before = current()["values"]
    changed = [k for k, v in cleaned.items() if before.get(k) != v]
    with db.tx() as c:
        c.execute("BEGIN IMMEDIATE")
        for key, value in cleaned.items():
            c.execute("INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, json.dumps(value)))
    for key, value in cleaned.items():
        _apply(key, value)
    return changed
