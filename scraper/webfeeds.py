"""
Website collector: pharmacy chains, online retailers and brand websites (Targets tab rows with a
Type and a Website feed / Product feed instead of a social handle; Wan, 4 Oct 2026).

It yields the SAME TargetResult / Post objects the social Scraper does, so the review, grounding,
ledger and filing code in main.py runs over a product page exactly as it runs over a caption. A
product page is the "post"; its URL is the dedupe key; its listing copy is the text.

Measured from a GitHub runner on 4 Oct 2026 (tools/web_probe.py, run 37196229407):
  Shopify  /search/suggest.json            Caring, BIG, Alpro, AA     200 JSON, body = description HTML
  WooCommerce  /wp-json/wc/store/v1        DoctorOnCall               200 JSON, description may be empty
  Magento GraphQL                          Guardian                   200 JSON (the catalogsearch HTML page is an empty JS shell)
  OpenCart HTML search                     Health Lane                200 HTML, product links in the page
  Brand homepages                          Eucerin, Cetaphil, SAFI, SimplySiti, Nuuha   200 HTML
  Watsons                                  Akamai "Access Denied" on plain HTTP AND on headless Chromium (403);
                                           a HEADED Chromium under Xvfb gets 200 and the page's own JSON API
                                           answers (tools/watsons_probe.py, 5 Oct 2026, runs 37249483780 and
                                           37251088889..): see _WatsonsBrowser below
  Shopee                                   login wall, not attempted (Wan: after the sweep, one login attempt)

A wall is a gap, never a pass: a host that answers 403/429/captcha is recorded as an error on that
target and the rest of that host's rows are skipped for the run (one note, not forty).
"""
import csv
import html as _html
import io
import json
import logging
import os
import re
import subprocess
import time
import unicodedata
from typing import Callable, Dict, Iterator, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urljoin, urlsplit, urlunsplit

import requests

from scraper import Post, TargetResult, canonical_url

log = logging.getLogger("kkm.web")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "application/json,text/html,*/*",
           "Accept-Language": "en-MY,en;q=0.8,ms;q=0.6"}
WEB_TYPE_LABEL = "Website listing"      # ReviewInput.target_type for every post from here
MAX_TEXT = 6000
WALL_STATUS = (401, 403, 429, 503)
WALL_WORDS = re.compile(r"captcha|verify you are human|access denied|unusual traffic|attention required|"
                        r"just a moment|enable javascript and cookies", re.I)
SKIP_TYPES = {
    # The parent rows of the retailer groups: their feed is a homepage, and the per-brand rows
    # beneath carry the actual searches.
    "retail chain": "parent row of the retailer groups; the per-brand rows are scanned instead",
    # Login-walled for an anonymous visitor (shop_probe, 1 Oct 2026). Wan will log in once after
    # the sweep, one attempt per site; nothing here tries.
    "shopee store": "Shopee is login-walled; not attempted until Wan logs in",
}


# --- small helpers ------------------------------------------------------------------------------

def _tokens(s: str) -> List[str]:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", s.lower())


def brand_matches(brand: str, *fields: str) -> bool:
    """The brand's words, in order and whole, appear in the title / vendor. A bare substring would
    let 'QV' match 'cinnamon QVC' and 'SAFI' match 'safing'; tokens do not."""
    hay = _tokens(" ".join(f for f in fields if f))
    for alt in re.split(r"\s*/\s*", brand or ""):
        want = _tokens(alt)
        if not want:
            continue
        n = len(want)
        if any(hay[i:i + n] == want for i in range(len(hay) - n + 1)):
            return True
    return False


_DROP_BLOCKS = re.compile(r"<(script|style|svg|noscript|template|head)\b.*?</\1\s*>", re.I | re.S)
_BREAKS = re.compile(r"</?(p|br|li|ul|ol|div|tr|table|h[1-6]|section|article|header|footer|blockquote)\b[^>]*>", re.I)


