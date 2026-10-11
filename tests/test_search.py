"""Tests for journal search. Run:  uv run python -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from journal import search  # noqa: E402


def entry(id, text, title=None, insight=None, question=None, day="2026-10-01"):
    return {"id": id, "text": text, "title": title, "insight": insight, "question": question,
            "created_at": f"{day}T09:00:00-04:00"}


ENTRIES = [
    entry("a", "I keep trying to forgive my brother for what he said at dinner.", title="Dinner with family"),
    entry("b", "Work was long. My manager praised the report.", insight="Paul wrote about forgiveness in Ephesians.",
          day="2026-10-02"),
    entry("c", "Anxious about the move. So many boxes.", title="Moving day", question="What are you afraid to leave behind?",
          day="2026-10-03"),
    entry("d", "A quiet morning; I prayed before work.", title="Morning prayer", day="2026-10-04"),
]


def ids(query):
    return [r["id"] for r in search.search(ENTRIES, query)]


class SearchTests(unittest.TestCase):
    def test_word_endings_dont_matter(self):
        self.assertEqual(sorted(ids("forgiveness")), ["a", "b"])
        self.assertEqual(ids("praying"), ["d"])

    def test_own_words_rank_above_the_reflection(self):
        self.assertEqual(ids("forgive"), ["a", "b"])        # a: in the entry; b: only in the reflection

    def test_every_word_must_match(self):
        self.assertEqual(ids("forgive brother"), ["a"])
        self.assertEqual(ids("forgive boxes"), [])

    def test_half_typed_last_word(self):
        self.assertEqual(ids("anxi"), ["c"])

    def test_one_letter_slip(self):
        self.assertEqual(ids("brothr"), ["a"])
        self.assertEqual(ids("manger"), ["b"])               # manager, one letter missing

    def test_quoted_phrase(self):
        self.assertEqual(ids('"my brother"'), ["a"])
        self.assertEqual(ids('"brother my"'), [])

    def test_titles_and_questions_are_searched(self):
        self.assertEqual(ids("moving"), ["c"])
        self.assertEqual(ids("afraid"), ["c"])

    def test_snippet_highlights_the_match(self):
        r = search.search(ENTRIES, "boxes")[0]
        self.assertEqual(r["field"], "entry")
        s, e = r["marks"][0]
        self.assertEqual(r["snippet"][s:e], "boxes")

    def test_reflection_match_says_so(self):
        r = next(r for r in search.search(ENTRIES, "ephesians"))
        self.assertEqual((r["id"], r["field"]), ("b", "reflection"))

    def test_accents_and_curly_quotes(self):
        es = [entry("x", "Café with Augustine’s Confessions")]
        self.assertEqual([r["id"] for r in search.search(es, "cafe augustine's")], ["x"])

    def test_too_short_finds_nothing(self):
        self.assertEqual(ids(""), [])


if __name__ == "__main__":
    unittest.main()
