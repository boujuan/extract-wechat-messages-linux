"""WeChat ≥ 4.1 passphrase-based SQLCipher key recovery.

WeChat 4.1 (first seen in the 4.1.1x Linux builds) stopped caching the
per-database raw SQLCipher keys in process memory as ``x'<hex>'`` literals,
which is what :mod:`wxextract.keys` scans for — the memory scan returns 0
keys on those builds. Instead, WeChat derives every database key from a
single 32-byte *passphrase* that is computed once at login:

    enc_key = PBKDF2-HMAC-SHA512(passphrase, db_salt, 256000, dklen=32)

The raw derived keys never appear in memory in scannable form, but the
passphrase is passed through the WCDB cipher configuration code every time a
database connection is established — including right after login. So the
recovery strategy is:

1. ``find_hook_offset()`` — static ELF analysis of the WeChat binary: locate
   the ``com.Tencent.WCDB.Config.Cipher`` string in ``.rodata``, follow the
   cross-references to the function that consumes the cipher config, and
   return its offset. Re-derived on every capture, so WeChat updates are
   survived as long as the anchor string persists.
2. ``capture_passphrase()`` — launch WeChat through its normal launcher and
   ``PTRACE_ATTACH`` the fresh WeChat process the moment it appears (right
   after exec, before its first thread exists; ``PTRACE_O_TRACECLONE``
   covers every thread spawned afterwards). Patch an ``INT3`` at
   ``base + hook_offset`` and wait for the breakpoint to fire during
   (auto-)login. The passphrase is read from the argument registers and
   validated immediately against a known database page-1 HMAC. The tracer
   then detaches and WeChat continues normally. (The launcher on AUR
   ``wechat-bin`` delegates spawning to the ``portable`` systemd daemon, so
   the WeChat process is not a descendant of the launcher — hence attach
   mode rather than ``PTRACE_TRACEME``.)
3. ``derive_keys()`` — PBKDF2-derive a key per database salt (parallel) and
   verify each against page 1's HMAC. ~350 ms for ~23 databases; this is the
   steady-state path on every subsequent run and self-heals whenever WeChat
   re-keys a database or adds a new one.

The passphrase is cached in ``<workspace>/passphrase.json`` (mode 600) and
survives WeChat restarts and upgrades until WeChat itself rotates it.

The capture approach follows the public wcdb-key-tool project (Linux route);
the tracer here is pure ``ptrace(2)`` via ctypes — no gdb dependency.

Linux-only (x86-64): ptrace + ELF + ``/proc`` based.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import multiprocessing
import os
import select
import signal
import stat
import struct
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .keys import KEY_SZ, DbFile, verify_enc_key

log = logging.getLogger("wxextract.passphrase")

KDF_ITER = 256000

# ---------------------------------------------------------------------------
# 1. Static ELF analysis — find the WCDB cipher-config function offset
# ---------------------------------------------------------------------------

_WCDB_ANCHOR = b"com.Tencent.WCDB.Config.Cipher"
_LEA_RSI = b"\x48\x8D\x35"   # lea rsi, [rip+disp32]
_LEA_RDI = b"\x48\x8D\x3D"   # lea rdi, [rip+disp32]
_FUNC_HEAD = b"\x55\x41\x57"  # push rbp; push r15


class HookNotFoundError(RuntimeError):
    """The WeChat binary doesn't contain a recognizable WCDB cipher hook."""


def _elf_sections(path: Path) -> dict[str, tuple[int, bytes]]:
    """Return {section_name: (vaddr, data)} for .rodata and .text."""
    data = path.read_bytes()
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise HookNotFoundError(f"{path}: not a little-endian ELF64 binary")
    e_shoff, = struct.unpack_from("<Q", data, 0x28)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 0x3A)
    headers = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        headers.append(struct.unpack_from("<IIQQQQ", data, off))  # name,type,flags,addr,offset,size
    shstr_off = headers[e_shstrndx][4]
    out: dict[str, tuple[int, bytes]] = {}
    for name, _stype, _flags, addr, offset, size in headers:
        end = data.index(b"\0", shstr_off + name)
        sname = data[shstr_off + name:end].decode("ascii", "replace")
        if sname in (".rodata", ".text"):
            out[sname] = (addr, data[offset:offset + size])
    if ".rodata" not in out or ".text" not in out:
        raise HookNotFoundError(f"{path}: missing .rodata/.text sections")
    return out


