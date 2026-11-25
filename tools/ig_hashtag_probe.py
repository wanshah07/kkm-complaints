"""
Can the sweep's recorded Instagram session READ a hashtag page, and can the scraper's own post reader judge what it finds?
Read-only: writes nothing to the sheet, the ledger or the repository. Wan, 24 Nov 2026: Skintific posts from doctors and
KOLs, found by hashtag / search instead of walking every doctor's grid (a person sweep reached 4 of 209 accounts in 110
minutes, because every post has to be opened to read its caption).

For each tag it prints: where the page landed, whether a login wall or an empty grid came back, how many post links the
page offered after scrolling, and, for the first few of them, what the scraper's own reader gets from the post page: the
owner handle, the date, whether the caption mentions the tag word, and the caption's first 120 characters. That is exactly
what a hashtag mode would need to decide, so the answer here is the answer for building it.

Usage: python tools/ig_hashtag_probe.py TAG [TAG ...]   (no #)    Needs PW_STORAGE_STATE_B64 (never printed).
Env: PROBE_POSTS (posts to open per tag, default 4), PROBE_SCROLLS (default 6).
"""
import base64, gzip, json, os, re, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scraper"))
from playwright.sync_api import sync_playwright  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0.0.0 Safari/537.36")
POST_RE = re.compile(r"/(?:[A-Za-z0-9_.]+/)?(?:p|reel|reels)/([A-Za-z0-9_-]{5,})")


def state_path():
    raw = (os.environ.get("PW_STORAGE_STATE_B64") or "").strip()
    if (os.environ.get("PW_STORAGE_STATE_IG_B64") or "").strip():
        from session_state import combined_b64
        raw = combined_b64(raw, os.environ["PW_STORAGE_STATE_IG_B64"])
    if not raw:
        return None
    b = base64.b64decode(raw)
    if b[:2] == b"\x1f\x8b":
        b = gzip.decompress(b)
    json.loads(b)
    fd, p = tempfile.mkstemp(suffix=".json")
    os.write(fd, b)
    os.close(fd)
    return p


def links_on(page):
    try:
        hrefs = page.locator("a[href]").evaluate_all("els => els.map(e => e.getAttribute('href'))")
    except Exception:
        return []
    out, seen = [], set()
    for h in hrefs:
        m = POST_RE.search(h or "")
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            out.append(h)
    return out


def main(tags):
    posts_n = int(os.environ.get("PROBE_POSTS", "4"))
    scrolls = int(os.environ.get("PROBE_SCROLLS", "6"))
    st = state_path()
    print("session:", "loaded" if st else "NONE (logged-out)")
    try:
        from scraper import (_dismiss_dialogs, _extract_date, _extract_owner, _extract_text, _is_login_wall,
                             _owner_from_url)
    except Exception as e:  # the probe still reports the grid if the scraper's helpers cannot be imported
        print("could not import the scraper's readers:", type(e).__name__, e)
        _dismiss_dialogs = _extract_date = _extract_owner = _extract_text = _is_login_wall = _owner_from_url = None
    with sync_playwright() as pw:
        for tag in tags:
            tag = tag.strip().lstrip("#").lower()
            if not tag:
                continue
            url = "https://www.instagram.com/explore/tags/%s/" % tag
            print("\n" + "=" * 90 + "\n#" + tag + "  " + url)
            b = pw.chromium.launch(headless=True)
            try:
                ctx = b.new_context(storage_state=st, locale="en-US", timezone_id="Asia/Kuala_Lumpur",
                                    viewport={"width": 1280, "height": 1600}, user_agent=UA)
                page = ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(5000)
                if _dismiss_dialogs:
                    _dismiss_dialogs(page)
                print("landed:", page.url, "| title:", (page.title() or "")[:70])
                if _is_login_wall:
                    print("login wall:", _is_login_wall(page))
                body = (page.inner_text("body") or "")[:600].replace("\n", " | ")
                print("body start:", body[:240])
                for tab in ("Recent", "Top", "Terbaru", "Teratas"):
                    try:
                        n = page.get_by_role("tab", name=tab).count() + page.get_by_text(tab, exact=True).count()
                        if n:
                            print("  tab/label present:", tab, n)
                    except Exception:
                        pass
                links = links_on(page)
                print("links before scroll:", len(links))
                for i in range(scrolls):
                    page.mouse.wheel(0, 1800)
                    page.wait_for_timeout(1800)
                    links = links_on(page)
                print("links after %d scrolls: %d" % (scrolls, len(links)))
                for h in links[:6]:
                    print("   ", h)
                for h in links[:posts_n]:
                    full = "https://www.instagram.com" + h if h.startswith("/") else h
                    print("\n  -- post", full)
                    p2 = ctx.new_page()
                    try:
                        p2.goto(full, wait_until="domcontentloaded", timeout=45000)
                        p2.wait_for_timeout(3000)
                        if _dismiss_dialogs:
                            _dismiss_dialogs(p2)
                        owner, is_handle = (None, False)
                        if _owner_from_url:
                            owner = _owner_from_url(full, "Instagram")
                            is_handle = owner is not None
                            if owner is None:
                                owner, is_handle = _extract_owner(p2, "Instagram")
                        text = _extract_text(p2, "Instagram") if _extract_text else ""
                        date = _extract_date(p2) if _extract_date else None
                        print("     owner=%r handle=%s date=%s mentions_tag=%s" % (owner, is_handle, date, tag in (text or "").lower()))
                        print("     text:", (text or "")[:120].replace("\n", " "))
                    except Exception as e:
                        print("     ERR", type(e).__name__, str(e)[:100])
                    finally:
                        p2.close()
            except Exception as e:
                print("ERR", type(e).__name__, str(e)[:160])
            finally:
                b.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: ig_hashtag_probe.py TAG [TAG ...]")
    main(sys.argv[1:])
