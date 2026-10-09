"""Asks Claude for a reflection on an entry, and turns the answer into
display-ready text with verified quotes.

Every failure becomes a ReflectionError with a friendly message; the entry
itself is already saved before any of this runs.
"""
import json
import logging
import os
import re
from collections import Counter
from datetime import datetime

import anthropic
from dotenv import load_dotenv

from . import sources, store
from .config import EDITION, EDITION_DIR, ROOT, TZ, settings

log = logging.getLogger(__name__)

PROMPTS_DIR = EDITION_DIR / "prompts"        # this edition's instructions for Claude
SHARED_PROMPTS_DIR = ROOT / "prompts"        # used by every edition (e.g. the support note)


class ReflectionError(Exception):
    """A failure with a message that's safe and friendly to show the user."""


# ---- Client and errors --------------------------------------------------

def _client():
    # Re-read .env each time so a newly added key works without a restart.
    load_dotenv(ROOT / ".env", override=True)
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        raise ReflectionError(
            "No Claude API key is set up yet. Add it to the .env file in the journal "
            "folder (see the README), then try again."
        )
    return anthropic.Anthropic(
        api_key=key,
        timeout=float(settings["api_timeout_seconds"]),
        max_retries=int(settings["api_retries"]),
    )


def friendly_error(exc, about_entry=True):
    """Translate an SDK exception into a plain-language message.

    about_entry: add the reassurance that the entry is saved (not for prompts).
    """
    saved = " Your entry is saved." if about_entry else ""
    model = settings["model"]
    if isinstance(exc, ReflectionError):
        return str(exc)
    if isinstance(exc, anthropic.AuthenticationError):
        return ("Claude didn't accept the API key in your .env file. Check that it was copied "
                "completely, and that it's an API key from console.anthropic.com "
                "(they start with sk-ant-api).")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "Your API key doesn't have permission to use Claude. Check it in the Anthropic Console."
    if isinstance(exc, anthropic.NotFoundError):
        return f'The model name in Settings ("{model}") wasn\'t recognized. Check the spelling.'
    if isinstance(exc, anthropic.RateLimitError):
        return "Claude is getting too many requests right now. Wait a minute, then try again."
    if isinstance(exc, anthropic.APITimeoutError):
        return f"Claude took too long to answer.{saved} Try again in a moment."
    if isinstance(exc, anthropic.APIConnectionError):
        return f"Couldn't reach Claude. Check your internet connection.{saved}"
    if isinstance(exc, anthropic.BadRequestError):
        msg = str(getattr(exc, "message", exc)).lower()
        if "credit" in msg or "billing" in msg:
            return ("Your Anthropic account is out of credits. Add credits at "
                    "console.anthropic.com → Billing, then try again.")
        return "Claude couldn't process this request. Details are in logs/journal.log."
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code in (500, 502, 503, 504, 529):
            return "Claude is busy or having trouble right now. Try again in a few minutes."
        return f"Claude returned an error ({exc.status_code}). Details are in logs/journal.log."
    return "Something went wrong while asking Claude. Details are in logs/journal.log."


def _request_options(schema=None, effort=None):
    """Model, effort and (optionally) the JSON response format.

    Haiku doesn't take an effort level, so it's left out there.
    """
    model = settings["model"]
    output_config = {}
    if not model.startswith("claude-haiku"):
        output_config["effort"] = effort or settings["reflection_effort"]
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    opts = {"model": model}
    if output_config:
        opts["output_config"] = output_config
    return opts


# ---- Prompt -------------------------------------------------------------

def _read_prompt(name):
    path = PROMPTS_DIR / name if (PROMPTS_DIR / name).exists() else SHARED_PROMPTS_DIR / name
    text = path.read_text(encoding="utf-8")
    return re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()


# The voices a reflection can draw on (from the edition; also used to spread reflections across them).
THINKERS = list(EDITION["voices"])
RECENT_REFLECTIONS = 8   # how far back to look when nudging towards variety
VOICE = EDITION.get("voice_singular", "thinker")   # what the edition calls one of its voices
# Example sentences in Claude's instructions, so they come from the edition's own voices.
EXAMPLES = {
    "paraphrase": "Seneca observed that we suffer more in imagination than in reality.",
    "reference": "Seneca, in On the Shortness of Life, argued that…",
    "quote_intro": "As Seneca wrote, [[q1]]",
    **EDITION.get("examples", {}),
}


