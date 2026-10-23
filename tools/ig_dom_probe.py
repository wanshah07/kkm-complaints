"""
Where is the Instagram POST CARD in the page? Read-only: loads post URLs with the sweep's own recorded session and prints
the DOM facts that decide which element to crop. Writes nothing to the sheet. 23 Oct 2026 (Wan: "for screenshot can make it
on the post image ... not the page image"): 76 of 77 Instagram shots in run #265 were `main[role='main']`, the whole column
with the 'More posts from ...' grid under the card, because none of the `article` selectors matches this layout any more.

For each URL (fresh context each) it prints: the visible counts of the selectors the scraper tries, and the ANCESTOR
CHAIN of the post's own anchor and of its <time> element, innermost first, each with tag, role, size and whether its text
already contains 'More posts from' (the level just BELOW the first one that does is the card). Annotated shots go to probe_out/.

Usage: python tools/ig_dom_probe.py URL [URL ...]      Needs PW_STORAGE_STATE_B64 (never printed).
"""
import base64, gzip, json, os, re, sys, tempfile
from playwright.sync_api import sync_playwright

OUT = "probe_out"
SELS = ["article", "main article", "[role='dialog'] article", "main[role='main']", "main", "article[role='presentation']",
        "div[role='presentation']", "section main > div", "main > div > div"]

JS_CHAIN = """(el) => { const out=[]; let n=el; for (let i=0;i<16 && n && n.tagName;i++, n=n.parentElement) {
  const r=n.getBoundingClientRect(); const t=(n.innerText||'');
  out.push({tag:n.tagName.toLowerCase(), role:n.getAttribute('role')||'', cls:(n.className&&n.className.toString().slice(0,28))||'',
            w:Math.round(r.width), h:Math.round(r.height), x:Math.round(r.x), y:Math.round(r.y),
            more:/more posts from|lagi siaran daripada/i.test(t), chars:t.length}); }
  return out; }"""


def state_path():
    raw = (os.environ.get("PW_STORAGE_STATE_B64") or "").strip()
    if not raw:
        return None
    b = base64.b64decode(raw)
    if b[:2] == b"\x1f\x8b":
        b = gzip.decompress(b)
    json.loads(b)
    fd, p = tempfile.mkstemp(suffix=".json"); os.write(fd, b); os.close(fd)
    return p


def post_id(url):
    m = re.search(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]{5,})", url)
    return m.group(1) if m else ""


def main(urls):
    os.makedirs(OUT, exist_ok=True)
    st = state_path()
    print("session:", "loaded" if st else "NONE (logged-out)")
    with sync_playwright() as pw:
        for i, url in enumerate(urls):
            print("\n" + "=" * 100 + "\n" + url)
            b = pw.chromium.launch(headless=True)
            try:
                ctx = b.new_context(storage_state=st, locale="en-US", timezone_id="Asia/Kuala_Lumpur",
                                    viewport={"width": 1280, "height": 1600},
                                    user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"))
                page = ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(6000)
                print("final url:", page.url, "| title:", (page.title() or "")[:60])
                for s in SELS:
                    try:
                        loc = page.locator(s)
                        vis = [k for k in range(min(loc.count(), 6)) if loc.nth(k).is_visible()]
                        bx = [loc.nth(k).bounding_box() for k in vis[:2]]
                        print("  %-30s count=%d visible=%d first_boxes=%s" % (
                            s, loc.count(), len(vis), [(round(x["width"]), round(x["height"])) for x in bx if x]))
                    except Exception as e:
                        print("  %-30s ERR %s" % (s, str(e)[:60]))
                pid = post_id(url)
                for label, sel in (("anchor", 'a[href*="%s"]' % pid if pid else None), ("time", "time")):
                    if not sel:
                        continue
                    try:
                        el = page.locator(sel).first
                        if el.count() == 0:
                            print("  chain from %s: none" % label); continue
                        chain = el.evaluate(JS_CHAIN)
                        print("  chain from %s (innermost first): tag role cls w x h @x,y more? chars" % label)
                        for c in chain:
                            print("    %-8s %-14s %-28s %4dx%-5d @%d,%d  more=%s chars=%d" % (
                                c["tag"], c["role"], c["cls"], c["w"], c["h"], c["x"], c["y"], c["more"], c["chars"]))
                    except Exception as e:
                        print("  chain from %s ERR %s" % (label, str(e)[:80]))
                page.screenshot(path=os.path.join(OUT, "ig_%d.png" % i), full_page=False)
            except Exception as e:
                print("NOT A MEASUREMENT:", repr(e)[:200])
            finally:
                b.close()


if __name__ == "__main__":
    main(sys.argv[1:])
