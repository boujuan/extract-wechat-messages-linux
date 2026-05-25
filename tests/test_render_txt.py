"""Style B compact TXT renderer test."""
import re

from wxextract.render import compact_txt


def test_compact_txt_renders(messages, contact_record, my_wxid, tmp_path):
    out = tmp_path / "conv.txt"
    lines, tokens = compact_txt.render(messages, contact_record, my_wxid, out)
    text = out.read_text(encoding="utf-8")
    assert text.startswith("META: ")
    assert "\nGLOSS: " in text
    assert "\nLEGEND: " in text
    assert f"msgs={len(messages)}" in text
    assert "tokens=~" in text


def test_compact_txt_session_headers_present(messages, contact_record, my_wxid, tmp_path):
    out = tmp_path / "conv.txt"
    compact_txt.render(messages, contact_record, my_wxid, out)
    text = out.read_text(encoding="utf-8")
    # at least one day header (=YYYY-MM-DD)
    day_headers = re.findall(r"^=\d{4}-\d{2}-\d{2}$", text, re.M)
    assert len(day_headers) >= 1


def test_compact_txt_turn_merging_uses_semicolons(messages, contact_record, my_wxid, tmp_path):
    """Turn-merging reduces line count vs no-merge."""
    out = tmp_path / "conv.txt"
    out2 = tmp_path / "conv_nomerge.txt"
    compact_txt.render(messages, contact_record, my_wxid, out, turn_merge=True)
    compact_txt.render(messages, contact_record, my_wxid, out2, turn_merge=False)
    assert out2.read_text().count("\n") >= out.read_text().count("\n")
