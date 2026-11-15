import unittest
from tests import _paths  # noqa: F401
import platform_status as ps

LOG = """2026-11-15T12:53:30.1Z   SAFI             Facebook   posts=1   ok
2026-11-15T12:53:30.2Z   SAFI             Threads    posts=20  ok
2026-11-15T12:53:30.3Z   SimplySiti       Instagram  posts=54  ok
2026-11-15T12:53:30.4Z   Elianto          Instagram  posts=40  ok
2026-11-15T12:53:30.5Z   Elianto          Facebook   posts=0   ERR no post links found - page shows a login prompt
2026-11-15T12:53:30.6Z   Elianto          Threads    posts=12  PARTIAL - run budget ended mid-profile
2026-11-15T12:53:30.7Z   Physiogel        Threads    posts=0   ERR no post links found - profile screenshot saved as EMPTY_x.png
"""


def entry(key, label, state, at):
    return {"key": key, "label": label, "state": state, "at": at, "carried": True, "detail": "old"}


class Evidence(unittest.TestCase):
    def test_log_counts(self):
        e = ps.evidence_from_log(LOG)
        self.assertEqual(e["Instagram"], {"ok": 2, "wall": 0, "signed_out": False})
        self.assertEqual(e["Threads"]["ok"], 2)           # ok and PARTIAL both prove the login reads
        self.assertEqual(e["Facebook"]["wall"], 1)

    def test_signed_out_line(self):
        e = ps.evidence_from_log("ERROR Instagram signed the recorded session out - not swept\n")
        self.assertTrue(e["Instagram"]["signed_out"])

    def test_sweep_that_read_accounts_makes_it_live_and_current(self):
        carried = [entry("instagram", "Instagram", "live", "2026-11-14T22:35:28Z"), entry("facebook", "Facebook", "live", "2026-11-15T12:06:07Z"),
                   entry("threads", "Threads", "live", "2026-11-14T22:35:28Z")]
        out = ps.apply_sweep_evidence(carried, ps.evidence_from_log(LOG), "2026-11-15T12:53:30Z")
        ig, fb, th = out
        self.assertEqual((ig["state"], ig["at"], ig["carried"]), ("live", "2026-11-15T12:53:30Z", False))
        self.assertIn("read 2 account(s) on Instagram", ig["detail"])
        self.assertEqual(th["at"], "2026-11-15T12:53:30Z")
        self.assertEqual(fb, carried[1])                  # Facebook has its own probe

    def test_signed_out_wins_even_when_some_accounts_read(self):
        carried = [entry("instagram", "Instagram", "live", "2026-11-14T22:35:28Z")]
        ev = {"Instagram": {"ok": 3, "wall": 0, "signed_out": True}}
        self.assertEqual(ps.apply_sweep_evidence(carried, ev, "2026-11-15T12:53:30Z")[0]["state"], "down")

    def test_no_evidence_keeps_the_old_result_and_its_time(self):
        carried = [entry("threads", "Threads", "live", "2026-11-14T22:35:28Z")]
        self.assertEqual(ps.apply_sweep_evidence(carried, {}, "2026-11-15T12:53:30Z"), carried)
        self.assertEqual(ps.apply_sweep_evidence(carried, {"Threads": {"ok": 0, "wall": 4, "signed_out": False}}, "2026-11-15T12:53:30Z"), carried)

    def test_an_older_sweep_never_overwrites_a_newer_probe(self):
        carried = [entry("instagram", "Instagram", "down", "2026-11-15T13:00:00Z")]
        self.assertEqual(ps.apply_sweep_evidence(carried, {"Instagram": {"ok": 5, "wall": 0, "signed_out": False}}, "2026-11-15T12:53:30Z"), carried)


if __name__ == "__main__":
    unittest.main()
