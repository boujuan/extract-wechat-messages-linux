"""Rich-based progress UI + final summary panel for the extraction pipeline.

Two pieces:

  - ProgressUI: a Live-updating panel that lists each pipeline stage with
    a spinner / check / skip / fail glyph, the elapsed time, and a short
    detail line. Disable with --no-progress or when stderr isn't a TTY.

  - render_summary: a pretty Panel + Table printed once at the end, listing
    every output file with size / lines / words / tokens / days.

Both go to stderr so stdout stays clean for piping.
"""
from __future__ import annotations

import contextlib
import re
import time
from dataclasses import dataclass
from pathlib import Path

from rich.box import ROUNDED
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

STAGE_ORDER = [
    "Discover",
    "Keys",
    "Snapshot",
    "Decrypt",
    "Contacts",
    "Extract",
    "Render",
    "Chunk",
]


# ---------------------------------------------------------------------------
# Stage tracking
# ---------------------------------------------------------------------------


@dataclass
class _Stage:
    name: str
    status: str = "pending"   # pending | running | done | skipped | failed
    detail: str = ""
    started_at: float = 0.0
    duration: float = 0.0


_STYLE_BY_STATUS = {
    "pending": ("·", "dim", "dim"),
    "done":    ("✓", "green bold", "green"),
    "skipped": ("⊘", "yellow", "yellow"),
    "failed":  ("✗", "red bold", "red"),
}


class ProgressUI:
    """Rich Live panel showing per-stage status."""

    def __init__(self, console: Console | None = None, enabled: bool = True):
        self.console = console or Console(stderr=True)
        self.enabled = enabled and self.console.is_terminal
        self.stages: dict[str, _Stage] = {n: _Stage(name=n) for n in STAGE_ORDER}
        self._live: Live | None = None
        self.t0 = time.perf_counter()

    # ----- rendering --------------------------------------------------------

    def _row(self, st: _Stage):
        if st.status == "running":
            icon = Spinner("dots", style="cyan")
            name = Text(st.name, style="cyan bold")
        else:
            glyph, name_style, _ = _STYLE_BY_STATUS[st.status]
            icon = Text(glyph, style=name_style)
            n_text = st.name
            if st.status == "done" and st.duration >= 0.01:
                n_text = f"{st.name}  [dim]{_fmt_dur(st.duration)}[/]"
            name = Text.from_markup(n_text, style=name_style if st.status != "done" else "green")
        return icon, name, Text(st.detail, style="dim italic", overflow="ellipsis")

    def _panel(self) -> Panel:
        t = Table.grid(padding=(0, 1), expand=False)
        t.add_column(width=2, no_wrap=True)
        t.add_column(min_width=12, no_wrap=True)
        t.add_column(overflow="ellipsis", max_width=80)
        for st in self.stages.values():
            t.add_row(*self._row(st))
        elapsed = time.perf_counter() - self.t0
        title = Text.from_markup(f"[bold cyan]wxextract[/]  [dim]· {elapsed:.1f}s[/]")
        return Panel(t, title=title, border_style="cyan", expand=False, padding=(0, 1))

    def _refresh(self):
        if self._live:
            self._live.update(self._panel())

    # ----- lifecycle --------------------------------------------------------

    def start(self):
        if not self.enabled:
            return
        self._live = Live(self._panel(), console=self.console,
                          refresh_per_second=12, transient=False)
        self._live.start()

    def stop(self):
        if self._live:
            self._live.update(self._panel())
            self._live.stop()
            self._live = None

    # ----- stage transitions ------------------------------------------------

    def begin(self, name: str, detail: str = ""):
        s = self.stages[name]
        s.status = "running"
        s.detail = detail
        s.started_at = time.perf_counter()
        self._refresh()

    def end(self, name: str, detail: str | None = None):
        s = self.stages[name]
        if s.started_at:
            s.duration = time.perf_counter() - s.started_at
        if detail is not None:
            s.detail = detail
        s.status = "done"
        self._refresh()

    def skip(self, name: str, reason: str = ""):
        s = self.stages[name]
        s.status = "skipped"
        s.detail = reason
        self._refresh()

    def fail(self, name: str, reason: str = ""):
        s = self.stages[name]
        if s.started_at:
            s.duration = time.perf_counter() - s.started_at
        s.status = "failed"
        s.detail = reason
        self._refresh()

    def detail(self, name: str, detail: str):
        self.stages[name].detail = detail
        self._refresh()

    @contextlib.contextmanager
    def stage(self, name: str, detail: str = ""):
        self.begin(name, detail)
        try:
            yield self
        except Exception as e:
            self.fail(name, type(e).__name__ + ": " + str(e)[:60])
            raise
        else:
            if self.stages[name].status == "running":
                self.end(name)


# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------


def _human_bytes(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024:
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TB"


def _fmt_dur(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.2f} s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s"


def file_stats(path: Path, fmt: str) -> dict:
    """size / lines / words / tokens / distinct-day-count for one output file."""
    from wxextract.tokens import count as count_tokens
    text = path.read_text(encoding="utf-8")
    sz = path.stat().st_size
    lines = text.count("\n") or 1
    words = len(text.split())
    tokens = count_tokens(text)
    if fmt == "txt-b":
        days = len(set(re.findall(r"^=(\d{4}-\d{2}-\d{2})$", text, re.M)))
    elif fmt == "xml":
        days = len({d[:10] for d in re.findall(r'<session start="(\d{4}-\d{2}-\d{2})', text)})
    elif fmt == "jsonl":
        days = len(set(re.findall(r'"dt":\s*"(\d{4}-\d{2}-\d{2})', text)))
    elif fmt == "md":
        days = len(set(re.findall(r"^## (\d{4}-\d{2}-\d{2})$", text, re.M)))
    else:
        days = 0
    return {"format": fmt, "path": str(path), "size": sz, "lines": lines,
            "words": words, "tokens": tokens, "days": days}


def render_summary(
    console: Console,
    *,
    contact,
    message_count: int,
    recall_count: int,
    date_range: tuple[str, str],
    total_days: int,
    total_time: float,
    outputs: list[dict],
    workspace: Path,
) -> None:
    """Print the final summary panel to console (typically stdout)."""

    # ── header info grid ────────────────────────────────────────────────────
    info = Table.grid(padding=(0, 2))
    info.add_column(style="cyan bold", no_wrap=True)
    info.add_column(style="bright_white")

    contact_line = Text()
    contact_line.append(contact.display_name, style="bold")
    if contact.alias:
        sub = f" ({contact.alias}"
        if contact.username and contact.username != contact.alias:
            sub += f" → {contact.username}"
        sub += ")"
        contact_line.append(sub, style="dim")
    info.add_row("Contact", contact_line)

    range_text = Text()
    range_text.append(f"{date_range[0]} → {date_range[1]}", style="bright_white")
    range_text.append(f"  ({total_days} days)", style="dim")
    info.add_row("Range", range_text)

    msg_text = Text()
    msg_text.append(f"{message_count:,}", style="bright_white")
    if recall_count:
        msg_text.append(f"  ({recall_count:,} recalls filtered)", style="dim")
    info.add_row("Messages", msg_text)

    info.add_row("Total time", Text(_fmt_dur(total_time), style="bright_white"))
    info.add_row("Workspace", Text(str(workspace), style="dim"))

    # ── output table ────────────────────────────────────────────────────────
    bits: list = [info]
    if outputs:
        t = Table(box=ROUNDED, show_header=True, header_style="bold cyan",
                  expand=False, padding=(0, 1))
        t.add_column("Format", style="bold cyan", no_wrap=True)
        t.add_column("File", style="bright_white", no_wrap=True, overflow="ellipsis", max_width=46)
        t.add_column("Size", justify="right")
        t.add_column("Lines", justify="right")
        t.add_column("Words", justify="right")
        t.add_column("Tokens", justify="right", style="yellow bold")
        t.add_column("Days", justify="right", style="magenta")
        sz_tot = ln_tot = wd_tot = tk_tot = days_tot = 0
        for o in outputs:
            t.add_row(
                o["format"],
                Path(o["path"]).name,
                _human_bytes(o["size"]),
                f"{o['lines']:,}",
                f"{o['words']:,}",
                f"{o['tokens']:,}",
                f"{o['days']}" if o["days"] else "—",
            )
            sz_tot += o["size"]
            ln_tot += o["lines"]
            wd_tot += o["words"]
            tk_tot += o["tokens"]
            days_tot += o["days"]
        if len(outputs) > 1:
            t.add_section()
            t.add_row(
                Text("TOTAL", style="bold"),
                "",
                Text(_human_bytes(sz_tot), style="bold"),
                Text(f"{ln_tot:,}", style="bold"),
                Text(f"{wd_tot:,}", style="bold"),
                Text(f"{tk_tot:,}", style="bold yellow"),
                "",   # Days TOTAL is meaningless (same range across formats); see header
            )
        bits.append(Text(""))
        bits.append(t)

    body = Group(*bits)
    panel = Panel(body, title=Text("Extraction Summary", style="bold green"),
                  border_style="green", expand=False, padding=(1, 2))
    console.print()
    console.print(panel)
