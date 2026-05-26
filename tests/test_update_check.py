"""Update-check unit tests. Mocks the network so no real GitHub call is made."""
import json

import pytest

from wxextract import update_check


def test_parse_version_handles_v_prefix_and_extras():
    assert update_check._parse_version("v0.1.0") == (0, 1, 0)
    assert update_check._parse_version("0.1.0") == (0, 1, 0)
    assert update_check._parse_version("v0.2.0-beta") == (0, 2, 0)
    assert update_check._parse_version("1.2.3+build") == (1, 2, 3)
    assert update_check._parse_version("garbage") == (0,)


def test_parse_version_ordering():
    assert update_check._parse_version("0.1.0") < update_check._parse_version("0.1.1")
    assert update_check._parse_version("0.1.0") < update_check._parse_version("0.2.0")
    assert update_check._parse_version("v0.1.0") < update_check._parse_version("v0.10.0")
    assert update_check._parse_version("1.0.0") > update_check._parse_version("0.99.99")


def test_should_check_uses_cache_age(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    # No cache → should check
    assert update_check.should_check() is True
    # Save recent cache → should NOT check
    update_check._save_cache({"checked_at": __import__("time").time()})
    assert update_check.should_check() is False
    # Force overrides
    assert update_check.should_check(force=True) is True


def test_should_check_after_24h(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    update_check._save_cache({"checked_at": __import__("time").time() - 25 * 3600})
    assert update_check.should_check() is True


def test_check_returns_none_when_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("WXE_NO_UPDATE_CHECK", "1")
    assert update_check.check() is None


def test_check_returns_newer_tag(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("WXE_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update_check, "fetch_latest_tag", lambda: "v99.0.0")
    assert update_check.check(force=True) == "v99.0.0"


def test_check_returns_none_when_running_latest(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("WXE_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update_check, "fetch_latest_tag",
                        lambda: "v" + update_check.__version__)
    assert update_check.check(force=True) is None


def test_check_handles_network_failure_silently(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("WXE_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update_check, "fetch_latest_tag", lambda: None)
    assert update_check.check(force=True) is None


def test_notice_mentions_upgrade_command():
    msg = update_check.notice("v0.99.0")
    assert "v0.99.0" in msg
    assert "uv tool upgrade wxextract" in msg


def test_cache_written_after_check(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(update_check, "fetch_latest_tag", lambda: "v0.2.0")
    update_check.check(force=True)
    cache = json.loads((tmp_path / "wxextract" / "last_update_check.json").read_text())
    assert "checked_at" in cache
    assert cache["latest_tag"] == "v0.2.0"
