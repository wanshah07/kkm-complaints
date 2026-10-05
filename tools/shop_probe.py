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


TT_CANDIDATES = [
    "[data-e2e='browse-video']", "[data-e2e='browse-video-container']", "[data-e2e='video-detail']",
    "#main-content-video_detail", "[class*='DivVideoDetailContainer']", "[class*='DivBrowserModeContainer']",
    "[data-e2e='browse-video-desc']", "[data-e2e='video-desc']", "h1[data-e2e='browse-video-desc']",
    "[data-e2e='browse-username']", "[data-e2e='browser-nickname']", "[data-e2e='browse-like-count']",
    "[data-e2e='user-post-item']", "[data-e2e='user-post-item-list']", "main", "time[datetime]",
]


def tiktok_structure(pw, spec, state):
    """spec = '@handle'. Prints STRUCTURE only (counts, selectors, the public caption of public posts),
    never the page body: a logged-in TikTok page carries the account's own notifications."""
    handle = spec.lstrip("@")
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(storage_state=state, locale="en-MY", timezone_id="Asia/Kuala_Lumpur",
                              viewport={"width": 1280, "height": 1600},
                              user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                          "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"))
    page = ctx.new_page()
    out = {"handle": handle}
    api = []

    def on_resp(r):
        if re.search(r"/api/(post/item_list|user/detail|challenge/item_list)", r.url):
            try:
                api.append((r.url.split("?")[0], r.status, r.text()[:400000]))
            except Exception as e:
                api.append((r.url.split("?")[0], r.status, "ERR " + str(e)[:80]))
    page.on("response", on_resp)
    page.goto(f"https://www.tiktok.com/@{handle}", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(9000)
    for _ in range(3):
        page.mouse.wheel(0, 1500)
        page.wait_for_timeout(3000)
    body = page.inner_text("body") if page.query_selector("body") else ""
    out["body_chars"] = len(body)
    out["anchors"] = page.locator("a[href]").count()
    out["flags"] = {k: (k in body.lower()) for k in ("something went wrong", "verify", "log in", "no content", "this account is private", "couldn't find this account", "try again")}
    out["has_rehydration_json"] = page.locator("script#__UNIVERSAL_DATA_FOR_REHYDRATION__").count()
    out["api_calls"] = []
    for u, st, txt in api:
        rec = {"url": u, "status": st, "len": len(txt)}
        try:
            d = json.loads(txt)
            items = d.get("itemList") or []
            rec["items"] = len(items)
            rec["hasMore"] = d.get("hasMore")
            rec["sample"] = [{"id": i.get("id"), "createTime": i.get("createTime"), "author": (i.get("author") or {}).get("uniqueId") if isinstance(i.get("author"), dict) else i.get("author"), "desc": (i.get("desc") or "")[:80]} for i in items[:3]]
            rec["keys"] = list(d)[:10]
        except Exception:
            rec["body_start"] = txt[:80]
        out["api_calls"].append(rec)
    out["profile_final_url"] = page.url
    out["profile_title"] = page.title()[:80]
    hrefs = page.locator("a[href]").evaluate_all("els => els.map(e => e.getAttribute('href'))")
    vids = [h for h in dict.fromkeys(hrefs) if h and re.search(r"/(video|photo)/\d+", h)]
    out["video_links"] = len(vids)
    out["sample"] = vids[:4]
    out["own_links"] = len([h for h in vids if h.lower().startswith(f"https://www.tiktok.com/@{handle.lower()}/") or h.lower().startswith(f"/@{handle.lower()}/")])
    out["profile_selectors"] = {c: page.locator(c).count() for c in TT_CANDIDATES if page.locator(c).count()}
    out["captcha_words"] = sorted({m.group(0).lower() for m in WALL.finditer(page.inner_text("body")[:3000])})
    print("TIKTOK PROFILE", json.dumps(out, indent=1))
    for v in vids[:2]:
        url = v if v.startswith("http") else "https://www.tiktok.com" + v
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)
        vid = re.search(r"/(?:video|photo)/(\d+)", url).group(1)
        import datetime
        rec = {"url": url, "final": page.url, "id_date": datetime.datetime.utcfromtimestamp(int(vid) >> 32).date().isoformat()}
        for prop in ("og:description", "og:title", "description", "twitter:title"):
            for sel in (f"meta[property='{prop}']", f"meta[name='{prop}']"):
                try:
                    v2 = page.locator(sel).first.get_attribute("content", timeout=500)
                except Exception:
                    v2 = None
                if v2:
                    rec["meta:" + prop] = v2[:300]
                    break
        rec["selectors"] = {c: page.locator(c).count() for c in TT_CANDIDATES if page.locator(c).count()}
        for c in ("[data-e2e='browse-video-desc']", "[data-e2e='video-desc']", "h1[data-e2e='browse-video-desc']", "[data-e2e='browse-username']", "[data-e2e='browser-nickname']"):
            try:
                if page.locator(c).count():
                    rec["text:" + c] = page.locator(c).first.inner_text(timeout=800)[:300]
            except Exception:
                pass
        for c in ("[data-e2e='browse-video']", "[data-e2e='browse-video-container']", "[class*='DivVideoDetailContainer']", "main"):
            try:
                if page.locator(c).count():
                    bb = page.locator(c).first.bounding_box()
                    if bb:
                        rec["box:" + c] = {k: int(bb[k]) for k in ("x", "y", "width", "height")}
            except Exception:
                pass
        rec["time_datetime"] = page.locator("time[datetime]").first.get_attribute("datetime") if page.locator("time[datetime]").count() else None
        print("TIKTOK VIDEO", json.dumps(rec, indent=1, ensure_ascii=False))
        time.sleep(4)
    browser.close()