def _rip_relative_refs(text: bytes, text_va: int, opcode: bytes, target_va: int) -> list[int]:
    """Offsets in .text where `opcode` (lea reg, [rip+disp32]) resolves to target_va."""
    hits: list[int] = []
    for off in range(len(text) - 6):
        if text[off:off + 3] != opcode:
            continue
        disp, = struct.unpack_from("<i", text, off + 3)
        if text_va + off + 7 + disp == target_va:
            hits.append(off)
    return hits


def find_hook_offset(binary: Path) -> int:
    """Return the VA offset (relative to image base) of the WCDB cipher-config
    function in the WeChat binary.

    Algorithm (mirrors wcdb-key-tool, verified against 4.1.13.9):
      anchor string --(lea rsi)--> caller that also has `lea rdi` 7 bytes
      earlier pointing at an anonymous config struct --(lea rsi on that
      struct)--> the consuming function; walk back ≤0x500 bytes to its
      prologue.
    """
    sections = _elf_sections(Path(binary))
    rodata_va, rodata = sections[".rodata"]
    text_va, text = sections[".text"]
    candidates: list[int] = []
    pos = 0
    while True:
        ao = rodata.find(_WCDB_ANCHOR, pos)
        if ao == -1:
            break
        pos = ao + 1
        anchor_va = rodata_va + ao
        for f1 in _rip_relative_refs(text, text_va, _LEA_RSI, anchor_va):
            if f1 < 7 or text[f1 - 7:f1 - 4] != _LEA_RDI:
                continue
            unk_disp, = struct.unpack_from("<i", text, f1 - 4)
            unk_va = text_va + f1 + unk_disp
            for f2 in _rip_relative_refs(text, text_va, _LEA_RSI, unk_va):
                for c in range(f2, max(0, f2 - 0x500) - 1, -1):
                    if text[c:c + 3] == _FUNC_HEAD:
                        va = text_va + c
                        if va not in candidates:
                            candidates.append(va)
                        break
    if not candidates:
        raise HookNotFoundError(
            f"{binary}: no WCDB cipher-config function found "
            f"(anchor {_WCDB_ANCHOR!r} xref chain failed)"
        )
    return sorted(candidates)[0]


# ---------------------------------------------------------------------------
# 2. ptrace(2) tracer — capture the passphrase at login
# ---------------------------------------------------------------------------

PTRACE_PEEKDATA = 2
PTRACE_POKEDATA = 5
PTRACE_CONT = 7
PTRACE_SINGLESTEP = 9
PTRACE_GETREGS = 12
PTRACE_SETREGS = 13
PTRACE_ATTACH = 16
PTRACE_DETACH = 17
PTRACE_SETOPTIONS = 0x4200

PTRACE_O_TRACEFORK = 1
PTRACE_O_TRACEVFORK = 2
PTRACE_O_TRACECLONE = 8
PTRACE_O_TRACEEXEC = 16
_PTRACE_OPTS = (
    PTRACE_O_TRACEFORK | PTRACE_O_TRACEVFORK | PTRACE_O_TRACECLONE | PTRACE_O_TRACEEXEC
)

PTRACE_EVENT_FORK = 1
PTRACE_EVENT_VFORK = 2
PTRACE_EVENT_CLONE = 3
PTRACE_EVENT_EXEC = 4

INT3 = 0xCC
_PTR_MIN, _PTR_MAX = 0x1000, 0x7FFFFFFFFFFF


class _user_regs_struct(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "r15", "r14", "r13", "r12", "rbp", "rbx", "r11", "r10", "r9", "r8",
        "rax", "rcx", "rdx", "rsi", "rdi", "orig_rax", "rip", "cs", "eflags",
        "rsp", "ss", "fs_base", "gs_base", "ds", "es", "fs", "gs",
    )]


_libc = ctypes.CDLL("libc.so.6", use_errno=True)
_libc.ptrace.restype = ctypes.c_long
_libc.ptrace.argtypes = [ctypes.c_long, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]


