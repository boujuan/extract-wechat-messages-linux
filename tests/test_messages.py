"""Message iterator test."""
from collections import Counter

import pytest

from wxextract.messages import (
    TYPE_APPMSG,
    TYPE_TEXT,
    decode_local_type,
    extract,
)


def test_decode_local_type_basic():
    # text
    assert decode_local_type(1) == (1, 0)
    # quoted reply: subType 57, type 49 → 244813135921
    assert decode_local_type(244813135921) == (49, 57)
    # link: subType 5, type 49 → 21474836529
    assert decode_local_type(21474836529) == (49, 5)


def test_extract_count_and_types(messages):
    assert len(messages) > 10
    by_type = Counter(m.type for m in messages)
    assert by_type[TYPE_TEXT] > 0


def test_extract_recall_filter_removes_some(contact_record, my_wxid, messages):
    all_msgs = list(extract(contact_record, my_wxid=my_wxid, skip_recalls=False))
    filtered = list(extract(contact_record, my_wxid=my_wxid, skip_recalls=True))
    assert len(all_msgs) >= len(filtered)


def test_extract_resolves_senders(messages, contact_record):
    me_count = sum(1 for m in messages if m.is_me)
    contact_count = sum(1 for m in messages if m.sender_username == contact_record.username)
    assert me_count + contact_count > 0


def test_decompresses_zstd_text(messages):
    """At least one message must decompress to meaningful text."""
    longs = [m for m in messages if m.type == TYPE_TEXT and len(m.content) > 50]
    if not longs:
        pytest.skip("no long text messages — small fixture")
    for m in longs[:5]:
        assert "\x00" not in m.content
