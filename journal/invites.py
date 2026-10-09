"""Invitation requests: someone using the app asks for a friend to be let in.

For now, a request goes to whoever runs the app (Settings → Invitation
requests, for the addresses in ADMIN_EMAILS), who adds the address to the
allowed list on the server (and Google's test users). A request shows as
"Has access" as soon as its address is on the allowed list.

Each request records who asked, so successful invitations can earn rewards
later (higher limits, free months, ...).

Like feedback, requests are not encrypted: they're meant to be read by
whoever runs the app.
"""
import re
from datetime import datetime

from . import config, db
from .config import TZ

STATUSES = ("requested", "declined")   # "added" is worked out from the allowed list
PER_DAY = 10
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
COLUMNS = ("id", "user_id", "requester_email", "requester_name", "email", "name", "note", "created_at", "status")


class InviteProblem(Exception):
    """Something about the request, safe to show the person."""


def has_access(email):
    return (email or "").lower() in config.ALLOWED_EMAILS


def _row(r):
    d = dict(zip(COLUMNS, r))
    d["status"] = "added" if has_access(d["email"]) else d["status"]
    return d


def add(user_id, requester_email, requester_name, email, name=None, note=None):
    """Record a request. Returns it. Raises InviteProblem."""
    email = (email or "").strip().lower()
    if not EMAIL.match(email):
        raise InviteProblem("That doesn't look like an email address. Use their Google (Gmail) address.")
    if email == (requester_email or "").lower():
        raise InviteProblem("That's your own address.")
    if has_access(email):
        raise InviteProblem("Good news: they already have access. Send them the link!")
    name = (name or "").strip()[:80] or None
    note = (note or "").strip()[:1000] or None
    now = datetime.now(TZ)
    with db.write_lock:
        con = db.connect()
        try:
            if con.execute("SELECT 1 FROM invites WHERE email = ? AND status = 'requested'", (email,)).fetchone():
                raise InviteProblem("Someone has already asked for them to be invited. They'll be added soon.")
            today = con.execute("SELECT COUNT(*) FROM invites WHERE user_id = ? AND created_at >= ?",
                                (user_id, now.date().isoformat())).fetchone()[0]
            if today >= PER_DAY:
                raise InviteProblem("That's plenty of invitations for one day. Please send more tomorrow.")
            with con:
                cur = con.execute(
                    "INSERT INTO invites (user_id, requester_email, requester_name, email, name, note, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (user_id, requester_email, requester_name, email, name, note, now.isoformat(timespec="seconds")))
            row = con.execute(f"SELECT {', '.join(COLUMNS)} FROM invites WHERE id = ?", (cur.lastrowid,)).fetchone()
        finally:
            con.close()
    return _row(row)


def count_for(user_id):
    """How many invitations this person has asked for (for the "invite someone?" suggestion)."""
    con = db.connect()
    try:
        return con.execute("SELECT COUNT(*) FROM invites WHERE user_id = ?", (user_id,)).fetchone()[0]
    finally:
        con.close()


def mine(user_id):
    """This person's requests, newest first (what they see in the Invite dialog)."""
    con = db.connect()
    try:
        rows = con.execute(f"SELECT {', '.join(COLUMNS)} FROM invites WHERE user_id = ? ORDER BY id DESC",
                           (user_id,)).fetchall()
    finally:
        con.close()
    return [{k: v for k, v in _row(r).items() if k in ("email", "name", "created_at", "status")} for r in rows]


def all_requests():
    con = db.connect()
    try:
        rows = con.execute(f"SELECT {', '.join(COLUMNS)} FROM invites ORDER BY id DESC").fetchall()
    finally:
        con.close()
    return [_row(r) for r in rows]


def set_status(invite_id, status):
    if status not in STATUSES:
        raise ValueError(status)
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                changed = con.execute("UPDATE invites SET status = ? WHERE id = ?", (status, invite_id)).rowcount
        finally:
            con.close()
    if not changed:
        raise KeyError(invite_id)
