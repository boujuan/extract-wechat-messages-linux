# Changelog

All notable changes to **wxextract** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.9.0] - 2026-06-09

Cross-source merging into one timeline.

### Added

- **`--merge`** (on `render`, `run`, `stats`) — treat every provided source
  (each `--alias` contact + every `--whatsapp-json` + every `--instagram-json`)
  as the **same person** and additionally emit one combined, chronologically
  interleaved view. `--merge-as NAME` sets the combined display name.
  - `render --merge` writes a combined output set alongside the per-source ones.
  - `stats --merge` adds a combined report section on top of the per-source sections.
- **`wxextract combined`** — one-shot orchestrator: fetch Instagram from a
  Download-Your-Information zip (`--instagram-export ZIP --thread SEL`), render
  all sources to text, and write the merged HTML report (combined + per-source),
  in a single invocation.
- Instagram now has a proper source badge (label + colour) in the HTML report.

### Removed

- **`--whatsapp-merge NAME=ALIAS`** (breaking) — superseded by the simpler,
  source-agnostic `--merge`.

## [0.8.1] - 2026-06-09

### Added

- **`wxextract stats --instagram-json PATH`** (+ `--instagram-only`) — the
  interactive HTML analytics report now includes Instagram conversations
  alongside WeChat and WhatsApp.
- **`wxextract instagram fetch --render`** — fetch and render in one step,
  with `--format` / `--chunk` / `--out-dir` passthrough.

### Changed

- `wxextract --help` now shows an Instagram example, and the README documents
  the combined WeChat + WhatsApp + Instagram render plus stats coverage.
- Sanitized example identifiers in docs, code comments, and tests.

## [0.8.0] - 2026-06-09

Instagram DMs as a first-class source.

### Added

- **`wxextract instagram`** subcommand — acquire Instagram DMs and normalize
  them into a `wxextract-instagram/1` JSON that renders through the same
  `txt-b` / `jsonl` / `xml` / `md` pipeline (and chunking) as WeChat and
  WhatsApp. Three auto-detected routes:
  - `--export` — the official *Download Your Information* export (a `.zip` or
    extracted folder; DMs live under `…/messages/inbox/`). Safe / ToS-clean.
  - `--thread` — the live private API. The selector resolves via your inbox, so
    it can be the id from the `instagram.com/direct/t/<id>` URL, a participant
    name, or the export id. Reads the browser session cookie (Zen/Firefox/
    Chrome/Brave/Edge). Against Instagram's ToS — small account-action risk.
  - `--dump` — a JSON saved by the `wxextract instagram snippet` browser
    console/bookmarklet dumper.
  - `wxextract instagram list --export …` enumerates an export's threads.
- **`wxextract render --instagram-json PATH`** (+ `--instagram-only`), mirroring
  `--whatsapp-json`.
- Export mojibake repair (double-encoded UTF-8 → real UTF-8), ms/µs → second
  timestamp normalization, and Instagram → canonical type mapping
  (text / image / voice / video / share-as-link / call).

### Changed

- Bad flags on a subcommand now print *that subcommand's* usage (with all its
  flags, e.g. `--out-dir`) instead of the bare top-level usage.
- `requests` + `browser-cookie3` added as dependencies (used only by the
  Instagram live route; the AUR package ships them as `optdepends`).

### Fixed

- Single external-source renders (`--instagram-only` / `--whatsapp-only`) now
  show the real contact in the summary header instead of "0 contacts".

## [0.7.1] - 2026-05-27

Packaging fixes for AUR submission.

### Added

- **`LICENSE`** file (MIT) at repo root. The project always declared
  MIT in `pyproject.toml` but the file itself was missing.

### Changed

- **`lxml`** removed from runtime `dependencies` — it's only imported
  by the XML well-formedness assertions in tests. Now lives in
  the `dev` dependency group. Shrinks the install footprint for
  end users.
- **AUR PKGBUILD overhaul**: pkgver bumped 0.1→0.7.1; real sha256
  hash (was `SKIP`); `wechat-bin` and `sqlcipher` moved to
  `optdepends` since the tool can render previously-decrypted data
  without WeChat installed and has a pure-Python AES fallback;
  `python-wheel` added to `makedepends` per the Arch Python
  package guidelines; `LICENSE` installed to
  `/usr/share/licenses/$pkgname/`.

[Unreleased]: https://github.com/boujuan/extract-wechat-messages-linux/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/boujuan/extract-wechat-messages-linux/compare/v0.8.1...v0.9.0
[0.8.1]: https://github.com/boujuan/extract-wechat-messages-linux/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/boujuan/extract-wechat-messages-linux/compare/v0.7.1...v0.8.0
[0.7.1]: https://github.com/boujuan/extract-wechat-messages-linux/releases/tag/v0.7.1

## [0.7.0] - 2026-05-26

`wxextract render` now accepts WhatsApp JSON files — TXT-B,
pseudo-XML, Markdown, and JSONL all use the same renderers and
chunker as the WeChat side.

### Added

- **`wxextract render --whatsapp-json PATH`** (repeatable) — render
  a WhatsApp conversation (JSON produced by
  `Parse_Whatsapp_LLM --format wxextract`) through the existing
  TXT-B / XML / MD / JSONL pipeline, with the same compression
  flags (`--time-precision`, `--sticker-emojis`,
  `--reply-preview`, `--squash-emoji`, `--redact`) and chunking
  options (`--chunk month/week/day/tokens:N`) you already use for
  WeChat. Can be combined with `--alias` to render both sources in
  one run, or use `--whatsapp-only` to skip WeChat data entirely.
