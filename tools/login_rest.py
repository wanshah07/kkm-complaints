#!/usr/bin/env python3
"""
Give the saved Instagram login a rest (Wan, 26 Nov 2026: "make the next login last longer").

Instagram ended the recorded login after about 8 hours (23 Nov), about 16 (24 Nov) and about 5.5 (25 Nov) of back-to-back
sweeps, and the last time it kept the cookie but showed a login prompt on every profile for 75 minutes. Nobody outside Instagram
can say which signal does it. What we control is how much and how evenly the login is used, so this caps both:

  * SINCE    uses of an older login do not count against the current one. The recording time is read off the saved login
             (the sessionid cookie's expiry minus 365 days), so a login recorded again starts with an empty budget by itself;
             the repository variable IG_LOGIN_SINCE (YYYY-MM-DDTHH:MM:SSZ) can push it later;
  * REST     the login is not started again until IG_REST_MINUTES (default 90) after the last run that used it ended;
  * BUDGET   across any rolling 24 hours the runs that used it add up to less than IG_DAILY_MINUTES (default 200: two full
             100-minute sweeps, or one sweep and the daily account check and a short hashtag run).

"Runs that used it" = the sweep (scraper.yml, hashtag runs included), the account check, and the hashtag probe: the workflows
that share the concurrency group kkm-complaint-scraper. A run shorter than 3 minutes is ignored (a selftest, or a run this very
guard skipped, which must not restart the rest).

The daily account check (REST=off) is held only to the budget, not the rest: it follows the daily sweep by design and only
opens profiles, so a rest rule would skip it every night.

Scheduled runs that fail the check are skipped. A manual run that fails it is skipped too, with the time it may start, unless
`ignore_rest` is ticked, so a person can always override and a chain of automatic dispatches cannot wear the login out.
IG_REST_GUARD=off (repository variable) turns the whole check off.

Writes ok=true|false and reason to $GITHUB_OUTPUT. Always exits 0.
Env: GH_TOKEN, REPO, EVENT, FORCE, BYPASS, REST (off = no rest gap), IG_LOGIN_SINCE, PW_STORAGE_STATE_IG_B64, PW_STORAGE_STATE_B64, IG_REST_MINUTES, IG_DAILY_MINUTES, IG_REST_GUARD
"""
import base64
import gzip
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

WORKFLOWS = ("scraper.yml", "account-check.yml", "ig-hashtag-probe.yml")
MIN_RUN_MINUTES = 3


