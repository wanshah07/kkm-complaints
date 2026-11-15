import logging, os, tempfile, types, unittest
from tests import _paths  # noqa: F401
import main as m
import scraper as sc
import platform_status as ps
import tiktok_links as tl


class Order(unittest.TestCase):
    BRANDS = [{"name": "QV", "handles": {"Instagram": "qv_my", "Facebook": "QVMalaysia", "TikTok": "qv"}, "type": ""},
              {"name": "Uriage", "handles": {"Instagram": "uriage_my"}, "type": ""},
              {"name": "drx", "handles": {"Instagram": "drx_new", "Threads": "drx_new"}, "type": "General Doctor"}]
    P = {"Instagram", "Facebook", "Threads"}

    def test_oldest_first(self):
        st = {"qv\x01instagram": {"handle": "qv_my", "sweep_at": "2026-11-14T12:00:00Z"},
              "uriage\x01instagram": {"handle": "uriage_my", "sweep_at": "2026-11-13T12:00:00Z"},
              "drx\x01instagram": {"handle": "drx_OLD", "sweep_at": "2026-11-15T00:00:00Z"}}   # other handle: not counted
        rows, never, total = m.order_oldest(self.BRANDS, self.P, st)
        self.assertEqual((never, total), (3, 5))
        self.assertEqual([(r["name"], list(r["handles"])) for r in rows],
                         [("QV", ["Facebook"]), ("drx", ["Instagram"]), ("drx", ["Threads"]),
                          ("Uriage", ["Instagram"]), ("QV", ["Instagram"])])
        self.assertEqual(rows[1]["type"], "General Doctor")               # the row's own fields travel with it
        self.assertTrue(all(len(r["handles"]) == 1 for r in rows))

    def test_editing_the_list_does_not_shift_the_order(self):
        st = {"qv\x01instagram": {"handle": "qv_my", "sweep_at": "2026-11-14T12:00:00Z"},
              "qv\x01facebook": {"handle": "QVMalaysia", "sweep_at": "2026-11-14T12:01:00Z"},
              "uriage\x01instagram": {"handle": "uriage_my", "sweep_at": "2026-11-13T12:00:00Z"},
              "drx\x01instagram": {"handle": "drx_new", "sweep_at": "2026-11-12T00:00:00Z"},
              "drx\x01threads": {"handle": "drx_new", "sweep_at": "2026-11-12T00:01:00Z"}}
        full = [(r["name"], list(r["handles"])[0]) for r in m.order_oldest(self.BRANDS, self.P, st)[0]]
        without = [(r["name"], list(r["handles"])[0]) for r in m.order_oldest(self.BRANDS[1:], self.P, st)[0]]
        self.assertEqual(without, [x for x in full if x[0] != "QV"])

    def test_sweep_status(self):
        rep = {"targets": [{"brand": "QV", "platform": "Instagram", "posts": 137, "opened": 0, "error": None, "truncated": False},
                           {"brand": "QV", "platform": "Facebook", "posts": 0, "opened": 0, "error": "no post links found — profile screenshot saved as E.png", "truncated": False},
                           {"brand": "Uriage", "platform": "Instagram", "posts": 60, "opened": 40, "error": None, "truncated": False, "capped": True},
                           {"brand": "drx", "platform": "Instagram", "posts": 9, "opened": 4, "error": None, "truncated": True},
                           {"brand": "drx", "platform": "Threads", "posts": 0, "opened": 0, "error": None, "truncated": True},
                           {"brand": "Ghost", "platform": "Instagram", "posts": 1, "opened": 1, "error": None, "truncated": False}],
               "handle_checks": []}
        by = {(x["brand"], x["platform"]): x for x in m.sweep_status_updates(rep, self.BRANDS)}
        self.assertEqual(set(by), {("QV", "Instagram"), ("QV", "Facebook"), ("Uriage", "Instagram"), ("drx", "Instagram")})
        self.assertEqual(by[("QV", "Facebook")]["sweep_note"], "no post links found")
        self.assertIn("partial: 40", by[("Uriage", "Instagram")]["sweep_note"])
        self.assertIn("partial: 4", by[("drx", "Instagram")]["sweep_note"])
        self.assertTrue(all("state" not in x and "checked_at" not in x for x in by.values()))

    def test_web_links_only(self):
        for good in ("https://www.instagram.com/p/abc/", "http://shop.example/products/x", " https://a.b/c "):
            self.assertTrue(m._is_web_link(good), good)
        for bad in ("javascript:alert(1)", "data:text/html,x", "", None, "manual://review", "//evil.example/x", "https://", "ftp://a.b/c"):
            self.assertFalse(m._is_web_link(bad), bad)

    def test_tiktok_column_is_quiet(self):
        class C:
            def targets(self):
                return [{"name": "CeraVe", "active": True, "handles": {"Instagram": "cerave", "TikTok": "ceravemy", "YouTube": "cv"}},
                        {"name": "QV", "active": True, "handles": {"Instagram": "qv", "TikTok": "qv", "YouTube": "qv"}}]
        with self.assertLogs("kkm.main", level="WARNING") as cm:
            brands = m.resolve_targets({"platforms": {"Instagram": {}}, "brands": []}, "sheet", C(), {})
        self.assertEqual([b["handles"] for b in brands], [{"Instagram": "cerave"}, {"Instagram": "qv"}])
        self.assertEqual(len(cm.output), 1)                         # YouTube once, TikTok never
        self.assertIn("YouTube", cm.output[0])


