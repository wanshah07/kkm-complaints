#!/usr/bin/env python3
"""
KKM Cosmetic Complaint System — Friday 23:30 MYT routine.

  scrape (Playwright) → dedupe against the sheet → NPRA review (rules + LLM) →
  screenshot → POST to the Apps Script webhook → run report in out/

Usage
  python main.py                       # full run
  python main.py --dry-run             # scrape + review, print payloads, no POST
  python main.py --brand "Eucerin" --platform Instagram
  python main.py --no-llm              # rules-only verdicts
  python main.py --review-only path.txt --brand X --platform Y --url Z   # review a pasted caption

Exit code is 0 when the run completed (even with per-target errors), 1 when nothing could run
(bad config, webhook unreachable, browser failure).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

try:  # .env next to this file is loaded when present (local / Windows runs); CI uses real env vars
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

from evaluator import ReviewInput, review
from scraper import Scraper, canonical_url, date_allowed
from uploader import AppsScriptClient, DriveUploader, attach_screenshot

def _myt():
    """
    Windows ships no IANA timezone database, so zoneinfo needs the `tzdata` package there.
    It is in requirements.txt; this fallback means a stale environment degrades to a fixed
    UTC+8 offset instead of killing the run. Malaysia has no daylight saving, so the offset
    is exact rather than an approximation.
    """
    try:
        return ZoneInfo("Asia/Kuala_Lumpur")
    except (ZoneInfoNotFoundError, KeyError):
        logging.getLogger("kkm.main").warning(
            "timezone database not available (pip install tzdata); using a fixed UTC+8 offset")
        return timezone(timedelta(hours=8), "MYT")


MYT = _myt()
HERE = os.path.dirname(os.path.abspath(__file__))

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("kkm.main")


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if not cfg.get("brands"):
        raise SystemExit("config.yaml has no brands")
    if not cfg.get("platforms"):
        raise SystemExit("config.yaml has no platforms")
    return cfg


def build_record(post, verdict: dict, run_cfg: dict, drive: Optional[DriveUploader]) -> dict:
    now_my = datetime.now(MYT)
    vt = verdict.get("violation_type") or "Other"
    if verdict["verdict"] == "Risky":
        vt = f"Risky: {vt}"
    reason = verdict.get("violation_reason", "")
    claims = verdict.get("claims") or []
    failing = [c for c in claims if c.get("verdict") in ("Unacceptable", "Risky")]
    if failing:
        reason = (reason + "\n\n" if reason else "") + "Claims:\n" + "\n".join(
            f'- [{c.get("verdict")}] "{c.get("claim")}" — {c.get("reason")} ({c.get("reference")})' for c in failing[:8])
    remarks = f"reviewer={verdict.get('reviewer', '')}"
    if verdict.get("notes"):
        remarks += f"; {verdict['notes']}"
    if post.errors:
        remarks += "; scrape: " + "; ".join(post.errors)
    rec = {
        "date": (post.posted_at or now_my.strftime("%Y-%m-%d")),
        "brand": post.brand,
        "platform": post.platform,
        "post_url": post.url,
        "extracted_text": post.text,
        "violation_type": vt,
        "violation_reason": reason,
        "product_name": verdict.get("product_name", ""),
        "notification_number": "",
        "jenis_aduan": "Iklan Kosmetik",
        "complaint_description": verdict.get("complaint_description_bm", ""),
        "confidence": round(float(verdict.get("confidence", 0)), 2),
        "remarks": remarks,
        "source": "scraper",
    }
    fname = f"{post.brand}_{post.platform}_{now_my.strftime('%Y%m%d')}_{abs(hash(post.url)) % 10_000_000}.jpg".replace(" ", "_")
    attach_screenshot(rec, post.screenshot_path, run_cfg, drive, fname)
    return rec


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="KKM cosmetic complaint scraper")
    ap.add_argument("--config", default=os.path.join(HERE, "config.yaml"))
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--brand", help="only this brand")
    ap.add_argument("--platform", help="only this platform")
    ap.add_argument("--dry-run", action="store_true", help="do not POST to the webhook")
    ap.add_argument("--no-llm", action="store_true", help="rules-only review")
    ap.add_argument("--compare", action="store_true",
                    help="put every post to both reviewers and print where they disagree; never pushes")
    ap.add_argument("--targets", choices=["auto", "sheet", "config"], default="auto",
                    help="where brands/handles come from: the sheet's Targets tab (needs webhook env), config.yaml, or auto (sheet if reachable)")
    ap.add_argument("--review-only", metavar="TEXTFILE", help="skip scraping; review this caption file")
    ap.add_argument("--url", default="manual://review", help="URL for --review-only")
    args = ap.parse_args(argv)

    started = datetime.now(MYT)
    cfg = load_config(args.config)
    run_cfg = cfg.get("run", {})
    run_dir = os.path.join(args.out, started.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(run_dir, exist_ok=True)
    report: Dict = {"started_myt": started.isoformat(), "targets": [], "pushed": [], "skipped": [],
                    "errors": [], "comparison": [], "flagged": []}

    # --- review-only mode (no browser, no webhook) ---------------------------
    if args.review_only:
        with open(args.review_only, "r", encoding="utf-8") as f:
            text = f.read()
        v = review(ReviewInput(brand=args.brand or "?", platform=args.platform or "?", url=args.url, text=text),
                   use_llm=not args.no_llm)
        print(json.dumps(v, ensure_ascii=False, indent=2))
        return 0

    if args.compare and not args.dry_run:
        # A comparison run is a measurement, not a sweep. Two reviewers disagreeing is the
        # expected outcome, and neither verdict has been adjudicated yet, so nothing it
        # produces may reach the sheet.
        log.info("--compare implies --dry-run; nothing will be pushed")
        args.dry_run = True

    # --- backend -----------------------------------------------------------------
    client: Optional[AppsScriptClient] = None
    known: set = set()
    if not args.dry_run:
        try:
            client = AppsScriptClient()
            ping = client.ping()
            log.info("webhook ok: %s", ping)
            known = {canonical_url(u) for u in client.known_urls()}
            log.info("%d known post URLs in the sheet", len(known))
            # Posts already judged and found compliant are not in the sheet, so without this
            # ledger every clean post is re-scraped and re-billed on every run, and a complaint
            # already actioned can come back around.
            seen = {canonical_url(u) for u in client.seen_urls()}
            new_to_us = len(seen - known)
            known |= seen
            log.info("%d previously reviewed post URLs in the ledger (%d of them not complaints)",
                     len(seen), new_to_us)
        except Exception as e:
            log.error("webhook unreachable: %s", e)
            report["errors"].append(f"webhook: {e}")
            _write_report(run_dir, report)
            return 1

    drive: Optional[DriveUploader] = None
    from scraper import env_str
    if env_str("SCREENSHOT_MODE", "apps_script").lower() == "drive_api":
        try:
            drive = DriveUploader()
        except Exception as e:
            log.warning("Drive uploader unavailable (%s); using payload upload", e)

    # --- targets: the sheet's Targets tab wins over config.yaml when reachable ---------
    brands = resolve_targets(cfg, args.targets, client, report)

    # --- scrape -----------------------------------------------------------------------
    # Streamed, one target at a time, and each is reviewed and filed before the next is
    # scraped. The alternative cost a three-hour run: everything was scraped first, the job
    # timeout cut in with one target left, and not a single post had been reviewed or filed.
    scraper = Scraper(cfg, os.path.join(run_dir, "screenshots"))
    planned = scraper.plan(brands, platform_filter=args.platform, brand_filter=args.brand)
    budget_min = float(run_cfg.get("max_run_minutes") or 0)
    deadline = (time.time() + budget_min * 60) if budget_min > 0 else None
    if deadline:
        log.info("%d target(s) planned; budget %.0f min, so the run stops starting new targets at %s MYT",
                 len(planned), budget_min,
                 datetime.fromtimestamp(deadline, MYT).strftime("%H:%M"))
    results = scraper.iter_run(brands, platform_filter=args.platform, brand_filter=args.brand,
                               should_stop=(lambda: deadline is not None and time.time() >= deadline))
    reached: set = set()

    hints = {b["name"]: b.get("product_hints", []) for b in brands}
    types = {b["name"]: b.get("type", "") for b in brands}
    spend = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 0, "usd": 0.0, "models": {}}
    lookback = int(run_cfg.get("lookback_days", 0) or 0)
    min_date = str(run_cfg.get("min_post_date", "") or "")
    date_rule = f"on/after {min_date}" if min_date else (f"within {lookback} days" if lookback else "any date")
    push_risky = bool(run_cfg.get("push_risky", False))
    min_conf = float(run_cfg.get("min_confidence", 0.6))
    records: List[dict] = []      # this target's findings, filed before the next target starts
    reviewed: List[dict] = []     # this target's judged posts, for the ledger
    all_records: List[dict] = []  # the whole run, for the dry-run preview and the final count

    def _file_target() -> None:
        """
        Push what this target produced, then record what it judged. In that order: a post must
        not be marked reviewed until any complaint it produced has reached the sheet, or a
        failed insert would bury the finding and the ledger would skip it forever after.
        """
        if args.dry_run or client is None:
            records.clear()
            reviewed.clear()
            return
        insert_ok = True
        if records:
            try:
                totals = client.insert(records)
                agg = report.setdefault("insert", {"inserted": 0, "duplicates": [], "errors": [], "ids": []})
                agg["inserted"] += totals.get("inserted", 0)
                for k in ("duplicates", "errors", "ids"):
                    agg[k] += totals.get(k, [])
                log.info("filed: inserted=%d duplicates=%d errors=%d",
                         totals.get("inserted", 0), len(totals.get("duplicates", [])),
                         len(totals.get("errors", [])))
            except Exception as e:
                insert_ok = False
                log.error("insert failed: %s", e)
                report["errors"].append(f"insert: {e}")
                # keep the payload on disk so it can be replayed by hand
                failed = os.path.join(run_dir, "payload_failed.json")
                existing = []
                if os.path.exists(failed):
                    try:
                        with open(failed, encoding="utf-8") as f:
                            existing = json.load(f)
                    except Exception:
                        existing = []
                with open(failed, "w", encoding="utf-8") as f:
                    json.dump(existing + records, f, ensure_ascii=False)
        if reviewed and insert_ok:
            report["marked_seen"] = report.get("marked_seen", 0) + client.mark_seen(reviewed)
        records.clear()
        reviewed.clear()

    try:
        for tr in results:
            reached.add((tr.brand, tr.platform))
            report["targets"].append({"brand": tr.brand, "platform": tr.platform, "profile": tr.profile_url,
                                      "posts": len(tr.posts), "error": tr.error})
            for post in tr.posts:
                cu = canonical_url(post.url)
                if getattr(post, "not_owned", False):
                    report["skipped"].append({"url": post.url, "why": (post.errors[-1] if post.errors else "different account")})
                    continue
                if getattr(post, "blocked", False):
                    # A login wall is not a clean post. Sending the gate for review buys a confident
                    # "Acceptable" on something that was never read, so the post stays unreviewed and
                    # is counted as an error: a platform we cannot see is a gap in the sweep, not a pass.
                    why = post.errors[-1] if post.errors else "login wall - not reviewed"
                    report["skipped"].append({"url": post.url, "why": why})
                    report["errors"].append(f"{post.brand}/{post.platform}: {why}  {post.url}")
                    continue
                if cu in known:
                    report["skipped"].append({"url": post.url, "why": "already in sheet"})
                    continue
                if not date_allowed(post.posted_at, lookback, min_date):
                    report["skipped"].append({"url": post.url, "why": f"posted {post.posted_at}, outside {date_rule}"})
                    continue
                if not post.text and not post.screenshot_path:
                    report["skipped"].append({"url": post.url, "why": "nothing extracted"})
                    continue
                ri = ReviewInput(brand=post.brand, platform=post.platform, url=post.url, text=post.text,
                                 screenshot_path=post.screenshot_path, product_hints=hints.get(post.brand),
                                 target_type=types.get(post.brand, ""))

                def _spend(verdict: dict) -> None:
                    # Count every call as soon as it returns: later branches can skip the post, and a
                    # call that was paid for must still appear in the spend total.
                    u = verdict.get("usage")
                    if not u:
                        return
                    spend["calls"] += 1
                    for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                        spend[k] += u.get(k, 0) or 0
                    spend["usd"] += u.get("usd", 0.0) or 0.0
                    spend["models"][u.get("model", "?")] = spend["models"].get(u.get("model", "?"), 0) + 1

                if args.compare:
                    # The same post, the same screenshot, two reviewers. Agreement on a handful of
                    # compliant posts proves very little; the disagreements are the whole point, and
                    # they are Wan's to adjudicate. Nothing is pushed from a comparison run.
                    a = review(ri, use_llm=not args.no_llm, provider="anthropic")
                    b = review(ri, use_llm=not args.no_llm, provider="openai")
                    _spend(a)
                    _spend(b)
                    report["comparison"].append({
                        "url": post.url, "brand": post.brand, "platform": post.platform,
                        "a": {"reviewer": a.get("reviewer"), "verdict": a.get("verdict") or "no verdict",
                              "confidence": a.get("confidence"), "type": a.get("violation_type"),
                              "reason": (a.get("violation_reason") or "")[:400]},
                        "b": {"reviewer": b.get("reviewer"), "verdict": b.get("verdict") or "no verdict",
                              "confidence": b.get("confidence"), "type": b.get("violation_type"),
                              "reason": (b.get("violation_reason") or "")[:400]},
                        # A reviewer that produced nothing has not agreed with anyone.
                        "agree": bool(a.get("verdict")) and a.get("verdict") == b.get("verdict"),
                    })
                    continue

                v = review(ri, use_llm=not args.no_llm)
                _spend(v)
                if not v.get("verdict"):
                    # The rules fallback always produces one, so reaching here means even that failed.
                    # An unreviewed post is an error to surface, never a quiet skip.
                    why = f"reviewer returned no verdict: {v.get('notes') or 'unknown'}"
                    report["skipped"].append({"url": post.url, "why": why})
                    report["errors"].append(f"{post.brand}/{post.platform}: {why}  {post.url}")
                    continue

                # The platform often hides the timestamp from anonymous visitors; when the reviewer can read
                # it off the screenshot, use it for the Date column and the lookback filter.
                if not post.posted_at and v.get("post_date"):
                    post.posted_at = v["post_date"]
                    if not date_allowed(post.posted_at, lookback, min_date):
                        report["skipped"].append({"url": post.url, "why": f"posted {post.posted_at} per screenshot, outside {date_rule}",
                                                  "verdict": v["verdict"], "confidence": v.get("confidence"),
                                                  "type": v.get("violation_type"), "reviewer": v.get("reviewer")})
                        known.add(cu)
                        continue
                entry = {"url": post.url, "verdict": v["verdict"], "confidence": v.get("confidence"),
                         "type": v.get("violation_type"), "reviewer": v.get("reviewer"),
                         "product": v.get("product_name", ""), "reason": (v.get("violation_reason") or "")[:600]}
                reviewed.append({"url": post.url, "brand": post.brand, "platform": post.platform,
                                 "verdict": v["verdict"], "confidence": v.get("confidence"),
                                 "reviewer": v.get("reviewer", "")})
                would_push = ((v["verdict"] == "Unacceptable" and v.get("confidence", 0) >= min_conf)
                              or (v["verdict"] == "Risky" and push_risky))
                if would_push and v.get("needs_visual_verification"):
                    # The reviewer's own reasoning cited wording that is not in the caption it was
                    # given. That is the shape of the sunburn/burn hallucination on run 35249509012:
                    # a confident, specific, unsupported verdict. It does not reach the sheet on the
                    # strength of that reasoning alone - it waits for eyes on the screenshot.
                    entry["why"] = "needs visual verification before filing - see GUIDE.md"
                    report["flagged"].append(entry)
                    log.warning("%s: %s (%s) held back pending visual check, not pushed",
                               post.url, v["verdict"], v.get("confidence"))
                elif would_push:
                    rec = build_record(post, v, run_cfg, drive)
                    records.append(rec)
                    all_records.append(rec)
                    report["pushed"].append(entry)
                else:
                    entry["why"] = "acceptable" if v["verdict"] == "Acceptable" else f"{v['verdict']} below threshold / not pushed"
                    report["skipped"].append(entry)
                known.add(cu)

            _file_target()
    except Exception as e:
        # Whatever has already been filed stays filed; say what stopped the sweep and finish
        # the report rather than losing the targets that did complete.
        log.exception("sweep stopped early")
        report["errors"].append(f"sweep stopped early: {e}")
        _file_target()

    missed = [{"brand": b, "platform": p} for b, p, _ in planned if (b, p) not in reached]
    if missed:
        report["not_reached"] = missed
        log.warning("%d of %d target(s) not reached this run; the next run picks them up",
                    len(missed), len(planned))

    spend["usd"] = round(spend["usd"], 4)
    rate = float(run_cfg.get("usd_to_myr") or 0)
    if rate > 0:
        spend["myr"] = round(spend["usd"] * rate, 2)
        spend["myr_rate_used"] = rate
    report["spend"] = spend
    log.info("%d non-compliant post(s) found, %d skipped", len(all_records), len(report["skipped"]))

    # A live run filed each target as it finished; nothing is held back to the end any more.
    # A dry run files nothing, so its payload is written out here instead.
    if all_records and (args.dry_run or client is None):
        preview = [{k: (v if k != "screenshot_base64" else f"<{len(v)} b64 chars>") for k, v in r.items()}
                   for r in all_records]
        with open(os.path.join(run_dir, "payload_preview.json"), "w", encoding="utf-8") as f:
            json.dump(preview, f, ensure_ascii=False, indent=2)
        log.info("dry run: payload written to %s", os.path.join(run_dir, "payload_preview.json"))
    elif report.get("marked_seen"):
        log.info("ledger: %d reviewed post(s) recorded as seen across %d target(s)",
                 report["marked_seen"], len(report["targets"]))

    report["finished_myt"] = datetime.now(MYT).isoformat()
    report["duration_s"] = round(time.time() - started.timestamp(), 1)
    _write_report(run_dir, report)
    _print_summary(report)
    return 0


def resolve_targets(cfg: dict, mode: str, client: Optional[AppsScriptClient], report: dict) -> List[dict]:
    """
    'sheet'  : Targets tab of the Google Sheet (Brand | Active | <platform columns> | Product hints)
    'config' : brands: in config.yaml
    'auto'   : sheet when APPS_SCRIPT_WEBHOOK_URL + token are set and answer, else config.yaml
    Only rows with Active = TRUE are scraped. Unknown platform columns are ignored with a warning.
    """
    if mode == "config":
        report["targets_source"] = "config.yaml"
        return cfg["brands"]
    c = client
    if c is None:
        try:
            c = AppsScriptClient()
        except Exception as e:
            if mode == "sheet":
                raise SystemExit(f"--targets sheet needs the webhook env: {e}")
            log.info("no webhook env; targets from config.yaml")
            report["targets_source"] = "config.yaml"
            return cfg["brands"]
    try:
        rows = c.targets()
    except Exception as e:
        if mode == "sheet":
            raise SystemExit(f"could not read Targets tab: {e}")
        log.warning("Targets tab unreachable (%s); using config.yaml", e)
        report["targets_source"] = "config.yaml (sheet unreachable)"
        return cfg["brands"]
    known_platforms = set(cfg.get("platforms", {}).keys())
    brands: List[dict] = []
    for t in rows:
        if not t.get("active", True):
            continue
        handles = {}
        for plat, handle in (t.get("handles") or {}).items():
            if plat in known_platforms:
                handles[plat] = handle
            else:
                log.warning("Targets tab: platform column %r has no entry in config.yaml platforms; skipped", plat)
        brands.append({"name": t["name"], "handles": handles, "product_hints": t.get("product_hints", []),
                       "type": t.get("type", "")})
    if not brands:
        log.warning("Targets tab has no active rows; using config.yaml")
        report["targets_source"] = "config.yaml (sheet empty)"
        return cfg["brands"]
    log.info("targets from sheet: %s", ", ".join(b["name"] for b in brands))
    report["targets_source"] = "sheet"
    return brands


def _write_report(run_dir: str, report: dict) -> None:
    path = os.path.join(run_dir, "run_report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log.info("report: %s", path)


def _print_summary(report: dict) -> None:
    print("\n=== KKM scraper run summary ===")
    for t in report["targets"]:
        print(f"  {t['brand']:<16} {t['platform']:<10} posts={t['posts']:<3} {('ERR ' + t['error']) if t['error'] else 'ok'}")
    flagged_n = len(report.get("flagged") or [])
    print(f"  pushed: {len(report['pushed'])}   skipped: {len(report['skipped'])}   "
          f"flagged: {flagged_n}   errors: {len(report['errors'])}")
    for p in report["pushed"]:
        print(f"    → PUSH {p['verdict']} ({p['confidence']}) {p['type']}  {p['url']}")
    for p in report.get("flagged") or []:
        print(f"    ⚠ HOLD {p['verdict']} ({p['confidence']}) {p['type']} — {p['why']}  {p['url']}")
        if p.get("reason"):
            print(f"        {p['reason'].replace(chr(10), ' ')[:500]}")
    for p in report["skipped"]:
        if p.get("verdict"):
            why = f" — {p['why']}" if p.get("why") else ""
            print(f"    · skip {p['verdict']} ({p.get('confidence')}) {p.get('type') or '-'} [{p.get('reviewer')}]{why}  {p['url']}")
            if p.get("reason"):
                print(f"        {p.get('product') or ''} :: {p['reason'].replace(chr(10), ' ')[:400]}")
        else:
            print(f"    · skip {p.get('why')}  {p['url']}")
    if report.get("comparison"):
        rows = report["comparison"]
        agreed = [r for r in rows if r["agree"]]
        split = [r for r in rows if not r["agree"]]
        a_name = rows[0]["a"]["reviewer"] or "A"
        b_name = rows[0]["b"]["reviewer"] or "B"
        print(f"\n  --- reviewer comparison ---   {a_name}  vs  {b_name}")
        print(f"  {len(rows)} post(s) reviewed by both; agreed on {len(agreed)}, split on {len(split)}")
        if rows:
            print(f"  raw agreement {100.0 * len(agreed) / len(rows):.0f}%  "
                  f"(agreement on compliant posts is cheap; the splits below are what matters)")
        for r in split:
            print(f"    ! SPLIT  {r['brand']} / {r['platform']}  {r['url']}")
            for side in ("a", "b"):
                d = r[side]
                print(f"        {d['reviewer']}: {d['verdict']} ({d['confidence']}) {d['type'] or '-'}")
                if d.get("reason"):
                    print(f"            {d['reason'].replace(chr(10), ' ')[:300]}")
        for r in agreed:
            d = r["a"]
            print(f"    = agree  {d['verdict']} ({d['confidence']} / {r['b']['confidence']})  {r['url']}")
        print("  Adjudicate the splits yourself — the reviewers do not settle each other.")
    if report.get("not_reached"):
        missed = report["not_reached"]
        print(f"\n  --- not reached this run ({len(missed)}) ---")
        print("  The run stopped on its time budget before these. Run again to pick them up;")
        print("  with the Reviewed ledger deployed, what was already done is skipped.")
        for m in missed:
            print(f"    · {m['brand']:<24} {m['platform']}")
    if report.get("insert"):
        print(f"  sheet insert: {report['insert'].get('inserted')} new, {len(report['insert'].get('duplicates', []))} duplicate(s)")
    sp = report.get("spend") or {}
    if sp.get("calls"):
        models = ", ".join(f"{m}x{n}" for m, n in sp.get("models", {}).items())
        per = sp["usd"] / sp["calls"] if sp["calls"] else 0
        print("\n  --- reviewer spend ---")
        print(f"  calls         {sp['calls']}   ({models})")
        print(f"  input tokens  {sp['input_tokens']:,}   (+{sp['cache_read_input_tokens']:,} cached read, "
              f"{sp['cache_creation_input_tokens']:,} cache write)")
        print(f"  output tokens {sp['output_tokens']:,}")
        line = f"  cost          USD {sp['usd']:.4f}   (USD {per:.4f} per reviewed post)"
        if sp.get("myr") is not None:
            line += f"   ~ RM {sp['myr']:.2f} at {sp['myr_rate_used']}"
        print(line)
        if sp["usd"] == 0 and sp["calls"]:
            print("  note: no price table for this provider; tokens counted, cost not estimated")
    elif report.get("targets"):
        print("\n  reviewer spend: no model calls (nothing reached the reviewer)")


if __name__ == "__main__":
    sys.exit(main())
