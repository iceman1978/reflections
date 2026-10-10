"""Import entries from a CSV file made with Export (CSV), into the current
person's encrypted journal. Used to move a journal (e.g. from your own
computer to the web version) or to restore entries from an export.

Entries already in the journal (same date, time and text) are skipped, so
importing the same file twice doesn't create duplicates.
"""
import csv
import io
import re
from datetime import datetime

from . import sources, store
from .config import TZ

REQUIRED = {"Date", "Time", "Entry"}


class ImportProblem(Exception):
    """Something about the file, safe to show the person."""


def _parse_sources(text):
    """Turn the export's source lines back into quote / idea citations (best effort).
    e.g. '— Seneca, On the Shortness of Life 3 (idea) https://…'"""
    quotes, references = [], []
    works = sources.works()
    for line in (text or "").splitlines():
        m = re.match(r"^—\s*(.+?),\s*(.+?)\s*(?:\(([^)]*)\))?\s*(https://\S+)?\s*$", line.strip())
        if not m:
            continue
        author, rest, kind, url = m.groups()
        work_id, location = None, ""
        for wid, w in works.items():
            if w["author"] == author and rest.startswith(w["title"]):
                work_id, location = wid, rest[len(w["title"]):].strip()
                break
        if not work_id:
            continue
        w = works[work_id]
        item = {"work": work_id, "author": w["author"], "title": w["title"], "translation": w["translation"],
                "location": location, "url": url or ""}
        (references if kind == "idea" else quotes).append(item)
    return quotes, references


def import_csv(data: bytes):
    """Returns (imported, skipped). Raises ImportProblem."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportProblem("That file isn't a CSV exported from the journal (it isn't UTF-8 text).") from None
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not REQUIRED <= set(reader.fieldnames):
        raise ImportProblem("That file doesn't look like a journal export: it needs Date, Time and Entry columns.")

    existing = {(e["created_at"][:16], e["text"].strip()) for e in store.all_entries()}
    imported = skipped = 0
    for row in reader:
        body = (row.get("Entry") or "").strip()
        if not body:
            continue
        try:
            when = datetime.strptime(f"{row['Date'].strip()} {row['Time'].strip()}", "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        except (ValueError, KeyError):
            raise ImportProblem(f"Couldn't read the date “{row.get('Date')} {row.get('Time')}”.") from None
        created = when.isoformat()
        if (created[:16], body) in existing:
            skipped += 1
            continue

        title = (row.get("Title") or "").strip()
        title = None if title in ("", "No Title") else title
        quotes, references = _parse_sources(row.get("Sources") or row.get("Quote Sources") or "")
        insight = (row.get("Insight") or "").strip() or None
        status = (row.get("Reflection Status") or "").strip() or (store.DONE if insight else store.NOT_REQUESTED)
        if status == store.PENDING:
            status = store.NOT_REQUESTED  # nothing is running for an imported entry
        updated = created
        if (row.get("Last Updated") or "").strip():
            try:
                updated = datetime.strptime(row["Last Updated"].strip(), "%Y-%m-%d %H:%M").replace(tzinfo=TZ).isoformat()
            except ValueError:
                pass

        entry = {
            "id": store.new_id(),
            "created_at": created,
            "updated_at": updated,
            "title": title,
            "title_source": "user" if title else None,
            "prompt": (row.get("Writing Prompt") or "").strip() or None,
            "text": body,
            "word_count": store.word_count(body),
            "insight": insight,
            "question": (row.get("Question") or "").strip() or None,
            "quotes": quotes,
            "references": references,
            "reflection_status": status,
            "reflection_error": None,
            "favourite": (row.get("Favorite") or row.get("Favourite") or "").strip().lower() in ("yes", "★", "true"),
            "hidden": (row.get("Hidden") or "").strip().lower() in ("yes", "true"),
        }
        store.put_entry(entry)
        existing.add((created[:16], body))
        imported += 1
    return imported, skipped