- **`TYPE_MEDIA_GENERIC` placeholder** properly handled in
  `render.common.body_of` — WhatsApp `<Media omitted>` now renders
  as `[media]` (consistent with WeChat's `[img]` / `[voice]` /
  `[video]` placeholders) instead of the raw `[type:1001/0]`
  fallback.

### Internal

- `_render_single_contact` gains an optional `preloaded_msgs` arg
  so the WhatsApp path can skip the extract() step and feed the
  loaded messages straight into `_render_and_chunk`.
- `_cmd_render` reshaped to iterate over `(contact, msgs|None)`
  tuples — WeChat contacts get `None` (extract on demand);
  WhatsApp pairs come pre-loaded. Path through the renderers +
  summary is otherwise identical.

[0.7.0]: https://github.com/boujuan/extract-wechat-messages-linux/releases/tag/v0.7.0

## [0.6.0] - 2026-05-26

Trend plots over time + daily timeline metric toggle + overview
double-counting fix.

### Added

- **Reply-latency over time** chart per contact — 14-day rolling
  average of reply latency, plotted on a log-scale y-axis with two
  lines (you / other). Makes drift in responsiveness obvious at a
  glance ("we used to reply in minutes; now we both take hours").
- **Burst-size over time** chart per contact — 14-day rolling average
  of chain length (messages per uninterrupted turn). Higher = more
  monologue-like; lower = more back-and-forth.
- **Daily timeline messages/words toggle** — the top-of-section daily
  bar chart now has a Messages / Words button group (Plotly
  `updatemenus`) that swaps all three traces (yours, other's, 7-day
  avg) between message count and word count without re-rendering.

### Fixed

- **Overview no longer double-counts merged contacts**. When
  `--whatsapp-merge` produces three sections for one person
  (combined + WhatsApp + WeChat), the Overview KPIs and bar/heatmap
  charts now only include the combined entry — totals are unique
  conversations, not 3× the truth. Per-contact sections still show
  their individual numbers.

### Internal

- `Counts` gains timestamped sample arrays for the trend plots
  (`my_reply_times_ts`, `their_reply_times_ts`, `my_chain_sizes_ts`,
  `their_chain_sizes_ts`) plus `daily_words_me` / `daily_words_them`
  for the toggle. Memory cost: O(num_chain_switches) per contact,
  trivial.
- `report.render_report()` gains an optional `overview_data`
  parameter so the caller can pass a deduped subset for the
  cross-contact aggregate when merges are active.

[0.6.0]: https://github.com/boujuan/extract-wechat-messages-linux/releases/tag/v0.6.0

## [0.5.0] - 2026-05-26

Cross-source comparison — WhatsApp conversations alongside WeChat ones
in the same interactive HTML report.

### Added

- **`--whatsapp-json PATH`** on `wxextract stats` (repeatable) —
  include a WhatsApp conversation parsed by Parse_Whatsapp_LLM's new
  `--format wxextract` emitter. Each adds its own section in the
  report with a `WhatsApp` badge.
- **`--whatsapp-merge NAME=ALIAS`** (repeatable) — declare that a
  WhatsApp contact and a WeChat alias are the same real-world person.
  The report then emits three consecutive sections for them: a
  **combined** synthetic view (messages from both sources, sorted by
  timestamp, fed back through `stats.compute`), the WhatsApp-only
  section, and the WeChat-only section.
- **`--whatsapp-only`** — skip WeChat data loading entirely. Useful
  for rendering reports from WhatsApp JSONs alone without a decrypted
  WeChat snapshot present.
- **`--out PATH` accepts a directory** — if PATH doesn't end with
  `.html`, it's treated as an output directory (created if missing)
  and `report.html` is written inside. Lets the user pass
  `--out ~/Documents/chats/Report/` directly.
- **Source badges** in the report — small coloured tags
  (cyan WeChat / green WhatsApp / gold Combined) appear in each
  contact's section subtitle.
- **`src/wxextract/whatsapp.py`** — new module with
  `load_whatsapp_json(path)` and `build_combined(a, b, …)`. The latter
  produces the synthetic merged contact + re-numbered, re-sorted
  message list with `sender_username` normalized so `stats.compute`'s
  chain logic counts both sides correctly.
- **`TYPE_MEDIA_GENERIC = 1001`** in `messages.py` — used by
  WhatsApp's `<Media omitted>` since the txt export can't
  disambiguate image / voice / video / sticker. Stats render it as
  a single `media` bucket.
- **`ContactRecord.source`** field (defaults to `"wechat"`) — drives
  the report badge and is set to `"whatsapp"` / `"combined"` for the
  new code paths.
- **`tests/test_whatsapp.py`** — 5-test smoke suite covering load,
  schema validation, compute over WhatsApp messages, single-source
  HTML render, and merge.

### Limitations (documented in --help and README)

These come from WhatsApp's `.txt` export format itself:

- No reply targets, no message IDs, no read receipts.
- All attachments collapse to a single `media` bucket (image / voice
  / video / sticker are indistinguishable in `.txt`).
- Minute precision only (no seconds).
- Edits are lossy (final text only); deletes are skipped.
- 1-on-1 only — emitter rejects group chats with >2 senders.

### Default behavior unchanged

`wxextract stats` without any WhatsApp flag is identical to v0.4 —
still WeChat-only, still writes to `<workspace>/output/report.html`.

[0.5.0]: https://github.com/boujuan/extract-wechat-messages-linux/releases/tag/v0.5.0

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
  explicit "Me" and the other party's name columns plus a header row
  that maps the bar colors to the participants.
- **Reply-time direction labels** — "Me → Other" was ambiguous (does
  it mean direction or responder?); now reads "Me replies to <other>"
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
