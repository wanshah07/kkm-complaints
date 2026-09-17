/**
 * KKM Cosmetic Complaint System — Google Apps Script backend
 * ==========================================================
 * Database : the bound Google Sheet (tab "Complaints"), plus a "Lookups" tab for
 *            the expandable Brand / Platform / Violation-type lists.
 * Webhook  : doPost(e) — JSON from the Python scraper (or any client) with a
 *            shared-secret token. Actions: insert | known_urls | update_status.
 * Web app  : doGet(e)  — serves the dashboard (Index.html) or, with ?action=…,
 *            a JSON API (list | get | known_urls | stats). The standalone dashboard in
 *            web/ (hosted on your own domain) talks to doPost with the DASHBOARD_KEY.
 * UI RPC   : the dashboard calls api* functions through google.script.run.
 *
 * One-time setup (from the Apps Script editor):
 *   1. Run  setup()  once. It builds both tabs, the data validation, the Drive
 *      folder for screenshots, and generates API_TOKEN + DASHBOARD_KEY in
 *      Script Properties. Read them from  Project Settings → Script properties.
 *   2. Deploy → New deployment → Web app → Execute as: Me, Access: Anyone.
 *   3. Give the scraper the /exec URL + API_TOKEN. Open the dashboard at
 *      <exec URL>?key=<DASHBOARD_KEY>  (or without the key when you are logged in
 *      as OWNER_EMAIL — see Script Properties).
 */

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
var SHEET_NAME = 'Complaints';
var LOOKUP_SHEET_NAME = 'Lookups';

// Column order is the contract with the scraper and the UI. Do not reorder
// without bumping SCHEMA_VERSION and running migrateSchema().
var HEADERS = [
  'ID',                 // 0  auto  KKM-YYYYMMDD-xxxx
  'Date',               // 1  detection date (yyyy-MM-dd)
  'Brand',              // 2
  'Platform',           // 3  Instagram | Facebook | Threads | …
  'Post URL',           // 4  unique key for dedupe
  'Screenshot Link',    // 5  Drive "anyone with link" viewer URL
  'Extracted Text',     // 6
  'Violation Type',     // 7  Medicinal claim | Mechanism claim | … (see Lookups)
  'Violation Reason',   // 8  free text with the Annex I Part 8 / Part 10 citation
  'Status',             // 9  New | In-Progress | Complete | Dismissed
  'Remarks',            // 10
  'Nama Kosmetik',      // 11 product name as advertised (KKM form: Nama kosmetik)
  'Nombor Notifikasi',  // 12 NOT number — blank until verified on QUEST3+
  'Jenis Aduan',        // 13 Iklan Kosmetik | Kualiti Kosmetik
  'Deskripsi Aduan',    // 14 ready-to-paste complaint description (BM)
  'Tarikh Melapor',     // 15 date the report was submitted to KKM
  'KKM Feedback',       // 16 NPRA/KKM reply pasted in by Wan after submission
  'Source',             // 17 scraper | manual
  'Confidence',         // 18 0–1 from the LLM reviewer, blank for manual
  'Created At',         // 19 ISO timestamp
  'Updated At'          // 20 ISO timestamp
];
var COL = {};
HEADERS.forEach(function (h, i) { COL[h] = i; });

var STATUSES = ['New', 'In-Progress', 'Complete', 'Dismissed'];
var STATUS_FLOW = { 'New': 'In-Progress', 'In-Progress': 'Complete', 'Complete': 'Complete', 'Dismissed': 'Dismissed' };
var JENIS_ADUAN = ['Iklan Kosmetik', 'Kualiti Kosmetik'];

var DEFAULT_BRANDS = ['La Roche-Posay', 'Eucerin', 'QV', 'The Raw'];
var DEFAULT_PLATFORMS = ['Instagram', 'Facebook', 'Threads'];
var DEFAULT_VIOLATION_TYPES = [
  'Medicinal / disease claim',
  'Mechanism claim (collagen, melanin, DNA, cells)',
  'Professional endorsement (doctor / dermatologist)',
  'Prohibited sunscreen wording',
  'Safety / no-side-effect claim',
  'Absolute / permanent result',
  'Comparison or disparagement',
  'Before-after without time elapsed',
  'GMP / MOH / approval reference',
  'Prohibited ingredient or procedure reference',
  'Unsubstantiated quantitative claim',
  'Other'
];

// Targets tab: the brands and handles the scraper visits. Edit this in the sheet, not in config.yaml.
// Columns after "Active" and before "Product hints" are platforms; add a column to add a platform.
var TARGET_SHEET_NAME = 'Targets';
var TARGET_FIXED_HEAD = ['Brand', 'Active'];
var TARGET_FIXED_TAIL = ['Product hints', 'Notes'];
var DEFAULT_TARGET_PLATFORMS = ['Instagram', 'Facebook', 'Threads'];
var DEFAULT_TARGETS = [
  ['La Roche-Posay', true, 'larocheposaymy', 'LaRochePosayMalaysia', 'larocheposaymy', 'Effaclar, Cicaplast, Anthelios, Toleriane, Lipikar, Mela B3, Hyalu B5', ''],
  ['Eucerin', true, 'eucerin_malaysia', 'EucerinMalaysia', 'eucerin_malaysia', 'Spotless Brightening, Ultrasensitive, Atopicontrol, Sun Gel-Creme, Dermopurifyer, Urea Repair, Hyaluron-Filler', ''],
  ['QV', true, 'qvskincaremy', 'QVSkincareMalaysia', 'qvskincaremy', 'QV Gentle Wash, QV Cream, QV Face, QV Baby, QV Intensive', ''],
  ['The Raw', true, 'therawmy', 'therawmy', 'therawmy', '', '']
];

var SCREENSHOT_FOLDER_NAME = 'KKM Complaint Screenshots';
var MAX_ROWS_RETURNED = 2000;
var PROPS = PropertiesService.getScriptProperties();
var TZ = 'Asia/Kuala_Lumpur';

