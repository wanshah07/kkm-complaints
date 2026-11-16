#!/usr/bin/env python3
"""
What can Instagram and Threads be read as, with no login at all, from a runner?

Measures only; files nothing and presents no cookies. For each Instagram handle: the profile page, the profile's
public embed (instagram.com/<handle>/embed/, the widget any website may show) and one post's captioned embed
(instagram.com/p/<code>/embed/captioned/). For each Threads handle: the public profile and one post's page.

  python tools/nologin_probe.py --instagram cetaphilmy,nutox_my --threads nutox_my,hygr.my --out nologin_out
"""
import argparse, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scraper"))

IG_LINKS = r"""() => { const s = []; for (const a of document.querySelectorAll('a[href]')) { const m = (a.href || '').match(/instagram\.com\/(?:[^\/]+\/)?(p|reel|tv)\/([A-Za-z0-9_-]{5,})/); if (m && !s.includes(m[1] + '/' + m[2])) s.push(m[1] + '/' + m[2]); } return s; }"""
TH_LINKS = r"""() => { const s = []; for (const a of document.querySelectorAll('a[href]')) { const m = (a.href || '').match(/threads\.(?:net|com)\/@([^\/]+)\/post\/([A-Za-z0-9_-]{5,})/); if (m && !s.includes(m[1] + '/post/' + m[2])) s.push(m[1] + '/post/' + m[2]); } return s; }"""


def body(page, n=400):
    try:
        return (page.locator("body").inner_text(timeout=4000) or "")
    except Exception:
        return ""


