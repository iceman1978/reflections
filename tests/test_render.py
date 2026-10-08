"""Tests for turning Claude's reflection into display text with checked quotes.
Run:  uv run python -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from journal.reflection import render_insight, resolve_references  # noqa: E402

SENECA_PARAPHRASE = ("Seneca observed that people guard their property carefully yet squander their time, "
                     "the one thing it is right to be stingy with")


class RenderTests(unittest.TestCase):
    def test_unverified_quote_replaces_whole_sentence(self):
        insight = ("You've made the diagnosis already. In On the Shortness of Life he wrote: [[q1]] "
                   "He wasn't scolding people for laziness.")
        quotes = [{"marker": "q1", "work": "shortness_of_life", "location": "3",
                   "quote": "made-up words that are not in Basore at all, whatsoever",
                   "paraphrase": SENECA_PARAPHRASE}]
        text, cites, unverified = render_insight(insight, quotes)
        self.assertEqual(text, "You've made the diagnosis already. " + SENECA_PARAPHRASE + ". "
                               "He wasn't scolding people for laziness.")
        self.assertEqual(cites, [])
        self.assertEqual(unverified[0]["work"], "shortness_of_life")

    def test_verified_quote_shows_source_wording(self):
        insight = "Marcus Aurelius put it plainly: [[q1]] That is the point."
        quotes = [{"marker": "q1", "work": "meditations", "location": "8.47",
                   "quote": "If thou art pained by any external thing, it is not this thing that disturbs thee, "
                            "but thy own judgement about it",
                   "paraphrase": "Marcus Aurelius held that our pain comes from our judgement."}]
        text, cites, unverified = render_insight(insight, quotes)
        self.assertIn("“If thou art pained by any external thing", text)
        self.assertEqual(cites[0]["location"], "8.47")
        self.assertEqual(unverified, [])

    def test_two_markers_and_a_missing_one(self):
        insight = "First: [[q1]] Then something else. Second: [[q2]] Done. Stray [[q9]] marker."
        quotes = [
            {"marker": "q1", "work": "letters", "location": "13", "quote": "not real words at all here today",
             "paraphrase": "Seneca warned that we suffer more in imagination than in reality."},
            {"marker": "q2", "work": "enchiridion", "location": "1",
             "quote": "Of things some are in our power, and others are not.", "paraphrase": "Epictetus divided things."},
        ]
        text, cites, unverified = render_insight(insight, quotes)
        self.assertTrue(text.startswith("Seneca warned that we suffer more in imagination than in reality. "
                                        "Then something else."))
        self.assertIn("Second: “Of things some are in our power, and others are not.” Done.", text)
        self.assertNotIn("[[", text)
        self.assertEqual([c["work"] for c in cites], ["enchiridion"])

    def test_references_become_links_without_duplicates(self):
        cites = [{"work": "meditations", "location": "8.47"}]
        refs = resolve_references([
            {"work": "shortness_of_life", "location": "3", "idea": "time is the scarcest thing"},
            {"work": "shortness_of_life", "location": "3", "idea": "same again"},
            {"work": "meditations", "location": "8.47", "idea": "already quoted"},
            {"work": "letters", "location": "999", "idea": "letter that doesn't exist"},
        ], cites)
        self.assertEqual(len(refs), 2)
        self.assertTrue(refs[0]["url"].endswith("On_the_shortness_of_life/Chapter_III"))
        self.assertEqual(refs[1]["location"], "")               # no such letter: no location shown...
        self.assertTrue(refs[1]["url"].endswith("Moral_letters_to_Lucilius"))  # ...and a link to the work


if __name__ == "__main__":
    unittest.main()
