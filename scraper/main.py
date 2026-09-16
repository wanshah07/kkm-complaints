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
    report: Dict = {"started_myt": started.isoformat(), "targets": [], "pushed": [], "skipped": [], "errors": []}

    # --- review-only mode (no browser, no webhook) ---------------------------
    if args.review_only:
        with open(args.review_only, "r", encoding="utf-8") as f:
            text = f.read()
        v = review(ReviewInput(brand=args.brand or "?", platform=args.platform or "?", url=args.url, text=text),
                   use_llm=not args.no_llm)
        print(json.dumps(v, ensure_ascii=False, indent=2))
        return 0

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
        except Exception as e:
            log.error("webhook unreachable: %s", e)
            report["errors"].append(f"webhook: {e}")
            _write_report(run_dir, report)
            return 1

    drive: Optional[DriveUploader] = None
    if os.getenv("SCREENSHOT_MODE", "apps_script").lower() == "drive_api":
        try:
            drive = DriveUploader()
        except Exception as e:
            log.warning("Drive uploader unavailable (%s); using payload upload", e)

    # --- targets: the sheet's Targets tab wins over config.yaml when reachable ---------
    brands = resolve_targets(cfg, args.targets, client, report)

    # --- scrape ---------------------------------------------------------------------
    try:
        scraper = Scraper(cfg, os.path.join(run_dir, "screenshots"))
        results = scraper.run(brands, platform_filter=args.platform, brand_filter=args.brand)
    except Exception as e:
        log.exception("browser run failed")
        report["errors"].append(f"browser: {e}")
        _write_report(run_dir, report)
        return 1

    hints = {b["name"]: b.get("product_hints", []) for b in brands}
    spend = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 0, "usd": 0.0, "models": {}}
    lookback = int(run_cfg.get("lookback_days", 0) or 0)
    min_date = str(run_cfg.get("min_post_date", "") or "")
    date_rule = f"on/after {min_date}" if min_date else (f"within {lookback} days" if lookback else "any date")
    push_risky = bool(run_cfg.get("push_risky", False))
    min_conf = float(run_cfg.get("min_confidence", 0.6))
    records: List[dict] = []

    for tr in results:
        report["targets"].append({"brand": tr.brand, "platform": tr.platform, "profile": tr.profile_url,
                                  "posts": len(tr.posts), "error": tr.error})
        for post in tr.posts:
            cu = canonical_url(post.url)
            if cu in known:
                report["skipped"].append({"url": post.url, "why": "already in sheet"})
                continue
            if not date_allowed(post.posted_at, lookback, min_date):
                report["skipped"].append({"url": post.url, "why": f"posted {post.posted_at}, outside {date_rule}"})
                continue
            if not post.text and not post.screenshot_path:
                report["skipped"].append({"url": post.url, "why": "nothing extracted"})
                continue
            v = review(ReviewInput(brand=post.brand, platform=post.platform, url=post.url, text=post.text,
                                   screenshot_path=post.screenshot_path, product_hints=hints.get(post.brand)),
                       use_llm=not args.no_llm)
            # The platform often hides the timestamp from anonymous visitors; when the reviewer can read
            # it off the screenshot, use it for the Date column and the lookback filter.
            if not post.posted_at and v.get("post_date"):
                post.posted_at = v["post_date"]
                if not date_allowed(post.posted_at, lookback, min_date):
                    report["skipped"].append({"url": post.url, "why": f"posted {post.posted_at} per screenshot, outside {date_rule}",
                                              "verdict": v["verdict"], "confidence": v.get("confidence"), "type": v.get("violation_type")})
                    known.add(cu)
                    continue
            u = v.get("usage")
            if u:
                spend["calls"] += 1
                for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                    spend[k] += u.get(k, 0) or 0
                spend["usd"] += u.get("usd", 0.0) or 0.0
                spend["models"][u.get("model", "?")] = spend["models"].get(u.get("model", "?"), 0) + 1
            entry = {"url": post.url, "verdict": v["verdict"], "confidence": v.get("confidence"),
                     "type": v.get("violation_type"), "reviewer": v.get("reviewer"),
                     "product": v.get("product_name", ""), "reason": (v.get("violation_reason") or "")[:600]}
            if v["verdict"] == "Unacceptable" and v.get("confidence", 0) >= min_conf:
                records.append(build_record(post, v, run_cfg, drive))
                report["pushed"].append(entry)
            elif v["verdict"] == "Risky" and push_risky:
                records.append(build_record(post, v, run_cfg, drive))
                report["pushed"].append(entry)
            else:
                entry["why"] = "acceptable" if v["verdict"] == "Acceptable" else f"{v['verdict']} below threshold / not pushed"
                report["skipped"].append(entry)
            known.add(cu)

    spend["usd"] = round(spend["usd"], 4)
    rate = float(run_cfg.get("usd_to_myr") or 0)
    if rate > 0:
        spend["myr"] = round(spend["usd"] * rate, 2)
        spend["myr_rate_used"] = rate
    report["spend"] = spend
    log.info("%d non-compliant post(s) to push, %d skipped", len(records), len(report["skipped"]))

    # --- push -----------------------------------------------------------------------
    if records:
        if args.dry_run or client is None:
            preview = [{k: (v if k != "screenshot_base64" else f"<{len(v)} b64 chars>") for k, v in r.items()} for r in records]
            with open(os.path.join(run_dir, "payload_preview.json"), "w", encoding="utf-8") as f:
                json.dump(preview, f, ensure_ascii=False, indent=2)
            log.info("dry run: payload written to %s", os.path.join(run_dir, "payload_preview.json"))
        else:
            try:
                totals = client.insert(records)
                report["insert"] = {k: (v if k != "ids" else v) for k, v in totals.items()}
                log.info("inserted=%d duplicates=%d errors=%d", totals["inserted"], len(totals["duplicates"]), len(totals["errors"]))
            except Exception as e:
                log.error("insert failed: %s", e)
                report["errors"].append(f"insert: {e}")
                # keep the payload on disk so it can be replayed by hand
                with open(os.path.join(run_dir, "payload_failed.json"), "w", encoding="utf-8") as f:
                    json.dump(records, f, ensure_ascii=False)

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
        brands.append({"name": t["name"], "handles": handles, "product_hints": t.get("product_hints", [])})
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
    print(f"  pushed: {len(report['pushed'])}   skipped: {len(report['skipped'])}   errors: {len(report['errors'])}")
    for p in report["pushed"]:
        print(f"    → PUSH {p['verdict']} ({p['confidence']}) {p['type']}  {p['url']}")
    for p in report["skipped"]:
        if p.get("verdict"):
            print(f"    · skip {p['verdict']} ({p.get('confidence')}) {p.get('type') or '-'} [{p.get('reviewer')}]  {p['url']}")
            if p.get("reason"):
                print(f"        {p.get('product') or ''} :: {p['reason'].replace(chr(10), ' ')[:400]}")
        else:
            print(f"    · skip {p.get('why')}  {p['url']}")
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
