"""wxextract CLI entry point.

End-to-end pipeline:
    discover → key-extract (live WeChat) → close → snapshot → decrypt →
    re-open → contacts → picker → messages → render → [chunk]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from rich.console import Console

from wxextract import __version__
from wxextract.util import default_workspace, human_bytes, setup_logging


def _make_formatter():
    """rich-argparse formatter with a couple of style tweaks.

    `RawDescriptionRichHelpFormatter` keeps newlines in description/epilog
    (so our examples render line-by-line) while still wrapping arg help text.
    """
    from rich_argparse import RawDescriptionRichHelpFormatter as Fmt
    Fmt.styles["argparse.prog"] = "bold cyan"
    Fmt.styles["argparse.groups"] = "bold yellow"
    Fmt.styles["argparse.args"] = "bold cyan"
    Fmt.styles["argparse.metavar"] = "dim cyan"
    Fmt.styles["argparse.help"] = "default"
    Fmt.styles["argparse.text"] = "default"
    Fmt.styles["argparse.syntax"] = "bold"
    return Fmt


_EPILOG = (
    "[bold yellow]Examples[/]\n"
    "  [cyan]wxextract[/]                                              "
    "[dim]interactive picker, all formats[/]\n"
    "  [cyan]wxextract --alias rachel_97213[/]                         "
    "[dim]extract one contact[/]\n"
    "  [cyan]wxextract --alias X --chunk month --format txt-b[/]       "
    "[dim]monthly chunks, single format[/]\n"
    "  [cyan]wxextract --alias X --sticker-emojis --reply-preview short[/]  "
    "[dim]most compact[/]\n"
    "  [cyan]wxextract preview --alias X --tail 30[/]                  "
    "[dim]peek at last 30 msgs[/]\n"
    "  [cyan]wxextract list[/]                                          "
    "[dim]contacts table[/]\n"
    "  [cyan]wxextract status[/]                                        "
    "[dim]install info + cache state[/]\n"
    "  [cyan]wxextract stats --alias X[/]                               "
    "[dim]per-contact analytics[/]\n"
    "  [cyan]wxextract images --alias X[/]                              "
    "[dim]decrypt .dat image attachments[/]\n"
    "  [cyan]wxextract resnap --no-relaunch[/]                          "
    "[dim]refresh data, keep WeChat closed[/]\n\n"
    "[bold yellow]Updating[/]\n"
    "  [cyan]uv tool upgrade wxextract[/]                               "
    "[dim]pull the latest release from GitHub (uv installs)[/]\n"
    "  [cyan]pipx upgrade wxextract[/]                                  "
    "[dim](pipx installs)[/]\n"
    "  [dim]The tool also auto-checks the GitHub releases API once per day "
    "and prints a short notice when a newer version is out. "
    "Disable with [/][cyan]--no-update-check[/][dim] or [/]"
    "[cyan]WXE_NO_UPDATE_CHECK=1[/][dim].[/]\n\n"
    "[dim]Docs: https://github.com/boujuan/extract-wechat-messages-linux[/]"
)


def _add_common_render_args(sp, *, include_pipeline: bool = False):
    """Shared argument groups for the `run` and `render` subcommands."""
    g_sel = sp.add_argument_group("Conversation selection")
    g_sel.add_argument("--alias", metavar="WECHAT_ID[,WECHAT_ID,...]",
                       help="WeChat ID of the contact(s) to extract (comma-separated for several). "
                            "Skips the interactive picker.")
    g_sel.add_argument("--all-contacts", action="store_true",
                       help="Extract every contact with messages. Implies sequential processing; "
                            "one set of files per contact in --out-dir.")
    g_sel.add_argument("--min-messages", type=int, default=1, metavar="N",
                       help="With --all-contacts, only render contacts with ≥ N messages. "
                            "Default: %(default)s.")
    g_sel.add_argument("--include-recalls", action="store_true",
                       help="Keep \"X recalled a message\" notifications (default: filter them out).")
    g_sel.add_argument("--since-last", action="store_true",
                       help="Only render messages newer than the previous run for this alias. "
                            "Reads/writes workspace/last_extract.json. Output filename gets an "
                            "_inc_<timestamp> suffix; if no new messages, exits cleanly with no file.")
    sp.set_defaults(skip_recalls=True)

    g_out = sp.add_argument_group("Output format")
    g_out.add_argument("--format", default="txt-b,jsonl,xml", metavar="LIST",
                       help="Comma list of formats: txt-b, jsonl, xml, md. "
                            "Default: %(default)s.")
    g_out.add_argument("--chunk", default="none", metavar="SPEC",
                       help="Split output: none | month | week | day | tokens:N. Default: %(default)s.")
    g_out.add_argument("--out-dir", type=str, default=None, metavar="PATH",
                       help="Override just the output directory (default: <workspace>/output/).")
    g_out.add_argument("--my-label", default="Me", metavar="STR",
                       help="Label for your own messages in renders. Default: %(default)s.")
    g_out.add_argument("--stats", action="store_true",
                       help="Also print the per-contact stats panel after the final summary.")

    g_compact = sp.add_argument_group("Compression / styling (TXT-B)")
    g_compact.add_argument("--time-precision", choices=("seconds", "minutes"),
                           default="seconds", metavar="MODE",
                           help="Timestamp granularity in TXT-B. Default: %(default)s.")
    g_compact.add_argument("--reply-preview", choices=("full", "short", "none"),
                           default="full", metavar="MODE",
                           help="Quoted reply rendering: full=sender+time+content, "
                                "short=sender+time, none=sender only. Default: %(default)s.")
    g_compact.add_argument("--gap", type=int, default=7200, metavar="SECONDS",
                           help="Silence gap that starts a new session header. Default: %(default)s.")
    g_compact.add_argument("--no-turn-merge", action="store_true",
                           help="Emit one line per message instead of joining same-sender turns with `;`.")
    g_compact.add_argument("--sticker-emojis", action="store_true",
                           help="Replace [Chuckle]/[Facepalm]/etc with Unicode emoji.")
    g_compact.add_argument("--squash-emoji", action="store_true",
                           help="Collapse [Tag][Tag][Tag] → [Tag×3] for runs of ≥3.")
    g_compact.add_argument("--redact", action="store_true",
                           help="Mask emails / phones / IBANs / long digit runs.")

    if include_pipeline:
        g_pipe = sp.add_argument_group("Pipeline behavior")
        g_pipe.add_argument("--force", action="store_true",
                            help="Bypass all caches: re-extract keys and re-decrypt every DB.")
        g_pipe.add_argument("--no-relaunch", action="store_true",
                            help="Don't re-launch WeChat after the snapshot "
                                 "(avoids the \"Open WeChat\" login-confirm dialog).")


def build_parser() -> argparse.ArgumentParser:
    Formatter = _make_formatter()
    p = argparse.ArgumentParser(
        prog="wxextract",
        description=(
            "[bold]Extract WeChat 4.x conversations on Linux into compact, LLM-ready text.[/]\n\n"
            "Discovers the WeChat install, recovers SQLCipher keys from a running "
            "WeChat process, snapshots and decrypts the chat databases, then renders "
            "one or more output formats with optional token-aware chunking."
        ),
        epilog=_EPILOG,
        formatter_class=Formatter,
    )

    # ── top-level: I/O & verbosity ────────────────────────────────────────────
    g_io = p.add_argument_group("Workspace & I/O")
    g_io.add_argument("--workspace", type=str, default=None, metavar="PATH",
                      help="Override the workspace directory "
                           "(snapshot, plain_dbs, output, all_keys.json). "
                           "Default: ~/.local/share/wxextract/ when installed, "
                           "<project>/workspace/ when running from source.")
    g_io.add_argument("--account-dir", type=str, default=None, metavar="PATH",
                      help="WeChat account folder (xwechat_files/wxid_<id>_<suffix>/). "
                           "Use when multiple accounts are logged in and auto-pick guesses wrong.")
    p.add_argument("--version", action="version", version=f"wxextract {__version__}",
                   help="Show wxextract version and exit.")

    g_verbosity = p.add_argument_group("Verbosity")
    me = g_verbosity.add_mutually_exclusive_group()
    me.add_argument("-v", "--verbose", action="store_true",
                    help="Verbose per-stage logging. Disables the live progress UI.")
    me.add_argument("-q", "--quiet", action="store_true",
                    help="Silence everything except errors and the final summary.")
    g_verbosity.add_argument("--no-progress", action="store_true",
                             help="Disable the live progress UI (plain log lines instead).")
    g_verbosity.add_argument("--no-summary", action="store_true",
                             help="Skip the final summary panel.")
    g_verbosity.add_argument("--no-update-check", action="store_true",
                             help="Skip the once-per-day GitHub release check "
                                  "(can also set WXE_NO_UPDATE_CHECK=1).")

    # ── subcommands ───────────────────────────────────────────────────────────
    sub = p.add_subparsers(
        dest="command", required=False, metavar="<command>",
        title="Commands",
        description="Run with no command for the full interactive pipeline.",
    )

    sp = sub.add_parser("status", help="Show discovered WeChat install + workspace cache state.",
                        description="Print what wxextract sees about your install + workspace, then exit.",
                        formatter_class=Formatter)

    sp = sub.add_parser("list", help="List contacts (requires prior snapshot/decrypt).",
                        description="Print the contacts table sorted by recency, then exit.",
                        formatter_class=Formatter)

    sp = sub.add_parser("resnap",
                        help="Close WeChat → refresh snapshot + decrypt → re-open.",
                        description="Force a fresh snapshot and decrypt pass; doesn't render any conversation.",
                        formatter_class=Formatter)
    g_resnap = sp.add_argument_group("Pipeline behavior")
    g_resnap.add_argument("--force", action="store_true",
                          help="Bypass all caches: re-extract keys and re-decrypt every DB.")
    g_resnap.add_argument("--no-relaunch", action="store_true",
                          help="Don't re-launch WeChat afterwards (avoids the login-confirm dialog).")

    sp = sub.add_parser("cleanup",
                        help="Delete cached workspace data (snapshot, decrypted DBs, "
                             "rendered outputs, keys, etc.) and the update-check cache.",
                        description="Selectively wipe wxextract's persisted state. "
                                    "Without category flags, defaults to --all. "
                                    "By default prompts for confirmation; use --yes to skip.",
                        formatter_class=Formatter)
    g_cat = sp.add_argument_group("What to remove (combinable; --all wins)")
    g_cat.add_argument("--all", action="store_true",
                       help="Wipe the entire workspace + the XDG cache "
                            "(equivalent to every category flag below).")
    g_cat.add_argument("--snapshot", action="store_true",
                       help="The rsync mirror of encrypted DBs (~700 MB).")
    g_cat.add_argument("--plain-dbs", action="store_true",
                       help="The decrypted SQLite databases (~230 MB).")
    g_cat.add_argument("--output", action="store_true",
                       help="Rendered conversation files (.txt / .xml / .jsonl / .md) "
                            "and the HTML report (report.html) under <workspace>/output/.")
    g_cat.add_argument("--media", action="store_true",
                       help="Decrypted .dat → .jpg/png/gif images.")
    g_cat.add_argument("--keys", action="store_true",
                       help="all_keys.json + image_key.json (sensitive; need re-scan).")
    g_cat.add_argument("--state", action="store_true",
                       help="last_extract.json + snapshot_stats.json "
                            "(per-contact incremental baselines + drift baseline).")
    g_cat.add_argument("--cache", action="store_true",
                       help="The XDG cache (update-check timestamp).")
    g_mode = sp.add_argument_group("How")
    g_mode.add_argument("--dry-run", action="store_true",
                        help="Show what would be deleted; don't actually delete.")
    g_mode.add_argument("--yes", "-y", action="store_true",
                        help="Skip the confirmation prompt.")

    sp = sub.add_parser("images",
                        help="Decrypt WeChat .dat image attachments into JPG/PNG/GIF/etc.",
                        description="Walk the message-attachment tree for a contact (or all) and "
                                    "decrypt every .dat image. V1 (legacy single-byte XOR) works "
                                    "automatically. V2 (WeChat ≥ 2025-08) requires --image-key with "
                                    "the 16-byte AES key from a WeChat memory scan.",
                        formatter_class=Formatter)
    sp.add_argument("--alias", metavar="WECHAT_ID",
                    help="WeChat ID of one contact (default: all contacts in the picker list).")
    sp.add_argument("--image-key", metavar="HEX_OR_ASCII",
                    help="Override the V2 AES key (32 hex chars OR 16 ASCII chars). "
                         "Default: auto-recover from a running WeChat process and cache.")
    sp.add_argument("--force-image-key", action="store_true",
                    help="Ignore the cached image key and re-scan WeChat memory.")
    sp.add_argument("--out-dir", metavar="PATH",
                    help="Where to write decrypted images. Default: <workspace>/media/<alias>/.")
    sp.add_argument("--include-thumbs", action="store_true",
                    help="Also decrypt _t.dat thumbnails (default: skip).")

    sp = sub.add_parser("stats",
                        help="Per-contact analytics: timeline, hourly heatmap, top emojis/words, response times.",
                        description="Compute and print conversation analytics. With --alias, prints one "
                                    "contact's panels to the terminal. With --html (or no --alias), renders "
                                    "a native interactive HTML report with Plotly charts: KPI cards, daily "
                                    "timeline, weekday×hour heatmaps, per-sender bars, reply-time histograms, "
                                    "chain box plots, and tables. "
                                    "Uses the existing decrypted plain_dbs/ (run `wxextract run` first).",
                        formatter_class=Formatter)
    sp.add_argument("--alias", metavar="WECHAT_ID",
                    help="WeChat ID of the contact. Comma list works too (a,b,c). "
                         "Omit to include every contact above --min-messages.")
    sp.add_argument("--my-label", default="Me", metavar="STR",
                    help="Label for your own messages. Default: %(default)s.")
    sp.add_argument("--top", type=int, default=12, metavar="N",
                    help="How many top emojis / words to show. Default: %(default)s.")
    sp.add_argument("--html", action="store_true",
                    help="Generate an interactive HTML report instead of terminal output. "
                         "Implied when --alias is omitted.")
    sp.add_argument("--min-messages", type=int, default=200, metavar="N",
                    help="(HTML report) skip contacts with fewer than N messages. Default: %(default)s.")
    sp.add_argument("--out", metavar="PATH",
                    help="(HTML report) output path. Default: <workspace>/output/report.html. "
                         "If PATH doesn't end with .html, it's treated as a directory and "
                         "report.html is written inside it (created if missing).")
    sp.add_argument("--open", action="store_true",
                    help="(HTML report) open the generated file in $BROWSER after writing.")
    sp.add_argument("--whatsapp-json", action="append", default=[], metavar="PATH",
                    help="Include a WhatsApp conversation alongside the WeChat data. PATH is "
                         "a JSON file produced by Parse_Whatsapp_LLM with --format wxextract. "
                         "Repeatable to include multiple WhatsApp contacts.")
    sp.add_argument("--whatsapp-merge", action="append", default=[], metavar="NAME=ALIAS",
                    help="Declare that the WhatsApp contact NAME and the WeChat ALIAS are the "
                         "same person. The report then emits three sections per merge: a "
                         "combined view, then the WhatsApp-only and WeChat-only views. "
                         "Repeatable.")
    sp.add_argument("--whatsapp-only", action="store_true",
                    help="Skip WeChat data and render a report containing only the "
                         "--whatsapp-json contacts. Useful for testing without a "
                         "decrypted WeChat snapshot.")

    sp = sub.add_parser("preview",
                        help="Peek at the most-recent N messages of a contact, no files written.",
                        description="Print the last N messages of a contact straight to stdout; "
                                    "useful for sanity-checking after a resnap.",
                        formatter_class=Formatter)
    sp.add_argument("--alias", required=True, metavar="WECHAT_ID",
                    help="WeChat ID of the contact.")
    sp.add_argument("--tail", type=int, default=20, metavar="N",
                    help="How many recent messages to show. Default: %(default)s.")
    sp.add_argument("--my-label", default="Me", metavar="STR",
                    help="Label for your own messages. Default: %(default)s.")

    sp = sub.add_parser("run",
                        help="Full pipeline: discover → snapshot → decrypt → render. (default)",
                        description="The default command. Runs the entire pipeline end-to-end with "
                                    "all caches active (fast-path skips work when no new messages).",
                        formatter_class=Formatter, epilog=_EPILOG)
    _add_common_render_args(sp, include_pipeline=True)

    sp = sub.add_parser("render",
                        help="Render only — assumes plain_dbs already exists.",
                        description="Skip the resnap step and render straight from existing plain_dbs. "
                                    "Fastest mode; use after a recent `wxextract run` to re-render with "
                                    "different flags.",
                        formatter_class=Formatter, epilog=_EPILOG)
    _add_common_render_args(sp, include_pipeline=False)

    return p


_RUN_FLAGS = {
    "--alias", "--format", "--chunk", "--gap", "--no-turn-merge", "--my-label",
    "--skip-recalls", "--include-recalls", "--squash-emoji", "--redact",
    "--sticker-emojis", "--time-precision", "--reply-preview",
    "--out-dir", "--force", "--no-relaunch",
}


def _inject_default_subcommand(argv: list[str]) -> list[str]:
    """If user types `wxextract --alias X ...` (or bare `wxextract`) with no
    explicit subcommand, inject `run`. Top-level flags (`--workspace`,
    `-v`, `-q`, etc.) pass through unchanged; the `run` lands after them
    but before any run-specific flags.
    """
    top_level = {"--workspace", "--account-dir", "-v", "--verbose", "-q", "--quiet",
                 "--no-progress", "--no-summary", "--no-update-check"}
    top_level_takes_arg = {"--workspace", "--account-dir"}
    subs = {"status", "list", "resnap", "run", "render", "preview",
            "stats", "images", "cleanup"}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in subs:
            return argv                   # already has subcommand
        if a in top_level:
            i += 2 if a in top_level_takes_arg else 1
            continue
        if a.startswith("--workspace=") or a.startswith("--account-dir="):
            i += 1
            continue
        if a.startswith("-"):
            # any other flag → must be a run/render flag; inject `run` before it
            head = argv[:i]
            tail = argv[i:]
            if any(t.split("=", 1)[0] in _RUN_FLAGS for t in tail):
                return head + ["run"] + tail
            return argv
        # bare positional with no subcommand — let argparse complain
        return argv
    # walked the whole argv with only top-level flags (or none): default to `run`
    return argv + ["run"]


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except KeyboardInterrupt:
        print("\n[cancelled]", file=sys.stderr)
        return 130


def _main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw = sys.argv[1:] if argv is None else list(argv)
    raw = _inject_default_subcommand(raw)
    args = parser.parse_args(raw)
    # With the live progress UI active we want logging quiet to avoid interleaving.
    # -v restores verbose INFO output (and turns the live UI off automatically).
    use_progress = not args.verbose and not args.no_progress
    log = setup_logging(verbose=args.verbose, quiet=args.quiet or use_progress)
    workspace = (Path(args.workspace).expanduser().resolve()
                 if args.workspace else default_workspace())
    cmd = args.command or "run"

    # Non-blocking GitHub release check (≤ once per 24h, cached).
    # Print the notice to stderr at the very end of the run instead of here,
    # so it doesn't get swallowed by the live progress UI.
    update_tag: str | None = None
    if not args.no_update_check and cmd in ("run", "render", "resnap"):
        try:
            from wxextract import update_check
            update_tag = update_check.check()
        except Exception:
            update_tag = None

    use_progress = not args.verbose and not args.no_progress and cmd in ("run", "render", "resnap")
    ui = None
    if use_progress:
        from rich.console import Console

        from wxextract.progress import ProgressUI
        ui = ProgressUI(console=Console(stderr=True), enabled=True)
        ui.start()
    try:
        if cmd == "status":
            return _cmd_status(workspace)
        if cmd == "list":
            return _cmd_list(workspace)
        if cmd == "resnap":
            return _cmd_resnap(workspace, force=getattr(args, "force", False),
                               no_relaunch=getattr(args, "no_relaunch", False),
                               account_dir=_account_dir_arg(args), ui=ui)
        if cmd == "render":
            return _cmd_render(args, workspace, log, ui=ui)
        if cmd == "preview":
            return _cmd_preview(args, workspace, log)
        if cmd == "stats":
            return _cmd_stats(args, workspace, log)
        if cmd == "images":
            return _cmd_images(args, workspace, log)
        if cmd == "cleanup":
            return _cmd_cleanup(args, workspace, log)
        if cmd == "run":
            return _cmd_run(args, workspace, log, ui=ui)
        parser.print_help()
        return 1
    finally:
        if ui is not None:
            ui.stop()
        if update_tag:
            from wxextract import update_check
            print(update_check.notice(update_tag), file=sys.stderr)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def _cmd_status(workspace: Path) -> int:
    from wxextract import discover, lifecycle
    print(f"wxextract {__version__}")
    print(f"workspace        : {workspace}")
    try:
        d = discover.discover()
        print(f"install kind     : {d.install_kind}")
        print(f"install version  : {d.install_version or '<not via pacman>'}")
        print(f"binary           : {d.binary_path or '<not found>'}")
        print(f"launch cmd       : {' '.join(d.launch_cmd) or '<none>'}")
        print(f"data root        : {d.data_root}")
        print(f"account          : {d.account_dir.name}")
        print(f"my wxid          : {d.my_wxid}")
        db_dir = d.db_storage()
        if db_dir.is_dir():
            dbs = list(db_dir.rglob("*.db"))
            total = sum(p.stat().st_size for p in dbs)
            print(f"db_storage       : {len(dbs)} *.db files, {human_bytes(total)}")
    except RuntimeError as e:
        print(f"discovery error  : {e}")
    pids = lifecycle.wechat_running()
    main_pid = lifecycle.main_wechat_pid()
    print(f"wechat processes : {len(pids)} (main PID = {main_pid or '<none>'})")
    snap_acct = _snapshot_account_dir(workspace)
    plain = workspace / "plain_dbs"
    keys = workspace / "all_keys.json"
    print(f"snapshot         : {snap_acct if snap_acct and snap_acct.exists() else '<none>'}")
    print(f"plain_dbs        : {'exists' if plain.is_dir() else '<none>'}")
    print(f"keys             : {'exists' if keys.is_file() else '<none>'}")
    return 0


def _snapshot_account_dir(workspace: Path) -> Path | None:
    """Find <workspace>/snapshot/wxid_*/ if it exists."""
    snap = workspace / "snapshot"
    if not snap.is_dir():
        return None
    for p in snap.iterdir():
        if p.is_dir() and p.name.startswith("wxid_"):
            return p
    return None


# ---------------------------------------------------------------------------
# list / picker
# ---------------------------------------------------------------------------


def _cmd_list(workspace: Path) -> int:
    from wxextract.contacts import load_contacts
    from wxextract.picker import render_table
    plain = workspace / "plain_dbs"
    if not plain.is_dir():
        print(f"[!] no plain_dbs at {plain}. Run `wxextract run` or `wxextract resnap` first.")
        return 2
    recs = load_contacts(plain)
    render_table(recs, Console(), limit=None)
    return 0


# ---------------------------------------------------------------------------
# resnap — refresh snapshot + decrypt without rendering
# ---------------------------------------------------------------------------


_ESSENTIAL_DBS = ("message/message_0.db", "message/message_1.db",
                  "message/message_2.db", "message/message_3.db",
                  "message/biz_message_0.db", "contact/contact.db")


def _content_stats(db_storage: Path, keys_by_rel: dict[str, str]) -> dict[str, int]:
    """Sum of rows across every Msg_<hash> table in each message_*.db, via sqlcipher CLI.

    This catches drift the mtime check misses (sync-in-progress, WAL-only writes).
    Cost ~0.2-0.5s per shard.
    """
    import shutil as _sh
    import subprocess as _sp
    if not _sh.which("sqlcipher"):
        return {}
    out: dict[str, int] = {}
    for rel, key_hex in keys_by_rel.items():
        if not rel.startswith("message/message_") or "fts" in rel or "resource" in rel:
            continue
        p = db_storage / rel
        if not p.is_file():
            continue
        # SQLite doesn't support a generic per-table COUNT in one SELECT, so list the
        # Msg_* tables first then sum COUNT(*) across them in a follow-up query.
        list_sql = (
            f"PRAGMA key = \"x'{key_hex}'\";\n"
            "PRAGMA cipher_compatibility = 4;\n"
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%';\n"
        )
        try:
            res = _sp.run(["sqlcipher", str(p)], input=list_sql, text=True,
                          capture_output=True, timeout=10)
        except (_sp.TimeoutExpired, OSError):
            continue
        if res.returncode != 0:
            continue
        tables = [t for t in res.stdout.split() if t.startswith("Msg_")]
        if not tables:
            out[rel] = 0
            continue
        union_sql = (
            f"PRAGMA key = \"x'{key_hex}'\";\n"
            "PRAGMA cipher_compatibility = 4;\n"
            + " ".join(f"SELECT COUNT(*) FROM {t};" for t in tables)
        )
        try:
            res2 = _sp.run(["sqlcipher", str(p)], input=union_sql, text=True,
                           capture_output=True, timeout=15)
        except (_sp.TimeoutExpired, OSError):
            continue
        if res2.returncode != 0:
            continue
        total = sum(int(x) for x in res2.stdout.split() if x.isdigit())
        out[rel] = total
    return out


def _save_snapshot_stats(workspace: Path, stats: dict[str, int]) -> None:
    p = workspace / "snapshot_stats.json"
    p.write_text(json.dumps(stats, indent=2))


def _load_snapshot_stats(workspace: Path) -> dict[str, int] | None:
    p = workspace / "snapshot_stats.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _snapshot_is_fresh(live: Path, snap: Path) -> bool:
    """True iff the DBs that actually carry user-visible conversation data
    (message shards + contact list, plus their WAL/SHM sidecars) have identical
    mtime+size in the snapshot.

    Other DBs (session, emoticon, general, ...) are touched constantly by
    WeChat in the background and don't affect chat extraction — we ignore
    those for the freshness check."""
    if not snap.is_dir():
        return False
    for rel in _ESSENTIAL_DBS:
        for variant in (rel, rel + "-wal", rel + "-shm"):
            lp = live / variant
            sp = snap / variant
            if not lp.exists():
                if sp.exists():
                    return False
                continue
            if not sp.exists():
                return False
            try:
                l_st, s_st = lp.stat(), sp.stat()
            except OSError:
                return False
            if abs(l_st.st_mtime - s_st.st_mtime) > 1 or l_st.st_size != s_st.st_size:
                return False
    return True


def _cached_keys_still_valid(keys_path: Path, db_storage: Path) -> dict[str, str] | None:
    """If all_keys.json exists AND every keyed DB validates with its saved key
    against the CURRENT live DB's page-1 HMAC, return the {rel: hex} map.
    Otherwise return None (need to re-scan)."""
    if not keys_path.is_file():
        return None
    try:
        from wxextract.keys import collect_dbs, load_keys, verify_enc_key
    except ImportError:
        return None
    saved = load_keys(keys_path)
    if not saved:
        return None
    db_files, salt_to_rels = collect_dbs(db_storage)
    # require every distinct salt to have a working saved key
    salt_to_key: dict[str, str] = {}
    for db in db_files:
        if db.rel in saved:
            salt_to_key[db.salt_hex] = saved[db.rel]
    for db in db_files:
        key_hex = salt_to_key.get(db.salt_hex)
        if not key_hex:
            return None
        if not verify_enc_key(bytes.fromhex(key_hex), db.page1):
            return None
    return saved


def _cmd_resnap(workspace: Path, force: bool = False, no_relaunch: bool = False,
                account_dir: Path | None = None, ui=None) -> int:
    import logging

    from wxextract import discover, lifecycle, snapshot
    from wxextract.decrypt import decrypt_all
    from wxextract.keys import collect_dbs, load_keys, save_keys, scan
    log = logging.getLogger("wxextract")
    if ui:
        ui.begin("Discover")
    d = discover.discover(prefer_account=account_dir)
    log.info(f"install kind: {d.install_kind}; binary: {d.binary_path}; launch: {' '.join(d.launch_cmd) or '<none>'}")
    if ui:
        ui.end("Discover", f"install={d.install_kind}  account={d.account_dir.name}")
    keys_path = workspace / "all_keys.json"

    # ── SNAPSHOT-FRESHNESS FAST PATH ──────────────────────────────────────────
    snap_root = workspace / "snapshot" / d.account_dir.name
    plain_dbs = workspace / "plain_dbs"
    if (
        not force
        and keys_path.is_file()
        and _snapshot_is_fresh(d.db_storage(), snapshot.db_storage_of(snap_root))
        and plain_dbs.is_dir()
    ):
        # Mtime+size check passed; now content-level drift check via sqlcipher.
        # Catches the "WeChat just sync'd, mtimes haven't moved yet" window.
        if ui:
            ui.begin("Keys", "validating cache + drift check")
        try:
            keys_for_check = load_keys(keys_path)
        except Exception:
            keys_for_check = {}
        live_stats = _content_stats(d.db_storage(), keys_for_check)
        saved = _load_snapshot_stats(workspace)
        if saved is None:
            log.info("no saved stats — measuring snapshot vs live to validate freshness")
            snap_stats = _content_stats(snapshot.db_storage_of(snap_root), keys_for_check)
            if snap_stats == live_stats and snap_stats:
                log.info(f"snapshot matches live ({sum(live_stats.values())} msgs) — fast-path OK; saving baseline")
                _save_snapshot_stats(workspace, snap_stats)
                if ui:
                    total = sum(live_stats.values())
                    ui.end("Keys", f"{len(keys_for_check)} cached keys ✓  (baseline saved: {total:,} msgs)")
                    ui.skip("Snapshot", "fresh — no changes")
                    ui.skip("Decrypt", "fresh — no changes")
                return 0
            log.info(f"snapshot vs live differ: snap={sum(snap_stats.values())} live={sum(live_stats.values())} — full resnap")
            if ui:
                ui.end("Keys", f"{len(keys_for_check)} cached keys; drift detected, resnapping")
        elif live_stats == saved:
            log.info(f"snapshot is fresh — nothing to do "
                     f"(content stats match: {sum(live_stats.values())} msgs across {len(live_stats)} shards)")
            if ui:
                total = sum(live_stats.values())
                ui.end("Keys", f"{len(keys_for_check)} cached keys ✓  ({total:,} msgs unchanged)")
                ui.skip("Snapshot", "fresh — no changes")
                ui.skip("Decrypt", "fresh — no changes")
            return 0
        else:
            log.info(f"content drift detected: live={sum(live_stats.values())} vs baseline={sum(saved.values())} — resnapping")
            if ui:
                delta = sum(live_stats.values()) - sum(saved.values())
                ui.end("Keys", f"drift detected ({delta:+,} msgs since last snap) — resnapping")

    # ── KEY CACHE CHECK ────────────────────────────────────────────────────────
    if ui and ui.stages["Keys"].status not in ("done", "skipped"):
        ui.begin("Keys", "validating cached keys vs live DBs")
    cached = None if force else _cached_keys_still_valid(keys_path, d.db_storage())
    if cached is not None:
        log.info(f"keys: reusing {len(cached)} cached keys from {keys_path.name} (validated against live DBs)")
        if ui:
            ui.end("Keys", f"{len(cached)} cached keys validated against live")
        keys_by_rel = cached
    else:
        if ui:
            ui.begin("Keys", "scanning /proc/<pid>/mem for SQLCipher keys")
        # need WeChat running to scan memory
        if not lifecycle.wechat_running():
            log.info("wechat not running — launching for key extraction")
            lifecycle.launch_wechat(cmd=d.launch_cmd or None)
            time.sleep(8)
        pids = []
        bin_str = str(d.binary_path) if d.binary_path else None
        mp = lifecycle.main_wechat_pid(binary=bin_str)
        if mp:
            pids.append(mp)
        pids += [p for p in lifecycle.wechat_running() if p != mp]
        scan_res = scan(pids, d.db_storage())
        log.info(f"keys: {len(scan_res.keys_by_rel)} / {len(scan_res.salt_to_rels)} via memory scan in {scan_res.elapsed:.2f}s")
        # 0/N is almost always "WeChat is at the Open WeChat dialog" → offer to wait + retry
        if len(scan_res.keys_by_rel) == 0 and len(scan_res.salt_to_rels) > 0 and sys.stdin.isatty():
            log.warning("0 keys recovered — WeChat is likely at the 'Open WeChat' login-confirm dialog.")
            log.warning("If you click that green 'Open WeChat' button now, the keys will load into memory.")
            for attempt in range(1, 4):
                try:
                    input(f"Press Enter to retry (attempt {attempt}/3), or Ctrl+C to abort: ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    return 3
                time.sleep(1.5)
                pids = []
                mp = lifecycle.main_wechat_pid(binary=bin_str)
                if mp:
                    pids.append(mp)
                pids += [p for p in lifecycle.wechat_running() if p != mp]
                scan_res = scan(pids, d.db_storage())
                log.info(f"retry {attempt}: keys {len(scan_res.keys_by_rel)} / {len(scan_res.salt_to_rels)}")
                if len(scan_res.keys_by_rel) == len(scan_res.salt_to_rels):
                    break
        if len(scan_res.keys_by_rel) < len(scan_res.salt_to_rels):
            log.error(
                f"could not recover all keys ({len(scan_res.keys_by_rel)}/{len(scan_res.salt_to_rels)})"
            )
            if scan_res.keys_by_rel == {}:
                log.error("0 keys found — WeChat is probably at the 'Open WeChat' login-confirm "
                          "dialog. Click that button so chats actually load, then re-run.")
            return 3
        db_files, _ = collect_dbs(d.db_storage())
        save_keys(scan_res, db_files, d.db_storage(), keys_path)
        keys_by_rel = load_keys(keys_path)
        if ui:
            ui.end("Keys", f"{len(scan_res.keys_by_rel)} keys recovered via memory scan in {scan_res.elapsed:.2f}s")

    # ── CLOSE → SNAPSHOT → DECRYPT ────────────────────────────────────────────
    if ui:
        ui.begin("Snapshot", "closing WeChat for consistent snapshot…")
    was_running = bool(lifecycle.wechat_running())
    if was_running:
        bin_str = str(d.binary_path) if d.binary_path else None
        if not lifecycle.close_wechat(binary=bin_str):
            log.warning("wechat did not close cleanly within 10s; continuing anyway")
    if ui:
        ui.detail("Snapshot", "rsync -aH …")
    snap_acct = snapshot.snapshot(d.account_dir, workspace / "snapshot")
    try:
        snap_size = sum(p.stat().st_size for p in (workspace / "snapshot").rglob("*") if p.is_file())
    except OSError:
        snap_size = 0
    if ui:
        from wxextract.progress import _human_bytes
        ui.end("Snapshot", f"{_human_bytes(snap_size)} synced to workspace/snapshot/")
        ui.begin("Decrypt", f"decrypting {len(keys_by_rel)} databases (parallel)…")
    decrypt_results = decrypt_all(
        snapshot.db_storage_of(snap_acct),
        keys_by_rel,
        workspace / "plain_dbs",
        skip_unchanged=not force,
    )
    n_fresh = sum(1 for r in decrypt_results if r.pages > 0)
    n_skipped = sum(1 for r in decrypt_results if r.ok and r.pages == 0)
    n_failed = sum(1 for r in decrypt_results if not r.ok)
    log.info(f"decrypt: {n_fresh} updated, {n_skipped} unchanged, {n_failed} failed")
    if n_failed:
        for r in decrypt_results:
            if not r.ok:
                log.error(f"  failed: {r.rel}: {r.error}")
    if ui:
        detail = f"{n_fresh} re-decrypted, {n_skipped} unchanged"
        if n_failed:
            detail += f", {n_failed} FAILED"
        ui.end("Decrypt", detail) if n_failed == 0 else ui.fail("Decrypt", detail)
    if was_running and not no_relaunch:
        lifecycle.launch_wechat(cmd=d.launch_cmd or None)
        log.info("(WeChat may show a login confirmation dialog — click 'Open WeChat' to resume)")
    elif was_running and no_relaunch:
        log.info("(skipping WeChat re-launch as requested; start it yourself when ready)")
    # Save content-stats baseline so the next run can detect drift cheaply
    try:
        snap_stats = _content_stats(snapshot.db_storage_of(snap_acct), keys_by_rel)
        if snap_stats:
            _save_snapshot_stats(workspace, snap_stats)
            log.info(f"saved snapshot stats: {sum(snap_stats.values())} msgs across {len(snap_stats)} shards")
    except Exception as e:
        log.warning(f"could not save snapshot stats: {e}")
    log.info("resnap complete")
    return 0 if n_failed == 0 else 4


# ---------------------------------------------------------------------------
# render — assumes plain_dbs already present
# ---------------------------------------------------------------------------


def _suggest_alias(recs, alias: str) -> str:
    """Return a 'did you mean X / Y?' hint via difflib closest matches."""
    import difflib
    pool = [r.alias for r in recs if r.alias] + [r.display_name for r in recs]
    close = difflib.get_close_matches(alias, pool, n=3, cutoff=0.5)
    if close:
        return f"  did you mean: {', '.join(repr(c) for c in close)}?"
    return ""


def _cmd_render(args, workspace: Path, log, ui=None) -> int:
    """Render one contact (or many via --alias=a,b or --all-contacts)."""
    from wxextract.contacts import find_by_alias, load_contacts
    from wxextract.picker import pick
    t_start = time.perf_counter()
    plain = workspace / "plain_dbs"
    if not plain.is_dir():
        print(f"[!] no plain_dbs at {plain}. Run `wxextract run` first.")
        return 2
    if ui:
        ui.begin("Contacts", "loading contact list…")
    recs = load_contacts(plain)
    contacts: list = []
    if getattr(args, "all_contacts", False):
        min_msgs = max(1, getattr(args, "min_messages", 1))
        contacts = [r for r in recs if r.message_count >= min_msgs]
        if ui:
            ui.end("Contacts", f"--all-contacts: {len(contacts)} (≥ {min_msgs} msgs)")
    elif getattr(args, "alias", None):
        aliases = [a.strip() for a in args.alias.split(",") if a.strip()]
        missing: list[str] = []
        for a in aliases:
            c = find_by_alias(recs, a)
            if c is None:
                missing.append(a)
            else:
                contacts.append(c)
        if missing:
            if ui:
                ui.fail("Contacts", f"alias(es) not found: {', '.join(missing)}")
                ui.stop()
            print(f"[!] alias(es) not found: {', '.join(repr(m) for m in missing)}")
            for m in missing:
                hint = _suggest_alias(recs, m)
                if hint:
                    print(f"  {m}:{hint}")
            return 2
        if ui:
            summary = ", ".join(f"{c.display_name} ({c.message_count:,})" for c in contacts)
            ui.end("Contacts", summary[:80])
    else:
        if ui:
            ui.stop()
        choice = pick(recs, Console())
        if choice is None:
            return 0
        contacts = [choice]
        if ui:
            ui.start()
            ui.end("Contacts", f"{choice.display_name} ({choice.alias or choice.username}) — {choice.message_count:,} msgs total")

    # ── render each contact in sequence ────────────────────────────────────
    rc_overall = 0
    all_outputs: list[tuple[str, Path]] = []
    all_msgs_sum = 0
    all_recall_sum = 0
    first_ts = None
    last_ts = 0
    summary_contact = contacts[0] if contacts else None
    per_contact_msgs: list = []        # [(contact, msgs)] — kept for --stats
    for i, contact in enumerate(contacts):
        if ui and i > 0:
            ui.begin("Extract", f"({i + 1}/{len(contacts)}) {contact.display_name}…")
        rc, outs, msgs, recall_count = _render_single_contact(args, workspace, log, ui, contact, len(contacts))
        if rc != 0:
            rc_overall = rc
        if msgs:
            all_outputs.extend(outs)
            all_msgs_sum += len(msgs)
            all_recall_sum += recall_count
            if first_ts is None or msgs[0].create_time < first_ts:
                first_ts = msgs[0].create_time
            if msgs[-1].create_time > last_ts:
                last_ts = msgs[-1].create_time
            per_contact_msgs.append((contact, msgs))

    if rc_overall == 0 and all_outputs and not getattr(args, "no_summary", False) and summary_contact:
        if len(contacts) == 1:
            sc = summary_contact
        else:
            sc = _make_aggregate_contact(contacts, len(all_outputs))
        from datetime import datetime as _dt
        first = _dt.fromtimestamp(first_ts) if first_ts else None
        last = _dt.fromtimestamp(last_ts) if last_ts else None
        _print_summary_with_range(sc, all_msgs_sum, all_recall_sum,
                                  first, last, all_outputs, workspace, ui,
                                  total_time=time.perf_counter() - t_start)
    # --stats: per-contact analytics panel(s)
    if rc_overall == 0 and getattr(args, "stats", False) and per_contact_msgs:
        import shutil as _sh

        from wxextract import stats as _stats
        if ui:
            ui.stop()
        width = _sh.get_terminal_size((140, 24)).columns
        cons = Console(width=max(width, 120))
        for contact, msgs in per_contact_msgs:
            counts = _stats.compute(msgs, contact, my_label=args.my_label, top_n=12)
            _stats.render(counts, contact, args.my_label, cons)
    return rc_overall


def _make_aggregate_contact(contacts, n_files):
    """Build a ContactRecord-shaped stub so the summary header reads sanely
    when multiple contacts were extracted in one run."""
    from wxextract.contacts import ContactRecord
    names = ", ".join(c.display_name for c in contacts[:4])
    if len(contacts) > 4:
        names += f", … +{len(contacts) - 4} more"
    return ContactRecord(
        username="",
        alias=f"{len(contacts)} contacts",
        nick_name="",
        remark=f"{len(contacts)} contacts → {n_files} files: {names}",
        local_type=1,
    )


def _render_single_contact(args, workspace, log, ui, contact, n_total):
    """Run extract + render+chunk for one contact.
    Returns (rc, [(fmt, Path), ...], msgs, recall_count)."""
    from wxextract.messages import extract
    if ui:
        prefix = f"({contact.display_name}) " if n_total > 1 else ""
        ui.begin("Extract", prefix + "walking message table…")
    my_wxid = _detect_my_wxid(workspace) or "wxid_unknown"
    skip = args.skip_recalls and not args.include_recalls
    msgs_all = list(extract(contact, my_wxid=my_wxid, skip_recalls=False))
    msgs = [m for m in msgs_all if not _is_recall_msg(m)] if skip else msgs_all
    recall_count = len(msgs_all) - len(msgs)

    # ── --since-last: filter to messages newer than the saved baseline ────────
    since_last = getattr(args, "since_last", False)
    since_suffix = ""
    if since_last:
        from wxextract import state as _state
        prior = _state.get(workspace, contact.alias) if contact.alias else None
        if prior:
            threshold = int(prior.get("last_local_id", 0))
            before_n = len(msgs)
            msgs = [m for m in msgs if m.local_id > threshold]
            log.info(f"--since-last: dropped {before_n - len(msgs)} msgs at or below local_id={threshold}")
            since_suffix = f"_inc_{int(time.time())}"
        else:
            log.info("--since-last: no prior baseline for this alias — emitting full export and seeding state")
            since_suffix = "_baseline"

    log.info(f"extracted {len(msgs)} messages for {contact.alias or contact.username}")
    if ui:
        d = f"{len(msgs):,} messages"
        if recall_count:
            d += f"  ({recall_count} recalls filtered)"
        if since_last:
            d += "  (incremental)"
        if n_total > 1:
            d = f"{contact.display_name}: " + d
        ui.end("Extract", d)

    # nothing new → exit cleanly without writing files for this contact
    if since_last and not msgs:
        if n_total == 1:
            print(f"Nothing new since the last run for {contact.alias or contact.username}")
        if msgs_all:
            last = msgs_all[-1]
            from wxextract import state as _state
            _state.save(workspace, contact.alias, username=contact.username,
                        last_local_id=last.local_id, last_create_time=last.create_time)
        return 0, [], [], recall_count

    out_dir = (Path(args.out_dir).expanduser().resolve()
               if getattr(args, "out_dir", None) else workspace / "output")
    rc, outputs = _render_and_chunk(
        args, msgs, contact, my_wxid, out_dir, ui=ui,
        name_suffix=since_suffix,
    )
    if rc == 0 and msgs:
        last = msgs[-1]
        from wxextract import state as _state
        _state.save(workspace, contact.alias, username=contact.username,
                    last_local_id=last.local_id, last_create_time=last.create_time)
    return rc, outputs, msgs, recall_count


def _print_summary_with_range(contact, message_count, recall_count, first_dt, last_dt,
                              outputs, workspace, ui, total_time: float = 0.0):
    """Final summary panel given an aggregate contact + already-computed range."""
    import shutil as _sh

    from rich.console import Console

    from wxextract.progress import file_stats, render_summary
    if ui:
        ui.stop()
    if not outputs:
        return
    stats = [file_stats(p, fmt) for fmt, p in outputs]
    total_days = ((last_dt.date() - first_dt.date()).days + 1) if (first_dt and last_dt) else 0
    # prefer the explicit total_time (full run wall-clock); fall back to ui.t0
    if total_time == 0.0 and ui:
        total_time = time.perf_counter() - ui.t0
    width = _sh.get_terminal_size((140, 24)).columns
    render_summary(
        Console(width=max(width, 120)),
        contact=contact,
        message_count=message_count,
        recall_count=recall_count,
        date_range=(first_dt.date().isoformat() if first_dt else "?",
                    last_dt.date().isoformat() if last_dt else "?"),
        total_days=total_days,
        total_time=total_time,
        outputs=stats,
        workspace=workspace,
    )


def _is_recall_msg(m) -> bool:
    from wxextract.messages import _is_recall
    return _is_recall(m.type, m.content)


_IMG_KEY_CACHE = "image_key.json"


def _load_cached_image_key(workspace: Path) -> tuple[bytes, int] | None:
    """Return (aes_key, xor_key) from workspace/image_key.json, or None."""
    p = workspace / _IMG_KEY_CACHE
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text())
        aes = bytes.fromhex(data["aes_key_hex"])
        xor = int(data.get("xor_key", 0x88))
        if len(aes) == 16:
            return aes, xor
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        pass
    return None


def _save_cached_image_key(workspace: Path, aes: bytes, xor: int) -> None:
    import stat as _stat
    p = workspace / _IMG_KEY_CACHE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"aes_key_hex": aes.hex(),
                             "xor_key": int(xor)}, indent=2))
    try:
        p.chmod(_stat.S_IRUSR | _stat.S_IWUSR)
    except OSError:
        pass


def _cmd_images(args, workspace: Path, log) -> int:
    """Decrypt .dat images for one contact (or all).

    AES key is auto-recovered from a running WeChat process on first run;
    subsequent runs read from workspace/image_key.json. Use --force-image-key
    to re-scan. Manual override: --image-key HEX (16 bytes = 32 hex chars,
    OR a 16-char ASCII string is also accepted).
    """
    from wxextract import discover, images, lifecycle
    from wxextract.contacts import find_by_alias, load_contacts
    plain = workspace / "plain_dbs"
    if not plain.is_dir():
        print(f"[!] no plain_dbs at {plain}. Run `wxextract run` first.")
        return 2
    d = discover.discover()

    # ── resolve AES key + XOR key ─────────────────────────────────────────
    aes_key: bytes | None = None
    xor_key: int = 0x88
    if args.image_key:
        raw = args.image_key.strip()
        try:
            aes_key = bytes.fromhex(raw)
            if len(aes_key) != 16:
                raise ValueError
        except ValueError:
            if len(raw) == 16:
                aes_key = raw.encode("ascii")
            else:
                print(f"[!] --image-key must be 32 hex chars or 16 ASCII chars, got {len(raw)}")
                return 2
        log.info(f"image key from --image-key flag ({aes_key.hex()[:8]}…)")
    elif not args.force_image_key:
        cached = _load_cached_image_key(workspace)
        if cached is not None:
            aes_key, xor_key = cached
            log.info(f"image key from cache ({aes_key.hex()[:8]}…, xor=0x{xor_key:02x})")

    if aes_key is None:
        # need to recover from memory — pick MULTIPLE test ciphertexts so a
        # candidate must validate against all of them (kills false positives)
        vectors = images.pick_test_ciphertexts(d.account_dir, n=5)
        if not vectors:
            print("[!] no V2 .dat files found — nothing to decrypt.")
            return 0
        log.info(f"using {len(vectors)} test vector(s) for key cross-validation: "
                 + ", ".join(p.name for _, p in vectors))
        xor_key = images.derive_xor_key(d.account_dir)
        log.info(f"derived xor key: 0x{xor_key:02x}")
        pids = []
        bin_str = str(d.binary_path) if d.binary_path else None
        mp = lifecycle.main_wechat_pid(binary=bin_str)
        if mp:
            pids.append(mp)
        pids += [p for p in lifecycle.wechat_running() if p != mp]
        if not pids:
            print("[!] WeChat is not running — cannot scan memory for the image AES key.")
            print("    Either: (a) open WeChat, view 2-3 images, then re-run, or")
            print("            (b) supply the key manually with --image-key.")
            return 3
        log.info(f"scanning {len(pids)} WeChat process(es) for image AES key…")
        aes_key = images.find_v2_aes_key(pids, vectors)
        if aes_key is None:
            print("[!] could not recover the image AES key from memory.")
            print("    The key is only cached after you view images in WeChat. Tips:")
            print("      1. open the WeChat UI and click into a chat with photos")
            print("      2. tap 2-3 thumbnails so the keys get loaded into the process")
            print("      3. immediately re-run  wxextract images …")
            return 3
        log.info(f"recovered image AES key: {aes_key!r} (cross-validated against {len(vectors)} files)")
        _save_cached_image_key(workspace, aes_key, xor_key)
        log.info(f"saved → workspace/{_IMG_KEY_CACHE}")

    # ── walk + decrypt ────────────────────────────────────────────────────
    recs = load_contacts(plain)
    if args.alias:
        c = find_by_alias(recs, args.alias)
        if c is None:
            print(f"[!] alias {args.alias!r} not found")
            return 2
        targets = [c]
    else:
        targets = recs
    base_out = (Path(args.out_dir).expanduser().resolve()
                if args.out_dir else workspace / "media")
    n_v1 = n_v2 = n_skipped = n_failed = 0
    for c in targets:
        conv_hash = c.md5_table().removeprefix("Msg_")
        dst_root = base_out / (c.alias or c.username or "unknown")
        any_for_contact = False
        for dat in images.walk_dat_files(d.account_dir, conv_hash):
            if not args.include_thumbs and dat.stem.endswith("_t"):
                continue
            any_for_contact = True
            is_v2 = images.is_v2(dat)
            try:
                if is_v2:
                    result = images.decrypt_v2(dat, aes_key, xor_key=xor_key)
                else:
                    result = images.decrypt_v1(dat)
            except Exception as e:
                log.error(f"  fail {dat.name}: {e}")
                n_failed += 1
                continue
            if result is None:
                if is_v2:
                    n_skipped += 1  # V2 decrypt failed → wrong key or different XOR
                else:
                    n_failed += 1
                continue
            data, fmt = result
            rel = dat.relative_to(d.account_dir / "msg" / "attach" / conv_hash)
            dst = (dst_root / rel).with_suffix("." + fmt)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            if is_v2:
                n_v2 += 1
            else:
                n_v1 += 1
        if any_for_contact:
            log.info(f"  {c.display_name} → {dst_root}")
    print(f"images: v1={n_v1}  v2={n_v2}  failed={n_failed}  v2-skipped(decrypt-fail)={n_skipped}")
    if n_v2 == 0 and n_skipped > 0:
        print("       (no V2 images decrypted successfully — try --force-image-key to re-scan)")
    return 0


def _cmd_cleanup(args, workspace: Path, log) -> int:
    """Selectively wipe workspace + XDG cache."""
    import shutil

    def _dir_size(p: Path) -> int:
        try:
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        except OSError:
            return 0

    def _human(n: int) -> str:
        f = float(n)
        for unit in ("B", "KB", "MB", "GB"):
            if f < 1024:
                return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
            f /= 1024
        return f"{f:.1f} TB"

    # XDG cache for update-check
    from wxextract.update_check import _cache_dir as _xdg_cache_dir
    xdg_cache = _xdg_cache_dir()

    # If no category flag was given, default to --all
    any_category = any(getattr(args, f, False) for f in
                       ("all", "snapshot", "plain_dbs", "output",
                        "media", "keys", "state", "cache"))
    if not any_category:
        args.all = True

    do_all = getattr(args, "all", False)
    plan: list[tuple[str, Path, str]] = []   # (category, path, why)

    def _consider(category: str, path: Path, desc: str, requested: bool):
        if (do_all or requested) and path.exists():
            plan.append((category, path, desc))

    _consider("snapshot",  workspace / "snapshot",            "encrypted DB snapshot tree",  args.snapshot)
    _consider("plain_dbs", workspace / "plain_dbs",           "decrypted SQLite DBs",        args.plain_dbs)
    _consider("output",    workspace / "output",              "rendered conversation files", args.output)
    _consider("media",     workspace / "media",               "decrypted images",            args.media)
    _consider("keys",      workspace / "all_keys.json",       "SQLCipher DB keys",           args.keys)
    _consider("keys",      workspace / "image_key.json",      "V2 image AES key",            args.keys)
    _consider("state",     workspace / "last_extract.json",   "per-contact --since-last baselines", args.state)
    _consider("state",     workspace / "snapshot_stats.json", "drift-detection baseline",    args.state)
    _consider("cache",     xdg_cache,                          "XDG cache (update check)",    args.cache)

    if not plan:
        print("Nothing to clean up — workspace and cache are already empty.")
        return 0

    # render plan
    from rich.box import ROUNDED
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    cons = Console()
    t = Table(box=ROUNDED, show_header=True, header_style="bold cyan")
    t.add_column("Category", style="bold")
    t.add_column("Path")
    t.add_column("Size", justify="right", style="yellow")
    t.add_column("Description", style="dim italic")
    total = 0
    for category, path, desc in plan:
        sz = _dir_size(path) if path.is_dir() else path.stat().st_size
        total += sz
        t.add_row(category, str(path), _human(sz), desc)
    t.add_section()
    t.add_row(Text("TOTAL", style="bold red"),
              "", Text(_human(total), style="bold red"), "")

    cons.print()
    cons.print(Panel(t,
                     title=Text("wxextract cleanup — plan",
                                style="bold red" if not args.dry_run else "bold yellow"),
                     border_style="red" if not args.dry_run else "yellow",
                     expand=False, padding=(1, 2)))

    if args.dry_run:
        print("Dry run only — nothing deleted. Re-run without --dry-run to apply.")
        return 0

    if not args.yes:
        try:
            from rich.prompt import Confirm
            if not Confirm.ask(
                f"\n[bold red]Delete {len(plan)} item(s) ({_human(total)})?[/bold red]",
                default=False, console=cons,
            ):
                print("Aborted.")
                return 1
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            return 1

    # actually delete
    n_files = n_dirs = 0
    for _category, path, _desc in plan:
        try:
            if path.is_dir():
                shutil.rmtree(path)
                n_dirs += 1
                log.info(f"  removed {path}/")
            else:
                path.unlink()
                n_files += 1
                log.info(f"  removed {path}")
        except OSError as e:
            log.error(f"  failed to remove {path}: {e}")
    print(f"Cleanup complete: removed {n_dirs} director{'y' if n_dirs == 1 else 'ies'} "
          f"and {n_files} file{'s' if n_files != 1 else ''} "
          f"({_human(total)} total).")
    return 0


def _resolve_report_out(out_str: str | None, workspace: Path) -> Path:
    """Resolve --out into a concrete .html file path.

    If unset → <workspace>/output/report.html.
    If ends with `.html` → use as file.
    Otherwise → treat as directory, append report.html, mkdir -p.
    """
    if not out_str:
        out = workspace / "output" / "report.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        return out
    p = Path(out_str).expanduser()
    if p.suffix.lower() == ".html":
        p.parent.mkdir(parents=True, exist_ok=True)
        return p.resolve()
    # directory mode
    p.mkdir(parents=True, exist_ok=True)
    return (p / "report.html").resolve()


def _open_in_browser(path: Path) -> None:
    import subprocess
    import webbrowser
    try:
        webbrowser.open(path.as_uri())
    except Exception:
        subprocess.Popen(["xdg-open", str(path)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _cmd_stats(args, workspace: Path, log) -> int:
    """Print conversation analytics for one contact (terminal), or render an HTML report.

    Supports WeChat contacts (default), WhatsApp JSON files (via
    --whatsapp-json, repeatable), and a merged "same person" view across
    sources (via --whatsapp-merge NAME=ALIAS).
    """
    import shutil as _sh

    from rich.console import Console

    from wxextract import report as _report
    from wxextract import stats as _stats
    from wxextract import whatsapp as _wa
    from wxextract.contacts import find_by_alias, load_contacts
    from wxextract.messages import extract

    # ── Validate --whatsapp-merge spec ──────────────────────────────
    merge_specs: list[tuple[str, str]] = []
    for spec in getattr(args, "whatsapp_merge", []) or []:
        if "=" not in spec:
            print(f"[!] --whatsapp-merge expects NAME=ALIAS, got {spec!r}")
            return 2
        name, alias = spec.split("=", 1)
        merge_specs.append((name.strip(), alias.strip()))

    whatsapp_paths = list(getattr(args, "whatsapp_json", []) or [])
    whatsapp_only = bool(getattr(args, "whatsapp_only", False))

    # ── HTML mode detection ─────────────────────────────────────────
    html_mode = (not args.alias) or args.html or bool(args.out) \
        or bool(whatsapp_paths) or whatsapp_only

    # ── Single-contact terminal mode (no WhatsApp involvement) ──────
    if not html_mode:
        plain = workspace / "plain_dbs"
        if not plain.is_dir():
            print(f"[!] no plain_dbs at {plain}. Run `wxextract run` first.")
            return 2
        recs = load_contacts(plain)
        my_wxid = _detect_my_wxid(workspace) or "wxid_unknown"
        contact = find_by_alias(recs, args.alias)
        if contact is None:
            print(f"[!] alias {args.alias!r} not found")
            hint = _suggest_alias(recs, args.alias)
            if hint:
                print(hint)
            return 2
        msgs = list(extract(contact, my_wxid=my_wxid, skip_recalls=True))
        counts = _stats.compute(msgs, contact, my_label=args.my_label, top_n=args.top)
        width = _sh.get_terminal_size((140, 24)).columns
        _stats.render(counts, contact, args.my_label, Console(width=max(width, 120)))
        return 0

    # ── HTML mode: build the (ContactRecord, list[Message]) pairs ───
    out = _resolve_report_out(args.out, workspace)
    recs: list = []
    my_wxid = "wxid_unknown"
    if not whatsapp_only:
        plain = workspace / "plain_dbs"
        if not plain.is_dir():
            print(f"[!] no plain_dbs at {plain}. "
                  f"Run `wxextract run` first, or pass --whatsapp-only to skip WeChat.")
            return 2
        recs = load_contacts(plain)
        my_wxid = _detect_my_wxid(workspace) or "wxid_unknown"

    # Which WeChat contacts to include
    aliases = ([a.strip() for a in args.alias.split(",") if a.strip()]
               if args.alias else None)
    if aliases is None:
        wechat_targets = [r for r in recs if (r.message_count or 0) >= args.min_messages]
        wechat_targets.sort(key=lambda r: -(r.message_count or 0))
    else:
        wechat_targets = []
        for a in aliases:
            r = find_by_alias(recs, a)
            if r is None:
                print(f"[!] alias {a!r} not found, skipping")
            else:
                wechat_targets.append(r)

    def _log(msg: str):
        if hasattr(log, "info"):
            log.info(msg)
        elif callable(log):
            log(msg)
        else:
            print(msg)

    # WeChat pairs (ContactRecord + raw messages)
    wechat_pairs: list[tuple] = []
    for r in wechat_targets:
        _log(f"[stats] WeChat {r.display_name} ({r.alias or r.username})…")
        try:
            msgs = list(extract(r, my_wxid=my_wxid, skip_recalls=True))
        except Exception as e:
            _log(f"[stats]   skipped ({e!s})")
            continue
        if not msgs:
            continue
        wechat_pairs.append((r, msgs))

    # WhatsApp pairs
    whatsapp_pairs: list[tuple] = []
    for p in whatsapp_paths:
        wp = Path(p).expanduser()
        if not wp.is_file():
            print(f"[!] WhatsApp JSON not found: {wp}")
            return 2
        try:
            wa_contact, wa_msgs, wa_my_label = _wa.load_whatsapp_json(wp)
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            print(f"[!] failed to load {wp}: {e}")
            return 2
        _log(f"[stats] WhatsApp {wa_contact.display_name} "
             f"({wa_my_label}'s side) — {len(wa_msgs):,} msgs")
        whatsapp_pairs.append((wa_contact, wa_msgs))

    # Apply merges: for each NAME=ALIAS spec, pull the matching pair out
    # of each list and build a combined synthetic pair. Section order:
    # combined → WhatsApp-only → WeChat-only (consecutive, per merge).
    merged_groups: list[list[tuple]] = []   # each inner list is a section group
    unmerged_wa = list(whatsapp_pairs)
    unmerged_we = list(wechat_pairs)
    for name, alias in merge_specs:
        wa_match = next(
            (pr for pr in unmerged_wa
             if name.lower() in (pr[0].display_name or "").lower()
             or name.lower() in (pr[0].alias or "").lower()),
            None,
        )
        we_match = next(
            (pr for pr in unmerged_we
             if alias.lower() in (pr[0].alias or "").lower()
             or alias.lower() in (pr[0].username or "").lower()),
            None,
        )
        if wa_match is None or we_match is None:
            print(f"[!] --whatsapp-merge {name}={alias}: "
                  f"{'WhatsApp side ' if wa_match is None else ''}"
                  f"{'WeChat side ' if we_match is None else ''}"
                  f"not found, skipping merge.")
            continue
        unmerged_wa.remove(wa_match)
        unmerged_we.remove(we_match)
        combined_pair = _wa.build_combined(
            wa_match, we_match,
            display_name=we_match[0].display_name,
            alias=f"{alias}_combined",
        )
        merged_groups.append([combined_pair, wa_match, we_match])

    # Compute Counts for each pair (using a single my_label so the
    # by_sender counter is consistent across sources)
    def _to_counts(pair):
        r, msgs = pair
        c = _stats.compute(msgs, r, my_label=args.my_label, top_n=args.top)
        return (r, c)

    final_pairs: list[tuple] = []
    for grp in merged_groups:
        for p in grp:
            cp = _to_counts(p)
            if cp[1].total > 0:
                final_pairs.append(cp)
    for p in unmerged_wa:
        cp = _to_counts(p)
        if cp[1].total > 0:
            final_pairs.append(cp)
    for p in unmerged_we:
        cp = _to_counts(p)
        if cp[1].total > 0:
            final_pairs.append(cp)

    if not final_pairs:
        print("[!] no data to render — check --alias / --whatsapp-json / --min-messages.")
        return 2

    n = _report.render_report(final_pairs,
                              my_label=args.my_label,
                              out_path=out)
    print(f"[+] wrote HTML report for {n} contact section(s) → {out}")
    if args.open:
        _open_in_browser(out)
    return 0


def _cmd_preview(args, workspace: Path, log) -> int:
    """Print the most-recent N messages, no files written."""
    from wxextract.contacts import find_by_alias, load_contacts
    from wxextract.messages import extract
    from wxextract.render.common import body_of
    plain = workspace / "plain_dbs"
    if not plain.is_dir():
        print(f"[!] no plain_dbs at {plain}. Run `wxextract run` first.")
        return 2
    recs = load_contacts(plain)
    contact = find_by_alias(recs, args.alias)
    if contact is None:
        print(f"[!] alias {args.alias!r} not found")
        hint = _suggest_alias(recs, args.alias)
        if hint:
            print(hint)
        return 2
    my_wxid = _detect_my_wxid(workspace) or "wxid_unknown"
    msgs = list(extract(contact, my_wxid=my_wxid))
    if not msgs:
        print(f"(no messages with {contact.display_name})")
        return 0
    tail = msgs[-args.tail:]
    me_label = args.my_label
    other_label = contact.display_name
    print(f"# {other_label} ({contact.alias or contact.username}) — last {len(tail)} of {len(msgs)} messages")
    print()
    for m in tail:
        from datetime import datetime as _dt
        dt = _dt.fromtimestamp(m.create_time).strftime("%Y-%m-%d %H:%M:%S")
        who = me_label if m.is_me else other_label
        body = body_of(m).text or ""
        print(f"[{dt}] {who}: {body}")
    return 0


# ---------------------------------------------------------------------------
# run — the whole pipeline
# ---------------------------------------------------------------------------


def _account_dir_arg(args) -> Path | None:
    v = getattr(args, "account_dir", None)
    return Path(v).expanduser().resolve() if v else None


def _cmd_run(args, workspace: Path, log, ui=None) -> int:
    rc = _cmd_resnap(workspace,
                     force=getattr(args, "force", False),
                     no_relaunch=getattr(args, "no_relaunch", False),
                     account_dir=_account_dir_arg(args),
                     ui=ui)
    if rc != 0:
        return rc
    return _cmd_render(args, workspace, log, ui=ui)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _detect_my_wxid(workspace: Path) -> str | None:
    """From the snapshot folder name (wxid_xxx_yyyy → wxid_xxx)."""
    snap = _snapshot_account_dir(workspace)
    if snap is None:
        return None
    name = snap.name
    return name.rsplit("_", 1)[0] if name.count("_") >= 2 else name


def _render_and_chunk(args, msgs, contact, my_wxid: str, out_dir: Path,
                      ui=None, name_suffix: str = "") -> tuple[int, list[tuple[str, Path]]]:
    """Render every requested format (+chunk). Returns (exit_code, [(fmt, path)])."""
    from wxextract.chunker import chunk_by_tokens, chunk_calendar
    from wxextract.render import compact_txt, jsonl, markdown, pseudo_xml
    formats = [f.strip() for f in args.format.split(",") if f.strip()]
    base_name = (contact.alias or contact.username) + name_suffix
    render_map = {
        "txt-b": (compact_txt, "txt"),
        "jsonl": (jsonl, "jsonl"),
        "xml": (pseudo_xml, "xml"),
        "md": (markdown, "md"),
    }
    chunk_arg = args.chunk
    chunk_kind = "none"
    chunk_tokens = 0
    if chunk_arg.startswith("tokens:"):
        chunk_kind = "tokens"
        chunk_tokens = int(chunk_arg.split(":", 1)[1])
    elif chunk_arg in ("month", "week", "day"):
        chunk_kind = chunk_arg

    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[tuple[str, Path]] = []

    if ui:
        ui.begin("Render", f"{len(formats)} format(s): {', '.join(formats)}")

    for fmt in formats:
        if fmt not in render_map:
            print(f"[!] unknown format {fmt!r}; choose from {sorted(render_map)}")
            continue
        if ui:
            ui.detail("Render", f"writing {fmt}…")
        mod, ext = render_map[fmt]
        out_path = out_dir / f"{base_name}.{ext}"

        common_kw = dict(
            my_label=args.my_label,
            squash=getattr(args, "squash_emoji", False),
            redact=getattr(args, "redact", False),
            stickers_to_emoji=getattr(args, "sticker_emojis", False),
        )
        if fmt == "txt-b":
            render_kwargs = dict(
                common_kw,
                gap_seconds=args.gap,
                turn_merge=not args.no_turn_merge,
                time_precision=getattr(args, "time_precision", "seconds"),
                reply_preview=getattr(args, "reply_preview", "full"),
            )
        elif fmt == "xml":
            render_kwargs = dict(common_kw, gap_seconds=args.gap)
        elif fmt == "md":
            render_kwargs = dict(common_kw, gap_seconds=args.gap)
        else:                                              # jsonl
            render_kwargs = dict(common_kw)

        if chunk_kind in ("month", "week", "day"):
            paths = chunk_calendar(msgs, contact, my_wxid, mod.render, out_path,
                                   chunk_kind, **render_kwargs)
        else:
            mod.render(msgs, contact, my_wxid, out_path, **render_kwargs)
            paths = [out_path]
            if chunk_kind == "tokens":
                paths = chunk_by_tokens(out_path, chunk_tokens, fmt=fmt)
                try:
                    out_path.unlink()
                except FileNotFoundError:
                    pass
        for p in paths:
            outputs.append((fmt, p))

    if ui:
        ui.end("Render", f"{len(outputs)} file(s) written")
        if chunk_kind != "none":
            ui.end("Chunk", f"chunked by {chunk_kind} → {len(outputs)} parts across {len(formats)} formats")
        else:
            ui.skip("Chunk", "no chunking requested")
    return 0, outputs


def _print_summary(contact, msgs, recall_count, outputs, workspace, ui):
    """Compute per-file stats and print the final summary panel."""
    import shutil as _sh
    from datetime import datetime

    from rich.console import Console

    from wxextract.progress import file_stats, render_summary

    if ui:
        ui.stop()

    stats = [file_stats(p, fmt) for fmt, p in outputs]

    first = datetime.fromtimestamp(msgs[0].create_time).date()
    last = datetime.fromtimestamp(msgs[-1].create_time).date()
    total_days = (last - first).days + 1

    total_time = (time.perf_counter() - ui.t0) if ui else 0.0
    # use the real terminal width when available, otherwise pick something
    # wide enough that the table doesn't wrap when output is piped.
    width = _sh.get_terminal_size((140, 24)).columns
    render_summary(
        Console(width=max(width, 120)),
        contact=contact,
        message_count=len(msgs),
        recall_count=recall_count,
        date_range=(first.isoformat(), last.isoformat()),
        total_days=total_days,
        total_time=total_time,
        outputs=stats,
        workspace=workspace,
    )


if __name__ == "__main__":
    sys.exit(main())
