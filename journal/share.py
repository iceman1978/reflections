"""Sharing a passage by email: someone sends a kept (or quoted) passage to a
friend, with a note of their own, from <app name> <share@<domain>>.

Only the passage and their note are sent: never an entry, a reflection or
anything else they wrote. The passage is checked against the library before
it goes (the same check as quotes in reflections), so the form can't be used
to send any text from the app's address. Replies go to the person sharing.

Limits keep the address's good name (which is what gets invitations and
reminders delivered): a few shares a day per person, a couple per address,
and anyone can ask not to receive shared passages (the link in every one).
"""
import json
import re
from datetime import datetime, timedelta, timezone

from itsdangerous import BadSignature, URLSafeSerializer

from . import config, db, emails, invites, kept, mail, passages, sources
from .config import TZ

PER_DAY = 10                 # shares per person, per day
PER_ADDRESS = 2              # to the same address, per day
SENDER = "share"


class ShareProblem(Exception):
    """Something about the share, safe to show the person."""


# ---- Asking not to receive shared passages ------------------------------------

def _signer(secret):
    return URLSafeSerializer(secret, salt="share-stop")


def stop_token(email, secret):
    return _signer(secret).dumps(email)


def email_from_token(token, secret):
    try:
        return _signer(secret).loads(token or "")
    except BadSignature:
        return None


def block(email):
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                con.execute("INSERT OR IGNORE INTO share_blocks (email, added_at) VALUES (?, ?)",
                            (email.lower(), datetime.now(timezone.utc).isoformat(timespec="seconds")))
        finally:
            con.close()


def _blocked(con, email):
    return con.execute("SELECT 1 FROM share_blocks WHERE email = ?", (email,)).fetchone() is not None


# ---- The passage -------------------------------------------------------------------

def passage_url(url, quote):
    """The passage's page, scrolled to its words (as on the page: static/app.js passageUrl)."""
    from urllib.parse import quote as enc
    words = [w for w in (quote or "").split() if w]
    if not words:
        return url
    words[0] = re.sub(r"^[^\w]+", "", words[0])
    words[-1] = re.sub(r"[^\w]+$", "", words[-1])
    part = lambda ws: enc(" ".join(w for w in ws if w), safe="").replace("-", "%2D")   # noqa: E731
    fragment = part(words) if len(words) <= 8 else f"{part(words[:4])},{part(words[-4:])}"
    return f"{url}{':~:text=' if '#' in url else '#:~:text='}{fragment}"


def where(c):
    """'Matthew 6:34 (BSB)' or 'Marcus Aurelius, Meditations 4.3'."""
    loc = c.get("location") or ""
    place = "" if not loc else f", {loc}" if re.match(r"[^\W\d_§]", loc) or loc.startswith("§") else f" {loc}"
    text = f"{c['author'] + ', ' if c.get('author') else ''}{c['title']}{place}"
    return f"{text} (BSB)" if c.get("translation") == "Berean Standard Bible" else text


# ---- Sending -----------------------------------------------------------------------

