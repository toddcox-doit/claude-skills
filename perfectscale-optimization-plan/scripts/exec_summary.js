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
const gatedUsd = S.automation_gated.usd, hpaUsd = S.automation_gated.hpa_usd || 0;   // hpa_usd absent in older plan JSON
const idlePct = Math.round(100 * I.total / I.node_cost);
const nClusters = S.scope.cluster_names.length;
const days = Math.round(S.window_days);
// S.window_days is the LONGEST cluster window. Quoting it alone hides an early baseline: one
// cluster on 18 days and another on 4 read as "18 days of measured data" while the second is
// scaled x8. Use the per-cluster baselines; derive them from run_rate_factor for older plan JSON.
const minHist = S.min_history_days || 14;
const BASE = (Array.isArray(S.baselines) ? S.baselines
  : Object.entries(S.run_rate_factor || {}).map(function (kv) {
      const w = kv[1] > 1.001 ? 30 / kv[1] : S.window_days;
      return {name: kv[0], factor: kv[1], window_days: w, early: w < minHist};
    })).slice().sort(function (a, b) { return b.window_days - a.window_days; });
const dayOf = b => Math.max(1, Math.round(b.window_days));
const dMin = BASE.length ? dayOf(BASE[BASE.length - 1]) : days;
const mixed = BASE.length > 1 && dMin !== days;
const daysLabel = mixed ? dMin + ' to ' + days : String(days);
const earlyB = BASE.filter(b => b.early);
const EA = S.early_achievable || {usd: 0, obs: 0};
const andList = xs => xs.length < 2 ? xs.join('') : xs.slice(0, -1).join(', ') + ' and ' + xs[xs.length - 1];
const annual = O.maximum.usd * 12;
const annualAdj = (T.achievable_now_monthly + T.blocked_pending_monthly) * A.factor * 12;
const inUse = I.node_cost - I.total;

const ROLE = {platform: 'the platform team', 'app-team': 'the application teams',
              'ps-admin': 'a PerfectScale administrator', finops: 'the FinOps lead',
              'ps-csm': 'your PerfectScale contact'};
const phase1 = S.actions.filter(a => a.phase === 1);
// The plan already ranks the opening moves (unblockers and critical risk first, then by the value
// they release). Taking the first three Phase 1 rows instead re-ranks them by nothing in particular
// and drops the unblockers, so the one-pager would recommend a different first move than the plan.
const byId = {};
S.actions.forEach(function (a) { byId[a.id] = a; });
// A plan JSON without a first_moves field at all was rendered by an older version of this skill -
// fall back, but say so, rather than silently reproducing the exact "re-ranks by nothing in
// particular and drops the unblockers" bug this field exists to fix. A first_moves field that is
// *present but empty* (a tenant with zero Phase 1 actions) is not this case and needs no warning.
const hasFirstMoves = Array.isArray(S.first_moves);
if (!hasFirstMoves) {
  console.error('warning: plan JSON has no first_moves field (rendered by an older version of this ' +
    'skill) - falling back to the first three Phase 1 actions by insertion order, which can drop ' +
    'unblockers. Re-render the plan JSON with the current engine for an accurate "What we recommend ' +
    'first" section.');
}
const firstMoves = (S.first_moves || []).map(function (id) { return byId[id]; })
  .filter(Boolean);