def _schema():
    work_ids = list(sources.works().keys())
    return {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "A short title for the entry, about 5 words, naming its topic "
                               "in the style of an essay title, e.g. 'On wit and teasing' or "
                               "'The novel I keep not writing'. Sentence case, no quotation "
                               "marks, no final period.",
            },
            "insight": {
                "type": "string",
                "description": "The insight, 120-250 words. Where a quote belongs, write its "
                               "marker, e.g. [[q1]], instead of the quoted words.",
            },
            "question": {"type": "string", "description": "One thought-provoking question."},
            "quotes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "marker": {"type": "string", "description": "q1, q2, ..."},
                        "work": {"type": "string", "enum": work_ids},
                        "location": {"type": "string"},
                        "quote": {"type": "string", "description": "Exact words, no quotation marks."},
                        "paraphrase": {
                            "type": "string",
                            "description": "One complete, attributed sentence that can replace the "
                                           "WHOLE sentence containing the marker if the quote can't "
                                           f"be verified, e.g. '{EXAMPLES['paraphrase']}' It follows "
                                           "the sentence before it, so if that sentence already names "
                                           "the author, use a pronoun or short form ('He observed that…') "
                                           "instead of repeating the full name.",
                        },
                    },
                    "required": ["marker", "work", "location", "quote", "paraphrase"],
                    "additionalProperties": False,
                },
            },
            "references": {
                "type": "array",
                "description": "Every work from the list whose ideas the insight draws on or "
                               f"attributes WITHOUT quoting it, e.g. '{EXAMPLES['reference']}'. "
                               "(Works you quote are cited from the quote.)",
                "items": {
                    "type": "object",
                    "properties": {
                        "work": {"type": "string", "enum": work_ids},
                        "location": {
                            "type": "string",
                            "description": "The most precise location you're confident of, in the "
                                           "work's location format; empty if unsure.",
                        },
                        "idea": {"type": "string", "description": "The idea, in a few words."},
                    },
                    "required": ["work", "location", "idea"],
                    "additionalProperties": False,
                },
            },
            "thinkers": {
                "type": "array",
                "description": f"The one to three {VOICE}s whose ideas the insight mainly draws on.",
                "items": {"type": "string", "enum": THINKERS},
            },
            "wellbeing_concern": {
                "type": "boolean",
                "description": "True only if the entry suggests real distress or risk.",
            },
        },
        "required": ["title", "insight", "question", "quotes", "references", "thinkers", "wellbeing_concern"],
        "additionalProperties": False,
    }


def _system_prompt():
    return (
        _read_prompt("reflection.md")
        + "\n\n## Works you may quote\n\n"
        + sources.describe_for_prompt()
        + "\n\n## Response format\n\n"
        "Respond with JSON matching the schema. In the insight, put each quote's marker "
        "(e.g. [[q1]]) exactly where the quoted words belong, with the words that introduce it "
        f"around the marker, e.g. '{EXAMPLES['quote_intro']}'. Give the quote a sentence of its own: "
        "if the quote can't be verified, that whole sentence is replaced by the paraphrase, which "
        "must therefore be a complete, attributed sentence. Leave quotes empty if you don't "
        "quote anything. Whenever you attribute an idea to one of the works listed above without "
        "quoting it, add it to references, so the reader can follow it to the source."
    )


def recent_thinkers(entry):
    """How often each thinker featured in the writer's last few reflections (not
    counting this entry). Older reflections, from before thinkers were recorded,
    are read from their quotes and source links."""
    reflected = [e for e in store.all_entries()
                 if e["id"] != entry["id"] and e.get("reflection_status") == store.DONE and e.get("insight")]
    counts = Counter()
    for e in reflected[-RECENT_REFLECTIONS:]:
        names = e.get("thinkers") or {c.get("author") for c in (e.get("quotes") or []) + (e.get("references") or [])}
        counts.update(n for n in set(names) if n in THINKERS)   # only thinkers still on offer
    return counts, min(len(reflected), RECENT_REFLECTIONS)


