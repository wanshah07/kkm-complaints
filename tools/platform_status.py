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
import argparse, base64, gzip, json, os, re, subprocess, sys, time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scraper"))

# One stable, public, busy profile per platform. Nothing about them is shown on the dashboard.
PROBES = [
    # key, label, platform in config.yaml, profile handle, links expected from a signed-in visit
    ("instagram", "Instagram", "Instagram", "dr.waninuuha", 25),
    ("facebook", "Facebook", "Facebook", "cetaphilmalaysia", 1),
    ("threads", "Threads", "Threads", "dr.waninuuha", 25),
]


def myt(iso):
    """'2026-11-26 20:52 MYT' for an ISO time the status files hold in UTC. Malaysia has no daylight saving, so a fixed +8 is exact.
    The status text used to print UTC with no label ('last run 2026-11-26 12:52'), which read as Malaysia time and looked 8 hours behind
    (Wan, 26 Nov 2026: "fix clock ... follow local device clock or MYT"). A string that cannot be read is returned as it came."""
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (t + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M MYT")
    except (ValueError, TypeError):
        return str(iso or "")[:16].replace("T", " ")


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


def facebook_by_ads(sc):
    return str(sc.run_cfg.get("facebook_source", "ads")).lower() == "ads"


def probe_facebook_ads(sc, browser, key, label, handle):
    """Facebook is read through Meta's Ad Library, with no login: can the test page's advertisements be listed?"""
    out = {"key": key, "label": label, "state": "unknown", "detail": "", "links": None}
    ctx = sc._context(browser, use_storage=False)
    try:
        _page, page_id, cards, err, _how = sc.facebook_ads_listing(ctx, handle, 12, brand="Cetaphil")
        if err:
            out.update(state="down" if "login" in err.lower() else "degraded",
                       detail="Meta's Ad Library could not be read for the test page: %s." % err)
        else:
            out.update(state="live", links=len(cards),
                       detail="Read through Meta's Ad Library, which needs no login: %d advertisement(s) listed for the test page." % len(cards))
    except Exception as e:
        out.update(state="unknown", detail="Could not check: %s" % type(e).__name__)
    finally:
        try:
            ctx.close()
        except Exception:
            pass
    return out


def probe_public(sc, browser, key, label, platform, handle, why):
    """The login is gone or absent: can the platform still be read as any visitor? 'degraded' when it can
    (the sweep then reads only each account's newest posts), 'down' when it cannot."""
    from scraper import _collect_links, _dismiss_dialogs
    out = {"key": key, "label": label, "state": "down", "detail": why, "links": None}
    ctx = sc._context(browser, use_storage=False)
    try:
        if platform == "Instagram":
            if sc._instagram_source() == "login":
                return out
            _page, codes, err = sc.instagram_embed_listing(ctx, handle)
            n = len(codes)
            if n:
                out.update(state="degraded", links=n,
                           detail="No working login (%s). Still reading the newest %d posts of each account through Instagram's "
                                  "public embed; older posts need the session recorded again (guide section 4)." % (why.rstrip("."), n))
            else:
                out["detail"] = "%s And the public embed could not be read either: %s." % (why, err)
        else:
            pcfg = sc.platforms[platform]
            url = pcfg["profile_url"].format(handle=handle)
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3500)
            _dismiss_dialogs(page)
            links = _collect_links(page, pcfg["post_link_patterns"], url, 12, platform=platform, handle=handle, scroll_pause=[1.0, 2.0])
            if links:
                out.update(state="degraded", links=len(links),
                           detail="No working login (%s). Still reading the newest %d posts of each account as any visitor may; "
                                  "older posts need the session recorded again (guide section 4)." % (why.rstrip("."), len(links)))
    except Exception as e:
        out["detail"] = "%s The public view could not be checked (%s)." % (why, type(e).__name__)
    finally:
        try:
            ctx.close()
        except Exception:
            pass
    return out


