"""The prayer list (In His Steps): prayers someone wants to carry, often taken
from words they highlighted in an entry or its reflection.

Each prayer: its words, when it was added, the entry it came from (and the
highlight, if any), and once answered, when and an optional note on how.
Stored like kept passages: one encrypted list per person (table `prayers`),
readable only with their passphrase.
"""
import json
import logging
import secrets

from . import crypto, db, vault

log = logging.getLogger(__name__)
MAX_CHARS = 500
MAX_NOTE = 1000


class PrayerProblem(Exception):
    """Something about the prayer, safe to show the person."""


def _load():
    s = vault.current()
    con = db.connect()
    try:
        row = con.execute("SELECT data FROM prayers WHERE user_id = ?", (s.user_id,)).fetchone()
    finally:
        con.close()
    if not row:
        return []
    try:
        return json.loads(crypto.decrypt(s.key, row[0], b"prayers"))
    except Exception:
        log.exception("Could not decrypt the prayer list")
        return []


def _save(items):
    s = vault.current()
    sealed = crypto.encrypt(s.key, json.dumps(items, ensure_ascii=False).encode("utf-8"), b"prayers")
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                con.execute("INSERT OR REPLACE INTO prayers (user_id, data) VALUES (?, ?)", (s.user_id, sealed))
        finally:
            con.close()


def all_prayers():
    """Open prayers newest first, then answered ones, most recently answered first."""
    items = _load()
    open_ = sorted((p for p in items if not p.get("answered_at")), key=lambda p: p["added_at"], reverse=True)
    answered = sorted((p for p in items if p.get("answered_at")), key=lambda p: p["answered_at"], reverse=True)
    return open_ + answered


def add(text, when, entry_id=None, highlight_id=None):
    text = " ".join((text or "").split())[:MAX_CHARS]
    if not text:
        raise PrayerProblem("Write a few words for the prayer.")
    item = {"id": secrets.token_hex(6), "text": text, "added_at": when, "entry_id": entry_id or None,
            "highlight_id": highlight_id or None, "answered_at": None, "answer_note": ""}
    with db.write_lock:
        items = _load()
        items.append(item)
        _save(items)
    return item


def update(prayer_id, when, answered=None, note=None, text=None):
    """Mark answered (with an optional note) or open again, or reword it. Raises KeyError."""
    with db.write_lock:
        items = _load()
        item = next((p for p in items if p["id"] == prayer_id), None)
        if not item:
            raise KeyError(prayer_id)
        if answered is not None:
            item["answered_at"] = when if answered else None
            if not answered:
                item["answer_note"] = ""
        if note is not None:
            item["answer_note"] = " ".join(str(note).split())[:MAX_NOTE]
        if text is not None:
            text = " ".join(str(text).split())[:MAX_CHARS]
            if not text:
                raise PrayerProblem("Write a few words for the prayer.")
            item["text"] = text
        _save(items)
    return item


def remove(prayer_id):
    """Delete a prayer. Returns it (so its highlight can be unlinked), or None."""
    with db.write_lock:
        items = _load()
        gone = next((p for p in items if p["id"] == prayer_id), None)
        if gone:
            _save([p for p in items if p["id"] != prayer_id])
    return gone


def unlink_highlight(highlight_id):
    """A highlight was removed: its prayer stays, still linked to the entry."""
    with db.write_lock:
        items = _load()
        changed = False
        for p in items:
            if p.get("highlight_id") == highlight_id:
                p["highlight_id"] = None
                changed = True
        if changed:
            _save(items)
