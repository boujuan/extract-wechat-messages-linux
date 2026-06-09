"""Instagram DM source for wxextract.

Instagram has no encrypted local DB to crack — the official *Download Your
Information* export is already plaintext JSON, and the web client speaks a
private REST API you're already authenticated to. So unlike the WeChat path
there is no key-scan / decrypt step.

Two capabilities live here:

  1. ``fetch_from_export`` / ``fetch_from_api`` / ``fetch_from_dump`` —
     acquire a single 1-on-1 conversation from one of three routes and
     normalize it into the ``wxextract-instagram/1`` JSON document.

  2. ``load_instagram_json`` — ingest that JSON into wxextract's in-memory
     ``ContactRecord`` + ``list[Message]`` (a near-mirror of
     ``whatsapp.load_whatsapp_json``), so the shared renderers (txt-b / jsonl /
     xml / md) emit identical output to the WeChat / WhatsApp sources.

Routes (auto-selected by the CLI):
  * ``--export`` : a ``.zip`` (or extracted folder) from Instagram → Settings →
    Download Your Information (JSON, Messages). DMs live at
    ``…/messages/inbox/<slug>_<thread_id>/message_<N>.json``. Safe / ToS-clean.
  * ``--thread`` : the numeric thread id from ``instagram.com/direct/t/<id>``.
    Paginates the private ``direct_v2/threads`` endpoint with your browser
    session cookie. Fast, but against Instagram's ToS — small account-action risk.
  * ``--dump``   : a JSON file saved by the browser console/bookmarklet
    (``console_snippet()``) that hit the same endpoint. Manual gray-area fallback.

The export route's strings are double-encoded UTF-8 (latin-1 mojibake) and need
``_demojibake``; the API / console routes already return proper UTF-8.
"""
from __future__ import annotations

import json
import random
import re
import time
import urllib.parse
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

from wxextract.contacts import ContactRecord
from wxextract.messages import (
    APPMSG_LINK,
    TYPE_APPMSG,
    TYPE_CALL,
    TYPE_IMAGE,
    TYPE_MEDIA_GENERIC,
    TYPE_STICKER,
    TYPE_TEXT,
    TYPE_VIDEO,
    TYPE_VOICE,
    Message,
)

SCHEMA = "wxextract-instagram/1"
SUPPORTED_SCHEMA = SCHEMA

# The web app id every instagram.com XHR sends; required by direct_v2.
IG_APP_ID = "936619743392459"
_UA = ("Mozilla/5.0 (X11; Linux x86_64; rv:131.0) "
       "Gecko/20100101 Firefox/131.0")

# message_<N>.json living under …/inbox/<thread_folder>/
_MSG_FILE_RE = re.compile(r"(?:^|/)inbox/([^/]+)/message_\d+\.json$")
# split a thread folder `<slug>_<thread_id_digits>` → (slug, thread_id)
_FOLDER_RE = re.compile(r"^(?P<slug>.+?)_(?P<tid>\d+)$")


# ---------------------------------------------------------------------------
# encoding / identity helpers
# ---------------------------------------------------------------------------


def _demojibake(s: str) -> str:
    """Reverse Instagram's export mojibake (UTF-8 bytes re-encoded as latin-1).

    e.g. '\\u00f0\\u009f\\u0098\\u0086' → '😆'. Returns the input unchanged when
    it isn't a string or the round-trip fails (already-clean UTF-8)."""
    if not isinstance(s, str):
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _split_folder(folder: str) -> tuple[str, str]:
    """`rachelpersonalcoachmacau_2023021262428743` →
    ('rachelpersonalcoachmacau', '2023021262428743'). Falls back to
    (folder, '') when there is no trailing _<digits>."""
    m = _FOLDER_RE.match(folder)
    if m:
        return m.group("slug"), m.group("tid")
    return folder, ""


