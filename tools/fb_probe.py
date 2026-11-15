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


POST_LINK = re.compile(r"/(posts|videos|reel|photos?)/[^?#]+|story\.php\?[^#]*|/photo/?\?fbid=\d+|/watch/?\?v=\d+|permalink\.php\?[^#]*")


def public_links(page):
    """Post links on the page, without Facebook's tracking tail, in the order they appear."""
    try:
        hrefs = page.evaluate("() => [...document.querySelectorAll('a[href]')].map(a => a.href)")
    except Exception:
        return []
    out = []
    for h in hrefs:
        if POST_LINK.search(h):
            h = re.split(r"[?&]__(?:cft|tn|xts)__", h)[0]
            if h not in out:
                out.append(h)
    return out


def close_dialog(page):
    """Press the X of Facebook's "See more from ..." box, exactly as a visitor would. 1 when one was closed."""
    try:
        x = page.locator("div[role='dialog'] [aria-label='Close']").first
        if x.is_visible(timeout=500):
            x.click()
            page.wait_for_timeout(600)
            return 1
    except Exception:
        pass
    return 0


PAGE_ID_PATTERNS = (("pageID", r'"pageID":"(\d{5,})"'), ("delegate_page", r'"delegate_page":\{[^}]*?"id":"(\d{5,})"'),
                    ("fb_page_url", r"fb://page/\?id=(\d{5,})"), ("fb_profile_url", r"fb://profile/(\d{5,})"),
                    ("userID", r'"userID":"(\d{5,})"'), ("page_id", r'"page_id":"(\d{5,})"'),
                    ("associated_page_id", r'"associated_page_id":"(\d{5,})"'))

CARDS_JS = """() => {
  const out = [];
  const leaves = [...document.querySelectorAll('span,div')].filter(e => e.childElementCount === 0 && /^Library ID:\\s*\\d+/.test((e.textContent || '').trim()));
  for (const s of leaves) {
    let el = s, hops = 0;
    while (el.parentElement && el.parentElement !== document.body && !/Sponsored/.test(el.innerText || '') && hops < 14) { el = el.parentElement; hops++; }
    const r = el.getBoundingClientRect();
    out.push({ id: s.textContent.match(/\\d+/)[0], hops, w: Math.round(r.width), h: Math.round(r.height), tag: el.tagName,
               sponsored: /Sponsored/.test(el.innerText || ''), ids_inside: ((el.innerText || '').match(/Library ID:/g) || []).length, text: (el.innerText || '') });
  }
  return out;
}"""