def _ptrace(req: int, pid: int, addr: int = 0, data: int = 0) -> int:
    ctypes.set_errno(0)
    val = _libc.ptrace(req, pid, ctypes.c_void_p(addr), ctypes.c_void_p(data))
    if val == -1 and ctypes.get_errno() != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))
    return val


def _peek(pid: int, addr: int) -> int:
    """Read one word; any errno is swallowed (caller checks plausibility)."""
    ctypes.set_errno(0)
    val = _libc.ptrace(PTRACE_PEEKDATA, pid, ctypes.c_void_p(addr), ctypes.c_void_p(0))
    if val == -1 and ctypes.get_errno() != 0:
        raise OSError(ctypes.get_errno(), "PEEKDATA")
    return val & 0xFFFFFFFFFFFFFFFF


def _read_mem(pid: int, addr: int, n: int) -> bytes:
    out = bytearray()
    for off in range(0, n, 8):
        chunk = _peek(pid, addr + off)
        out += struct.pack("<Q", chunk)
    return bytes(out[:n])


def _getregs(pid: int) -> _user_regs_struct:
    regs = _user_regs_struct()
    _ptrace(PTRACE_GETREGS, pid, 0, ctypes.addressof(regs))
    return regs


def _setregs(pid: int, regs: _user_regs_struct) -> None:
    _ptrace(PTRACE_SETREGS, pid, 0, ctypes.addressof(regs))


def _image_base(pid: int, binary: str, wait: float = 3.0) -> int:
    """PIE load base: the mapping of `binary` at file offset 0."""
    deadline = time.monotonic() + wait
    while True:
        try:
            with open(f"/proc/{pid}/maps") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 6 and parts[5] == binary and parts[2] == "00000000":
                        return int(parts[0].split("-")[0], 16)
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise CaptureError(f"cannot find image base for {binary} in pid {pid}")
        time.sleep(0.01)


class CaptureError(RuntimeError):
    """Passphrase capture failed."""


def _passphrase_candidates(pid: int, regs: _user_regs_struct) -> list[bytes]:
    """Candidate 32-byte passphrases from the SysV argument registers.

    Two calling layouts observed (rsi = 2nd argument):
      1. rsi points at the raw 32 bytes, rdx (3rd arg) == 32.
      2. rsi points at a small struct: data pointer at +8, size (== 32) at +16.
    """
    cands: list[bytes] = []
    rsi, rdx = regs.rsi, regs.rdx
    if _PTR_MIN < rsi < _PTR_MAX:
        if rdx == 32:
            try:
                cands.append(_read_mem(pid, rsi, 32))
            except OSError:
                pass
        try:
            hdr = _read_mem(pid, rsi, 24)
            ptr, size = struct.unpack_from("<QQ", hdr, 8)
            if size == 32 and _PTR_MIN < ptr < _PTR_MAX:
                cands.append(_read_mem(pid, ptr, 32))
        except OSError:
            pass
    return [c for c in cands if len(c) == 32]


def _valid_candidate(cand: bytes, probe_page1: bytes | None) -> bool:
    if len(cand) != KEY_SZ:
        return False
    if probe_page1 is None:
        return True
    return verify_enc_key(pbkdf2_key(cand, probe_page1[:16]), probe_page1)


def pbkdf2_key(passphrase: bytes, salt: bytes, iterations: int = KDF_ITER) -> bytes:
    """SQLCipher 4 page-key derivation from the WeChat passphrase."""
    return hashlib.pbkdf2_hmac("sha512", passphrase, salt, iterations, dklen=KEY_SZ)


