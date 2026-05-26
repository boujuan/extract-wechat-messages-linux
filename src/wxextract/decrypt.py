"""SQLCipher 4 → plain SQLite decryption.

Two engines:

  - **sqlcipher CLI** (preferred when the `sqlcipher` binary is on PATH):
    uses ``sqlcipher_export()`` which copies live data via SQLite's own
    APIs, correctly replaying any pending WAL frames. This is what the
    L1en2407 baseline does and is the canonical way.

  - **pure-Python AES-256-CBC** (fallback): reads encrypted .db pages
    directly and writes the decrypted SQLite file. Fast, no shell deps,
    but **does not replay WAL** — if the snapshot has un-checkpointed
    writes in `.db-wal`, those rows won't appear in the output.

`decrypt_file()` auto-selects: tries sqlcipher CLI first, falls back to
pure-Python with a warning. Override via the `engine=` kwarg.

Page layout (pure-Python path):
  - Page 1: [salt(16)] [encrypted_payload(4000)] [iv(16)] [hmac(64)]
  - Pages 2+: [encrypted_payload(4016)] [iv(16)] [hmac(64)]

Parallelism: one worker per DB via ProcessPoolExecutor (CPU-bound AES).
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from Crypto.Cipher import AES

from wxextract.keys import (
    PAGE_SZ,
    RESERVE_SZ,
    SALT_SZ,
)

log = logging.getLogger("wxextract.decrypt")

IV_SZ = 16
SQLITE_HDR = b"SQLite format 3\x00"


@dataclass
class DecryptResult:
    rel: str
    out_path: Path
    pages: int
    ok: bool
    error: str | None = None
    engine: str = ""              # "sqlcipher" | "python" | "" (skipped)


# ---------------------------------------------------------------------------
# sqlcipher CLI engine — replays WAL via sqlcipher_export()
# ---------------------------------------------------------------------------


def have_sqlcipher_cli() -> bool:
    return shutil.which("sqlcipher") is not None


def decrypt_via_cli(src: Path, dst: Path, enc_key: bytes,
                    timeout: float = 60.0) -> DecryptResult:
    """Use the `sqlcipher` CLI's sqlcipher_export() to copy the live state
    (main + WAL replayed) into a plain SQLite file at `dst`."""
    rel = dst.name
    if not have_sqlcipher_cli():
        return DecryptResult(rel=rel, out_path=dst, pages=0, ok=False,
                             error="sqlcipher binary not on PATH", engine="sqlcipher")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        # remove any existing target so sqlcipher_export creates it fresh
        try:
            dst.unlink()
        except FileNotFoundError:
            pass
        sql = (
            f"PRAGMA key = \"x'{enc_key.hex()}'\";\n"
            "PRAGMA cipher_compatibility = 4;\n"
            f"ATTACH DATABASE '{dst}' AS plain KEY '';\n"
            "SELECT sqlcipher_export('plain');\n"
            "DETACH DATABASE plain;\n"
        )
        res = subprocess.run(
            ["sqlcipher", str(src)],
            input=sql, text=True,
            capture_output=True, timeout=timeout,
        )
        if res.returncode != 0:
            err = (res.stderr or res.stdout or "").strip()[:200]
            return DecryptResult(rel=rel, out_path=dst, pages=0, ok=False,
                                 error=f"sqlcipher exit {res.returncode}: {err}",
                                 engine="sqlcipher")
        if not dst.is_file() or dst.stat().st_size < PAGE_SZ:
            return DecryptResult(rel=rel, out_path=dst, pages=0, ok=False,
                                 error="sqlcipher produced empty / missing output",
                                 engine="sqlcipher")
        # page count after export = file_size / page_size (typically 4096)
        pages = dst.stat().st_size // PAGE_SZ
        return DecryptResult(rel=rel, out_path=dst, pages=pages, ok=True,
                             engine="sqlcipher")
    except subprocess.TimeoutExpired:
        return DecryptResult(rel=rel, out_path=dst, pages=0, ok=False,
                             error=f"sqlcipher timeout >{timeout}s", engine="sqlcipher")
    except Exception as exc:
        return DecryptResult(rel=rel, out_path=dst, pages=0, ok=False,
                             error=f"{type(exc).__name__}: {exc}", engine="sqlcipher")


# ---------------------------------------------------------------------------
# Pure-Python engine — fast, no WAL replay
# ---------------------------------------------------------------------------


def _decrypt_page(enc_key: bytes, page: bytes, pgno: int) -> bytes:
    iv = page[PAGE_SZ - RESERVE_SZ : PAGE_SZ - RESERVE_SZ + IV_SZ]
    if pgno == 1:
        encrypted = page[SALT_SZ : PAGE_SZ - RESERVE_SZ]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        return SQLITE_HDR + cipher.decrypt(encrypted) + b"\x00" * RESERVE_SZ
    encrypted = page[: PAGE_SZ - RESERVE_SZ]
    cipher = AES.new(enc_key, AES.MODE_CBC, iv)
    return cipher.decrypt(encrypted) + b"\x00" * RESERVE_SZ


def decrypt_via_python(src: Path, dst: Path, enc_key: bytes) -> DecryptResult:
    """Pure-Python AES-CBC per-page decrypt. Skips WAL frames."""
    rel = dst.name
    try:
        size = src.stat().st_size
        if size < PAGE_SZ:
            return DecryptResult(rel=rel, out_path=dst, pages=0, ok=False,
                                 error="file too small", engine="python")
        with open(src, "rb") as f:
            page1 = f.read(PAGE_SZ)
        from wxextract.keys import verify_enc_key
        if not verify_enc_key(enc_key, page1):
            return DecryptResult(rel=rel, out_path=dst, pages=0, ok=False,
                                 error="page1 HMAC mismatch", engine="python")
        total_pages = (size + PAGE_SZ - 1) // PAGE_SZ
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(src, "rb") as fin, open(dst, "wb") as fout:
            for pgno in range(1, total_pages + 1):
                page = fin.read(PAGE_SZ)
                if not page:
                    break
                if len(page) < PAGE_SZ:
                    page += b"\x00" * (PAGE_SZ - len(page))
                fout.write(_decrypt_page(enc_key, page, pgno))
        return DecryptResult(rel=rel, out_path=dst, pages=total_pages, ok=True,
                             engine="python")
    except Exception as exc:
        return DecryptResult(rel=rel, out_path=dst, pages=0, ok=False,
                             error=str(exc), engine="python")


def decrypt_file(src: Path, dst: Path, enc_key: bytes,
                 engine: str = "auto") -> DecryptResult:
    """Decrypt one DB. engine ∈ {auto, sqlcipher, python}.

    `auto` (default): use sqlcipher CLI when available (correctly replays
    WAL frames); otherwise fall back to pure-Python AES-CBC.
    """
    if engine == "auto":
        engine = "sqlcipher" if have_sqlcipher_cli() else "python"
    if engine == "sqlcipher":
        return decrypt_via_cli(src, dst, enc_key)
    if engine == "python":
        return decrypt_via_python(src, dst, enc_key)
    raise ValueError(f"unknown engine {engine!r}; expected auto|sqlcipher|python")


def integrity_check(db_path: Path) -> str:
    """Return 'ok' if SQLite says so, else the error text."""
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return row[0] if row else "no result"
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        return f"DatabaseError: {e}"


def _is_fresh(src: Path, dst: Path) -> bool:
    """True if dst exists and is newer than src (incremental skip)."""
    try:
        return dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime
    except OSError:
        return False


# top-level worker so ProcessPoolExecutor can pickle it
def _worker(args: tuple[str, str, str, str]) -> DecryptResult:
    src_s, dst_s, key_hex, engine = args
    return decrypt_file(Path(src_s), Path(dst_s), bytes.fromhex(key_hex), engine=engine)


def decrypt_all(
    db_storage: Path,
    keys_by_rel: dict[str, str],
    out_dir: Path,
    *,
    workers: int | None = None,
    skip_unchanged: bool = True,
    engine: str = "auto",
) -> list[DecryptResult]:
    """Decrypt every keyed DB under db_storage into out_dir.

    engine ∈ {auto, sqlcipher, python}. auto picks sqlcipher CLI when
    available so WAL frames are replayed; falls back to pure-Python.

    Returns one DecryptResult per attempted file (including skipped ones).
    """
    if engine == "auto":
        engine = "sqlcipher" if have_sqlcipher_cli() else "python"
        if engine == "python":
            log.warning("sqlcipher CLI not found on PATH — using pure-Python decrypt "
                        "(may miss WAL frames). Install sqlcipher for WAL-correct output.")
    workers = workers or os.cpu_count() or 4
    jobs: list[tuple[str, str, str, str]] = []
    skipped: list[DecryptResult] = []
    for rel, key_hex in keys_by_rel.items():
        src = db_storage / rel
        dst = out_dir / rel
        if not src.exists():
            log.warning(f"keys had {rel} but file missing under {db_storage}")
            continue
        if skip_unchanged and _is_fresh(src, dst):
            skipped.append(DecryptResult(rel=rel, out_path=dst, pages=0,
                                         ok=True, error=None, engine="skipped"))
            continue
        jobs.append((str(src), str(dst), key_hex, engine))
    log.info(f"decrypt: {len(jobs)} db files via {engine} "
             f"(skipped {len(skipped)} unchanged), {workers} workers")
    results: list[DecryptResult] = list(skipped)
    if jobs:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_worker, j): j[0] for j in jobs}
            for fut in as_completed(futures):
                res = fut.result()
                if res.ok:
                    log.info(f"  OK  ({res.engine:9s}) {res.rel:40s}  {res.pages} pages")
                else:
                    log.error(f"  FAIL ({res.engine:9s}) {res.rel:40s}  {res.error}")
                results.append(res)
    return results