def send(user_id, sharer_email, sharer_name, to, name, note, work, location, quote, site_url, secret):
    """Email a passage. Raises ShareProblem (or mail.MailProblem if the email can't go)."""
    to = (to or "").strip().lower()
    if not invites.EMAIL.match(to):
        raise ShareProblem("That doesn't look like an email address.")
    if to == (sharer_email or "").lower():
        raise ShareProblem("That's your own address.")
    found = checked(work, location, quote)
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    con = db.connect()
    try:
        if _blocked(con, to):
            raise ShareProblem("They've asked not to receive passages shared through "
                               f"{config.APP_NAME}.")
        mine = con.execute("SELECT email FROM shares WHERE user_id = ? AND sent_at >= ?", (user_id, since)).fetchall()
    finally:
        con.close()
    if len(mine) >= PER_DAY:
        raise ShareProblem("That's plenty of sharing for one day. Please share more tomorrow.")
    if sum(e == to for (e,) in mine) >= PER_ADDRESS:
        raise ShareProblem("You've shared a couple with them today already. Save the next for tomorrow.")

    who = (sharer_name or "").strip() or "A friend"
    name = (name or "").strip()[:80] or None
    note = (note or "").strip()[:1000] or None
    stop_url = f"{emails.site(site_url)}share/stop?t={stop_token(to, secret)}"
    saved = passages.save(found)                          # its theme picks the drawing
    picture = passages.drawing(saved and saved["theme"], "email-vignettes")
    con = db.connect()
    try:
        member = con.execute("SELECT 1 FROM users WHERE lower(email) = ?", (to,)).fetchone() is not None
    finally:
        con.close()
    mail.send(to, *email(found, who, name, note, invites.allowed(to), site_url, stop_url,
                         f"{emails.site(site_url)}static/{picture}" if picture else None, link(saved["key"], site_url),
                         member),
              local=SENDER, reply_to=sharer_email or None)
    passages.shared(saved["key"])
    with db.write_lock:                                   # waiting for them, in their Kept passages
        con = db.connect()
        try:
            with con:
                con.execute("INSERT INTO shared_inbox (email, passage, sharer, note, shared_at) VALUES (?, ?, ?, ?, ?)",
                            (to, json.dumps({f: found.get(f) for f in kept.FIELDS}, ensure_ascii=False), who, note,
                             datetime.now(TZ).isoformat(timespec="seconds")))
        finally:
            con.close()
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                con.execute("INSERT INTO shares (user_id, email, sent_at) VALUES (?, ?, ?)",
                            (user_id, to, datetime.now(timezone.utc).isoformat(timespec="seconds")))
        finally:
            con.close()


def checked(work, location, quote):
    """The passage as the library has it (sources.cite). Raises ShareProblem if it isn't there."""
    found = sources.cite(work, location, quote)
    if not found:
        raise ShareProblem("That passage couldn't be checked against the library, so it can't be shared.")
    return found


def link(key, site_url):
    """The passage's own page: https://<domain>/p/<short id>."""
    return f"{emails.site(site_url)}p/{passages.short(key)}"


def preview(work, location, quote, site_url):
    """For the share pop-up: the passage's theme, its drawing (chosen once, then
    saved) and its link (for "Share another way")."""
    saved = passages.save(checked(work, location, quote))
    theme = saved and saved["theme"]
    return {"theme": theme, "drawing": passages.drawing(theme), "link": link(saved["key"], site_url)}


def email(found, who, name, note, invited, site_url, stop_url, vignette_url=None, page_url=None, member=False):
    """(subject, text, html) for a shared passage. "Read it in context" goes to
    the passage's page in the app (page_url), which links on to the source."""
    app, lead = config.APP_NAME, config.EDITION.get("lead", "")
    subject = f"{who} shared a passage with you"
    hello = f"Hi {name}," if name else "Hi,"
    opening = f"{who} shared this with you from {app}:"
    cite = where(found)
    link = page_url or passage_url(found["url"], found["quote"])
    lead_text = lead[:1].lower() + lead[1:] if lead else ""
    about = (f"It's also waiting in your Kept passages in {app}." if member else
             f"{who} keeps a journal with {app}, and so can you: open it any time at {emails.site(site_url)}"
             if invited else
             f"{who} keeps a journal with {app}, {lead_text.rstrip('.')}. It's invitation-only for now: "
             f"if you'd like to try it, ask {who} for an invitation.")
    text = "\n\n".join(p for p in (
        hello, opening, f"“{note}”\n— {who}" if note else None, f"“{found['quote']}”\n{cite}",
        f"Read it in context: {link}", about, f"Don't want passages shared with you? {stop_url}",
        f"Replies go to {who}.") if p)
    html = emails.page(site_url, subject=subject, preview=f"“{found['quote'][:90]}”", hello=hello,
                       opening=opening, message=note, who=f"— {who}" if note else "",
                       passage=found["quote"], passage_cite=cite, button_text="Read it in context",
                       button_url=link, sign_in=about, block_url=stop_url, reply_note=f"Replies go to {who}.",
                       vignette_url=vignette_url)
    return subject, text, html
