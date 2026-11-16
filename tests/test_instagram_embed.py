import os, tempfile, types, unittest
from tests import _paths  # noqa: F401
import scraper as sc
import account_check as ac
import main as m


class Dates(unittest.TestCase):
    def test_the_code_carries_the_day_it_was_posted(self):
        # five posts whose times are known from the page itself (probe runs of 16 Nov 2026)
        for code, day in (("DfwmF4ODZ6S", "2026-11-15"), ("DeOLJv9EtE8", "2026-10-08"), ("DfyDDFElYoG", "2026-11-16"),
                          ("DSUw_SKk7Eh", "2025-12-16"), ("DRI1cORk1aO", "2025-11-17")):
            self.assertEqual(sc.instagram_post_date(code), day)

    def test_not_a_code(self):
        for bad in ("", "abc", "!!bad!!", None):
            self.assertIsNone(sc.instagram_post_date(bad))


class OnePostOneRow(unittest.TestCase):
    def test_reel_and_p_addresses_are_the_same_post(self):
        forms = sc.instagram_known_forms("Cetaphilmy", "DfpHEmNvo74")
        for stored in ("https://www.instagram.com/cetaphilmy/reel/DfpHEmNvo74/", "https://www.instagram.com/p/DfpHEmNvo74/?utm_source=ig_embed",
                       "https://www.instagram.com/cetaphilmy/p/DfpHEmNvo74/"):
            self.assertIn(sc.canonical_url(stored), forms)
        self.assertNotIn(sc.canonical_url("https://www.instagram.com/cetaphilmy/p/Dflh_gyiHOd/"), forms)

    def test_the_scraper_knows_a_post_under_any_address(self):
        s = sc.Scraper({"run": {}, "platforms": {}}, tempfile.mkdtemp())
        s.known_urls = {sc.canonical_url("https://www.instagram.com/cetaphilmy/reel/DfpHEmNvo74/")}
        self.assertTrue(s._instagram_known("cetaphilmy", "https://www.instagram.com/cetaphilmy/p/DfpHEmNvo74/"))
        self.assertFalse(s._instagram_known("cetaphilmy", "https://www.instagram.com/cetaphilmy/p/Dflh_gyiHOd/"))

    def test_which_requests_are_a_post_address(self):
        self.assertTrue(sc._IG_POST_ADDRESS.match("https://www.instagram.com/p/DfpHEmNvo74/?utm_source=ig_embed&ig_rid=x"))
        self.assertFalse(sc._IG_POST_ADDRESS.match("https://www.instagram.com/p/DfpHEmNvo74/embed/captioned/"))   # the embed itself must load
        self.assertFalse(sc._IG_POST_ADDRESS.match("https://www.instagram.com/cetaphilmy/embed/"))


class Caption(unittest.TestCase):
    def test_owner_is_the_first_line(self):
        self.assertEqual(sc.parse_instagram_embed_caption("cetaphilmy\n\nReady for stronger skin?\n\nVisit us\nView all 12 comments"),
                         ("cetaphilmy", "Ready for stronger skin?\n\nVisit us"))
        self.assertEqual(sc.parse_instagram_embed_caption("syazanafiras\n\nPart 1\nView all comments")[0], "syazanafiras")
        self.assertEqual(sc.parse_instagram_embed_caption(""), ("", ""))


class WhenItIsUsed(unittest.TestCase):
    def mk(self, run=None):
        os.environ.pop("PW_STORAGE_STATE_B64", None); os.environ.pop("PW_STORAGE_STATE_PATH", None)
        return sc.Scraper({"run": run or {}, "platforms": {"Instagram": {"profile_url": "https://www.instagram.com/{handle}/", "post_link_patterns": ["/p/"]}}}, tempfile.mkdtemp())

    def test_no_saved_login_means_the_public_embed(self):
        s = self.mk(); seen = []
        s._scrape_instagram_embed = lambda browser, brand, handle, res: seen.append(handle) or res
        with self.assertLogs("kkm.scraper", level="WARNING"):
            s._scrape_target(None, "B", "Instagram", "h")
        self.assertEqual(seen, ["h"]); self.assertIn("Instagram", s.public_mode); self.assertEqual(s.session_lost, {})

    def test_login_only_setting_never_falls_back(self):
        s = self.mk({"instagram_source": "login"})
        s._scrape_instagram_embed = lambda *a, **k: self.fail("fell back although the setting says login")
        s._context = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("would open a browser"))
        with self.assertRaises(RuntimeError):
            s._scrape_target(None, "B", "Instagram", "h")


class Reporting(unittest.TestCase):
    def test_account_check_says_limited_with_the_reason(self):
        res = types.SimpleNamespace(error=None, public_view=True, signed_out_view=False, via_ads=False, renamed_to=None, drift_note=None)
        out = ac.classify(res, 6)
        self.assertEqual(out["state"], "limited"); self.assertIn("newest 6 post(s)", out["detail"]); self.assertIn("public embed", out["detail"])

    def test_sweep_stamp_carries_the_note(self):
        report = {"targets": [{"brand": "B", "platform": "Instagram", "posts": 6, "error": None, "truncated": False, "capped": False, "public": True, "opened": 2}]}
        ups = m.sweep_status_updates(report, [{"name": "B", "handles": {"Instagram": "h"}}])
        self.assertEqual(len(ups), 1); self.assertIn("no login", ups[0]["sweep_note"])


if __name__ == "__main__":
    unittest.main()