def html_to_text(s: str, cap: int = MAX_TEXT) -> str:
    s = _DROP_BLOCKS.sub(" ", s or "")
    s = _BREAKS.sub("\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.split("\n")]
    out: List[str] = []
    for ln in lines:
        if ln and (not out or ln != out[-1]):
            out.append(ln)
    return "\n".join(out)[:cap]


def _clean_url(u: str) -> str:
    p = urlsplit(u)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


# --- the rows -----------------------------------------------------------------------------------

def rows_from_csv(text: str) -> List[dict]:
    """Targets tab as CSV (Google's gviz export) -> website rows. A row is a website row when it
    has a Type and a feed and no social handle, the same test main.py uses to set it aside."""
    out: List[dict] = []
    for r in csv.DictReader(io.StringIO(text)):
        name = (r.get("Brand") or "").strip()
        typ = (r.get("Type") or "").strip()
        if not name or not typ:
            continue
        if any((r.get(c) or "").strip() for c in ("Instagram", "Facebook", "Threads")):
            continue
        out.append({"name": name, "type": typ,
                    "active": (r.get("Active") or "").strip().lower() in ("true", "yes", "1"),
                    "website_feed": (r.get("Website feed") or "").strip(),
                    "product_feed": (r.get("Product feed") or "").strip()})
    return out


def load_web_rows(web_rows: List[dict], sheet_id: str = "") -> Tuple[List[dict], str]:
    """
    Feed URLs for the website rows. The Apps Script's `targets` action carries them once Code.gs
    4 Oct 2026 is deployed (`website_feed` / `product_feed` on each row). Until it is, the Apps
    Script folds a URL into its last path segment, so the URLs are read from the Targets tab
    itself through Google's CSV export, which needs the sheet to be readable by link. Whichever
    source answers, Active and Type still come from the rows main.py already resolved.
    """
    if web_rows and any(r.get("website_feed") or r.get("product_feed") for r in web_rows):
        return [r for r in web_rows if r.get("active", True)], "apps-script"
    sid = sheet_id or os.environ.get("TARGETS_SHEET_ID", "")
    if not sid:
        raise RuntimeError("no feed URLs from the Apps Script and TARGETS_SHEET_ID is not set")
    url = f"https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv&sheet=Targets"
    r = requests.get(url, headers={"User-Agent": UA}, timeout=40)
    if r.status_code != 200 or "text/csv" not in r.headers.get("content-type", ""):
        raise RuntimeError(f"Targets tab not readable as CSV (HTTP {r.status_code}); deploy the new Code.gs "
                           "or share the sheet by link")
    rows = [x for x in rows_from_csv(r.content.decode("utf-8")) if x["active"]]
    return rows, "sheet-csv"


# --- Watsons: a real browser, because Akamai refuses everything else ------------------------------

WATSONS_HOST = "www.watsons.com.my"
WATSONS_BASE = "https://www.watsons.com.my"
WATSONS_API = "https://api.watsons.com.my/api/v2/wtcmy"
_WATSONS_FETCH_JS = ("async (u) => { const r = await fetch(u, {credentials: 'include'}); "
                     "return {status: r.status, text: await r.text()}; }")


class _WatsonsBrowser:
    """
    Stock Playwright Chromium, HEADED, on a virtual display. Measured 5 Oct 2026 on a GitHub runner:
    plain HTTP and headless Chromium both get Akamai "Access Denied" (403) on every Watsons page,
    headed Chromium gets 200 on the homepage, a brand list and a product page. No stealth plugin, no
    spoofed header or fingerprint: nothing here pretends to be anything but the browser it is. If
    Akamai starts refusing this too, that is an answer, not something to engineer around.

    The page's own JSON API (api.watsons.com.my) is read from inside the loaded page with fetch(), so
    it travels on the same session and cookies the page itself uses:
      products/search?fields=FULL&query=:bestSeller:productBrandCode:<code>&pageSize=100   brand list
      products/<code>?fields=FULL                                                          one product
    The brand list carries names and ingredients only; the claim copy (`description`, directions,
    keywords) is on the single-product call.
    """

    def __init__(self, delay: float = 1.2):
        self.delay = delay
        self._pw = self._browser = self._ctx = self._page = self._xvfb = None
        self._last_at = 0.0

    def _display(self) -> dict:
        env = dict(os.environ)
        if env.get("DISPLAY"):
            return env
        self._xvfb = subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1366x900x24", "-nolisten", "tcp"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            if os.path.exists("/tmp/.X11-unix/X99"):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("Xvfb did not start (no virtual display for a headed browser)")
        env["DISPLAY"] = ":99"
        return env

    def start(self):
        from playwright.sync_api import sync_playwright   # only a Watsons run needs it
        env = self._display()
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=False, env=env)
        self._ctx = self._browser.new_context(locale="en-MY", timezone_id="Asia/Kuala_Lumpur",
                                              viewport={"width": 1366, "height": 900})
        self._page = self._ctx.new_page()
        self.goto(WATSONS_BASE + "/")

    def goto(self, url: str):
        resp = self._page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            self._page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        status = resp.status if resp else None
        title = self._page.title() or ""
        if (status and status >= 400) or WALL_WORDS.search(title):
            raise RuntimeError(f"HTTP {status} from {WATSONS_HOST} (headed browser; {title[:40]!r})")

    def api(self, path_and_query: str):
        wait = self.delay - (time.time() - self._last_at)
        if wait > 0:
            time.sleep(wait)
        try:
            res = self._page.evaluate(_WATSONS_FETCH_JS, WATSONS_API + path_and_query)
        finally:
            self._last_at = time.time()
        if res["status"] in WALL_STATUS:
            raise RuntimeError(f"HTTP {res['status']} from api.watsons.com.my (headed browser)")
        if res["status"] != 200:
            raise RuntimeError(f"HTTP {res['status']} from api.watsons.com.my")
        return json.loads(res["text"])

    def brand_products(self, list_url: str, brand_code: str) -> List[dict]:
        self.goto(list_url)        # the way a visitor gets there; also refreshes the session
        out: List[dict] = []
        for page_no in range(3):
            d = self.api("/products/search?fields=FULL&query=" + quote(f":bestSeller:productBrandCode:{brand_code}")
                         + f"&pageSize=100&currentPage={page_no}&sort=bestSeller&lang=en&curr=MYR")
            out.extend(d.get("products") or [])
            if page_no + 1 >= int((d.get("pagination") or {}).get("totalPages") or 1):
                break
        return out

    def product(self, code: str) -> dict:
        return self.api(f"/products/{code}?fields=FULL&lang=en&curr=MYR")

    def close(self):
        for obj, fn in ((self._ctx, "close"), (self._browser, "close"), (self._pw, "stop"), (self._xvfb, "terminate")):
            try:
                if obj:
                    getattr(obj, fn)()
            except Exception:
                pass
        self._pw = self._browser = self._ctx = self._page = self._xvfb = None


