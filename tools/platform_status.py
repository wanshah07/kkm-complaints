#!/usr/bin/env python3
"""
Is each platform the scraper depends on live right now? Writes status.json for the dashboard.

Opens one stable public profile per platform with the SAME recorded browser session the sweeps use
(PW_STORAGE_STATE_B64), and reports one of:
    live      the session is signed in and the page offered what a signed-in visit offers
    degraded  the page loaded but offered less than expected (a signed-out grid, a thin feed)
    down      a login wall, or the platform ended the session
    unknown   the probe could not tell (timeout, page layout change); never reported as live
TikTok is not probed here: the sweep never reads TikTok accounts, only links pasted into the Links tab, so
its status is "the link checker's last run" and "how many days the TikTok session has left".

Written 14 Nov 2026, after Instagram signed the recorded session out and four runs reported "posts=12 ok"
for every account before anybody noticed. A status nobody has to remember to look for is the fix.

Usage (the workflow does this; locally set PW_STORAGE_STATE_B64 first):
    python tools/platform_status.py --out status.json
It prints one line per platform and always exits 0, so a down platform never hides itself behind a red run:
the dashboard is where it shows.
"""
import argparse, base64, gzip, json, os, sys, time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scraper"))

# One stable, public, busy profile per platform. Nothing about them is shown on the dashboard.
PROBES = [
    # key, label, platform in config.yaml, profile handle, links expected from a signed-in visit
    ("instagram", "Instagram", "Instagram", "dr.waninuuha", 25),
    ("facebook", "Facebook", "Facebook", "cetaphilmalaysia", 1),
    ("threads", "Threads", "Threads", "dr.waninuuha", 25),
]


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cookie_days_left(raw_b64, site_words, name_words):
    """Days until the soonest-expiring login cookie of a site; None when there is none."""
    try:
        raw = base64.b64decode(raw_b64)
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        state = json.loads(raw)
    except Exception:
        return None
    best = None
    for c in state.get("cookies", []) or []:
        dom = str(c.get("domain", "")).lower()
        if not any(w in dom for w in site_words) or str(c.get("name", "")) not in name_words:
            continue
        exp = c.get("expires")
        if exp is None or exp <= 0:
            continue
        d = (float(exp) - time.time()) / 86400.0
        best = d if best is None else min(best, d)
    return None if best is None else round(best, 1)


