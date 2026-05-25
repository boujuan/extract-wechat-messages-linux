"""Pseudo-XML renderer — Claude-friendly structured format.

Tags are minimal and consistent; whitespace inside `<m>` is significant.
Synthetic example:

    <conversation contact="alice_42" range="2026-01-01..2026-01-15" msgs="312">
    <glossary>A=Alice U=Me</glossary>
    <session start="2026-01-01T10:29:01">
    <m s="A" t="10:29:01">hi there</m>
    <m s="U" t="10:30:10">hey</m>
    ...
    </session>
    <gap dur="3h"/>
    <session start="2026-01-01T11:27:32">
    <m s="A" t="11:27:32">all good</m>
    <m s="U" t="11:42:30" reply-to="A:11:27:32:all good">cool</m>
    </session>
    </conversation>
"""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

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


REPLY_PREVIEW_MAX = 40


def _fmt_gap(seconds: int) -> str:
    if seconds < 3600:
        return f"{max(1, seconds // 60)}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _truncate(text: str, n: int = REPLY_PREVIEW_MAX) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _reply_attr(body: Body, identity: Identity) -> str:
    """Build the reply-to="sender:hh:mm:ss:preview" attribute value."""
    if body.reply is None:
        return ""
    rletter = identity.by_username.get(body.reply.sender_username, "?")
    rtime = ""
    if body.reply.ts:
        rtime = datetime.fromtimestamp(body.reply.ts).strftime("%H:%M:%S")
    preview = _truncate(body.reply.content)
    return f"{rletter}:{rtime}:{preview}"


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

    first_ts = messages[0].create_time if messages else 0
    last_ts = messages[-1].create_time if messages else 0
    first_d = datetime.fromtimestamp(first_ts).date() if first_ts else ""
    last_d = datetime.fromtimestamp(last_ts).date() if last_ts else ""

    buf = io.StringIO()
    # leading conversation tag
    alias = contact.alias or contact.username
    buf.write(
        f'<conversation contact={quoteattr(alias)} '
        f'range="{first_d}..{last_d}" msgs="{len(messages)}">\n'
    )
    gloss_text = " ".join(f"{k}={v}" for k, v in identity.glossary.items())
    buf.write(f"<glossary>{escape(gloss_text)}</glossary>\n")
    buf.write(f"<legend>{escape(LEGEND_TEXT)}</legend>\n")

    for sess in sessionize(messages, gap_seconds=gap_seconds):
        if sess.gap_before > 0:
            buf.write(f'<gap dur="{_fmt_gap(sess.gap_before)}"/>\n')
        sd = datetime.fromtimestamp(sess.start_ts).strftime("%Y-%m-%dT%H:%M:%S")
        buf.write(f'<session start="{sd}">\n')
        for m in sess.messages:
            body = body_of(m, squash=squash, redact=redact, stickers_to_emoji=stickers_to_emoji)
            letter = letter_for(identity, m)
            t = datetime.fromtimestamp(m.create_time).strftime("%H:%M:%S")
            text = escape(body.text or "")
            attrs = f's="{letter}" t="{t}"'
            ra = _reply_attr(body, identity)
            if ra:
                attrs += f" reply-to={quoteattr(ra)}"
            buf.write(f"<m {attrs}>{text}</m>\n")
        buf.write("</session>\n")
    buf.write("</conversation>\n")

    body_text = buf.getvalue()
    tokens = count_tokens(body_text)
    # rewrite the leading conversation tag to include tokens=~Nk
    new_first = (
        f'<conversation contact={quoteattr(alias)} '
        f'range="{first_d}..{last_d}" msgs="{len(messages)}" tokens="~{fmt_short(tokens)}">\n'
    )
    body_text = body_text.replace(
        f'<conversation contact={quoteattr(alias)} '
        f'range="{first_d}..{last_d}" msgs="{len(messages)}">\n',
        new_first,
        1,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body_text, encoding="utf-8")
    return body_text.count("\n"), tokens
