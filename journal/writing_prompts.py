"""Writing prompts: one suggestion at a time, informed by recent entries."""
import json
import logging
import random
from datetime import datetime

from . import config, store, tokens
from .config import TZ
from .library import Library
from .reflection import ReflectionError, _client, _read_prompt, _request_options, friendly_error

log = logging.getLogger(__name__)

WINDOW_DAYS = 60        # where someone is on their journey: older entries are left out
JUST_WRITTEN_DAYS = 2   # today's and yesterday's entries: context only, not a thread to return to
SWEET_SPOT = (5, 21)    # days ago: long enough to feel like returning, recent enough to matter
MAX_ENTRIES = 10
MAX_HIGHLIGHTS = 20
MAX_PRAYERS = 8
STARTER_UNDER = 3      # with fewer entries than this, prompts come from the edition's own list first
ENTRY_CHARS = 1500     # enough to catch the gist of a ~500-word entry
PROMPT_EFFORT = "low"  # prompts are short; keep them quick and cheap

APPROACHES = ["revisit_theme", "follow_up_question", "highlighted_line", "prayer", "fresh"]

SCHEMA = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string", "description": "The writing prompt itself, one or two sentences."},
        "approach": {"type": "string", "enum": APPROACHES},
        "based_on_entry": {
            "type": "string",
            "description": "The entry ID this prompt builds on, or an empty string for a fresh prompt.",
        },
    },
    "required": ["prompt", "approach", "based_on_entry"],
    "additionalProperties": False,
}


def _age(e, now):
    """Whole days since the entry was written, by the calendar (today = 0)."""
    return (now.date() - datetime.fromisoformat(e["created_at"]).astimezone(TZ).date()).days


def _weight(e, now):
    """How good a thread an entry is to return to: best from about a week to three
    weeks ago, better still when they marked it as mattering."""
    age = _age(e, now)
    weight = 3 if SWEET_SPOT[0] <= age <= SWEET_SPOT[1] else 2 if age <= 35 else 1
    weight += 2 * any(h.get("where") in ("entry", "reflection", "question") for h in e.get("highlights") or [])
    weight += bool(e.get("favourite")) + (e.get("rating") == "yes") - (e.get("rating") == "no")
    return weight + random.random()          # a little chance, so "Another" draws on different entries


def _choose_entries(entries, now, avoid_entry=None):
    """(just written, to return to): the last day or two's entries, for context only,
    and up to MAX_ENTRIES more from the last WINDOW_DAYS days, favouring the best threads."""
    window = [e for e in entries if _age(e, now) <= WINDOW_DAYS]
    just = [e for e in window if _age(e, now) < JUST_WRITTEN_DAYS][-2:]
    earlier = [e for e in window if _age(e, now) >= JUST_WRITTEN_DAYS and e["id"] != avoid_entry]
    picked = sorted(earlier, key=lambda e: _weight(e, now), reverse=True)[:MAX_ENTRIES - len(just)]
    return just, sorted(picked, key=lambda e: e["created_at"])


def _ago(days):
    return "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago"


def _highlights(entries, now):
    """Lines they marked in the last WINDOW_DAYS days, newest first: [(entry, highlight)]."""
    marks = [(e, h) for e in entries if _age(e, now) <= WINDOW_DAYS for h in e.get("highlights") or []
             if h.get("text")]
    return sorted(marks, key=lambda m: m[0]["created_at"], reverse=True)[:MAX_HIGHLIGHTS]


