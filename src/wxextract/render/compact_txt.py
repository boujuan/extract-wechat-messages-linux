"""Style B — ultra-compact TXT for LLM context windows.

Format (synthetic example):
    G:A=Alice|U=Me
    META:alice_42|range=2026-01-01..2026-01-15|msgs=312|tokens=~9.4k

    =2026-01-01
    U 10:30:10 hey
    A :35:40 hi there
    U 11:00:10 how was your day?;[image]

    =11:27 +3h
    A 11:27:32 pretty good
    A :28 [img]

Time abbreviation:
  - first message of session → HH:MM:SS
  - same hour as previous   → :MM:SS
  - same minute as previous → :SS
Turn merging:
  consecutive same-sender within window (default 60s) joined by `;`.
Quoted replies inline:
  `body [↩R 17:38 "quoted preview"]`
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
    Session,
    body_of,
    build_identity,
    letter_for,
    sessionize,
)
from wxextract.tokens import count as count_tokens
from wxextract.tokens import fmt_short

QUOTE_PREVIEW_MAX = 40


def _fmt_time(ts: int, prev_ts: int | None, precision: str = "seconds") -> str:
    """Abbreviate timestamp relative to prev_ts (None = first in session)."""
    dt = datetime.fromtimestamp(ts)
    if precision == "minutes":
        if prev_ts is None:
            return dt.strftime("%H:%M")
        pdt = datetime.fromtimestamp(prev_ts)
        if dt.hour == pdt.hour and dt.minute == pdt.minute:
            return ""  # same minute as previous → omit time entirely
        if dt.hour == pdt.hour:
            return dt.strftime(":%M")
        return dt.strftime("%H:%M")
    # default: seconds
    if prev_ts is None:
        return dt.strftime("%H:%M:%S")
    pdt = datetime.fromtimestamp(prev_ts)
    if dt.hour == pdt.hour and dt.minute == pdt.minute:
        return dt.strftime(":%S")
    if dt.hour == pdt.hour:
        return dt.strftime(":%M:%S")
    return dt.strftime("%H:%M:%S")


def _fmt_gap(seconds: int) -> str:
    """Render a silence gap as +Nh / +Nm / +Nd."""
    if seconds < 3600:
        return f"+{max(1, seconds // 60)}m"
    if seconds < 86400:
        return f"+{seconds // 3600}h"
    return f"+{seconds // 86400}d"


def _truncate(text: str, n: int = QUOTE_PREVIEW_MAX) -> str:
    text = " ".join(text.split())  # collapse whitespace/newlines
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def _render_body(body: Body, identity: Identity, reply_preview: str = "full") -> str:
    """Apply quoted-reply inlining (Style B = single line).

    reply_preview ∈ {full, short, none}:
      full  — `[↩R 17:38 "first 40 chars…"]`  (default, max context)
      short — `[↩R 17:38]`                     (sender + time, no preview)
      none  — `[↩R]`                           (sender only)
    """
    if body.reply is not None:
        rletter = identity.by_username.get(body.reply.sender_username, "") or "?"
        head = body.text or ""
        if reply_preview == "none":
            return f"{head} [↩{rletter}]".strip()
        rtime = ""
        if body.reply.ts:
            rtime = " " + datetime.fromtimestamp(body.reply.ts).strftime("%H:%M")
        if reply_preview == "short":
            return f"{head} [↩{rletter}{rtime}]".strip()
        # full
        preview = _truncate(body.reply.content)
        return f'{head} [↩{rletter}{rtime} "{preview}"]'.strip()
    return body.text


def _glossary_line(identity: Identity) -> str:
    parts = [f"{letter}={display}" for letter, display in identity.glossary.items()]
    return "GLOSS: " + "  ".join(parts)


def _legend_line() -> str:
    return "LEGEND: " + LEGEND_TEXT


def _render_session(
    sess: Session,
    identity: Identity,
    out: io.StringIO,
    last_session_day: str | None,
    *,
    turn_merge: bool,
    turn_window: int,
    squash: bool = False,
    redact: bool = False,
    stickers_to_emoji: bool = False,
    time_precision: str = "seconds",
    reply_preview: str = "full",
) -> str:
    """Render one session into `out`. Returns the new last_session_day."""
    sd = datetime.fromtimestamp(sess.start_ts)
    day = sd.strftime("%Y-%m-%d")
    if day != last_session_day:
        out.write(f"\n={day}\n")
    else:
        # mid-day gap header: just hours:minutes + gap
        out.write(f"\n={sd.strftime('%H:%M')} {_fmt_gap(sess.gap_before)}\n")

    prev_ts: int | None = None
    prev_letter: str | None = None
    line_open = False
    for m in sess.messages:
        body = body_of(m, squash=squash, redact=redact, stickers_to_emoji=stickers_to_emoji)
        text = _render_body(body, identity, reply_preview=reply_preview)
        letter = letter_for(identity, m)
        if (
            turn_merge
            and prev_letter == letter
            and prev_ts is not None
            and (m.create_time - prev_ts) <= turn_window
            and line_open
        ):
            out.write(";" + text)
        else:
            if line_open:
                out.write("\n")
            time_s = _fmt_time(m.create_time, prev_ts, precision=time_precision)
            # if time_s is empty (same-minute mode), drop the gap entirely
            sep = " " if time_s else ""
            out.write(f"{letter}{sep}{time_s} {text}".rstrip())
            line_open = True
        prev_ts = m.create_time
        prev_letter = letter
    if line_open:
        out.write("\n")
    return day


def render(
    messages: list[Message],
    contact: ContactRecord,
    my_wxid: str,
    out_path: Path,
    *,
    my_label: str = "Me",
    gap_seconds: int = 7200,
    turn_merge: bool = True,
    turn_window: int = 60,
    squash: bool = False,
    redact: bool = False,
    stickers_to_emoji: bool = False,
    time_precision: str = "seconds",
    reply_preview: str = "full",
) -> tuple[int, int]:
    """Write the Style B file; return (lines_written, token_count)."""
    identity = build_identity(messages, contact, my_wxid, my_label=my_label)

    buf = io.StringIO()
    last_day: str | None = None
    for sess in sessionize(messages, gap_seconds=gap_seconds):
        last_day = _render_session(
            sess, identity, buf, last_day, turn_merge=turn_merge,
            turn_window=turn_window, squash=squash, redact=redact,
            stickers_to_emoji=stickers_to_emoji,
            time_precision=time_precision, reply_preview=reply_preview,
        )
    body_text = buf.getvalue()

    first_dt = datetime.fromtimestamp(messages[0].create_time).date() if messages else None
    last_dt = datetime.fromtimestamp(messages[-1].create_time).date() if messages else None
    meta_pre = (
        f"META: contact={contact.alias or contact.username} | "
        f"range={first_dt}..{last_dt} | "
        f"msgs={len(messages)} | tokens=~"
    )
    meta_post = ""
    gloss = _glossary_line(identity)
    legend = _legend_line()

    # estimate tokens once with a placeholder; header is small so body dominates
    sample = f"{meta_pre}PLACEHOLDER{meta_post}\n{gloss}\n{legend}\n{body_text}"
    tokens = count_tokens(sample)
    header = f"{meta_pre}{fmt_short(tokens)}{meta_post}\n{gloss}\n{legend}\n"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(header + body_text, encoding="utf-8")
    return body_text.count("\n") + 2, tokens
