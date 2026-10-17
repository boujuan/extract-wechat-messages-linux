"""Unit tests for ``tools/elf_xref.py`` — the dev-only ELF xref scanner.

The fixtures build tiny synthetic ELF64 files in memory, so these tests run
anywhere without a 100 MB WeChat binary or a real toolchain. Every section's
``sh_addr`` is set to ``ADDR_BASE + sh_offset`` (deliberately *not* equal to
the file offset) so that any regression which confuses virtual addresses with
file offsets fails loudly.

The displacement tests are regression guards for a real bug: matching the
``8d`` opcode without accounting for the optional REX prefix makes the
effective-address arithmetic off by one, which silently yields *zero* xrefs.
"""
from __future__ import annotations

import importlib.util
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "elf_xref.py"

_spec = importlib.util.spec_from_file_location("elf_xref", TOOL_PATH)
assert _spec is not None and _spec.loader is not None
elf_xref = importlib.util.module_from_spec(_spec)
# Register before exec: pydantic resolves the (postponed) model annotations
# via sys.modules[cls.__module__].
sys.modules[_spec.name] = elf_xref
_spec.loader.exec_module(elf_xref)

SHF_ALLOC = elf_xref.SHF_ALLOC
SHF_EXEC = elf_xref.SHF_ALLOC | elf_xref.SHF_EXECINSTR
ADDR_BASE = 0x400000
RODATA = b"TracerPid:\x00"
NEEDLE = b"TracerPid:"


# ---------------------------------------------------------------------------
# Synthetic ELF64 builder
# ---------------------------------------------------------------------------

def build_elf(sections: list[tuple[str, int, bytes]]) -> tuple[bytes, dict[str, tuple[int, int, int]]]:
    """Build a minimal ELF64 LE with a section header table.

    ``sections`` is ``[(name, flags, data), ...]``. Returns ``(bytes, layout)``
    where ``layout[name] = (sh_addr, sh_offset, sh_size)``.
    """
    names = [n for n, _f, _d in sections] + [".shstrtab"]
    shstr = bytearray(b"\0")
    name_off: dict[str, int] = {}
    for n in names:
        if n in name_off:
            continue
        name_off[n] = len(shstr)
        shstr += n.encode() + b"\0"

    blobs = [(n, f, d) for n, f, d in sections]
    blobs.append((".shstrtab", 0, bytes(shstr)))

    off = 64
    laid: list[tuple[str, int, int, int, bytes]] = []
    for n, f, d in blobs:
        laid.append((n, f, off, len(d), d))
        off = (off + len(d) + 7) & ~7
    shoff = off

    header = bytearray(64)
    header[0:4] = b"\x7fELF"
    header[4] = 2  # ELFCLASS64
    header[5] = 1  # ELFDATA2LSB
    header[6] = 1  # EV_CURRENT
    struct.pack_into("<Q", header, 0x28, shoff)
    struct.pack_into("<H", header, 0x3A, 64)               # e_shentsize
    struct.pack_into("<H", header, 0x3C, len(laid) + 1)    # e_shnum (+ SHT_NULL)
    struct.pack_into("<H", header, 0x3E, len(laid))        # e_shstrndx

    out = bytearray(header)
    for _n, _f, o, _size, d in laid:
        out += b"\0" * (o - len(out))
        out += d
    out += b"\0" * (shoff - len(out))
    out += b"\0" * 64  # SHT_NULL header
    for n, f, o, size, _d in laid:
        sh = struct.pack("<IIQQQQ", name_off[n], 1, f, ADDR_BASE + o, o, size)
        out += sh + b"\0" * (64 - len(sh))

    layout = {n: (ADDR_BASE + o, o, size) for n, _f, o, size, _d in laid}
    return bytes(out), layout


