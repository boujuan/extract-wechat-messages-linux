"""Shared utilities: paths, logging, formatting."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def _xdg_data_home() -> Path:
    """$XDG_DATA_HOME or ~/.local/share per the XDG Base Directory spec."""
    env = os.environ.get("XDG_DATA_HOME", "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".local" / "share"


def invoking_sudo_user() -> str | None:
    """The user behind `sudo`, or None.

    Only meaningful when running as root via sudo (env_reset strips the
    caller's environment except SUDO_USER/SUDO_UID/SUDO_GID).
    """
    if os.geteuid() != 0:
        return None
    user = os.environ.get("SUDO_USER", "").strip()
    return user if user and user != "root" else None


def sudo_user_home() -> Path | None:
    """Home directory of the user behind `sudo`, or None."""
    user = invoking_sudo_user()
    if user is None:
        return None
    try:
        import pwd
        return Path(pwd.getpwnam(user).pw_dir)
    except (ImportError, KeyError, OSError):
        return Path("/home") / user


def chown_to_invoking_user(path: Path) -> None:
    """Recursively chown `path` to the invoking user (SUDO_UID/SUDO_GID).

    No-op when not running as root via sudo. Files created under `sudo`
    would otherwise be root-owned and break later unprivileged runs
    (snapshot/decrypt caches, key caches).
    """
    uid_s = os.environ.get("SUDO_UID", "").strip()
    if os.geteuid() != 0 or not uid_s:
        return
    gid_s = os.environ.get("SUDO_GID", "").strip() or uid_s
    try:
        uid, gid = int(uid_s), int(gid_s)
    except ValueError:
        return
    targets = [path]
    if path.is_dir():
        targets += sorted(path.rglob("*"))
    for p in targets:
        try:
            os.chown(p, uid, gid)
        except OSError:
            pass


def _looks_like_source_checkout(util_path: Path) -> bool:
    """True if running from an editable source clone (project root has
    pyproject.toml three levels above this file)."""
    candidate = util_path.resolve().parent.parent.parent
    return (candidate / "pyproject.toml").is_file()


def default_workspace() -> Path:
    """Workspace location, resolved in priority order:

    1. ``--workspace`` flag (handled in CLI, not here)
    2. ``WXE_WORKSPACE`` env var
    3. ``<project-root>/workspace/`` when running from a source checkout
       (editable install / ``uv run`` inside the cloned repo)
    4. ``$XDG_DATA_HOME/wxextract/`` — typically ``~/.local/share/wxextract/``
       — when installed system-wide (``uv tool install``, ``pipx``, ``pip``)

    Under ``sudo`` the workspace maps to the invoking user's home instead
    of /root, so an elevated key-recovery run lands in the same workspace
    the user's normal runs use.
    """
    env = os.environ.get("WXE_WORKSPACE")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__)
    if _looks_like_source_checkout(here):
        return (here.resolve().parent.parent.parent / "workspace").resolve()
    if invoking_sudo_user() is not None and not os.environ.get("XDG_DATA_HOME", "").strip():
        home = sudo_user_home() or Path.home()
        return (home / ".local" / "share" / "wxextract").resolve()
    return (_xdg_data_home() / "wxextract").resolve()


def setup_logging(verbose: bool = True, quiet: bool = False) -> logging.Logger:
    level = logging.WARNING if quiet else (logging.INFO if verbose else logging.WARNING)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname).1s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    return logging.getLogger("wxextract")


def human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"
