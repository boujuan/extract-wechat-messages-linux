"""Shared test fixtures.

Tests that touch real decrypted databases are opt-in via environment variables:

    WXE_TEST_PLAIN_DBS=/path/to/workspace/plain_dbs   (required)
    WXE_TEST_ALIAS=<wechat-id-of-a-known-contact>     (for per-contact tests)
    WXE_TEST_MY_WXID=<your-own-wxid>                  (for sender-resolution tests)
    WXE_TEST_SNAPSHOT_DB_STORAGE=/path/to/.../db_storage  (for decrypt round-trip tests)
    WXE_TEST_KEYS_JSON=/path/to/all_keys.json         (for decrypt round-trip tests)

If unset, the relevant tests are skipped.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def _env_path(name: str) -> Path | None:
    v = os.environ.get(name, "").strip()
    return Path(v).expanduser() if v else None


def _env_str(name: str) -> str | None:
    v = os.environ.get(name, "").strip()
    return v or None


@pytest.fixture(scope="session")
def plain_dbs() -> Path:
    p = _env_path("WXE_TEST_PLAIN_DBS")
    if p is None or not p.is_dir():
        pytest.skip("WXE_TEST_PLAIN_DBS not set or not a directory")
    return p


@pytest.fixture(scope="session")
def test_alias() -> str:
    v = _env_str("WXE_TEST_ALIAS")
    if v is None:
        pytest.skip("WXE_TEST_ALIAS not set")
    return v


@pytest.fixture(scope="session")
def my_wxid() -> str:
    v = _env_str("WXE_TEST_MY_WXID")
    if v is None:
        pytest.skip("WXE_TEST_MY_WXID not set")
    return v


@pytest.fixture(scope="session")
def snapshot_db_storage() -> Path:
    p = _env_path("WXE_TEST_SNAPSHOT_DB_STORAGE")
    if p is None or not p.is_dir():
        pytest.skip("WXE_TEST_SNAPSHOT_DB_STORAGE not set or not a directory")
    return p


@pytest.fixture(scope="session")
def keys_json() -> Path:
    p = _env_path("WXE_TEST_KEYS_JSON")
    if p is None or not p.is_file():
        pytest.skip("WXE_TEST_KEYS_JSON not set or not a file")
    return p


@pytest.fixture(scope="module")
def contact_record(plain_dbs, test_alias):
    """Resolve the test contact record by alias."""
    from wxextract.contacts import find_by_alias, load_contacts
    recs = load_contacts(plain_dbs)
    rec = find_by_alias(recs, test_alias)
    if rec is None:
        pytest.skip(f"alias {test_alias!r} not found in contact.db")
    return rec


@pytest.fixture(scope="module")
def messages(contact_record, my_wxid):
    """All messages with the test contact, recall-filtered."""
    from wxextract.messages import extract
    return list(extract(contact_record, my_wxid=my_wxid))


@pytest.fixture(scope="module")
def messages_with_recalls(contact_record, my_wxid):
    from wxextract.messages import extract
    return list(extract(contact_record, my_wxid=my_wxid, skip_recalls=False))
