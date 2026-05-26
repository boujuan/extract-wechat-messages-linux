"""WeChat .dat image decryption.

Two formats observed in the wild:

  - **V1 (legacy)**: simple single-byte XOR. Each .dat file is the image
    with every byte XORed by one constant. We auto-detect the byte by
    comparing the encrypted header against the known magic bytes of
    common image formats (JPEG / PNG / GIF / WebP / TIFF / BMP).

  - **V2 (WeChat 4.x, ≥ 2025-08)**: AES-128-ECB on the first chunk +
    untouched middle + single-byte XOR on the last chunk. Header layout:

        [0..6]   "07 08 V2 08 07"      — 6-byte signature
        [6..10]  uint32 LE aes_size    — bytes of AES-encrypted prefix
        [10..14] uint32 LE xor_size    — bytes of XORed suffix
        [14]     1-byte padding
        [15..]   aligned_aes_data || raw_middle || xor_suffix

    V2 needs the 16-byte AES key, which WeChat keeps cached in process
    memory (not on disk). Auto-recovery of this key is out of scope here;
    callers pass it via `aes_key=`.

Output magic-byte sniffing handles JPEG / PNG / GIF / WebP / TIFF / BMP
and the WeChat-proprietary `wxgf` (HEVC stream) container.
"""
from __future__ import annotations

import struct
from pathlib import Path

V2_MAGIC = b"\x07\x08V2\x08\x07"
V1_MAGIC = b"\x07\x08V1\x08\x07"
V1_AES_KEY = b"cfcd208495d565ef"   # md5("0")[:16] — fixed for V1 header path

# (extension, magic bytes). Listed longest-magic-first so JPEG's 3-byte FF D8 FF
# can't mis-match a 2-byte BMP.
_IMAGE_MAGIC = [
    ("png",  bytes([0x89, 0x50, 0x4E, 0x47])),
    ("gif",  bytes([0x47, 0x49, 0x46, 0x38])),
    ("tif",  bytes([0x49, 0x49, 0x2A, 0x00])),
    ("webp", bytes([0x52, 0x49, 0x46, 0x46])),
    ("jpg",  bytes([0xFF, 0xD8, 0xFF])),
]


def is_v2(path: Path) -> bool:
    """True iff the .dat file starts with the V2 6-byte signature."""
    try:
        with open(path, "rb") as f:
            return f.read(4) == V2_MAGIC[:4]
    except OSError:
        return False


def detect_xor_key(path: Path) -> int | None:
    """Inspect the .dat header; return the single XOR byte if it looks like
    a V1 file matching any known image magic, else None."""
    try:
        with open(path, "rb") as f:
            header = f.read(16)
    except OSError:
        return None
    if len(header) < 4 or header[:4] == V2_MAGIC[:4]:
        return None
    for _ext, magic in _IMAGE_MAGIC:
        candidate = header[0] ^ magic[0]
        if all(i >= len(header) or (header[i] ^ candidate) == magic[i]
               for i in range(1, len(magic))):
            return candidate
    # BMP: 2-byte magic 'BM' — need extra plausibility check via file-size field
    bmp_magic = bytes([0x42, 0x4D])
    key = header[0] ^ bmp_magic[0]
    if len(header) >= 14 and (header[1] ^ key) == bmp_magic[1]:
        dec = bytes(b ^ key for b in header[:14])
        try:
            bmp_size, _, _, offset = struct.unpack_from("<IHHI", dec, 2)
            if abs(bmp_size - path.stat().st_size) < 1024 and 14 <= offset <= 1078:
                return key
        except struct.error:
            pass
    return None


def detect_format(decrypted_head: bytes) -> str:
    if decrypted_head[:3] == bytes([0xFF, 0xD8, 0xFF]):
        return "jpg"
    if decrypted_head[:4] == bytes([0x89, 0x50, 0x4E, 0x47]):
        return "png"
    if decrypted_head[:3] == b"GIF":
        return "gif"
    if decrypted_head[:2] == b"BM":
        return "bmp"
    if decrypted_head[:4] == b"RIFF" and len(decrypted_head) >= 12 and decrypted_head[8:12] == b"WEBP":
        return "webp"
    if decrypted_head[:4] == bytes([0x49, 0x49, 0x2A, 0x00]):
        return "tif"
    if decrypted_head[:4] == b"wxgf":
        return "hevc"
    return "bin"


def decrypt_v1(path: Path) -> tuple[bytes, str] | None:
    """Decrypt a V1 (single-byte XOR) image. Returns (data, ext) or None."""
    key = detect_xor_key(path)
    if key is None:
        return None
    data = path.read_bytes()
    out = bytes(b ^ key for b in data)
    return out, detect_format(out[:16])


