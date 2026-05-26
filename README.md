# wxextract

**Extract WeChat 4.x conversations on Linux into compact LLM-ready text.**

Works with the AUR `wechat-bin` build (`/opt/wechat/wechat`, version 4.1.x).
Produces three output formats:

| Format       | Best for                              | Token density                             |
|--------------|---------------------------------------|-------------------------------------------|
| `txt-b`      | Direct LLM context (Claude, GPT, etc.) | **~50% smaller than naive plain text**    |
| `pseudo-xml` | Structured Claude prompts             | ~16% smaller, well-formed XML             |
| `jsonl`      | RAG ingestion, search, analytics      | Full fidelity (per-message records)       |

## Install

**One command** (recommended — installs globally, isolated venv, on PATH):

```sh
uv tool install git+https://github.com/boujuan/extract-wechat-messages-linux
```

That's it. `wxextract` is now at `~/.local/bin/wxextract` (already on PATH for
most shells). Verify:

```sh
wxextract --version       # → wxextract 0.1.0
wxextract --help          # colorized help with grouped flags + examples
wxextract status          # what's discovered + where the workspace lives
```

### Updating

`uv tool` does **not** auto-update. To pull the latest from GitHub:

```sh
uv tool upgrade wxextract
```

If you have a local checkout you want to install instead of the git
URL, use `uv tool install --force /path/to/clone` (a re-install snapshot
of the current code; not auto-tracking).

### Uninstall

```sh
uv tool uninstall wxextract
```

### Alternative installers

```sh
pipx install git+https://github.com/boujuan/extract-wechat-messages-linux       # pipx
pip install --user git+https://github.com/boujuan/extract-wechat-messages-linux # pip
```

### For development (editable install + tests)

```sh
git clone https://github.com/boujuan/extract-wechat-messages-linux
cd extract-wechat-messages-linux
uv sync                                          # creates .venv, installs editable + dev deps
uv run pytest                                    # run tests (28 always pass + 28 opt-in)
uv run wxextract --help
```

## Quickstart

```sh
wxextract                                        # interactive: auto-discover → snapshot → pick contact → render
wxextract --alias <wechat_id> --format txt-b --chunk month
```

(`--alias`, `--format`, etc. work with or without the explicit `run`
subcommand — `wxextract --alias X` is equivalent to `wxextract run --alias X`.)

The first run will:
1. Discover your WeChat install + data dir.
2. Extract per-DB SQLCipher keys from the running WeChat process (~0.3 s).
3. Cleanly close WeChat, snapshot the encrypted databases, decrypt in parallel.
4. Re-launch WeChat so you can keep using it. WeChat shows a one-click
   "Open WeChat" confirmation dialog after relaunch — click it to resume.
5. Show a sortable contact picker (or skip via `--alias`).
6. Render the chosen conversation in every requested format.

## Caching / incremental behavior

| What | Check | Effect |
|---|---|---|
| Snapshot freshness | `message_0..3.db` + `contact.db` mtime+size match live | Skip close / snap / decrypt / relaunch entirely (~1 s total run) |
| Key cache | `workspace/all_keys.json` exists AND every saved key validates page-1 HMAC against the current live DBs | Skip the memory scan |
| Decrypt | per-DB: skip if `plain_dbs/X.db` is newer than the source | Re-decrypt only changed DBs |
| Snapshot | `rsync -aH --delete` | Only changed files copied |

Second+ runs with no new messages: **~1 second, never touches WeChat, no dialog.**

When real changes exist (e.g. someone sent you a new message), the tool
detects this from `message_*.db` mtime change and does a full resnap, which
closes WeChat for ~2 s and re-launches it (one click on the dialog to
resume).

Use `--force` to bypass all caches, or `--no-relaunch` to skip the auto-launch
(avoiding the dialog — start WeChat yourself when ready).

## Subcommands

```
wxextract status                              # what's installed, what's cached
wxextract list                                # contacts table (after first run)
wxextract resnap                              # close WeChat → fresh snapshot + decrypt → re-open
wxextract render --alias X                    # render without re-snapshotting
wxextract preview --alias X --tail 20         # print last N messages, no files written
wxextract stats   --alias X                   # per-contact analytics panel (terminal)
wxextract stats   --alias X --html            # interactive HTML report for one contact
wxextract stats                               # HTML report across all contacts (≥200 msgs)
wxextract stats --whatsapp-json raquel.json --whatsapp-merge "Raquel=rachel_97213" \
                --out ~/Documents/Raquel/Report/        # combined WeChat+WhatsApp report
wxextract images  --alias X                   # decrypt .dat image attachments
wxextract run                                 # everything end-to-end (default)
```

