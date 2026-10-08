"""Writing prompts: one suggestion at a time, informed by recent entries."""
import json
import logging
from datetime import datetime

from . import store
from .config import TZ
from .reflection import ReflectionError, _client, _read_prompt, _request_options, friendly_error

log = logging.getLogger(__name__)

RECENT_ENTRIES = 8
ENTRY_CHARS = 1500     # enough to catch the gist of a ~500-word entry
PROMPT_EFFORT = "low"  # prompts are short; keep them quick and cheap

APPROACHES = ["revisit_theme", "follow_up_question", "fresh"]

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


def _recent_entries():
    entries = store.visible_entries()  # hidden entries stay out of it
    return entries[-RECENT_ENTRIES:]


def _context(entries, already_suggested):
    if not entries:
        parts = ["They haven't written any entries yet. Offer a fresh prompt to begin with."]
    else:
        parts = [f"Their {len(entries)} most recent entries, oldest first:"]
        for e in entries:
            when = datetime.fromisoformat(e["created_at"]).astimezone(TZ)
            text = " ".join(e["text"].split())
            if len(text) > ENTRY_CHARS:
                text = text[:ENTRY_CHARS] + " […]"
            block = [f'<entry id="{e["id"]}" date="{when:%A, %B} {when.day}, {when.year}" '
                     f'title="{e.get("title") or "No Title"}">', text]
            if e.get("prompt"):
                block.append(f"(Written in response to the prompt: {e['prompt']})")
            if e.get("question"):
                block.append(f"(Reflection question they were given: {e['question']})")
            block.append("</entry>")
            parts.append("\n".join(block))
    if already_suggested:
        parts.append("Already suggested just now. Offer something clearly different:\n"
                     + "\n".join(f"- {p}" for p in already_suggested))
    return "\n\n".join(parts)


def suggest(already_suggested=()):
    """Returns {"prompt", "approach", "based_on": {id, title, date} or None}."""
    entries = _recent_entries()
    already = [p for p in already_suggested if p][-10:]
    # Prompts used for recent entries shouldn't come back either.
    already += [e["prompt"] for e in entries if e.get("prompt") and e["prompt"] not in already]

    try:
        response = _client().messages.create(
            max_tokens=4000,
            system=_read_prompt("writing_prompt.md")
            + "\n\n## Response format\n\nRespond with JSON matching the schema.",
            messages=[{"role": "user", "content": _context(entries, already)}],
            **_request_options(schema=SCHEMA, effort=PROMPT_EFFORT),
        )
    except Exception as exc:
        log.warning("Writing prompt request failed: %r", exc)
        raise ReflectionError(friendly_error(exc, about_entry=False)) from exc

    if response.stop_reason == "refusal":
        raise ReflectionError("Claude couldn't suggest a prompt right now. Try again.")
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