def recent_scripture(entry):
    """How often each book of the Bible was cited in the last few reflections (editions with Scripture)."""
    bible = {w: work["cite_title"] for w, work in sources.works().items() if "bible" in work["pages"]}
    if not bible:
        return Counter()
    reflected = [e for e in store.all_entries()
                 if e["id"] != entry["id"] and e.get("reflection_status") == store.DONE and e.get("insight")]
    counts = Counter()
    for e in reflected[-RECENT_REFLECTIONS:]:
        books = {bible[c.get("work")] for c in (e.get("quotes") or []) + (e.get("references") or [])
                 if c.get("work") in bible}
        counts.update(books)
    return counts


def _variety_note(entry):
    counts, seen = recent_thinkers(entry)
    if not seen:
        return None
    used = ", ".join(f"{name} ({n})" for name, n in counts.most_common()) or f"no named {VOICE}"
    note = (f"For variety: this person's last {seen} reflection{'s' if seen != 1 else ''} drew mainly on "
            f"{used}.")
    fresh = [name for name in THINKERS if name not in counts]
    if fresh:
        note += (f" Not used recently: {', '.join(fresh)}. Prefer one of those this time, unless a recent "
                 f"{VOICE} is clearly the best lens for this particular entry.")
    books = recent_scripture(entry)
    if books:
        note += (" Scripture they've recently been given: "
                 + ", ".join(f"{book} ({n})" for book, n in books.most_common())
                 + ". Unless one of those clearly fits best, open a different part of the Bible this time.")
    return note


def _user_message(entry):
    when = datetime.fromisoformat(entry["created_at"]).astimezone(TZ)
    clock = when.strftime("%I:%M %p").lstrip("0")
    parts = [f"Journal entry written {when:%A, %B} {when.day}, {when.year} at {clock}."]
    if entry.get("title"):
        parts.append(f"They titled it: {entry['title']}")
    if entry.get("prompt"):
        parts.append(f"They were writing in response to this prompt: {entry['prompt']}")
    parts.append("<entry>\n" + entry["text"] + "\n</entry>")
    if (variety := _variety_note(entry)):
        parts.append(variety)
    return "\n\n".join(parts)


# ---- The call -----------------------------------------------------------

