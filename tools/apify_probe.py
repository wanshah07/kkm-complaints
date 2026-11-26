"""
What does Apify's Instagram scraper hand back, what does it cost, and how long does it take? (Wan, 26 Nov 2026: make Apify the main reader,
Playwright the fallback.) Manual and read-only: runs ONE actor on ONE public profile or hashtag with a small limit and prints the run's status,
cost, duration, the field names of an item and a few items' dates and captions. Prints no token and no account name of ours.

Usage: python tools/apify_probe.py ACTOR TARGET [LIMIT]
   ACTOR   apify/instagram-scraper | apify/instagram-hashtag-scraper | ... (slash or tilde)
   TARGET  an Instagram handle (labmuffinbeautyscience) or, with a leading #, a hashtag (#skintific)
Needs APIFY_TOKEN.
"""
import json, os, sys, time, urllib.request, urllib.error

API = "https://api.apify.com/v2/"


def call(method, path, body=None, tok=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, {"error": (e.read() or b"")[:300].decode("utf-8", "replace")}


def main():
    tok = (os.environ.get("APIFY_TOKEN") or "").strip()
    if not tok:
        sys.exit("APIFY_TOKEN is not set")
    actor = sys.argv[1].replace("/", "~")
    target = sys.argv[2]
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    if target.startswith("#"):
        inp = {"hashtags": [target.lstrip("#")], "resultsLimit": limit}
    elif "instagram-scraper" in actor:
        inp = {"directUrls": [f"https://www.instagram.com/{target.lstrip('@')}/"], "resultsType": "posts", "resultsLimit": limit,
               "addParentData": False}
    else:
        inp = {"username": [target.lstrip("@")], "resultsLimit": limit}
    print("input keys:", sorted(inp))
    t0 = time.time()
    st, run = call("POST", f"acts/{actor}/runs?memory=1024&timeout=240", inp, tok)
    if st not in (200, 201):
        sys.exit(f"start failed HTTP {st}: {run}")
    rid = run["data"]["id"]
    ds = run["data"]["defaultDatasetId"]
    for _ in range(60):
        time.sleep(5)
        st, r = call("GET", f"actor-runs/{rid}", None, tok)
        d = r.get("data", {})
        if d.get("status") in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            break
    print(f"run status={d.get('status')} seconds={round(time.time() - t0)} usageTotalUsd={d.get('usageTotalUsd')} "
          f"stats.computeUnits={((d.get('stats') or {}).get('computeUnits'))} pricingModel={((d.get('pricingInfo') or {}).get('pricingModel'))}")
    st, items = call("GET", f"datasets/{ds}/items?clean=true&limit=50", None, tok)
    items = items if isinstance(items, list) else []
    print("items:", len(items))
    if items:
        print("fields of item 0:", sorted(items[0].keys()))
    for it in items[:6]:
        cap = str(it.get("caption") or "").replace("\n", " ")[:70]
        print({k: it.get(k) for k in ("shortCode", "type", "timestamp", "isPinned", "ownerUsername", "likesCount", "commentsCount")},
              "| caption:", cap, "| displayUrl host:", (str(it.get("displayUrl") or "").split("/")[2] if it.get("displayUrl") else None),
              "| images:", len(it.get("images") or []), "| child:", len(it.get("childPosts") or []))
    if items and any(it.get("error") for it in items):
        print("errors:", [str(it.get("error"))[:120] for it in items if it.get("error")][:3])


if __name__ == "__main__":
    main()
