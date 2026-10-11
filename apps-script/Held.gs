/**
 * KKM Cosmetic Complaint System — Held tab, the dashboard's "To check" segment (Wan, 11 Oct 2026).
 *
 * ADD THIS AS A NEW FILE in the Apps Script editor (+ → Script → name it Held), paste this file, then
 * re-paste Code.gs (its switch carries the held_* cases) and Deploy → Manage deployments → New version.
 *
 * Wan: "for held post and to check by eye, do put different segment, so I can check and process further -
 * dismiss, put to another route or as cosmetic ads".
 *
 * A held post is one the reviewer called Unacceptable or Risky but whose reasoning quoted wording that is not in the
 * caption the scraper captured (the shape of a hallucinated verdict), so the run did NOT file it. Until now it lived
 * only in the run log. It now waits here, in its own tab, with the whole record, until Wan has looked at the post
 * and decided:
 *   Dismiss          the verdict was wrong or the post is fine; nothing is filed
 *   File as cosmetic ad   it goes to Complaints as Iklan Kosmetik, exactly as the scraper would have filed it
 *   Route to ...     it goes to Complaints on another route (MDA, MMC, Makanan, ...), with the description
 *                    rewritten for that route
 * The reviewed-posts ledger already holds every post a run has judged, held ones included, so a dismissed post
 * is not found and held a second time.
 *
 * Columns:  A Post URL  B Account  C Platform  D Verdict  E Confidence  F Violation type  G Product
 *           H Why held  I Found (date or run)  J Status  K Record (JSON, system use)  L Updated
 * Status:   'to check' | 'filed' | 'dismissed'. Filed and dismissed rows stay in the tab as history; only
 *           'to check' rows are listed.
 */
var HELD_SHEET_NAME = 'Held';
var HELD_HEADERS = ['Post URL', 'Account', 'Platform', 'Verdict', 'Confidence', 'Violation type', 'Product',
                    'Why held', 'Found', 'Status', 'Record (system)', 'Updated'];
var HELD_MAX_ROWS = 300;

function heldSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(HELD_SHEET_NAME);
  if (!sh) {
    sh = ss.insertSheet(HELD_SHEET_NAME);
    sh.getRange(1, 1, 1, HELD_HEADERS.length).setValues([HELD_HEADERS]).setFontWeight('bold');
    sh.setFrozenRows(1);
    [420, 160, 90, 100, 90, 230, 200, 420, 150, 100, 120, 170].forEach(function (w, i) { sh.setColumnWidth(i + 1, w); });
  }
  return sh;
}

function heldRows_(sh) {
  var last = sh.getLastRow();
  if (last < 2) return [];
  return sh.getRange(2, 1, last - 1, HELD_HEADERS.length).getValues().map(function (r, i) {
    return { row: i + 2, url: String(r[0] || '').trim(), account: String(r[1] || ''), platform: String(r[2] || ''),
             verdict: String(r[3] || ''), confidence: r[4], type: String(r[5] || ''), product: String(r[6] || ''),
             why: String(r[7] || ''), found: String(r[8] || ''), status: String(r[9] || ''), record: String(r[10] || ''),
             updated: String(r[11] || '') };
  }).filter(function (o) { return o.url; });
}

function heldKey_(u) { return canonicalUrl_(String(u || '').trim()); }

function heldFind_(sh, url) {
  var k = heldKey_(url);
  return heldRows_(sh).filter(function (o) { return heldKey_(o.url) === k; })[0] || null;
}

function heldVerdict_(r) {
  return /^Risky/i.test(String(r.violation_type || r['Violation Type'] || '')) ? 'Risky' : 'Unacceptable';
}

// Called by the scraper (API_TOKEN) with the records of posts it held back. Idempotent: a post already listed here, or
// already in Complaints, is skipped, so a re-run or a retry never doubles a card.
function heldAdd_(records) {
  if (!Array.isArray(records) || !records.length) return { ok: false, error: 'No records supplied' };
  return withLock_(function () {
    var sh = heldSheet_();
    var have = {};
    heldRows_(sh).forEach(function (o) { have[heldKey_(o.url)] = true; });
    var known = {};
    try { known = knownUrlSet_(sheet_()); } catch (e) { /* carry on without the Complaints check */ }
    var add = [], skipped = [];
    records.forEach(function (rec) {
      var url = String(rec.post_url || rec['Post URL'] || rec.url || '').trim();
      if (!url) return;
      var k = heldKey_(url);
      if (have[k] || known[k]) { skipped.push(url); return; }
      have[k] = true;
      var body = JSON.stringify(rec);
      if (body.length > 45000) {            // one cell holds 50,000 characters: cut the longest text, never the JSON
        rec = JSON.parse(JSON.stringify(rec));
        rec.extracted_text = String(rec.extracted_text || '').slice(0, 6000);
        rec.violation_reason = String(rec.violation_reason || '').slice(0, 6000);
        body = JSON.stringify(rec);
      }
      var conf = rec.confidence != null && rec.confidence !== '' ? Number(rec.confidence) : '';
      add.push([url, String(rec.brand || rec['Brand'] || ''), String(rec.platform || rec['Platform'] || ''),
                heldVerdict_(rec), isNaN(conf) ? '' : conf,
                String(rec.violation_type || rec['Violation Type'] || '').replace(/^Risky:\s*/i, ''),
                String(rec.product_name || rec['Nama Kosmetik'] || ''),
                String(rec.held_why || 'Needs a look at the post: the reviewer quoted wording that is not in the captured caption'),
                String(rec.held_found || nowIso_().slice(0, 10)), 'to check', body, nowIso_()]);
    });
    if (add.length) {
      sh.getRange(Math.max(sh.getLastRow(), 1) + 1, 1, add.length, HELD_HEADERS.length).setValues(add);
    }
    return { ok: true, added: add.length, skipped: skipped.length };
  });
}

