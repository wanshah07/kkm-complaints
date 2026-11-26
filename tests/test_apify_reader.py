import os, tempfile, unittest
from unittest import mock
from tests import _paths  # noqa: F401
import apify_reader as ar


class Resp:
    def __init__(self, code, data):
        self.status_code, self._d, self.text = code, data, ""

    def json(self):
        return self._d


class FakeApify:
    """requests.Session look-alike: one canned answer per endpoint, every call recorded."""

    def __init__(self, items=None, status="SUCCEEDED", usd=0.01, limits=None, start_code=201, items_code=200):
        self.items, self.status, self.usd, self.start_code, self.items_code = items or [], status, usd, start_code, items_code
        self.limits = limits if limits is not None else Resp(200, {"data": {"current": {"monthlyUsageUsd": 0.1}, "limits": {"maxMonthlyUsageUsd": 10}}})
        self.calls = []
        self.bodies = []

    def request(self, method, url, json=None, headers=None, timeout=None):
        path = url.split("/v2/", 1)[1]
        self.calls.append((method, path))
        if path.startswith("users/me/limits"):
            return self.limits
        if method == "POST" and path.startswith("acts/"):
            self.bodies.append(json)
            return Resp(self.start_code, {"data": {"id": f"run{len(self.bodies)}", "defaultDatasetId": f"ds{len(self.bodies)}"}})
        if path.startswith("actor-runs/"):
            return Resp(200, {"data": {"status": self.status, "usageTotalUsd": self.usd}})
        if path.startswith("datasets/"):
            return Resp(self.items_code, self.items)
        return Resp(404, {})


def item(code, owner, handle=None, ts="2026-11-25T04:00:00.000Z", typ="Sidecar", **kw):
    return {"shortCode": code, "ownerUsername": owner, "inputUrl": f"https://www.instagram.com/{handle or owner}/", "timestamp": ts,
            "caption": f"caption {code}", "type": typ, "displayUrl": "https://scontent.example/x.jpg", "images": ["https://scontent.example/y.jpg"], **kw}


def feed(http, plan, **kw):
    return ar.ApifyFeed("tok", plan, session=http, sleep=lambda s: None, **kw)


class Normalise(unittest.TestCase):
    def test_post_fields_and_kinds(self):
        p = ar.normalise_item(item("AAAAAAAAAAA", "Brand", typ="Video"))
        self.assertEqual((p.code, p.kind, p.owner, p.text), ("AAAAAAAAAAA", "reel", "Brand", "caption AAAAAAAAAAA"))
        self.assertTrue(p.url.endswith("/Brand/reel/AAAAAAAAAAA/"))
        self.assertEqual(ar.normalise_item(item("BBBBBBBBBBB", "b", productType="clips")).kind, "reel")
        self.assertEqual(ar.normalise_item(item("CCCCCCCCCCC", "b")).kind, "p")
        self.assertEqual(len(ar.normalise_item(item("DDDDDDDDDDD", "b")).images), 2)

    def test_error_records_and_junk_are_not_posts(self):
        self.assertIsNone(ar.normalise_item({"error": "no_items", "errorDescription": "x"}))
        self.assertIsNone(ar.normalise_item({"ownerUsername": "x"}))
        self.assertIsNone(ar.normalise_item("nope"))

    def test_an_item_answers_the_account_it_was_asked_for(self):
        self.assertEqual(ar.handle_of_item(item("EEEEEEEEEEE", "Renamed", handle="OldHandle")), "oldhandle")
        self.assertEqual(ar.handle_of_item({"ownerUsername": "Zed"}), "zed")


