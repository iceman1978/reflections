"""Signing in with an emailed code: for any email address, not only Google's.

Someone types their address; if it's on the invite list (the same list Google
sign-in checks), the app emails a 6-digit code and a sign-in link from
<app name> <signin@<domain>>. Typing the code, or following the link, signs
them in: owning the inbox is the proof, as it is with Google. An address that
isn't invited gets nothing, and the page says the same thing either way, so
nobody can find out who uses the app.

Codes and links work once, for 10 minutes; a code stops working after 5 wrong
tries; an address can ask a few times an hour, and a device for a few
addresses. Only scrambled (keyed hash) copies are stored.

Signing in only says who someone is: their journal stays locked behind their
passphrase, whichever way they sign in.
"""
import hashlib
import hmac
import logging
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from . import config, db, emails, invites, mail

log = logging.getLogger(__name__)

LIFETIME = timedelta(minutes=10)
MAX_TRIES = 5
PER_ADDRESS = 4              # codes per address, per hour
PER_DEVICE = 6               # addresses per device (IP), per hour
SENDER = "signin"

_recent = {}                 # device -> [times], for PER_DEVICE (one server process)
_recent_lock = threading.Lock()


class SignInProblem(Exception):
    """Safe to show the person."""


def _digest(secret, *parts):
    return hmac.new(secret.encode(), "|".join(parts).encode(), hashlib.sha256).hexdigest()


def _now():
    return datetime.now(timezone.utc)


def _device_allowed(device):
    now = time.time()
    with _recent_lock:
        times = [t for t in _recent.get(device, []) if now - t < 3600]
        if len(times) >= PER_DEVICE:
            _recent[device] = times
            return False
        times.append(now)
        _recent[device] = times
        return True


def request(email, device, secret, site_url):
    """Email a code (and link) if the address is invited. Says nothing either way;
    raises SignInProblem only for a mistyped address or too many requests."""
    email = (email or "").strip().lower()
    if not invites.EMAIL.match(email):
        raise SignInProblem("That doesn't look like an email address.")
    if not _device_allowed(device or "?"):
        raise SignInProblem("That's a lot of sign-in requests. Please wait a while and try again.")
    if not invites.allowed(email):
        log.info("Sign-in code asked for an address that isn't invited (nothing sent)")
        return
    since = (_now() - timedelta(hours=1)).isoformat(timespec="seconds")
    con = db.connect()
    try:
        asked = con.execute("SELECT COUNT(*) FROM signin_codes WHERE email = ? AND created_at >= ?",
                            (email, since)).fetchone()[0]
    finally:
        con.close()
    if asked >= PER_ADDRESS:
        raise SignInProblem("That's a lot of codes for one address. Please use the latest one, or wait a while.")
    code = f"{secrets.randbelow(1_000_000):06d}"
    token = secrets.token_urlsafe(24)
    now = _now()
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                con.execute("INSERT INTO signin_codes (email, code_hash, link_hash, created_at, expires_at) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (email, _digest(secret, email, code), _digest(secret, "link", token),
                             now.isoformat(timespec="seconds"), (now + LIFETIME).isoformat(timespec="seconds")))
                con.execute("DELETE FROM signin_codes WHERE expires_at < ?",          # tidy away old ones
                            ((now - timedelta(days=1)).isoformat(timespec="seconds"),))
        finally:
            con.close()
    link = f"{emails.site(site_url)}auth/email?t={token}"
    try:
        mail.send(email, *message(code, link, site_url), local=SENDER)
    except mail.MailProblem as e:
        log.warning("A sign-in code wasn't sent: %s", e)
        raise SignInProblem("The email couldn't be sent just now. Please try again in a minute.") from None


def message(code, link, site_url):
    """(subject, text, html) for a sign-in code."""
    app = config.APP_NAME
    subject = f"{code} is your {app} sign-in code"
    opening = f"Here's your code to sign in to {app}:"
    note = ("It works once, for 10 minutes. If you didn't ask for it, you can ignore this email: "
            "nobody can sign in without it, and your journal stays locked behind your passphrase.")
    text = "\n\n".join([opening, code, f"Or sign in with this link: {link}", note, emails.NO_REPLY])
    html = emails.page(site_url, subject=subject, preview=f"Your code: {code}", hello="Hi,", opening=opening,
                       code=code, button_text="Sign in", button_url=link, sign_in=note)
    return subject, text, html


def _use(con, row_id):
    con.execute("UPDATE signin_codes SET used = 1 WHERE id = ?", (row_id,))


def check_code(email, code, secret):
    """The address, if the code is right (and still good). Raises SignInProblem otherwise."""
    email = (email or "").strip().lower()
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    wrong = SignInProblem("That code didn't work. Check the latest email, or ask for a new code.")
    with db.write_lock:
        con = db.connect()
        try:
            with con:              # saved before raising: a wrong try must count
                row = con.execute("SELECT id, code_hash, tries FROM signin_codes WHERE email = ? AND used = 0 "
                                  "AND expires_at >= ? ORDER BY id DESC LIMIT 1",
                                  (email, _now().isoformat(timespec="seconds"))).fetchone()
                right = bool(row) and row[2] < MAX_TRIES and hmac.compare_digest(row[1], _digest(secret, email, code))
                if right:
                    _use(con, row[0])
                elif row:
                    con.execute("UPDATE signin_codes SET tries = tries + 1 WHERE id = ?", (row[0],))
        finally:
            con.close()
    if not right:
        raise wrong
    if not invites.allowed(email):                       # removed since the code was sent
        raise SignInProblem("This address isn't on the invite list.")
    return email


def check_link(token, secret):
    """The address for a sign-in link, if it's still good (it works once). Else None."""
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                row = con.execute("SELECT id, email FROM signin_codes WHERE link_hash = ? AND used = 0 "
                                  "AND expires_at >= ?", (_digest(secret, "link", token or ""),
                                                          _now().isoformat(timespec="seconds"))).fetchone()
                if not row:
                    return None
                _use(con, row[0])
        finally:
            con.close()
    return row[1] if invites.allowed(row[1]) else None