def _ptrace_guidance() -> str:
    """Actionable guidance for a ptrace-denied attach failure, including the
    current kernel.yama.ptrace_scope value and the elevate-keys-only path."""
    scope = None
    try:
        scope = Path("/proc/sys/kernel/yama/ptrace_scope").read_text().strip()
    except OSError:
        pass
    lines = ["ptrace was denied by the kernel — wxextract cannot attach to the WeChat process."]
    if scope is not None:
        lines.append(f"current kernel.yama.ptrace_scope = {scope}")
    lines += [
        "Fix (pick one):",
        "  1) allow ptrace until reboot:  sudo sysctl kernel.yama.ptrace_scope=0",
        "  2) make it permanent:          echo 'kernel.yama.ptrace_scope = 0' | "
        "sudo tee /etc/sysctl.d/90-wxextract-ptrace.conf",
        "  3) elevate only the key step, then re-run normally:  sudo wxextract keys",
    ]
    return "\n".join(lines)


def _pids_of_exe(binary: str) -> set[int]:
    out: set[int] = set()
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            if os.readlink(f"/proc/{entry.name}/exe") == binary:
                out.add(int(entry.name))
        except OSError:
            continue
    return out


def _tracer(binary: str, hook_off: int, timeout: float,
            probe_page1: bytes | None, launch_cmd: list[str] | None) -> bytes:
    """Run in a forked helper. Launch WeChat via `launch_cmd` (the launcher
    delegates to the portable/systemd daemon, so the wechat process is NOT
    our descendant and cannot be TRACEME'd), then PTRACE_ATTACH the fresh
    WeChat process the moment it appears — right after exec, before its
    first thread exists — with TRACECLONE covering every later thread. Arm
    INT3, wait for the login-time hit, return the passphrase. WeChat is
    left running (detached) in all outcomes."""
    import subprocess

    pre_existing = _pids_of_exe(binary)
    if pre_existing:
        raise CaptureError(
            "WeChat is already running — close it first (the passphrase can "
            "only be captured while WeChat performs its login)"
        )
    argv = launch_cmd if launch_cmd else [binary]
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)

    # wait for the daemon-spawned wechat process to appear
    deadline = time.monotonic() + min(timeout, 30.0)
    target = None
    while time.monotonic() < deadline:
        new = _pids_of_exe(binary) - pre_existing
        if new:
            target = min(new)
            break
        if proc.poll() is not None and not new:
            # launcher exited (it's just a client); keep polling for the daemon
            pass
        time.sleep(0.002)
    if target is None:
        raise CaptureError("WeChat did not start (no new wechat process appeared)")

    # attach: main thread first (single-threaded right after exec)
    try:
        _ptrace(PTRACE_ATTACH, target)
    except OSError as e:
        raise CaptureError(
            f"PTRACE_ATTACH on the WeChat process (pid {target}) failed: {e}\n"
            f"{_ptrace_guidance()}"
        ) from e
    os.waitpid(target, 0)  # attach SIGSTOP
    tracked: set[int] = {target}
    # any sibling threads that snuck in before the attach
    try:
        tids = [int(t) for t in os.listdir(f"/proc/{target}/task")]
    except OSError:
        tids = []
    for tid in tids:
        if tid in tracked:
            continue
        try:
            _ptrace(PTRACE_ATTACH, tid)
            os.waitpid(tid, 0)
            tracked.add(tid)
        except OSError:
            pass
    for tid in tracked:
        _ptrace(PTRACE_SETOPTIONS, tid, 0, _PTRACE_OPTS)

    base = _image_base(target, binary)
    bp_addr = base + hook_off
    word_addr = bp_addr & ~7
    word_shift = (bp_addr & 7) * 8
    orig_word = _peek(target, word_addr)
    _ptrace(PTRACE_POKEDATA, target, word_addr,
            (orig_word & ~(0xFF << word_shift)) | (INT3 << word_shift))
    armed = True
    log.info(f"attached to pid {target}; breakpoint armed at {bp_addr:#x}")
    for tid in tracked:
        _ptrace(PTRACE_CONT, tid, 0, 0)
    log.info("waiting for WeChat login — press the green 'Enter/Log in' "
             f"button in the WeChat window (timeout {timeout:.0f}s)")

    def teardown() -> None:
        """Resume + detach every traced thread, then SIGCONT the target.
        PTRACE_DETACH alone does NOT resume group-stopped threads — one
        queued-but-undrained stop event froze WeChat's UI solid early in
        development, so this must be exhaustive."""
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            try:
                pid, _st = os.waitpid(-1, os.WNOHANG | 0x40000000)
            except ChildProcessError:
                break
            if pid == 0:
                break
            tracked.add(pid)
            try:
                _ptrace(PTRACE_CONT, pid, 0, 0)
            except OSError:
                pass
        for tid in list(tracked):
            try:
                _ptrace(PTRACE_DETACH, tid)
            except OSError:
                pass
        try:
            os.kill(target, signal.SIGCONT)
        except OSError:
            pass

    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG | 0x40000000)  # __WALL
            except ChildProcessError as e:
                raise CaptureError("wechat process exited before login") from e
            if pid == 0:
                if time.monotonic() > deadline:
                    raise CaptureError(
                        f"timed out after {timeout:.0f}s waiting for WeChat login "
                        "(if a QR-code login is showing, scan it while capture runs)"
                    )
                time.sleep(0.02)
                continue
            if os.WIFEXITED(status) or os.WIFSIGNALED(status):
                tracked.discard(pid)
                if pid == target:
                    code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -os.WTERMSIG(status)
                    raise CaptureError(f"wechat exited during capture (status {code})")
                continue
            if not os.WIFSTOPPED(status):
                continue
            sig = os.WSTOPSIG(status)
            event = (status >> 16) & 0xFF
            tracked.add(pid)
            if event in (PTRACE_EVENT_FORK, PTRACE_EVENT_VFORK, PTRACE_EVENT_CLONE):
                try:
                    _ptrace(PTRACE_SETOPTIONS, pid, 0, _PTRACE_OPTS)
                except OSError:
                    pass
                _ptrace(PTRACE_CONT, pid, 0, 0)
                continue
            if sig == signal.SIGTRAP:
                regs = _getregs(pid)
                if armed and regs.rip - 1 == bp_addr:
                    captured = None
                    for cand in _passphrase_candidates(pid, regs):
                        if _valid_candidate(cand, probe_page1):
                            captured = cand
                            break
                    # restore the original instruction before doing anything else
                    _ptrace(PTRACE_POKEDATA, pid, word_addr, orig_word)
                    regs.rip = bp_addr
                    _setregs(pid, regs)
                    _ptrace(PTRACE_SINGLESTEP, pid, 0, 0)
                    try:
                        os.waitpid(pid, 0)  # consume the step trap
                    except ChildProcessError:
                        pass
                    if captured is not None:
                        _ptrace(PTRACE_CONT, pid, 0, 0)
                        return captured
                    # wrong 32-byte argument — re-arm and keep waiting
                    _ptrace(PTRACE_POKEDATA, pid, word_addr,
                            (orig_word & ~(0xFF << word_shift)) | (INT3 << word_shift))
                    _ptrace(PTRACE_CONT, pid, 0, 0)
                    continue
                _ptrace(PTRACE_CONT, pid, 0, 0)  # spurious trap
                continue
            if sig in (signal.SIGSTOP, signal.SIGTSTP, signal.SIGTTIN, signal.SIGTTOU):
                _ptrace(PTRACE_CONT, pid, 0, 0)  # never deliver job-control stops
                continue
            _ptrace(PTRACE_CONT, pid, 0, sig)  # deliver everything else
    finally:
        teardown()


