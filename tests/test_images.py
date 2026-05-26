"""Image decryption: synthetic V1 XOR fixture; V2 detection only (real V2 requires AES key)."""
from pathlib import Path

from wxextract import images


def _make_v1_jpeg(tmp_path: Path, key: int = 0xAA) -> Path:
    """Write a tiny "JPEG" (just the magic bytes) XOR'd with `key` to a .dat file."""
    jpeg = bytes([0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00])
    enc = bytes(b ^ key for b in jpeg)
    p = tmp_path / "fake.dat"
    p.write_bytes(enc)
    return p


def test_detect_xor_key_finds_jpeg(tmp_path):
    p = _make_v1_jpeg(tmp_path, key=0x37)
    assert images.detect_xor_key(p) == 0x37


def test_decrypt_v1_round_trip(tmp_path):
    p = _make_v1_jpeg(tmp_path, key=0x42)
    result = images.decrypt_v1(p)
    assert result is not None
    data, fmt = result
    assert fmt == "jpg"
    assert data[:3] == bytes([0xFF, 0xD8, 0xFF])


def test_is_v2_detects_signature(tmp_path):
    v2 = tmp_path / "v2.dat"
    v2.write_bytes(b"\x07\x08V2\x08\x07" + b"\x00" * 10)
    assert images.is_v2(v2) is True

    v1 = _make_v1_jpeg(tmp_path)
    assert images.is_v2(v1) is False


def test_detect_xor_key_returns_none_for_v2(tmp_path):
    p = tmp_path / "v2.dat"
    p.write_bytes(b"\x07\x08V2\x08\x07" + b"\x00" * 10)
    assert images.detect_xor_key(p) is None


def test_decrypt_dispatch_v1(tmp_path):
    p = _make_v1_jpeg(tmp_path, key=0x99)
    assert images.decrypt(p) is not None


def test_decrypt_dispatch_v2_without_key_returns_none(tmp_path):
    p = tmp_path / "v2.dat"
    p.write_bytes(b"\x07\x08V2\x08\x07" + b"\x00" * 50)
    assert images.decrypt(p) is None


def test_detect_format_known_magics():
    assert images.detect_format(b"\xFF\xD8\xFF\xE0...") == "jpg"
    assert images.detect_format(b"\x89PNG\r\n\x1a\n") == "png"
    assert images.detect_format(b"GIF89a..") == "gif"
    assert images.detect_format(b"RIFF\x00\x00\x00\x00WEBP....") == "webp"
    assert images.detect_format(b"BM\x00\x00\x00\x00") == "bmp"
    assert images.detect_format(b"wxgf\x00\x00") == "hevc"
    assert images.detect_format(b"\x00\x00\x00") == "bin"
