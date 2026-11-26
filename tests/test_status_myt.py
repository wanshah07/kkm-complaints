import unittest
from tests import _paths  # noqa: F401
import platform_status as ps


class StatusTimesAreMalaysiaTime(unittest.TestCase):
    def test_utc_becomes_myt_and_says_so(self):
        self.assertEqual(ps.myt("2026-11-26T12:52:36Z"), "2026-11-26 20:52 MYT")
        self.assertEqual(ps.myt("2026-11-26T17:30:00Z"), "2026-11-27 01:30 MYT")        # across midnight
        self.assertEqual(ps.myt("2026-11-26T12:52:36+00:00"), "2026-11-26 20:52 MYT")

    def test_unreadable_input_is_not_a_crash(self):
        self.assertEqual(ps.myt(""), "")
        self.assertEqual(ps.myt("junk"), "junk")
        self.assertEqual(ps.myt(None), "")


if __name__ == "__main__":
    unittest.main()