def capture_passphrase(binary: Path, timeout: float = 150.0,
                       probe_page1: bytes | None = None,
                       launch_cmd: list[str] | None = None) -> bytes:
    """Launch WeChat (via `launch_cmd` — the full launcher chain, e.g.
    ``['/usr/bin/wechat']``; falls back to the bare binary) under a ptrace
    tracer and capture the 32-byte WCDB passphrase when WeChat performs its
    login-time key setup.

    Runs the tracer in a forked helper so ``waitpid(-1, __WALL)`` can never
    steal exit statuses from unrelated wxextract subprocesses. WeChat is
    left running after capture (detached).

    `probe_page1` (any known DB's first page) validates the candidate on the
    spot, guaranteeing a wrong 32-byte register read is never cached.
    """
    binary_str = str(binary)
    hook_off = find_hook_offset(binary)
    log.info(f"WCDB cipher hook offset: {hook_off:#x}")

    err_r, res_w = os.pipe()
    helper = os.fork()
    if helper == 0:
        os.close(err_r)
        code = 0
        try:
            ph = _tracer(binary_str, hook_off, timeout, probe_page1, launch_cmd)
            os.write(res_w, b"OK" + ph.hex().encode())
        except BaseException as e:  # helper must never raise into the parent
            try:
                os.write(res_w, b"ERR" + repr(e).encode())
            except OSError:
                pass
            code = 1
        finally:
            os.close(res_w)
            os._exit(code)

    os.close(res_w)
    payload = b""
    deadline = time.monotonic() + timeout + 30  # grace for detach+cleanup
    try:
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                try:
                    os.kill(helper, signal.SIGKILL)  # kernel detaches tracees
                except ProcessLookupError:
                    pass
                raise CaptureError("capture helper did not finish in time")
            r, _w, _e = select.select([err_r], [], [], min(remain, 0.5))
            if not r:
                continue
            chunk = os.read(err_r, 65536)
            if not chunk:
                break
            payload += chunk
    finally:
        os.close(err_r)
        try:
            os.waitpid(helper, 0)
        except ChildProcessError:
            pass
    if payload.startswith(b"OK") and len(payload) == 2 + 64:
        ph = bytes.fromhex(payload[2:].decode())
        # safety net: if any wechat thread is still job-stopped after the
        # helper's teardown, jog it now — a frozen thread froze the UI in
        # early development.
        for pid in _pids_of_exe(binary_str):
            try:
                os.kill(pid, signal.SIGCONT)
            except OSError:
                pass
        return ph
    msg = payload[3:].decode("utf-8", "replace") if payload.startswith(b"ERR") else payload.decode("utf-8", "replace")
    raise CaptureError(msg or "capture helper produced no result")


