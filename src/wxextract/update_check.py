"""Non-blocking check for newer wxextract releases on GitHub.

Hits the GitHub releases API at most once per CHECK_INTERVAL_HOURS,
caching the result in ~/.cache/wxextract/last_update_check.json. On
network failure or timeout, returns silently — never blocks the
actual extraction flow.

Disable globally with `WXE_NO_UPDATE_CHECK=1` or per-invocation with
`--no-update-check`.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from wxextract import __version__

GITHUB_API = "https://api.github.com/repos/boujuan/extract-wechat-messages-linux/releases/latest"
CHECK_INTERVAL_HOURS = 24
TIMEOUT_SECONDS = 2.0
USER_AGENT = f"wxextract/{__version__} update-check"


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME", "").strip()
    if base:
        return Path(base).expanduser() / "wxextract"
    return Path.home() / ".cache" / "wxextract"


def _cache_path() -> Path:
    return _cache_dir() / "last_update_check.json"


def _parse_version(v: str) -> tuple[int, ...]:
    """Compare semver-ish strings like '0.1.0' or 'v0.1.0' as tuples."""
    v = v.lstrip("v").split("-", 1)[0].split("+", 1)[0]
    parts = []
    for piece in v.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts) if parts else (0,)


def _load_cache() -> dict:
    try:
        return json.loads(_cache_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(data: dict) -> None:
    p = _cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2))
    except OSError:
        pass


def should_check(force: bool = False) -> bool:
    """True iff CHECK_INTERVAL_HOURS has elapsed since the last check."""
    if force:
        return True
    cache = _load_cache()
    last = cache.get("checked_at", 0)
    return (time.time() - last) >= CHECK_INTERVAL_HOURS * 3600


def fetch_latest_tag() -> str | None:
    """Return the latest tag name from the GitHub releases API, or None."""
    req = urllib.request.Request(GITHUB_API, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None
    tag = payload.get("tag_name")
    return tag if isinstance(tag, str) else None


def check(disabled_env: str = "WXE_NO_UPDATE_CHECK",
          force: bool = False) -> str | None:
    """Return the latest tag name if newer than the running version,
    else None. Honors WXE_NO_UPDATE_CHECK=1 / 'true' / 'yes' as opt-out.
    """
    if os.environ.get(disabled_env, "").strip().lower() in ("1", "true", "yes", "on"):
        return None
    if not should_check(force=force):
        return None
    latest = fetch_latest_tag()
    now = int(time.time())
    _save_cache({"checked_at": now, "latest_tag": latest, "current": __version__})
    if latest is None:
        return None
    if _parse_version(latest) > _parse_version(__version__):
        return latest
    return None


def notice(latest_tag: str) -> str:
    """Render a one-line update notice for stderr/stdout."""
    return (
        f"\n[update] wxextract {latest_tag} is available "
        f"(you have {__version__}). Run:  "
        f"uv tool upgrade wxextract"
    )