class Feed(unittest.TestCase):
    def test_batch_is_one_run_and_empty_means_no_new_posts(self):
        http = FakeApify([item("AAAAAAAAAAA", "a"), item("BBBBBBBBBBB", "a"), item("CCCCCCCCCCC", "b")])
        f = feed(http, ["a", "b", "c"])
        self.assertEqual([p.code for p in f.posts_for("a")], ["AAAAAAAAAAA", "BBBBBBBBBBB"])
        self.assertEqual([p.code for p in f.posts_for("b")], ["CCCCCCCCCCC"])
        self.assertEqual(f.posts_for("c"), [])                       # served, nothing new in the window
        self.assertEqual(sum(1 for m, p in http.calls if m == "POST"), 1)
        body = http.bodies[0]
        self.assertEqual(body["resultsType"], "posts")
        self.assertEqual(len(body["directUrls"]), 3)
        self.assertEqual(body["resultsLimit"], 6)
        self.assertRegex(body["onlyPostsNewerThan"], r"^\d{4}-\d{2}-\d{2}$")

    def test_batches_are_fetched_only_as_the_sweep_reaches_them(self):
        http = FakeApify([item("AAAAAAAAAAA", "h0")])
        f = feed(http, [f"h{i}" for i in range(5)], batch=2)
        f.posts_for("h0")
        self.assertEqual(len(http.bodies), 1)
        self.assertEqual(len(http.bodies[0]["directUrls"]), 2)        # h0 and h1 only: h2..h4 were never paid for
        f.posts_for("h1")
        self.assertEqual(len(http.bodies), 1)                         # already fetched with h0

    def test_a_failed_run_means_not_served_and_the_reason_is_kept(self):
        http = FakeApify(status="FAILED")
        f = feed(http, ["a", "b"])
        self.assertIsNone(f.posts_for("a"))
        self.assertIn("FAILED", f.why_not("a"))
        self.assertIsNone(f.posts_for("b"))                           # same batch, not retried

    def test_unreadable_results_and_refused_start_are_not_served(self):
        self.assertIsNone(feed(FakeApify(items_code=500), ["a"]).posts_for("a"))
        self.assertIsNone(feed(FakeApify(start_code=402), ["a"]).posts_for("a"))

    def test_two_failed_runs_in_a_row_switch_apify_off_for_the_run(self):
        f = feed(FakeApify(status="FAILED"), ["a", "b", "c"], batch=1)
        self.assertIsNone(f.posts_for("a"))
        self.assertIsNone(f.posts_for("b"))
        self.assertIn("twice", f.disabled or "")
        runs = f.runs
        self.assertIsNone(f.posts_for("c"))
        self.assertEqual(f.runs, runs)                                # no third attempt

    def test_a_good_run_resets_the_failure_count(self):
        http = FakeApify(items=[item("AAAAAAAAAAA", "a")])
        f = feed(http, ["a", "b"], batch=1)
        f.failures = 1
        f.posts_for("a")
        self.assertEqual(f.failures, 0)

    def test_the_run_budget_stops_further_batches(self):
        http = FakeApify(items=[item("AAAAAAAAAAA", "a")], usd=0.6)
        f = feed(http, ["a", "b"], batch=1, max_usd=0.5)
        self.assertIsNotNone(f.posts_for("a"))
        self.assertIsNone(f.posts_for("b"))
        self.assertIn("budget", f.why_not("b"))
        self.assertAlmostEqual(f.spent_usd, 0.6)

    def test_refused_token_or_nearly_spent_plan_turns_it_off(self):
        f = feed(FakeApify(limits=Resp(401, {})), ["a"])
        f.check_plan()
        self.assertIn("refused", f.disabled)
        self.assertIsNone(f.posts_for("a"))
        g = feed(FakeApify(limits=Resp(200, {"data": {"current": {"monthlyUsageUsd": 9.5}, "limits": {"maxMonthlyUsageUsd": 10}}})), ["a"])
        g.check_plan()
        self.assertIn("used this month", g.disabled)
        h = feed(FakeApify(limits=Resp(500, {})), ["a"])
        h.check_plan()
        self.assertIsNone(h.disabled)                                 # a blip is not a reason to switch off

    def test_from_env(self):
        with mock.patch.dict(os.environ, {"APIFY_TOKEN": "", "APIFY_MODE": ""}, clear=False):
            self.assertIsNone(ar.ApifyFeed.from_env(["a"]))
        with mock.patch.dict(os.environ, {"APIFY_TOKEN": "t", "APIFY_MODE": "off"}):
            self.assertIsNone(ar.ApifyFeed.from_env(["a"]))
        with mock.patch.dict(os.environ, {"APIFY_TOKEN": "t", "APIFY_MODE": "auto"}), \
                mock.patch.object(ar.ApifyFeed, "check_plan", lambda self: None):
            f = ar.ApifyFeed.from_env(["A", "b"])
            self.assertEqual(f.plan, ["a", "b"])
            self.assertEqual((f.per_profile, f.newer_than_days, f.batch), (6, 3, 20))
        self.assertIsNone(ar.ApifyFeed.from_env([]))