class Reader(unittest.TestCase):
    def test_private_is_the_platforms_sentence(self):
        class Page:
            def __init__(self, text):
                self.text = text

            def locator(self, _):
                return types.SimpleNamespace(inner_text=lambda timeout=0: self.text)

            def screenshot(self, **k):
                raise RuntimeError("no browser")
        d = tempfile.mkdtemp()
        self.assertIn("private", sc._diagnose_empty(Page("drx 120 followers This account is private Follow to see"), d, "t"))
        self.assertIn("private", sc._diagnose_empty(Page("This profile is private."), d, "t"))
        self.assertEqual(sc._diagnose_empty(Page("Dr X · Private practice dermatologist, KL"), d, "t"), "")
        self.assertIn("no posts yet", sc._diagnose_empty(Page("drx Threads No threads yet"), d, "t"))

    def test_notes_reach_the_remarks(self):
        import inspect
        src = inspect.getsource(m)
        self.assertIn('"; note: " + "; ".join(post.notes)', src)
        p = sc.Post(brand="b", platform="TikTok", url="https://www.tiktok.com/@a/video/1")
        self.assertEqual(p.notes, [])
        p.notes.append("x")
        self.assertEqual(sc.Post(brand="b", platform="TikTok", url="u").notes, [])        # not shared between posts

    def test_target_result_defaults(self):
        r = sc.TargetResult(brand="b", platform="Instagram", profile_url="u")
        self.assertEqual((r.links_found, r.signed_out_view, r.capped), (0, False, False))


class Status(unittest.TestCase):
    def test_carry(self):
        import json
        d = tempfile.mkdtemp(); p = os.path.join(d, "s.json")
        with open(p, "w") as f:
            json.dump({"checked_at": "2026-11-14T22:35:28Z", "platforms": [
            {"key": "instagram", "label": "Instagram", "state": "live", "detail": "ok"},
            {"key": "facebook", "label": "Facebook", "state": "down", "detail": "login", "at": "2026-11-14T10:00:00Z"},
            {"key": "threads", "label": "Threads", "state": "live", "detail": "ok"},
            {"key": "tiktok", "label": "TikTok links", "state": "live", "detail": ""}]}, f)
        c = ps.carry_previous(p)
        self.assertEqual([x["key"] for x in c], ["instagram", "facebook", "threads"])
        self.assertEqual([x["at"] for x in c], ["2026-11-14T22:35:28Z", "2026-11-14T10:00:00Z", "2026-11-14T22:35:28Z"])
        self.assertTrue(all(x["carried"] for x in c))
        self.assertIsNone(ps.carry_previous(os.path.join(d, "missing.json")))
        with open(p, "w") as f:
            json.dump({"platforms": [{"key": "instagram"}]}, f)
        self.assertIsNone(ps.carry_previous(p))                      # an incomplete file is not carried


class LinkMeta(unittest.TestCase):
    def test_meta(self):
        import json
        os.environ.pop("TIKTOK_LINK_META", None)
        self.assertEqual(tl.link_meta(), {})
        os.environ["TIKTOK_LINK_META"] = "{not json"
        with self.assertLogs("kkm.tiktok", level="WARNING"):
            self.assertEqual(tl.link_meta(), {})
        os.environ["TIKTOK_LINK_META"] = json.dumps({"CeraveMY": {"name": "CeraVe", "type": "", "hints": ["SA", ""]}, "x": {"name": ""}, "y": 5})
        self.assertEqual(tl.link_meta(), {"ceravemy": {"name": "CeraVe", "type": "", "hints": ["SA"]}})
        os.environ.pop("TIKTOK_LINK_META")
        self.assertEqual(tl._handle_in("https://www.tiktok.com/@CeraveMY/video/123"), "ceravemy")


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main()
