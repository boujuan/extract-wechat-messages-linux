"""Loader for WhatsApp data exported by Parse_Whatsapp_LLM in the
`wxextract-whatsapp/1` JSON schema.

The actual `.txt` parsing lives in the Parse_Whatsapp_LLM project — this
module just consumes the JSON intermediate produced by its `--format
wxextract` emitter and converts it to wxextract's in-memory
`ContactRecord` + `list[Message]` shapes.
"""
from __future__ import annotations

import json
from pathlib import Path

from wxextract.contacts import ContactRecord
from wxextract.messages import Message

SUPPORTED_SCHEMA = "wxextract-whatsapp/1"


def load_whatsapp_json(path: Path) -> tuple[ContactRecord, list[Message], str]:
    """Read a wxextract-whatsapp JSON file and return (contact, messages, my_label).

    `my_label` is the name of the "me" sender as the emitter recorded
    it — callers should plumb it into stats.compute / report.render
    so the rendered labels match the source.
    """
    raw = path.read_text(encoding="utf-8")
    doc = json.loads(raw)
    schema = doc.get("schema")
    if schema != SUPPORTED_SCHEMA:
        raise ValueError(
            f"{path}: unsupported schema {schema!r}, expected {SUPPORTED_SCHEMA!r}. "
            f"Re-run Parse_Whatsapp_LLM with --format wxextract."
        )

    cdict = doc["contact"]
    contact = ContactRecord(
        username=cdict["username"],
        alias=cdict.get("alias", ""),
        nick_name=cdict.get("nick_name", "") or cdict.get("display_name", ""),
        remark=cdict.get("remark", ""),
        local_type=int(cdict.get("local_type", 1)),
        message_count=int(cdict.get("message_count", 0)),
        first_ts=int(cdict.get("first_message_ts", 0)),
        last_ts=int(cdict.get("last_message_ts", 0)),
        source=cdict.get("source", "whatsapp"),
    )

    messages: list[Message] = []
    for m in doc["messages"]:
        is_me = bool(m["is_me"])
        # Normalize sender_username so stats.compute's
        # `sender_username == contact.username` check classifies
        # non-me messages as "them". The original WhatsApp display name
        # is still available via contact.display_name.
        sender_username = m["sender_username"] if is_me else contact.username
        messages.append(Message(
            local_id=int(m["local_id"]),
            server_id=int(m.get("server_id", 0)),
            create_time=int(m["create_time"]),
            sender_id=0,
            sender_username=sender_username,
            is_me=is_me,
            type=int(m["type"]),
            sub_type=int(m.get("sub_type", 0)),
            raw_local_type=int(m.get("raw_local_type", m["type"])),
            content=m.get("content", ""),
            source=m.get("source", "whatsapp"),
            status=int(m.get("status", 3)),
        ))

    my_label = doc.get("my_label", "Me")
    return contact, messages, my_label


def build_combined(
    a: tuple[ContactRecord, list[Message]],
    b: tuple[ContactRecord, list[Message]],
    *,
    display_name: str,
    username: str | None = None,
    alias: str | None = None,
) -> tuple[ContactRecord, list[Message]]:
    """Merge two (contact, messages) pairs into a synthetic combined view.

    Used when the same real-world person appears across two sources
    (e.g. WhatsApp Alice + WeChat Alice). Messages are concatenated
    and re-sorted by create_time; the synthetic contact carries
    `source="combined"`.
    """
    from dataclasses import replace
    (ca, ma) = a
    (cb, mb) = b
    # The synthetic contact's username unifies sender_username for
    # non-me messages from both sources — otherwise stats.compute would
    # miss "them" classifications when each source uses its own
    # username scheme.
    new_username = username or f"combined:{ca.username}+{cb.username}"
    def _rewrite(m):
        if m.is_me:
            return m
        return replace(m, sender_username=new_username)
    merged_msgs = sorted(
        [_rewrite(m) for m in (ma + mb)],
        key=lambda m: m.create_time,
    )
    # Re-number local_id so the merged stream has a consistent ordering.
    merged_msgs = [replace(m, local_id=i) for i, m in enumerate(merged_msgs, start=1)]
    # Both contacts contribute to the synthetic contact's username
    # so any downstream code that keys off contact.username doesn't
    # collide with either source.
    contact = ContactRecord(
        username=new_username,
        alias=alias or "combined",
        nick_name=display_name,
        remark="",
        local_type=1,
        message_count=len(merged_msgs),
        first_ts=merged_msgs[0].create_time if merged_msgs else 0,
        last_ts=merged_msgs[-1].create_time if merged_msgs else 0,
        source="combined",
    )
    # The merged messages keep their per-message `is_me` flag because
    # that's already set correctly from each source. The combined
    # contact's display name overrides the per-message rendering.
    return contact, merged_msgs
