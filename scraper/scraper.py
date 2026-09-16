"""
Playwright scraper for public brand profiles on Instagram, Facebook and Threads.

Strategy (deliberately generic so selector churn does not break the run):
  1. open the profile, dismiss cookie / login dialogs, scroll a little;
  2. collect post permalinks by URL pattern (config.yaml → platforms.*.post_link_patterns);
  3. open each permalink, read the caption from Open Graph / meta tags, <h1>, article text and
     image alt text, read the <time datetime> when present, and screenshot the viewport;
  4. return Post objects; every failure is logged per target and never aborts the run.

Login walls: pass PW_STORAGE_STATE_B64 (base64 of a Playwright storage_state.json exported from a
browser logged in to a throwaway account) or PW_STORAGE_STATE_PATH. Without it, Instagram and
Facebook may show fewer posts or only meta text; Threads is usually readable anonymously.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

from dateutil import parser as dateparser
from playwright.sync_api import Browser, BrowserContext, Page, TimeoutError as PWTimeout, sync_playwright

log = logging.getLogger("kkm.scraper")


@dataclass
class Post:
    brand: str
    platform: str
    url: str
    text: str = ""
    posted_at: Optional[str] = None      # ISO date if found
    screenshot_path: Optional[str] = None
    errors: List[str] = field(default_factory=list)


@dataclass
class TargetResult:
    brand: str
    platform: str
    profile_url: str
    posts: List[Post] = field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
DISMISS_SELECTORS = [
    "button:has-text('Decline optional cookies')", "button:has-text('Allow all cookies')",
    "button:has-text('Only allow essential cookies')", "button:has-text('Accept all')",
    "[aria-label='Close']", "div[role='dialog'] button:has-text('Not now')",
    "button:has-text('Not now')", "button:has-text('Not Now')",
]


def canonical_url(u: str) -> str:
    """Same normalisation as the Apps Script side so dedupe agrees across both."""
    u = (u or "").strip()
    u = re.sub(r"^http://", "https://", u, flags=re.I)
    u = re.sub(r"^https://(m|www|web)\.", "https://", u, flags=re.I)
    parts = urlsplit(u)
    keep = [kv for kv in parts.query.split("&") if re.match(r"^(story_fbid|id|v|fbid|set)=", kv, re.I)]
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "&".join(keep), "")).lower()


_TRACKING_PARAMS = re.compile(r"^(__cft__.*|__tn__|mibextid|igsh|igshid|utm_\w+|fbclid|ref|refsrc|rdid|_rdr)$", re.I)


_POST_SUBVIEW = re.compile(r"/(media|liked|reposts|replies|photo|comments)/?$", re.I)


def clean_post_url(u: str) -> str:
    """The URL stored in the sheet and quoted in the complaint: tracking parameters removed,
    identity parameters (story_fbid, id, v, fbid, set) kept, case preserved."""
    u = (u or "").strip()
    parts = urlsplit(u)
    keep = [kv for kv in parts.query.split("&") if kv and not _TRACKING_PARAMS.match(kv.split("=", 1)[0])]
    path = _POST_SUBVIEW.sub("", parts.path)  # /post/ABC/media is the same post as /post/ABC
    return urlunsplit((parts.scheme, parts.netloc, path, "&".join(keep), ""))


def _dismiss_dialogs(page: Page) -> None:
    for sel in DISMISS_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=600):
                loc.click(timeout=1500)
                page.wait_for_timeout(400)
        except Exception:
            pass
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


def _is_login_wall(page: Page) -> bool:
    url = page.url.lower()
    if "/login" in url or "login.php" in url or "/accounts/login" in url or "/r.php" in url:
        return True
    try:
        if not page.locator("input[name='password'], input[type='password']").first.is_visible(timeout=800):
            return False
    except Exception:
        return False
    # A password box can also belong to a dismissible prompt on an otherwise readable page.
    # Treat it as a wall only when no post content is present behind it.
    try:
        return page.locator("article, [role='article'], div[data-pressable-container]").count() == 0
    except Exception:
        return True


def _meta(page: Page, prop: str) -> str:
    for sel in (f"meta[property='{prop}']", f"meta[name='{prop}']"):
        try:
            v = page.locator(sel).first.get_attribute("content", timeout=800)
            if v:
                return v.strip()
        except Exception:
            continue
    return ""


def _clean_caption(s: str) -> str:
    s = (s or "").strip()
    # Instagram og:description: '123 likes, 4 comments - brand on September 1, 2026: "caption"'
    m = re.match(r"^[\d,\.KM\s]*likes?,\s*[\d,\.KM\s]*comments?\s*-\s*[^:]{0,80}:\s*[\"“](.*)[\"”]\s*$", s, re.S)
    if m:
        return m.group(1).strip()
    m = re.match(r"^[^:]{0,80}\son\s(?:Instagram|Threads):\s*[\"“](.*)[\"”]\s*$", s, re.S)
    if m:
        return m.group(1).strip()
    return s


def _extract_text(page: Page, platform: str) -> str:
    chunks: List[str] = []
    for prop in ("og:description", "description", "og:title", "twitter:description"):
        v = _clean_caption(_meta(page, prop))
        if v and v not in chunks:
            chunks.append(v)
    # visible caption blocks
    selectors = {
        "Instagram": ["article h1", "article div[role='button'] span", "article ul li span", "h1"],
        "Facebook": ["div[data-ad-preview='message']", "div[data-ad-comet-preview='message']", "div[dir='auto']", "[role='article'] div[dir='auto']"],
        "Threads": ["div[data-pressable-container] span", "article span", "div[dir='auto']"],
    }.get(platform, ["article", "main"])
    for sel in selectors:
        try:
            texts = page.locator(sel).all_inner_texts()
        except Exception:
            continue
        for t in texts[:40]:
            t = t.strip()
            if len(t) >= 15 and t not in chunks:
                chunks.append(t)
        if sum(len(c) for c in chunks) > 6000:
            break
    # image alt text often carries on-image copy (Instagram auto-alt or brand-set)
    try:
        for alt in page.locator("article img[alt], main img[alt]").evaluate_all("els => els.map(e => e.alt)")[:10]:
            alt = (alt or "").strip()
            if len(alt) > 25 and alt not in chunks and not alt.lower().startswith("photo by"):
                chunks.append("[image alt] " + alt)
    except Exception:
        pass
    text = "\n".join(chunks)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:12000]


def _extract_date(page: Page) -> Optional[str]:
    try:
        dt = page.locator("time[datetime]").first.get_attribute("datetime", timeout=800)
        if dt:
            return dateparser.parse(dt).date().isoformat()
    except Exception:
        pass
    for prop in ("article:published_time", "og:updated_time"):
        v = _meta(page, prop)
        if v:
            try:
                return dateparser.parse(v).date().isoformat()
            except Exception:
                continue
    return None


def _wait_for_posts(page: Page, patterns: List[str], timeout_ms: int = 15000) -> bool:
    """Wait until at least one anchor matching a post pattern is in the DOM. Instagram and
    Threads render the grid well after domcontentloaded, so harvesting too early finds nothing."""
    sel = ", ".join(f'a[href*="{p}"]' for p in patterns if "=" not in p) or "a[href]"
    try:
        page.wait_for_selector(sel, timeout=timeout_ms, state="attached")
        return True
    except PWTimeout:
        return False


def _diagnose_empty(page: Page, out_dir: str, tag: str) -> str:
    """No post links: save the profile page so the cause is visible next time, and return a hint."""
    hint = ""
    try:
        body = (page.locator("body").inner_text(timeout=3000) or "")[:400].replace("\n", " ")
    except Exception:
        body = ""
    low = body.lower()
    if "log in" in low or "sign up" in low:
        hint = "page shows a login prompt"
    elif "sorry, this page isn" in low or "page isn't available" in low:
        hint = "profile not available (handle wrong or region-blocked)"
    elif "something went wrong" in low or "try again later" in low:
        hint = "platform soft-block, try again later or slow the run down"
    elif "private" in low:
        hint = "profile is private to this account"
    try:
        path = os.path.join(out_dir, f"EMPTY_{re.sub(r'[^a-z0-9]+', '_', tag.lower())}.png")
        page.screenshot(path=path, full_page=False, type="png")
        hint = (hint + "; " if hint else "") + f"profile screenshot saved as {os.path.basename(path)}"
    except Exception:
        pass
    return hint


def _owned_by(url: str, platform: str, handle: str) -> bool:
    """
    A brand profile page also links to reposts, quoted posts and commenters' posts. Those are other
    people's content, not the brand's advertising, so they must not become complaints against it.
    """
    if not handle:
        return True
    h = re.escape(handle.strip().lstrip("@"))
    path = urlsplit(url).path.lower()
    if platform == "Threads":
        return bool(re.match(rf"^/@{h}/post/", path, re.I))
    if platform == "Facebook":
        if re.search(r"/(story\.php|permalink\.php)", path, re.I):
            return True  # identity lives in the query string, checked by the profile we came from
        return bool(re.match(rf"^/{h}(/|$)", path, re.I))
    return True  # Instagram grid links carry no handle in the path


def _collect_links(page: Page, patterns: List[str], base: str, limit: int,
                   platform: str = "", handle: str = "") -> List[str]:
    hrefs: List[str] = []
    for _ in range(6):  # scroll to load a few rows
        try:
            found = page.locator("a[href]").evaluate_all("els => els.map(e => e.getAttribute('href'))")
        except Exception:
            found = []
        for h in found:
            if not h:
                continue
            if any(p in h for p in patterns):
                full = urljoin(base, h)
                c = canonical_url(full)
                if c not in [canonical_url(x) for x in hrefs]:
                    hrefs.append(full)
        if len(hrefs) >= limit * 2:
            break
        try:
            page.mouse.wheel(0, 1600)
            page.wait_for_timeout(1500)
        except Exception:
            break
    # de-noise: drop profile-level anchors, other people's posts, and duplicate sub-views
    out: List[str] = []
    seen_clean = set()
    for h in hrefs:
        if re.search(r"/(explore|accounts|login|hashtag|reels/audio|comments?)/", h, re.I):
            continue
        if "facebook.com" in h and re.search(r"/(photos/a\.|groups/)", h):
            continue
        if not _owned_by(h, platform, handle):
            continue
        c = clean_post_url(h)
        key = canonical_url(c)
        if key in seen_clean:
            continue
        seen_clean.add(key)
        out.append(c)
    return out[:limit]


# ---------------------------------------------------------------------------
# main entry
# ---------------------------------------------------------------------------
class Scraper:
    def __init__(self, cfg: dict, out_dir: str):
        self.cfg = cfg
        self.run_cfg = cfg.get("run", {})
        self.platforms: Dict[str, dict] = cfg.get("platforms", {})
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self._storage_state_path = self._materialise_storage_state()

    def _materialise_storage_state(self) -> Optional[str]:
        p = os.getenv("PW_STORAGE_STATE_PATH")
        if p and os.path.exists(p):
            return p
        b64 = os.getenv("PW_STORAGE_STATE_B64")
        if b64:
            try:
                raw = base64.b64decode(b64)
                json.loads(raw)  # validate
                fd, path = tempfile.mkstemp(prefix="pw_state_", suffix=".json")
                with os.fdopen(fd, "wb") as f:
                    f.write(raw)
                return path
            except Exception as e:
                log.warning("PW_STORAGE_STATE_B64 invalid, ignoring: %s", e)
        return None

    def _context(self, browser: Browser) -> BrowserContext:
        kwargs = dict(
            viewport={"width": 1280, "height": 1600},
            device_scale_factor=1,
            locale=self.run_cfg.get("locale", "en-MY"),
            timezone_id=self.run_cfg.get("timezone", "Asia/Kuala_Lumpur"),
            user_agent=self.run_cfg.get("user_agent"),
        )
        if self._storage_state_path:
            kwargs["storage_state"] = self._storage_state_path
        ctx = browser.new_context(**kwargs)
        ctx.set_default_timeout(20000)
        # Block heavy media to keep runs fast; images are still needed for screenshots so only video/fonts.
        ctx.route(re.compile(r".*\.(mp4|webm|m3u8|woff2?)(\?.*)?$"), lambda route: route.abort())
        return ctx

    def run(self, brands: List[dict], platform_filter: Optional[str] = None, brand_filter: Optional[str] = None) -> List[TargetResult]:
        results: List[TargetResult] = []
        with sync_playwright() as pw:
            launch_kwargs = dict(headless=bool(self.run_cfg.get("headless", True)),
                                 args=["--disable-blink-features=AutomationControlled"])
            # Use a system / pre-installed Chromium instead of the Playwright-managed one when asked
            # (VPS with distro Chromium, or a runner whose browser bundle does not match the pip version).
            if os.getenv("PW_CHROMIUM_PATH"):
                launch_kwargs["executable_path"] = os.getenv("PW_CHROMIUM_PATH")
            browser = pw.chromium.launch(**launch_kwargs)
            try:
                for brand in brands:
                    if brand_filter and brand["name"].lower() != brand_filter.lower():
                        continue
                    for platform, handle in (brand.get("handles") or {}).items():
                        if platform_filter and platform.lower() != platform_filter.lower():
                            continue
                        if platform not in self.platforms or not handle:
                            continue
                        results.append(self._scrape_target(browser, brand["name"], platform, str(handle)))
            finally:
                browser.close()
        return results

    def _scrape_target(self, browser: Browser, brand: str, platform: str, handle: str) -> TargetResult:
        pcfg = self.platforms[platform]
        profile_url = pcfg["profile_url"].format(handle=handle)
        res = TargetResult(brand=brand, platform=platform, profile_url=profile_url)
        limit = int(self.run_cfg.get("max_posts_per_profile", 6))
        ctx = self._context(browser)
        page = ctx.new_page()
        t0 = time.time()
        try:
            log.info("[%s/%s] open %s", brand, platform, profile_url)
            page.goto(profile_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            _dismiss_dialogs(page)
            if _is_login_wall(page):
                res.error = "login wall (provide PW_STORAGE_STATE_B64)"
                log.warning("[%s/%s] %s", brand, platform, res.error)
                return res
            page.wait_for_timeout(2500)
            if not _wait_for_posts(page, pcfg["post_link_patterns"]):
                log.info("[%s/%s] no post anchors after wait; scrolling anyway", brand, platform)
            links = _collect_links(page, pcfg["post_link_patterns"], profile_url, limit,
                                   platform=platform, handle=handle)
            log.info("[%s/%s] %d post links", brand, platform, len(links))
            if not links:
                hint = _diagnose_empty(page, self.out_dir, f"{brand}_{platform}")
                res.error = "no post links found" + (f" — {hint}" if hint else " (layout change or restricted profile)")
                log.warning("[%s/%s] %s", brand, platform, res.error)
            for link in links:
                res.posts.append(self._scrape_post(ctx, brand, platform, link))
        except PWTimeout as e:
            res.error = f"timeout: {e}"
            log.error("[%s/%s] %s", brand, platform, res.error)
        except Exception as e:  # keep the run alive
            res.error = f"{type(e).__name__}: {e}"
            log.error("[%s/%s] %s", brand, platform, res.error)
        finally:
            try:
                ctx.close()
            except Exception:
                pass
        log.info("[%s/%s] done in %.1fs", brand, platform, time.time() - t0)
        return res

    def _scrape_post(self, ctx: BrowserContext, brand: str, platform: str, url: str) -> Post:
        post = Post(brand=brand, platform=platform, url=clean_post_url(url))
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            _dismiss_dialogs(page)
            if _is_login_wall(page):
                post.errors.append("login wall on post page")
            post.text = _extract_text(page, platform)
            post.posted_at = _extract_date(page)
            fname = re.sub(r"[^a-z0-9]+", "_", f"{brand}_{platform}_{urlsplit(url).path}".lower()).strip("_")[:90]
            path = os.path.join(self.out_dir, f"{fname}.png")
            try:
                page.screenshot(path=path, full_page=False, type="png")
                post.screenshot_path = path
            except Exception as e:
                post.errors.append(f"screenshot failed: {e}")
            if not post.text:
                post.errors.append("no text extracted")
        except PWTimeout:
            post.errors.append("timeout loading post")
        except Exception as e:
            post.errors.append(f"{type(e).__name__}: {e}")
        finally:
            page.close()
        return post


def date_allowed(posted_at: Optional[str], lookback_days: int = 0, min_post_date: str = "") -> bool:
    """
    Unknown dates pass (we cannot prove they are old). Known dates must satisfy both rules that are on:
    lookback_days (0 = off) and min_post_date (YYYY-MM-DD, "" = off).
    """
    if not posted_at:
        return True
    try:
        d = dateparser.parse(posted_at)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
    except Exception:
        return True
    if lookback_days and d < datetime.now(timezone.utc) - timedelta(days=int(lookback_days)):
        return False
    if min_post_date:
        try:
            m = dateparser.parse(str(min_post_date)).replace(tzinfo=timezone.utc)
            if d < m:
                return False
        except Exception:
            pass
    return True


def within_lookback(posted_at: Optional[str], days: int) -> bool:  # backwards-compatible alias
    return date_allowed(posted_at, days, "")
