"""Polish tests: emoji squash + PII redaction."""
from wxextract.render import compact_txt
from wxextract.render.common import redact_pii, squash_emoji_runs


def test_squash_emoji_basic():
    assert squash_emoji_runs("[Chuckle][Chuckle][Chuckle]") == "[Chuckle×3]"
    assert squash_emoji_runs("[Smile][Smile]") == "[Smile][Smile]"
    assert squash_emoji_runs("[Facepalm][Facepalm][Facepalm][Facepalm]") == "[Facepalm×4]"
    assert squash_emoji_runs("hi [Wow][Wow][Wow] there") == "hi [Wow×3] there"


def test_squash_emoji_doesnt_touch_text():
    assert squash_emoji_runs("normal text") == "normal text"
    assert squash_emoji_runs("[image]") == "[image]"


def test_redact_email():
    assert "[redacted-email]" in redact_pii("ping me at user@example.com tomorrow")


def test_redact_phone():
    out = redact_pii("call +34 612 345 678 anytime")
    assert "[redacted-phone]" in out


def test_redact_keeps_short_numbers():
    assert redact_pii("year 2026") == "year 2026"


def test_squash_never_increases_tokens(messages, contact_record, my_wxid, tmp_path):
    """End-to-end: squashing should never increase token count."""
    no_squash = tmp_path / "conv.txt"
    yes_squash = tmp_path / "conv_sq.txt"
    _l, t_no = compact_txt.render(messages, contact_record, my_wxid, no_squash, squash=False)
    _l, t_yes = compact_txt.render(messages, contact_record, my_wxid, yes_squash, squash=True)
    assert t_yes <= t_no
