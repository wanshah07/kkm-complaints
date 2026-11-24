"""
Instagram review by hashtag (Wan, 24 Nov 2026: Skintific posts from doctors and KOLs, found by hashtag or search).

A person sweep reaches about four accounts in a 100-minute run, because every post of every account has to be opened to
read its caption. Instagram's own hashtag results do the opposite: one page already lists posts about the subject, from
anybody. The probe (tools/ig_hashtag_probe.py, run 45368492881, 24 Nov 2026) showed that the sweep's recorded login
reads them: /explore/tags/<tag>/ lands on /explore/search/keyword/?q=%23<tag>, offers 24 to 48 post links, and each post
page gives its caption and date through the scraper's own reader. Results are not in date order (a 2022 post sat between
two from 2026), so no "four old posts in a row" stop applies here: every link is opened and the date window decides.

What this does, and nothing else: open each tag's result page, collect the post links, skip what the sheet or the ledger
already holds, open the rest with the scraper's own post reader, and hand them to the ordinary pipeline in main.py (date
window, --focus, screenshot, reviewer, sheet, ledger), one TargetResult per owning account so the Brand column names the
account. A post by an official brand account of the hashtag's subject is never reviewed here (Wan: not from the official
account). The account's handle is read from the caption line ("... - handle on <date>:"), because the page's og:title
often carries only a display name.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from playwright.sync_api import sync_playwright

from scraper import (Post, Scraper, TargetResult, _collect_links, _dismiss_dialogs, _is_login_wall, _pace,
                     canonical_url, clean_post_url, env_str)

log = logging.getLogger("kkm.hashtag")

SEARCH_URL = "https://www.instagram.com/explore/tags/{tag}/"
PATTERNS = ["/p/", "/reel/"]
_HANDLE = r"[A-Za-z0-9_.]{2,30}"
_DATE = r"[A-Z][a-z]+ \d{1,2}, \d{4}"
# '243 likes, 62 comments - alena.m.ugc on April 28, 2026: "..."'  or  'syhznr on December 6, 2025: "..."'
_CAPTION_OWNER = re.compile(rf"(?:^|\s-\s)({_HANDLE}) on {_DATE}", re.U)


def clean_tags(raw) -> List[str]:
    """Tags as typed ('#Skintific', 'skintificmy,skintificmalaysia') -> unique lower-case words, no #."""
    if isinstance(raw, (list, tuple)):
        raw = " ".join(raw)
    out: List[str] = []
    for tok in re.split(r"[\s,]+", str(raw or "")):
        tok = tok.strip().lstrip("#").lower()
        if re.fullmatch(r"[\w.]{2,60}", tok, re.U) and tok not in out:
            out.append(tok)
    return out


def owner_handle(text: str, extracted: Optional[str] = None) -> str:
    """The posting account's handle: from the caption line when it carries one, else what the page gave."""
    m = _CAPTION_OWNER.search((text or "")[:300])
    if m:
        return m.group(1).lower()
    return (extracted or "").strip().lstrip("@").lower()


def is_official(owner: str, words: List[str]) -> bool:
    """An official account of the subject: its handle carries one of the words (skintific.my, skintificofficial)."""
    o = re.sub(r"[^a-z0-9]", "", (owner or "").lower())
    return bool(o) and any(w and w in o for w in words)


