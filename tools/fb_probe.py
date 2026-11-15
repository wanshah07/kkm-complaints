#!/usr/bin/env python3
"""
What exactly does Facebook do with the saved login when a runner presents it?

The status strip can only say "Facebook shows a login page". That is one of several different things, and each has
a different fix:

  no_login_in_file   the saved session holds no Facebook login cookies (c_user / xs): Facebook was never signed in
                     in the window that recorded it, or the cookies were dropped when the file was shrunk
  expired            the cookies are past their date
  rejected           the cookies are in date, Facebook cleared them on first use (it does not accept this login
                     from this machine): recording again the same way ends the same way
  checkpoint         Facebook wants the account confirmed (a code, a photo, a "was this you?")
  signed_in          the login is accepted; if pages still come back short, the fault is in how pages are read

Prints names and dates only: never a cookie value, never the account's name. The repo and its logs are public.
Loads ONLY Facebook's cookies, so the Instagram and Threads logins in the same file are not presented anywhere.

  python tools/fb_probe.py [--handle cetaphilmalaysia] [--out fb_probe_out]
"""
import argparse
import base64
import gzip
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scraper"))

FB_WORDS = ("facebook.com", "fb.com")
LOGIN_COOKIES = ("c_user", "xs")
KNOWN = ("c_user", "xs", "datr", "sb", "fr", "presence", "wd", "locale", "ps_l", "ps_n", "dpr", "oo", "usida", "i_user")
REASONS = (
    (r"session (has )?expired|log in again|logged out", "Facebook says the session ended and asks to log in again"),
    (r"suspicious|unusual (login|activity)|we noticed|was this you|confirm (it'?s|your) (you|identity)", "Facebook flagged the login as unusual"),
    (r"account (has been )?(locked|disabled|suspended)|we'?ve locked", "Facebook locked or disabled the account"),
    (r"enter (the )?(login |security )?code|two-factor|approve (the|your) login", "Facebook asks for a login code or approval"),
    (r"captcha|security check|are you a robot", "Facebook shows a security check"),
)


def load_state(raw_b64):
    raw = base64.b64decode(raw_b64)
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def fb_cookies(state):
    return [c for c in (state.get("cookies") or []) if any(w in str(c.get("domain", "")).lower() for w in FB_WORDS)]


def describe_file(state, now=None):
    """What the saved session holds for Facebook: names, days left, never a value."""
    now = now or time.time()
    rows = {}
    for c in fb_cookies(state):
        name = str(c.get("name", ""))
        if name not in KNOWN:
            continue
        exp = c.get("expires")
        days = None if exp is None or exp <= 0 else round((float(exp) - now) / 86400.0, 1)
        rows[name] = {"days_left": days, "has_value": bool(c.get("value")), "domain": str(c.get("domain", ""))}
    held = [n for n in LOGIN_COOKIES if rows.get(n, {}).get("has_value")]
    if len(held) < len(LOGIN_COOKIES):
        verdict = "no_login_in_file"
    elif any(rows[n]["days_left"] is not None and rows[n]["days_left"] <= 0 for n in LOGIN_COOKIES):
        verdict = "expired"
    else:
        verdict = "in_date"
    # A Facebook login cookie is issued for 365 days, so its age says when the session was recorded.
    age = None
    d = rows.get("xs", {}).get("days_left")
    if d is not None and 0 < d <= 400:
        age = round(365 - d, 1)
    return {"cookies": rows, "verdict": verdict, "recorded_days_ago": age}


def classify(file_verdict, url, held_after, has_login_form, body):
    """One word for what Facebook did, from what the page and the cookie jar showed after loading."""
    low_url = (url or "").lower()
    if file_verdict in ("no_login_in_file", "expired"):
        return file_verdict
    if "/checkpoint" in low_url or "/two_step_verification" in low_url or "/login/device-based" in low_url:
        return "checkpoint"
    signed = all(n in held_after for n in LOGIN_COOKIES)
    if signed and not has_login_form and "/login" not in low_url:
        return "signed_in"
    if not signed:
        return "rejected"
    return "checkpoint" if re.search(r"confirm|code|verify|was this you", (body or "").lower()) else "rejected"


def reason_in(body):
    low = (body or "").lower()
    for pat, text in REASONS:
        if re.search(pat, low):
            return text
    return ""


