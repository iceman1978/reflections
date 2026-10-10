"""Export the journal as a CSV file that opens directly in Excel.

This is the one place the journal leaves the encrypted database in readable
form, so it only happens when asked for (Settings → Export).
"""
import csv
import io
from datetime import datetime

from . import store
from .config import TZ

COLUMNS = ["Date", "Time", "Title", "Writing Prompt", "Entry", "Word Count", "Insight", "Question",
           "Sources", "Reflection Status", "Last Updated", "Favorite", "Hidden"]


def _local(iso):
    return datetime.fromisoformat(iso).astimezone(TZ)


def format_sources(quotes, references):
    """Quoted passages first, then works whose ideas were paraphrased."""
    lines = []
    for q, kind in [(q, "quoted") for q in quotes or []] + [(r, "idea") for r in references or []]:
        cite = f"— {q.get('author', '')}, {q.get('title', '')} {q.get('location', '')}".strip()
        cite += f" ({kind})"
        if q.get("url"):
            cite += f" {q['url']}"
        lines.append(cite)
    return "\n".join(lines)


def journal_csv():
    """The whole journal (hidden entries included, marked) as CSV text."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")  # Excel-style line endings
    writer.writerow(COLUMNS)
    for e in store.all_entries():
        created = _local(e["created_at"])
        writer.writerow([
            f"{created:%Y-%m-%d}",
            f"{created:%H:%M}",
            e.get("title") or "No Title",
            e.get("prompt") or "",
            e.get("text") or "",
            e.get("word_count", 0),
            e.get("insight") or "",
            e.get("question") or "",
            format_sources(e.get("quotes"), e.get("references")),
            e.get("reflection_status") or "",
            f"{_local(e['updated_at']):%Y-%m-%d %H:%M}",
            "Yes" if e.get("favourite") else "",
            "Yes" if e.get("hidden") else "",
        ])
    # The byte-order mark tells Excel the file is UTF-8, so accents and
    # curly quotes show correctly.
    return "﻿" + out.getvalue()