# Feature rows that carry what the brand says (how to use it, who it is for, what it does). The rest
# (elabHeight, elabDepth, isGuestCheckoutAllowed, elabIsLivestream ...) is shop plumbing, not a claim.
_WATSONS_COPY_ROW = re.compile(r"uses?$|usage|direction|benefit|skin.?type|warning|caution|claim|feature|"
                               r"description|how.?to|indication|suitable|concern|function", re.I)


def watsons_text(p: dict) -> str:
    """Listing copy for the reviewer, from one `products/<code>?fields=FULL` answer: the name, the
    description, the directions and other feature rows, the keyword line, the ingredients. Prices,
    promotions, delivery terms and customer reviews are not the brand's claims and stay out."""
    name = p.get("elabProductName") or p.get("name") or ""
    brand = ((p.get("masterBrand") or {}).get("name")) or ""
    parts = [f"{brand} {name}".strip()]
    desc = html_to_text(p.get("description") or "")
    if desc:
        parts.append(desc)
    ingredients = (p.get("elabIngredients") or "").strip()
    for c in p.get("classifications") or []:
        for f in c.get("features") or []:
            code = (f.get("code") or "").lower()
            vals = " ".join(html_to_text(v.get("value") or "") for v in f.get("featureValues") or []).strip()
            if not vals:
                continue
            if "ingredient" in code:
                ingredients = ingredients or vals
            elif _WATSONS_COPY_ROW.search(code.rsplit(".", 1)[-1]):
                label = (f.get("name") or code.rsplit(".", 1)[-1]).strip()
                parts.append(f"{label}: {vals}")
    kw = (p.get("shortDescription") or "").strip()
    if "," in kw:
        parts.append("Keywords: " + kw)
    if ingredients:
        parts.append("Ingredients: " + ingredients[:600])
    return "\n".join(x for x in parts if x)


