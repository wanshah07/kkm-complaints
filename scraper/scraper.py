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
import gzip
import json
import random
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit, urlunsplit

from dateutil import parser as dateparser
from playwright.sync_api import Browser, BrowserContext, Page, TimeoutError as PWTimeout, sync_playwright

log = logging.getLogger("kkm.scraper")


def env_str(name: str, default: str = "") -> str:
    """
    Read an environment variable, tolerating a .env line like
        PW_CHROMIUM_PATH=            # optional: path to a system binary
    Some parsers hand back the comment as the value, which then fails far away from
    the cause. A value that is blank, or begins with #, means "not set".
    """
    v = (os.getenv(name) or "").strip()
    if v.startswith("#"):
        return default
    v = re.split(r"\s+#", v, 1)[0].strip()
    return v or default


@dataclass
class Post:
    brand: str
    platform: str
    url: str
    text: str = ""
    posted_at: Optional[str] = None      # ISO date if found
    screenshot_path: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    not_owned: bool = False  # confirmed to be a different account's post; never sent for review
    blocked: bool = False    # the platform served a login wall, not the post; never sent for review


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
    u = re.sub(r"^https://(mbasic|touch|m|www|web)\.", "https://", u, flags=re.I)
    parts = urlsplit(u)
    keep = [kv for kv in parts.query.split("&") if re.match(r"^(story_fbid|id|v|fbid|set)=", kv, re.I)]
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "&".join(keep), "")).lower()


_TRACKING_PARAMS = re.compile(
    r"^(__cft__.*|__tn__|mibextid|igsh|igshid|utm_\w+|fbclid|ref|refid|refsrc|rdid|_rdr|eav|paipv|_ft_|sfnsn|extid|hc_\w+)$",
    re.I)


_POST_SUBVIEW = re.compile(r"/(media|liked|reposts|replies|photo|comments)/?$", re.I)
_MIRROR_HOST = re.compile(r"^(mbasic|m|touch)\.facebook\.com$", re.I)


def clean_post_url(u: str) -> str:
    """The URL stored in the sheet and quoted in the complaint: tracking parameters removed,
    identity parameters (story_fbid, id, v, fbid, set) kept, case preserved."""
    u = (u or "").strip()
    parts = urlsplit(u)
    keep = [kv for kv in parts.query.split("&") if kv and not _TRACKING_PARAMS.match(kv.split("=", 1)[0])]
    path = _POST_SUBVIEW.sub("", parts.path)  # /post/ABC/media is the same post as /post/ABC
    host = "www.facebook.com" if _MIRROR_HOST.match(parts.netloc) else parts.netloc
    return urlunsplit((parts.scheme, host, path, "&".join(keep), ""))


def _pace(cfg_pair, what: str = "") -> float:
    """
    Sleep a random time inside [min, max] seconds. Randomised rather than fixed: a constant
    interval is itself a bot signature. Returns the seconds waited (0 when disabled).
    """
    try:
        lo, hi = float(cfg_pair[0]), float(cfg_pair[1])
    except (TypeError, ValueError, IndexError):
        return 0.0
    if hi <= 0:
        return 0.0
    if lo > hi:
        lo, hi = hi, lo
    secs = random.uniform(max(lo, 0.0), hi)
    if secs <= 0:
        return 0.0
    if secs >= 5 and what:
        log.info("pacing: waiting %.0fs before %s", secs, what)
    time.sleep(secs)
    return secs


_OWNER_AT = re.compile(r"\(@([a-z0-9_.]+)\)", re.I)
_OWNER_PREFIX = re.compile(
    r"^\s*(?:[\d,.]+\s*(?:likes?|views?),?\s*(?:[\d,.]+\s*comments?)?\s*-\s*)?"
    r"([^(:]{1,60}?)\s+on\s+(?:Instagram|Threads)\b", re.I)


_IG_PATH_OWNER = re.compile(r"^/([a-z0-9_.]+)/(?:p|reel|tv)/", re.I)


