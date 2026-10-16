"""Whether a reflection spoke to someone: "Yes" or "Not quite", with optional
reasons. The rating itself lives in the entry, encrypted with the rest of it.

For Admin, an anonymous copy is counted here: the day, the rating, the tone the
reflection was written in, the writers it quoted and the reasons. Nothing anyone
wrote, and no link to who: each row is keyed by a one-way hash of the entry (with
a random salt kept in the database), only so that changing a rating replaces the
earlier one instead of counting twice.
"""
import hashlib
import hmac
import json
import secrets
from collections import Counter
from datetime import date, timedelta

from . import db

RATINGS = ("yes", "no")
# The "Not quite" reasons, in the order they're offered.
REASONS = {
    "missed": "Missed the point",
    "long": "Too long",
    "tone": "Wrong tone",
    "quote": "A quote didn't fit",
    "preachy": "Too preachy",
    "gentle": "Too gentle",
}


def clean(rating, reasons):
    """(rating, reasons) as stored: rating "yes" / "no" / None; reasons only with "no"."""
    rating = rating if rating in RATINGS else None
    reasons = [r for r in REASONS if r in (reasons or [])] if rating == "no" else []
    return rating, reasons


def writers_of(entry):
    """The writers a reflection drew on, as named in its citations."""
    names = entry.get("thinkers") or [c.get("author") for c in (entry.get("quotes") or []) + (entry.get("references") or [])]
    return sorted({n for n in names if n})


def _salt():
    salt = db.get_meta("rating_salt")
    if not salt:
        salt = secrets.token_hex(32)
        db.set_meta("rating_salt", salt)
    return salt


def _key(user_id, entry_id):
    return hmac.new(_salt().encode(), f"{user_id}|{entry_id}".encode(), hashlib.sha256).hexdigest()


def record(user_id, entry, rating, reasons):
    """Count (or, with no rating, forget) the anonymous copy of this rating."""
    key = _key(user_id, entry["id"])
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                if rating is None:
                    con.execute("DELETE FROM reflection_ratings WHERE key = ?", (key,))
                else:
                    con.execute("INSERT OR REPLACE INTO reflection_ratings (key, day, rating, tone, writers, reasons) "
                                "VALUES (?, ?, ?, ?, ?, ?)",
                                (key, date.today().isoformat(), rating, entry.get("reflection_tone"),
                                 json.dumps(writers_of(entry)), json.dumps(reasons)))
        finally:
            con.close()


def summary(days=30):
    """Admin's counts: how many reflections spoke to people, overall, by tone and
    by writer, and how often each reason was given, over the last `days` days."""
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    con = db.connect()
    try:
        rows = con.execute("SELECT rating, tone, writers, reasons FROM reflection_ratings WHERE day >= ?", (since,)).fetchall()
    finally:
        con.close()

    def share(group):
        n = len(group)
        yes = sum(1 for r in group if r == "yes")
        return {"rated": n, "yes": yes, "percent": round(100 * yes / n) if n else None}

    by_tone, by_writer, reasons = {}, {}, Counter()
    for rating, tone, writers, why in rows:
        by_tone.setdefault(tone or "unknown", []).append(rating)
        for w in json.loads(writers or "[]"):
            by_writer.setdefault(w, []).append(rating)
        reasons.update(json.loads(why or "[]"))
    writers = sorted(({"writer": w, **share(g)} for w, g in by_writer.items()), key=lambda x: (-x["rated"], x["writer"]))
    return {
        **share([r[0] for r in rows]),
        "by_tone": [{"tone": t, **share(by_tone[t])} for t in ("encourage", "reflect", "challenge", "unknown") if t in by_tone],
        "by_writer": writers[:12],
        "reasons": [{"reason": REASONS[k], "count": reasons[k]} for k in REASONS if reasons[k]],
    }
