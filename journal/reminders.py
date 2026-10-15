"""Reminder emails: a gentle note when someone hasn't written for a while.

On the web only, from <app name> <reminder@<domain>>. Each morning from 7:00
(the journal's time zone), anyone with reminders on (Settings → Basic; on by
default) who can sign in gets at most one email:

- 2, 6 and 14 days after their last entry, then nothing until they write
  again (which starts the count over);
- once, a day after signing in, if they never chose their passphrase.

What it knows: the days each person wrote (the `activity` counts) and how
many entries they have. Never what they wrote: entries are encrypted, and
nothing from them goes into an email.

Every reminder has a link that turns reminders off without signing in
(and the headers Gmail and others use for one-click unsubscribe).
"""
import json
import logging
import threading
import time
from datetime import date, datetime

from itsdangerous import BadSignature, URLSafeSerializer

from . import config, db, emails, invites, mail
from .config import TZ

log = logging.getLogger(__name__)

STAGES = (2, 6, 14)          # days without an entry before each reminder
SEND_FROM, SEND_UNTIL = 7, 20  # hours, in the journal's time zone
CHECK_EVERY = 600            # seconds
SENDER = "reminder"


# ---- Turning reminders off from the email ----------------------------------

def _signer(secret):
    return URLSafeSerializer(secret, salt="reminders-off")


def off_token(user_id, secret):
    return _signer(secret).dumps(user_id)


def user_from_token(token, secret):
    try:
        return _signer(secret).loads(token or "")
    except BadSignature:
        return None


def turn_off(user_id):
    from . import users
    users.update_prefs(user_id, {"email_reminders": False})
    log.info("Reminders turned off from an email")


# ---- Who is due --------------------------------------------------------------

def _state(con, user_id):
    row = con.execute("SELECT baseline, stage, sent_on, setup_sent, ever FROM reminders WHERE user_id = ?",
                      (user_id,)).fetchone()
    return dict(zip(("baseline", "stage", "sent_on", "setup_sent", "ever"), row)) if row else None


def due(today):
    """Who gets a reminder today, and which: [(person, kind, days)]. kind is
    "setup" or 1, 2, 3 (the stage); days is the time since their last entry
    (or since they set up, if they haven't written yet)."""
    con = db.connect()
    try:
        people = con.execute("SELECT id, email, name, created_at, key_password IS NOT NULL, prefs FROM users "
                             "WHERE email IS NOT NULL AND email != ''").fetchall()
        last = dict(con.execute("SELECT user_id, MAX(day) FROM activity WHERE entries > 0 GROUP BY user_id"))
        counts = dict(con.execute("SELECT user_id, COUNT(*) FROM entries GROUP BY user_id"))
        started = con.execute("SELECT MIN(day) FROM activity").fetchone()[0]   # counts kept since
        states = {row[0]: _state(con, row[0]) for row in people}
    finally:
        con.close()
    allowed = invites.access_list()
    out = []
    for uid, email, name, created, set_up, prefs in people:
        prefs = json.loads(prefs or "{}")
        if not prefs.get("email_reminders", True) or (allowed and email.lower() not in allowed):
            continue
        joined = _local_day(created)
        state = states.get(uid) or {}
        person = {"id": uid, "email": email, "name": (prefs.get("user_name") or "").strip()
                  or (name or "").split(" ")[0] or None, "entries": counts.get(uid, 0),
                  "first": not state.get("ever")}
        if not set_up:
            if (today - joined).days >= 1 and not state.get("setup_sent"):
                out.append((person, "setup", (today - joined).days))
            continue
        # Days since their last entry; before the counts began, since they began (or since joining).
        baseline = last.get(uid) or max(filter(None, (joined.isoformat(), started)))
        days = (today - date.fromisoformat(baseline)).days
        stage = sum(days >= d for d in STAGES)
        done = state.get("stage", 0) if state.get("baseline") == baseline else 0
        if stage > done and state.get("sent_on") != today.isoformat():
            person["baseline"], person["was"] = baseline, (state.get("baseline"), done)
            out.append((person, stage, days))
    return out


def _local_day(stamp):
    """A stored time (UTC, ISO) as a day in the journal's time zone."""
    try:
        return datetime.fromisoformat(stamp).astimezone(TZ).date()
    except (TypeError, ValueError):
        return datetime.now(TZ).date()


def _claim(person, kind, today):
    """Mark the reminder as sent before sending it, so two checks can't both send it.
    Returns False if another check got there first."""
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                con.execute("INSERT OR IGNORE INTO reminders (user_id) VALUES (?)", (person["id"],))
                if kind == "setup":
                    return con.execute("UPDATE reminders SET setup_sent = 1, ever = 1 WHERE user_id = ? "
                                       "AND setup_sent = 0", (person["id"],)).rowcount == 1
                return con.execute(
                    "UPDATE reminders SET baseline = ?, stage = ?, sent_on = ?, ever = 1 WHERE user_id = ? "
                    "AND (sent_on IS NULL OR sent_on != ?) AND (baseline IS ? AND stage = ? OR baseline IS NOT ?)",
                    (person["baseline"], kind, today.isoformat(), person["id"], today.isoformat(),
                     person["baseline"], person["was"][1], person["baseline"])).rowcount == 1
        finally:
            con.close()


