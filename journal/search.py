"""Searching the journal: entries, titles, reflections and questions.

Runs over the unlocked journal in memory (like everything else the page
shows), so no readable index is ever written to disk. Each entry's words are
prepared once and kept in memory until the journal locks.

Forgiving on purpose, since people rarely remember exact wording:
  - word endings don't matter: "forgive" finds forgiving and forgiveness;
  - the last word can be half-typed: "anxi" finds anxious and anxiety;
  - a one-letter slip in a longer word still matches: "patiance";
  - "a phrase in quotes" must appear as written (endings still forgiven).
Every word (or phrase) must appear somewhere in the entry. Titles count
most, then the entry itself, then the reflection and its question.
"""
import functools
import re
import threading
import unicodedata

from . import vault

FIELDS = (("title", 4.0), ("text", 3.0), ("insight", 1.5), ("question", 1.5))
SNIPPET_FIELDS = ("text", "insight", "question")        # where a snippet can come from
LABELS = {"text": "entry", "insight": "reflection", "question": "question"}
MAX_RESULTS = 60
SNIPPET_CHARS = 170

_cache = {}                     # (user, entry id) -> (fingerprint, prepared words)
_vocab = {}                     # user -> {"stems": set, "words": set}: every word in their journal
_cache_lock = threading.Lock()


@vault.on_lock
def _forget(user_id):
    with _cache_lock:
        for key in [k for k in _cache if k[0] == user_id]:
            del _cache[key]
        _vocab.pop(user_id, None)


# ---- Words ----------------------------------------------------------------

@functools.lru_cache(maxsize=4096)
def _fold_char(ch):
    base = [c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)]
    c = (base[0] if base else ch).lower()
    return "'" if c in "’‘" else c if len(c) == 1 else ch


def _fold(text):
    """Lowercase, accents removed, one character per character (so positions still line up)."""
    if text.isascii():
        return text.lower()
    return "".join(_fold_char(ch) for ch in text)


SUFFIXES = ("ations", "nesses", "ation", "ingly", "ments", "ness", "ment",
            "ings", "edly", "ful", "ing", "ies", "ied", "ers", "ed", "es", "ly", "er", "s", "e")


@functools.lru_cache(maxsize=200_000)
def stem(word):
    """A light stemmer: enough to treat forgive / forgiving / forgiveness alike."""
    w = word.strip("'")
    if w.endswith("'s"):
        w = w[:-2]
    for _ in range(3):                                    # e.g. hopefully -> hopeful -> hope -> hop
        for suffix in SUFFIXES:
            if w.endswith(suffix) and len(w) - len(suffix) >= 3:
                w = w[: -len(suffix)] + ("y" if suffix in ("ies", "ied") else "")
                break
        else:
            break
    return w


WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")


def words(text):
    """[(stem, start, end, word)] for each word, with positions in the original text."""
    return [(stem(m.group()), m.start(), m.end(), m.group()) for m in WORD.finditer(_fold(text or ""))]


def _index(tokens):
    stem_at, word_at = {}, {}
    for i, (s, _, _, w) in enumerate(tokens):
        stem_at.setdefault(s, []).append(i)
        word_at.setdefault(w, []).append(i)
    return {"tokens": tokens, "stem_at": stem_at, "word_at": word_at}


def _prepared(entry, user_id):
    """Each field's words, indexed, kept until the entry changes or the journal locks."""
    key = (user_id, entry["id"])
    fingerprint = tuple(entry.get(f) or "" for f, _ in FIELDS)
    with _cache_lock:
        cached = _cache.get(key)
        if cached and cached[0] == fingerprint:
            return cached[1]
    prepared = {field: _index(words(entry.get(field) or "")) for field, _ in FIELDS}
    with _cache_lock:
        _cache[key] = (fingerprint, prepared)
        vocab = _vocab.setdefault(user_id, {"stems": set(), "words": set()})
        for f in prepared.values():
            vocab["stems"].update(f["stem_at"])
            vocab["words"].update(f["word_at"])
    return prepared


# ---- Matching ---------------------------------------------------------------

