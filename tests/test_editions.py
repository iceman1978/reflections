"""Tests for editions: both edition files, and In His Steps' library (Bible verses,
CCEL and Gutenberg chapters). Run with the other tests:

    uv run python -m unittest discover tests
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from journal import sources  # noqa: E402

EDITIONS = ROOT / "editions"


class EditionFileTests(unittest.TestCase):
    def test_every_edition_is_complete(self):
        for folder in EDITIONS.iterdir():
            with self.subTest(edition=folder.name):
                ed = json.loads((folder / "edition.json").read_text(encoding="utf-8"))
                for key in ("app_name", "description", "lead", "points", "invite_line", "voices"):
                    self.assertTrue(ed.get(key), key)
                self.assertTrue((folder / "prompts" / "reflection.md").exists())
                self.assertTrue((folder / "prompts" / "writing_prompt.md").exists())
                self.assertTrue((ROOT / "static" / "brands" / folder.name / "favicon.ico").exists())
                works = json.loads((folder / "sources.json").read_text(encoding="utf-8"))["works"]
                missing = [w for w in works if not (sources.TEXTS_DIR / f"{w}.json").exists()]
                self.assertEqual(missing, [], "run tools/build_sources.py for this edition")


    def test_sources_page_lists_every_work_once(self):
        for folder in EDITIONS.iterdir():
            with self.subTest(edition=folder.name):
                ed = json.loads((folder / "edition.json").read_text(encoding="utf-8"))
                works = json.loads((folder / "sources.json").read_text(encoding="utf-8"))["works"]
                listed = [w for g in ed.get("source_groups", []) for w in g["works"]]
                self.assertEqual(sorted(listed), sorted(works), "every work in exactly one group on the Sources page")


class QuietHourLibraryTests(unittest.TestCase):
    """Uses In His Steps' library whichever edition is running."""

    def setUp(self):
        self._saved = sources._cache["map"]
        sources._cache["map"] = json.loads((EDITIONS / "christian" / "sources.json").read_text(encoding="utf-8"))

    def tearDown(self):
        sources._cache["map"] = self._saved

    def test_bible_quote_is_cited_by_chapter_and_verse(self):
        c = sources.cite("romans", "8:28", "And we know that God works all things together for the good "
                                            "of those who love Him")
        self.assertEqual((c["author"], c["title"], c["location"]), ("", "Romans", "8:28"))
        self.assertEqual(c["url"], "https://biblehub.com/bsb/romans/8.htm")

    def test_psalms_are_cited_as_psalm(self):
        c = sources.cite("psalms", "23:1", "The LORD is my shepherd; I shall not want.")
        self.assertEqual((c["title"], c["location"]), ("Psalm", "23:1"))

    def test_drifting_wording_does_not_run_into_the_next_verse(self):
        # "to each" isn't in the BSB's 12:3, but "each" is in 12:4 ("Just as each of us...").
        c = sources.cite("romans", "12:3", "Do not think of yourself more highly than you ought, but think of "
                                           "yourself with sober judgment, in accordance with the measure of faith "
                                           "that God has assigned to each.")
        self.assertEqual(c["location"], "12:3")
        self.assertTrue(c["quote"].endswith("according to the measure of faith God has given you."), c["quote"])

    def test_other_translation_wording_shows_the_bsb_verse(self):
        # The ESV's wording of Ecclesiastes 11:4; the BSB says "watches... fail to sow".
        c = sources.cite("ecclesiastes", "11:4", "He who observes the wind will not sow, and he who regards "
                                                 "the clouds will not reap.")
        self.assertEqual(c["location"], "11:4")
        self.assertEqual(c["quote"], "He who watches the wind will fail to sow, and he who observes the clouds "
                                     "will fail to reap.")

    def test_bsb_lookup_spans_verses_and_drops_superscriptions(self):
        c = sources.cite("psalms", "23:1", "The Lord is my shepherd, I lack nothing.")
        self.assertEqual(c["quote"], "The LORD is my shepherd; I shall not want.")   # not "A Psalm of David."
        c = sources.cite("proverbs", "3:5-6", "Trust in the LORD with all your heart and lean not on your own "
                                              "understanding; in all your ways submit to him, and he will make "
                                              "your paths straight.")
        self.assertEqual(c["location"], "3:5-6")

    def test_wrong_reference_is_not_rescued(self):
        # Philippians 4:6 in the NIV's words, but given the wrong verse: paraphrase, never the wrong verse.
        self.assertIsNone(sources.cite("psalms", "23:1", "Do not be anxious about anything, but in every "
                                                         "situation, by prayer and petition, present your requests"))

    def test_source_quotation_marks_are_not_doubled(self):
        c = sources.cite("psalms", "46:10", "Be still and know that I am God")
        self.assertFalse(c["quote"].startswith("“"))

    def test_wrong_bible_wording_is_not_verified(self):
        # The ESV/NIV wording differs from the BSB, so it's paraphrased instead.
        self.assertIsNone(sources.cite("philippians", "", "do not be anxious about anything, but in "
                                                          "everything by prayer and supplication"))

    def test_bible_reference_links_to_its_chapter(self):
        r = sources.reference("james", "1:22", "Doers of the word")
        self.assertEqual(r["url"], "https://biblehub.com/bsb/james/1.htm")
        self.assertEqual(r["location"], "1:22")

    def test_ccel_quote_is_cited_by_chapter_name(self):
        c = sources.cite("lawrence_practice", "", "that we might accustom ourselves to a continual "
                                                  "conversation with Him, with freedom and in simplicity")
        self.assertEqual(c["location"], "Fourth Conversation")
        self.assertEqual(c["url"], "https://ccel.org/ccel/lawrence/practice/practice.iii.iv.html")

    def test_ccel_reference_matches_book_and_chapter_in_order(self):
        r = sources.reference("kempis_imitation", "Book 1, Chapter 3", "")
        self.assertTrue(r["url"].endswith("imitation.ONE.3.html"), r["url"])
        self.assertEqual(r["location"], "Book 1, Chapter 3")
        r = sources.reference("kempis_imitation", "Book 3, Chapter 1", "")
        self.assertTrue(r["url"].endswith("imitation.THREE.1.html"), r["url"])

    def test_letters_and_conversations_are_told_apart(self):
        self.assertTrue(sources.reference("lawrence_practice", "Third Letter", "")["url"].endswith("practice.iv.iii.html"))
        self.assertTrue(sources.reference("lawrence_practice", "Letter 3", "")["url"].endswith("practice.iv.iii.html"))
        self.assertTrue(sources.reference("lawrence_practice", "Third Conversation", "")["url"].endswith("practice.iii.iii.html"))

    def test_gutenberg_chapter_by_numeral(self):
        r = sources.reference("tozer_pursuit", "Chapter II", "")
        self.assertIn("#II_The_Blessedness_of_Possessing_Nothing", r["url"])
        self.assertEqual(r["location"], "II. The Blessedness of Possessing Nothing")

    def test_unknown_location_falls_back_to_the_work(self):
        r = sources.reference("owen_mortification", "somewhere in the middle", "")
        self.assertEqual((r["url"], r["location"]), ("https://ccel.org/ccel/owen/mort", ""))


class LabelTokenTests(unittest.TestCase):
    def test_numbers_in_any_form(self):
        self.assertEqual(sources.label_tokens("Third Letter"), ["3", "letter"])
        self.assertEqual(sources.label_tokens("IV. The Fight", drop_generic=True), ["4", "fight"])
        self.assertEqual(sources.label_tokens("Book ONE. Thoughts")[:2], ["book", "1"])
        self.assertEqual(sources.label_tokens("Part I, The First Stage", drop_generic=True), ["part", "1", "1", "stage"])

    def test_the_pronoun_i_is_not_a_numeral(self):
        self.assertEqual(sources.label_tokens("What I Learned"), ["what", "i", "learned"])


if __name__ == "__main__":
    unittest.main()
