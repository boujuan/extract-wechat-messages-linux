"""Tests for wxextract.passphrase — WeChat ≥4.1 passphrase-based recovery.

Pure-crypto tests build synthetic SQLCipher page-1 images whose HMAC matches
a known passphrase, so derivation/validation runs end-to-end without WeChat.
The live-capture test is opt-in (restarts WeChat and needs a manual login
click): set WXE_TEST_LIVE_CAPTURE=1 and WXE_TEST_DB_STORAGE=<db_storage>.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import stat
import struct
from pathlib import Path

import pytest

from wxextract import passphrase as pp
from wxextract.keys import PAGE_SZ, SALT_SZ, DbFile, verify_enc_key


def _make_page1(passphrase: bytes, salt: bytes) -> bytes:
    """Craft a 4096-byte SQLCipher page 1 whose stored HMAC validates for
    `passphrase` (same math as keys.verify_enc_key)."""
    key = pp.pbkdf2_key(passphrase, salt)
    page = bytearray(os.urandom(PAGE_SZ))
    page[:SALT_SZ] = salt
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", key, mac_salt, 2, dklen=32)
    mac = hmac.digest(mac_key, bytes(page[16:PAGE_SZ - 80 + 16]) + struct.pack("<I", 1), "sha512")
    page[PAGE_SZ - 64:] = mac
    return bytes(page)


def _make_db(rel: str, passphrase: bytes) -> DbFile:
    salt = os.urandom(SALT_SZ)
    return DbFile(rel=rel, abs=Path(f"/virtual/{rel}"), size=PAGE_SZ,
                  salt_hex=salt.hex(), page1=_make_page1(passphrase, salt))


PASSPHRASE = bytes(range(32))  # deterministic test passphrase


@pytest.fixture(scope="module")
def three_dbs() -> list[DbFile]:
    return [_make_db(f"msg/{i}.db", PASSPHRASE) for i in range(3)]


# ── derivation ─────────────────────────────────────────────────────────────

def test_pbkdf2_key_matches_sqlcipher_params():
    salt = bytes(16)
    key = pp.pbkdf2_key(b"x" * 32, salt, iterations=256000)
    assert len(key) == 32
    # independent re-computation
    assert key == hashlib.pbkdf2_hmac("sha512", b"x" * 32, salt, 256000, dklen=32)


def test_derive_keys_validates_all_dbs(three_dbs):
    keys = pp.derive_keys(PASSPHRASE, three_dbs, parallel=False)
    assert set(keys) == {db.rel for db in three_dbs}
    for db in three_dbs:
        assert verify_enc_key(bytes.fromhex(keys[db.rel]), db.page1)


def test_derive_keys_rejects_wrong_passphrase(three_dbs):
    assert pp.derive_keys(b"\xff" * 32, three_dbs, parallel=False) == {}


def test_derive_keys_parallel_matches_serial(three_dbs):
    serial = pp.derive_keys(PASSPHRASE, three_dbs, parallel=False)
    parallel = pp.derive_keys(PASSPHRASE, three_dbs, parallel=True)
    assert serial == parallel


# ── cache roundtrip ────────────────────────────────────────────────────────

def test_passphrase_cache_roundtrip(tmp_path: Path):
    info = pp.PassphraseInfo(passphrase=PASSPHRASE, binary="/opt/wechat/wechat",
                             hook_off=0x8790170, captured_at="2026-09-05T00:00:00")
    path = tmp_path / "passphrase.json"
    pp.save_passphrase(path, info)
    assert path.stat().st_mode & 0o777 == 0o600
    assert pp.load_passphrase(path) == PASSPHRASE


def test_load_passphrase_tolerates_corrupt_cache(tmp_path: Path):
    bad = tmp_path / "passphrase.json"
    bad.write_text("{not json")
    assert pp.load_passphrase(bad) is None
    bad.write_text('{"passphrase": "abcd"}')  # wrong length
    assert pp.load_passphrase(bad) is None


# ── ELF static analysis ────────────────────────────────────────────────────

REAL_BINARY = Path("/opt/wechat/wechat")

pytestmark_real = pytest.mark.skipif(
    not REAL_BINARY.is_file(), reason="no /opt/wechat/wechat on this machine"
)


@pytestmark_real
def test_find_hook_offset_on_real_binary():
    off = pp.find_hook_offset(REAL_BINARY)
    assert isinstance(off, int) and off > 0
    # must land inside .text, on the prologue bytes we search for
    text_va, text = pp._elf_sections(REAL_BINARY)[".text"]
    assert text_va <= off < text_va + len(text)
    assert text[off - text_va: off - text_va + 3] == b"\x55\x41\x57"


@pytestmark_real
def test_find_hook_offset_rejects_non_wechat_elf():
    with pytest.raises(pp.HookNotFoundError):
        pp.find_hook_offset(Path("/usr/bin/ls"))


# ── live capture (opt-in; restarts WeChat, needs a manual login click) ─────

@pytest.mark.skipif(
    not (os.environ.get("WXE_TEST_LIVE_CAPTURE") and os.environ.get("WXE_TEST_DB_STORAGE")),
    reason="set WXE_TEST_LIVE_CAPTURE=1 and WXE_TEST_DB_STORAGE=<db_storage> to run",
)
def test_live_capture_derives_all_keys():
    from wxextract import lifecycle
    from wxextract.keys import collect_dbs

    db_storage = Path(os.environ["WXE_TEST_DB_STORAGE"]).expanduser()
    db_files, salt_to_rels = collect_dbs(db_storage)
    lifecycle.close_wechat()
    ph = pp.capture_passphrase(
        REAL_BINARY, timeout=180.0,
        probe_page1=db_files[0].page1, launch_cmd=["/usr/bin/wechat"],
    )
    keys = pp.derive_keys(ph, db_files)
    assert len(keys) == len(db_files), f"only {len(keys)}/{len(db_files)} keyed"
    # health: no wechat thread left job-stopped after the tracer detached
    import time as _t
    _t.sleep(2)
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if os.readlink(f"/proc/{entry.name}/exe") != str(REAL_BINARY):
                continue
        except OSError:
            continue
        for task in (entry / "task").iterdir():
            try:
                state = (task / "stat").read_text().split()[2]
            except OSError:
                continue
            assert state != "T", f"thread {task} left stopped by the tracer"
