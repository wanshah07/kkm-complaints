import base64, gzip, json, unittest
import tests._paths  # noqa: F401
from session_state import combined_b64, merge_state


def b64(state, gz=False):
    raw = json.dumps(state).encode()
    return base64.b64encode(gzip.compress(raw) if gz else raw).decode()


def ck(domain, name="x", value="v"):
    return {"name": name, "value": value, "domain": domain, "path": "/"}


MAIN = {"cookies": [ck(".instagram.com", "sessionid", "old"), ck(".facebook.com", "xs", "fb"),
                    ck(".threads.net", "sessionid", "th")], "origins": []}
IG = {"cookies": [ck(".instagram.com", "sessionid", "new"), ck(".instagram.com", "csrftoken", "c")], "origins": []}


class Overlay(unittest.TestCase):
    def test_instagram_replaced_others_kept(self):
        out = merge_state(MAIN, IG)
        ig = [c["value"] for c in out["cookies"] if "instagram" in c["domain"]]
        self.assertEqual(sorted(ig), ["c", "new"])
        self.assertIn("fb", [c["value"] for c in out["cookies"]])
        self.assertIn("th", [c["value"] for c in out["cookies"]])

    def test_no_overlay_returns_main_untouched(self):
        self.assertEqual(combined_b64(b64(MAIN), ""), b64(MAIN))

    def test_no_main_uses_overlay(self):
        out = json.loads(gzip.decompress(base64.b64decode(combined_b64("", b64(IG, gz=True)))))
        self.assertEqual(len(out["cookies"]), 2)

    def test_garbage_overlay_ignored(self):
        self.assertEqual(combined_b64(b64(MAIN), "not-base64!!"), b64(MAIN))


if __name__ == "__main__":
    unittest.main()
