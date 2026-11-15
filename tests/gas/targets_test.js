// Targets.gs against a fake spreadsheet, using the REAL targets_(), ensureTargets_() and sanitizeHandle_() from
// Code.gs. Run: node tests/gas/targets_test.js   (exit code 1 on any failure)
const fs = require('fs'), vm = require('vm'), path = require('path');
const REPO = path.join(__dirname, '..', '..');
const code = fs.readFileSync(path.join(REPO, 'apps-script/Code.gs'), 'utf8');
function grabFn(name) {
  const i = code.indexOf('function ' + name + '('); if (i < 0) throw new Error('no ' + name);
  let d = 0; for (let k = code.indexOf('{', i); k < code.length; k++) { if (code[k] === '{') d++; else if (code[k] === '}') { d--; if (d === 0) return code.slice(i, k + 1); } }
}
function grabVar(name) { const m = code.match(new RegExp('var ' + name + ' = [\\s\\S]*?;\\n')); if (!m) throw new Error('no var ' + name); return m[0]; }

class Range {
  constructor(sh, r, c, nr, nc) { this.sh = sh; this.r = r; this.c = c; this.nr = nr || 1; this.nc = nc || 1; }
  getValues() { const o = []; for (let i = 0; i < this.nr; i++) { const row = []; for (let j = 0; j < this.nc; j++) row.push((this.sh.d[this.r - 1 + i] || [])[this.c - 1 + j] ?? ''); o.push(row); } return o; }
  setValues(v) {
    if (v.length !== this.nr || v[0].length !== this.nc) throw new Error('setValues shape ' + v.length + 'x' + v[0].length + ' vs ' + this.nr + 'x' + this.nc);
    for (let i = 0; i < this.nr; i++) { while (this.sh.d.length < this.r + i) this.sh.d.push([]); const row = this.sh.d[this.r - 1 + i];
      for (let j = 0; j < this.nc; j++) { while (row.length < this.c + j) row.push(''); const x = v[i][j]; if (typeof x === 'string' && /^[=+]/.test(x)) throw new Error('formula written: ' + x); row[this.c - 1 + j] = x; } }
    return this;
  }
  setValue(x) { return this.setValues([[x]]); }
  clearContent() { for (let i = 0; i < this.nr; i++) { const row = this.sh.d[this.r - 1 + i]; if (row) for (let j = 0; j < this.nc; j++) if (row.length >= this.c + j) row[this.c - 1 + j] = ''; } return this; }
  setFontWeight() { return this; } setBackground() { return this; } setFontColor() { return this; } setDataValidation() { return this; } setNumberFormat() { return this; }
}
class Sheet {
  constructor(n) { this.n = n; this.d = []; }
  getLastRow() { let n = this.d.length; while (n > 0 && (this.d[n - 1] || []).every(x => x === '' || x === undefined)) n--; return n; }
  getLastColumn() { return Math.max(0, ...this.d.map(r => { let n = r.length; while (n > 0 && (r[n - 1] === '' || r[n - 1] === undefined)) n--; return n; })); }
  getMaxRows() { return 1000; } getRange(r, c, nr, nc) { return new Range(this, r, c, nr, nc); }
  setFrozenRows() {} setColumnWidth() {} deleteRow(r) { this.d.splice(r - 1, 1); }
}
const book = {};
global.SpreadsheetApp = { getActiveSpreadsheet: () => ({ getSheetByName: n => book[n] || null, insertSheet: n => (book[n] = new Sheet(n)) }), newDataValidation: () => ({ requireCheckbox() { return this; }, build() { return {}; } }) };
global.withLock_ = fn => fn();
for (const v of ['TARGET_SHEET_NAME', 'TARGET_FIXED_HEAD', 'TARGET_FIXED_TAIL', 'TARGET_META_COLUMNS', 'DEFAULT_TARGET_PLATFORMS', 'OWN_BRANDS']) vm.runInThisContext(grabVar(v));
global.DEFAULT_TARGETS = [];
for (const f of ['ensureTargets_', 'sanitizeHandle_', 'targets_', 'isWebLink_']) vm.runInThisContext(grabFn(f));
vm.runInThisContext(fs.readFileSync(path.join(REPO, 'apps-script/Targets.gs'), 'utf8'));

