import types, unittest
from tests import _paths  # noqa: F401
import scraper as sc
import account_check as ac

CARD = ("​\nActive\nLibrary ID: 28706402602391891\nStarted running on Nov 6, 2026\nPlatforms\n​\n​\n"
        "This ad has multiple versions\n​\nOpen Dropdown\n​\nSee ad details\nCetaphil\nSponsored\n"
        "Your skin has different needs during the day and night!\n\nFormulated with Gallic-AOX Power.\n0:00 / 0:15\n"
        "SHOPEE.COM.MY\nDay Protection, Night Repair\nShop Now")


class AdCard(unittest.TestCase):
    def test_a_real_card(self):
        c = sc.parse_ad_card(CARD)
        self.assertEqual((c["id"], c["started"], c["advertiser"], c["active"]), ("28706402602391891", "2026-11-06", "Cetaphil", True))
        self.assertTrue(c["body"].startswith("Your skin has different needs"))
        self.assertIn("Day Protection, Night Repair", c["body"])          # the headline is part of the advertisement
        for furniture in ("Library ID", "Open Dropdown", "See ad details", "0:00 / 0:15", "Sponsored", "Platforms", "multiple versions"):
            self.assertNotIn(furniture, c["body"])

    def test_day_first_date_and_long_month(self):
        self.assertEqual(sc.parse_ad_card("Library ID: 1\nStarted running on 6 Nov 2026\nX\nSponsored\nhi")["started"], "2026-11-06")
        self.assertEqual(sc.parse_ad_card("Library ID: 1\nStarted running on September 21, 2025\nX\nSponsored\nhi")["started"], "2025-09-21")

    def test_card_without_words_or_date(self):
        c = sc.parse_ad_card("Active\nLibrary ID: 77\nPlatforms\nSee ad details\nBrand X\nSponsored\n0:00 / 0:30")
        self.assertEqual((c["id"], c["started"], c["advertiser"], c["body"]), ("77", None, "Brand X", ""))
        self.assertEqual(sc.parse_ad_card("nothing here")["id"], "")

    def test_library_notices_are_not_the_advertisement(self):
        c = sc.parse_ad_card("Library ID: 5\nB\nSponsored\nRepair starts with the skin barrier.\nLow impression count\nSorry, we're having trouble playing this video.\n1-Week Repair\nShop Now")
        self.assertEqual(c["body"], "Repair starts with the skin barrier.\n1-Week Repair\nShop Now")

    def test_an_ad_text_line_that_looks_like_furniture_is_kept_when_it_is_a_sentence(self):
        c = sc.parse_ad_card("Library ID: 5\nB\nSponsored\nActive ingredients: niacinamide 10%\nPlatforms we love")
        self.assertEqual(c["body"], "Active ingredients: niacinamide 10%\nPlatforms we love")


class PageId(unittest.TestCase):
    def test_the_page_not_the_profile(self):
        html = '{"userID":"100064324674075","delegate_page":{"is_business_page_active":false,"id":"686566354710191"}} fb://profile/100064324674075'
        self.assertEqual(sc.facebook_page_id(html), "686566354710191")

    def test_fallbacks_and_numeric_handle(self):
        self.assertEqual(sc.facebook_page_id('"pageID":"12345678"'), "12345678")
        self.assertEqual(sc.facebook_page_id('x fb://profile/100064324674075 y'), "100064324674075")
        self.assertEqual(sc.facebook_page_id("", "686566354710191"), "686566354710191")
        self.assertIsNone(sc.facebook_page_id("<html>This content isn't available right now</html>", "nosuchpage"))


class FindingThePage(unittest.TestCase):
    def test_matched_on_the_advertiser_link_not_the_name(self):
        card = {"id": "1", "text": "", "pages": ["EucerinMY"]}
        self.assertTrue(sc.ad_card_is_by(card, "eucerinmy")); self.assertTrue(sc.ad_card_is_by(card, "@EucerinMY/"))
        self.assertFalse(sc.ad_card_is_by(card, "Eucerin")); self.assertFalse(sc.ad_card_is_by(card, ""))
        self.assertFalse(sc.ad_card_is_by({"id": "2", "pages": ["Guardianmy"]}, "EucerinMY"))      # a retailer advertising the brand
        self.assertTrue(sc.ad_card_is_by({"id": "3", "pages": ["smartdailydealsMY", "ShopeeMY"]}, "shopeemy"))   # a joint advertisement

    def test_page_number_candidates_most_mentioned_first(self):
        html = 'view_all_page_id=111111 "page_id":"222222" view_all_page_id=222222 "pageID":"222222" view_all_page_id=333333'
        self.assertEqual(sc.ad_page_id_candidates(html), ["222222", "111111", "333333"])
        self.assertEqual(sc.ad_page_id_candidates("nothing"), [])


class Links(unittest.TestCase):
    def test_each_ad_is_its_own_row(self):
        a, b = sc.AD_LIBRARY_AD.format(ad_id="111"), sc.AD_LIBRARY_AD.format(ad_id="222")
        self.assertNotEqual(sc.canonical_url(a), sc.canonical_url(b))
        self.assertEqual(sc.canonical_url(a), "https://facebook.com/ads/library?id=111")
        self.assertEqual(sc.clean_post_url(a), a)                          # the stored link still opens the ad
        self.assertEqual(sc.canonical_url(a + "&__cft__[0]=zz"), sc.canonical_url(a))

    def test_list_address(self):
        u = sc.AD_LIBRARY_LIST.format(country="MY", page_id="686566354710191")
        self.assertIn("country=MY", u); self.assertIn("view_all_page_id=686566354710191", u); self.assertIn("active_status=active", u)

    def test_a_running_ad_is_always_current(self):
        self.assertFalse(sc.Post(brand="b", platform="Instagram", url="u").always_current)
        self.assertFalse(sc.date_allowed("2023-01-01", 0, "2024-06-01"))    # what the flag is there to get past


class AccountStatus(unittest.TestCase):
    def res(self, **kw):
        base = dict(error=None, signed_out_view=False, via_ads=True, renamed_to=None, drift_note=None)
        base.update(kw); return types.SimpleNamespace(**base)

    def test_ads_listed_is_live(self):
        out = ac.classify(self.res(), 5)
        self.assertEqual(out["state"], "live"); self.assertIn("5 advertisement(s)", out["detail"])

    def test_no_ads_is_still_a_page_that_reads(self):
        out = ac.classify(self.res(ads_how="looked up in the Ad Library by the name 'QV', matched on the handle @QVSkincareMY: none of 30 advertisement(s) under that name is by this page"), 0)
        self.assertEqual(out["state"], "live"); self.assertIn("No advertisement by this page", out["detail"]); self.assertIn("none of 30", out["detail"])

    def test_wrong_handle_and_login(self):
        self.assertEqual(ac.classify(self.res(error="the page's id could not be read from its public page — profile not available (handle wrong or region-blocked)"), 0)["state"], "not_found")
        self.assertEqual(ac.classify(self.res(error="Facebook asked for a login before showing the page, so its id could not be read"), 0)["state"], "blocked")
        self.assertEqual(ac.classify(self.res(error="the Ad Library opened but showed no list (layout change, or a short block)"), 0)["state"], "error")


if __name__ == "__main__":
    unittest.main()
