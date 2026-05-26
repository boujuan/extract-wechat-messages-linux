"""Per-contact state JSON for --since-last."""
import json

import pytest

from wxextract import state


def test_load_empty(tmp_path):
    assert state.load(tmp_path) == {}
    assert state.get(tmp_path, "missing") is None


def test_save_and_get(tmp_path):
    state.save(tmp_path, "alice_42",
               username="wxid_alice", last_local_id=42, last_create_time=1_700_000_000)
    rec = state.get(tmp_path, "alice_42")
    assert rec is not None
    assert rec["last_local_id"] == 42
    assert rec["last_create_time"] == 1_700_000_000
    assert rec["username"] == "wxid_alice"
    assert "last_run_at_iso" in rec
    # readable as JSON
    raw = json.loads((tmp_path / "last_extract.json").read_text())
    assert "alice_42" in raw


def test_save_overwrites_per_alias(tmp_path):
    state.save(tmp_path, "alice_42", username="wxid_alice",
               last_local_id=10, last_create_time=1)
    state.save(tmp_path, "alice_42", username="wxid_alice",
               last_local_id=20, last_create_time=2)
    assert state.get(tmp_path, "alice_42")["last_local_id"] == 20


def test_save_multiple_aliases_coexist(tmp_path):
    state.save(tmp_path, "alice", username="wxid_a", last_local_id=1, last_create_time=1)
    state.save(tmp_path, "bob",   username="wxid_b", last_local_id=2, last_create_time=2)
    data = state.load(tmp_path)
    assert set(data.keys()) == {"alice", "bob"}


def test_clear_alias(tmp_path):
    state.save(tmp_path, "alice", username="wxid_a", last_local_id=1, last_create_time=1)
    state.save(tmp_path, "bob",   username="wxid_b", last_local_id=2, last_create_time=2)
    state.clear(tmp_path, "alice")
    assert state.get(tmp_path, "alice") is None
    assert state.get(tmp_path, "bob") is not None


def test_clear_all(tmp_path):
    state.save(tmp_path, "alice", username="wxid_a", last_local_id=1, last_create_time=1)
    state.clear(tmp_path)
    assert state.load(tmp_path) == {}
    assert not (tmp_path / "last_extract.json").exists()


def test_corrupted_file_returns_empty(tmp_path):
    (tmp_path / "last_extract.json").write_text("not valid json {")
    assert state.load(tmp_path) == {}
