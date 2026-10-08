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
