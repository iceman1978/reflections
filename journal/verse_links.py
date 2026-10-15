""""Read the passage" in the reader's own translation (Settings → Basic), opened
at the chapter with the verse highlighted, like the BSB links.

A highlight has to match the translation's own words, and quotes are BSB, so
/read looks the verse up on BibleHub once (its page for a verse in a
translation has the verse as its title), keeps only the few words a highlight
needs (the first and last four), and sends the reader to the chapter with
them. If the lookup fails, the chapter opens without a highlight.
"""
import html
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote as enc

import requests

from . import db

log = logging.getLogger(__name__)

TRANSLATIONS = ("niv", "esv", "nlt", "kjv", "nkjv", "nasb")
TIMEOUT = 6


def fragment(text):
    """The highlight for some words: the first and last four (all of them, if eight or fewer),
    starting from the second word: on BibleHub's chapter pages a verse's number runs into
    its first word ("14for when..."), and a highlight must start at the start of a word."""
    words = [w for w in (text or "").split() if w]
    if len(words) > 5:            # the verse number runs into the first word ("14for"), which can't be matched
        words = words[1:]
    if not words:
        return ""
    words[0] = re.sub(r"^[^\w]+", "", words[0])
    words[-1] = re.sub(r"[^\w]+$", "", words[-1])
    part = lambda ws: enc(" ".join(w for w in ws if w), safe="").replace("-", "%2D")   # noqa: E731
    return part(words) if len(words) <= 8 else f"{part(words[:4])},{part(words[-4:])}"


def _look_up(t, book, chapter, verse):
    """The verse's words in that translation, from BibleHub's page for it, or None."""
    try:
        r = requests.get(f"https://biblehub.com/{t}/{book}/{chapter}-{verse}.htm", timeout=TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0 (journal; verse links)"})
        title = re.search(r"<title>[^<:]*\d+:\d+ [A-Z]+: (.*?)</title>", r.text, re.S)
    except requests.RequestException:
        title = None
    if not title:
        log.info("Couldn't look up %s %s %s:%s", t, book, chapter, verse)
        return None
    return html.unescape(title.group(1)).strip().strip("“”\"")


def url(t, book, chapter, verse):
    """The chapter in translation t, with the verse highlighted when its words can be found."""
    chapter_url = f"https://biblehub.com/{t}/{book}/{chapter}.htm"
    con = db.connect()
    try:
        row = con.execute("SELECT fragment FROM verse_highlights WHERE t = ? AND book = ? AND chapter = ? AND verse = ?",
                          (t, book, chapter, verse)).fetchone()
    finally:
        con.close()
    if row:
        found = row[0]
    else:
        words = _look_up(t, book, chapter, verse)
        found = fragment(words) if words else ""
        if words:                                        # keep only the highlight, not the verse
            with db.write_lock:
                con = db.connect()
                try:
                    with con:
                        con.execute("INSERT OR REPLACE INTO verse_highlights (t, book, chapter, verse, fragment, found_at) "
                                    "VALUES (?, ?, ?, ?, ?, ?)", (t, book, chapter, verse, found,
                                                                  datetime.now(timezone.utc).isoformat(timespec="seconds")))
                finally:
                    con.close()
    return f"{chapter_url}#:~:text={found}" if found else chapter_url
