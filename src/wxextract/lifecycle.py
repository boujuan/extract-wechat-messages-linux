"""Detect, close, and re-launch WeChat.

The AUR build runs `/opt/wechat/wechat` (the actual binary) wrapped by
`/usr/bin/wechat` which sets up the bwrap sandbox. Closing means sending
SIGTERM to the main `/opt/wechat/wechat` process; the sandbox supervisor
and helper processes follow.
"""
from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

log = logging.getLogger("wxextract.lifecycle")


# Defaults — used when no Discovery is provided. AUR `wechat-bin` paths.
DEFAULT_MAIN_BINARY = "/opt/wechat/wechat"
DEFAULT_LAUNCH_CMD = ["/usr/bin/wechat"]
_ALL_PATTERNS = ("wechat", "weixin", "RadiumWMPF")


def _proc_pids() -> list[tuple[int, str, str]]:
    """Return (pid, comm, exe_basename) for every /proc/<pid>/ we can inspect."""
    out: list[tuple[int, str, str]] = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            with open(f"/proc/{pid}/comm") as f:
                comm = f.read().strip()
        except OSError:
            continue
        exe = ""
        try:
            exe = os.path.basename(os.readlink(f"/proc/{pid}/exe"))
        except OSError:
            pass
        out.append((pid, comm, exe))
    return out


def main_wechat_pid(binary: str | None = None) -> int | None:
    """Return the PID of the main wechat binary process, or None.

    `binary` is the absolute path of the actual binary (from Discovery.binary_path).
    Falls back to the AUR default if unset (back-compat).
    """
    expected = binary or DEFAULT_MAIN_BINARY
    for pid, comm, _exe in _proc_pids():
        if comm.lower() == "wechat":
            try:
                exe = os.readlink(f"/proc/{pid}/exe")
            except OSError:
                exe = ""
            if exe == expected:
                return pid
    # Flatpak sandbox: no host-side exe match. Fall back to any /comm=wechat
    if binary is None:
        return None  # don't broaden the default-path behavior
    for pid, comm, _exe in _proc_pids():
        if comm.lower() == "wechat":
            return pid
    return None


def wechat_running() -> list[int]:
    """All PIDs that look like part of the WeChat process group (main + helpers).
    Excludes our own process and obvious script interpreters.
    """
    me = os.getpid()
    pids: list[int] = []
    for pid, comm, exe in _proc_pids():
        if pid == me:
            continue
        comm_l = comm.lower()
        exe_l = exe.lower()
        if any(p.lower() in comm_l or p.lower() in exe_l for p in _ALL_PATTERNS):
            # exclude python/sh/etc that happen to mention 'wechat' in argv
            if exe_l and any(exe_l.startswith(s) for s in ("python", "bash", "sh", "zsh")):
                continue
            pids.append(pid)
    return sorted(pids)


def _all_dead(pids: list[int]) -> bool:
    for pid in pids:
        try:
            os.kill(pid, 0)
            return False
        except ProcessLookupError:
            continue
        except PermissionError:
            return False
    return True


def close_wechat(timeout: float = 10.0, poll: float = 0.25,
                 binary: str | None = None) -> bool:
    """Send SIGTERM to the main WeChat process; wait for the whole group to exit.

    Returns True if WeChat is fully closed (or wasn't running). False if
    something is still alive after `timeout`; caller can decide to escalate.
    """
    main_pid = main_wechat_pid(binary=binary)
    if main_pid is None:
        # maybe orphaned helpers? scan and signal anyway
        leftover = wechat_running()
        if not leftover:
            return True
        log.warning(f"no main wechat PID but found helpers: {leftover}")
        for p in leftover:
            try:
                os.kill(p, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
    else:
        log.info(f"sending SIGTERM to wechat main PID {main_pid}")
        try:
            os.kill(main_pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError) as e:
            log.warning(f"could not SIGTERM main PID: {e}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not wechat_running():
            log.info("wechat fully closed")
            return True
        time.sleep(poll)
    remaining = wechat_running()
    log.warning(f"wechat still alive after {timeout}s: {remaining}")
    return False


def force_kill(pids: list[int] | None = None) -> None:
    targets = pids if pids is not None else wechat_running()
    for p in targets:
        try:
            os.kill(p, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def launch_wechat(cmd: list[str] | None = None) -> int | None:
    """Start WeChat in the background, detached from our session.

    `cmd` is the launch argv (from Discovery.launch_cmd); falls back to AUR.
    """
    cmd = cmd or DEFAULT_LAUNCH_CMD
    if not cmd:
        log.warning("no launch command available; cannot auto-launch")
        return None
    head = cmd[0]
    # if it's a path, verify it exists
    if head.startswith("/") and not Path(head).is_file():
        log.warning(f"{head} not found; cannot auto-launch")
        return None
    log.info(f"launching {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc.pid