def ads_probe(ctx, handle, out_dir):
    pg = ctx.new_page()
    try:
        pg.goto("https://www.facebook.com/%s" % handle, wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(3000)
        html = pg.content()
        found = {}
        for name, pat in PAGE_ID_PATTERNS:
            vals = re.findall(pat, html)
            if vals:
                found[name] = sorted(set(vals), key=vals.count, reverse=True)[:3]
        print("ADPAGE %s id candidates: %s" % (handle, found))
        page_id = next((found[k][0] for k in ("pageID", "delegate_page", "fb_page_url", "associated_page_id", "page_id", "fb_profile_url", "userID") if k in found), "")
        if not page_id:
            print("ADPAGE no page id found in the public page")
            return
        url = "https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=MY&view_all_page_id=%s" % page_id
        pg.goto(url, wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(7000)
        series = []
        for i in range(8):
            series.append(len(pg.evaluate(CARDS_JS)))
            pg.mouse.wheel(0, 3000)
            pg.wait_for_timeout(1800)
        cards = pg.evaluate(CARDS_JS)
        body = pg.locator("body").inner_text(timeout=4000) or ""
        res = re.search(r"~?([\d,]+)\s+results?", body)
        print("ADLIST page_id=%s redirected_to_login=%s results_line=%s cards_by_scroll=%s final=%d" % (page_id, "/login" in pg.url, res.group(0) if res else "none", series, len(cards)))
        for c in cards[:3]:
            print("ADLIST card id=%s hops=%s box=%sx%s tag=%s sponsored=%s ids_inside=%s text_chars=%d" % (c["id"], c["hops"], c["w"], c["h"], c["tag"], c["sponsored"], c["ids_inside"], len(c["text"])))
            print("ADLIST card text: %s" % json.dumps(c["text"][:900], ensure_ascii=False))
        if not cards:
            print("ADLIST page text head: %s" % json.dumps(body[:500], ensure_ascii=False))
            return
        one = pg.context.new_page()
        one.goto("https://www.facebook.com/ads/library/?id=%s" % cards[0]["id"], wait_until="domcontentloaded", timeout=45000)
        one.wait_for_timeout(7000)
        oc = one.evaluate(CARDS_JS)
        obody = one.locator("body").inner_text(timeout=4000) or ""
        print("ADONE url=%s redirected_to_login=%s cards=%d body_chars=%d dialogs=%d" % (clean_url(one.url) + "?id=" + cards[0]["id"], "/login" in one.url, len(oc), len(obody), one.locator("div[role='dialog']").count()))
        for c in oc[:2]:
            print("ADONE card id=%s hops=%s box=%sx%s sponsored=%s ids_inside=%s text_chars=%d same_text_as_list=%s" % (c["id"], c["hops"], c["w"], c["h"], c["sponsored"], c["ids_inside"], len(c["text"]), c["text"] == cards[0]["text"]))
            print("ADONE card text: %s" % json.dumps(c["text"][:1200], ensure_ascii=False))
        print("ADONE body head: %s" % json.dumps(obody[:700], ensure_ascii=False))
        one.screenshot(path=os.path.join(out_dir, "ad_one.png"), full_page=False)
        pg.screenshot(path=os.path.join(out_dir, "ad_list.png"), full_page=False)
    except Exception as e:
        print("ADPAGE could not finish: %s %s" % (type(e).__name__, str(e)[:200]))
    finally:
        pg.close()


def clean_url(u):
    return re.sub(r"[?#].*$", "", u or "")


SEARCH_JS = """() => {
  const out = [];
  const leaves = [...document.querySelectorAll('span,div')].filter(e => e.childElementCount === 0 && /^Library ID:\\s*\\d+/.test((e.textContent || '').trim()));
  for (const s of leaves) {
    let el = s, hops = 0;
    while (el.parentElement && el.parentElement !== document.body && !/Sponsored/.test(el.innerText || '') && hops < 14) { el = el.parentElement; hops++; }
    const lines = (el.innerText || '').split('\\n').map(x => x.trim()).filter(Boolean);
    const at = lines.findIndex(x => x === 'Sponsored');
    const name = at > 0 ? lines[at - 1] : '';
    const links = [...el.querySelectorAll('a[href]')].map(a => ({ t: (a.innerText || '').trim().slice(0, 40), h: a.href.replace(/[?&](__cft__|__tn__|ref|eav|paipv)[^&]*/g, '').slice(0, 160) }));
    out.push({ id: s.textContent.match(/\\d+/)[0], name, links: links.filter(l => l.t === name || /facebook\\.com\\/(\\d{5,}|[A-Za-z0-9.]+)\\/?$/.test(l.h)).slice(0, 4), all: links.length });
  }
  return out;
}"""


def resolve_test(handles, words):
    """Which public route gives a page's numeric id, and what an Ad Library card says about its advertiser."""
    import yaml
    from urllib.parse import quote
    from playwright.sync_api import sync_playwright
    from scraper import facebook_page_id
    with open(os.path.join(HERE, "..", "scraper", "config.yaml"), "r", encoding="utf-8") as f:
        run_cfg = (yaml.safe_load(f) or {}).get("run", {}) or {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            ctx = browser.new_context(viewport={"width": 1280, "height": 1600}, locale=run_cfg.get("locale", "en-MY"),
                                      timezone_id=run_cfg.get("timezone", "Asia/Kuala_Lumpur"), user_agent=run_cfg.get("user_agent"))
            ctx.set_default_timeout(20000)
            pg = ctx.new_page()
            for h in [x.strip() for x in handles.split(",") if x.strip()]:
                row = []
                for name, url in (("page", "https://www.facebook.com/%s" % h),
                                  ("plugin", "https://www.facebook.com/plugins/page.php?href=%s&tabs=&width=340&height=130&small_header=true" % quote("https://www.facebook.com/%s" % h, safe="")),
                                  ("page2", "https://www.facebook.com/%s" % h)):
                    try:
                        r = pg.goto(url, wait_until="domcontentloaded", timeout=45000)
                        pg.wait_for_timeout(2500)
                        html = pg.content()
                        row.append("%s: http=%s login=%s id=%s chars=%d" % (name, r.status if r else "?", "/login" in pg.url.lower(), facebook_page_id(html), len(html)))
                    except Exception as e:
                        row.append("%s: %s" % (name, type(e).__name__))
                    time.sleep(3)
                print("RESOLVE %-22s %s" % (h, " | ".join(row)))
            for w in [x.strip() for x in words.split(",") if x.strip()]:
                try:
                    pg.goto("https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=MY&q=%s&search_type=keyword_unordered&media_type=all" % quote(w),
                            wait_until="domcontentloaded", timeout=45000)
                    pg.wait_for_timeout(7000)
                    cards = pg.evaluate(SEARCH_JS)
                    names = {}
                    for c in cards:
                        names.setdefault(c["name"], []).append(c)
                    print("SEARCH %-12s cards=%d advertisers=%s" % (w, len(cards), {k: len(v) for k, v in names.items()}))
                    for k, v in list(names.items())[:5]:
                        print("SEARCH %-12s advertiser=%r links=%s all_links_in_card=%s" % (w, k, json.dumps(v[0]["links"], ensure_ascii=False), v[0]["all"]))
                except Exception as e:
                    print("SEARCH %-12s could not read: %s" % (w, type(e).__name__))
                time.sleep(4)
        finally:
            browser.close()


def sweep_test(spec, out_dir):
    """The sweep's own Facebook route (Scraper._scrape_target) on a few pages. Prints, files nothing."""
    import yaml
    from playwright.sync_api import sync_playwright
    from scraper import Scraper, canonical_url
    with open(os.path.join(HERE, "..", "scraper", "config.yaml"), "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    os.makedirs(out_dir, exist_ok=True)
    saved = os.environ.pop("PW_STORAGE_STATE_B64", None)  # this route presents no login; prove it needs none
    try:
        sc = Scraper(cfg, out_dir)
    finally:
        if saved is not None:
            os.environ["PW_STORAGE_STATE_B64"] = saved
    bad = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            for pair in [x for x in spec.split(",") if "=" in x]:
                brand, handle = [v.strip() for v in pair.split("=", 1)]
                t0 = time.time()
                res = sc._scrape_target(browser, brand, "Facebook", handle)
                new = [p for p in res.posts if not p.known]
                print("SWEEP %-16s handle=%s via_ads=%s ads_listed=%s new=%d error=%s capped=%s secs=%.0f" % (
                    brand, handle, res.via_ads, res.links_found, len(new), res.error, res.capped, time.time() - t0))
                print("SWEEP %-16s how: %s" % (brand, res.ads_how))
                if res.error:
                    bad += 1
                for p in new[:2]:
                    size = os.path.getsize(p.screenshot_path) if p.screenshot_path and os.path.exists(p.screenshot_path) else 0
                    print("SWEEP   ad url=%s started=%s text_chars=%d shot_bytes=%d current=%s errors=%s" % (p.url, p.posted_at, len(p.text or ""), size, p.always_current, p.errors))
                    print("SWEEP   canonical=%s note=%s" % (canonical_url(p.url), p.notes))
                    print("SWEEP   text: %s" % json.dumps((p.text or "")[:500], ensure_ascii=False))
                # a second visit with the first one's ads known must open none of them
                sc.known_urls = set(sc.known_urls) | {canonical_url(p.url) for p in new}
                again = sc._scrape_target(browser, brand, "Facebook", handle)
                print("SWEEP %-16s second visit: listed=%s new=%d (expect 0)" % (brand, again.links_found, len([p for p in again.posts if not p.known])))
                time.sleep(5)
        finally:
            browser.close()
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--handle", default="cetaphilmalaysia")
    ap.add_argument("--out", default=os.path.join(HERE, "fb_probe_out"))
    ap.add_argument("--public", default="cetaphilmalaysia,larocheposaymalaysia", help="pages to read with no cookies at all, comma separated")
    ap.add_argument("--scrolls", type=int, default=10)
    ap.add_argument("--adq", default="cetaphil", help="a word to look up in Meta's Ad Library (Malaysia)")
    ap.add_argument("--ads-page", default="cetaphilmalaysia", help="a Facebook page handle whose own ads are listed ('' to skip)")
    ap.add_argument("--sweep-test", default="", help="Brand=handle[,Brand=handle]: run the sweep's own Facebook code on these and print what it would hand the reviewer; nothing is filed")
    ap.add_argument("--only-sweep-test", action="store_true", help="skip the login and public-page probes")
    ap.add_argument("--resolve", default="", help="handles, comma separated: which public route gives each page's numeric id (page, page plugin)")
    ap.add_argument("--search", default="", help="words, comma separated: Ad Library keyword search, printing each card's advertiser and where its name links")
    args = ap.parse_args()
    if args.resolve or args.search:
        resolve_test(args.resolve, args.search)
    if args.sweep_test:
        code = sweep_test(args.sweep_test, args.out)
        if args.only_sweep_test:
            return code
    elif args.only_sweep_test:
        return 0
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
                # No pictures from this context at all. A signed-in feed shows the account, and so does a REJECTED
                # login: Facebook answers it with "Continue as <name>" and the profile photo (15 Nov 2026: that
                # picture was uploaded once and deleted). Pictures come from the cookie-less visit below only.
                try:
                    chooser = page.get_by_text("Use another profile").first.is_visible(timeout=800)
                except Exception:
                    chooser = False
                if chooser:
                    print("STEP %-6s Facebook remembers this browser and offers to continue as the account: the login was ended, not forgotten" % key)
            ctx.close()

            # ---- The same page with no cookies at all: what any visitor is shown.
            pub = browser.new_context(viewport={"width": 1280, "height": 1600}, locale=run_cfg.get("locale", "en-MY"),
                                      timezone_id=run_cfg.get("timezone", "Asia/Kuala_Lumpur"), user_agent=ua)
            pub.set_default_timeout(20000)
            for handle in [h.strip() for h in args.public.split(",") if h.strip()]:
                pp = pub.new_page()
                try:
                    pp.goto("https://www.facebook.com/%s" % handle, wait_until="domcontentloaded", timeout=45000)
                    pp.wait_for_timeout(4000)
                    walled = "/login" in pp.url
                    first = public_links(pp)
                    closed = 0
                    series = []
                    for i in range(args.scrolls):
                        closed += close_dialog(pp)
                        pp.mouse.wheel(0, 2400)
                        pp.wait_for_timeout(1600)
                        series.append(len(public_links(pp)))
                    closed += close_dialog(pp)
                    links = public_links(pp)
                    print("PUBLIC %-22s redirected_to_login=%s links_at_load=%d after_%d_scrolls=%d dialogs_closed=%d growth=%s" % (
                        handle, walled, len(first), args.scrolls, len(links), closed, series))
                    kinds = {}
                    for u in links:
                        k = re.search(r"/(posts|videos|reel|photos?|watch)\b|story\.php|permalink\.php", u)
                        kinds[k.group(0).strip("/") if k else "other"] = kinds.get(k.group(0).strip("/") if k else "other", 0) + 1
                    print("PUBLIC %-22s kinds=%s" % (handle, kinds))
                    pp.screenshot(path=os.path.join(args.out, "public_%s.png" % re.sub(r"[^a-z0-9]+", "_", handle.lower())), full_page=False)
                except Exception as e:
                    print("PUBLIC %-22s could not read: %s" % (handle, type(e).__name__))
                finally:
                    pp.close()
                time.sleep(6)

            # ---- Meta's Ad Library: public by design, every ad a page is running on Facebook and Instagram.
            ap_ = pub.new_page()
            try:
                ap_.goto("https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=MY&q=%s&search_type=keyword_unordered&media_type=all" % args.adq,
                         wait_until="domcontentloaded", timeout=45000)
                ap_.wait_for_timeout(7000)
                body = ""
                try:
                    body = ap_.locator("body").inner_text(timeout=4000) or ""
                except Exception:
                    pass
                ids = len(set(re.findall(r"Library ID:\s*(\d+)", body)))
                res = re.search(r"~?([\d,]+)\s+results?", body)
                print("ADLIB redirected_to_login=%s library_ids_on_page=%d results_line=%s text_chars=%d url=%s" % (
                    "/login" in ap_.url, ids, res.group(0) if res else "none", len(body), clean_url(ap_.url)))
                ap_.screenshot(path=os.path.join(args.out, "adlib.png"), full_page=False)
            except Exception as e:
                print("ADLIB could not read: %s" % type(e).__name__)

            # ---- One brand page's own ads: resolve the page id from the public page, list its ads, open one.
            if args.ads_page:
                ads_probe(pub, args.ads_page, args.out)
            pub.close()
        finally:
            browser.close()
    final = verdicts[0] if verdicts else "unknown"
    print("RESULT %s" % final)
    return 0


if __name__ == "__main__":
    sys.exit(main())
