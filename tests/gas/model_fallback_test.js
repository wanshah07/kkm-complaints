// The Apps Script reviewer's model fallback, using the REAL modelChain_(), modelOffResponse_(), draftViaGateway_() and
// draftViaGatewayModel_() from Code.gs against a fake UrlFetchApp. Run: node tests/gas/model_fallback_test.js (exit 1 on a failure)
const fs = require('fs'), vm = require('vm'), path = require('path');
const code = fs.readFileSync(path.join(__dirname, '..', '..', 'apps-script/Code.gs'), 'utf8');
function grabFn(name) {
  const i = code.indexOf('function ' + name + '('); if (i < 0) throw new Error('no ' + name);
  let d = 0; for (let k = code.indexOf('{', i); k < code.length; k++) { if (code[k] === '{') d++; else if (code[k] === '}') { d--; if (!d) return code.slice(i, k + 1); } }
}
let fail = 0; const ok = (c, m) => { if (!c) { fail++; console.log('FAIL', m); } else console.log('ok  ', m); };

function sandbox(props, responses) {
  const asked = [];
  const ctx = {
    PROPS: { getProperty: k => props[k] || '' },
    NPRA_SYSTEM_PROMPT: 'x', REVIEWER_DEFAULT_BASE: 'https://gw/v1', REVIEWER_DEFAULT_MODEL: 'm1',
    UrlFetchApp: { fetch: (u, o) => { const model = JSON.parse(o.payload).model; asked.push(model); const r = responses[model] || [200, '{"choices":[{"message":{"content":"{}"}}]}'];
      return { getResponseCode: () => r[0], getContentText: () => r[1] }; } },
  };
  vm.createContext(ctx);
  ['modelChain_', 'modelOffResponse_', 'reviewerConfig_', 'draftViaGatewayModel_', 'draftViaGateway_'].forEach(n => vm.runInContext(grabFn(n), ctx));
  return { ctx, asked };
}

{ const { ctx, asked } = sandbox({ OPENAI_MODEL: 'm1', OPENAI_MODEL_FALLBACKS: 'm2, m3' }, { m1: [404, '{"error":"model not found"}'] });
  const r = vm.runInContext('draftViaGateway_("k","t","","image/png")', ctx);
  ok(JSON.stringify(asked) === '["m1","m2"]' && r.fellBackTo === 'm2', 'a 404 on the first model hands over to the second'); }
{ const { ctx, asked } = sandbox({ OPENAI_MODEL: 'm1', OPENAI_MODEL_FALLBACKS: 'm2' }, { m1: [529, 'Overloaded'] });
  vm.runInContext('draftViaGateway_("k","t","","image/png")', ctx);
  ok(asked.join() === 'm1,m2', 'an overloaded first model hands over'); }
{ const { ctx, asked } = sandbox({ OPENAI_MODEL: 'm1', OPENAI_MODEL_FALLBACKS: 'm2' }, { m1: [401, 'bad key'] });
  let err = ''; try { vm.runInContext('draftViaGateway_("k","t","","image/png")', ctx); } catch (e) { err = String(e.message); }
  ok(asked.join() === 'm1' && /check the key/.test(err), 'a 401 is the key: it stops, the second model is NOT tried'); }
{ const { ctx } = sandbox({ OPENAI_MODEL: 'm1', OPENAI_MODEL_FALLBACKS: 'm2' }, { m1: [503, 'x'], m2: [503, 'y'] });
  let err = ''; try { vm.runInContext('draftViaGateway_("k","t","","image/png")', ctx); } catch (e) { err = String(e.message); }
  ok(/is off/.test(err), 'every model off: the error says so'); }
{ const { ctx } = sandbox({}, {});
  ok(JSON.stringify(vm.runInContext('modelChain_("a"," b,a ;c",[])', ctx)) === '["a","b","c"]', 'chain drops repeats and blanks');
  ok(JSON.stringify(vm.runInContext('modelChain_("a","",["x","a"])', ctx)) === '["a","x"]', 'defaults only when the property is empty'); }
process.exit(fail ? 1 : 0);