def parse(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def used_intervals(runs, now, since=None):
    """[(start, end)] of the finished runs that really used the login in the last 24 hours.
    `since` (IG_LOGIN_SINCE): when the current login was recorded. Use on an older login is no use of this one, so it is not counted."""
    out = []
    for r in runs:
        if r.get("status") != "completed" or not r.get("run_started_at") or not r.get("updated_at"):
            continue
        s, e = parse(r["run_started_at"]), parse(r["updated_at"])
        if (e - s) < timedelta(minutes=MIN_RUN_MINUTES) or e < now - timedelta(hours=24):
            continue
        if since and e <= since:
            continue
        out.append((max(s, now - timedelta(hours=24), since or s), e))
    return sorted(out)


def login_recorded_at(*states_b64, now=None):
    """
    When the current Instagram login was recorded, read off the saved login itself so nobody has to remember to say so: Instagram
    gives `sessionid` a one-year cookie, so (its expiry - 365 days) is the moment it was issued (the status strip read 365.0 days
    left minutes after the 26 Nov 2026 recording). Takes the newest over the secrets given; None if none can be read.
    """
    now = now or datetime.now(timezone.utc)
    best = None
    for b64 in states_b64:
        try:
            raw = base64.b64decode((b64 or "").strip())
            raw = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
            cookies = json.loads(raw).get("cookies", []) or []
        except Exception:
            continue
        for c in cookies:
            if c.get("name") == "sessionid" and c.get("value") and "instagram.com" in str(c.get("domain", "")):
                exp = c.get("expires")
                if isinstance(exp, (int, float)) and exp > 0:
                    t = datetime.fromtimestamp(exp, timezone.utc) - timedelta(days=365)
                    if t <= now and (best is None or t > best):
                        best = t.replace(microsecond=0)
    return best


def decide(intervals, now, rest_min=90, daily_min=200, event="schedule", force=False, bypass=False, guard=True, rest=True):
    """dict(ok, reason, allowed_at). `intervals` is used_intervals()."""
    if not guard:
        return {"ok": True, "reason": "login rest guard is off (IG_REST_GUARD=off)", "allowed_at": None}
    if bypass:
        return {"ok": True, "reason": "this run does not use the login (selftest)", "allowed_at": None}
    last_end = max((e for _, e in intervals), default=None)
    total = sum((e - s).total_seconds() for s, e in intervals) / 60.0
    rest_ok_at = last_end + timedelta(minutes=rest_min) if last_end else None
    why, allowed = [], None
    if rest and rest_ok_at and now < rest_ok_at:
        why.append(f"the login's last use ended {int((now - last_end).total_seconds() // 60)} min ago and it rests "
                   f"{rest_min} min between runs")
        allowed = rest_ok_at
    if total >= daily_min:
        # the earliest moment the rolling 24 hours drop back under the budget
        t, acc = None, total
        for s, e in intervals:
            acc -= (e - s).total_seconds() / 60.0
            if acc < daily_min:
                t = e + timedelta(hours=24)
                break
        why.append(f"the login was used {int(total)} min in the last 24 h (budget {daily_min})")
        allowed = max(allowed, t) if allowed and t else (t or allowed)
    if not why:
        return {"ok": True, "reason": f"rested: {int(total)} of {daily_min} min used in the last 24 h", "allowed_at": None}
    reason = "; ".join(why) + (f". It may run again after {allowed:%Y-%m-%d %H:%MZ}." if allowed else ".")
    if force:
        return {"ok": True, "reason": "ignore_rest ticked, running anyway: " + reason, "allowed_at": allowed}
    return {"ok": False, "reason": ("skipped, " if event == "schedule" else "not started, ") + reason +
            ("" if event == "schedule" else " Tick ignore_rest to override."), "allowed_at": allowed}


def fetch_runs(repo, token, now):
    since = (now - timedelta(hours=26)).strftime("%Y-%m-%d")
    runs = []
    for wf in WORKFLOWS:
        url = f"https://api.github.com/repos/{repo}/actions/workflows/{wf}/runs?status=completed&created=%3E%3D{since}&per_page=100"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            runs += json.load(r).get("workflow_runs", [])
    return runs


def main():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    env = os.environ.get
    guard = (env("IG_REST_GUARD") or "on").strip().lower() != "off"
    num = lambda k, d: int(float(env(k) or d))
    try:
        since = login_recorded_at(env("PW_STORAGE_STATE_IG_B64"), env("PW_STORAGE_STATE_B64"), now=now)
        if (env("IG_LOGIN_SINCE") or "").strip():
            try:
                since = max(filter(None, [since, parse(env("IG_LOGIN_SINCE").strip())]))
            except ValueError:
                print("IG_LOGIN_SINCE is not YYYY-MM-DDTHH:MM:SSZ; ignored")
        intervals = used_intervals(fetch_runs(env("REPO"), env("GH_TOKEN"), now), now, since) if guard else []
        res = decide(intervals, now, num("IG_REST_MINUTES", 90), num("IG_DAILY_MINUTES", 200), env("EVENT") or "schedule",
                     (env("FORCE") or "").lower() == "true", bool((env("BYPASS") or "").strip()), guard,
                     (env("REST") or "on").strip().lower() != "off")
    except Exception as e:      # a broken check must never stop the sweep
        res = {"ok": True, "reason": f"the rest check could not run ({type(e).__name__}); running", "allowed_at": None}
    print(res["reason"])
    print(f"::{'notice' if res['ok'] else 'warning'}::login rest: {res['reason']}")
    if env("GITHUB_OUTPUT"):
        with open(env("GITHUB_OUTPUT"), "a") as f:
            f.write(f"ok={'true' if res['ok'] else 'false'}\nreason={res['reason']}\n")


if __name__ == "__main__":
    main()
