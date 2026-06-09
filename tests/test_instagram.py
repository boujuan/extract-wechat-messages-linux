"""Instagram source: loader + export/API normalization smoke tests."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from wxextract import instagram, stats
from wxextract.messages import (
    APPMSG_LINK,
    TYPE_APPMSG,
    TYPE_CALL,
    TYPE_IMAGE,
    TYPE_TEXT,
    TYPE_VOICE,
)


def _mojibake(s: str) -> str:
    """Produce Instagram's export mojibake form of a clean UTF-8 string."""
    return s.encode("utf-8").decode("latin-1")


# ---------------------------------------------------------------------------
# loader
# ---------------------------------------------------------------------------


def _synth_doc():
    return {
        "schema": "wxextract-instagram/1",
        "source": "instagram",
        "contact": {
            "username": "alice_123@instagram", "alias": "alice",
            "nick_name": "Alice", "remark": "", "display_name": "Alice",
            "local_type": 1, "source": "instagram",
            "message_count": 3, "first_message_ts": 1_750_000_000,
            "last_message_ts": 1_750_000_200,
        },
        "my_label": "Bob",
        "messages": [
            {"local_id": 1, "server_id": 0, "create_time": 1_750_000_000,
             "sender_username": "Bob", "is_me": True, "type": TYPE_TEXT,
             "sub_type": 0, "content": "hi", "source": "instagram",
             "raw_local_type": TYPE_TEXT, "status": 3},
            {"local_id": 2, "server_id": 0, "create_time": 1_750_000_100,
             "sender_username": "Alice", "is_me": False, "type": TYPE_TEXT,
             "sub_type": 0, "content": "hey", "source": "instagram",
             "raw_local_type": TYPE_TEXT, "status": 3},
            {"local_id": 3, "server_id": 0, "create_time": 1_750_000_200,
             "sender_username": "Alice", "is_me": False, "type": TYPE_VOICE,
             "sub_type": 0, "content": "", "source": "instagram",
             "raw_local_type": TYPE_VOICE, "status": 3},
        ],
    }


def test_load_instagram_json(tmp_path):
    p = tmp_path / "alice.json"
    p.write_text(json.dumps(_synth_doc()), encoding="utf-8")
    contact, msgs, my_label = instagram.load_instagram_json(p)
    assert contact.display_name == "Alice"
    assert contact.source == "instagram"
    assert my_label == "Bob"
    assert len(msgs) == 3
    assert sum(1 for m in msgs if m.is_me) == 1
    # non-me sender collapsed to contact.username so stats classify "them"
    assert all(m.sender_username == contact.username for m in msgs if not m.is_me)
    c = stats.compute(msgs, contact, my_label="Me", top_n=5)
    assert c.total == 3
    assert c.by_type[TYPE_TEXT] == 2
    assert c.by_type[TYPE_VOICE] == 1


def test_load_rejects_bad_schema(tmp_path):
    doc = _synth_doc()
    doc["schema"] = "wxextract-whatsapp/1"
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported schema"):
        instagram.load_instagram_json(p)


# ---------------------------------------------------------------------------
# export normalization (the real DYI shape)
# ---------------------------------------------------------------------------

OTHER = "Älïce 🌮"   # accent + emoji → exercises the mojibake round-trip


def _export_messages():
    return [
        {"sender_name": "Bob", "timestamp_ms": 1_780_000_002_000,
         "content": _mojibake("Hola 😆"), "is_geoblocked_for_viewer": False,
         "is_unsent_image_by_messenger_kid_parent": False},
        {"sender_name": _mojibake(OTHER), "timestamp_ms": 1_780_000_001_000,
         "content": "hey there"},
        {"sender_name": "Bob", "timestamp_ms": 1_780_000_003_000,
         "content": "You sent an attachment.",
         "share": {"link": "https://instagram.com/reel/X",
                   "share_text": _mojibake("wow 😎")}},
        {"sender_name": "Bob", "timestamp_ms": 1_780_000_004_000,
         "photos": [{"uri": "…/photos/1.jpg", "creation_timestamp": 1}]},
        {"sender_name": _mojibake(OTHER), "timestamp_ms": 1_780_000_005_000,
         "audio_files": [{"uri": "…/audio/1.ogg"}]},
        {"sender_name": "Bob", "timestamp_ms": 1_780_000_006_000,
         "content": "Video chat ended", "call_duration": 42},
        {"sender_name": "Bob", "timestamp_ms": 1_780_000_007_000,
         "content": "deleted", "is_unsent": True},
    ]


