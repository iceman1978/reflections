"""Passages: every passage kept or shared in the app, saved once, with its theme,
its own short link (/p/<id>, a page that links on to the source), and counts:
how many times it's been kept (starred), and shared by email or another way.
Groundwork for a future feature around the passages people find most meaningful.

Only public texts are kept here (a passage from the library, word for word,
and where it's from) and anonymous counts: never who kept or shared it, who
it went to, their note, or anything anyone wrote. Each person's own kept
passages stay private and encrypted (journal/kept.py).

Each passage's theme (one from the edition's list) is chosen once, by a small
Claude model reading just the passage, and picks the drawing in the email.
When nothing on the list fits well, the model names the theme that's missing
(`missing_theme`), and a pick that failed keeps why (`theme_problem`): Admin →
"Passages that need a theme" lists both, to grow the themes and drawings.
A passage without a theme is tried again the next time it's shared.
"""
import json
import logging
import re
from datetime import datetime, timezone

from . import db, kept, themes, tokens
from .config import ROOT

log = logging.getLogger(__name__)

THEME_MODEL = themes.TAGGING_MODEL
COLUMNS = ("key", "work", "location", "quote", "url", "theme", "created_at", "shares", "last_shared_at",
           "missing_theme", "theme_problem", "keeps", "other_shares", "title", "author", "translation")
SHORT = 10                   # characters of the key in a passage's link: /p/<first 10>


def slug(theme):
    """"Grief & loss" -> "grief-and-loss" (the drawings' file names: tools/make_vignettes.py)."""
    s = re.sub(r"['’]", "", (theme or "").lower()).replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def drawing(theme, folder="vignettes", ext="png"):
    """The theme's drawing under static/<folder>/, as a path from static/, or None."""
    name = f"{folder}/{slug(theme)}.{ext}"
    return name if theme and (ROOT / "static" / name).exists() else None


NO_FIT = "(none fits)"


def _theme_of(citation):
    """(theme, missing_theme, problem) for a passage: the edition theme that fits it
    best (None if none fits well, or the pick failed); the theme the list is
    missing, if the model would name a better one; and why a pick failed."""
    from . import reflection          # here, to avoid a circular import
    if not themes.THEMES:
        return None, None, "This edition has no themes."
    where = " ".join(p for p in (citation.get("author"), citation.get("title"), citation.get("location")) if p)
    try:
        response = reflection._client().messages.create(
            model=THEME_MODEL, max_tokens=300,
            system=("You choose the one theme, from this list, that a passage of Scripture or classic writing "
                    "speaks to most directly:\n" + "\n".join(themes.THEMES)
                    + f"\n\nIf none of them fits it well, answer \"{NO_FIT}\". Either way, if a theme that isn't "
                      "on the list would fit clearly better, name it in missing_theme (two or three words, in the "
                      "list's style); otherwise leave missing_theme empty."),
            messages=[{"role": "user", "content": f"{where}\n\n“{citation['quote']}”"}],
            output_config={"format": {"type": "json_schema", "schema": {
                "type": "object",
                "properties": {"theme": {"type": "string", "enum": [*themes.THEMES, NO_FIT]},
                               "missing_theme": {"type": "string"}},
                "required": ["theme", "missing_theme"], "additionalProperties": False}}},
        )
        tokens.record("passage", response)
        data = json.loads(next(b.text for b in response.content if b.type == "text"))
    except Exception as exc:
        log.warning("Couldn't choose a theme for a passage: %r", exc)
        return None, None, (f"{type(exc).__name__}: {exc}")[:300]
    theme = data.get("theme") if data.get("theme") in themes.THEMES else None
    missing = " ".join((data.get("missing_theme") or "").split())[:60] or None
    if missing and missing.lower() in {t.lower() for t in themes.THEMES}:
        missing = None                    # that one's on the list already
    return theme, missing, None