def _share_appmsg_xml(share: dict) -> str:
    """Synthesize the minimal WeChat-style appmsg XML the shared renderer's
    ``_appmsg_body`` expects, so a share renders as ``[link: text — url]``."""
    title = share.get("share_text") or share.get("link") or "shared post"
    url = share.get("link") or ""
    return (f"<appmsg><title>{_xml_escape(title)}</title>"
            f"<url>{_xml_escape(url)}</url></appmsg>")


# ---------------------------------------------------------------------------
# normalization → wxextract-instagram/1 message records
# ---------------------------------------------------------------------------


def _classify_export(m: dict) -> tuple[int, int, str] | None:
    """Map one *export* message dict → (type, sub_type, content).

    Returns None to skip the message (unsent/deleted). Noise fields
    (`is_geoblocked_for_viewer`, `is_unsent_image_by_messenger_kid_parent`)
    are ignored. `content` / `share.share_text` are expected to be
    de-mojibake'd already by the caller."""
    if m.get("is_unsent"):
        return None
    share = m.get("share")
    if isinstance(share, dict) and share:
        return TYPE_APPMSG, APPMSG_LINK, _share_appmsg_xml(share)
    if m.get("photos"):
        return TYPE_IMAGE, 0, ""
    if m.get("videos"):
        return TYPE_VIDEO, 0, ""
    if m.get("audio_files"):
        return TYPE_VOICE, 0, ""
    if m.get("sticker") or m.get("gifs"):
        return TYPE_STICKER, 0, ""
    if "call_duration" in m:
        dur = m.get("call_duration") or 0
        # `Call ended Ns` is what render.common._call_body matches → `[call Ns]`
        return TYPE_CALL, 0, (f"Call ended {int(dur)}s" if dur and dur > 0 else "")
    content = m.get("content") or ""
    if content:
        if content == "You sent an attachment.":
            return TYPE_MEDIA_GENERIC, 0, ""
        return TYPE_TEXT, 0, content
    return TYPE_MEDIA_GENERIC, 0, ""


def _normalize_export_messages(raw_msgs: list[dict], me_name: str) -> list[dict]:
    """Export message dicts (any order) → sorted message records.

    Each record: {create_time, sender_name, is_me, type, sub_type, content}.
    Strings are de-mojibake'd; timestamps converted ms → seconds."""
    recs: list[dict] = []
    for m in raw_msgs:
        ts_ms = m.get("timestamp_ms")
        if ts_ms is None:
            continue
        sender = _demojibake(m.get("sender_name") or "")
        # fix the text fields _classify_export reads
        if isinstance(m.get("content"), str):
            m = {**m, "content": _demojibake(m["content"])}
        share = m.get("share")
        if isinstance(share, dict) and isinstance(share.get("share_text"), str):
            m = {**m, "share": {**share, "share_text": _demojibake(share["share_text"])}}
        classified = _classify_export(m)
        if classified is None:
            continue
        typ, sub, content = classified
        recs.append({
            "create_time": int(ts_ms) // 1000,
            "sender_name": sender,
            "is_me": sender == me_name,
            "type": typ, "sub_type": sub, "content": content,
        })
    recs.sort(key=lambda r: r["create_time"])
    return recs


_ITEM_MEDIA_IMAGE = 1
_ITEM_MEDIA_VIDEO = 2


