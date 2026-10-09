"""Quote sources: the curated map (editions/<edition>/sources.json) and quote checking.

Claude never writes links. It names a work from the map plus a location and
the words it wants to quote. We then look for those words in our local copy
of that exact public-domain translation (sources/texts/). If they're there,
we show the source's exact wording and link to the page where it was found.
If not, the reflection uses Claude's paraphrase instead, with no link.
"""
import json
import logging
import random
import re
import threading
import unicodedata
import urllib.parse
from difflib import SequenceMatcher

from .config import EDITION_DIR, ROOT

log = logging.getLogger(__name__)

SOURCES_PATH = EDITION_DIR / "sources.json"   # this edition's library
TEXTS_DIR = ROOT / "sources" / "texts"

MIN_QUOTE_WORDS = 4
MATCH_THRESHOLD = 0.8   # share of the quote's words found, in order
TRIMMED_THRESHOLD = 0.7   # after dropping stray end words: the source's own words are shown, so a little drift is fine

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
    """A Wikisource page title becomes its link; works from elsewhere (CCEL,
    Project Gutenberg, the Bible) store the full link instead of a title."""
    if title.startswith("https://"):
        return title
    return load_map()["site"] + urllib.parse.quote(title.replace(" ", "_"), safe="/;:,()'!")


def _cite_title(work):
    return work.get("cite_title") or work["title"]


# ---- Matching a location to a labelled page (CCEL and Gutenberg books) ---------

# Words that name the kind of division, not which one: dropped from a location
# like "Chapter 4" so it can match a page labelled "IV. The Fight".
GENERIC_WORDS = {"chapter", "chap", "ch", "section", "sect", "no", "page", "p", "the", "of", "and", "a", "an"}
DIVISION_WORDS = {"part", "book", "chapter", "section", "letter", "lesson", "stage", "conversation", "sermon", "day"}


def label_tokens(text, drop_generic=False):
    """'Third Letter' -> ['3', 'letter'];  'Book ONE. Thoughts...' -> ['book', '1', 'thought', ...];
    'IV. The Fight' -> ['4', 'fight'] (with drop_generic).  Plurals are made singular,
    so 'Letters' matches 'Letter'."""
    raw = re.findall(r"[A-Za-z]+\.?|\d+", text or "")
    out = []
    for i, tok in enumerate(raw):
        word = tok.rstrip(".")
        prev = out[-1] if out else None
        if word.isdigit():
            out.append(str(int(word)))
        elif re.fullmatch(r"[IVXLC]+", word) and (word != "I" or tok.endswith(".") or i == 0 or prev in DIVISION_WORDS):
            out.append(str(roman_to_int(word)))
        elif word.lower() in NUMBER_WORDS:
            out.append(str(NUMBER_WORDS[word.lower()]))
        else:
            w = word.lower()
            if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
                w = w[:-1]
            out.append(w)
    if drop_generic:
        out = [w for w in out if w not in GENERIC_WORDS]
    return out


def _is_subsequence(needle, haystack):
    it = iter(haystack)
    return all(any(h == n for h in it) for n in needle)


def match_label(location, pages):
    """The labelled page a location like "Letter 3" or "Book 1, Chapter 3" points
    to, or None. The location's words and numbers must appear, in order, in the
    page's label (its headings from the top down); the most specific page wins."""
    want = label_tokens(location, drop_generic=True)
    if not want:
        return None
    best = None
    for page in pages:
        have = label_tokens(page.get("label") or "", drop_generic=True)
        if _is_subsequence(want, have) and (best is None or len(have) < best[0]):
            best = (len(have), page)
    return best[1] if best else None


def describe_for_prompt():
    """The list of quotable works, appended to the reflection system prompt.
    Shuffled each time, so no thinker gets an edge just from coming first."""
    lines = []
    for work_id, w in random.sample(list(works().items()), len(works())):
        by = f'{w["author"]}, ' if w["author"] else ""   # books of the Bible have no author line
        line = (f'- "{work_id}": {by}{w["title"]} ({w["translation"]}). '
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
                      "label": page.get("label"), "location": page.get("location"),
                      "orig": orig, "norm": norm, "sections": sections, "vocab": set(norm)})
    with _lock:
        _cache["texts"][work_id] = pages
    return pages