def _owner_from_url(url: str, platform: str) -> Optional[str]:
    """Instagram serves a post under its own account's path — /<handle>/p/<id>/ — whenever the link
    came off a grid, so a link to another account's post names that account."""
    if platform != "Instagram":
        return None
    m = _IG_PATH_OWNER.match(urlsplit(url).path)
    return m.group(1).lower() if m else None


def _extract_owner(page: Page, platform: str) -> Tuple[Optional[str], bool]:
    """
    Read the post's author off the page. Returns (owner, is_handle).

    A bare /p/<id>/ Instagram URL carries no handle, and a profile grid also surfaces "Suggested
    for you" content, so the author has to come from the page: og:title usually reads
    '1,234 Likes, 56 Comments - Brand (@handle) on Instagram: "caption"'.

    The @handle is the account's real name and can be compared with the Targets cell. Some posts
    carry only the DISPLAY name — 'Eucerin Malaysia on Instagram: …' — which is not a handle at all
    (@eucerin_my displays as "Eucerin Malaysia"), so it comes back with is_handle False and must
    never be used to reject a post.
    """
    if platform not in ("Instagram", "Threads"):
        return None, False
    for prop in ("og:title", "twitter:title", "og:description"):
        v = _meta(page, prop) or ""
        m = _OWNER_AT.search(v)
        if m:
            return m.group(1).lower(), True
        m = _OWNER_PREFIX.search(v)
        if m:
            return re.sub(r"[^a-z0-9_.]", "", m.group(1).lower()), False
    return None, False


def _handles_match(a: Optional[str], b: Optional[str]) -> bool:
    norm = lambda s: re.sub(r"[._]", "", (s or "").lower())
    return bool(a and b) and norm(a) == norm(b)


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


_WALL_URL_PARTS = ("/login", "login.php", "/accounts/login", "/r.php", "/checkpoint")

# What a platform puts on the page instead of the post when it wants a session. Facebook's
# newer gate carries no password box at all — it offers "Continue as <name>" over a
# "Create new account" link — so a password field alone does not recognise a wall, and a
# wall that goes unrecognised is screenshotted and reviewed as if it were the post.
_WALL_MARKERS = (
    "log in to facebook",
    "log into facebook",
    "explore the things you love",
    "use another profile",
    "create new account",
    "log in to see photos",
    "sign up to see photos",
    "log in or sign up to view",
    "see more on facebook",
    "log in to continue",
)

# A gate page carries almost nothing. A profile or post page carries captions, comments and
# chrome, and can mention "Create new account" in its own footer without being a wall, so the
# wording above only decides a page that has little else on it.
_WALL_MAX_TEXT = 1500


# The post itself, rather than whatever the viewport happens to hold. A post page also renders
# the thread around it — reposts, replies, suggested content — and that changes between loads,
# so a viewport shot feeds the reviewer a different picture each run and the verdict moves with
# it. Run 35235876472 read a neighbouring perfume promotion off a Threads page and called the
# post Risky; an hour later the same URL came back Acceptable.
# Ordered narrowest first. The last entry on each platform is a wide container rather than the
# post itself: still far better than the viewport, because it excludes the site header, the
# sidebar and the "suggested for you" rail, and the run log names which one matched so a
# selector that has gone stale is visible rather than silent.
_POST_ELEMENT_SELECTORS = {
    "Instagram": ("article", "main article", "[role='dialog'] article",
                  "main[role='main']", "main"),
    "Facebook": ("[role='article']", "div[data-ad-preview='message']", "[role='main']"),
    "Threads": ("div[data-pressable-container='true']", "[role='article']", "article",
                "[role='main']"),
}
# Instagram laid its post out after the old 800ms probe had already given up, so every post
# fell back to the viewport. The probe now waits about as long as a slow render takes; the
# cost is bounded by the pacing wait that follows each post anyway.
_ELEMENT_PROBE_MS = 2500
# Element shots are uncapped by the viewport, and a long thread makes an image the reviewer is
# charged for by the pixel. Past this height the shot is clipped from the top of the post.
_MAX_SHOT_PX = 2400