def tiktok_search_structure(pw, query, owner, state, open_n=8):
    """'tiktok-search:<query>|<owner handle>': how many of the first search hits belong to <owner>.
    Prints handles, id-dates and counts only; no page body."""
    import datetime
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(storage_state=state, locale="en-MY", timezone_id="Asia/Kuala_Lumpur",
                              viewport={"width": 1280, "height": 1600},
                              user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                          "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"))
    page = ctx.new_page()
    from urllib.parse import quote
    for tab in ("video", "user"):
        page.goto(f"https://www.tiktok.com/search/{tab}?q={quote(query)}", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(8000)
        for _ in range(2):
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(2500)
        hrefs = page.locator("a[href]").evaluate_all("els => els.map(e => e.getAttribute('href'))")
        vids = [h for h in dict.fromkeys(hrefs) if h and re.search(r"/video/\d+", h)]
        users = sorted({m.group(1) for h in hrefs if h for m in [re.match(r"^/@([A-Za-z0-9_.]+)$", h)] if m})
        print(f"TIKTOK SEARCH tab={tab} q={query!r}: video_links={len(vids)} user_links={len(users)} sample_users={users[:8]}")
        if tab == "video":
            hits = []
            for v in vids[:open_n]:
                url = "https://www.tiktok.com" + v if v.startswith("/") else v
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(3500)
                    d = (page.locator("meta[name='description']").first.get_attribute("content", timeout=800) or "")
                    m = re.search(r"\(@([A-Za-z0-9_.]+)\)", d)
                    vid = re.search(r"/video/(\d+)", url).group(1)
                    hits.append((m.group(1) if m else None, datetime.datetime.fromtimestamp(int(vid) >> 32, datetime.timezone.utc).date().isoformat()))
                except Exception as e:
                    hits.append(("ERR " + type(e).__name__, ""))
                time.sleep(2)
            print("   first hits (handle, date):", hits)
            print("   own:", sum(1 for h, _ in hits if h and h.lower() == owner.lower()), "of", len(hits))
    browser.close()


def main(argv):
    ts = [a for a in argv if a.startswith("tiktok-search:")]
    if ts:
        state = session_path()
        print("session:", "recorded session loaded" if state else "anonymous")
        with sync_playwright() as pw:
            for a in ts:
                q, _, owner = a[len("tiktok-search:"):].partition("|")
                tiktok_search_structure(pw, q, owner, state)
        if state:
            os.unlink(state)
        return 0
    tt = [a for a in argv if a.startswith("tiktok:")]
    if tt:
        state = session_path()
        print("session:", "recorded session loaded" if state else "anonymous")
        with sync_playwright() as pw:
            for a in tt:
                tiktok_structure(pw, a[len("tiktok:"):], state)
        if state:
            os.unlink(state)
        return 0
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
