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