let bad = 0; const check = (n, c, x) => { console.log((c ? 'PASS ' : 'FAIL ') + n + (c ? '' : '  -> ' + JSON.stringify(x))); if (!c) bad++; };
const T = book['Targets'] = new Sheet('Targets');
T.d = [['Brand', 'Active', 'Instagram', 'Facebook', 'Threads', 'Product hints', 'Notes', 'Type', 'Website feed', 'Product feed', 'TikTok'],
  ['La Roche-Posay', true, 'larocheposaymalaysia', 'https://www.facebook.com/larocheposaymalaysia/', '', 'Effaclar, Cicaplast', '', '', '', '', 'larocheposay_my'],
  ['drx', true, 'drx', '', 'drx', '', '', 'General Doctor', '', '', ''],
  ['Guardian MY', true, '', '', '', '', '', 'Guardian MY', 'https://guardian.com.my/feed', '', '']];
const K = tstatusKey_;

let a = targetsAdmin_();
check('admin: platforms from headers', JSON.stringify(a.platforms) === JSON.stringify(['Instagram', 'Facebook', 'Threads', 'TikTok']), a.platforms);
check('admin: targets, types, states, no status, no undo', a.targets.length === 3 && a.types.join() === 'General Doctor,Guardian MY' && a.states.includes('limited') && Object.keys(a.status).length === 0 && a.undo === null, a);
check('admin: TargetStatus tab created with headers', book['TargetStatus'].d[0].join() === TSTATUS_HEADERS.join());

const H = targetHandle_;
check('handle forms', H('@cerave') === 'cerave' && H('https://www.instagram.com/cerave/?hl=en') === 'cerave' && H('https://www.tiktok.com/@o.two.o_my') === 'o.two.o_my' && H('tiktok.com/@abc') === 'abc' && H('https://www.facebook.com/profile.php?id=100064812345678') === '100064812345678' && H('') === '');
check('handle refused', H('see bio') === null && H('=HYPERLINK("x")') === null && H('a/b/c') === null && H('<script>') === null);

let r = targetSave_({ name: 'CeraVe', active: true, handles: { Instagram: '@cerave.my', TikTok: 'https://www.tiktok.com/@ceravemy', Facebook: '' }, product_hints: 'SA Cleanser, Moisturising Cream', type: '' });
check('add row', r.ok && r.added && T.getLastRow() === 5 && T.d[4][0] === 'CeraVe' && T.d[4][1] === true && T.d[4][2] === 'cerave.my' && T.d[4][10] === 'ceravemy' && T.d[4][5] === 'SA Cleanser, Moisturising Cream', [r, T.d[4]]);
check('add duplicate refused', !targetSave_({ name: 'cerave', handles: { Instagram: 'x' } }).ok);
check('own brand refused by name', !targetSave_({ name: 'Valorith', handles: { Instagram: 'valorith.my' } }).ok);
check('own brand refused by handle', !targetSave_({ name: 'Some shop', handles: { Instagram: 'facerinna.official' } }).ok);
check('unknown platform refused', !targetSave_({ name: 'New1', handles: { YouTube: 'abc' } }).ok);
check('bad handle refused', !targetSave_({ name: 'New2', handles: { Instagram: 'not a handle' } }).ok);
check('no handle and no type refused', !targetSave_({ name: 'New3', handles: { Instagram: '' } }).ok);
check('formula in name neutralised', (() => { const x = targetSave_({ name: '=cmd|x', handles: { Instagram: 'okhandle' } }); return x.ok && T.d[T.getLastRow() - 1][0] === 'cmd|x'; })());
check('website row with type only', targetSave_({ name: 'Shop site', type: 'Brand website', website_feed: 'https://shop.example/feed', handles: {} }).ok);

r = targetStatusUpdate_([{ brand: 'CeraVe', platform: 'Instagram', handle: 'cerave.my', state: 'live', detail: '12 post(s) visible', posts: 12, checked_at: '2026-11-14T13:00:00Z', suggest: '' },
  { brand: 'La Roche-Posay', platform: 'Instagram', handle: 'larocheposaymalaysia', state: 'not_found', detail: 'not available', posts: 0, checked_at: '2026-11-14T13:00:05Z' },
  { brand: 'La Roche-Posay', platform: 'Facebook', handle: 'larocheposaymalaysia', state: 'limited', detail: 'signed-out view', posts: 1, checked_at: '2026-11-14T13:00:09Z' },
  { brand: 'X', platform: 'Instagram', state: 'bogus' }, { brand: '', platform: 'Instagram', state: 'live' }]);
check('status: 3 stored (limited accepted), bad state and blank brand skipped', r.updated === 3 && book['TargetStatus'].getLastRow() === 4, r);
targetStatusUpdate_([{ brand: 'cerave', platform: 'instagram', sweep_at: '2026-11-14T14:00:00Z', sweep_posts: 40, sweep_note: '' }]);
let s = targetsAdmin_().status[K('CeraVe', 'Instagram')];
check('status: sweep fields merge, check fields kept, no duplicate row', book['TargetStatus'].getLastRow() === 4 && s.state === 'live' && s.posts === '12' && s.sweep_posts === '40' && s.checked_at === '2026-11-14T13:00:00Z', s);

