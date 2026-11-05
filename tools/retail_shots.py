"""
Screenshots for the complaint rows that came from a shop or brand WEBSITE (Wan, 5 Nov 2026: "get the
screenshot for all row without screenshot").

Why those rows have none: scraper/webfeeds.py reads a product page through the shop's own JSON or HTML
feed, never through a rendered page, so it files the listing copy with no picture. The dashboard will not
mark a row "ready to file" without a Screenshot Link, so these rows sit one field short.

What this does, per row: open the row's Post URL in a real Chromium (headed under Xvfb, because Watsons'
Akamai wall answers 403 to headless - tools/watsons_probe.py), check that the page really is the product
page, take a full-page screenshot, send it through the same Apps Script upload the scraper uses, and write
the Drive link into Screenshot Link with update_fields. Nothing else on the row changes.

A wall is a gap, never a pass. A page is only photographed when it (a) is not an Access Denied / captcha /
challenge page and (b) carries the product's own name. Anything else is logged with its reason and left
without a screenshot; three walls in a row on one host skip that host for the rest of the run.

Usage: python tools/retail_shots.py [--limit N] [--ids ID,ID] [--hosts a.com,b.com] [--dry-run] [--max-minutes M]
"""
import argparse, base64, os, random, re, sys, time
from collections import Counter, defaultdict
from urllib.parse import urlsplit

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scraper"))
from uploader import AppsScriptClient, compress_screenshot  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

SOCIAL = {"instagram", "threads", "facebook", "tiktok", "youtube", "x", "twitter"}
WALL = re.compile(r"access denied|captcha|verify you are human|unusual traffic|are you a robot|"
                  r"pardon our interruption|request unsuccessful|reference #|just a moment", re.I)
STOP = set("the and for with of in a an to by".split())
UNIT = re.compile(r"^(\d+(\.\d+)?(ml|g|gm|gr|l|kg|iu|mg)|\d+s)$")


def norm(s):
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9%+ ]", " ", s.replace("é", "e").replace("è", "e"))
    return re.sub(r"\s+", " ", s).strip()


def toks(s):
    return [w for w in norm(s).split() if w not in STOP and not UNIT.match(w) and not re.match(r"^\d+$", w) and len(w) > 2]


def host(u):
    return re.sub(r"^www\.", "", urlsplit(u).netloc.lower())


def pick_rows(client, args):
    rows = client.list_rows()
    want_ids = {x.strip() for x in args.ids.split(",") if x.strip()}
    want_hosts = {x.strip().lower() for x in args.hosts.split(",") if x.strip()}
    out = []
    for r in rows:
        if want_ids and r.get("ID") not in want_ids:
            continue
        url = (r.get("Post URL") or "").strip()
        if not url.lower().startswith("http") or r.get("Screenshot Link"):
            continue
        if (r.get("Status") or "New") not in ("New", "In-Progress"):
            continue
        if (r.get("Jenis Aduan") or "Iklan Kosmetik") != "Iklan Kosmetik":
            continue
        if (r.get("Platform") or "").strip().lower() in SOCIAL:
            continue
        if want_hosts and host(url) not in want_hosts:
            continue
        out.append(r)
    # rows that already carry a NOT number first: a screenshot is the last thing they lack
    out.sort(key=lambda r: (0 if re.match(r"^NOT\s*\d", r.get("Nombor Notifikasi") or "") else 1, host(r["Post URL"]), r["ID"]))
    return out[: args.limit] if args.limit else out


def shoot(browser, r, tmp):
    url = r["Post URL"].strip()
    ctx = browser.new_context(locale="en-MY", timezone_id="Asia/Kuala_Lumpur", viewport={"width": 1280, "height": 1000})
    page = ctx.new_page()
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        status = resp.status if resp else None
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        time.sleep(2)
        title = (page.title() or "")[:120]
        text = page.inner_text("body")[:300000] if page.query_selector("body") else ""
        if status and status >= 400:
            return "blocked", f"HTTP {status} '{title}'", None
        if WALL.search(title) or WALL.search(text[:1500]):
            return "blocked", f"wall text in '{title}'", None
        need = toks(r.get("Nama Kosmetik") or "")
        hay = norm(title + " " + text)
        if need:
            have = sum(1 for w in need if w in hay)
            if have < max(1, int(0.6 * len(need) + 0.5)):
                return "wrong-page", f"only {have}/{len(need)} name words on the page '{title}'", None
        # let lazy sections render, then photograph the whole page (the uploader caps it at 2400px)
        for _ in range(4):
            page.mouse.wheel(0, 900)
            time.sleep(0.6)
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(0.8)
        path = os.path.join(tmp, f"{r['ID']}.png")
        page.screenshot(path=path, full_page=True)
        return "shot", f"HTTP {status} '{title}'", path
    except Exception as e:
        return "NOT A MEASUREMENT", str(e)[:140], None
    finally:
        ctx.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ids", default="")
    ap.add_argument("--hosts", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-minutes", type=int, default=150)
    args = ap.parse_args()
    client = AppsScriptClient()
    rows = pick_rows(client, args)
    print(f"{len(rows)} rows to photograph (dry_run={args.dry_run})", flush=True)
    t0 = time.time(); tally = Counter(); per_host = defaultdict(Counter); streak = Counter(); skipped_hosts = set()
    tmp = os.path.join(os.getcwd(), "shots_tmp"); os.makedirs(tmp, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        for r in rows:
            if time.time() - t0 > args.max_minutes * 60:
                print("run budget reached; the rest is left for the next run", flush=True); break
            h = host(r["Post URL"])
            if h in skipped_hosts:
                tally["skipped-host"] += 1; continue
            outcome, why, path = shoot(browser, r, tmp)
            link = ""
            if outcome == "shot" and not args.dry_run:
                try:
                    data = compress_screenshot(path)
                    link = client.upload_screenshot(base64.b64encode(data).decode(), "image/jpeg", f"{r['ID']}_page.jpg")
                    if not link:
                        outcome, why = "upload-failed", "no link returned"
                    else:
                        client._post({"action": "update_fields", "id": r["ID"], "fields": {"Screenshot Link": link}})
                except Exception as e:
                    outcome, why = "upload-failed", str(e)[:140]
            tally[outcome] += 1; per_host[h][outcome] += 1
            streak[h] = streak[h] + 1 if outcome == "blocked" else 0
            print(f"SHOT|{r['ID']}|{h}|{outcome}|{link}|{why}", flush=True)
            if streak[h] >= 3:
                skipped_hosts.add(h); print(f"  3 walls in a row on {h}: skipping the rest of its rows this run", flush=True)
            time.sleep(random.uniform(3, 8))
        browser.close()
    print("SUMMARY", dict(tally), flush=True)
    for h, c in per_host.items():
        print(f"  {h}: {dict(c)}", flush=True)


if __name__ == "__main__":
    main()
