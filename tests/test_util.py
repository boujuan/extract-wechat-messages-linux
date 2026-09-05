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


# ── sudo support (issue #1) ────────────────────────────────────────────────

def test_invoking_sudo_user(monkeypatch):
    monkeypatch.setattr(util.os, "geteuid", lambda: 1000)
    monkeypatch.setenv("SUDO_USER", "alice")
    assert util.invoking_sudo_user() is None  # not root → not a sudo context

    monkeypatch.setattr(util.os, "geteuid", lambda: 0)
    assert util.invoking_sudo_user() == "alice"
    monkeypatch.setenv("SUDO_USER", "root")
    assert util.invoking_sudo_user() is None  # root login, not via sudo
    monkeypatch.delenv("SUDO_USER")
    assert util.invoking_sudo_user() is None


class _FakePwd:
    def __init__(self, pw_dir: str):
        self.pw_dir = pw_dir


def test_sudo_user_home(monkeypatch):
    import pwd as pwd_mod

    monkeypatch.setattr(util.os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_USER", "alice")
    monkeypatch.setattr(pwd_mod, "getpwnam", lambda name: _FakePwd("/home/alice"))
    assert util.sudo_user_home() == Path("/home/alice")

    monkeypatch.setattr(util.os, "geteuid", lambda: 1000)
    assert util.sudo_user_home() is None


def test_default_workspace_sudo_maps_to_invoking_user(monkeypatch):
    import pwd as pwd_mod

    monkeypatch.delenv("WXE_WORKSPACE", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(util, "_looks_like_source_checkout", lambda p: False)
    monkeypatch.setattr(util.os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_USER", "alice")
    monkeypatch.setattr(pwd_mod, "getpwnam", lambda name: _FakePwd("/home/alice"))
    assert util.default_workspace() == Path("/home/alice/.local/share/wxextract")


def test_chown_to_invoking_user_noop_when_not_root(tmp_path, monkeypatch):
    f = tmp_path / "f.txt"
    f.write_text("x")
    monkeypatch.setattr(util.os, "geteuid", lambda: 1000)
    monkeypatch.setenv("SUDO_UID", "1000")
    monkeypatch.setenv("SUDO_GID", "1000")
    util.chown_to_invoking_user(f)  # must be a silent no-op

    monkeypatch.setattr(util.os, "geteuid", lambda: 0)
    monkeypatch.delenv("SUDO_UID", raising=False)
    util.chown_to_invoking_user(f)  # root but no SUDO_UID → no-op


def test_chown_to_invoking_user_recurses(tmp_path, monkeypatch):
    monkeypatch.setattr(util.os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_UID", "1000")
    monkeypatch.setenv("SUDO_GID", "1000")
    d = tmp_path / "ws"
    (d / "sub").mkdir(parents=True)
    (d / "a").write_text("x")
    (d / "sub" / "b").write_text("y")

    seen: list[tuple[Path, int, int]] = []
    monkeypatch.setattr(util.os, "chown", lambda p, u, g: seen.append((Path(p), u, g)))
    util.chown_to_invoking_user(d)

    assert len(seen) == 4  # dir + sub + a + b
    assert all(u == 1000 and g == 1000 for _, u, g in seen)
    assert {p.name for p, _, _ in seen} == {"ws", "sub", "a", "b"}
