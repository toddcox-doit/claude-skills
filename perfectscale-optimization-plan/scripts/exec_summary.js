// One-page executive summary generated from the plan JSON. No tenant text is hard-coded.
// Output format is chosen by the extension: .docx or .html (self-contained, no external assets).
// usage: node exec_summary.js <plan.json> <out.docx|out.html>
//        (NODE_PATH is not required; the docx module is resolved from the global roots)
const fs = require('fs');
const path = require('path');

const S = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = process.argv[3];
if (!out) {
  console.error('usage: exec_summary.js <plan.json> <out.docx|out.html>');
  process.exit(2);
}
const isHtml = out.toLowerCase().endsWith('.html');

const F = 'Calibri', INK = '1F2937', MUT = '5B6472', ACC = '1F4FBF', CARD = 'F3F6FB', LINE = 'D5DAE1';
const ld = iso => new Date(iso + 'T12:00:00Z').toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
const usd = v => '$' + Math.round(v).toLocaleString('en-US');
const r100 = v => Math.round(v / 100) * 100;

// ---------------------------------------------------------------- content
// Built once, rendered twice. Neither renderer computes a figure of its own,
// so the .docx and the .html can never disagree.
//
// Written for a reader who decides rather than implements: the conclusion
// first, then the ask, then what it costs to wait, then evidence. Internal
// action IDs, cluster inventories and method notes stay in the plan.
const T = S.totals, A = S.adoption, I = S.idle, O = S.outlook, f = Math.round(A.factor * 100);
const byKey = Object.fromEntries(S.actions.map(a => [a.key, a]));
const oom = byKey.oom_fix, opt = byKey.stale_optout, mem = byKey.memory_limits;
const apShare = I.auto_provisioned / I.total;
const nOom = oom ? (oom.scope.match(/(\d+) workloads/) || [])[1] : null;
const nMem = mem ? (mem.scope.match(/(\d+) workloads/) || [])[1] : null;
const nOpt = opt ? (opt.scope.match(/(\d+) pods/) || [])[1] : null;
const gated = Math.round(S.automation_gated.share * 100);
const idlePct = Math.round(100 * I.total / I.node_cost);
const nClusters = S.scope.cluster_names.length;
const days = Math.round(S.window_days);
const annual = O.maximum.usd * 12;
const annualAdj = (T.achievable_now_monthly + T.blocked_pending_monthly) * A.factor * 12;
const inUse = I.node_cost - I.total;

const ROLE = {platform: 'the platform team', 'app-team': 'the application teams',
              'ps-admin': 'a PerfectScale administrator', finops: 'the FinOps lead',
              'ps-csm': 'your PerfectScale contact'};
const phase1 = S.actions.filter(a => a.phase === 1);
const lead = phase1.slice().sort(function (x, y) {
  return (y.savings_monthly_usd || 0) - (x.savings_monthly_usd || 0); })[0] || S.actions[0];
const EFFORT = {XS: 'a few hours', S: 'a few days', M: 'a few weeks', L: 'about a quarter'};
function move(a) {
  var who = ROLE[a.owner_role] || 'the owning team';
  var cost = a.savings_basis === 'resiliency_investment'
    ? 'costs ' + usd(r100(a.savings_monthly_usd)) + '/mo and buys reliability'
    : (r100(a.savings_monthly_usd) >= 100 ? 'worth ' + usd(r100(a.savings_monthly_usd)) + '/mo'
       : (a.is_unblocker ? 'no saving of its own: it clears the way for the right-sizing below'
          : 'no dollar claim yet'));
  return [a.title + '. ',
          'Led by ' + who + ', ' + (EFFORT[a.effort_tier] || 'a few weeks') + ' of work, ' + cost + '.'];
}

