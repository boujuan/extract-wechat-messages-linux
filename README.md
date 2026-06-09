# wxextract

**Extract WeChat 4.x conversations on Linux into compact LLM-ready text,
with an interactive HTML analytics report.** Optional WhatsApp and
Instagram ingest let you analyse multiple channels side-by-side for the
same person.

Works with the AUR `wechat-bin` build (`/opt/wechat/wechat`, version 4.1.x).
Four output formats plus a native interactive HTML report:

| Format       | Best for                              | Token density vs naive plain text |
|--------------|---------------------------------------|-----------------------------------|
| `txt-b`      | Direct LLM context (Claude, GPT, etc.) | **~50–63 % smaller**              |
| `pseudo-xml` | Structured prompts (cited references)  | Larger (well-formed XML tags)     |
| `md`         | Obsidian / human reading              | YAML frontmatter + day headers    |
| `jsonl`      | RAG ingestion, search, analytics      | Full fidelity (per-message records) |
| **HTML report** | Interactive analytics dashboard    | Plotly charts, KPIs, sticky nav   |

## Install

**One command** (recommended — installs globally, isolated venv, on PATH):

```sh
uv tool install git+https://github.com/boujuan/extract-wechat-messages-linux
```

That's it. `wxextract` is now at `~/.local/bin/wxextract` (already on PATH for
most shells). Verify:

```sh
wxextract --version       # → wxextract 0.7.0
wxextract --help          # colorized help with grouped flags + examples
wxextract status          # what's discovered + where the workspace lives
```

### Updating

`uv tool` does **not** auto-update. To pull the latest from GitHub:

```sh
uv tool upgrade wxextract
```

If you have a local checkout you want to install instead of the git
URL, use `uv tool install --force /path/to/clone`.

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
uv run pytest                                    # unit tests
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
4. Re-launch WeChat so you can keep using it.
5. Show a sortable contact picker (or skip via `--alias`).
6. Render the chosen conversation in every requested format.

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
wxextract images  --alias X                   # decrypt .dat image attachments
wxextract instagram list --export dump.zip    # list Instagram DM threads in an export
wxextract instagram fetch --export dump.zip --thread X   # → wxextract-instagram JSON
wxextract cleanup --all                       # wipe workspace (snapshot/decrypted DBs/output/keys/cache)
wxextract run                                 # everything end-to-end (default)
```

## HTML report (interactive dashboard)

`wxextract stats` produces a native interactive HTML report with Plotly
charts, KPI cards, sticky navigation, and lazy-rendered figures so it
paints instantly even with dozens of charts:

- Per-contact KPIs (total / your-share / their-share / median replies / longest silence / words)
- Daily timeline with rangeslider + 7-day rolling average + **Messages / Words toggle**
- Cumulative messages area chart
- Weekday × hour activity heatmaps (combined + per-sender)
- Hour-of-day + weekday grouped bars (per sender)
- Monthly volume + stacked message-type bars
- Reply-latency log-bucket histograms + percentile table
- **Reply latency over time** (14-day rolling, log y-axis)
- **Burst size over time** (14-day rolling chain length)
- Chain box plots (messages & words, log y-axis)
- Top emojis + top words horizontal bars
- Cross-contact Overview: bar chart, contact × month heatmap, median-reply log bars

```sh
# One contact
wxextract stats --alias alice_wxid --html --out ~/Documents/chats/alice/

# All contacts ≥ 200 msgs
wxextract stats --out ~/Documents/chats/all/
```

If `--out` doesn't end with `.html` it's treated as a directory and
`report.html` is written inside. `--open` launches the file in your
default browser when done.

## WhatsApp integration (optional)

`wxextract` can fold WhatsApp conversations into the same report —
useful when the same person uses both channels.

Parse the WhatsApp `.txt` export first using
[Parse_Whatsapp_LLM](https://github.com/boujuan/Parse_Whatsapp_LLM)
(its `--format wxextract` emitter produces a JSON file in the schema
wxextract expects):

```sh
python3 ~/Coding/Parse_Whatsapp_LLM/whatsapp_llm_optimizer.py \
    --format wxextract --me "Your Name" \
    -o ~/Documents/chats/whatsapp/ \
    "~/Downloads/WhatsApp Chat with Alice.txt"