def _classify_item(it: dict) -> tuple[int, int, str] | None:
    """Map one private-API `direct_v2` thread item → (type, sub_type, content).

    Returns None to skip (placeholders, thread events). Text is already proper
    UTF-8 from the API/console route."""
    kind = it.get("item_type")
    if kind == "text":
        return TYPE_TEXT, 0, it.get("text") or ""
    if kind == "link":
        link = it.get("link") or {}
        ctx = link.get("link_context") or {}
        return TYPE_APPMSG, APPMSG_LINK, _share_appmsg_xml(
            {"share_text": link.get("text") or ctx.get("link_title") or "",
             "link": ctx.get("link_url") or ""})
    if kind in ("reel_share", "story_share", "clip", "felix_share", "media_share"):
        share = (it.get(kind) or {})
        text = share.get("text") or (share.get("media") or {}).get("caption") or kind
        return TYPE_APPMSG, APPMSG_LINK, _share_appmsg_xml({"share_text": text, "link": ""})
    if kind in ("media", "raven_media", "visual_media"):
        media = it.get("media") or (it.get("visual_media") or {}).get("media") or {}
        return (TYPE_VIDEO if media.get("media_type") == _ITEM_MEDIA_VIDEO else TYPE_IMAGE), 0, ""
    if kind == "voice_media":
        return TYPE_VOICE, 0, ""
    if kind == "animated_media":
        return TYPE_STICKER, 0, ""
    if kind == "video_call_event":
        return TYPE_CALL, 0, ""
    if kind in ("action_log", "placeholder", None):
        return None
    return TYPE_MEDIA_GENERIC, 0, ""


def _normalize_thread_items(items: list[dict], *, my_pk: str,
                            users_by_pk: dict[str, str]) -> list[dict]:
    """Private-API thread items (any order) → sorted message records.
    Timestamps are microseconds → seconds."""
    recs: list[dict] = []
    for it in items:
        ts = it.get("timestamp")
        if ts is None:
            continue
        uid = str(it.get("user_id", ""))
        classified = _classify_item(it)
        if classified is None:
            continue
        typ, sub, content = classified
        recs.append({
            "create_time": int(ts) // 1_000_000,
            "sender_name": users_by_pk.get(uid, uid),
            "is_me": bool(my_pk) and uid == str(my_pk),
            "type": typ, "sub_type": sub, "content": content,
        })
    recs.sort(key=lambda r: r["create_time"])
    return recs


def _build_doc(*, slug: str, thread_id: str, display_name: str, me_name: str,
               recs: list[dict], input_file: str) -> dict:
    """Assemble the wxextract-instagram/1 document from message records."""
    username = f"{slug}_{thread_id}@instagram" if thread_id else f"{slug}@instagram"
    out_messages = []
    for i, r in enumerate(recs, start=1):
        out_messages.append({
            "local_id": i,
            "server_id": 0,
            "create_time": r["create_time"],
            "sender_username": r["sender_name"],   # loader collapses non-me → username
            "is_me": r["is_me"],
            "type": r["type"],
            "sub_type": r["sub_type"],
            "content": r["content"],
            "source": "instagram",
            "raw_local_type": r["type"],
            "status": 3,
        })
    return {
        "schema": SCHEMA,
        "source": "instagram",
        "input_file": input_file,
        "contact": {
            "username": username,
            "alias": slug,
            "nick_name": display_name,
            "remark": "",
            "display_name": display_name,
            "local_type": 1,
            "source": "instagram",
            "message_count": len(out_messages),
            "first_message_ts": out_messages[0]["create_time"] if out_messages else 0,
            "last_message_ts": out_messages[-1]["create_time"] if out_messages else 0,
        },
        "my_label": me_name or "Me",
        "messages": out_messages,
    }


def _derive_me(title: str, participants: list[str], override: str | None) -> str:
    """In a 1-on-1 export, participants == [other, me] and title == other's name,
    so `me` is the participant whose name != title. Raises on group threads."""
    if override:
        return override
    if len(participants) > 2:
        raise ValueError(
            f"group thread with {len(participants)} participants is not supported "
            f"in v1 (1-on-1 only). Pass --me NAME to force, or pick a 1-on-1 thread.")
    others = [p for p in participants if p != title]
    if len(participants) == 2 and len(others) == 1:
        return others[0]
    raise ValueError(
        f"could not auto-derive your name from participants {participants!r} "
        f"(title={title!r}). Pass --me NAME explicitly.")


# ---------------------------------------------------------------------------
# export route (zip or folder)
# ---------------------------------------------------------------------------