## Flags worth knowing

```
--format txt-b,jsonl,xml         # comma list; default is all three
--chunk none|month|week|day|tokens:N
--alias <wechat_id>              # skip interactive picker
--gap SECONDS                    # silence gap that starts a new session (default 7200 = 2h)
--no-turn-merge                  # one line per message instead of joining with `;`
--squash-emoji                   # [Tag][Tag][Tag] → [Tag×3]
--sticker-emojis                 # [Chuckle] → 😄, [Facepalm] → 🤦, etc.
--time-precision seconds|minutes # timestamp granularity in TXT-B (default: seconds)
--reply-preview full|short|none  # quote rendering: full content, sender+time, or sender only
--redact                         # mask emails / phones / IBANs / long digit runs
--my-label "Me"                  # how to label your own messages
--include-recalls                # keep "X recalled a message" notifications (default: drop)
--force                          # bypass all caches; re-extract keys, re-decrypt
--no-relaunch                    # don't auto-launch WeChat after snapshot
--out-dir PATH                   # override just the output directory (rendered files)
--workspace PATH                 # override default workspace location
--account-dir PATH               # explicit WeChat account folder (for multi-account systems)
--all-contacts --min-messages N  # batch-extract every contact (≥ N msgs)
--since-last                     # incremental: emit only new messages since the last run
--stats                          # also print the per-contact analytics panel after the summary
--no-update-check                # skip the once-per-day GitHub releases check
```

### Compression dial

| Combination | Tokens (11.8k-msg sample) | vs naive plain text |
|---|---|---|
| Default (full quotes, seconds) | ~219 k | −51 % |
| `--time-precision minutes` | ~207 k | −54 % |
| `+ --reply-preview short` | ~192 k | −57 % |
| `+ --reply-preview none --sticker-emojis` | **~167 k** | **−63 %** |

### Install variants supported

| Install | Detection | Status |
|---|---|---|
| AUR `wechat-bin` | `/opt/wechat/wechat` + `/usr/bin/wechat` launcher | ✅ Primary target |
| Flatpak `com.tencent.wechat` | `flatpak info com.tencent.wechat` | ✅ Auto-detected; launched via `flatpak run` |
| Manual install | `which wechat` / `which weixin` | ⚠️ Best-effort |
| Wine WeChat 3.x | — | ❌ Different storage layout |

## Style B (compact TXT) at a glance

Synthetic example (no real chats):

```
META: contact=alice_42 | range=2026-01-01..2026-01-15 | msgs=312 | tokens=~9.4k
GLOSS: U=Me  A=Alice
LEGEND: Chronological 1-on-1 chat. Lines: "<sender> <time> <body>". Time shrinks within a session: H:M:S (first) → :M:S (same hour) → :S (same min). ";" joins same-sender messages within 60s. "[↩X H:M \"…\"]" = reply to sender X with preview. "=YYYY-MM-DD" = new day. "=H:M +Nh" = mid-day gap resume. Media placeholders: [img] [voice Ns] [video Ns] [sticker] [call Nm] [file:name] [link:title] [sys:…]. [Word]-style tags like [Chuckle] are WeChat built-in stickers (mapped to Unicode emoji where possible).

=2026-01-01
A 10:29:01 hi there
U :30:10 hey
A :35:40 how was your day?
U 11:00:10 pretty good;[image]

=15:11 +4h
U 15:11:32 you around tonight?
A 15:13:11 yeah for sure 😄
```

Every output file (and every chunk) opens with the same three header
lines — so each chunk is self-contained as LLM context:

- **`META:`** carries contact / date range / message count / token estimate.
- **`GLOSS:`** maps short identifiers (`U`, `A`, `B`, …) → display names.
- **`LEGEND:`** explains the format conventions in one line, so any LLM
  consuming the file can interpret it without guessing.

Body conventions:

- `=YYYY-MM-DD` starts a new day; `=HH:MM +Nh` marks a mid-day silence gap.
- Times shrink within a session: `H:M:S` (first) → `:M:S` (same hour) → `:S` (same min).
- Same-sender messages within 60 s are joined by `;`.
- Quoted replies inline as `[↩U 22:48 "preview"]` (preview is configurable
  via `--reply-preview {full|short|none}`).
- WeChat stickers (`[Chuckle]`, `[Facepalm]`, ...) map to Unicode emoji
  when `--sticker-emojis` is passed.

## Live progress UI & summary

When you run `wxextract` (or `run` / `render` / `resnap`), the tool shows a
live status panel powered by `rich.Live`, with a spinner on the in-flight
stage and elapsed time per completed stage:

