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
