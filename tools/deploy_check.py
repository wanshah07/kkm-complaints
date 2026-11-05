"""Read-only check of the live Apps Script deployment (Wan, 5 Nov 2026: 'republished, please check').
Nothing is written. Prints what the new Code.gs does that the old one did not."""
import collections, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scraper"))
from uploader import AppsScriptClient

c = AppsScriptClient()
print("PING", c.ping(), flush=True)
rows = c.list_rows(status="")
print("LIST_ROWS", len(rows), flush=True)
sk = [r for r in rows if "skintific" in " ".join(str(r.get(k, "")) for k in ("Brand", "Nama Kosmetik", "Violation Reason", "Remarks", "Post URL")).lower()]
print("SKINTIFIC_ROWS_IN_LIST", len(sk), flush=True)
for r in sk[:30]:
    print("SK", r.get("ID"), r.get("Brand"), r.get("Status"), flush=True)
ids = collections.Counter(str(r.get("ID", "")) for r in rows)
dups = {k: v for k, v in ids.items() if v > 1}
print("DUPLICATE_IDS", dups, flush=True)
for k in dups:
    for r in rows:
        if str(r.get("ID")) == k:
            print("DUP", k, r.get("_row"), r.get("Brand"), r.get("Platform"), r.get("Post URL"), r.get("Status"), flush=True)
