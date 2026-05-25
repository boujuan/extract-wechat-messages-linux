"""Interactive contact picker using `rich`.

Renders a sortable, filterable table and lets the user pick by number or by
typing a substring filter.
"""
from __future__ import annotations

from datetime import datetime

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from wxextract.contacts import ContactRecord


def _fmt_ts(ts: int) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def render_table(records: list[ContactRecord], console: Console, *, limit: int | None = 50) -> None:
    table = Table(show_lines=False, header_style="bold")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Alias", style="cyan")
    table.add_column("Messages", justify="right")
    table.add_column("Last seen", style="dim")
    rows = records if limit is None else records[:limit]
    for i, r in enumerate(rows, 1):
        table.add_row(
            str(i),
            r.display_name,
            r.alias or "—",
            f"{r.message_count:,}",
            _fmt_ts(r.last_ts),
        )
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
