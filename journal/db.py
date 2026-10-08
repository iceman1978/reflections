"""The journal database: one SQLite file, journal.db in the data folder.

Tables
  meta     app-wide bookkeeping, as JSON text
  users    one row per person: their Google identity (on the web), their
           preferences, and their data key wrapped by their passphrase and by
           their recovery key. On a personal computer there's a single user, "local".
  entries  one row per entry: a random ID, its owner, and the encrypted entry
  drafts   at most one encrypted draft per person
  usage    how many reflections / prompts each person used per day (for limits)
  feedback suggestions sent with the Feedback button. NOT encrypted: they're
           meant to be read by whoever runs the app (Settings → Feedback received)

Entry contents (text, title, dates, reflection, flags...) exist only inside
the encrypted `data` column, each person's under their own key.
"""
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone

from .config import BACKUP_DIR, DB_PATH, PREF_KEYS, settings

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta    (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users   (id TEXT PRIMARY KEY, google_sub TEXT UNIQUE, email TEXT, name TEXT,
                                    created_at TEXT NOT NULL, prefs TEXT NOT NULL DEFAULT '{}',
                                    key_password TEXT, key_recovery TEXT);
CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, data BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS entries_by_user ON entries (user_id);
CREATE TABLE IF NOT EXISTS drafts  (user_id TEXT PRIMARY KEY, data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS usage   (user_id TEXT NOT NULL, day TEXT NOT NULL, kind TEXT NOT NULL,
                                    count INTEGER NOT NULL, PRIMARY KEY (user_id, day, kind));
CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, email TEXT,
                                    name TEXT, created_at TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
                                    screen TEXT, device TEXT, done INTEGER NOT NULL DEFAULT 0);
"""

LOCAL_USER = "local"

write_lock = threading.RLock()  # one writer at a time
_initialised = set()


def _columns(con, table):
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def _tables(con):
    return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _upgrade_from_v1(con, path):
    """The single-user encrypted journal (version 1) becomes the 'local' account.

    Nothing is decrypted: the wrapped keys move from `meta` to the user row,
    and every entry is marked as belonging to that user. A copy of the file is
    kept in backups/ first.
    """
    target = BACKUP_DIR / "journal-before-accounts.db"
    if not target.exists():
        dst = sqlite3.connect(target)
        try:
            con.backup(dst)
        finally:
            dst.close()
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall()) if "meta" in _tables(con) else {}
    prefs = {k: settings[k] for k in PREF_KEYS if k in settings}
    with con:
        if "user_id" not in _columns(con, "entries"):
            con.execute("ALTER TABLE entries ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local'")
        con.executescript(SCHEMA)
        if "draft" in _tables(con):
            con.execute("INSERT OR REPLACE INTO drafts (user_id, data) SELECT ?, data FROM draft", (LOCAL_USER,))
            con.execute("DROP TABLE draft")
        if "key_password" in meta and not con.execute("SELECT 1 FROM users WHERE id = ?", (LOCAL_USER,)).fetchone():
            con.execute(
                "INSERT INTO users (id, name, created_at, prefs, key_password, key_recovery) VALUES (?, ?, ?, ?, ?, ?)",
                (LOCAL_USER, "local", datetime.now(timezone.utc).isoformat(), json.dumps(prefs),
                 json.dumps(json.loads(meta["key_password"])),
                 json.dumps(json.loads(meta["key_recovery"])) if "key_recovery" in meta else None))
        con.execute("DELETE FROM meta WHERE key IN ('key_password', 'key_recovery')")
    log.info("Journal database upgraded to accounts (a copy of the old file is in backups/)")


def _prepare(con, path):
    con.execute("PRAGMA journal_mode = WAL")
    tables = _tables(con)
    old = "draft" in tables or ("entries" in tables and "user_id" not in _columns(con, "entries"))
    if old:
        _upgrade_from_v1(con, path)
    con.executescript(SCHEMA)
    with con:
        con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))


def connect(path=DB_PATH):
    """A new connection with safe settings. Use `with con:` for a transaction
    that is either fully saved or not at all."""
    con = sqlite3.connect(path, timeout=15)
    con.execute("PRAGMA synchronous = FULL")     # survive power loss
    con.execute("PRAGMA secure_delete = ON")     # overwrite deleted data
    if str(path) not in _initialised:
        with write_lock:
            if str(path) not in _initialised:
                _prepare(con, path)
                _initialised.add(str(path))
    return con


def get_meta(key, default=None):
    con = connect()
    try:
        row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    finally:
        con.close()
    return json.loads(row[0]) if row else default


def set_meta(key, value):
    with write_lock:
        con = connect()
        try:
            with con:
                con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value)))
        finally:
            con.close()


def checkpoint(con):
    """Fold the write-ahead log into the main file (so deleted data leaves it too)."""
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