def _group_export(path: Path):
    """Return ({thread_folder: [identifiers]}, read_fn). For a zip, identifiers
    are arcnames and read_fn(zf, name); for a folder they're Paths. The caller
    keeps the ZipFile open for the read_fn's lifetime."""
    grouped: dict[str, list] = {}
    if path.is_file() and path.suffix.lower() == ".zip":
        zf = zipfile.ZipFile(path)
        for name in zf.namelist():
            m = _MSG_FILE_RE.search(name)
            if m:
                grouped.setdefault(m.group(1), []).append(name)
        for v in grouped.values():
            v.sort()
        return grouped, zf, (lambda n: zf.read(n))
    if path.is_dir():
        for jp in path.rglob("message_*.json"):
            m = _MSG_FILE_RE.search(jp.as_posix())
            if m:
                grouped.setdefault(m.group(1), []).append(jp)
        for v in grouped.values():
            v.sort(key=lambda p: p.name)
        return grouped, None, (lambda n: Path(n).read_bytes())
    raise ValueError(f"--export must be a .zip or an extracted export folder: {path}")


def _read_thread(read_fn, names) -> tuple[str, list[str], list[dict]]:
    """Read every message_<N>.json of a thread → (title, participants, messages).
    Title/participants are de-mojibake'd; messages are raw export dicts."""
    title = ""
    participants: list[str] = []
    all_msgs: list[dict] = []
    for n in names:
        doc = json.loads(read_fn(n))
        if not title:
            title = _demojibake(doc.get("title") or "")
        if not participants:
            participants = [_demojibake(p.get("name", "")) for p in doc.get("participants", [])]
        all_msgs.extend(doc.get("messages", []))
    return title, participants, all_msgs


def list_threads(export_path: str | Path) -> list[dict]:
    """List every inbox thread in an export. Returns dicts with
    slug / thread_id / display_name / message_count, sorted by size desc."""
    path = Path(export_path).expanduser()
    grouped, zf, read_fn = _group_export(path)
    rows = []
    try:
        for folder, names in grouped.items():
            title, participants, msgs = _read_thread(read_fn, names)
            slug, tid = _split_folder(folder)
            rows.append({
                "folder": folder,
                "slug": slug,
                "thread_id": tid,
                "display_name": title or slug,
                "participants": participants,
                "message_count": len(msgs),
            })
    finally:
        if zf is not None:
            zf.close()
    rows.sort(key=lambda r: r["message_count"], reverse=True)
    return rows


def _select_folders(grouped: dict, selector: str | None, all_threads: bool) -> list[str]:
    folders = list(grouped)
    if all_threads:
        return folders
    if not selector:
        raise ValueError("pass --thread <slug-or-id substring> to pick a thread, "
                         "--all for every thread, or run `wxextract instagram list` first")
    sel = selector.lower()
    matches = [f for f in folders if sel in f.lower()]
    if not matches:
        raise ValueError(f"no thread matches {selector!r} "
                         f"(run `wxextract instagram list --export …` to see them)")
    if len(matches) > 1:
        raise ValueError(f"{selector!r} matches {len(matches)} threads: "
                         f"{', '.join(sorted(matches)[:8])}… — be more specific or use --all")
    return matches


def fetch_from_export(export_path: str | Path, selector: str | None, *,
                      me: str | None = None, all_threads: bool = False) -> list[dict]:
    """Parse one or more threads from an export → list of wxextract-instagram docs."""
    path = Path(export_path).expanduser()
    grouped, zf, read_fn = _group_export(path)
    docs: list[dict] = []
    try:
        for folder in _select_folders(grouped, selector, all_threads):
            title, participants, raw_msgs = _read_thread(read_fn, grouped[folder])
            slug, tid = _split_folder(folder)
            me_name = _derive_me(title, participants, me)
            recs = _normalize_export_messages(raw_msgs, me_name)
            docs.append(_build_doc(slug=slug, thread_id=tid,
                                   display_name=title or slug, me_name=me_name,
                                   recs=recs, input_file=path.name))
    finally:
        if zf is not None:
            zf.close()
    return docs


