#!/usr/bin/env python3
"""
Account check: is every handle in the Targets tab live and readable? (Wan, 14 Nov 2026: "can I add/remove/edit
accounts targets for all platforms, and got status the account is run and live and tested ... do dry run for all")

A sweep only learns that a handle is wrong when it reaches that account, which can be days after the row was
typed. This visits each account's PROFILE and nothing else: no post is opened, nothing is reviewed, nothing is
filed. It is the first step of a sweep (the same code: Scraper._scrape_target, stopped before the first post), so
"live" here means exactly "the sweep can read this account".

Per account it records one of:
  live        posts are visible                         renamed    live, but the account answers to another handle
  empty       the profile opens and shows no posts      not_found  the platform says there is no such profile
  private     the profile is private to our login       blocked    a login wall or soft block: not the account's fault
  limited     only the public, signed-out view was read: our login was not accepted for that page
  error       the visit itself failed

Instagram, Facebook and Threads are read with the sweep's own login (PW_STORAGE_STATE_B64), paced, one profile at a
time, so this shares the sweep's concurrency group and never runs beside it. TikTok is read from TikTok's public
embed page (tools/tiktok_discover.py), no login. If Instagram signs the recorded session out, the run stops at once
and says so; the accounts not reached stay "not checked", never "not found".

Results go to the sheet's TargetStatus tab through the Apps Script action `target_status_update` (Targets.gs), which
the dashboard's Targets panel reads. Against a deployment that predates Targets.gs the results are printed and the
run still succeeds. Accounts never checked go first, then the ones checked longest ago, so a handle added today is
tested by the next run.

Usage: python tools/account_check.py [--limit N] [--offset N] [--platform P] [--brand NAME] [--max-minutes M]
                                     [--recheck-hours H] [--dry-run]
"""
import argparse, json, os, sys, time
from collections import Counter
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scraper"))
sys.path.insert(0, HERE)

import yaml  # noqa: E402
import requests  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from scraper import Scraper, _pace, env_str  # noqa: E402
from uploader import AppsScriptClient  # noqa: E402
import tiktok_discover as ttd  # noqa: E402

PACE = [12, 28]          # seconds between profiles: a profile visit opens no post, so less than the sweep's 45-120
PROFILE_LINKS = 12       # enough to say "posts are visible" without scrolling the whole grid
OWN = ("lumixpert", "facerinna", "facerina", "bodibreze", "nextprime", "valorith")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pairs_from(rows, social_platforms, only_platform="", only_brand=""):
    """[(brand, platform, handle)] for active rows, in sheet order: a row's platforms stay together, so two visits
    to the same platform are a whole row apart."""
    out = []
    for t in rows or []:
        if not t.get("active", True):
            continue
        name = str(t.get("name") or "").strip()
        if not name or any(o in name.lower() for o in OWN):
            continue
        if only_brand and name.lower() != only_brand.lower():
            continue
        for plat, handle in (t.get("handles") or {}).items():
            plat, handle = str(plat).strip(), str(handle or "").strip()
            if not handle or (only_platform and plat.lower() != only_platform.lower()):
                continue
            if plat in social_platforms or plat.lower() == "tiktok":
                out.append((name, plat, handle))
    return out