const C = {
  title: S.tenant + ': ' + usd(r100(annual)) + ' a year of addressable Kubernetes spend',
  meta: 'Executive summary  |  ' + nClusters + ' clusters  |  ' + days +
        ' days of measured data  |  ' + ld(S.generated),
  status: S.status ? S.status + ': figures and wording are open to change.' : null,

  askLabel: 'The decision',
  ask: gated >= 50
    ? 'Approve switching PerfectScale automation on, starting with one non-production cluster. ' +
      usd(r100(S.automation_gated.usd)) + ' a month of the savings below is waiting on that single decision; ' +
      'the rest is configuration work your teams already own.'
    : 'Approve the configuration work below. Only ' + usd(r100(S.automation_gated.usd)) +
      ' a month depends on switching automation on; the larger share is right-sizing your teams already own.',

  lede: [
    ['Across ' + nClusters + ' clusters we measured ', false],
    [usd(r100(T.achievable_now_monthly)) + ' a month that can be saved now', true],
    [' and a further ', false],
    [usd(r100(T.blocked_pending_monthly)) + ' a month held behind specific blockers', true],
    ['. Nothing has been applied yet, so none of it has been realised.', false],
  ],

  cards: [
    ['Addressable each year', usd(r100(annual)),
     [usd(r100(O.maximum.usd)) + '/mo at full adoption', 'Savings only; reliability spend is separate',
      'At ' + f + '% adoption: ' + usd(r100(annualAdj)) + '/yr']],
    ['Available now', usd(r100(T.achievable_now_monthly)) + '/mo',
     ['Right-sizing the applications', 'Source: PodFit',
      'At ' + f + '% adoption: ' + usd(r100(A.achievable_now.adjusted)) + '/mo']],
    ['Held by a blocker', usd(r100(T.blocked_pending_monthly)) + '/mo',
     ['Node-pool changes and idle capacity', 'Source: InfraFit',
      'An upper bound: no pool reaches zero idle']],
    ['Idle right now', idlePct + '% idle',
     [usd(r100(I.total)) + '/mo of ' + usd(r100(I.node_cost)) + ' node spend', 'Source: InfraFit',
      'Today’s state, not yet a saving']],
  ],

  // one honest picture: how today's infrastructure bill divides
  bar: {title: 'Where the node bill goes today', total: I.node_cost,
        parts: [['Idle', I.total, idlePct], ['In use', inUse, 100 - idlePct]]},

  delayLabel: 'The cost of waiting',
  delay: 'The ' + usd(r100(T.achievable_now_monthly)) + ' a month marked available now is a run rate, not a ' +
         'one-off. Each month the decision waits, that much goes unrealised, and the ' + idlePct +
         '% idle share of the node bill keeps being paid.',

  sections: [
    ['What we recommend first', phase1.slice(0, 3).map(move)],
    ['What we need from you', S.decision_detail.slice(0, 4).map(function (d) {
      return ['', d.text.replace(/\s+/g, ' ') +
              (r100(d.usd) >= 100 ? ' Unlocks about ' + usd(r100(d.usd)) + ' a month.' : '')]; })],
    ['What the numbers rest on', [
      ['', 'Figures come from ' + days + ' days of measured data across ' + nClusters +
           ' clusters, not a sample or a model.'],
      ['', gated + '% of what is available now depends on switching automation on. The remainder is ' +
           'configuration your teams change directly.'],
      ...(oom ? [['', 'Separately, about ' + nOom + ' workloads are being restarted for running out of memory' +
           (nMem ? ' and ' + nMem + ' have no memory limit' : '') + '. Fixing that costs about ' +
           usd(r100(oom.savings_monthly_usd)) + ' a month and should proceed whatever is decided commercially.']] : []),
      ...(opt ? [['', nOpt + ' pods carry settings that stop nodes draining, holding back node savings.']] : []),
      ...(apShare > 0.3 ? [['', 'Most idle sits on short-lived pools the autoscaler creates, so its cause ' +
           'needs confirming before any of it is claimed.']] : []),
    ]],
  ],

  closing: 'Savings and reliability spend are counted separately and idle capacity is never added to either. ' +
           'The ' + f + '% view is the conservative planning case. We will re-measure on ' + ld(S.rerun_date) +
           ' and report what moved.' + (S.early ? ' Costs are scaled from a short window to a 30-day run rate.' : ''),
};

