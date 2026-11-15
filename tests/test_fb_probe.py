import time, unittest
from tests import _paths  # noqa: F401
import fb_probe as fp


def state(**cookies):
    now = time.time()
    return {"cookies": [{"name": n, "value": v, "domain": ".facebook.com", "expires": now + d * 86400} for n, (v, d) in cookies.items()]
            + [{"name": "sessionid", "value": "x", "domain": ".instagram.com", "expires": now + 300 * 86400}]}


class FbProbe(unittest.TestCase):
    def test_file_without_a_facebook_login(self):
        self.assertEqual(fp.describe_file(state(datr=("a", 300)))["verdict"], "no_login_in_file")
        self.assertEqual(fp.describe_file(state(c_user=("1", 300), xs=("", 300)))["verdict"], "no_login_in_file")

    def test_expired_and_in_date(self):
        self.assertEqual(fp.describe_file(state(c_user=("1", -2), xs=("a", -2)))["verdict"], "expired")
        d = fp.describe_file(state(c_user=("1", 364.1), xs=("a", 364.1)))
        self.assertEqual(d["verdict"], "in_date")
        self.assertAlmostEqual(d["recorded_days_ago"], 0.9, places=1)

    def test_only_facebook_cookies_are_loaded(self):
        self.assertEqual({c["domain"] for c in fp.fb_cookies(state(c_user=("1", 9), xs=("a", 9)))}, {".facebook.com"})

    def test_values_never_appear(self):
        d = fp.describe_file(state(c_user=("100012345", 9), xs=("SECRETVALUE", 9)))
        self.assertNotIn("SECRETVALUE", repr(d)); self.assertNotIn("100012345", repr(d))

    def test_classify(self):
        c = fp.classify
        self.assertEqual(c("in_date", "https://www.facebook.com/", ["c_user", "xs", "datr"], False, "feed"), "signed_in")
        self.assertEqual(c("in_date", "https://www.facebook.com/login/?next=x", ["datr"], True, "Log in"), "rejected")
        self.assertEqual(c("in_date", "https://www.facebook.com/", ["datr", "fr"], True, "Log in"), "rejected")
        self.assertEqual(c("in_date", "https://www.facebook.com/checkpoint/1501092823525282/", ["c_user", "xs"], False, ""), "checkpoint")
        self.assertEqual(c("in_date", "https://www.facebook.com/", ["c_user", "xs"], True, "Confirm it's you"), "checkpoint")
        self.assertEqual(c("no_login_in_file", "https://www.facebook.com/", [], True, ""), "no_login_in_file")
        self.assertEqual(c("expired", "https://www.facebook.com/", [], True, ""), "expired")

    def test_reason_and_url(self):
        self.assertIn("unusual", fp.reason_in("We noticed an unusual login from a device you don't usually use"))
        self.assertEqual(fp.reason_in("Eucerin Malaysia"), "")
        self.assertEqual(fp.clean_url("https://www.facebook.com/login/?next=https%3A%2F%2Fx#a"), "https://www.facebook.com/login/")


if __name__ == "__main__":
    unittest.main()
