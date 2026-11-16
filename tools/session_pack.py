#!/usr/bin/env python3
"""
Turn the browser session you recorded (storage_state.json) into the text for the GitHub secret PW_STORAGE_STATE_B64.

It keeps only the Instagram and Threads logins (Facebook is read through Meta's Ad Library and needs none, and a
personal Facebook login does not belong in an automated scraper), drops the browser's local storage, compresses it,
checks that each login is really in the file and prints how long it lasts. It prints names and days, never a value.

    py session_pack.py storage_state.json            (Windows: the text is copied to the clipboard)
    python3 session_pack.py storage_state.json       (elsewhere: written to session_b64.txt, delete it afterwards)

Then GitHub -> repo -> Settings -> Secrets and variables -> Actions -> PW_STORAGE_STATE_B64 -> Update -> paste -> save.
"""
import base64, gzip, json, os, subprocess, sys, time

KEEP = {"Instagram": ("instagram.",), "Threads": ("threads.",)}
LOGIN = {"Instagram": "sessionid", "Threads": "sessionid"}
LIMIT = 48000          # a GitHub secret stops at 48 KB


def describe(state, now=None):
    """Per site: (cookies kept, login cookie present, days the login cookie lasts or None)."""
    now = now or time.time()
    out = {}
    for site, words in KEEP.items():
        cs = [c for c in state.get("cookies", []) if any(w in str(c.get("domain", "")).lower() for w in words)]
        login = [c for c in cs if c.get("name") == LOGIN[site] and c.get("value")]
        days = None
        if login:
            exp = login[0].get("expires")
            days = None if exp is None or exp <= 0 else round((float(exp) - now) / 86400.0, 1)
        out[site] = (len(cs), bool(login), days)
    return out


def pack(state):
    """The state reduced to the two logins, as base64 of its gzip."""
    keep = [c for c in state.get("cookies", []) if any(w in str(c.get("domain", "")).lower() for ws in KEEP.values() for w in ws)]
    raw = json.dumps({"cookies": keep, "origins": []}, separators=(",", ":")).encode()
    return base64.b64encode(gzip.compress(raw, 9)).decode()


def main(argv):
    if len(argv) != 2 or not os.path.exists(argv[1]):
        print("usage: session_pack.py storage_state.json")
        return 2
    state = json.load(open(argv[1], "r", encoding="utf-8"))
    info = describe(state)
    ok = True
    for site, (n, has, days) in info.items():
        if has:
            print("%-10s login found, %d cookie(s) kept, lasts about %s day(s)" % (site, n, days if days is not None else "this browser session"))
        else:
            ok = False
            print("%-10s NO LOGIN in the file (%d cookie(s) seen). Record again and sign in to %s in the SAME window before closing it." % (site, n, site))
    others = sorted({str(c.get("domain", "")).lstrip(".") for c in state.get("cookies", [])
                     if not any(w in str(c.get("domain", "")).lower() for ws in KEEP.values() for w in ws)})
    if others:
        print("left out on purpose:", ", ".join(others[:8]))
    if not ok:
        return 1
    text = pack(state)
    print("secret text is %d characters (limit %d)" % (len(text), LIMIT))
    if len(text) > LIMIT:
        print("TOO LARGE for a GitHub secret; tell Claude")
        return 1
    done = False
    if os.name == "nt":
        try:
            subprocess.run(["clip"], input=text.encode("utf-16le"), check=True)
            done = True
            print("Copied to the clipboard. Paste it into the secret, then delete storage_state.json.")
        except Exception:
            pass
    if not done:
        open("session_b64.txt", "w").write(text)
        print("Written to session_b64.txt. Paste its contents into the secret, then delete it and storage_state.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
