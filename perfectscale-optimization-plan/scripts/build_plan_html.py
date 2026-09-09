#!/usr/bin/env python3
"""Render a PerfectScale optimization-plan payload as a self-contained HTML worksheet.

    python3 build_plan_html.py <payload.json> <output.html>

No network at build or open time: no CDN, no fonts, no chart library. Light and
dark are both selected (not an automatic flip) and the print stylesheet forces
light. Validation is strict and fails loudly — a blank cell in front of a
customer is a credibility event, so a missing field stops the build instead.
"""

import html
import json
import sys
from collections import OrderedDict

# --- Palette ---------------------------------------------------------------
# From the dataviz reference palette. Sequential blue carries magnitude (ROI
# meters); the phase ramp is the same hue stepped ordinally. Status colors are
# fixed in both modes and always ship with an icon + label, never color alone.

EFFORT_WEIGHT = {"XS": 1, "S": 2, "M": 4, "L": 8}

RISK = OrderedDict([
    ("low",      ("●", "Low",      "var(--status-good)")),
    ("medium",   ("▲", "Medium",   "var(--status-warning)")),
    ("high",     ("▲", "High",     "var(--status-serious)")),
    ("critical", ("■", "Critical", "var(--status-critical)")),
])

PHASES = OrderedDict([
    (1, ("Prove it",          "Days 0–30")),
    (2, ("Structural",        "Days 30–90")),
    (3, ("Compound & govern", "Day 90+")),
])

OWNER_LABEL = {
    "ps-admin": "PerfectScale admin",
    "platform": "Platform / K8s",
    "app-team": "Application team",
    "finops":   "FinOps",
    "ps-csm":   "PerfectScale CSM",
}

# Short forms for the table, where eleven columns have to fit on a laptop
# screen-share. The full label is used in the per-action detail card.
OWNER_SHORT = {
    "ps-admin": "PS admin",
    "platform": "Platform",
    "app-team": "App team",
    "finops":   "FinOps",
    "ps-csm":   "PS CSM",
}

REQUIRED_TOP = ["tenant", "generated", "scope", "totals", "actions"]
REQUIRED_ACTION = [
    "id", "title", "action_type", "cluster", "scope", "savings_monthly_usd",
    "savings_basis", "confidence", "effort_tier", "effort_rationale",
    "change_risk", "owner_role", "depends_on", "unblocks", "phase", "steps",
    "verification", "evidence",
]
REQUIRED_TOTALS = [
    "achievable_now_monthly", "blocked_pending_monthly",
    "resiliency_investment_monthly",
]


def die(problems):
    print("Payload validation failed:\n", file=sys.stderr)
    for p in problems:
        print("  - " + p, file=sys.stderr)
    print(
        "\nFix the payload rather than the renderer. See "
        "assets/plan-payload.schema.json.",
        file=sys.stderr,
    )
    sys.exit(1)


def validate(p):
    problems = []
    for k in REQUIRED_TOP:
        if k not in p:
            problems.append(f"missing top-level key: {k}")
    for k in REQUIRED_TOTALS:
        if k not in p.get("totals", {}):
            problems.append(f"missing totals.{k}")
    if not p.get("unblocker_sentence"):
        problems.append(
            "missing unblocker_sentence — every plan states how much of the "
            "total sits behind zero-savings prerequisites"
        )

    ids = set()
    for i, a in enumerate(p.get("actions", [])):
        tag = a.get("id") or f"actions[{i}]"
        for k in REQUIRED_ACTION:
            if k not in a:
                problems.append(f"{tag}: missing {k}")
        if a.get("id") in ids:
            problems.append(f"{tag}: duplicate id")
        ids.add(a.get("id"))
        if a.get("effort_tier") not in EFFORT_WEIGHT:
            problems.append(f"{tag}: effort_tier must be XS|S|M|L")
        if a.get("change_risk") not in RISK:
            problems.append(f"{tag}: change_risk must be low|medium|high|critical")
        if a.get("phase") not in PHASES:
            problems.append(f"{tag}: phase must be 1|2|3")
        if a.get("owner_role") not in OWNER_LABEL:
            problems.append(f"{tag}: unknown owner_role {a.get('owner_role')!r}")
        if a.get("savings_basis") not in (
            "achievable_now", "blocked_pending", "resiliency_investment"
        ):
            problems.append(f"{tag}: bad savings_basis")
        steps = a.get("steps") or []
        if not 2 <= len(steps) <= 6:
            problems.append(f"{tag}: steps must be 2-6 (got {len(steps)})")

    # Sequencing invariants — a plan whose order cannot be executed is worse
    # than no plan, so these are build errors, not warnings.
    by_id = {a.get("id"): a for a in p.get("actions", [])}
    for a in p.get("actions", []):
        for dep in a.get("depends_on") or []:
            if dep not in by_id:
                problems.append(f"{a.get('id')}: depends_on unknown action {dep}")
            elif by_id[dep].get("phase", 0) > a.get("phase", 0):
                problems.append(
                    f"{a.get('id')} (phase {a.get('phase')}) depends on {dep} "
                    f"(phase {by_id[dep].get('phase')}) — a prerequisite may "
                    f"never sit in a later phase"
                )
        for u in a.get("unblocks") or []:
            if u not in by_id:
                problems.append(f"{a.get('id')}: unblocks unknown action {u}")
            elif a.get("id") in (by_id[u].get("unblocks") or []):
                problems.append(
                    f"dependency cycle between {a.get('id')} and {u} — these "
                    f"are co-requisites; merge them into one action"
                )
    if problems:
        die(problems)


