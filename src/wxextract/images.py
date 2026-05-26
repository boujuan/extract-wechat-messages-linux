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
