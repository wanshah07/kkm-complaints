#!/usr/bin/env python3
"""
TikTok discovery for the accounts in the Targets tab (Wan, 14 Nov 2026: "I want auto found tiktok post without
manual, but I don't want kacau other live platform set").

A TikTok account's post LIST is not readable by a runner (5 Oct 2026: item_list answers an automated session with
an empty 200). TikTok's own creator EMBED page is: https://www.tiktok.com/embed/@<handle> is the public widget any
website may show, it needs no login, and its page state carries the account's pinned videos plus its ten newest
(measured 14 Nov 2026 from a GitHub runner, probe run 43589748514). So the newest posts can be NAMED without
anybody pasting a link, and the existing link reader (scraper/tiktok_links.py) does the rest.

What this does, and nothing else:
  1. reads the Targets tab (Apps Script `targets`) and keeps the active rows with a handle in the `TikTok` column;
  2. reads each handle's embed page and takes the video ids it lists;
  3. drops what is already in the sheet or the reviewed ledger (`known_urls`, `seen_urls`), anything older than
     run.min_post_date in scraper/config.yaml (a TikTok id carries its own date), and private items;
  4. prints at most TIKTOK_AUTO_MAX links (newest first) as GitHub step outputs:
         count=N   links=<space separated>   meta=<JSON: handle -> the Targets row's name / type / product hints>

It opens no browser, signs in to nothing, and writes nothing anywhere: not the sheet, not the Links tab, not the
ledger. The Instagram / Facebook / Threads sweep and its login are not involved.

Only ten posts an account are visible at a time, so there is no back-history; a run every few hours sees everything
an account posts from now on. If TikTok changes or withdraws the embed page, every handle fails here and the run
goes red, rather than quietly finding nothing.

Standard library plus `requests`. Reads APPS_SCRIPT_WEBHOOK_URL and APPS_SCRIPT_API_TOKEN.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0.0.0 Safari/537.36")
_HANDLE = re.compile(r"^[A-Za-z0-9._]{2,30}$")
_STATE = re.compile(r'<script id="__FRONTITY_CONNECT_STATE__"[^>]*>(.*?)</script>', re.S)
_VIDEO_ID = re.compile(r"tiktok\.com/@[^/]+/(?:video|photo)/(\d{10,})", re.I)
# Wan's own brands are never a target of this system, whatever a Targets row says.
_OWN = ("lumixpert", "facerinna", "facerina", "bodibreze", "nextprime", "valorith")


def call(payload: dict) -> dict:
    url, token = os.environ["APPS_SCRIPT_WEBHOOK_URL"], os.environ["APPS_SCRIPT_API_TOKEN"]
    last = ""
    for attempt in range(3):
        try:
            r = requests.post(url, json=dict(payload, token=token), timeout=180, allow_redirects=True)
            data = r.json()
            if data.get("ok"):
                return data
            last = str(data.get("error"))
        except (requests.RequestException, ValueError) as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(4 + attempt * 6)
    sys.exit(f"webhook refused {payload.get('action')}: {last[:200]}")


def emit(**kv) -> None:
    line = "\n".join(f"{k}={v}" for k, v in kv.items())
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    print(line)


def clean_handle(raw) -> str:
    """'@name', 'tiktok.com/@name', 'https://www.tiktok.com/@name?lang=en' -> 'name'; '' when it is not a handle."""
    s = str(raw or "").strip()
    m = re.search(r"tiktok\.com/@([^/?#\s]+)", s, re.I)
    if m:
        s = m.group(1)
    s = s.lstrip("@").strip()
    return s if _HANDLE.match(s) else ""


def tiktok_targets(rows: List[dict]) -> Tuple[Dict[str, dict], List[str]]:
    """({handle lower: {handle, name, type, hints}}, [notes]) for the active Targets rows with a TikTok handle."""
    out: Dict[str, dict] = {}
    notes: List[str] = []
    for t in rows or []:
        if not t.get("active", True):
            continue
        raw = next((v for k, v in (t.get("handles") or {}).items() if str(k).strip().lower() == "tiktok"), "")
        if not str(raw or "").strip():
            continue
        name = str(t.get("name") or "").strip()
        h = clean_handle(raw)
        if not h:
            notes.append(f"{name}: the TikTok column holds {str(raw)[:40]!r}, which is not a handle; skipped")
            continue
        low = (name + " " + h).lower()
        if any(o in low for o in _OWN):
            notes.append(f"{name}: own brand, never swept")
            continue
        if h.lower() in out:
            notes.append(f"{name}: @{h} is already on the row {out[h.lower()]['name']!r}; kept once")
            continue
        out[h.lower()] = {"handle": h, "name": name or h, "type": str(t.get("type") or "").strip(),
                          "hints": [str(x) for x in (t.get("product_hints") or []) if str(x).strip()][:20]}
    return out, notes


def id_date(video_id: str) -> Optional[str]:
    """A TikTok id's top 32 bits are unix seconds (the same reading scraper.py makes)."""
    try:
        return datetime.fromtimestamp(int(video_id) >> 32, timezone.utc).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def parse_embed(html: str, handle: str) -> Tuple[List[dict], str]:
    """([{id, desc}], problem). The account's own, public videos named by the embed page; problem is '' when
    the page was read, even if it lists nothing."""
    m = _STATE.search(html or "")
    if not m:
        return [], "the embed page carries no page state (TikTok changed it, or served a challenge)"
    try:
        data = ((json.loads(m.group(1)).get("source") or {}).get("data") or {})
    except ValueError:
        return [], "the embed page state is not JSON"
    node = next((v for k, v in data.items() if str(k).lower() == f"/embed/@{handle.lower()}"), None)
    if not isinstance(node, dict):
        node = next((v for k, v in data.items() if str(k).lower().startswith("/embed/@") and isinstance(v, dict)), None)
    if not isinstance(node, dict):
        return [], "the embed page state has no entry for this account"
    if node.get("isError"):
        return [], "TikTok answered the embed page with an error for this account"
    user = node.get("userInfo") or {}
    if user.get("privateAccount"):
        return [], "the account is private"
    out, seen = [], set()
    for v in node.get("videoList") or []:
        vid = str((v or {}).get("id") or "")
        if not re.fullmatch(r"\d{10,20}", vid) or vid in seen:
            continue
        if v.get("privateItem"):
            continue
        author = str(v.get("authorUniqueId") or "")
        if author and author.lower() != handle.lower():
            continue          # somebody else's video shown on the page is not this target's post
        seen.add(vid)
        out.append({"id": vid, "desc": str(v.get("desc") or "")})
    return out, ""


