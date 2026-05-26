# Changelog

All notable changes to **wxextract** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-05-26

Native interactive HTML report. The previous Rich-export approach
("a screenshot of the terminal in HTML") is gone — replaced with a
real native report built around Plotly.js, KPI cards, sortable
tables, and a sticky-nav sidebar.

### Added

- **`wxextract/report.py`** — new module that emits a self-contained
  dark-themed HTML report. Each contact section includes:
  - KPI tiles (total / your-share / their-share / median replies /
    longest silence / total words)
  - Daily timeline with a range slider and 7-day rolling-average overlay
  - Cumulative-messages area chart
  - Combined weekday × hour activity heatmap, plus per-sender heatmaps
  - Grouped hour-of-day + weekday bars (per sender)
  - Monthly volume + message-type stacked bars
  - Reply-latency histograms (log-bucketed, you vs other) +
    full percentile table
  - Chain-length box plots (messages and words, log y-axis)
  - Chain dynamics + media count tables
  - Top emojis + top words horizontal bars
- **Multi-contact overview** — when no `--alias` is given, a leading
  Overview section compares contacts: stacked bars of total messages
  by sender, contact × month activity heatmap, and median-reply-time
  bars on a log axis.
- **Single-contact HTML mode** — `wxextract stats --alias X --html`
  (or `--out path.html`) generates the report for one contact.
  Comma-separated aliases work too: `--alias a,b,c --html`.
- **Lazy figure rendering** — Plotly figures only initialize when
  scrolled into view, so a report with dozens of charts still paints
  the first screen instantly.
- **Sticky-nav active-section highlighter** — the sidebar TOC tracks
  scroll position and highlights the current contact.
- **`hour_dow_me` / `hour_dow_them`** added to `Counts` so the
  weekday × hour heatmaps can split by sender.

### Removed

- `stats.render_html_report` — the Rich `export_html()`-based report.
  CLI flow is unchanged for callers; everything routes through the
  new `report.render_report_from_contacts`.

[Unreleased]: https://github.com/boujuan/extract-wechat-messages-linux/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/boujuan/extract-wechat-messages-linux/releases/tag/v0.4.0

## [0.3.0] - 2026-05-26

Stats v3 — deep analytics + comprehensive HTML report.

### Added

- **Std dev for chains & response times** — every distribution panel now
  shows median + mean + std dev so you can read variability, not just
  central tendency.
- **Word counts per sender** — total words and per-chain word stats
  (median / mean / std dev / longest / total), text-only (non-text msgs
  contribute 0).
- **Per-sender hour & weekday breakdown** — "Activity by hour" and
  "Activity by weekday" are now stacked bars with cyan = you, magenta =
  the other person, so you can see who's active when.
- **Per-sender media counts** — new "Message types by sender" table
  splits image / voice / video / sticker / call / appmsg counts between
  the two participants (you can finally see who initiates calls).
- **Reply-time histograms** — log-bucket sparkline panels next to each
  reply-time table (30s · 1m · 2m · 5m · 10m · 30m · 1h · 2h · 6h · 12h
  · 1d · 2d · 1w · >1w) make the response-time *shape* obvious at a
  glance.
- **HTML report** — `wxextract stats` (no `--alias`) now renders a
  comprehensive HTML report across all contacts above `--min-messages`
  (default 200) into `<workspace>/output/report.html`. Includes a
  contact table-of-contents, per-contact anchors, and all panels in
  full Rich-color via `Console.export_html()`. New flags:
  `--min-messages`, `--out`, `--open`.

### Fixed

- **Daily timeline labels** — replaced the ambiguous "25/21" format with
  explicit "Me" and "Rachel" columns plus a header row that maps the
  bar colors to the participants.
- **Reply-time direction labels** — "Me → Rachel" was ambiguous (does
  it mean direction or responder?); now reads "Me replies to Rachel"
  with an explicit subtitle.

[0.3.0]: https://github.com/boujuan/extract-wechat-messages-linux/releases/tag/v0.3.0

## [0.2.0] - 2026-05-26

Stats & polish release. Adds bidirectional analytics, daily timeline,
inline stats during extraction, V2 image-key memory recovery, and several
UX fixes around the picker, the bare-invocation crash, and the GitHub
update notifier.

### Added

- **`--stats` flag on `run` / `render`** — print the per-contact stats
  panel inline after the extraction summary. Saves a separate
  `wxextract stats` call.
- **Bidirectional response-time analysis**: separate panels for
  "Me → Other" and "Other → Me" with p50/p75/p90/p99/max.
- **Daily timeline panel** — per-day stacked bar (yours vs theirs) over
  the conversation's full range; auto-falls back to weekly buckets for
  conversations longer than ~120 days.
- **Unified emoji counts** — `stats.compute()` now runs the same sticker
  → Unicode mapping before counting, so `[Facepalm]` and `🤦` no longer
  show up as separate rows. Opt-out via `unify_emoji_tags=False`.
- **V2 image-key memory recovery (best-effort)** — `wxextract images`
  now auto-scans WeChat processes for the 16-byte AES key, validates
  against ≥5 cross-file test vectors (random `BM` false-positive
  eliminated), and caches to `workspace/image_key.json`.
  `--force-image-key` re-scans; `--image-key HEX|ASCII` overrides
  manually. Falls back gracefully when the key isn't currently cached
  in memory (recommends opening WeChat + viewing a few images).
