#!/usr/bin/env python3
"""
Take back "last swept" records a sweep wrote while it was browsing logged out.

15 Nov 2026: a bug (#23) made every run drop the saved login without saying so. Sweep run 14 then wrote a "last sweep"
stamp for 53 accounts, among them Instagram and Threads accounts that hit a login wall or were read as a signed-out
visitor (Threads shows about four posts that way). A stamp sends the account to the back of the "oldest first" order,
so those accounts would have waited their turn again without ever being read. This clears the stamp (sweep_at,
sweep_posts, sweep_note) on exactly those records so the next run visits them first.

It touches the TargetStatus tab only, through the same `target_status_update` route the sweep uses. It never reads
or writes the Complaints sheet, so the status column J, dismissals and own-brand rows are out of its reach.

  python tools/sweep_status_repair.py --after 2026-11-15T17:20:00Z --before 2026-11-15T17:30:00Z \
         [--platforms Instagram,Threads] [--dry-run]

A record is cleared when its sweep_at falls in [after, before] AND its platform is one of --platforms.
"""
import argparse, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scraper"))


def pick(status, after, before, platforms, names=None):
    """The records to clear: [(brand, platform, handle, sweep_at, note)], from the backend's status map.
    The map is keyed "brand\\x01platform" in lower case and its records carry no brand or platform of their own;
    `names` maps a lower-case brand to the name the Targets tab spells."""
    want = {p.strip().lower(): p.strip() for p in platforms if p.strip()}
    names = {str(k).lower(): v for k, v in (names or {}).items()}
    out = []
    for key, rec in (status or {}).items():
        brand_l, _, plat_l = str(key).partition("\x01")
        at = str(rec.get("sweep_at") or "")
        if not at or not (after <= at <= before) or plat_l not in want:
            continue
        out.append((names.get(brand_l, brand_l), want[plat_l], str(rec.get("handle") or ""), at, str(rec.get("sweep_note") or "")))
    return sorted(out)


def updates_for(records):
    return [{"brand": b, "platform": p, "handle": h, "sweep_at": "", "sweep_posts": 0, "sweep_note": ""} for b, p, h, _at, _n in records]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--after", required=True)
    ap.add_argument("--before", required=True)
    ap.add_argument("--platforms", default="Instagram,Threads")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    from uploader import AppsScriptClient
    client = AppsScriptClient()
    admin = client._post({"action": "targets_admin"}, retries=3)
    status = admin.get("status") or {}
    names = {str(t.get("name") or "").lower(): str(t.get("name") or "") for t in admin.get("targets") or []}
    sample = next(iter(status.values()), {})
    print("status records: %d; fields seen: %s" % (len(status), sorted(sample.keys())), flush=True)
    records = pick(status, args.after, args.before, args.platforms.split(","), names)
    for b, p, h, at, note in records:
        print("CLEAR %-22s %-10s @%-26s swept %s note=%r" % (b, p, h, at, note[:60]), flush=True)
    print("%d record(s) match %s..%s on %s" % (len(records), args.after, args.before, args.platforms), flush=True)
    if args.dry_run or not records:
        print("dry run: nothing written" if args.dry_run else "nothing to clear")
        return 0
    ups = updates_for(records)
    done = 0
    for i in range(0, len(ups), 100):
        r = client._post({"action": "target_status_update", "updates": ups[i:i + 100]}, retries=3)
        done += int(r.get("updated") or 0)
    print("cleared %d record(s)" % done, flush=True)
    # read back: none of them may still carry a stamp
    after = client._post({"action": "targets_admin"}, retries=3).get("status") or {}
    left = pick(after, args.after, args.before, args.platforms.split(","), names)
    print("read back: %d record(s) still stamped in that window (expect 0)" % len(left), flush=True)
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