```
╭───────────── wxextract  · 4.2s ─────────────╮
│  ✓  Discover  54 ms     install=aur  …      │
│  ✓  Keys  330 ms        19 cached keys ✓    │
│  ⊘  Snapshot            fresh — no changes  │
│  ⊘  Decrypt             fresh — no changes  │
│  ✓  Contacts  786 ms    🐑Rachel — 11,948 …│
│  ✓  Extract  84 ms      11,903 messages     │
│  ⠹  Render              writing xml…        │
│  ·  Chunk                                   │
╰─────────────────────────────────────────────╯
```

When it finishes, a summary panel prints to stdout with per-file
size / lines / words / tokens / days plus a TOTAL row. Disable with
`--no-progress` / `--no-summary`. Use `-v` for plain log lines instead.

## How it works

```
discover ── locate ~/.local/share/WeChat_Data/xwechat_files/<wxid>/
   │
lifecycle ── while WeChat is running, extract SQLCipher keys from /proc/<pid>/mem
   │
key scan ── regex x'…' candidates, validate each via per-page HMAC-SHA512
   │       (SQLCipher 4: AES-256-CBC, PBKDF2-HMAC-SHA512 256 000 iter)
   │
close ──── SIGTERM, poll for clean exit (≤10 s, fallback prompt)
   │
snapshot ── rsync -aH workspace/snapshot/<account>/
   │
decrypt ── per-page AES-256-CBC, parallel across DBs, → workspace/plain_dbs/
   │
launch ──── re-open WeChat
   │
contacts ── load decrypted contact.db, annotate with per-talker stats
   │
picker ──── rich-powered table; filter by typing
   │
messages ── walk Msg_<md5(username)> table, decompress zstd content
   │       decompose local_type = (subType << 32) | type
   │
render ──── txt-b / jsonl / xml + sessionization + identity normalization
   │
chunk ──── month / week / day / tokens:N (re-renders per bucket, or splits at session edges)
```

## Tests

```sh
uv run pytest                  # unit tests; always pass

# Tests that touch real decrypted databases are opt-in via env var:
WXE_TEST_PLAIN_DBS=/path/to/plain_dbs \
WXE_TEST_ALIAS=<your-contact-alias> \
WXE_TEST_MY_WXID=<your-own-wxid> \
  uv run pytest
```

Coverage: SQLCipher key derivation, byte-for-byte decrypt against a
known-good baseline, message-count parity, JSON/XML well-formedness, TXT-B
compression ratio ≥ 30%, chunker integrity, emoji-squash and redact regexes.

## Output locations

The workspace directory holds everything wxextract produces:

```
workspace/
├── all_keys.json         (chmod 600 — recovered SQLCipher keys; treat as sensitive)
├── snapshot_stats.json   (per-shard row counts; drift detection baseline)
├── snapshot/             (rsync mirror of the encrypted DB tree)
├── plain_dbs/            (decrypted SQLite, queryable with any sqlite3 client)
└── output/               (your rendered conversations — .txt / .xml / .jsonl)
```

The workspace location depends on how wxextract was invoked:

| Situation | Workspace path |
|---|---|
| `--workspace PATH` or `WXE_WORKSPACE=…` | That path |
| Installed via `uv tool` / `pipx` / `pip --user` (recommended) | `~/.local/share/wxextract/` (XDG-compliant via `$XDG_DATA_HOME`) |
| Running from a source checkout via `uv run …` | `<project>/workspace/` |

To send rendered files somewhere outside the workspace (e.g. a synced
folder), use `--out-dir PATH`. That overrides only `output/`; everything
else stays in the workspace.

## Limitations

- Linux only. The key-extraction `/proc/<pid>/mem` path is Linux-specific;
  Windows / macOS support would need different scanners.
- 1-on-1 chats only for now (group chats / bizchat deferred).
- Memory-key extraction needs WeChat to be running and at least one account
  logged in *and past the "Open WeChat" dialog* — keys aren't cached until
  chats actually load. If `/proc/<pid>/mem` is blocked (e.g. tightened
  `kernel.yama.ptrace_scope`), fall back to `sudo`.
- Token counts use `tiktoken cl100k_base` — a good ±5 % proxy for Claude
  models, not exact.

## License & credits

See `NOTICE.md`. The SQLCipher key-scan and AES-256-CBC per-page decrypt
algorithms were re-implemented from the SQLCipher 4 spec and from observable
WeChat behavior, informed by the public `L1en2407/wechat-decrypt` reference.
