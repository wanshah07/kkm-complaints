#!/usr/bin/env python3
"""
Take the screenshot of a filed TikTok row again, from TikTok's embed player (Wan, 14 Nov 2026).

Why: a logged-out TikTok video page lays a "Log in to TikTok" dialog over the video, and until the fix of
14 Nov 2026 the screenshot was taken with that dialog on top of the post (rows 7338-7340, the first automatic
TikTok run). A row already in the sheet is never opened again by a sweep, so its picture stays as it is unless
it is asked for here.

What it does, per complaint ID given: read the row, check it is a TikTok video row that is still open (New or
In-Progress) and not an own-brand row, take the picture from tiktok.com/embed/v2/<id> (scraper.py,
_tiktok_embed_shot), store it through the same Apps Script upload the scraper uses, and write the new Drive link
into Screenshot Link with update_fields. Nothing else on the row changes: not Status, not the verdict, not the
text. A row that fails any check is named with its reason and left exactly as it was.

Usage: python tools/tiktok_reshoot.py --ids KKM-...,KKM-... [--dry-run]
"""
import argparse, base64, os, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scraper"))
import yaml  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from scraper import Post, Scraper  # noqa: E402
from uploader import AppsScriptClient, compress_screenshot  # noqa: E402

OWN = ("lumixpert", "facerinna", "facerina", "bodibreze", "nextprime", "valorith")
VIDEO = re.compile(r"^https://www\.tiktok\.com/@[A-Za-z0-9._]+/(?:video|photo)/\d{10,}$")


class _Shooter(Scraper):
    """Only what _tiktok_embed_shot reads: the run settings and where to write the picture."""
    def __init__(self, run_cfg: dict, out_dir: str):
        self.run_cfg, self.out_dir = run_cfg, out_dir


def refuse(row: dict) -> str:
    if not row:
        return "no such row"
    url = str(row.get("Post URL") or "").strip()
    if str(row.get("Platform") or "").strip() != "TikTok" or not VIDEO.match(url):
        return "not a TikTok video row"
    if str(row.get("Status") or "").strip() not in ("New", "In-Progress"):
        return f"status is {row.get('Status')!r}; only an open row is re-photographed"
    if any(o in (str(row.get("Brand") or "") + " " + url).lower() for o in OWN):
        return "own-brand row"
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    ids = [x.strip() for x in re.split(r"[\s,]+", args.ids) if x.strip()][:40]
    if not ids:
        print("no complaint ID given"); return 1
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "scraper", "config.yaml"), encoding="utf-8") as f:
        run_cfg = (yaml.safe_load(f) or {}).get("run", {})
    out = os.path.join(os.getcwd(), "reshoot_tmp"); os.makedirs(out, exist_ok=True)
    client = AppsScriptClient()
    done = failed = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=bool(run_cfg.get("headless", True)))
        ctx = browser.new_context()
        try:
            for cid in ids:
                row = client._post({"action": "get", "id": cid}).get("row") or {}
                why = refuse(row)
                if why:
                    failed += 1; print(f"RESHOOT|{cid}|skipped|{why}", flush=True); continue
                url = row["Post URL"].strip()
                post = Post(brand=row.get("Brand") or "", platform="TikTok", url=url)
                if not _Shooter(run_cfg, out)._tiktok_embed_shot(ctx, post, "reshoot", url):
                    failed += 1; print(f"RESHOOT|{cid}|failed|the embed player could not be read", flush=True); continue
                if args.dry_run:
                    done += 1; print(f"RESHOOT|{cid}|dry-run|picture taken, nothing written", flush=True); continue
                link = client.upload_screenshot(base64.b64encode(compress_screenshot(post.screenshot_path)).decode(),
                                                "image/jpeg", f"{cid}_tiktok.jpg")
                if not link:
                    failed += 1; print(f"RESHOOT|{cid}|failed|the picture could not be stored", flush=True); continue
                client._post({"action": "update_fields", "id": cid, "fields": {"Screenshot Link": link}})
                done += 1; print(f"RESHOOT|{cid}|done|{link}", flush=True)
        finally:
            ctx.close(); browser.close()
    print(f"re-photographed {done}, not done {failed}")
    return 0 if done or not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