class ScraperUsesIt(unittest.TestCase):
    def scraper(self):
        import scraper
        s = scraper.Scraper({"run": {}, "platforms": {"Instagram": {"profile_url": "https://www.instagram.com/{handle}/"}}}, tempfile.mkdtemp())
        s._apify_evidence = lambda *a, **k: None
        s._context = lambda *a, **k: mock.Mock()
        return s, scraper

    def result(self, scraper):
        return scraper.TargetResult(brand="Brand", platform="Instagram", profile_url="x")

    def test_unserved_target_returns_none_so_playwright_takes_it(self):
        s, m = self.scraper()
        f = mock.Mock()
        f.posts_for.return_value = None
        f.why_not.return_value = "the Apify run ended FAILED"
        s.apify = f
        self.assertIsNone(s._scrape_instagram_apify(mock.Mock(), "Brand", "brand_my", self.result(m)))

    def test_no_feed_means_not_served(self):
        s, m = self.scraper()
        s.apify = None
        self.assertIsNone(s._scrape_instagram_apify(mock.Mock(), "Brand", "brand_my", self.result(m)))

    def test_served_posts_become_posts_known_ones_are_skipped_and_other_accounts_refused(self):
        s, m = self.scraper()
        posts = [ar.normalise_item(item("AAAAAAAAAAA", "brand_my", ts="2026-11-25T04:00:00.000Z")),
                 ar.normalise_item(item("BBBBBBBBBBB", "brand_my", ts="2026-11-24T04:00:00.000Z")),
                 ar.normalise_item(item("CCCCCCCCCCC", "someone_else", handle="brand_my", ts="2026-11-23T04:00:00.000Z"))]
        f = mock.Mock()
        f.posts_for.return_value = posts
        s.apify = f
        s.known_urls = {"https://instagram.com/brand_my/p/bbbbbbbbbbb"}
        r = s._scrape_instagram_apify(mock.Mock(), "Brand", "brand_my", self.result(m))
        self.assertEqual(r.reader, "apify")
        by = {p.url.rstrip("/").split("/")[-1]: p for p in r.posts}
        self.assertFalse(by["AAAAAAAAAAA"].known)
        self.assertEqual(by["AAAAAAAAAAA"].text, "caption AAAAAAAAAAA")
        self.assertEqual(by["AAAAAAAAAAA"].posted_at, "2026-11-25T04:00:00.000Z")
        self.assertTrue(by["BBBBBBBBBBB"].known)
        self.assertTrue(by["CCCCCCCCCCC"].not_owned)
        self.assertTrue(any("Apify" in n for n in by["AAAAAAAAAAA"].notes))

    def test_the_visit_cap_is_honoured(self):
        s, m = self.scraper()
        s.run_cfg = {"max_new_posts_per_visit": 1}
        f = mock.Mock()
        f.posts_for.return_value = [ar.normalise_item(item("AAAAAAAAAAA", "b", ts="2026-11-25T04:00:00.000Z")),
                                    ar.normalise_item(item("BBBBBBBBBBB", "b", ts="2026-11-24T04:00:00.000Z"))]
        s.apify = f
        r = s._scrape_instagram_apify(mock.Mock(), "Brand", "b", self.result(m))
        self.assertTrue(r.capped)
        self.assertEqual(len([p for p in r.posts if not p.known]), 1)


if __name__ == "__main__":
    unittest.main()
