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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wxextract",
        description="Extract WeChat 4.x conversations on Linux into compact LLM-ready text.",
    )
    p.add_argument("--version", action="version", version=f"wxextract {__version__}")
    p.add_argument("--workspace", type=str, default=None, metavar="PATH",
                   help="working directory for snapshot/plain_dbs/output (default: <project>/workspace/)")
    p.add_argument("-v", "--verbose", action="store_true", default=True)
    p.add_argument("-q", "--quiet", action="store_true")

    sub = p.add_subparsers(dest="command", required=False)
    sub.add_parser("status", help="print discovered WeChat install + workspace state, exit")
    sub.add_parser("list", help="print contacts table, exit (requires prior snapshot/decrypt)")
    rs = sub.add_parser("resnap", help="close WeChat, refresh snapshot + decrypt, re-open")
    rs.add_argument("--force", action="store_true",
                    help="re-extract keys and re-decrypt even if cached versions look fresh")
    rs.add_argument("--no-relaunch", action="store_true",
                    help="don't re-launch WeChat after snapshot (avoids the login-confirm dialog)")

    for name, help_ in [
        ("run", "full pipeline (default if no subcommand)"),
        ("render", "only render an already-decrypted conversation"),
    ]:
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--alias", help="WeChat ID of the contact to extract (skips picker)")
        sp.add_argument("--format", default="txt-b,jsonl,xml",
                        help="comma-list of {txt-b,jsonl,xml} (default: all three)")
        sp.add_argument("--chunk", default="none",
                        help="none|month|week|day|tokens:N (default: none)")
        sp.add_argument("--gap", type=int, default=7200, help="session gap seconds (default: 7200)")
        sp.add_argument("--no-turn-merge", action="store_true")
        sp.add_argument("--my-label", default="Me")
        sp.add_argument("--skip-recalls", action="store_true", default=True)
        sp.add_argument("--include-recalls", action="store_true",
                        help="opposite of --skip-recalls")
        sp.add_argument("--squash-emoji", action="store_true",
                        help="collapse [Tag][Tag][Tag] → [Tag×3] for runs of ≥3")
        sp.add_argument("--redact", action="store_true",
                        help="replace emails / phones / IBANs / long digit runs with placeholders")
        if name == "run":
            sp.add_argument("--force", action="store_true",
                            help="re-extract keys and re-decrypt even if cached versions look fresh")
            sp.add_argument("--no-relaunch", action="store_true",
                            help="don't re-launch WeChat after snapshot (avoids the login-confirm dialog)")
    return p


_RUN_FLAGS = {
    "--alias", "--format", "--chunk", "--gap", "--no-turn-merge", "--my-label",
    "--skip-recalls", "--include-recalls", "--squash-emoji", "--redact", "--force",
}


def _inject_default_subcommand(argv: list[str]) -> list[str]:
    """If user types `wxextract --alias X ...` with no subcommand, treat it as `run`.

    Detect by scanning until we hit either a known subcommand or a `--`-flag
    that belongs to `run`/`render`. Top-level flags (`--workspace`, `-v`, `-q`)
    are passed through unchanged.
    """
    top_level = {"--workspace", "-v", "--verbose", "-q", "--quiet"}
    subs = {"status", "list", "resnap", "run", "render"}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in subs:
            return argv                   # already has subcommand
        if a in top_level:
            i += 2 if a == "--workspace" else 1
            continue
        if a.startswith("--workspace="):
            i += 1
            continue
        if a.startswith("-"):
            # any other flag → must be a run/render flag; inject `run`
            head = argv[:i]
            tail = argv[i:]
            if any(t.split("=", 1)[0] in _RUN_FLAGS for t in tail):
                return head + ["run"] + tail
            return argv
        # bare positional with no subcommand — let argparse complain
        return argv
    return argv


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw = sys.argv[1:] if argv is None else list(argv)
    raw = _inject_default_subcommand(raw)
    args = parser.parse_args(raw)
    log = setup_logging(verbose=args.verbose, quiet=args.quiet)
    workspace = (Path(args.workspace).expanduser().resolve()
                 if args.workspace else default_workspace())
    cmd = args.command or "run"

    if cmd == "status":
        return _cmd_status(workspace)
    if cmd == "list":
        return _cmd_list(workspace)
    if cmd == "resnap":
        return _cmd_resnap(workspace, force=getattr(args, "force", False),
                           no_relaunch=getattr(args, "no_relaunch", False))
    if cmd == "render":
        return _cmd_render(args, workspace, log)
    if cmd == "run":
        return _cmd_run(args, workspace, log)
    parser.print_help()
    return 1


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def _cmd_status(workspace: Path) -> int:
    from wxextract import discover, lifecycle
    print(f"wxextract {__version__}")
    print(f"workspace        : {workspace}")
    try:
        d = discover.discover()
        print(f"install version  : {d.install_version or '<not via pacman>'}")
        print(f"binary           : {d.binary_path or '<not found>'}")
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