const openingMoves = hasFirstMoves ? firstMoves : phase1.slice(0, 3);
const EFFORT = {XS: 'a few hours', S: 'a few days', M: 'a few weeks', L: 'about a quarter'};
// Titles and decision text both carry Markdown code ticks around resource names for the plan's own
// rendering. Neither the .docx nor this HTML interprets them, so they would reach an executive as
// literal backticks if not stripped here.
const stripTicks = s => s.replace(/`/g, '');
function move(a) {
  var who = ROLE[a.owner_role] || 'the owning team';
  var cost = a.savings_basis === 'resiliency_investment'
    ? 'costs ' + usd(r100(a.savings_monthly_usd)) + '/mo and buys reliability'
    : (r100(a.savings_monthly_usd) >= 100 ? 'worth ' + usd(r100(a.savings_monthly_usd)) + '/mo'
       : (a.is_unblocker ? 'no saving of its own: it clears the way for the right-sizing below'
          : 'no dollar claim yet'));
  return [stripTicks(a.title) + '. ',
          'Led by ' + who + ', ' + (EFFORT[a.effort_tier] || 'a few weeks') + ' of work, ' + cost + '.'];
}

const C = {
  title: S.tenant + ': ' + usd(r100(annual)) + ' a year of addressable Kubernetes spend',
  meta: 'Executive summary  |  ' + nClusters + ' clusters  |  ' + daysLabel +
        ' days of measured data  |  ' + ld(S.generated),
  status: S.status ? S.status + ': figures and wording are open to change.' : null,

  askLabel: 'The decision',
  // When automation-gated money sits on workloads whose HPA scales on pod CPU/memory, the decision
  // is pod automation with PerfectScale's HPA automation alongside, and the summary says so.
  ask: gated >= 50 && r100(hpaUsd) >= 100
    ? 'Approve switching PerfectScale automation on, starting with one non-production cluster, with HPA automation ' +
      'alongside: ' + usd(r100(gatedUsd)) + ' a month of the savings below waits on it, ' + usd(r100(hpaUsd)) +
      ' of that on workloads whose HPAs scale on CPU or memory.'
    : gated >= 50
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
    ['What we recommend first', openingMoves.map(move)],
    ['What we need from you', S.decision_detail.slice(0, 4).map(function (d) {
      return ['', stripTicks(d.text.replace(/\s+/g, ' ')) +
              (r100(d.usd) >= 100 ? ' Unlocks about ' + usd(r100(d.usd)) + ' a month.' : '')]; })],
    ['What the numbers rest on', [
      ['', mixed
        ? 'Figures come from measured data, not a sample or a model: ' +
          andList(BASE.map(b => dayOf(b) + ' days on ' + b.name)) + '.'
        : 'Figures come from ' + days + ' days of measured data across ' + nClusters +
          ' clusters, not a sample or a model.'],
      ...(earlyB.length ? [['', andList(earlyB.map(b => b.name)) + (earlyB.length > 1 ? ' have' : ' has') +
           ' under ' + minHist + ' days of history, so ' + (earlyB.length > 1 ? 'their' : 'its') +
           ' figures are scaled up to a monthly rate and are indicative until a full month is measured.' +
           (r100(EA.usd) >= 100 ? ' At least ' + usd(r100(EA.usd)) + ' a month of the savings available now rests on ' +
             (earlyB.length > 1 ? 'them' : 'it') + ', from ' + usd(r100(EA.obs)) + ' actually observed.' : '')]] : []),
      ['', gated + '% of what is available now depends on switching automation on. The remainder is ' +
           'configuration your teams change directly.'],
      ...(oom ? [['', 'Separately, ' + (nOom === '1' ? 'one workload is' : 'about ' + nOom + ' workloads are') +
           ' being restarted for running out of memory' +
           (nMem ? ' and ' + nMem + (nMem === '1' ? ' has' : ' have') + ' no memory limit' : '') + '. ' +
           (r100(oom.savings_monthly_usd) >= 100 ? 'Fixing that costs about ' + usd(r100(oom.savings_monthly_usd)) + ' a month'
             : 'Fixing that adds little or no spend') + ' and should proceed whatever is decided commercially.']] : []),
      ...(opt ? [['', nOpt + ' pods carry settings that stop nodes draining, holding back node savings.']] : []),
      ...(apShare > 0.3 ? [['', 'Most idle sits on short-lived pools the autoscaler creates, so its cause ' +
           'needs confirming before any of it is claimed.']] : []),
    ]],
  ],

  closing: 'Savings and reliability spend are counted separately and idle capacity is never added to either. ' +
           'The ' + f + '% view is the conservative planning case. ' +
           // A POV runs 30 days: promise a look on demand or at 30-day maturity, never a date months out.
           ((S.revisit && S.revisit.text) || 'We can revisit these figures on demand and report what moved.') +
           (S.early ? ' Costs are scaled from a short window to a 30-day run rate.' : ''),
};

// ----------------------------------------------------- savings goal vs data
// Present only when the tenant config carries a savings_goal (plan JSON field `goal`, built by
// psplan/goal.py). Each bar runs from $0: conservative, realistic and theoretical-maximum zones,
// with the goal drawn over them as a marker (or a band for a range). Cluster rows share one scale
// so their lengths compare; the all-clusters total gets its own, or it would flatten the rows.
const G = S.goal && ((S.goal.rows || []).length || S.goal.total) ? S.goal : null;
const VCLS = {supported: 'ok', stretch: 'st', unlikely: 'un', not_supported: 'no'};
const goalTxt = r => usd(r.goal_low) + (r.goal_high !== r.goal_low ? '-' + usd(r.goal_high) : '') + '/mo';
function goalSegs(r, scale) {
  const g1 = r.goal_low, g2 = Math.max(r.goal_high, r.goal_low + scale * 0.008);
  const clip = v => Math.min(Math.max(v, 0), scale);
  const pts = Array.from(new Set([0, r.conservative, r.realistic, r.maximum, g1, g2, scale].map(clip)))
    .sort((a, b) => a - b);
  const out = [];
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i], b = pts[i + 1];
    if (b - a < scale * 1e-6) continue;
    const m = (a + b) / 2;
    const k = (m >= g1 && m <= g2) ? 'goal' : m <= r.conservative ? 'z1' : m <= r.realistic ? 'z2'
      : m <= r.maximum ? 'z3' : 'z0';
    if (out.length && out[out.length - 1][1] === k) out[out.length - 1][0] += (b - a) / scale;
    else out.push([(b - a) / scale, k]);
  }
  return out;
}
function goalRow(r, scale) {
  return {
    name: r.name, goal: 'Goal ' + goalTxt(r), verdict: r.verdict_label, vcls: VCLS[r.verdict],
    segs: goalSegs(r, scale), sentence: r.sentence,
    figs: 'Conservative ' + usd(r.conservative) + '  |  Realistic ' + usd(r.realistic) +
          '  |  Maximum ' + usd(r.maximum) +
          (r.goal_share_of_bill != null ? '  |  Goal = ' + Math.round(r.goal_share_of_bill * 100) +
            '% of ' + usd(r.node_cost) + '/mo node bill' : ''),
  };
}
if (G) {
  const top = r => Math.max(r.maximum, r.goal_high) * 1.08 || 1;
  const sc = Math.max(1, ...G.rows.map(top));
  const gf = Math.round(G.adoption_factor * 100);
  C.goal = {
    title: 'Savings goal vs what the data supports',
    takeaway: G.takeaway,
    rows: G.rows.map(r => goalRow(r, sc)),
    total: G.total ? goalRow(G.total, top(G.total)) : null,
    key: [['z1', 'Conservative: ' + gf + '% of right-sizing'], ['z2', 'Realistic: with node scale-down'],
          ['z3', 'Theoretical maximum'], ['goal', 'Goal']],
    note: (G.source ? 'Goal: ' + G.source + '. ' : '') +
          (G.baseline_source ? 'Baseline: ' + G.baseline_source + '. ' : '') +
          ((G.missing_clusters || []).length ? 'Not collected in this run, so not checked: ' +
            G.missing_clusters.join(', ') + '. ' : '') + G.method,
  };
}

// ------------------------------------------------------------------ HTML
function renderHtml() {
  const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const card = ([lab, val, lines]) => `<div class="card"><div class="lab">${esc(lab)}</div><div class="val">${esc(val)}</div>` +
    lines.map(l => `<div class="${l.indexOf('At ') === 0 ? 'ln at' : 'ln'}">${esc(l)}</div>`).join('') + '</div>';
  // Each category is a <details> block, closed by default: the reader sees the decision, the
  // headline cards and the list of categories, and opens what they came for. Native HTML, so it
  // works without script; the one script only opens everything for printing.
  const sec = (title, inner) => `<details class="sec"><summary><h2>${esc(title)}</h2></summary><div class="secb">${inner}</div></details>`;
  const section = ([title, bullets]) => sec(title, '<ul>' +
    bullets.map(([lead, rest]) => `<li>${lead ? `<b>${esc(lead)}</b>` : ''}${esc(rest)}</li>`).join('') + '</ul>');
  const bar = `<div class="barwrap"><div class="barlab">${esc(C.bar.title)}<span>${esc(usd(C.bar.total))}/mo</span></div>` +
    `<div class="bar">` + C.bar.parts.map((p, i) =>
      `<span class="seg s${i}" style="width:${p[2]}%" title="${esc(p[0])}: ${esc(usd(p[1]))}"></span>`).join('') + `</div>` +
    `<div class="barkey">` + C.bar.parts.map((p, i) =>
      `<span><i class="sw s${i}"></i>${esc(p[0])} ${esc(usd(p[1]))} (${p[2]}%)</span>`).join('') + `</div></div>`;
  const gRow = (r, cls) => `<div class="grow${cls || ''}"><div class="gname"><b>${esc(r.name)}</b><span>${esc(r.goal)}</span></div>` +
    `<div class="gbar" role="img" aria-label="${esc(r.sentence)}">` +
    r.segs.map(([w, k]) => `<span class="gz ${k}" style="width:${(w * 100).toFixed(2)}%"></span>`).join('') + `</div>` +
    `<span class="pill v-${r.vcls}">${esc(r.verdict)}</span>` +
    `<div class="gfig">${esc(r.figs)}</div><div class="gsen">${esc(r.sentence)}</div></div>`;
  const goal = C.goal ? sec(C.goal.title, `<p class="gtake">${esc(C.goal.takeaway)}</p><div class="goal">` +
    C.goal.rows.map(r => gRow(r)).join('') + (C.goal.total ? gRow(C.goal.total, ' gtot') : '') +
    `<div class="gkey">` + C.goal.key.map(([k, l]) => `<span><i class="sw k-${k}"></i>${esc(l)}</span>`).join('') + `</div></div>` +
    `<p class="gnote">${esc(C.goal.note)}</p>`) : '';
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
  .gtake{margin:0 0 9px;font-size:15.5px;font-weight:700}
  .goal{background:var(--card);border-radius:6px;padding:12px 14px 9px;margin:0 0 6px}
  .grow{display:grid;grid-template-columns:200px 1fr 112px;column-gap:12px;row-gap:2px;align-items:center;padding:7px 0}
  .grow+.grow{border-top:1px solid var(--line)}
  .grow.gtot{border-top:2px solid #AEB8C8;margin-top:2px}
  .gname b{display:block;font-size:13.5px;word-break:break-word;line-height:1.25}
  .gname span{font-size:12px;color:var(--mut)}
  .gbar{display:flex;height:18px;border-radius:3px;overflow:hidden;background:#E6EAF1}
  .gz{display:block;height:100%}
  .gz.z1{background:var(--acc)}.gz.z2{background:#7E9BDB}.gz.z3{background:#C9D2E4}.gz.z0{background:#E6EAF1}
  .gz.goal{background:#B45309}
  .pill{justify-self:start;font-size:11.5px;font-weight:700;letter-spacing:.03em;border-radius:10px;padding:2px 9px;white-space:nowrap}
  .v-ok{background:#E3F3EA;color:#1E6B43}.v-st{background:#FDF3DC;color:#8A5A00}
  .v-un{background:#FDE9D7;color:#9A3D06}.v-no{background:#FBE4E2;color:#A3241B}
  .gfig,.gsen{grid-column:2 / 4;font-size:11.5px;color:var(--mut);line-height:1.35}
  .gsen{color:var(--ink)}
  .gkey{display:flex;flex-wrap:wrap;gap:6px 16px;margin-top:6px;font-size:11.5px;color:var(--mut)}
  .k-z1{background:var(--acc)}.k-z2{background:#7E9BDB}.k-z3{background:#C9D2E4}.k-goal{background:#B45309}
  .gnote{font-size:11.5px;color:var(--mut);margin:0 0 12px;line-height:1.4}
  .delay{background:var(--warmbg);border-radius:6px;padding:11px 15px;margin:0 0 4px;font-size:15px}
  .delay .k{display:block;font-size:11.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--warm);font-weight:700;margin-bottom:3px}
  ul{margin:0;padding-left:19px} li{margin-bottom:6px;line-height:1.45}
  details.sec{border-bottom:1px solid var(--line)}
  details.sec>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:8px;padding:2px 0}
  details.sec>summary::-webkit-details-marker{display:none}
  details.sec>summary::before{content:'';width:7px;height:7px;border-right:2px solid var(--mut);border-bottom:2px solid var(--mut);
     transform:rotate(-45deg);transition:transform .15s;flex:none;margin-left:2px}
  details.sec[open]>summary::before{transform:rotate(45deg)}
  details.sec>summary h2{border:0;margin:12px 0;padding:0;flex:1}
  details.sec>summary:hover h2{color:var(--ink)}
  details.sec>summary:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
  .secb{padding:0 0 12px}
  .secbar{display:flex;justify-content:flex-end;gap:12px;margin:18px 0 0}
  .secbar button{background:none;border:0;padding:0;color:var(--acc);font:inherit;font-size:12.5px;cursor:pointer}
  .secbar button:hover{text-decoration:underline}
  .closing{margin-top:12px;font-size:12.5px;font-style:italic;color:var(--mut);line-height:1.45}
  @media (max-width:820px){ .cards{grid-template-columns:repeat(2,1fr)} body{padding:22px 16px}
    .grow{grid-template-columns:1fr auto} .pill{grid-row:1;grid-column:2} .gbar{grid-column:1 / 3} .gfig,.gsen{grid-column:1 / 3} }
  @media print{ body{padding:0;max-width:none} h2{break-after:avoid} .secbar{display:none} details.sec>summary::before{display:none} li,.card,.ask,.delay,.grow{break-inside:avoid}
    .gz,.pill,.sw{-webkit-print-color-adjust:exact;print-color-adjust:exact} }
</style></head><body>
<h1>${esc(C.title)}</h1>
<p class="meta">${esc(C.meta)}</p>
${C.status ? `<p class="status">${esc(C.status)}</p>` : ''}
<div class="ask"><span class="k">${esc(C.askLabel)}</span><p>${esc(C.ask)}</p></div>
<p class="lede">${C.lede.map(([s, bold]) => bold ? `<b>${esc(s)}</b>` : esc(s)).join('')}</p>
<div class="cards">${C.cards.map(card).join('')}</div>
${bar}
<div class="delay"><span class="k">${esc(C.delayLabel)}</span>${esc(C.delay)}</div>
<div class="secbar"><button type="button" data-open="1">Expand all</button><button type="button" data-open="0">Collapse all</button></div>
${C.sections.map(section).join('\n')}
${goal}
<p class="closing">${esc(C.closing)}</p>
<script>
document.querySelectorAll('.secbar button').forEach(function (b) {
  b.addEventListener('click', function () {
    document.querySelectorAll('details.sec').forEach(function (d) { d.open = b.dataset.open === '1'; });
  });
});
window.addEventListener('beforeprint', function () {
  document.querySelectorAll('details.sec').forEach(function (d) { d.open = true; });
});
</script>
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

  // Savings goal vs data: one three-column row per goal (name | bar | verdict). The bar is a nested
  // single-row table whose cells are the zones, so Word draws it without an image.
  const GFILL = { z1: ACC, z2: '7E9BDB', z3: 'C9D2E4', z0: 'E6EAF1', goal: 'B45309' };
  const PILL = { ok: ['E3F3EA', '1E6B43'], st: ['FDF3DC', '8A5A00'], un: ['FDE9D7', '9A3D06'], no: ['FBE4E2', 'A3241B'] };
  const GW = [2500, 6500, 1800], BARW = 6260;
  const goalBar = segs => {
    const ws = segs.map(([w]) => Math.max(24, Math.round(BARW * w)));
    return new Table({ width: { size: ws.reduce((a, b) => a + b, 0), type: WidthType.DXA }, columnWidths: ws,
      borders: { top: nb, bottom: nb, left: nb, right: nb, insideHorizontal: nb, insideVertical: nb },
      rows: [new TableRow({ children: segs.map(([, k], i) => new TableCell({
        width: { size: ws[i], type: WidthType.DXA }, shading: { type: ShadingType.CLEAR, fill: GFILL[k] },
        margins: { top: 0, bottom: 0, left: 0, right: 0 }, borders: { top: nb, bottom: nb, left: nb, right: nb },
        children: [p([t(' ', { size: 14 })], { after: 0 })] })) })] });
  };
  const goalRowDocx = (r, isTotal) => new TableRow({ children: [
    new TableCell({ width: { size: GW[0], type: WidthType.DXA }, margins: { top: 70, bottom: 70, left: 100, right: 80 },
      borders: { top: isTotal ? { style: BorderStyle.SINGLE, size: 8, color: 'AEB8C8' } : nb, bottom: nb, left: nb, right: nb },
      shading: { type: ShadingType.CLEAR, fill: CARD },
      children: [p([t(r.name, { size: 18, bold: true })], { after: 0 }), p([t(r.goal, { size: 16, color: MUT })], { after: 0 })] }),
    new TableCell({ width: { size: GW[1], type: WidthType.DXA }, margins: { top: 70, bottom: 70, left: 120, right: 120 },
      borders: { top: isTotal ? { style: BorderStyle.SINGLE, size: 8, color: 'AEB8C8' } : nb, bottom: nb, left: nb, right: nb },
      shading: { type: ShadingType.CLEAR, fill: CARD },
      children: [goalBar(r.segs), p([t(r.figs, { size: 15, color: MUT })], { before: 30, after: 10 }),
                 p([t(r.sentence, { size: 15 })], { after: 0 })] }),
    new TableCell({ width: { size: GW[2], type: WidthType.DXA }, margins: { top: 70, bottom: 70, left: 80, right: 100 },
      borders: { top: isTotal ? { style: BorderStyle.SINGLE, size: 8, color: 'AEB8C8' } : nb, bottom: nb, left: nb, right: nb },
      shading: { type: ShadingType.CLEAR, fill: CARD },
      children: [new Paragraph({ spacing: { after: 0 }, shading: { type: ShadingType.CLEAR, fill: PILL[r.vcls][0] },
        children: [t(' ' + r.verdict + ' ', { size: 16, bold: true, color: PILL[r.vcls][1] })] })] }),
  ] });
  // Page 1 is the one-pager and is full without this; the goal check takes page 2 whole.
  const goalBlock = !C.goal ? [] : [
    new Paragraph({ pageBreakBefore: true, spacing: { before: 0, after: 55 },
      border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: LINE, space: 2 } },
      children: [t(C.goal.title.toUpperCase(), { bold: true, size: 15, color: MUT })] }),
    p([t(C.goal.takeaway, { size: 19, bold: true })], { after: 70 }),
    new Table({ width: { size: 10800, type: WidthType.DXA }, columnWidths: GW,
      borders: { top: nb, bottom: nb, left: nb, right: nb, insideHorizontal: nb, insideVertical: nb },
      rows: [...C.goal.rows.map(r => goalRowDocx(r, false)), ...(C.goal.total ? [goalRowDocx(C.goal.total, true)] : [])] }),
    p(C.goal.key.flatMap(([k, l], i) => [t((i ? '     ' : '') + '\u25A0 ', { size: 16, color: GFILL[k] }), t(l, { size: 15, color: MUT })]),
      { before: 50, after: 30 }),
    p([t(C.goal.note, { size: 15, color: MUT })], { after: 90 }),
  ];

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
    ...goalBlock,
  ];
  const doc = new Document({
    numbering: { config: [{ reference: 'b', levels: [{ level: 0, format: LevelFormat.BULLET, text: '•', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 300, hanging: 200 } } } }] }] },
    sections: [{ properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 720, bottom: 600, left: 720, right: 720 } } }, children: body }],
  });
  Packer.toBuffer(doc).then(buf => fs.writeFileSync(out, buf));
}

if (isHtml) fs.writeFileSync(out, renderHtml()); else renderDocx();
