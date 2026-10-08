"""Quote sources: the curated map (config/sources.json) and quote checking.

Claude never writes links. It names a work from the map plus a location and
the words it wants to quote. We then look for those words in our local copy
of that exact public-domain translation (sources/texts/). If they're there,
we show the source's exact wording and link to the page where it was found.
If not, the reflection uses Claude's paraphrase instead, with no link.
"""
import json
import logging
import re
import threading
import unicodedata
import urllib.parse
from difflib import SequenceMatcher

from .config import ROOT

log = logging.getLogger(__name__)

SOURCES_PATH = ROOT / "config" / "sources.json"
TEXTS_DIR = ROOT / "sources" / "texts"

MIN_QUOTE_WORDS = 4
MATCH_THRESHOLD = 0.8   # share of the quote's words found, in order

_lock = threading.Lock()
_cache = {"map": None, "texts": {}}


# ---- The map ------------------------------------------------------------

def load_map():
    with _lock:
        if _cache["map"] is None:
            _cache["map"] = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
        return _cache["map"]


def works():
    return load_map()["works"]


def roman(n):
    out = ""
    for value, numeral in ((100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"),
                           (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:
            out += numeral
            n -= value
    return out


def roman_to_int(s):
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total = 0
    for i, ch in enumerate(s):
        v = values[ch]
        total += -v if i + 1 < len(s) and values[s[i + 1]] > v else v
    return total


NUMBER_WORDS = {
    w: i for i, ws in enumerate([
        (), ("one", "first"), ("two", "second"), ("three", "third"), ("four", "fourth"),
        ("five", "fifth"), ("six", "sixth"), ("seven", "seventh"), ("eight", "eighth"),
        ("nine", "ninth"), ("ten", "tenth"), ("eleven", "eleventh"), ("twelve", "twelfth"),
    ]) for w in ws
}


def key_part(value, aliases=None):
    """Turn one captured piece of a page title into a location piece.

    '7' -> '7', 'XXXIII' -> '33', 'Three' / 'Third' -> '3', aliases first.
    """
    value = (aliases or {}).get(value, value)
    if value.isdigit():
        return str(int(value))
    if re.fullmatch(r"[IVXLC]+", value):
        return str(roman_to_int(value))
    return str(NUMBER_WORDS.get(value.lower(), value))


def enumerate_pages(work):
    """Yield (page_key, wiki_title) for every page of a template or single-page work.

    page_key is the page's location prefix, e.g. "4" or "1.12" ("" if one page).
    Prefix-style works are listed from Wikisource by tools/build_sources.py.
    """
    pages = work["pages"]
    if "single" in pages:
        yield "", pages["single"]
        return
    if "template" not in pages:
        raise ValueError("prefix-style pages are listed by tools/build_sources.py")
    template = pages["template"]
    levels = pages["levels"]

    def fill(numbers):
        title = template
        for n in numbers:
            title = re.sub(r"\{(n|roman)\}", lambda m: str(n) if m.group(1) == "n" else roman(n), title, count=1)
        return title

    def walk(numbers):
        depth = len(numbers)
        if depth == len(levels):
            yield ".".join(str(n) for n in numbers), fill(numbers)
            return
        spec = levels[depth]
        count = spec if isinstance(spec, int) else spec[numbers[-1] - 1]
        for n in range(1, count + 1):
            yield from walk(numbers + [n])

    yield from walk([])


def page_url(title):
    return load_map()["site"] + urllib.parse.quote(title.replace(" ", "_"), safe="/;:,()'!")


def describe_for_prompt():
    """The list of quotable works, appended to the reflection system prompt."""
    lines = []
    for work_id, w in works().items():
        line = (f'- "{work_id}": {w["author"]}, {w["title"]} ({w["translation"]}). '
                f'Location format: {w["location_format"]}.')
        if w.get("note"):
            line += f" {w['note']}"
        lines.append(line)
    return "\n".join(lines)


# ---- Quote checking -----------------------------------------------------

def _norm(word):
    word = unicodedata.normalize("NFKD", word.lower())
    word = word.replace("’", "'").replace("‘", "'")
    return re.sub(r"[^a-z0-9']", "", word.encode("ascii", "ignore").decode()).strip("'")


def _load_text(work_id):
    with _lock:
        if work_id in _cache["texts"]:
            return _cache["texts"][work_id]
    path = TEXTS_DIR / f"{work_id}.json"
    if not path.exists():
        log.warning("No local text for %s; quotes from it will be shown as paraphrases. "
                    "Run tools/build_sources.py", work_id)
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    pages = []
    for page in data["pages"]:
        orig, norm, sections = [], [], []
        for para in page["paragraphs"]:
            for word in para["text"].split():
                n = _norm(word)
                if n:
                    orig.append(word)
                    norm.append(n)
                    sections.append(para.get("section"))
        pages.append({"key": page["key"], "title": page["title"],
                      "orig": orig, "norm": norm, "sections": sections, "vocab": set(norm)})
    with _lock:
        _cache["texts"][work_id] = pages
    return pages


def find_quote(work_id, quote):
    """Look for a quote in the local text. Returns a match dict or None."""
    q = [n for n in (_norm(w) for w in (quote or "").split()) if n]
    if len(q) < MIN_QUOTE_WORDS:
        return None
    pages = _load_text(work_id)
    if not pages:
        return None

    content = {w for w in q if len(w) > 3} or set(q)
    window = len(q) + max(3, len(q) // 4)
    step = max(1, len(q) // 4)
    best = None  # (score, page, start, end)

    for page in pages:
        # Skip pages that don't contain most of the quote's key words.
        if len(content & page["vocab"]) < 0.6 * len(content):
            continue
        norm = page["norm"]
        for start in range(0, max(1, len(norm) - len(q) + 1), step):
            chunk = norm[start:start + window]
            sm = SequenceMatcher(None, q, chunk, autojunk=False)
            blocks = [b for b in sm.get_matching_blocks() if b.size]
            matched = sum(b.size for b in blocks)
            score = matched / len(q)
            if blocks and (best is None or score > best[0]):
                best = (score, page, start + blocks[0].b, start + blocks[-1].b + blocks[-1].size)

    if not best or best[0] < MATCH_THRESHOLD:
        return None
    score, page, s, e = best
    text = " ".join(page["orig"][s:e]).strip(" ,;:—-")
    section = page["sections"][s]
    return {"page_key": page["key"], "title": page["title"], "section": section,
            "text": text, "score": round(score, 2)}


def _location_parts(location):
    """'Letter 13' -> ['13'];  'Book IV, 3' -> ['4', '3'];  'I-II, q. 2, a. 8' -> ['I-II', '2', '8'];
    'Third Essay, 12' -> ['3', '12'];  'Prologue.4' -> ['Prologue', '4']."""
    parts = []
    for token in re.split(r"[.,;:§\s]+", location or ""):
        if not token:
            continue
        if re.fullmatch(r"I+-I+", token):          # Summa parts: I-II, II-II
            parts.append(token)
        elif token.lower() == "prologue":
            parts.append("Prologue")
        elif token.isdigit() or re.fullmatch(r"[IVXLC]+", token) or token.lower() in NUMBER_WORDS:
            parts.append(key_part(token))
        # other words ("Letter", "Book", "q", "a", "Section"...) carry no position
    return parts


def reference(work_id, location, idea=""):
    """A link for an idea attributed to a work but not quoted.

    The location ("4.3", "Letter 13", "I-II.2.8"...) is matched against the
    work's real pages; the most specific page that exists wins. If none
    matches, the link goes to the work's main page instead of a guess.
    The location can't be checked against the words (there are none), so
    these are shown as "Read the source", not "Read the passage".
    """
    work = works().get(work_id)
    if not work:
        return None
    location = (location or "").strip()
    url, found_key = page_url(work["work_page"]), None
    pages = _load_text(work_id) or []
    by_key = {p["key"]: p["title"] for p in pages}
    if "" in by_key:                       # a one-page work
        url, found_key = page_url(by_key[""]), ""
    else:
        parts = _location_parts(location)
        for n in range(len(parts), 0, -1):
            key = ".".join(parts[:n])
            if key in by_key:
                url, found_key = page_url(by_key[key]), key
                break
    return {
        "work": work_id,
        "author": work["author"],
        "title": work["title"],
        "translation": work["translation"],
        # Only show a location the app could match to a real page.
        "location": location if found_key is not None and location else "",
        "url": url,
        "idea": (idea or "").strip()[:300],
    }


def cite(work_id, location, quote):
    """Resolve one quote Claude proposed.

    Returns a citation dict (verified, with link) or None (use the paraphrase).
    """
    work = works().get(work_id)
    if not work:
        return None
    match = find_quote(work_id, quote)
    if not match:
        log.info("Quote not found in %s (%s); using paraphrase", work_id, location)
        return None

    # Location: what we found beats what Claude said.
    key, section = match["page_key"], match["section"]
    claimed = (location or "").strip()
    if section and work.get("location_from_section"):
        found_location = section
    elif section:
        found_location = ".".join(p for p in (key, section) if p)
    elif not key:
        found_location = claimed  # one-page work: nothing finer to check against
    elif claimed == key or re.match(re.escape(key) + r"(?!\d)", claimed):
        found_location = claimed  # same page; keep Claude's finer detail
    else:
        found_location = key

    return {
        "work": work_id,
        "author": work["author"],
        "title": work["title"],
        "translation": work["translation"],
        "location": found_location,
        "quote": match["text"],
        "url": page_url(match["title"]),
    }
