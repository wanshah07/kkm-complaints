import os, unittest
from unittest import mock
from tests import _paths  # noqa: F401
import model_fallback as mf


class Err(Exception):
    def __init__(self, msg="", status=None):
        super().__init__(msg)
        self.status_code = status


class Timeout(Exception):
    pass


class Classify(unittest.TestCase):
    def test_off_kinds(self):
        self.assertEqual(mf.is_off(Err("model: claude-x", 404)), "permanent")
        self.assertEqual(mf.is_off(Err("The model `x` does not exist", 400)), "permanent")
        self.assertEqual(mf.is_off(Err("model has been deprecated", 500)), "permanent")
        self.assertEqual(mf.is_off(Err("Overloaded", 529)), "transient")
        self.assertEqual(mf.is_off(Err("slow down", 429)), "transient")
        self.assertEqual(mf.is_off(Err("boom", 503)), "transient")
        self.assertEqual(mf.is_off(Timeout("read timed out")), "transient")
        self.assertEqual(mf.is_off(Err("No available channel for model kimi", None)), "permanent")

    def test_not_off(self):
        self.assertIsNone(mf.is_off(Err("invalid x-api-key", 401)))
        self.assertIsNone(mf.is_off(Err("max_tokens too large", 400)))
        self.assertIsNone(mf.is_off(ValueError("bad json")))
        self.assertIsNone(mf.is_off(RuntimeError("model refused the request (stop_reason=refusal)")))


class Chain(unittest.TestCase):
    def test_chain_building(self):
        self.assertEqual(mf.chain("a", "b, c a", ("z",)), ["a", "b", "c"])
        self.assertEqual(mf.chain("a", "", ("b", "a")), ["a", "b"])
        self.assertEqual(mf.chain("a", ""), ["a"])

    def test_first_model_answers(self):
        calls = []
        r = mf.run("p", ["a", "b"], lambda m: calls.append(m) or "ok-" + m, mf.Board())
        self.assertEqual((r, calls), ("ok-a", ["a"]))

    def test_dead_model_skipped_and_remembered(self):
        b, calls = mf.Board(), []

        def call(m):
            calls.append(m)
            if m == "a":
                raise Err("not found", 404)
            return m
        self.assertEqual(mf.run("p", ["a", "b"], call, b), "b")
        self.assertEqual(mf.run("p", ["a", "b"], call, b), "b")
        self.assertEqual(calls, ["a", "b", "b"])           # a was asked once, not once per post

    def test_primary_comes_back_after_cooldown(self):
        t = [0.0]
        b = mf.Board(clock=lambda: t[0])
        state = {"a_up": False}

        def call(m):
            if m == "a" and not state["a_up"]:
                raise Err("Overloaded", 529)
            return m
        self.assertEqual(mf.run("p", ["a", "b"], call, b), "b")
        t[0] += mf.TRANSIENT_SECONDS + 1
        state["a_up"] = True
        self.assertEqual(mf.run("p", ["a", "b"], call, b), "a")

    def test_other_errors_are_raised_not_swallowed(self):
        with self.assertRaises(ValueError):
            mf.run("p", ["a", "b"], lambda m: (_ for _ in ()).throw(ValueError("bad reply")), mf.Board())

    def test_all_off_raises_and_then_asks_only_one(self):
        b, calls = mf.Board(), []

        def call(m):
            calls.append(m)
            raise Err("Overloaded", 529)
        with self.assertRaises(Err):
            mf.run("p", ["a", "b", "c"], call, b)
        self.assertEqual(calls, ["a", "b", "c"])
        calls.clear()
        with self.assertRaises(Err):
            mf.run("p", ["a", "b", "c"], call, b)
        self.assertEqual(len(calls), 1)                      # everything is cooling down: one try, not three

    def test_providers_do_not_share_a_board_entry(self):
        b = mf.Board()
        b.mark("p1|a", "permanent", "x")
        self.assertEqual(mf.run("p2", ["a"], lambda m: m, b), "a")


class EvaluatorUsesTheChain(unittest.TestCase):
    def test_anthropic_falls_to_next_model(self):
        try:
            import anthropic  # noqa: F401
        except ImportError:
            self.skipTest("anthropic SDK not installed")
        import evaluator
        seen = []

        def fake(client, sdk, inp, model):
            seen.append(model)
            if model == "claude-haiku-4-5":
                raise Err("model: claude-haiku-4-5", 404)
            return {"verdict": "Acceptable", "reviewer": "anthropic:" + model}
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k", "ANTHROPIC_MODEL": "claude-haiku-4-5",
                                          "ANTHROPIC_MODEL_FALLBACKS": "claude-sonnet-4-6"}), \
                mock.patch.object(evaluator, "_anthropic_call", fake), \
                mock.patch.object(mf, "BOARD", mf.Board()):
            out = evaluator._review_anthropic(object())
        self.assertEqual(seen, ["claude-haiku-4-5", "claude-sonnet-4-6"])
        self.assertEqual(out["reviewer"], "anthropic:claude-sonnet-4-6")


if __name__ == "__main__":
    unittest.main()


class RealSdkErrors(unittest.TestCase):
    """The classification is only worth anything if it reads the exceptions the SDKs really raise."""

    class Req:                      # what the SDKs read off an httpx request when they build an error
        headers, url, method = {}, "https://example.test/v1", "POST"

    class Resp:
        def __init__(self, c):
            self.status_code, self.headers, self.request = c, {}, RealSdkErrors.Req()

    @classmethod
    def response(cls, code):
        """A real httpx response when httpx is installed (CI has it), else the stub: the SDKs read headers and the request off it."""
        try:
            import httpx
            return httpx.Response(code, request=httpx.Request("POST", "https://example.test/v1"))
        except ImportError:
            return cls.Resp(code)

    @classmethod
    def request(cls):
        try:
            import httpx
            return httpx.Request("POST", "https://example.test/v1")
        except ImportError:
            return cls.Req()

    def test_anthropic_and_openai(self):
        try:
            import anthropic, openai
        except ImportError:
            self.skipTest("SDKs not installed")
        R = self.response
        for cls, code, msg, want in [
                (anthropic.NotFoundError, 404, "model: claude-x", "permanent"),
                (anthropic.InternalServerError, 529, "Overloaded", "transient"),
                (anthropic.RateLimitError, 429, "rate", "transient"),
                (anthropic.AuthenticationError, 401, "invalid x-api-key", None),
                (anthropic.BadRequestError, 400, "max_tokens too big", None),
                (openai.NotFoundError, 404, "The model `kimi-x` does not exist", "permanent"),
                (openai.AuthenticationError, 401, "bad key", None)]:
            self.assertEqual(mf.is_off(cls(msg, response=R(code), body=None)), want, f"{cls.__name__} {code}")
        self.assertEqual(mf.is_off(anthropic.APIConnectionError(request=self.request())), "transient")
        self.assertEqual(mf.is_off(openai.APITimeoutError(request=self.request())), "transient")
