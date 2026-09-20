#!/usr/bin/env python3
"""Ask an OpenAI-compatible endpoint the questions that decide whether it can run
the KKM complaint review -- and ask them three different ways, because HOW the
request is made turns out to matter.

api.mireld.my resolves from a GitHub runner, accepts the TCP connection,
completes TLS, and then sends nothing at all until the timeout. Silence after a
completed handshake is not a refusal. A refusal is a 401 or a 403 and arrives in
milliseconds; a WAF or a bot filter that has decided it does not like the caller
drops the request on the floor and lets it hang. So the interesting question is
not "is the endpoint up" but "what about this caller does it not like", and the
cheapest hypothesis is the most boring one: urllib announces itself as
`Python-urllib/3.12` and presents a TLS fingerprint no browser has ever had.

Hence three transports, tried in order, each asked the same first question:

  urllib      stdlib, default headers. The baseline that hangs.
  headers     stdlib, but a real browser's User-Agent and Accept headers. If
              this works, the block is a header rule and the scraper needs two
              lines changed.
  browser     a real Chromium, driven by Playwright, doing fetch() from a page.
              Genuine browser TLS fingerprint, genuine header set, genuine
              everything. If ONLY this works, the block is fingerprinting, and
              the route is the browser the scraper is already running.

The first transport that answers is then asked the rest, because a transport
that cannot reach the endpoint cannot tell us anything about its models:

  1 reach     does it answer /v1/models
  2 chat      does a plain chat completion come back
  3 schema    does response_format json_schema + strict work -- evaluator.py
              asks for exactly this and json.loads() the answer
  4 vision    does an inline base64 image go through -- LLM_USE_SCREENSHOT=1
              sends every complaint's screenshot this way

Nothing here writes anywhere. It prints a table and exits non-zero if the
scraper could not be moved to this endpoint as things stand.
"""
import base64, json, os, sys, urllib.error, urllib.request

BASE = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
KEY = os.environ.get("OPENAI_API_KEY") or ""
MODEL = os.environ.get("PROBE_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4.1"
TIMEOUT = int(os.environ.get("PROBE_TIMEOUT") or "45")
ONLY = (os.environ.get("PROBE_TRANSPORT") or "").strip().lower()

# Chrome 128 on Windows -- the same string the scraper already presents to
# Instagram and Facebook, so if this is what unlocks Mireld the scraper is
# already configured to send it.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
BROWSER_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://api.mireld.my",
    "Referer": "https://api.mireld.my/",
}

PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
VERDICT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["Acceptable", "Violation"]},
        "confidence": {"type": "number"},
    },
    "required": ["verdict", "confidence"],
}


# ---------------------------------------------------------------- transports
# Each returns (ok, payload_or_message) and never raises: every question here is
# allowed to come back no, and the answer is the point.

