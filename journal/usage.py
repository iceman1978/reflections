"""Daily limits per person (on the hosted version), so one API key can be
shared without surprises: e.g. 3 reflections and 10 writing prompts a day.

Only successful requests count, so a reflection that fails (no internet,
Claude busy) can simply be retried. Days follow the journal's time zone.
"""
from datetime import datetime

from . import db
from .config import PROMPTS_PER_DAY, REFLECTIONS_PER_DAY, TZ

LIMITS = {"reflection": REFLECTIONS_PER_DAY, "prompt": PROMPTS_PER_DAY}   # 0 = no limit


class LimitReached(Exception):
    pass


def _today():
    return datetime.now(TZ).date().isoformat()


def used(user_id, kind):
    con = db.connect()
    try:
        row = con.execute("SELECT count FROM usage WHERE user_id = ? AND day = ? AND kind = ?",
                          (user_id, _today(), kind)).fetchone()
    finally:
        con.close()
    return row[0] if row else 0


def remaining(user_id, kind):
    """How many are left today, or None if there's no limit."""
    limit = LIMITS.get(kind) or 0
    return None if not limit else max(0, limit - used(user_id, kind))


def check(user_id, kind):
    """Raise LimitReached (with a friendly message) if today's allowance is used up."""
    left = remaining(user_id, kind)
    if left == 0:
        if kind == "reflection":
            raise LimitReached(f"You've had today's {LIMITS[kind]} reflections. Your entry is saved; "
                               "you can ask for its reflection tomorrow.")
        raise LimitReached(f"You've had today's {LIMITS[kind]} writing prompts. More tomorrow.")


def record(user_id, kind):
    """Count one successful use (always: the limits use it, and so do the usage stats)."""
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                con.execute("INSERT INTO usage (user_id, day, kind, count) VALUES (?, ?, ?, 1) "
                            "ON CONFLICT (user_id, day, kind) DO UPDATE SET count = count + 1",
                            (user_id, _today(), kind))
        finally:
            con.close()
