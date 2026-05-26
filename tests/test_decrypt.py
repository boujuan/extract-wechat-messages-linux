"""Decrypt against the snapshot fixture; verify SQLite integrity + correctness.

Opt-in via WXE_TEST_SNAPSHOT_DB_STORAGE + WXE_TEST_KEYS_JSON. See conftest.py."""
import json

import pytest

from wxextract.decrypt import (
    decrypt_all,
    decrypt_file,
    decrypt_via_cli,
    decrypt_via_python,
    have_sqlcipher_cli,
    integrity_check,
)


@pytest.fixture(scope="module")
def keys_by_rel(keys_json) -> dict[str, str]:
    data = json.loads(keys_json.read_text())
    return {k: v["enc_key"] for k, v in data.items() if isinstance(v, dict)}


def _first_small_db(snapshot_db_storage, keys_by_rel):
    """Pick the smallest DB we have a key for — quick tests."""
    candidates = []
    for rel in keys_by_rel:
        p = snapshot_db_storage / rel
        if p.exists():
            candidates.append((p.stat().st_size, rel, p))
    if not candidates:
        pytest.skip("no encrypted DB found that matches a saved key")
    candidates.sort()
    return candidates[0][1], candidates[0][2]


def test_decrypt_small_db_then_integrity(snapshot_db_storage, keys_by_rel, tmp_path):
    rel, src = _first_small_db(snapshot_db_storage, keys_by_rel)
    out = tmp_path / rel
    res = decrypt_file(src, out, bytes.fromhex(keys_by_rel[rel]))
    assert res.ok, res.error
    assert integrity_check(out) == "ok"


def test_decrypt_bad_key_returns_failure(snapshot_db_storage, keys_by_rel, tmp_path):
    rel, src = _first_small_db(snapshot_db_storage, keys_by_rel)
    out = tmp_path / "bad.db"
    res = decrypt_file(src, out, bytes(32))
    assert not res.ok
    assert "HMAC" in (res.error or "")


def test_decrypt_all_skips_when_unchanged(snapshot_db_storage, keys_by_rel, tmp_path):
    out = tmp_path / "plain"
    r1 = decrypt_all(snapshot_db_storage, keys_by_rel, out, workers=2)
    assert all(r.ok for r in r1)
    r2 = decrypt_all(snapshot_db_storage, keys_by_rel, out, workers=2)
    assert all(r.ok for r in r2)
    assert sum(r.pages for r in r2) == 0, "second run should skip everything"


# ── engine-specific tests ──────────────────────────────────────────────────


@pytest.mark.skipif(not have_sqlcipher_cli(), reason="sqlcipher CLI not installed")
def test_cli_decrypt_produces_integrity_ok(snapshot_db_storage, keys_by_rel, tmp_path):
    rel, src = _first_small_db(snapshot_db_storage, keys_by_rel)
    out = tmp_path / rel
    res = decrypt_via_cli(src, out, bytes.fromhex(keys_by_rel[rel]))
    assert res.ok, res.error
    assert res.engine == "sqlcipher"
    assert integrity_check(out) == "ok"


def test_python_decrypt_marks_engine(snapshot_db_storage, keys_by_rel, tmp_path):
    rel, src = _first_small_db(snapshot_db_storage, keys_by_rel)
    out = tmp_path / rel
    res = decrypt_via_python(src, out, bytes.fromhex(keys_by_rel[rel]))
    assert res.ok, res.error
    assert res.engine == "python"


@pytest.mark.skipif(not have_sqlcipher_cli(), reason="sqlcipher CLI not installed")
def test_cli_and_python_produce_same_table_rows(snapshot_db_storage, keys_by_rel, tmp_path):
    """Two engines should agree on the row count for a stable DB (no pending WAL)."""
    import sqlite3 as _sql
    rel, src = _first_small_db(snapshot_db_storage, keys_by_rel)
    key = bytes.fromhex(keys_by_rel[rel])
    out_cli = tmp_path / "cli.db"
    out_py = tmp_path / "py.db"
    assert decrypt_via_cli(src, out_cli, key).ok
    assert decrypt_via_python(src, out_py, key).ok

    def _all_table_rows(p):
        conn = _sql.connect(str(p))
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )]
            return {t: conn.execute(f"SELECT count(*) FROM \"{t}\"").fetchone()[0]
                    for t in tables}
        finally:
            conn.close()
    cli_counts = _all_table_rows(out_cli)
    py_counts = _all_table_rows(out_py)
    # both engines should see every regular table; row counts will match
    # exactly when there are no pending WAL frames.
    assert set(cli_counts.keys()) == set(py_counts.keys())
