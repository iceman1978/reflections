"""Feedback from the people using the app: a fix or improvement to what's
there, or an idea for a new feature.

Unlike entries, feedback is NOT encrypted: it's written to be read by whoever
runs the app (Settings → Feedback received, for the people in ADMIN_EMAILS).
The form says so, so nobody puts anything private in it.
"""
import csv
import io
from datetime import datetime

from . import db
from .config import TZ

KINDS = {"fix": "Fix or improvement", "feature": "New feature"}
MAX_CHARS = 5000
PER_DAY = 20   # per person, to keep a runaway script from filling the disk
COLUMNS = ("id", "user_id", "email", "name", "created_at", "kind", "text", "screen", "device", "done")


class FeedbackProblem(Exception):
    """Something about the submission, safe to show the person."""


def add(user_id, email, name, kind, text, screen=None, device=None):
    """Save one piece of feedback. Returns its ID. Raises FeedbackProblem."""
    text = (text or "").strip()
    if kind not in KINDS:
        raise FeedbackProblem("Choose whether this is a fix or a new feature.")
    if not text:
        raise FeedbackProblem("Write a few words first.")
    if len(text) > MAX_CHARS:
        raise FeedbackProblem(f"That's a bit long: please keep it under {MAX_CHARS:,} characters.")
    now = datetime.now(TZ)
    with db.write_lock:
        con = db.connect()
        try:
            sent_today = con.execute("SELECT COUNT(*) FROM feedback WHERE user_id = ? AND created_at >= ?",
                                     (user_id, now.date().isoformat())).fetchone()[0]
            if sent_today >= PER_DAY:
                raise FeedbackProblem("Thanks for all the feedback today! Please send more tomorrow.")
            with con:
                cur = con.execute(
                    "INSERT INTO feedback (user_id, email, name, created_at, kind, text, screen, device) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (user_id, email, name, now.isoformat(timespec="seconds"), kind, text,
                     (screen or "")[:40] or None, (device or "")[:300] or None))
            return cur.lastrowid
        finally:
            con.close()


def count_for(user_id):
    """How much feedback this person has sent (for the "send feedback?" suggestion)."""
    con = db.connect()
    try:
        return con.execute("SELECT COUNT(*) FROM feedback WHERE user_id = ?", (user_id,)).fetchone()[0]
    finally:
        con.close()


def all_feedback():
    """Everything received, newest first."""
    con = db.connect()
    try:
        rows = con.execute(f"SELECT {', '.join(COLUMNS)} FROM feedback ORDER BY id DESC").fetchall()
    finally:
        con.close()
    return [dict(zip(COLUMNS, r), done=bool(r[-1]), kind_label=KINDS.get(r[5], r[5])) for r in rows]


def set_done(feedback_id, done):
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                changed = con.execute("UPDATE feedback SET done = ? WHERE id = ?",
                                      (1 if done else 0, feedback_id)).rowcount
        finally:
            con.close()
    if not changed:
        raise KeyError(feedback_id)


def as_csv():
    """All feedback as a CSV that opens cleanly in Excel."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(["Date", "Time", "From", "Type", "Feedback", "Screen", "Device", "Done"])
    for f in all_feedback():
        when = datetime.fromisoformat(f["created_at"])
        who = f["email"] or f["name"] or f["user_id"]
        w.writerow([when.strftime("%Y-%m-%d"), when.strftime("%H:%M"), who, f["kind_label"], f["text"],
                    f["screen"] or "", f["device"] or "", "Yes" if f["done"] else ""])
    return "﻿" + out.getvalue()
