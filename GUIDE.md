# KKM Cosmetic Complaint System — Operating Guide

Everything you do week to week, and everything you change without touching code.

| I want to… | Where | Section |
|---|---|---|
| Work through this week's findings | Dashboard | [1](#1-the-weekly-routine) |
| Add or remove a brand | Google Sheet, `Targets` tab | [2](#2-brands-and-handles) |
| Fix a handle that stopped working | Google Sheet, `Targets` tab | [2](#2-brands-and-handles) |
| Add a platform (TikTok, YouTube…) | Sheet + repo, needs me | [3](#3-adding-a-platform) |
| Fix "login wall" errors | Your PC, Option A | [4](#4-refreshing-the-login-session) |
| Change how far back it reviews | `scraper/config.yaml` | [5](#5-tuning-the-review) |
| Change which Claude model reviews | GitHub variable | [5](#5-tuning-the-review) |
| Skip a Friday, or change the time | `.github/workflows/scraper.yml` | [6](#6-the-schedule) |
| Run it right now | GitHub Actions | [7](#7-running-on-demand) |
| Work out why nothing was found | Run log + artefact | [8](#8-troubleshooting) |

**The three places this system lives**

| Piece | Address |
|---|---|
| Dashboard | https://aduan.kkmhalalconsultant.com |
| Database (Google Sheet) | `Complaints`, `Targets` and `Lookups` tabs |
| Code and schedule | https://github.com/wanshah07/kkm-complaints |

---

## 1. The weekly routine

The scraper runs Friday 23:30 MYT. Saturday morning, open the dashboard.

1. **New** rows are this week's findings. Click one. It moves to **In-Progress** automatically, so you never lose your place.
2. Read the **Violation Reason**. It quotes each failing claim and cites the Annex I part and clause. This is the reviewer's work, not yours to redo, but it is a screen and not a verdict.
3. Check the **screenshot** in the drawer. Most violations here are on the artwork, not in the caption. Confirm the claim is really on screen.
4. **Verify the notification number.** Click *Semak QUEST3+*, search the product, copy the NOT number into **Nombor Notifikasi**. The scraper never guesses this.
5. Edit **Deskripsi Aduan** if you want to sharpen it. It is written in BM and ready to paste. Replace any `[SAHKAN: …]` marker before filing.
6. Click **Copy Deskripsi**, then **Open KKM form**, and submit.
7. Back in the dashboard, click **Mark Complete**. Today's date is stamped into **Tarikh Melapor**.

**Dismiss** is for false positives. The row stays for the record but stops showing as New, and its URL is remembered so it never returns.

**Verdicts you will see**

| Verdict | What it means | Typical action |
|---|---|---|
| Unacceptable | Matches a prohibited claim or presentation | File it |
| `Risky: …` | Depends on substantiation or context | Judge it; often file, sometimes dismiss |

Risky rows only appear because `push_risky` is on. Turn it off in `scraper/config.yaml` if they become noise.

---

## 2. Brands and handles

**All of this is the `Targets` tab in your sheet. Nothing else, no code, no redeploy.**

| Brand | Active | Instagram | Facebook | Threads | Product hints | Notes |
|---|---|---|---|---|---|---|

- **Add a brand**: new row, brand name, tick **Active**, fill the handles you want watched.
- **Pause a brand**: untick **Active**. The row stays, the scraper skips it.
- **Remove a platform for one brand**: clear that cell. Blank is skipped cleanly.
- **Product hints**: comma-separated product lines. Only helps the reviewer name the product in the row. Optional.

The scraper reads this tab at the start of every run. `scraper/config.yaml` is only a fallback used when the sheet cannot be reached.

### Getting a handle right

**The handle is the part of the profile URL after the slash, not the page's display name.**

| Platform | Profile URL | Handle to enter |
|---|---|---|
| Instagram | `instagram.com/eucerin_my` | `eucerin_my` |
| Facebook | `facebook.com/EucerinMalaysia` | `EucerinMalaysia` |
| Threads | `threads.net/@qvskincare` | `qvskincare` |

No `@`, no spaces, no `https://`. **A handle with spaces in it is a display name and will fail**: Facebook's page *QV Skincare Malaysia* might live at `facebook.com/QVSkincareMY`, and only the URL tells you which.

To check one: open the profile in your browser and read the address bar. If the URL shows a long number instead of a name, that numeric ID works too, paste it as the handle.

**A wrong handle is not silent.** The run log says `no post links found — profile not available (handle wrong or region-blocked)` and saves a screenshot of what it saw into the run artefact.

---

## 3. Adding a platform

Instagram, Facebook and Threads work today. Anything else is a three-part job:

1. **You**: add a column to the `Targets` tab with the platform's name, and fill in handles.
2. **Me**: add an entry to `scraper/config.yaml` describing that platform's profile URL shape and how its post links look.
3. **You**: log into that platform during the next session recording (section 4), otherwise it hits a login wall.

Ask me and I do part two in a few minutes. A column added without part two is ignored with a warning in the log, so nothing breaks.

---

## 4. Refreshing the login session

Instagram, Facebook and Threads hide most content from visitors who are not logged in. The scraper carries a recorded browser session to get past that. It expires every few weeks; you will know because the run log fills with `login wall`.

On your Windows PC, in PowerShell:

```
py -m playwright codegen --save-storage=storage_state.json https://www.instagram.com/
```

A browser window opens. In that one window, in order:

1. Log into **Instagram** with the throwaway account.
2. Address bar → `facebook.com` → log in → wait for the feed.
3. Address bar → `threads.net` → log in → wait for it to load.
4. Visit each brand profile once, so the session looks used.
5. **Close the window.** Closing is what writes the file.

Then convert and upload:

```
[Convert]::ToBase64String([IO.File]::ReadAllBytes("storage_state.json")) | Set-Clipboard
```

GitHub → repo → **Settings → Secrets and variables → Actions** → `PW_STORAGE_STATE_B64` → **Update** → Ctrl+V → save. Finally:

```
Remove-Item storage_state.json
```

That file is a live login. Do not leave it on the desktop and do not email it.

**Use a throwaway account, never your own.** Automated browsing can get an account rate-limited or locked.

---

## 5. Tuning the review

### Date range, thresholds, volume

`scraper/config.yaml`, section `run:`

| Setting | Now | What it does |
|---|---|---|
| `min_post_date` | `2025-09-01` | Only posts published on or after this date are reviewed |
| `lookback_days` | `0` (off) | Rolling alternative: only the last N days |
| `max_posts_per_profile` | `6` | Newest posts examined per brand per platform |
| `push_risky` | `true` | Risky verdicts go to the sheet too |
| `min_confidence` | `0.6` | Unacceptable verdicts below this are dropped |

Posts whose date cannot be read anywhere always pass the date filter. Skipping them would create blind spots.

### Which model reviews

Set the repository **variable** `ANTHROPIC_MODEL` under Settings → Secrets and variables → Actions → Variables.

| Value | USD per reviewed post | When |
|---|---|---|
| `claude-haiku-4-5` | about 0.015 | Default. Cheapest; hedges more on borderline calls |
| `claude-sonnet-5` | about 0.031 | Better judgement on ambiguous claims |
| `claude-opus-5` | about 0.078 | A call you want to be sure of |

No code change, takes effect on the next run. Delete the variable to fall back to the default.
Figures assume no cache hit; a warm cache roughly halves the input side.

### Knowing what it actually cost

Every run ends with a **reviewer spend** block in the log and a `spend` object in `run_report.json`:

```
  --- reviewer spend ---
  calls         6   (claude-haiku-4-5x6)
  input tokens  4,100   (+41,000 cached read, 8,200 cache write)
  output tokens 9,300
  cost          USD 0.0930   (USD 0.0155 per reviewed post)
```

Set `usd_to_myr` in `scraper/config.yaml` to your card's rate and the line also shows RM. Left at
`0` it reports USD only, because a made-up exchange rate is worse than none. Posts that never
reach the model, because the regex pre-screen found nothing, cost zero and are not counted.

### The rules themselves

`scraper/npra_rules.py` holds the regex pre-screen and the rulebook handed to the reviewer, both citing Annex I Part 8 and Part 10. Changing these is my side. Tell me what NPRA changed and I update it.

---

## 6. The schedule

`.github/workflows/scraper.yml`

- **Time**: `cron: '30 15 * * 5'` is UTC, which is Friday 23:30 MYT. GitHub only accepts UTC, so subtract 8 hours from the Malaysian time you want.
- **Skip a week**: add the date to `SKIP_DATES` in the *Skip suppressed scheduled dates* step, space-separated, `YYYY-MM-DD`, Malaysian date. Manual runs are never affected. Entries expire by themselves.
- **Dormancy**: GitHub pauses schedules in a repository with no commits for 60 days. Any push, or one manual run, re-arms it.

---

## 7. Running on demand

GitHub → repo → **Actions** → *KKM complaint scraper* → **Run workflow**.

| Input | Use |
|---|---|
| `dry_run` | Tick to scrape and review but write nothing to the sheet. Results appear in the log and artefact. |
| `brand` | One brand only, exact name from the `Targets` tab |
| `platform` | `Instagram`, `Facebook` or `Threads` |

Running twice never duplicates rows. Dedupe is by post URL, normalised so tracking parameters do not fool it.

**From your own PC** instead, in the `scraper` folder: `run_windows.bat --dry-run`, or `run_windows.bat` for a live run. Your home connection is treated more kindly by Instagram than GitHub's servers are, so this is the more reliable route if datacenter blocks persist.

---

## 8. Troubleshooting

Open the run: GitHub → **Actions** → click the run. The summary at the end of the *Run scraper* step lists every brand and platform. The **Artifacts** section holds the full report, screenshots, and a picture of any profile that came back empty.

| Log says | Cause | Fix |
|---|---|---|
| `login wall` | Session expired or that platform was not logged in during recording | Section 4 |
| `no post links found — profile not available` | Handle wrong, or the platform is blocking the runner | Check the handle in your browser (section 2); if it loads for you, run from your PC instead (section 7) |
| `no post links found — platform soft-block` | Too much traffic from that address | Wait a few hours, or run from your PC |
| `retrying via mbasic…` then still nothing | Facebook handle wrong, or the page has no public posts | Open `mbasic.facebook.com/<handle>` in your browser and see what it shows |
| Instagram fails from GitHub but works in your browser | Instagram blocks datacenter addresses | Run from your own PC (section 7); GitHub cannot fix this |
| `posted <date>, outside on/after 2025-09-01` | Working as intended | Widen `min_post_date` if you want older posts |
| `already in sheet` | Working as intended | Nothing |
| `LLM review failed … falling back to rules` | API key missing, out of credit, or a transient error | Check credit at console.anthropic.com; the run still completes on the regex rules alone |
| Dashboard says `Unauthorised` | Wrong dashboard key, or Apps Script not redeployed after a code change | Re-enter the key; in Apps Script, Deploy → Manage deployments → New version |
| Dashboard says non-JSON response | Apps Script deployment is old or not set to *Anyone* | Redeploy as Web app, Execute as Me, Access Anyone |

**Nothing found is a normal result.** Most weeks compliant brands produce nothing. An empty run with all profiles reporting `ok` means the system worked.

---

## 9. Keys and access

| Name | Where it lives | What it is |
|---|---|---|
| `DASHBOARD_KEY` | Apps Script → Project Settings → Script properties | Opens the dashboard. Paste once per browser. |
| `API_TOKEN` | Same place | Lets the scraper write to the sheet. Rotate from the sheet's *KKM Complaints* menu. |
| `APPS_SCRIPT_WEBHOOK_URL` | GitHub secret | Your `/exec` address |
| `APPS_SCRIPT_API_TOKEN` | GitHub secret | Copy of `API_TOKEN` |
| `ANTHROPIC_API_KEY` | GitHub secret | Billing at console.anthropic.com |
| `PW_STORAGE_STATE_B64` | GitHub secret | The recorded browser session |

The dashboard key is not a password to an account. It gates the page; the sheet itself is protected by your Google login.

---

## 10. What this system does not do

Being clear about this matters for how you use the output.

- **It screens, it does not decide.** Every row is a first-pass reading. The regulatory verdict and the decision to file are yours.
- **It reads what is public.** It does not log into brand accounts, read private posts, or touch anything behind a paywall.
- **It cannot verify a notification number.** QUEST3+ has no API here. That lookup is manual, by design, because a wrong NOT number in a complaint is worse than a blank one.
- **It does not judge ingredients.** Annex II to VII questions need the live PDFs, which change often. This system covers Part 8 claims and Part 10 presentation only.
- **It does not submit to KKM.** The form is filled by you. Nothing is sent to a regulator automatically, and that is deliberate.
