#!/usr/bin/env python3
"""
Read the KKM complaint Google Form and print every question with its entry id.

    python tools/form_schema.py            # the URL saved behind "Open KKM form"
    python tools/form_schema.py <form-url> # any other form

Why this exists. The dashboard's "Fill KKM form" button opens the form PRE-FILLED
(Google's own ?usp=pp_url&entry.N=... link), and that needs the form's entry ids.
They are not visible in the form, they change if a question is deleted and re-added,
and docs.google.com is not reachable from the Claude environment the dashboard was
built in. A GitHub runner reaches it fine, so this runs there, and re-checking after
KKM edits the form is one dispatch.

It reads the URL from the backend's own `bootstrap` answer (the same value the
dashboard reads), so nothing has to be copied by hand. It prints the form URL, the
questions and their ids. It writes nothing - not to the sheet, not to the repo, not
to the form - and it never submits anything.
"""
from __future__ import annotations

import json
import os
import re
import sys

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# Google's internal item-type codes, as they appear in FB_PUBLIC_LOAD_DATA_.
TYPES = {0: "short_answer", 1: "paragraph", 2: "multiple_choice", 3: "dropdown",
         4: "checkboxes", 5: "linear_scale", 6: "title_description", 7: "grid",
         8: "section", 9: "date", 10: "time", 11: "image", 12: "video", 13: "file_upload"}
# These carry no answer and have no entry id.
NO_ANSWER = {6, 8, 11, 12}


def form_url_from_backend() -> str:
    # The same call AppsScriptClient._post makes, done directly: importing the client drags in
    # the scraper module and Playwright, which a five-second read has no use for.
    url, token = os.environ.get("APPS_SCRIPT_WEBHOOK_URL", ""), os.environ.get("APPS_SCRIPT_API_TOKEN", "")
    if not url or not token:
        raise SystemExit("APPS_SCRIPT_WEBHOOK_URL and APPS_SCRIPT_API_TOKEN are required")
    r = requests.post(url, json={"action": "bootstrap", "token": token}, timeout=180, allow_redirects=True)
    try:
        data = r.json()
    except ValueError:
        title = re.search(r"<title>(.*?)</title>", r.text or "", re.S | re.I)
        raise SystemExit(f"backend answered HTTP {r.status_code} ({r.headers.get('content-type', '?')}) "
                         f"with no JSON; page title: {title.group(1).strip() if title else '-'}; "
                         f"first bytes: {(r.text or '')[:240]!r}")
    if not data.get("ok"):
        raise SystemExit(f"backend refused bootstrap: {data.get('error')}")
    return (data.get("kkmFormUrl") or "").strip()


def parse(html: str) -> dict:
    m = re.search(r"FB_PUBLIC_LOAD_DATA_\s*=\s*(\[.*?\]);\s*</script>", html, re.S)
    if not m:
        raise ValueError("no FB_PUBLIC_LOAD_DATA_ in the page - not a public Google Form view")
    data = json.loads(m.group(1))
    body = data[1]
    title = (body[8] if len(body) > 8 and isinstance(body[8], str) else "") or (data[3] if len(data) > 3 else "")
    items = []
    for it in body[1] or []:
        kind = it[3] if len(it) > 3 else None
        q = {"title": (it[1] or "").strip(), "type": TYPES.get(kind, f"type_{kind}"),
             "description": (it[2] or "").strip() if len(it) > 2 and isinstance(it[2], str) else ""}
        if kind not in NO_ANSWER and len(it) > 4 and it[4]:
            fields = []
            for f in it[4]:
                opts = [o[0] for o in (f[1] or []) if o and o[0] not in (None, "")] if len(f) > 1 else []
                fields.append({"entry": f"entry.{f[0]}", "required": bool(f[2]) if len(f) > 2 else False,
                               "options": opts})
            q["fields"] = fields
        items.append(q)
    return {"title": title, "items": items}


def main(argv) -> int:
    url = argv[1] if len(argv) > 1 else form_url_from_backend()
    if not url:
        print("No form URL is saved yet. Open the dashboard, click 'Open KKM form' once and paste "
              "the form link; it is stored in the sheet's Script Properties as KKM_FORM_URL.")
        return 2
    print(f"form URL : {url}")
    s = requests.Session()
    r = s.get(url, headers={"User-Agent": UA, "Accept-Language": "ms-MY,ms;q=0.9,en;q=0.8"},
              timeout=60, allow_redirects=True)
    print(f"final URL: {r.url.split('?')[0]}   HTTP {r.status_code}")
    if "accounts.google.com" in r.url:
        print("The form asks for a Google sign-in before it shows its questions. A pre-filled link "
              "still works in your own signed-in browser, but the questions cannot be read from "
              "here; open the form yourself, use 'Get pre-filled link', and send that link.")
        return 3
    try:
        form = parse(r.text)
    except ValueError as e:
        print(f"could not read the form: {e}")
        return 4
    m = re.search(r"/forms/d/e/([A-Za-z0-9_-]+)/", r.url)
    base = f"https://docs.google.com/forms/d/e/{m.group(1)}/viewform" if m else r.url.split("?")[0]
    print(f"prefill  : {base}?usp=pp_url&entry.N=...")
    print(f"title    : {form['title']}")
    print()
    for i, q in enumerate(form["items"], 1):
        head = f"{i:2d}. [{q['type']}] {q['title']}"
        if "fields" not in q:
            print(head + "   (no answer)")
            continue
        for f in q["fields"]:
            print(f"{head}\n      {f['entry']}{'  REQUIRED' if f['required'] else ''}")
            for o in f["options"]:
                print(f"        - {o}")
        if q["description"]:
            print(f"      note: {q['description'][:200]}")
    print()
    print("JSON " + json.dumps({"base": base, **form}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
