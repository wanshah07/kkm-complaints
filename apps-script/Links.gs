/**
 * KKM Cosmetic Complaint System — Links tab (Wan, 5 Oct 2026).
 *
 * ADD THIS AS A NEW FILE in the Apps Script editor (+ → Script → name it Links), paste this file,
 * then Deploy → Manage deployments → New version. The Code.gs switch already carries the cases
 * (links_list, links_add, links_retry, links_pending, links_update), so Code.gs must be re-pasted too.
 *
 * A post cannot be found by a runner on TikTok (the account's post list is closed to automation), so
 * the posts to review are NAMED: Wan pastes video links into the dashboard, they land in this tab, and
 * a scheduled workflow picks them up, reviews them, files the findings in Complaints and writes the
 * outcome back here. Wan never has to open the sheet; the tab is just the queue's storage.
 *
 * Columns (the layout already live in the sheet since 5 Oct):
 *   A Link   B Added / note   C Status   D Run   E Result   F How to use (text, never touched)
 *
 * Findings are HELD, not filed (Wan, 5 Oct 2026). An Unacceptable or Risky verdict waits here as 'to decide' with the
 * complete record in column H, and the dashboard's "Add to database" button files it (links_file) or "Dismiss"
 * removes it (links_dismiss). An Acceptable verdict is cleared from the tab a few minutes after it lands. Nothing is
 * forgotten by that: every judged post is in the Reviewed ledger, and linksAdd_ checks it, so the same video pasted
 * again answers "already reviewed" instead of being read (and paid for) twice.
 *
 * Extra columns: G Updated (ISO time of the last write), H Held record (JSON, system use).
 *
 * Status is the whole state machine:
 *   ''                        waiting to be picked up (TikTok video links only)
 *   'queued <UTC ISO>'        claimed by a run that has not started reading yet
 *   'running'                 a run is reading it (D carries the run number)
 *   'done' | 'error'          finished (E carries the one-line result); acceptable 'done' rows are cleared after 10 min
 *   'to decide'               Unacceptable / Risky held for Wan: Add to database (-> 'filed') or Dismiss (row removed)
 *   'filed'                   added to Complaints by Wan's click (cleared after 10 min)
 *   'not readable: …' | 'skipped: …' | 'not a TikTok video link'   decided on arrival, never queued
 */
var LINKS_SHEET_NAME = 'Links';
var LINKS_HEADERS = ['Link (paste one per row)', 'Added / note', 'Status (filled by the system)', 'Run', 'Result', 'How to use', 'Updated', 'Held record (system)'];
var LINKS_MAX_PER_RUN = 10;
var LINKS_STALE_MIN = 90;
var LINKS_CLEAR_MIN = 10;

function linksSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(LINKS_SHEET_NAME);
  if (!sh) {
    sh = ss.insertSheet(LINKS_SHEET_NAME);
    sh.getRange(1, 1, 1, LINKS_HEADERS.length).setValues([LINKS_HEADERS]).setFontWeight('bold');
    sh.setFrozenRows(1);
    [420, 200, 220, 90, 420, 360, 170, 120].forEach(function (w, i) { sh.setColumnWidth(i + 1, w); });
  }
  return sh;
}

