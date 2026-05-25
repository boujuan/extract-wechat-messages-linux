"""Locate the WeChat install, data root, and account folder.

WeChat 4.x on Linux (AUR `wechat-bin`) stores data in
`~/.local/share/WeChat_Data/xwechat_files/<wxid>_<suffix>/`. The Documents
mirror (`~/Documents/xwechat_files/`) is a legacy/secondary path that's often
stale. We pick whichever location has the most recently modified message DB.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Discovery:
    install_version: str | None
    binary_path: Path | None      # the actual /opt/wechat/wechat or equivalent
    launch_cmd: list[str]         # what to exec to start WeChat (`/usr/bin/wechat`, `flatpak run …`, etc.)
    data_root: Path
    account_dir: Path
    my_wxid: str
    install_kind: str = "unknown"  # aur | flatpak | manual | unknown

    def db_storage(self) -> Path:
        return self.account_dir / "db_storage"


_DATA_ROOT_CANDIDATES = (
    "~/.local/share/WeChat_Data/xwechat_files",
    "~/Documents/xwechat_files",
    "~/.var/app/com.tencent.wechat/data/xwechat_files",
)


def _query_pacman_version() -> str | None:
    if not shutil.which("pacman"):
        return None
    try:
        out = subprocess.run(
            ["pacman", "-Q", "wechat-bin"], capture_output=True, text=True, timeout=5
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip().split(maxsplit=1)[-1]
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _detect_install() -> tuple[str, Path | None, list[str]]:
    """Detect install variant. Returns (kind, binary_path, launch_cmd).

    kind ∈ {aur, flatpak, manual, unknown}.
    binary_path = the actual binary that holds WCDB in memory (for PID detection).
    launch_cmd  = the command list to spawn WeChat (Popen-friendly).
    """
    # AUR wechat-bin (also CachyOS, Manjaro AUR)
    if Path("/opt/wechat/wechat").is_file():
        launcher = "/usr/bin/wechat" if Path("/usr/bin/wechat").is_file() else "/opt/wechat/wechat"
        return ("aur", Path("/opt/wechat/wechat"), [launcher])

    # Flatpak — `flatpak info` returns 0 if installed
    if shutil.which("flatpak"):
        try:
            out = subprocess.run(
                ["flatpak", "info", "com.tencent.wechat"],
                capture_output=True, timeout=3,
            )
            if out.returncode == 0:
                # Flatpak's main wechat binary lives inside the sandbox; we can only see the wrapper PID
                wrapper = shutil.which("flatpak")
                return ("flatpak", None, [wrapper, "run", "com.tencent.wechat"])
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Manual install — anything named wechat/weixin on PATH
    for name in ("wechat", "weixin"):
        cand = shutil.which(name)
        if cand:
            return ("manual", Path(cand), [cand])

    return ("unknown", None, [])


def _find_binary() -> Path | None:
    """Back-compat helper (kept for older callers): returns the binary."""
    return _detect_install()[1]


def _newest_message_mtime(root: Path) -> float:
    """Return max mtime under <root>/<*>/db_storage/message/*.db; 0 if none."""
    best = 0.0
    if not root.is_dir():
        return best
    for acc in root.iterdir():
        msg_dir = acc / "db_storage" / "message"
        if not msg_dir.is_dir():
            continue
        for db in msg_dir.glob("*.db"):
            try:
                m = db.stat().st_mtime
                if m > best:
                    best = m
            except OSError:
                pass
    return best


def find_data_root() -> Path:
    """Return the path most likely to be the active xwechat_files root."""
    expanded = [Path(p).expanduser() for p in _DATA_ROOT_CANDIDATES]
    scored = [(p, _newest_message_mtime(p)) for p in expanded]
    scored.sort(key=lambda kv: kv[1], reverse=True)
    if not scored or scored[0][1] == 0.0:
        raise RuntimeError(
            "no WeChat data root found. Looked under:\n  "
            + "\n  ".join(str(p) for p in expanded)
        )
    return scored[0][0]


def list_accounts(data_root: Path) -> list[Path]:
    """Account folders look like 'wxid_xxxxx_yyyy'."""
    if not data_root.is_dir():
        return []
    return sorted(
        p for p in data_root.iterdir()
        if p.is_dir() and p.name.startswith("wxid_") and (p / "db_storage").is_dir()
    )


def discover(prefer_data_root: Path | None = None, prefer_account: Path | None = None) -> Discovery:
    """One-shot discovery. Returns a populated Discovery or raises."""
    data_root = prefer_data_root or find_data_root()
    accounts = list_accounts(data_root)
    if not accounts:
        raise RuntimeError(f"no wxid_* accounts under {data_root}")
    if prefer_account is not None:
        if prefer_account not in accounts:
            raise RuntimeError(f"requested account {prefer_account} not under {data_root}")
        account = prefer_account
    elif len(accounts) == 1:
        account = accounts[0]
    else:
        # newest by mtime
        account = max(accounts, key=lambda p: p.stat().st_mtime)
    # the dir name is wxid_<id>_<suffix>; strip suffix
    name = account.name
    if "_" in name and name.count("_") >= 2:
        my_wxid = name.rsplit("_", 1)[0]
    else:
        my_wxid = name
    kind, binary, launch_cmd = _detect_install()
    return Discovery(
        install_version=_query_pacman_version(),
        binary_path=binary,
        launch_cmd=launch_cmd,
        data_root=data_root,
        account_dir=account,
        my_wxid=my_wxid,
        install_kind=kind,
    )
