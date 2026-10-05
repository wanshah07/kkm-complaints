"""
TikTok review by video link (Wan, 5 Oct 2026).

A TikTok account's post LIST is not readable by a runner: measured the same day, the page's own
/api/post/item_list answers an automated session with an empty 200, and search returns the same
unrelated videos whatever is asked. That is TikTok's own bot protection and nothing here tries to get
round it. A single VIDEO page does load, so the posts to review are named by link and read one at a
time, the way a person would open them.

Per link it reads the caption (the page's own `video-desc`), the owner (the @handle in the page
description), and the date (decoded from the video id: its top 32 bits are unix seconds). Everything
after that is the ordinary pipeline in main.py: screenshot, reviewer, sheet, ledger.

Links come from the environment (TIKTOK_LINKS), one per line or space separated. A stock Chromium is
used: no stealth flags, no spoofed headers.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Callable, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from scraper import Post, Scraper, TargetResult, _pace, canonical_url, clean_post_url, env_str

log = logging.getLogger("kkm.tiktok")

_VIDEO_PATH = re.compile(r"^/@[^/]+/(?:video|photo)/\d{10,}", re.I)
_SHORT_HOSTS = ("vm.tiktok.com", "vt.tiktok.com")


def parse_links(raw: str) -> Tuple[List[str], List[Tuple[str, str]]]:
    """(links to open, [(text, why rejected)]). A link is accepted when it is a TikTok video / photo
    page or a TikTok short link (those redirect to the video). Duplicates collapse on the canonical form."""
    ok: List[str] = []
    bad: List[Tuple[str, str]] = []
    seen = set()
    for tok in re.split(r"[\s,]+", (raw or "").strip()):
        if not tok:
            continue
        if not re.match(r"^https?://", tok, re.I):
            bad.append((tok[:80], "not a link"))
            continue
        parts = urlsplit(tok)
        host = parts.netloc.lower().removeprefix("www.")
        if host in _SHORT_HOSTS or (host == "tiktok.com" and parts.path.startswith("/t/")):
            key = tok.split("?")[0].rstrip("/").lower()
        elif host == "tiktok.com" and _VIDEO_PATH.match(parts.path):
            key = canonical_url(clean_post_url(tok))
        else:
            bad.append((tok[:80], "not a TikTok video link"))
            continue
        if key in seen:
            continue
        seen.add(key)
        ok.append(tok)
    return ok, bad


class TikTokLinks(Scraper):
    """Reads the named TikTok videos and yields one TargetResult per owning account."""

    def __init__(self, cfg: dict, out_dir: str, links: List[str]):
        super().__init__(cfg, out_dir, tiktok_session=True)
        self.links = links

    def plan(self, brands=None, platform_filter=None, brand_filter=None) -> List[Tuple[str, str, str]]:
        return [("TikTok link", "TikTok", u) for u in self.links]

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
                for i, link in enumerate(self.links):
                    if should_stop and should_stop():
                        log.warning("run budget reached after %d of %d link(s)", i, len(self.links))
                        break
                    url = clean_post_url(link) if "/video/" in link or "/photo/" in link else link
                    if canonical_url(url) in self.known_urls:
                        posts.append(Post(brand="", platform="TikTok", url=url, known=True))
                        continue
                    if i:
                        _pace(self.run_cfg.get("pause_between_posts"), "the next TikTok link")
                    p = self._scrape_post(ctx, "tiktok_link", "TikTok", link, handle="")
                    posts.append(p)
                ctx.close()
            finally:
                browser.close()
        # One result per owning account, so the sheet's Brand column carries the account.
        by: Dict[str, List[Post]] = {}
        for p in posts:
            owner = p.owner or "unknown_tiktok_account"
            p.brand = owner
            by.setdefault(owner, []).append(p)
        for owner, ps in by.items():
            tr = TargetResult(brand=owner, platform="TikTok", profile_url=f"https://www.tiktok.com/@{owner}", posts=ps)
            if all(x.blocked for x in ps):
                tr.error = "login wall / verification on every link (TikTok session expired?)"
            yield tr