def decrypt_v2(path: Path, aes_key: bytes, xor_key: int = 0x88) -> tuple[bytes, str] | None:
    """Decrypt a V2 (AES-ECB + XOR) image. Requires the 16-byte AES key
    (caller obtains it from a WeChat process memory scan)."""
    if len(aes_key) < 16:
        raise ValueError(f"aes_key must be at least 16 bytes, got {len(aes_key)}")
    from Crypto.Cipher import AES
    from Crypto.Util import Padding

    data = path.read_bytes()
    if len(data) < 15:
        return None
    sig = data[:6]
    if sig not in (V2_MAGIC, V1_MAGIC):
        return None
    aes_size, xor_size = struct.unpack_from("<LL", data, 6)
    # PKCS7 alignment: round up to next multiple of 16. When already aligned,
    # an extra full block of padding is emitted.
    aligned = aes_size - (~(~aes_size % 16))
    if sig == V1_MAGIC:
        aes_key = V1_AES_KEY
    offset = 15
    if offset + aligned > len(data):
        return None
    try:
        cipher = AES.new(aes_key[:16], AES.MODE_ECB)
        dec_aes = Padding.unpad(cipher.decrypt(data[offset:offset + aligned]),
                                AES.block_size)
    except (ValueError, KeyError):
        return None
    offset += aligned
    raw_end = len(data) - xor_size
    raw = data[offset:raw_end] if offset < raw_end else b""
    dec_xor = bytes(b ^ xor_key for b in data[raw_end:])
    out = dec_aes + raw + dec_xor
    return out, detect_format(out[:16])


def decrypt(path: Path, aes_key: bytes | None = None) -> tuple[bytes, str] | None:
    """Auto-pick V1 / V2. Returns None if V2 without key (caller can warn)."""
    if is_v2(path):
        if aes_key is None:
            return None
        return decrypt_v2(path, aes_key)
    return decrypt_v1(path)


def walk_dat_files(account_dir: Path, conv_hash: str | None = None):
    """Yield (.dat Path) under the WeChat attach tree.
    Optionally restrict to one conversation hash."""
    attach = account_dir / "msg" / "attach"
    if not attach.is_dir():
        return
    if conv_hash:
        attach = attach / conv_hash
        if not attach.is_dir():
            return
    yield from attach.rglob("*.dat")


# ---------------------------------------------------------------------------
# V2 AES image-key recovery (memory scan) — Linux /proc/PID/mem
# ---------------------------------------------------------------------------

import re as _re  # noqa: E402

# Image AES keys observed in WeChat are 16-byte alphanumeric ASCII strings.
# We scan in two passes:
#   1. Standalone 16-/32-char alphanum tokens (fast, catches most installs).
#   2. Sliding window within longer alphanum runs (catches keys embedded in
#      surrounding text without non-alphanum boundary). Triggered only if (1)
#      finds nothing.
_RE_KEY16 = _re.compile(rb"(?<![A-Za-z0-9])[A-Za-z0-9]{16}(?![A-Za-z0-9])")
_RE_KEY32 = _re.compile(rb"(?<![A-Za-z0-9])[A-Za-z0-9]{32}(?![A-Za-z0-9])")
_RE_LONG_RUN = _re.compile(rb"[A-Za-z0-9]{17,}")

# 3+ byte magics only (2-byte 'BM' produces too many false positives at
# 1/65536 — with 16-char alphanum candidates scanned over hundreds of MB of
# memory, you get random AES output matching BM coincidentally).
_IMAGE_FORMAT_MAGICS = (
    bytes([0xFF, 0xD8, 0xFF]),         # JPEG
    bytes([0x89, 0x50, 0x4E, 0x47]),   # PNG
    b"GIF8",                            # GIF87a/89a
    b"RIFF",                            # WebP container (first 4 bytes)
    b"wxgf",                            # WeChat HEVC stream
    bytes([0x49, 0x49, 0x2A, 0x00]),   # TIFF little-endian
)


def pick_test_ciphertexts(account_dir: Path, n: int = 5) -> list[tuple[bytes, Path]]:
    """Pick up to `n` V2 .dat files (mix of thumbnails + full-size) and return
    each's first AES block as a (ciphertext, file_path) test vector.

    We use MULTIPLE samples for key validation: a candidate key only counts
    as the right one if it decrypts ALL samples to a known image magic. Any
    16-byte alphanumeric string has a ~1/2^24 chance of randomly producing
    a 3-byte JPEG magic for one sample; the chance of doing that for ≥3
    independent samples is ~1/2^72 — effectively impossible.
    """
    attach = account_dir / "msg" / "attach"
    if not attach.is_dir():
        return []
    paths_thumb = sorted(attach.rglob("Img/*_t.dat"),
                         key=lambda p: -p.stat().st_mtime if p.exists() else 0)
    paths_full = sorted([p for p in attach.rglob("Img/*.dat")
                         if not p.stem.endswith("_t")],
                        key=lambda p: -p.stat().st_mtime if p.exists() else 0)
    out: list[tuple[bytes, Path]] = []
    # interleave thumb + full for variety
    candidates = []
    for i in range(max(len(paths_thumb), len(paths_full))):
        if i < len(paths_thumb):
            candidates.append(paths_thumb[i])
        if i < len(paths_full):
            candidates.append(paths_full[i])
    for p in candidates:
        if len(out) >= n:
            break
        try:
            with open(p, "rb") as f:
                head = f.read(31)
        except OSError:
            continue
        if len(head) == 31 and head[:4] == V2_MAGIC[:4]:
            out.append((head[15:31], p))
    return out