def _trim_strays(blocks, sections, start):
    """Drop a word or two matched at either end of a quote, apart from the rest.

    If Claude's wording drifts at the end ("...faith that God has assigned to
    each"), its last word can match a word further on ("Just as each of us",
    in the next verse), stretching the quote over text it never meant. A short
    match separated by a gap of unmatched words (or by a verse or section
    boundary) is a stray, not part of the quote."""
    def stray(edge, neighbour, after):
        if edge.size > 2:
            return False
        gap_from, gap_to = ((neighbour.b + neighbour.size, edge.b) if after
                            else (edge.b + edge.size, neighbour.b))
        gap = gap_to - gap_from
        crosses = sections[start + edge.b] != sections[start + neighbour.b]
        return gap >= 3 or (gap >= 1 and crosses)

    blocks = list(blocks)
    while len(blocks) > 1 and stray(blocks[-1], blocks[-2], after=True):
        blocks.pop()
    while len(blocks) > 1 and stray(blocks[0], blocks[1], after=False):
        blocks.pop(0)
    return blocks


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
                best = (score, page, start, blocks)

    if not best or best[0] < MATCH_THRESHOLD:
        return None
    score, page, start, blocks = best
    blocks = _trim_strays(blocks, page["sections"], start)
    score = sum(b.size for b in blocks) / len(q)
    if score < TRIMMED_THRESHOLD:
        return None
    s, e = start + blocks[0].b, start + blocks[-1].b + blocks[-1].size
    # Don't stop a word or two short of the end of the sentence ("...faith God has").
    ends_sentence = re.compile(r"[.!?;:][\"”’)]*$")
    if not ends_sentence.search(page["orig"][e - 1]):
        for extra in range(1, 5):
            i = e - 1 + extra
            if i >= len(page["orig"]) or page["sections"][i] != page["sections"][e - 1]:
                break
            if ends_sentence.search(page["orig"][i]):
                e = i + 1
                break
    text = " ".join(page["orig"][s:e]).strip(' ,;:—-“”"')   # the reflection adds its own quotation marks
    section = page["sections"][s]
    return {"page_key": page["key"], "title": page["title"], "section": section,
            "end_section": page["sections"][e - 1],
            "page_location": page["location"], "text": text, "score": round(score, 2)}


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

    The location ("4.3", "Letter 13", "I-II.2.8", "8:28"...) is matched against the
    work's real pages; the most specific page that exists wins. If none
    matches, the link goes to the work's main page instead of a guess.
    The location can't be checked against the words (there are none), so
    these are shown as "Read the source", not "Read the passage".
    """
    work = works().get(work_id)
    if not work:
        return None
    location = (location or "").strip()
    url, found = page_url(work["work_page"]), False
    pages = _load_text(work_id) or []
    by_key = {p["key"]: p["title"] for p in pages}
    if "" in by_key:                       # a one-page work
        url, found = page_url(by_key[""]), True
    elif pages and pages[0].get("label"):  # chapters known by their headings (CCEL, Gutenberg)
        page = match_label(location, pages)
        if page:
            url, found, location = page_url(page["title"]), True, page["location"] or location
    else:
        parts = _location_parts(location)
        for n in range(len(parts), 0, -1):
            key = ".".join(parts[:n])
            if key in by_key:
                url, found = page_url(by_key[key]), True
                break
    return {
        "work": work_id,
        "author": work["author"],
        "title": _cite_title(work),
        "translation": work["translation"],
        # Only show a location the app could match to a real page.
        "location": location if found and location else "",
        "url": url,
        "idea": (idea or "").strip()[:300],
    }


# ---- Bible quotes in another translation's words ------------------------

# Claude knows the ESV and NIV better than the BSB, so it often quotes the
# right verse in the wrong words. If the verse at the reference it gave says
# the same thing, show that verse in the BSB's words instead of a paraphrase.
VERSE_REF = re.compile(r"(\d+)\s*:\s*(\d+)(?:\s*[-–]\s*(\d+))?")
MAX_VERSES = 3
STOPWORDS = set("""a an and are as at be but by for from had has have he her his i if in into is it its
    my not of on or our shall she so that the their them then there they this those to unto was we were
    what when which who whom will with you your ye thee thou thy thine him me us all""".split())


def _content(text):
    return {w for w in (_norm(x) for x in text.split()) if len(w) > 2 and w not in STOPWORDS}


def bible_verse_for(work_id, location, quote):
    """The BSB text of the verse(s) at `location` ("11:4", "4:6-7"), as a citation,
    if they share most of the quote's key words; otherwise None."""
    work = works()[work_id]
    ref = VERSE_REF.search(location or "")
    want = _content(quote)
    if not ref or len(want) < 3:
        return None
    chapter, first = ref.group(1), int(ref.group(2))
    last = min(int(ref.group(3) or first), first + MAX_VERSES - 1)
    path = TEXTS_DIR / f"{work_id}.json"
    if not path.exists():
        return None
    page = next((p for p in json.loads(path.read_text(encoding="utf-8"))["pages"] if p["key"] == chapter), None)
    if not page:
        return None
    verses = [para for para in page["paragraphs"] if first <= int(para["section"]) <= last]
    if not verses:
        return None
    # Leave out sentences that share nothing with the quote (e.g. "A Psalm of David.").
    sentences = [s for v in verses for s in re.split(r"(?:(?<=[.!?])|(?<=[.!?][”’\"]))\s+", v["text"]) if s]
    while len(sentences) > 1 and not (_content(sentences[0]) & want):
        sentences.pop(0)
    while len(sentences) > 1 and not (_content(sentences[-1]) & want):
        sentences.pop()
    text = " ".join(sentences).strip('“”"')   # the reflection adds its own quotation marks
    shared = want & _content(text)
    if len(shared) < max(2, 0.5 * len(want)):
        return None
    end = int(verses[-1]["section"])
    return {
        "work": work_id,
        "author": work["author"],
        "title": _cite_title(work),
        "translation": work["translation"],
        "location": f"{chapter}:{first}" + (f"-{end}" if end != first else ""),
        "quote": text,
        "url": page_url(page["title"]),
    }


