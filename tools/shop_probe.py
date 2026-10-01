"""
Can a GitHub runner SEE Shopee and TikTok? Read-only; writes nothing to the sheet.

Wan, 1 Oct 2026: add Shopee (product listings AND the shop feed) first, TikTok second. Before any
scraper is written the question is the one the Mireld probe asked of a model gateway: not "is the
site up" but "does it answer THIS MACHINE, and with what". This environment cannot even reach
either host (the egress proxy refuses the CONNECT), so only a runner can say.

Three outcomes are kept apart on purpose, because the 19 Sep Playwright probe once reported one
broken source as five zeros through a page it shared between subjects:
  measured   the page loaded and we read what it showed (content, a login wall, a captcha)
  blocked    the page loaded but is a wall: login redirect, captcha, access denied, empty shell
  NOT A MEASUREMENT   navigation itself failed (DNS, reset, timeout) - says nothing about the site
Every URL gets a FRESH browser context.

Usage: python tools/shop_probe.py URL [URL ...]      (no URLs = a default set)
"""
import base64, gzip, json, os, re, sys, time, tempfile
from playwright.sync_api import sync_playwright

DEFAULTS = [
    "https://shopee.com.my/",
    "https://shopee.com.my/search?keyword=whitening%20cream",
    "https://www.tiktok.com/@tiktok",
    "https://www.tiktok.com/search?q=whitening%20cream",
]
WALL = re.compile(r"captcha|verify you are human|are you a robot|security check|access denied|"
                  r"unusual traffic|slide to verify|log ?in to continue|sign ?in to continue|"
                  r"please log ?in|pengesahan|sahkan anda|anti.?bot", re.I)
OUT = "probe_out"


def session_path():
    """A recorded browser session, ONLY when asked for (PROBE_WITH_SESSION=1) and the secret is set.
    A SEPARATE secret from the scraper's (PW_STORAGE_STATE_SHOP_B64): a throwaway Shopee / TikTok
    login in a Playwright storage_state, base64, optionally gzipped. Kept apart so recording it
    cannot overwrite the Instagram / Facebook / Threads session the sweep runs on. Never printed."""
    if os.environ.get("PROBE_WITH_SESSION") != "1":
        return None
    raw = (os.environ.get("PW_STORAGE_STATE_SHOP_B64") or "").strip()
    if not raw:
        return None
    b = base64.b64decode(raw)
    if b[:2] == b"\x1f\x8b":
        b = gzip.decompress(b)
    f = tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False)
    f.write(b); f.close()
    return f.name


def probe(pw, url, i, state=None):
    r = {"url": url, "outcome": "NOT A MEASUREMENT", "http": None, "final_url": None, "title": "",
         "text_chars": 0, "login_redirect": False, "wall_words": [], "rm_prices": 0,
         "item_links": 0, "video_links": 0, "sample_links": [], "with_session": bool(state), "snippet": "", "error": ""}
    browser = pw.chromium.launch(headless=True, executable_path=os.environ.get("PW_CHROMIUM") or None)
    try:
        ctx = browser.new_context(
            storage_state=state,
            locale="ms-MY", timezone_id="Asia/Kuala_Lumpur", viewport={"width": 1280, "height": 1600},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"))
        page = ctx.new_page()
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        r["http"] = resp.status if resp else None
        page.wait_for_timeout(7000)
        for _ in range(4):                       # lazy grids (TikTok videos, Shopee products) appear on scroll
            page.mouse.wheel(0, 1400)
            page.wait_for_timeout(2500)
        r["final_url"] = page.url
        r["title"] = (page.title() or "")[:120]
        body = page.inner_text("body") if page.query_selector("body") else ""
        r["text_chars"] = len(body)
        r["snippet"] = re.sub(r"\s+", " ", body)[:300]
        r["login_redirect"] = bool(re.search(r"login|signin|buyer/login|passport", page.url, re.I))
        r["wall_words"] = sorted({m.group(0).lower() for m in WALL.finditer(body + " " + r["title"])})[:6]
        r["rm_prices"] = len(re.findall(r"RM\s?\d", body))
        r["item_links"] = len(page.query_selector_all("a[href*='-i.']"))
        r["sample_links"] = [a.get_attribute("href") for a in (page.query_selector_all("a[href*='-i.'], a[href*='/video/']")[:3])]
        r["video_links"] = len(page.query_selector_all("a[href*='/video/']"))
        os.makedirs(OUT, exist_ok=True)
        page.screenshot(path=f"{OUT}/probe_{i}.png", full_page=False)
        wall = r["login_redirect"] or r["wall_words"] or r["http"] in (401, 403, 429) or r["text_chars"] < 200
        useful = r["rm_prices"] >= 3 or r["item_links"] >= 3 or r["video_links"] >= 3
        r["outcome"] = "blocked" if (wall and not useful) else "measured"
    except Exception as e:                                   # navigation failed: no verdict on the site
        r["error"] = str(e)[:200]
    finally:
        browser.close()
    return r


def main(argv):
    urls = argv or DEFAULTS
    rows = []
    state = session_path()
    print("session:", "recorded session loaded" if state else "anonymous")
    with sync_playwright() as pw:
        for i, u in enumerate(urls):
            rows.append(probe(pw, u, i, state))
            time.sleep(3)
    if state:
        os.unlink(state)
    os.makedirs(OUT, exist_ok=True)
    json.dump(rows, open(f"{OUT}/shop_probe.json", "w"), indent=1)
    print("\n=== shop probe ===")
    for r in rows:
        print(f"\n[{r['outcome']}] {r['url']}")
        if r["error"]:
            print(f"   navigation failed: {r['error']}")
            continue
        print(f"   http={r['http']} final={r['final_url']}")
        print(f"   title={r['title']!r} text={r['text_chars']} chars")
        print(f"   login_redirect={r['login_redirect']} wall_words={r['wall_words']}")
        print(f"   sample links: {r['sample_links']}")
        print(f"   RM prices={r['rm_prices']} shopee item links={r['item_links']} tiktok video links={r['video_links']}")
        print(f"   sees: {r['snippet']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
