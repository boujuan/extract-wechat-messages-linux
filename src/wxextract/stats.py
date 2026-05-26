"""Conversation analytics for `wxextract stats`.

Pure-Python summary computation; rendering helpers live alongside but
can also be imported standalone.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from wxextract.contacts import ContactRecord
from wxextract.messages import (
    TYPE_APPMSG,
    TYPE_CALL,
    TYPE_IMAGE,
    TYPE_STICKER,
    TYPE_SYSTEM,
    TYPE_TEXT,
    TYPE_VIDEO,
    TYPE_VOICE,
    Message,
)

# Crude "real word" filter — drops short tokens, all-digit tokens.
_WORD_RE = re.compile(r"[A-Za-zÀ-ɏ一-鿿][A-Za-zÀ-ɏ一-鿿']{2,}")
_EMOJI_TAG_RE = re.compile(r"\[[A-Z][A-Za-z]{1,18}\]")
# Approximate unicode emoji range coverage (BMP + supplementary planes)
_UNICODE_EMOJI_RE = re.compile(
    "[" "\U0001F300-\U0001F9FF" "☀-➿" "\U0001F000-\U0001F2FF"
    "\U00002700-\U000027BF" "\U0001FA70-\U0001FAFF" "]"
)


_STOPWORDS = frozenset({
    "the", "and", "for", "you", "are", "but", "not", "with", "have", "this",
    "that", "was", "from", "they", "she", "him", "her", "his", "all", "one",
    "out", "what", "when", "your", "can", "just", "like", "get", "got", "has",
    "had", "will", "would", "could", "should", "there", "their", "them",
    "about", "into", "than", "then", "now", "very", "much", "more", "some",
    "any", "how", "why", "who", "where", "which", "well", "even", "also",
    "only", "really", "still", "back", "down", "over", "after", "before",
    "yes", "yeah", "haha", "hahaha", "hahahaha", "lol", "ok", "okay", "ohh",
    "ohhh", "ahh", "ahhh", "hmm", "hmmm", "yep", "yup", "nope", "nah",
    "wow", "omg", "btw", "tbh", "lmao", "lmaoo", "imo", "rn",
})


@dataclass
class Counts:
    total: int = 0
    by_sender: dict[str, int] = field(default_factory=Counter)
    by_type: dict[int, int] = field(default_factory=Counter)
    by_month: dict[str, int] = field(default_factory=Counter)        # "2026-04"
    by_weekday: dict[int, int] = field(default_factory=Counter)      # 0=Mon
    by_hour: dict[int, int] = field(default_factory=Counter)         # 0..23
    top_emojis: list[tuple[str, int]] = field(default_factory=list)
    top_words: list[tuple[str, int]] = field(default_factory=list)
    response_time_seconds: list[int] = field(default_factory=list)        # me → them (incoming→my outgoing)
    their_response_time_seconds: list[int] = field(default_factory=list)  # them → me (my outgoing→their incoming)
    longest_silence_seconds: int = 0
    longest_silence_between: tuple[str, str] = ("", "")              # (iso_a, iso_b)
    first_ts: int = 0
    last_ts: int = 0
    active_days: int = 0
    daily_counts: dict[str, int] = field(default_factory=dict)       # "2026-04-09" → total
    daily_me:     dict[str, int] = field(default_factory=dict)
    daily_them:   dict[str, int] = field(default_factory=dict)


_TYPE_LABEL = {
    TYPE_TEXT: "text",
    TYPE_IMAGE: "image",
    TYPE_VOICE: "voice",
    TYPE_VIDEO: "video",
    TYPE_STICKER: "sticker",
    TYPE_APPMSG: "appmsg",
    TYPE_CALL: "call",
    TYPE_SYSTEM: "system",
}


def compute(messages: list[Message], contact: ContactRecord, my_label: str = "Me",
            top_n: int = 12, unify_emoji_tags: bool = True) -> Counts:
    """Compute per-contact analytics.

    `unify_emoji_tags`: when True (default), [Facepalm] / [Drool] / etc. are
    mapped to their Unicode equivalent before counting, so the top-emoji list
    isn't split between the tag form and the Unicode form for the same sticker.
    """
    from wxextract.render.common import sticker_to_emoji
    c = Counts()
    if not messages:
        return c

    sender_words: dict[str, list[str]] = {my_label: [], contact.display_name: []}
    emoji_counter: Counter[str] = Counter()
    word_counter: Counter[str] = Counter()
    days: set[str] = set()
    daily: Counter[str] = Counter()
    daily_me: Counter[str] = Counter()
    daily_them: Counter[str] = Counter()

    c.first_ts = messages[0].create_time
    c.last_ts = messages[-1].create_time
    prev_ts = None
    # bidirectional response-time tracking: two pending "first-after-flip" timestamps
    pending_other_ts: int | None = None     # them speaking, waiting for my reply
    pending_me_ts: int | None = None        # me speaking, waiting for their reply

    for m in messages:
        c.total += 1
        sender = my_label if m.is_me else (contact.display_name if m.sender_username == contact.username else (m.sender_username or "?"))
        c.by_sender[sender] += 1
        c.by_type[m.type] += 1
        dt = datetime.fromtimestamp(m.create_time)
        date_str = dt.strftime("%Y-%m-%d")
        c.by_month[dt.strftime("%Y-%m")] += 1
        c.by_weekday[dt.weekday()] += 1
        c.by_hour[dt.hour] += 1
        days.add(date_str)
        daily[date_str] += 1
        if m.is_me:
            daily_me[date_str] += 1
        elif m.sender_username == contact.username:
            daily_them[date_str] += 1

        # silence + bidirectional response time
        if prev_ts is not None:
            gap = m.create_time - prev_ts
            if gap > c.longest_silence_seconds:
                c.longest_silence_seconds = gap
                c.longest_silence_between = (
                    datetime.fromtimestamp(prev_ts).isoformat(timespec="minutes"),
                    dt.isoformat(timespec="minutes"),
                )
            if m.is_me:
                if pending_other_ts is not None:
                    c.response_time_seconds.append(m.create_time - pending_other_ts)
                    pending_other_ts = None
                # remember when *I* started waiting for their reply
                if pending_me_ts is None:
                    pending_me_ts = m.create_time
            elif m.sender_username == contact.username:
                if pending_me_ts is not None:
                    c.their_response_time_seconds.append(m.create_time - pending_me_ts)
                    pending_me_ts = None
                if pending_other_ts is None:
                    pending_other_ts = m.create_time
        else:
            if m.is_me:
                pending_me_ts = m.create_time
            elif m.sender_username == contact.username:
                pending_other_ts = m.create_time
        prev_ts = m.create_time

        if m.type == TYPE_TEXT and m.content:
            text = sticker_to_emoji(m.content) if unify_emoji_tags else m.content
            for tag in _EMOJI_TAG_RE.findall(text):
                emoji_counter[tag] += 1
            for ch in _UNICODE_EMOJI_RE.findall(text):
                emoji_counter[ch] += 1
            for w in _WORD_RE.findall(text.lower()):
                if w in _STOPWORDS:
                    continue
                word_counter[w] += 1
                if sender in sender_words:
                    sender_words[sender].append(w)

    c.top_emojis = emoji_counter.most_common(top_n)
    c.top_words = word_counter.most_common(top_n)
    c.active_days = len(days)
    c.daily_counts = dict(daily)
    c.daily_me = dict(daily_me)
    c.daily_them = dict(daily_them)
    return c


# ---------------------------------------------------------------------------
# rich rendering
# ---------------------------------------------------------------------------


def _bar(value: int, max_value: int, width: int = 30) -> str:
    if max_value <= 0:
        return ""
    n = max(1, round(value / max_value * width)) if value else 0
    return "█" * n


def _fmt_dur(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _percentile(seq: list[int], p: float) -> int:
    if not seq:
        return 0
    s = sorted(seq)
    k = int(round((len(s) - 1) * p))
    return s[k]


def _response_table(title: str, rt: list[int]):
    from rich.box import ROUNDED
    from rich.table import Table
    t = Table(box=ROUNDED, show_header=True, header_style="bold cyan",
              title=f"Response time: {title}", title_style="bold")
    t.add_column("Stat")
    t.add_column("Value", justify="right", style="bright_white")
    if rt:
        t.add_row("Samples", f"{len(rt):,}")
        t.add_row("Median",  _fmt_dur(_percentile(rt, 0.5)))
        t.add_row("p75",     _fmt_dur(_percentile(rt, 0.75)))
        t.add_row("p90",     _fmt_dur(_percentile(rt, 0.9)))
        t.add_row("p99",     _fmt_dur(_percentile(rt, 0.99)))
        t.add_row("Max",     _fmt_dur(max(rt)))
    else:
        t.add_row("Samples", "0 (one-way conversation)")
    return t


def _render_daily_timeline(c: Counts, my_label: str, other_name: str):
    """Per-day stacked bar of message volume; auto-falls back to per-week for long ranges."""
    from datetime import date as _date
    from datetime import timedelta

    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    if not c.daily_counts:
        return Panel(Text("(no data)"), title="Daily timeline", border_style="cyan",
                     expand=False, padding=(0, 1))

    # build a complete day list across the full range (zero-fill gaps)
    first = _date.fromtimestamp(c.first_ts) if c.first_ts else None
    last = _date.fromtimestamp(c.last_ts) if c.last_ts else None
    days: list[_date] = []
    d = first
    while d and last and d <= last:
        days.append(d)
        d = d + timedelta(days=1)
    bucket = "day"
    if len(days) > 120:
        # collapse to weekly buckets (ISO week start)
        weekly: dict[str, dict[str, int]] = {}
        for d in days:
            key = (d - timedelta(days=d.weekday())).isoformat()
            agg = weekly.setdefault(key, {"all": 0, "me": 0, "them": 0})
            iso = d.isoformat()
            agg["all"] += c.daily_counts.get(iso, 0)
            agg["me"] += c.daily_me.get(iso, 0)
            agg["them"] += c.daily_them.get(iso, 0)
        buckets = list(weekly.items())
        bucket = "week"
    else:
        buckets = [(d.isoformat(), {
            "all": c.daily_counts.get(d.isoformat(), 0),
            "me": c.daily_me.get(d.isoformat(), 0),
            "them": c.daily_them.get(d.isoformat(), 0),
        }) for d in days]

    max_total = max((b["all"] for _, b in buckets), default=0)
    if max_total == 0:
        return Panel(Text("(no data)"), title="Daily timeline", border_style="cyan",
                     expand=False, padding=(0, 1))

    bar_width = 40
    t = Table.grid(padding=(0, 1))
    t.add_column(width=11, no_wrap=True)        # date / week-start
    t.add_column(width=bar_width + 2)            # stacked bar
    t.add_column(width=10, justify="right")     # count
    t.add_column(width=14, justify="right", style="dim")  # split

    for key, agg in buckets:
        total = agg["all"]
        me = agg["me"]
        them = agg["them"]
        if total == 0:
            bar = Text("·", style="dim")
        else:
            cells = max(1, round(total / max_total * bar_width))
            me_cells = round(cells * me / total) if total else 0
            them_cells = max(0, cells - me_cells)
            bar = Text()
            bar.append("█" * me_cells, style="cyan")
            bar.append("█" * them_cells, style="magenta")
        t.add_row(
            Text(key, style="cyan"),
            bar,
            Text(f"{total:,}" if total else "·",
                 style="bright_white" if total else "dim"),
            Text(f"{me:,}/{them:,}" if total else "",
                 style="dim"),
        )

    legend = Text()
    legend.append("█", style="cyan")
    legend.append(f" {my_label}    ", style="dim")
    legend.append("█", style="magenta")
    legend.append(f" {other_name}", style="dim")

    title = "Daily timeline" if bucket == "day" else "Weekly timeline (ISO week start; range too long for per-day)"
    return Panel(Group(t, legend), title=title, border_style="cyan",
                 expand=False, padding=(0, 1))


def render(c: Counts, contact: ContactRecord, my_label: str, console) -> None:
    """Print the stats panel to console."""
    from rich.box import ROUNDED
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    if c.total == 0:
        console.print(Panel("No messages.", title="Stats", border_style="yellow"))
        return

    # ── header ──────────────────────────────────────────────────────────────
    head = Table.grid(padding=(0, 2))
    head.add_column(style="cyan bold", no_wrap=True)
    head.add_column(style="bright_white")
    head.add_row("Contact", f"{contact.display_name}  [dim]({contact.alias or contact.username})[/]")
    head.add_row("Range",
                 f"{datetime.fromtimestamp(c.first_ts).date()} → "
                 f"{datetime.fromtimestamp(c.last_ts).date()}  "
                 f"[dim]({c.active_days} active days)[/]")
    head.add_row("Messages", f"{c.total:,}")
    by_sender_str = "  ".join(f"[bold]{s}[/] {n:,} ({n/c.total*100:.0f}%)"
                              for s, n in c.by_sender.most_common())
    head.add_row("By sender", Text.from_markup(by_sender_str))

    # ── types ───────────────────────────────────────────────────────────────
    t_types = Table(box=ROUNDED, show_header=True, header_style="bold cyan",
                    title="Message types", title_style="bold")
    t_types.add_column("Type")
    t_types.add_column("Count", justify="right")
    t_types.add_column("Share", justify="right")
    t_types.add_column("")
    max_type = max(c.by_type.values()) if c.by_type else 0
    for typ, n in sorted(c.by_type.items(), key=lambda kv: -kv[1]):
        t_types.add_row(
            _TYPE_LABEL.get(typ, f"type:{typ}"),
            f"{n:,}",
            f"{n / c.total * 100:.1f}%",
            Text(_bar(n, max_type, width=25), style="cyan"),
        )

    # ── monthly volume ──────────────────────────────────────────────────────
    t_months = Table(box=ROUNDED, show_header=True, header_style="bold cyan",
                     title="Per month", title_style="bold")
    t_months.add_column("Month")
    t_months.add_column("Msgs", justify="right")
    t_months.add_column("")
    max_month = max(c.by_month.values()) if c.by_month else 0
    for month in sorted(c.by_month.keys()):
        n = c.by_month[month]
        t_months.add_row(month, f"{n:,}",
                         Text(_bar(n, max_month, width=40), style="green"))

    # ── hourly heatmap ──────────────────────────────────────────────────────
    t_hours = Table.grid(padding=(0, 1))
    t_hours.add_column(width=4, no_wrap=True)
    t_hours.add_column()
    t_hours.add_column(width=8, justify="right")
    max_hour = max(c.by_hour.values()) if c.by_hour else 0
    for h in range(24):
        n = c.by_hour.get(h, 0)
        t_hours.add_row(
            Text(f"{h:02d}h", style="cyan"),
            Text(_bar(n, max_hour, width=50), style="magenta"),
            Text(f"{n:,}", style="dim"),
        )
    hour_panel = Panel(t_hours, title="Activity by hour", border_style="cyan",
                       padding=(0, 1), expand=False)

    # ── weekday distribution ───────────────────────────────────────────────
    t_dow = Table.grid(padding=(0, 1))
    t_dow.add_column(width=4, no_wrap=True)
    t_dow.add_column()
    t_dow.add_column(width=8, justify="right")
    days_label = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    max_dow = max(c.by_weekday.values()) if c.by_weekday else 0
    for i, label in enumerate(days_label):
        n = c.by_weekday.get(i, 0)
        t_dow.add_row(Text(label, style="cyan"),
                      Text(_bar(n, max_dow, width=50), style="yellow"),
                      Text(f"{n:,}", style="dim"))
    dow_panel = Panel(t_dow, title="Activity by weekday", border_style="cyan",
                      padding=(0, 1), expand=False)

    # ── top emojis + top words ──────────────────────────────────────────────
    t_emoji = Table(box=ROUNDED, show_header=True, header_style="bold cyan",
                    title="Top emoji / stickers", title_style="bold")
    t_emoji.add_column("Tag")
    t_emoji.add_column("Count", justify="right")
    for tag, n in c.top_emojis:
        t_emoji.add_row(tag, f"{n:,}")

    t_words = Table(box=ROUNDED, show_header=True, header_style="bold cyan",
                    title="Top words", title_style="bold")
    t_words.add_column("Word")
    t_words.add_column("Count", justify="right")
    for w, n in c.top_words:
        t_words.add_row(w, f"{n:,}")

    # ── bidirectional response time ──────────────────────────────────────────
    other_name = contact.display_name
    t_resp_mine = _response_table(f"{my_label} → {other_name}", c.response_time_seconds)
    t_resp_their = _response_table(f"{other_name} → {my_label}", c.their_response_time_seconds)

    t_silence = Table(box=ROUNDED, show_header=True, header_style="bold cyan",
                      title="Silence", title_style="bold")
    t_silence.add_column("Stat")
    t_silence.add_column("Value", justify="right", style="bright_white")
    t_silence.add_row("Longest gap", _fmt_dur(c.longest_silence_seconds))
    t_silence.add_row("Between",
                      f"{c.longest_silence_between[0]} → {c.longest_silence_between[1]}")

    # ── daily timeline ────────────────────────────────────────────────────
    daily_panel = _render_daily_timeline(c, my_label, other_name)

    body = Group(head, "",
                 t_types, "",
                 t_months, "",
                 daily_panel, "",
                 hour_panel, dow_panel, "",
                 t_emoji, t_words, "",
                 t_resp_mine, t_resp_their, "",
                 t_silence)
    console.print()
    console.print(Panel(body, title=Text("Conversation stats", style="bold green"),
                        border_style="green", padding=(1, 2), expand=False))

