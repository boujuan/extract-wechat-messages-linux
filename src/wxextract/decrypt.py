"""Per-page AES-256-CBC decryption of SQLCipher 4 databases.

Each encrypted SQLite page is 4096 bytes split as:
  - Page 1: [salt(16)] [encrypted_payload(4000)] [iv(16)] [hmac(64)]
  - Pages 2+: [encrypted_payload(4016)] [iv(16)] [hmac(64)]

After decryption we replace the salt on page 1 with the SQLite magic header
('SQLite format 3\x00') and zero-pad the trailing 80 bytes — yielding a
plain SQLite file readable by any sqlite3 client.

Parallelism: one worker per DB via ProcessPoolExecutor (CPU-bound AES).
"""
from __future__ import annotations

import logging
import os
import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from Crypto.Cipher import AES

from wxextract.keys import (
    HMAC_SZ,
    PAGE_SZ,
    RESERVE_SZ,
    SALT_SZ,
    derive_mac_key,
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


def _decrypt_page(enc_key: bytes, page: bytes, pgno: int) -> bytes:
    iv = page[PAGE_SZ - RESERVE_SZ : PAGE_SZ - RESERVE_SZ + IV_SZ]
    if pgno == 1:
        encrypted = page[SALT_SZ : PAGE_SZ - RESERVE_SZ]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        return SQLITE_HDR + cipher.decrypt(encrypted) + b"\x00" * RESERVE_SZ
    encrypted = page[: PAGE_SZ - RESERVE_SZ]
    cipher = AES.new(enc_key, AES.MODE_CBC, iv)
    return cipher.decrypt(encrypted) + b"\x00" * RESERVE_SZ


def decrypt_file(src: Path, dst: Path, enc_key: bytes) -> DecryptResult:
    """Decrypt one DB. Returns DecryptResult; does NOT raise on bad HMAC."""
    rel = dst.name
    try:
        size = src.stat().st_size
        if size < PAGE_SZ:
            return DecryptResult(rel=rel, out_path=dst, pages=0, ok=False, error="file too small")
        with open(src, "rb") as f:
            page1 = f.read(PAGE_SZ)
        # validate page 1
        from wxextract.keys import verify_enc_key
        if not verify_enc_key(enc_key, page1):
            return DecryptResult(rel=rel, out_path=dst, pages=0, ok=False, error="page1 HMAC mismatch")
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
        return DecryptResult(rel=rel, out_path=dst, pages=total_pages, ok=True)
    except Exception as exc:
        return DecryptResult(rel=rel, out_path=dst, pages=0, ok=False, error=str(exc))


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
def _worker(args: tuple[str, str, str]) -> DecryptResult:
    src_s, dst_s, key_hex = args
    return decrypt_file(Path(src_s), Path(dst_s), bytes.fromhex(key_hex))


def decrypt_all(
    db_storage: Path,
    keys_by_rel: dict[str, str],
    out_dir: Path,
    *,
    workers: int | None = None,
    skip_unchanged: bool = True,
) -> list[DecryptResult]:
    """Decrypt every keyed DB under db_storage into out_dir.

    Returns one DecryptResult per attempted file (including skipped ones, marked ok=True).
    """
    workers = workers or os.cpu_count() or 4
    jobs: list[tuple[str, str, str]] = []
    skipped: list[DecryptResult] = []
    for rel, key_hex in keys_by_rel.items():
        src = db_storage / rel
        dst = out_dir / rel
        if not src.exists():
            log.warning(f"keys had {rel} but file missing under {db_storage}")
            continue
        if skip_unchanged and _is_fresh(src, dst):
            skipped.append(DecryptResult(rel=rel, out_path=dst, pages=0, ok=True, error=None))
            continue
        jobs.append((str(src), str(dst), key_hex))
    log.info(f"decrypt: {len(jobs)} db files (skipped {len(skipped)} unchanged), {workers} workers")
    results: list[DecryptResult] = list(skipped)
    if jobs:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_worker, j): j[0] for j in jobs}
            for fut in as_completed(futures):
                res = fut.result()
                if res.ok:
                    log.info(f"  OK  {res.rel:40s}  {res.pages} pages")
                else:
                    log.error(f"  FAIL {res.rel:40s}  {res.error}")
                results.append(res)
    return results