def _prayer_lines(now):
    """Their prayer list (editions that have one): open prayers, and ones answered lately."""
    if not config.EDITION.get("prayer_list"):
        return []
    from . import prayers
    try:
        items = prayers.all_prayers()
    except Exception:
        log.exception("Couldn't read the prayer list for a prompt")
        return []

    def days(iso):
        return (now.date() - datetime.fromisoformat(iso).astimezone(TZ).date()).days
    open_ = [p for p in items if not p.get("answered_at")][:MAX_PRAYERS]
    answered = [p for p in items if p.get("answered_at") and days(p["answered_at"]) <= WINDOW_DAYS][:MAX_PRAYERS // 2]
    lines = [f'- "{p["text"]}" (added {_ago(days(p["added_at"]))}, still open)' for p in open_]
    lines += [f'- "{p["text"]}" (answered {_ago(days(p["answered_at"]))}'
              + (f'; their note: "{p["answer_note"]}"' if p.get("answer_note") else "") + ")" for p in answered]
    return lines


def _entry_block(e, now, just_written=False):
    when = datetime.fromisoformat(e["created_at"]).astimezone(TZ)
    text = " ".join(e["text"].split())
    if len(text) > ENTRY_CHARS:
        text = text[:ENTRY_CHARS] + " […]"
    marks = ['favorite="yes"'] if e.get("favourite") else []
    if e.get("rating") == "yes":
        marks.append('reflection="spoke to them"')
    elif e.get("rating") == "no":
        marks.append('reflection="did not quite land"')
    if just_written:
        marks.append('just_written="yes: context only, do not build the prompt on it"')
    block = [f'<entry id="{e["id"]}" date="{when:%A, %B} {when.day}, {when.year}" age="{_ago(_age(e, now))}" '
             f'title="{e.get("title") or "No Title"}"{"".join(" " + m for m in marks)}>', text]
    if e.get("prompt"):
        block.append(f"(Written in response to the prompt: {e['prompt']})")
    if e.get("question"):
        block.append(f"(Reflection question they were given: {e['question']})")
    block.append("</entry>")
    return "\n".join(block)


def _context(entries, already_suggested, avoid_entry=None, now=None):
    now = now or datetime.now(TZ)
    just, earlier = _choose_entries(entries, now, avoid_entry)
    if not entries:
        parts = ["They haven't written any entries yet. Offer a fresh prompt to begin with."]
    elif not just and not earlier:
        parts = [f"They haven't written for a while (their last entry was {_ago(_age(entries[-1], now))}). "
                 "Offer a fresh prompt, gently, to begin again."]
    else:
        parts = []
        if earlier:
            parts.append(f"Entries from the last {WINDOW_DAYS} days to choose from, oldest first:\n\n"
                         + "\n\n".join(_entry_block(e, now) for e in earlier))
        if just:
            parts.append("Just written (context only: where they are now; don't build the prompt on these):\n\n"
                         + "\n\n".join(_entry_block(e, now, just_written=True) for e in just))
        marks = _highlights(entries, now)
        if marks:
            where = {"entry": "in their entry", "reflection": "in its reflection", "question": "in its question"}
            parts.append("Lines they highlighted themselves, newest first (what they marked as mattering):\n"
                         + "\n".join(f'- "{h["text"]}" ({where.get(h.get("where"), "in an entry")}, '
                                      f'{_ago(_age(e, now))}, entry {e["id"]})' for e, h in marks))
    prayers_ = _prayer_lines(now)
    if prayers_:
        parts.append("Their prayer list:\n" + "\n".join(prayers_))
    if avoid_entry:
        parts.append(f"The last prompt built on entry {avoid_entry}: choose a different thread this time.")
    if already_suggested:
        parts.append("Already suggested just now. Offer something clearly different:\n"
                     + "\n".join(f"- {p}" for p in already_suggested))
    return "\n\n".join(parts)


def starter(already_suggested=()):
    """For someone just starting (fewer than STARTER_UNDER entries): one of the
    edition's hand-written questions ("reminder_prompts" in edition.json, the
    same ones the reminder emails ask), not yet suggested or answered. Instant,
    and no prompt from the day's allowance. None once they've written a few
    entries, or seen every question: then Claude suggests one from their entries."""
    entries = store.visible_entries()
    if len(entries) >= STARTER_UNDER:
        return None
    used = set(already_suggested) | {e.get("prompt") for e in entries}
    choices = [p for p in config.EDITION.get("reminder_prompts") or [] if p not in used]
    if not choices:
        return None
    return {"prompt": random.choice(choices), "approach": "starter", "based_on": None}


def suggest(already_suggested=(), writers=None, avoid_entry=None):
    """Returns {"prompt", "approach", "based_on": {id, title, date} or None}.
    `writers`: the reader's choice of writers (their "writers" preference).
    `avoid_entry`: the entry the previous prompt built on, so the next one finds another thread."""
    off_note = Library(writers).off_note(brief=True)
    entries = store.visible_entries()  # hidden entries stay out of it
    already = [p for p in already_suggested if p][-10:]
    # Prompts used for recent entries shouldn't come back either.
    already += [e["prompt"] for e in entries[-MAX_ENTRIES:] if e.get("prompt") and e["prompt"] not in already]

    try:
        response = _client().messages.create(
            max_tokens=4000,
            system=_read_prompt("writing_prompt.md")
            + (f"\n\n{off_note}" if off_note else "")
            + "\n\n## Response format\n\nRespond with JSON matching the schema.",
            messages=[{"role": "user", "content": _context(entries, already, avoid_entry)}],
            **_request_options(schema=SCHEMA, effort=PROMPT_EFFORT),
        )
    except Exception as exc:
        log.warning("Writing prompt request failed: %r", exc)
        raise ReflectionError(friendly_error(exc, about_entry=False)) from exc

    tokens.record("prompt", response)
    if response.stop_reason == "refusal":
        raise ReflectionError("A prompt couldn't be suggested right now. Try again.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
        prompt = " ".join(data["prompt"].split()).strip().strip('"“”')
    except (ValueError, KeyError, AttributeError):
        log.error("Unusable writing prompt (stop_reason=%s, %d characters)", response.stop_reason, len(text))
        raise ReflectionError("The prompt came back incomplete. Try again.") from None
    if not prompt:
        raise ReflectionError("The prompt came back empty. Try again.")

    based_on = None
    source = next((e for e in entries if e["id"] == data.get("based_on_entry")), None)
    if source and data.get("approach") != "fresh":
        when = datetime.fromisoformat(source["created_at"]).astimezone(TZ)
        based_on = {"id": source["id"], "title": source.get("title"),
                    "date": f"{when:%B} {when.day}"}
    log.info("Writing prompt suggested (%s%s)", data.get("approach"),
             f", from {source['id']}" if based_on else "")
    return {"prompt": prompt, "approach": data.get("approach"), "based_on": based_on}