// ---------------------------------------------------------------------------
// One-time setup
// ---------------------------------------------------------------------------
function setup() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = getOrCreateSheet_(ss, SHEET_NAME);
  ensureHeaders_(sheet);
  var lookups = getOrCreateSheet_(ss, LOOKUP_SHEET_NAME);
  ensureLookups_(lookups);
  applyValidation_(sheet, lookups);
  ensureTargets_(ss);
  ensureFolder_();
  if (!PROPS.getProperty('API_TOKEN')) PROPS.setProperty('API_TOKEN', randomToken_(40));
  if (!PROPS.getProperty('DASHBOARD_KEY')) PROPS.setProperty('DASHBOARD_KEY', randomToken_(24));
  if (!PROPS.getProperty('OWNER_EMAIL')) PROPS.setProperty('OWNER_EMAIL', Session.getEffectiveUser().getEmail());
  if (!PROPS.getProperty('KKM_FORM_URL')) PROPS.setProperty('KKM_FORM_URL', '');
  if (!PROPS.getProperty('SCHEMA_VERSION')) PROPS.setProperty('SCHEMA_VERSION', '1');
  Logger.log('Setup complete.\nAPI_TOKEN=%s\nDASHBOARD_KEY=%s\nOWNER_EMAIL=%s',
    PROPS.getProperty('API_TOKEN'), PROPS.getProperty('DASHBOARD_KEY'), PROPS.getProperty('OWNER_EMAIL'));
  return { ok: true, apiToken: PROPS.getProperty('API_TOKEN'), dashboardKey: PROPS.getProperty('DASHBOARD_KEY') };
}

function onOpen() {
  SpreadsheetApp.getUi().createMenu('KKM Complaints')
    .addItem('Run setup / repair headers', 'setup')
    .addItem('Show API token + dashboard key', 'showSecrets_')
    .addItem('Rotate API token', 'rotateApiToken_')
    .addToUi();
}

function showSecrets_() {
  SpreadsheetApp.getUi().alert(
    'API_TOKEN (scraper):\n' + PROPS.getProperty('API_TOKEN') +
    '\n\nDASHBOARD_KEY (append ?key=… to the web app URL):\n' + PROPS.getProperty('DASHBOARD_KEY'));
}

function rotateApiToken_() {
  PROPS.setProperty('API_TOKEN', randomToken_(40));
  showSecrets_();
}

function getOrCreateSheet_(ss, name) {
  return ss.getSheetByName(name) || ss.insertSheet(name);
}

function ensureHeaders_(sheet) {
  var lastCol = Math.max(sheet.getLastColumn(), 1);
  var existing = sheet.getRange(1, 1, 1, lastCol).getValues()[0].map(function (h) { return String(h).trim(); });
  var same = existing.length >= HEADERS.length && HEADERS.every(function (h, i) { return existing[i] === h; });
  if (!same) {
    // Insert any header that is missing at its own position, so the sheet's own data moves
    // with it. Writing the header row straight over the old one would silently misalign every
    // existing row by the number of inserted columns.
    var hasData = sheet.getLastRow() > 1;
    if (hasData && existing.filter(String).length) {
      HEADERS.forEach(function (h, i) {
        if (existing.indexOf(h) >= 0) return;              // already somewhere, leave it
        if (i === 0) sheet.insertColumnBefore(1); else sheet.insertColumnAfter(i);
        sheet.getRange(1, i + 1).setValue(h);
        existing.splice(i, 0, h);
        console.log('Schema migration: inserted column "' + h + '" at position ' + (i + 1));
      });
    }
    sheet.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]);
  }
  var hdr = sheet.getRange(1, 1, 1, HEADERS.length);
  hdr.setFontWeight('bold').setBackground('#0f172a').setFontColor('#ffffff').setWrap(false);
  sheet.setFrozenRows(1);
  var widths = [150, 95, 130, 100, 260, 220, 380, 220, 380, 100, 220, 200, 150, 130, 420, 110, 320, 80, 90, 160, 160];
  widths.forEach(function (w, i) { sheet.setColumnWidth(i + 1, w); });
  if (sheet.getMaxColumns() > HEADERS.length) {
    sheet.deleteColumns(HEADERS.length + 1, sheet.getMaxColumns() - HEADERS.length);
  }
}

function ensureLookups_(sheet) {
  var lists = [
    ['Brands', DEFAULT_BRANDS],
    ['Platforms', DEFAULT_PLATFORMS],
    ['Violation Types', DEFAULT_VIOLATION_TYPES],
    ['Statuses', STATUSES],
    ['Jenis Aduan', JENIS_ADUAN]
  ];
  lists.forEach(function (pair, c) {
    var col = c + 1;
    if (sheet.getRange(1, col).getValue() !== pair[0]) {
      sheet.getRange(1, col).setValue(pair[0]).setFontWeight('bold');
    }
    // Only seed when the column is empty below the header; never overwrite the user's edits.
    var below = sheet.getRange(2, col, Math.max(sheet.getLastRow() - 1, 1), 1).getValues()
      .map(function (r) { return r[0]; }).filter(String);
    if (below.length === 0) {
      sheet.getRange(2, col, pair[1].length, 1).setValues(pair[1].map(function (v) { return [v]; }));
    }
    sheet.setColumnWidth(col, 260);
  });
  sheet.setFrozenRows(1);
}

function ensureTargets_(ss) {
  var sheet = ss.getSheetByName(TARGET_SHEET_NAME);
  var created = false;
  if (!sheet) { sheet = ss.insertSheet(TARGET_SHEET_NAME); created = true; }
  if (sheet.getLastRow() === 0) {
    var headers = TARGET_FIXED_HEAD.concat(DEFAULT_TARGET_PLATFORMS, TARGET_FIXED_TAIL);
    sheet.getRange(1, 1, 1, headers.length).setValues([headers])
      .setFontWeight('bold').setBackground('#0f172a').setFontColor('#ffffff');
    sheet.getRange(2, 1, DEFAULT_TARGETS.length, headers.length).setValues(DEFAULT_TARGETS);
    sheet.setFrozenRows(1);
    [170, 70, 180, 200, 180, 360, 240].forEach(function (w, i) { sheet.setColumnWidth(i + 1, w); });
    created = true;
  }
  // Active column as checkboxes
  var rows = Math.max(sheet.getMaxRows() - 1, 1);
  sheet.getRange(2, 2, rows, 1).setDataValidation(SpreadsheetApp.newDataValidation().requireCheckbox().build());
  return sheet;
}

