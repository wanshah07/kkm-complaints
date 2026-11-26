"""
Apify as the MAIN reader, Playwright as the fallback (Wan, 26 Nov 2026: "untuk kkmcomplaint system jadikan APIFY main dan playwright as fallback").

Why: the saved Instagram login kept dying (ended ~8 h, ~16 h, ~5.5 h and, on 26 Nov, ~15 minutes after it was recorded: Instagram does not
like a login recorded at home being used from GitHub's data-centre addresses). Apify's Instagram scraper needs no login of ours and answered
the probe in 32 seconds with four posts, one of them 2 hours old, for US$0.008 (tools/apify_probe.py, 26 Nov 2026).

What this module does, and nothing else: asks Apify's Instagram scraper for each account's newest posts and hands them back as plain
records (code, address, caption, date, owner, pictures). scraper.py turns them into Posts and takes the evidence screenshot from the post's public
embed (no login) or, failing that, from the picture Apify returned. EVERY failure here means "not served": the target goes down the old route
(saved login, then public embed), so an Apify outage, a missing token, an exhausted plan or a refused account never stops a sweep.

Cost control (the free plan is small): one run reads a BATCH of accounts (the start-up cost is shared), batches are fetched only as the sweep
reaches them (a sweep cut short by its time budget never paid for accounts it did not reach), each account is asked for at most
APIFY_POSTS_PER_PROFILE posts newer than APIFY_NEWER_THAN_DAYS, and the sweep stops asking once it has spent APIFY_MAX_USD_PER_RUN or the
month's plan is 90% used. Env: APIFY_TOKEN, APIFY_MODE (auto | off), APIFY_IG_ACTOR, APIFY_POSTS_PER_PROFILE (8), APIFY_NEWER_THAN_DAYS (14),
APIFY_BATCH (20), APIFY_MAX_USD_PER_RUN (1.0), APIFY_RUN_TIMEOUT_S (600).
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

import requests

log = logging.getLogger("kkm.apify")

API = "https://api.apify.com/v2/"
DEFAULT_IG_ACTOR = "apify/instagram-scraper"
TERMINAL = ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT")
_IG_HANDLE = re.compile(r"instagram\.com/([A-Za-z0-9_.]+)/?")


def _num(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


@dataclass
class ApifyPost:
    code: str
    url: str                       # https://www.instagram.com/<owner>/p/<code>/ (or /reel/)
    text: str
    posted_at: Optional[str]       # ISO timestamp as Apify gave it (UTC)
    owner: str
    kind: str = "p"                # "p" or "reel"
    images: List[str] = field(default_factory=list)   # first picture first; a carousel lists its slides
    pinned: bool = False
    sponsored: bool = False


def normalise_item(it: dict) -> Optional[ApifyPost]:
    """One dataset item -> ApifyPost, or None for an item that is not a post (an error record, no short code)."""
    if not isinstance(it, dict) or it.get("error") or not it.get("shortCode"):
        return None
    code = str(it["shortCode"])
    owner = str(it.get("ownerUsername") or "").strip()
    reel = str(it.get("productType") or "").lower() == "clips" or str(it.get("type") or "").lower() == "video"
    kind = "reel" if reel else "p"
    imgs: List[str] = []
    for u in [it.get("displayUrl")] + list(it.get("images") or []):
        if isinstance(u, str) and u.startswith("http") and u not in imgs:
            imgs.append(u)
    return ApifyPost(code=code, url=f"https://www.instagram.com/{owner}/{kind}/{code}/" if owner else f"https://www.instagram.com/{kind}/{code}/",
                     text=str(it.get("caption") or ""), posted_at=str(it.get("timestamp") or "") or None, owner=owner, kind=kind,
                     images=imgs, pinned=bool(it.get("isPinned")), sponsored=bool(it.get("paidPartnership")))


def handle_of_item(it: dict) -> str:
    """Which requested account an item answers: its inputUrl (exact), else the post's owner. Lower case, no @."""
    m = _IG_HANDLE.search(str(it.get("inputUrl") or ""))
    return (m.group(1) if m else str(it.get("ownerUsername") or "")).lower()


