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
    TYPE_MEDIA_GENERIC,
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
    by_type_me:    dict[int, int] = field(default_factory=Counter)
    by_type_them:  dict[int, int] = field(default_factory=Counter)
    by_month: dict[str, int] = field(default_factory=Counter)        # "2026-04"
    by_weekday: dict[int, int] = field(default_factory=Counter)      # 0=Mon
    by_weekday_me:   dict[int, int] = field(default_factory=Counter)
    by_weekday_them: dict[int, int] = field(default_factory=Counter)
    by_hour: dict[int, int] = field(default_factory=Counter)         # 0..23
    by_hour_me:   dict[int, int] = field(default_factory=Counter)
    by_hour_them: dict[int, int] = field(default_factory=Counter)
    # (weekday, hour) -> count, for activity heatmaps
    hour_dow_me:   dict[tuple[int, int], int] = field(default_factory=Counter)
    hour_dow_them: dict[tuple[int, int], int] = field(default_factory=Counter)
    top_emojis: list[tuple[str, int]] = field(default_factory=list)
    top_words: list[tuple[str, int]] = field(default_factory=list)
    response_time_seconds: list[int] = field(default_factory=list)        # me reply latency
    their_response_time_seconds: list[int] = field(default_factory=list)  # their reply latency
    longest_silence_seconds: int = 0
    longest_silence_between: tuple[str, str] = ("", "")              # (iso_a, iso_b)
    first_ts: int = 0
    last_ts: int = 0
    active_days: int = 0
    daily_counts: dict[str, int] = field(default_factory=dict)       # "2026-04-09" → total
    daily_me:     dict[str, int] = field(default_factory=dict)
    daily_them:   dict[str, int] = field(default_factory=dict)
    daily_words_me:   dict[str, int] = field(default_factory=dict)   # for the timeline word-count toggle
    daily_words_them: dict[str, int] = field(default_factory=dict)
    # Time-stamped samples for the "metrics over time" plots in the
    # HTML report. Each element is (sample_ts_seconds, value).
    my_reply_times_ts:    list[tuple[int, int]] = field(default_factory=list)
    their_reply_times_ts: list[tuple[int, int]] = field(default_factory=list)
    my_chain_sizes_ts:    list[tuple[int, int]] = field(default_factory=list)
    their_chain_sizes_ts: list[tuple[int, int]] = field(default_factory=list)
    # chain stats — a "chain" is a run of consecutive messages from the same sender
    my_chain_lengths:    list[int] = field(default_factory=list)
    their_chain_lengths: list[int] = field(default_factory=list)
    my_chain_words:    list[int] = field(default_factory=list)   # words per chain
    their_chain_words: list[int] = field(default_factory=list)
    # who initiates a chain: at every switch, +1 to the side that just started talking
    chain_starts_me:    int = 0
    chain_starts_them:  int = 0
    # word totals
    my_total_words:    int = 0
    their_total_words: int = 0


