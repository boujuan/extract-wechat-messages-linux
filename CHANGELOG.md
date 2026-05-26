# Changelog

All notable changes to **wxextract** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/boujuan/extract-wechat-messages-linux/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/boujuan/extract-wechat-messages-linux/releases/tag/v0.1.0
