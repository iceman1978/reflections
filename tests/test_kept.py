"""Tests for kept passages (quotations starred in reflections).
Run:  uv run python -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from journal import kept  # noqa: E402


def cite(work, author, title, location, quote):
    return {"work": work, "author": author, "title": title, "translation": "t", "location": location,
            "quote": quote, "url": f"https://example.org/{work}"}


SENECA_A = cite("seneca_letters", "Seneca", "Moral Letters", "1", "Hold every hour in your grasp.")
SENECA_B = cite("seneca_shortness", "Seneca", "On the Shortness of Life", "1", "Life is long if you know how to use it.")
MARCUS = cite("meditations", "Marcus Aurelius", "Meditations", "4.3", "Look within.")
PSALM = cite("psalms", "", "Psalm", "23:1", "The LORD is my shepherd; I shall not want.")
ROMANS = cite("romans", "", "Romans", "8:28", "And we know that God works all things together for good.")


def stored(citation, kept_at):
    return {**citation, "key": kept.key_of(citation), "kept_at": kept_at}


def entry(id, day, *quotes, title=None):
    return {"id": id, "title": title, "created_at": f"2026-10-{day:02d}T09:00:00-04:00", "quotes": list(quotes)}


class KeyTests(unittest.TestCase):
    def test_same_words_from_the_same_place_are_one_passage(self):
        self.assertEqual(kept.key_of(MARCUS), kept.key_of(dict(MARCUS, url="elsewhere", kept=True)))

    def test_different_words_or_places_are_different_passages(self):
        self.assertNotEqual(kept.key_of(MARCUS), kept.key_of(dict(MARCUS, quote="Look within. Within is the fountain.")))
        self.assertNotEqual(kept.key_of(MARCUS), kept.key_of(dict(MARCUS, location="7.59")))

    def test_slug(self):
        self.assertEqual(kept.slug("Marcus Aurelius"), "marcus-aurelius")
        self.assertEqual(kept.slug("Søren Kierkegaard"), "s-ren-kierkegaard")


class GroupingTests(unittest.TestCase):
    def test_most_kept_writer_first(self):
        groups = kept.grouped([stored(MARCUS, "1"), stored(SENECA_A, "2"), stored(SENECA_B, "3")], [])
        self.assertEqual([(g["name"], g["count"]) for g in groups], [("Seneca", 2), ("Marcus Aurelius", 1)])
        self.assertEqual(groups[0]["anchor"], "seneca")

    def test_a_writers_works_are_sections_in_library_order(self):
        groups = kept.grouped([stored(SENECA_B, "1"), stored(SENECA_A, "2")], [],
                              work_order=["seneca_letters", "seneca_shortness"])
        self.assertEqual([s["name"] for s in groups[0]["sections"]], ["Moral Letters", "On the Shortness of Life"])

    def test_scripture_comes_first_grouped_by_book(self):
        groups = kept.grouped([stored(SENECA_A, "1"), stored(SENECA_B, "2"), stored(MARCUS, "3"),
                               stored(ROMANS, "4"), stored(PSALM, "5")], [], work_order=["psalms", "romans"])
        self.assertEqual(groups[0]["name"], kept.SCRIPTURE)          # first, though Seneca has as many
        self.assertEqual([s["name"] for s in groups[0]["sections"]], ["Psalm", "Romans"])
        self.assertEqual(groups[0]["anchor"], "")

    def test_newest_kept_first_within_a_work(self):
        later = dict(MARCUS, location="7.59", quote="Dig within.")
        groups = kept.grouped([stored(MARCUS, "2026-10-01"), stored(later, "2026-10-05")], [])
        self.assertEqual([p["location"] for p in groups[0]["sections"][0]["passages"]], ["7.59", "4.3"])

    def test_each_passage_lists_every_entry_that_quoted_it_newest_first(self):
        entries = [entry("a", 1, MARCUS, title="First"), entry("b", 5, MARCUS, SENECA_A), entry("c", 3, PSALM)]
        groups = kept.grouped([stored(MARCUS, "1")], entries)
        passage = groups[0]["sections"][0]["passages"][0]
        self.assertEqual([e["id"] for e in passage["entries"]], ["b", "a"])
        self.assertEqual(passage["entries"][1]["title"], "First")

    def test_a_passage_outlives_its_entries(self):
        groups = kept.grouped([stored(MARCUS, "1")], [])
        self.assertEqual(groups[0]["sections"][0]["passages"][0]["entries"], [])


if __name__ == "__main__":
    unittest.main()
