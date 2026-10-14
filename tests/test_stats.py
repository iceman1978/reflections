"""Tests for streak counting. Run:  uv run python -m unittest discover tests"""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from journal.stats import compute, local_date  # noqa: E402

TODAY = date(2026, 10, 7)


def days_ago(*ns):
    return [TODAY - timedelta(days=n) for n in ns]


class StreakTests(unittest.TestCase):
    def test_no_entries(self):
        s = compute([], TODAY)
        self.assertEqual((s["current_streak"], s["longest_streak"], s["wrote_today"]), (0, 0, False))

    def test_wrote_today_only(self):
        s = compute(days_ago(0), TODAY)
        self.assertEqual((s["current_streak"], s["wrote_today"]), (1, True))

    def test_streak_including_today(self):
        self.assertEqual(compute(days_ago(0, 1, 2), TODAY)["current_streak"], 3)

    def test_yesterday_keeps_streak_alive_until_midnight(self):
        s = compute(days_ago(1, 2, 3), TODAY)
        self.assertEqual((s["current_streak"], s["wrote_today"]), (3, False))

    def test_full_missed_day_resets(self):
        self.assertEqual(compute(days_ago(2, 3, 4), TODAY)["current_streak"], 0)

    def test_several_entries_one_day_count_once(self):
        s = compute(days_ago(0, 0, 0, 1), TODAY)
        self.assertEqual((s["current_streak"], s["days_written"]), (2, 2))

    def test_gap_breaks_streak(self):
        self.assertEqual(compute(days_ago(0, 1, 3, 4, 5), TODAY)["current_streak"], 2)

    def test_longest_streak_in_the_past(self):
        s = compute(days_ago(0, 10, 11, 12, 13, 14), TODAY)
        self.assertEqual((s["current_streak"], s["longest_streak"]), (1, 5))

    def test_previous_best_leaves_out_the_current_streak(self):
        # "Best streak yet" means beating an earlier streak, so the current one isn't counted.
        self.assertEqual(compute(days_ago(0, 1, 2, 3), TODAY)["previous_best"], 0)          # first streak
        self.assertEqual(compute(days_ago(0, 1, 2, 10, 11), TODAY)["previous_best"], 2)     # beating 2 with 3
        self.assertEqual(compute(days_ago(0, 1, 10, 11, 12, 13), TODAY)["previous_best"], 4)
        self.assertEqual(compute(days_ago(1, 2, 3, 9, 10), TODAY)["previous_best"], 2)      # not written today yet
        self.assertEqual(compute(days_ago(5, 6, 9), TODAY)["previous_best"], 2)            # no current streak

    def test_streak_across_month_and_year(self):
        new_year = date(2027, 1, 1)
        dates = [date(2026, 12, 30), date(2026, 12, 31), new_year]
        self.assertEqual(compute(dates, new_year)["current_streak"], 3)

    def test_local_date_uses_toronto_time(self):
        # 11:30 pm in Toronto is already the next day in UTC; it must count as the 7th.
        self.assertEqual(local_date("2026-10-08T03:30:00+00:00"), date(2026, 10, 7))
        self.assertEqual(local_date("2026-10-07T23:30:00-04:00"), date(2026, 10, 7))


if __name__ == "__main__":
    unittest.main()


class MaterialEditTests(unittest.TestCase):
    """One reflection per entry, unless its text has changed materially since."""
    TEXT = ("I had a hard conversation with my brother today about our father's care. We both want what is best "
            "but we keep talking past each other, and I left feeling unheard and a little ashamed of my tone.")

    def test_fixing_typos_or_punctuation_is_not_material(self):
        from journal.store import materially_changed
        self.assertFalse(materially_changed(self.TEXT, self.TEXT.replace("today", "today,").replace("unheard", "unhaerd")))
        self.assertFalse(materially_changed(self.TEXT, self.TEXT.upper()))
        self.assertFalse(materially_changed(self.TEXT, self.TEXT))

    def test_adding_a_new_thought_is_material(self):
        from journal.store import materially_changed
        more = self.TEXT + (" Later he called back and apologised, and we agreed to visit Dad together on Sunday "
                            "and to ask the nurse our questions side by side.")
        self.assertTrue(materially_changed(self.TEXT, more))

    def test_rewriting_much_of_a_short_entry_is_material(self):
        from journal.store import materially_changed
        self.assertTrue(materially_changed("Tired today. Work was long.", "Tired today. Slept badly and argued with Sam."))
        self.assertFalse(materially_changed("Tired today. Work was long.", "Tired today. Work was so long."))
