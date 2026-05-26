"""Markdown renderer test."""
import re

from wxextract.render import markdown


def test_md_renders_frontmatter(messages, contact_record, my_wxid, tmp_path):
    out = tmp_path / "conv.md"
    markdown.render(messages, contact_record, my_wxid, out)
    text = out.read_text(encoding="utf-8")
    # frontmatter delimited by ---
    assert text.startswith("---\n")
    # frontmatter fields
    assert f"messages: {len(messages)}\n" in text
    assert f"contact: {contact_record.alias}\n" in text or \
           f'contact: "{contact_record.alias}"\n' in text
    assert "glossary:\n" in text
    assert "tokens: ~" in text
    # H1 title appears below frontmatter
    assert f"# Conversation with {contact_record.display_name}" in text


def test_md_has_day_headers(messages, contact_record, my_wxid, tmp_path):
    out = tmp_path / "conv.md"
    markdown.render(messages, contact_record, my_wxid, out)
    text = out.read_text(encoding="utf-8")
    days = re.findall(r"^## (\d{4}-\d{2}-\d{2})$", text, re.M)
    assert len(days) >= 1
    # days are unique and chronological
    assert days == sorted(set(days))


def test_md_per_message_format(messages, contact_record, my_wxid, tmp_path):
    out = tmp_path / "conv.md"
    markdown.render(messages, contact_record, my_wxid, out)
    text = out.read_text(encoding="utf-8")
    # at least one bolded "**HH:MM:SS X**" line
    msg_lines = re.findall(r"\*\*\d{2}:\d{2}:\d{2} [A-Z?]\*\* —", text)
    assert len(msg_lines) > 10


def test_md_quoted_replies_are_blockquotes(messages, contact_record, my_wxid, tmp_path):
    out = tmp_path / "conv.md"
    markdown.render(messages, contact_record, my_wxid, out)
    text = out.read_text(encoding="utf-8")
    # blockquotes for reply context
    quotes = re.findall(r"^> [A-Z?](?: at \d{2}:\d{2})? —", text, re.M)
    assert len(quotes) > 5
