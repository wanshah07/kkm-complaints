#!/usr/bin/env python3
"""
Does Threads accept the saved login from a runner?

Presents ONLY the Threads cookies of the saved session (never Instagram's, so a sweep that is using the Instagram
login at the same moment is not disturbed), opens a public Threads profile, and reports: whether the login cookie is
still held after the page loaded, whether the page still offers to log in, and how many posts it lists while
scrolling (a signed-out visitor sees 4 to 5). Prints counts and names only; takes no picture (a signed-in page can
show the account) and never prints a cookie value.

  python tools/threads_login_probe.py --handle hygr.my
"""
import argparse, base64, gzip, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scraper"))

LINKS = r"""() => { const s = []; for (const a of document.querySelectorAll('a[href]')) { const m = (a.href || '').match(/threads\.(?:net|com)\/@([^\/]+)\/post\/([A-Za-z0-9_-]{5,})/); if (m && !s.includes(m[1] + '/' + m[2])) s.push(m[1] + '/' + m[2]); } return s; }"""


def threads_cookies(state):
    return [c for c in state.get("cookies", []) if "threads." in str(c.get("domain", "")).lower()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--handle", default="hygr.my")
    args = ap.parse_args()
    raw_b64 = (os.environ.get("PW_STORAGE_STATE_B64") or "").strip()
    if not raw_b64:
        print("THREADS no secret")
        return 1
    raw = base64.b64decode(raw_b64)
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    state = json.loads(raw)
    cookies = threads_cookies(state)
    names = sorted({c.get("name") for c in cookies})
    login = [c for c in cookies if c.get("name") == "sessionid" and c.get("value")]
    print("THREADS file: %d threads cookie(s), names=%s, login cookie present=%s" % (len(cookies), names, bool(login)))
    if not login:
        print("THREADS RESULT no_login_in_file")
        return 1
    import yaml
    from playwright.sync_api import sync_playwright
    with open(os.path.join(HERE, "..", "scraper", "config.yaml"), "r", encoding="utf-8") as f:
        run_cfg = (yaml.safe_load(f) or {}).get("run", {}) or {}
    results = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            for label, st in (("with_login", {"cookies": cookies, "origins": []}), ("no_login", None)):
                kw = dict(viewport={"width": 1280, "height": 1600}, locale=run_cfg.get("locale", "en-MY"),
                          timezone_id=run_cfg.get("timezone", "Asia/Kuala_Lumpur"), user_agent=run_cfg.get("user_agent"))
                if st:
                    kw["storage_state"] = st
                ctx = browser.new_context(**kw)
                ctx.set_default_timeout(20000)
                pg = ctx.new_page()
                pg.goto("https://www.threads.com/@%s" % args.handle, wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(4500)
                series = []
                for _ in range(8):
                    series.append(len(pg.evaluate(LINKS)))
                    pg.mouse.wheel(0, 2600)
                    pg.wait_for_timeout(1500)
                held = sorted({c.get("name") for c in ctx.cookies() if c.get("value") and "threads." in str(c.get("domain", "")).lower()})
                try:
                    txt = pg.locator("body").inner_text(timeout=3000) or ""
                except Exception:
                    txt = ""
                modal = bool(re.search(r"say more with threads|log in or sign up", txt, re.I))
                url = re.sub(r"[?#].*$", "", pg.url)
                print("THREADS %-10s url=%s sessionid_held=%s login_prompt_text=%s links_by_scroll=%s" % (label, url, "sessionid" in held, modal, series))
                results[label] = (series[-1], "sessionid" in held, modal)
                ctx.close()
                time.sleep(4)
        finally:
            browser.close()
    w, n = results.get("with_login"), results.get("no_login")
    verdict = "accepted" if w and w[1] and not w[2] else "rejected" if w and not w[1] else "unclear"
    print("THREADS RESULT %s (posts with login %s, without %s)" % (verdict, w[0] if w else None, n[0] if n else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
