"""Usage statistics for whoever runs the app (Settings → Usage, admins only).

Recorded per person per day: that they used the app, and how many entries
they wrote. Reflections and prompts come from the usage table (daily limits).
Nothing about what anyone writes is recorded; entries stay encrypted.

Days follow the journal's time zone.
"""
import threading
from datetime import date, datetime, timedelta

from . import db
from .config import TZ

_seen = set()               # (user_id, day) already recorded by this process
_seen_lock = threading.Lock()


def _today():
    return datetime.now(TZ).date()


def seen(user_id):
    """Mark today as a day this person used the app (one database write per day)."""
    day = _today().isoformat()
    with _seen_lock:
        if (user_id, day) in _seen:
            return
        _seen.add((user_id, day))
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                con.execute("INSERT OR IGNORE INTO activity (user_id, day) VALUES (?, ?)", (user_id, day))
        finally:
            con.close()


def entry_written(user_id):
    seen(user_id)
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                con.execute("UPDATE activity SET entries = entries + 1 WHERE user_id = ? AND day = ?",
                            (user_id, _today().isoformat()))
        finally:
            con.close()


def summary(days=30):
    """Everything the Usage section shows."""
    today = _today()
    since = (today - timedelta(days=days - 1)).isoformat()
    week = (today - timedelta(days=6)).isoformat()
    con = db.connect()
    try:
        people = con.execute("SELECT id, email, name, created_at, key_password IS NOT NULL, prefs FROM users").fetchall()
        entries_total = dict(con.execute("SELECT user_id, COUNT(*) FROM entries GROUP BY user_id").fetchall())
        span = {u: (first, last) for u, first, last in
                con.execute("SELECT user_id, MIN(day), MAX(day) FROM activity GROUP BY user_id")}
        recent = {u: (n, e) for u, n, e in con.execute(
            "SELECT user_id, COUNT(*), SUM(entries) FROM activity WHERE day >= ? GROUP BY user_id", (since,))}
        reflections = dict(con.execute(
            "SELECT user_id, SUM(count) FROM usage WHERE kind = 'reflection' AND day >= ? GROUP BY user_id", (since,)))
        daily = dict(con.execute(
            "SELECT day, COUNT(DISTINCT user_id) FROM activity WHERE day >= ? GROUP BY day", (since,)))
        active_week = con.execute("SELECT COUNT(DISTINCT user_id) FROM activity WHERE day >= ?", (week,)).fetchone()[0]
        tracking_since = con.execute("SELECT MIN(day) FROM activity").fetchone()[0]
    finally:
        con.close()

    rows = []
    for uid, email, name, created, has_passphrase, _prefs in people:
        first, last = span.get(uid, (None, None))
        active_days, entries_recent = recent.get(uid, (0, 0))
        rows.append({
            "who": email or name or uid,
            "joined": (created or "")[:10],
            "set_up": bool(has_passphrase),
            "first_seen": first,
            "last_seen": last,
            "days_since": (today - date.fromisoformat(last)).days if last else None,
            "active_days": active_days,
            "entries_recent": entries_recent or 0,
            "entries_total": entries_total.get(uid, 0),
            "reflections_recent": reflections.get(uid, 0) or 0,
        })
    rows.sort(key=lambda r: (r["last_seen"] or "", r["active_days"]), reverse=True)

    active = [r for r in rows if r["active_days"]]
    series = [{"day": (today - timedelta(days=i)).isoformat(),
               "users": daily.get((today - timedelta(days=i)).isoformat(), 0)} for i in range(days - 1, -1, -1)]
    return {
        "days": days,
        "today": today.isoformat(),
        "tracking_since": tracking_since,
        "people": len(rows),
        "set_up": sum(r["set_up"] for r in rows),
        "active_today": daily.get(today.isoformat(), 0),
        "active_week": active_week,
        "active_month": len(active),
        "returning": sum(1 for r in active if r["active_days"] >= 2),
        "avg_active_days": round(sum(r["active_days"] for r in active) / len(active), 1) if active else 0,
        "entries_recent": sum(r["entries_recent"] for r in rows),
        "reflections_recent": sum(r["reflections_recent"] for r in rows),
        "series": series,
        "rows": rows,
    }
