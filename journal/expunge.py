"""Deleting a person entirely (Admin → Usage → Remove → Delete everything).

Unlike Remove (access only, the journal kept should they be let back in), this
takes them out of the system: their account, journal, drafts, kept passages,
prayer list, passkeys, counts, reminders, invitation requests, shares and
feedback, by their account and by their email address, from the database and
from every backup. Anything still waiting for them (passages shared with them,
sign-in codes) goes too.

What stays, because it can't be linked back to them: the anonymous counts of
kept and shared passages, the anonymous reflection ratings, and the app's total
token counts. A "don't send me shared passages" request for their address also
stays: it's a promise made to that address, which should still hold.
"""
import logging

from . import backup, db, store, vault

log = logging.getLogger(__name__)

# (table, column) holding the person's account ID, or their email address.
BY_USER = (("entries", "user_id"), ("drafts", "user_id"), ("kept", "user_id"), ("prayers", "user_id"),
           ("usage", "user_id"), ("activity", "user_id"), ("reminders", "user_id"), ("passkeys", "user_id"),
           ("invites", "user_id"), ("shares", "user_id"), ("feedback", "user_id"), ("users", "id"))
BY_EMAIL = (("access", "email"), ("invites", "email"), ("invites", "requester_email"), ("signin_codes", "email"),
            ("shared_inbox", "email"), ("feedback", "email"))


def statements(con, user_id, email):
    """The deletes for one person, for the tables (and columns) this database has."""
    tables = {row[0]: {c[1] for c in con.execute(f"PRAGMA table_info({row[0]})")}
              for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    out = []
    for table, column in BY_USER:
        if column in tables.get(table, ()):
            out.append((f"DELETE FROM {table} WHERE {column} = ?", (user_id,)))
    if email:
        for table, column in BY_EMAIL:
            if column in tables.get(table, ()):
                out.append((f"DELETE FROM {table} WHERE lower({column}) = ?", (email.lower(),)))
    return out


def expunge(user_id):
    """Delete this person from everything. Returns (found, backups that couldn't be cleaned)."""
    con = db.connect()
    try:
        row = con.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        con.close()
    if not row:
        return False, []
    email = (row[0] or "").strip().lower() or None
    vault.lock_user(user_id, "account deleted")           # signed out everywhere, journal key forgotten
    with db.write_lock:
        con = db.connect()
        try:
            con.execute("PRAGMA secure_delete = ON")
            with con:
                for sql, args in statements(con, user_id, email):
                    con.execute(sql, args)
            db.checkpoint(con)
            con.execute("VACUUM")                          # rebuild the file without the old pages
        finally:
            con.close()
    store._forget(user_id)
    failed = backup.forget_everything(lambda c: statements(c, user_id, email))
    log.warning("An account was deleted entirely (%d backups couldn't be cleaned)", len(failed))
    return True, failed
