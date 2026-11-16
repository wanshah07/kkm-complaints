import base64, gzip, json, time, unittest
from tests import _paths  # noqa: F401
import session_pack as sp
import scraper as sc


def state(ig=True, th=True):
    now = time.time(); c = []
    if ig:
        c += [{"name": "sessionid", "value": "SECRETIG", "domain": ".instagram.com", "expires": now + 300 * 86400},
              {"name": "csrftoken", "value": "t", "domain": ".instagram.com", "expires": now + 300 * 86400}]
    if th:
        c += [{"name": "sessionid", "value": "SECRETTH", "domain": ".threads.com", "expires": now + 200 * 86400}]
    c += [{"name": "c_user", "value": "100099", "domain": ".facebook.com", "expires": now + 300 * 86400},
          {"name": "xs", "value": "SECRETFB", "domain": ".facebook.com", "expires": now + 300 * 86400}]
    return {"cookies": c, "origins": [{"origin": "https://www.instagram.com", "localStorage": [{"name": "a", "value": "b" * 5000}]}]}


class Pack(unittest.TestCase):
    def test_facebook_and_origins_are_left_out(self):
        packed = json.loads(gzip.decompress(base64.b64decode(sp.pack(state()))))
        self.assertEqual(packed["origins"], [])
        self.assertEqual({c["domain"] for c in packed["cookies"]}, {".instagram.com", ".threads.com"})

    def test_what_it_makes_is_what_the_scraper_reads(self):
        text = sp.pack(state()); raw = gzip.decompress(base64.b64decode(text))
        self.assertEqual(len(json.loads(raw)["cookies"]), 3)

    def test_it_reports_a_missing_login_and_never_a_value(self):
        d = sp.describe(state(th=False))
        self.assertEqual(d["Threads"][:2], (0, False)); self.assertTrue(d["Instagram"][1])
        self.assertAlmostEqual(d["Instagram"][2], 300, delta=1)
        self.assertNotIn("SECRET", repr(d))

    def test_the_scraper_loads_the_packed_text(self):
        import os, tempfile
        os.environ["PW_STORAGE_STATE_B64"] = sp.pack(state()); os.environ.pop("PW_STORAGE_STATE_PATH", None)
        try:
            with self.assertLogs("kkm.scraper", level="INFO") as cap:
                s = sc.Scraper({"run": {}, "platforms": {}}, tempfile.mkdtemp())
            self.assertIsNotNone(s._storage_state_path); self.assertNotIn("invalid, ignoring", "\n".join(cap.output))
        finally:
            os.environ.pop("PW_STORAGE_STATE_B64", None)


if __name__ == "__main__":
    unittest.main()