```

Then either render it through the same TXT-B / XML / MD / JSONL
pipeline:

```sh
wxextract render --whatsapp-only \
    --whatsapp-json ~/Documents/chats/whatsapp/whatsapp_chat_with_alice.wxextract.json \
    --format txt-b,xml,md --chunk month \
    --out-dir ~/Documents/chats/whatsapp/
```

…or include it in the HTML report (with `--whatsapp-merge` to declare
"this WhatsApp contact and this WeChat alias are the same person" —
produces three sections: a combined view, then each source on its own):

```sh
wxextract stats \
    --alias alice_wxid \
    --whatsapp-json ~/Documents/chats/whatsapp/whatsapp_chat_with_alice.wxextract.json \
    --whatsapp-merge "Alice=alice_wxid" \
    --out ~/Documents/chats/combined/
```

**Limitations** (from WhatsApp `.txt` format): no reply targets, no
message IDs, all attachments collapse to one `media` bucket, minute
precision only, edits/deletes lossy, 1-on-1 only.

## Instagram integration (optional)

`wxextract instagram` acquires an Instagram DM thread and normalizes it
into a `wxextract-instagram/1` JSON that renders through the same
TXT-B / XML / MD / JSONL pipeline. Three acquisition routes, auto-detected:

| Route | Flag | Notes |
|---|---|---|
| Official export | `--export PATH` | Settings → *Download Your Information* (JSON, Messages). A `.zip` or extracted folder. **Safe / ToS-clean.** |
| Live private API | `--thread <id>` | Paginates `direct_v2/threads` with your browser session cookie. Fast, but **against Instagram's ToS** — small account-action risk. |
| Browser console | `--dump PATH` | A JSON saved by `wxextract instagram snippet` (a browser-agnostic console/bookmarklet dumper). |

```sh
# 1. list the threads in an export (slug · message count · display name)
wxextract instagram list --export ~/Downloads/instagram-export.zip

# 2. fetch one thread by slug substring → <workspace>/output/<slug>.json
#    ("me" auto-derives as the participant that isn't the thread title)
wxextract instagram fetch --export ~/Downloads/instagram-export.zip --thread alice

# 3. render it like any other source
wxextract render --instagram-only \
    --instagram-json ~/.local/share/wxextract/output/alice.json \
    --format txt-b,xml,md,jsonl --chunk month
```

Live route (Zen / Firefox / Chrome / Brave / Edge cookie auto-detected). The
selector is resolved via your inbox, so it can be the id from the
`instagram.com/direct/t/<id>` URL, a participant name, or the export id.
Add `--render` to fetch **and** render in one step:

```sh
wxextract instagram fetch --thread alice --cookies-from-browser zen
wxextract instagram fetch --thread alice --cookies-from-browser zen \
    --render --format txt-b,md --chunk month --out-dir ~/chats/instagram
```

Type mapping: text → text; photos → `[image]`; videos → `[video]`;
voice notes → `[voice]`; shares/reels/links → `[link: text — url]`;
calls → `[call]`. **Mojibake** in the official export (double-encoded
UTF-8) is repaired automatically.

All three sources render in one command (one set of files per contact), and the
HTML report (`wxextract stats`) accepts `--instagram-json` too:

```sh
wxextract render --alias alice_wxid \
    --whatsapp-json alice.wxextract.json \
    --instagram-json alice_ig.json --chunk month
