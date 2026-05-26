"""Chunk rendered output by month / week / day / token budget.

Calendar chunking re-renders the message stream per bucket (clean, correct).
Token chunking operates on the rendered text, splitting at session boundaries
(which are the only safe split points without breaking message references).

Filename convention:
    <stem>_2026-04.<ext>          month
    <stem>_2026-W14.<ext>         week (ISO)
    <stem>_2026-04-09.<ext>       day
    <stem>_part01.<ext>           tokens
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from wxextract.contacts import ContactRecord
from wxextract.messages import Message
from wxextract.tokens import count as count_tokens

RenderFn = Callable[..., tuple[int, int]]


# ---------------------------------------------------------------------------
# Calendar chunking — re-render per bucket
# ---------------------------------------------------------------------------


def _bucket_month(ts: int) -> tuple[str, str]:
    d = date.fromtimestamp(ts)
    return f"{d.year:04d}-{d.month:02d}", f"{d.year:04d}-{d.month:02d}"


def _bucket_week(ts: int) -> tuple[str, str]:
    d = date.fromtimestamp(ts)
    iso = d.isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}", f"{iso.year:04d}-W{iso.week:02d}"


def _bucket_day(ts: int) -> tuple[str, str]:
    d = date.fromtimestamp(ts).isoformat()
    return d, d


_BUCKETERS = {"month": _bucket_month, "week": _bucket_week, "day": _bucket_day}


def chunk_calendar(
    messages: list[Message],
    contact: ContactRecord,
    my_wxid: str,
    render_fn: RenderFn,
    base_path: Path,
    granularity: str,
    **render_kwargs,
) -> list[Path]:
    """Bucket messages by month/week/day, render each, return output paths."""
    if granularity not in _BUCKETERS:
        raise ValueError(f"unsupported granularity {granularity!r}")
    bucketer = _BUCKETERS[granularity]
    buckets: dict[str, list[Message]] = {}
    for m in messages:
        key, _label = bucketer(m.create_time)
        buckets.setdefault(key, []).append(m)
    paths: list[Path] = []
    for key in sorted(buckets):
        out = base_path.with_name(f"{base_path.stem}_{key}{base_path.suffix}")
        render_fn(buckets[key], contact, my_wxid, out, **render_kwargs)
        paths.append(out)
    return paths


# ---------------------------------------------------------------------------
# Token chunking — operate on already-rendered text
# ---------------------------------------------------------------------------


def _split_txt_b_into_header_and_sessions(text: str) -> tuple[str, list[str]]:
    """Split a Style B file into (header, [session_blocks]).

    Header = first two non-blank lines (G: + META:).
    Sessions = blocks starting with `=` line and continuing until next `=` line.
    """
    lines = text.splitlines(keepends=True)
    header_lines: list[str] = []
    body_start = 0
    for i, ln in enumerate(lines):
        if ln.startswith("="):
            body_start = i
            break
        header_lines.append(ln)
    else:
        return "".join(header_lines), []
    header = "".join(header_lines)
    sessions: list[str] = []
    cur: list[str] = []
    for ln in lines[body_start:]:
        if ln.startswith("="):
            if cur:
                sessions.append("".join(cur))
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        sessions.append("".join(cur))
    return header, sessions


def _split_xml_into_header_and_sessions(text: str) -> tuple[str, list[str], str]:
    """Split pseudo-XML into (open, [session blocks], close).

    Open = everything up to but not including first <session>.
    Sessions = each <session>...</session> with any preceding <gap/> kept attached.
    Close = </conversation>\n.
    """
    open_end = text.find("<session ")
    if open_end == -1:
        return text, [], ""
    open_text = text[:open_end]
    rest = text[open_end:]
    end = rest.rfind("</conversation>")
    close_text = rest[end:] if end != -1 else ""
    body = rest[:end] if end != -1 else rest

    # split bodies into chunks; a chunk = optional leading <gap.../> + one <session>...</session>
    sessions: list[str] = []
    buf: list[str] = []
    lines = body.splitlines(keepends=True)
    pending_gap: list[str] = []
    for ln in lines:
        if ln.startswith("<gap "):
            pending_gap.append(ln)
        elif ln.startswith("<session "):
            buf = list(pending_gap) + [ln]
            pending_gap = []
        elif ln.startswith("</session>"):
            buf.append(ln)
            sessions.append("".join(buf))
            buf = []
        else:
            buf.append(ln)
    return open_text, sessions, close_text


def chunk_by_tokens_txt(text: str, max_tokens: int) -> list[str]:
    """Pack Style B text into chunks ≤ max_tokens each, splitting only on session boundaries.
    Glossary + META are repeated at the top of every chunk."""
    header, sessions = _split_txt_b_into_header_and_sessions(text)
    if not sessions:
        return [text]
    chunks: list[str] = []
    cur_parts: list[str] = []
    cur_tokens = count_tokens(header)
    for sess in sessions:
        sess_t = count_tokens(sess)
        if cur_parts and cur_tokens + sess_t > max_tokens:
            chunks.append(header + "".join(cur_parts))
            cur_parts = [sess]
            cur_tokens = count_tokens(header) + sess_t
        else:
            cur_parts.append(sess)
            cur_tokens += sess_t
    if cur_parts:
        chunks.append(header + "".join(cur_parts))
    return chunks


def chunk_by_tokens_xml(text: str, max_tokens: int) -> list[str]:
    open_text, sessions, close_text = _split_xml_into_header_and_sessions(text)
    if not sessions:
        return [text]
    chunks: list[str] = []
    cur_parts: list[str] = []
    cur_tokens = count_tokens(open_text + close_text)
    for sess in sessions:
        sess_t = count_tokens(sess)
        if cur_parts and cur_tokens + sess_t > max_tokens:
            chunks.append(open_text + "".join(cur_parts) + close_text)
            cur_parts = [sess]
            cur_tokens = count_tokens(open_text + close_text) + sess_t
        else:
            cur_parts.append(sess)
            cur_tokens += sess_t
    if cur_parts:
        chunks.append(open_text + "".join(cur_parts) + close_text)
    return chunks


def chunk_by_tokens(
    rendered_path: Path,
    max_tokens: int,
    fmt: str,
) -> list[Path]:
    """Split a rendered file into token-bounded parts on disk.

    `fmt` is one of {'txt-b', 'xml'}. JSONL chunking is intentionally not session-aware
    (each line is independent) — we just pack lines greedily."""
    text = rendered_path.read_text(encoding="utf-8")
    if fmt == "txt-b":
        chunks = chunk_by_tokens_txt(text, max_tokens)
    elif fmt == "xml":
        chunks = chunk_by_tokens_xml(text, max_tokens)
    elif fmt == "jsonl":
        chunks = _chunk_jsonl_by_tokens(text, max_tokens)
    else:
        raise ValueError(f"unsupported fmt {fmt!r}")
    paths: list[Path] = []
    for i, chunk in enumerate(chunks, 1):
        out = rendered_path.with_name(
            f"{rendered_path.stem}_part{i:02d}{rendered_path.suffix}"
        )
        out.write_text(chunk, encoding="utf-8")
        paths.append(out)
    return paths


def _chunk_jsonl_by_tokens(text: str, max_tokens: int) -> list[str]:
    """JSONL is line-per-record; greedy pack lines. First line (meta) repeats."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return [text]
    meta_line = lines[0] if lines[0].startswith("{") and '"_meta"' in lines[0] else ""
    records = lines[1:] if meta_line else lines
    chunks: list[str] = []
    cur: list[str] = [meta_line] if meta_line else []
    cur_tokens = count_tokens(meta_line)
    for ln in records:
        ln_t = count_tokens(ln)
        if cur_tokens + ln_t > max_tokens and (len(cur) > 1 or not meta_line):
            chunks.append("".join(cur))
            cur = [meta_line] if meta_line else []
            cur_tokens = count_tokens(meta_line)
        cur.append(ln)
        cur_tokens += ln_t
    if cur and (len(cur) > 1 or not meta_line):
        chunks.append("".join(cur))
    return chunks
