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
- **Type** (optional column): blank or `Brand` for a company's own page. `Doctor` or `KOL` for a person who promotes products — see below.

**Adding a column is not free-form.** Every column that is not `Brand`, `Active`, `Type`, `Product hints` or `Notes` is read as a *platform*, and a platform with no entry in `scraper/config.yaml` is skipped with a warning. Add notes to the `Notes` column; ask me before adding any other column.

### The same post is never reviewed twice

The Complaints tab only holds posts that turned out non-compliant. Everything judged clean left no trace, so every week the same compliant posts were scraped, screenshotted and paid for again — and a complaint you had already dealt with could come back around.

A **Reviewed** tab now holds one row per post the scraper has judged, whatever the verdict: URL, brand, platform, verdict, confidence, reviewer, first seen. At the start of a run its URLs are merged into the skip list alongside the complaint URLs; at the end, everything judged this run is appended.

It is a checklist, not a findings record. Nothing in it is a complaint, and dismissing a complaint does not remove it — a dismissed post stays skipped, which is the point.

Three things worth knowing:

- **The ledger is written after the insert, never before.** A post is only marked reviewed once any complaint it produced has actually reached the sheet. A failed insert would otherwise bury the finding permanently.
- **A dry run writes nothing.** Rehearsals do not consume posts.
- **To re-review a post deliberately**, delete its row from the Reviewed tab. It will be picked up on the next run.

**To deploy it**, in the Apps Script editor:

1. **+** next to Files → **Script** → name it `ReviewedLedger`. Paste the contents of `apps-script/ReviewedLedger.gs` from the repo.
2. In `Code.gs`, find `case 'known_urls':` in the action switch and add these two cases directly under it:

   ```js
   case 'seen_urls':     if (!viewer) return deny;
                         return { ok: true, urls: seenUrls_() };
   case 'mark_seen':     if (!machine) return deny;
                         return markSeen_(body.entries || []);
   ```

3. **Deploy → Manage deployments → New version.** Saving alone changes nothing.

The `Reviewed` tab builds itself on first use; `setup()` does not need re-running. Until it is deployed the run logs `no reviewed-post ledger on this deployment` and carries on exactly as before, re-reviewing clean posts. Nothing breaks; it just keeps costing.

### Watching a doctor or KOL account

A cosmetic advertisement carried by a doctor is a Part 10 s.4.1 problem in itself, so these accounts are worth watching — but the row has to say what it is.

- **Brand** column: the person's name (`Dr Aina`, `Mek Yun`). It labels the sheet row and the complaint.
- **Type** column: `Doctor` or `KOL`.
- Handles: their own account, same as any other row.

With `Type` set, the reviewer is told the account is not the brand: the claims tables apply as usual, plus s.4.1 (doctor / dentist / pharmacist / dermatologist endorsement, or the impression of one — title, white coat, clinic setting, credentials in the bio — is unacceptable for a cosmetic, paid or not) and s.6 (a testimonial must be genuine). Leave `Type` blank for a brand and nothing changes.

#### What a doctor or KOL post is checked for

Any row whose **Type** is not blank, `Brand`, `own` or `Company` is judged as an advertisement carried by a person rather than a brand's own post. Three checkpoints run before any claims assessment, and the reviewer has to say which it found:

1. **Endorsement** — professional standing lent to a cosmetic: their own title or credentials in the post, a clinic setting, scrubs or a white coat, `Dr` in the handle or display name, or wording inviting trust because of who is saying it. Part 10 s.4.1, and it is a breach with or without a claim attached.
2. **Partnership** — a commercial arrangement: `#ad`, `#sponsored`, paid partnership, collab, affiliate or discount code, order link, "gifted", a brand tagged as partner. Recorded whether disclosed or not — an undisclosed one is the more serious finding, and a disclosed one still carries s.4.1 for a health professional.
3. **Product shown** — an identifiable cosmetic: packaging on camera, product held or applied, a legible name on the artwork, a product tag.

