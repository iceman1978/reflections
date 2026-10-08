"""Journal entries, stored encrypted in journal.db.

Each entry is a JSON document, encrypted as a whole with its owner's data key
and stored under a random ID. Everything here works on the journal of the
person whose session is current (see vault.use / vault.current), so one
person can never read or change another's entries. While someone is unlocked,
their decrypted entries are kept in memory (journals are small); locking
forgets them. Every write goes to the database, in a transaction, before
anything else happens, so a crash or a failed API call can't lose it.
"""
import json
import logging
import re
import secrets
import threading
from datetime import datetime

from . import backup, crypto, db, vault
from .config import TZ

log = logging.getLogger(__name__)
_lock = threading.RLock()
_caches = {}   # user_id -> {entry_id: entry}, while unlocked
_ID_RE = re.compile(r"^[0-9a-f]{16}$")

# Reflection status values (stored in the entry and shown in exports)
NOT_REQUESTED = "Not requested"
PENDING = "Pending"      # saved; reflection not finished yet
DONE = "Done"
FAILED = "Failed"        # retryable; reflection_error says why

TITLE_MAX = 120


def now():
    return datetime.now(TZ)


def word_count(text):
    return len(re.findall(r"\S+", text or ""))


def new_id():
    """Random, so an ID says nothing about when an entry was written."""
    return secrets.token_hex(8)


def clean_title(title):
    """Tidy a title; returns None for blank."""
    title = " ".join((title or "").split()).strip(" \"'“”‘’")
    if title.endswith(".") and not title.endswith(".."):
        title = title[:-1]
    return title[:TITLE_MAX] or None


# ---- Encryption of rows ---------------------------------------------------

def _seal(key, entry):
    return crypto.encrypt(key, json.dumps(entry, ensure_ascii=False).encode("utf-8"), context=entry["id"].encode())


def _open_row(key, entry_id, blob):
    return json.loads(crypto.decrypt(key, blob, context=entry_id.encode()))


def _load(session):
    con = db.connect()
    try:
        rows = con.execute("SELECT id, data FROM entries WHERE user_id = ?", (session.user_id,)).fetchall()
    finally:
        con.close()
    loaded = {}
    for entry_id, blob in rows:
        try:
            loaded[entry_id] = _open_row(session.key, entry_id, blob)
        except Exception:
            log.exception("Could not decrypt entry %s", entry_id)
    return loaded


@vault.on_lock
def _forget(user_id):
    with _lock:
        _caches.pop(user_id, None)


def _entries():
    """The current person's decrypted entries (raises vault.Locked)."""
    s = vault.current()
    with _lock:
        cache = _caches.get(s.user_id)
        if cache is None:
            cache = _caches[s.user_id] = _load(s)
        return cache


def reload():
    """Re-read the current person's entries from the database."""
    s = vault.current()
    with _lock:
        _caches[s.user_id] = _load(s)


def put_entry(entry):
    """Write an entry exactly as given (encrypted), then update the cache."""
    s = vault.current()
    sealed = _seal(s.key, entry)
    with _lock, db.write_lock:
        con = db.connect()
        try:
            owner = con.execute("SELECT user_id FROM entries WHERE id = ?", (entry["id"],)).fetchone()
            if owner and owner[0] != s.user_id:
                raise KeyError(entry["id"])  # never overwrite someone else's entry
            with con:
                con.execute("INSERT OR REPLACE INTO entries (id, user_id, data) VALUES (?, ?, ?)",
                            (entry["id"], s.user_id, sealed))
        finally:
            con.close()
        _entries()[entry["id"]] = entry
    backup.daily()
    return entry


# ---- Entries ----------------------------------------------------------------

def create_entry(text, prompt=None, reflect=False, title=None, created_at=None):
    ts = now()
    title = clean_title(title)
    entry = {
        "id": new_id(),
        "created_at": created_at or ts.isoformat(),
        "updated_at": ts.isoformat(),
        "title": title,
        "title_source": "user" if title else None,   # "user" or "generated"
        "prompt": prompt or None,
        "text": text,
        "word_count": word_count(text),
        "insight": None,
        "question": None,
        "quotes": [],
        "reflection_status": PENDING if reflect else NOT_REQUESTED,
        "reflection_error": None,
        "favourite": False,
        "hidden": False,
    }
    return put_entry(entry)


def get_entry(entry_id):
    if not _ID_RE.match(entry_id or ""):
        raise KeyError(entry_id)
    entry = _entries().get(entry_id)
    if entry is None:
        raise KeyError(entry_id)
    return dict(entry)


def update_entry(entry_id, touch=True, **fields):
    """Change fields of an entry. touch=False leaves 'Last Updated' alone
    (for things like starring, which don't change the entry itself)."""
    with _lock:
        entry = get_entry(entry_id)
        entry.update(fields)
        if "text" in fields:
            entry["word_count"] = word_count(entry["text"])
        if touch:
            entry["updated_at"] = now().isoformat()
        return put_entry(entry)


def set_hidden(entry_id, hidden):
    """Hidden entries vanish from the app but stay in the journal (and exports)."""
    return update_entry(entry_id, hidden=bool(hidden))


def delete_entry_permanently(entry_id):
    """Remove an entry from the journal and from every backup."""
    s = vault.current()
    with _lock, db.write_lock:
        get_entry(entry_id)  # KeyError if missing or someone else's
        con = db.connect()
        try:
            with con:
                con.execute("DELETE FROM entries WHERE id = ? AND user_id = ?", (entry_id, s.user_id))
            db.checkpoint(con)
        finally:
            con.close()
        _entries().pop(entry_id, None)
    return backup.forget_entry(entry_id)


def _when(entry):
    return datetime.fromisoformat(entry["created_at"])


def all_entries():
    """All of the current person's entries (including hidden ones), oldest first."""
    with _lock:
        return sorted((dict(e) for e in _entries().values()), key=_when)


def visible_entries():
    return [e for e in all_entries() if not e.get("hidden")]


def wipe_all_entries():
    """Delete all of the current person's entries and draft (and from backups).
    Returns (how many entries, backups that couldn't be cleaned)."""
    s = vault.current()
    with _lock, db.write_lock:
        count = len(_entries())
        con = db.connect()
        try:
            with con:
                con.execute("DELETE FROM entries WHERE user_id = ?", (s.user_id,))
                con.execute("DELETE FROM drafts WHERE user_id = ?", (s.user_id,))
            db.checkpoint(con)
            con.execute("VACUUM")  # rebuild the file without the old pages
        finally:
            con.close()
        _entries().clear()
    return count, backup.forget_user(s.user_id)


# ---- Drafts ---------------------------------------------------------------

def save_draft(text, prompt=None, title=None):
    s = vault.current()
    draft = {"text": text, "prompt": prompt, "title": title, "saved_at": now().isoformat()}
    sealed = crypto.encrypt(s.key, json.dumps(draft, ensure_ascii=False).encode("utf-8"), b"draft")
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                con.execute("INSERT OR REPLACE INTO drafts (user_id, data) VALUES (?, ?)", (s.user_id, sealed))
        finally:
            con.close()


def load_draft():
    s = vault.current()
    con = db.connect()
    try:
        row = con.execute("SELECT data FROM drafts WHERE user_id = ?", (s.user_id,)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    try:
        return json.loads(crypto.decrypt(s.key, row[0], b"draft"))
    except Exception:
        log.exception("Could not decrypt the draft")
        return None


def clear_draft():
    s = vault.current()
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                con.execute("DELETE FROM drafts WHERE user_id = ?", (s.user_id,))
        finally:
            con.close()
