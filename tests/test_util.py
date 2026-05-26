"""default_workspace() selection logic."""
from pathlib import Path

import pytest

from wxextract import util


def test_env_var_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("WXE_WORKSPACE", str(tmp_path / "myws"))
    assert util.default_workspace() == (tmp_path / "myws").resolve()


def test_source_checkout_uses_project_workspace(monkeypatch):
    """When running from a clone, workspace is <project>/workspace/."""
    monkeypatch.delenv("WXE_WORKSPACE", raising=False)
    ws = util.default_workspace()
    # the source checkout used during dev has pyproject.toml three levels above util.py
    assert ws.name == "workspace"
    assert (ws.parent / "pyproject.toml").is_file()


def test_xdg_fallback_when_not_in_checkout(tmp_path, monkeypatch):
    monkeypatch.delenv("WXE_WORKSPACE", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    # simulate "not a source checkout" by pointing at a fake util.py file
    monkeypatch.setattr(util, "_looks_like_source_checkout", lambda p: False)
    ws = util.default_workspace()
    assert ws == (tmp_path / "xdg" / "wxextract").resolve()


def test_xdg_default_when_var_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("WXE_WORKSPACE", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(util, "_looks_like_source_checkout", lambda p: False)
    monkeypatch.setattr(util.Path, "home", lambda: tmp_path)
    ws = util.default_workspace()
    assert ws == (tmp_path / ".local" / "share" / "wxextract").resolve()