def test_normalize_export_messages():
    recs = instagram._normalize_export_messages(_export_messages(), me_name="Bob")
    # 7 inputs minus 1 unsent = 6, sorted ascending by ts
    assert len(recs) == 6
    assert [r["create_time"] for r in recs] == sorted(r["create_time"] for r in recs)
    # ms → seconds
    assert recs[0]["create_time"] == 1_780_000_001
    # mojibake fixed on sender + content
    assert recs[0]["sender_name"] == OTHER
    text = next(r for r in recs if r["type"] == TYPE_TEXT and "Hola" in r["content"])
    assert text["content"] == "Hola 😆"
    assert text["is_me"] is True
    # share → appmsg/link with synthesized XML (mojibake fixed inside)
    share = next(r for r in recs if r["type"] == TYPE_APPMSG)
    assert share["sub_type"] == APPMSG_LINK
    assert "wow 😎" in share["content"] and "instagram.com/reel/X" in share["content"]
    # media + call types
    assert any(r["type"] == TYPE_IMAGE for r in recs)
    assert any(r["type"] == TYPE_VOICE for r in recs)
    call = next(r for r in recs if r["type"] == TYPE_CALL)
    assert call["content"] == "Call ended 42s"


def _make_export_zip(tmp_path) -> Path:
    title = _mojibake(OTHER)
    doc = {
        "participants": [{"name": title}, {"name": "Bob"}],
        "messages": _export_messages(),
        "title": title,
        "is_still_participant": True,
        "thread_path": "inbox/alice_123",
        "magic_words": [],
    }
    zpath = tmp_path / "export.zip"
    arc = "your_instagram_activity/messages/inbox/alice_123/message_1.json"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(arc, json.dumps(doc))
    return zpath


def test_fetch_from_export_end_to_end(tmp_path):
    zpath = _make_export_zip(tmp_path)
    # listing finds the thread with the de-mojibake'd display name
    rows = instagram.list_threads(zpath)
    assert len(rows) == 1
    assert rows[0]["slug"] == "alice"
    assert rows[0]["display_name"] == OTHER
    # fetch by slug substring → one wxextract-instagram doc
    docs = instagram.fetch_from_export(zpath, "alice")
    assert len(docs) == 1
    doc = docs[0]
    assert doc["schema"] == "wxextract-instagram/1"
    assert doc["contact"]["alias"] == "alice"
    assert doc["contact"]["username"] == "alice_123@instagram"
    assert doc["my_label"] == "Bob"               # auto-derived (participant != title)
    assert doc["contact"]["message_count"] == 6
    # round-trips through the loader and stats
    out = tmp_path / "alice.json"
    instagram.write_doc(doc, out)
    contact, msgs, _ = instagram.load_instagram_json(out)
    assert len(msgs) == 6
    assert sum(1 for m in msgs if m.is_me) == 4   # Bob sent 4 of the 6


# ---------------------------------------------------------------------------
# private-API / console-dump normalization
# ---------------------------------------------------------------------------


def test_normalize_thread_items():
    items = [
        {"item_type": "text", "timestamp": 1_780_000_002_000_000,
         "user_id": 111, "text": "from me"},
        {"item_type": "text", "timestamp": 1_780_000_001_000_000,
         "user_id": 222, "text": "from them"},
        {"item_type": "voice_media", "timestamp": 1_780_000_003_000_000, "user_id": 222},
        {"item_type": "action_log", "timestamp": 1_780_000_004_000_000, "user_id": 222},
    ]
    recs = instagram._normalize_thread_items(
        items, my_pk="111", users_by_pk={"111": "Me", "222": "Them"})
    assert len(recs) == 3                       # action_log skipped
    # microseconds → seconds, sorted ascending
    assert recs[0]["create_time"] == 1_780_000_001
    assert recs[0]["sender_name"] == "Them" and recs[0]["is_me"] is False
    assert any(r["is_me"] for r in recs)
    assert any(r["type"] == TYPE_VOICE for r in recs)


def test_derive_me_and_groups():
    assert instagram._derive_me("Alice", ["Alice", "Bob"], None) == "Bob"
    assert instagram._derive_me("Alice", ["Alice", "Bob"], "Override") == "Override"
    with pytest.raises(ValueError, match="group thread"):
        instagram._derive_me("Group", ["A", "B", "C"], None)
