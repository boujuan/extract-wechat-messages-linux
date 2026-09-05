from pathlib import Path

import pytest

from wxextract.discover import discover, find_data_root, list_accounts


def _wechat_installed() -> bool:
    """True iff this machine has a WeChat install discoverable on disk."""
    try:
        find_data_root()
        return True
    except RuntimeError:
        return False


requires_wechat = pytest.mark.skipif(
    not _wechat_installed(),
    reason="no WeChat install detected on this machine (CI / fresh systems)",
)


@requires_wechat
def test_find_data_root_on_this_system():
    """Smoke test: when WeChat is installed, find_data_root() must return."""
    root = find_data_root()
    assert root.is_dir()
    assert root.name == "xwechat_files"


@requires_wechat
def test_list_accounts():
    root = find_data_root()
    accounts = list_accounts(root)
    assert len(accounts) >= 1
    assert all(a.name.startswith("wxid_") for a in accounts)


@requires_wechat
def test_discover_returns_populated_object():
    d = discover()
    assert d.data_root.is_dir()
    assert d.account_dir.is_dir()
    assert d.my_wxid.startswith("wxid_")
    assert d.db_storage().is_dir()


def test_discover_with_explicit_data_root(tmp_path):
    fake = tmp_path / "xwechat_files"
    fake.mkdir()
    (fake / "wxid_test").mkdir()
    (fake / "wxid_test" / "db_storage").mkdir()
    d = discover(prefer_data_root=fake)
    assert d.data_root == fake
    assert d.my_wxid == "wxid_test"


def test_find_data_root_raises_when_nothing(tmp_path, monkeypatch):
    """Patch the candidate list to point at an empty tmp dir, confirm error."""
    import wxextract.discover as disc
    monkeypatch.setattr(disc, "_DATA_ROOT_CANDIDATES", (str(tmp_path / "nope"),))
    with pytest.raises(RuntimeError):
        find_data_root()


# ── sudo-aware data-root candidates (issue #1) ─────────────────────────────

class _FakePwd:
    def __init__(self, pw_dir: str):
        self.pw_dir = pw_dir


def test_data_root_candidates_sudo_prepends_invoking_user(monkeypatch):
    import pwd as pwd_mod

    from wxextract import discover
    monkeypatch.setattr(discover.os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_USER", "alice")
    monkeypatch.setattr(pwd_mod, "getpwnam", lambda name: _FakePwd("/home/alice"))
    cands = discover._data_root_candidates()
    assert cands[0] == Path("/home/alice/.local/share/WeChat_Data/xwechat_files")
    assert Path("/home/alice/Documents/xwechat_files") in cands
    # the root home is still searched as a fallback
    assert any(str(p).startswith(str(Path.home())) for p in cands)


def test_data_root_candidates_normal_user(monkeypatch):
    from wxextract import discover
    monkeypatch.setattr(discover.os, "geteuid", lambda: 1000)
    monkeypatch.setenv("SUDO_USER", "alice")  # ignored when not root
    cands = discover._data_root_candidates()
    home = Path.home()
    assert cands[0] == home / ".local/share/WeChat_Data/xwechat_files"
    assert not any(str(p).startswith("/home/alice") for p in cands)


def test_data_root_candidates_dedupes_root_login(monkeypatch):
    """sudo as root itself, or SUDO_USER whose home == HOME → no duplicates."""
    import pwd as pwd_mod

    from wxextract import discover
    monkeypatch.setattr(discover.os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_USER", "alice")
    monkeypatch.setattr(pwd_mod, "getpwnam", lambda name: _FakePwd(str(Path.home())))
    cands = discover._data_root_candidates()
    assert len(cands) == len(set(cands)) == len(discover._DATA_ROOT_CANDIDATES)
