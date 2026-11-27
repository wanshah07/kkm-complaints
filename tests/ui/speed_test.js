// The dashboard with 8,600 complaints and a SLOW backend, in a real browser: a click must show at once, be sent behind
// it, and be put back if the backend refuses (Wan, 27 Nov 2026: "why status change or when click button the reaction
// is too slow"). Not part of CI (it needs Chromium).
//   cd web && python3 -m http.server 8766 &        then:   node tests/ui/speed_test.js
const { chromium } = require('playwright');
const N = 8600, SLOW = 2500, BR = ['La Roche-Posay', 'Eucerin', 'CeraVe', 'Nuuha Beauty'], PL = ['Instagram', 'Facebook', 'Threads', 'TikTok'];
const LONG = ('The post says the serum treats acne and eczema within seven days. ').repeat(12);            // 800+ characters
const mk = () => Array.from({ length: N }, (_, i) => ({ ID: 'KKM-20261114-' + (10000 + i), Date: '2026-11-' + String(1 + i % 27).padStart(2, '0'), Brand: BR[i % 4], Platform: PL[i % 4],
  'Post URL': 'https://www.instagram.com/p/ABC' + i, 'Screenshot Link': '', 'Extracted Text': LONG, 'Violation Type': 'Medicinal / disease claim', 'Violation Reason': LONG + ' END' + i,
  Status: i % 50 === 0 ? 'In-Progress' : 'New', Remarks: '', 'Nama Kosmetik': '', 'Nombor Notifikasi': '', 'Jenis Aduan': 'Iklan Kosmetik', 'Deskripsi Aduan': 'Tuan/Puan', 'Tarikh Melapor': '', 'KKM Feedback': '',
  Source: 'scraper', Confidence: 0.8, 'Created At': '2026-11-20T00:00:00Z', 'Updated At': '2026-11-20T00:00:00Z' }));