targetStatusUpdate_([{ brand: 'CeraVe', platform: 'TikTok', handle: 'ceravemy', state: 'live', posts: 13, checked_at: '2026-11-14T13:01:00Z' }]);
r = targetSave_({ name: 'CeraVe', active: false, handles: { Instagram: 'cerave', TikTok: 'ceravemy' }, notes: 'paused for now' }, 'CeraVe');
a = targetsAdmin_();
check('edit: row updated in place', r.ok && !r.added && r.changed.join() === 'Instagram' && T.d[4][2] === 'cerave' && T.d[4][1] === false && T.d[4][6] === 'paused for now' && T.d[4][5] === 'SA Cleanser, Moisturising Cream', [r, T.d[4]]);
check('edit: changed handle loses its check, the other keeps it', !a.status[K('CeraVe', 'Instagram')] && a.status[K('CeraVe', 'TikTok')].state === 'live');
check('edit: FB URL untouched when unchanged', (() => { const x = targetSave_({ name: 'La Roche-Posay', active: true, handles: { Instagram: 'larocheposaymy', Facebook: 'larocheposaymalaysia' } }, 'La Roche-Posay'); return x.ok && x.changed.join() === 'Instagram' && T.d[1][3] === 'https://www.facebook.com/larocheposaymalaysia/' && T.d[1][2] === 'larocheposaymy'; })(), T.d[1]);
check('rename refused', !targetSave_({ name: 'CeraVe MY', handles: {} }, 'CeraVe').ok);
check('edit of a missing row refused', !targetSave_({ name: 'Ghost', handles: { Instagram: 'g' } }, 'Ghost').ok);

targetStatusUpdate_([{ brand: 'drx', platform: 'Instagram', handle: 'drx', state: 'live', posts: 9, checked_at: '2026-11-14T13:02:00Z', sweep_at: '2026-11-13T00:00:00Z' }]);
targetStatusUpdate_([{ brand: 'drx', platform: 'Instagram', handle: 'drx_new', sweep_at: '2026-11-14T15:00:00Z', sweep_posts: 3 }]);
s = targetsAdmin_().status[K('drx', 'Instagram')];
check('status: a different handle resets the old record', s.handle === 'drx_new' && s.state === '' && s.sweep_posts === '3', s);

// ---- change log and undo
const L = book['TargetsLog'];
check('log: adds and edits recorded with before and after', L && L.d[0].join() === TLOG_HEADERS.join() && L.d.filter(x => x[1] === 'add').length === 3 && L.d.filter(x => x[1] === 'edit').length === 2 && JSON.parse(L.d.find(x => x[1] === 'edit' && x[2] === 'CeraVe')[3]).Instagram === 'cerave.my', L && L.d.map(x => x.slice(1, 3)));
const before = T.getLastRow(); const ceraveRow = T.d[4].slice();
r = targetRemove_('cerave'); a = targetsAdmin_();
check('remove: row and its status gone, others intact', r.ok && T.getLastRow() === before - 1 && !a.targets.some(t => t.name === 'CeraVe') && !Object.keys(a.status).some(k => k.startsWith('cerave')) && a.targets.some(t => t.name === 'drx'), a.status);
check('remove: offered for undo', a.undo && a.undo.name === 'CeraVe' && /^\d{4}-/.test(a.undo.at), a.undo);
r = targetUndo_(); a = targetsAdmin_();
const back = T.d[T.getLastRow() - 1];
check('undo: the same row is back, by column', r.ok && r.name === 'CeraVe' && JSON.stringify(back) === JSON.stringify(ceraveRow) && a.undo === null, [back, ceraveRow]);
check('undo: nothing left to undo', !targetUndo_().ok);
targetRemove_('drx'); targetSave_({ name: 'drx', handles: { Instagram: 'drx_again' }, type: 'General Doctor' });
check('undo: not offered when the name was added again', targetsAdmin_().undo === null && !targetUndo_().ok);
check('remove of a missing row refused', !targetRemove_('nobody').ok);
check('targets_() still reads the tab for the scraper', targets_().filter(t => t.active).length >= 3 && targets_()[0].handles.Facebook === 'larocheposaymalaysia');

// ---- link safety (Code.gs)
check('isWebLink_', isWebLink_('https://www.instagram.com/p/x/') && isWebLink_(' http://a.b/c') && !isWebLink_('javascript:alert(1)') && !isWebLink_('data:text/html,x') && !isWebLink_('') && !isWebLink_(null) && !isWebLink_('//evil.example') && !isWebLink_('https://'));
console.log('FAILURES:', bad); process.exit(bad ? 1 : 0);
