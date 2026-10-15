"""Passages: every passage shared from the app, saved once, with its theme and
how often it's been shared. Groundwork for a future feature around the
passages people find most meaningful.

Only public texts are kept here (a passage from the library, word for word,
and where it's from): never who shared it, who it went to, their note, or
anything anyone wrote. Kept passages (the stars) stay private and encrypted,
apart from this.

Each passage's theme (one from the edition's list) is chosen once, by a small
Claude model reading just the passage, and picks the drawing in the email.
"""
import json
import logging
import re
from datetime import datetime, timezone

from . import db, kept, themes, tokens
from .config import ROOT

log = logging.getLogger(__name__)

THEME_MODEL = themes.TAGGING_MODEL
COLUMNS = ("key", "work", "location", "quote", "url", "theme", "created_at", "shares", "last_shared_at")


def slug(theme):
    """"Grief & loss" -> "grief-and-loss" (the drawings' file names: tools/make_vignettes.py)."""
    s = re.sub(r"['’]", "", (theme or "").lower()).replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def drawing(theme, folder="vignettes"):
    """The theme's drawing under static/<folder>/, as a path from static/, or None."""
    name = f"{folder}/{slug(theme)}.png"
    return name if theme and (ROOT / "static" / name).exists() else None


def _theme_of(citation):
    """The edition theme that best fits a passage, or None if it can't be chosen."""
    from . import reflection          # here, to avoid a circular import
    if not themes.THEMES:
        return None
    where = " ".join(p for p in (citation.get("author"), citation.get("title"), citation.get("location")) if p)
    try:
        response = reflection._client().messages.create(
            model=THEME_MODEL, max_tokens=200,
            system=("You choose the one theme, from this list, that a passage of Scripture or classic writing "
                    "speaks to most directly:\n" + "\n".join(themes.THEMES)),
            messages=[{"role": "user", "content": f"{where}\n\n“{citation['quote']}”"}],
            output_config={"format": {"type": "json_schema", "schema": {
                "type": "object", "properties": {"theme": {"type": "string", "enum": themes.THEMES}},
                "required": ["theme"], "additionalProperties": False}}},
        )
        tokens.record("passage", response)
        theme = json.loads(next(b.text for b in response.content if b.type == "text"))["theme"]
    except Exception as exc:
        log.warning("Couldn't choose a theme for a passage: %r", exc)
        return None
    return theme if theme in themes.THEMES else None


def save(citation):
    """The saved passage for a checked citation (sources.cite), saving it and
    choosing its theme the first time. Returns a dict (COLUMNS)."""
    key = kept.key_of(citation)
    found = get(key)
    if found and found["theme"]:
        return found
    theme = _theme_of(citation)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                con.execute("INSERT OR IGNORE INTO passages (key, work, location, quote, url, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (key, citation["work"], citation.get("location"), citation["quote"],
                             citation.get("url"), now))
                if theme:
                    con.execute("UPDATE passages SET theme = ? WHERE key = ?", (theme, key))
        finally:
            con.close()
    return get(key)


def get(key):
    con = db.connect()
    try:
        row = con.execute(f"SELECT {', '.join(COLUMNS)} FROM passages WHERE key = ?", (key,)).fetchone()
    finally:
        con.close()
    return dict(zip(COLUMNS, row)) if row else None


def shared(key):
    """Count one share."""
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                con.execute("UPDATE passages SET shares = shares + 1, last_shared_at = ? WHERE key = ?",
                            (datetime.now(timezone.utc).isoformat(timespec="seconds"), key))
        finally:
            con.close()


def most_shared(limit=20):
    """The most shared passages, most first (for a future community page)."""
    con = db.connect()
    try:
        rows = con.execute(f"SELECT {', '.join(COLUMNS)} FROM passages WHERE shares > 0 "
                           "ORDER BY shares DESC, last_shared_at DESC LIMIT ?", (limit,)).fetchall()
    finally:
        con.close()
    return [dict(zip(COLUMNS, r)) for r in rows]
