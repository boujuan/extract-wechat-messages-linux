"""Shared utilities: paths, logging, formatting."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def default_workspace() -> Path:
    """Workspace path: --workspace CLI arg > WXE_WORKSPACE env > <project-root>/workspace.

    Project root = the directory containing pyproject.toml (3 parents up from this
    file when installed editably: util.py → wxextract/ → src/ → project root).
    """
    env = os.environ.get("WXE_WORKSPACE")
    if env:
        return Path(env).expanduser().resolve()
    return (Path(__file__).resolve().parent.parent.parent / "workspace").resolve()


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
