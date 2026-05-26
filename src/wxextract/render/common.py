"""Shared transformation pipeline for all renderers.

- Body extraction (parse the XML payload of media/appmsg messages once)
- Sessionizer (split by silence gap)
- IdentityNormalizer (map sender username → short letter + glossary)
- TurnMerger (collapse same-sender adjacent messages — used by Style B)
- MediaPlaceholder formatting (image/voice/video/sticker/file/link/call)
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from wxextract.contacts import ContactRecord
from wxextract.messages import (
    APPMSG_APPLET,
    APPMSG_FILE,
    APPMSG_FORWARD,
    APPMSG_LINK,
    APPMSG_QUOTE,
    TYPE_APPMSG,
    TYPE_CALL,
    TYPE_IMAGE,
    TYPE_STICKER,
    TYPE_SYSTEM,
    TYPE_TEXT,
    TYPE_VIDEO,
    TYPE_VOICE,
    Message,
)

# ---------------------------------------------------------------------------
# Body extraction — produce a renderer-agnostic structured representation
# ---------------------------------------------------------------------------


@dataclass
class ReplyTarget:
    display: str = ""
    sender_username: str = ""
    ts: int = 0
    content: str = ""
    type: int = 0


@dataclass
class Body:
    text: str = ""                    # human-readable body for compact renderers
    kind: str = "text"                # text | image | voice | video | sticker | appmsg | call | system | unknown
    media: dict = field(default_factory=dict)  # type-specific metadata
    reply: ReplyTarget | None = None  # populated for quoted-reply messages


def _parse_xml(content: str) -> ET.Element | None:
    if not content:
        return None
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        return None


def _image_body(content: str) -> Body:
    md5 = re.search(r'md5="([^"]+)"', content or "")
    aes = re.search(r'aeskey="([^"]+)"', content or "")
    body = Body(text="[image]", kind="image")
    if md5:
        body.media["md5"] = md5.group(1)
    if aes:
        body.media["aeskey"] = aes.group(1)
    return body


def _voice_body(content: str) -> Body:
    m = re.search(r'voicelength="(\d+)"', content or "")
    body = Body(text="[voice]", kind="voice")
    if m:
        ms = int(m.group(1))
        body.media["duration_ms"] = ms
        body.text = f"[voice {ms / 1000:.1f}s]"
    return body


def _video_body(content: str) -> Body:
    m = re.search(r'playlength="(\d+)"', content or "")
    body = Body(text="[video]", kind="video")
    if m:
        secs = int(m.group(1))
        body.media["duration_s"] = secs
        body.text = f"[video {secs}s]"
    return body


def _sticker_body(content: str) -> Body:
    m = re.search(r'md5\s*=\s*"([^"]+)"', content or "")
    body = Body(text="[sticker]", kind="sticker")
    if m:
        body.media["md5"] = m.group(1)
    return body


def _call_body(content: str) -> Body:
    end = re.search(r"Call ended (\S+)", content or "")
    canceled = "Call canceled" in (content or "") or "Canceled" in (content or "")
    if end:
        dur = end.group(1)
        return Body(text=f"[call {dur}]", kind="call", media={"duration": dur})
    if canceled:
        return Body(text="[call canceled]", kind="call", media={"status": "canceled"})
    return Body(text="[call]", kind="call")


def _system_body(content: str) -> Body:
    """Render a type=10000 system message. Always returns a Body (no None);
    recall filtering happens upstream in messages.extract()."""
    text = (content or "").strip()
    if "recalled a message" in text or "recalled this message" in text or "撤回" in text:
        return Body(text="[sys:recall]", kind="system", media={"sysmsg_type": "recall"})
    if text.startswith("<?xml") or text.startswith("<sysmsg"):
        root = _parse_xml(text)
        if root is not None:
            sm_type = root.get("type") or "msg"
            inner = root.findtext(".//content") or ""
            payload = inner.strip() if inner else ""
            return Body(text=f"[sys:{sm_type}{(' ' + payload) if payload else ''}]", kind="system",
                        media={"sysmsg_type": sm_type, "payload": payload})
    return Body(text=f"[sys] {text}" if text else "[sys]", kind="system")


def _appmsg_body(content: str, sub: int, sender_label: str = "") -> Body:
    root = _parse_xml(content)
    if root is None:
        return Body(text=f"[appmsg:{sub}]", kind="appmsg")
    title = (root.findtext(".//title") or "").strip()
    url = (root.findtext(".//url") or "").strip()
    if sub == APPMSG_QUOTE:
        ref = root.find(".//refermsg")
        reply = ReplyTarget()
        if ref is not None:
            reply.display = (ref.findtext("displayname") or "").strip()
            reply.sender_username = (ref.findtext("fromusr") or ref.findtext("chatusr") or "").strip()
            ts_raw = ref.findtext("createtime") or ""
            reply.ts = int(ts_raw) if ts_raw.isdigit() else 0
            reply.type = int(ref.findtext("type") or 1)
            qcontent = (ref.findtext("content") or "").strip()
            # nested appmsg → distill to its title
            if reply.type == TYPE_APPMSG and (qcontent.startswith("<msg>") or qcontent.startswith("<?xml")):
                qroot = _parse_xml(qcontent if qcontent.startswith("<?xml") else qcontent)
                if qroot is not None:
                    qtitle = (qroot.findtext(".//title") or "").strip()
                    qcontent = qtitle or qcontent
            elif reply.type == TYPE_IMAGE:
                qcontent = "[image]"
            elif reply.type == TYPE_VOICE:
                qcontent = "[voice]"
            elif reply.type == TYPE_VIDEO:
                qcontent = "[video]"
            elif reply.type == TYPE_STICKER:
                qcontent = "[sticker]"
            reply.content = qcontent
        return Body(text=title, kind="appmsg", media={"sub_type": sub}, reply=reply)
    if sub == APPMSG_LINK:
        return Body(text=f"[link: {title}{(' — ' + url) if url else ''}]", kind="appmsg",
                    media={"sub_type": sub, "title": title, "url": url})
    if sub == APPMSG_FILE:
        fname = (root.findtext(".//appattach/fileext") or "").strip()
        nbytes = (root.findtext(".//appattach/totallen") or "").strip()
        return Body(text=f"[file: {title}{('.' + fname) if fname else ''}{(' (' + nbytes + ' B)') if nbytes else ''}]",
                    kind="appmsg",
                    media={"sub_type": sub, "title": title, "filename_ext": fname, "size": nbytes})
    if sub == APPMSG_APPLET:
        return Body(text=f"[applet: {title}{(' — ' + url) if url else ''}]", kind="appmsg",
                    media={"sub_type": sub, "title": title, "url": url})
    if sub == APPMSG_FORWARD:
        return Body(text=f"[forward: {title}]", kind="appmsg", media={"sub_type": sub, "title": title})
    return Body(text=f"[appmsg:{sub} {title}]", kind="appmsg", media={"sub_type": sub, "title": title})


_EMOJI_RUN_RE = re.compile(r"(\[[A-Za-z]{2,16}\])\1{2,}")

# WeChat sticker/emoji tag → Unicode replacement.
# Covers the entire default WeChat "Classic" / "Animated" emoji set as observed
# in real chats (frequency-ordered for the common ones). Unknown tags pass
# through unchanged so we never lose information.
_STICKER_TO_EMOJI = {
    # smiles / laughs
    "[Smile]": "🙂", "[Grin]": "😁", "[Chuckle]": "😄", "[Laugh]": "😆",
    "[Joyful]": "😂", "[ROFL]": "🤣", "[Snicker]": "🤭",
    "[Happy]": "😊", "[Blush]": "😊", "[Shy]": "☺️", "[Flushed]": "😳",
    # tongues / playful
    "[Tongue]": "😝", "[Drool]": "🤤", "[Slobber]": "🤤",
    "[Smirk]": "😏", "[Sneer]": "😏", "[Trick]": "😏", "[Sly]": "😏",
    "[CoolGuy]": "😎", "[Cool]": "😎",
    # sad / hurt
    "[Sad]": "😢", "[Cry]": "😭", "[Sob]": "😭", "[Tears]": "😭",
    "[TearingUp]": "🥲", "[Whimper]": "🥺", "[Hurt]": "😖",
    "[Disappointed]": "😞", "[LetDown]": "😞", "[Concerned]": "😟",
    "[Broken]": "💔", "[HeartBroken]": "💔",
    # tired / sleepy
    "[Sleep]": "😴", "[Sleepy]": "😪", "[Drowsy]": "😪", "[Yawn]": "🥱",
    # surprise / shock
    "[Wow]": "😮", "[Surprised]": "😲", "[Surprise]": "😮",
    "[Shocked]": "😨", "[Terror]": "😱", "[Tremble]": "😨",
    # awkward / sweaty
    "[Sweat]": "😅", "[ColdSweat]": "😰", "[Awkward]": "😅", "[Whew]": "😅",
    # dizzy / dazed
    "[Dizzy]": "😵", "[Shrunken]": "🥴", "[Toasted]": "🥴",
    # angry
    "[Angry]": "😠", "[Rage]": "😡", "[Scowl]": "😤", "[Pout]": "😤",
    "[Mad]": "😡",
    # mouth / face
    "[Speechless]": "😑", "[Quiet]": "🤫", "[Slight]": "😐",
    "[Facepalm]": "🤦", "[Grimace]": "😬",
    "[Puke]": "🤮", "[Sick]": "🤒",
    # love / kiss
    "[Heart]": "❤️", "[LoveAtFirstSight]": "😍", "[Kiss]": "😘",
    "[BlowKiss]": "😘", "[Lips]": "💋",
    # think / smart
    "[Think]": "🤔", "[Smart]": "🤓", "[Nerd]": "🤓", "[Idea]": "💡",
    # hands / gestures
    "[Yeah]": "👍", "[ThumbsUp]": "👍", "[ThumbDown]": "👎",
    "[OK]": "👌", "[Clap]": "👏", "[Strong]": "💪", "[GoForIt]": "💪",
    "[Pray]": "🙏", "[Worship]": "🙏", "[Bow]": "🙇",
    "[Wave]": "👋", "[Bye]": "👋",
    "[Shake]": "🤝", "[Handshake]": "🤝",
    "[Salute]": "🫡", "[Respect]": "🫡",
    "[NoProb]": "🤷", "[Shrug]": "🤷", "[Whatever]": "🤷",
    "[Fist]": "✊", "[Punch]": "👊",
    # eyes
    "[Onlooker]": "👀", "[Watch]": "👀",
    "[Cleaver]": "🔪", "[Knife]": "🔪",
    # food / drink
    "[Cake]": "🎂", "[Gift]": "🎁", "[Rose]": "🌹",
    "[Watermelon]": "🍉", "[Banana]": "🍌", "[Beer]": "🍺",
    "[Coffee]": "☕", "[Tea]": "🍵", "[BabyBottle]": "🍼",
    "[Bread]": "🍞", "[IceCream]": "🍦",
    # nature
    "[Sun]": "☀️", "[Moon]": "🌙", "[Star]": "⭐",
    "[Cloud]": "☁️", "[Rain]": "🌧️", "[Snow]": "❄️",
    # animals
    "[Doge]": "🐶", "[Cat]": "🐱", "[Pig]": "🐷",
    # objects
    "[Fire]": "🔥", "[Bomb]": "💣", "[Skull]": "💀",
    "[Music]": "🎵", "[Headphone]": "🎧",
    "[Football]": "⚽", "[Soccer]": "⚽", "[Basketball]": "🏀",
    "[PingPong]": "🏓", "[Mahjong]": "🀄",
    "[Run]": "🏃", "[Walk]": "🚶",
    "[Hug]": "🤗",
    # misc oddballs
    "[Hammer]": "🔨", "[Tools]": "🔧",
    "[Poop]": "💩", "[NosePick]": "🤧",
    "[Smug]": "😏", "[OMG]": "😱", "[Hey]": "🙋",
    "[Word]": "💬",
    "[Lol]": "😆", "[Lmao]": "🤣", "[Wtf]": "😳",
}
_STICKER_RE = re.compile(r"\[[A-Za-z]{2,20}\]")


# Compact legend shown once per file (or per chunk). Explains the file's
# structure and shorthand so an LLM can interpret without guessing.
LEGEND_TEXT = (
    'Chronological 1-on-1 chat. Lines: "<sender> <time> <body>". '
    'Time shrinks within a session: H:M:S (first) → :M:S (same hour) → :S (same min). '
    '";" joins same-sender messages within 60s. '
    '"[↩X H:M \\"…\\"]" = reply to sender X with preview. '
    '"=YYYY-MM-DD" = new day. "=H:M +Nh" = mid-day gap resume. '
    'Media placeholders: [img] [voice Ns] [video Ns] [sticker] [call Nm] [file:name] [link:title] [sys:…]. '
    '[Word]-style tags like [Chuckle] are WeChat built-in stickers (mapped to Unicode emoji where possible).'
)
_REDACT_RULES = (
    ("email",  re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("phone",  re.compile(r"\b(?:\+?\d[\d\s().-]{8,}\d)\b")),
    ("iban",   re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")),
    ("digits", re.compile(r"\b\d{12,}\b")),
)


def sticker_to_emoji(text: str) -> str:
    """Replace known WeChat sticker tags with Unicode equivalents. Unknown tags untouched."""
    if not text or "[" not in text:
        return text
    def repl(m):
        tag = m.group(0)
        return _STICKER_TO_EMOJI.get(tag, tag)
    return _STICKER_RE.sub(repl, text)


def squash_emoji_runs(text: str) -> str:
    """[Chuckle][Chuckle][Chuckle] → [Chuckle×3] for runs of ≥3 identical tags."""
    def repl(m):
        tag = m.group(1)               # e.g. '[Chuckle]'
        inner = tag[1:-1]              # 'Chuckle'
        n = len(m.group(0)) // len(tag)
        return f"[{inner}×{n}]"
    return _EMOJI_RUN_RE.sub(repl, text)


def redact_pii(text: str) -> str:
    out = text
    for kind, rx in _REDACT_RULES:
        out = rx.sub(f"[redacted-{kind}]", out)
    return out


def _apply_text_transforms(text: str, *, squash: bool, redact: bool,
                           stickers_to_emoji: bool) -> str:
    if not text:
        return text
    if stickers_to_emoji:
        text = sticker_to_emoji(text)
    if squash:
        text = squash_emoji_runs(text)
    if redact:
        text = redact_pii(text)
    return text


def body_of(msg: Message, *, squash: bool = False, redact: bool = False,
            stickers_to_emoji: bool = False) -> Body:
    """Produce a structured body for any message.

    Transforms (sticker mapping, emoji-run squash, PII redaction) are applied
    to every text-bearing field — including quoted-reply text and the quote
    preview — so the same [Tag] in any context is normalized identically.
    """
    t, sub = msg.type, msg.sub_type
    if t == TYPE_TEXT:
        body = Body(text=msg.content.strip(), kind="text")
    elif t == TYPE_IMAGE:
        body = _image_body(msg.content)
    elif t == TYPE_VOICE:
        body = _voice_body(msg.content)
    elif t == TYPE_VIDEO:
        body = _video_body(msg.content)
    elif t == TYPE_STICKER:
        body = _sticker_body(msg.content)
    elif t == TYPE_APPMSG:
        body = _appmsg_body(msg.content, sub)
    elif t == TYPE_CALL:
        body = _call_body(msg.content)
    elif t == TYPE_SYSTEM:
        body = _system_body(msg.content)
    else:
        body = Body(text=f"[type:{t}/{sub}]", kind="unknown",
                    media={"type": t, "sub_type": sub})

    # apply transforms uniformly to every text-bearing field
    if body is None:
        return body
    body.text = _apply_text_transforms(body.text, squash=squash, redact=redact,
                                       stickers_to_emoji=stickers_to_emoji)
    if body.reply is not None:
        body.reply.content = _apply_text_transforms(
            body.reply.content, squash=squash, redact=redact,
            stickers_to_emoji=stickers_to_emoji,
        )
    return body


# ---------------------------------------------------------------------------
# Identity normalization
# ---------------------------------------------------------------------------


@dataclass
class Identity:
    glossary: dict[str, str]  # letter → display
    by_username: dict[str, str]   # username → letter
    target_letter: str = "R"
    me_letter: str = "U"


def build_identity(
    messages: Iterable[Message],
    contact: ContactRecord,
    my_wxid: str,
    my_label: str = "Me",
) -> Identity:
    glossary: dict[str, str] = {"U": my_label, "R": contact.display_name}
    by_username: dict[str, str] = {my_wxid: "U", contact.username: "R"}
    next_letter_iter = iter("ABCDEFGHIJKLMNOPQSTVWXYZ")  # skip R, U
    for m in messages:
        if not m.sender_username or m.sender_username in by_username:
            continue
        try:
            letter = next(next_letter_iter)
        except StopIteration:
            letter = "?"
        # try to discover a display name later via contact db; for now, use the wxid suffix
        display = m.sender_username
        by_username[m.sender_username] = letter
        glossary[letter] = display
    return Identity(glossary=glossary, by_username=by_username)


def letter_for(identity: Identity, m: Message) -> str:
    if m.is_me:
        return identity.me_letter
    return identity.by_username.get(m.sender_username, "?")


# ---------------------------------------------------------------------------
# Sessionization
# ---------------------------------------------------------------------------


@dataclass
class Session:
    start_ts: int
    gap_before: int   # seconds since last session's last message (0 for first)
    messages: list[Message]


def sessionize(messages: Iterable[Message], gap_seconds: int = 7200) -> Iterator[Session]:
    """Split a chronological message stream into sessions on silence > gap."""
    sess: Session | None = None
    last_ts = 0
    for m in messages:
        if sess is None:
            sess = Session(start_ts=m.create_time, gap_before=0, messages=[m])
            last_ts = m.create_time
            continue
        if (m.create_time - last_ts) > gap_seconds:
            yield sess
            sess = Session(start_ts=m.create_time, gap_before=(m.create_time - last_ts), messages=[m])
        else:
            sess.messages.append(m)
        last_ts = m.create_time
    if sess is not None:
        yield sess


# ---------------------------------------------------------------------------
# Turn merging (consecutive same-sender within window)
# ---------------------------------------------------------------------------


@dataclass
class Turn:
    sender_letter: str
    start_ts: int
    parts: list[tuple[Message, Body]]   # (msg, formatted body)


def turns_of(messages: list[Message], identity: Identity, window: int = 60,
             *, squash: bool = False, redact: bool = False) -> list[Turn]:
    turns: list[Turn] = []
    cur: Turn | None = None
    last_ts = 0
    for m in messages:
        body = body_of(m, squash=squash, redact=redact)
        letter = letter_for(identity, m)
        if (
            cur is None
            or cur.sender_letter != letter
            or (m.create_time - last_ts) > window
        ):
            cur = Turn(sender_letter=letter, start_ts=m.create_time, parts=[(m, body)])
            turns.append(cur)
        else:
            cur.parts.append((m, body))
        last_ts = m.create_time
    return turns
