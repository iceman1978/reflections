"""Tests for the wipe confirmation. Run:  uv run python -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from journal.wipe import answer_matches, new_challenge  # noqa: E402


class WipeAnswerTests(unittest.TestCase):
    def test_exact(self):
        self.assertTrue(answer_matches("352", "three five two"))

    def test_case_spacing_and_punctuation_dont_matter(self):
        for answer in ["Three Five Two", "  three   five two ", "three-five-two", "three, five, two"]:
            self.assertTrue(answer_matches("352", answer), answer)

    def test_zero(self):
        self.assertTrue(answer_matches("907", "nine zero seven"))

    def test_wrong_answers(self):
        for answer in ["", "352", "three five", "three five two one", "two five three",
                       "three hundred fifty two", "thre five two", "three five too"]:
            self.assertFalse(answer_matches("352", answer), answer)

    def test_challenge_is_three_digits(self):
        for _ in range(50):
            number = new_challenge()["number"]
            self.assertRegex(number, r"^[1-9][0-9]{2}$")


if __name__ == "__main__":
    unittest.main()
