"""Per-contact persistent state in workspace/last_extract.json.

Tracks the largest local_id we've ever rendered per alias so `--since-last`
runs can emit just the deltas.

Schema::

    {
      "rachel_97213": {
        "alias": "rachel_97213",
        "username": "F100001957286229",
        "last_local_id": 11923,
        "last_create_time": 1779724567,
        "last_run_at": 1779730000,
        "last_run_at_iso": "2026-05-26T01:46:40"
      },
      ...
    }
"""
from __future__ import annotations

import json
import os
import stat
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def _state_path(workspace: Path) -> Path:
    return workspace / "last_extract.json"


def load(workspace: Path) -> dict[str, dict[str, Any]]:
    p = _state_path(workspace)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def get(workspace: Path, alias: str) -> dict[str, Any] | None:
    return load(workspace).get(alias)


def save(workspace: Path, alias: str, *, username: str,
         last_local_id: int, last_create_time: int) -> None:
    data = load(workspace)
    now = int(time.time())
    data[alias] = {
        "alias": alias,
        "username": username,
        "last_local_id": last_local_id,
        "last_create_time": last_create_time,
        "last_run_at": now,
        "last_run_at_iso": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
    }
    p = _state_path(workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    try:
        p.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def clear(workspace: Path, alias: str | None = None) -> None:
    """Remove state for one alias (or the whole file if alias=None)."""
    p = _state_path(workspace)
    if alias is None:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        return
    data = load(workspace)
    data.pop(alias, None)
    if data:
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
