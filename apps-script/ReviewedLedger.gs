/**
 * KKM Cosmetic Complaint System — Reviewed ledger.
 *
 * ADD THIS AS A NEW FILE in the Apps Script editor (+ → Script → name it ReviewedLedger),
 * then add these two lines to the action switch in Code.gs, next to case 'known_urls':
 *
 *     case 'seen_urls':     if (!viewer) return deny;
 *                           return { ok: true, urls: seenUrls_() };
 *     case 'mark_seen':     if (!machine) return deny;
 *                           return markSeen_(body.entries || []);
 *
 * Then Deploy → Manage deployments → New version. Saving alone changes nothing.
 *
 * The Reviewed tab creates itself on first use — no need to re-run setup().
 * It uses canonicalUrl_() from Code.gs; Apps Script shares one global scope across files.
 */

// ---------------------------------------------------------------------------
// Reviewed ledger
//
// The Complaints tab only ever holds posts that turned out to be non-compliant, so without a
// separate record a compliant post is scraped, screenshotted and paid for again on every run,
// and a complaint already dealt with can come back around. This tab holds one row per post the
// scraper has judged, whatever the verdict. It is a checklist, not a findings record: nothing
// here is a complaint, and dismissing a complaint does not remove it from here.
// ---------------------------------------------------------------------------
var REVIEWED_SHEET_NAME = 'Reviewed';
var REVIEWED_HEADERS = ['Post URL', 'Brand', 'Platform', 'Verdict', 'Confidence', 'Reviewer', 'First seen'];

function reviewedSheet_() {
  var ss = SpreadsheetApp.getActive();
  var sh = ss.getSheetByName(REVIEWED_SHEET_NAME);
  if (!sh) {
    sh = ss.insertSheet(REVIEWED_SHEET_NAME);
    sh.getRange(1, 1, 1, REVIEWED_HEADERS.length).setValues([REVIEWED_HEADERS]).setFontWeight('bold');
    sh.setFrozenRows(1);
  }
  return sh;
}

function seenUrlSet_() {
  var sh = reviewedSheet_();
  var set = {};
  var last = sh.getLastRow();
  if (last < 2) return set;
  sh.getRange(2, 1, last - 1, 1).getValues().forEach(function (r) {
    if (r[0]) set[canonicalUrl_(String(r[0]))] = true;
  });
  return set;
}

function seenUrls_() {
  return Object.keys(seenUrlSet_());
}

function markSeen_(entries) {
  if (!entries || !entries.length) return { ok: true, added: 0 };
  var sh = reviewedSheet_();
  var already = seenUrlSet_();
  var now = new Date();
  var rows = [];
  entries.forEach(function (e) {
    var url = String((e && e.url) || '').trim();
    if (!url) return;
    var key = canonicalUrl_(url);
    if (already[key]) return;   // never write the same post twice
    already[key] = true;
    rows.push([url, e.brand || '', e.platform || '', e.verdict || '',
               e.confidence === undefined || e.confidence === null ? '' : e.confidence,
               e.reviewer || '', now]);
  });
  if (rows.length) sh.getRange(sh.getLastRow() + 1, 1, rows.length, REVIEWED_HEADERS.length).setValues(rows);
  return { ok: true, added: rows.length };
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
