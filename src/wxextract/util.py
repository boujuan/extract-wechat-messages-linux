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
    """
    env = os.environ.get("WXE_WORKSPACE")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__)
    if _looks_like_source_checkout(here):
        return (here.resolve().parent.parent.parent / "workspace").resolve()
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