def scroll_series(page, js, rounds=6):
    out = []
    for _ in range(rounds):
        out.append(len(page.evaluate(js)))
        page.mouse.wheel(0, 2600)
        page.wait_for_timeout(1500)
    return out, page.evaluate(js)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instagram", default="")
    ap.add_argument("--threads", default="")
    ap.add_argument("--out", default=os.path.join(HERE, "nologin_out"))
    args = ap.parse_args()
    import yaml
    from playwright.sync_api import sync_playwright
    with open(os.path.join(HERE, "..", "scraper", "config.yaml"), "r", encoding="utf-8") as f:
        run_cfg = (yaml.safe_load(f) or {}).get("run", {}) or {}
    os.makedirs(args.out, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 1600}, locale=run_cfg.get("locale", "en-MY"),
                                  timezone_id=run_cfg.get("timezone", "Asia/Kuala_Lumpur"), user_agent=run_cfg.get("user_agent"))
        ctx.set_default_timeout(20000)
        pg = ctx.new_page()
        for h in [x.strip() for x in args.instagram.split(",") if x.strip()]:
            safe = re.sub(r"[^a-z0-9]+", "_", h.lower())
            try:
                pg.goto("https://www.instagram.com/%s/" % h, wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(3500)
                series, links = scroll_series(pg, IG_LINKS)
                print("IG %-20s profile: login_url=%s links_by_scroll=%s final=%d text_chars=%d" % (h, "/accounts/login" in pg.url, series, len(links), len(body(pg))))
                pg.screenshot(path=os.path.join(args.out, "ig_profile_%s.png" % safe))
            except Exception as e:
                print("IG %-20s profile: %s" % (h, type(e).__name__))
            time.sleep(4)
            codes = []
            try:
                r = pg.goto("https://www.instagram.com/%s/embed/" % h, wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(3500)
                codes = pg.evaluate(IG_LINKS)
                t = body(pg)
                html = pg.content()
                hrefs = pg.evaluate("() => [...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href')).slice(0, 12)")
                short = []
                for m in re.findall(r'"shortcode"\s*:\s*"([A-Za-z0-9_-]{5,})"', html) + re.findall(r'\\?/(?:p|reel)\\?/([A-Za-z0-9_-]{8,})', html):
                    if m not in short:
                        short.append(m)
                tiles = pg.evaluate("() => { const im = [...document.querySelectorAll('img')].filter(i => i.naturalWidth >= 200 || i.width >= 200); return im.slice(0, 8).map(i => { let e = i, hops = 0; while (e && e.tagName !== 'A' && hops < 8) { e = e.parentElement; hops++; } return { a: e && e.tagName === 'A' ? e.getAttribute('href') : null, alt: (i.alt || '').slice(0, 90), role: i.closest('[role]') ? i.closest('[role]').getAttribute('role') : null }; }); }")
                print("IG %-20s embed dom: anchors=%s" % (h, json.dumps(hrefs)[:500]))
                print("IG %-20s embed dom: shortcodes_in_html=%s" % (h, short[:10]))
                print("IG %-20s embed dom: tiles=%s" % (h, json.dumps(tiles, ensure_ascii=False)[:700]))
                if not codes and short:
                    codes = ["p/" + c for c in short]
                print("IG %-20s embed:   http=%s login_url=%s posts=%d first=%s text_chars=%d head=%s" % (
                    h, r.status if r else "?", "/accounts/login" in pg.url, len(codes), codes[:3], len(t), json.dumps(t[:160], ensure_ascii=False)))
                pg.screenshot(path=os.path.join(args.out, "ig_embed_%s.png" % safe))
            except Exception as e:
                print("IG %-20s embed:   %s" % (h, type(e).__name__))
            time.sleep(4)
            for code in codes[:2]:
                try:
                    kind, cid = code.split("/")
                    r = pg.goto("https://www.instagram.com/%s/%s/embed/captioned/" % (kind, cid), wait_until="domcontentloaded", timeout=45000)
                    pg.wait_for_timeout(3000)
                    t = body(pg)
                    cap = ""
                    try:
                        cap = pg.locator(".Caption").first.inner_text(timeout=2500)
                    except Exception:
                        pass
                    imgs = pg.locator("img").count()
                    when = re.search(r'datetime="([^"]+)"', pg.content())
                    print("IG %-20s post %s: http=%s login_url=%s caption_chars=%d imgs=%d datetime=%s text_chars=%d" % (
                        h, code, r.status if r else "?", "/accounts/login" in pg.url, len(cap), imgs, when.group(1) if when else None, len(t)))
                    print("IG %-20s post %s caption: %s" % (h, code, json.dumps((cap or t)[:300], ensure_ascii=False)))
                    pg.screenshot(path=os.path.join(args.out, "ig_post_%s_%s.png" % (safe, cid)))
                except Exception as e:
                    print("IG %-20s post %s: %s" % (h, code, type(e).__name__))
                time.sleep(4)
        for h in [x.strip() for x in args.threads.split(",") if x.strip()]:
            safe = re.sub(r"[^a-z0-9]+", "_", h.lower())
            links = []
            try:
                pg.goto("https://www.threads.com/@%s" % h, wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(4000)
                series, links = scroll_series(pg, TH_LINKS, rounds=8)
                own = [l for l in links if l.split("/")[0].lower() == h.lower()]
                t = body(pg)
                print("TH %-20s profile: url=%s links_by_scroll=%s final=%d own=%d login_words=%s text_chars=%d" % (
                    h, re.sub(r"[?#].*$", "", pg.url), series, len(links), len(own), bool(re.search(r"log in|sign up", t, re.I)), len(t)))
                pg.screenshot(path=os.path.join(args.out, "th_profile_%s.png" % safe))
            except Exception as e:
                print("TH %-20s profile: %s" % (h, type(e).__name__))
            time.sleep(4)
            for l in [x for x in links if x.split("/")[0].lower() == h.lower()][:1]:
                try:
                    pg.goto("https://www.threads.com/@%s" % l, wait_until="domcontentloaded", timeout=45000)
                    pg.wait_for_timeout(3500)
                    t = body(pg)
                    when = re.search(r'datetime="([^"]+)"', pg.content())
                    print("TH %-20s post %s: login_url=%s text_chars=%d datetime=%s head=%s" % (h, l, "/login" in pg.url, len(t), when.group(1) if when else None, json.dumps(t[:260], ensure_ascii=False)))
                    pg.screenshot(path=os.path.join(args.out, "th_post_%s.png" % safe))
                except Exception as e:
                    print("TH %-20s post: %s" % (h, type(e).__name__))
                time.sleep(4)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
