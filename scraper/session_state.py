"""
Two saved logins, one browser state (Wan, 25 Nov 2026: a new secret PW_STORAGE_STATE_IG_B64 for Instagram).

PW_STORAGE_STATE_B64 is the long-standing state with Facebook, Threads and Instagram in it. Instagram ends its login
every few hours to days under heavy sweeps, and re-recording the whole state for that is wasteful, so a second secret
carries just a fresh Instagram login. This module lays it over the first:

  * for each site the new state has cookies for (instagram.com, facebook.com, threads.*), the old state's cookies and
    local-storage origins for that site are replaced by the new ones;
  * every site the new state does not mention is left exactly as the old state had it;
  * with no old state, the new one is used alone; with no new one, the old one is returned untouched.

Nothing here is ever printed: both values are secrets.
"""
from __future__ import annotations

import base64
import gzip
import json
from typing import Optional

_SITES = ("instagram.com", "facebook.com", "threads.")


def _decode(b64: str) -> Optional[dict]:
    b64 = (b64 or "").strip()
    if not b64 or b64.startswith("#"):
        return None
    try:
        raw = base64.b64decode(b64)
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        state = json.loads(raw)
        return state if isinstance(state, dict) else None
    except Exception:
        return None


def _site_of(host: str) -> Optional[str]:
    host = (host or "").lower()
    for s in _SITES:
        if s in host:
            return s
    return None


def merge_state(main: Optional[dict], overlay: Optional[dict]) -> Optional[dict]:
    if not overlay:
        return main
    if not main:
        return overlay
    new_sites = {s for c in overlay.get("cookies", []) or [] for s in [_site_of(str(c.get("domain", "")))] if s}
    for o in overlay.get("origins", []) or []:
        s = _site_of(str(o.get("origin", "")))
        if s:
            new_sites.add(s)
    cookies = [c for c in main.get("cookies", []) or [] if _site_of(str(c.get("domain", ""))) not in new_sites]
    cookies += list(overlay.get("cookies", []) or [])
    origins = [o for o in main.get("origins", []) or [] if _site_of(str(o.get("origin", ""))) not in new_sites]
    origins += list(overlay.get("origins", []) or [])
    out = dict(main)
    out["cookies"], out["origins"] = cookies, origins
    return out


def combined_b64(main_b64: str, ig_b64: str) -> str:
    """Base64 of the combined state, or the main value unchanged when there is nothing to combine."""
    main, ig = _decode(main_b64), _decode(ig_b64)
    if not ig:
        return main_b64 or ""
    merged = merge_state(main, ig)
    return base64.b64encode(gzip.compress(json.dumps(merged).encode("utf-8"))).decode("ascii")