def esc(s):
    return html.escape(str(s if s is not None else ""))


def money(v, dash_zero=True):
    if v is None:
        return "n/a"
    if v == 0 and dash_zero:
        return "—"
    neg = v < 0
    s = "${:,.0f}".format(abs(v))
    return "(" + s + ")" if neg else s


def risk_cell(level):
    icon, label, color = RISK[level]
    return (
        f'<span class="risk"><span class="risk-icon" aria-hidden="true" '
        f'style="color:{color}">{icon}</span>{esc(label)}</span>'
    )


def meter(score, max_score, action):
    """Horizontal bar meter: single sequential hue, magnitude only.

    Anchored to a left baseline, 4px rounded data-end, thin. The numeric score
    sits beside it in muted ink — text never wears the series color.
    """
    if score is None:
        return '<span class="muted">n/a</span>'
    if not score:
        # A true zero is an unblocker: its value is the released_value shown on
        # the title, not a bar. A 1px sliver would read as a rendering artifact.
        return '<span class="muted">&mdash;</span>'
    pct = 0 if not max_score else max(2.5, round(score / max_score * 100, 1))
    tip = (
        f"ROI {score:,.0f} = {money(action.get('savings_monthly_usd'), False)}/mo "
        f"× {action.get('confidence')} confidence ÷ "
        f"{action.get('effort_tier')} effort"
    )
    return (
        f'<span class="meter" tabindex="0" data-tip="{esc(tip)}">'
        f'<span class="meter-track"><span class="meter-fill" style="width:{pct}%"></span></span>'
        f'<span class="meter-val">{score:,.0f}</span></span>'
    )


def field(key, kind="text"):
    ph = {"owner": "name", "target": "date", "decision": "yes / no"}[kind]
    return (
        f'<input class="ws" type="text" data-key="{esc(key)}" '
        f'placeholder="{ph}" autocomplete="off">'
    )


def dollar_cell(a):
    """The $/mo cell, with a resiliency investment marked as spend.

    A resiliency figure sitting unmarked in the same column as a saving invites
    the reader to add them, which is the one arithmetic the whole plan forbids.
    Parenthesized-and-labelled is the accounting convention and it survives
    being printed in black and white.
    """
    v = a.get("savings_monthly_usd")
    if a.get("savings_basis") == "resiliency_investment" and v:
        return (
            f'<span class="spend">({money(abs(v), False)})'
            f'<span class="sub">spend, not saving</span></span>'
        )
    return money(v)