def _urllib(path, body, extra_headers, timeout):
    headers = {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"}
    headers.update(extra_headers or {})
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, json.load(r)
    except urllib.error.HTTPError as e:
        return False, "HTTP %s — %s" % (e.code, e.read().decode("utf-8", "replace")[:300])
    except (urllib.error.URLError, TimeoutError) as e:
        # A read timeout is a BARE TimeoutError, not a URLError, so it has to be
        # named or it escapes as a traceback.
        return False, "no answer — %s" % e
    except json.JSONDecodeError as e:
        return False, "answered, but not with JSON — %s" % e


def t_urllib(path, body=None, timeout=None):
    return _urllib(path, body, None, timeout or TIMEOUT)


def t_headers(path, body=None, timeout=None):
    return _urllib(path, body, BROWSER_HEADERS, timeout or TIMEOUT)


_page = None


def t_browser(path, body=None, timeout=None):
    """Ask from inside a real Chromium, via fetch() on a page.

    This is the transport the scraper itself already has: the KKM job installs
    Chromium and drives it with Playwright, so if this is the only thing that
    reaches Mireld, calling the endpoint through the same browser context is a
    route rather than a workaround.

    The page is navigated to the endpoint's own origin first so the fetch is
    same-origin and no CORS preflight enters the picture -- a CORS failure would
    look like a network failure and would be the wrong answer to the question.
    """
    global _page
    timeout = (timeout or TIMEOUT) * 1000
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "playwright is not installed on this runner"
    try:
        if _page is None:
            pw = sync_playwright().start()
            br = pw.chromium.launch()
            ctx = br.new_context(user_agent=UA, locale="en-MY")
            _page = ctx.new_page()
            try:
                _page.goto(BASE.rsplit("/v1", 1)[0] + "/", wait_until="domcontentloaded", timeout=timeout)
            except Exception:
                # The origin may serve nothing at / -- about:blank still lets
                # fetch() run, it just makes the request cross-origin.
                pass
        r = _page.evaluate(
            """async ([url, key, body, ms]) => {
                const ctrl = new AbortController();
                const t = setTimeout(() => ctrl.abort(), ms);
                try {
                    const res = await fetch(url, {
                        method: body ? 'POST' : 'GET',
                        headers: { 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json' },
                        body: body ? JSON.stringify(body) : undefined,
                        signal: ctrl.signal,
                    });
                    const text = await res.text();
                    return { status: res.status, text: text.slice(0, 4000) };
                } catch (e) {
                    return { status: 0, text: String(e) };
                } finally { clearTimeout(t); }
            }""",
            [BASE + path, KEY, body, timeout],
        )
    except Exception as e:
        return False, "the browser could not make the request — %s" % str(e)[:200]
    if not r or not r.get("status"):
        return False, "no answer — %s" % (r or {}).get("text", "")[:200]
    if r["status"] >= 400:
        return False, "HTTP %s — %s" % (r["status"], r["text"][:300])
    try:
        return True, json.loads(r["text"])
    except json.JSONDecodeError as e:
        return False, "answered, but not with JSON — %s" % e


TRANSPORTS = [
    ("urllib", "stdlib, default headers", t_urllib),
    ("headers", "stdlib, browser User-Agent", t_headers),
    ("browser", "real Chromium via Playwright", t_browser),
]


# ---------------------------------------------------------------- questions

def text_of(payload):
    try:
        return (payload["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return ""


def ask_all(call):
    """The four questions, through whichever transport reached the endpoint."""
    out = {}

    ok, payload = call("/models")
    ids = sorted(m.get("id", "") for m in payload.get("data", [])) if ok else []
    out["reach"] = (ok, "%d models listed" % len(ids) if ok else payload)
    out["_ids"] = ids

    def chat(messages, **extra):
        body = {"model": MODEL, "messages": messages, "temperature": 0, "max_tokens": 64}
        body.update(extra)
        return call("/chat/completions", body)

    ok, payload = chat([{"role": "user", "content": "Reply with the single word: alive"}])
    out["chat"] = (ok and bool(text_of(payload)), text_of(payload) if ok else payload)

    ok, payload = chat(
        [{"role": "user", "content": "A cream promises to cure eczema in 3 days. Verdict?"}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "npra_verdict", "strict": True, "schema": VERDICT_SCHEMA}},
    )
    if ok:
        raw = text_of(payload)
        try:
            parsed = json.loads(raw or "{}")
            good = "verdict" in parsed
            out["schema"] = (good, raw[:200] if good else "no verdict key: " + raw[:200])
        except json.JSONDecodeError:
            # The dangerous case: the endpoint ACCEPTS response_format and then
            # ignores it. evaluator.py json.loads() this and would throw into the
            # rules-only fallback on every single post, silently.
            out["schema"] = (False, "accepted response_format but answered prose: " + raw[:200])
    else:
        out["schema"] = (False, payload)

    b64 = base64.standard_b64encode(PIXEL).decode()
    ok, payload = chat([{"role": "user", "content": [
        {"type": "text", "text": "What colour is this image? One word."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
    ]}])
    out["vision"] = (ok and bool(text_of(payload)), text_of(payload) if ok else payload)
    return out


def main():
    if not KEY:
        sys.exit("OPENAI_API_KEY is not set on this repository.")
    print("Endpoint : " + BASE)
    print("Model    : " + MODEL)
    print("Timeout  : %ss per call\n" % TIMEOUT)

    print("Which transports can reach it at all")
    print("-" * 72)
    reached = None
    tried = []
    for name, desc, call in TRANSPORTS:
        if ONLY and ONLY != name:
            continue
        ok, detail = call("/models", timeout=TIMEOUT)
        tried.append((name, ok, detail))
        print("%-4s %-9s %-32s %s" % ("PASS" if ok else "FAIL", name, desc, str(detail)[:120] if not ok else "answered"))
        if ok and reached is None:
            reached = (name, call)
    print("-" * 72)

    if reached is None:
        print("\nNOTHING REACHED IT. Every transport was met with the same answer, so this is "
              "not about headers or TLS fingerprinting — the endpoint is not serving this "
              "machine at all. The scraper cannot be moved here.")
        return 1

    name, call = reached
    print("\nAsking the rest through the first transport that answered: %s\n" % name)
    r = ask_all(call)

    if r["_ids"]:
        print("Models this endpoint enables:")
        for i in r["_ids"]:
            print("  " + i)
        if MODEL not in r["_ids"]:
            print("\n!! %s is NOT in that list. Set PROBE_MODEL or OPENAI_MODEL to one of the "
                  "above, or questions 2 to 4 measure nothing.\n" % MODEL)
        print()

    labels = {
        "reach":  "1 reachable (%s)" % name,
        "chat":   "2 plain chat completion",
        "schema": "3 json_schema strict  (evaluator needs this)",
        "vision": "4 inline image         (LLM_USE_SCREENSHOT=1 needs this)",
    }
    print("-" * 72)
    for k in ("reach", "chat", "schema", "vision"):
        good, detail = r[k]
        print("%-4s %-44s %s" % ("PASS" if good else "FAIL", labels[k], str(detail)[:150]))
    print("-" * 72)

    if name != "urllib":
        print("\nNOTE: the stdlib default did NOT reach this endpoint but %s did. That means the "
              "block is about the CALLER, not the network, and the openai SDK — which is plain "
              "Python HTTP — would be blocked the same way urllib was. Moving the scraper here "
              "means routing its LLM calls through %s." % (name, name))

    need = ("reach", "chat", "schema")
    failed = [k for k in need if not r[k][0]]
    if failed:
        print("\nNOT READY. %s came back no, so this endpoint cannot replace the current reviewer "
              "without the scraper silently dropping to rules-only verdicts."
              % ", ".join(labels[k].split()[1] for k in failed))
        return 1
    if not r["vision"][0]:
        print("\nUSABLE WITHOUT SCREENSHOTS. Chat and structured output work, but the image part "
              "was refused — set LLM_USE_SCREENSHOT=0 before switching, or every post's "
              "screenshot fails the call and falls back to rules.")
        return 0
    print("\nREADY. All four answered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