// ------------------------------------------------------------------ HTML
function renderHtml() {
  const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const card = ([lab, val, lines]) => `<div class="card"><div class="lab">${esc(lab)}</div><div class="val">${esc(val)}</div>` +
    lines.map(l => `<div class="${l.indexOf('At ') === 0 ? 'ln at' : 'ln'}">${esc(l)}</div>`).join('') + '</div>';
  const section = ([title, bullets]) => `<h2>${esc(title)}</h2><ul>` +
    bullets.map(([lead, rest]) => `<li>${lead ? `<b>${esc(lead)}</b>` : ''}${esc(rest)}</li>`).join('') + '</ul>';
  const bar = `<div class="barwrap"><div class="barlab">${esc(C.bar.title)}<span>${esc(usd(C.bar.total))}/mo</span></div>` +
    `<div class="bar">` + C.bar.parts.map((p, i) =>
      `<span class="seg s${i}" style="width:${p[2]}%" title="${esc(p[0])}: ${esc(usd(p[1]))}"></span>`).join('') + `</div>` +
    `<div class="barkey">` + C.bar.parts.map((p, i) =>
      `<span><i class="sw s${i}"></i>${esc(p[0])} ${esc(usd(p[1]))} (${p[2]}%)</span>`).join('') + `</div></div>`;
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(C.title)}</title>
<style>
  :root { --ink:#${INK}; --mut:#${MUT}; --acc:#${ACC}; --card:#${CARD}; --line:#${LINE}; --warm:#8A5A00; --warmbg:#FDF6E8; }
  *{box-sizing:border-box}
  body{margin:0;padding:34px 30px;background:#fff;color:var(--ink);
       font:16px/1.5 Calibri,Carlito,"Segoe UI",system-ui,-apple-system,sans-serif;
       max-width:1000px;margin-inline:auto}
  h1{font-size:27px;line-height:1.2;margin:0 0 6px;letter-spacing:-.2px}
  h2{font-size:15px;letter-spacing:.06em;text-transform:uppercase;color:var(--mut);
     margin:20px 0 7px;padding-bottom:4px;border-bottom:1px solid var(--line)}
  .meta{font-size:12.5px;color:var(--mut);margin:0 0 16px;letter-spacing:.02em}
  .status{font-weight:700;color:#b45309;margin:0 0 10px}
  .ask{background:var(--card);border-left:3px solid var(--acc);border-radius:0 6px 6px 0;padding:12px 16px;margin:0 0 15px}
  .ask .k{display:block;font-size:11.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--acc);font-weight:700;margin-bottom:3px}
  .ask p{margin:0;font-size:16.5px;line-height:1.45}
  .lede{margin:0 0 15px;font-size:16px}
  .cards{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:0 0 14px}
  .card{background:var(--card);border-radius:6px;padding:12px 13px}
  .lab{font-size:11.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--mut)}
  .val{font-size:24px;font-weight:700;color:var(--acc);line-height:1.15;margin:3px 0 7px;letter-spacing:-.4px}
  .ln{font-size:11.5px;color:var(--mut);margin-bottom:3px;line-height:1.35}
  .ln.at{color:var(--ink);font-weight:700}
  .barwrap{margin:0 0 15px}
  .barlab{display:flex;justify-content:space-between;font-size:12px;color:var(--mut);margin-bottom:4px}
  .barlab span{font-variant-numeric:tabular-nums}
  .bar{display:flex;height:16px;border-radius:4px;overflow:hidden}
  .seg{display:block;height:100%}.s0{background:var(--acc)}.s1{background:#C9D2E4}
  .barkey{display:flex;gap:16px;margin-top:5px;font-size:12px;color:var(--mut)}
  .sw{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px}
  .delay{background:var(--warmbg);border-radius:6px;padding:11px 15px;margin:0 0 4px;font-size:15px}
  .delay .k{display:block;font-size:11.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--warm);font-weight:700;margin-bottom:3px}
  ul{margin:0;padding-left:19px} li{margin-bottom:6px;line-height:1.45}
  .closing{margin-top:12px;font-size:12.5px;font-style:italic;color:var(--mut);line-height:1.45}
  @media (max-width:820px){ .cards{grid-template-columns:repeat(2,1fr)} body{padding:22px 16px} }
  @media print{ body{padding:0;max-width:none} h2{break-after:avoid} li,.card,.ask,.delay{break-inside:avoid} }