def row(a, max_score, worksheet):
    title = esc(a["title"])
    if a.get("is_unblocker"):
        rv = money(a.get("released_value") or 0, dash_zero=False)
        title += f' <strong class="unblocker">· unblocker, releases {rv}/mo</strong>'
    conf = ""
    if a.get("confidence") in ("medium", "low"):
        conf = f' <span class="conf">{esc(a["confidence"])} confidence</span>'
    cells = [
        f'<td class="id"><a href="#{esc(a["id"])}">{esc(a["id"])}</a></td>',
        f'<td class="title">{title}{conf}</td>',
        f'<td class="scope">{esc(a["cluster"])}<span class="sub">{esc(a["scope"])}</span></td>',
        f'<td class="num">{dollar_cell(a)}</td>',
        f'<td class="num">{meter(a.get("roi_score"), max_score, a)}</td>',
        f'<td class="ctr"><span class="tier">{esc(a["effort_tier"])}</span></td>',
        f'<td>{risk_cell(a["change_risk"])}</td>',
        f'<td class="role" title="{esc(OWNER_LABEL[a["owner_role"]])}">'
        f'{esc(OWNER_SHORT[a["owner_role"]])}</td>',
    ]
    if worksheet:
        aid = a["id"]
        cells += [
            f'<td class="ws-cell">{field(aid + ".owner", "owner")}</td>',
            f'<td class="ws-cell">{field(aid + ".target", "target")}</td>',
            f'<td class="ws-cell">{field(aid + ".decision", "decision")}</td>',
        ]
    return "<tr>" + "".join(cells) + "</tr>"


def table(actions, max_score, worksheet=True):
    head = [
        "#", "Action", "Cluster / Scope", "$/mo", "ROI", "Effort", "Risk", "Role",
    ]
    if worksheet:
        head += ["Owner", "Target", "Decision"]
    ths = "".join(f"<th>{esc(h)}</th>" for h in head)
    rows = "".join(row(a, max_score, worksheet) for a in actions)
    return (
        f'<div class="table-wrap"><table><thead><tr>{ths}</tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
    )


def detail(a, by_id):
    steps = "".join(f"<li>{esc(s)}</li>" for s in a["steps"])
    facts = [
        money(a.get("savings_monthly_usd"), dash_zero=False) + "/mo",
        f'{a["effort_tier"]} effort',
        RISK[a["change_risk"]][1] + " risk",
        OWNER_LABEL[a["owner_role"]],
    ]
    if a.get("is_unblocker"):
        facts.insert(1, "releases " + money(a.get("released_value") or 0, False) + "/mo")
    unblocks = ""
    if a.get("unblocks"):
        parts = []
        for u in a["unblocks"]:
            t = by_id.get(u, {})
            parts.append(
                f'<a href="#{esc(u)}">{esc(u)}</a> '
                f'({money(t.get("savings_monthly_usd"), False)}/mo)'
            )
        unblocks = f'<p class="meta"><strong>Unblocks:</strong> {", ".join(parts)}</p>'
    deps = ""
    if a.get("depends_on"):
        parts = [f'<a href="#{esc(d)}">{esc(d)}</a>' for d in a["depends_on"]]
        deps = f'<p class="meta"><strong>Requires first:</strong> {", ".join(parts)}</p>'
    return f"""<section class="card" id="{esc(a['id'])}">
  <h4>{esc(a['id'])} — {esc(a['title'])}</h4>
  <p class="facts">{" · ".join(esc(f) for f in facts)}</p>
  <p class="rationale">Effort: {esc(a['effort_rationale'])}</p>
  <p class="evidence">{esc(a['evidence'])}</p>
  <ol class="steps">{steps}</ol>
  <p class="meta"><strong>Verify:</strong> {esc(a['verification'])}</p>
  {deps}{unblocks}
</section>"""


