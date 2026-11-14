/**
 * Targets panel and per-account status (Wan, 14 Nov 2026: "can I add/remove/edit accounts targets for all
 * platforms, and got status the account is run and live and tested").
 *
 * Paste this as a NEW file in the Apps Script editor (Targets.gs), re-paste Code.gs (it carries the four routes
 * below), then Deploy > Manage deployments > New version. Until then the dashboard's Targets panel says the backend
 * has no Targets update yet, and the account check prints its results instead of storing them.
 *
 *   targets_admin          viewer   every Targets row + the platform columns + each account's status
 *   target_save            viewer   add a row, or change one (matched by its Brand name)
 *   target_remove          viewer   delete a row (to pause one instead, save it with Active off)
 *   target_status_update   machine  the account check and the sweep write what they saw
 *
 * The Targets tab stays the one list the scraper reads; this only edits it the way a person would, one row at a
 * time, under the script lock. Status lives in its OWN tab, TargetStatus, because every extra column in Targets is
 * read as a platform. One status row per Brand + Platform:
 *
 *   State         live | renamed | empty | not_found | private | blocked | error   ('' = not checked yet)
 *   Checked at    when tools/account_check.py last visited the profile (UTC ISO)
 *   Last sweep …  when a sweep last reached the account, how many posts it saw, and its note
 *
 * Changing a handle clears that account's check, so the panel shows "not checked yet" until the next account
 * check, which takes never-checked accounts first. An own-brand name or handle is refused: this system never
 * watches Wan's own brands.
 */
var TSTATUS_SHEET_NAME = 'TargetStatus';
var TSTATUS_HEADERS = ['Brand', 'Platform', 'Handle', 'State', 'Detail', 'Posts seen', 'Checked at',
                       'Suggested handle', 'Last sweep at', 'Last sweep posts', 'Last sweep note'];
var TSTATUS_FIELDS = ['brand', 'platform', 'handle', 'state', 'detail', 'posts', 'checked_at',
                      'suggest', 'sweep_at', 'sweep_posts', 'sweep_note'];
var TSTATUS_STATES = ['live', 'renamed', 'empty', 'not_found', 'private', 'blocked', 'error'];
var TSTATUS_CHECK_FIELDS = ['state', 'detail', 'posts', 'checked_at', 'suggest'];

function targetStatusSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(TSTATUS_SHEET_NAME);
  if (!sh) {
    sh = ss.insertSheet(TSTATUS_SHEET_NAME);
    sh.getRange(1, 1, sh.getMaxRows(), TSTATUS_HEADERS.length).setNumberFormat('@');   // text: a time must stay as written
    sh.getRange(1, 1, 1, TSTATUS_HEADERS.length).setValues([TSTATUS_HEADERS]).setFontWeight('bold');
    sh.setFrozenRows(1);
    [200, 100, 200, 90, 420, 90, 170, 160, 170, 110, 320].forEach(function (w, i) { sh.setColumnWidth(i + 1, w); });
  }
  return sh;
}

function tstatusKey_(brand, platform) {
  return (String(brand || '').trim() + '\u0001' + String(platform || '').trim()).toLowerCase();
}

function tstatusText_(v) {
  if (v instanceof Date) return v.toISOString().replace(/\.\d+Z$/, 'Z');
  return String(v === null || v === undefined ? '' : v);
}

// [{brand, platform, handle, state, ...}] for every status row.
function targetStatusRows_(sh) {
  var last = sh.getLastRow();
  if (last < 2) return [];
  return sh.getRange(2, 1, last - 1, TSTATUS_HEADERS.length).getValues().map(function (r) {
    var o = {};
    TSTATUS_FIELDS.forEach(function (f, i) { o[f] = tstatusText_(r[i]).trim(); });
    return o;
  }).filter(function (o) { return o.brand && o.platform; });
}

function targetStatusWrite_(sh, rows) {
  var last = sh.getLastRow();
  if (last > 1) sh.getRange(2, 1, last - 1, TSTATUS_HEADERS.length).clearContent();
  if (!rows.length) return;
  sh.getRange(2, 1, rows.length, TSTATUS_HEADERS.length).setNumberFormat('@').setValues(rows.map(function (o) {
    return TSTATUS_FIELDS.map(function (f) { return o[f] === undefined || o[f] === null ? '' : String(o[f]); });
  }));
}

// A cell must never become a formula, and one line is one line.
function targetText_(v, max) {
  return String(v === null || v === undefined ? '' : v).replace(/[\r\n\t]+/g, ' ').trim()
    .replace(/^[=+]+/, '').slice(0, max || 300);
}

function targetOwnBrand_(text) {
  var hay = String(text || '').toLowerCase();
  for (var i = 0; i < OWN_BRANDS.length; i++) {
    for (var j = 0; j < OWN_BRANDS[i].keys.length; j++) {
      if (hay.indexOf(OWN_BRANDS[i].keys[j]) >= 0) return OWN_BRANDS[i].name;
    }
  }
  return '';
}

