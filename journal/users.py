"""People and their preferences.

On a personal computer there's one user, "local". On the web, each person who
signs in with Google (and is on the allowed list) gets their own user, with
their own journal, encryption key and preferences.
"""
import json
import secrets
from datetime import datetime, timezone

from . import config, db
from .config import PREF_KEYS
from .db import LOCAL_USER


def get(user_id):
    con = db.connect()
    try:
        row = con.execute("SELECT id, google_sub, email, name, prefs, key_password, key_recovery "
                          "FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    return {"id": row[0], "google_sub": row[1], "email": row[2], "name": row[3],
            "prefs": json.loads(row[4] or "{}"),
            "key_password": json.loads(row[5]) if row[5] else None,
            "key_recovery": json.loads(row[6]) if row[6] else None}


def ensure_local():
    """The single user of a journal on a personal computer."""
    if not get(LOCAL_USER):
        prefs = {k: config.settings[k] for k in PREF_KEYS if k in config.settings}
        with db.write_lock:
            con = db.connect()
            try:
                with con:
                    con.execute("INSERT OR IGNORE INTO users (id, name, created_at, prefs) VALUES (?, ?, ?, ?)",
                                (LOCAL_USER, "local", datetime.now(timezone.utc).isoformat(), json.dumps(prefs)))
            finally:
                con.close()
    return LOCAL_USER


def from_google(sub, email, name):
    """Find or create the user for a Google account. Returns the user ID."""
    con = db.connect()
    try:
        row = con.execute("SELECT id FROM users WHERE google_sub = ?", (sub,)).fetchone()
    finally:
        con.close()
    if row:
        set_identity(row[0], email, name)
        return row[0]
    user_id = secrets.token_hex(8)
    first_name = (name or "").split()[0] if name else ""
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                con.execute("INSERT INTO users (id, google_sub, email, name, created_at, prefs) VALUES (?, ?, ?, ?, ?, ?)",
                            (user_id, sub, email, name, datetime.now(timezone.utc).isoformat(),
                             json.dumps({"user_name": first_name})))
        finally:
            con.close()
    return user_id


def set_identity(user_id, email, name):
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                con.execute("UPDATE users SET email = ?, name = ? WHERE id = ?", (email, name, user_id))
        finally:
            con.close()


def set_keys(user_id, key_password=None, key_recovery=None):
    """Store a wrapped data key (by passphrase and/or by recovery key)."""
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                if key_recovery is not None:
                    con.execute("UPDATE users SET key_recovery = ? WHERE id = ?", (json.dumps(key_recovery), user_id))
                if key_password is not None:
                    con.execute("UPDATE users SET key_password = ? WHERE id = ?", (json.dumps(key_password), user_id))
        finally:
            con.close()


# ---- Preferences ----------------------------------------------------------

def prefs(user_id):
    """This person's preferences, with defaults filled in."""
    user = get(user_id)
    stored = user["prefs"] if user else {}
    return {k: stored.get(k, config.DEFAULTS[k]) for k in PREF_KEYS}


def update_prefs(user_id, changes):
    """Validate and save preference changes. Raises ValueError with a readable message."""
    clean = config.validate({k: v for k, v in changes.items() if k in PREF_KEYS})
    current = get(user_id)["prefs"]
    current.update(clean)
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                con.execute("UPDATE users SET prefs = ? WHERE id = ?", (json.dumps(current), user_id))
        finally:
            con.close()
    return prefs(user_id)
