"""Hashtag review (24 Nov 2026): tag cleaning, the posting account read off the caption line, the official-account filter."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))

from ig_hashtag import clean_tags, is_official, owner_handle  # noqa: E402


class Tags(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(clean_tags("#Skintific, skintificmy  #skintific"), ["skintific", "skintificmy"])
        self.assertEqual(clean_tags(["#a1", "b_2"]), ["a1", "b_2"])
        self.assertEqual(clean_tags(" ,# "), [])


class Owner(unittest.TestCase):
    # the caption lines the probe (run 45368492881) really read off post pages
    def test_with_likes(self):
        t = '243 likes, 62 comments - alena.m.ugc on April 28, 2026: "The kind of routine I keep coming back to'
        self.assertEqual(owner_handle(t, "alenaugccontentcreatorlosangeles"), "alena.m.ugc")

    def test_without_likes(self):
        self.assertEqual(owner_handle('syhznr on December 6, 2025: "what is the point', ""), "syhznr")

    def test_25k(self):
        t = '25K likes, 619 comments - mahaliniraharja on January 26, 2026: "Back to my favorite'
        self.assertEqual(owner_handle(t, None), "mahaliniraharja")

    def test_falls_back_to_the_page(self):
        self.assertEqual(owner_handle("no caption line here", "@Someone"), "someone")
        self.assertEqual(owner_handle("", None), "")

    def test_a_date_inside_the_caption_is_not_the_owner(self):
        t = 'hello world: "posted on April 28, 2026 by me"'
        self.assertEqual(owner_handle(t, "real"), "real")


class Official(unittest.TestCase):
    def test_official_accounts_are_excluded(self):
        for h in ("skintificmalaysia", "skintific.my", "skintificofficial", "Skintific_ID"):
            self.assertTrue(is_official(h, ["skintific"]), h)

    def test_doctors_and_shops_are_kept(self):
        for h in ("dr_amandaelli", "frank.kks", "kochi.officialstore", ""):
            self.assertFalse(is_official(h, ["skintific"]), h)

    def test_no_words_excludes_nothing(self):
        self.assertFalse(is_official("skintific.my", []))


class Wiring(unittest.TestCase):
    def test_workflow_passes_the_inputs_and_scheduled_runs_have_none(self):
        wf = (ROOT / ".github" / "workflows" / "scraper.yml").read_text(encoding="utf-8")
        for name in ("IN_HASHTAG", "IN_EXCLUDE_OWNER"):
            m = re.search(rf"{name}: (.*)", wf).group(1)
            self.assertNotIn("schedule", m)
        self.assertIn('--hashtag "${H[@]}"', wf)
        self.assertIn('--exclude-owner "${X[@]}"', wf)

    def test_main_runs_the_mode_only_with_the_flag(self):
        main = (ROOT / "scraper" / "main.py").read_text(encoding="utf-8")
        self.assertIn("ht_mode = bool(args.hashtag)", main)
        self.assertIn("link_mode = tt_mode or ht_mode", main)


if __name__ == "__main__":
    unittest.main()
