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
 * Status is the whole state machine:
 *   ''                        waiting to be picked up (TikTok video links only)
 *   'queued <UTC ISO>'        claimed by a run that has not started reading yet
 *   'running'                 a run is reading it (D carries the run number)
 *   'done' | 'error'          finished (E carries the one-line result)
 *   'not readable: …' | 'skipped: …' | 'not a TikTok video link'   decided on arrival, never queued
 */
var LINKS_SHEET_NAME = 'Links';
var LINKS_HEADERS = ['Link (paste one per row)', 'Added / note', 'Status (filled by the system)', 'Run', 'Result', 'How to use'];
var LINKS_MAX_PER_RUN = 10;
var LINKS_STALE_MIN = 90;

function linksSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(LINKS_SHEET_NAME);
  if (!sh) {
    sh = ss.insertSheet(LINKS_SHEET_NAME);
    sh.getRange(1, 1, 1, LINKS_HEADERS.length).setValues([LINKS_HEADERS]).setFontWeight('bold');
    sh.setFrozenRows(1);
    [420, 200, 220, 90, 420, 360].forEach(function (w, i) { sh.setColumnWidth(i + 1, w); });
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
  return sh.getRange(2, 1, last - 1, 5).getValues().map(function (r, i) {
    return { row: i + 2, link: String(r[0] || '').trim(), note: String(r[1] || ''), status: String(r[2] || ''),
             run: String(r[3] || ''), result: String(r[4] || '') };
  }).filter(function (o) { return o.link; });
}

function linksList_() {
  return withLock_(function () {
    var rows = linksRows_(linksSheet_());
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
    var add = [], out = { ok: true, added: [], duplicates: [], rejected: [] };
    urls.forEach(function (u) {
      var k = linkKey_(u);
      if (have[k]) { out.duplicates.push({ link: u, status: have[k].status || 'waiting' }); return; }
      have[k] = { status: '' };
      var kind = classifyLink_(u);
      var status = kind === 'tiktok' ? '' : LINK_DECISIONS_[kind];
      add.push([u, String(note || 'Added in dashboard'), status, '', '']);
      (kind === 'tiktok' ? out.added : out.rejected).push({ link: u, status: status || 'waiting' });
    });
    if (add.length) {
      var start = Math.max(sh.getLastRow(), 1) + 1;
      sh.getRange(start, 1, add.length, 5).setValues(add);
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
      n++;
    });
    return { ok: true, updated: n, missing: missing };
  });
}