def probe_one(sc, browser, key, label, platform, handle, want_links):
    from scraper import _collect_links, _dismiss_dialogs, _is_login_wall, _wait_for_posts
    from playwright.sync_api import TimeoutError as PWTimeout
    pcfg = sc.platforms[platform]
    url = pcfg["profile_url"].format(handle=handle)
    ctx = sc._context(browser)
    page = ctx.new_page()
    out = {"key": key, "label": label, "state": "unknown", "detail": "", "links": None}
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3500)
        _dismiss_dialogs(page)
        lost = sc._session_signed_out(ctx, platform)
        if lost:
            out.update(state="down", detail="Instagram ended the saved login. Record the session again (guide section 4).")
            return out
        if _is_login_wall(page):
            out.update(state="down", detail="%s shows a login page. Record the session again (guide section 4)." % label)
            return out
        _wait_for_posts(page, pcfg["post_link_patterns"])
        links = _collect_links(page, pcfg["post_link_patterns"], url, 40, platform=platform, handle=handle,
                               scroll_pause=[1.0, 2.0])
        out["links"] = len(links)
        # The cookie that proves a signed-in browser, held AFTER the page loaded (a rejected one is cleared).
        names = {"Instagram": ("sessionid",), "Facebook": ("c_user", "xs"), "Threads": ("sessionid",)}[platform]
        # All cookies of the platform's own domain family, not those matching the profile URL: Threads
        # redirects threads.net to threads.com, and its login cookie lives on the domain it redirected to.
        dom_word = {"Instagram": "instagram.com", "Facebook": "facebook.com", "Threads": "threads."}[platform]
        try:
            held = {c.get("name") for c in ctx.cookies() if c.get("value") and dom_word in str(c.get("domain", "")).lower()}
        except Exception:
            held = set()
        signed_in = all(n in held for n in names) if platform == "Facebook" else any(n in held for n in names)
        if not signed_in:
            out.update(state="down", detail="The saved login is no longer held after loading the page. Record the session again.")
        elif len(links) >= want_links:
            out.update(state="live", detail="Signed in and reading posts normally.")
        elif len(links) == 0:
            out.update(state="degraded", detail="Signed in, but no posts were offered. It may be a page change or a short block; the next check will say.")
        else:
            out.update(state="degraded" if platform != "Facebook" else "live",
                       detail="Signed in, but fewer posts than normal were offered (%d, expected %d or more)." % (len(links), want_links)
                       if platform != "Facebook" else "Signed in and reading posts normally.")
        return out
    except PWTimeout:
        out.update(state="unknown", detail="The page did not load in time. Not counted as live.")
        return out
    except Exception as e:  # never let one platform hide the others
        out.update(state="unknown", detail="Could not check: %s" % type(e).__name__)
        return out
    finally:
        try:
            ctx.close()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="status.json")
    ap.add_argument("--no-browser", action="store_true", help="skip the platform probes (offline test)")
    ap.add_argument("--last-runs", default="", help="JSON from the workflow: last runs of the scraper and the TikTok checker")
    args = ap.parse_args()

    result = {"checked_at": now_iso(), "platforms": [], "notes": []}
    raw_b64 = os.environ.get("PW_STORAGE_STATE_B64", "")
    shop_b64 = os.environ.get("PW_STORAGE_STATE_SHOP_B64", "")

    if not args.no_browser:
        import yaml
        from playwright.sync_api import sync_playwright
        from scraper import Scraper
        with open(os.path.join(HERE, "..", "scraper", "config.yaml"), "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        sc = Scraper(cfg, os.path.join(HERE, "status_tmp"))
        if not sc._storage_state_path:
            result["notes"].append("No saved login is configured (PW_STORAGE_STATE_B64 empty or invalid): every platform is read signed out.")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            try:
                for i, (key, label, platform, handle, want) in enumerate(PROBES):
                    if i:
                        time.sleep(8)
                    r = probe_one(sc, browser, key, label, platform, handle, want)
                    if not sc._storage_state_path and r["state"] == "live":
                        r["state"], r["detail"] = "degraded", "Reading posts, but no saved login is configured."
                    result["platforms"].append(r)
                    print("%-10s %-9s links=%s  %s" % (label, r["state"], r["links"], r["detail"]), flush=True)
            finally:
                browser.close()

    days = cookie_days_left(raw_b64, ("instagram.com",), ("sessionid",))
    result["session_days_left"] = {"instagram": days,
                                   "facebook": cookie_days_left(raw_b64, ("facebook.com",), ("xs",)),
                                   "threads": cookie_days_left(raw_b64, ("threads.",), ("sessionid",))}
    tt_days = cookie_days_left(shop_b64, ("tiktok.",), ("sessionid", "sessionid_ss", "sid_tt"))

    last = {}
    if args.last_runs and os.path.exists(args.last_runs):
        try:
            last = json.load(open(args.last_runs, "r", encoding="utf-8"))
        except Exception:
            last = {}
    tk = last.get("tiktok") or {}
    tt = {"key": "tiktok", "label": "TikTok links", "links": None, "state": "unknown",
          "detail": "Works on links pasted into the dashboard; the sweep does not read TikTok accounts."}
    if tk.get("conclusion") == "success":
        tt["state"] = "live"
        tt["detail"] = "The link checker is running (last run %s). Works on links you paste into the Links tab." % tk.get("at", "")[:16].replace("T", " ")
    elif tk.get("conclusion"):
        tt["state"] = "down"
        tt["detail"] = "The link checker's last run did not succeed (%s)." % tk.get("conclusion")
    if tt_days is not None:
        tt["detail"] += " TikTok login: %s day(s) left." % int(tt_days)
        if tt_days < 7 and tt["state"] == "live":
            tt["state"] = "degraded"
    result["platforms"].append(tt)

    # TikTok accounts (14 Nov 2026): tools/tiktok_discover.py finds the newest videos of the Targets rows with a
    # TikTok handle through TikTok's public embed page. It fails its run when no account could be read, so its last
    # run IS the probe. No pill until it has run once.
    ta = last.get("tiktok_auto") or {}
    if ta.get("conclusion"):
        ok = ta.get("conclusion") == "success"
        result["platforms"].append({
            "key": "tiktok_auto", "label": "TikTok accounts", "links": None, "state": "live" if ok else "down",
            "detail": ("New videos of the accounts in the Targets tab's TikTok column are found and reviewed "
                       "automatically (last run %s). Only an account's ten newest are visible at a time."
                       % ta.get("at", "")[:16].replace("T", " ")) if ok else
                      ("The automatic TikTok discovery's last run did not succeed (%s). TikTok may have changed its "
                       "embed page, or a video hit a login wall; pasted links still work." % ta.get("conclusion"))})

    sc_last = last.get("scraper") or {}
    if sc_last:
        result["last_sweep"] = {"conclusion": sc_last.get("conclusion"), "at": sc_last.get("at"), "title": sc_last.get("title", "")}

    states = [p["state"] for p in result["platforms"]]
    result["overall"] = "down" if "down" in states else ("degraded" if "degraded" in states or "unknown" in states else "live")
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("overall:", result["overall"], "->", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