def clean_url(u):
    return re.sub(r"[?#].*$", "", u or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--handle", default="cetaphilmalaysia")
    ap.add_argument("--out", default=os.path.join(HERE, "fb_probe_out"))
    args = ap.parse_args()
    raw_b64 = (os.environ.get("PW_STORAGE_STATE_B64") or "").strip()
    if not raw_b64:
        print("RESULT no_session  PW_STORAGE_STATE_B64 is empty")
        return 1
    try:
        state = load_state(raw_b64)
    except Exception as e:
        print("RESULT unreadable_session  %s" % type(e).__name__)
        return 1
    info = describe_file(state)
    print("FILE verdict=%s recorded_about=%s day(s) ago" % (info["verdict"], info["recorded_days_ago"]))
    for n in KNOWN:
        if n in info["cookies"]:
            r = info["cookies"][n]
            print("FILE cookie %-9s days_left=%-7s value=%s domain=%s" % (n, r["days_left"], "yes" if r["has_value"] else "EMPTY", r["domain"]))
    missing = [n for n in LOGIN_COOKIES if n not in info["cookies"]]
    if missing:
        print("FILE missing login cookie(s): %s" % ", ".join(missing))
    print("FILE other sites in the file: %s" % ", ".join(sorted({re.sub(r"^\.", "", str(c.get("domain", ""))) for c in state.get("cookies") or []
                                                                 if not any(w in str(c.get("domain", "")).lower() for w in FB_WORDS)})[:12]))

    import yaml
    from playwright.sync_api import sync_playwright
    with open(os.path.join(HERE, "..", "scraper", "config.yaml"), "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    run_cfg = cfg.get("run", {}) or {}
    ua = run_cfg.get("user_agent")
    print("BROWSER user_agent=%s locale=%s timezone=%s" % (ua or "(Chromium default, headless)", run_cfg.get("locale", "en-MY"), run_cfg.get("timezone", "Asia/Kuala_Lumpur")))
    os.makedirs(args.out, exist_ok=True)
    only_fb = {"cookies": fb_cookies(state), "origins": []}
    verdicts = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            ctx = browser.new_context(viewport={"width": 1280, "height": 1600}, locale=run_cfg.get("locale", "en-MY"),
                                      timezone_id=run_cfg.get("timezone", "Asia/Kuala_Lumpur"), user_agent=ua, storage_state=only_fb)
            ctx.set_default_timeout(20000)
            page = ctx.new_page()
            print("BROWSER reports user agent: %s" % page.evaluate("navigator.userAgent"))
            steps = (("home", "https://www.facebook.com/"), ("page", "https://www.facebook.com/%s" % args.handle),
                     ("mbasic", "https://mbasic.facebook.com/%s" % args.handle))
            for key, url in steps:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(4000)
                except Exception as e:
                    print("STEP %-6s could not load: %s" % (key, type(e).__name__))
                    continue
                held = sorted({c.get("name") for c in ctx.cookies() if c.get("value") and any(w in str(c.get("domain", "")).lower() for w in FB_WORDS)} & set(KNOWN))
                try:
                    form = page.locator("input[name='email'], input[name='pass']").first.is_visible(timeout=1000)
                except Exception:
                    form = False
                try:
                    body = page.locator("body").inner_text(timeout=3000) or ""
                except Exception:
                    body = ""
                v = classify(info["verdict"], page.url, held, form, body)
                verdicts.append(v)
                links = 0
                try:
                    links = page.locator("a[href*='/posts/'], a[href*='/videos/'], a[href*='/reel/'], a[href*='story.php'], a[href*='/photos/']").count()
                except Exception:
                    pass
                print("STEP %-6s verdict=%-11s url=%s login_form=%s post_links=%d cookies_held=%s" % (key, v, clean_url(page.url), form, links, ",".join(held) or "none"))
                why = reason_in(body)
                if why:
                    print("STEP %-6s reason: %s" % (key, why))
                if v != "signed_in" or key != "home":      # a signed-in home feed shows the account: no picture of that
                    try:
                        page.screenshot(path=os.path.join(args.out, "fb_%s.png" % key), full_page=False)
                    except Exception:
                        pass
        finally:
            browser.close()
    final = verdicts[0] if verdicts else "unknown"
    print("RESULT %s" % final)
    return 0


if __name__ == "__main__":
    sys.exit(main())
