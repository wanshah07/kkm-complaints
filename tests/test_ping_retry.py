"""The run's first call to the backend (ping) waits and tries again when the backend is silent (27 Nov 2026)."""
import unittest

import requests

from tests import _paths  # noqa: F401
import uploader


class FakeResp:
    def __init__(self, data=None, text="", status=200):
        self._d, self.text, self.status_code, self.headers, self.url = data, text, status, {}, "https://x/exec"

    def json(self):
        if self._d is None:
            raise ValueError("not json")
        return self._d


class FakeSession:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def get(self, url, params=None, timeout=None):
        self.calls.append(timeout)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def client(answers):
    c = uploader.AppsScriptClient(url="https://x/exec", token="t")
    c.session = FakeSession(answers)
    return c


class PingRetry(unittest.TestCase):
    def test_answers_first_time(self):
        c = client([FakeResp({"ok": True})])
        self.assertEqual(c.ping(pause=0), {"ok": True})
        self.assertEqual(len(c.session.calls), 1)

    def test_silent_twice_then_answers(self):
        c = client([requests.ReadTimeout("silent"), requests.ConnectionError("reset"), FakeResp({"ok": True})])
        self.assertEqual(c.ping(pause=0), {"ok": True})
        self.assertEqual(len(c.session.calls), 3)
        self.assertTrue(all(t <= 60 for t in c.session.calls))

    def test_silent_every_time_fails_and_says_so(self):
        c = client([requests.ReadTimeout("silent")] * 3)
        with self.assertRaises(RuntimeError) as e:
            c.ping(pause=0)
        self.assertIn("no answer to 3 pings", str(e.exception))
        self.assertEqual(len(c.session.calls), 3)

    def test_a_non_json_answer_is_not_retried(self):
        c = client([FakeResp(None, "<html>Sign in</html>"), FakeResp({"ok": True})])
        with self.assertRaises(RuntimeError) as e:
            c.ping(pause=0)
        self.assertIn("did not return JSON", str(e.exception))
        self.assertEqual(len(c.session.calls), 1)


if __name__ == "__main__":
    unittest.main()