def read_embed(session: requests.Session, handle: str) -> Tuple[List[dict], str]:
    url = f"https://www.tiktok.com/embed/@{handle}"
    last = ""
    for attempt in range(2):
        try:
            r = session.get(url, headers={"User-Agent": UA, "Accept-Language": "en-MY,en;q=0.9,ms;q=0.8"}, timeout=40)
        except requests.RequestException as e:
            last = f"could not be fetched ({type(e).__name__})"
        else:
            if r.status_code == 200:
                return parse_embed(r.text, handle)
            if r.status_code in (400, 404):
                return [], f"TikTok has no account @{handle} (HTTP {r.status_code}); check the handle in the Targets tab"
            last = f"HTTP {r.status_code}"
        time.sleep(5)
    return [], last


def min_post_date() -> str:
    """run.min_post_date from scraper/config.yaml, read without PyYAML (the discovery job installs nothing)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scraper", "config.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            m = re.search(r'^\s*min_post_date:\s*"?(\d{4}-\d{2}-\d{2})"?', f.read(), re.M)
        return m.group(1) if m else ""
    except OSError:
        return ""


def choose(found: Dict[str, List[dict]], known_ids: set, min_date: str, cap: int) -> Tuple[List[str], Dict[str, int]]:
    """(links newest first, at most cap; counts). found is {handle: [{id, desc}]}."""
    counts = {"listed": 0, "known": 0, "old": 0, "new": 0}
    fresh: List[Tuple[int, str]] = []
    for handle, vids in found.items():
        for v in vids:
            counts["listed"] += 1
            if v["id"] in known_ids:
                counts["known"] += 1
                continue
            d = id_date(v["id"])
            if min_date and d and d < min_date:
                counts["old"] += 1
                continue
            fresh.append((int(v["id"]), f"https://www.tiktok.com/@{handle}/video/{v['id']}"))
    counts["new"] = len(fresh)
    fresh.sort(reverse=True)          # the id is time-ordered, so this is newest first across every account
    return [u for _, u in fresh[:max(0, cap)]], counts


def main() -> int:
    cap = int(os.environ.get("TIKTOK_AUTO_MAX") or 20)
    only = clean_handle(os.environ.get("INPUT_HANDLE") or "")
    targets, notes = tiktok_targets(call({"action": "targets"}).get("targets") or [])
    for n in notes:
        print(f"::notice::{n}")
    if only:
        targets = {k: v for k, v in targets.items() if k == only.lower()}
    if not targets:
        print("::notice::no active Targets row has a TikTok handle" + (f" @{only}" if only else "") + "; nothing to do")
        emit(count=0, links="", meta="{}")
        return 0

    found: Dict[str, List[dict]] = {}
    problems: List[str] = []
    session = requests.Session()
    for i, (low, t) in enumerate(targets.items()):
        if i:
            time.sleep(3)
        vids, problem = read_embed(session, t["handle"])
        if problem:
            problems.append(f"{t['name']} (@{t['handle']}): {problem}")
            print(f"::warning::TikTok discovery: {t['name']} (@{t['handle']}): {problem}")
            continue
        found[t["handle"]] = vids
        print(f"{t['name']:<28} @{t['handle']:<24} {len(vids)} video(s) listed")
    if not found:
        # Every account failed. That is the embed page itself, not seven wrong handles: say so loudly.
        print("::error::TikTok discovery read no account at all; TikTok may have changed or withdrawn the embed page")
        emit(count=0, links="", meta="{}")
        return 1

    known_ids = set()
    for action in ("known_urls", "seen_urls"):      # the sheet's complaints, then every post already judged
        for u in (call({"action": action}).get("urls") or []):
            m = _VIDEO_ID.search(str(u))
            if m:
                known_ids.add(m.group(1))
    links, counts = choose(found, known_ids, min_post_date(), cap)
    held_over = counts["new"] - len(links)
    print(f"{counts['listed']} listed, {counts['known']} already reviewed, {counts['old']} before the date limit, "
          f"{counts['new']} new" + (f"; {len(links)} taken now, {held_over} wait for the next run" if held_over else ""))
    meta = {low: {"name": t["name"], "type": t["type"], "hints": t["hints"]} for low, t in targets.items()}
    emit(count=len(links), links=" ".join(links), meta=json.dumps(meta, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