# ---------------------------------------------------------------------------
# live private-API route
# ---------------------------------------------------------------------------


def _ds_user_id_from_sessionid(sessionid: str) -> str:
    """sessionid looks like '<ds_user_id>%3A<token>…' → the pk before the colon."""
    return urllib.parse.unquote(sessionid).split(":", 1)[0]


def _zen_cookie_files() -> list[Path]:
    base = Path.home() / ".zen"
    if not base.is_dir():
        return []
    files = list(base.glob("*/cookies.sqlite"))
    # prefer a "Default (release)" profile, then most-recently-used
    files.sort(key=lambda p: ("Default (release)" not in p.parent.name, -p.stat().st_mtime))
    return files


def _cj_to_dict(cj) -> dict:
    return {c.name: c.value for c in cj if "instagram.com" in (c.domain or "")}


def load_cookies(*, browser: str | None = None, sessionid: str | None = None,
                 cookie_file: str | None = None) -> dict:
    """Resolve instagram.com cookies. Priority: explicit sessionid > cookie_file >
    named browser > auto (Zen, then the usual browsers). 'Browser flexible'."""
    if sessionid:
        return {"sessionid": sessionid,
                "ds_user_id": _ds_user_id_from_sessionid(sessionid)}
    try:
        import browser_cookie3 as bc3
    except ImportError as e:
        raise RuntimeError(
            "the live --thread route needs browser_cookie3 (and requests). "
            "Reinstall the tool: `uv tool install --reinstall <repo>`, or pass "
            "--sessionid <value> copied from your browser cookies.") from e

    if cookie_file:
        return _cj_to_dict(bc3.firefox(cookie_file=str(Path(cookie_file).expanduser()),
                                       domain_name="instagram.com"))

    def _try(fn) -> dict | None:
        try:
            d = _cj_to_dict(fn())
            return d if d.get("sessionid") else None
        except Exception:
            return None

    if browser in (None, "zen"):
        for zf in _zen_cookie_files():
            got = _try(lambda: bc3.firefox(cookie_file=str(zf), domain_name="instagram.com"))
            if got:
                return got
        if browser == "zen":
            raise RuntimeError("no logged-in instagram.com cookie found in any "
                               "~/.zen/*/cookies.sqlite profile")

    backends = {
        "firefox": lambda: bc3.firefox(domain_name="instagram.com"),
        "chrome": lambda: bc3.chrome(domain_name="instagram.com"),
        "chromium": lambda: bc3.chromium(domain_name="instagram.com"),
        "brave": lambda: bc3.brave(domain_name="instagram.com"),
        "edge": lambda: bc3.edge(domain_name="instagram.com"),
    }
    if browser and browser in backends:
        got = _try(backends[browser])
        if got:
            return got
        raise RuntimeError(f"no logged-in instagram.com cookie found in {browser}")

    for fn in backends.values():               # auto: try each browser
        got = _try(fn)
        if got:
            return got
    raise RuntimeError("could not find a logged-in instagram.com session in any "
                       "browser. Log into Instagram, or pass --sessionid.")


_INBOX_URL = "https://www.instagram.com/api/v1/direct_v2/inbox/"


def _thread_url(thread_id: str) -> str:
    return f"https://www.instagram.com/api/v1/direct_v2/threads/{thread_id}/"


def _ig_slug(name: str) -> str:
    """Mimic Instagram's export folder slug: lowercase, alphanumerics only
    (so the live route's alias matches the export's, e.g. `rachelpersonalcoachmacau`)."""
    return "".join(c.lower() for c in (name or "") if c.isalnum()) or "instagram"


