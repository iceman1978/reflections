"""Kept passages: the quotations someone has starred in their reflections.

Only word-for-word quotes can be kept (the ones the app verified against its
own copy of the source), so a kept passage is always the source's exact
wording, with its work, place and link. They're stored as one encrypted list
per person (table `kept`), apart from the entries: the same passage can come
up in several reflections, and it stays kept if one of those entries is deleted.

A passage someone shares with you (journal/share.py) waits, unencrypted, in
`shared_inbox` under your email address (the server can't add to your list
while you're away: only you hold its key), and moves into your list, with who
shared it and their note, the next time you unlock. A shared passage you didn't
keep yourself has "own": false; a passage kept before this has no "own".
"""
import hashlib
import json
import logging
import re

from . import crypto, db, vault

log = logging.getLogger(__name__)
FIELDS = ("work", "author", "title", "translation", "location", "quote", "url")
SCRIPTURE = "Scripture"       # the heading for books of the Bible (which have no author)


def key_of(passage):
    """A passage's identity: the same words from the same place in the same work."""
    raw = "|".join(str(passage.get(f) or "") for f in ("work", "location", "quote"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def slug(name):
    """'Marcus Aurelius' -> 'marcus-aurelius' (the anchor of a writer on the Sources page)."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


# ---- Storage ----------------------------------------------------------------

def _load():
    s = vault.current()
    con = db.connect()
    try:
        row = con.execute("SELECT data FROM kept WHERE user_id = ?", (s.user_id,)).fetchone()
    finally:
        con.close()
    if not row:
        return []
    try:
        return json.loads(crypto.decrypt(s.key, row[0], b"kept"))
    except Exception:
        log.exception("Could not decrypt the kept passages")
        return []


def _save(passages):
    s = vault.current()
    sealed = crypto.encrypt(s.key, json.dumps(passages, ensure_ascii=False).encode("utf-8"), b"kept")
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                con.execute("INSERT OR REPLACE INTO kept (user_id, data) VALUES (?, ?)", (s.user_id, sealed))
        finally:
            con.close()


def passages():
    """The current person's kept passages (raises vault.Locked)."""
    return _load()


def keys():
    return {p["key"] for p in _load()}


def keep(citation, when):
    """Keep a verified quotation (a citation from an entry's `quotes`). Returns its key."""
    key = key_of(citation)
    with db.write_lock:
        current = _load()
        if not any(p["key"] == key for p in current):
            current.append({**{f: citation.get(f) or "" for f in FIELDS}, "key": key, "kept_at": when})
            _save(current)
    return key


def take_shared(email):
    """Move passages shared with this address into the current person's kept
    list, then delete them from the waiting list. Returns how many."""
    if not email:
        return 0
    con = db.connect()
    try:
        rows = con.execute("SELECT id, passage, sharer, note, shared_at FROM shared_inbox WHERE email = ?",
                           (email.lower(),)).fetchall()
    finally:
        con.close()
    if not rows:
        return 0
    with db.write_lock:
        current = _load()
        by_key = {p["key"]: p for p in current}
        for _, passage, sharer, note, at in rows:
            citation = json.loads(passage)
            key = key_of(citation)
            gift = {"by": sharer or "A friend", "note": note or "", "at": at}
            if key in by_key:
                shared = by_key[key].setdefault("shared", [])
                if gift not in shared:
                    shared.append(gift)
            else:
                by_key[key] = {**{f: citation.get(f) or "" for f in FIELDS}, "key": key, "kept_at": at,
                               "own": False, "shared": [gift]}
                current.append(by_key[key])
        _save(current)
        con = db.connect()
        try:
            with con:
                con.executemany("DELETE FROM shared_inbox WHERE id = ?", [(r[0],) for r in rows])
        finally:
            con.close()
    log.info("%d shared passage(s) moved into a kept list", len(rows))
    return len(rows)


def release(key):
    """No longer keep the passage with this key."""
    with db.write_lock:
        current = _load()
        remaining = [p for p in current if p["key"] != key]
        if len(remaining) != len(current):
            _save(remaining)


# ---- The Kept passages page ---------------------------------------------------

def grouped(kept_passages, entries, work_order=()):
    """Kept passages arranged for the page: one group per writer, the most kept
    first; books of the Bible together under "Scripture" (first), a section per
    book in the Bible's order. Each passage lists the entries whose reflections
    quoted it, newest first. `entries` are the visible entries; `work_order` is
    the library's order of work ids."""
    quoted_in = {}
    for e in sorted(entries, key=lambda e: e["created_at"], reverse=True):
        for q in e.get("quotes") or []:
            quoted_in.setdefault(key_of(q), []).append(
                {"id": e["id"], "title": e.get("title"), "created_at": e["created_at"]})
    order = {work: i for i, work in enumerate(work_order)}

    groups = {}
    for p in sorted(kept_passages, key=lambda p: p.get("kept_at") or "", reverse=True):
        scripture = not p.get("author")
        name = SCRIPTURE if scripture else p["author"]
        group = groups.setdefault(name, {"name": name, "scripture": scripture, "count": 0, "sections": {}})
        group["count"] += 1
        section = group["sections"].setdefault(p["work"], {"name": p["title"], "work": p["work"], "passages": []})
        section["passages"].append({**p, "entries": quoted_in.get(p["key"], [])})

    out = []
    for group in groups.values():
        sections = sorted(group["sections"].values(), key=lambda s: order.get(s["work"], len(order)))
        out.append({**group, "sections": sections, "anchor": "" if group["scripture"] else slug(group["name"])})
    out.sort(key=lambda g: (not g["scripture"], -g["count"], g["name"]))
    return out
