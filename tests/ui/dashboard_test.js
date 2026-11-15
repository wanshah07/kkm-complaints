// Dashboard in a real browser against a mocked backend. Not part of CI (it needs Chromium).
//   cd web && python3 -m http.server 8766 &        then:   node tests/ui/dashboard_test.js [path/to/axe.min.js]
const { chromium } = require('playwright'); const fs = require('fs');
const AXE = process.argv[2] && fs.existsSync(process.argv[2]) ? fs.readFileSync(process.argv[2], 'utf8') : '';
const iso = (m) => new Date(Date.now() - m * 60000).toISOString().replace(/\.\d+Z$/, 'Z');
const K = (b, p) => (b + '\u0001' + p).toLowerCase();
const BR = ['La Roche-Posay', 'Eucerin', 'CeraVe', 'Nuuha Beauty', 'Uriage', 'Dr Dermis', 'drhalina', 'SimplySiti'], PL = ['Instagram', 'Facebook', 'Threads', 'TikTok'];
const rows = Array.from({ length: 40 }, (_, i) => ({ ID: 'KKM-20261114-' + (1000 + i), Date: '2026-11-' + String(1 + i % 14).padStart(2, '0'), Brand: BR[i % 8], Platform: PL[i % 4],
  'Post URL': i === 0 ? 'javascript:alert(document.domain)' : 'https://www.instagram.com/p/ABC' + i, 'Screenshot Link': i % 7 ? 'https://drive.google.com/file/d/1abc' + i + '/view' : '', 'Extracted Text': 'Caption ' + i,
  'Violation Type': 'Medicinal / disease claim', 'Violation Reason': 'Annex I Part 8.', Status: ['New', 'New', 'In-Progress', 'Complete'][i % 4], Remarks: '', 'Nama Kosmetik': 'Serum', 'Nombor Notifikasi': '',
  'Jenis Aduan': 'Iklan Kosmetik', 'Deskripsi Aduan': 'Tuan/Puan', 'Tarikh Melapor': '', 'KKM Feedback': '', Source: 'scraper', Confidence: 0.8, 'Created At': iso(600), 'Updated At': iso(600) }));