def _api_session(cookies: dict):
    import requests
    sess = requests.Session()
    sess.headers.update({
        "X-IG-App-ID": IG_APP_ID,
        "User-Agent": _UA,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*",
        "X-ASBD-ID": "129477",
        "X-IG-WWW-Claim": "0",
        "Referer": "https://www.instagram.com/direct/inbox/",
    })
    sess.cookies.update(cookies)
    return sess


def _inbox_threads(sess, *, max_pages: int = 40, log=print) -> list[dict]:
    """Page the DM inbox → raw thread objects (enough to resolve a selector)."""
    out: list[dict] = []
    cursor: str | None = None
    for _ in range(max_pages):
        params: dict = {"limit": 20, "thread_message_limit": 1, "persistentBadging": "true"}
        if cursor:
            params["cursor"] = cursor
        r = sess.get(_INBOX_URL, params=params, timeout=30)
        if r.status_code in (401, 403):
            raise RuntimeError(f"Instagram returned {r.status_code} reading the inbox — "
                               f"session expired or checkpoint required. Re-login in your browser.")
        r.raise_for_status()
        inbox = (r.json() or {}).get("inbox") or {}
        out.extend(inbox.get("threads", []))
        if not inbox.get("has_older"):
            break
        cursor = inbox.get("oldest_cursor")
        if not cursor:
            break
        time.sleep(0.5 + random.random() * 0.6)
    return out


def _users_by_pk(t: dict) -> dict[str, str]:
    return {str(u.get("pk")): (u.get("full_name") or u.get("username") or str(u.get("pk")))
            for u in t.get("users", [])}


def _fmt_thread(t: dict) -> str:
    """One candidate line: the /direct/t/<id> URL id + participant names."""
    names = ", ".join(u.get("full_name") or u.get("username") or "" for u in t.get("users", []))
    title = t.get("thread_title") or ""
    label = f"{names}" + (f"  «{title}»" if title and title != names else "")
    return f"    {t.get('messaging_thread_key')}  {label}"


def _candidate_block(threads: list[dict], cap: int = 30) -> str:
    lines = [_fmt_thread(t) for t in threads[:cap]]
    if len(threads) > cap:
        lines.append(f"    … and {len(threads) - cap} more")
    return "\n".join(lines)


def _thread_matches(t: dict, sel: str) -> bool:
    """Match a selector against a thread's three ids (REST thread_id, thread_v2_id,
    messaging_thread_key = the /direct/t/<id> URL id) or a participant name/username."""
    ids = {str(t.get(k)) for k in ("thread_id", "thread_v2_id", "messaging_thread_key") if t.get(k)}
    if sel in ids:
        return True
    s = sel.lower()
    for u in t.get("users", []):
        if s in (u.get("username") or "").lower() or s in (u.get("full_name") or "").lower():
            return True
    title = t.get("thread_title") or ""
    return bool(title) and s in title.lower()