class InstagramHashtag(Scraper):
    """Reads the posts Instagram lists under each hashtag and yields one TargetResult per owning account."""

    def __init__(self, cfg: dict, out_dir: str, tags: List[str], exclude_words: Optional[List[str]] = None,
                 max_posts: int = 40):
        super().__init__(cfg, out_dir)
        self.tags = tags
        self.max_posts = max(1, int(max_posts))
        # Official accounts are the ones whose handle carries the hashtag's own brand word.
        self.exclude_words = [w for w in (exclude_words or []) if w]
        self.report_tags: Dict[str, dict] = {}

    def plan(self, brands=None, platform_filter=None, brand_filter=None) -> List[Tuple[str, str, str]]:
        return [(f"#{t}", "Instagram", t) for t in self.tags]

    def _links_for(self, ctx, tag: str) -> Tuple[List[str], Optional[str]]:
        """(post links, error). A signed-out session or a login wall is an error, never an empty list."""
        page = ctx.new_page()
        try:
            url = SEARCH_URL.format(tag=tag)
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)
            _dismiss_dialogs(page)
            lost = self._session_signed_out(ctx, "Instagram")
            if lost:
                self.session_lost["Instagram"] = lost
                return [], lost
            if _is_login_wall(page):
                return [], "login wall on the hashtag page (record the Instagram session again)"
            links = _collect_links(page, PATTERNS, url, max(self.max_posts * 2, 24), platform="Instagram", handle="",
                                   scroll_pause=self.run_cfg.get("pause_after_scroll"))
            return links, None
        finally:
            page.close()

    def iter_run(self, brands=None, platform_filter=None, brand_filter=None,
                 should_stop: Optional[Callable[[], bool]] = None) -> Iterator[TargetResult]:
        self.should_stop = should_stop
        posts: List[Post] = []
        with sync_playwright() as pw:
            launch = dict(headless=bool(self.run_cfg.get("headless", True)))
            path = env_str("PW_CHROMIUM_PATH")
            if path and os.path.exists(path):
                launch["executable_path"] = path
            browser = pw.chromium.launch(**launch)
            try:
                ctx = self._context(browser)
                seen_codes = set()
                for tag in self.tags:
                    if should_stop and should_stop():
                        log.warning("run budget reached before #%s", tag)
                        break
                    t0 = time.time()
                    links, err = self._links_for(ctx, tag)
                    info = {"links": len(links), "opened": 0, "known": 0, "official": 0, "error": err}
                    self.report_tags[tag] = info
                    log.info("[#%s/Instagram] %d post link(s)%s", tag, len(links), f" - {err}" if err else "")
                    if err:
                        yield TargetResult(brand=f"#{tag}", platform="Instagram",
                                           profile_url=SEARCH_URL.format(tag=tag), error=err, truncated=True)
                        if "Instagram" in self.session_lost:
                            break
                        continue
                    opened = 0
                    for link in links:
                        url = clean_post_url(link)
                        key = canonical_url(url)
                        if key in seen_codes:
                            continue
                        seen_codes.add(key)
                        if key in self.known_urls:
                            info["known"] += 1
                            continue
                        if should_stop and should_stop():
                            log.warning("[#%s/Instagram] run budget reached after %d post(s) opened", tag, opened)
                            break
                        if opened >= self.max_posts:
                            log.info("[#%s/Instagram] %d new post(s) opened (the cap); the rest wait for the next run",
                                     tag, opened)
                            break
                        if opened:
                            _pace(self.run_cfg.get("pause_between_posts"), "the next post")
                        p = self._scrape_post(ctx, f"#{tag}", "Instagram", link, handle="")
                        opened += 1
                        info["opened"] = opened
                        owner = owner_handle(p.text, p.owner)
                        p.owner = owner or None
                        if owner and is_official(owner, self.exclude_words):
                            p.not_owned = True
                            p.errors.append(f"official account (@{owner}): not wanted here, this run reads other "
                                            "people's posts - not reviewed")
                            info["official"] += 1
                        posts.append(p)
                    log.info("[#%s/Instagram] done in %.1fs", tag, time.time() - t0)
                ctx.close()
            finally:
                browser.close()
        # One result per owning account, so the sheet's Brand column carries the account.
        by: Dict[str, List[Post]] = {}
        for p in posts:
            owner = p.owner or "unknown_instagram_account"
            p.brand = owner
            by.setdefault(owner, []).append(p)
        for owner, ps in by.items():
            tr = TargetResult(brand=owner, platform="Instagram", profile_url=f"https://www.instagram.com/{owner}/",
                              posts=ps)
            if all(x.blocked for x in ps):
                tr.error = "login wall on every post (Instagram session expired?)"
            yield tr
