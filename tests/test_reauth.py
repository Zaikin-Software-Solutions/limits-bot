"""Ручная дата обновления логина Claude и напоминание за N дней."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from app.reauth import (
    format_status,
    login_reminder,
    login_status,
    parse_date,
    set_last_login,
    today_msk,
)

TODAY = date(2026, 9, 30)


class ParseTests(unittest.TestCase):
    def test_accepted_formats(self):
        for text in ("25.09.2026", "2026-09-25", "25/09/2026", "25.09.26", " 25.09 "):
            self.assertEqual(parse_date(text, TODAY), date(2026, 9, 25), text)

    def test_words(self):
        self.assertEqual(parse_date("сегодня", TODAY), TODAY)
        self.assertEqual(parse_date("Вчера", TODAY), date(2026, 9, 29))

    def test_day_month_without_year_never_lands_in_future(self):
        self.assertEqual(parse_date("15.12", TODAY), date(2025, 12, 15))

    def test_future_and_garbage_rejected(self):
        for text in ("01.10.2026", "abc", "31.02.2026", ""):
            with self.assertRaises(ValueError, msg=text):
                parse_date(text, TODAY)

    def test_moscow_date_near_utc_midnight(self):
        # 22:30 UTC 29 сентября = 01:30 МСК 30 сентября
        now = datetime(2026, 9, 29, 22, 30, tzinfo=timezone.utc)
        self.assertEqual(today_msk(now), date(2026, 9, 30))


class ReminderTests(unittest.TestCase):
    def _login(self, last: str) -> dict:
        login: dict = {}
        set_last_login(login, date.fromisoformat(last))
        return login

    def test_status_counts_days_to_due(self):
        status = login_status(self._login("2026-09-25"), TODAY, 30)
        self.assertEqual(status.due, date(2026, 10, 25))
        self.assertEqual(status.days_left, 25)

    def test_no_date_means_no_reminder_and_hint(self):
        self.assertIsNone(login_reminder({}, TODAY, 30, 2))
        self.assertIn("/token", format_status(None))

    def test_silent_before_warn_window(self):
        self.assertIsNone(login_reminder(self._login("2026-09-10"), TODAY, 30, 2))  # осталось 10 дн.

    def test_corrupt_stored_value_is_ignored(self):
        self.assertIsNone(login_reminder({"last_login": "не дата"}, TODAY, 30, 2))
        self.assertIsNone(login_status({"last_login": 5}, TODAY, 30))

    def test_warns_two_days_before_once(self):
        login = self._login("2026-09-02")  # срок 02.10, осталось 2 дня
        text = login_reminder(login, TODAY, 30, 2)
        self.assertIn("02.10.2026", text)
        self.assertIsNone(login_reminder(login, TODAY, 30, 2))

    def test_overdue_alert_follows_warning_once(self):
        login = self._login("2026-09-02")
        login_reminder(login, TODAY, 30, 2)
        later = date(2026, 10, 4)
        self.assertIn("просрочен", login_reminder(login, later, 30, 2))
        self.assertIsNone(login_reminder(login, later, 30, 2))

    def test_new_date_rearms_reminder(self):
        login = self._login("2026-09-02")
        login_reminder(login, TODAY, 30, 2)
        set_last_login(login, date(2026, 8, 31))  # срок 30.09, сегодня
        self.assertIn("сегодня", login_reminder(login, TODAY, 30, 2))

    def test_backdated_entry_is_supported(self):
        status = login_status(self._login("2026-08-01"), TODAY, 30)
        self.assertLess(status.days_left, 0)
        self.assertIn("просрочено", format_status(status))


if __name__ == "__main__":
    unittest.main()
