"""
Turn cookie exports from a PLAIN Chrome into the Playwright session the shop probe reads, with no
recorder involved (Wan, Oct 2026: Shopee / TikTok login, old account, plain Chrome, no recorder).

Run it on YOUR PC, never on a server:
    py tools\\cookies_to_state.py shopee.json tiktok.json

Input  : one or more cookie exports in the JSON format of the Cookie-Editor extension (Export > JSON).
Output : a gzip+base64 string for the GitHub secret PW_STORAGE_STATE_SHOP_B64, copied to the Windows
         clipboard (and written to shop_state.b64.txt if there is no clipboard). Cookie VALUES are never
         printed. Only shopee.* and tiktok.* cookies are kept, everything else in the export is dropped.
It also says, per site, whether the cookie that proves a logged-in session is present, so a logged-out
export is caught here and not after a runner has spent an attempt on it.
"""
import base64, gzip, json, subprocess, sys

KEEP = ("shopee.", "tiktok.")
PROOF = {"shopee": ("SPC_EC", "SPC_ST"), "tiktok": ("sessionid", "sid_tt", "sessionid_ss")}
SAMESITE = {"no_restriction": "None", "none": "None", "lax": "Lax", "strict": "Strict",
            "unspecified": "Lax", "": "Lax"}


def convert(exports):
    cookies, seen = [], set()
    for items in exports:
        for c in items:
            dom = (c.get("domain") or "").lower()
            if not any(k in dom for k in KEEP) or not c.get("name"):
                continue
            key = (c["name"], dom, c.get("path") or "/")
            if key in seen:
                continue
            seen.add(key)
            exp = c.get("expirationDate")
            cookies.append({
                "name": c["name"], "value": c.get("value", ""), "domain": c.get("domain"),
                "path": c.get("path") or "/",
                "expires": float(exp) if (exp and not c.get("session")) else -1,
                "httpOnly": bool(c.get("httpOnly")), "secure": bool(c.get("secure")),
                "sameSite": SAMESITE.get(str(c.get("sameSite") or "").lower(), "Lax"),
            })
    return {"cookies": cookies, "origins": []}


def proof(state):
    out = {}
    for site, names in PROOF.items():
        have = {c["name"] for c in state["cookies"] if site in (c["domain"] or "").lower()}
        out[site] = {"cookies": sum(1 for c in state["cookies"] if site in (c["domain"] or "").lower()),
                     "login_cookie": bool(have & set(names))}
    return out


def encode(state):
    return base64.b64encode(gzip.compress(json.dumps(state, separators=(",", ":")).encode())).decode()


def main(paths):
    if not paths:
        print(__doc__)
        return 2
    exports = []
    for p in paths:
        with open(p, encoding="utf-8-sig") as f:
            data = json.load(f)
        exports.append(data["cookies"] if isinstance(data, dict) and "cookies" in data else data)
    state = convert(exports)
    for site, r in proof(state).items():
        flag = "logged in" if r["login_cookie"] else "NO LOGIN COOKIE - this export looks logged out"
        print(f"{site:7s} {r['cookies']:3d} cookies  {flag}")
    b64 = encode(state)
    print(f"secret length {len(b64)} characters (GitHub's limit is 48000)")
    if len(b64) > 48000:
        print("TOO LARGE for a GitHub secret")
        return 1
    try:
        subprocess.run(["clip"], input=b64.encode(), check=True)
        print("Copied to the clipboard. Paste it into the secret PW_STORAGE_STATE_SHOP_B64, then delete the exports.")
    except Exception:
        open("shop_state.b64.txt", "w").write(b64)
        print("No clipboard: written to shop_state.b64.txt. Paste its contents into the secret, then delete the file.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
