"""The admin's "new work" email: what's new since the last one, at most twice a day.

At 9:00 and 17:00 (the journal's time zone), if anything is new since the last
check, the addresses in ADMIN_EMAILS get a short email from
<app name> <admin@<domain>>: invitation requests waiting (with who asked),
new feedback, people who joined, and emails that couldn't be sent. Counts and
the askers' names only: no addresses, notes or feedback text, which stay in
the app. Each admin can turn it off (Admin tab).
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from . import config, db, emails, invites, mail
from .config import TZ

log = logging.getLogger(__name__)

SLOTS = (9, 17)              # hours, in the journal's time zone
SENDER = "admin"


def _meta(con, key):
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _after(stamp, since):
    try:
        return datetime.fromisoformat(stamp) > since
    except (TypeError, ValueError):
        return False


def gather(since):
    """What's new since `since` (an aware datetime)."""
    con = db.connect()
    try:
        asks = con.execute("SELECT requester_name, requester_email, email, created_at FROM invites "
                           "WHERE status = 'requested'").fetchall()
        feedback = con.execute("SELECT created_at FROM feedback WHERE done = 0").fetchall()
        people = con.execute("SELECT created_at FROM users WHERE email IS NOT NULL").fetchall()
        failed = con.execute("SELECT at FROM mail_failures").fetchall()
    finally:
        con.close()
    allowed = invites.access_list()
    waiting = [(name or (asker or "someone").split("@")[0]) for name, asker, email, at in asks
               if email not in allowed and _after(at, since)]
    return {"requests": waiting,
            "feedback": sum(_after(at, since) for (at,) in feedback),
            "joined": sum(_after(at, since) for (at,) in people),
            "failed": sum(_after(at, since) for (at,) in failed)}


def anything(news):
    return bool(news["requests"] or news["feedback"] or news["joined"] or news["failed"])


def _plural(n, one, many=None):
    return f"{n} {one if n == 1 else many or one + 's'}"


def email(news, when, site_url):
    """(subject, text, html)."""
    app = config.APP_NAME
    lines, parts = [], []
    if news["requests"]:
        names = sorted(set(news["requests"]))
        n = len(news["requests"])
        lines.append(f"{_plural(n, 'invitation request')} waiting for you, from {', '.join(names)}")
        parts.append(_plural(n, "invitation request"))
    if news["feedback"]:
        lines.append(f"{_plural(news['feedback'], 'new piece', 'new pieces')} of feedback")
        parts.append(f"{news['feedback']} new feedback")
    if news["joined"]:
        lines.append(f"{_plural(news['joined'], 'person', 'people')} joined")
        parts.append(f"{news['joined']} joined")
    if news["failed"]:
        lines.append(f"{_plural(news['failed'], 'email')} couldn't be sent (Render's logs say why)")
        parts.append(f"{news['failed']} email{'s' if news['failed'] != 1 else ''} not sent")
    subject = f"{app}: {', '.join(parts)}"
    opening = f"Since {when}:"
    home = emails.site(site_url)
    link = f"{home}?settings=admin"
    text = "\n\n".join([opening, "\n".join(f"- {l}" for l in lines), f"Open Admin: {link}",
                        "You can turn these emails off at the top of the Admin tab."])
    html = emails.page(site_url, subject=subject, preview="; ".join(lines), hello="Hi,", opening=opening,
                       points=lines, button_text="Open Admin", button_url=link,
                       sign_in="You can turn these emails off at the top of the Admin tab.")
    return subject, text, html


def _admins():
    """The admins who want the email: their addresses."""
    con = db.connect()
    try:
        rows = dict((e.lower(), p) for e, p in con.execute("SELECT email, prefs FROM users WHERE email IS NOT NULL"))
    finally:
        con.close()
    out = []
    for address in sorted(config.ADMIN_EMAILS):
        try:
            on = json.loads(rows.get(address) or "{}").get("admin_digest", True)
        except ValueError:
            on = True
        if on:
            out.append(address)
    return out


def send_due(now, site_url):
    """At a slot (9:00, 17:00), email the admins what's new. Returns True if an email went."""
    slot = max((h for h in SLOTS if now.hour >= h), default=None)
    if slot is None or not config.ADMIN_EMAILS:
        return False
    slot_id = f"{now.date().isoformat()}T{slot:02d}"
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                if _meta(con, "digest_slot") == slot_id:
                    return False
                since_text = _meta(con, "digest_since")
                con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('digest_slot', ?)", (slot_id,))
        finally:
            con.close()
    since = datetime.fromisoformat(since_text) if since_text else now - timedelta(hours=12)
    news = gather(since)
    sent = False
    if anything(news):
        when = since.astimezone(TZ)
        label = ("this morning" if when.date() == now.date() and when.hour < 12 else
                 "this afternoon" if when.date() == now.date() else
                 "yesterday" if when.date() == now.date() - timedelta(days=1) else when.strftime("%B %d"))
        for address in _admins():
            try:
                mail.send(address, *email(news, label, site_url), local=SENDER)
                sent = True
            except mail.MailProblem as e:
                log.warning("The admin email wasn't sent: %s", e)
        if not sent:                                   # try again at the next check
            with db.write_lock:
                con = db.connect()
                try:
                    with con:
                        con.execute("DELETE FROM meta WHERE key = 'digest_slot'")
                finally:
                    con.close()
            return False
    with db.write_lock:                                # the next email covers from now
        con = db.connect()
        try:
            with con:
                con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('digest_since', ?)",
                            (now.astimezone(timezone.utc).isoformat(timespec="seconds"),))
        finally:
            con.close()
    if sent:
        log.info("Sent the admin email")
    return sent


def record_failure(sender):
    """An email that couldn't be sent (counted in the next admin email)."""
    try:
        with db.write_lock:
            con = db.connect()
            try:
                with con:
                    con.execute("INSERT INTO mail_failures (at, sender) VALUES (?, ?)",
                                (datetime.now(timezone.utc).isoformat(timespec="seconds"), sender))
            finally:
                con.close()
    except Exception:
        log.exception("Couldn't record an email failure")