_TYPE_LABEL = {
    TYPE_TEXT: "text",
    TYPE_IMAGE: "image",
    TYPE_VOICE: "voice",
    TYPE_VIDEO: "video",
    TYPE_STICKER: "sticker",
    TYPE_APPMSG: "appmsg",
    TYPE_CALL: "call",
    TYPE_SYSTEM: "system",
    TYPE_MEDIA_GENERIC: "media",
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
    # bidirectional response-time tracking
    pending_other_ts: int | None = None
    pending_me_ts: int | None = None
    # chain tracking — track msg-count AND word-count per chain
    current_chain_side: str | None = None
    current_chain_len: int = 0
    current_chain_words: int = 0
    current_chain_start_ts: int | None = None  # ts of the first message in the active chain

    def _close_chain(side, length, words, start_ts):
        if length <= 0:
            return
        if side == "me":
            c.my_chain_lengths.append(length)
            c.my_chain_words.append(words)
            if start_ts is not None:
                c.my_chain_sizes_ts.append((start_ts, length))
        elif side == "them":
            c.their_chain_lengths.append(length)
            c.their_chain_words.append(words)
            if start_ts is not None:
                c.their_chain_sizes_ts.append((start_ts, length))

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
        side: str | None = None
        if m.is_me:
            daily_me[date_str] += 1
            c.by_hour_me[dt.hour] += 1
            c.by_weekday_me[dt.weekday()] += 1
            c.by_type_me[m.type] += 1
            c.hour_dow_me[(dt.weekday(), dt.hour)] += 1
            side = "me"
        elif m.sender_username == contact.username:
            daily_them[date_str] += 1
            c.by_hour_them[dt.hour] += 1
            c.by_weekday_them[dt.weekday()] += 1
            c.by_type_them[m.type] += 1
            c.hour_dow_them[(dt.weekday(), dt.hour)] += 1
            side = "them"

        # word count for this message (text messages only — non-text is 0)
        msg_words = 0
        if m.type == TYPE_TEXT and m.content:
            text = sticker_to_emoji(m.content) if unify_emoji_tags else m.content
            msg_words = len(_WORD_RE.findall(text))
        if side == "me":
            c.my_total_words += msg_words
            c.daily_words_me[date_str] = c.daily_words_me.get(date_str, 0) + msg_words
        elif side == "them":
            c.their_total_words += msg_words
            c.daily_words_them[date_str] = c.daily_words_them.get(date_str, 0) + msg_words

        # chain bookkeeping (msg + word count)
        if side is not None:
            if current_chain_side == side:
                current_chain_len += 1
                current_chain_words += msg_words
            else:
                _close_chain(current_chain_side, current_chain_len,
                             current_chain_words, current_chain_start_ts)
                current_chain_side = side
                current_chain_len = 1
                current_chain_words = msg_words
                current_chain_start_ts = m.create_time
                if side == "me":
                    c.chain_starts_me += 1
                else:
                    c.chain_starts_them += 1

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
                    latency = m.create_time - pending_other_ts
                    c.response_time_seconds.append(latency)
                    c.my_reply_times_ts.append((m.create_time, latency))
                    pending_other_ts = None
                # remember when *I* started waiting for their reply
                if pending_me_ts is None:
                    pending_me_ts = m.create_time
            elif m.sender_username == contact.username:
                if pending_me_ts is not None:
                    latency = m.create_time - pending_me_ts
                    c.their_response_time_seconds.append(latency)
                    c.their_reply_times_ts.append((m.create_time, latency))
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

    # close the final open chain
    _close_chain(current_chain_side, current_chain_len,
                 current_chain_words, current_chain_start_ts)

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


def _mean(seq):
    return (sum(seq) / len(seq)) if seq else 0.0


def _stdev(seq):
    """Population standard deviation. 0 if <2 samples."""
    if len(seq) < 2:
        return 0.0
    import math
    m = _mean(seq)
    return math.sqrt(sum((x - m) ** 2 for x in seq) / len(seq))


def _hist_blocks(seq: list[int], n_bins: int = 16) -> str:
    """Log-scaled response-time histogram as ▁▂▃▄▅▆▇█ block characters.
    Buckets:  <30s · 30s-1m · 1-2m · 2-5m · 5-10m · 10-30m · 30m-1h · 1-2h ·
              2-6h · 6-12h · 12-24h · 1-2d · 2-7d · >7d  (14 buckets)."""
    if not seq:
        return ""
    # log-ish buckets in seconds
    edges = [30, 60, 120, 300, 600, 1800, 3600, 7200,
             21600, 43200, 86400, 172800, 604800]
    bins = [0] * (len(edges) + 1)
    for x in seq:
        placed = False
        for i, e in enumerate(edges):
            if x < e:
                bins[i] += 1
                placed = True
                break
        if not placed:
            bins[-1] += 1
    blocks = " ▁▂▃▄▅▆▇█"
    mx = max(bins) or 1
    out = ""
    for v in bins:
        idx = min(len(blocks) - 1, max(0, round(v / mx * (len(blocks) - 1))))
        out += blocks[idx]
    return out


def _hist_legend() -> str:
    return "<30s 1m 2m 5m 10m 30m 1h 2h 6h 12h 1d 2d 7d >7d"


def _response_table(title: str, rt: list[int], subtitle: str | None = None):
    from rich.box import ROUNDED
    from rich.table import Table
    t = Table(box=ROUNDED, show_header=True, header_style="bold cyan",
              title=title, title_style="bold",
              caption=subtitle, caption_style="dim italic")
    t.add_column("Stat")
    t.add_column("Value", justify="right", style="bright_white")
    if rt:
        t.add_row("Samples", f"{len(rt):,}")
        t.add_row("Mean",    _fmt_dur(_mean(rt)))
        t.add_row("Std dev", _fmt_dur(_stdev(rt)))
        t.add_row("Median",  _fmt_dur(_percentile(rt, 0.5)))
        t.add_row("p75",     _fmt_dur(_percentile(rt, 0.75)))
        t.add_row("p90",     _fmt_dur(_percentile(rt, 0.9)))
        t.add_row("p99",     _fmt_dur(_percentile(rt, 0.99)))
        t.add_row("Max",     _fmt_dur(max(rt)))
    else:
        t.add_row("Samples", "0 (one-way conversation)")
    return t


def _hist_panel(title: str, rt: list[int], color: str = "cyan"):
    """Small panel showing the log-bucket histogram + bucket-label legend."""
    from rich.console import Group
    from rich.panel import Panel
    from rich.text import Text
    if not rt:
        return Panel(Text("(no samples)", style="dim"), title=title,
                     border_style="cyan", expand=False, padding=(0, 1))
    body = Text(_hist_blocks(rt), style=color)
    legend = Text(_hist_legend(), style="dim")
    return Panel(Group(body, legend), title=title, border_style="cyan",
                 expand=False, padding=(0, 1))


def _render_daily_timeline(c: Counts, my_label: str, other_name: str):
    """Per-day stacked bar of message volume; auto-falls back to per-week for long ranges."""
    from datetime import date as _date
    from datetime import timedelta

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
    t.add_column(width=8, justify="right")      # total
    t.add_column(width=10, justify="right", style="cyan")    # me count (matches bar color)
    t.add_column(width=10, justify="right", style="magenta") # them count

    # explicit header row so the colors are unambiguous
    t.add_row(
        Text("Date", style="bold"),
        Text(f"  ◀ {my_label} (cyan)   |   {other_name} (magenta) ▶", style="dim"),
        Text("total", style="bold"),
        Text(my_label, style="bold cyan"),
        Text(other_name, style="bold magenta"),
    )

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
            Text(key, style="dim"),
            bar,
            Text(f"{total:,}" if total else "·",
                 style="bright_white" if total else "dim"),
            Text(f"{me:,}" if total else "",
                 style="cyan" if me >= them else "dim cyan"),
            Text(f"{them:,}" if total else "",
                 style="magenta" if them > me else "dim magenta"),
        )

    title = ("Daily timeline" if bucket == "day"
             else "Weekly timeline (ISO week start; range too long for per-day)")
    return Panel(t, title=title, border_style="cyan",
                 expand=False, padding=(0, 1))


