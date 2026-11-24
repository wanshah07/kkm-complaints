"""The scraper workflow has two crons; the daily one must give a scheduled (input-less) run its own defaults."""
import re
import unittest
from pathlib import Path

import yaml

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "scraper.yml"
DAILY = "7 20 * * 0-4,6"
WEEKLY = "30 15 * * 5"


class WorkflowSchedule(unittest.TestCase):
    def setUp(self):
        self.text = WF.read_text(encoding="utf-8")
        self.doc = yaml.safe_load(self.text)

    def test_two_crons(self):
        on = self.doc.get("on") or self.doc[True]
        crons = [c["cron"] for c in on["schedule"]]
        self.assertEqual(crons, [WEEKLY, DAILY])

    def test_daily_defaults_keyed_on_the_exact_cron(self):
        for name, value in (("IN_ONLY_TYPE", "brand"), ("IN_ORDER", "oldest"), ("IN_MAX_OPEN", "15")):
            m = re.search(rf"{name}: (.*)", self.text)
            self.assertIsNotNone(m, name)
            self.assertIn(f"github.event.schedule == '{DAILY}'", m.group(1), name)
            self.assertIn(f"'{value}'", m.group(1), name)

    def test_weekly_run_keeps_every_target_type(self):
        # the Friday cron string appears in no default expression, so its inputs fall through to ''
        env = self.doc["jobs"]["scrape"]["steps"]
        step = next(s for s in env if s.get("name") == "Run scraper")
        for k in ("IN_ONLY_TYPE", "IN_ORDER", "IN_MAX_OPEN"):
            self.assertNotIn(WEEKLY, step["env"][k])

    def test_person_sweep_not_in_daily(self):
        m = re.search(r"IN_ONLY_TYPE: (.*)", self.text).group(1)
        self.assertNotIn("person", m)

    def test_since_input_reaches_the_scraper_and_never_defaults_on_a_schedule(self):
        # Wan, 24 Nov 2026: "Skintific ... focus only this year". A one-off date floor for a dispatch; the
        # scheduled runs must keep the config.yaml floor, so the env default is blank on every cron.
        on = self.doc.get("on") or self.doc[True]
        self.assertIn("since", on["workflow_dispatch"]["inputs"])
        m = re.search(r"IN_SINCE: (.*)", self.text).group(1)
        self.assertNotIn("schedule", m)
        self.assertIn('--since "$IN_SINCE"', self.text)
        main = (WF.parent.parent.parent / "scraper" / "main.py").read_text(encoding="utf-8")
        self.assertIn('run_cfg["min_post_date"] = args.since.strip()', main)


if __name__ == "__main__":
    unittest.main()