function heldList_() {
  return withLock_(function () {
    var sh = heldSheet_();
    var rows = heldRows_(sh).filter(function (o) { return o.status === 'to check'; }).map(function (o) {
      var rec = {};
      try { rec = o.record ? JSON.parse(o.record) : {}; } catch (e) {}
      return { url: o.url, account: o.account, platform: o.platform, verdict: o.verdict, confidence: o.confidence,
               type: o.type, product: o.product, why: o.why, found: o.found,
               date: String(rec.date || ''), hasRecord: !!o.record,
               reason: String(rec.violation_reason || '').slice(0, 700), screenshot: String(rec.screenshot_link || '') };
    });
    rows.reverse();                                            // newest first
    var done = heldRows_(sh).filter(function (o) { return o.status !== 'to check'; }).length;
    return { ok: true, held: rows.slice(0, HELD_MAX_ROWS), total: rows.length, handled: done };
  });
}

// The whole reason and caption, read on demand so the list stays small.
function heldDetail_(url) {
  return withLock_(function () {
    var hit = heldFind_(heldSheet_(), url);
    if (!hit) return { ok: false, error: 'Post not found in the Held tab' };
    var r = {};
    try { r = hit.record ? JSON.parse(hit.record) : {}; } catch (e) { return { ok: false, error: 'The held record is unreadable' }; }
    return { ok: true, status: hit.status, detail: {
      url: hit.url, account: hit.account, platform: hit.platform, verdict: hit.verdict, confidence: hit.confidence,
      type: hit.type, product: hit.product, why: hit.why, found: hit.found, date: r.date || '',
      reason: r.violation_reason || '', text: String(r.extracted_text || '').slice(0, 3000),
      screenshot: r.screenshot_link || '', remarks: r.remarks || '' } };
  });
}

// route: a value of ROUTES. Defaults to the cosmetic route, which is "File as cosmetic ad".
// The insert takes the script lock itself, so it runs OUTSIDE ours (the same order as linksFile_).
function heldFile_(url, route) {
  route = route ? String(route) : 'Iklan Kosmetik';
  if (JENIS_ADUAN.indexOf(route) < 0) return { ok: false, error: 'Unknown route: ' + route };
  var hit = withLock_(function () { return heldFind_(heldSheet_(), url); });
  if (!hit) return { ok: false, error: 'Post not found in the Held tab' };
  if (hit.status !== 'to check') return { ok: false, error: 'This post was already handled (' + hit.status + ')' };
  if (!hit.record) return { ok: false, error: 'No record is stored for this post' };
  var rec = JSON.parse(hit.record);
  rec.post_url = rec.post_url || hit.url;
  rec.jenis_aduan = route;
  if (route !== 'Iklan Kosmetik') {
    // The stored description is the cosmetic one; the insert writes a fresh one for the chosen route.
    delete rec.complaint_description; delete rec.deskripsi_aduan; delete rec['Deskripsi Aduan'];
  }
  rec.remarks = 'Checked by Wan (' + Utilities.formatDate(new Date(), TZ, 'yyyy-MM-dd') + '), was held for a visual check. '
              + String(rec.remarks || '');
  var res = apiInsertRecords_([rec], 'scraper');
  var note;
  if (res.inserted) note = 'added to Complaints as ' + route + ' (' + (res.ids && res.ids[0] || '') + ')';
  else if (res.duplicates && res.duplicates.length) note = 'already in Complaints';
  else return { ok: false, error: (res.errors && res.errors[0] && res.errors[0].error) || res.error || 'The insert did not go through' };
  return withLock_(function () {
    var sh = heldSheet_(), row = heldFind_(sh, url);
    if (row) sh.getRange(row.row, 10, 1, 3).setValues([['filed', row.record, nowIso_()]]);
    return { ok: true, result: note, id: res.ids && res.ids[0] || '' };
  });
}

function heldDismiss_(url) {
  return withLock_(function () {
    var sh = heldSheet_(), row = heldFind_(sh, url);
    if (!row) return { ok: false, error: 'Post not found in the Held tab' };
    if (row.status !== 'to check') return { ok: true, result: 'already ' + row.status };
    sh.getRange(row.row, 10, 1, 3).setValues([['dismissed', row.record, nowIso_()]]);
    return { ok: true, result: 'dismissed' };
  });
}