An endorsement or undisclosed partnership around an identifiable cosmetic is Unacceptable under s.4.1 **even when every individual claim would pass the Part 8 tables**. A post by such a person showing no identifiable product and making no claim is Acceptable — the prompt says so explicitly, so the reviewer is not pushed into reaching for a finding.

### How many targets fit in one run

Roughly **3 minutes per brand × platform**, most of it the deliberate pacing (section 5). Ten targets is about 30 minutes. The workflow stops at 180 minutes, so keep it under about 50 targets, or cut `max_posts_per_profile` / the `pause_*` ranges to fit more. Reviewer cost runs about USD 0.007 per post examined.

The scraper reads this tab at the start of every run. `scraper/config.yaml` is only a fallback used when the sheet cannot be reached.

### Getting a handle right

**You can paste either the bare handle or the full profile URL — the sheet now cleans it up for you.**

| Platform | You can paste | Becomes |
|---|---|---|
| Instagram | `instagram.com/eucerin_my` or `eucerin_my` | `eucerin_my` |
| Facebook | `facebook.com/EucerinMalaysia` or `EucerinMalaysia` | `EucerinMalaysia` |
| Threads | `threads.net/@qvskincare` or `qvskincare` | `qvskincare` |

Easiest: open the profile in your browser, copy the address bar, paste the whole thing into the cell. `sanitizeHandle_()` in `Code.gs` strips the domain, the `@`, the query string (`?hl=en`), and stray path segments (e.g. Instagram's `/popular/…` prefix) down to just the handle, before the scraper ever sees it. A handle with spaces in it is still a display name and will fail: Facebook's page *QV Skincare Malaysia* might live at `facebook.com/QVSkincareMY`, and only the URL tells you which.

If the URL shows a long number instead of a name, that numeric ID works too, paste it as the handle (or the full URL containing it).

**A wrong handle is not silent.** The run log says `no post links found — profile not available (handle wrong or region-blocked)` and saves a screenshot of what it saw into the run artefact.

### Adding a new brand and taking it live — the whole flow

1. Sheet → `Targets` tab → new row. Type the brand name in **Brand**, tick **Active**.
2. Paste a handle or the full profile URL into **Instagram** / **Facebook** / **Threads** — whichever the brand actually has. Leave the rest blank.
3. Optional: comma-separated product lines in **Product hints**, so the reviewer names the product correctly in the row.
4. That's it — **no code change, no redeploy.** The next run (scheduled or manual) reads the sheet fresh and picks the brand up automatically.
5. To check it immediately rather than waiting for Friday: GitHub → Actions → *KKM complaint scraper* → Run workflow → set **brand** to the exact name you typed → Run. Tick **dry_run** first if you just want to see what it would find without writing to the sheet.
6. Read the run's log/summary (section 8) for that brand's rows: `posts=N  ok` means the handle resolved and it looked at real posts; `profile not available` means the handle is wrong — open the profile in your own browser and re-copy the URL into the cell; `different account: post is by @X, not @Y` means the handle you configured isn't the account actually posting — find the real @handle and update the cell.

A **redeploy** (Apps Script → Deploy → Manage deployments → New version) is only needed when `Code.gs` or `Index.html` itself changes — never for adding, editing or pausing a brand, and never for a new platform column left unconfigured on the scraper side (section 3).

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

### "Value is too large"

A GitHub secret stops at 48 KB and a session with Facebook in it can exceed that. The bulk is `origins` — the localStorage cache Instagram and Facebook dump into the file. Auth lives in the cookies, so drop the rest:

```
$s = Get-Content storage_state.json -Raw | ConvertFrom-Json
$s.origins = @()
$s | ConvertTo-Json -Depth 20 -Compress | Set-Content storage_state.min.json -NoNewline
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("storage_state.min.json"))
$b64.Length
```

Under 48000 → `$b64 | Set-Clipboard` and paste. Still over, drop cookies for sites that are not watched, then repeat the four lines:

```
$s.cookies = $s.cookies | Where-Object { $_.domain -match 'instagram|facebook|threads' }
```

Or gzip it, which the scraper also accepts (roughly ten times smaller):

```
$in = [IO.File]::OpenRead("storage_state.json"); $out = [IO.File]::Create("storage_state.gz")
$gz = New-Object IO.Compression.GzipStream($out, [IO.Compression.CompressionMode]::Compress)
$in.CopyTo($gz); $gz.Close(); $out.Close(); $in.Close()
[Convert]::ToBase64String([IO.File]::ReadAllBytes("storage_state.gz")) | Set-Clipboard
```

Delete every copy afterwards: `Remove-Item storage_state.json, storage_state.min.json, storage_state.gz -ErrorAction SilentlyContinue`

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

### Pacing (how human the browsing looks)

`scraper/config.yaml`, section `run:`. Each is a `[min, max]` in seconds, waited at random.

| Setting | Default | Waited before |
|---|---|---|
| `pause_between_posts` | `[8, 25]` | each post page after the first |
| `pause_between_profiles` | `[45, 120]` | each brand/platform after the first |
| `pause_after_scroll` | `[1.5, 3.5]` | each scroll on a profile grid |

This is what keeps an account from being restricted: opening a dozen profiles in ninety
seconds reads as a bot. The waits are random, not fixed, because a constant interval is itself
a signature. Defaults turn a two-minute run into roughly fifteen, which does not matter for a
weekly overnight job. Set a pair to `[0, 0]` to disable it, for example when testing one brand
and you want the answer quickly.

If an account does get restricted, raising these will not lift the restriction on its own.
Warm the account first (section 4), then keep the pacing on so it does not happen again.

### Which model reviews

Set the repository **variable** `ANTHROPIC_MODEL` under Settings → Secrets and variables → Actions → Variables.

| Value | USD per reviewed post | When |
|---|---|---|
| `claude-haiku-4-5` | about 0.015 | Default. Cheapest; hedges more on borderline calls |
| `claude-sonnet-5` | about 0.031 | Better judgement on ambiguous claims |
| `claude-opus-5` | about 0.078 | A call you want to be sure of |

No code change, takes effect on the next run. Delete the variable to fall back to the default.
Figures assume no cache hit; a warm cache roughly halves the input side.

### Using another provider, or a gateway

Set the **variable** `LLM_PROVIDER` to `openai` and the scraper uses the OpenAI-compatible path instead. That path also speaks to any gateway with a `/v1/chat/completions` endpoint — rootsys, OpenRouter, a local server — which is how GLM, Kimi, DeepSeek and MiniMax are reached:

| Variable | Example |
|---|---|
| `LLM_PROVIDER` | `openai` |
| `OPENAI_BASE_URL` | `https://rootsys.cloud/v1` (leave unset for OpenAI itself) |
| `OPENAI_MODEL` | `kimi-k3`, `glm-5.3`, `deepseek-v4-pro`, `gpt-4.1` |

plus the secret `OPENAI_API_KEY` holding that gateway's key.

**Pick a model that can see images.** Most violations here are on the artwork, not in the caption. A text-only model still returns verdicts, so nothing looks broken — the run just stops catching before/after shots, white coats, "Dr" captions and on-image percentages. The log says so when it happens: `would not take the screenshot; judged on the caption alone`. Search the run log for that line before trusting a quiet week.

Gateways differ in what they accept. Strict `json_schema` falls back to `json_object`, then to a plain request, and a fenced ```` ```json ```` reply is still parsed, so a limited gateway degrades rather than failing the run.

**Cost reporting only covers Anthropic.** `MODEL_PRICES` has no entry for other providers, so the spend block reports tokens honestly and `USD 0.0000` — which is correct for a prepaid gateway plan, and wrong for metered billing. Watch the provider's own dashboard for those.

There is no failover between providers. A dead key or a refusing gateway drops the post to the **regex rules only** (`reviewer=rules-only` in Remarks), which catches blatant prohibited wording and misses every judgement call.

### When the reviewer cites wording that was never said

Found 17 Sep 2026. A Threads post — a pink-office meme captioned *"when they ask me what i actually did during my 9 - 5 shift"*, nothing else — came back **Unacceptable (0.85), "sunburn/burn healing"**. There is no such wording anywhere on the post. The reviewer invented it.

This is a different failure from the screenshot bugs above. Those made the reviewer look at the wrong thing. This one shows it can look at the right thing and still state something false, confidently, as if quoting the post. A high confidence number does not mean the citation is real.

**Every Risky or Unacceptable verdict that quotes specific wording is now checked against the post's own scraped caption.** If the quoted phrase appears nowhere in that text, the row is held back — never pushed to the sheet — and the run summary shows it plainly:

```
pushed: 0   skipped: 12   flagged: 1   errors: 1
  ⚠ HOLD Unacceptable (0.85) Treatment claim — needs visual verification before filing - see GUIDE.md
      [VERIFY before filing: quoted wording not found in the scraped caption ("sunburn and burn
      healing") - confirm against the screenshot before this reaches the sheet] The post claims...
```

**What this catches:** a fabricated quote — wording attributed to the post's text that the scrape never captured. **What it cannot catch:** a fabricated claim read off the *image* rather than the caption, since there is no caption text to check it against. A flagged row still needs your eyes before it becomes a sheet entry; an unflagged Risky or Unacceptable is not thereby proven true — it only means nothing in its own citation contradicted itself. Open the run's artifact and look at the screenshot before treating any high-confidence verdict as settled, flagged or not.

### Checking a reviewer before you trust it

A model that agrees with Haiku on five compliant posts has proved nothing — compliant posts are easy and everything agrees on them. What tells you whether a reviewer is safe to run is where it *disagrees*, and whether you side with it.

Run it: **Actions → Run workflow**, tick **compare**. Every post is put to both reviewers — the same caption, the same screenshot, in the same run — and the summary prints the splits:

```
  --- reviewer comparison ---   anthropic:claude-haiku-4-5  vs  openai:kimi-k2.7
  10 post(s) reviewed by both; agreed on 9, split on 1
  raw agreement 90%  (agreement on compliant posts is cheap; the splits below are what matters)
    ! SPLIT  The Raw / Threads  https://www.threads.net/@theraw.skin/post/...
        anthropic:claude-haiku-4-5: Acceptable (0.95) -
        openai:kimi-k2.7: Risky (0.8) Superlative and comparative claims without substantiation
```

A comparison run never pushes — `--compare` forces `--dry-run`, because neither verdict has been adjudicated yet. It costs two reviews per post, so run it on one or two brands, not the whole list.

**Read the splits yourself and decide who was right.** That is the only measure of accuracy that means anything here, and the call is yours as the safety assessor — the two reviewers do not settle each other. A model that catches what the other missed is worth keeping; one that flags sale announcements as claims will bury you in false positives; one that clears a real Part 8 breach is disqualified regardless of how cheap it is.

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
| `login wall on post page - not reviewed` | The platform served its gate instead of the post. The post is **not** reviewed and the run counts it as an error, so it will never be recorded as compliant | Refresh the session (section 4). Until then that post is unseen, not clean |
| `no post links found — profile not available` | Handle wrong, or the platform is blocking the runner | Check the handle in your browser (section 2); if it loads for you, run from your PC instead (section 7) |
| `no post links found — platform soft-block` | Too much traffic from that address | Wait a few hours, or run from your PC |
| `retrying via mbasic…` then still nothing | Facebook handle wrong, or the page has no public posts | Open `mbasic.facebook.com/<handle>` in your browser and see what it shows |
| Instagram fails from GitHub but works in your browser | Instagram blocks datacenter addresses | Run from your own PC (section 7); GitHub cannot fix this |
| A row's Post URL is on an account you didn't configure (e.g. a global/US handle you never set) | Instagram's profile page can surface "Suggested for you" or related-account content; the scraper now checks the actual poster of each post against the Targets handle and skips a mismatch, but a row filed before that check went in is real content from the wrong account | Dismiss the row — it is not the Malaysian brand's own post |
| `posted <date>, outside on/after 2025-09-01` | Working as intended | Widen `min_post_date` if you want older posts |
| `already in sheet` | Working as intended | Nothing |
| A Targets handle cell held a full profile URL and the run log showed a doubled address (`instagram.com/https://www.instagram.com/...`) | Fixed 16 Sep 2026: `sanitizeHandle_()` now strips a pasted URL down to the handle before the scraper ever sees it | Nothing to do — paste bare handles or full URLs, both work now |
| `different account: post is by @X, not @Y` | The configured handle isn't the account actually posting anymore (brand changed handles, or it was never right) | Find the real @handle in your browser and update the Targets cell |
| `Failed to launch chromium because executable doesn't exist at # optional: …` | A `.env` line has a comment after the value, and the comment was read as the value | Put comments on their own line. Blank means "not set". |
| `PW_STORAGE_STATE_B64 invalid … codec can't decode` | Same cause as above | Same fix; leave it blank on Windows and keep `storage_state.json` beside `main.py` |
| `No time zone found with key Asia/Kuala_Lumpur` (Windows) | The `tzdata` package is missing from `.venv` | In the scraper folder: `.venv\Scripts\activate` then `pip install tzdata`, or re-run `setup_windows.bat` |
| `LLM review failed … falling back to rules` | API key missing, out of credit, or a transient error | Check credit at console.anthropic.com; the run still completes on the regex rules alone |
| Dashboard says `Unauthorised` | Wrong dashboard key, or Apps Script not redeployed after a code change | Re-enter the key; in Apps Script, Deploy → Manage deployments → New version |
| Dashboard says non-JSON response | Apps Script deployment is old or not set to *Anyone* | Redeploy as Web app, Execute as Me, Access Anyone |
| `webhook did not return JSON (200): the deployment is asking for a Google sign-in…` | The run now reads the page Apps Script served and names the cause; the message carries the fix | Follow what the message says — it is one of the five causes below |
| `APPS_SCRIPT_WEBHOOK_URL does not look like a deployed Web app URL` | The secret holds an editor, `/dev` or shortened URL | Deploy → Manage deployments → copy the **Web app** URL; it ends in `/exec` |
| `browser session has expired (N cookies, none still valid)` | The recorded login is dead; every profile will hit a wall and report 0 posts | Re-record it, section 4 |
| `browser session for facebook.com expires in 2.3 day(s)` | Working as intended — early warning | Re-record before it lapses |
| `browser session carries no dated cookie for facebook.com` | That platform was not logged in when the session was recorded | Re-record with all three platforms signed in |

### A verdict is only worth what the screenshot showed

Found 17 Sep 2026, on The Raw's Facebook posts. Facebook served a *"Continue as <name>"* interstitial — no password box, the post URL unchanged — so the old wall check, which looked for a password field or a `/login` address, saw nothing wrong. The gate was screenshotted and sent for review. The reviewer answered honestly: there is no cosmetic claim on a login page, so **Acceptable**. Four Facebook posts passed without anyone, human or model, ever seeing them.

Two things changed:

- The wall check no longer relies on a password box. It reads the page text for a platform's gate wording, but only when the post itself is absent from the page, so a dismissible login prompt floating over a readable post still counts as readable.
- A post behind a wall is now marked `blocked`: it never reaches the reviewer, and it is counted in the run's **errors**, not quietly among the skips. A platform we cannot see is a gap in the sweep, not a pass.

The screenshot is still saved to the artifact so you can see for yourself what the runner was served.

### Why a verdict used to move between runs

Found 17 Sep 2026. The same Threads post came back **Risky (0.80)** at 14:48 and **Acceptable (0.95)** at 15:42, same model, same settings. The model was not the problem — the picture was.

A post page renders the thread around the post: reposts, replies, suggested content. That changes between loads. The screenshot captured the viewport, so each run handed the reviewer a slightly different image, and one run read a neighbouring perfume promotion and judged The Raw on it.

The screenshot is now taken of the post's own container (`article` on Instagram, `[role='article']` on Facebook, the pressable container on Threads), falling back to the viewport only when none of them is found. A very long thread is clipped to 2,400px from the top of the post rather than captured whole. The run log says which it used:

```
[The Raw/Threads] screenshot: post element div[data-pressable-container='true'] (700x900)
[The Raw/Facebook] screenshot: viewport (no post element matched)
```

The container is matched by the post's own id, read off the URL we asked for — `DdYbeTBlLb7` on Threads, the `/p/` code on Instagram, the `pfbid` on Facebook. A Threads page renders the whole thread as pressable containers, and taking the first one meant one URL came back describing three different captions across three runs: it was framing whichever post rendered first, not the one we came for.

So the log now says how sure it is:

```
[The Raw/Threads] screenshot: post element div[data-pressable-container='true'] anchored to DdYbeTBlLb7 (638x551)
[The Raw/Instagram] screenshot: post element main[role='main'] (could not anchor to Bw4wNC4Bj_l) (1280x1686)
[The Raw/Facebook] screenshot: viewport (no post element matched)
```

- **anchored to `<id>`** — the frame provably holds the post being reviewed. Trust the verdict.
- **could not anchor** — the frame is the page's main column: it contains the right post, plus its neighbours. The verdict is about the right post but may be coloured by what sits beside it.
- **viewport** — the old behaviour, and the selector for that platform needs updating.

When a post id is known, an unanchored narrow container is never used: framing a different account's post and filing the verdict under this URL is worse than including a neighbour.

### When the webhook returns a web page instead of JSON

Apps Script answers a misconfigured deployment with HTML, and the run used to die on it thirteen seconds in with `Expecting value: line 1 column 1 (char 0)` — true, and useless. The run now reads that page and names which of these it is:

| What the page says | What it means | The fix |
|---|---|---|
| Sign-in page, or redirected to `accounts.google.com` | Access is not *Anyone* | Deploy → Manage deployments → edit → **Who has access: Anyone** |
| "Sorry, unable to open the file" / "Page not found" | The `/exec` URL no longer resolves to a deployment | Deploy → Manage deployments → copy the current **Web app URL** into `APPS_SCRIPT_WEBHOOK_URL` |
| "Script function not found" | Not deployed as a Web app | Deploy → **New deployment** → type **Web app** |
| "Authorization is required" | Scopes changed and the script was never re-authorised | Open the script, run any function once, accept the permissions |
| Any other HTML | The deployment is stale | Deploy → Manage deployments → **New version** |

The URL itself is also checked at startup: it should look like `https://script.google.com/macros/s/<id>/exec`. A `/dev` URL, an editor URL or a shortened link warns before the run spends fifteen minutes scraping.

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

**This guide, on the dashboard itself**: click **FAQ** in the dashboard header, or open the Apps Script `/exec` URL with `?view=guide`. It asks for a passcode: `GUIDE_PASSCODE` in Script properties, falling back to `DASHBOARD_KEY` when that is not set. Typed once per browser, then remembered.

A Google-account check is not possible on that route. A web app deployed *Execute as Me / Access Anyone* is never told who is viewing — `Session.getActiveUser().getEmail()` comes back empty for everyone, including you — so a passcode is the gate that actually works.

---

## 10. What this system does not do

Being clear about this matters for how you use the output.

- **It screens, it does not decide.** Every row is a first-pass reading. The regulatory verdict and the decision to file are yours.
- **It reads what is public.** It does not log into brand accounts, read private posts, or touch anything behind a paywall.
- **It cannot verify a notification number.** QUEST3+ has no API here. That lookup is manual, by design, because a wrong NOT number in a complaint is worse than a blank one.
- **It does not judge ingredients.** Annex II to VII questions need the live PDFs, which change often. This system covers Part 8 claims and Part 10 presentation only.
- **It does not submit to KKM.** The form is filled by you. Nothing is sent to a regulator automatically, and that is deliberate.
