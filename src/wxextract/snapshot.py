"""rsync the WeChat encrypted DB tree into the workspace.

We mirror the entire xwechat_files account folder (including db_storage/,
config/, msg/, resource/) so we can later resolve media references without
re-opening WeChat. WAL + SHM files are included so SQLCipher can replay
pending writes during decryption.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("wxextract.snapshot")


def snapshot(src_account_dir: Path, dst_root: Path) -> Path:
    """rsync src_account_dir/ → dst_root/<account_name>/.

    Returns the destination account directory path.
    """
    dst_account = dst_root / src_account_dir.name
    dst_account.mkdir(parents=True, exist_ok=True)
    if shutil.which("rsync") is None:
        raise RuntimeError("rsync not found in PATH")
    log.info(f"snapshot: {src_account_dir} → {dst_account}")
    res = subprocess.run(
        [
            "rsync",
            "-aH",
            "--delete",
            "--info=stats1",
            f"{src_account_dir}/",
            f"{dst_account}/",
        ],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"rsync failed (exit {res.returncode}): {res.stderr.strip()}")
    log.info(res.stdout.strip().splitlines()[-1] if res.stdout.strip() else "rsync ok")
    return dst_account


def db_storage_of(account_dir: Path) -> Path:
    return account_dir / "db_storage"
