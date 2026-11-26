import unittest
from datetime import datetime, timedelta, timezone
from tests import _paths  # noqa: F401
import login_rest as lr

NOW = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)


def run(start_ago_min, minutes):
    s = NOW - timedelta(minutes=start_ago_min)
    f = "%Y-%m-%dT%H:%M:%SZ"
    return {"status": "completed", "run_started_at": s.strftime(f), "updated_at": (s + timedelta(minutes=minutes)).strftime(f)}


class Rest(unittest.TestCase):
    def d(self, runs, **kw):
        return lr.decide(lr.used_intervals(runs, NOW), NOW, **kw)

    def test_no_runs_is_fine(self):
        self.assertTrue(self.d([])["ok"])

    def test_run_that_just_ended_blocks_a_scheduled_run(self):
        r = self.d([run(150, 100)], event="schedule")                     # ended 50 min ago
        self.assertFalse(r["ok"])
        self.assertEqual(r["allowed_at"], NOW + timedelta(minutes=40))
        self.assertTrue(r["reason"].startswith("skipped"))

    def test_after_the_rest_it_may_run(self):
        self.assertTrue(self.d([run(220, 100)])["ok"])                    # ended 120 min ago, 100 used

    def test_daily_budget(self):
        r = self.d([run(700, 105), run(500, 105)])                        # 210 min in 24 h, last ended 290 min ago
        self.assertFalse(r["ok"])
        self.assertIn("budget 200", r["reason"])
        self.assertGreater(r["allowed_at"], NOW)

    def test_manual_run_says_how_to_override_and_force_runs(self):
        blocked = self.d([run(150, 100)], event="workflow_dispatch")
        self.assertFalse(blocked["ok"])
        self.assertIn("ignore_rest", blocked["reason"])
        self.assertTrue(self.d([run(150, 100)], event="workflow_dispatch", force=True)["ok"])

    def test_short_runs_and_old_runs_do_not_count(self):
        self.assertTrue(self.d([run(30, 12)])["ok"])                      # a 12-minute run (selftest, a skipped one, or a sweep Apify served)
        self.assertTrue(self.d([run(30 * 60, 100)])["ok"])                # 30 hours ago

    def test_account_check_is_held_to_the_budget_only(self):
        self.assertTrue(self.d([run(150, 100)], rest=False)["ok"])        # right behind a sweep: allowed
        self.assertFalse(self.d([run(700, 105), run(500, 105)], rest=False)["ok"])   # but not past the budget

    def test_use_of_an_older_login_does_not_count(self):
        runs = [run(700, 105), run(500, 105), run(300, 105)]              # 315 min, the last ended 195 min ago
        self.assertFalse(self.d(runs)["ok"])
        since = NOW - timedelta(minutes=100)                              # login re-recorded 100 min ago
        self.assertTrue(lr.decide(lr.used_intervals(runs, NOW, since), NOW)["ok"])
        recent = [run(150, 100)]                                          # a run that straddles the re-record: only its tail counts
        iv = lr.used_intervals(recent, NOW, NOW - timedelta(minutes=70))
        self.assertEqual(iv[0][0], NOW - timedelta(minutes=70))

    def test_bypass_and_guard_off(self):
        self.assertTrue(self.d([run(150, 100)], bypass=True)["ok"])
        self.assertTrue(self.d([run(150, 100)], guard=False)["ok"])

    def test_a_run_started_yesterday_counts_only_its_last_24_hours(self):
        iv = lr.used_intervals([run(24 * 60 + 30, 100)], NOW)             # ended 70 min into the window? started 24h30m ago
        self.assertEqual(len(iv), 1)
        self.assertEqual(iv[0][0], NOW - timedelta(hours=24))


class RecordedAt(unittest.TestCase):
    def state(self, exp, gz=False):
        import base64, gzip, json
        raw = json.dumps({"cookies": [{"name": "sessionid", "value": "v", "domain": ".instagram.com", "expires": exp},
                                       {"name": "sessionid", "value": "t", "domain": ".threads.net", "expires": exp + 999}]}).encode()
        return base64.b64encode(gzip.compress(raw) if gz else raw).decode()

    def test_recorded_time_is_expiry_minus_a_year(self):
        issued = NOW - timedelta(hours=6)
        exp = (issued + timedelta(days=365)).timestamp()
        self.assertEqual(lr.login_recorded_at(self.state(exp, gz=True), now=NOW), issued)

    def test_newest_of_two_secrets_and_junk_ignored(self):
        old = (NOW - timedelta(days=3) + timedelta(days=365)).timestamp()
        new = (NOW - timedelta(hours=2) + timedelta(days=365)).timestamp()
        self.assertEqual(lr.login_recorded_at("junk", self.state(old), self.state(new), now=NOW), NOW - timedelta(hours=2))

    def test_nothing_readable_is_none(self):
        self.assertIsNone(lr.login_recorded_at("", "junk", now=NOW))


if __name__ == "__main__":
    unittest.main()
