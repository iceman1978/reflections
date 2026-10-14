"""Tests for a reader's library: choosing which of the edition's writers
reflections draw on. Run:  uv run python -m unittest discover tests"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from journal import config, reflection, sources  # noqa: E402
from journal.library import MIN_WRITERS, WRITERS, Library, for_settings  # noqa: E402

EDITIONS = ROOT / "editions"
# A writer who is on by default and has quotable works in this edition.
QUOTED = next(w["name"] for w in WRITERS
              if w.get("default", True) and any(work["author"] == w["name"] for work in sources.works().values()))


def works_by(name):
    return {wid for wid, w in sources.works().items() if w["author"] == name}


class EditionWriterTests(unittest.TestCase):
    """Every edition's writers line up with its voices, works and instructions."""

    def test_writers_cover_the_voices_and_the_works(self):
        for folder in EDITIONS.iterdir():
            with self.subTest(edition=folder.name):
                ed = json.loads((folder / "edition.json").read_text(encoding="utf-8"))
                writers = ed["writers"]
                names = [w["name"] for w in writers]
                self.assertEqual(len(names), len(set(names)), "a writer is listed twice")
                voices = [v for w in writers for v in w.get("voices", [w["name"]])]
                self.assertEqual(sorted(voices), sorted(ed["voices"]))
                authors = {w["author"] for w in json.loads((folder / "sources.json").read_text(encoding="utf-8"))["works"].values()}
                self.assertEqual(authors - {""} - set(names), set(), "a quotable work's author isn't among the writers")
                for w in writers:
                    self.assertTrue(w["era"] and w["guide"], w["name"])
                    if w["name"] not in authors:
                        self.assertTrue(w.get("works"), f"{w['name']} has no quotable work, so needs its books named")
                eras = [w["era"] for w in writers]
                self.assertEqual(len([e for i, e in enumerate(eras) if i == 0 or eras[i - 1] != e]), len(set(eras)),
                                 "an era's writers should be listed together")
                self.assertGreaterEqual(sum(w.get("default", True) for w in writers), MIN_WRITERS)

    def test_the_instructions_take_their_writers_from_the_data(self):
        for folder in EDITIONS.iterdir():
            with self.subTest(edition=folder.name):
                text = (folder / "prompts" / "reflection.md").read_text(encoding="utf-8")
                self.assertEqual(text.count("{{writers}}"), 1)
                self.assertNotIn("  - **", text, "a writer's notes belong in edition.json, where they can be switched off")


class LibraryTests(unittest.TestCase):
    def test_by_default_only_the_writers_marked_off_are_off(self):
        self.assertEqual([w["name"] for w in Library().off], [w["name"] for w in WRITERS if not w.get("default", True)])
        self.assertEqual(Library(None).voices(), Library({}).voices())

    def test_switching_a_writer_off_removes_everything_about_them(self):
        lib = Library({QUOTED: False})
        self.assertNotIn(QUOTED, lib.voices())
        self.assertFalse(works_by(QUOTED) & set(lib.works()))
        self.assertNotIn(f"**{QUOTED}:**", lib.guide())
        self.assertIn(QUOTED, lib.off_note())
        schema = reflection._schema(lib)
        self.assertFalse(works_by(QUOTED) & set(schema["properties"]["quotes"]["items"]["properties"]["work"]["enum"]))
        self.assertNotIn(QUOTED, schema["properties"]["thinkers"]["items"]["enum"])
        prompt = reflection._system_prompt(lib)
        self.assertNotIn("{{", prompt)
        for work_id in works_by(QUOTED):
            self.assertNotIn(f'"{work_id}"', prompt)
        self.assertIn("does not include", prompt)

    def test_works_with_no_author_are_always_included(self):
        everyone_off = Library({w["name"]: False for w in WRITERS})
        self.assertEqual(set(everyone_off.works()), {wid for wid, w in sources.works().items() if not w["author"]})

    def test_a_full_library_says_nothing_about_missing_writers(self):
        self.assertIsNone(Library({w["name"]: True for w in WRITERS}).off_note())

    def test_settings_lists_every_writer_once_by_era(self):
        eras = for_settings({QUOTED: False})
        listed = [w for era in eras for w in era["writers"]]
        self.assertEqual([w["name"] for w in listed], [w["name"] for w in WRITERS])
        self.assertFalse(next(w for w in listed if w["name"] == QUOTED)["on"])
        self.assertTrue(all(w["about"] for w in listed))


class ChoiceTests(unittest.TestCase):
    def test_only_differences_from_the_defaults_are_kept(self):
        defaults = {w["name"]: w.get("default", True) for w in WRITERS}
        saved = config.validate({"writers": {**defaults, QUOTED: False, "Nobody at all": False}})["writers"]
        self.assertEqual(saved, {QUOTED: False})

    def test_at_least_a_few_writers_must_stay_on(self):
        with self.assertRaises(ValueError):
            config.validate({"writers": {w["name"]: False for w in WRITERS}})
        keep = [w["name"] for w in WRITERS][:MIN_WRITERS]
        saved = config.validate({"writers": {w["name"]: w["name"] in keep for w in WRITERS}})["writers"]
        self.assertEqual(len(Library(saved).writers), MIN_WRITERS)

    def test_anything_but_a_list_of_choices_is_refused(self):
        with self.assertRaises(ValueError):
            config.validate({"writers": "Augustine"})


if __name__ == "__main__":
    unittest.main()
