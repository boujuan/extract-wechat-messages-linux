# wxextract

**Extract WeChat 4.x conversations on Linux into compact LLM-ready text.**

Works with the AUR `wechat-bin` build (`/opt/wechat/wechat`, version 4.1.x).
Produces three output formats:

| Format       | Best for                              | Token density                             |
|--------------|---------------------------------------|-------------------------------------------|
| `txt-b`      | Direct LLM context (Claude, GPT, etc.) | **~50% smaller than naive plain text**    |
| `pseudo-xml` | Structured Claude prompts             | ~16% smaller, well-formed XML             |
| `jsonl`      | RAG ingestion, search, analytics      | Full fidelity (per-message records)       |

## Quickstart

```sh
git clone https://github.com/boujuan/extract-wechat-messages-linux
cd extract-wechat-messages-linux
uv sync                                                  # one-shot setup
uv run wxextract                                         # interactive: auto-discover → snapshot → pick contact → render
uv run wxextract --alias <wechat_id> --format txt-b --chunk month
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
wxextract status            # what's installed, what's cached
wxextract list              # show contacts table (after first run)
wxextract resnap            # close WeChat → fresh snapshot + decrypt → re-open
wxextract render --alias X  # render without re-snapshotting
wxextract run               # everything end-to-end (default)
```

## Flags worth knowing

```
--format txt-b,jsonl,xml         # comma list; default is all three
--chunk none|month|week|day|tokens:N
--alias <wechat_id>              # skip interactive picker
--gap SECONDS                    # silence gap that starts a new session (default 7200 = 2h)
--no-turn-merge                  # one line per message instead of joining with `;`
--squash-emoji                   # [Tag][Tag][Tag] → [Tag×3]
--redact                         # mask emails / phones / IBANs / long digit runs
--my-label "Me"                  # how to label your own messages
--include-recalls                # keep "X recalled a message" notifications (default: drop)
--force                          # bypass all caches; re-extract keys, re-decrypt
--no-relaunch                    # don't auto-launch WeChat after snapshot
--workspace PATH                 # override default workspace location
```

## Style B (compact TXT) at a glance

Synthetic example (no real chats):

```
G:U=Me|A=Alice
META:alice_42|range=2026-01-01..2026-01-15|msgs=312|tokens=~9.4k

=2026-01-01
A 10:29:01 hi there
U :30:10 hey
A :35:40 how was your day?
U 11:00:10 pretty good;[image]

=15:11 +4h
U 15:11:32 you around tonight?
A 15:13:11 yeah for sure
```

- `G:` glossary maps short letters (`U`, `A`, `B`, …) to display names.
- `META:` carries the contact alias, date range, message count, token estimate.
- `=YYYY-MM-DD` starts a new day; `=HH:MM +Nh` marks a mid-day silence gap.
- Times shrink as the session goes on (`:35:40` = same hour as previous,
  `:40` = same minute).
- Same-sender messages within 60 s are joined by `;`.
- Quoted replies inline as `[↩U 22:48 "preview"]`.

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

Everything lands under `<project-root>/workspace/` by default:

```
workspace/
├── all_keys.json   (chmod 600 — the recovered SQLCipher keys; treat as sensitive)
├── snapshot/       (rsync mirror of the encrypted DB tree)
├── plain_dbs/      (decrypted SQLite, queryable with any client)
└── output/         (your rendered conversations)
```

Override with `--workspace PATH` or `WXE_WORKSPACE=…`.

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