CSS = """
:root{color-scheme:light;
--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--baseline:#c3c2b7;--border:rgba(11,11,11,.10);
--meter:#2a78d6;--p1:#86b6ef;--p2:#2a78d6;--p3:#0d366b;
--status-good:#0ca30c;--status-warning:#fab219;--status-serious:#ec835a;--status-critical:#d03b3b;
--field:#ffffff;}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;
--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;
--grid:#2c2c2a;--baseline:#383835;--border:rgba(255,255,255,.10);
--meter:#3987e5;--p1:#9ec5f4;--p2:#3987e5;--p3:#184f95;--field:#232322;}}
:root[data-theme="dark"]{color-scheme:dark;
--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;
--grid:#2c2c2a;--baseline:#383835;--border:rgba(255,255,255,.10);
--meter:#3987e5;--p1:#9ec5f4;--p2:#3987e5;--p3:#184f95;--field:#232322;}

*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;}
.wrap{max-width:1340px;margin:0 auto;padding:32px 24px 96px}
h1{font-size:26px;margin:0 0 6px;letter-spacing:-.01em}
h2{font-size:19px;margin:44px 0 4px;letter-spacing:-.005em}
h3{font-size:15px;margin:28px 0 10px;color:var(--ink2);font-weight:600}
h4{font-size:14px;margin:0 0 6px}
p{margin:0 0 10px}
a{color:inherit}
.sub{display:block;color:var(--muted);font-size:12px}
/* A resiliency figure must never read as a saving in the same column. */
.spend{color:var(--ink2)}
.spend .sub{font-size:11px;font-style:italic;white-space:nowrap}
.muted{color:var(--muted)}
.deck{color:var(--ink2);font-size:13px;margin:0 0 4px}
.readonly{margin:14px 0 0;padding:9px 12px;border:1px solid var(--border);
border-radius:6px;background:var(--surface);font-size:13px;color:var(--ink2)}

/* KPI row - stat tiles. Three figures, never summed. */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:24px 0 8px}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px 16px}
.kpi .lab{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-bottom:6px}
.kpi .val{font-size:30px;font-weight:600;letter-spacing:-.02em;line-height:1.1}
.kpi .ann{font-size:12px;color:var(--ink2);margin-top:3px}
.nosum{font-size:12px;color:var(--muted);margin:6px 0 0}
.lede{margin:18px 0 0;padding:14px 16px;background:var(--surface);
border:1px solid var(--border);border-left:3px solid var(--meter);border-radius:6px;font-size:15px}

/* Phase headers - ordinal ramp of the sequential hue, always with a text label */
.phase{display:flex;align-items:baseline;gap:10px;margin:44px 0 2px}
.pchip{display:inline-block;width:9px;height:9px;border-radius:2px;flex:0 0 auto}
.p1 .pchip{background:var(--p1)}.p2 .pchip{background:var(--p2)}.p3 .pchip{background:var(--p3)}
.pmeta{font-size:13px;color:var(--ink2);margin:0 0 12px}

/* Tables */
.table-wrap{overflow-x:auto;background:var(--surface);border:1px solid var(--border);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:13px}
th{text-align:left;font-weight:600;font-size:11px;text-transform:uppercase;
letter-spacing:.05em;color:var(--muted);padding:9px 10px;border-bottom:1px solid var(--baseline);
white-space:nowrap}
td{padding:9px 10px;border-bottom:1px solid var(--grid);vertical-align:top}
tr:last-child td{border-bottom:none}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.ctr{text-align:center}
.id a{text-decoration:none;color:var(--muted);font-variant-numeric:tabular-nums}
.id a:hover{color:var(--ink)}
.title{min-width:230px}
.scope{min-width:170px;color:var(--ink2)}
.role{color:var(--ink2);white-space:nowrap}
.unblocker{color:var(--ink)}
.conf{display:inline-block;margin-left:6px;font-size:11px;color:var(--muted);
border:1px solid var(--border);border-radius:3px;padding:0 5px}
.tier{display:inline-block;min-width:26px;font-weight:600;font-size:12px;
font-variant-numeric:tabular-nums;color:var(--ink2)}
.risk{display:inline-flex;align-items:center;gap:5px;white-space:nowrap;color:var(--ink2)}
.risk-icon{font-size:10px;line-height:1}

/* ROI meter - single sequential hue, anchored to a left baseline */
.meter{display:inline-flex;align-items:center;gap:8px;position:relative}
.meter-track{display:block;width:54px;height:8px;background:var(--grid);
border-radius:4px;border-left:1px solid var(--baseline)}
.meter-fill{display:block;height:8px;background:var(--meter);
border-radius:0 4px 4px 0;box-shadow:0 0 0 2px var(--surface)}
.meter-val{font-variant-numeric:tabular-nums;font-size:12px;color:var(--ink2);min-width:30px;text-align:right}
.meter[data-tip]:hover::after,.meter[data-tip]:focus::after{content:attr(data-tip);
position:absolute;left:0;bottom:calc(100% + 7px);z-index:20;white-space:nowrap;
background:var(--ink);color:var(--surface);font-size:11px;padding:5px 8px;
border-radius:4px;pointer-events:none}

/* Worksheet fields - the point of the artifact */
.ws-cell{min-width:90px}
input.ws{width:100%;min-width:82px;font:inherit;font-size:12px;padding:5px 6px;
color:var(--ink);background:var(--field);border:1px dashed var(--baseline);border-radius:4px}
input.ws:focus{outline:2px solid var(--meter);outline-offset:1px;border-style:solid}
input.ws::placeholder{color:var(--muted);opacity:.75}
.wsnote{font-size:12px;color:var(--muted);margin:8px 0 0}

/* Action detail cards */
.card{background:var(--surface);border:1px solid var(--border);border-radius:8px;
padding:16px 18px;margin:12px 0;scroll-margin-top:16px}
.facts{font-size:13px;font-weight:600;color:var(--ink);margin:0 0 4px}
.rationale{font-size:12px;color:var(--muted);margin:0 0 10px}
.evidence{font-size:13px;color:var(--ink2)}
.steps{margin:10px 0;padding-left:20px;font-size:13px}
.steps li{margin:5px 0}
.steps code{font-size:12px;background:var(--grid);padding:1px 4px;border-radius:3px}
.meta{font-size:12px;color:var(--ink2);margin:6px 0 0}
.note{margin:20px 0;padding:14px 16px;background:var(--surface);
border:1px solid var(--border);border-left:3px solid var(--p2);border-radius:6px;font-size:14px}
ul.decisions{margin:10px 0;padding-left:20px}
ul.decisions li{margin:6px 0}

.bar{position:fixed;top:12px;right:12px;display:flex;gap:8px;z-index:40}
.bar button{font:inherit;font-size:12px;padding:5px 11px;border-radius:6px;
border:1px solid var(--border);background:var(--surface);color:var(--ink2);cursor:pointer}
.bar button:hover{color:var(--ink)}

@media print{
  /* Force light regardless of the selected theme. The dark rule is
     :root[data-theme="dark"] (specificity 0,2,0), so overriding a bare :root
     (0,1,0) here loses and the plan prints white-on-black. Match the dark
     selector's specificity so the print values win. */
  :root,:root[data-theme="dark"],:root[data-theme="light"]{color-scheme:light!important;
  --surface:#fff;--page:#fff;--ink:#000;--ink2:#333;--muted:#666;
  --grid:#e1e0d9;--baseline:#c3c2b7;--border:#d5d4cd;--meter:#2a78d6;
  --p1:#86b6ef;--p2:#2a78d6;--p3:#0d366b;--field:#fff;}
  *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
  body{background:#fff}
  .bar{display:none}
  .wrap{max-width:none;padding:0}
  .table-wrap{overflow:visible}
  tr,.card,.kpi{break-inside:avoid;page-break-inside:avoid}
  h2{break-after:avoid;page-break-after:avoid}
  input.ws{border:1px solid #c3c2b7}
  a{text-decoration:none}
}
"""