wxextract stats --instagram-json alice_ig.json --html   # interactive report
```

**Limitations** (v1): 1-on-1 only (group DMs skipped), reactions and
unsent messages dropped, media rendered as placeholders (not downloaded),
the official export carries no @handle (the thread-folder slug is used).
The live/console routes need `requests` + `browser_cookie3` (installed
with the tool).

## Caching / incremental behavior

| What | Check | Effect |
|---|---|---|
| Snapshot freshness | `message_0..3.db` + `contact.db` mtime+size match live | Skip close / snap / decrypt / relaunch entirely (~1 s total run) |
| Key cache | `workspace/all_keys.json` exists AND every saved key validates page-1 HMAC against the current live DBs | Skip the memory scan |
| Decrypt | per-DB: skip if `plain_dbs/X.db` is newer than the source | Re-decrypt only changed DBs |
| Snapshot | `rsync -aH --delete` | Only changed files copied |

Second+ runs with no new messages: **~1 second, never touches WeChat, no dialog.**

Use `--force` to bypass all caches, or `--no-relaunch` to skip the auto-launch.

## Flags worth knowing

```
--format txt-b,jsonl,xml,md      # comma list; default is txt-b,jsonl,xml
--chunk none|month|week|day|tokens:N
--alias <wechat_id>              # skip interactive picker; comma list works
--all-contacts --min-messages N  # batch-extract every contact (≥ N msgs)
--since-last                     # incremental: only new messages since last run
--gap SECONDS                    # silence gap that starts a new session (default 7200 = 2h)
--no-turn-merge                  # one line per message instead of joining with `;`
--squash-emoji                   # [Tag][Tag][Tag] → [Tag×3]
--sticker-emojis                 # [Chuckle] → 😄, [Facepalm] → 🤦, etc.
--time-precision seconds|minutes # timestamp granularity in TXT-B
--reply-preview full|short|none  # quote rendering verbosity
--redact                         # mask emails / phones / IBANs / long digit runs
--my-label "Me"                  # how to label your own messages
--include-recalls                # keep "X recalled a message" notifications
--force                          # bypass all caches; re-extract keys, re-decrypt
--no-relaunch                    # don't auto-launch WeChat after snapshot
--out-dir PATH                   # override just the output directory
--workspace PATH                 # override default workspace location
--account-dir PATH               # explicit WeChat account folder (multi-account)
--stats                          # also print analytics panel after the summary
--no-update-check                # skip the once-per-day GitHub releases check
--whatsapp-json PATH             # include a WhatsApp JSON (repeatable)
--whatsapp-merge NAME=ALIAS      # same-person mapping (repeatable)
--whatsapp-only                  # skip WeChat data entirely
--instagram-json PATH            # include an Instagram JSON (repeatable)
--instagram-only                 # skip WeChat data entirely
```

### Compression dial

| Combination | Token reduction vs naive plain text |
|---|---|
| Default (full quotes, seconds) | ~−51 % |
| `--time-precision minutes` | ~−54 % |
| `+ --reply-preview short` | ~−57 % |
| `+ --reply-preview none --sticker-emojis` | **~−63 %** |

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
META: contact=alice_wxid | range=2026-01-01..2026-01-15 | msgs=312 | tokens=~9.4k
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
│  ✓  Contacts  786 ms    Alice — 11,948 …    │
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
render ──── txt-b / jsonl / xml / md + sessionization + identity normalization
   │       (same pipeline for WeChat, WhatsApp, and Instagram sources)
   │
chunk ──── month / week / day / tokens:N
   │
report ──── stats compute + Plotly HTML render (interactive dashboard)
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
compression ratio ≥ 30 %, chunker integrity, emoji-squash and redact regexes,
WhatsApp JSON loader + combined-contact merge, Instagram loader + export/API
normalization (mojibake repair, ms/µs timestamps, type mapping, me-derivation).

## Output locations

The workspace directory holds everything wxextract produces:

```
workspace/
├── all_keys.json         (chmod 600 — recovered SQLCipher keys; treat as sensitive)
├── image_key.json        (V2 image AES key, when cached)
├── snapshot_stats.json   (per-shard row counts; drift detection baseline)
├── last_extract.json     (per-contact --since-last baselines)
├── snapshot/             (rsync mirror of the encrypted DB tree)
├── plain_dbs/            (decrypted SQLite, queryable with any sqlite3 client)
├── media/                (decrypted images, if you ran `wxextract images`)
└── output/               (rendered conversations + HTML report)
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

`wxextract cleanup --all` wipes the workspace + XDG cache. Selective:
`--snapshot --plain-dbs --output --media --keys --state --cache`.

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
- WhatsApp ingest is from the standard `.txt` chat export only — no reply
  targets, no message IDs, all attachments collapse to one `media` bucket.

## License & credits

See `NOTICE.md`. The SQLCipher key-scan and AES-256-CBC per-page decrypt
algorithms were re-implemented from the SQLCipher 4 spec and from observable
WeChat behavior, informed by the public `L1en2407/wechat-decrypt` reference.
WhatsApp `.txt` parsing is provided by
[Parse_Whatsapp_LLM](https://github.com/boujuan/Parse_Whatsapp_LLM).