def cite(work_id, location, quote):
    """Resolve one quote Claude proposed.

    Returns a citation dict (verified, with link) or None (use the paraphrase).
    """
    work = works().get(work_id)
    if not work:
        return None
    match = find_quote(work_id, quote)
    if not match and "bible" in work["pages"]:
        rescued = bible_verse_for(work_id, location, quote)
        if rescued:
            log.info("Quote in %s %s matched the BSB verse by reference", work_id, location)
            return rescued
    if not match:
        log.info("Quote not found in %s (%s); using paraphrase", work_id, location)
        return None

    # Location: what we found beats what Claude said.
    key, section = match["page_key"], match["section"]
    claimed = (location or "").strip()
    separator = work.get("location_separator", ".")
    if section and work.get("location_from_section"):
        found_location = work.get("section_prefix", "") + section
    elif match["page_location"] is not None:    # a labelled chapter (CCEL, Gutenberg)
        found_location = match["page_location"]
    elif section:
        found_location = separator.join(p for p in (key, section) if p)
        end = match["end_section"]
        if separator == ":" and end and end != section:      # a quote running over several verses: "3:5-6"
            found_location += f"-{end}"
    elif not key:
        found_location = claimed  # one-page work: nothing finer to check against
    elif claimed == key or re.match(re.escape(key) + r"(?!\d)", claimed):
        found_location = claimed  # same page; keep Claude's finer detail
    else:
        found_location = key

    return {
        "work": work_id,
        "author": work["author"],
        "title": _cite_title(work),
        "translation": work["translation"],
        "location": found_location,
        "quote": match["text"],
        "url": page_url(match["title"]),
    }
