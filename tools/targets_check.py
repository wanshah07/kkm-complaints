"""Read-only check that the Apps Script deployment has Targets.gs (Wan, 14 Nov 2026: 'done deployed').
Asks the two new routes the dashboard and the account check use. Writes no status: `target_status_update`
is sent an empty list. The first `targets_admin` creates the empty TargetStatus tab, which is expected."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scraper"))
from uploader import AppsScriptClient

c = AppsScriptClient()
ok = True
try:
    d = c._post({"action": "targets_admin"}, retries=2)
    t = d.get("targets") or []
    print("TARGETS_ADMIN ok: platforms=%s rows=%d active=%d status_rows=%d types=%d" % (
        d.get("platforms"), len(t), sum(1 for x in t if x.get("active")), len(d.get("status") or {}), len(d.get("types") or [])), flush=True)
except Exception as e:
    ok = False
    print("TARGETS_ADMIN FAILED:", str(e)[:200], flush=True)
try:
    print("TARGET_STATUS_UPDATE (empty list) ->", c._post({"action": "target_status_update", "updates": []}, retries=2), flush=True)
except Exception as e:
    ok = False
    print("TARGET_STATUS_UPDATE FAILED:", str(e)[:200], flush=True)
sys.exit(0 if ok else 1)
