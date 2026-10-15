"""How many tokens each call to Claude uses, to keep an eye on time and cost.

Every reflection, writing prompt and theme-tagging batch is logged with its
token counts (logs/journal.log), and added to a running total per day and kind
of call in the database (table `token_usage`). Counts only: never anything
anyone wrote. Admins see the averages in Settings → Admin → Usage.
"""
import logging
from datetime import datetime, timedelta

from . import db
from .config import TZ

log = logging.getLogger(__name__)
KINDS = {"reflection": "Reflection", "prompt": "Writing prompt", "themes": "Theme tagging", "passage": "Passage theme"}


def _counts(usage):
    """The numbers from a response's usage (thinking is included in the output)."""
    get = lambda name: int(getattr(usage, name, 0) or 0)   # noqa: E731
    return {
        "input": get("input_tokens"),
        "output": get("output_tokens"),
        "cache_read": get("cache_read_input_tokens"),
        "cache_write": get("cache_creation_input_tokens"),
    }


def record(kind, response):
    """Log one call's token use and add it to today's total. Never raises."""
    try:
        counts = _counts(getattr(response, "usage", None))
        log.info("Tokens for %s (%s): %d in, %d out, %d cache read, %d cache write", kind,
                 getattr(response, "model", "?"), counts["input"], counts["output"],
                 counts["cache_read"], counts["cache_write"])
        day = datetime.now(TZ).date().isoformat()
        with db.write_lock:
            con = db.connect()
            try:
                with con:
                    con.execute(
                        "INSERT INTO token_usage (day, kind, calls, input, output, cache_read, cache_write) "
                        "VALUES (?, ?, 1, ?, ?, ?, ?) ON CONFLICT (day, kind) DO UPDATE SET "
                        "calls = calls + 1, input = input + excluded.input, output = output + excluded.output, "
                        "cache_read = cache_read + excluded.cache_read, cache_write = cache_write + excluded.cache_write",
                        (day, kind, counts["input"], counts["output"], counts["cache_read"], counts["cache_write"]))
            finally:
                con.close()
    except Exception:
        log.exception("Couldn't record token use for %s", kind)


def summary(days=30):
    """Per kind of call over the last `days` days: calls and average tokens per call."""
    since = (datetime.now(TZ).date() - timedelta(days=days - 1)).isoformat()
    con = db.connect()
    try:
        rows = con.execute("SELECT kind, SUM(calls), SUM(input), SUM(output), SUM(cache_read) FROM token_usage "
                           "WHERE day >= ? GROUP BY kind", (since,)).fetchall()
    finally:
        con.close()
    out = []
    for kind, calls, inp, outp, cached in rows:
        out.append({"kind": kind, "label": KINDS.get(kind, kind), "calls": calls,
                    "avg_input": round((inp + cached) / calls), "avg_output": round(outp / calls),
                    "input": inp + cached, "output": outp})
    order = list(KINDS)
    return sorted(out, key=lambda r: order.index(r["kind"]) if r["kind"] in order else len(order))