def _unclaim(person, kind):
    """The email couldn't be sent: put things back, to try again next time."""
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                if kind == "setup":
                    con.execute("UPDATE reminders SET setup_sent = 0 WHERE user_id = ?", (person["id"],))
                else:
                    baseline, stage = person["was"]
                    con.execute("UPDATE reminders SET baseline = ?, stage = ?, sent_on = NULL WHERE user_id = ?",
                                (baseline, stage, person["id"]))
                if person["first"]:
                    con.execute("UPDATE reminders SET ever = 0 WHERE user_id = ?", (person["id"],))
        finally:
            con.close()


# ---- The email -----------------------------------------------------------------

def _prompt(person, today):
    prompts = config.EDITION.get("reminder_prompts") or []
    if not prompts:
        return None
    return prompts[(sum(person["id"].encode()) + today.toordinal()) % len(prompts)]


def email(person, kind, days, today, site_url, off_url):
    """(subject, text, html) for one reminder."""
    app = config.APP_NAME
    hello = f"Hi {person['name']}," if person["name"] else "Hi,"
    wrote = person["entries"] > 0
    few = "You don't need to write a lot: a few honest sentences will do."
    if kind == "setup":
        subject = "Your journal is ready when you are"
        opening = f"You signed in to {app}, but haven't chosen your passphrase yet."
        paragraphs = ["It takes a minute, and then your journal is ready. A few honest lines a day is all it asks."]
        button, sign_in = "Finish setting up", ("Sign in with Google, then choose a passphrase only you know: "
                                                 "it keeps your journal private.")
    elif kind == 1:
        subject = "A few lines today?" if wrote else "Your journal is waiting for its first entry"
        opening = (f"It's been {days} days since your last entry." if wrote
                   else "Your journal is set up and waiting for its first entry.")
        paragraphs, button, sign_in = [few], "Write today's entry", None
    elif kind == 2:
        subject = f"It's been {days} days"
        opening = (f"It's been {days} days since your last entry." if wrote
                   else f"It's been {days} days since you set up your journal, and it's still waiting for its first entry.")
        paragraphs = ["There's no need to catch up on everything you missed: just start with today. "
                      "A few sentences will do."]
        button, sign_in = "Write today's entry", None
    else:
        subject = "Your journal is here whenever you're ready"
        opening = (f"It's been {days} days since your last entry." if wrote
                   else f"It's been {days} days since you set up your journal.")
        paragraphs = ["Your journal will be here whenever you're ready, and starting again takes only a few lines.",
                      "This is the last reminder until you write again."]
        button, sign_in = "Open my journal", None
    if person["first"]:
        paragraphs.append("You can turn these reminders off in Settings → Basic, or with the link at the bottom.")
    prompt = None if kind == "setup" else _prompt(person, today)
    quote, cite = emails.quote()
    text = "\n\n".join(p for p in (
        hello, opening, *paragraphs, f"A question to start with: {prompt}" if prompt else None,
        f"{button}: {emails.site(site_url)}", sign_in, f"{quote}\n{cite}" if quote else None,
        f"Turn off reminders: {off_url}", emails.NO_REPLY) if p)
    html = emails.page(site_url, subject=subject, preview=f"{opening} {paragraphs[0]}", hello=hello,
                       opening=opening, paragraphs=paragraphs, prompt=prompt, button_text=button,
                       sign_in=sign_in, unsubscribe_url=off_url)
    return subject, text, html


# ---- Sending -----------------------------------------------------------------------

def send_due(today, secret, site_url):
    """Send today's reminders. Returns how many went."""
    home = emails.site(site_url)
    sent = 0
    for person, kind, days in due(today):
        if not _claim(person, kind, today):
            continue
        off_url = f"{home}reminders/off?t={off_token(person['id'], secret)}"
        try:
            mail.send(person["email"], *email(person, kind, days, today, home, off_url), local=SENDER,
                      headers={"List-Unsubscribe": f"<{off_url}>",
                               "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})
        except mail.MailProblem as e:
            _unclaim(person, kind)
            log.warning("A reminder wasn't sent: %s", e)
            if not mail.ready():
                break                  # nothing will go until it's set up
            continue
        sent += 1
    if sent:
        log.info("Sent %d reminder(s)", sent)
    return sent


def _watch(secret, site_url):
    while True:
        try:
            now = datetime.now(TZ)
            if SEND_FROM <= now.hour < SEND_UNTIL and mail.ready():
                send_due(now.date(), secret, site_url)
        except Exception:                                   # never let the timer die
            log.exception("Reminders check failed")
        time.sleep(CHECK_EVERY)


def start(secret):
    """On the server: check every few minutes, and send in the morning."""
    if not config.PUBLIC_HOST:
        return
    threading.Thread(target=_watch, args=(secret, f"https://{config.PUBLIC_HOST}/"),
                     name="reminders", daemon=True).start()