def _post_element(page: Page, platform: str):
    """The locator and box of the post's own container, or (None, None) to fall back."""
    selectors = _POST_ELEMENT_SELECTORS.get(platform, ())
    for i, sel in enumerate(selectors):
        try:
            loc = page.locator(sel).first
            # Only the first selector is worth waiting on; by the time it has had its 2.5s the
            # page has rendered, and the rest are a question about the DOM, not about timing.
            if not loc.is_visible(timeout=_ELEMENT_PROBE_MS if i == 0 else 500):
                continue
            box = loc.bounding_box()
            # Guard against a wrapper that collapsed to nothing, or a stray inline element.
            if not box or box["width"] < 200 or box["height"] < 120:
                continue
            return loc, box, sel
        except Exception:
            continue
    return None, None, ""


def _save_screenshot(page: Page, post: "Post", brand: str, platform: str, url: str, out_dir: str) -> None:
    fname = re.sub(r"[^a-z0-9]+", "_", f"{brand}_{platform}_{urlsplit(url).path}".lower()).strip("_")[:90]
    path = os.path.join(out_dir, f"{fname}.png")
    loc, box, sel = _post_element(page, platform)
    try:
        if loc is not None and box["height"] <= _MAX_SHOT_PX:
            loc.screenshot(path=path, type="png")
            log.info("[%s/%s] screenshot: post element %s (%.0fx%.0f)",
                     brand, platform, sel, box["width"], box["height"])
        elif loc is not None:
            page.screenshot(path=path, type="png",
                            clip={"x": box["x"], "y": box["y"],
                                  "width": box["width"], "height": float(_MAX_SHOT_PX)})
            log.info("[%s/%s] screenshot: post element %s clipped to %dpx of %.0f",
                     brand, platform, sel, _MAX_SHOT_PX, box["height"])
        else:
            page.screenshot(path=path, full_page=False, type="png")
            log.info("[%s/%s] screenshot: viewport (no post element matched)", brand, platform)
        post.screenshot_path = path
    except Exception as e:
        # An element shot can still fail on a detached node; the viewport is better than nothing.
        try:
            page.screenshot(path=path, full_page=False, type="png")
            post.screenshot_path = path
            log.info("[%s/%s] screenshot: viewport after element shot failed (%s)", brand, platform, e)
        except Exception as e2:
            post.errors.append(f"screenshot failed: {e2}")


def _has_post_content(page: Page) -> bool:
    try:
        return page.locator("article, [role='article'], div[data-pressable-container]").count() > 0
    except Exception:
        return False


