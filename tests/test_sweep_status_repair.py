import unittest
from tests import _paths  # noqa: F401
import sweep_status_repair as r

S = {
    "a\x01instagram": {"brand": "A", "platform": "Instagram", "handle": "a", "sweep_at": "2026-11-15T17:25:05Z", "sweep_note": "login wall (provide PW_STORAGE_STATE_B64)"},
    "a\x01threads": {"brand": "A", "platform": "Threads", "handle": "a", "sweep_at": "2026-11-15T17:25:05Z", "sweep_note": ""},
    "a\x01facebook": {"brand": "A", "platform": "Facebook", "handle": "a", "sweep_at": "2026-11-15T17:25:05Z"},
    "b\x01instagram": {"brand": "B", "platform": "Instagram", "handle": "b", "sweep_at": "2026-11-15T10:19:00Z"},
    "c\x01instagram": {"brand": "C", "platform": "Instagram", "handle": "c", "sweep_at": ""},
}


class Repair(unittest.TestCase):
    def test_only_the_window_and_the_platforms_asked_for(self):
        got = r.pick(S, "2026-11-15T17:20:00Z", "2026-11-15T17:30:00Z", ["Instagram", "Threads"])
        self.assertEqual([(b, p) for b, p, *_ in got], [("A", "Instagram"), ("A", "Threads")])

    def test_update_clears_only_the_sweep_fields(self):
        up = r.updates_for(r.pick(S, "2026-11-15T17:20:00Z", "2026-11-15T17:30:00Z", ["Instagram"]))
        self.assertEqual(up, [{"brand": "A", "platform": "Instagram", "handle": "a", "sweep_at": "", "sweep_posts": 0, "sweep_note": ""}])
        for u in up:
            self.assertFalse({"state", "detail", "checked_at", "posts", "suggest"} & set(u))     # the account check's record is left alone

    def test_nothing_in_window(self):
        self.assertEqual(r.pick(S, "2026-11-16T00:00:00Z", "2026-11-16T01:00:00Z", ["Instagram"]), [])


if __name__ == "__main__":
    unittest.main()
