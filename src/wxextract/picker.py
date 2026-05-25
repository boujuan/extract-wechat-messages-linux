"""Interactive contact picker using `rich`.

Renders a sortable, filterable table and lets the user pick by number or by
typing a substring filter.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

import zstandard as zstd
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from wxextract.contacts import ContactRecord


_ZSTD = zstd.ZstdDecompressor()


def _fmt_ts(ts: int) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _last_message_snippet(rec: ContactRecord, max_chars: int = 40) -> str:
    """Read the most recent text message for this contact, truncated."""
    if not rec.message_db or not rec.message_table:
        return ""
    try:
        conn = sqlite3.connect(str(rec.message_db))
        try:
            row = conn.execute(
                f"""SELECT local_type, message_content, WCDB_CT_message_content
                    FROM {rec.message_table}
                    WHERE local_type = 1
                    ORDER BY create_time DESC LIMIT 1"""
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return ""
    if not row:
        return ""
    _typ, blob, ct = row
    if blob is None:
        return ""
    if ct == 4 and isinstance(blob, (bytes, bytearray)):
        try:
            text = _ZSTD.decompress(blob, max_output_size=64 * 1024).decode("utf-8", errors="replace")
        except zstd.ZstdError:
            text = blob.decode("utf-8", errors="replace")
    elif isinstance(blob, (bytes, bytearray)):
        text = blob.decode("utf-8", errors="replace")
    else:
        text = str(blob)
    text = " ".join(text.split())
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return text


def render_table(records: list[ContactRecord], console: Console, *, limit: int | None = 50,
                 show_snippets: bool = True) -> None:
    table = Table(show_lines=False, header_style="bold")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Alias", style="cyan")
    table.add_column("Msgs", justify="right")
    table.add_column("Last seen", style="dim")
    if show_snippets:
        table.add_column("Last message", style="dim italic")
    rows = records if limit is None else records[:limit]
    for i, r in enumerate(rows, 1):
        cols = [
            str(i),
            r.display_name,
            r.alias or "—",
            f"{r.message_count:,}",
            _fmt_ts(r.last_ts),
        ]
        if show_snippets:
            cols.append(_last_message_snippet(r))
        table.add_row(*cols)
    console.print(table)
    if limit is not None and len(records) > limit:
        console.print(f"[dim]({len(records) - limit} more — type a filter to narrow)[/dim]")


def _apply_filter(records: list[ContactRecord], q: str) -> list[ContactRecord]:
    q_l = q.lower()
    return [
        r for r in records
        if q_l in r.display_name.lower()
        or q_l in r.alias.lower()
        or q_l in r.nick_name.lower()
        or q_l in r.remark.lower()
    ]


def pick(records: list[ContactRecord], console: Console | None = None) -> ContactRecord | None:
    """Show the table and prompt for selection.

    Returns the chosen ContactRecord, or None if the user quits.
    """
    console = console or Console()
    current = records
    while True:
        if not current:
            console.print("[red]No matches — empty filter (Enter) to reset, 'q' to quit[/red]")
        else:
            render_table(current, console)
        answer = Prompt.ask("Pick # / type filter / [b]q[/b] to quit", console=console).strip()
        if answer.lower() in ("q", "quit", "exit"):
            return None
        if answer == "":
            current = records
            continue
        if answer.isdigit():
            i = int(answer)
            if 1 <= i <= len(current):
                return current[i - 1]
            console.print(f"[yellow]out of range (1–{len(current)})[/yellow]")
            continue
        # treat as filter
        filtered = _apply_filter(records, answer)
        current = filtered
