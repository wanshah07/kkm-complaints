import unittest
from tests import _paths  # noqa: F401
import main as m

BRANDS = [{"name": "A", "handles": {"Instagram": "a"}}, {"name": "B", "handles": {"Instagram": "b"}}]


class LoginPromptIsNotASweep(unittest.TestCase):
    def test_login_prompt_and_signed_out_are_not_recorded(self):
        report = {"targets": [
            {"brand": "A", "platform": "Instagram", "posts": 0, "opened": 0,
             "error": "no post links found — page shows a login prompt; profile screenshot saved as EMPTY_a.png"},
            {"brand": "B", "platform": "Instagram", "posts": 0, "opened": 0,
             "error": "Instagram signed the saved login out (a login prompt on 3 profiles in a row) - not swept."}]}
        self.assertEqual(m.sweep_status_updates(report, BRANDS), [])

    def test_a_real_read_is_still_recorded(self):
        report = {"targets": [{"brand": "A", "platform": "Instagram", "posts": 40, "opened": 5, "error": ""}]}
        got = m.sweep_status_updates(report, BRANDS)
        self.assertEqual([(g["brand"], g["sweep_posts"]) for g in got], [("A", 40)])

    def test_profile_not_available_is_still_recorded(self):
        report = {"targets": [{"brand": "A", "platform": "Instagram", "posts": 0, "opened": 0,
                               "error": "no post links found — profile not available (handle wrong or region-blocked)"}]}
        self.assertEqual(len(m.sweep_status_updates(report, BRANDS)), 1)


if __name__ == "__main__":
    unittest.main()
