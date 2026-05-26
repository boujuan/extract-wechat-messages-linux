"""Obsidian-friendly Markdown renderer.

Output shape::

    ---
    contact: rachel_97213
    display_name: "🐑Rachel"
    date_range: 2026-04-09..2026-05-26
    messages: 11903
    recall_count: 45
    glossary:
      U: Me
      R: "🐑Rachel"
    legend: "Chronological 1-on-1 chat ..."
    ---

    # Conversation with 🐑Rachel

    ## 2026-04-09

    **10:30 U** — Ola Raquelinha 👋

    **10:35 R** — Ola Juanlinho😆

    ### 11:27 (+3h)

    **11:27 R** — Got it[Chuckle]
    > U at 10:30 — Ola Raquelinha 👋

The body keeps each message on its own bolded line so it renders cleanly
in Obsidian, Logseq, or any standard Markdown viewer. Quoted replies are
emitted as Markdown blockquotes referencing the original sender + time.
"""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

from wxextract.contacts import ContactRecord
from wxextract.messages import Message
from wxextract.render.common import (
    LEGEND_TEXT,
    Body,
    Identity,
    body_of,
    build_identity,
    letter_for,
    sessionize,
)
from wxextract.tokens import count as count_tokens
from wxextract.tokens import fmt_short

QUOTE_PREVIEW_MAX = 80


def _yaml_escape(s: str) -> str:
    """Minimal YAML scalar escaping — quote strings with `:` or unicode."""
    if not s:
        return '""'
    if any(c in s for c in ':#"\'\n\r\t') or s != s.strip():
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _truncate(text: str, n: int = QUOTE_PREVIEW_MAX) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _fmt_gap(seconds: int) -> str:
    if seconds < 3600:
        return f"+{max(1, seconds // 60)}m"
    if seconds < 86400:
        return f"+{seconds // 3600}h"
    return f"+{seconds // 86400}d"


def _frontmatter(contact: ContactRecord, identity: Identity,
                 messages: list[Message]) -> str:
    first = datetime.fromtimestamp(messages[0].create_time).date() if messages else None
    last = datetime.fromtimestamp(messages[-1].create_time).date() if messages else None
    lines = ["---"]
    lines.append(f"contact: {_yaml_escape(contact.alias or contact.username)}")
    lines.append(f"display_name: {_yaml_escape(contact.display_name)}")
    if contact.username and contact.username != contact.alias:
        lines.append(f"username: {_yaml_escape(contact.username)}")
    if first and last:
        lines.append(f"date_range: {first}..{last}")
    lines.append(f"messages: {len(messages)}")
    lines.append("glossary:")
    for letter, display in identity.glossary.items():
        lines.append(f"  {letter}: {_yaml_escape(display)}")
    lines.append(f"legend: {_yaml_escape(LEGEND_TEXT)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _render_reply_quote(body: Body, identity: Identity) -> str:
    if body.reply is None:
        return ""
    rletter = identity.by_username.get(body.reply.sender_username, "?")
    rtime = ""
    if body.reply.ts:
        rtime = datetime.fromtimestamp(body.reply.ts).strftime(" at %H:%M")
    preview = _truncate(body.reply.content)
    return f"> {rletter}{rtime} — {preview}\n"


def render(
    messages: list[Message],
    contact: ContactRecord,
    my_wxid: str,
    out_path: Path,
    *,
    my_label: str = "Me",
    gap_seconds: int = 7200,
    squash: bool = False,
    redact: bool = False,
    stickers_to_emoji: bool = False,
) -> tuple[int, int]:
    identity = build_identity(messages, contact, my_wxid, my_label=my_label)

    buf = io.StringIO()
    buf.write(_frontmatter(contact, identity, messages))
    buf.write(f"\n# Conversation with {contact.display_name}\n")

    last_day: str | None = None
    for sess in sessionize(messages, gap_seconds=gap_seconds):
        sd = datetime.fromtimestamp(sess.start_ts)
        day = sd.strftime("%Y-%m-%d")
        if day != last_day:
            buf.write(f"\n## {day}\n\n")
            last_day = day
        elif sess.gap_before > 0:
            buf.write(f"\n### {sd.strftime('%H:%M')} ({_fmt_gap(sess.gap_before)})\n\n")
        for m in sess.messages:
            body = body_of(m, squash=squash, redact=redact,
                           stickers_to_emoji=stickers_to_emoji)
            letter = letter_for(identity, m)
            t = datetime.fromtimestamp(m.create_time).strftime("%H:%M:%S")
            quote = _render_reply_quote(body, identity)
            if quote:
                buf.write(quote)
            text = (body.text or "").replace("\n", "  \n")  # MD line break
            buf.write(f"**{t} {letter}** — {text}\n\n")

    body_text = buf.getvalue()
    tokens = count_tokens(body_text)

    # rewrite frontmatter to include token count
    new_fm_line = f"tokens: ~{fmt_short(tokens)}\n---\n"
    body_text = body_text.replace("---\n\n# Conversation", new_fm_line + "\n# Conversation", 1)
    body_text = body_text.replace("---\n" + new_fm_line, new_fm_line, 1)  # avoid double `---`

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body_text, encoding="utf-8")
    return body_text.count("\n"), tokens