function sanitizeHandle_(raw) {
  // A Targets cell should hold a bare handle, but it's easy to paste the full profile URL
  // instead (e.g. copied straight from the address bar). Reduce either form to just the
  // handle so `profile_url.format(handle=...)` never doubles the domain.
  if (raw === null || raw === undefined) return '';
  var v = String(raw).trim();
  if (!v) return '';
  var looksLikeUrl = /^https?:\/\//i.test(v) || /(?:^|\.)(?:instagram|facebook|threads)\.(?:com|net)\b/i.test(v);
  if (!looksLikeUrl) return v.replace(/^\/+|\/+$/g, '').replace(/^@/, '');
  var withoutScheme = v.replace(/^https?:\/\//i, '');
  var slash = withoutScheme.indexOf('/');
  var path = slash === -1 ? '' : withoutScheme.slice(slash);
  path = path.split(/[?#]/)[0];
  var IGNORE = ['popular', 'explore', 'reel', 'reels', 'p', 'stories', 'tv', 'tags', 'hashtag', 'profile.php'];
  var segments = path.split('/')
    .filter(function (s) { return s.length > 0; })
    .filter(function (s) { return IGNORE.indexOf(s.toLowerCase()) < 0; });
  var handle = segments.length ? segments[segments.length - 1] : '';
  return handle.replace(/^@/, '');
}

function targets_() {
  // Reads the Targets tab. Returns [{name, active, handles: {Platform: handle}, product_hints: [..], notes}].
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ensureTargets_(ss);
  var last = sheet.getLastRow();
  if (last < 2) return [];
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0].map(function (h) { return String(h).trim(); });
  var iBrand = headers.indexOf('Brand'), iActive = headers.indexOf('Active');
  var iHints = headers.indexOf('Product hints'), iNotes = headers.indexOf('Notes');
  var platformCols = [];
  headers.forEach(function (h, i) {
    if (h && TARGET_FIXED_HEAD.indexOf(h) < 0 && TARGET_FIXED_TAIL.indexOf(h) < 0) platformCols.push({ name: h, i: i });
  });
  var out = [];
  sheet.getRange(2, 1, last - 1, headers.length).getValues().forEach(function (row) {
    var name = String(row[iBrand] || '').trim();
    if (!name) return;
    var active = iActive >= 0 ? (row[iActive] === true || String(row[iActive]).toLowerCase() === 'true') : true;
    var handles = {};
    platformCols.forEach(function (pc) {
      var v = sanitizeHandle_(row[pc.i]);
      if (v) handles[pc.name] = v;
    });
    var hints = iHints >= 0 ? String(row[iHints] || '').split(',').map(function (x) { return x.trim(); }).filter(String) : [];
    out.push({ name: name, active: active, handles: handles, product_hints: hints, notes: iNotes >= 0 ? String(row[iNotes] || '') : '' });
  });
  return out;
}

function applyValidation_(sheet, lookups) {
  var rows = Math.max(sheet.getMaxRows() - 1, 1);
  function rule(rangeA1) {
    return SpreadsheetApp.newDataValidation()
      .requireValueInRange(lookups.getRange(rangeA1), true).setAllowInvalid(true).build();
  }
  sheet.getRange(2, COL['Brand'] + 1, rows, 1).setDataValidation(rule('A2:A200'));
  sheet.getRange(2, COL['Platform'] + 1, rows, 1).setDataValidation(rule('B2:B200'));
  sheet.getRange(2, COL['Violation Type'] + 1, rows, 1).setDataValidation(rule('C2:C200'));
  sheet.getRange(2, COL['Status'] + 1, rows, 1).setDataValidation(
    SpreadsheetApp.newDataValidation().requireValueInList(STATUSES, true).setAllowInvalid(false).build());
  sheet.getRange(2, COL['Jenis Aduan'] + 1, rows, 1).setDataValidation(
    SpreadsheetApp.newDataValidation().requireValueInList(JENIS_ADUAN, true).setAllowInvalid(true).build());
}

function ensureFolder_() {
  var id = PROPS.getProperty('SCREENSHOT_FOLDER_ID');
  if (id) {
    try { return DriveApp.getFolderById(id); } catch (err) { /* recreate below */ }
  }
  var it = DriveApp.getFoldersByName(SCREENSHOT_FOLDER_NAME);
  var folder = it.hasNext() ? it.next() : DriveApp.createFolder(SCREENSHOT_FOLDER_NAME);
  PROPS.setProperty('SCREENSHOT_FOLDER_ID', folder.getId());
  return folder;
}

// ---------------------------------------------------------------------------
// HTTP entry points
// ---------------------------------------------------------------------------
function doGet(e) {
  e = e || {}; var p = e.parameter || {};
  if (p.view === 'guide') {
    return serveGuide_(p);
  }
  if (p.action) {
    return jsonResponse_(handleApi_(p.action, p, p));
  }
  if (!isAuthorisedViewer_(p.key)) {
    return HtmlService.createHtmlOutput(
      '<!doctype html><html><body style="font-family:system-ui;padding:40px;color:#0f172a">' +
      '<h2>KKM Complaint Dashboard</h2><p>Access key missing or wrong. Open the URL with ' +
      '<code>?key=&lt;DASHBOARD_KEY&gt;</code> (see Script Properties).</p></body></html>')
      .setTitle('KKM Complaints — locked');
  }
  var t = HtmlService.createTemplateFromFile('Index');
  t.dashboardKey = p.key || '';
  t.kkmFormUrl = PROPS.getProperty('KKM_FORM_URL') || '';
  t.guideUrl = ScriptApp.getService().getUrl() + '?view=guide';
  return t.evaluate()
    .setTitle('KKM Cosmetic Complaints')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

function doPost(e) {
  var body;
  try {
    body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
  } catch (err) {
    return jsonResponse_({ ok: false, error: 'Invalid JSON body' }, 400);
  }
  var token = body.token || (e.parameter && e.parameter.token);
  var key = body.key || (e.parameter && e.parameter.key);
  var action = body.action || 'insert';
  if (action !== 'ping' && !checkToken_(token) && !isAuthorisedViewer_(key)) {
    return jsonResponse_({ ok: false, error: 'Unauthorised' }, 401);
  }
  return jsonResponse_(handleApi_(action, body, { token: token, key: key }));
}

function handleApi_(action, body, p) {
  p = p || {}; body = body || {};
  var machine = checkToken_(p.token);
  var viewer = machine || isAuthorisedViewer_(p.key);
  var deny = { ok: false, error: 'Unauthorised' };
  try {
    switch (action) {
      case 'ping':          return { ok: true, time: new Date().toISOString(), schema: PROPS.getProperty('SCHEMA_VERSION') || '1' };
      // --- scraper (API_TOKEN) ---
      case 'insert':        if (!machine) return deny;
                            return apiInsertRecords_(body.records || (body.record ? [body.record] : []), 'scraper');
      case 'targets':       if (!viewer) return deny;
                            return { ok: true, targets: targets_() };
      case 'known_urls':    if (!viewer) return deny;
                            return { ok: true, urls: knownUrls_() };
      // --- dashboard (DASHBOARD_KEY) or scraper ---
      case 'list':          if (!viewer) return deny;
                            return { ok: true, rows: listRecords_(p.status || body.status, p.brand || body.brand, p.platform || body.platform), stats: stats_() };
      case 'get':           if (!viewer) return deny;
                            return { ok: true, row: getRecord_(p.id || body.id) };
      case 'stats':         if (!viewer) return deny;
                            return { ok: true, stats: stats_() };
      case 'update_status': if (!viewer) return deny;
                            return updateStatus_(body.id, body.status, body.remarks);
      case 'bootstrap':     if (!viewer) return deny;
                            return { ok: true, rows: listRecords_(), stats: stats_(), lookups: lookups_(),
                                     kkmFormUrl: PROPS.getProperty('KKM_FORM_URL') || '',
                                     questUrl: 'https://quest3plus.bpfk.gov.my/pmo2/index.php', statuses: STATUSES };
      case 'open':          if (!viewer) return deny;
                            return { ok: true, row: openRecord_(body.id) };
      case 'update_fields': if (!viewer) return deny;
                            return updateFields_(body.id, body.fields || {});
      case 'insert_manual': if (!viewer) return deny;
                            return insertManual_(body.record || {});
      case 'upload_screenshot': if (!viewer) return deny;
                            return { ok: true, link: saveScreenshot_(body.base64, body.mime, body.filename) };
      case 'set_form_url':  if (!viewer) return deny;
                            PROPS.setProperty('KKM_FORM_URL', String(body.url || '')); return { ok: true };
      case 'draft_review':  if (!viewer) return deny;
                            return draftReview_(body);
      default:              return { ok: false, error: 'Unknown action: ' + action };
    }
  } catch (err) {
    console.error(err);
    return { ok: false, error: String(err && err.message || err) };
  }
}

// ---------------------------------------------------------------------------
// Reviewer for manual entries: the same rulebook the scraper uses, called from
// the dashboard so a post pasted by hand comes back in the NPRA report format.
// Needs ANTHROPIC_API_KEY in Script Properties; ANTHROPIC_MODEL is optional.
// ---------------------------------------------------------------------------
function draftReview_(body) {
  var key = PROPS.getProperty('ANTHROPIC_API_KEY');
  if (!key) {
    return { ok: false, error: 'ANTHROPIC_API_KEY is not set. Apps Script → Project Settings → Script properties.' };
  }
  var text = String(body.text || '').trim();
  var b64 = String(body.imageBase64 || '');
  if (!text && !b64) return { ok: false, error: 'Paste the post text, attach the screenshot, or both.' };

  var parts = [];
  if (body.brand) parts.push('Brand: ' + body.brand);
  if (body.platform) parts.push('Platform: ' + body.platform);
  if (body.url) parts.push('Post URL: ' + body.url);
  if (body.productHints) parts.push('Known product lines: ' + body.productHints);
  parts.push('');
  parts.push(text ? 'POST TEXT:\n' + text : 'POST TEXT: (none supplied — read the screenshot)');

  var content = [];
  if (b64) {
    content.push({
      type: 'image',
      source: { type: 'base64', media_type: String(body.imageMime || 'image/png'), data: b64.replace(/^data:[^,]+,/, '') }
    });
  }
  content.push({ type: 'text', text: parts.join('\n') });

  var res = UrlFetchApp.fetch('https://api.anthropic.com/v1/messages', {
    method: 'post',
    contentType: 'application/json',
    muteHttpExceptions: true,
    headers: { 'x-api-key': key, 'anthropic-version': '2023-06-01' },
    payload: JSON.stringify({
      model: PROPS.getProperty('ANTHROPIC_MODEL') || 'claude-haiku-4-5',
      max_tokens: 2000,
      system: NPRA_SYSTEM_PROMPT,
      messages: [{ role: 'user', content: content }]
    })
  });
  if (res.getResponseCode() !== 200) {
    throw new Error('Reviewer API ' + res.getResponseCode() + ': ' + res.getContentText().slice(0, 300));
  }
  var body_ = JSON.parse(res.getContentText());
  var out = (body_.content || []).filter(function (c) { return c.type === 'text'; })
    .map(function (c) { return c.text; }).join('').trim();
  var data = parseReviewerJson_(out);
  if (!data) throw new Error('Reviewer did not return JSON: ' + out.slice(0, 200));
  return {
    ok: true,
    draft: {
      verdict: data.verdict || '',
      confidence: typeof data.confidence === 'number' ? data.confidence : '',
      violationType: data.violation_type || '',
      violationReason: data.violation_reason || '',
      extractedText: data.extracted_text || text,
      productName: data.product_name || '',
      deskripsi: data.complaint_description_bm || '',
      notes: data.notes || ''
    }
  };
}

// The model is asked for bare JSON; a code fence or a stray sentence around it
// still parses rather than failing the whole draft.
function parseReviewerJson_(out) {
  var t = String(out || '').replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/, '').trim();
  try { return JSON.parse(t); } catch (err) { /* fall through to the brace scan */ }
  var start = t.indexOf('{'), end = t.lastIndexOf('}');
  if (start < 0 || end <= start) return null;
  try { return JSON.parse(t.slice(start, end + 1)); } catch (err) { return null; }
}

function openRecord_(id) {
  var row = getRecord_(id);
  if (!row) throw new Error('Not found: ' + id);
  if (row['Status'] === 'New') {
    updateStatus_(id, 'In-Progress');
    row = getRecord_(id);
  }
  return row;
}

var EDITABLE_FIELDS = ['Remarks', 'Nama Kosmetik', 'Nombor Notifikasi', 'Jenis Aduan', 'Deskripsi Aduan',
                       'KKM Feedback', 'Violation Type', 'Violation Reason', 'Brand', 'Platform',
                       'Screenshot Link', 'Extracted Text'];

function updateFields_(id, fields) {
  return withLock_(function () {
    var sheet = sheet_();
    var r = findRowById_(sheet, id);
    if (r < 0) throw new Error('Not found: ' + id);
    var rowVals = sheet.getRange(r, 1, 1, HEADERS.length).getValues()[0];
    Object.keys(fields || {}).forEach(function (k) {
      if (EDITABLE_FIELDS.indexOf(k) >= 0) rowVals[COL[k]] = fields[k] == null ? '' : String(fields[k]);
    });
    rowVals[COL['Updated At']] = nowIso_();
    sheet.getRange(r, 1, 1, HEADERS.length).setValues([rowVals]);
    return { ok: true, row: rowToObject_(rowVals) };
  });
}

function insertManual_(record) {
  if (!record || !record['Brand'] || !record['Platform'] || !record['Post URL']) {
    throw new Error('Brand, Platform and Post URL are required.');
  }
  var res = apiInsertRecords_([record], 'manual');
  if (!res.ok) throw new Error(res.error || 'Insert failed');
  if (res.inserted === 0) throw new Error('That Post URL is already in the database (' + (res.duplicates[0] || '') + ').');
  return { ok: true, row: getRecord_(res.ids[0]), stats: stats_() };
}

// ---------------------------------------------------------------------------
// UI RPC (google.script.run). Every call carries the dashboard key.
// ---------------------------------------------------------------------------
function apiBootstrap(key) {
  requireViewer_(key);
  return {
    rows: listRecords_(),
    stats: stats_(),
    lookups: lookups_(),
    kkmFormUrl: PROPS.getProperty('KKM_FORM_URL') || '',
    questUrl: 'https://quest3plus.bpfk.gov.my/pmo2/index.php',
    statuses: STATUSES
  };
}

function apiList(key, filters) {
  requireViewer_(key);
  filters = filters || {};
  return { rows: listRecords_(filters.status, filters.brand, filters.platform), stats: stats_() };
}

function apiOpen(key, id) {
  // Called when a row is opened in the dashboard: New -> In-Progress automatically.
  requireViewer_(key);
  return openRecord_(id);
}

function apiSetStatus(key, id, status, remarks) {
  requireViewer_(key);
  return updateStatus_(id, status, remarks);
}

function apiAdvanceStatus(key, id) {
  requireViewer_(key);
  var row = getRecord_(id);
  if (!row) throw new Error('Not found: ' + id);
  return updateStatus_(id, STATUS_FLOW[row['Status']] || 'In-Progress');
}

function apiUpdateFields(key, id, fields) {
  requireViewer_(key);
  return updateFields_(id, fields);
}

function apiCreateManual(key, record) {
  requireViewer_(key);
  return insertManual_(record);
}

function apiUploadScreenshot(key, base64, mimeType, filename) {
  requireViewer_(key);
  return { ok: true, link: saveScreenshot_(base64, mimeType, filename) };
}

function apiSetKkmFormUrl(key, url) {
  requireViewer_(key);
  PROPS.setProperty('KKM_FORM_URL', String(url || ''));
  return { ok: true };
}

// ---------------------------------------------------------------------------
// Core data operations
// ---------------------------------------------------------------------------
function apiInsertRecords_(records, source) {
  if (!Array.isArray(records) || records.length === 0) return { ok: false, error: 'No records supplied' };
  return withLock_(function () {
    var sheet = sheet_();
    ensureHeaders_(sheet);
    var known = knownUrlSet_(sheet);
    var toAppend = [], ids = [], duplicates = [], errors = [];
    var today = Utilities.formatDate(new Date(), TZ, 'yyyy-MM-dd');
    records.forEach(function (rec, i) {
      try {
        rec = normaliseRecord_(rec);
        if (!rec['Post URL']) { errors.push({ index: i, error: 'Post URL missing' }); return; }
        var urlKey = canonicalUrl_(rec['Post URL']);
        if (known[urlKey]) { duplicates.push(rec['Post URL']); return; }
        known[urlKey] = true;
        var link = rec['Screenshot Link'] || '';
        if (!link && rec.screenshot_base64) {
          link = saveScreenshot_(rec.screenshot_base64, rec.screenshot_mime || 'image/jpeg',
            safeFilename_(rec['Brand'] + '_' + rec['Platform'] + '_' + today + '_' + (i + 1)) + extFor_(rec.screenshot_mime));
        }
        var id = makeId_();
        var row = new Array(HEADERS.length).fill('');
        row[COL['ID']] = id;
        row[COL['Date']] = rec['Date'] || today;
        row[COL['Brand']] = rec['Brand'];
        row[COL['Platform']] = rec['Platform'];
        row[COL['Post URL']] = rec['Post URL'];
        row[COL['Screenshot Link']] = link;
        row[COL['Extracted Text']] = rec['Extracted Text'];
        row[COL['Violation Type']] = rec['Violation Type'];
        row[COL['Violation Reason']] = rec['Violation Reason'];
        row[COL['Status']] = 'New';
        row[COL['Remarks']] = rec['Remarks'];
        row[COL['Nama Kosmetik']] = rec['Nama Kosmetik'];
        row[COL['Nombor Notifikasi']] = rec['Nombor Notifikasi'];
        row[COL['Jenis Aduan']] = rec['Jenis Aduan'] || 'Iklan Kosmetik';
        row[COL['Deskripsi Aduan']] = rec['Deskripsi Aduan'] || buildComplaintDescription_(rec);
        row[COL['Tarikh Melapor']] = '';
        row[COL['KKM Feedback']] = rec['KKM Feedback'];
        row[COL['Source']] = rec['Source'] || source || 'scraper';
        row[COL['Confidence']] = rec['Confidence'];
        row[COL['Created At']] = nowIso_();
        row[COL['Updated At']] = nowIso_();
        toAppend.push(row); ids.push(id);
      } catch (err) {
        errors.push({ index: i, error: String(err && err.message || err) });
      }
    });
    if (toAppend.length) {
      sheet.getRange(sheet.getLastRow() + 1, 1, toAppend.length, HEADERS.length).setValues(toAppend);
      SpreadsheetApp.flush();
    }
    return { ok: true, inserted: toAppend.length, ids: ids, duplicates: duplicates, errors: errors };
  });
}

function normaliseRecord_(rec) {
  // Accept both Sheet-header keys ("Post URL") and snake_case keys (post_url).
  var map = {
    date: 'Date', brand: 'Brand', platform: 'Platform', post_url: 'Post URL', url: 'Post URL',
    screenshot_link: 'Screenshot Link', screenshot_url: 'Screenshot Link', extracted_text: 'Extracted Text',
    text: 'Extracted Text', violation_type: 'Violation Type', violation_reason: 'Violation Reason',
    reason: 'Violation Reason', remarks: 'Remarks', product_name: 'Nama Kosmetik', nama_kosmetik: 'Nama Kosmetik',
    notification_number: 'Nombor Notifikasi', nombor_notifikasi: 'Nombor Notifikasi', jenis_aduan: 'Jenis Aduan',
    complaint_type: 'Jenis Aduan', deskripsi_aduan: 'Deskripsi Aduan', complaint_description: 'Deskripsi Aduan',
    source: 'Source', confidence: 'Confidence', kkm_feedback: 'KKM Feedback', feedback: 'KKM Feedback'
  };
  var out = { screenshot_base64: rec.screenshot_base64 || '', screenshot_mime: rec.screenshot_mime || '' };
  HEADERS.forEach(function (h) { out[h] = rec[h] != null ? rec[h] : ''; });
  Object.keys(map).forEach(function (k) {
    if (rec[k] != null && rec[k] !== '' && !out[map[k]]) out[map[k]] = rec[k];
  });
  ['Brand', 'Platform', 'Post URL', 'Extracted Text', 'Violation Type', 'Violation Reason', 'Remarks',
   'Nama Kosmetik', 'Nombor Notifikasi', 'Jenis Aduan', 'Deskripsi Aduan', 'KKM Feedback', 'Source'].forEach(function (h) {
    out[h] = out[h] == null ? '' : String(out[h]).trim();
  });
  if (out['Extracted Text'].length > 45000) out['Extracted Text'] = out['Extracted Text'].slice(0, 45000) + ' …';
  if (out['Confidence'] !== '' && out['Confidence'] != null) {
    var c = Number(out['Confidence']); out['Confidence'] = isNaN(c) ? '' : Math.round(c * 100) / 100;
  }
  if (out['Date']) {
    var d = new Date(out['Date']);
    out['Date'] = isNaN(d.getTime()) ? '' : Utilities.formatDate(d, TZ, 'yyyy-MM-dd');
  }
  if (out['Jenis Aduan'] && JENIS_ADUAN.indexOf(out['Jenis Aduan']) < 0) out['Jenis Aduan'] = 'Iklan Kosmetik';
  return out;
}

function buildComplaintDescription_(rec) {
  // Fallback complaint text (BM) when the client did not supply one. Nothing here is
  // a fact the scraper cannot vouch for: it only restates what was captured.
  var product = rec['Nama Kosmetik'] || '[SAHKAN: nama produk]';
  var lines = [
    'Aduan iklan kosmetik bernotifikasi.',
    'Jenama: ' + (rec['Brand'] || '-') + '. Produk: ' + product + '. Platform: ' + (rec['Platform'] || '-') + '.',
    'Pautan iklan: ' + (rec['Post URL'] || '-'),
    'Dakwaan yang dikesan: ' + (rec['Violation Reason'] || '-'),
    'Jenis pelanggaran: ' + (rec['Violation Type'] || '-') + '.',
    'Dakwaan ini tidak dibenarkan untuk produk kosmetik mengikut Guidelines for Control of Cosmetic Products in Malaysia, Annex I Part 8 (Guideline for Cosmetic Claims) dan Part 10 (Guideline for Cosmetic Advertisement), NPRA.',
    'Tangkapan skrin dilampirkan. Nombor notifikasi: ' + (rec['Nombor Notifikasi'] || '[SAHKAN: semak QUEST3+]') + '.'
  ];
  return lines.join('\n');
}

function updateStatus_(id, status, remarks) {
  if (STATUSES.indexOf(status) < 0) return { ok: false, error: 'Invalid status. Use one of: ' + STATUSES.join(', ') };
  return withLock_(function () {
    var sheet = sheet_();
    var r = findRowById_(sheet, id);
    if (r < 0) return { ok: false, error: 'Not found: ' + id };
    var rowVals = sheet.getRange(r, 1, 1, HEADERS.length).getValues()[0];
    rowVals[COL['Status']] = status;
    if (remarks != null && remarks !== '') rowVals[COL['Remarks']] = String(remarks);
    if (status === 'Complete' && !rowVals[COL['Tarikh Melapor']]) {
      rowVals[COL['Tarikh Melapor']] = Utilities.formatDate(new Date(), TZ, 'yyyy-MM-dd');
    }
    rowVals[COL['Updated At']] = nowIso_();
    sheet.getRange(r, 1, 1, HEADERS.length).setValues([rowVals]);
    return { ok: true, row: rowToObject_(rowVals), stats: stats_() };
  });
}

function listRecords_(status, brand, platform) {
  var sheet = sheet_();
  var last = sheet.getLastRow();
  if (last < 2) return [];
  var vals = sheet.getRange(2, 1, last - 1, HEADERS.length).getValues();
  var out = [];
  for (var i = vals.length - 1; i >= 0; i--) { // newest first
    var row = vals[i];
    if (!row[COL['ID']]) continue;
    if (status && row[COL['Status']] !== status) continue;
    if (brand && row[COL['Brand']] !== brand) continue;
    if (platform && row[COL['Platform']] !== platform) continue;
    out.push(rowToObject_(row));
    if (out.length >= MAX_ROWS_RETURNED) break;
  }
  return out;
}

function getRecord_(id) {
  var sheet = sheet_();
  var r = findRowById_(sheet, id);
  if (r < 0) return null;
  return rowToObject_(sheet.getRange(r, 1, 1, HEADERS.length).getValues()[0]);
}

function stats_() {
  var counts = { total: 0 }; STATUSES.forEach(function (s) { counts[s] = 0; });
  var byBrand = {}, byPlatform = {};
  var sheet = sheet_();
  var last = sheet.getLastRow();
  if (last >= 2) {
    var vals = sheet.getRange(2, 1, last - 1, HEADERS.length).getValues();
    vals.forEach(function (row) {
      if (!row[COL['ID']]) return;
      counts.total++;
      var s = row[COL['Status']]; counts[s] = (counts[s] || 0) + 1;
      var b = row[COL['Brand']]; byBrand[b] = (byBrand[b] || 0) + 1;
      var p = row[COL['Platform']]; byPlatform[p] = (byPlatform[p] || 0) + 1;
    });
  }
  return { counts: counts, byBrand: byBrand, byPlatform: byPlatform, generatedAt: nowIso_() };
}

function lookups_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(LOOKUP_SHEET_NAME);
  if (!sheet) return { brands: DEFAULT_BRANDS, platforms: DEFAULT_PLATFORMS, violationTypes: DEFAULT_VIOLATION_TYPES, jenisAduan: JENIS_ADUAN };
  function colList(c) {
    var last = sheet.getLastRow();
    if (last < 2) return [];
    return sheet.getRange(2, c, last - 1, 1).getValues().map(function (r) { return String(r[0]).trim(); }).filter(String);
  }
  var brands = colList(1).length ? colList(1) : DEFAULT_BRANDS.slice();
  try {
    targets_().forEach(function (t) { if (brands.indexOf(t.name) < 0) brands.push(t.name); });
  } catch (err) { console.warn('targets unavailable for lookups: ' + err); }
  return {
    brands: brands,
    platforms: colList(2).length ? colList(2) : DEFAULT_PLATFORMS,
    violationTypes: colList(3).length ? colList(3) : DEFAULT_VIOLATION_TYPES,
    jenisAduan: JENIS_ADUAN
  };
}

function knownUrls_() {
  return Object.keys(knownUrlSet_(sheet_()));
}

function knownUrlSet_(sheet) {
  var set = {};
  var last = sheet.getLastRow();
  if (last < 2) return set;
  sheet.getRange(2, COL['Post URL'] + 1, last - 1, 1).getValues().forEach(function (r) {
    if (r[0]) set[canonicalUrl_(String(r[0]))] = true;
  });
  return set;
}

// ---------------------------------------------------------------------------
// Screenshot storage (Drive)
// ---------------------------------------------------------------------------
function saveScreenshot_(base64, mimeType, filename) {
  if (!base64) return '';
  var folder = ensureFolder_();
  var data = base64.indexOf('base64,') >= 0 ? base64.split('base64,')[1] : base64;
  var bytes = Utilities.base64Decode(data);
  var blob = Utilities.newBlob(bytes, mimeType || 'image/jpeg', filename || ('screenshot_' + Date.now() + '.jpg'));
  var file = folder.createFile(blob);
  try {
    file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
  } catch (err) {
    // Workspace domains can forbid link sharing; the owner can still open the file.
    console.warn('Sharing not applied: ' + err);
  }
  return 'https://drive.google.com/file/d/' + file.getId() + '/view';
}

// ---------------------------------------------------------------------------
// Auth helpers
// ---------------------------------------------------------------------------
function checkToken_(token) {
  var expected = PROPS.getProperty('API_TOKEN');
  return !!expected && !!token && constantTimeEquals_(String(token), expected);
}

function isAuthorisedViewer_(key) {
  var expected = PROPS.getProperty('DASHBOARD_KEY');
  if (expected && key && constantTimeEquals_(String(key), expected)) return true;
  if (isAdminEmail_()) return true;
  return false;
}

function isAdminEmail_() {
  try {
    var email = Session.getActiveUser().getEmail();
    var owner = PROPS.getProperty('OWNER_EMAIL');
    return !!(email && owner && email.toLowerCase() === owner.toLowerCase());
  } catch (err) {
    return false; // anonymous / identity not disclosed
  }
}

// The guide is behind a passcode: GUIDE_PASSCODE when set, otherwise DASHBOARD_KEY.
// A web app deployed "Execute as Me / Access Anyone" is never told who is viewing
// (Session.getActiveUser() is empty), so a Google-account check cannot gate it.
// Signed in as the owner still bypasses the prompt where the identity IS available.
function guidePasscode_() {
  return PROPS.getProperty('GUIDE_PASSCODE') || PROPS.getProperty('DASHBOARD_KEY') || '';
}

function serveGuide_(p) {
  p = p || {};
  var expected = guidePasscode_();
  var supplied = String(p.pass || '');
  var ok = isAdminEmail_() || (!!expected && !!supplied && constantTimeEquals_(supplied, expected));
  if (!ok) return guideLockPage_(!!supplied, !expected);

  var t = HtmlService.createTemplateFromFile('Guide');
  t.dashboardUrl = ScriptApp.getService().getUrl();
  t.passcodeJson = JSON.stringify(supplied);   // the page remembers it for this browser
  return t.evaluate()
    .setTitle('KKM Operating Guide')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

// The prompt. A passcode already stored in this browser is filled in and submitted
// automatically, so it is typed once; a wrong one comes back here with a message.
function guideLockPage_(wrong, unset) {
  var url = ScriptApp.getService().getUrl();
  var msg = unset
    ? '<p style="color:#b91c1c">No passcode is set. Add <code>GUIDE_PASSCODE</code> in Apps Script → Project Settings → Script properties.</p>'
    : (wrong ? '<p style="color:#b91c1c">Wrong passcode.</p>' : '');
  return HtmlService.createHtmlOutput(
    '<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">' +
    '<style>body{font-family:Inter,system-ui,sans-serif;background:#f8fafc;color:#0f172a;margin:0;' +
    'display:grid;place-items:center;min-height:100vh}' +
    '.card{background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:28px;max-width:380px;width:90%;' +
    'box-shadow:0 10px 30px rgba(15,23,42,.06)}h2{margin:0 0 6px;font-size:18px}' +
    'p{font-size:14px;color:#475569;margin:6px 0 14px}' +
    'input{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;font-size:14px}' +
    'button{margin-top:10px;width:100%;padding:10px 12px;border:0;border-radius:10px;background:#0f172a;color:#fff;' +
    'font-size:14px;font-weight:600;cursor:pointer}</style></head><body><div class="card">' +
    '<h2>KKM Operating Guide</h2><p>Enter the passcode to open the guide.</p>' + msg +
    '<form id="f" method="get" action="' + url + '" target="_top">' +
    '<input type="hidden" name="view" value="guide">' +
    '<input id="pass" type="password" name="pass" placeholder="Passcode" autofocus autocomplete="current-password">' +
    '<button type="submit">Open guide</button></form>' +
    '<script>(function(){try{' +
    'var saved=localStorage.getItem("kkm.guidepass");' +
    'var tried=' + (wrong ? 'true' : 'false') + ';' +
    'if(saved&&!tried){document.getElementById("pass").value=saved;document.getElementById("f").submit();}' +
    'if(tried){localStorage.removeItem("kkm.guidepass");}' +
    '}catch(e){}})();<\/script>' +
    '</div></body></html>')
    .setTitle('KKM Guide — locked')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

function requireViewer_(key) {
  if (!isAuthorisedViewer_(key)) throw new Error('Unauthorised: dashboard key missing or wrong.');
}

function constantTimeEquals_(a, b) {
  if (a.length !== b.length) return false;
  var diff = 0;
  for (var i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------
function sheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) { sheet = ss.insertSheet(SHEET_NAME); ensureHeaders_(sheet); }
  return sheet;
}

function findRowById_(sheet, id) {
  var last = sheet.getLastRow();
  if (last < 2 || !id) return -1;
  var ids = sheet.getRange(2, COL['ID'] + 1, last - 1, 1).getValues();
  for (var i = 0; i < ids.length; i++) if (String(ids[i][0]) === String(id)) return i + 2;
  return -1;
}

function rowToObject_(row) {
  var o = {};
  HEADERS.forEach(function (h, i) {
    var v = row[i];
    if (v instanceof Date) v = Utilities.formatDate(v, TZ, 'yyyy-MM-dd');
    o[h] = v == null ? '' : v;
  });
  return o;
}

function withLock_(fn) {
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try { return fn(); } finally { lock.releaseLock(); }
}

function makeId_() {
  return 'KKM-' + Utilities.formatDate(new Date(), TZ, 'yyyyMMdd') + '-' + randomToken_(4).toUpperCase();
}

function randomToken_(n) {
  var chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  var out = '';
  for (var i = 0; i < n; i++) out += chars.charAt(Math.floor(Math.random() * chars.length));
  return out;
}

function canonicalUrl_(u) {
  u = String(u || '').trim();
  try {
    u = u.replace(/^http:\/\//i, 'https://').replace(/^https:\/\/(m|www|web)\./i, 'https://');
    u = u.split('#')[0];
    // Strip tracking params but keep identity params Facebook relies on (story_fbid, id, v, fbid).
    var parts = u.split('?');
    if (parts.length > 1) {
      var keep = parts[1].split('&').filter(function (kv) {
        return /^(story_fbid|id|v|fbid|set)=/i.test(kv);
      });
      u = parts[0] + (keep.length ? '?' + keep.join('&') : '');
    }
    return u.replace(/\/+$/, '').toLowerCase();
  } catch (err) { return u.toLowerCase(); }
}

function safeFilename_(s) {
  return String(s || 'screenshot').replace(/[^a-z0-9_\-]+/gi, '_').slice(0, 80);
}

function extFor_(mime) {
  if (/png/i.test(mime || '')) return '.png';
  if (/webp/i.test(mime || '')) return '.webp';
  return '.jpg';
}

function nowIso_() {
  return Utilities.formatDate(new Date(), TZ, "yyyy-MM-dd'T'HH:mm:ssXXX");
}

function jsonResponse_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function include(filename) {
  return HtmlService.createHtmlOutputFromFile(filename).getContent();
}

// ---------------------------------------------------------------------------
// Smoke test — run from the editor after setup() to prove the pipeline end to end.
// ---------------------------------------------------------------------------
function smokeTest_() {
  var res = apiInsertRecords_([{
    brand: 'Test Brand', platform: 'Instagram',
    post_url: 'https://www.instagram.com/p/SMOKE' + Date.now() + '/',
    extracted_text: 'Merawat jerawat secara efektif dalam 3 hari.',
    violation_type: 'Medicinal / disease claim',
    violation_reason: '"Merawat jerawat" = treats acne. Annex I Part 8, Skin products: "Heals, treats or stops acne" is unacceptable.',
    product_name: 'Test Serum', confidence: 0.95
  }], 'scraper');
  Logger.log(JSON.stringify(res));
  Logger.log(JSON.stringify(updateStatus_(res.ids[0], 'In-Progress', 'opened in smoke test')));
  Logger.log(JSON.stringify(stats_()));
}
