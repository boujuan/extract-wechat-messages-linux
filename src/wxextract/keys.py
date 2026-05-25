"""Recover SQLCipher 4 per-DB encryption keys from a running WeChat process.

Algorithm (reimplemented from the SQLCipher 4 spec + observed WeChat behavior):

1. For every encrypted DB under db_storage/, the first 16 bytes of the file
   are the per-DB salt. WeChat (via WCDB) caches the derived raw enc_key in
   process memory, sometimes adjacent to its associated salt, in the form
   `x'<64hex_enc_key>...<32hex_salt>'`.
2. Scan /proc/<pid>/maps for readable regions belonging to the WeChat
   process. Skip vsyscall / vdso / system library mappings that aren't
   sqlcipher- or wcdb-related.
3. Within each region, regex-find the `x'...'` hex pattern. For each
   match, treat the first 64 hex chars as a candidate 32-byte key.
4. Validate the candidate by recomputing the page-1 HMAC of every DB whose
   salt is still unresolved; on match, record the key for that DB.
5. Cross-verify: try found keys against any remaining salts (some DBs
   share keys).

SQLCipher 4 page-1 HMAC verification:
    mac_salt = salt ^ (0x3a * 16)
    mac_key  = PBKDF2-HMAC-SHA512(enc_key, mac_salt, iter=2, dklen=32)
    expected = HMAC-SHA512(mac_key, page1[16:4032] + le_u32(1))
    stored   = page1[-64:]
    valid    = expected == stored
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import logging
import os
import re
import stat
import struct
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("wxextract.keys")

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
HMAC_SZ = 64
RESERVE_SZ = 80  # IV(16) + HMAC(64)

_HEX_RE = re.compile(rb"x'([0-9a-fA-F]{64,192})'")

_SKIP_MAPPINGS = {"[vdso]", "[vsyscall]", "[vvar]"}
_SKIP_PATH_PREFIXES = ("/usr/lib/", "/lib/", "/usr/share/")
_HOT_SUBSTRINGS = ("wcdb", "wechat", "weixin", "sqlcipher")


# ---------------------------------------------------------------------------
# Pure-crypto helpers (testable without /proc)
# ---------------------------------------------------------------------------


def derive_mac_key(enc_key: bytes, salt: bytes) -> bytes:
    mac_salt = bytes(b ^ 0x3A for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)


def verify_enc_key(enc_key: bytes, page1: bytes) -> bool:
    """True iff enc_key validates page 1's HMAC. page1 must be 4096 bytes."""
    if len(page1) < PAGE_SZ or len(enc_key) != KEY_SZ:
        return False
    salt = page1[:SALT_SZ]
    mac_key = derive_mac_key(enc_key, salt)
    hmac_data = page1[SALT_SZ : PAGE_SZ - RESERVE_SZ + 16]  # body + IV
    stored = page1[PAGE_SZ - HMAC_SZ : PAGE_SZ]
    mac = _hmac.new(mac_key, hmac_data, hashlib.sha512)
    mac.update(struct.pack("<I", 1))
    return _hmac.compare_digest(mac.digest(), stored)


# ---------------------------------------------------------------------------
# DB salt collection
# ---------------------------------------------------------------------------


@dataclass
class DbFile:
    rel: str           # path relative to db_storage root
    abs: Path
    size: int
    salt_hex: str
    page1: bytes


def collect_dbs(db_storage: Path) -> tuple[list[DbFile], dict[str, list[str]]]:
    """Walk db_storage/, return (db_files, salt_to_rels)."""
    db_files: list[DbFile] = []
    salt_to_rels: dict[str, list[str]] = {}
    for root, _dirs, files in os.walk(db_storage):
        for fn in files:
            if not fn.endswith(".db") or fn.endswith("-wal") or fn.endswith("-shm"):
                continue
            path = Path(root) / fn
            try:
                sz = path.stat().st_size
            except OSError:
                continue
            if sz < PAGE_SZ:
                continue
            with open(path, "rb") as f:
                p1 = f.read(PAGE_SZ)
            rel = str(path.relative_to(db_storage))
            salt_hex = p1[:SALT_SZ].hex()
            db_files.append(DbFile(rel=rel, abs=path, size=sz, salt_hex=salt_hex, page1=p1))
            salt_to_rels.setdefault(salt_hex, []).append(rel)
    return db_files, salt_to_rels