def probe_one(sc, browser, key, label, platform, handle, want_links):
    from scraper import _collect_links, _dismiss_dialogs, _is_login_wall, _wait_for_posts
    if platform == "Facebook" and facebook_by_ads(sc):
        return probe_facebook_ads(sc, browser, key, label, handle)
    if platform in ("Instagram", "Threads") and not sc._storage_state_path:
        return probe_public(sc, browser, key, label, platform, handle, "No saved login is configured.")
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
            return probe_public(sc, browser, key, label, platform, handle, "Instagram ended the saved login.")
        if _is_login_wall(page):
            if platform in ("Instagram", "Threads"):
                return probe_public(sc, browser, key, label, platform, handle, "%s shows a login page to the saved login." % label)
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
        if not signed_in and platform in ("Instagram", "Threads"):
            return probe_public(sc, browser, key, label, platform, handle, "The saved login is no longer held after loading the page.")
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


def carry_previous(path):
    """The Instagram / Facebook / Threads entries of the previous status.json, each keeping the time it was really
    probed (`at`; a file written before 15 Nov 2026 has only the file's own checked_at). None when unusable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:
        return None
    out = []
    for p in prev.get("platforms") or []:
        if p.get("key") in ("instagram", "facebook", "threads"):
            q = dict(p)
            q["at"] = p.get("at") or prev.get("checked_at") or ""
            q["carried"] = True
            out.append(q)
    return out if len(out) == 3 else None


# ---------------------------------------------------------------------------------------------------------------
# While sweeps run back to back, the login is never free for a probe, so Instagram and Threads showed "old" for a day
# (15 Nov 2026) although both were being read all day. The sweep is itself the best test of the login: it opens real
# profiles with it. So the status takes its evidence from the last finished sweep: how many accounts of each platform
# it read, and whether Instagram signed the session out. No second browser ever touches the session.
_TABLE_ROW = re.compile(r"\s{2,}(?P<brand>.+?)\s{2,}(?P<plat>Instagram|Facebook|Threads|TikTok)\s+posts=(?P<n>\d+)\s+(?P<rest>.*?)\s*$")


def evidence_from_log(text):
    """Per platform, from a sweep's log: accounts read (ok / partial) and walls, and whether the session was ended."""
    out = {}
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text or "")
    for line in text.splitlines():
        m = _TABLE_ROW.search(line)
        if m:
            e = out.setdefault(m.group("plat"), {"ok": 0, "wall": 0, "signed_out": False})
            rest = m.group("rest").lower()
            if rest.startswith("ok") or rest.startswith("partial"):
                e["ok"] += 1
            elif "login wall" in rest or "login prompt" in rest:
                e["wall"] += 1
        if "signed the recorded session out" in line:
            out.setdefault("Instagram", {"ok": 0, "wall": 0, "signed_out": False})["signed_out"] = True
    return out


def apply_sweep_evidence(carried, found):
    """Replace a carried Instagram / Threads entry with what a finished sweep showed, when that is newer.
    `found` is {platform: (evidence, finished_at)}."""
    out = []
    for c in carried:
        hit = found.get({"instagram": "Instagram", "threads": "Threads"}.get(c.get("key"), ""))
        if not hit or not hit[1] or (c.get("at") or "") >= hit[1]:
            out.append(c)
            continue
        ev, run_at = hit
        when = myt(run_at)
        q = dict(c)
        if ev["signed_out"]:
            q.update(state="down", at=run_at, carried=False, links=None,
                     detail="Instagram ended the saved login during the sweep that finished %s. Record the session again (guide section 4)." % when)
        elif ev["ok"] >= 1:
            q.update(state="live", at=run_at, carried=False, links=ev["ok"],
                     detail="Signed in and reading posts: the sweep that finished %s read %d account(s) on %s." % (when, ev["ok"], c.get("label", "")))
        out.append(q)
    return out