def _lea(modrm: int, disp: int, rex: int = 0x48) -> bytes:
    """`lea reg, [rip+disp32]` (7 bytes: REX + 8d + modrm + disp32)."""
    return bytes([rex, 0x8D, modrm]) + struct.pack("<i", disp)


def _layout(text_len: int, text_name: str = ".text",
            rodata: bytes = RODATA) -> dict[str, tuple[int, int, int]]:
    _, layout = build_elf([
        (text_name, SHF_EXEC, b"\x90" * text_len),
        (".rodata", SHF_ALLOC, rodata),
    ])
    return layout


def _elf_with_lea_to(rodata: bytes = RODATA, needle: bytes = NEEDLE,
                     text_name: str = ".text", modrm: int = 0x3D,
                     rex: int = 0x48) -> tuple[bytes, dict[str, tuple[int, int, int]], int]:
    """ELF whose ``text_name`` section starts with a lea targeting ``needle``."""
    layout = _layout(7, text_name, rodata)
    text_addr = layout[text_name][0]
    target = layout[".rodata"][0] + rodata.index(needle)
    lea = _lea(modrm, target - (text_addr + 7), rex)
    data, layout = build_elf([
        (text_name, SHF_EXEC, lea),
        (".rodata", SHF_ALLOC, rodata),
    ])
    return data, layout, target


# ---------------------------------------------------------------------------
# ELF parsing
# ---------------------------------------------------------------------------

def test_parse_elf64_reads_sections_with_vaddrs_distinct_from_offsets():
    data, layout = build_elf([
        (".text", SHF_EXEC, b"\x90" * 8),
        (".rodata", SHF_ALLOC, RODATA),
    ])
    sections = {s.name: s for s in elf_xref.parse_elf64(data)}

    assert set(sections) == {".text", ".rodata", ".shstrtab"}
    text = sections[".text"]
    assert text.addr == layout[".text"][0]
    assert text.offset == layout[".text"][1]
    assert text.addr != text.offset  # fixture guarantees this
    assert text.addr >= ADDR_BASE
    assert text.is_exec and not text.is_readable_data
    assert sections[".rodata"].is_readable_data


@pytest.mark.parametrize("payload", [
    b"",
    b"not an elf at all",
    b"\x7fELF" + b"\0" * 60,                    # 32-bit: e_ident[EI_CLASS] != 2
])
def test_parse_elf64_rejects_non_64bit_le_elf(payload):
    with pytest.raises(ValueError):
        elf_xref.parse_elf64(payload)