def reflect(entry):
    """Returns the fields to store on the entry. Raises ReflectionError."""
    try:
        client = _client()
        response = client.messages.create(
            max_tokens=16000,
            system=_system_prompt(),
            messages=[{"role": "user", "content": _user_message(entry)}],
            **_request_options(schema=_schema()),
        )
    except Exception as exc:
        log.warning("Reflection request failed for %s: %r", entry["id"], exc)
        raise ReflectionError(friendly_error(exc)) from exc

    if response.stop_reason == "refusal":
        log.warning("Claude declined to reflect on %s", entry["id"])
        raise ReflectionError("Claude declined to reflect on this entry. You can try again, "
                              "or edit the entry first.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
        insight, question = data["insight"].strip(), data["question"].strip()
    except (ValueError, KeyError, AttributeError):
        log.error("Unusable reflection for %s (stop_reason=%s, %d characters)",
                  entry["id"], response.stop_reason, len(text))  # never log the text itself
        raise ReflectionError("Claude's answer came back incomplete. Try again.") from None

    rendered, citations, unverified = render_insight(insight, data.get("quotes") or [])
    references = resolve_references((data.get("references") or []) + unverified, citations)
    log.info("Reflection for %s: %d words, %d verified quote(s) of %d proposed, %d reference(s)",
             entry["id"], len(rendered.split()), len(citations), len(data.get("quotes") or []),
             len(references))
    return {
        "suggested_title": _short_title(data.get("title")),
        "insight": rendered,
        "question": question,
        "quotes": citations,
        "references": references,
        "wellbeing_concern": bool(data.get("wellbeing_concern")),
        "thinkers": [n for n in (data.get("thinkers") or []) if n in THINKERS][:3],
        "reflection_model": response.model,
        "reflected_at": datetime.now(TZ).isoformat(),
    }


def _short_title(title):
    """Keep generated titles short (about 5 words; never more than 8)."""
    words = (title or "").split()
    return " ".join(words[:8]) or None


def resolve_references(refs, citations):
    """Turn Claude's references into links built from the source map, one per
    work and location; works already cited by a verified quote aren't repeated."""
    quoted = {(c["work"], c["location"]) for c in citations}
    out, seen = [], set()
    for r in refs:
        try:
            ref = sources.reference(r.get("work"), r.get("location"), r.get("idea"))
        except Exception:
            log.exception("Couldn't resolve a reference to %s", r.get("work"))
            continue
        if not ref:
            continue
        key = (ref["work"], ref["location"])
        if key in seen or key in quoted:
            continue
        seen.add(key)
        out.append(ref)
    return out


MARKER = re.compile(r"\[\[\s*([A-Za-z0-9_]+)\s*\]\]")
SENTENCE_END_BEFORE = re.compile(r"[.!?][\"”’)]*\s+|\n")
SENTENCE_END_AFTER = re.compile(r"[.!?][\"”’)]*(?=\s|$)|\n")


QUOTE_ENDS_SENTENCE = re.compile(r"[.!?\"”’)]*(?=\s+[A-Z“\"]|\s*$)")


def _sentence_around(text, start, end):
    """The span of the sentence containing the marker text[start:end]."""
    s = 0
    for m in SENTENCE_END_BEFORE.finditer(text, 0, start):
        s = m.end()
    # A quote usually ends its sentence, even without a full stop after the
    # marker ("he wrote: [[q1]] He wasn't…"): a capital letter next means a new sentence.
    ends_here = QUOTE_ENDS_SENTENCE.match(text, end)
    if ends_here:
        return s, ends_here.end()
    after = SENTENCE_END_AFTER.search(text, end)
    e = len(text) if not after else (after.start() if after.group() == "\n" else after.end())
    return s, e


def _as_sentence(paraphrase):
    p = " ".join((paraphrase or "").split())
    if not p:
        return ""
    p = p[0].upper() + p[1:]
    return p if p[-1] in ".!?" else p + "."


def render_insight(insight, quotes):
    """Swap each [[marker]] for the verified quote. If a quote can't be
    verified, the whole sentence around it is replaced by the paraphrase (a
    complete sentence), so the text always reads cleanly.
    Returns (text, verified citations, unverified quotes as references)."""
    citations, unverified = [], []
    by_marker = {q.get("marker", "").strip("[] "): q for q in quotes}
    text = insight
    # Work from the last marker back, so earlier positions stay valid.
    while True:
        found = list(MARKER.finditer(text))
        if not found:
            break
        m = found[-1]
        q = by_marker.get(m.group(1))
        if not q:
            text = text[:m.start()] + text[m.end():]
            continue
        try:
            cite = sources.cite(q.get("work"), q.get("location"), q.get("quote"))
        except Exception:
            log.exception("Quote check failed for %s; using paraphrase", q.get("work"))
            cite = None
        if cite:
            citations.append(cite)
            text = text[:m.start()] + f"“{cite['quote']}”" + text[m.end():]
        else:
            # Not verified: the paraphrase replaces the sentence, still linked to its source.
            unverified.append({"work": q.get("work"), "location": q.get("location"), "idea": q.get("paraphrase", "")})
            s, e = _sentence_around(text, m.start(), m.end())
            text = text[:s] + _as_sentence(q.get("paraphrase")) + text[e:]
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    return text, citations[::-1], unverified[::-1]


def support_note():
    return _read_prompt("support_note.md")


def test_key():
    """Checks the key, model and account with a tiny request. Returns a message."""
    try:
        client = _client()
        client.models.retrieve(settings["model"])
        client.messages.create(
            max_tokens=64,
            messages=[{"role": "user", "content": "Reply with the word OK."}],
            **_request_options(),
        )
    except Exception as exc:
        log.warning("API key test failed: %r", exc)
        raise ReflectionError(friendly_error(exc)) from exc
    return f"Your API key works, and the model {settings['model']} is available."