def last_sweep_evidence(repo, runs=5):
    """{platform: (evidence, finished_at)} from the newest finished scraper runs that read that platform, each with its
    own run's time. {} on any trouble; the reason is printed so the workflow log says why."""
    def gh(path):
        # A job log carries colour codes, and newer gh refuses to print those unless told it may. Older gh has no
        # such flag, so it is asked with the flag first and without it if the flag is unknown.
        r = subprocess.run(["gh", "api", "--allow-escape-sequences", path], capture_output=True, text=True, timeout=120)
        if r.returncode != 0 and "unknown flag" in (r.stderr or ""):
            r = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            return r.stdout
        # Last resort, with no gh in the way: the same call with the job's own token (the log address redirects to a
        # signed one, which urllib follows without sending the token on).
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        if token:
            try:
                import urllib.request
                req = urllib.request.Request("https://api.github.com/" + path, headers={
                    "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "User-Agent": "kkm-status"})
                with urllib.request.urlopen(req, timeout=120) as resp:
                    return resp.read().decode("utf-8", "replace")
            except Exception as e:
                print("sweep evidence: %s failed through gh (%s) and directly (%s)" % (path.split("?")[0], (r.stderr or "").strip()[:120], type(e).__name__), flush=True)
                return ""
        print("sweep evidence: gh api %s failed: %s" % (path.split("?")[0], (r.stderr or "").strip()[:200]), flush=True)
        return ""
    if not repo:
        print("sweep evidence: GITHUB_REPOSITORY is not set", flush=True)
        return {}
    try:
        listing = json.loads(gh("repos/%s/actions/workflows/scraper.yml/runs?per_page=%d&status=completed" % (repo, runs)) or "{}")
    except Exception as e:
        print("sweep evidence: unreadable run list: %s" % type(e).__name__, flush=True)
        return {}
    found = {}
    for run in (listing.get("workflow_runs") or []):
        if run.get("conclusion") not in ("success", "failure"):
            continue
        try:
            jobs = json.loads(gh("repos/%s/actions/runs/%s/jobs" % (repo, run["id"])) or "{}").get("jobs") or []
            log = gh("repos/%s/actions/jobs/%s/logs" % (repo, jobs[0]["id"])) if jobs else ""
        except Exception as e:
            print("sweep evidence: run %s unreadable: %s" % (run.get("id"), type(e).__name__), flush=True)
            continue
        ev = evidence_from_log(log)
        print("sweep evidence: run %s finished %s: %s" % (run.get("id"), run.get("updated_at"), {k: v["ok"] for k, v in ev.items()} or "no platform table"), flush=True)
        for plat, e in ev.items():
            if plat not in found and (e["ok"] or e["signed_out"]):
                found[plat] = (e, run.get("updated_at") or "")
        if all(k in found for k in ("Instagram", "Threads")):
            break
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="status.json")
    ap.add_argument("--no-browser", action="store_true", help="skip the platform probes (offline test)")
    ap.add_argument("--last-runs", default="", help="JSON from the workflow: last runs of the scraper and the TikTok checker")
    ap.add_argument("--carry", default="", help="the previous status.json: a sweep or an account check is using the login "
                                                "right now, so do not probe; keep each platform's last result and its time")
    args = ap.parse_args()

    result = {"checked_at": now_iso(), "platforms": [], "notes": []}
    raw_b64 = os.environ.get("PW_STORAGE_STATE_B64", "")
    if (os.environ.get("PW_STORAGE_STATE_IG_B64") or "").strip():
        # the Instagram secret is laid over the main state inside the scraper, so the days left must read the same mix
        from session_state import combined_b64
        raw_b64 = combined_b64(raw_b64, os.environ["PW_STORAGE_STATE_IG_B64"])
    shop_b64 = os.environ.get("PW_STORAGE_STATE_SHOP_B64", "")

    carried = carry_previous(args.carry) if args.carry else None
    sc = None
    if not args.no_browser:
        import yaml
        from playwright.sync_api import sync_playwright
        from scraper import Scraper
        with open(os.path.join(HERE, "..", "scraper", "config.yaml"), "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        sc = Scraper(cfg, os.path.join(HERE, "status_tmp"))
    if carried is not None:
        # The probe uses the same login as the sweep. Two browsers on one session at once is what gets a session
        # signed out, so while a sweep or an account check runs the last real result is kept, with its own time.
        # Facebook is the exception since 15 Nov 2026: it is read through the Ad Library with no login at all, so
        # it is tested every time. Otherwise a day of back-to-back sweeps would show its last result all day.
        fresh_fb = sc is not None and facebook_by_ads(sc)
        if fresh_fb:
            fb = next(p for p in PROBES if p[0] == "facebook")
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
                try:
                    r = probe_facebook_ads(sc, browser, fb[0], fb[1], fb[3])
                finally:
                    browser.close()
            r["at"] = now_iso()
            carried = [r if c.get("key") == "facebook" else c for c in carried]
            if not any(c.get("key") == "facebook" for c in carried):
                carried.insert(1, r)
            print("%-10s %-9s links=%s  %s" % (r["label"], r["state"], r["links"], r["detail"]), flush=True)
        found = last_sweep_evidence(os.environ.get("GITHUB_REPOSITORY", ""))
        before = [(c.get("key"), c.get("state"), c.get("at")) for c in carried]
        carried = apply_sweep_evidence(carried, found)
        from_sweep = [c for c, b in zip(carried, before) if (c.get("key"), c.get("state"), c.get("at")) != b]
        result["platforms"].extend(carried)
        result["notes"].append("%s not probed: a sweep or an account check was using the login.%s" % (
            "Instagram and Threads were" if fresh_fb else "Instagram, Facebook and Threads were",
            " Their state comes from the last finished sweep." if from_sweep else ""))
        for r in carried:
            if not (fresh_fb and r.get("key") == "facebook"):
                print("%-10s %-9s carried from %s" % (r["label"], r["state"], r.get("at", "")), flush=True)
    elif sc is not None:
        if not sc._storage_state_path:
            result["notes"].append("No saved login is configured (PW_STORAGE_STATE_B64 empty or invalid): every platform is read signed out.")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            try:
                for i, (key, label, platform, handle, want) in enumerate(PROBES):
                    if i:
                        time.sleep(8)
                    r = probe_one(sc, browser, key, label, platform, handle, want)
                    if not sc._storage_state_path and r["state"] == "live" and not (platform == "Facebook" and facebook_by_ads(sc)):
                        r["state"], r["detail"] = "degraded", "Reading posts, but no saved login is configured."
                    r["at"] = now_iso()
                    result["platforms"].append(r)
                    print("%-10s %-9s links=%s  %s" % (label, r["state"], r["links"], r["detail"]), flush=True)
            finally:
                browser.close()

    days = cookie_days_left(raw_b64, ("instagram.com",), ("sessionid",))
    result["session_days_left"] = {"instagram": days,
                                   # No Facebook login is used when it is read through the Ad Library.
                                   "facebook": None if (sc is not None and facebook_by_ads(sc)) else cookie_days_left(raw_b64, ("facebook.com",), ("xs",)),
                                   "threads": cookie_days_left(raw_b64, ("threads.",), ("sessionid",))}
    tt_days = cookie_days_left(shop_b64, ("tiktok.",), ("sessionid", "sessionid_ss", "sid_tt"))

    last = {}
    if args.last_runs and os.path.exists(args.last_runs):
        try:
            last = json.load(open(args.last_runs, "r", encoding="utf-8"))
        except Exception:
            last = {}
    tk = last.get("tiktok") or {}
    tt = {"key": "tiktok", "label": "TikTok links", "links": None, "state": "unknown", "at": tk.get("at", ""),
          "detail": "Works on links pasted into the dashboard; the sweep does not read TikTok accounts."}
    if tk.get("conclusion") == "success":
        tt["state"] = "live"
        tt["detail"] = "The link checker is running (last run %s). Works on links you paste into the Links tab." % myt(tk.get("at", ""))
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
            "at": ta.get("at", ""),
            "detail": ("New videos of the accounts in the Targets tab's TikTok column are found and reviewed "
                       "automatically (last run %s). Only an account's ten newest are visible at a time."
                       % myt(ta.get("at", ""))) if ok else
                      ("The automatic TikTok discovery's last run did not succeed (%s). TikTok may have changed its "
                       "embed page, or a video hit a login wall; pasted links still work." % ta.get("conclusion"))})

    # Apify (26 Nov 2026) is the MAIN reader of Instagram, Playwright with the saved login the fallback. So Instagram is "live" when
    # Apify answers, even while the saved login is ended: say so, and keep the login's own state in the words.
    from apify_reader import plan_status
    ap = plan_status(os.environ.get("APIFY_TOKEN", ""))
    if ap:
        ig = next((p for p in result["platforms"] if p.get("key") == "instagram"), None)
        if ig is not None and ap[0] == "live" and ig.get("state") != "live":
            ig["detail"] = "Read through Apify, so no login of ours is needed. The saved login (the fallback) is not working: " + str(ig.get("detail") or "")[:180]
            ig["state"] = "live"
        result["platforms"].append({"key": "apify", "label": "Apify", "links": None, "state": ap[0], "at": now_iso(), "detail": ap[1]})

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