def test_parse_elf64_rejects_missing_section_table():
    header = bytearray(64)
    header[0:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1
    # e_shoff stays 0 -> no section header table
    with pytest.raises(ValueError, match="section header"):
        elf_xref.parse_elf64(bytes(header))


def test_only_allocated_data_sections_are_searched_for_anchors():
    sections = [
        elf_xref.Section(name=".rodata", typ=1, flags=SHF_ALLOC, addr=0x1000,
                         offset=0, size=4, data=b"abcd"),
        elf_xref.Section(name=".text", typ=1, flags=SHF_EXEC, addr=0x2000,
                         offset=0, size=4, data=b"abcd"),
        elf_xref.Section(name=".bss", typ=8, flags=SHF_ALLOC, addr=0x3000,
                         offset=0, size=4, data=b""),
        elf_xref.Section(name=".comment", typ=1, flags=0, addr=0x4000,
                         offset=0, size=4, data=b"abcd"),
    ]
    hits = elf_xref.find_anchors(sections, ["abcd"])
    assert [(h.section, h.va) for h in hits] == [(".rodata", 0x1000)]


# ---------------------------------------------------------------------------
# lea xrefs
# ---------------------------------------------------------------------------

def test_finds_rip_relative_lea_xref():
    data, layout, target = _elf_with_lea_to()
    sections = elf_xref.parse_elf64(data)
    xrefs = elf_xref.find_lea_xrefs(sections, target)

    # site_va points at the 8d opcode, i.e. one byte past the REX prefix.
    assert [x.site_va for x in xrefs] == [layout[".text"][0] + 1]
    assert xrefs[0].section == ".text"
    assert xrefs[0].raw == _lea(0x3D, target - (layout[".text"][0] + 7))[1:]


def test_displacement_math_does_not_match_neighbouring_addresses():
    """Regression: the old off-by-one matched nothing (or the wrong site).

    Two leas target ``target`` and ``target + 1``; each must resolve to
    exactly its own site, and neither may leak into the other's address.
    """
    layout = _layout(15)
    text_addr = layout[".text"][0]
    target = layout[".rodata"][0] + RODATA.index(NEEDLE)

    first = _lea(0x3D, target - (text_addr + 7))
    second = _lea(0x35, (target + 1) - (text_addr + 8 + 7))
    data, _ = build_elf([
        (".text", SHF_EXEC, first + b"\x90" + second),
        (".rodata", SHF_ALLOC, RODATA),
    ])
    sections = elf_xref.parse_elf64(data)

    assert [x.site_va for x in elf_xref.find_lea_xrefs(sections, target)] == [text_addr + 1]
    assert [x.site_va for x in elf_xref.find_lea_xrefs(sections, target + 1)] == [text_addr + 9]


def test_rex_prefixed_lea_in_non_text_exec_section():
    name = ".text.hot"
    data, layout, target = _elf_with_lea_to(text_name=name, modrm=0x05, rex=0x4C)
    xrefs = elf_xref.find_lea_xrefs(elf_xref.parse_elf64(data), target)

    assert [x.site_va for x in xrefs] == [layout[name][0] + 1]
    assert xrefs[0].section == name


def test_addr_size_override_is_not_treated_as_rip_relative():
    """`67` (address-size override) makes `modrm 05` a 32-bit absolute operand."""
    layout = _layout(8)
    text_addr = layout[".text"][0]
    target = layout[".rodata"][0] + RODATA.index(NEEDLE)

    text = b"\x67" + _lea(0x3D, target - (text_addr + 8))
    data, _ = build_elf([
        (".text", SHF_EXEC, text),
        (".rodata", SHF_ALLOC, RODATA),
    ])
    assert elf_xref.find_lea_xrefs(elf_xref.parse_elf64(data), target) == []


def test_lea_at_end_of_section_is_not_read_past_the_buffer():
    data, layout, target = _elf_with_lea_to()
    sections = elf_xref.parse_elf64(data)
    text = next(s for s in sections if s.name == ".text")
    truncated = text.model_copy(update={"data": text.data[:-1]})
    # No exception, and the incomplete instruction is ignored.
    assert elf_xref.find_lea_xrefs([truncated], target) == []


# ---------------------------------------------------------------------------
# call refs / pointer refs
# ---------------------------------------------------------------------------

def test_finds_direct_call_and_jmp_references():
    call_target = 0x500000
    jmp_target = 0x600000
    layout = _layout(10)
    text_addr = layout[".text"][0]

    call = b"\xe8" + struct.pack("<i", call_target - (text_addr + 5))
    jmp = b"\xe9" + struct.pack("<i", jmp_target - (text_addr + 5 + 5))
    data, _ = build_elf([
        (".text", SHF_EXEC, call + jmp),
        (".rodata", SHF_ALLOC, RODATA),
    ])
    sections = elf_xref.parse_elf64(data)

    assert [c.site_va for c in elf_xref.find_call_refs(sections, call_target)] == [text_addr]
    assert [c.site_va for c in elf_xref.find_call_refs(sections, jmp_target)] == [text_addr + 5]


def test_finds_pointer_references():
    rodata = b"\x00" * 16 + RODATA
    pointer = struct.pack("<Q", 0)
    _, layout = build_elf([
        (".rodata", SHF_ALLOC, rodata),
        (".data.rel.ro", SHF_ALLOC, b"\x00" * 8 + pointer),
    ])
    target = layout[".rodata"][0] + rodata.index(NEEDLE)

    data, layout = build_elf([
        (".rodata", SHF_ALLOC, rodata),
        (".data.rel.ro", SHF_ALLOC, b"\x00" * 8 + struct.pack("<Q", target)),
    ])
    refs = elf_xref.find_pointer_refs(elf_xref.parse_elf64(data), target)

    assert [(r.section, r.va) for r in refs] == [
        (".data.rel.ro", layout[".data.rel.ro"][0] + 8)
    ]


# ---------------------------------------------------------------------------
# end-to-end scan + CLI
# ---------------------------------------------------------------------------

def test_scan_marks_referenced_and_dead_strings():
    rodata = RODATA + b"deadbeef\x00"
    data, _layout_, target = _elf_with_lea_to(rodata=rodata)
    reports = elf_xref.scan(elf_xref.parse_elf64(data), ["TracerPid:", "deadbeef"])
    by_needle = {r.anchor.needle: r for r in reports if r.anchor}

    assert by_needle["TracerPid:"].anchor.va == target
    assert by_needle["TracerPid:"].resolved.is_referenced
    assert not by_needle["deadbeef"].resolved.is_referenced


def test_scan_resolves_explicit_targets():
    data, layout, target = _elf_with_lea_to()
    reports = elf_xref.scan(elf_xref.parse_elf64(data), [], targets=[target])

    assert len(reports) == 1
    assert reports[0].anchor is None
    assert reports[0].label == f"target {target:#x}"
    assert reports[0].resolved.va == target


def test_cli_text_and_json_output(tmp_path):
    data, layout, target = _elf_with_lea_to()
    sample = tmp_path / "sample.elf"
    sample.write_bytes(data)

    text_run = subprocess.run(
        [sys.executable, str(TOOL_PATH), str(sample), "TracerPid:"],
        capture_output=True, text=True, check=False,
    )
    assert text_run.returncode == 0, text_run.stderr
    assert f"{layout['.text'][0] + 1:#x}" in text_run.stdout
    assert "lea xref" in text_run.stdout

    json_run = subprocess.run(
        [sys.executable, str(TOOL_PATH), "--json", str(sample), "TracerPid:"],
        capture_output=True, text=True, check=False,
    )
    assert json_run.returncode == 0, json_run.stderr
    payload = json.loads(json_run.stdout)
    assert payload["results"][0]["code_xrefs"][0]["site_va"] == layout[".text"][0] + 1

    target_run = subprocess.run(
        [sys.executable, str(TOOL_PATH), "--target", hex(target), str(sample)],
        capture_output=True, text=True, check=False,
    )
    assert target_run.returncode == 0, target_run.stderr
    assert f"target {target:#x}" in target_run.stdout


def test_cli_rejects_bad_input(tmp_path):
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"this is not an elf")

    run = subprocess.run(
        [sys.executable, str(TOOL_PATH), str(junk), "TracerPid:"],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 1
    assert "not an ELF file" in run.stderr


def test_cli_requires_an_anchor_or_target(tmp_path):
    sample = tmp_path / "sample.elf"
    sample.write_bytes(_elf_with_lea_to()[0])

    run = subprocess.run(
        [sys.executable, str(TOOL_PATH), str(sample)],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode != 0
    assert "ANCHOR or --target" in run.stderr


@pytest.mark.skipif(not os.environ.get("WXE_TEST_ELF"), reason="WXE_TEST_ELF not set")
def test_real_elf_opt_in():
    """Point WXE_TEST_ELF at a real binary (e.g. /opt/wechat/wechat) to smoke-test."""
    path = Path(os.environ["WXE_TEST_ELF"]).expanduser()
    if not path.is_file():
        pytest.skip(f"{path} is not a file")
    reports = elf_xref.scan_path(path, ["TracerPid:"])
    assert isinstance(reports, list)
