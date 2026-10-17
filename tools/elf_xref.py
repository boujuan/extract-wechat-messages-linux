"""ELF64 cross-reference (xref) scanner for reverse-engineering triage.

Dev/RE utility — **not** part of the shipped ``wxextract`` runtime (it lives
outside ``src/wxextract`` and is excluded from the wheel). It answers the
question *"which code actually references this string / this function?"* for
a stripped x86-64 ELF, which is what you need when triaging things like
suspected anti-debugging checks (e.g. whether ``TracerPid:`` /
``/proc/self/status`` are referenced at all, or are merely dead strings left
behind by a statically-linked library).

Requires ``pydantic`` (dev dependency group — run ``uv sync`` first).

For each anchor string it reports:

1. **Occurrences** in every allocated, non-executable section (``.rodata``,
   ``.data.rel.ro``, ...) with the virtual address (``sh_addr``) of each.
2. **Code xrefs** — ``lea reg, [rip+disp32]`` sites whose effective address
   resolves to that string. A ``lea`` is how position-independent code
   materialises the *address* of a string constant (a PIE cannot embed an
   absolute pointer in code), so it is the canonical xref.
3. **Pointer refs** — 8-byte little-endian pointers equal to the address
   (catches pointer/vtable tables in ``.data.rel.ro``).

It can also resolve an arbitrary address (e.g. a function you found), which
adds **call refs** — ``call``/``jmp rel32`` sites that transfer control to
it — so you can tell whether a suspicious routine is reachable.

Correctness notes (exactly what the unit tests pin down):

* ``sh_addr`` is used for all address arithmetic, never the file offset —
  for a large binary the two differ (WeChat: ``.text`` vaddr ``0x44ee000``
  vs file offset ``0x44ed000``).
* For ``8d modrm disp32`` the displacement starts at ``opcode+2`` and the
  next instruction begins at ``opcode+6`` (a REX prefix, if present, sits
  *before* the opcode and does not extend the count). Getting this off by
  one silently yields zero hits — the bug this module's tests guard.
* A ``0x67`` (address-size override) prefix turns ``modrm 05`` into a
  32-bit absolute operand, **not** RIP-relative, so it is skipped. Legacy
  prefixes precede the REX byte, so the check looks back over both.

Usage::

    python tools/elf_xref.py /opt/wechat/wechat 'TracerPid:' '/proc/self/status'
    python tools/elf_xref.py --target 0x45b3bd0 /opt/wechat/wechat
    python tools/elf_xref.py --json ./libandromeda.so 'TracerPid:'
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict

SHF_ALLOC = 0x2
SHF_EXECINSTR = 0x4
SHT_NOBITS = 8

_ADDR_SIZE_OVERRIDE = 0x67
# Legacy prefixes may appear before the REX byte (e.g. 67 48 8d ...).
_LEGACY_PREFIXES = frozenset({
    0x26, 0x2E, 0x36, 0x3E, 0x64, 0x65, 0x66, 0x67, 0xF0, 0xF2, 0xF3,
})
# lea r/m, [rip+disp32]: opcode 8d, modrm mod=00 rm=101 (reg varies -> 8 modrms)
_LEA_RIP = re.compile(rb"\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]")
# call/jmp rel32: e8 / e9, 5-byte instructions
_CALL_REL = re.compile(rb"[\xe8\xe9]")
_CALL_REL_LEN = 5

_FROZEN = ConfigDict(frozen=True)


class Section(BaseModel):
    """One ELF section header plus its (possibly empty) file bytes."""

    model_config = _FROZEN

    name: str
    typ: int
    flags: int
    addr: int
    offset: int
    size: int
    data: bytes

    @property
    def is_exec(self) -> bool:
        return bool(self.flags & SHF_EXECINSTR)

    @property
    def is_readable_data(self) -> bool:
        """Allocated, non-executable, and backed by file bytes (not .bss)."""
        return bool(self.flags & SHF_ALLOC) and not self.is_exec and self.typ != SHT_NOBITS


class Anchor(BaseModel):
    """A string occurrence and the virtual address it was found at."""

    model_config = _FROZEN

    needle: str
    section: str
    va: int


class Xref(BaseModel):
    """A ``lea reg, [rip+disp32]`` site whose target is the resolved address.

    ``site_va`` is the address of the ``8d`` opcode byte (a preceding REX
    prefix is not included, to avoid misattributing a stray 0x4X byte that
    actually terminates the previous instruction).
    """

    model_config = _FROZEN

    site_va: int
    section: str
    raw: bytes


class CallRef(BaseModel):
    """A ``call``/``jmp rel32`` site that transfers control to the address."""

    model_config = _FROZEN

    site_va: int
    section: str
    raw: bytes


class PtrRef(BaseModel):
    """A pointer-sized value equal to the resolved address."""

    model_config = _FROZEN

    va: int
    section: str


class Resolved(BaseModel):
    """Everything that points at one address."""

    model_config = _FROZEN

    va: int
    code_xrefs: tuple[Xref, ...] = ()
    call_refs: tuple[CallRef, ...] = ()
    ptr_refs: tuple[PtrRef, ...] = ()

    @property
    def is_referenced(self) -> bool:
        return bool(self.code_xrefs or self.call_refs or self.ptr_refs)


class Report(BaseModel):
    """Anchor (if any) plus its resolution."""

    model_config = _FROZEN

    anchor: Anchor | None = None
    resolved: Resolved
    label: str = ""


def parse_elf64(data: bytes) -> list[Section]:
    """Parse section headers of a 64-bit little-endian ELF.

    Only section headers are parsed (enough for xref work); the program
    header table is ignored. Raises :class:`ValueError` for anything that
    is not a 64-bit LE ELF with a section header table.
    """
    if len(data) < 64 or data[:4] != b"\x7fELF":
        raise ValueError("not an ELF file")
    if data[4] != 2 or data[5] != 1:
        raise ValueError("only 64-bit little-endian ELF is supported")
    e_shoff, = struct.unpack_from("<Q", data, 0x28)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 0x3A)
    if not e_shoff or not e_shnum:
        raise ValueError("ELF has no section header table")
    if e_shstrndx >= e_shnum:
        raise ValueError(f"bad e_shstrndx {e_shstrndx} (have {e_shnum} sections)")

    headers = [
        struct.unpack_from("<IIQQQQ", data, e_shoff + i * e_shentsize)
        for i in range(e_shnum)
    ]
    shstr = headers[e_shstrndx]
    shstr_data = data[shstr[4]:shstr[4] + shstr[5]]

    def name_at(idx: int) -> str:
        end = shstr_data.find(b"\0", idx)
        if end < 0:
            end = len(shstr_data)
        return shstr_data[idx:end].decode("latin-1")

    out: list[Section] = []
    for i, (name, typ, flags, addr, offset, size) in enumerate(headers):
        if i == 0 or name == 0:  # SHT_NULL
            continue
        raw = b"" if typ == SHT_NOBITS else data[offset:offset + size]
        out.append(Section(name=name_at(name), typ=typ, flags=flags, addr=addr,
                           offset=offset, size=size, data=raw))
    return out


def find_anchors(sections: list[Section], needles: list[str]) -> list[Anchor]:
    """Every occurrence of every needle in allocated, non-exec sections."""
    hits: list[Anchor] = []
    for section in sections:
        if not section.is_readable_data:
            continue
        for needle in needles:
            nb = needle.encode()
            start = 0
            while (i := section.data.find(nb, start)) != -1:
                hits.append(Anchor(needle=needle, section=section.name, va=section.addr + i))
                start = i + 1
    return hits


def _has_addr_size_override(blob: bytes, op: int) -> bool:
    """True if the instruction starting at ``op`` carries a ``0x67`` prefix.

    ``0x67`` switches ``modrm 05`` from RIP-relative to a 32-bit absolute
    operand, so such a site is *not* an xref to the displacement target.
    Prefixes precede the REX byte, so look back over up to four of them.
    """
    i = op - 1
    for _ in range(4):
        if i < 0:
            return False
        b = blob[i]
        if b == _ADDR_SIZE_OVERRIDE:
            return True
        if b in _LEGACY_PREFIXES or 0x40 <= b <= 0x4F:  # REX
            i -= 1
            continue
        return False
    return False


def find_lea_xrefs(sections: list[Section], target_va: int) -> list[Xref]:
    """All ``lea reg, [rip+disp32]`` sites in executable sections targeting
    ``target_va``."""
    hits: list[Xref] = []
    for section in sections:
        if not section.is_exec or section.typ == SHT_NOBITS:
            continue
        blob = section.data
        for m in _LEA_RIP.finditer(blob):
            op = m.start()
            if op + 6 > len(blob):
                continue
            if op and _has_addr_size_override(blob, op):
                continue  # 67-prefixed => 32-bit absolute, not RIP-relative
            disp, = struct.unpack_from("<i", blob, op + 2)
            site_va = section.addr + op
            # next instruction is opcode + 6 bytes (REX, if any, precedes it)
            if site_va + 6 + disp == target_va:
                hits.append(Xref(site_va=site_va, section=section.name, raw=blob[op:op + 6]))
    return hits


def find_call_refs(sections: list[Section], target_va: int) -> list[CallRef]:
    """All ``call``/``jmp rel32`` sites in executable sections that target
    ``target_va`` (direct control-flow references to a function)."""
    hits: list[CallRef] = []
    for section in sections:
        if not section.is_exec or section.typ == SHT_NOBITS:
            continue
        blob = section.data
        for m in _CALL_REL.finditer(blob):
            op = m.start()
            if op + _CALL_REL_LEN > len(blob):
                continue
            disp, = struct.unpack_from("<i", blob, op + 1)
            site_va = section.addr + op
            if site_va + _CALL_REL_LEN + disp == target_va:
                hits.append(CallRef(site_va=site_va, section=section.name,
                                    raw=blob[op:op + _CALL_REL_LEN]))
    return hits


def find_pointer_refs(sections: list[Section], target_va: int) -> list[PtrRef]:
    """All pointer-sized little-endian values equal to ``target_va``."""
    needle = struct.pack("<Q", target_va)
    hits: list[PtrRef] = []
    for section in sections:
        if not section.data:
            continue
        start = 0
        while (i := section.data.find(needle, start)) != -1:
            hits.append(PtrRef(va=section.addr + i, section=section.name))
            start = i + 1
    return hits


def resolve(sections: list[Section], target_va: int) -> Resolved:
    """Find every reference to ``target_va`` within ``sections``."""
    return Resolved(
        va=target_va,
        code_xrefs=tuple(find_lea_xrefs(sections, target_va)),
        call_refs=tuple(find_call_refs(sections, target_va)),
        ptr_refs=tuple(find_pointer_refs(sections, target_va)),
    )


def scan(sections: list[Section], needles: list[str],
         targets: list[int] | None = None) -> list[Report]:
    """Resolve every anchor occurrence, plus any explicit numeric targets."""
    reports: list[Report] = []
    for anchor in find_anchors(sections, needles):
        reports.append(Report(anchor=anchor, resolved=resolve(sections, anchor.va)))
    for va in targets or []:
        reports.append(Report(resolved=resolve(sections, va), label=f"target {va:#x}"))
    return reports


def scan_path(path: Path, needles: list[str],
              targets: list[int] | None = None) -> list[Report]:
    return scan(parse_elf64(path.read_bytes()), needles, targets)


def _section_of(resolved: Resolved) -> str:
    for seq in (resolved.code_xrefs, resolved.call_refs, resolved.ptr_refs):
        if seq:
            return seq[0].section
    return "?"


def _render_text(path: Path, reports: list[Report]) -> str:
    lines = [f"# {path}"]
    if not reports:
        lines.append("(no anchor occurrences found in allocated data sections)")
    for rep in reports:
        if rep.anchor is not None:
            a = rep.anchor
            lines.append(f"{a.needle!r} in {a.section} @ {a.va:#x}")
        else:
            lines.append(f"{rep.label} in {_section_of(rep.resolved)}")
        r = rep.resolved
        if not r.is_referenced:
            lines.append("    (no code, call, or pointer references — dead?)")
        for x in r.code_xrefs:
            lines.append(f"    lea xref  site={x.site_va:#x} ({x.section}) bytes={x.raw.hex()}")
        for c in r.call_refs:
            lines.append(f"    call ref  site={c.site_va:#x} ({c.section}) bytes={c.raw.hex()}")
        for p in r.ptr_refs:
            lines.append(f"    ptr ref   {p.va:#x} ({p.section})")
    return "\n".join(lines)


def _render_json(path: Path, reports: list[Report]) -> str:
    payload = {
        "path": str(path),
        "results": [
            {
                "anchor": None if rep.anchor is None else {
                    "needle": rep.anchor.needle,
                    "section": rep.anchor.section,
                    "va": rep.anchor.va,
                },
                "label": rep.label,
                "va": rep.resolved.va,
                "code_xrefs": [
                    {"site_va": x.site_va, "section": x.section, "bytes": x.raw.hex()}
                    for x in rep.resolved.code_xrefs
                ],
                "call_refs": [
                    {"site_va": c.site_va, "section": c.section, "bytes": c.raw.hex()}
                    for c in rep.resolved.call_refs
                ],
                "ptr_refs": [
                    {"va": p.va, "section": p.section} for p in rep.resolved.ptr_refs
                ],
            }
            for rep in reports
        ],
    }
    return json.dumps(payload, indent=2)


def _parse_target(token: str) -> int:
    return int(token, 0)  # int(x, 0) accepts 0x.., 0o.., 0b.., decimal


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="elf_xref",
        description="Find x86-64 code/call/pointer references to string anchors or "
                    "addresses in an ELF64 binary.",
    )
    ap.add_argument("binary", type=Path, help="ELF64 file to scan")
    ap.add_argument("anchor", nargs="*", help="string(s) to look up (e.g. 'TracerPid:')")
    ap.add_argument("--target", action="append", default=[], metavar="HEX",
                    help="also resolve an explicit address (e.g. 0x45b3bd0); repeatable")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args(argv)

    if not args.anchor and not args.target:
        ap.error("provide at least one ANCHOR or --target")

    try:
        targets = [_parse_target(t) for t in args.target]
        reports = scan_path(args.binary, args.anchor, targets)
    except (OSError, ValueError) as e:
        print(f"elf_xref: {e}", file=sys.stderr)
        return 1

    render = _render_json if args.json else _render_text
    print(render(args.binary, reports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