# ---------------------------------------------------------------------------
# Memory region enumeration
# ---------------------------------------------------------------------------


@dataclass
class MemRegion:
    start: int
    size: int
    name: str   # mapping name or anonymous
    hot: bool   # True if mapped from wcdb/wechat/sqlcipher (likely to hold the key)


def list_regions(pid: int) -> list[MemRegion]:
    regions: list[MemRegion] = []
    with open(f"/proc/{pid}/maps") as f:
        for line in f:
            parts = line.rstrip("\n").split()
            if len(parts) < 2 or "r" not in parts[1]:
                continue
            name = parts[5] if len(parts) >= 6 else ""
            if name in _SKIP_MAPPINGS:
                continue
            name_l = name.lower()
            hot = any(s in name_l for s in _HOT_SUBSTRINGS)
            if (
                any(name.startswith(p) for p in _SKIP_PATH_PREFIXES)
                and not hot
            ):
                continue
            start_s, end_s = parts[0].split("-")
            start = int(start_s, 16)
            size = int(end_s, 16) - start
            if 0 < size < 500 * 1024 * 1024:
                regions.append(MemRegion(start=start, size=size, name=name, hot=hot))
    # hot regions first — keys are typically close to wcdb/sqlcipher symbols
    regions.sort(key=lambda r: (not r.hot, r.size))
    return regions


# ---------------------------------------------------------------------------
# Memory scan + validation
# ---------------------------------------------------------------------------


@dataclass
class ScanResult:
    keys_by_rel: dict[str, str] = field(default_factory=dict)   # rel_path → enc_key_hex
    salt_to_rels: dict[str, list[str]] = field(default_factory=dict)
    elapsed: float = 0.0
    pids_scanned: list[int] = field(default_factory=list)
    hex_matches: int = 0


def _scan_region(pid: int, region: MemRegion) -> bytes | None:
    """Read a region from /proc/<pid>/mem; return bytes or None on failure."""
    try:
        with open(f"/proc/{pid}/mem", "rb", buffering=0) as mem:
            mem.seek(region.start)
            return mem.read(region.size)
    except (OSError, ValueError):
        return None


def _try_keys_against_remaining(
    candidates: list[bytes],
    db_files: list[DbFile],
    remaining_salts: set[str],
    keys_by_salt: dict[str, str],
) -> int:
    """Cross-verify a batch of candidate enc_keys against remaining salts.
    Returns number of new matches.
    """
    hits = 0
    for cand in candidates:
        if not remaining_salts:
            break
        for db in db_files:
            if db.salt_hex in remaining_salts and verify_enc_key(cand, db.page1):
                keys_by_salt[db.salt_hex] = cand.hex()
                remaining_salts.discard(db.salt_hex)
                hits += 1
                break
    return hits


