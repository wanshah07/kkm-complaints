# KKM Cosmetic Complaint System

Weekly post-market surveillance of cosmetic brand social accounts, reviewed against
NPRA **Annex I Part 8** (Guideline for Cosmetic Claims) and **Part 10** (Guideline for
Cosmetic Advertisement), with every non-compliant post landed in a Google Sheet that
mirrors KKM's *Pelaporan Aduan Kosmetik Bernotifikasi* form, ready to submit.

```
                      Friday 23:30 MYT (GitHub Actions cron '30 15 * * 5')
                                        │
   config.yaml ──▶ scraper/main.py ── Playwright ──▶ Instagram · Facebook · Threads
   (brands, handles)      │                            (profile → newest N posts → text + screenshot)
                          │
                          ├─ npra_rules.py   regex pre-screen (EN + BM), citations
                          ├─ evaluator.py    Claude (claude-opus-5) as NPRA Claims Reviewer, JSON verdict
                          │                  reads the screenshot too — on-image claims count
                          └─ uploader.py     JPEG-compress → POST JSON to the Apps Script webhook
                                        │
                        apps-script/Code.gs  (doPost) ── dedupe by Post URL ── Drive folder for screenshots
                                        │
                        Google Sheet "Complaints"  ◀── apps-script/Index.html dashboard (doGet)
                              Status: New → In-Progress (opened) → Complete (submitted to KKM)
```

| Module | Path | What it is |
|---|---|---|
| A | `apps-script/Code.gs`, `appsscript.json` | Sheet initialiser, `doPost` webhook, `doGet` UI + JSON API, `api*` RPC for the dashboard |
| B | `apps-script/Index.html` | Tailwind dashboard: stats, filters, table → drawer (auto In-Progress), KKM form fields, manual entry with screenshot upload |
| C | `scraper/` | Playwright scraper, NPRA rule engine, LLM reviewer, Drive/webhook client |
| D | `.github/workflows/scraper.yml` | Friday 23:30 MYT cron, manual dispatch with dry-run / brand / platform |
| E | `web/index.html`, `.github/workflows/pages.yml` | The same dashboard as a static page on GitHub Pages under your own domain, talking to the Apps Script JSON API |

## Sheet columns (the contract)

`ID · Date · Brand · Platform · Post URL · Screenshot Link · Extracted Text · Violation Type ·
Violation Reason · Status · Remarks · Nama Kosmetik · Nombor Notifikasi · Jenis Aduan ·
Deskripsi Aduan · Tarikh Melapor · Source · Confidence · Created At · Updated At`

- `Post URL` is the dedupe key (normalised the same way in `Code.gs` and `scraper.py`).
- `Nombor Notifikasi` stays blank until you verify it on QUEST3+; the dashboard links there.
  The scraper never guesses a NOT number.
- `Deskripsi Aduan` is written in BM by the reviewer for Unacceptable verdicts and can be
  edited in the drawer, then copied straight into the form.
- `Tarikh Melapor` is stamped automatically when a row is marked **Complete**.
- A fourth status, **Dismissed**, exists for false positives so they stop appearing as New.
- A `Lookups` tab holds the Brand / Platform / Violation Type lists for the dashboard dropdowns
  and sheet validation.
- A **`Targets` tab is where you add a brand to monitor.** Columns: `Brand | Active | Instagram |
  Facebook | Threads | Product hints | Notes`. One row per brand, the handle (without @) under each
  platform, tick *Active*. The scraper reads this tab at the start of every run (`--targets auto`,
  the default) and falls back to `config.yaml` only when the sheet is unreachable. Adding a platform
  is a new column with the platform's name, plus a matching entry under `platforms:` in
  `config.yaml` so the scraper knows the profile URL shape.

## Deployment

### 1. Google Sheet + Apps Script (Module A + B)

1. Create a Google Sheet. **Extensions → Apps Script.**
2. Replace `Code.gs` with `apps-script/Code.gs`. **+ → HTML**, name it `Index`, paste
   `apps-script/Index.html`. **Project Settings → Show manifest**, replace `appsscript.json`.
3. Run `setup()` once from the editor (authorise Sheets + Drive). It creates the
   `Complaints` and `Lookups` tabs, validation, the Drive folder
   *KKM Complaint Screenshots*, and generates `API_TOKEN` and `DASHBOARD_KEY`.
   Read both in **Project Settings → Script properties** (or menu *KKM Complaints → Show…*
   after reloading the sheet).
4. Optional: set Script property `KKM_FORM_URL` to your KKM Google Form link; the dashboard
   shows an *Open KKM form* button (it also prompts for it once if empty).
5. **Deploy → New deployment → Web app.** Execute as **Me**, access **Anyone**. Copy the
   `/exec` URL.
6. Dashboard: `<exec URL>?key=<DASHBOARD_KEY>`. When you are signed in as the deploying
   account the key is optional.
7. Sanity check: run `smokeTest_()` in the editor. One test row appears, moves to
   In-Progress, and the stats log prints. Delete the row afterwards or Dismiss it.

Any code change needs **Deploy → Manage deployments → Edit → Version: New** to go live.

### 1b. Dashboard on your own domain (Module E)

`web/index.html` is the dashboard as a static page. It calls the Apps Script web app's
JSON API with `fetch()` (POST as `text/plain`, so no CORS preflight) and keeps the
`DASHBOARD_KEY` only in the browser's localStorage. Nothing secret is in the file.

1. Push to `main`. The *Deploy dashboard to GitHub Pages* workflow publishes `web/` and
   prints the `github.io` URL in the run summary.
