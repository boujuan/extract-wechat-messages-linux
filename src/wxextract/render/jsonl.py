"""Full-fidelity JSONL output: one record per message, sorted chronologically.

Designed for downstream programmatic use (RAG ingestion, search, analytics).
No compression, no abbreviation — every field is preserved.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from wxextract.contacts import ContactRecord
from wxextract.messages import Message
from wxextract.render.common import body_of, build_identity, letter_for


def render(
    messages: list[Message],
    contact: ContactRecord,
    my_wxid: str,
    out_path: Path,
    my_label: str = "Me",
    *,
    squash: bool = False,
    redact: bool = False,
    stickers_to_emoji: bool = False,
) -> int:
    """Write JSONL; return number of records emitted."""
    identity = build_identity(messages, contact, my_wxid, my_label=my_label)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_path.open("w", encoding="utf-8") as f:
        # leading metadata record
        f.write(json.dumps({
            "_meta": True,
            "contact": {
                "alias": contact.alias,
                "username": contact.username,
                "display": contact.display_name,
                "nick_name": contact.nick_name,
                "remark": contact.remark,
            },
            "my_wxid": my_wxid,
            "glossary": identity.glossary,
            "message_count": len(messages),
            "range": {
                "first_ts": messages[0].create_time if messages else 0,
                "last_ts": messages[-1].create_time if messages else 0,
                "first_dt": datetime.fromtimestamp(messages[0].create_time).isoformat() if messages else None,
                "last_dt": datetime.fromtimestamp(messages[-1].create_time).isoformat() if messages else None,
            },
        }, ensure_ascii=False) + "\n")
        for m in messages:
            body = body_of(m, squash=squash, redact=redact, stickers_to_emoji=stickers_to_emoji)
            rec = {
                "id": m.local_id,
                "server_id": m.server_id,
                "ts": m.create_time,
                "dt": datetime.fromtimestamp(m.create_time).isoformat(),
                "sender": letter_for(identity, m),
                "sender_username": m.sender_username,
                "is_me": m.is_me,
                "type": m.type,
                "sub_type": m.sub_type,
                "kind": body.kind,
                "body": body.text,
            }
            if body.media:
                rec["media"] = body.media
            if body.reply is not None:
                rec["reply_to"] = asdict(body.reply)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    return n
