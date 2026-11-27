// How many times the backend reads the WHOLE Complaints sheet for one dashboard call, using the real Code.gs against a
// fake spreadsheet that counts reads (Wan, 27 Nov 2026: "why status change or when click button the reaction is too
// slow"). A whole-sheet read is the slow part of every call, so the counts are the contract.
// Run: node tests/gas/speed_test.js   (exit code 1 on any failure)
const fs = require('fs'), vm = require('vm'), path = require('path');
const code = fs.readFileSync(path.join(__dirname, '..', '..', 'apps-script/Code.gs'), 'utf8');
let bad = 0; const check = (n, c, x) => { console.log((c ? 'PASS ' : 'FAIL ') + n + (c ? '' : '  -> ' + JSON.stringify(x))); if (!c) bad++; };

function world(nRows) {
  const reads = { whole: 0, column: 0, row: 0, writes: 0 }; let data = [];
  const sheet = {
    getLastRow: () => data.length + 1,
    getRange: (r, c, nr, nc) => ({
      getValues: () => { if (nr > 1 && nc > 1) reads.whole++; else if (nr > 1) reads.column++; else reads.row++;
        const o = []; for (let i = 0; i < nr; i++) { const row = []; for (let j = 0; j < nc; j++) row.push((data[r - 2 + i] || [])[c - 1 + j] ?? ''); o.push(row); } return o; },
      setValues: v => { reads.writes++; v.forEach((row, i) => { data[r - 2 + i] = row.slice(); }); }
    })
  };
  const ctx = {
    console: { log() {}, warn() {}, error() {} }, JSON, Math, Date, Object, Array, String, Number, RegExp, Error, isNaN, parseInt, parseFloat,
    PropertiesService: { getScriptProperties: () => ({ getProperty: () => '', setProperty: () => {} }) },
    SpreadsheetApp: { getActiveSpreadsheet: () => ({ getSheetByName: n => (n === 'Complaints' ? sheet : null), insertSheet: () => sheet }) },
    LockService: { getScriptLock: () => ({ waitLock: () => {}, releaseLock: () => {} }) },
    Utilities: { formatDate: (d) => new Date(d.getTime() + 8 * 3600000).toISOString().slice(0, 10) },
  };
  vm.createContext(ctx); vm.runInContext(code, ctx, { filename: 'Code.gs' });
  const H = vm.runInContext('HEADERS', ctx), COL = vm.runInContext('COL', ctx);
  data = Array.from({ length: nRows }, (_, i) => { const row = H.map(() => ''); row[COL['ID']] = 'KKM-' + i; row[COL['Brand']] = 'Eucerin'; row[COL['Platform']] = 'Instagram';
    row[COL['Post URL']] = 'https://x/' + i; row[COL['Status']] = i % 3 ? 'New' : 'In-Progress'; row[COL['Jenis Aduan']] = 'Iklan Kosmetik'; return row; });
  // every call is a new request in Apps Script: globals start again
  const call = (src) => { vm.runInContext('READ_ONCE_ = false; ALL_ROWS_ = null;', ctx); Object.keys(reads).forEach(k => reads[k] = 0); const out = vm.runInContext(src, ctx); return { out, reads: Object.assign({}, reads) }; };
  ctx.checkToken_ = () => false; ctx.isAuthorisedViewer_ = () => true;
  return { call, COL, row: i => data[i] };
}

{ const w = world(60);
  let r = w.call(`handleApi_('update_status', { id: 'KKM-1', status: 'Dismissed', lite: 1 }, { key: 'k' })`);
  check('a status change from the dashboard (lite) reads the whole sheet 0 times', r.reads.whole === 0 && r.reads.writes === 1, r.reads);
  check('  ...and still answers with the changed row, no stats', r.out.ok && r.out.row['Status'] === 'Dismissed' && r.out.stats === undefined, Object.keys(r.out));
  check('  ...and the sheet row really changed', w.row(1)[w.COL['Status']] === 'Dismissed');
  r = w.call(`handleApi_('update_status', { id: 'KKM-2', status: 'Dismissed' }, { key: 'k' })`);
  check('without lite (an older page, the scraper) the answer still carries stats', r.out.ok && r.out.stats && r.out.stats.counts.Dismissed === 2 && r.reads.whole === 1, r.reads);
  r = w.call(`handleApi_('update_status', { id: 'KKM-2', status: 'Nonsense', lite: 1 }, { key: 'k' })`);
  check('an unknown status is still refused', r.out.ok === false && r.reads.writes === 0);
  r = w.call(`handleApi_('update_status', { id: 'KKM-nope', status: 'New', lite: 1 }, { key: 'k' })`);
  check('an unknown ID is still refused', r.out.ok === false && r.reads.writes === 0); }

{ const w = world(60);
  let r = w.call(`handleApi_('open', { id: 'KKM-4' }, { key: 'k' })`);
  check('opening a New complaint moves it to In-Progress with 0 whole-sheet reads and 1 write', r.out.row['Status'] === 'In-Progress' && r.reads.whole === 0 && r.reads.writes === 1 && r.reads.row === 2, r.reads);
  r = w.call(`handleApi_('open', { id: 'KKM-3' }, { key: 'k' })`);
  check('opening one already In-Progress writes nothing', r.out.row['Status'] === 'In-Progress' && r.reads.writes === 0 && r.reads.whole === 0, r.reads);
  r = w.call(`(function () { try { return handleApi_('open', { id: 'KKM-nope' }, { key: 'k' }); } catch (e) { return { ok: false, error: String(e.message) }; } })()`);
  check('opening an ID that is not in the sheet fails', r.out.ok === false, r.out); }

{ const w = world(60);
  let r = w.call(`handleApi_('list', {}, { key: 'k' })`);
  check('list reads the whole sheet once (was twice)', r.reads.whole === 1 && r.out.rows.length === 60 && r.out.stats.counts.total === 60, r.reads);
  r = w.call(`handleApi_('bootstrap', {}, { key: 'k' })`);
  check('bootstrap reads the whole sheet once (was three times)', r.reads.whole === 1 && r.out.rows.length === 60 && r.out.summary.byStatus.total === 60 && r.out.stats.counts.New === 40, r.reads);
  r = w.call(`apiBootstrap('k')`);
  check('the Apps Script-hosted page boots with one read too', r.reads.whole === 1 && r.out.rows.length === 60, r.reads);
  // a write followed by a count, in ONE request, must count what was written (the one-read copy is for read-only calls)
  r = w.call(`(function () { var a = stats_().counts.Dismissed; updateStatus_('KKM-7', 'Dismissed'); return [a, stats_().counts.Dismissed]; })()`);
  check('a call that writes never counts from a stale copy', r.out[0] === 0 && r.out[1] === 1, r.out); }
process.exit(bad ? 1 : 0);
