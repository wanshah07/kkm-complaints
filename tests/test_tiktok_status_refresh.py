"""The TikTok pill is refreshed by a workflow_run on 'TikTok auto discovery' (its own cron fires late)."""
import unittest
from pathlib import Path
import yaml

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"


class TikTokStatusRefresh(unittest.TestCase):
    def setUp(self):
        self.doc = yaml.safe_load((WF / "tiktok-status-refresh.yml").read_text(encoding="utf-8"))
        self.on = self.doc.get("on") or self.doc[True]

    def test_watches_the_discovery_workflow_by_its_exact_name(self):
        name = yaml.safe_load((WF / "tiktok-auto.yml").read_text(encoding="utf-8"))["name"]
        self.assertEqual(self.on["workflow_run"]["workflows"], [name])
        self.assertEqual(self.on["workflow_run"]["types"], ["completed"])

    def test_only_needs_actions_write_and_dispatches_platform_status(self):
        self.assertEqual(self.doc["permissions"], {"actions": "write"})
        steps = self.doc["jobs"]["refresh"]["steps"]
        self.assertTrue(any("platform-status.yml" in s.get("run", "") for s in steps))
        self.assertTrue((WF / "platform-status.yml").exists())

    def test_touches_no_secret(self):
        text = (WF / "tiktok-status-refresh.yml").read_text(encoding="utf-8")
        self.assertNotIn("secrets.", text.replace("# ", ""))


if __name__ == "__main__":
    unittest.main()