def _add(con, citation):
    key = kept.key_of(citation)
    con.execute("INSERT OR IGNORE INTO passages (key, work, location, quote, url, created_at, title, author, translation) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (key, citation["work"], citation.get("location"), citation["quote"], citation.get("url"),
                 datetime.now(timezone.utc).isoformat(timespec="seconds"), citation.get("title"),
                 citation.get("author"), citation.get("translation")))
    return key


def save(citation):
    """The saved passage for a checked citation (sources.cite), saving it and
    choosing its theme the first time. Returns a dict (COLUMNS)."""
    key = kept.key_of(citation)
    found = get(key)
    if found and found["theme"]:
        return found
    theme, missing, problem = _theme_of(citation)
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                _add(con, citation)
                con.execute("UPDATE passages SET theme = ?, missing_theme = ?, theme_problem = ? WHERE key = ?",
                            (theme, missing, problem, key))
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


def shared(key, by_email=True):
    """Count one share: by email, or another way (the phone's share sheet)."""
    column = "shares" if by_email else "other_shares"
    with db.write_lock:
        con = db.connect()
        try:
            with con:
                con.execute(f"UPDATE passages SET {column} = {column} + 1, last_shared_at = ? WHERE key = ?",
                            (datetime.now(timezone.utc).isoformat(timespec="seconds"), key))
        finally:
            con.close()


def kept_count(citation, change):
    """Someone kept (+1) or let go of (-1) a passage: an anonymous count. Never raises."""
    try:
        with db.write_lock:
            con = db.connect()
            try:
                with con:
                    key = _add(con, citation) if change > 0 else kept.key_of(citation)
                    con.execute("UPDATE passages SET keeps = MAX(0, keeps + ?) WHERE key = ?", (change, key))
            finally:
                con.close()
    except Exception:
        log.exception("Couldn't count a kept passage")


def released(key):
    """A passage let go of, known only by its key (kept passages are stored by key)."""
    try:
        with db.write_lock:
            con = db.connect()
            try:
                with con:
                    con.execute("UPDATE passages SET keeps = MAX(0, keeps - 1) WHERE key = ?", (key,))
            finally:
                con.close()
    except Exception:
        log.exception("Couldn't count a released passage")


def keep_counts(keys):
    """How many times everyone has kept each of these passages (by key)."""
    keys = list(keys)
    if not keys:
        return {}
    con = db.connect()
    try:
        return dict(con.execute(f"SELECT key, keeps FROM passages WHERE key IN ({','.join('?' * len(keys))})",
                                keys).fetchall())
    finally:
        con.close()


def short(key):
    return key[:SHORT]


def by_short(code):
    """The passage for a link's short code, or None."""
    if not re.fullmatch(r"[0-9a-f]{%d}" % SHORT, code or ""):
        return None
    con = db.connect()
    try:
        row = con.execute(f"SELECT {', '.join(COLUMNS)} FROM passages WHERE key LIKE ?", (code + "%",)).fetchone()
    finally:
        con.close()
    return dict(zip(COLUMNS, row)) if row else None


def needing_themes():
    """Passages with no theme, or whose theme could be better: for growing the themes and drawings."""
    con = db.connect()
    try:
        rows = con.execute(f"SELECT {', '.join(COLUMNS)} FROM passages WHERE theme IS NULL "
                           "OR missing_theme IS NOT NULL ORDER BY created_at DESC").fetchall()
    finally:
        con.close()
    return [dict(zip(COLUMNS, r)) for r in rows]


def most_shared(limit=20):
    """The most shared passages, most first (for a future community page)."""
    con = db.connect()
    try:
        rows = con.execute(f"SELECT {', '.join(COLUMNS)} FROM passages WHERE shares + other_shares > 0 "
                           "ORDER BY shares + other_shares DESC, last_shared_at DESC LIMIT ?", (limit,)).fetchall()
    finally:
        con.close()
    return [dict(zip(COLUMNS, r)) for r in rows]
