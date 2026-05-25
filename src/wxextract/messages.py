"""Walk a target contact's per-talker message table, decompress, type-decode,
and yield Message dataclasses.

Type system:
  local_type is a 64-bit integer encoded as (subType << 32) | type.
  type  1     = text
  type  3     = image
  type 34     = voice
  type 43     = video
  type 47     = sticker
  type 49     = appmsg (subType picks: 4=applet, 5=link, 6=file, 57=quoted-reply, 62=forward)
  type 50     = call (VoIP)
  type 10000  = system message

`message_content` is zstd-compressed when WCDB_CT_message_content == 4
(magic `28b52ffd`).
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import zstandard as zstd

from wxextract.contacts import ContactRecord

log = logging.getLogger("wxextract.messages")

ZSTD_DECOMPRESSOR = zstd.ZstdDecompressor()

# message type constants (low 32 bits of local_type)
TYPE_TEXT = 1
TYPE_IMAGE = 3
TYPE_VOICE = 34
TYPE_VIDEO = 43
TYPE_STICKER = 47
TYPE_APPMSG = 49
TYPE_CALL = 50
TYPE_SYSTEM = 10000

# appmsg subtypes (high 32 bits of local_type when type==49)
APPMSG_APPLET = 4
APPMSG_LINK = 5
APPMSG_FILE = 6
APPMSG_QUOTE = 57
APPMSG_FORWARD = 62


@dataclass
class Message:
    local_id: int
    server_id: int
    create_time: int           # unix epoch seconds
    sender_id: int             # Name2Id rowid
    sender_username: str       # resolved internal id (wxid_xxx or empty)
    is_me: bool
    type: int
    sub_type: int
    raw_local_type: int
    content: str               # decompressed message_content (UTF-8 string)
    source: str                # decompressed source field (often XML)
    status: int


def decode_local_type(local_type: int) -> tuple[int, int]:
    return local_type & 0xFFFFFFFF, local_type >> 32


def decompress_field(blob: object, ct: int) -> str:
    if blob is None:
        return ""
    if ct == 4 and isinstance(blob, (bytes, bytearray)):
        try:
            return ZSTD_DECOMPRESSOR.decompress(blob, max_output_size=20 * 1024 * 1024).decode(
                "utf-8", errors="replace"
            )
        except zstd.ZstdError:
            return blob.decode("utf-8", errors="replace")
    if isinstance(blob, (bytes, bytearray)):
        return blob.decode("utf-8", errors="replace")
    return str(blob)


def _is_recall(typ: int, content: str) -> bool:
    """Two flavors: XML <sysmsg type='revokemsg'> and the plain 'X recalled a message'."""
    if typ == TYPE_SYSTEM:
        if "<sysmsg" in content and 'type="revokemsg"' in content:
            return True
        if "recalled a message" in content or "recalled this message" in content or "撤回" in content:
            return True
    return False


def load_sender_map(message_db: Path) -> dict[int, str]:
    """Read Name2Id from a shard: rowid → user_name."""
    conn = sqlite3.connect(str(message_db))
    try:
        rows = conn.execute("SELECT rowid, user_name FROM Name2Id").fetchall()
    finally:
        conn.close()
    return {int(rid): (uname or "") for rid, uname in rows}


def extract(
    contact: ContactRecord,
    my_wxid: str,
    *,
    skip_recalls: bool = True,
) -> Iterator[Message]:
    """Yield every message in `contact.message_table` in chronological order."""
    if not contact.message_db or not contact.message_table:
        raise RuntimeError(f"contact {contact.username} has no message table located")
    sender_map = load_sender_map(contact.message_db)
    conn = sqlite3.connect(str(contact.message_db))
    try:
        cur = conn.execute(
            f"""
            SELECT local_id, server_id, local_type, real_sender_id, create_time, status,
                   source, message_content,
                   WCDB_CT_source, WCDB_CT_message_content
            FROM {contact.message_table}
            ORDER BY create_time ASC, sort_seq ASC, local_id ASC
            """
        )
        for row in cur:
            (local_id, server_id, lt, sid, ts, status,
             source_blob, content_blob, src_ct, ct_ct) = row
            typ, sub = decode_local_type(int(lt))
            content = decompress_field(content_blob, int(ct_ct or 0))
            if skip_recalls and _is_recall(typ, content):
                continue
            source = decompress_field(source_blob, int(src_ct or 0))
            sender_username = sender_map.get(int(sid), "")
            yield Message(
                local_id=int(local_id),
                server_id=int(server_id or 0),
                create_time=int(ts or 0),
                sender_id=int(sid),
                sender_username=sender_username,
                is_me=(sender_username == my_wxid),
                type=typ,
                sub_type=sub,
                raw_local_type=int(lt),
                content=content,
                source=source,
                status=int(status or 0),
            )
    finally:
        conn.close()