// kind: 'tiktok' (a video / photo page or a short link) | 'shopee' | 'own' | 'other'
function classifyLink_(raw) {
  var s = String(raw || '').trim();
  if (/valorith|facerin/i.test(s)) return 'own';
  var m = s.match(/^https?:\/\/([^\/?#]+)(\/[^?#]*)?/i);
  if (!m) return 'other';
  var host = m[1].toLowerCase().replace(/^www\./, '');
  var path = m[2] || '';
  if (host === 'vm.tiktok.com' || host === 'vt.tiktok.com') return 'tiktok';
  if (host === 'tiktok.com' && (/^\/t\//i.test(path) || /^\/@[^\/]+\/(video|photo)\/\d{10,}/i.test(path))) return 'tiktok';
  if (/(^|\.)shopee\./i.test(host)) return 'shopee';
  return 'other';
}

function linkKey_(raw) {
  return String(raw || '').trim().split('#')[0].split('?')[0].replace(/\/+$/, '').toLowerCase();
}

var LINK_DECISIONS_ = {
  shopee: 'not readable: Shopee verifies runners; paste the listing text in chat',
  own: 'skipped: own brand',
  other: 'not a TikTok video link'
};

function linksRows_(sh) {
  var last = sh.getLastRow();
  if (last < 2) return [];
  return sh.getRange(2, 1, last - 1, 8).getValues().map(function (r, i) {
    return { row: i + 2, link: String(r[0] || '').trim(), note: String(r[1] || ''), status: String(r[2] || ''),
             run: String(r[3] || ''), result: String(r[4] || ''), updated: r[6] ? new Date(r[6]).getTime() : NaN,
             held: String(r[7] || '') };
  }).filter(function (o) { return o.link; });
}

function nowIso_() { return new Date().toISOString().replace(/\.\d+Z$/, 'Z'); }

// Rows that have served their purpose leave the tab. Their history stays: the Reviewed ledger holds every judged
// post, Complaints holds every filed one, and a repeat paste is answered from those two.
function linksSweep_(sh) {
  var now = Date.now(), gone = [];
  linksRows_(sh).forEach(function (o) {
    var clearable = (o.status === 'done' && /^(reviewed: acceptable|already reviewed)/.test(o.result)) || o.status === 'filed';
    if (!clearable) return;
    if (isNaN(o.updated)) { sh.getRange(o.row, 7).setValue(nowIso_()); return; }   // a row older than column G: start its clock now
    if (now - o.updated > LINKS_CLEAR_MIN * 60000) gone.push(o.row);
  });
  gone.sort(function (a, b) { return b - a; }).forEach(function (r) { sh.deleteRow(r); });
  return gone.length;
}

function linksList_() {
  return withLock_(function () {
    var sh = linksSheet_();
    linksSweep_(sh);
    var rows = linksRows_(sh).map(function (o) { o.canFile = !!o.held; delete o.held; delete o.updated; return o; });
    rows.reverse();                                  // newest first
    return { ok: true, links: rows.slice(0, 200), total: rows.length };
  });
}

// urls: array or newline / space separated string. Rejected-on-arrival links are recorded WITH their reason, so
// a pasted Shopee link shows why it will never be read instead of vanishing.
function linksAdd_(urls, note) {
  if (typeof urls === 'string') urls = urls.split(/[\s,]+/);
  urls = (urls || []).map(function (u) { return String(u || '').trim(); }).filter(String);
  if (!urls.length) return { ok: false, error: 'No link supplied' };
  if (urls.length > 50) return { ok: false, error: 'At most 50 links at a time' };
  return withLock_(function () {
    var sh = linksSheet_();
    var have = {};
    linksRows_(sh).forEach(function (o) { have[linkKey_(o.link)] = o; });
    var seen = {}, known = {};
    try { seen = seenUrlSet_(); } catch (e) { /* ledger not deployed: carry on without it */ }
    try { known = knownUrlSet_(sheet_()); } catch (e) {}
    var add = [], out = { ok: true, added: [], duplicates: [], rejected: [] };
    urls.forEach(function (u) {
      var k = linkKey_(u);
      if (have[k]) { out.duplicates.push({ link: u, status: have[k].status || 'waiting' }); return; }
      have[k] = { status: '' };
      var kind = classifyLink_(u);
      var ck = canonicalUrl_(u);
      if (kind === 'tiktok' && (seen[ck] || known[ck])) {
        out.rejected.push({ link: u, status: 'already reviewed' });   // history, not queue: nothing to add
        return;
      }
      var status = kind === 'tiktok' ? '' : LINK_DECISIONS_[kind];
      add.push([u, String(note || 'Added in dashboard'), status, '', '', '', nowIso_()]);
      (kind === 'tiktok' ? out.added : out.rejected).push({ link: u, status: status || 'waiting' });
    });
    if (add.length) {
      var start = Math.max(sh.getLastRow(), 1) + 1;
      sh.getRange(start, 1, add.length, 7).setValues(add);
    }
    return out;
  });
}

// Put a finished / failed row back in the queue (dashboard "Retry").
function linksRetry_(link) {
  return withLock_(function () {
    var sh = linksSheet_();
    var k = linkKey_(link);
    var hit = linksRows_(sh).filter(function (o) { return linkKey_(o.link) === k; })[0];
    if (!hit) return { ok: false, error: 'Link not found' };
    if (classifyLink_(hit.link) !== 'tiktok') return { ok: false, error: 'Only TikTok video links can be retried' };
    sh.getRange(hit.row, 3, 1, 3).setValues([['', '', '']]);
    sh.getRange(hit.row, 7, 1, 2).setValues([[nowIso_(), '']]);
    return { ok: true };
  });
}

function parseClaimTime_(status) {
  var m = String(status).match(/^(?:queued|running)(?: (\d{4}-\d\d-\d\dT[\d:]+Z?))?/);
  return m && m[1] ? Date.parse(m[1].slice(-1) === 'Z' ? m[1] : m[1] + 'Z') : NaN;
}

// Called by the scheduled workflow. Claims up to LINKS_MAX_PER_RUN waiting TikTok rows in ONE locked step, so two
// overlapping ticks can never take the same row. Also frees rows a dead run left behind.
function linksPending_() {
  return withLock_(function () {
    var sh = linksSheet_();
    linksSweep_(sh);
    var now = Date.now(), stamp = new Date(now).toISOString().replace(/\.\d+Z$/, 'Z');
    var rows = linksRows_(sh), claimed = [], released = 0;
    rows.forEach(function (o) {
      if (!/^(queued|running)/.test(o.status)) return;
      var t = parseClaimTime_(o.status);
      if (!isNaN(t) && now - t > LINKS_STALE_MIN * 60000) {
        // A run that never reported: queued -> back in the queue; running -> an error Wan can see and retry.
        if (/^queued/.test(o.status)) { sh.getRange(o.row, 3, 1, 3).setValues([['', '', '']]); o.status = ''; released++; }
        else { sh.getRange(o.row, 3, 1, 3).setValues([['error', o.run, 'the run did not report back; press Retry']]); }
      }
    });
    rows.forEach(function (o) {
      if (claimed.length >= LINKS_MAX_PER_RUN) return;
      if (o.status === '' && classifyLink_(o.link) === 'tiktok') {
        sh.getRange(o.row, 3).setValue('queued ' + stamp);
        claimed.push(o.link);
      }
    });
    return { ok: true, links: claimed, released: released };
  });
}

// updates: [{link, status, run, result}] — writes C:E of the matching rows and nothing else.
function linksUpdate_(updates) {
  if (!Array.isArray(updates) || !updates.length) return { ok: false, error: 'No updates supplied' };
  return withLock_(function () {
    var sh = linksSheet_();
    var byKey = {};
    linksRows_(sh).forEach(function (o) { byKey[linkKey_(o.link)] = o; });
    var n = 0, missing = [];
    updates.forEach(function (u) {
      var o = byKey[linkKey_(u.link)];
      if (!o) { missing.push(u.link); return; }
      var status = u.status != null ? String(u.status) : o.status;
      var run = u.run != null ? String(u.run) : o.run;
      var result = u.result != null ? String(u.result).slice(0, 500) : o.result;
      if (/^running/.test(status) && !/^running \d/.test(status)) {
        status = 'running ' + new Date().toISOString().replace(/\.\d+Z$/, 'Z');
      }
      sh.getRange(o.row, 3, 1, 3).setValues([[status, run, result]]);
      sh.getRange(o.row, 7).setValue(nowIso_());
      if (u.held != null) sh.getRange(o.row, 8).setValue(String(u.held));
      n++;
    });
    return { ok: true, updated: n, missing: missing };
  });
}

// Wan pressed "Add to database" on a held finding. The insert takes the script lock itself, so it runs OUTSIDE ours.
function linksFile_(link) {
  var hit = withLock_(function () {
    var k = linkKey_(link);
    return linksRows_(linksSheet_()).filter(function (o) { return linkKey_(o.link) === k; })[0] || null;
  });
  if (!hit) return { ok: false, error: 'Link not found' };
  if (hit.status !== 'to decide' || !hit.held) return { ok: false, error: 'Nothing held for this link' };
  var rec = JSON.parse(hit.held);
  var res = apiInsertRecords_([rec], 'scraper');
  var note;
  if (res.inserted) note = 'added to Complaints (' + (res.ids && res.ids[0] || '') + ')';
  else if (res.duplicates && res.duplicates.length) note = 'already in Complaints (' + res.duplicates[0] + ')';
  else return { ok: false, error: (res.errors && res.errors[0]) || res.error || 'The insert did not go through' };
  return withLock_(function () {
    var sh = linksSheet_(), k2 = linkKey_(link);
    var row = linksRows_(sh).filter(function (o) { return linkKey_(o.link) === k2; })[0];
    if (row) {
      sh.getRange(row.row, 3, 1, 3).setValues([['filed', row.run, note]]);
      sh.getRange(row.row, 7, 1, 2).setValues([[nowIso_(), '']]);
    }
    return { ok: true, result: note };
  });
}

// Wan dismissed a held finding: the row goes, the Reviewed ledger keeps the post so it is not read again.
function linksDismiss_(link) {
  return withLock_(function () {
    var sh = linksSheet_(), k = linkKey_(link);
    var row = linksRows_(sh).filter(function (o) { return linkKey_(o.link) === k; })[0];
    if (!row) return { ok: false, error: 'Link not found' };
    if (row.status !== 'to decide' && row.status !== 'error') return { ok: false, error: 'Only a held or errored link can be dismissed' };
    sh.deleteRow(row.row);
    return { ok: true };
  });
}

// The reason behind a held finding, for the dashboard's popup. Read on demand so the list stays small.
function linksDetail_(link) {
  return withLock_(function () {
    var k = linkKey_(link);
    var hit = linksRows_(linksSheet_()).filter(function (o) { return linkKey_(o.link) === k; })[0];
    if (!hit) return { ok: false, error: 'Link not found' };
    if (!hit.held) return { ok: false, error: 'No finding is held for this link' };
    var r = JSON.parse(hit.held);
    return { ok: true, status: hit.status, detail: {
      link: hit.link, brand: r.brand || '', date: r.date || '',
      verdict: /^Risky/i.test(String(r.violation_type || '')) ? 'Risky' : 'Unacceptable',
      confidence: r.confidence, violation_type: String(r.violation_type || '').replace(/^Risky:\s*/i, ''),
      product: r.product_name || '', reason: r.violation_reason || '', text: String(r.extracted_text || '').slice(0, 3000),
      screenshot: r.screenshot_link || '', description: r.complaint_description || '', remarks: r.remarks || '' } };
  });
}