- **XOR-key derivation** — empirical detection of the V2 XOR byte from
  the JPEG EOI markers in thumbnails (falls back to `0x88`).
- **GitHub release auto-update notice** — once-per-day silent check
  against the releases API; prints a one-line "uv tool upgrade wxextract"
  notice when a newer tag exists. `--no-update-check` /
  `WXE_NO_UPDATE_CHECK=1` to disable. New `Updating` section in `--help`.

### Fixed

- **AttributeError on bare invocation** (`wxextract`, `wxextract -v`,
  `wxextract --workspace …`). The auto-injection helper now appends
  `run` when no subcommand was provided.
- **Picker freeze + lost Ctrl+C** when verbose logging scrolled the
  prompt off-screen. Added a `rich.rule()` banner immediately before
  the prompt, `try/except KeyboardInterrupt + EOFError` in `pick()`,
  and a top-level Ctrl+C catch in `main()` that returns exit code 130.
- **Stats `daily_counts` field** now exposed for downstream tooling.

### Documentation

- README updated with all v0.2 flags and the "Updating" section.
- CHANGELOG follows Keep-a-Changelog format with full
  Added / Fixed / Documentation sections per release.

[0.2.0]: https://github.com/boujuan/extract-wechat-messages-linux/releases/tag/v0.2.0

## [0.1.0] - 2026-05-26

First public release.

### Added

- **End-to-end pipeline** for WeChat 4.x on Linux: discover install →
  recover SQLCipher keys from memory → close WeChat → snapshot →
  decrypt → render conversations.
- **Native SQLCipher 4 key recovery** via `/proc/<pid>/mem` scan + per-page
  HMAC validation. Recovers all 19 per-DB keys in ~0.3 s on the reference
  system.
- **Two decrypt engines**:
  - `sqlcipher` CLI via `sqlcipher_export()` — default when available;
    correctly replays WAL frames.
  - Pure-Python AES-256-CBC fallback.
- **Three output formats**:
  - `txt-b` — ultra-compact for LLM context (~50 % smaller than naïve plain
    text by default; up to 63 % with `--reply-preview none --time-precision
    minutes --sticker-emojis`).
  - `xml` — well-formed pseudo-XML with `<conversation>` / `<session>` /
    `<m>` tags, ideal for structured Claude prompts.
  - `jsonl` — full-fidelity per-message records for RAG / search / analytics.
  - `md` — Obsidian-friendly Markdown with YAML frontmatter, H2 day
    headers, blockquoted reply context.
- **Token-aware chunking**: `--chunk month | week | day | tokens:N`.
- **Compression flags**: `--sticker-emojis` (120+ WeChat tags → Unicode),
  `--squash-emoji`, `--time-precision`, `--reply-preview`, `--redact`.
- **Multi-contact selection**: `--alias a,b,c` (comma list) and
  `--all-contacts [--min-messages N]`.
- **Incremental mode** (`--since-last`): per-contact baseline in
  `workspace/last_extract.json`; subsequent runs emit only newer messages
  with a `_inc_<ts>` filename suffix.
- **Content-level drift detection**: after the mtime fast-path passes,
  the tool also runs a sqlcipher `COUNT(*)` query against live message
  shards and compares to a saved baseline before declaring "no changes".
- **Live progress UI** (`rich.Live`): 8-stage panel with spinners /
  check-marks / skip-glyphs and per-stage elapsed times.
- **Final summary panel**: per-file size / lines / words / tokens / days,
  bold TOTAL row, range header.
- **Subcommands**: `status`, `list`, `resnap`, `preview`, `run`, `render`,
  `stats`, `images`.
- **`stats` subcommand**: per-contact analytics with type distribution,
  monthly volume bars, hourly heatmap, weekday distribution, top
  emoji/words, response-time percentiles, longest silence.
- **`images` subcommand**: walks the WeChat attachment tree and decrypts
  V1 (legacy single-byte XOR) and V2 (AES-128-ECB + XOR; needs
  `--image-key`) image `.dat` files.
- **Colourized CLI help** via `rich-argparse`: grouped flag sections,
  inline defaults, copy-paste examples in the epilog.
- **Lifecycle automation**: `--no-relaunch`, auto-launch when WeChat is
  closed, 3-try retry loop when 0/N keys recovered (the "Open WeChat"
  login-dialog case).
- **Multi-install support**: AUR `wechat-bin`, Flatpak `com.tencent.wechat`,
  manual installs. `--account-dir` for systems with multiple logged-in
  accounts.
- **XDG-compliant workspace**: `~/.local/share/wxextract/` when installed
  globally, `<project>/workspace/` when running from a source checkout.
- **GitHub Actions CI**: ruff lint + pytest on Python 3.12 and 3.13;
  builds wheel + sdist artifacts.
- **GitHub Actions release**: tag-driven; builds dist artifacts and
  creates a GitHub release with auto-generated notes.
- **Tests**: 60+ unit + integration tests. Integration tests opt-in via
  `WXE_TEST_*` env vars so the unit suite always passes in CI.

### Documentation

- `README.md` covering install (uv tool / pipx / pip), update flow,
  caching behavior, all flags, the compression dial, install variants,
  Style B example, live UI walkthrough, output-location matrix, tests.
- `NOTICE.md` crediting the SQLCipher 4 spec + `L1en2407/wechat-decrypt`
  as the reference implementation.

[0.1.0]: https://github.com/boujuan/extract-wechat-messages-linux/releases/tag/v0.1.0