# ---------------------------------------------------------------------------
# 3. Key derivation (steady-state path) + passphrase cache
# ---------------------------------------------------------------------------

@dataclass
class PassphraseInfo:
    passphrase: bytes
    binary: str
    hook_off: int
    captured_at: str


def _derive_one(job: tuple[bytes, str, bytes]) -> tuple[str, str | None]:
    """Worker: (passphrase, rel, page1) → (rel, enc_key_hex or None)."""
    passphrase, rel, page1 = job
    key = pbkdf2_key(passphrase, page1[:16])
    return rel, key.hex() if verify_enc_key(key, page1) else None


def derive_keys(passphrase: bytes, db_files: list[DbFile],
                parallel: bool = True) -> dict[str, str]:
    """Derive + HMAC-validate a key for every DB. Only valid keys are
    returned ({rel_path: enc_key_hex}). Parallel across CPUs (~350 ms for
    ~23 DBs); falls back to serial when multiprocessing is unavailable.

    Uses the ``fork`` start method: no ``__main__`` re-import in workers
    (avoids re-running caller code under spawn/forkserver) — fine on Linux,
    which is the only supported platform anyway.
    """
    jobs = [(passphrase, db.rel, db.page1) for db in db_files]
    results: list[tuple[str, str | None]] = []
    if parallel and len(jobs) > 1:
        try:
            ctx = multiprocessing.get_context("fork")
            with ProcessPoolExecutor(max_workers=min(len(jobs), os.cpu_count() or 4),
                                     mp_context=ctx) as ex:
                results = list(ex.map(_derive_one, jobs))
        except Exception as e:  # fork/pool issues → serial
            log.warning(f"parallel derivation failed ({e}); falling back to serial")
            results = []
    if not results:
        results = [_derive_one(j) for j in jobs]
    return {rel: key for rel, key in results if key is not None}


def save_passphrase(path: Path, info: PassphraseInfo) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1,
        "passphrase": info.passphrase.hex(),
        "binary": info.binary,
        "hook_off": hex(info.hook_off),
        "captured_at": info.captured_at,
    }, indent=2))
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600 — as sensitive as the keys
    log.info(f"passphrase cached → {path} (mode 600)")


def load_passphrase(path: Path) -> bytes | None:
    try:
        data = json.loads(path.read_text())
        ph = bytes.fromhex(data["passphrase"])
        return ph if len(ph) == KEY_SZ else None
    except (OSError, ValueError, KeyError):
        return None
