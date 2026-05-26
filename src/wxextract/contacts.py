"""Load contacts from the decrypted contact.db and annotate with per-talker
message-table stats (count, first/last timestamps, shard path).

Internal username format: regular contacts are `wxid_*`, OpenIM contacts are
hex-prefixed (e.g. `F100000…`), group chats end in `@chatroom`. For v1 we
surface only the first two (local_type 1 + 2).
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("wxextract.contacts")


@dataclass
class ContactRecord:
    username: str         # internal wxid (e.g. wxid_xxx or F1000...)
    alias: str            # public WeChat ID (may be empty)
    nick_name: str
    remark: str           # user-set alias (preferred display name)
    local_type: int       # 1=normal, 2=stranger, 3=blocked, etc.
    message_count: int = 0
    first_ts: int = 0
    last_ts: int = 0
    message_db: Path | None = None
    message_table: str | None = None

    @property
    def display_name(self) -> str:
        return self.remark or self.nick_name or self.alias or self.username

    def md5_table(self) -> str:
        return "Msg_" + hashlib.md5(self.username.encode()).hexdigest()


def _table_exists(db: Path, name: str) -> bool:
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _table_stats(db: Path, table: str) -> tuple[int, int, int]:
    """Return (count, min_create_time, max_create_time)."""
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            f"SELECT COUNT(*), COALESCE(MIN(create_time),0), COALESCE(MAX(create_time),0) FROM {table}"
        ).fetchone()
        return (int(row[0]), int(row[1]), int(row[2]))
    finally:
        conn.close()


def load_contacts(plain_dbs: Path, include_unmessaged: bool = False) -> list[ContactRecord]:
    """Load contacts joined with message-table stats.

    `plain_dbs` is the root of decrypted databases (mirrors db_storage/).
    """
    contact_db = plain_dbs / "contact" / "contact.db"
    if not contact_db.is_file():
        raise RuntimeError(f"contact.db not found at {contact_db}")

    message_dbs = sorted((plain_dbs / "message").glob("message_*.db"))
    # filter out FTS sidecars
    message_dbs = [p for p in message_dbs if "fts" not in p.name and "resource" not in p.name]
    log.debug(f"message shards: {[p.name for p in message_dbs]}")

    conn = sqlite3.connect(str(contact_db))
    try:
        rows = conn.execute(
            "SELECT username, alias, nick_name, remark, local_type FROM contact "
            "WHERE username IS NOT NULL AND username != ''"
        ).fetchall()
    finally:
        conn.close()

    out: list[ContactRecord] = []
    for username, alias, nick_name, remark, local_type in rows:
        if local_type not in (1, 2):
            continue
        rec = ContactRecord(
            username=username,
            alias=alias or "",
            nick_name=nick_name or "",
            remark=remark or "",
            local_type=int(local_type),
        )
        table = rec.md5_table()
        for db in message_dbs:
            if _table_exists(db, table):
                rec.message_db = db
                rec.message_table = table
                rec.message_count, rec.first_ts, rec.last_ts = _table_stats(db, table)
                break
        if include_unmessaged or rec.message_count > 0:
            out.append(rec)
    out.sort(key=lambda r: (-r.last_ts, -r.message_count))
    return out


def find_by_alias(records: list[ContactRecord], alias: str) -> ContactRecord | None:
    alias_l = alias.lower()
    for r in records:
        if r.alias.lower() == alias_l:
            return r
    return None


def find_by_username(records: list[ContactRecord], username: str) -> ContactRecord | None:
    for r in records:
        if r.username == username:
            return r
    return None