class ApifyFeed:
    """Instagram accounts read through Apify, a batch at a time, only as the sweep reaches them."""

    def __init__(self, token: str, plan: List[str], actor: str = DEFAULT_IG_ACTOR, per_profile: int = 8, newer_than_days: int = 14,
                 batch: int = 20, max_usd: float = 1.0, run_timeout_s: int = 600, session: Optional[requests.Session] = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time):
        self.token = token
        self.actor = actor.replace("/", "~")
        self.plan = [h.lower() for h in plan]
        self.per_profile = max(1, int(per_profile))
        self.newer_than_days = max(1, int(newer_than_days))
        self.batch = max(1, int(batch))
        self.max_usd = float(max_usd)
        self.run_timeout_s = int(run_timeout_s)
        self.http = session or requests.Session()
        self.sleep, self.clock = sleep, clock
        self.spent_usd = 0.0
        self.runs = 0
        self.failures = 0                                   # failed runs in a row
        self.results: Dict[str, List[ApifyPost]] = {}      # served (an empty list: no new posts in the window)
        self.failed: Dict[str, str] = {}                    # not served: use the fallback, and why
        self.disabled: Optional[str] = None                # the whole feed is off for this run, and why

    # -- construction from the environment ------------------------------------------------------------------------
    @classmethod
    def from_env(cls, plan: List[str], **kw) -> Optional["ApifyFeed"]:
        tok = (os.getenv("APIFY_TOKEN") or "").strip()
        mode = (os.getenv("APIFY_MODE") or "auto").strip().lower()
        if mode == "off" or not tok or not plan:
            if not tok and mode != "off":
                log.info("Apify: no APIFY_TOKEN, so Instagram is read the old way (saved login, then public embed)")
            return None
        feed = cls(tok, plan, actor=(os.getenv("APIFY_IG_ACTOR") or DEFAULT_IG_ACTOR).strip(),
                   per_profile=int(_num("APIFY_POSTS_PER_PROFILE", 8)), newer_than_days=int(_num("APIFY_NEWER_THAN_DAYS", 14)),
                   batch=int(_num("APIFY_BATCH", 20)), max_usd=_num("APIFY_MAX_USD_PER_RUN", 1.0),
                   run_timeout_s=int(_num("APIFY_RUN_TIMEOUT_S", 600)), **kw)
        feed.check_plan()
        return feed

    # -- http -----------------------------------------------------------------------------------------------------
    def _call(self, method: str, path: str, body: Optional[dict] = None, timeout: int = 60) -> Tuple[int, object]:
        try:
            r = self.http.request(method, API + path, json=body, headers={"Authorization": "Bearer " + self.token}, timeout=timeout)
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, {"error": (r.text or "")[:200]}
        except requests.RequestException as e:
            return 0, {"error": type(e).__name__}

    def check_plan(self) -> None:
        """Switch the feed off for this run when the token is refused or the month's plan is nearly spent."""
        st, me = self._call("GET", "users/me/limits")
        if st == 401 or st == 403:
            self.disabled = f"Apify refused the token (HTTP {st})"
        elif st == 200 and isinstance(me, dict):
            d = me.get("data") or {}
            used = float((d.get("current") or {}).get("monthlyUsageUsd") or 0)
            cap = float((d.get("limits") or {}).get("maxMonthlyUsageUsd") or 0)
            if cap and used >= 0.9 * cap:
                self.disabled = f"Apify's plan is {used:.2f} of {cap:.2f} USD used this month"
        # any other answer (a blip) is not a reason to switch off: the batch itself will say if Apify is down
        if self.disabled:
            log.warning("Apify is off for this run: %s. Instagram is read the old way.", self.disabled)

    # -- reading --------------------------------------------------------------------------------------------------
    def posts_for(self, handle: str) -> Optional[List[ApifyPost]]:
        """The account's recent posts ([] = none in the window), or None when Apify did not serve it (use the fallback).
        Fetches the batch this account belongs to if it has not been fetched yet."""
        h = (handle or "").lower()
        if self.disabled:
            return None
        if h not in self.results and h not in self.failed:
            self._fetch_batch_for(h)
        return self.results.get(h)

    def why_not(self, handle: str) -> str:
        return self.disabled or self.failed.get((handle or "").lower(), "")

    def _fetch_batch_for(self, h: str) -> None:
        start = self.plan.index(h) if h in self.plan else -1
        pool = self.plan[start:] if start >= 0 else [h]
        todo = [x for x in pool if x not in self.results and x not in self.failed][: self.batch]
        if h not in todo:
            todo = [h] + todo[: self.batch - 1]
        if self.spent_usd >= self.max_usd:
            for x in todo:
                self.failed[x] = f"this run's Apify budget ({self.max_usd:.2f} USD) is spent"
            return
        self._run(todo)

    def _run(self, handles: List[str]) -> None:
        since = (datetime.now(timezone.utc) - timedelta(days=self.newer_than_days)).strftime("%Y-%m-%d")
        body = {"directUrls": [f"https://www.instagram.com/{h}/" for h in handles], "resultsType": "posts",
                "resultsLimit": self.per_profile, "onlyPostsNewerThan": since, "addParentData": False}
        log.info("Apify: reading %d Instagram account(s) (%s ...), up to %d posts each since %s", len(handles), handles[0], self.per_profile, since)
        st, run = self._call("POST", f"acts/{self.actor}/runs?memory=2048&timeout={self.run_timeout_s}", body)
        if st not in (200, 201) or not isinstance(run, dict) or not (run.get("data") or {}).get("id"):
            return self._fail(handles, f"could not start the Apify run (HTTP {st})")
        rid, ds = run["data"]["id"], run["data"].get("defaultDatasetId")
        self.runs += 1
        deadline = self.clock() + self.run_timeout_s + 60
        data: dict = {}
        while self.clock() < deadline:
            self.sleep(5)
            st, r = self._call("GET", f"actor-runs/{rid}")
            data = (r.get("data") if isinstance(r, dict) else None) or {}
            if data.get("status") in TERMINAL:
                break
        self.spent_usd += float(data.get("usageTotalUsd") or 0)
        if data.get("status") != "SUCCEEDED":
            return self._fail(handles, f"the Apify run ended {data.get('status') or 'without finishing'}")
        st, items = self._call("GET", f"datasets/{ds}/items?clean=true&limit=5000", timeout=120)
        if st != 200 or not isinstance(items, list):
            return self._fail(handles, f"could not read the Apify results (HTTP {st})")
        by: Dict[str, List[ApifyPost]] = {h: [] for h in handles}
        seen: set = set()
        for it in items:
            h = handle_of_item(it)
            p = normalise_item(it)
            if p and h in by and p.code not in seen:
                seen.add(p.code)
                by[h].append(p)
        self.failures = 0
        for h, posts in by.items():
            self.results[h] = posts
        log.info("Apify: %d post(s) for %d account(s), run cost %.4f USD, %.4f USD spent so far this sweep",
                 sum(len(v) for v in by.values()), len(handles), float(data.get("usageTotalUsd") or 0), self.spent_usd)

    def _fail(self, handles: List[str], why: str) -> None:
        log.warning("Apify did not serve %d account(s): %s. They are read the old way.", len(handles), why)
        for h in handles:
            self.failed[h] = why
        self.failures += 1
        if self.failures >= 2 and not self.disabled:
            self.disabled = f"Apify failed twice in a row ({why})"
            log.warning("Apify is off for the rest of this run: %s", self.disabled)


