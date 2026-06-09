"""WhatsApp JSON ingestion smoke test."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from wxextract import report, stats, whatsapp
from wxextract.messages import TYPE_MEDIA_GENERIC, TYPE_TEXT


def _synth_doc():
    return {
        "schema": "wxextract-whatsapp/1",
        "source": "whatsapp",
        "contact": {
            "username": "bob@whatsapp",
            "alias": "bob_whatsapp",
            "nick_name": "Bob",
            "remark": "",
            "display_name": "Bob",
            "local_type": 1,
            "source": "whatsapp",
            "message_count": 5,
            "first_message_ts": 1_750_000_000,
            "last_message_ts": 1_750_000_400,
        },
        "my_label": "Me",
        "messages": [
            {"local_id": 1, "server_id": 0, "create_time": 1_750_000_000,
             "sender_username": "Me", "is_me": True,
             "type": TYPE_TEXT, "sub_type": 0,
             "content": "hi Bob", "source": "whatsapp",
             "raw_local_type": TYPE_TEXT, "status": 3},
            {"local_id": 2, "server_id": 0, "create_time": 1_750_000_100,
             "sender_username": "Bob", "is_me": False,
             "type": TYPE_TEXT, "sub_type": 0,
             "content": "hey! how are you?", "source": "whatsapp",
             "raw_local_type": TYPE_TEXT, "status": 3},
            {"local_id": 3, "server_id": 0, "create_time": 1_750_000_200,
             "sender_username": "Bob", "is_me": False,
             "type": TYPE_TEXT, "sub_type": 0,
             "content": "good, you?", "source": "whatsapp",
             "raw_local_type": TYPE_TEXT, "status": 3},
            {"local_id": 4, "server_id": 0, "create_time": 1_750_000_300,
             "sender_username": "Me", "is_me": True,
             "type": TYPE_MEDIA_GENERIC, "sub_type": 0,
             "content": "", "source": "whatsapp",
             "raw_local_type": TYPE_MEDIA_GENERIC, "status": 3},
            {"local_id": 5, "server_id": 0, "create_time": 1_750_000_400,
             "sender_username": "Me", "is_me": True,
             "type": TYPE_TEXT, "sub_type": 0,
             "content": "here's a photo 📸", "source": "whatsapp",
             "raw_local_type": TYPE_TEXT, "status": 3},
        ],
    }


def test_load_whatsapp_json(tmp_path):
    p = tmp_path / "bob.json"
    p.write_text(json.dumps(_synth_doc()), encoding="utf-8")
    contact, msgs, my_label = whatsapp.load_whatsapp_json(p)
    assert contact.display_name == "Bob"
    assert contact.source == "whatsapp"
    assert my_label == "Me"
    assert len(msgs) == 5
    assert sum(1 for m in msgs if m.is_me) == 3
    assert sum(1 for m in msgs if m.type == TYPE_MEDIA_GENERIC) == 1


def test_load_rejects_bad_schema(tmp_path):
    doc = _synth_doc()
    doc["schema"] = "not-our-schema"
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported schema"):
        whatsapp.load_whatsapp_json(p)


def test_stats_compute_runs_on_whatsapp(tmp_path):
    p = tmp_path / "bob.json"
    p.write_text(json.dumps(_synth_doc()), encoding="utf-8")
    contact, msgs, _ = whatsapp.load_whatsapp_json(p)
    c = stats.compute(msgs, contact, my_label="Me", top_n=5)
    assert c.total == 5
    assert c.by_type[TYPE_TEXT] == 4
    assert c.by_type[TYPE_MEDIA_GENERIC] == 1
    # one valid chain switch: me → them at msg 2, then them → me at msg 4
    assert c.chain_starts_me >= 1
    assert c.chain_starts_them >= 1


def test_render_report_with_whatsapp_pair(tmp_path):
    p = tmp_path / "bob.json"
    p.write_text(json.dumps(_synth_doc()), encoding="utf-8")
    contact, msgs, _ = whatsapp.load_whatsapp_json(p)
    c = stats.compute(msgs, contact, my_label="Me", top_n=5)
    out = tmp_path / "report.html"
    n = report.render_report([(contact, c)], my_label="Me", out_path=out)
    assert n == 1
    text = out.read_text(encoding="utf-8")
    assert "Bob" in text
    assert "WhatsApp" in text
    assert "Plotly.newPlot" in text
    assert text.count('<script type="application/json"') >= 4


def test_build_combined_merges_messages(tmp_path):
    p = tmp_path / "bob.json"
    p.write_text(json.dumps(_synth_doc()), encoding="utf-8")
    contact_wa, msgs_wa, _ = whatsapp.load_whatsapp_json(p)
    # Synthesize a tiny WeChat-side pair so we can call build_combined.
    from wxextract.contacts import ContactRecord
    from wxextract.messages import Message
    contact_we = ContactRecord(
        username="wxid_test", alias="bob_wxid",
        nick_name="Bob", remark="", local_type=1,
        message_count=2, first_ts=1_770_000_000, last_ts=1_770_000_010,
        source="wechat",
    )
    msgs_we = [
        Message(local_id=1, server_id=0, create_time=1_770_000_000,
                sender_id=0, sender_username="wxid_test", is_me=False,
                type=TYPE_TEXT, sub_type=0, raw_local_type=TYPE_TEXT,
                content="WeChat-side hi", source="", status=3),
        Message(local_id=2, server_id=0, create_time=1_770_000_010,
                sender_id=0, sender_username="me_wxid", is_me=True,
                type=TYPE_TEXT, sub_type=0, raw_local_type=TYPE_TEXT,
                content="reply", source="", status=3),
    ]
    combined_contact, combined_msgs = whatsapp.build_combined(
        (contact_wa, msgs_wa), (contact_we, msgs_we),
        display_name="Bob",
        alias="bob_combined",
    )
    assert combined_contact.source == "combined"
    assert combined_contact.display_name == "Bob"
    assert len(combined_msgs) == 7
    # Sorted by create_time → WhatsApp range first, then WeChat
    assert combined_msgs[0].create_time == 1_750_000_000
    assert combined_msgs[-1].create_time == 1_770_000_010
    # local_ids re-numbered
    assert [m.local_id for m in combined_msgs] == list(range(1, 8))


def test_build_combined_many_three_sources():
    """N-way merge: 3 sources interleave into one sorted, renumbered timeline."""
    from wxextract.contacts import ContactRecord
    from wxextract.messages import Message

    def _pair(uname, source, ts_list):
        c = ContactRecord(username=uname, alias=uname, nick_name=uname.title(),
                          remark="", local_type=1, source=source)
        msgs = [Message(local_id=i, server_id=0, create_time=ts, sender_id=0,
                        sender_username=uname, is_me=(i % 2 == 0),
                        type=TYPE_TEXT, sub_type=0, raw_local_type=TYPE_TEXT,
                        content=f"{source}-{ts}", source="", status=3)
                for i, ts in enumerate(ts_list, start=1)]
        return c, msgs

    we = _pair("wxid_a", "wechat", [300, 100])
    wa = _pair("a@whatsapp", "whatsapp", [200, 400])
    ig = _pair("a@instagram", "instagram", [150, 500])
    contact, msgs = whatsapp.build_combined_many(
        [we, wa, ig], display_name="Alice", alias="alice")
    assert contact.source == "combined"
    assert contact.display_name == "Alice" and contact.alias == "alice"
    assert len(msgs) == 6
    # fully interleaved by create_time across all three sources
    assert [m.create_time for m in msgs] == [100, 150, 200, 300, 400, 500]
    assert [m.local_id for m in msgs] == [1, 2, 3, 4, 5, 6]
    # non-me senders unified to one username; is_me preserved
    them = {m.sender_username for m in msgs if not m.is_me}
    assert them == {contact.username}