JS = """
(function(){
  var K='psplan:'+document.body.dataset.planKey+':';
  document.querySelectorAll('input.ws').forEach(function(el){
    var k=K+el.dataset.key, v=null;
    try{v=localStorage.getItem(k)}catch(e){}
    if(v!==null)el.value=v;
    el.addEventListener('input',function(){
      try{localStorage.setItem(k,el.value)}catch(e){}
    });
  });
  var root=document.documentElement, TK='psplan:theme', saved=null;
  try{saved=localStorage.getItem(TK)}catch(e){}
  if(saved)root.setAttribute('data-theme',saved);
  document.getElementById('theme').addEventListener('click',function(){
    var dark=root.getAttribute('data-theme')==='dark'||
      (!root.getAttribute('data-theme')&&matchMedia('(prefers-color-scheme:dark)').matches);
    var next=dark?'light':'dark';
    root.setAttribute('data-theme',next);
    try{localStorage.setItem(TK,next)}catch(e){}
  });
  document.getElementById('clear').addEventListener('click',function(){
    if(!confirm('Clear all Owner / Target / Decision entries on this plan?'))return;
    document.querySelectorAll('input.ws').forEach(function(el){
      el.value='';try{localStorage.removeItem(K+el.dataset.key)}catch(e){}
    });
  });
})();
"""