</style></head><body>
<h1>${esc(C.title)}</h1>
<p class="meta">${esc(C.meta)}</p>
${C.status ? `<p class="status">${esc(C.status)}</p>` : ''}
<div class="ask"><span class="k">${esc(C.askLabel)}</span><p>${esc(C.ask)}</p></div>
<p class="lede">${C.lede.map(([s, bold]) => bold ? `<b>${esc(s)}</b>` : esc(s)).join('')}</p>
<div class="cards">${C.cards.map(card).join('')}</div>
${bar}
<div class="delay"><span class="k">${esc(C.delayLabel)}</span>${esc(C.delay)}</div>
${C.sections.map(section).join('\n')}
<p class="closing">${esc(C.closing)}</p>
</body></html>
`;
}

// ------------------------------------------------------------------ DOCX
function renderDocx() {
  let D;
  try { D = require('docx'); } catch (e) {
    const roots = [];
    for (const cmd of ['npm root -g', 'npm root']) {
      try { roots.push(require('child_process').execSync(cmd, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()); } catch (e2) { /* npm not on PATH */ }
    }
    roots.push('/opt/homebrew/lib/node_modules', '/usr/local/lib/node_modules', path.join(process.env.HOME || '', '.local/lib/node_modules'));
    for (const r of roots) { if (!r) continue; try { D = require(path.join(r, 'docx')); break; } catch (e2) { /* next root */ } }
    if (!D) { console.error("exec_summary: cannot resolve the 'docx' module.\nInstall it with:  npm install -g docx"); process.exit(3); }
  }
  const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, WidthType, ShadingType, BorderStyle, LevelFormat, AlignmentType } = D;
  const t = (s, o = {}) => new TextRun({ text: s, font: F, size: o.size || 20, color: o.color || INK, bold: o.bold, italics: o.italics });
  const p = (runs, o = {}) => new Paragraph({ spacing: { after: o.after ?? 80, before: o.before || 0, line: 252 }, children: Array.isArray(runs) ? runs : [t(runs)] });
  const h = s => new Paragraph({ spacing: { before: 150, after: 55 }, border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: LINE, space: 2 } }, children: [t(s.toUpperCase(), { bold: true, size: 15, color: MUT })] });
  const b = (lead, rest) => new Paragraph({ numbering: { reference: 'b', level: 0 }, spacing: { after: 50, line: 252 }, children: [t(lead, { bold: true }), t(rest)] });
  const nb = { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' };
  const wh = { style: BorderStyle.SINGLE, size: 12, color: 'FFFFFF' };
  const card = ([lab, val, lines]) => new TableCell({ width: { size: 2700, type: WidthType.DXA }, shading: { type: ShadingType.CLEAR, fill: CARD }, margins: { top: 80, bottom: 80, left: 120, right: 120 },
    borders: { top: wh, bottom: nb, left: wh, right: wh },
    children: [p([t(lab, { size: 16, color: MUT })], { after: 0 }), p([t(val, { size: 32, bold: true, color: ACC })], { after: 20 }),
      ...lines.map(l => p([t(l, { size: 16, color: l.startsWith('At ') ? INK : MUT, bold: l.startsWith('At ') })], { after: 10 }))] });

  // tinted full-width callout, used for the ask and the cost-of-waiting note
  const callout = (kicker, text, fill, kickerColor) => new Table({
    width: { size: 10800, type: WidthType.DXA }, columnWidths: [10800],
    borders: { top: nb, bottom: nb, left: nb, right: nb, insideHorizontal: nb, insideVertical: nb },
    rows: [new TableRow({ children: [new TableCell({
      width: { size: 10800, type: WidthType.DXA },
      shading: { type: ShadingType.CLEAR, fill: fill },
      margins: { top: 110, bottom: 110, left: 160, right: 160 },
      borders: { top: nb, bottom: nb, left: nb, right: nb },
      children: [
        p([t(kicker.toUpperCase(), { size: 15, bold: true, color: kickerColor })], { after: 30 }),
        p([t(text, { size: 20 })], { after: 0 }),
      ] })] })] });

  // the node-bill split as a two-cell table acting as a bar
  const barRow = new Table({
    width: { size: 10800, type: WidthType.DXA },
    columnWidths: C.bar.parts.map(pt => Math.max(400, Math.round(10800 * pt[2] / 100))),
    borders: { top: nb, bottom: nb, left: nb, right: nb, insideHorizontal: nb, insideVertical: wh },
    rows: [new TableRow({ children: C.bar.parts.map((pt, i) => new TableCell({
      width: { size: Math.max(400, Math.round(10800 * pt[2] / 100)), type: WidthType.DXA },
      shading: { type: ShadingType.CLEAR, fill: i === 0 ? ACC : 'C9D2E4' },
      margins: { top: 40, bottom: 40, left: 60, right: 60 },
      children: [p([t(' ', { size: 12 })], { after: 0 })] })) })] });

  const body = [
    new Paragraph({ spacing: { after: 30 }, children: [t(C.title, { size: 32, bold: true })] }),
    p([t(C.meta, { size: 16, color: MUT })], { after: 110 }),
    ...(C.status ? [p([t(C.status, { size: 17, bold: true, color: 'B45309' })], { after: 60 })] : []),
    callout(C.askLabel, C.ask, CARD, ACC),
    p([], { after: 70 }),
    p(C.lede.map(([s, bold]) => t(s, { bold })), { after: 110 }),
    new Table({ width: { size: 10800, type: WidthType.DXA }, columnWidths: [2700, 2700, 2700, 2700],
      borders: { top: nb, bottom: nb, left: nb, right: nb, insideHorizontal: nb, insideVertical: nb },
      rows: [new TableRow({ children: C.cards.map(card) })] }),
    p([t(C.bar.title + '  -  ' + usd(C.bar.total) + '/mo', { size: 16, color: MUT })], { before: 110, after: 40 }),
    barRow,
    p([t(C.bar.parts.map(pt => pt[0] + ' ' + usd(pt[1]) + ' (' + pt[2] + '%)').join('     '),
        { size: 16, color: MUT })], { before: 40, after: 90 }),
    callout(C.delayLabel, C.delay, 'FDF6E8', '8A5A00'),
    p([], { after: 40 }),
    ...C.sections.flatMap(([title, bullets]) => [h(title), ...bullets.map(([lead, rest]) => b(lead, rest))]),
    p([t(C.closing, { size: 16, italics: true, color: MUT })], { before: 110, after: 0 }),
  ];
  const doc = new Document({
    numbering: { config: [{ reference: 'b', levels: [{ level: 0, format: LevelFormat.BULLET, text: '•', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 300, hanging: 200 } } } }] }] },
    sections: [{ properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 720, bottom: 600, left: 720, right: 720 } } }, children: body }],
  });
  Packer.toBuffer(doc).then(buf => fs.writeFileSync(out, buf));
}

if (isHtml) fs.writeFileSync(out, renderHtml()); else renderDocx();
