"""Read-only: run the scraper's OWN screenshot function on Instagram post URLs with the sweep's session and report which
element it framed and how big the PNG is. Writes nothing to the sheet. Shots go to probe_out/. 23 Oct 2026."""
import os, sys, base64, gzip, json, tempfile, logging
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scraper"))
logging.basicConfig(level=logging.INFO, format="%(message)s")
import scraper as sc
from playwright.sync_api import sync_playwright

OUT = "probe_out"


def state():
    raw = (os.environ.get("PW_STORAGE_STATE_B64") or "").strip()
    if not raw:
        return None
    b = base64.b64decode(raw)
    if b[:2] == b"\x1f\x8b":
        b = gzip.decompress(b)
    fd, p = tempfile.mkstemp(suffix=".json"); os.write(fd, b); os.close(fd)
    return p


os.makedirs(OUT, exist_ok=True)
st = state()
with sync_playwright() as pw:
    for i, url in enumerate(sys.argv[1:]):
        b = pw.chromium.launch(headless=True)
        try:
            ctx = b.new_context(storage_state=st, viewport={"width": 1280, "height": 1600}, device_scale_factor=1,
                                locale="en-MY", timezone_id="Asia/Kuala_Lumpur")
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(6000)
            post = sc.Post(brand="probe", platform="Instagram", url=url)
            sc._save_screenshot(page, post, "probe", "Instagram", url, OUT)
            path = getattr(post, "screenshot_path", None)
            if path:
                import struct
                with open(path, "rb") as f:
                    head = f.read(24)
                w, h = struct.unpack(">II", head[16:24])
                print("RESULT", url, "->", os.path.basename(path), "%dx%d" % (w, h))
            else:
                print("RESULT", url, "-> no screenshot", post.errors)
        except Exception as e:
            print("NOT A MEASUREMENT", url, repr(e)[:160])
        finally:
            b.close()
