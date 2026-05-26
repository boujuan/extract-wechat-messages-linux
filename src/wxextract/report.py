"""Native interactive HTML report for `wxextract stats`.

Self-contained dark-themed report with Plotly.js charts (loaded via
CDN), KPI cards, tables, and a sticky navigation sidebar. Works for
a single contact or as a multi-contact dashboard.
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from wxextract.contacts import ContactRecord
from wxextract.stats import (
    _TYPE_LABEL,
    Counts,
    _fmt_dur,
    _mean,
    _percentile,
    _stdev,
)

_PAL = {
    "bg":      "#0d1117",
    "surface": "#161b22",
    "elev":    "#1c2128",
    "border":  "#30363d",
    "muted":   "#8b949e",
    "text":    "#e6edf3",
    "accent":  "#58a6ff",
    "warn":    "#d29922",
    "ok":      "#3fb950",
    "me":      "#22d3ee",   # cyan
    "them":    "#ec4899",   # magenta/pink
    "grid":    "#21262d",
}

# Plotly via CDN keeps the report file small. The user only needs
# internet on first open — browsers cache it after that.
_PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"

# ── Plotly layout helpers ───────────────────────────────────────────

def _layout(height: int = 320, **overrides) -> dict:
    axis = {"gridcolor": _PAL["grid"], "linecolor": _PAL["border"],
            "zerolinecolor": _PAL["border"], "tickcolor": _PAL["border"],
            "tickfont": {"color": _PAL["muted"]}}
    base = {
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {"color": _PAL["text"],
                 "family": "ui-sans-serif, -apple-system, 'Segoe UI', Inter, sans-serif",
                 "size": 12},
        "margin": {"l": 60, "r": 24, "t": 12, "b": 50},
        "hoverlabel": {"bgcolor": _PAL["surface"], "bordercolor": _PAL["border"],
                       "font": {"color": _PAL["text"]}},
        "legend": {"bgcolor": "rgba(22,27,34,0.6)",
                   "bordercolor": _PAL["border"], "borderwidth": 1,
                   "font": {"color": _PAL["text"]}},
        "xaxis": dict(axis),
        "yaxis": dict(axis),
        "height": height,
        "autosize": True,
    }
    base.update(overrides)
    return base


def _config() -> dict:
    return {
        "displaylogo": False,
        "responsive": True,
        "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d"],
        "toImageButtonOptions": {"format": "png", "scale": 2, "filename": "wxextract"},
    }


# ── per-contact figures ─────────────────────────────────────────────

def _fig_monthly(c: Counts) -> dict:
    months = sorted(c.by_month.keys())
    if not months:
        return None
    lay = _layout(height=260)
    lay["yaxis"]["title"] = "messages"
    return {
        "data": [{"type": "bar", "x": months,
                  "y": [c.by_month[m] for m in months],
                  "marker": {"color": _PAL["accent"]},
                  "hovertemplate": "<b>%{x}</b><br>%{y:,} messages<extra></extra>"}],
        "layout": lay,
    }


def _roll_mean(seq: list[float], window: int) -> list[float]:
    """Trailing window mean — out[i] = mean(seq[max(0,i-window+1):i+1])."""
    out: list[float] = []
    win: list[float] = []
    for v in seq:
        win.append(v)
        if len(win) > window:
            win.pop(0)
        out.append(sum(win) / len(win))
    return out


def _fig_daily(c: Counts) -> dict:
    """Daily timeline with a Messages/Words toggle.

    Six traces total: 3 for messages (you, other, 7-day avg total) and 3
    for words. A Plotly `updatemenus` button swaps visibility + y-axis
    title between the two metrics.
    """
    dates = sorted(c.daily_counts.keys())
    if not dates:
        return None
    me_msgs = [c.daily_me.get(d, 0) for d in dates]
    them_msgs = [c.daily_them.get(d, 0) for d in dates]
    me_words = [c.daily_words_me.get(d, 0) for d in dates]
    them_words = [c.daily_words_them.get(d, 0) for d in dates]
    msg_roll = _roll_mean(
        [a + b for a, b in zip(me_msgs, them_msgs, strict=False)], 7)
    word_roll = _roll_mean(
        [a + b for a, b in zip(me_words, them_words, strict=False)], 7)

    lay = _layout(height=420, barmode="stack", hovermode="x unified")
    lay["xaxis"]["type"] = "date"
    lay["xaxis"]["rangeslider"] = {"visible": True, "thickness": 0.06,
                                   "bgcolor": _PAL["surface"]}
    lay["yaxis"]["title"] = "messages / day"
    lay["margin"] = {"l": 60, "r": 24, "t": 50, "b": 50}
    lay["updatemenus"] = [{
        "type": "buttons",
        "direction": "right",
        "x": 0.0, "xanchor": "left",
        "y": 1.15, "yanchor": "top",
        "bgcolor": _PAL["surface"],
        "bordercolor": _PAL["border"],
        "font": {"color": _PAL["text"], "size": 11},
        "active": 0,
        "buttons": [
            {"label": "Messages", "method": "update",
             "args": [{"visible": [True, True, True, False, False, False]},
                      {"yaxis.title.text": "messages / day"}]},
            {"label": "Words", "method": "update",
             "args": [{"visible": [False, False, False, True, True, True]},
                      {"yaxis.title.text": "words / day"}]},
        ],
    }]
    return {
        "data": [
            # Messages traces (visible by default)
            {"type": "bar", "x": dates, "y": me_msgs, "name": "You",
             "marker": {"color": _PAL["me"]},
             "legendgroup": "msgs", "visible": True},
            {"type": "bar", "x": dates, "y": them_msgs, "name": "Other",
             "marker": {"color": _PAL["them"]},
             "legendgroup": "msgs", "visible": True},
            {"type": "scatter", "x": dates, "y": msg_roll, "name": "7-day avg",
             "mode": "lines", "line": {"color": _PAL["warn"], "width": 2},
             "legendgroup": "msgs", "visible": True},
            # Words traces (hidden until toggled)
            {"type": "bar", "x": dates, "y": me_words, "name": "You (words)",
             "marker": {"color": _PAL["me"]},
             "legendgroup": "words", "visible": False},
            {"type": "bar", "x": dates, "y": them_words, "name": "Other (words)",
             "marker": {"color": _PAL["them"]},
             "legendgroup": "words", "visible": False},
            {"type": "scatter", "x": dates, "y": word_roll, "name": "7-day avg (words)",
             "mode": "lines", "line": {"color": _PAL["warn"], "width": 2},
             "legendgroup": "words", "visible": False},
        ],
        "layout": lay,
    }


def _bucket_daily(samples: list[tuple[int, int]]) -> tuple[list[str], list[float]]:
    """Group (ts_seconds, value) samples by date and return (dates, daily_means)."""
    if not samples:
        return [], []
    from collections import defaultdict as _dd
    bucket: dict[str, list[int]] = _dd(list)
    for ts, v in samples:
        d = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        bucket[d].append(v)
    dates = sorted(bucket.keys())
    return dates, [sum(bucket[d]) / len(bucket[d]) for d in dates]


def _fig_reply_time_over_time(c: Counts) -> dict:
    me_d, me_v = _bucket_daily(c.my_reply_times_ts)
    them_d, them_v = _bucket_daily(c.their_reply_times_ts)
    if not me_v and not them_v:
        return None
    win = 14
    me_roll = _roll_mean(me_v, win)
    them_roll = _roll_mean(them_v, win)
    lay = _layout(height=320, hovermode="x unified")
    lay["xaxis"]["type"] = "date"
    lay["yaxis"]["type"] = "log"
    lay["yaxis"]["title"] = "reply latency (s, log)"
    lay["yaxis"]["tickvals"] = [10, 60, 600, 3600, 86400]
    lay["yaxis"]["ticktext"] = ["10s", "1m", "10m", "1h", "1d"]
    return {
        "data": [
            {"type": "scatter", "x": me_d, "y": me_roll,
             "name": "You reply (14d avg)",
             "mode": "lines", "line": {"color": _PAL["me"], "width": 2}},
            {"type": "scatter", "x": them_d, "y": them_roll,
             "name": "Other replies (14d avg)",
             "mode": "lines", "line": {"color": _PAL["them"], "width": 2}},
        ],
        "layout": lay,
    }


def _fig_burst_size_over_time(c: Counts) -> dict:
    me_d, me_v = _bucket_daily(c.my_chain_sizes_ts)
    them_d, them_v = _bucket_daily(c.their_chain_sizes_ts)
    if not me_v and not them_v:
        return None
    win = 14
    me_roll = _roll_mean(me_v, win)
    them_roll = _roll_mean(them_v, win)
    lay = _layout(height=320, hovermode="x unified")
    lay["xaxis"]["type"] = "date"
    lay["yaxis"]["title"] = "avg burst size (msgs/chain)"
    return {
        "data": [
            {"type": "scatter", "x": me_d, "y": me_roll,
             "name": "Your bursts (14d avg)",
             "mode": "lines", "line": {"color": _PAL["me"], "width": 2}},
            {"type": "scatter", "x": them_d, "y": them_roll,
             "name": "Their bursts (14d avg)",
             "mode": "lines", "line": {"color": _PAL["them"], "width": 2}},
        ],
        "layout": lay,
    }


def _fig_cumulative(c: Counts) -> dict:
    dates = sorted(c.daily_counts.keys())
    if not dates:
        return None
    cum_me, cum_them = 0, 0
    me_s, them_s = [], []
    for d in dates:
        cum_me += c.daily_me.get(d, 0)
        cum_them += c.daily_them.get(d, 0)
        me_s.append(cum_me)
        them_s.append(cum_them)
    lay = _layout(height=300, hovermode="x unified")
    lay["xaxis"]["type"] = "date"
    lay["yaxis"]["title"] = "cumulative messages"
    return {
        "data": [
            {"type": "scatter", "x": dates, "y": me_s, "name": "You",
             "mode": "lines", "stackgroup": "one",
             "line": {"color": _PAL["me"], "width": 0},
             "fillcolor": _PAL["me"]},
            {"type": "scatter", "x": dates, "y": them_s, "name": "Other",
             "mode": "lines", "stackgroup": "one",
             "line": {"color": _PAL["them"], "width": 0},
             "fillcolor": _PAL["them"]},
        ],
        "layout": lay,
    }


def _fig_heatmap_hour_dow(c: Counts, side: str = "both") -> dict:
    labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    z = []
    for d in range(7):
        row = []
        for h in range(24):
            v_me = c.hour_dow_me.get((d, h), 0)
            v_them = c.hour_dow_them.get((d, h), 0)
            row.append(v_me if side == "me" else
                       v_them if side == "them" else
                       v_me + v_them)
        z.append(row)
    scale = ([[0, _PAL["bg"]], [0.05, "#0e4a3a"], [0.4, "#1f7a5a"],
              [0.8, "#3dc287"], [1, "#a4f7d6"]] if side == "me" else
             [[0, _PAL["bg"]], [0.05, "#4a0e3a"], [0.4, "#9a2e6a"],
              [0.8, "#e15399"], [1, "#fcd2e6"]] if side == "them" else
             [[0, _PAL["bg"]], [0.05, "#0c3a5a"], [0.4, "#1d6fa5"],
              [0.8, "#3da0e0"], [1, "#a4e0f7"]])
    lay = _layout(height=300)
    lay["xaxis"].update({"title": "hour of day", "dtick": 2,
                         "gridcolor": "rgba(0,0,0,0)"})
    lay["yaxis"].update({"autorange": "reversed",
                         "gridcolor": "rgba(0,0,0,0)"})
    return {
        "data": [{"type": "heatmap", "z": z, "x": list(range(24)), "y": labels,
                  "colorscale": scale, "showscale": True,
                  "colorbar": {"thickness": 10, "len": 0.7, "x": 1.02,
                               "tickfont": {"color": _PAL["muted"]}},
                  "hovertemplate": "<b>%{y} %{x}:00</b><br>%{z:,} msgs<extra></extra>"}],
        "layout": lay,
    }


def _fig_hour_bar(c: Counts) -> dict:
    hours = list(range(24))
    lay = _layout(height=280, barmode="group")
    lay["xaxis"].update({"title": "hour of day", "dtick": 1})
    lay["yaxis"]["title"] = "messages"
    return {
        "data": [
            {"type": "bar", "x": hours,
             "y": [c.by_hour_me.get(h, 0) for h in hours],
             "name": "You", "marker": {"color": _PAL["me"]}},
            {"type": "bar", "x": hours,
             "y": [c.by_hour_them.get(h, 0) for h in hours],
             "name": "Other", "marker": {"color": _PAL["them"]}},
        ],
        "layout": lay,
    }


def _fig_dow_bar(c: Counts) -> dict:
    labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    lay = _layout(height=280, barmode="group")
    lay["yaxis"]["title"] = "messages"
    return {
        "data": [
            {"type": "bar", "x": labels,
             "y": [c.by_weekday_me.get(i, 0) for i in range(7)],
             "name": "You", "marker": {"color": _PAL["me"]}},
            {"type": "bar", "x": labels,
             "y": [c.by_weekday_them.get(i, 0) for i in range(7)],
             "name": "Other", "marker": {"color": _PAL["them"]}},
        ],
        "layout": lay,
    }


def _fig_types(c: Counts) -> dict:
    type_ids = sorted(c.by_type.keys(), key=lambda t: -c.by_type[t])
    labels = [_TYPE_LABEL.get(t, f"t{t}") for t in type_ids]
    me_vals = [c.by_type_me.get(t, 0) for t in type_ids]
    them_vals = [c.by_type_them.get(t, 0) for t in type_ids]
    lay = _layout(height=300, barmode="stack")
    lay["yaxis"]["autorange"] = "reversed"
    return {
        "data": [
            {"type": "bar", "y": labels, "x": me_vals, "name": "You",
             "orientation": "h", "marker": {"color": _PAL["me"]},
             "text": [f"{v:,}" if v else "" for v in me_vals],
             "textposition": "inside", "textfont": {"color": "#0c1116"}},
            {"type": "bar", "y": labels, "x": them_vals, "name": "Other",
             "orientation": "h", "marker": {"color": _PAL["them"]},
             "text": [f"{v:,}" if v else "" for v in them_vals],
             "textposition": "inside", "textfont": {"color": "#0c1116"}},
        ],
        "layout": lay,
    }


def _fig_top_emojis(c: Counts, n: int = 15) -> dict:
    top = c.top_emojis[:n]
    if not top:
        return None
    labels = [e[0] for e in top][::-1]
    vals = [e[1] for e in top][::-1]
    lay = _layout(height=max(220, 24 * len(top) + 80))
    lay["yaxis"]["tickfont"] = {"size": 16}
    return {
        "data": [{"type": "bar", "orientation": "h",
                  "x": vals, "y": labels,
                  "marker": {"color": _PAL["warn"]},
                  "text": [f"{v:,}" for v in vals],
                  "textposition": "outside",
                  "textfont": {"color": _PAL["text"]},
                  "hovertemplate": "%{y}: %{x:,}<extra></extra>"}],
        "layout": lay,
    }


def _fig_top_words(c: Counts, n: int = 25) -> dict:
    top = c.top_words[:n]
    if not top:
        return None
    labels = [w[0] for w in top][::-1]
    vals = [w[1] for w in top][::-1]
    lay = _layout(height=max(220, 20 * len(top) + 80))
    return {
        "data": [{"type": "bar", "orientation": "h",
                  "x": vals, "y": labels,
                  "marker": {"color": _PAL["accent"]},
                  "text": [f"{v:,}" for v in vals],
                  "textposition": "outside",
                  "textfont": {"color": _PAL["text"]},
                  "hovertemplate": "%{y}: %{x:,}<extra></extra>"}],
        "layout": lay,
    }


_REPLY_EDGES = [30, 60, 120, 300, 600, 1800, 3600, 7200,
                21600, 43200, 86400, 172800, 604800]
_REPLY_LABELS = ["<30s", "30–60s", "1–2m", "2–5m", "5–10m", "10–30m",
                 "30m–1h", "1–2h", "2–6h", "6–12h", "12h–1d",
                 "1–2d", "2–7d", ">1w"]


def _bin_reply(seq: list[int]) -> list[int]:
    bins = [0] * (len(_REPLY_EDGES) + 1)
    for x in seq:
        placed = False
        for i, e in enumerate(_REPLY_EDGES):
            if x < e:
                bins[i] += 1
                placed = True
                break
        if not placed:
            bins[-1] += 1
    return bins


def _fig_reply_hist(c: Counts) -> dict:
    me_b = _bin_reply(c.response_time_seconds)
    them_b = _bin_reply(c.their_response_time_seconds)
    if not (sum(me_b) or sum(them_b)):
        return None
    lay = _layout(height=320, barmode="group")
    lay["xaxis"]["title"] = "reply latency (log-bucketed)"
    lay["yaxis"]["title"] = "samples"
    return {
        "data": [
            {"type": "bar", "x": _REPLY_LABELS, "y": me_b, "name": "You reply",
             "marker": {"color": _PAL["me"]}, "opacity": 0.9,
             "hovertemplate": "%{x}: %{y:,}<extra>You</extra>"},
            {"type": "bar", "x": _REPLY_LABELS, "y": them_b, "name": "Other replies",
             "marker": {"color": _PAL["them"]}, "opacity": 0.9,
             "hovertemplate": "%{x}: %{y:,}<extra>Other</extra>"},
        ],
        "layout": lay,
    }


def _fig_chain_box(c: Counts, key: str = "msgs") -> dict:
    if key == "msgs":
        me, them, title = c.my_chain_lengths, c.their_chain_lengths, "messages per chain"
    else:
        me, them, title = c.my_chain_words, c.their_chain_words, "words per chain"
    if not me and not them:
        return None
    lay = _layout(height=320)
    lay["yaxis"]["title"] = title
    lay["yaxis"]["type"] = "log"
    return {
        "data": [
            {"type": "box", "y": me, "name": "You",
             "marker": {"color": _PAL["me"]}, "boxmean": "sd",
             "line": {"color": _PAL["me"]}},
            {"type": "box", "y": them, "name": "Other",
             "marker": {"color": _PAL["them"]}, "boxmean": "sd",
             "line": {"color": _PAL["them"]}},
        ],
        "layout": lay,
    }


# ── multi-contact figures ────────────────────────────────────────────

def _fig_contact_totals(data: list[tuple[ContactRecord, Counts]]) -> dict:
    data = sorted(data, key=lambda x: -x[1].total)
    names = [r.display_name for r, _ in data]
    me = [sum(v for k, v in c.by_sender.items() if k.lower() == "me") for _, c in data]
    them = [c.total - m for (_, c), m in zip(data, me, strict=False)]
    lay = _layout(height=max(220, 36 * len(names) + 80), barmode="stack")
    lay["xaxis"]["title"] = "messages"
    return {
        "data": [
            {"type": "bar", "orientation": "h",
             "x": me[::-1], "y": names[::-1], "name": "You",
             "marker": {"color": _PAL["me"]},
             "hovertemplate": "%{y}: %{x:,} from You<extra></extra>"},
            {"type": "bar", "orientation": "h",
             "x": them[::-1], "y": names[::-1], "name": "Other",
             "marker": {"color": _PAL["them"]},
             "hovertemplate": "%{y}: %{x:,} from Other<extra></extra>"},
        ],
        "layout": lay,
    }


def _fig_contact_month_heat(data: list[tuple[ContactRecord, Counts]]) -> dict:
    data = sorted(data, key=lambda x: -x[1].total)
    months = sorted({m for _, c in data for m in c.by_month.keys()})
    if not months:
        return None
    names = [r.display_name for r, _ in data]
    z = [[c.by_month.get(m, 0) for m in months] for _, c in data]
    lay = _layout(height=max(280, 32 * len(names) + 80))
    lay["yaxis"].update({"autorange": "reversed", "gridcolor": "rgba(0,0,0,0)"})
    lay["xaxis"]["gridcolor"] = "rgba(0,0,0,0)"
    return {
        "data": [{"type": "heatmap", "z": z, "x": months, "y": names,
                  "colorscale": [[0, _PAL["bg"]], [0.05, "#0c3a5a"],
                                 [0.4, "#1d6fa5"], [0.8, "#3da0e0"],
                                 [1, "#a4e0f7"]],
                  "hovertemplate": "<b>%{y}</b> · %{x}<br>%{z:,} msgs<extra></extra>",
                  "colorbar": {"thickness": 10, "len": 0.7,
                               "tickfont": {"color": _PAL["muted"]}}}],
        "layout": lay,
    }


def _fig_reply_medians(data: list[tuple[ContactRecord, Counts]]) -> dict:
    data = sorted(data, key=lambda x: -x[1].total)
    names = [r.display_name for r, _ in data]
    me_med = [_percentile(c.response_time_seconds, 0.5) or 0 for _, c in data]
    them_med = [_percentile(c.their_response_time_seconds, 0.5) or 0 for _, c in data]
    lay = _layout(height=max(220, 36 * len(names) + 80), barmode="group")
    lay["xaxis"].update({"type": "log", "title": "seconds (log)",
                         "tickvals": [10, 60, 600, 3600, 86400],
                         "ticktext": ["10s", "1m", "10m", "1h", "1d"]})
    return {
        "data": [
            {"type": "bar", "orientation": "h",
             "x": me_med[::-1], "y": names[::-1],
             "name": "You reply in (median)",
             "marker": {"color": _PAL["me"]},
             "hovertemplate": "%{y}: %{x:,}s<extra>You</extra>"},
            {"type": "bar", "orientation": "h",
             "x": them_med[::-1], "y": names[::-1],
             "name": "Other replies in (median)",
             "marker": {"color": _PAL["them"]},
             "hovertemplate": "%{y}: %{x:,}s<extra>Other</extra>"},
        ],
        "layout": lay,
    }


# ── HTML helpers ────────────────────────────────────────────────────

def _slug(s: str) -> str:
    keep = "".join(c if c.isalnum() else "-" for c in s)
    return keep.strip("-").lower() or "contact"


def _kpi(label: str, value: str, sub: str = "", color: str | None = None) -> str:
    color = color or _PAL["accent"]
    return (f'<div class="kpi">'
            f'<div class="kpi-label">{html.escape(label)}</div>'
            f'<div class="kpi-value" style="color:{color}">{html.escape(value)}</div>'
            f'<div class="kpi-sub">{html.escape(sub)}</div>'
            f'</div>')


_FIG_COUNTER = [0]


def _figure_block(title: str, fig: dict | None, *, sub: str = "",
                  full: bool = False) -> str:
    if fig is None:
        return ""
    _FIG_COUNTER[0] += 1
    fid = f"fig-{_FIG_COUNTER[0]}"
    cls = "card full" if full else "card"
    spec = json.dumps(fig, default=str, ensure_ascii=False)
    return (f'<div class="{cls}">'
            f'<div class="card-title">{html.escape(title)}</div>'
            + (f'<div class="card-sub">{html.escape(sub)}</div>' if sub else '')
            + f'<div class="figure" id="{fid}"></div>'
            f'<script type="application/json" id="{fid}-spec">{spec}</script>'
            f'</div>')


def _table(title: str, headers: list[str], rows: list[list[str]],
           *, sub: str = "", classes: list[str] | None = None) -> str:
    cls_list = classes or [""] * len(headers)
    thead = "".join(f'<th class="{c}">{html.escape(h)}</th>'
                    for h, c in zip(headers, cls_list, strict=False))
    body = ""
    for row in rows:
        cells = "".join(f'<td class="{c}">{cell}</td>'
                        for cell, c in zip(row, cls_list, strict=False))
        body += f'<tr>{cells}</tr>'
    sub_html = f'<div class="card-sub">{html.escape(sub)}</div>' if sub else ''
    return (f'<div class="card">'
            f'<div class="card-title">{html.escape(title)}</div>'
            + sub_html
            + f'<table class="data"><thead><tr>{thead}</tr></thead>'
            f'<tbody>{body}</tbody></table>'
            f'</div>')


def _fmt(v: float | int | None, fn=_fmt_dur) -> str:
    if v is None or v == 0:
        return "—"
    return fn(int(v)) if fn is _fmt_dur else str(v)


# ── section renderers ───────────────────────────────────────────────

def _reply_table(c: Counts) -> str:
    rt_me, rt_them = c.response_time_seconds, c.their_response_time_seconds
    rows = [
        ["Samples", f"{len(rt_me):,}", f"{len(rt_them):,}"],
        ["Mean", _fmt(_mean(rt_me)), _fmt(_mean(rt_them))],
        ["Std dev", _fmt(_stdev(rt_me)), _fmt(_stdev(rt_them))],
        ["Median (p50)", _fmt(_percentile(rt_me, 0.5)), _fmt(_percentile(rt_them, 0.5))],
        ["p75", _fmt(_percentile(rt_me, 0.75)), _fmt(_percentile(rt_them, 0.75))],
        ["p90", _fmt(_percentile(rt_me, 0.9)), _fmt(_percentile(rt_them, 0.9))],
        ["p99", _fmt(_percentile(rt_me, 0.99)), _fmt(_percentile(rt_them, 0.99))],
        ["Max", _fmt(max(rt_me) if rt_me else 0), _fmt(max(rt_them) if rt_them else 0)],
    ]
    return _table("Reply-time percentiles",
                  ["Stat", "You", "Other"],
                  rows, classes=["", "num me", "num them"])


def _chain_table(c: Counts) -> str:
    def _med(s): return f"{_percentile(s, 0.5):,}" if s else "—"
    def _avg(s): return f"{_mean(s):.2f}" if s else "—"
    def _sd(s):  return f"{_stdev(s):.2f}" if s else "—"
    def _mx(s):  return f"{max(s):,}" if s else "—"
    rows = [
        ["Chains started", f"{c.chain_starts_me:,}", f"{c.chain_starts_them:,}"],
        ["Median msgs/chain", _med(c.my_chain_lengths), _med(c.their_chain_lengths)],
        ["Mean msgs/chain", _avg(c.my_chain_lengths), _avg(c.their_chain_lengths)],
        ["Std dev (msgs)", _sd(c.my_chain_lengths), _sd(c.their_chain_lengths)],
        ["Longest chain (msgs)", _mx(c.my_chain_lengths), _mx(c.their_chain_lengths)],
        ["Median words/chain", _med(c.my_chain_words), _med(c.their_chain_words)],
        ["Mean words/chain", _avg(c.my_chain_words), _avg(c.their_chain_words)],
        ["Std dev (words)", _sd(c.my_chain_words), _sd(c.their_chain_words)],
        ["Longest chain (words)", _mx(c.my_chain_words), _mx(c.their_chain_words)],
        ["Total words", f"{c.my_total_words:,}", f"{c.their_total_words:,}"],
    ]
    return _table("Chain dynamics",
                  ["Stat", "You", "Other"],
                  rows,
                  sub="A chain = consecutive messages from the same sender",
                  classes=["", "num me", "num them"])


def _media_table(c: Counts) -> str:
    type_ids = sorted(c.by_type.keys(), key=lambda t: -c.by_type[t])
    rows = []
    for t in type_ids:
        label = _TYPE_LABEL.get(t, f"t{t}")
        me = c.by_type_me.get(t, 0)
        them = c.by_type_them.get(t, 0)
        rows.append([label, f"{me:,}", f"{them:,}", f"{me + them:,}"])
    return _table("Message types by sender",
                  ["Type", "You", "Other", "Total"], rows,
                  classes=["", "num me", "num them", "num"])


def _render_contact(r: ContactRecord, c: Counts, my_label: str) -> str:
    sec_id = _slug(r.alias or r.username)
    first = datetime.fromtimestamp(c.first_ts) if c.first_ts else None
    last = datetime.fromtimestamp(c.last_ts) if c.last_ts else None
    span_days = ((last - first).days + 1) if first and last else 0
    me_n = sum(v for k, v in c.by_sender.items() if k.lower() == my_label.lower())
    them_n = c.total - me_n
    msgs_per_active = c.total / max(c.active_days, 1)
    me_avg_words = (c.my_total_words / max(me_n, 1)) if me_n else 0
    them_avg_words = (c.their_total_words / max(them_n, 1)) if them_n else 0
    kpis = "".join([
        _kpi("Total messages", f"{c.total:,}", f"~{msgs_per_active:.0f} per active day"),
        _kpi("Active days", f"{c.active_days:,}", f"of {span_days} day span"),
        _kpi("Your share", f"{100*me_n/max(c.total,1):.0f}%",
             f"{me_n:,} sent · {me_avg_words:.0f} avg words/msg", _PAL["me"]),
        _kpi("Their share", f"{100*them_n/max(c.total,1):.0f}%",
             f"{them_n:,} sent · {them_avg_words:.0f} avg words/msg", _PAL["them"]),
        _kpi("Median reply (you)",
             _fmt(_percentile(c.response_time_seconds, 0.5)) if c.response_time_seconds else "—",
             f"n={len(c.response_time_seconds):,}", _PAL["me"]),
        _kpi("Median reply (them)",
             _fmt(_percentile(c.their_response_time_seconds, 0.5)) if c.their_response_time_seconds else "—",
             f"n={len(c.their_response_time_seconds):,}", _PAL["them"]),
        _kpi("Longest silence",
             _fmt_dur(c.longest_silence_seconds) if c.longest_silence_seconds else "—",
             f"{c.longest_silence_between[0][:10]} → {c.longest_silence_between[1][:10]}"
             if c.longest_silence_seconds else "", _PAL["warn"]),
        _kpi("Total words",
             f"{c.my_total_words + c.their_total_words:,}",
             f"{c.my_total_words:,} you · {c.their_total_words:,} other"),
    ])

    source_label = {"wechat": "WeChat", "whatsapp": "WhatsApp",
                    "combined": "Combined"}.get(r.source, r.source or "")
    source_class = "src-" + (r.source or "wechat")
    badge = (f'<span class="tag {source_class}">{html.escape(source_label)}</span> '
             if source_label else '')
    sub = (f'{badge}'
           f'{c.total:,} messages · '
           f'{first.date() if first else ""} → {last.date() if last else ""} · '
           f'{c.active_days} active days · '
           f'<span class="tag">{html.escape(r.alias or r.username)}</span>')

    parts = [
        f'<section id="{sec_id}">',
        f'<h2>{html.escape(r.display_name)}</h2>',
        f'<div class="section-sub">{sub}</div>',
        f'<div class="kpis">{kpis}</div>',
        _figure_block("Daily timeline", _fig_daily(c), full=True,
                      sub="Stacked bars per day, split by sender. Line: 7-day rolling "
                          "average of the total. Toggle the buttons (top-left) to swap "
                          "between message count and word count."),
        _figure_block("Cumulative messages", _fig_cumulative(c), full=True,
                      sub="Running total over time — see growth and plateaus at a glance"),
        '<div class="grid cols-2">',
        _figure_block("Reply latency over time",
                      _fig_reply_time_over_time(c),
                      sub="14-day rolling average of reply latency, per sender. "
                          "Falling line = becoming more responsive over time."),
        _figure_block("Burst size over time",
                      _fig_burst_size_over_time(c),
                      sub="14-day rolling average of chain length (messages per "
                          "uninterrupted turn). Higher = more monologue-like; "
                          "lower = more back-and-forth."),
        '</div>',
        _figure_block("Weekday × hour activity heatmap (combined)",
                      _fig_heatmap_hour_dow(c, "both"), full=True,
                      sub="When are you two most active together?"),
        '<div class="grid cols-2">',
        _figure_block("Your activity by hour×weekday",
                      _fig_heatmap_hour_dow(c, "me"),
                      sub="Your messages only"),
        _figure_block("Their activity by hour×weekday",
                      _fig_heatmap_hour_dow(c, "them"),
                      sub="Other's messages only"),
        '</div>',
        '<div class="grid cols-2">',
        _figure_block("Hour-of-day", _fig_hour_bar(c),
                      sub="grouped: you (cyan) vs other (magenta)"),
        _figure_block("Weekday", _fig_dow_bar(c)),
        _figure_block("Per month", _fig_monthly(c)),
        _figure_block("Message types (stacked)", _fig_types(c)),
        '</div>',
        '<div class="grid cols-2">',
        _figure_block("Reply latency distribution", _fig_reply_hist(c),
                      sub="how long between turn changes — log-bucketed"),
        _reply_table(c),
        _figure_block("Chain length (messages)", _fig_chain_box(c, "msgs"),
                      sub="log-scale; box = IQR, line = median, marker = mean"),
        _figure_block("Chain length (words)", _fig_chain_box(c, "words"),
                      sub="text only; non-text msgs count as 0 words"),
        '</div>',
        '<div class="grid cols-2">',
        _chain_table(c),
        _media_table(c),
        '</div>',
        '<div class="grid cols-2">',
        _figure_block("Top emojis", _fig_top_emojis(c),
                      sub="tags unified with their Unicode equivalent (e.g. [Facepalm] = 🤦)"),
        _figure_block("Top words", _fig_top_words(c),
                      sub="3+ chars, stop-words filtered"),
        '</div>',
        '</section>',
    ]
    return "\n".join(parts)


def _render_overview(data: list[tuple[ContactRecord, Counts]], my_label: str) -> str:
    n = len(data)
    total_msgs = sum(c.total for _, c in data)
    total_me = sum(sum(v for k, v in c.by_sender.items() if k.lower() == my_label.lower()) for _, c in data)
    total_words_me = sum(c.my_total_words for _, c in data)
    total_words_them = sum(c.their_total_words for _, c in data)
    most_active = max(data, key=lambda x: x[1].total) if data else None

    kpis = "".join([
        _kpi("Contacts", f"{n}", "in this report"),
        _kpi("Total messages", f"{total_msgs:,}", "across all contacts"),
        _kpi("Sent by you", f"{total_me:,}",
             f"{100*total_me/max(total_msgs,1):.0f}% of all msgs", _PAL["me"]),
        _kpi("Your words", f"{total_words_me:,}", "across all chats", _PAL["me"]),
        _kpi("Their words", f"{total_words_them:,}", "across all chats", _PAL["them"]),
        _kpi("Most active",
             most_active[0].display_name if most_active else "—",
             f"{most_active[1].total:,} msgs" if most_active else "",
             _PAL["warn"]),
    ])

    parts = [
        '<section id="overview">',
        '<h2>Overview</h2>',
        '<div class="section-sub">Cross-contact comparison and aggregates</div>',
        f'<div class="kpis">{kpis}</div>',
        _figure_block("Messages per contact (stacked by sender)",
                      _fig_contact_totals(data), full=True),
        _figure_block("Activity over time (contact × month)",
                      _fig_contact_month_heat(data), full=True),
        _figure_block("Median reply time per contact",
                      _fig_reply_medians(data), full=True,
                      sub="log scale — lower = faster"),
        '</section>',
    ]
    return "\n".join(parts)


# ── CSS + HTML template ─────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; }
html, body {
  margin: 0;
  padding: 0;
  background: #0d1117;
  color: #e6edf3;
  font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI",
               Inter, sans-serif;
  font-size: 14px;
  line-height: 1.5;
}
.layout {
  display: grid;
  grid-template-columns: 240px 1fr;
  min-height: 100vh;
}
.nav {
  background: #161b22;
  border-right: 1px solid #30363d;
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
  padding: 20px 0;
}
.nav-header {
  padding: 0 20px 16px;
  border-bottom: 1px solid #30363d;
  margin-bottom: 12px;
}
.nav-header h1 {
  margin: 0;
  font-size: 17px;
  color: #58a6ff;
  font-weight: 700;
  letter-spacing: -0.3px;
}
.nav-header .meta {
  color: #8b949e;
  font-size: 11px;
  margin-top: 4px;
  line-height: 1.4;
}
.nav a {
  display: block;
  padding: 7px 20px;
  color: #c9d1d9;
  text-decoration: none;
  border-left: 2px solid transparent;
  font-size: 13px;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}
.nav a:hover {
  background: rgba(56,139,253,0.08);
  border-left-color: #58a6ff;
  color: #fff;
}
.nav a.active {
  background: rgba(56,139,253,0.12);
  border-left-color: #58a6ff;
  color: #58a6ff;
  font-weight: 600;
}
.nav a .count {
  color: #6e7681;
  font-size: 11px;
  margin-left: 6px;
}
.main {
  padding: 32px 40px;
  max-width: 1500px;
  width: 100%;
}
section {
  margin-bottom: 56px;
  scroll-margin-top: 16px;
}
section h2 {
  font-size: 24px;
  color: #58a6ff;
  margin: 0 0 6px;
  font-weight: 700;
  letter-spacing: -0.4px;
}
section .section-sub {
  color: #8b949e;
  font-size: 13px;
  margin-bottom: 24px;
}
.kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}
.kpi {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 12px 16px;
}
.kpi-label {
  color: #8b949e;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  font-weight: 600;
}
.kpi-value {
  font-size: 26px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  margin-top: 4px;
  line-height: 1.1;
  letter-spacing: -0.5px;
}
.kpi-sub {
  color: #6e7681;
  font-size: 11px;
  margin-top: 4px;
  font-variant-numeric: tabular-nums;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(440px, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}
.grid.cols-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
@media (max-width: 1100px) {
  .grid.cols-2 { grid-template-columns: 1fr; }
}
.card {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 16px 18px;
  min-width: 0;
}
.card.full { grid-column: 1 / -1; }
.card-title {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.1px;
}
.card-sub {
  font-size: 12px;
  color: #8b949e;
  margin-top: 2px;
  margin-bottom: 8px;
}
.figure {
  width: 100%;
  min-height: 240px;
}
table.data {
  width: 100%;
  border-collapse: collapse;
  font-variant-numeric: tabular-nums;
  font-size: 13px;
  margin-top: 8px;
}
table.data th, table.data td {
  padding: 7px 10px;
  text-align: left;
  border-bottom: 1px solid #21262d;
}
table.data th {
  color: #8b949e;
  font-weight: 600;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.6px;
}
table.data td.num, table.data th.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.me { color: #22d3ee; }
.them { color: #ec4899; }
.tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 12px;
  background: rgba(88,166,255,0.15);
  color: #58a6ff;
  font-size: 11px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  margin-right: 6px;
}
.tag.src-wechat   { background: rgba(34,211,238,0.15);  color: #22d3ee; }
.tag.src-whatsapp { background: rgba(63,185,80,0.15);   color: #3fb950; }
.tag.src-combined { background: rgba(210,153,34,0.15);  color: #d29922; }
.callout {
  background: rgba(210,153,34,0.08);
  border: 1px solid rgba(210,153,34,0.4);
  border-radius: 8px;
  padding: 12px 16px;
  color: #d29922;
  margin: 8px 0 24px;
  font-size: 13px;
}
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: #0d1117; }
::-webkit-scrollbar-thumb { background: #30363d; border-radius: 5px; }
::-webkit-scrollbar-thumb:hover { background: #484f58; }
@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; }
  .nav { position: relative; height: auto; max-height: 240px; }
  .main { padding: 16px; }
}
"""

