import time, types, unittest
from tests import _paths  # noqa: F401
import account_check as ac


def res(**k):
    base = dict(error=None, renamed_to=None, drift_note=None, links_found=0, signed_out_view=False)
    base.update(k)
    return types.SimpleNamespace(**base)


class AccountCheck(unittest.TestCase):
    P = {"Instagram", "Facebook", "Threads"}
    ROWS = [{"name": "CeraVe", "active": True, "handles": {"Instagram": "cerave", "Threads": "cerave", "TikTok": "ceravemy", "YouTube": "x"}},
            {"name": "Off", "active": False, "handles": {"Instagram": "off"}},
            {"name": "Valorith", "active": True, "handles": {"Instagram": "valorith"}},
            {"name": "Web only", "active": True, "handles": {}},
            {"name": "Dr X", "active": True, "handles": {"Instagram": "drx", "Facebook": ""}}]

    def test_pairs(self):
        p = ac.pairs_from(self.ROWS, self.P)
        self.assertEqual(p, [("CeraVe", "Instagram", "cerave"), ("CeraVe", "Threads", "cerave"),
                             ("CeraVe", "TikTok", "ceravemy"), ("Dr X", "Instagram", "drx")])
        self.assertEqual(ac.pairs_from(self.ROWS, self.P, "tiktok"), [("CeraVe", "TikTok", "ceravemy")])
        self.assertEqual(ac.pairs_from(self.ROWS, self.P, "", "dr x"), [("Dr X", "Instagram", "drx")])

    def test_order(self):
        p = ac.pairs_from(self.ROWS, self.P)
        iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
        now = time.time()
        st = {"cerave\x01instagram": {"checked_at": iso(now - 3600), "handle": "cerave"},
              "cerave\x01threads": {"checked_at": iso(now - 3 * 86400), "handle": "cerave"},
              "dr x\x01instagram": {"checked_at": iso(now - 60), "handle": "OLDHANDLE"}}
        o, fresh = ac.order(p, st, 0)
        self.assertEqual([x[1] + ":" + x[0] for x in o], ["TikTok:CeraVe", "Instagram:Dr X", "Threads:CeraVe", "Instagram:CeraVe"])
        o, fresh = ac.order(p, st, 24)
        self.assertEqual((len(o), fresh), (3, 1))

    def test_states(self):
        self.assertEqual(ac.classify(res(), 12)["state"], "live")
        c = ac.classify(res(renamed_to="newname"), 9)
        self.assertEqual((c["state"], c["suggest"]), ("renamed", "newname"))
        self.assertEqual(ac.classify(res(error="login wall (provide PW_STORAGE_STATE_B64)"), 0)["state"], "blocked")
        self.assertEqual(ac.classify(res(error="no post links found — page shows a login prompt; profile screenshot saved as E.png"), 0)["state"], "blocked")
        self.assertEqual(ac.classify(res(error="no post links found — profile not available (handle wrong or region-blocked)"), 0)["state"], "not_found")
        self.assertEqual(ac.classify(res(error="no post links found — profile is private to this account"), 0)["state"], "private")
        e = ac.classify(res(error="no post links found — profile has no posts yet; profile screenshot saved as E.png"), 0)
        self.assertEqual((e["state"], "not posted" in e["detail"]), ("empty", True))
        self.assertEqual(ac.classify(res(error="no post links found — profile screenshot saved as E.png"), 0)["state"], "empty")
        self.assertEqual(ac.classify(res(error="timeout: Page.goto: Timeout 45000ms exceeded."), 0)["state"], "error")

    def test_signed_out_view_is_not_live(self):
        # The Facebook case of 14 Nov 2026: nothing signed in, one post signed out.
        self.assertEqual(ac.classify(res(signed_out_view=True), 1, signed_in=True)["state"], "limited")
        # With no login configured at all, the signed-out view is all there is: that is "live".
        self.assertEqual(ac.classify(res(signed_out_view=True), 1, signed_in=False)["state"], "live")

    def test_tiktok(self):
        class S:
            def __init__(self, code, text=""):
                self.c, self.t = code, text

            def get(self, *a, **k):
                return types.SimpleNamespace(status_code=self.c, text=self.t)
        ac.ttd.time.sleep = lambda *_: None
        from tests.test_tiktok_discover import embed, vid, NEW
        self.assertEqual(ac.check_tiktok(S(200, embed("ceravemy", [vid(NEW)])), "ceravemy")["state"], "live")
        self.assertEqual(ac.check_tiktok(S(200, embed("ceravemy", [])), "ceravemy")["state"], "empty")
        self.assertEqual(ac.check_tiktok(S(400), "nosuch")["state"], "not_found")
        self.assertEqual(ac.check_tiktok(S(503), "x1")["state"], "blocked")


if __name__ == "__main__":
    unittest.main()
