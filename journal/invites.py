"""Invitations: who may sign in, and requests to let a friend in.

Someone using the app asks for a friend to be let in; the request goes to
whoever runs the app (Settings → Invitation requests, for the addresses in
ADMIN_EMAILS). Their Invite button lets the person in on the spot and sends
them an invitation email; they can also invite someone directly. A request
shows as "Has access" once its address can sign in.

Who can sign in: everyone in the `access` table, plus any addresses still in
ALLOWED_EMAILS on the server. Those are copied into the table when the app
starts, so once it has run, ALLOWED_EMAILS can be emptied and the list kept
in Settings alone. With neither, anyone can.

Each request records who asked, so successful invitations can earn rewards
later (higher limits, free months, ...).

Like feedback, requests are not encrypted: they're meant to be read by
whoever runs the app.
"""
import re
from datetime import datetime

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config, db, mail
from .config import ROOT, TZ

EMAILS = Environment(loader=FileSystemLoader(ROOT / "templates" / "email"), autoescape=select_autoescape(["html"]))


def _png_width(path):
    """A PNG's width, from its header (no imaging library on the server)."""
    with open(path, "rb") as f:
        return int.from_bytes(f.read(24)[16:20], "big")

STATUSES = ("requested", "declined")   # "added" is worked out from the allowed list
PER_DAY = 10
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
COLUMNS = ("id", "user_id", "requester_email", "requester_name", "email", "name", "note", "created_at", "status",
           "message", "emailed_at")


class InviteProblem(Exception):
    """Something about the request, safe to show the person."""


def access_list():
    """Every address that can sign in: the server's list and everyone invited from the app."""
    con = db.connect()
    try:
        return config.ALLOWED_EMAILS | {r[0] for r in con.execute("SELECT email FROM access")}
    finally:
        con.close()


def import_allowed():
    """Copy the addresses in ALLOWED_EMAILS into the access table (once each).
    Returns how many were new."""
    now = datetime.now(TZ).isoformat(timespec="seconds")
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                return sum(con.execute("INSERT OR IGNORE INTO access (email, added_at) VALUES (?, ?)",
                                       (email, now)).rowcount for email in sorted(config.ALLOWED_EMAILS))
        finally:
            con.close()


def grant(email):
    """Let someone sign in (again), without sending anything."""
    email = (email or "").strip().lower()
    if not EMAIL.match(email):
        raise InviteProblem("That doesn't look like an email address.")
    _write("INSERT OR IGNORE INTO access (email, added_at) VALUES (?, ?)",
           (email, datetime.now(TZ).isoformat(timespec="seconds")))


def remove(email):
    """Take away someone's access. Their journal stays, should they be let back in.
    Returns False if ALLOWED_EMAILS on the server still lets them in."""
    email = (email or "").strip().lower()
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                con.execute("DELETE FROM access WHERE email = ?", (email,))
        finally:
            con.close()
    return email not in config.ALLOWED_EMAILS


def has_access(email):
    return (email or "").lower() in access_list()


def allowed(email):
    """May this address sign in? Anyone can while there's no list at all."""
    everyone = access_list()
    return not everyone or (email or "").lower() in everyone


def _row(r, everyone=None):
    d = dict(zip(COLUMNS, r))
    if d["email"] in (access_list() if everyone is None else everyone):
        d["status"] = "added"
    return d


def add(user_id, requester_email, requester_name, email, name=None, note=None):
    """Record a request. Returns it. Raises InviteProblem.

    The note is a personal note for the person invited, kept as the request's
    `message` and put in their invitation email. (Requests from before
    2026-10-15 kept a note for whoever runs the app in `note`, never sent.)"""
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
                    "INSERT INTO invites (user_id, requester_email, requester_name, email, name, message, created_at) "
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
    everyone = access_list()
    return [{k: v for k, v in _row(r, everyone).items() if k in ("email", "name", "created_at", "status")} for r in rows]


def all_requests():
    con = db.connect()
    try:
        rows = con.execute(f"SELECT {', '.join(COLUMNS)} FROM invites ORDER BY id DESC").fetchall()
    finally:
        con.close()
    everyone = access_list()
    return [_row(r, everyone) for r in rows]


# ---- Letting someone in, and the invitation email ----------------------------

def _get(invite_id):
    con = db.connect()
    try:
        row = con.execute(f"SELECT {', '.join(COLUMNS)} FROM invites WHERE id = ?", (invite_id,)).fetchone()
    finally:
        con.close()
    if not row:
        raise KeyError(invite_id)
    return row