def order(pairs, status, recheck_hours):
    """Never checked first, then oldest check first. Accounts checked within recheck_hours are left out."""
    def key(p):
        return f"{p[0]}\x01{p[1]}".lower()
    cutoff = time.time() - recheck_hours * 3600 if recheck_hours > 0 else None
    fresh, rest = 0, []
    for i, p in enumerate(pairs):
        s = status.get(key(p)) or {}
        at = str(s.get("checked_at") or "")
        same_handle = str(s.get("handle") or "").lower() == p[2].lower()
        ts = 0.0
        if at and same_handle:
            try:
                ts = datetime.strptime(at[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                ts = 0.0
        if cutoff and ts and ts >= cutoff:
            fresh += 1
            continue
        rest.append((ts, i, p))
    rest.sort()
    return [p for _, _, p in rest], fresh


def classify(res, links: int, signed_in: bool = True) -> dict:
    """state / detail / suggest from a TargetResult stopped before its first post."""
    err = str(res.error or "")
    low = err.lower()
    if low.startswith("login wall") or "login prompt" in low or "soft-block" in low or "asked for a login" in low:
        return {"state": "blocked", "detail": "The platform asked for a login or slowed us down; the account itself was not judged."}
    if "profile not available" in low:
        return {"state": "not_found", "detail": "The platform says this profile is not available. Check the handle."}
    if "private" in low:
        return {"state": "private", "detail": "The profile is private to our login, so its posts cannot be read."}
    if "no posts yet" in low:
        return {"state": "empty", "detail": "The account exists but has not posted anything yet."}
    if low.startswith("no post links found"):
        return {"state": "empty", "detail": "The profile opened but showed no posts (no posts yet, or the platform hid them)."}
    if err:
        return {"state": "error", "detail": err.split(" — ")[0][:160]}
    if signed_in and getattr(res, "signed_out_view", False):
        # Signed in, the page showed nothing; signed out, it showed something. That is the login being refused
        # for this page, and the sweep then reads only the public view (on Facebook, usually one post). Calling
        # that "live" hid a dead Facebook session for a day (14 Nov 2026).
        return {"state": "limited", "detail": f"Only the public, signed-out view could be read ({links} post(s)). "
                                              "Our login was not accepted for this page; record the session again."}
    if getattr(res, "via_ads", False):
        # Facebook is read through Meta's Ad Library. A page running no advertisement is still a page that reads.
        how = str(getattr(res, "ads_how", "") or "")
        if not links and "none of" in how:
            # Looked up by name and nothing links to this handle. That is a page running nothing here, or a handle
            # that is not the page's own; the two cannot be told apart from the Library, so it is not called Live.
            return {"state": "empty", "detail": "No advertisement by this page was found in Malaysia's Ad Library, and the page itself "
                                                "could not be opened to confirm the handle. Check the handle if this page does advertise. ("
                                                + how[:200] + ")"}
        return {"state": "live", "detail": ((f"{links} advertisement(s) running in Malaysia (Meta Ad Library)." if links
                                             else "No advertisement by this page is running in Malaysia right now (Meta Ad Library).")
                                            + (" Found: " + how[:220] + "." if how else ""))}
    out = {"state": "live", "detail": f"{links} post(s) visible on the profile."}
    if res.renamed_to:
        out.update(state="renamed", suggest=res.renamed_to,
                   detail=f"Live, but the account now answers to @{res.renamed_to}. Correct the handle.")
    elif res.drift_note:
        out["detail"] += " " + str(res.drift_note)[:200]
    return out


def check_tiktok(session, handle: str) -> dict:
    vids, problem = ttd.read_embed(session, ttd.clean_handle(handle) or handle)
    if problem:
        low = problem.lower()
        state = "not_found" if "no account" in low else "private" if "private" in low else "blocked"
        return {"state": state, "detail": problem[:200], "posts": 0}
    if not vids:
        return {"state": "empty", "detail": "The account's embed page lists no videos.", "posts": 0}
    return {"state": "live", "detail": f"{len(vids)} video(s) listed (pinned plus the ten newest).", "posts": len(vids)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="most accounts this run (0 = as many as the time allows)")
    ap.add_argument("--offset", type=int, default=0, help="skip this many accounts of the ordered list")
    ap.add_argument("--platform", default="")
    ap.add_argument("--brand", default="")
    ap.add_argument("--max-minutes", type=float, default=150)
    ap.add_argument("--recheck-hours", type=float, default=0, help="leave out accounts checked this recently")
    ap.add_argument("--dry-run", action="store_true", help="check, print, write nothing to the sheet")
    args = ap.parse_args()

    with open(os.path.join(HERE, "..", "scraper", "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg.setdefault("run", {})["max_posts_per_profile"] = PROFILE_LINKS
    client = AppsScriptClient()
    pairs = pairs_from(client.targets(), set((cfg.get("platforms") or {}).keys()), args.platform, args.brand)

    status, can_write = {}, not args.dry_run
    backend_states = None
    try:
        admin = client._post({"action": "targets_admin"}, retries=2)
        status = {str(k).lower(): v for k, v in (admin.get("status") or {}).items()}
        backend_states = admin.get("states")          # absent on a Targets.gs deployed before 15 Nov 2026
    except Exception as e:
        if "unknown action" in str(e).lower():
            print("::notice::the deployed Apps Script has no Targets panel support yet (Targets.gs + a new version); "
                  "results are printed here and not stored", flush=True)
            can_write = False
        else:
            print(f"::warning::account status could not be read ({str(e)[:120]}); checking in sheet order", flush=True)
    todo, fresh = order(pairs, status, args.recheck_hours)
    todo = todo[args.offset:]
    if args.limit > 0:
        todo = todo[:args.limit]
    print(f"{len(pairs)} account(s) in the Targets tab; {fresh} checked recently and left out; {len(todo)} to check now", flush=True)

    out_dir = os.path.join(os.getcwd(), "account_check_tmp")
    scraper = Scraper(cfg, out_dir)
    scraper.should_stop = lambda: True          # never open a post: the profile is the whole visit
    signed_in = bool(getattr(scraper, "_storage_state_path", None))

    results, pending, tally = [], [], Counter()
    deadline = time.time() + args.max_minutes * 60
    stopped = ""
    tt_session = requests.Session()

    def flush():
        nonlocal can_write
        if not pending or not can_write:
            pending.clear()
            return
        try:
            client._post({"action": "target_status_update", "updates": list(pending)}, retries=3)
        except Exception as e:
            if "unknown action" in str(e).lower():
                can_write = False
            print(f"::warning::account status not stored ({str(e)[:120]})", flush=True)
        pending.clear()

    with sync_playwright() as pw:
        launch = dict(headless=bool(scraper.run_cfg.get("headless", True)))
        path = env_str("PW_CHROMIUM_PATH")
        if path and os.path.exists(path):
            launch["executable_path"] = path
        browser = pw.chromium.launch(**launch)
        try:
            for i, (brand, plat, handle) in enumerate(todo):
                if time.time() > deadline:
                    stopped = f"time budget reached after {i} of {len(todo)} account(s); the rest are checked by the next run"
                    break
                if i:
                    _pace(PACE, f"{brand} on {plat}")
                rec = {"brand": brand, "platform": plat, "handle": handle, "checked_at": now_iso(), "suggest": ""}
                if plat.lower() == "tiktok":
                    rec.update(check_tiktok(tt_session, handle))
                else:
                    res = scraper._scrape_target(browser, brand, plat, handle)
                    if plat in scraper.session_lost:
                        stopped = f"{plat} signed the recorded session out; record it again (GUIDE section 4). Stopped after {i} account(s)"
                        break
                    links = int(getattr(res, "links_found", 0) or 0)
                    rec.update(classify(res, links, signed_in))
                    rec["posts"] = links
                    if rec["state"] == "limited" and "limited" not in (backend_states or []):
                        # A backend that does not know the state would drop the whole line; say it the old way.
                        rec["state"] = "blocked"
                results.append(rec)
                pending.append(rec)
                tally[(plat, rec["state"])] += 1
                print("CHECK|%s|%s|%s|%s|%s|%s|%s" % (brand, plat, handle, rec["state"], rec.get("posts", 0),
                                                     rec.get("suggest", ""), rec["detail"]), flush=True)
                if len(pending) >= 15:
                    flush()
        finally:
            browser.close()
    flush()

    print("\n=== Account check summary ===")
    for plat in sorted({p for p, _ in tally}):
        row = ", ".join(f"{s} {n}" for (p, s), n in sorted(tally.items()) if p == plat)
        print(f"  {plat:<10} {row}")
    print(f"  checked {len(results)} of {len(todo)}" + ("" if can_write else "  (nothing stored)"))
    with open(os.path.join(os.getcwd(), "account_check.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False)
    if stopped:
        print(f"::{'error' if 'signed' in stopped else 'notice'}::{stopped}", flush=True)
    return 1 if "signed" in stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
