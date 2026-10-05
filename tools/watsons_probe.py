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
           "json_calls": [], "api": [], "links": [], "excerpt": ""}
    ctx = browser.new_context(locale="en-MY", timezone_id="Asia/Kuala_Lumpur",
                              viewport={"width": 1366, "height": 900})
    page = ctx.new_page()

    def on_resp(r):
        try:
            ct = r.headers.get("content-type", "")
            if "json" in ct and "watsons" in r.url:
                out["json_calls"].append(f"{r.status} {r.url[:150]}")
                if "/products/search" in r.url or re.search(r"/products/\d+\?", r.url):
                    out["api"].append((r.url, r.text()[:900000]))
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
        out["excerpt"] = re.sub(r"\s+", " ", text)[:700]
        if "/p/BP_" in url:
            i = text.find("HOME /")
            body = text[i:] if i >= 0 else text
            print("  PRODUCT TEXT total", len(text), "from HOME:", len(body))
            lines = [l.strip() for l in body.split("\n") if l.strip()]
            print("  PRODUCT LINES", len(lines))
            for n, l in enumerate(lines):
                if len(l) > 70:
                    print(f"   LONG {n:3d} {l[:400]}")
            code = re.search(r"/p/BP_(\d+)", url).group(1)

            def longstrings(o, acc, path=""):
                if isinstance(o, str):
                    if len(o) > 120:
                        acc.append((path, o))
                elif isinstance(o, dict):
                    for k, v in o.items():
                        longstrings(v, acc, path + "/" + k)
                elif isinstance(o, list):
                    for i, v in enumerate(o):
                        longstrings(v, acc, f"{path}[{i}]")
            for label, api in (("PRODUCT FULL", f"https://api.watsons.com.my/api/v2/wtcmy/products/{code}?fields=FULL&lang=en&curr=MYR"),
                               ("CMS PRODUCTPAGE", f"https://api.watsons.com.my/api/v2/wtcmy/users/anonymous/cms/pages?pageType=ProductPage&code=BP_{code}&lang=en&curr=MYR")):
                try:
                    txt = page.evaluate("async (u) => { const r = await fetch(u, {credentials: 'include'}); return r.status + '|' + await r.text(); }", api)
                    st, body = txt.split("|", 1)
                    print(f"  {label} status={st} len={len(body)}")
                    acc = []
                    try:
                        longstrings(json.loads(body), acc)
                    except Exception:
                        print("   not json:", body[:200])
                    for pth, v in acc[:14]:
                        print(f"   {label} {pth[:90]} :: {re.sub(chr(10), ' ', v)[:300]}")
                except Exception as e:
                    print(f"  {label} failed {type(e).__name__} {str(e)[:150]}")
        m = re.search(r"/all-brands/list/(\d+)/", url)
        if m:
            test = ("https://api.watsons.com.my/api/v2/wtcmy/products/search?fields=FULL&query=%3AbestSeller%3AproductBrandCode%3A"
                    + m.group(1) + "&pageSize=100&currentPage=0&sort=bestSeller&lang=en&curr=MYR")
            try:
                res = page.evaluate("async (u) => { const r = await fetch(u, {credentials: 'include'}); "
                                    "const t = await r.text(); let n = null, pg = null; "
                                    "try { const d = JSON.parse(t); n = (d.products || []).length; pg = d.pagination; } catch (e) {} "
                                    "return {status: r.status, len: t.length, n, pg}; }", test)
                out["detail"] += f" IN-PAGE FETCH pageSize=100: {res}"
                print("  IN-PAGE FETCH pageSize=100:", res)
            except Exception as e:
                print("  IN-PAGE FETCH failed:", type(e).__name__, str(e)[:200])
        hrefs = page.eval_on_selector_all('a[href*="/p/BP_"]', "els => els.map(e => e.href)")
        out["links"] = list(dict.fromkeys(hrefs))
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
        print(f"  product links on page: {len(r['links'])}", r["links"][:6])
        print("  excerpt:", r["excerpt"])
        for u, body in r["api"][:3]:
            print("  API", u[:500])
            try:
                d = json.loads(body)
                if "fields=FULL" in u and "productBrandCode" in u:
                    pr0 = d["products"][0]
                    print("   FULL product: every top-level key -> type/len/preview")
                    for k, v in pr0.items():
                        print(f"     {k}: {type(v).__name__} {len(str(v))} {str(v)[:110]!r}")
                    for c in pr0.get("classifications") or []:
                        for f in c.get("features") or []:
                            vals = [x.get("value", "") for x in f.get("featureValues") or []]
                            print("     feature", f.get("name"), "|", f.get("code", "")[-40:], "|", str(vals)[:120])
                    print("   pagination:", d.get("pagination"), "n products:", len(d["products"]))
                print("   keys:", list(d)[:12], "pagination:", d.get("pagination"))
                pr = (d.get("products") or [d])[0]
                print("   product keys:", list(pr)[:40])
                print("   sample:", json.dumps(pr)[:900])
            except Exception as e:
                print("   (not parsed)", type(e).__name__, body[:300])
    print("\nSUMMARY", json.dumps({r["url"]: r["outcome"] for r in results}))


if __name__ == "__main__":
    main()