def pick_test_ciphertext(account_dir: Path) -> tuple[bytes, Path] | None:
    """Back-compat single-vector picker."""
    vectors = pick_test_ciphertexts(account_dir, n=1)
    return vectors[0] if vectors else None


def _try_key(key: bytes, ciphertext: bytes) -> str | None:
    """Decrypt the 16-byte ciphertext with `key` (AES-128-ECB). If the
    plaintext starts with a known image magic, return the format name.
    Otherwise None."""
    from Crypto.Cipher import AES
    try:
        plain = AES.new(key[:16], AES.MODE_ECB).decrypt(ciphertext)
    except (ValueError, KeyError):
        return None
    for magic in _IMAGE_FORMAT_MAGICS:
        if plain.startswith(magic):
            return magic.decode("latin-1", errors="replace")
    return None


def find_v2_aes_key(pids: list[int],
                    test_vectors: list[tuple[bytes, Path]] | bytes,
                    progress_cb=None) -> bytes | None:
    """Scan each PID's readable memory for the V2 image AES key.

    `test_vectors` is a list of (ciphertext, file_path) pairs (recommended
    ≥3 for cross-validation). A legacy single `bytes` input is also
    accepted for back-compat — but use the list form to avoid false
    positives. A candidate is accepted only when it produces a known image
    magic for EVERY supplied test vector.

    Returns the key as bytes (16 long) or None.
    """
    if isinstance(test_vectors, (bytes, bytearray)):
        # back-compat: wrap single ciphertext, no extra validation
        ciphers = [bytes(test_vectors)]
    else:
        ciphers = [c for c, _p in test_vectors]
    if not ciphers:
        return None

    from wxextract.keys import list_regions

    def _validates_all(candidate: bytes) -> bool:
        return all(_try_key(candidate, c) for c in ciphers)

    tried: set[bytes] = set()  # avoid re-validating the same string from multiple regions

    # Pass 1 — exact boundary 16/32 alphanum tokens
    for pid in pids:
        try:
            regions = list_regions(pid)
        except (OSError, PermissionError):
            continue
        total = len(regions)
        for i, region in enumerate(regions):
            if progress_cb:
                progress_cb(pid, i, total, region)
            try:
                with open(f"/proc/{pid}/mem", "rb", buffering=0) as mem:
                    mem.seek(region.start)
                    data = mem.read(region.size)
            except (OSError, ValueError):
                continue
            for m in _RE_KEY16.finditer(data):
                cand = m.group()
                if cand in tried:
                    continue
                tried.add(cand)
                if _try_key(cand, ciphers[0]) and _validates_all(cand):
                    return cand
            for m in _RE_KEY32.finditer(data):
                cand = m.group()[:16]
                if cand in tried:
                    continue
                tried.add(cand)
                if _try_key(cand, ciphers[0]) and _validates_all(cand):
                    return cand

    # Pass 2 — sliding window within long alphanum runs (key embedded in larger string)
    for pid in pids:
        try:
            regions = list_regions(pid)
        except (OSError, PermissionError):
            continue
        for region in regions:
            try:
                with open(f"/proc/{pid}/mem", "rb", buffering=0) as mem:
                    mem.seek(region.start)
                    data = mem.read(region.size)
            except (OSError, ValueError):
                continue
            for m in _RE_LONG_RUN.finditer(data):
                run = m.group()
                for off in range(len(run) - 15):
                    cand = run[off:off + 16]
                    if cand in tried:
                        continue
                    tried.add(cand)
                    if _try_key(cand, ciphers[0]) and _validates_all(cand):
                        return cand
    return None


def derive_xor_key(account_dir: Path, sample_size: int = 32) -> int:
    """Most WeChat V2 .dat files use a constant XOR key (0x88 by default,
    but we verify empirically). Sample several thumbnails: the last 2 bytes
    of each (after V2 XOR) should be (FF, D9) — the JPEG EOI marker. The
    mode of (last_byte XOR D9) across files is the XOR key. Falls back to
    0x88 if nothing conclusive."""
    from collections import Counter
    attach = account_dir / "msg" / "attach"
    if not attach.is_dir():
        return 0x88
    candidates: Counter[int] = Counter()
    paths = sorted(attach.rglob("Img/*_t.dat"),
                   key=lambda p: -p.stat().st_mtime if p.exists() else 0)
    for p in paths[:sample_size]:
        try:
            sz = p.stat().st_size
            if sz < 32:
                continue
            with open(p, "rb") as f:
                head = f.read(6)
                f.seek(sz - 2)
                tail = f.read(2)
        except OSError:
            continue
        if head != V2_MAGIC or len(tail) != 2:
            continue
        x, y = tail
        k1 = x ^ 0xFF
        k2 = y ^ 0xD9
        if k1 == k2:
            candidates[k1] += 1
    if candidates:
        return candidates.most_common(1)[0][0]
    return 0x88
