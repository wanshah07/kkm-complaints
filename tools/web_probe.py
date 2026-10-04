"""
Can a GitHub runner READ the pharmacy / retailer / brand websites Wan added to the Targets tab (4 Oct 2026)?
Read-only; writes nothing to the sheet. Same three outcomes as shop_probe.py, kept apart on purpose:
  measured            the answer arrived and we read it (JSON that parses, or HTML with real content)
  blocked             an answer arrived but it is a wall (403/429/captcha/anti-bot/empty JS shell)
  NOT A MEASUREMENT   the request itself failed (DNS, reset, timeout) - says nothing about the site
Every URL is fetched on its own connection. A feed that answers is also parsed, so "200" is never
reported as success when the body is not a product list.
Usage: python tools/web_probe.py [URL ...]   (no URLs = the built-in sample, one of each feed type)
"""
import json, re, sys, time, urllib.request, urllib.error

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")
SAMPLE = [
    ("caring shopify",   "https://estore.caring2u.com/search/suggest.json?q=cerave&resources[type]=product&resources[limit]=10"),
    ("big shopify",      "https://bigpharmacy.com.my/search/suggest.json?q=cerave&resources[type]=product&resources[limit]=10"),
    ("alpro shopify",    "https://alpropharmacy.com.my/search/suggest.json?q=cerave&resources[type]=product&resources[limit]=10"),
    ("aa shopify",       "https://aapharmacy.com.my/search/suggest.json?q=cerave&resources[type]=product&resources[limit]=10"),
    ("doctoroncall wc",  "https://www.doctoroncall.com.my/wp-json/wc/store/v1/products?search=cetaphil&per_page=100"),
    ("healthlane oc",    "https://estore.healthlane.com.my/index.php?route=product/search&search=cerave"),
    ("guardian graphql", "https://guardian.com.my/graphql?query=%7Bproducts%28search%3A%22cerave%22%2CpageSize%3A100%29%7Btotal_count%20items%7Bname%20sku%20url_key%20stock_status%20price_range%7Bminimum_price%7Bfinal_price%7Bvalue%7D%7D%7D%7D%7D%7D"),
    ("guardian search",  "https://guardian.com.my/catalogsearch/result/?q=cerave"),
    ("watsons brand",    "https://www.watsons.com.my/all-brands/b/154349/cerave"),
    ("watsons list",     "https://www.watsons.com.my/all-brands/list/154349/cerave"),
    ("brand eucerin",    "https://www.eucerin.my/"),
    ("brand cetaphil",   "https://www.cetaphil.com.my/"),
    ("brand safi",       "https://www.safi.com.my/"),
    ("brand simplysiti", "https://www.simplysiti.com.my/"),
    ("brand nuuha",      "https://nuuhabeauty.com/"),
]
WALL = re.compile(r"captcha|verify you are human|access denied|unusual traffic|akamai|cloudflare|"
                  r"attention required|just a moment|enable javascript|robot", re.I)


RM_PRICE = re.compile(r"RM\s?\d")


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,text/html,*/*",
                                               "Accept-Language": "en-MY,en;q=0.8,ms;q=0.6"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read(600000), None
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", "") if e.headers else "", e.read(4000), None
    except Exception as e:
        return None, "", b"", f"{type(e).__name__}: {e}"


def classify(label, status, ctype, body, err):
    r = {"label": label, "status": status, "ctype": ctype.split(";")[0], "bytes": len(body), "outcome": "", "detail": ""}
    if err:
        r["outcome"], r["detail"] = "NOT A MEASUREMENT", err[:160]
        return r
    text = body.decode("utf-8", "replace")
    if status in (401, 403, 429, 503) or (status and status >= 400):
        r["outcome"], r["detail"] = "blocked", f"HTTP {status} {text[:120]!r}"
        return r
    if "json" in ctype or text.lstrip()[:1] in "{[":
        try:
            j = json.loads(text)
        except Exception as e:
            r["outcome"], r["detail"] = "blocked", f"JSON expected, parse failed: {e}; head={text[:100]!r}"
            return r
        n = None
        if isinstance(j, list):
            n = len(j)
        elif isinstance(j, dict):
            res = (j.get("resources") or {}).get("results", {}).get("products") if "resources" in j else None
            if res is not None:
                n = len(res)
            elif "data" in j and isinstance(j["data"], dict) and "products" in j["data"]:
                n = len((j["data"]["products"] or {}).get("items") or [])
        r["outcome"] = "measured"
        r["detail"] = f"JSON ok, products={n}; head={text[:140]!r}"
        return r
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    visible = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", text, flags=re.S | re.I)
    visible = re.sub(r"\s+", " ", visible).strip()
    wall = WALL.findall(visible[:3000] + (title.group(1) if title else ""))
    ttl = title.group(1).strip()[:80] if title else ""
    n_rm = len(RM_PRICE.findall(visible))
    walls = sorted(set(w.lower() for w in wall))
    r["detail"] = f"title={ttl!r} visible_chars={len(visible)} rm_prices={n_rm} wall={walls}"
    r["outcome"] = "blocked" if (wall or len(visible) < 400) else "measured"
    return r


def main(argv):
    targets = [(u, u) for u in argv] if argv else SAMPLE
    out = []
    for label, url in targets:
        s, ct, body, err = fetch(url)
        r = classify(label, s, ct, body, err)
        out.append(r)
        print(f"[{r['outcome']:<17}] {r['label']:<17} HTTP {r['status']} {r['ctype']:<24} {r['bytes']:>7}B  {r['detail']}", flush=True)
        time.sleep(1.5)
    n = {k: sum(1 for r in out if r["outcome"] == k) for k in ("measured", "blocked", "NOT A MEASUREMENT")}
    print("SUMMARY", json.dumps(n))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