2. Custom domain: add a repository **variable** `PAGES_CUSTOM_DOMAIN`
   (Settings → Secrets and variables → Actions → Variables), e.g.
   `aduan.kkmhalalconsultant.com`, then at your DNS provider add a **CNAME** record
   `aduan → wanshah07.github.io`. Re-run the Pages workflow. GitHub issues the HTTPS
   certificate within a few minutes of DNS propagating; tick *Enforce HTTPS* under
   Settings → Pages once it shows as verified.
3. Open the page, paste the web app `/exec` URL and the `DASHBOARD_KEY`, Connect.
   The ⚙ button reopens that screen; *Forget key* clears it from the browser.

The Apps Script-hosted page (`<exec URL>?key=…`) keeps working alongside it.

### 2. Scraper (Module C)

```bash
cd scraper
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium
cp .env.example .env   # fill in the webhook URL, API token and an LLM key
set -a; source .env; set +a

python main.py --dry-run                      # scrape + review, payload to out/<run>/payload_preview.json
python main.py --brand Eucerin --platform Threads
python main.py                                # full run, pushes to the sheet
python main.py --review-only caption.txt --brand "QV" --platform Facebook   # review a pasted caption
```

Brands come from the sheet's `Targets` tab (see above); `config.yaml` is the fallback and the place platforms are defined. `run.push_risky: true`
also pushes *Risky* verdicts (typed `Risky: …`). `run.min_confidence` drops low-confidence
Unacceptable calls.

**Login walls.** Instagram and Facebook often hide posts from anonymous browsers. Export a
storage state from a throwaway account and store it as a secret:

```bash
python -m playwright codegen --save-storage=storage_state.json https://www.instagram.com/
base64 -w0 storage_state.json    # → PW_STORAGE_STATE_B64
```

Threads is generally readable anonymously. When a wall is hit, the run logs it per target
and carries on with the rest.

**Screenshots.** Default `SCREENSHOT_MODE=apps_script`: the JPEG travels inside the webhook
payload and the Apps Script files it in Drive (no extra credentials). `drive_api` uploads
with a service account instead; `none` skips screenshots.

### 3. Cron (Module D)

GitHub Actions: add the secrets listed at the top of
`.github/workflows/scraper.yml` under **Settings → Secrets and variables →
Actions**. The schedule is `30 15 * * 5` (UTC) = Friday 23:30 MYT. Use *Run workflow* for
a manual or dry run. Each run uploads `out/` (report + screenshots) as an artefact for 30 days.

**Windows PC alternative (Option B, residential IP, your own logged-in browser session):**

1. Install Python 3.12 from python.org, ticking *Add python.exe to PATH*.
2. Download this repo (Code → Download ZIP) or `git clone`, open the `scraper` folder.
3. Double-click `setup_windows.bat` (creates `.venv`, installs dependencies and Chromium, copies `.env.example` to `.env`).
4. Open `.env` in Notepad and fill in `APPS_SCRIPT_WEBHOOK_URL`, `APPS_SCRIPT_API_TOKEN`, `ANTHROPIC_API_KEY`.
   For login walls add `PW_STORAGE_STATE_PATH=storage_state.json` and create that file with
   `.venv\Scripts\playwright codegen --save-storage=storage_state.json https://www.instagram.com/` (log in to Instagram, Facebook and Threads in that window, then close it).
5. Test: `run_windows.bat --dry-run`, then read `logs\run-<date>.log` and `out\<run>\payload_preview.json`.
6. Double-click `schedule_windows.bat` once. It registers *KKM complaint scraper* in Task Scheduler for every Friday 23:30 local time. The PC must be awake then; set *Power Options → Sleep → Never* or wake it by 23:25.

VPS alternative (`crontab -e`, server clock in UTC):

```
30 15 * * 5  cd /opt/kkm-complaints/scraper && . .venv/bin/activate && set -a && . .env && set +a && python main.py >> /var/log/kkm-scraper.log 2>&1
```

If the server runs on Asia/Kuala_Lumpur time use `30 23 * * 5` instead.

## Webhook API (for any other client)

`POST <exec URL>` with JSON body, always including `"token": "<API_TOKEN>"`:

| action | body | result |
|---|---|---|
| `insert` (default) | `records: [ {brand, platform, post_url, extracted_text, violation_type, violation_reason, product_name?, screenshot_link? or screenshot_base64+screenshot_mime, date?, confidence?, remarks?, complaint_description?} ]` | `{ok, inserted, ids, duplicates, errors}` |
| `known_urls` | — | `{ok, urls}` |
| `update_status` | `id, status (New/In-Progress/Complete/Dismissed), remarks?` | `{ok, row, stats}` |

`GET <exec URL>?action=list&token=…` (`&status=New&brand=…&platform=…`), `?action=stats`,
`?action=get&id=…`, `?action=ping` return JSON.

## Review logic in one paragraph

Every post is broken into claims (caption, hashtags, product name, on-image text). The
regex bank in `npra_rules.py` marks candidates with the Part 8 row or Part 10 clause they
would breach. The reviewer prompt in `evaluator.py` carries the condensed rulebook, the
team's skin-layer test and word swaps, and the enforcement precedents (treat/heal, doctor
KOLs, "loved by dermatologists", product-into-mouth), and returns a strict JSON verdict:
`Acceptable | Risky | Unacceptable`, the violation type, a reason quoting each failing
claim with its citation, the product name, and the BM complaint description. Only
Unacceptable verdicts at or above `min_confidence` are pushed; posts with no candidate
hits and no screenshot never reach the LLM.