const stats = { counts: { total: 40, New: 20, 'In-Progress': 10, Complete: 10, Dismissed: 0 }, byBrand: {}, byPlatform: {}, ownBrand: { total: 0, open: 0 } };
function freshTargets() {
  const t = [
    { name: 'La Roche-Posay', active: true, type: '', handles: { Instagram: 'larocheposaymalaysia', Facebook: 'larocheposaymalaysia', TikTok: 'larocheposay_my' }, product_hints: ['Effaclar'], notes: '', website_feed: '', product_feed: '' },
    { name: 'Eucerin', active: true, type: '', handles: { Instagram: 'eucerin_my', Facebook: 'EucerinMY' }, product_hints: [], notes: '' },
    { name: 'drx', active: true, type: 'General Doctor', handles: { Instagram: 'drx_old', Threads: 'drx_old' }, product_hints: [], notes: 'watch <b>closely</b>' },
    { name: 'Paused Brand', active: false, type: '', handles: { Instagram: 'pausedbrand' }, product_hints: [], notes: '' },
    { name: 'Guardian MY', active: true, type: 'Guardian MY', handles: {}, product_hints: [], notes: '', website_feed: 'https://guardian.example/feed', product_feed: '' }];
  const st = {};
  st[K('La Roche-Posay', 'Instagram')] = { handle: 'larocheposaymalaysia', state: 'not_found', detail: 'The platform says this profile is not available.', posts: '0', checked_at: iso(30) };
  st[K('La Roche-Posay', 'Facebook')] = { handle: 'larocheposaymalaysia', state: 'limited', detail: 'Only the public, signed-out view could be read (1 post(s)).', posts: '1', checked_at: iso(29), sweep_at: iso(600), sweep_posts: '4' };
  st[K('La Roche-Posay', 'TikTok')] = { handle: 'larocheposay_my', state: 'live', posts: '13', checked_at: iso(28) };
  st[K('Eucerin', 'Instagram')] = { handle: 'eucerin_my', state: 'live', posts: '12', checked_at: iso(60 * 24 * 12) };          // 12 days old
  st[K('Eucerin', 'Facebook')] = { handle: 'EucerinMY', state: 'blocked', detail: 'The platform asked for a login.', posts: '0', checked_at: iso(26) };
  st[K('drx', 'Instagram')] = { handle: 'drx_old', state: 'renamed', detail: 'Live, but the account now answers to @drx_new.', posts: '9', checked_at: iso(25), suggest: 'drx_new' };
  st[K('drx', 'Threads')] = { handle: 'SOMEONE_ELSE', state: 'live', posts: '9', checked_at: iso(25) };
  return { targets: t, status: st, undo: null };
}
(async () => {
  const br = await chromium.launch(); let bad = 0; const errs = [];
  const check = (n, c, x) => { console.log((c ? 'PASS ' : 'FAIL ') + n + (c ? '' : '  -> ' + JSON.stringify(x))); if (!c) bad++; };
  async function open(vp, opt = {}) {
    const st = freshTargets(), calls = [];
    const ctx = await br.newContext({ viewport: vp, deviceScaleFactor: 1, hasTouch: vp.width < 640 });
    await ctx.addInitScript(() => { localStorage.setItem('kkm.backend', 'http://localhost:8766/api'); localStorage.setItem('kkm.key', 'k'); });
    const p = await ctx.newPage(); p.on('pageerror', e => errs.push(e.message)); p.on('dialog', d => d.accept());
    await p.route('**/api', async r => {
      const b = JSON.parse(r.request().postData() || '{}'); calls.push(b); const a = b.action; let o = { ok: true };
      if (a === 'bootstrap') o = opt.badBoot ? { ok: true, rows: [], stats: {} } : { ok: true, rows, stats, lookups: { brands: BR, platforms: PL, violationTypes: [] }, kkmFormUrl: '', questUrl: 'https://quest3plus.bpfk.gov.my/pmo2/index.php', statuses: ['New', 'In-Progress', 'Complete', 'Dismissed', 'Action taken'], routes: [], summary: null };
      else if (a === 'list') o = { ok: true, rows, stats };
      else if (a === 'open') o = { ok: true, row: rows.find(x => x.ID === b.id) };
      else if (a === 'targets') o = { ok: true, targets: st.targets };
      else if (a === 'own_brand_list') o = { ok: true, rows: [], counts: {}, brands: [] };
      else if (a === 'held_list') o = { ok: true, held: [], total: 0 };
      else if (a === 'links_list') o = { ok: true, links: [] };
      else if (a === 'targets_admin') o = opt.oldBackend ? { ok: false, error: 'Unknown action: targets_admin' } : { ok: true, platforms: PL, types: ['General Doctor', 'Guardian MY'], states: [], has: { type: true, website_feed: true, product_feed: true }, targets: st.targets, status: st.status, undo: st.undo };
      else if (a === 'target_save') {
        const t = b.target, n = b.original;
        if (/valorith/i.test(JSON.stringify(t))) o = { ok: false, error: 'Valorith is one of your own brands. Own brands are never watched by this system.' };
        else if (n) { const row = st.targets.find(x => x.name === n), ch = []; row.active = t.active; for (const pl in t.handles) if (t.handles[pl] !== (row.handles[pl] || '')) { ch.push(pl); if (t.handles[pl]) row.handles[pl] = t.handles[pl]; else delete row.handles[pl]; delete st.status[K(n, pl)]; } o = { ok: true, name: n, added: false, changed: ch }; }
        else { const h = {}; for (const pl in t.handles) if (t.handles[pl]) h[pl] = t.handles[pl].replace(/^@/, ''); st.targets.push({ name: t.name, active: t.active, type: t.type, handles: h, product_hints: [], notes: t.notes, website_feed: t.website_feed || '' }); o = { ok: true, name: t.name, added: true, changed: Object.keys(h) }; }
      } else if (a === 'target_remove') { const row = st.targets.find(x => x.name === b.name); st.removed = row; st.targets = st.targets.filter(x => x !== row); st.undo = { name: b.name, at: iso(0) }; o = { ok: true, removed: b.name }; }
      else if (a === 'target_undo') { st.targets.push(st.removed); st.undo = null; o = { ok: true, name: st.removed.name }; }
      await r.fulfill({ contentType: 'application/json', body: JSON.stringify(o) });
    });
    await p.route('**/status.json*', r => opt.status ? r.fulfill({ contentType: 'application/json', body: JSON.stringify(opt.status) }) : r.fulfill({ status: 404, body: '' }));
    await p.route('**/drive.google.com/**', r => r.fulfill({ status: 404, body: '' }));
    await p.goto('http://localhost:8766/index.html' + (opt.hash || '')); await p.waitForTimeout(1600);
    await p.addStyleTag({ content: '#connect{display:none!important}' });
    return { p, ctx, st, calls };
  }
  const text = (p, sel) => p.evaluate(s => document.querySelector(s) ? document.querySelector(s).innerText : null, sel);
  const click = (p, sel) => p.evaluate(s => document.querySelector(s).click(), sel);

  // ============ desktop: links, drawer, dialogs
  let { p, ctx, st, calls } = await open({ width: 1366, height: 900 }, { hash: '#list' });
  const hrefs = await p.evaluate(() => [...document.querySelectorAll('#rows a')].map(a => a.getAttribute('href')));
  check('a javascript: Post URL never becomes a link', hrefs.length > 10 && !hrefs.some(h => /^\s*javascript:/i.test(h)) && hrefs.includes('#') && hrefs.some(h => /^https:\/\/www\.instagram\.com/.test(h)), hrefs.slice(0, 3));
  check('modals carry role=dialog and a label', await p.evaluate(() => ['drawer', 'modal', 'linksModal', 'tgModal', 'adminModal', 'ldModal'].every(id => { const e = document.getElementById(id); return e.getAttribute('role') === 'dialog' && e.getAttribute('aria-modal') === 'true' && e.getAttribute('aria-label'); })));
  await p.focus('#btnRefresh'); await p.evaluate(() => document.querySelector('#rows tr').click()); await p.waitForTimeout(500);
  check('drawer: unsafe link is inert there too', (await p.getAttribute('#dUrl', 'href')) === '#');
  check('drawer: focus moves inside', await p.evaluate(() => document.getElementById('drawer').contains(document.activeElement)));
  for (let i = 0; i < 60; i++) await p.keyboard.press('Tab');
  check('drawer: Tab stays inside', await p.evaluate(() => document.getElementById('drawer').contains(document.activeElement)));
  await p.keyboard.press('Escape'); await p.waitForTimeout(300);
  check('drawer: focus returns to where it was', await p.evaluate(() => document.activeElement && document.activeElement.id === 'btnRefresh'), await p.evaluate(() => document.activeElement && (document.activeElement.id || document.activeElement.tagName)));

  check('desktop drawer: status buttons first, then the screenshot', await p.evaluate(() => { const k = [...document.getElementById('dBody').children]; return k[0].classList.contains('stRow') && k.indexOf(document.getElementById('dShotWrap')) === k.indexOf(document.getElementById('dAfterShot')) - 1; }));

  // ============ Manage menu (desktop header)
  const vis = (sel) => p.evaluate(s => { const e = document.querySelector(s); return !!e && e.offsetParent !== null; }, sel);
  check('header: Targets, TikTok links and Admin are not loose buttons', !(await vis('#btnTargets')) && !(await vis('#btnLinks')) && !(await vis('#btnAdmin')) && await vis('#btnManage'));
  check('header: six buttons, on one line', await p.evaluate(() => { const b = [...document.querySelectorAll('header div.ml-auto.sm\\:flex > a, header div.ml-auto.sm\\:flex > button, header div.ml-auto.sm\\:flex > div > button')].filter(e => e.offsetParent !== null); const tops = new Set(b.map(e => Math.round(e.getBoundingClientRect().top / 8))); return b.length === 6 && tops.size === 1; }), await p.evaluate(() => [...document.querySelectorAll('header div.ml-auto.sm\\:flex > a, header div.ml-auto.sm\\:flex > button, header div.ml-auto.sm\\:flex > div > button')].filter(e => e.offsetParent !== null).map(e => e.textContent.trim())));
  await p.click('#btnManage'); await p.waitForTimeout(100);
  check('Manage opens its three items and says so', await vis('#btnTargets') && await vis('#btnLinks') && await vis('#btnAdmin') && (await p.getAttribute('#btnManage', 'aria-expanded')) === 'true');
  await p.click('#btnExport'); await p.waitForTimeout(100);
  check('opening Export closes Manage', !(await vis('#btnTargets')) && await vis('#exportMenu') && (await p.getAttribute('#btnManage', 'aria-expanded')) === 'false');
  await p.click('#btnManage'); await p.waitForTimeout(100);
  check('opening Manage closes Export', await vis('#btnTargets') && !(await vis('#exportMenu')));
  await p.keyboard.press('Escape'); await p.waitForTimeout(100);
  check('Escape closes Manage', !(await vis('#btnTargets')));
  await p.click('#btnManage'); await p.waitForTimeout(100); await p.click('#btnTargets'); await p.waitForTimeout(600);
  check('a menu item opens its panel and the menu closes', await p.evaluate(() => !document.getElementById('tgModal').classList.contains('hidden') && document.getElementById('manageMenu').classList.contains('hidden')));
  check('Manage badge adds up what needs a look, in red for accounts', await p.evaluate(() => { const b = document.getElementById('manageBadge'); return b.textContent.trim() === '2' && b.offsetParent !== null && b.classList.contains('bg-red-100') && b.classList.contains('text-xs') && /2 account\(s\) need attention/.test(b.title); }), await p.evaluate(() => document.getElementById('manageBadge').outerHTML));
  check('chips: only the one in use is pressed', await p.evaluate(() => { const c = [...document.querySelectorAll('#tgChips [data-tgchip]')]; return c.length === 6 && c.filter(x => x.getAttribute('aria-pressed') === 'true').length === 1 && c[0].getAttribute('aria-pressed') === 'true'; }));
  await click(p, '#tgChips [data-tgchip="attention"]'); await p.waitForTimeout(100);
  check('chips: pressing one moves the pressed state', await p.evaluate(() => [...document.querySelectorAll('#tgChips [data-tgchip]')].filter(x => x.getAttribute('aria-pressed') === 'true').map(x => x.dataset.tgchip).join() === 'attention'));
  await click(p, '#tgChips [data-tgchip=""]'); await p.waitForTimeout(100);
  await p.keyboard.press('Escape'); await p.waitForTimeout(300);
  check('closing the panel returns focus to Manage', await p.evaluate(() => document.activeElement && document.activeElement.id === 'btnManage'), await p.evaluate(() => document.activeElement && (document.activeElement.id || document.activeElement.tagName)));

  // ============ Targets panel
  await click(p, '#btnTargets'); await p.waitForTimeout(600);
  let chips = await text(p, '#tgChips'), list = (await text(p, '#tgList')).replace(/\n+/g, ' ');
  check('chips count accounts and states', /Accounts watched 7/.test(chips) && /Live 2/.test(chips) && /Needs attention 2/.test(chips) && /Not checked yet 1/.test(chips) && /Could not check 2/.test(chips) && /Paused rows 1/.test(chips), chips);
  check('states: not found, login refused, handle changed, could not check', /Not found/.test(list) && /Login refused/.test(list) && /Handle changed/.test(list) && /Could not check/.test(list) && /Use @drx_new/.test(list), list.slice(0, 300));
  check('an old check says so', /12 days ago: may be out of date/.test(list), list);
  check('a status for another handle is not shown as this one', /Threads\s*@drx_old\s*Not checked yet/.test(list), list);
  check('notes are escaped, not rendered', await p.evaluate(() => !document.querySelector('#tgList b') && document.getElementById('tgList').innerText.includes('<b>closely</b>')));
  check('badge on the header button', (await text(p, '#tgBadge')).trim() === '2');
  await p.selectOption('#tgSort', 'attention'); await p.waitForTimeout(100);
  check('sort: needs attention first', (await p.evaluate(() => [...document.querySelectorAll('#tgList > div > div:first-child > span:first-child')].map(e => e.textContent))).join('|').startsWith('La Roche-Posay|drx|Eucerin'), await p.evaluate(() => [...document.querySelectorAll('#tgList > div > div:first-child > span:first-child')].map(e => e.textContent)));
  await p.selectOption('#tgSort', 'name'); await p.waitForTimeout(100);
  check('sort: name', (await p.evaluate(() => [...document.querySelectorAll('#tgList > div > div:first-child > span:first-child')].map(e => e.textContent))).join('|') === 'drx|Eucerin|La Roche-Posay|Paused Brand');
  await p.selectOption('#tgSort', ''); await p.selectOption('#tgState', 'attention'); await p.waitForTimeout(100); list = await text(p, '#tgList');
  check('filter: needs attention', /La Roche-Posay/.test(list) && /drx/.test(list) && !/Eucerin/.test(list));
  await p.selectOption('#tgState', ''); await p.selectOption('#tgKind', 'web'); await p.waitForTimeout(100);
  check('filter: websites', /Guardian MY/.test(await text(p, '#tgList')) && !/Eucerin/.test(await text(p, '#tgList')));
  await click(p, '.tgEdit'); await p.waitForTimeout(150);
  check('website row: feed fields open for editing', await p.evaluate(() => { const d = document.getElementById('tgFeeds'); return !d.classList.contains('hidden') && d.open && document.getElementById('tgWeb').value === 'https://guardian.example/feed'; }));
  await click(p, '#tgCancel'); await p.selectOption('#tgKind', 'social'); await p.waitForTimeout(100);
  await click(p, '.tgUse'); await p.waitForTimeout(200);
  check('Use @new opens the edit form with the handle filled', await p.evaluate(() => !document.getElementById('tgForm').classList.contains('hidden') && document.getElementById('tgName').disabled && document.querySelector('#tgHandles input[data-plat="Instagram"]').value === 'drx_new' && !document.getElementById('tgFeeds').open));
  await click(p, '#tgSave'); await p.waitForTimeout(500);
  let save = calls.filter(c => c.action === 'target_save').pop();
  check('edit saved: original name, new handle, feeds not sent for an account row', save.original === 'drx' && save.target.handles.Instagram === 'drx_new' && save.target.website_feed === undefined, save);
  await click(p, '#tgAdd'); await p.waitForTimeout(150);
  await p.fill('#tgName', 'CeraVe'); await p.fill('#tgHandles input[data-plat="Instagram"]', '@cerave.my'); await click(p, '#tgSave'); await p.waitForTimeout(500);
  save = calls.filter(c => c.action === 'target_save').pop();
  check('add sends no original', save.original === '' && save.target.name === 'CeraVe' && /CeraVe/.test(await text(p, '#tgList')));
  await click(p, '#tgAdd'); await p.fill('#tgName', 'Valorith'); await p.fill('#tgHandles input[data-plat="Instagram"]', 'valorith.my'); await click(p, '#tgSave'); await p.waitForTimeout(400);
  check('refusal shown in the form', /own brands/i.test(await text(p, '#tgFormMsg')));
  await click(p, '#tgCancel');
  check('no undo offered before a removal', await p.evaluate(() => document.getElementById('tgUndo').classList.contains('hidden')));
  await p.evaluate(() => [...document.querySelectorAll('.tgDel')].find(b => b.dataset.name === 'Eucerin').click()); await p.waitForTimeout(500);
  check('remove, then undo is offered by name', !/Eucerin/.test(await text(p, '#tgList')) && /Undo: put "Eucerin" back/.test(await text(p, '#tgUndo')));
  await click(p, '#tgUndo'); await p.waitForTimeout(500);
  check('undo puts the row back', calls.some(c => c.action === 'target_undo') && /Eucerin/.test(await text(p, '#tgList')) && await p.evaluate(() => document.getElementById('tgUndo').classList.contains('hidden')));
  await p.keyboard.press('Escape'); await ctx.close();

  // ============ phone
  ({ p, ctx, st, calls } = await open({ width: 360, height: 760 }, { hash: '#list' }));
  const firstCard = await p.evaluate(() => { const c = document.querySelector('#cards > *'); return c ? Math.round(c.getBoundingClientRect().top) : null; });
  check('phone: the first complaint is on the first screen', firstCard !== null && firstCard < 520, firstCard);
  check('phone: no sideways scroll', await p.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
  check('phone: selects are folded under Filters', await p.evaluate(() => document.getElementById('fMore').classList.contains('hidden') && document.getElementById('fToggle').offsetParent !== null));
  await click(p, '#fToggle'); await p.waitForTimeout(150);
  check('phone: Filters opens full-width selects, none clipped', await p.evaluate(() => ['fBrand', 'fType', 'fPlatform', 'fRoute'].every(id => { const e = document.getElementById(id); const r = e.getBoundingClientRect(), box = e.closest('section').getBoundingClientRect(); return e.offsetParent !== null && r.width > 250 && r.right <= box.right - 8; })) && (await p.getAttribute('#fToggle', 'aria-expanded')) === 'true');
  await p.selectOption('#fPlatform', 'Instagram'); await p.waitForTimeout(100);
  check('phone: the button counts the filters in use', (await text(p, '#fToggleN')).trim() === '1');
  const small = await p.evaluate(() => [...document.querySelectorAll('#statusChips button, #routeTabs button, #fToggle')].filter(e => e.offsetParent !== null && e.getBoundingClientRect().height < 40).map(e => e.textContent.trim().slice(0, 16) + ' ' + Math.round(e.getBoundingClientRect().height)));
  check('phone: chips and tabs are at least 40px tall', small.length === 0, small);
  await p.screenshot({ path: 'phone_list.png' });
  await p.evaluate(() => document.querySelector('#cards > *').click()); await p.waitForTimeout(500);
  check('phone drawer: confirm box keeps its size', await p.evaluate(() => { const e = document.getElementById('kkmConfirm'); const r = e.getBoundingClientRect(); return !e.offsetParent || r.width >= 19; }), await p.evaluate(() => document.getElementById('kkmConfirm').getBoundingClientRect().width));
  check('phone drawer: the screenshot comes first, the status buttons after it', await p.evaluate(() => { const k = [...document.getElementById('dBody').children]; const a = document.getElementById('dShotWrap').getBoundingClientRect(), b = document.querySelector('#dBody .stRow').getBoundingClientRect(); return k[0].id === 'dShotWrap' && k[1].classList.contains('stRow') && a.bottom <= b.top; }));
  check('phone drawer: the four status buttons are a 2x2 grid', await p.evaluate(() => { const b = [...document.querySelectorAll('#dBody .stRow > button')].map(e => e.getBoundingClientRect()); return b.length === 4 && Math.abs(b[0].top - b[1].top) < 2 && Math.abs(b[2].top - b[3].top) < 2 && b[2].top > b[0].top && Math.abs(b[0].width - b[1].width) < 2; }));
  check('phone drawer: the status buttons are still on the first screen', await p.evaluate(() => document.querySelector('#dBody .stRow').getBoundingClientRect().bottom <= window.innerHeight), await p.evaluate(() => Math.round(document.querySelector('#dBody .stRow').getBoundingClientRect().bottom)));
  await p.setViewportSize({ width: 1024, height: 760 }); await p.waitForTimeout(150);
  check('widening the window puts the screenshot back under the buttons', await p.evaluate(() => { const k = [...document.getElementById('dBody').children]; return k[0].classList.contains('stRow') && document.getElementById('dShotWrap').nextElementSibling.id === 'dAfterShot'; }));
  await p.setViewportSize({ width: 360, height: 760 }); await p.waitForTimeout(150);
  await p.screenshot({ path: 'phone_drawer.png' });
  await p.keyboard.press('Escape');
  await click(p, '#btnMore'); await p.waitForTimeout(100); await click(p, '[data-more="targets"]'); await p.waitForTimeout(600);
  check('phone: Targets panel has no sideways overflow', await p.evaluate(() => ![...document.querySelectorAll('#tgModal *')].some(e => e.getBoundingClientRect().right > window.innerWidth + 1)));
  await ctx.close();

  // ============ a backend that answers in the wrong shape, and one without Targets.gs
  ({ p, ctx } = await open({ width: 1366, height: 900 }, { badBoot: true }));
  const msg = await p.evaluate(() => document.getElementById('cErr').textContent + ' | ' + document.getElementById('loading').textContent);
  check('wrong-shaped answer is explained, not shown as a JavaScript error', /not in the shape this page expects/.test(msg) && !/Cannot read properties/.test(msg), msg.slice(0, 200));
  await ctx.close();
  ({ p, ctx } = await open({ width: 1366, height: 900 }, { oldBackend: true }));
  await click(p, '#btnTargets'); await p.waitForTimeout(700);
  check('old backend: read-only list with the reason', /Read only for now/.test(await text(p, '#tgMsg')) && await p.evaluate(() => document.getElementById('tgAdd').classList.contains('hidden') && !document.querySelector('.tgEdit')));
  await ctx.close();

  // ============ status strip
  const status = { checked_at: iso(20), platforms: [
    { key: 'instagram', label: 'Instagram', state: 'live', detail: 'Signed in and reading posts normally.', at: iso(60 * 20), carried: true },
    { key: 'facebook', label: 'Facebook', state: 'down', detail: 'Facebook shows a login page.', at: iso(30) },
    { key: 'threads', label: 'Threads', state: 'live', detail: 'ok', at: iso(30) },
    { key: 'tiktok_auto', label: 'TikTok accounts', state: 'live', detail: 'ok', at: iso(40) }] };
  ({ p, ctx } = await open({ width: 1366, height: 900 }, { status }));
  const pills = await p.evaluate(() => [...document.querySelectorAll('#platPills button')].map(b => b.textContent));
  check('status: a carried result older than 14h reads old, the rest keep their state', /Instagram old/.test(pills[0]) && /Facebook down/.test(pills[1]) && /Threads live/.test(pills[2]), pills);
  await p.evaluate(() => document.querySelectorAll('#platPills button')[1].click()); await p.waitForTimeout(100);
  check('status: a down platform links to the fix', await p.evaluate(() => { const a = document.querySelector('#platDetail a'); return !!a && /section 4/.test(a.textContent) && /^https?:/.test(a.href) && /Measured/.test(document.getElementById('platDetail').textContent); }));
  await ctx.close();

  // ============ accessibility scan
  if (AXE) {
    for (const [name, vp, hash] of [['phone list', { width: 360, height: 760 }, '#list'], ['desktop home', { width: 1366, height: 900 }, '#home'], ['desktop list', { width: 1366, height: 900 }, '#list']]) {
      ({ p, ctx } = await open(vp, { hash }));
      await p.evaluate(AXE);
      const v = await p.evaluate(async () => (await axe.run(document, { resultTypes: ['violations'] })).violations.map(x => x.id + ' ×' + x.nodes.length + ' ' + x.nodes[0].target.join(' ')));
      check('axe: no violations on ' + name, v.length === 0, v);
      await ctx.close();
    }
  } else console.log('SKIP axe scan (pass the path to axe.min.js)');
  check('no page errors', errs.length === 0, errs);
  console.log('FAILURES:', bad); await br.close(); process.exit(bad ? 1 : 0);
})();