_JS = """
// Lazy-render Plotly figures when scrolled into view — keeps the
// initial paint fast even with dozens of charts in the page.
const config = %CONFIG%;
const seen = new Set();
function renderFig(el) {
  if (seen.has(el.id)) return;
  seen.add(el.id);
  const specEl = document.getElementById(el.id + '-spec');
  if (!specEl) return;
  try {
    const spec = JSON.parse(specEl.textContent);
    Plotly.newPlot(el.id, spec.data, spec.layout, config);
  } catch (e) { console.error('fig', el.id, e); }
}
const io = new IntersectionObserver((entries) => {
  for (const e of entries) if (e.isIntersecting) renderFig(e.target);
}, { rootMargin: '300px' });
document.querySelectorAll('.figure').forEach(el => io.observe(el));

// Sticky-nav active-section highlighter
const navLinks = document.querySelectorAll('.nav a[href^="#"]');
const sections = Array.from(document.querySelectorAll('section[id]'));
function onScroll() {
  let cur = sections[0]?.id;
  for (const s of sections) {
    if (s.getBoundingClientRect().top - 100 <= 0) cur = s.id;
  }
  navLinks.forEach(a => a.classList.toggle(
    'active', a.getAttribute('href') === '#' + cur));
}
window.addEventListener('scroll', onScroll, { passive: true });
onScroll();
"""


