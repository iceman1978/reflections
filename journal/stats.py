"""Entry count and streaks, always computed from the entries' dates.

Nothing is stored, so the numbers stay right after edits, deletes or restores.
A day counts once however many entries it has. The current streak stays alive
through today: if you wrote yesterday but not yet today, it still shows
yesterday's count, and resets to 0 only after a whole day is missed.
Hidden entries count; permanently deleted ones don't.
"""
from datetime import datetime, timedelta

from . import store
from .config import TZ


def local_date(iso):
    return datetime.fromisoformat(iso).astimezone(TZ).date()


def compute(dates, today):
    """dates: iterable of datetime.date (duplicates fine). Returns streak numbers."""
    days = set(dates)
    one = timedelta(days=1)

    if today in days:
        anchor = today
    elif today - one in days:
        anchor = today - one
    else:
        anchor = None

    current = 0
    while anchor is not None and anchor in days:
        current += 1
        anchor -= one

    longest = run = 0
    previous = None
    for d in sorted(days):
        run = run + 1 if previous is not None and d - previous == one else 1
        longest = max(longest, run)
        previous = d

    return {
        "current_streak": current,
        "longest_streak": longest,
        "wrote_today": today in days,
        "days_written": len(days),
    }


def journal_stats():
    entries = store.all_entries()  # includes hidden entries
    today = datetime.now(TZ).date()
    result = compute((local_date(e["created_at"]) for e in entries), today)
    result["total_entries"] = len(entries)
    result["today"] = today.isoformat()
    return result