def _render_chain_dynamics(c: Counts, my_label: str, other_name: str):
    """Chain stats with std dev + per-chain word counts."""
    from rich.box import ROUNDED
    from rich.table import Table
    t = Table(box=ROUNDED, show_header=True, header_style="bold cyan",
              title="Chain dynamics", title_style="bold",
              caption="a 'chain' = consecutive messages from the same sender "
                      "(uninterrupted by the other)",
              caption_style="dim italic")
    t.add_column("Stat")
    t.add_column(my_label, justify="right", style="bright_white")
    t.add_column(other_name, justify="right", style="bright_white")

    def _row(label: str, mine, theirs):
        t.add_row(label, mine, theirs)

    _row("Chains started",
         f"{c.chain_starts_me:,}", f"{c.chain_starts_them:,}")

    # message-count stats per chain
    _row("Median chain msgs",
         f"{_percentile(c.my_chain_lengths, 0.5)}",
         f"{_percentile(c.their_chain_lengths, 0.5)}")
    _row("Mean chain msgs",
         f"{_mean(c.my_chain_lengths):.2f}",
         f"{_mean(c.their_chain_lengths):.2f}")
    _row("Std dev (msgs)",
         f"{_stdev(c.my_chain_lengths):.2f}",
         f"{_stdev(c.their_chain_lengths):.2f}")
    _row("Longest chain (msgs)",
         f"{max(c.my_chain_lengths, default=0)}",
         f"{max(c.their_chain_lengths, default=0)}")
    _row("Total msgs",
         f"{sum(c.my_chain_lengths):,}",
         f"{sum(c.their_chain_lengths):,}")
    t.add_section()
    # word-count stats per chain
    _row("Median chain words",
         f"{_percentile(c.my_chain_words, 0.5)}",
         f"{_percentile(c.their_chain_words, 0.5)}")
    _row("Mean chain words",
         f"{_mean(c.my_chain_words):.1f}",
         f"{_mean(c.their_chain_words):.1f}")
    _row("Std dev (words)",
         f"{_stdev(c.my_chain_words):.1f}",
         f"{_stdev(c.their_chain_words):.1f}")
    _row("Longest chain (words)",
         f"{max(c.my_chain_words, default=0):,}",
         f"{max(c.their_chain_words, default=0):,}")
    _row("Total words",
         f"{c.my_total_words:,}",
         f"{c.their_total_words:,}")
    return t


def _split_bar(me: int, them: int, max_total: int, width: int = 50):
    from rich.text import Text
    bar = Text()
    total = me + them
    if total == 0 or max_total == 0:
        return Text("·", style="dim")
    cells = max(1, round(total / max_total * width))
    me_cells = round(cells * me / total)
    them_cells = max(0, cells - me_cells)
    bar.append("█" * me_cells, style="cyan")
    bar.append("█" * them_cells, style="magenta")
    return bar