const short = r => Object.assign({}, r, { 'Extracted Text': r['Extracted Text'].slice(0, 300), 'Violation Reason': r['Violation Reason'].slice(0, 300), _truncated: true });
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const br = await chromium.launch(); let bad = 0; const errs = [];
  const check = (n, c, x) => { console.log((c ? 'PASS ' : 'FAIL ') + n + (c ? '' : '  -> ' + JSON.stringify(x))); if (!c) bad++; };
  async function open(vp) {
    const rows = mk(), calls = [], ctl = { fail: {}, delay: { update_status: SLOW, open: SLOW, list: SLOW, update_fields: 300 }, staleList: null };
    const count = () => { const c = { total: N, New: 0, 'In-Progress': 0, Complete: 0, Dismissed: 0, 'Action taken': 0, Archived: 0 }; rows.forEach(r => c[r.Status]++); return { counts: c, byBrand: {}, byPlatform: {}, ownBrand: { total: 0, open: 0 } }; };
    const ctx = await br.newContext({ viewport: vp || { width: 1366, height: 800 } });
    await ctx.addInitScript(() => { localStorage.setItem('kkm.backend', 'http://localhost:8766/api'); localStorage.setItem('kkm.key', 'k'); });
    const p = await ctx.newPage(); p.on('pageerror', e => errs.push(e.message)); p.on('dialog', d => d.accept());
    await p.route('**/api', async r => {
      const b = JSON.parse(r.request().postData() || '{}'); const a = b.action; let o = { ok: true };
      calls.push({ a, id: b.id, status: b.status, lite: b.lite, fields: b.fields, t: Date.now() });
      if (a === 'bootstrap') o = { ok: true, rows: rows.map(short), stats: count(), lookups: { brands: BR, platforms: PL, violationTypes: ['Medicinal / disease claim'] }, kkmFormUrl: '', questUrl: '', statuses: ['New', 'In-Progress', 'Complete', 'Dismissed', 'Action taken'], routes: [], summary: null };
      else if (a === 'list') { const snap = b.status === 'Archived' ? [] : rows.map(short), st = count(); await sleep(ctl.delay.list); o = { ok: true, rows: snap, stats: st }; }   // read at the START, like the real sheet
      else if (a === 'open') { await sleep(ctl.delay.open); if (ctl.fail.open) o = { ok: false, error: 'boom' }; else { const x = rows.find(x => x.ID === b.id); if (x.Status === 'New') x.Status = 'In-Progress'; o = { ok: true, row: Object.assign({}, x) }; } }
      else if (a === 'update_status') { await sleep(ctl.delay.update_status); if (ctl.fail.update_status) o = { ok: false, error: 'boom' }; else { const x = rows.find(x => x.ID === b.id); x.Status = b.status; if (b.remarks) x.Remarks = b.remarks; x['Updated At'] = 'now'; o = { ok: true, row: Object.assign({}, x) }; if (!b.lite) o.stats = count(); } }
      else if (a === 'update_fields') { await sleep(ctl.delay.update_fields); const x = rows.find(x => x.ID === b.id); Object.assign(x, b.fields); o = { ok: true, row: Object.assign({}, x) }; }
      else if (a === 'targets') o = { ok: true, targets: [] };
      else if (a === 'own_brand_list') o = { ok: true, rows: [], counts: {}, brands: [] };
      else if (a === 'held_list') o = { ok: true, held: [], total: 0 };
      else if (a === 'links_list') o = { ok: true, links: [] };
      await r.fulfill({ contentType: 'application/json', body: JSON.stringify(o) });
    });
    await p.route('**/status.json*', r => r.fulfill({ status: 404, body: '' }));
    await p.route('**/drive.google.com/**', r => r.fulfill({ status: 404, body: '' }));
    await p.goto('http://localhost:8766/index.html#list'); await p.waitForFunction(() => document.querySelectorAll('#rows tr').length > 0, null, { timeout: 60000 });
    await p.waitForTimeout(SLOW + 400);                                           // the archive call that follows boot
    const q = {
      badge: () => p.evaluate(() => document.getElementById('dStatus').textContent),
      tile: (name) => p.evaluate((name) => Number([...document.querySelectorAll('#stats [data-tile]')].find(b => b.dataset.tile === name).querySelector('.text-2xl').textContent), name),
      rowBadge: (id) => p.evaluate((id) => { const tr = document.querySelector(`#rows tr[data-id="${id}"]`); return tr ? tr.querySelector('.badge').textContent : null; }, id),
      click: (sel) => p.evaluate((sel) => document.querySelector(sel).click(), sel),
      toast: () => p.evaluate(() => document.getElementById('toast').textContent),
      nRows: () => p.evaluate(() => document.querySelectorAll('#rows tr').length),
      paint: (sel) => p.evaluate((sel) => new Promise(res => { const t = performance.now(); document.querySelector(sel).click(); requestAnimationFrame(() => setTimeout(() => res(Math.round(performance.now() - t)), 0)); }), sel),
    };
    return { p, rows, calls, ctl, q, ctx };
  }
  const ID = i => 'KKM-20261114-' + (10000 + i);

  // --- the list is a window ---
  { const { p, q, ctx } = await open();
    check('8,600 complaints: 100 rows are drawn, not 8,600', await q.nRows() === 100 && await p.evaluate(() => document.querySelectorAll('#cards > button').length) === 100);
    check('the count still says how many match', /^8600 of 8600/.test(await p.evaluate(() => document.getElementById('count').textContent)), await p.evaluate(() => document.getElementById('count').textContent));
    const ms = [await q.paint('[data-chip="In-Progress"]'), await q.paint('[data-chip=""]'), await q.paint('[data-tile="New"]'), await q.paint('[data-tile=""]')];
    check('a chip or tile click repaints in well under half a second (was about 3 s)', Math.max(...ms) < 500, ms);
    await p.evaluate(() => document.getElementById('moreBtn').scrollIntoView()); await p.waitForTimeout(400);
    const n2 = await q.nRows();
    check('scrolling to the end of the list draws the next 100 under it', n2 >= 200 && n2 % 100 === 0, n2);
    check('the button says what is left', /not shown yet/.test(await p.evaluate(() => document.getElementById('moreBtn').textContent)));
    await p.evaluate(() => window.scrollTo(0, 0)); await p.waitForTimeout(100);       // at the END of a list the next rows load by themselves; from the top they wait
    await q.click('[data-chip="In-Progress"]'); await p.waitForTimeout(100);
    check('a filter with 172 matches shows 100 and offers the rest', await q.nRows() === 100 && !(await p.evaluate(() => document.getElementById('more').classList.contains('hidden'))));
    await q.click('#moreBtn'); await p.waitForTimeout(100);
    check('...and after "show more" all 172 are drawn and the button goes', await q.nRows() === 172 && await p.evaluate(() => document.getElementById('more').classList.contains('hidden')), await q.nRows());
    await p.evaluate(() => window.scrollTo(0, 0)); await q.click('[data-chip=""]'); await p.waitForTimeout(100);
    check('a new filter starts from the top again', await q.nRows() === 100);
    await p.evaluate(() => window.dispatchEvent(new Event('beforeprint'))); const all = await q.nRows();
    await p.evaluate(() => window.dispatchEvent(new Event('afterprint')));
    check('printing draws every matching row, and the window comes back after it', all === 8600 && await q.nRows() === 100, all);
    await p.evaluate(() => { window.open = () => null; document.querySelector('#rows tr a').addEventListener('click', e => e.preventDefault()); document.querySelector('#rows tr a').click(); }); await p.waitForTimeout(150);
    check('a click on a row\'s "post" link does not open the complaint', await p.evaluate(() => document.getElementById('drawer').classList.contains('translate-x-full')));
    await ctx.close(); }

  // --- opening and changing status show at once ---
  { const { p, q, calls, rows, ctx } = await open();
    const new0 = await q.tile('New'), prog0 = await q.tile('In-Progress');
    let t = Date.now(); await q.click(`#rows tr[data-id="${ID(1)}"]`);
    await p.waitForFunction(() => document.getElementById('dStatus').textContent === 'In-Progress', null, { timeout: 1000 }); const dt = Date.now() - t;
    check('opening a New complaint shows In-Progress at once (the backend takes 2.5 s)', dt < 600, dt);
    check('...the tiles move with it', await q.tile('New') === new0 - 1 && await q.tile('In-Progress') === prog0 + 1, [await q.tile('New'), await q.tile('In-Progress')]);
    check('...and the row in the list', await q.rowBadge(ID(1)) === 'In-Progress');
    await p.fill('#eRemarks', 'typed while it was opening');
    await p.waitForTimeout(SLOW + 600);
    check('the full text arrives behind it (the list copy was cut at 300 characters)', (await p.inputValue('#eViolationReason')).endsWith('END1'));
    check('...and what was typed meanwhile is still there', await p.inputValue('#eRemarks') === 'typed while it was opening');
    check('opening costs ONE backend call (it used to be followed by two whole-list reads)', calls.filter(c => c.t > t).map(c => c.a).join() === 'open', calls.filter(c => c.t > t).map(c => c.a));
    await p.fill('#eRemarks', '');
    t = Date.now(); const n = calls.length; await q.click('.stBtn[data-status="Dismissed"]');
    await p.waitForFunction(() => document.getElementById('dStatus').textContent === 'Dismissed', null, { timeout: 1000 }); const ds = Date.now() - t;
    check('a status button shows the new status at once (it used to wait for the backend)', ds < 600, ds);
    check('...tiles and list row with it', await q.tile('Dismissed') === 1 && await q.tile('In-Progress') === prog0 && await q.rowBadge(ID(1)) === 'Dismissed', [await q.tile('Dismissed'), await q.tile('In-Progress')]);
    check('...and the buttons stay usable', await p.evaluate(() => [...document.querySelectorAll('.stBtn')].every(b => !b.disabled)));
    await p.waitForTimeout(SLOW + 500);
    check('the change is sent once, asking for the light answer', calls.slice(n).filter(c => c.a === 'update_status').length === 1 && calls.slice(n)[0].lite === 1 && rows[1].Status === 'Dismissed', calls.slice(n));
    check('after the backend confirms, nothing moves', await q.badge() === 'Dismissed' && await q.tile('Dismissed') === 1 && await q.tile('New') === new0 - 1);
    await ctx.close(); }

  // --- a refused change is put back, loudly ---
  { const { p, q, ctl, rows, ctx } = await open();
    await q.click(`#rows tr[data-id="${ID(50)}"]`); await p.waitForTimeout(SLOW + 500);            // already In-Progress
    const prog0 = await q.tile('In-Progress');
    ctl.fail.update_status = true; await q.click('.stBtn[data-status="Dismissed"]'); await p.waitForTimeout(150);
    check('refused change: shown at first', await q.badge() === 'Dismissed' && await q.tile('Dismissed') === 1);
    await p.waitForTimeout(SLOW + 500);
    check('...then put back: badge, list row and tiles', await q.badge() === 'In-Progress' && await q.rowBadge(ID(50)) === 'In-Progress' && await q.tile('Dismissed') === 0 && await q.tile('In-Progress') === prog0, [await q.badge(), await q.tile('Dismissed')]);
    check('...and the message says it did NOT change', /NOT changed \(still In-Progress\)/.test(await q.toast()), await q.toast());
    check('...the sheet was never touched', rows[50].Status === 'In-Progress');
    ctl.fail.update_status = false;
    // an open that fails puts a New row back to New
    await q.click('#btnClose'); ctl.fail.open = true; const new0 = await q.tile('New');
    await q.click(`#rows tr[data-id="${ID(2)}"]`); await p.waitForTimeout(150);
    check('open that fails: In-Progress at first', await q.badge() === 'In-Progress' && await q.tile('New') === new0 - 1);
    await p.waitForTimeout(SLOW + 500);
    check('...then back to New everywhere', await q.badge() === 'New' && await q.rowBadge(ID(2)) === 'New' && await q.tile('New') === new0, [await q.badge(), await q.tile('New')]);
    await ctx.close(); }

  // --- order, races and the refresh that was already on its way ---
  { const { p, q, calls, rows, ctl, ctx } = await open();
    await q.click(`#rows tr[data-id="${ID(50)}"]`); await p.waitForTimeout(SLOW + 500);
    const n = calls.length;
    await q.click('.stBtn[data-status="Dismissed"]'); await p.waitForTimeout(80); await q.click('.stBtn[data-status="New"]'); await p.waitForTimeout(150);
    check('two quick clicks: the page shows the last one', await q.badge() === 'New');
    await p.waitForTimeout(SLOW * 2 + 700);
    const sent = calls.slice(n).filter(c => c.a === 'update_status').map(c => c.status);
    check('...both reach the backend, in the order clicked, one after the other', sent.join() === 'Dismissed,New' && rows[50].Status === 'New', sent);
    check('...and the page still shows the last one after both answers', await q.badge() === 'New' && await q.rowBadge(ID(50)) === 'New' && await q.tile('Dismissed') === 0);
    // a refresh starts, THEN a status is clicked: the refresh read the old status and must not paint it back
    ctl.delay.list = 3000; ctl.delay.update_status = 500;
    await q.click('#btnRefresh'); await p.waitForTimeout(300);
    await q.click('.stBtn[data-status="Dismissed"]'); await p.waitForTimeout(900);                  // confirmed while the refresh is still out
    check('status confirmed while a refresh is on its way', rows[50].Status === 'Dismissed' && await q.badge() === 'Dismissed');
    await p.waitForTimeout(3000 + 3000 + 600);                                                      // the refresh and its archive call land
    check('...the stale refresh does not paint the old status back', await q.rowBadge(ID(50)) === 'Dismissed' && await q.tile('Dismissed') === 1, [await q.rowBadge(ID(50)), await q.tile('Dismissed')]);
    const n2 = calls.filter(c => c.a === 'list' && !c.status).length;
    await q.click('#btnClose'); await p.evaluate(() => { refresh(true); refresh(true); refresh(true); }); await p.waitForTimeout(200);
    check('three refreshes asked at once make one call', calls.filter(c => c.a === 'list' && !c.status).length === n2 + 1, calls.filter(c => c.a === 'list' && !c.status).length - n2);
    await ctx.close(); }

  // --- two rows opened quickly, and Save before the full row is in ---
  { const { p, q, calls, rows, ctx } = await open();
    await q.click(`#rows tr[data-id="${ID(3)}"]`); await p.waitForTimeout(100); await q.click('#btnClose');
    await q.click(`#rows tr[data-id="${ID(4)}"]`); await p.waitForTimeout(SLOW * 2 + 700);
    check('open A, then B: the drawer stays on B when A answers', (await p.evaluate(() => document.getElementById('dTitle').textContent)).includes(ID(4)) && (await p.inputValue('#eViolationReason')).endsWith('END4'), await p.evaluate(() => document.getElementById('dTitle').textContent));
    await q.click('#btnClose');
    await q.click(`#rows tr[data-id="${ID(5)}"]`); await p.waitForTimeout(100);
    check('before the full row is in, the drawer holds the shortened copy', (await p.inputValue('#eViolationReason')).length === 300);
    await p.fill('#eNama', 'Serum X'); await q.click('#btnSave'); await p.waitForTimeout(SLOW + 1200);
    const saved = calls.filter(c => c.a === 'update_fields').pop();
    check('Save pressed early waits for the full row: the full reason is saved, not the shortened one', saved && saved.fields['Violation Reason'].endsWith('END5') && saved.fields['Nama Kosmetik'] === 'Serum X', saved && saved.fields['Violation Reason'].length);
    check('...and the sheet copy is whole', rows[5]['Violation Reason'].endsWith('END5') && rows[5]['Nama Kosmetik'] === 'Serum X');
    await ctx.close(); }

  // --- phone ---
  { const { p, q, ctx } = await open({ width: 390, height: 780 });
    const new0 = await q.tile('New');
    await p.evaluate(() => document.querySelectorAll('#cards > button')[1].click()); await p.waitForTimeout(200);
    check('phone: tapping a card opens it and shows In-Progress at once', await q.badge() === 'In-Progress' && await q.tile('New') === new0 - 1);
    await ctx.close(); }

  check('no page errors', errs.length === 0, errs.slice(0, 3));
  await br.close(); process.exit(bad ? 1 : 0);
})();