def fetch_from_api(thread: str, *, browser: str | None = None,
                   sessionid: str | None = None, cookie_file: str | None = None,
                   limit: int = 40, max_messages: int = 0, me: str | None = None,
                   log=print) -> list[dict]:
    """Resolve `thread` (the /direct/t/<id> URL id, a name, or a thread id) via the
    inbox, then paginate the private direct_v2 thread endpoint → [wxextract doc]."""
    try:
        import requests  # noqa: F401
    except ImportError as e:
        raise RuntimeError("the live --thread route needs `requests`. Reinstall: "
                           "`uv tool install --reinstall <repo>`.") from e

    cookies = load_cookies(browser=browser, sessionid=sessionid, cookie_file=cookie_file)
    if not cookies.get("sessionid"):
        raise RuntimeError("resolved cookies have no `sessionid` — not logged in.")
    my_pk = str(cookies.get("ds_user_id", ""))
    sess = _api_session(cookies)

    # ── resolve the selector to a REST thread_id via the inbox ────────────────
    # The /direct/t/<id> URL id is `messaging_thread_key`, NOT the id the thread
    # endpoint wants (`thread_id`) — fetching by the URL id 500s. So look it up.
    log("  resolving thread via inbox…")
    threads = _inbox_threads(sess, log=log)
    matches = [t for t in threads if _thread_matches(t, str(thread))]
    if not matches:
        raise RuntimeError(
            f"no DM thread matched {thread!r} among your {len(threads)} inbox threads. "
            f"Pass the /direct/t/<id> URL id, a name, or the export id. Available:\n"
            + _candidate_block(threads))
    if len(matches) > 1:
        raise RuntimeError(
            f"{thread!r} matched {len(matches)} threads — narrow it down, or re-run with one "
            f"of these /direct/t/<id> URL ids:\n" + _candidate_block(matches))
    picked = matches[0]
    rest_id = str(picked.get("thread_id"))
    v2_id = str(picked.get("thread_v2_id") or "")
    users_by_pk = _users_by_pk(picked)
    title = picked.get("thread_title") or ""
    others = [name for pk, name in users_by_pk.items() if pk != my_pk]
    other_name = title or (others[0] if others else rest_id)
    log(f"  → {other_name} (thread_id={rest_id[:14]}…, v2={v2_id})")

    # ── paginate the resolved thread (cursor drives the older direction) ──────
    items: list[dict] = []
    cursor = None
    while True:
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        r = sess.get(_thread_url(rest_id), params=params, timeout=30)
        if r.status_code in (401, 403):
            raise RuntimeError(f"Instagram returned {r.status_code} — session expired "
                               f"or checkpoint required. Re-login in your browser.")
        r.raise_for_status()
        tobj = (r.json() or {}).get("thread") or {}
        for pk, name in _users_by_pk(tobj).items():
            users_by_pk.setdefault(pk, name)
        batch = tobj.get("items") or []
        items.extend(batch)
        log(f"  +{len(batch)} (total {len(items)})")
        if max_messages and len(items) >= max_messages:
            items = items[:max_messages]
            break
        if not tobj.get("has_older"):
            break
        cursor = tobj.get("oldest_cursor")
        if not cursor:
            break
        time.sleep(1.2 + random.random() * 1.3)    # be gentle

    recs = _normalize_thread_items(items, my_pk=my_pk, users_by_pk=users_by_pk)
    if me:                                          # honor explicit --me override
        for rr in recs:
            rr["is_me"] = rr["sender_name"] == me
    return [_build_doc(slug=_ig_slug(other_name), thread_id=(v2_id or rest_id),
                       display_name=other_name, me_name=(me or "Me"),
                       recs=recs, input_file=f"direct_v2:{rest_id}")]


# ---------------------------------------------------------------------------
# browser console / bookmarklet dump route
# ---------------------------------------------------------------------------


def fetch_from_dump(dump_path: str | Path, *, me: str | None = None) -> list[dict]:
    """Parse a console/bookmarklet dump → [wxextract-instagram doc].

    Accepts either the snippet's ``{viewer_id, users, items, thread_id}`` shape,
    a bare ``{thread: {items: […]}}``, or a bare ``[…]`` array of items."""
    path = Path(dump_path).expanduser()
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        items, users_by_pk, my_pk, thread_id = data, {}, "", ""
    else:
        thread = data.get("thread") or {}
        items = data.get("items") or thread.get("items") or []
        users_by_pk = {str(k): v for k, v in (data.get("users") or {}).items()}
        if not users_by_pk:
            for u in thread.get("users", []):
                users_by_pk[str(u.get("pk"))] = u.get("full_name") or u.get("username") or str(u.get("pk"))
        my_pk = str(data.get("viewer_id") or "")
        thread_id = str(data.get("thread_id") or "")
    recs = _normalize_thread_items(items, my_pk=my_pk, users_by_pk=users_by_pk)
    if me:
        for r in recs:
            r["is_me"] = r["sender_name"] == me
    others = [name for pk, name in users_by_pk.items() if pk != my_pk]
    display_name = others[0] if others else (thread_id or "instagram")
    return [_build_doc(slug=_ig_slug(display_name), thread_id=thread_id,
                       display_name=display_name, me_name=(me or "Me"),
                       recs=recs, input_file=path.name)]