def _stacked_panel(title: str, labels: list[str],
                   me_counts: dict, them_counts: dict,
                   my_label: str, other_name: str,
                   label_width: int = 4):
    """Per-bucket stacked-bar panel: cyan = me, magenta = them."""
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    t = Table.grid(padding=(0, 1))
    t.add_column(width=label_width, no_wrap=True)
    t.add_column(width=52)
    t.add_column(width=12, justify="right", style="dim")
    max_total = 0
    for lab in labels:
        # support int (hour) or int (weekday) keys mapped onto labels
        key = lab if not lab.isdigit() else int(lab)
        total = me_counts.get(key, 0) + them_counts.get(key, 0)
        if total > max_total:
            max_total = total
    for lab in labels:
        key = lab if not lab.isdigit() else int(lab)
        me = me_counts.get(key, 0)
        them = them_counts.get(key, 0)
        bar = _split_bar(me, them, max_total, width=50)
        t.add_row(
            Text(str(lab), style="cyan"),
            bar,
            Text(f"{me:,}/{them:,}" if (me + them) else "·",
                 style="dim"),
        )
    legend = Text()
    legend.append("█", style="cyan")
    legend.append(f" {my_label}    ", style="dim")
    legend.append("█", style="magenta")
    legend.append(f" {other_name}", style="dim")
    return Panel(Group(t, legend), title=title, border_style="cyan",
                 expand=False, padding=(0, 1))


def _media_table(c: Counts, my_label: str, other_name: str):
    """Per-sender breakdown of message types (image / voice / video / call / etc.)."""
    from rich.box import ROUNDED
    from rich.table import Table
    t = Table(box=ROUNDED, show_header=True, header_style="bold cyan",
              title="Message types by sender", title_style="bold")
    t.add_column("Type")
    t.add_column(my_label, justify="right", style="cyan")
    t.add_column(other_name, justify="right", style="magenta")
    t.add_column("Total", justify="right", style="bright_white")
    for typ in sorted(c.by_type.keys(), key=lambda k: -c.by_type[k]):
        label = _TYPE_LABEL.get(typ, f"type:{typ}")
        me = c.by_type_me.get(typ, 0)
        them = c.by_type_them.get(typ, 0)
        total = c.by_type.get(typ, 0)
        t.add_row(label, f"{me:,}", f"{them:,}", f"{total:,}")
    return t


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

    other_name = contact.display_name

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

    # ── types (per-sender) ────────────────────────────────────────────────
    t_types = _media_table(c, my_label, other_name)

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

    # ── hourly heatmap (stacked by sender) ────────────────────────────────
    hour_labels = [f"{h:02d}h" for h in range(24)]
    # _stacked_panel sees label-as-string; we pass int-keyed dicts → adapt
    me_h = {f"{h:02d}h": c.by_hour_me.get(h, 0) for h in range(24)}
    them_h = {f"{h:02d}h": c.by_hour_them.get(h, 0) for h in range(24)}
    hour_panel = _stacked_panel("Activity by hour",
                                hour_labels, me_h, them_h,
                                my_label, other_name, label_width=4)

    # ── weekday distribution (stacked by sender) ──────────────────────────
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    me_d = {dow_names[i]: c.by_weekday_me.get(i, 0) for i in range(7)}
    them_d = {dow_names[i]: c.by_weekday_them.get(i, 0) for i in range(7)}
    dow_panel = _stacked_panel("Activity by weekday",
                               dow_names, me_d, them_d,
                               my_label, other_name, label_width=4)

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

    # ── chain dynamics (msgs + words, both with std dev) ──────────────────
    t_chain = _render_chain_dynamics(c, my_label, other_name)

    # ── bidirectional response time (table + log-histogram panel each) ────
    t_resp_mine = _response_table(
        f"{my_label} replies to {other_name}", c.response_time_seconds,
        subtitle=f"how long {my_label!r} takes to start replying after {other_name} sends",
    )
    hist_mine = _hist_panel(f"{my_label} reply distribution",
                            c.response_time_seconds, color="cyan")
    t_resp_their = _response_table(
        f"{other_name} replies to {my_label}", c.their_response_time_seconds,
        subtitle=f"how long {other_name!r} takes to start replying after {my_label} sends",
    )
    hist_their = _hist_panel(f"{other_name} reply distribution",
                             c.their_response_time_seconds, color="magenta")

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
                 t_chain, "",
                 t_resp_mine, hist_mine,
                 t_resp_their, hist_their, "",
                 t_silence)
    console.print()
    console.print(Panel(body, title=Text("Conversation stats", style="bold green"),
                        border_style="green", padding=(1, 2), expand=False))



