#!/usr/bin/env python3
"""
Links tab plumbing for the scheduled TikTok link review (Wan, 5 Oct 2026).

  gather   Claim the waiting TikTok links (Apps Script `links_pending`, one locked step, so two ticks can
           never take the same row) and print them as GitHub step outputs:  count=N  links=<space separated>
           A manual dispatch that carries its own links (env INPUT_LINKS) uses those and claims nothing.
  fail     The review step died. Mark this run's links as an error so the dashboard says so at once
           (env LINKS = the links, space separated) instead of waiting for the 90-minute stale rule.

Only the standard library plus `requests` (already on the runner). Reads APPS_SCRIPT_WEBHOOK_URL and
APPS_SCRIPT_API_TOKEN. Writes nothing but the Links tab's Status / Run / Result columns, through Links.gs.
"""
from __future__ import annotations

import os
import sys

import requests


class Refused(Exception):
    pass


def call(payload: dict) -> dict:
    url, token = os.environ["APPS_SCRIPT_WEBHOOK_URL"], os.environ["APPS_SCRIPT_API_TOKEN"]
    r = requests.post(url, json=dict(payload, token=token), timeout=120, allow_redirects=True)
    try:
        data = r.json()
    except ValueError:
        sys.exit(f"webhook did not return JSON ({r.status_code}); first bytes: {(r.text or '')[:160]!r}")
    if not data.get("ok"):
        raise Refused(str(data.get("error")))
    return data


def emit(**kv) -> None:
    line = "\n".join(f"{k}={v}" for k, v in kv.items())
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    print(line)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "gather"
    if mode == "gather":
        given = " ".join((os.environ.get("INPUT_LINKS") or "").split())
        if given:
            emit(count=len(given.split()), links=given, claimed="false")
            return 0
        try:
            d = call({"action": "links_pending"})
        except Refused as e:
            if "Unknown action" in str(e):
                # The deployed Apps Script predates Links.gs. Not a failure of this run: say so and stand by,
                # so a scheduled tick does not fail every fifteen minutes until it is redeployed.
                print("::notice::the deployed Apps Script has no Links support yet (Links.gs + a new version); nothing to do")
                emit(count=0, links="", claimed="false")
                return 0
            sys.exit(f"webhook refused links_pending: {e}")
        links = d.get("links") or []
        if d.get("released"):
            print(f"released {d['released']} row(s) a dead run left queued")
        emit(count=len(links), links=" ".join(links), claimed="true")
        return 0
    if mode == "fail":
        links = (os.environ.get("LINKS") or "").split()
        run = "#" + os.environ.get("GITHUB_RUN_NUMBER", "?")
        if links:
            call({"action": "links_update", "updates": [
                {"link": l, "status": "error", "run": run,
                 "result": "the review run failed before reporting; press Retry"} for l in links]})
        return 0
    sys.exit(f"unknown mode {mode!r}")


if __name__ == "__main__":
    raise SystemExit(main())