def _one_slip(a, b):
    """True if a and b differ by one letter (changed, added or removed)."""
    if abs(len(a) - len(b)) > 1 or a == b:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def parse(query):
    """[(stems, prefix_ok, typed)]: each item is one word, or a "quoted phrase"
    (several stems). typed holds each word as typed when it has 5+ letters (so a
    one-letter slip in it is forgiven), else None."""
    terms = []
    folded = _fold(query or "")
    for phrase, word in re.findall(r'"([^"]+)"|(\S+)', folded):
        typed = [m.group() for m in WORD.finditer(phrase or word)]
        if typed:
            terms.append(([stem(w) for w in typed], False, [w if len(w) >= 5 else None for w in typed]))
    if terms and not folded.rstrip().endswith('"') and len(terms[-1][0]) == 1:
        terms[-1] = (terms[-1][0], True, terms[-1][2])    # the word still being typed
    return terms


def _matchers(term, vocab):
    """For each word of a term: the stems and whole words in the journal that count as a match.
    Worked out once per search against the journal's vocabulary, so each entry needs only lookups."""
    stems, prefix_ok, typed = term
    out = []
    for k, s in enumerate(stems):
        ok_stems = {s}
        if prefix_ok and k == len(stems) - 1 and len(s) >= 3:
            ok_stems |= {v for v in vocab["stems"] if v.startswith(s)}       # the word still being typed
        ok_words = {w for w in vocab["words"] if _one_slip(w, typed[k])} if typed[k] else set()
        out.append((ok_stems, ok_words))
    return out


def _positions(field, matchers):
    """Indexes in the field's words where the term (one word, or a phrase) starts."""
    first_stems, first_words = matchers[0]
    starts = set()
    for s in first_stems:
        starts.update(field["stem_at"].get(s, ()))
    for w in first_words:
        starts.update(field["word_at"].get(w, ()))
    tokens = field["tokens"]
    hits = []
    for i in sorted(starts):
        if i + len(matchers) > len(tokens):
            continue
        if all(tokens[i + k][0] in ms or tokens[i + k][3] in mw for k, (ms, mw) in enumerate(matchers) if k):
            hits.append(i)
    return hits


# ---- Results ------------------------------------------------------------------

def _snippet(text, spans):
    """A window of text around the first match, cut at word boundaries, and the
    matched words' positions inside it (for highlighting)."""
    first = spans[0][0]
    start = max(0, first - SNIPPET_CHARS // 3)
    end = min(len(text), start + SNIPPET_CHARS)
    if start > 0 and " " in text[start:first]:
        start = text.index(" ", start) + 1
    if end < len(text) and text.rfind(" ", first, end) > first:
        end = text.rfind(" ", first, end)
    prefix, suffix = ("…" if start > 0 else ""), ("…" if end < len(text) else "")
    piece = re.sub(r"\s", " ", text[start:end])           # line breaks become spaces; positions unchanged
    marks = [[s - start + len(prefix), e - start + len(prefix)] for s, e in spans if s >= start and e <= end]
    return prefix + piece + suffix, marks


def search(entries, query, user_id=None):
    """Entries matching the query, best first. Each result: id, field (where the
    snippet comes from), snippet and marks ([start, end] to highlight)."""
    terms = parse(query)
    if not terms:
        return []
    prepared_all = [(entry, _prepared(entry, user_id)) for entry in entries]
    with _cache_lock:
        vocab = _vocab.get(user_id, {"stems": set(), "words": set()})
        matchers = [_matchers(term, vocab) for term in terms]
    results = []
    for entry, prepared in prepared_all:
        score, found = 0.0, {}
        for (stems, _, _), term_matchers in zip(terms, matchers):
            term_score = 0.0
            for field, weight in FIELDS:
                tokens = prepared[field]["tokens"]
                hits = _positions(prepared[field], term_matchers)
                if hits:
                    term_score += weight * (1 + min(len(hits), 5) * 0.2)
                    spans = [(tokens[i][1], tokens[i + len(stems) - 1][2]) for i in hits]
                    found.setdefault(field, []).extend(spans)
            if not term_score:
                break                                     # every term must match somewhere
            score += term_score
        else:
            field = next((f for f in SNIPPET_FIELDS if f in found), None)
            snippet, marks = ("", [])
            if field:
                snippet, marks = _snippet(entry.get(field) or "", sorted(found[field]))
            title_marks = sorted([list(s) for s in found.get("title", [])])
            results.append({"id": entry["id"], "score": round(score, 2), "created_at": entry["created_at"],
                            "field": LABELS.get(field, "title"), "snippet": snippet, "marks": marks,
                            "title_marks": title_marks})
    results.sort(key=lambda r: r["created_at"], reverse=True)   # newest first among equal scores...
    results.sort(key=lambda r: -r["score"])                     # ...best first overall (a stable sort)
    return results[:MAX_RESULTS]