# ---------------------------------------------------------------------------
# output + console snippet
# ---------------------------------------------------------------------------


def write_doc(doc: dict, out_path: str | Path) -> Path:
    p = Path(out_path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def console_snippet() -> str:
    """Return the browser-agnostic console/bookmarklet dumper JS."""
    asset = Path(__file__).with_name("assets") / "ig_dump.js"
    try:
        return asset.read_text(encoding="utf-8")
    except OSError:
        return _IG_DUMP_JS


# Fallback copy of assets/ig_dump.js (kept in sync) so the command works even
# if package data wasn't installed.
_IG_DUMP_JS = r"""// wxextract — Instagram DM console dumper (browser-agnostic).
// 1. Open the DM at instagram.com/direct/t/<id>/ while logged in.
// 2. Open DevTools → Console, paste this, press Enter.
// 3. It paginates the full thread and downloads ig_<id>.json.
//    Feed it to: wxextract instagram fetch --dump ig_<id>.json
(async () => {
  const APP_ID = "936619743392459";
  const tid = (location.pathname.match(/\/direct\/t\/(\d+)/) || [])[1];
  if (!tid) return console.error("Open a DM thread first (…/direct/t/<id>/).");
  const viewer = (document.cookie.match(/ds_user_id=(\d+)/) || [])[1] || "";
  let cursor = null, items = [], users = {}, page = 0;
  while (true) {
    const u = new URL(`https://www.instagram.com/api/v1/direct_v2/threads/${tid}/`);
    u.searchParams.set("limit", "40");
    u.searchParams.set("direction", "older");
    if (cursor) u.searchParams.set("cursor", cursor);
    const r = await fetch(u, { headers: { "X-IG-App-ID": APP_ID }, credentials: "include" });
    if (!r.ok) { console.error("HTTP", r.status); break; }
    const t = ((await r.json()) || {}).thread || {};
    (t.users || []).forEach(x => users[String(x.pk)] = x.full_name || x.username || String(x.pk));
    items = items.concat(t.items || []);
    console.log(`page ${++page}: +${(t.items || []).length} (total ${items.length})`);
    if (!t.has_older || !t.oldest_cursor) break;
    cursor = t.oldest_cursor;
    await new Promise(s => setTimeout(s, 1500 + Math.random() * 1500));
  }
  const blob = new Blob(
    [JSON.stringify({ thread_id: tid, viewer_id: viewer, users, items }, null, 2)],
    { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `ig_${tid}.json`;
  a.click();
  console.log(`done — ${items.length} items → ig_${tid}.json`);
})();
"""


# ---------------------------------------------------------------------------
# loader (mirrors whatsapp.load_whatsapp_json)
# ---------------------------------------------------------------------------


def load_instagram_json(path: Path) -> tuple[ContactRecord, list[Message], str]:
    """Read a wxextract-instagram JSON file → (contact, messages, my_label)."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    schema = doc.get("schema")
    if schema != SUPPORTED_SCHEMA:
        raise ValueError(
            f"{path}: unsupported schema {schema!r}, expected {SUPPORTED_SCHEMA!r}. "
            f"Re-run `wxextract instagram fetch`.")

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
        source=cdict.get("source", "instagram"),
    )

    messages: list[Message] = []
    for m in doc["messages"]:
        is_me = bool(m["is_me"])
        # Collapse non-me sender_username to contact.username so stats.compute's
        # `sender_username == contact.username` check classifies "them" correctly.
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
            source=m.get("source", "instagram"),
            status=int(m.get("status", 3)),
        ))

    my_label = doc.get("my_label", "Me")
    return contact, messages, my_label
