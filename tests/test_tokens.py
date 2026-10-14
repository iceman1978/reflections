"""Tests for token logging. Needs a throwaway JOURNAL_DATA_DIR, like the vault tests:

    JOURNAL_DATA_DIR=<empty folder> uv run python -m unittest discover tests
"""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if not os.environ.get("JOURNAL_DATA_DIR"):
    raise unittest.SkipTest("Set JOURNAL_DATA_DIR to a throwaway folder to run the token tests.")

from journal import db, tokens  # noqa: E402


def response(inp, out, cache_read=0):
    return SimpleNamespace(model="claude-test", usage=SimpleNamespace(
        input_tokens=inp, output_tokens=out, cache_read_input_tokens=cache_read, cache_creation_input_tokens=0))


class TokenTests(unittest.TestCase):
    def setUp(self):
        con = db.connect()
        with con:
            con.execute("DELETE FROM token_usage")
        con.close()

    def test_calls_add_up_per_kind_and_average(self):
        tokens.record("reflection", response(10000, 900))
        tokens.record("reflection", response(8000, 700, cache_read=2000))
        tokens.record("prompt", response(3000, 100))
        by = {r["kind"]: r for r in tokens.summary()}
        self.assertEqual(by["reflection"]["calls"], 2)
        self.assertEqual(by["reflection"]["avg_input"], 10000)      # cached input counts as input
        self.assertEqual(by["reflection"]["avg_output"], 800)
        self.assertEqual(by["prompt"]["avg_input"], 3000)
        self.assertEqual([r["kind"] for r in tokens.summary()], ["reflection", "prompt"])

    def test_a_response_without_usage_never_breaks_a_reflection(self):
        tokens.record("reflection", object())                       # no exception
        self.assertEqual(tokens.summary()[0]["avg_input"], 0)


if __name__ == "__main__":
    unittest.main()
