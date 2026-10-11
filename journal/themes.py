"""Themes: each entry is tagged with 1-3 themes from the edition's fixed list
(editions/<edition>/themes.txt), so Past entries can be browsed by theme.

New entries get their themes with their reflection (no extra request).
Earlier entries are tagged in small batches while the journal is open: the
page asks for the next batch until none are left (see app.py). Batches use a
small, inexpensive model and send only each entry's title and the start of
its text. They don't count toward the daily reflection limit.
"""
import json
import logging

from .config import EDITION_DIR

log = logging.getLogger(__name__)

BATCH_SIZE = 12
EXCERPT_CHARS = 1200          # enough of an entry to tell what it's about
TAGGING_MODEL = "claude-haiku-5-5"


def _load():
    path = EDITION_DIR / "themes.txt"
    if not path.exists():
        return []
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and line not in names:
            names.append(line)
    return names


THEMES = _load()


def clean(chosen):
    """Only themes from the list, at most 3, no repeats, in the list's spelling."""
    by_lower = {t.lower(): t for t in THEMES}
    out = []
    for t in chosen or []:
        t = by_lower.get(str(t).strip().lower())
        if t and t not in out:
            out.append(t)
    return out[:3]


def needs_tagging(entry):
    return "themes" not in entry and bool((entry.get("text") or "").strip())


SCHEMA_DESCRIPTION = ("The 1 to 3 themes from the list that best describe what this entry is about: the "
                      "writer's own life and concerns, not the reflection's ideas. Fewer is fine.")


def schema_property():
    """The "themes" field for the reflection's JSON schema."""
    return {"type": "array", "description": SCHEMA_DESCRIPTION, "items": {"type": "string", "enum": THEMES}}


def tag_batch(entries):
    """{entry id: [themes]} for a batch of entries, from one small request.
    Raises reflection.ReflectionError (friendly message) if it fails."""
    from . import reflection          # here, to avoid a circular import
    if not THEMES or not entries:
        return {}
    listing = "\n\n".join(
        f'<entry id="{e["id"]}">\nTitle: {e.get("title") or "(none)"}\n{(e.get("text") or "")[:EXCERPT_CHARS]}\n</entry>'
        for e in entries)
    schema = {
        "type": "object",
        "properties": {"entries": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "themes": schema_property()},
            "required": ["id", "themes"], "additionalProperties": False}}},
        "required": ["entries"], "additionalProperties": False,
    }
    try:
        response = reflection._client().messages.create(
            model=TAGGING_MODEL, max_tokens=2000,
            system=("You sort private journal entries into themes, choosing only from this list:\n"
                    + "\n".join(THEMES)
                    + "\n\nFor each entry, give the 1 to 3 themes that best describe what it's about (the "
                      "writer's own life and concerns). Fewer is fine when an entry is about one thing."),
            messages=[{"role": "user", "content": listing}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        data = json.loads(next(b.text for b in response.content if b.type == "text"))
    except Exception as exc:
        log.warning("Theme tagging failed for a batch of %d: %r", len(entries), exc)
        raise reflection.ReflectionError(reflection.friendly_error(exc, about_entry=False)) from exc
    wanted = {e["id"] for e in entries}
    return {item["id"]: clean(item.get("themes")) for item in data.get("entries", []) if item.get("id") in wanted}
