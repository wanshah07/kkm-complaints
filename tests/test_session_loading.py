import base64, gzip, json, os, tempfile, time, unittest
from tests import _paths  # noqa: F401
import scraper as sc

# 15 Nov 2026: a NameError inside the session-age report was swallowed by the "invalid, ignoring" handler, so a whole
# sweep ran logged out and nothing said why. This builds a real Scraper from the environment the workflows use.


def state():
    now = time.time()
    return {"cookies": [
        {"name": "sessionid", "value": "x", "domain": ".instagram.com", "expires": now + 300 * 86400},
        {"name": "sessionid", "value": "y", "domain": ".threads.com", "expires": now + 300 * 86400},
        {"name": "datr", "value": "z", "domain": ".facebook.com", "expires": now + 390 * 86400}], "origins": []}


class SessionLoading(unittest.TestCase):
    def build(self, raw_b64, run=None):
        old = os.environ.get("PW_STORAGE_STATE_B64"), os.environ.pop("PW_STORAGE_STATE_PATH", None)
        os.environ["PW_STORAGE_STATE_B64"] = raw_b64
        try:
            here = os.path.join(os.path.dirname(sc.__file__), "storage_state.json")
            self.assertFalse(os.path.exists(here), "a storage_state.json next to scraper.py would hide the test")
            with self.assertLogs("kkm.scraper", level="INFO") as cap:
                s = sc.Scraper({"run": run or {}, "platforms": {}}, tempfile.mkdtemp())
            return s, "\n".join(cap.output)
        finally:
            if old[0] is None:
                os.environ.pop("PW_STORAGE_STATE_B64", None)
            else:
                os.environ["PW_STORAGE_STATE_B64"] = old[0]

    def test_plain_and_gzipped_sessions_load(self):
        raw = json.dumps(state()).encode()
        for b in (base64.b64encode(raw).decode(), base64.b64encode(gzip.compress(raw)).decode()):
            s, out = self.build(b)
            self.assertIsNotNone(s._storage_state_path, out)
            self.assertNotIn("invalid, ignoring", out)
            self.assertIn("using browser session from PW_STORAGE_STATE_B64", out)
            self.assertEqual(len(json.load(open(s._storage_state_path))["cookies"]), 3)

    def test_facebook_is_not_warned_about_when_read_through_the_ad_library(self):
        st = state(); st["cookies"] = [c for c in st["cookies"] if "facebook" not in c["domain"]]
        s, out = self.build(base64.b64encode(json.dumps(st).encode()).decode())
        self.assertNotIn("carries no dated cookie", out)
        s, out = self.build(base64.b64encode(json.dumps(st).encode()).decode(), run={"facebook_source": "page"})
        self.assertIn("no dated cookie for facebook.com", out)

    def test_a_broken_secret_is_ignored_and_says_so(self):
        s, out = self.build(base64.b64encode(b"not json").decode())
        self.assertIsNone(s._storage_state_path); self.assertIn("invalid, ignoring", out)


if __name__ == "__main__":
    unittest.main()
