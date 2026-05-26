"""Stats computation tests."""
from wxextract import stats


def test_compute_returns_counts(messages, contact_record):
    c = stats.compute(messages, contact_record, my_label="Me", top_n=5)
    assert c.total == len(messages)
    assert c.first_ts == messages[0].create_time
    assert c.last_ts == messages[-1].create_time
    assert c.active_days >= 1


def test_compute_by_sender_is_complete(messages, contact_record):
    c = stats.compute(messages, contact_record, my_label="Me")
    assert sum(c.by_sender.values()) == len(messages)


def test_compute_hourly_distribution_fits_24_buckets(messages, contact_record):
    c = stats.compute(messages, contact_record, my_label="Me")
    assert all(0 <= h <= 23 for h in c.by_hour.keys())


def test_compute_top_emojis_and_words(messages, contact_record):
    c = stats.compute(messages, contact_record, my_label="Me", top_n=5)
    # for a real chat we should have several common emojis or words
    assert len(c.top_emojis) >= 1 or len(c.top_words) >= 5


def test_compute_response_times_populated(messages, contact_record):
    c = stats.compute(messages, contact_record, my_label="Me")
    # at least one back-and-forth in any non-trivial chat
    assert len(c.response_time_seconds) >= 1


def test_empty_messages_returns_zero_counts(contact_record):
    c = stats.compute([], contact_record, my_label="Me")
    assert c.total == 0
    assert c.active_days == 0


# ── synthetic timeline locks down the chain + response-time semantics ─────

def _synth_msg(ts, who, contact_username="rachel_id"):
    from types import SimpleNamespace
    return SimpleNamespace(
        local_id=ts, server_id=0, create_time=ts, sender_id=0,
        sender_username=contact_username if who == "R" else "wxid_self",
        is_me=(who == "M"),
        type=1, sub_type=0, raw_local_type=1,
        content="", source="", status=0,
    )


def test_chain_to_chain_response_time_semantics():
    """Chain switch produces exactly one sample on the responding side.
    Sample count for the two directions differs by at most 1."""
    from wxextract.contacts import ContactRecord
    rachel = ContactRecord(username="rachel_id", alias="rachel", nick_name="Rachel",
                           remark="", local_type=1)
    # Timeline:   R R . M M M . R . M M . R R
    # Chains:     [RR]  [MMM]  [R]  [MM]  [RR]
    # Switches:   R→M       M→R    R→M   M→R
    msgs = [_synth_msg(0, "R"),  _synth_msg(10, "R"),
            _synth_msg(60, "M"), _synth_msg(65, "M"), _synth_msg(70, "M"),
            _synth_msg(120, "R"),
            _synth_msg(180, "M"), _synth_msg(190, "M"),
            _synth_msg(300, "R"), _synth_msg(310, "R")]
    c = stats.compute(msgs, rachel, my_label="Me")

    # Me's reply latency (after Rachel ends a chain, time until my first reply):
    # 1st chain switch R→M at t=60 after R closed at t=10 → 60-0 = 60s (vs LAST-R t=10 would be 50s; we use FIRST)
    # 2nd chain switch R→M at t=180 after R closed at t=120 → 180-120 = 60s
    assert c.response_time_seconds == [60, 60]

    # Rachel's reply latency (after Me ends a chain, time until her first reply):
    # M→R at t=120 after M started at t=60 → 120-60 = 60s
    # M→R at t=300 after M started at t=180 → 300-180 = 120s
    assert c.their_response_time_seconds == [60, 120]

    # Chain bookkeeping
    assert c.my_chain_lengths == [3, 2]
    assert c.their_chain_lengths == [2, 1, 2]
    assert c.chain_starts_me == 2
    assert c.chain_starts_them == 3

    # Sample-count parity property: the two reply-time lists differ by at most 1
    assert abs(len(c.response_time_seconds) -
               len(c.their_response_time_seconds)) <= 1


def test_no_samples_when_one_sided():
    """If only one person talks, neither response-time array has samples."""
    from wxextract.contacts import ContactRecord
    rachel = ContactRecord(username="rachel_id", alias="rachel", nick_name="Rachel",
                           remark="", local_type=1)
    msgs = [_synth_msg(i * 10, "R") for i in range(5)]  # only R speaks
    c = stats.compute(msgs, rachel, my_label="Me")
    assert c.response_time_seconds == []
    assert c.their_response_time_seconds == []
    assert c.chain_starts_them == 1   # one big chain
    assert c.chain_starts_me == 0
    assert c.their_chain_lengths == [5]