def _is_login_wall(page: Page) -> bool:
    url = page.url.lower()
    if any(part in url for part in _WALL_URL_PARTS):
        return True
    # Everything below is a wall only when the post is not on the page behind it: a password
    # box, and a "create new account" link, can equally belong to a dismissible prompt
    # floating over a post that reads perfectly well.
    if _has_post_content(page):
        return False
    try:
        if page.locator("input[name='password'], input[type='password']").first.is_visible(timeout=800):
            return True
    except Exception:
        pass
    try:
        body = (page.locator("body").inner_text(timeout=1500) or "").strip()
    except Exception:
        return False
    if len(body) > _WALL_MAX_TEXT:
        return False
    low = body.lower()
    return any(marker in low for marker in _WALL_MARKERS)


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
                   platform: str = "", handle: str = "", scroll_pause=None) -> List[str]:
    scroll_pause = scroll_pause or [1.5, 3.5]
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
            page.mouse.wheel(0, random.randint(1200, 2000))
            page.wait_for_timeout(int(_pace(scroll_pause) * 1000) or 1500)
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

    @staticmethod
    def _report_session_age(raw: bytes) -> None:
        """
        A recorded session dies quietly: the run still works, every profile just hits a wall and
        reports 0 posts, which reads like the brands went quiet. Say it plainly up front instead.
        """
        try:
            state = json.loads(raw)
        except Exception:
            return
        now = time.time()
        live, expired, per_site = 0, 0, {}
        for c in state.get("cookies", []) or []:
            exp = c.get("expires")
            domain = str(c.get("domain", "")).lstrip(".").replace("www.", "")
            site = next((s for s in ("facebook.com", "instagram.com", "threads.net") if s in domain), None)
            # -1 is a session cookie: it has no expiry to check, so it is not evidence either way.
            if exp is None or exp <= 0:
                continue
            if exp > now:
                live += 1
                if site:
                    per_site[site] = max(per_site.get(site, 0.0), float(exp))
            else:
                expired += 1
        if not live and expired:
            log.warning("browser session has expired (%d cookies, none still valid) - "
                        "expect login walls on every platform; re-record it (GUIDE section 4)", expired)
            return
        for site, exp in sorted(per_site.items()):
            days = (exp - now) / 86400.0
            if days < 7:
                log.warning("browser session for %s expires in %.1f day(s) - re-record it soon", site, days)
            else:
                log.info("browser session for %s valid for %.0f more day(s)", site, days)
        missing = [s for s in ("facebook.com", "instagram.com", "threads.net") if s not in per_site]
        if missing:
            log.warning("browser session carries no dated cookie for %s - that platform was "
                        "probably not logged in when the session was recorded", ", ".join(missing))

    def _materialise_storage_state(self) -> Optional[str]:
        # 1. an explicit path, 2. storage_state.json sitting next to this file (the Windows
        # case, so no .env line is needed), 3. base64 in the environment (the CI case).
        p = env_str("PW_STORAGE_STATE_PATH")
        if p and os.path.exists(p):
            log.info("using browser session from %s", p)
            self._report_session_age(open(p, "rb").read())
            return p
        here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage_state.json")
        if os.path.exists(here):
            log.info("using browser session from %s", here)
            self._report_session_age(open(here, "rb").read())
            return here
        b64 = env_str("PW_STORAGE_STATE_B64")
        if b64:
            try:
                raw = base64.b64decode(b64)
                # A GitHub secret stops at 48 KB and a recorded session can exceed that once
                # Facebook is in it, so a gzipped state is accepted too (about 10x smaller).
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                json.loads(raw)  # validate
                self._report_session_age(raw)
                fd, path = tempfile.mkstemp(prefix="pw_state_", suffix=".json")
                with os.fdopen(fd, "wb") as f:
                    f.write(raw)
                log.info("using browser session from PW_STORAGE_STATE_B64")
                return path
            except Exception as e:
                log.warning("PW_STORAGE_STATE_B64 invalid, ignoring: %s", e)
        log.info("no browser session found; logged-out browsing (expect login walls)")
        return None

    def _context(self, browser: Browser, use_storage: bool = True) -> BrowserContext:
        kwargs = dict(
            viewport={"width": 1280, "height": 1600},
            device_scale_factor=1,
            locale=self.run_cfg.get("locale", "en-MY"),
            timezone_id=self.run_cfg.get("timezone", "Asia/Kuala_Lumpur"),
            user_agent=self.run_cfg.get("user_agent"),
        )
        if use_storage and self._storage_state_path:
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
            chromium_path = env_str("PW_CHROMIUM_PATH")
            if chromium_path and os.path.exists(chromium_path):
                launch_kwargs["executable_path"] = chromium_path
            elif chromium_path:
                log.warning("PW_CHROMIUM_PATH points at %r which does not exist; using the bundled browser",
                            chromium_path)
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
                        if results:  # no wait before the very first profile
                            _pace(self.run_cfg.get("pause_between_profiles"), f"{brand['name']} on {platform}")
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
                                   platform=platform, handle=handle,
                                   scroll_pause=self.run_cfg.get("pause_after_scroll"))
            log.info("[%s/%s] %d post links", brand, platform, len(links))

            # Some platforms only render their feed with JavaScript. Where a plain-HTML mirror
            # exists (Facebook's mbasic), try it before giving up.
            for alt_tpl in (pcfg.get("profile_url_fallbacks") or []):
                if links:
                    break
                alt = alt_tpl.format(handle=handle)
                log.info("[%s/%s] retrying via %s", brand, platform, alt)
                try:
                    page.goto(alt, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(2000)
                    _dismiss_dialogs(page)
                    if _is_login_wall(page):
                        log.info("[%s/%s] fallback hit a login wall", brand, platform)
                        continue
                    links = _collect_links(page, pcfg["post_link_patterns"], alt, limit,
                                           platform=platform, handle=handle,
                                           scroll_pause=self.run_cfg.get("pause_after_scroll"))
                    log.info("[%s/%s] %d post links via fallback", brand, platform, len(links))
                except PWTimeout:
                    log.info("[%s/%s] fallback timed out", brand, platform)
                except Exception as e:
                    log.info("[%s/%s] fallback failed: %s", brand, platform, e)

            # A stale or challenged session is worse than no session at all: Facebook serves some
            # public pages to anonymous visitors while blocking a login it distrusts. Before giving
            # up, try the profile once more with no cookies.
            if not links and self._storage_state_path:
                log.info("[%s/%s] retrying signed out, with no stored session", brand, platform)
                clean = self._context(browser, use_storage=False)
                clean_page = None
                try:
                    clean_page = clean.new_page()
                    clean_page.goto(profile_url, wait_until="domcontentloaded", timeout=45000)
                    clean_page.wait_for_timeout(2500)
                    _dismiss_dialogs(clean_page)
                    links = _collect_links(clean_page, pcfg["post_link_patterns"], profile_url, limit,
                                           platform=platform, handle=handle,
                                           scroll_pause=self.run_cfg.get("pause_after_scroll"))
                    log.info("[%s/%s] %d post links signed out", brand, platform, len(links))
                except Exception as e:
                    log.info("[%s/%s] signed-out retry failed: %s", brand, platform, e)
                if links:
                    # Keep browsing in the context that actually worked, posts included.
                    try:
                        ctx.close()
                    except Exception:
                        pass
                    ctx, page = clean, clean_page
                else:
                    try:
                        clean.close()
                    except Exception:
                        pass

            if not links:
                hint = _diagnose_empty(page, self.out_dir, f"{brand}_{platform}")
                res.error = "no post links found" + (f" — {hint}" if hint else " (layout change or restricted profile)")
                log.warning("[%s/%s] %s", brand, platform, res.error)
            for i, link in enumerate(links):
                if i:  # no wait before the first post of a profile
                    _pace(self.run_cfg.get("pause_between_posts"), "the next post")
                res.posts.append(self._scrape_post(ctx, brand, platform, link, handle=handle))
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

    def _scrape_post(self, ctx: BrowserContext, brand: str, platform: str, url: str, handle: str = "") -> Post:
        post = Post(brand=brand, platform=platform, url=clean_post_url(url))
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            _dismiss_dialogs(page)
            if _is_login_wall(page):
                # The screenshot here is the gate, not the post. Reviewing it asks the model to
                # judge a page with no cosmetic claim on it, and the honest answer to that is
                # "Acceptable" — a clean verdict on a post nobody ever saw. Keep the image for
                # diagnosis, mark the post, and let main.py skip it.
                post.errors.append("login wall on post page - not reviewed")
                post.blocked = True
                _save_screenshot(page, post, brand, platform, url, self.out_dir)
                return post
            if platform == "Instagram" and handle:
                # Only a real handle rejects a post: from the URL path, or an @handle in the page's
                # own metadata. A display name ("Eucerin Malaysia" for @eucerin_my) is not a handle
                # and rejecting on it throws away the brand's own posts.
                owner = _owner_from_url(url, platform)
                is_handle = owner is not None
                if owner is None:
                    owner, is_handle = _extract_owner(page, platform)
                if owner and not _handles_match(owner, handle):
                    if is_handle:
                        post.errors.append(f"different account: post is by @{owner}, not @{handle} — not reviewed")
                        post.not_owned = True
                        return post
                    log.info("[%s/%s] page shows the display name %r, not a handle; reviewing anyway",
                             brand, platform, owner)
                elif not owner:
                    log.info("[%s/%s] could not read the post owner off the page; reviewing anyway", brand, platform)
            post.text = _extract_text(page, platform)
            post.posted_at = _extract_date(page)
            _save_screenshot(page, post, brand, platform, url, self.out_dir)
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