def build(p):
    validate(p)
    actions = p["actions"]
    by_id = {a["id"]: a for a in actions}
    scored = [a.get("roi_score") for a in actions if a.get("roi_score")]
    max_score = max(scored) if scored else 0
    t = p["totals"]
    key = (p["tenant"] + "-" + p["generated"]).lower().replace(" ", "-")

    def tile(lab, val, ann=""):
        return (
            f'<div class="kpi"><div class="lab">{esc(lab)}</div>'
            f'<div class="val">{val}</div>'
            f'<div class="ann">{esc(ann)}</div></div>'
        )

    a_now = t["achievable_now_monthly"]
    a_blk = t["blocked_pending_monthly"]
    a_res = t["resiliency_investment_monthly"]
    kpis = "".join([
        tile("Achievable now", money(a_now, False), f"{money(a_now * 12, False)} annual run-rate"),
        tile("Blocked pending", money(a_blk, False), f"{money(a_blk * 12, False)} annual run-rate"),
        tile("Resiliency investment", "(" + money(abs(a_res), False) + ")", "added spend, not a saving"),
        tile("Actions", str(len(actions)),
             f"{len([a for a in actions if a.get('is_unblocker')])} unblockers"),
    ])

    sc = p["scope"]
    shown = p.get("actions_shown", len(actions))
    body_actions = [a for a in actions if a.get("in_body", True)]

    parts = [
        f'<h1>PerfectScale Optimization Plan — {esc(p["tenant"])}</h1>',
        f'<p class="deck">Prepared {esc(p["generated"])} · '
        f'{sc["clusters_analyzed"]} of {sc["clusters_total"]} clusters · '
        f'{p.get("data_window_days", 30)}-day data window</p>',
        f'<p class="deck">Showing {shown} of {len(actions)} actions'
        + (f' · {p["session_length_min"]}-minute session' if p.get("session_length_min") else "")
        + " · full list in Appendix A</p>",
        '<p class="readonly">This is an analysis. No changes were applied to your '
        "environment.</p>",
        '<h2>The opportunity</h2>',
        f'<div class="kpis">{kpis}</div>',
        '<p class="nosum">These figures are not summed. Blocked-pending savings need '
        "the remediation in Phases 1 and 2 before they are available, and the "
        "resiliency row is money to spend, not save. Annual figures are 30-day "
        "run-rates — illustrative, not a savings guarantee.</p>",
        f'<p class="lede">{esc(p["unblocker_sentence"])}</p>',
    ]

    if p.get("where_it_is"):
        parts += ["<h2>Where it is</h2>", f"<p>{esc(p['where_it_is'])}</p>"]

    if p.get("decisions"):
        items = "".join(f"<li>{esc(d)}</li>" for d in p["decisions"])
        parts += [
            "<h2>Decisions we need from you today</h2>",
            f'<ul class="decisions">{items}</ul>',
        ]

    if p.get("constraints"):
        parts.append(
            f'<p class="note"><strong>Constraints reflected in this '
            f'sequencing:</strong> {esc(p["constraints"])}</p>'
        )

    for ph, (name, window) in PHASES.items():
        # Render in the payload's own order. Do NOT re-sort by roi_score here:
        # the payload is already topologically ordered, and a pure ROI sort can
        # print a prerequisite below the action that depends on it — a sequence
        # the customer cannot actually execute.
        all_rows = [a for a in actions if a["phase"] == ph]
        rows = [a for a in body_actions if a["phase"] == ph]
        if not rows:
            continue
        # Subtotal per savings_basis, never blended — the same rule the
        # headline follows. A phase that mixes achievable-now with
        # blocked-pending shows two figures, not their sum. Subtotal the WHOLE
        # phase, not just the rows above the session cap, so the phase figures
        # still reconcile against Appendix A.
        def basis_sum(b):
            return sum(
                a.get("savings_monthly_usd") or 0
                for a in all_rows if a.get("savings_basis") == b
            )

        bits = [f"{len(all_rows)} actions"
                + (f" ({len(rows)} shown)" if len(rows) < len(all_rows) else "")]
        now, blocked, resil = (
            basis_sum("achievable_now"),
            basis_sum("blocked_pending"),
            basis_sum("resiliency_investment"),
        )
        if now or not (blocked or resil):
            bits.append(f"{money(now, False)}/mo achievable")
        if blocked:
            bits.append(f"{money(blocked, False)}/mo blocked pending")
        if resil:
            bits.append(f"({money(abs(resil), False)})/mo resiliency investment")
        # The union of downstream actions outside this phase — NOT the sum of
        # released_value across unblockers. Two unblockers on one chain both
        # count that chain, so summing overstates it, sometimes by 2-3x. This is
        # the same double-count the plan forbids when summing achievable-now
        # with blocked-pending, so it must not reappear in a phase subtotal.
        downstream, seen = set(), set()

        def walk(aid):
            for d in by_id.get(aid, {}).get("unblocks", []) or []:
                if d in seen:
                    continue
                seen.add(d)
                downstream.add(d)
                walk(d)

        for a in all_rows:
            walk(a["id"])
        released = sum(
            by_id[d].get("savings_monthly_usd") or 0
            for d in downstream
            if d in by_id
            and by_id[d].get("phase") != ph
            and by_id[d].get("savings_basis") != "resiliency_investment"
        )
        if released:
            bits.append(f"unlocks a further {money(released, False)}/mo in later phases")
        meta = " · ".join(bits)
        parts += [
            f'<div class="phase p{ph}"><span class="pchip"></span>'
            f"<h2>Phase {ph} — {esc(name)}</h2>"
            f'<span class="muted">{esc(window)}</span></div>',
            f'<p class="pmeta">{esc(meta)}</p>',
            table(rows, max_score, worksheet=True),
            '<p class="wsnote">Owner, Target and Decision are yours to fill in during '
            "this session — entries save in this browser.</p>",
            "<h3>Detail</h3>",
        ]
        parts += [detail(a, by_id) for a in rows]
        if ph == 1 and p.get("flywheel_note"):
            parts.append(f'<p class="note">{esc(p["flywheel_note"])}</p>')

    if p.get("checkpoints"):
        rows = "".join(
            f"<tr><td>{esc(c['name'])}</td><td>{esc(c['date'])}</td>"
            f"<td>{esc(c['what'])}</td><td>{esc(c['expected'])}</td></tr>"
            for c in p["checkpoints"]
        )
        parts += [
            "<h2>How we&rsquo;ll know it worked</h2>",
            '<div class="table-wrap"><table><thead><tr><th>Checkpoint</th><th>Date</th>'
            f"<th>What to look at</th><th>Expected</th></tr></thead><tbody>{rows}</tbody></table></div>",
        ]
        b = p.get("baseline") or {}
        if b:
            parts.append(
                f'<p class="wsnote">Baseline today: {b.get("days_active", "—")} days '
                f'active, {money(b.get("total_saved_usd"), False)} saved, '
                f'{b.get("risks_addressed", "—")} risks addressed.</p>'
            )

    allsorted = sorted(actions, key=lambda a: (a["phase"], -(a.get("roi_score") or 0)))
    parts += [
        f"<h2>Appendix A — All {len(actions)} actions</h2>",
        table(allsorted, max_score, worksheet=False),
    ]

    if p.get("method_notes"):
        items = "".join(f"<li>{esc(n)}</li>" for n in p["method_notes"])
        parts += [
            "<h2>Appendix B — Method, confidence and limits</h2>",
            f'<ul class="decisions">{items}</ul>',
        ]
    parts.append(
        '<p class="wsnote">Actions are ranked by monthly saving, adjusted for '
        "confidence, divided by an effort weight (XS=1, S=2, M=4, L=8). Effort comes "
        "from a fixed rubric, then escalates for production blast radius, restart "
        "requirements, cross-team ownership and unmet prerequisites — each "
        "action&rsquo;s detail shows which applied.</p>"
    )

    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>PerfectScale Optimization Plan — {esc(p['tenant'])}</title>"
        f"<style>{CSS}</style></head>"
        f'<body data-plan-key="{esc(key)}">'
        '<div class="bar"><button id="theme" type="button">Theme</button>'
        '<button id="clear" type="button">Clear entries</button></div>'
        f'<div class="wrap">{"".join(parts)}</div>'
        f"<script>{JS}</script></body></html>\n"
    )


def main():
    if len(sys.argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        sys.exit(2)
    with open(sys.argv[1]) as f:
        payload = json.load(f)
    out = build(payload)
    with open(sys.argv[2], "w") as f:
        f.write(out)
    n = len(payload["actions"])
    print(f"Wrote {sys.argv[2]} — {n} actions, "
          f"{len([a for a in payload['actions'] if a.get('is_unblocker')])} unblockers")


if __name__ == "__main__":
    main()