// 'name', '@name', a profile link, or a Facebook numeric id -> the bare handle; null when it is none of those.
function targetHandle_(raw) {
  var v = String(raw === null || raw === undefined ? '' : raw).trim();
  if (!v) return '';
  if (v.length > 300 || /[\s<>"'`]/.test(v)) return null;
  var id = v.match(/[?&]id=(\d{5,})/);
  if (id) return id[1];
  var asUrl = /^https?:\/\//i.test(v) ? v : (/^[a-z0-9.\-]+\.(com|net)\//i.test(v) ? 'https://' + v : '');
  var bare = asUrl ? sanitizeHandle_(asUrl) : v.replace(/^@/, '');
  return /^[A-Za-z0-9._\-]{1,100}$/.test(bare) ? bare : null;
}

function targetColumns_(sheet) {
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0].map(function (h) { return String(h).trim(); });
  var platforms = headers.filter(function (h) {
    return h && TARGET_FIXED_HEAD.indexOf(h) < 0 && TARGET_FIXED_TAIL.indexOf(h) < 0 && TARGET_META_COLUMNS.indexOf(h) < 0;
  });
  return { headers: headers, platforms: platforms };
}

function targetsAdmin_() {
  var targets = targets_();                                          // also creates the tab on a fresh sheet
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(TARGET_SHEET_NAME);
  var cols = targetColumns_(sheet);
  var status = {};
  targetStatusRows_(targetStatusSheet_()).forEach(function (o) {
    var s = {};
    TSTATUS_FIELDS.slice(2).forEach(function (f) { s[f] = o[f]; });
    status[tstatusKey_(o.brand, o.platform)] = s;
  });
  var types = {};
  targets.forEach(function (t) { if (t.type) types[t.type] = 1; });
  return { ok: true, platforms: cols.platforms, types: Object.keys(types).sort(),
           has: { type: cols.headers.indexOf('Type') >= 0, website_feed: cols.headers.indexOf('Website feed') >= 0,
                  product_feed: cols.headers.indexOf('Product feed') >= 0 },
           targets: targets, status: status };
}

function targetFindRow_(rows, iBrand, name) {
  var n = String(name || '').trim().toLowerCase();
  if (!n) return -1;
  for (var i = 0; i < rows.length; i++) {
    if (String(rows[i][iBrand] || '').trim().toLowerCase() === n) return i;
  }
  return -1;
}

// Drop the status rows of a brand: all of them (platforms omitted) or only the named platforms.
function targetStatusDrop_(brand, platforms) {
  var sh = targetStatusSheet_();
  var rows = targetStatusRows_(sh);
  var b = String(brand || '').trim().toLowerCase();
  var want = platforms ? platforms.map(function (p) { return String(p).toLowerCase(); }) : null;
  var keep = rows.filter(function (o) {
    if (o.brand.toLowerCase() !== b) return true;
    return want ? want.indexOf(o.platform.toLowerCase()) < 0 : false;
  });
  if (keep.length !== rows.length) targetStatusWrite_(sh, keep);
}

function targetSave_(t, original) {
  t = t || {};
  var name = targetText_(t.name, 80);
  original = String(original || '').trim();
  if (!name) return { ok: false, error: 'A name is required (the brand, or the account as you want it shown).' };
  if (original && name.toLowerCase() !== original.toLowerCase()) {
    return { ok: false, error: 'A row cannot be renamed here, because filed complaints carry its name. Add a new row and remove the old one.' };
  }
  var given = t.handles || {};
  var own = targetOwnBrand_(name + ' ' + Object.keys(given).map(function (k) { return given[k]; }).join(' '));
  if (own) return { ok: false, error: own + ' is one of your own brands. Own brands are never watched by this system.' };

  return withLock_(function () {
    var sheet = ensureTargets_(SpreadsheetApp.getActiveSpreadsheet());
    var cols = targetColumns_(sheet);
    var headers = cols.headers;
    var ix = function (h) { return headers.indexOf(h); };
    var last = sheet.getLastRow();
    var rows = last > 1 ? sheet.getRange(2, 1, last - 1, headers.length).getValues() : [];
    var at = original ? targetFindRow_(rows, ix('Brand'), original) : -1;
    if (original && at < 0) return { ok: false, error: 'That row is no longer in the Targets tab. Refresh and try again.' };
    if (!original && targetFindRow_(rows, ix('Brand'), name) >= 0) {
      return { ok: false, error: '"' + name + '" is already in the Targets tab. Edit that row instead.' };
    }
    var row = at >= 0 ? rows[at].slice() : headers.map(function () { return ''; });
    if (at < 0) row[ix('Brand')] = name;
    if (ix('Active') >= 0) row[ix('Active')] = t.active !== false;

    var changed = [];
    for (var plat in given) {
      if (!Object.prototype.hasOwnProperty.call(given, plat)) continue;
      var col = cols.platforms.indexOf(plat) >= 0 ? ix(plat) : -1;
      if (col < 0) {
        if (!String(given[plat] || '').trim()) continue;
        return { ok: false, error: 'There is no "' + plat + '" column in the Targets tab. A new platform needs a column and a reader first.' };
      }
      var bare = targetHandle_(given[plat]);
      if (bare === null) return { ok: false, error: plat + ': "' + String(given[plat]).slice(0, 60) + '" is not a handle or a profile link.' };
      var before = sanitizeHandle_(row[col]);
      if (bare.toLowerCase() !== String(before).toLowerCase()) { row[col] = bare; changed.push(plat); }
    }
    var hasHandle = cols.platforms.some(function (p) { return sanitizeHandle_(row[ix(p)]); });
    var text = { 'Type': t.type, 'Notes': t.notes, 'Website feed': t.website_feed, 'Product feed': t.product_feed,
                 'Product hints': Array.isArray(t.product_hints) ? t.product_hints.join(', ') : t.product_hints };
    Object.keys(text).forEach(function (h) {
      if (text[h] === undefined || text[h] === null || ix(h) < 0) return;
      row[ix(h)] = targetText_(text[h], h === 'Type' ? 80 : 1000);
    });
    if (!hasHandle && !String(row[ix('Type')] || '').trim()) {
      return { ok: false, error: 'Give at least one handle. (A website row needs its Type instead.)' };
    }
    sheet.getRange((at >= 0 ? at : rows.length) + 2, 1, 1, headers.length).setValues([row]);
    if (changed.length) targetStatusDrop_(name, changed);      // a new handle has not been tested yet
    return { ok: true, name: at >= 0 ? String(rows[at][ix('Brand')]).trim() : name, added: at < 0, changed: changed };
  });
}

function targetRemove_(name) {
  name = String(name || '').trim();
  if (!name) return { ok: false, error: 'No row named.' };
  return withLock_(function () {
    var sheet = ensureTargets_(SpreadsheetApp.getActiveSpreadsheet());
    var headers = targetColumns_(sheet).headers;
    var last = sheet.getLastRow();
    var rows = last > 1 ? sheet.getRange(2, 1, last - 1, headers.length).getValues() : [];
    var at = targetFindRow_(rows, headers.indexOf('Brand'), name);
    if (at < 0) return { ok: false, error: 'That row is no longer in the Targets tab.' };
    sheet.deleteRow(at + 2);
    targetStatusDrop_(name, null);
    return { ok: true, removed: String(rows[at][headers.indexOf('Brand')]).trim() };
  });
}

// updates: [{brand, platform, handle?, state?, detail?, posts?, checked_at?, suggest?, sweep_at?, sweep_posts?, sweep_note?}]
// Only the fields an update carries are written, so the account check and the sweep never erase each other.
function targetStatusUpdate_(updates) {
  updates = (updates || []).slice(0, 600);
  if (!updates.length) return { ok: true, updated: 0 };
  return withLock_(function () {
    var sh = targetStatusSheet_();
    var rows = targetStatusRows_(sh);
    var at = {};
    rows.forEach(function (o, i) { at[tstatusKey_(o.brand, o.platform)] = i; });
    var n = 0;
    updates.forEach(function (u) {
      u = u || {};
      var brand = targetText_(u.brand, 80), platform = targetText_(u.platform, 30);
      if (!brand || !platform) return;
      if (u.state !== undefined && TSTATUS_STATES.indexOf(String(u.state)) < 0) return;
      var k = tstatusKey_(brand, platform);
      var o = at[k] === undefined ? null : rows[at[k]];
      if (!o) { o = { brand: brand, platform: platform }; TSTATUS_FIELDS.slice(2).forEach(function (f) { o[f] = ''; }); at[k] = rows.length; rows.push(o); }
      var handle = u.handle === undefined ? '' : targetText_(u.handle, 100);
      if (handle && o.handle && handle.toLowerCase() !== o.handle.toLowerCase()) {
        // The row was pointed at another account: what was recorded about the old one does not describe this one.
        TSTATUS_FIELDS.slice(3).forEach(function (f) { o[f] = ''; });
      }
      if (handle) o.handle = handle;
      TSTATUS_FIELDS.slice(3).forEach(function (f) {
        if (u[f] === undefined || u[f] === null) return;
        o[f] = (f === 'posts' || f === 'sweep_posts') ? String(Math.max(0, parseInt(u[f], 10) || 0)) : targetText_(u[f], 400);
      });
      n++;
    });
    targetStatusWrite_(sh, rows);
    return { ok: true, updated: n };
  });
}