# --- the collector --------------------------------------------------------------------------------

class WebCollector:
    def __init__(self, run_cfg: Optional[dict] = None, delay: float = 1.2):
        cfg = (run_cfg or {}).get("web", {}) or {}
        self.max_products = int(cfg.get("max_products_per_target", 10))
        self.max_site_pages = int(cfg.get("max_site_pages", 6))
        self.delay = float(cfg.get("delay_s", delay))
        self.known_urls: set = set()
        self.should_stop: Optional[Callable[[], bool]] = None
        self.walled: Dict[str, str] = {}       # host -> why; every later row on it is skipped
        self.skipped_rows: List[dict] = []
        self._last_at = 0.0
        self._wb_browser: Optional[_WatsonsBrowser] = None
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    # -- network ---------------------------------------------------------------------------------
    def _get(self, url: str, **kw) -> requests.Response:
        wait = self.delay - (time.time() - self._last_at)
        if wait > 0:
            time.sleep(wait)
        try:
            return self.session.get(url, timeout=30, **kw)
        finally:
            self._last_at = time.time()

    def _check_wall(self, host: str, r: requests.Response) -> Optional[str]:
        if r.status_code in WALL_STATUS:
            why = f"HTTP {r.status_code} from {host}"
        elif "html" in r.headers.get("content-type", "") and WALL_WORDS.search(r.text[:3000]):
            why = f"anti-bot page from {host}"
        else:
            return None
        self.walled[host] = why
        return why

    # -- planning --------------------------------------------------------------------------------
    def classify(self, row: dict) -> Tuple[str, str]:
        """(kind, term). kind is shopify | woo | guardian | opencart | site | skip:<why>"""
        typ = row["type"].strip().lower()
        if typ in SKIP_TYPES:
            return "skip:" + SKIP_TYPES[typ], ""
        pf, wf = row.get("product_feed", ""), row.get("website_feed", "")
        feed = pf or wf
        if not feed:
            return "skip:no feed URL on the row", ""
        host = urlsplit(feed).netloc.lower()
        if "shopee." in host:
            return "skip:" + SKIP_TYPES["shopee store"], ""
        if "/search/suggest.json" in feed:
            return "shopify", (parse_qs(urlsplit(feed).query).get("q") or [row["name"]])[0]
        if "/wp-json/wc/store" in feed:
            return "woo", (parse_qs(urlsplit(feed).query).get("search") or [row["name"]])[0]
        if host.endswith("guardian.com.my"):
            m = re.search(r'search:"([^"]+)"', requests.utils.unquote(feed))
            return "guardian", (m.group(1) if m else row["name"])
        if "route=product/search" in feed:
            return "opencart", (parse_qs(urlsplit(feed).query).get("search") or [row["name"]])[0]
        if "watsons.com.my" in host:
            return "watsons", row["name"]
        if "/all-brands/" in feed or "/list/" in feed:
            return "skip:unrecognised listing URL", ""
        return "site", row["name"]

    def plan(self, rows: List[dict]) -> List[Tuple[str, str, str]]:
        out = []
        for r in rows:
            kind, _ = self.classify(r)
            if kind.startswith("skip:"):
                continue
            out.append((r["name"], r["type"], r.get("product_feed") or r.get("website_feed")))
        return out

    # -- per kind: -> list of (url, title_for_brand_match, vendor, text) -------------------------
    def _shopify(self, row, term):
        feed = row.get("product_feed") or row["website_feed"]
        base = "{0.scheme}://{0.netloc}".format(urlsplit(feed))
        url = f"{base}/search/suggest.json?q={quote(term)}&resources[type]=product&resources[limit]=10"
        r = self._get(url)
        host = urlsplit(url).netloc
        if self._check_wall(host, r):
            raise RuntimeError(self.walled[host])
        items = ((r.json().get("resources") or {}).get("results") or {}).get("products") or []
        for p in items:
            text = "\n".join(x for x in (p.get("title", ""), p.get("vendor", ""),
                                         html_to_text(p.get("body", ""))) if x)
            yield (urljoin(base, _clean_url(p.get("url", "") or "/products/" + p.get("handle", ""))),
                   p.get("title", ""), p.get("vendor", ""), text)

    def _woo(self, row, term):
        feed = row.get("product_feed") or row["website_feed"]
        r = self._get(feed)
        host = urlsplit(feed).netloc
        if self._check_wall(host, r):
            raise RuntimeError(self.walled[host])
        for p in r.json():
            brands = " ".join((b or {}).get("name", "") for b in (p.get("brands") or []))
            desc = html_to_text(p.get("description", "")) or html_to_text(p.get("short_description", ""))
            text = "\n".join(x for x in (_html.unescape(p.get("name", "")), brands, desc) if x)
            yield p.get("permalink", ""), p.get("name", ""), brands, text

    def _guardian(self, row, term):
        q = ('{products(search:"%s",pageSize:20){total_count items{name sku url_key url_suffix brand '
             'description{html} short_description{html}}}}' % term.replace('"', ""))
        r = self._get("https://guardian.com.my/graphql", params={"query": q})
        if self._check_wall("guardian.com.my", r):
            raise RuntimeError(self.walled["guardian.com.my"])
        for p in (((r.json().get("data") or {}).get("products") or {}).get("items") or []):
            desc = html_to_text((p.get("description") or {}).get("html", "")) or \
                html_to_text((p.get("short_description") or {}).get("html", ""))
            text = "\n".join(x for x in (p.get("name", ""), p.get("brand") or "", desc) if x)
            yield (f"https://guardian.com.my/{p.get('url_key', '')}{p.get('url_suffix') or '.html'}",
                   p.get("name", ""), p.get("brand") or "", text)

    def _opencart(self, row, term):
        feed = row.get("website_feed") or row["product_feed"]
        host = urlsplit(feed).netloc
        r = self._get(feed)
        if self._check_wall(host, r):
            raise RuntimeError(self.walled[host])
        base = "{0.scheme}://{0.netloc}/".format(urlsplit(feed))
        seen, links = set(), []
        for h in re.findall(r'href="([^"]+)"', r.text):
            h = _html.unescape(h)
            if not re.search(r"(\.html|-html)\?search=", h):
                continue
            full = urljoin(base, h)
            clean = _clean_url(full)
            if clean not in seen:
                seen.add(clean)
                links.append(clean)
        for link in links:
            slug = urlsplit(link).path.rsplit("/", 1)[-1]
            if not brand_matches(row["name"], slug.replace("-", " ")):
                continue
            if canonical_url(link) in self.known_urls:
                continue
            page = self._get(link)
            if self._check_wall(host, page):
                raise RuntimeError(self.walled[host])
            t = page.text
            title = re.search(r"<h1[^>]*>(.*?)</h1>", t, re.S | re.I)
            title = html_to_text(title.group(1)) if title else slug
            desc = re.search(r'id="tab-description"[^>]*>(.*?)(?=<div[^>]+id="tab-|</section>|$)', t, re.S | re.I)
            meta = re.search(r'<meta name="description" content="([^"]*)"', t, re.I)
            body = html_to_text(desc.group(1)) if desc else _html.unescape(meta.group(1) if meta else "")
            yield link, title, "", "\n".join(x for x in (title, body) if x)

    def _site(self, row, term):
        """A brand's own website: the homepage and a few product / shop pages one click away."""
        home = row.get("website_feed") or row["product_feed"]
        host = urlsplit(home).netloc
        r = self._get(home)
        if self._check_wall(host, r):
            raise RuntimeError(self.walled[host])
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code} from {host}")
        pages = [(home, r.text)]
        seen = {_clean_url(home).rstrip("/")}
        cands = []
        for h in re.findall(r'href="([^"#]+)"', r.text):
            full = _clean_url(urljoin(home, _html.unescape(h)))
            ph = urlsplit(full)
            if ph.netloc != host or full.rstrip("/") in seen:
                continue
            if re.search(r"/(product|produk|products|shop|collections?|skincare|range|our-products)", ph.path, re.I) \
                    and not re.search(r"\.(jpg|png|webp|pdf|css|js)$", ph.path, re.I):
                seen.add(full.rstrip("/"))
                cands.append(full)
        for c in cands[: self.max_site_pages]:
            if canonical_url(c) in self.known_urls:
                continue
            pr = self._get(c)
            if pr.status_code == 200 and "html" in pr.headers.get("content-type", ""):
                pages.append((c, pr.text))
        for url, body in pages:
            title = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
            title = html_to_text(title.group(1)) if title else ""
            text = html_to_text(body)
            if len(text) >= 80:
                yield url, row["name"], row["name"], (title + "\n" + text) if title else text

    def _watsons(self, row, term):
        """Watsons MY: a brand list page (every product of the brand code) or one product page."""
        feed = row.get("product_feed") or row.get("website_feed") or ""
        if WATSONS_HOST in self.walled:
            raise RuntimeError(self.walled[WATSONS_HOST])
        try:
            if self._wb_browser is None:
                wb = _WatsonsBrowser(self.delay)
                try:
                    wb.start()
                except RuntimeError:
                    wb.close()
                    raise
                except Exception as e:
                    wb.close()
                    raise RuntimeError(f"browser could not start: {type(e).__name__}: {str(e)[:120]}")
                self._wb_browser = wb
            b = self._wb_browser
            m = re.search(r"/all-brands/(?:list|b)/(\d+)/", feed)
            pm = re.search(r"/p/BP_(\d+)", feed)
            if m:
                found = [(str(x.get("code", "")).replace("BP_", ""), urljoin(WATSONS_BASE, x.get("url", "")))
                         for x in b.brand_products(feed, m.group(1)) if x.get("code")]
            elif pm:
                found = [(pm.group(1), _clean_url(feed))]
            else:
                raise RuntimeError("unrecognised Watsons feed URL")
            kept = 0
            for code, url in found:
                if kept >= self.max_products:
                    return
                if canonical_url(url) in self.known_urls:
                    continue
                full = b.product(code)
                kept += 1
                yield (url, full.get("elabProductName") or full.get("name") or "",
                       ((full.get("masterBrand") or {}).get("name")) or "", watsons_text(full)[:MAX_TEXT])
        except RuntimeError as e:
            if re.search(r"HTTP [45]\d\d|could not start|did not start", str(e)):
                self.walled[WATSONS_HOST] = str(e)
            raise

    def close(self):
        if self._wb_browser is not None:
            self._wb_browser.close()
            self._wb_browser = None

    # -- the run ---------------------------------------------------------------------------------
    def iter_run(self, rows: List[dict], should_stop: Optional[Callable[[], bool]] = None) -> Iterator[TargetResult]:
        try:
            yield from self._iter_rows(rows, should_stop)
        finally:
            self.close()        # the Watsons browser, if one was started

    def _iter_rows(self, rows: List[dict], should_stop: Optional[Callable[[], bool]] = None) -> Iterator[TargetResult]:
        self.should_stop = should_stop
        for row in rows:
            kind, term = self.classify(row)
            name, channel = row["name"], row["type"]
            feed = row.get("product_feed") or row.get("website_feed") or ""
            if kind.startswith("skip:"):
                self.skipped_rows.append({"brand": name, "channel": channel, "why": kind[5:]})
                continue
            if should_stop and should_stop():
                log.warning("run budget reached; stopping before %s on %s", name, channel)
                return
            host = urlsplit(feed).netloc
            if host in self.walled:
                yield TargetResult(brand=name, platform=channel, profile_url=feed,
                                   error=f"skipped: {self.walled[host]} earlier this run")
                continue
            res = TargetResult(brand=name, platform=channel, profile_url=feed)
            try:
                gen = getattr(self, "_" + kind)(row, term)
                kept = 0
                for url, title, vendor, text in gen:
                    if not url or canonical_url(url) in self.known_urls:
                        continue
                    if kind in ("shopify", "woo", "guardian") and not brand_matches(name, title, vendor):
                        continue
                    if kept >= self.max_products:
                        res.truncated = False
                        break
                    kept += 1
                    res.posts.append(Post(brand=name, platform=channel, url=url, text=text[:MAX_TEXT]))
                    if should_stop and should_stop():
                        res.truncated = True
                        break
                log.info("%s / %s: %d new listing(s) from %s", name, channel, len(res.posts), kind)
            except Exception as e:
                res.error = f"{type(e).__name__}: {str(e)[:200]}"
                log.warning("%s / %s: %s", name, channel, res.error)
            yield res
