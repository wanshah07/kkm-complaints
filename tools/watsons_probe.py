"""
Can a REAL browser on a GitHub runner read watsons.com.my, where plain HTTP gets an Akamai 403?
Read-only, a handful of page loads, writes nothing to the sheet. No stealth plugins, no header or
fingerprint spoofing: stock Playwright Chromium, headless and then headed under Xvfb, one fresh
context per URL. For each load it reports one of three outcomes (never conflated):
  measured            the page rendered and carries product content (names / RM prices)
  blocked             an answer arrived but it is a wall (Access Denied / captcha / challenge / empty shell)
  NOT A MEASUREMENT   the load itself failed (DNS, timeout, crash) - says nothing about the site
It also lists the JSON responses the page fetched for itself, because a product API the page calls is
a cheaper route than parsing the rendered HTML.
Usage: python tools/watsons_probe.py [--headed] [URL ...]
"""
import json, re, sys, time
from playwright.sync_api import sync_playwright

SAMPLE = [
    "https://www.watsons.com.my/",
    "https://www.watsons.com.my/all-brands/b/154349/cerave",
    "https://www.watsons.com.my/ceradan-skin-barrier-repair-cream-80g/p/BP_65213",
]
WALL = re.compile(r"access denied|captcha|verify you are human|unusual traffic|are you a robot|"
                  r"pardon our interruption|request unsuccessful|reference #", re.I)
RM = re.compile(r"RM\s?\d")


def probe(browser, url):
    out = {"url": url, "status": None, "title": "", "outcome": "", "detail": "", "rm_prices": 0,
           "json_calls": []}
    ctx = browser.new_context(locale="en-MY", timezone_id="Asia/Kuala_Lumpur",
                              viewport={"width": 1366, "height": 900})
    page = ctx.new_page()

    def on_resp(r):
        try:
            ct = r.headers.get("content-type", "")
            if "json" in ct and "watsons" in r.url:
                out["json_calls"].append(f"{r.status} {r.url[:150]}")
        except Exception:
            pass
    page.on("response", on_resp)
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        out["status"] = resp.status if resp else None
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        time.sleep(2)
        out["title"] = (page.title() or "")[:100]
        text = page.inner_text("body")[:200000] if page.query_selector("body") else ""
        out["rm_prices"] = len(RM.findall(text))
        if (out["status"] and out["status"] >= 400) or WALL.search(out["title"] + " " + text[:600]):
            out["outcome"], out["detail"] = "blocked", f"HTTP {out['status']} {text[:140]!r}"
        elif out["rm_prices"] >= 3:
            out["outcome"] = "measured"
        else:
            out["outcome"], out["detail"] = "blocked", f"rendered but no product content ({len(text)} chars): {text[:140]!r}"
    except Exception as e:
        out["outcome"], out["detail"] = "NOT A MEASUREMENT", f"{type(e).__name__}: {str(e)[:160]}"
    finally:
        ctx.close()
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    headed = "--headed" in sys.argv
    urls = args or SAMPLE
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        print(f"browser {browser.version}  mode={'headed' if headed else 'headless'}")
        results = [probe(browser, u) for u in urls]
        browser.close()
    for r in results:
        print(f"\n[{r['outcome']}] {r['url']}\n  status={r['status']} title={r['title']!r} rm_prices={r['rm_prices']}")
        if r["detail"]:
            print("  " + r["detail"])
        for j in r["json_calls"][:12]:
            print("  json:", j)
    print("\nSUMMARY", json.dumps({r["url"]: r["outcome"] for r in results}))


if __name__ == "__main__":
    main()