def _cmd_resnap(workspace: Path, force: bool = False, no_relaunch: bool = False) -> int:
    from wxextract import discover, lifecycle, snapshot
    from wxextract.decrypt import decrypt_all
    from wxextract.keys import collect_dbs, load_keys, save_keys, scan
    log = setup_logging(verbose=True)
    d = discover.discover()
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
        log.info("snapshot is fresh — nothing to do (no close/snap/decrypt needed)")
        return 0

    # ── KEY CACHE CHECK ────────────────────────────────────────────────────────
    cached = None if force else _cached_keys_still_valid(keys_path, d.db_storage())
    if cached is not None:
        log.info(f"keys: reusing {len(cached)} cached keys from {keys_path.name} (validated against live DBs)")
        keys_by_rel = cached
    else:
        # need WeChat running to scan memory
        if not lifecycle.wechat_running():
            log.info("wechat not running — launching for key extraction")
            lifecycle.launch_wechat()
            time.sleep(8)
        pids = []
        mp = lifecycle.main_wechat_pid()
        if mp:
            pids.append(mp)
        pids += [p for p in lifecycle.wechat_running() if p != mp]
        scan_res = scan(pids, d.db_storage())
        log.info(f"keys: {len(scan_res.keys_by_rel)} / {len(scan_res.salt_to_rels)} via memory scan in {scan_res.elapsed:.2f}s")
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

    # ── CLOSE → SNAPSHOT → DECRYPT ────────────────────────────────────────────
    was_running = bool(lifecycle.wechat_running())
    if was_running:
        if not lifecycle.close_wechat():
            log.warning("wechat did not close cleanly within 10s; continuing anyway")
    snap_acct = snapshot.snapshot(d.account_dir, workspace / "snapshot")
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
    if was_running and not no_relaunch:
        lifecycle.launch_wechat()
        log.info("(WeChat may show a login confirmation dialog — click 'Open WeChat' to resume)")
    elif was_running and no_relaunch:
        log.info("(skipping WeChat re-launch as requested; start it yourself when ready)")
    log.info("resnap complete")
    return 0 if n_failed == 0 else 4


# ---------------------------------------------------------------------------
# render — assumes plain_dbs already present
# ---------------------------------------------------------------------------


def _cmd_render(args, workspace: Path, log) -> int:
    from wxextract.contacts import find_by_alias, load_contacts
    from wxextract.messages import extract
    from wxextract.picker import pick
    plain = workspace / "plain_dbs"
    if not plain.is_dir():
        print(f"[!] no plain_dbs at {plain}. Run `wxextract run` first.")
        return 2
    recs = load_contacts(plain)
    contact = None
    if args.alias:
        contact = find_by_alias(recs, args.alias)
        if contact is None:
            print(f"[!] alias {args.alias!r} not found")
            return 2
    else:
        contact = pick(recs, Console())
        if contact is None:
            return 0
    my_wxid = _detect_my_wxid(workspace) or "wxid_unknown"
    skip = args.skip_recalls and not args.include_recalls
    msgs = list(extract(contact, my_wxid=my_wxid, skip_recalls=skip))
    log.info(f"extracted {len(msgs)} messages")
    out_dir = workspace / "output"
    return _render_and_chunk(args, msgs, contact, my_wxid, out_dir)


# ---------------------------------------------------------------------------
# run — the whole pipeline
# ---------------------------------------------------------------------------


def _cmd_run(args, workspace: Path, log) -> int:
    rc = _cmd_resnap(workspace,
                     force=getattr(args, "force", False),
                     no_relaunch=getattr(args, "no_relaunch", False))
    if rc != 0:
        return rc
    return _cmd_render(args, workspace, log)


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


def _render_and_chunk(args, msgs, contact, my_wxid: str, out_dir: Path) -> int:
    from wxextract.chunker import chunk_by_tokens, chunk_calendar
    from wxextract.render import compact_txt, jsonl, pseudo_xml
    formats = [f.strip() for f in args.format.split(",") if f.strip()]
    base_name = contact.alias or contact.username
    render_map = {
        "txt-b": (compact_txt, "txt"),
        "jsonl": (jsonl, "jsonl"),
        "xml": (pseudo_xml, "xml"),
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
    summary: list[tuple[str, int, int, int]] = []   # (path, bytes, lines, tokens)
    for fmt in formats:
        if fmt not in render_map:
            print(f"[!] unknown format {fmt!r}; choose from {sorted(render_map)}")
            continue
        mod, ext = render_map[fmt]
        out_path = out_dir / f"{base_name}.{ext}"

        # render
        render_kwargs = {}
        common_kw = dict(
            my_label=args.my_label,
            squash=getattr(args, "squash_emoji", False),
            redact=getattr(args, "redact", False),
        )
        if fmt == "txt-b":
            render_kwargs = dict(common_kw, gap_seconds=args.gap,
                                 turn_merge=not args.no_turn_merge)
        elif fmt == "xml":
            render_kwargs = dict(common_kw, gap_seconds=args.gap)
        elif fmt == "jsonl":
            render_kwargs = dict(common_kw)

        if chunk_kind in ("month", "week", "day"):
            paths = chunk_calendar(msgs, contact, my_wxid, mod.render, out_path,
                                   chunk_kind, **render_kwargs)
        else:
            if fmt == "jsonl":
                mod.render(msgs, contact, my_wxid, out_path, **render_kwargs)
            else:
                mod.render(msgs, contact, my_wxid, out_path, **render_kwargs)
            paths = [out_path]
            if chunk_kind == "tokens":
                paths = chunk_by_tokens(out_path, chunk_tokens, fmt=fmt)
                try:
                    out_path.unlink()
                except FileNotFoundError:
                    pass

        from wxextract.tokens import count as count_tokens
        for p in paths:
            text = p.read_text(encoding="utf-8")
            sz = p.stat().st_size
            ln = text.count("\n")
            tk = count_tokens(text)
            summary.append((str(p), sz, ln, tk))

    print()
    print("Output:")
    for path, sz, ln, tk in summary:
        print(f"  {path:60s}  {human_bytes(sz):>10s}  {ln:>7,d} lines  {tk:>10,d} tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
