"""Test SQLCipher key derivation + validation.

Pure-crypto tests run unconditionally. Round-trip tests need real DBs and
are opt-in via env vars (see conftest.py)."""
from __future__ import annotations

import json

import pytest

from wxextract.keys import (
    PAGE_SZ,
    collect_dbs,
    derive_mac_key,
    verify_enc_key,
)


# ── pure-crypto unit tests ────────────────────────────────────────────────────


def test_derive_mac_key_is_stable():
    """PBKDF2 derivation must be deterministic."""
    enc = b"\x11" * 32
    salt = b"\xab" * 16
    mac = derive_mac_key(enc, salt)
    assert len(mac) == 32
    assert derive_mac_key(enc, salt) == mac  # deterministic
    # changing salt changes mac
    assert derive_mac_key(enc, bytes(b ^ 0xff for b in salt)) != mac


def test_verify_enc_key_rejects_short_input():
    enc = b"\x00" * 32
    assert not verify_enc_key(enc, b"")
    assert not verify_enc_key(enc, b"\x00" * (PAGE_SZ - 1))
    assert not verify_enc_key(b"\x00" * 16, b"\x00" * PAGE_SZ)


# ── real-DB round-trip tests (opt-in) ────────────────────────────────────────


@pytest.fixture(scope="module")
def db_files(snapshot_db_storage):
    files, _salts = collect_dbs(snapshot_db_storage)
    return files


@pytest.fixture(scope="module")
def saved_keys(keys_json) -> dict[str, str]:
    data = json.loads(keys_json.read_text())
    return {k: v["enc_key"] for k, v in data.items() if isinstance(v, dict)}


def test_collect_dbs_finds_some(db_files):
    assert len(db_files) >= 1


def test_verify_enc_key_accepts_saved_keys(db_files, saved_keys):
    """Every db whose key we've recorded must validate page-1 HMAC."""
    matched = 0
    for db in db_files:
        if db.rel not in saved_keys:
            continue
        enc = bytes.fromhex(saved_keys[db.rel])
        assert verify_enc_key(enc, db.page1), f"failed to verify {db.rel}"
        matched += 1
    assert matched >= 1, "no keys could be cross-validated"


def test_verify_enc_key_rejects_wrong_key(db_files, saved_keys):
    """Flipping every bit of the key must make verification fail."""
    db = next(d for d in db_files if d.rel in saved_keys)
    enc = bytes.fromhex(saved_keys[db.rel])
    wrong = bytes(b ^ 0xFF for b in enc)
    assert not verify_enc_key(wrong, db.page1)
