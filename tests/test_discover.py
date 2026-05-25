from pathlib import Path

import pytest

from wxextract.discover import discover, find_data_root, list_accounts


def test_find_data_root_on_this_system():
    """Smoke test: this machine has WeChat installed, so find_data_root() must return."""
    root = find_data_root()
    assert root.is_dir()
    assert root.name == "xwechat_files"


def test_list_accounts():
    root = find_data_root()
    accounts = list_accounts(root)
    assert len(accounts) >= 1
    assert all(a.name.startswith("wxid_") for a in accounts)


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