_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<script src="{plotly}"></script>
<style>{css}</style>
</head>
<body>
<div class="layout">
  <aside class="nav">
    <div class="nav-header">
      <h1>wxextract</h1>
      <div class="meta">Conversation report<br>{generated}</div>
    </div>
    <nav>{nav}</nav>
  </aside>
  <main class="main">{body}</main>
</div>
<script>{js}</script>
</body>
</html>
"""


# ── public API ──────────────────────────────────────────────────────

def render_report(
    contact_data: list[tuple[ContactRecord, Counts]],
    *,
    my_label: str = "Me",
    out_path: Path,
    title: str | None = None,
    overview_data: list[tuple[ContactRecord, Counts]] | None = None,
) -> int:
    """Render an HTML report. Returns the number of contacts written.

    `overview_data` lets the caller control which pairs feed the
    overview aggregates (KPIs, comparison charts). Useful when
    `contact_data` contains merged + per-source duplicates of the same
    real-world contact (a "combined" view plus its component sources)
    — passing only the combined + unmerged entries via `overview_data`
    keeps the overview totals from double-counting.
    """
    if not contact_data:
        return 0
    _FIG_COUNTER[0] = 0   # reset id counter per render
    # Caller controls ordering — the multi-source CLI flow uses this to
    # interleave merged + per-source sections in a specific order.
    is_multi = len(contact_data) > 1
    ov_data = overview_data if overview_data is not None else contact_data
    nav_items: list[str] = []
    sections: list[str] = []
    if is_multi:
        nav_items.append('<a href="#overview">Overview</a>')
        sections.append(_render_overview(ov_data, my_label))
    for r, c in contact_data:
        sid = _slug(r.alias or r.username)
        nav_items.append(
            f'<a href="#{sid}">{html.escape(r.display_name)}'
            f'<span class="count">{c.total:,}</span></a>'
        )
        sections.append(_render_contact(r, c, my_label))

    auto_title = ("wxextract — conversation report" if is_multi
                  else f"wxextract — {contact_data[0][0].display_name}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_TEMPLATE.format(
        title=html.escape(title or auto_title),
        plotly=_PLOTLY_CDN,
        css=_CSS,
        js=_JS.replace("%CONFIG%", json.dumps(_config())),
        generated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        nav="\n".join(nav_items),
        body="\n".join(sections),
    ), encoding="utf-8")
    return len(contact_data)


def render_report_from_contacts(
    recs: list[ContactRecord],
    *,
    my_wxid: str,
    my_label: str = "Me",
    top_n: int = 12,
    min_messages: int = 200,
    aliases: list[str] | None = None,
    out_path: Path,
    log=None,
) -> int:
    """Compute Counts for each requested contact and render the report.

    If `aliases` is given, restrict to those contacts (no min_messages filter).
    Otherwise include every contact with ≥min_messages.
    """
    from wxextract.contacts import find_by_alias
    from wxextract.messages import extract
    from wxextract.stats import compute

    def _log(msg: str):
        if log is None:
            return
        if callable(log):
            log(msg)
        elif hasattr(log, "info"):
            log.info(msg)

    if aliases:
        targets = []
        for a in aliases:
            r = find_by_alias(recs, a)
            if r is not None:
                targets.append(r)
            else:
                _log(f"[stats] alias {a!r} not found, skipping")
    else:
        targets = [r for r in recs
                   if (r.message_count or 0) >= min_messages]
        targets.sort(key=lambda r: -(r.message_count or 0))

    if not targets:
        return 0

    data: list[tuple[ContactRecord, Counts]] = []
    for r in targets:
        _log(f"[stats] {r.display_name} ({r.alias or r.username})…")
        try:
            msgs = list(extract(r, my_wxid=my_wxid, skip_recalls=True))
        except Exception as e:
            _log(f"[stats]   skipped ({e!s})")
            continue
        if not msgs:
            continue
        c = compute(msgs, r, my_label=my_label, top_n=top_n)
        if c.total == 0:
            continue
        data.append((r, c))
        _log(f"[stats]   ok ({c.total:,} msgs)")

    if not data:
        return 0
    return render_report(data, my_label=my_label, out_path=out_path)