def plan_status(token: str, session: Optional[requests.Session] = None) -> Optional[Tuple[str, str]]:
    """(state, detail) for the dashboard's status strip: is Apify, the main Instagram reader, usable right now? None when there is no token.
    state is live | degraded | down, in the strip's own words."""
    if not (token or "").strip():
        return None
    http = session or requests.Session()
    try:
        r = http.request("GET", API + "users/me/limits", headers={"Authorization": "Bearer " + token.strip()}, timeout=30)
        code, data = r.status_code, r.json()
    except (requests.RequestException, ValueError) as e:
        return "degraded", f"Could not reach Apify ({type(e).__name__}); Instagram falls back to the saved login or the public embed."
    if code in (401, 403):
        return "down", f"Apify refused the token (HTTP {code}): put a new APIFY_TOKEN secret in. Instagram falls back to the saved login."
    if code != 200:
        return "degraded", f"Apify answered HTTP {code}; Instagram falls back to the saved login or the public embed."
    d = (data or {}).get("data") or {}
    used = float((d.get("current") or {}).get("monthlyUsageUsd") or 0)
    cap = float((d.get("limits") or {}).get("maxMonthlyUsageUsd") or 0)
    pct = f"{used:.2f} of {cap:.2f} USD used this month" if cap else f"{used:.2f} USD used this month"
    if cap and used >= 0.9 * cap:
        return "degraded", f"Apify's plan is nearly spent ({pct}); Instagram falls back to the saved login or the public embed until it renews."
    return "live", f"Apify is the main Instagram reader, no login of ours needed ({pct}). Playwright is only the fallback."