def _write(sql, args):
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                return con.execute(sql, args)
        finally:
            con.close()


def admit(invite_id, site_url):
    """Let the person in a request sign in, and email them an invitation.

    Returns (invite, problem): problem is None once the email has gone, or
    says why it didn't (they can sign in all the same). Raises KeyError.
    Calling it again sends the email again."""
    inv = dict(zip(COLUMNS, _get(invite_id)))
    now = datetime.now(TZ).isoformat(timespec="seconds")
    _write("INSERT OR IGNORE INTO access (email, added_at) VALUES (?, ?)", (inv["email"], now))
    _write("UPDATE invites SET status = 'requested' WHERE id = ?", (invite_id,))   # undo a decline
    problem = None
    try:
        mail.send(inv["email"], *invitation(inv, site_url))
    except mail.MailProblem as e:
        problem = str(e)
    else:
        _write("UPDATE invites SET emailed_at = ? WHERE id = ?", (now, invite_id))
    return _row(_get(invite_id)), problem


def invite(user_id, inviter_email, inviter_name, email, name=None, message=None, site_url=""):
    """Whoever runs the app invites someone directly: let them in and email them.
    Returns (invite, problem), as admit() does. Raises InviteProblem."""
    email = (email or "").strip().lower()
    if not EMAIL.match(email):
        raise InviteProblem("That doesn't look like an email address. Use their Google (Gmail) address.")
    name = (name or "").strip()[:80] or None
    message = (message or "").strip()[:1000] or None
    cur = _write("INSERT INTO invites (user_id, requester_email, requester_name, email, name, created_at, message) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (user_id, inviter_email, inviter_name, email, name,
                  datetime.now(TZ).isoformat(timespec="seconds"), message))
    return admit(cur.lastrowid, site_url)


def invitation(inv, site_url):
    """The invitation email for a request: (subject, text, html).

    Only the message (a personal note written for them) goes in; the older
    `note`, written to whoever runs the app, never does."""
    edition = config.EDITION
    app, lead = config.APP_NAME, edition.get("lead", "")
    who = (inv.get("requester_name") or "").strip()
    subject = f"{who} invited you to {app}" if who else f"You're invited to {app}"
    hello = f"Hi {inv['name']}," if inv.get("name") else "Hi,"
    opening = f"{who} has invited you to {app}" if who else f"You're invited to {app}"   # then " — " and the welcome line
    # Whoever asked may not know which address someone uses with Google.
    ask = f"ask {who} to invite" if who else "ask for an invitation for"
    sign_in = (f"Sign in with your Google account. If {inv['email']} isn't the address you use with Google, "
               f"{ask} the one you do (only Google sign-in is available while {app} is being tested). "
               "The first time, you'll choose a passphrase that keeps your journal private: "
               "only you can read what you write.")
    no_reply = ("This address doesn't receive replies. If you have a question, use the Feedback "
                "button (the speech bubble) once you're signed in.")
    message = inv.get("message")
    points = edition.get("email_points") or [p["title"] for p in edition.get("points", [])]
    quote = edition.get("welcome_quote") or {}
    quote_cite = re.sub(r"<[^>]+>", "", quote.get("cite_html", ""))
    text = "\n\n".join(p for p in (
        hello, f"{opening} — {lead}" if lead else f"{opening}.", f"“{message}”" if message else None,
        "\n".join(f"- {p}" for p in points) or None,
        f"Open {app}: {site_url}", sign_in,
        f"{quote['text']}\n{quote_cite}" if quote else None, no_reply) if p)
    site = site_url if site_url.endswith("/") else site_url + "/"
    brand = f"{site}static/brands/{config.EDITION_NAME}"
    wordmark = ROOT / "static" / "brands" / config.EDITION_NAME / "email-wordmark.png"
    page = EMAILS.get_template("invitation.html").render(
        subject=subject, preview=f"{opening} — {lead}" if lead else f"{opening}.", app=app, tagline=edition.get("tagline"),
        logo_url=f"{brand}/logo.png", wordmark_url=f"{brand}/email-wordmark.png",
        wordmark_width=_png_width(wordmark) // 2 if wordmark.exists() else 300,
        hello=hello, opening=opening if lead else f"{opening}.", lead=lead, message=message, who=f"— {who}" if who else "",
        points=points, site_url=site, sign_in=sign_in, quote=quote.get("text"), quote_cite=quote_cite,
        no_reply=no_reply)
    return subject, text, page


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