def scan_pid(
    pid: int,
    db_files: list[DbFile],
    remaining_salts: set[str],
    keys_by_salt: dict[str, str],
) -> int:
    """Scan one PID's memory; return number of hex matches found (any length)."""
    try:
        regions = list_regions(pid)
    except (OSError, PermissionError) as e:
        log.warning(f"PID {pid}: cannot read maps: {e}")
        return 0
    total = sum(r.size for r in regions)
    log.info(f"PID {pid}: {len(regions)} regions, {total / 1024 / 1024:.0f} MB")
    hex_matches = 0
    for region in regions:
        if not remaining_salts:
            break
        data = _scan_region(pid, region)
        if data is None:
            continue
        for m in _HEX_RE.finditer(data):
            hex_matches += 1
            hex_str = m.group(1)
            n = len(hex_str)
            # candidate enc_key is always the first 64 hex chars
            if n < 64:
                continue
            enc_key_hex = hex_str[:64].decode("ascii")
            try:
                enc_key = bytes.fromhex(enc_key_hex)
            except ValueError:
                continue
            # if the match also carries a salt tail (96 or longer), only try matching dbs
            if n == 96 or (n > 96 and n % 2 == 0):
                tail_salt_hex = hex_str[-32:].decode("ascii").lower()
                if tail_salt_hex in remaining_salts:
                    for db in db_files:
                        if db.salt_hex == tail_salt_hex and verify_enc_key(enc_key, db.page1):
                            keys_by_salt[db.salt_hex] = enc_key_hex
                            remaining_salts.discard(db.salt_hex)
                            log.info(f"  FOUND salt={db.salt_hex}  enc_key={enc_key_hex[:16]}…  ← PID {pid} @ 0x{region.start + m.start():x}")
                            break
            else:
                # 64-char bare key: brute-test against every remaining salt
                _try_keys_against_remaining([enc_key], db_files, remaining_salts, keys_by_salt)
    return hex_matches


def scan(pids: list[int], db_storage: Path) -> ScanResult:
    """Top-level scan: try every PID until all DB salts are resolved."""
    t0 = time.time()
    db_files, salt_to_rels = collect_dbs(db_storage)
    if not db_files:
        raise RuntimeError(f"no .db files under {db_storage}")
    log.info(f"collected {len(db_files)} db files, {len(salt_to_rels)} distinct salts")
    remaining = set(salt_to_rels.keys())
    keys_by_salt: dict[str, str] = {}
    hex_matches = 0
    pids_scanned: list[int] = []
    for pid in pids:
        if not remaining:
            break
        pids_scanned.append(pid)
        hex_matches += scan_pid(pid, db_files, remaining, keys_by_salt)
    # cross-verify
    if remaining and keys_by_salt:
        log.info(f"cross-verify: {len(remaining)} salts still unresolved")
        candidates = [bytes.fromhex(h) for h in set(keys_by_salt.values())]
        _try_keys_against_remaining(candidates, db_files, remaining, keys_by_salt)
    # expand salt-keyed dict into rel-keyed dict
    keys_by_rel: dict[str, str] = {}
    for db in db_files:
        if db.salt_hex in keys_by_salt:
            keys_by_rel[db.rel] = keys_by_salt[db.salt_hex]
    return ScanResult(
        keys_by_rel=keys_by_rel,
        salt_to_rels=salt_to_rels,
        elapsed=time.time() - t0,
        pids_scanned=pids_scanned,
        hex_matches=hex_matches,
    )


def save_keys(result: ScanResult, db_files: list[DbFile], db_storage: Path, out: Path) -> None:
    """Write keys to JSON file (chmod 600). Format matches L1en2407 so downstream tooling stays compatible."""
    salt_by_rel = {db.rel: (db.salt_hex, db.size) for db in db_files}
    payload: dict[str, object] = {}
    for rel, key_hex in result.keys_by_rel.items():
        salt_hex, sz = salt_by_rel[rel]
        payload[rel] = {
            "enc_key": key_hex,
            "salt": salt_hex,
            "size_mb": round(sz / 1024 / 1024, 1),
        }
    payload["_db_dir"] = str(db_storage)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    out.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    log.info(f"wrote {len(result.keys_by_rel)} keys → {out}")


def load_keys(path: Path) -> dict[str, str]:
    """Return {rel_path → enc_key_hex} from a saved keys file."""
    data = json.loads(path.read_text())
    out: dict[str, str] = {}
    for rel, v in data.items():
        if rel.startswith("_"):
            continue
        if isinstance(v, dict) and "enc_key" in v:
            out[rel] = v["enc_key"]
    return out
