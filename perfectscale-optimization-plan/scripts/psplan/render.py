"""Renderers: Markdown (plan, per-cluster findings), self-contained HTML, XLSX/CSV, JSON."""
import csv
import html
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from .model import short_name
from .util import long_date, pct, slug, usd

PHASE = {1: "Phase 1 - Foundations and quick wins", 2: "Phase 2 - Capture the savings", 3: "Phase 3 - Keep the gains"}
BASIS = {"achievable_now": "Savings (achievable)", "blocked_pending": "Savings (pending blockers)", "resiliency_investment": "Spend (resiliency)"}
LANE = {"toggle": "Toggle", "config": "Config change", "manage": "Managed", "manual": "Manual", "platform": "Platform", "decision": "Decision"}
# Icon + word, always both -- never color alone, so the plan reads in black and white.
RISK_ICON = {"low": "● Low", "medium": "▲ Medium", "high": "▲ High", "critical": "■ Critical"}


def risk_text(level):
    return RISK_ICON.get(level, str(level).title())


def fname(cfg, stem, ext):
    return f"{slug(cfg['customer'])}-{stem}-{cfg['prepared']}-{cfg['version_tag']}.{ext}"


# ---------------------------------------------------------------- shared text
def money(a):
    """Dollar/Value-column text only. An unblocker's released_value is never shown
    here -- it would read as the row's own saving, the same overstatement as summing
    achievable-now with blocked-pending. Use unblock_note() for that figure instead,
    rendered in a separate, clearly non-dollar-column location (see plan-voice.md's
    'Unblockers' table convention)."""
    if a["savings_basis"] == "resiliency_investment":
        r = a.get("range")
        return f"{usd(r['likely'])} spend (range {usd(r['low'])}-{usd(r['literal'])})" if r else f"{usd(a['savings_monthly_usd'])} spend"
    if a["savings_monthly_usd"]:
        return usd(a["savings_monthly_usd"]) + "/mo"
    return "—"  # true zero (e.g. an unblocker's own savings) -- never a bare 0


def unblock_note(a):
    """Annotation text for a pure unblocker action (own savings = 0 but it releases
    value elsewhere). Callers must place this away from the dollar/Value column --
    e.g. appended to the title cell or as separate prose -- per plan-voice.md."""
    if not a.get("savings_monthly_usd") and a.get("released_value"):
        return f"unblocker, releases {usd(a['released_value'])}/mo"
    return ""


def mo_yr(v):
    """Monthly headline figure with its annual run-rate alongside (never replacing
    it), per plan-voice.md: '30-day actuals x 12 for annual, always labeled
    run-rate, illustrative, not a guarantee.' Scoped to plan-level headline totals
    only -- do not use this per-action."""
    return f"{usd(v)}/mo (~{usd(v * 12)}/yr run-rate, illustrative)"


GROUPING_SOURCES = [("Ephemeral workloads grouping", "https://docs.perfectscale.io/customize-workflow/ephemeral-workloads-grouping"), ("InfraFit: node right-sizing", "https://docs.perfectscale.io/visibility-and-optimization/infrafit-or-node-right-sizing"), ("PodFit: vertical pod right-sizing", "https://docs.perfectscale.io/visibility-and-optimization/podfit-or-vertical-pod-right-sizing"), ("Node group view", "https://docs.perfectscale.io/visibility-and-optimization/infrafit-or-node-right-sizing/node-group-view"), ("Karpenter optimization", "https://docs.perfectscale.io/visibility-and-optimization/karpenter-optimization")]

MONEY_BASES = [
    "The four blocks at the top come from two analyses. PodFit looks at workloads and how much CPU and memory each one requests: it produces Achievable now (a saving that can start immediately) and Resiliency spend (money to add so under-provisioned workloads stop failing). InfraFit looks at the nodes those workloads run on: it produces Pending blockers (a saving that waits on a blocker such as a stale annotation or a pool minimum) and Node idle (capacity you pay for that pods do not use, a measurement of today rather than a saving).",
    "Three rules keep the numbers honest.",
    "1. Savings and spend are never netted. Resiliency spend is protection. Subtracting it from savings would hide a reliability fix inside a cost story.",
    "2. Node idle is never added to anything. Part of Pending blockers is a slice of it, so adding them counts the same dollar twice.",
    "3. Achievable and Pending overlap and differ in certainty. Right-sizing requests is what lets nodes be removed, so the same dollar can sit in both, and Pending includes an idle ceiling that no pool fully reaches. They are combined only in the Planning range, which is labelled as such.",
]
FEEDBACK_PROMPTS = [
    "Which decision in the ranked list would you raise first on a call?",
    "What is missing that this customer or their leadership would expect to see?",
]


def adoption_rows(S):
    ad = S["adoption"]; f = ad["factor"]; pct_f = f"{f * 100:.0f}%"
    pb = ad["blocked_pending"]
    return pct_f, [
        ("Achievable now", "PodFit", "PerfectScale computed", usd(ad["achievable_now"]["full"]), usd(ad["achievable_now"]["adjusted"]), "Savings assume every recommendation in scope is applied."),
        ("Resiliency spend (likely)", "PodFit", "Spend", usd(ad["resiliency_investment"]["likely"]), usd(ad["resiliency_investment"]["likely"]), f"Not reduced: lower adoption means less protection, not a lower bill. Range {usd(ad['resiliency_investment']['low'])} to {usd(ad['resiliency_investment']['literal'])}."),
        ("Pending: machine-type estimates", "InfraFit", "PerfectScale estimate", usd(pb["estimate"]["full"]), usd(pb["estimate"]["adjusted"]), "PerfectScale's own estimate per pool; closest to a real saving."),
        ("Pending: idle ceiling", "InfraFit", "Ceiling", usd(pb["ceiling"]["full"]), usd(pb["ceiling"]["adjusted"]) + " or less", "Total idle cost of the pools. Assumes idle falls to zero, which no pool does, so the adjusted figure is still an upper bound."),
        ("Node idle", "InfraFit", "Current state", usd(ad["node_idle"]["current"]), usd(ad["node_idle"]["current"]), "Where the money sits today, not a saving. No adoption factor applies."),
    ]


def fit_text(S):
    fs = S["fit_split"]
    return (f"Origin by PerfectScale product: Achievable now is {pct(fs['achievable_now']['PodFit']['usd'], fs['achievable_now']['PodFit']['usd'] + fs['achievable_now']['InfraFit']['usd'], True)} PodFit; "
            f"Pending blockers is {pct(fs['blocked_pending']['InfraFit']['usd'], fs['blocked_pending']['PodFit']['usd'] + fs['blocked_pending']['InfraFit']['usd'], True)} InfraFit; "
            f"Resiliency spend is {pct(fs['resiliency_investment']['PodFit']['usd'], fs['resiliency_investment']['PodFit']['usd'] + fs['resiliency_investment']['InfraFit']['usd'], True)} PodFit; Node idle is 100% InfraFit. No block mixes the two.")


def scale_text(S):
    sc, I = S["scale_context"], S["idle"]
    return (f"For proportion: achievable savings are {sc['achievable_vs_node_cost'] * 100:.1f}% of {usd(I['node_cost'])}/mo node spend, while idle node cost is {sc['idle_vs_node_cost'] * 100:.0f}% of it. "
            f"Idle is about {sc['idle_per_achievable_dollar']:.0f}x the size of the achievable savings, but most of it cannot be claimed as a saving: {usd(I['auto_provisioned'])}/mo sits on short-lived auto-provisioned pools whose lifetime is minutes, "
            f"so the cause and the lever are not yet established (see 'What we know, what we think, and what we need from you'); it is not a per-workload right-sizing action.") if sc["idle_per_achievable_dollar"] else ""


def footnote_lines(S, pf="70%"):
    ad = S["adoption"]; pb = ad["blocked_pending"]
    a, es, ce = usd(ad["achievable_now"]["full"]), usd(pb["estimate"]["full"]), usd(pb["ceiling"]["full"])
    o = S["outlook"]
    return [("Low", f"(Achievable now {a} + Pending machine-type estimates {es}) x adoption rate {pf} = {usd(o['low']['usd'])}"),
            ("Planning", f"(Achievable now {a} + Pending machine-type estimates {es} + Pending idle ceiling {ce}) x adoption rate {pf} = {usd(o['planning']['usd'])}"),
            ("Maximum", f"Achievable now {a} + Pending machine-type estimates {es} + Pending idle ceiling {ce}, at 100% adoption = {usd(o['maximum']['usd'])}")]


def outlook_rows(S):
    o = S["outlook"]
    return [("Low", o["low"]["label"], usd(o["low"]["usd"])), ("Planning", o["planning"]["label"], usd(o["planning"]["usd"])), ("Maximum", o["maximum"]["label"], usd(o["maximum"]["usd"]))]


def idle_rows(S):
    rows = []
    for c in S["cluster_idle"]:
        rows.append((c["cluster"], f"{usd(c['idle'])} ({c['idle_pct'] * 100:.0f}%)", usd(c["auto_provisioned"]), f"{c['ap_inflated_share'] * 100:.0f}%", usd(c["long_lived"]),
                     f"{usd(c['gpu'])} ({c['gpu_share'] * 100:.0f}%)", f"{c['driver']} ({usd(c['driver_usd'])}, {c['driver_share'] * 100:.0f}%)"))
    return rows


GROUP_ORDER = [("Ownership", "who can act on it"), ("Intent", "whether it was deliberate"), ("Access and constraints", "what we need to be able to proceed")]
DECISIONS_TITLE = "Questions to confirm with the customer"
DECISIONS_LEAD = ("These are questions for the customer, not choices for us to make. The answers decide how much of the savings above is reachable and how quickly. "
                  "Within each group, questions that unlock claimed savings come first, ranked by dollars; questions about unclaimed idle follow. Dollar figures are at 100% adoption.")


BASIS_LABEL = {"measured": "Measured", "inferred": "Inferred", "unknown": "Not known"}


def evidence_rows(S):
    return [(BASIS_LABEL[x["basis"]], x["claim"], x["support"], x["check"]) for x in S["evidence"]]


def decision_groups(S):
    out = []
    for g, cap in GROUP_ORDER:
        ds = [d for d in S["decision_detail"] if d.get("group") == g]
        if ds:
            out.append((g, cap, [dl(d) for d in ds]))
    return out


def dl(d):
    tag = f" Unlocks about {usd(d['usd'])}/mo ({d['kind']}{'; ' + d['note'] if d['note'] else ''})." if d["usd"] else (f" ({d['note']})" if d["note"] else "")
    return d["text"] + tag


def decision_lines(S):
    out = []
    for d in S["decision_detail"]:
        tag = f" Unlocks about {usd(d['usd'])}/mo ({d['kind']}{'; ' + d['note'] if d['note'] else ''})." if d["usd"] else (f" ({d['note']})" if d["note"] else "")
        out.append(d["text"] + tag)
    return out


def exec_summary(cfg, S):
    t, o = S["totals"], S["observed"]
    L = []
    c = len(S["scope"]["cluster_names"])
    L.append(f"{cfg['customer']}'s {c} cluster{'s' if c > 1 else ''} in scope ({', '.join(S['scope']['cluster_names'])}) were analyzed two ways. "
             f"Looking at workloads (PodFit), **{mo_yr(t['achievable_now_monthly'])}** of savings can be captured now, and **{mo_yr(t['resiliency_investment_monthly'])}** of resiliency spend should be added to stop workloads failing. "
             f"Looking at nodes (InfraFit), **{usd(S['idle']['total'])}/mo** of capacity is idle today, a measurement rather than a saving, and **{mo_yr(t['blocked_pending_monthly'])}** of savings is waiting on specific blockers. "
             f"Savings and spend are kept apart; see 'How to read the four blocks'.")
    L.append(f"All figures assume every recommendation is adopted. At {S['adoption']['factor'] * 100:.0f}% adoption, achievable falls to about {usd(S['adoption']['achievable_now']['adjusted'])}/mo and pending blockers to about {usd(S['adoption']['blocked_pending']['adjusted'])}/mo, and the idle-ceiling part of that is still an upper bound. Resiliency spend is not reduced.")
    L.append(f"Of the achievable total, **{pct(S['automation_gated']['usd'], t['achievable_now_monthly'], True)}** needs PerfectScale automation switched on; the rest is configuration or manual work that does not.")
    if S["early"]:
        L.append(f"Costs reported by the API are actual spend over the connected window, not a monthly figure. Figures here are scaled to a 30-day run-rate "
                 f"({', '.join(f'{k} x{v:.2f}' for k, v in S['run_rate_factor'].items() if v > 1.001)}); observed (unscaled) totals: {usd(o['achievable_now'])} achievable, {usd(o['blocked_pending'])} pending.")
    return L


def phase_table(S, ph):
    rows = [a for a in S["actions"] if a["phase"] == ph]
    if not rows:
        return []
    out = [f"### {PHASE[ph]}", "", "| ID | Action | Fit | Cluster | Value | Lane | Effort | Confidence | Depends on |", "|---|---|---|---|---|---|---|---|---|"]
    for a in rows:
        note = unblock_note(a)
        title = f"{a['title']} · **{note}**" if note else a["title"]
        out.append(f"| {a['id']} | {title} | {a['fit']} | {a['cluster']} | {money(a)} | {LANE.get(a['lane'], a['lane'])} | {a['effort_tier']} | {a['confidence']} | {', '.join(a['depends_on']) or '-'} |")
    return out + [""]


def render_plan_md(cfg, S):
    d = date.fromisoformat(cfg["prepared"])
    L = [f"# {cfg['customer']} Kubernetes Optimization Plan", "", f"Prepared {long_date(d)} by {cfg['author']}. Version {cfg['version_tag']}.", "", "## Executive summary", ""]
    if cfg.get("status"):
        L[2:2] = [f"> **{cfg['status']}.** Figures and wording are open to change. See 'Feedback requested' at the end.", ""]
    for p in exec_summary(cfg, S):
        L += [p, ""]
    L += ["### How to read the four blocks", ""] + [f"{x}" + chr(10) for x in MONEY_BASES]
    pf, rows = adoption_rows(S)
    L += [f"### Adoption view: 100% versus {pf}", "", f"| Figure | Source | Kind | At 100% | At {pf} | Note |", "|---|---|---|---|---|---|"]
    L += [f"| {a} | {b} | {c} | {d} | {e_} | {n} |" for a, b, c, d, e_, n in rows] + [""]
    L += ["### Planning range", "", "| State | Built from | Monthly savings |", "|---|---|---|"] + [f"| {a} | {b} | {c} |" for a, b, c in outlook_rows(S)] + [""] + [f"*{k} = {v}.*  " for k, v in footnote_lines(S, pf)] + ["", S["outlook"]["note"], ""]
    if scale_text(S):
        L += ["### Putting the numbers in proportion", "", scale_text(S), ""]
    cf = S["confidence"]
    L += ["### What we know, what we think, and what we need from you", "", cf["takeaway"], ""]
    for title, key in (("What we know", "know"), ("What we think", "think"), ("What we need from you", "need")):
        if cf[key]:
            L += [f"**{title}**", ""] + [f"- {x}" for x in cf[key]] + [""]
    if cf.get("grouping"):
        L += ["**How grouping connects to node sizing**", "", cf["grouping"], "", "Sources (PerfectScale public documentation): " + "; ".join(f"[{n}]({u})" for n, u in GROUPING_SOURCES) + ".", ""]
    L += ["### Why each cluster's idle is what it is", "", "| Cluster | Idle | Auto-provisioned idle | Of which on shapes where CPU requests are 5x or more of use | Long-lived pool idle | GPU pool idle | Largest driver |", "|---|---|---|---|---|---|---|"]
    L += ["| " + " | ".join(row) + " |" for row in idle_rows(S)] + [""]
    L += ["### The four blocks", "", "| Block | Monthly | Comes from | What it is | How it relates |", "|---|---|---|---|---|",
          f"| Achievable now | {usd(S['totals']['achievable_now_monthly'])} | PodFit (workloads) | A saving: PerfectScale-computed reduction on workload requests, reachable by a toggle, a config change or manual right-sizing | Separate from node idle |",
          f"| Resiliency spend | {usd(S['totals']['resiliency_investment_monthly'])} | PodFit (workloads) | Money to add to fix under-provisioned workloads (likely case; range {usd(S['totals']['resiliency_range'].get('low', 0))} to {usd(S['totals']['resiliency_range'].get('literal', 0))}) | Opposite sign; never netted against savings |",
          f"| Pending blockers | {usd(S['totals']['blocked_pending_monthly'])} | InfraFit (nodes) | A saving that waits on a blocker: PerfectScale machine-type estimates plus an idle ceiling on long-lived pools | The idle-ceiling part is a slice of node idle |",
          f"| Node idle | {usd(S['idle']['total'])} | InfraFit (nodes) | A measurement of today: node capacity paid for and not used | The starting point; not a saving |", ""]
    lanes = S["achievable_by_lane"]
    if lanes:
        L += ["Achievable savings by how they are reached: " + "; ".join(f"{LANE.get(k, k).lower()} {usd(v)}" for k, v in sorted(lanes.items(), key=lambda x: -x[1]) if v) + ".", ""]
    I = S["idle"]
    L += [f"Node idle cost in scope is {usd(I['total'])}/mo of {usd(I['node_cost'])}/mo node spend. {usd(I['claimed_long_lived'])} is claimed by actions above, "
          f"{usd(I['auto_provisioned'])} sits on short-lived auto-provisioned pools (deliberately not claimed), and {usd(I['unclaimed_other'])} is unclaimed.", ""]
    L += ["### First moves", ""]
    by = {a["id"]: a for a in S["actions"]}
    for i in S["first_moves"]:
        a = by[i]
        note = unblock_note(a)
        value = f"{money(a)}; {note}" if note else money(a)
        L.append(f"- **{a['id']} - {a['title']}** ({value}; {a['effort_tier']}, {a['confidence']} confidence). {a['evidence'].split('. ')[0].rstrip('.')}.")
    L += ["", "## The plan", ""]
    for ph in (1, 2, 3):
        L += phase_table(S, ph)
    L += ["## Action detail", ""]
    for a in S["actions"]:
        note = unblock_note(a)
        L += [f"### {a['id']} - {a['title']}", "",
              f"- **Cluster:** {a['cluster']}  |  **Scope:** {a['scope']}",
              f"- **Value:** {money(a)}" + (f" · {note}" if note else "") + f" ({BASIS[a['savings_basis']]})" + (f"; observed unscaled {usd(a['obs'])}" if a.get("obs") and S["early"] and abs(a["obs"] - a["savings_monthly_usd"]) > 1 else ""),
              f"- **Effort:** {a['effort_rationale']}  |  **Risk:** {risk_text(a['change_risk'])}  |  **Confidence:** {a['confidence']}  |  **Owner:** {a['owner_role']}" + (f" ({', '.join(a['owner_hint'])})" if a["owner_hint"] else ""),
              f"- **Lane:** {LANE.get(a['lane'])}" + ("  |  **Gated on automation**" if a["gated_on_automation"] else "") + (f"  |  **Depends on:** {', '.join(a['depends_on'])}" if a["depends_on"] else "") + (f"  |  **Unblocks:** {', '.join(f'{d} ({money(by[d])})' if by[d]['savings_monthly_usd'] else d for d in a['unblocks'])}" if a["unblocks"] else ""),
              "", a["evidence"], ""]
        for i, s in enumerate(a["steps"], 1):
            L.append(f"{i}. {s}")
        L.append("")
    L += ["## Appendix: evidence detail", "", "The statements above, with their basis and how to check each one in PerfectScale.", "", "| Status | Statement | Basis | How to check |", "|---|---|---|---|"] + [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in evidence_rows(S)] + [""]
    L += (["## Feedback requested", ""] + [f"{i}. {x}" for i, x in enumerate(FEEDBACK_PROMPTS, 1)] + [""]) if cfg.get("status") else []
    L += [f"## {DECISIONS_TITLE}", "", DECISIONS_LEAD, ""]
    for g, cap, lines in decision_groups(S):
        L += [f"### {g}: {cap}", ""] + [f"{n}. {x}" for n, x in enumerate(lines, 1)] + [""]
    L += ["", "## How these numbers were built", "",
          f"- Source: PerfectScale public API, {S['window_days']:.0f} days observed on the longest-running cluster.",
          "- Reported costs are actual spend over the connected window. Clusters with under 14 days of history are scaled by 30 / window days; full windows are never scaled.",
          "- Savings and node idle come from different measurements and are never added together.",
          "- ROI = savings x confidence / effort weight (XS 1, S 2, M 4, L 8). Prerequisites that release more value than their own are moved one phase earlier.",
          f"- Regenerate on {S['rerun_date']} and diff against this plan.", ""]
    return "\n".join(L)


def render_cluster_md(cfg, S, f):
    d = date.fromisoformat(cfg["prepared"])
    P = f["pools"]
    L = [f"# {cfg['customer']} - {f['name']} findings", "", f"Prepared {long_date(d)}. {f['window_days']:.0f} days observed" + (f", scaled x{f['factor']:.2f} to a 30-day run-rate." if f["factor"] > 1.001 else "; no scaling applied."), ""]
    if f["early"]:
        L += ["> Early baseline: under 14 days of history. Treat run-rate figures as indicative and re-check after a full month.", ""]
    L += ["## At a glance", "", "| Measure | Value |", "|---|---|",
          f"| Workloads | {f['n_workloads']:,} |", f"| Pod cost | {usd(f['cost'])}/mo |", f"| Pod waste | {usd(f['waste'])}/mo ({pct(f['waste'], f['cost'])}) |",
          f"| Potential savings | {usd(f['pot'])}/mo |", f"| Node cost | {usd(P['cost'])}/mo |", f"| Node idle | {usd(P['idle'])}/mo ({P['idle_pct'] * 100:.0f}%) |",
          f"| Automation actions on record | {f['audit']['n']} |", ""]
    L += ["## Potential savings by lane", "", "| Lane | Workloads | Waste | Potential savings |", "|---|---|---|---|"]
    for k in ("controller", "job", "stateful", "held_out", "managed"):
        x = f["lanes"][k]
        L.append(f"| {k.replace('_', ' ')} | {x['n']:,} | {usd(x['waste'])} | {usd(x['pot'])} |")
    L += ["", "## Largest opportunities", "", "| Workload | Namespace | Lane | CPU now to rec | Waste | Potential |", "|---|---|---|---|---|---|"]
    for r in f["top"][:12]:
        L.append(f"| {short_name(r['name'])} | {r['ns']} | {r['lane']} | {r['cpu_cur']:.1f} to {r['cpu_rec']:.1f} | {usd(r['waste'])} | {usd(r['pot'])} |")
    L += ["", "## Node pools", "", "| Pool | Kind | Machine | Nodes min/avg/max | Idle | Cost | Recommendation |", "|---|---|---|---|---|---|---|"]
    for p in sorted(P["long_lived"], key=lambda p: -p["idle"])[:10]:
        rec = f"{p['rec']['to']} ({usd(p['rec']['save'])}/mo)" if p["rec"] else "-"
        L.append(f"| `{p['id']}` | long-lived | {p['machine']} | {p['nodes_min']:.0f}/{p['nodes_avg']:.0f}/{p['nodes_max']:.0f} | {usd(p['idle'])} | {usd(p['cost'])} | {rec} |")
    if P["ap_n"]:
        L.append(f"| {P['ap_n']:,} auto-provisioned | short-lived | various | - | {usd(P['ap_idle'])} | {usd(P['ap_cost'])} | median {P['ap_median_wall_min']:.0f} min wall-clock |")
    U = f["unevictable"]
    L += ["", "## What blocks node scale-down", "", f"{U['blocking']:,} pods block eviction" + (" (partial list: cluster-wide pull fell back to top groups)." if U["partial"] else "."), ""]
    if U["codes"]:
        L += ["| Reason | Pods | Outside platform namespaces | Typical fix |", "|---|---|---|---|"]
        for k, c in sorted(U["codes"].items(), key=lambda kv: -kv[1]["all"]):
            L.append(f"| {c['label']} | {c['all']:,} | {c['nm']:,} | {c['fix']} |")
    L += ["", "## Policies", "", ", ".join(f"{k}: {v:,}" for k, v in sorted(f["policies"].items(), key=lambda kv: -kv[1])), ""]
    if f["flags"]:
        L += ["## Data flags", ""] + [f"- {x}" for x in f["flags"]] + [""]
    return "\n".join(L)


# ---------------------------------------------------------------- HTML
CSS = """:root{--bg:#fff;--fg:#1a1d23;--mut:#5b6472;--line:#e3e6eb;--acc:#2457d6;--card:#f6f8fb}
@media(prefers-color-scheme:dark){:root{--bg:#14171c;--fg:#e8ebf0;--mut:#9aa3b0;--line:#2a2f38;--acc:#7da2ff;--card:#1c2027}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}main{max-width:1100px;margin:0 auto;padding:24px 16px}
h1{font-size:26px}h2{margin-top:36px;border-bottom:1px solid var(--line);padding-bottom:6px}.mut{color:var(--mut)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.card b{display:block;font-size:24px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
a{color:var(--acc)}th{cursor:pointer;position:relative}th:hover{color:var(--acc)}.wrap{overflow-x:auto}details{border:1px solid var(--line);border-radius:8px;margin:8px 0;padding:6px 12px}
/* headers wrap like cells: a narrowed column must not spill over its neighbour */
th,td{overflow-wrap:break-word;word-break:normal;white-space:normal}
th .grip{position:absolute;top:0;right:-4px;width:9px;height:100%;cursor:col-resize;user-select:none;z-index:2;touch-action:none}
th .grip:hover::after,th .grip.on::after{content:"";position:absolute;top:3px;bottom:3px;left:4px;width:2px;background:var(--acc);border-radius:1px}
th:last-child .grip{display:none}
table.rsz{user-select:none}
.hidecol{display:none}
.tbar{display:flex;align-items:center;gap:10px;margin:10px 0 6px;font-size:12px;color:var(--mut);position:relative}
.tbar .cfg{font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:6px;padding:3px 9px;cursor:pointer}
.tbar .cfg:hover,.tbar .cfg[aria-expanded=true]{border-color:var(--acc);color:var(--acc)}
.tbar .lnk{font:inherit;color:var(--acc);background:none;border:0;padding:0;cursor:pointer;text-decoration:underline}
.tip{position:absolute;left:0;top:100%;margin-top:4px;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:5px 9px;font-size:12px;color:var(--fg);white-space:nowrap;z-index:20;box-shadow:0 2px 8px rgba(0,0,0,.18)}
.menu{position:absolute;left:0;top:100%;margin-top:4px;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px;z-index:30;min-width:210px;max-height:60vh;overflow:auto;box-shadow:0 4px 14px rgba(0,0,0,.22)}
.menu .ttl{font-weight:600;color:var(--fg);margin:0 0 6px}
.menu label{display:flex;align-items:center;gap:7px;padding:3px 2px;color:var(--fg);cursor:pointer;font-size:13px}
.menu label:hover{color:var(--acc)}.menu input{margin:0}
.menu .acts{display:flex;gap:8px;margin-top:8px;padding-top:8px;border-top:1px solid var(--line)}
.menu .acts button{font:inherit;font-size:12px;color:var(--fg);background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:3px 8px;cursor:pointer}
.menu .acts button:hover{border-color:var(--acc);color:var(--acc)}
summary{cursor:pointer;font-weight:600}.tag{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:0 8px;font-size:12px;margin-right:4px}
.trio{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}.trio .card .h{display:block;font-size:16px;margin-bottom:6px}.trio ul{margin:0;padding-left:18px;font-size:14px}.trio li{margin:4px 0}.fn{font-size:12px;line-height:1.5;color:var(--mut);margin:6px 0 10px}.ctl{position:sticky;top:0;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:8px 0 14px;z-index:5}.ctl input[type=number]{width:60px}
.card .sub,.card .adj,.card .tagline{display:block;margin-top:4px}.card .adj{margin-top:6px}.card .sub,.card .tagline,.card .adj{font-size:14px}.card .adj b{display:inline;font-size:inherit;font-weight:inherit}.card .adj{font-variant-numeric:tabular-nums}.tagline .tag{border:0;padding:0;margin:0;border-radius:0;color:var(--mut)}.num{font-variant-numeric:tabular-nums;text-align:left;font-weight:400;font-size:14px}
svg text{fill:var(--fg);font-size:12px}svg rect{fill:var(--card);stroke:var(--line)}svg path{stroke:var(--mut);fill:none}"""
JS = """document.querySelectorAll('table.sort').forEach(function(t){t.querySelectorAll('th').forEach(function(th,i){th.addEventListener('click',function(){
if(t.dataset.dragged==='1'){t.dataset.dragged='';return}
var b=t.tBodies[0],r=Array.prototype.slice.call(b.rows),d=th.dataset.d==='1'?-1:1;th.dataset.d=d===1?'1':'0';
r.sort(function(x,y){var a=x.cells[i].dataset.v||x.cells[i].textContent,c=y.cells[i].dataset.v||y.cells[i].textContent,na=parseFloat(a),nc=parseFloat(c);
return(isNaN(na)||isNaN(nc)?a.localeCompare(c):na-nc)*d});r.forEach(function(x){b.appendChild(x)})})})})"""


# Drag-to-resize columns. The table keeps its overall width: every pixel a column
# gains is taken from its right-hand neighbour, so nothing reflows sideways and no
# horizontal scrollbar appears. Widths persist per table in localStorage.
JS_RESIZE = """(function(){var MIN=44;
function K(t,n){return 'psplan.cols.'+(t.dataset.rsz||'t')+'.'+n}
document.querySelectorAll('table').forEach(function(t,ti){
 var head=t.tHead&&t.tHead.rows[0];if(!head)return;
 var n=head.cells.length;if(n<3||!t.tBodies[0]||!t.tBodies[0].rows.length)return;
 t.dataset.rsz='t'+ti;
 var labels=Array.prototype.map.call(head.cells,function(c){return c.textContent.trim()||('Column '+(c.cellIndex+1))});
 function measure(){return Array.prototype.map.call(head.cells,function(c){return c.getBoundingClientRect().width})}

 // Measure under the browser's own auto layout before freezing anything:
 // switching to fixed first would measure a collapsed table and bake in
 // equal, useless columns.
 var base=measure();
 var box=(t.parentNode&&t.parentNode.clientWidth)||0;
 var sum=base.reduce(function(a,b){return a+b},0);
 if(box>sum+2){var longest=0,best=0;
  for(var c=0;c<n;c++){var mx=0;
   Array.prototype.forEach.call(t.tBodies[0].rows,function(r){var cell=r.cells[c];
    if(cell&&cell.textContent.length>mx)mx=cell.textContent.length});
   if(mx>longest){longest=mx;best=c}}
  base[best]+=box-sum;sum=box}
 var TOTAL=sum;

 var cg=document.createElement('colgroup');
 for(var i=0;i<n;i++)cg.appendChild(document.createElement('col'));
 t.insertBefore(cg,t.firstChild);t.classList.add('rsz');t.style.tableLayout='fixed';
 var cols=cg.children;

 var w=base.slice(),hid={},stash={};
 var saved=null;try{var sv=localStorage.getItem(K(t,n));if(sv)saved=JSON.parse(sv)}catch(e){}
 if(saved&&saved.w&&saved.w.length===n){w=saved.w.slice();hid=saved.hid||{};stash=saved.stash||{}}

 function vis(){var a=[];for(var i=0;i<n;i++)if(!hid[i])a.push(i);return a}
 function save(){try{localStorage.setItem(K(t,n),JSON.stringify({w:w.map(Math.round),hid:hid,stash:stash}))}catch(e){}}
 function paint(){
  var v=vis();
  for(var i=0;i<n;i++){
   var off=!!hid[i];
   cols[i].style.display=off?'none':'';
   head.cells[i].classList.toggle('hidecol',off);
   Array.prototype.forEach.call(t.tBodies[0].rows,function(r){
    if(r.cells[i])r.cells[i].classList.toggle('hidecol',off)});
   if(!off)cols[i].style.width=w[i]+'px'}
  t.style.width=v.reduce(function(a,i){return a+w[i]},0)+'px'}
 function normalise(){ // visible widths always sum to TOTAL, so the table never changes size
  var v=vis();if(!v.length)return;
  var s=v.reduce(function(a,i){return a+w[i]},0)||1,k=TOTAL/s;
  v.forEach(function(i){w[i]=Math.max(MIN,w[i]*k)})}

 function setVisible(i,on){
  if(on){ if(!hid[i])return; delete hid[i];
   var want=stash[i]||base[i]||120,v=vis().filter(function(j){return j!==i});
   var room=v.reduce(function(a,j){return a+w[j]-MIN},0);
   want=Math.max(MIN,Math.min(want,room));
   var s=v.reduce(function(a,j){return a+w[j]},0)||1;
   v.forEach(function(j){w[j]=Math.max(MIN,w[j]-want*(w[j]/s))});
   w[i]=want;
  } else { if(hid[i])return; if(vis().length<=1)return;
   stash[i]=w[i];hid[i]=1; }
  normalise();paint();save()}

 function autofit(){ // let the engine size the visible columns to their content, then refreeze
  var prevW=t.style.width,prevL=t.style.tableLayout;
  for(var i=0;i<n;i++)cols[i].style.width='';
  t.style.tableLayout='auto';t.style.width='';
  var m=measure(),v=vis();
  var s=v.reduce(function(a,i){return a+m[i]},0)||1,k=TOTAL/s;
  v.forEach(function(i){w[i]=Math.max(MIN,m[i]*k)});
  t.style.tableLayout='fixed';t.style.width=prevW;
  normalise();paint();save()}

 function reset(){w=base.slice();hid={};stash={};paint();
  try{localStorage.removeItem(K(t,n))}catch(e){}
  Array.prototype.forEach.call(menu.querySelectorAll('input'),function(cb,i){cb.checked=true})}

 normalise();paint();

 // --- drag to resize -------------------------------------------------
 Array.prototype.forEach.call(head.cells,function(th,i){
  var g=document.createElement('span');g.className='grip';
  g.title='Drag to resize this column. Double-click to reset the table.';
  th.appendChild(g);
  g.addEventListener('click',function(e){e.stopPropagation()});
  g.addEventListener('dblclick',function(e){e.stopPropagation();e.preventDefault();reset()});
  g.addEventListener('pointerdown',function(e){
   var v=vis(),at=v.indexOf(i);if(at<0||at===v.length-1)return;
   e.preventDefault();e.stopPropagation();
   var right=v.slice(at+1),x0=e.clientX,a0=w[i],start=w.slice();
   g.classList.add('on');try{g.setPointerCapture(e.pointerId)}catch(err){}
   function mv(ev){
    // borrow from the visible columns to the right, nearest first, each down to
    // MIN; the pair total is invariant so the table width never moves
    var room=right.reduce(function(a,j){return a+start[j]-MIN},0);
    var d=Math.max(MIN-a0,Math.min(ev.clientX-x0,room));
    w[i]=a0+d;var need=d;
    for(var q=0;q<right.length;q++){var j=right[q],give;
     if(need>0)give=Math.min(need,start[j]-MIN);else give=need;
     w[j]=start[j]-give;need-=give;if(need===0&&d>0)break}
    if(need!==0&&d<0)w[right[0]]=start[right[0]]-need;
    paint();t.dataset.dragged='1'}
   function up(){g.classList.remove('on');
    try{g.releasePointerCapture(e.pointerId)}catch(err){}
    g.removeEventListener('pointermove',mv);g.removeEventListener('pointerup',up);
    save();setTimeout(function(){t.dataset.dragged=''},0)}
   g.addEventListener('pointermove',mv);g.addEventListener('pointerup',up)})});

 // --- column picker ---------------------------------------------------
 var bar=document.createElement('div');bar.className='tbar';
 var btn=document.createElement('button');btn.type='button';btn.className='cfg';
 btn.setAttribute('aria-expanded','false');btn.setAttribute('aria-haspopup','true');
 btn.textContent='Columns';
 var TIP='Choose which columns to show, autofit them, or reset the table';
 btn.title=TIP;
 var hint=document.createElement('span');hint.textContent='Drag a column edge to resize';
 bar.appendChild(btn);bar.appendChild(hint);

 var tip=null;
 btn.addEventListener('pointerenter',function(){
  if(tip||btn.getAttribute('aria-expanded')==='true')return;
  tip=document.createElement('span');tip.className='tip';tip.textContent=TIP;bar.appendChild(tip)});
 function dropTip(){if(tip){tip.remove();tip=null}}
 btn.addEventListener('pointerleave',dropTip);

 var menu=document.createElement('div');menu.className='menu';menu.hidden=true;
 var ttl=document.createElement('p');ttl.className='ttl';ttl.textContent='Show columns';menu.appendChild(ttl);
 labels.forEach(function(lab,i){
  var l=document.createElement('label'),cb=document.createElement('input');
  cb.type='checkbox';cb.checked=!hid[i];
  cb.addEventListener('change',function(){
   setVisible(i,cb.checked);
   if(!cb.checked&&!hid[i])cb.checked=true});   // refused: last visible column
  l.appendChild(cb);l.appendChild(document.createTextNode(lab));menu.appendChild(l)});
 var acts=document.createElement('div');acts.className='acts';
 var fit=document.createElement('button');fit.type='button';fit.textContent='Autofit selected';
 fit.title='Size the shown columns to their content, keeping the table the same width';
 var rst=document.createElement('button');rst.type='button';rst.textContent='Reset to default';
 rst.title='Restore every column and its original width';
 fit.addEventListener('click',autofit);rst.addEventListener('click',reset);
 acts.appendChild(fit);acts.appendChild(rst);menu.appendChild(acts);
 bar.appendChild(menu);

 btn.addEventListener('click',function(e){e.stopPropagation();dropTip();
  var open=menu.hidden;menu.hidden=!open;btn.setAttribute('aria-expanded',open?'true':'false')});
 document.addEventListener('click',function(e){
  if(!menu.hidden&&!bar.contains(e.target)){menu.hidden=true;btn.setAttribute('aria-expanded','false')}});
 document.addEventListener('keydown',function(e){
  if(e.key==='Escape'&&!menu.hidden){menu.hidden=true;btn.setAttribute('aria-expanded','false');btn.focus()}});

 // above the table: the control is only useful if it is seen before the table is read
 (t.parentNode.classList.contains('wrap')?t.parentNode:t).insertAdjacentElement('beforebegin',bar);
});})();"""


JS_CALC = """(function(){var D=JSON.parse(document.getElementById('plan').textContent);
function m(v){return '$'+Math.round(v).toLocaleString('en-US')}
function set(k,t){document.querySelectorAll('[data-calc="'+k+'"]').forEach(function(n){n.textContent=t})}
function run(p){p=Math.max(0,Math.min(100,Number(p)||0));var f=p/100;
set('pct',p+'%');set('ach_adj',m(D.ach*f));set('est_adj',m(D.est*f));set('ceil_adj',m(D.ceil*f));set('pend_adj',m((D.est+D.ceil)*f));
set('low',m((D.ach+D.est)*f));set('plan',m((D.ach+D.est+D.ceil)*f));
document.querySelectorAll('[data-calc-row]').forEach(function(n){if(n.dataset.spend==='1')return;var v=Number(n.dataset.calcRow);n.textContent=v?m(v*f)+'/mo':'-'});
var a=document.getElementById('adopt'),b=document.getElementById('adoptr');if(a&&a.value!=p)a.value=p;if(b&&b.value!=p)b.value=p}
['adopt','adoptr'].forEach(function(id){var n=document.getElementById(id);if(n)n.addEventListener('input',function(){run(n.value)})});
var s=document.getElementById('adopt');if(s)run(s.value)})();"""


def e(x):
    return html.escape(str(x))


def dep_svg(S):
    acts = S["actions"]
    known = {a["id"] for a in acts}
    # A dangling depends_on (pruned action, cross-cluster id, upstream typo) must not
    # take down the whole report -- drop just that edge from the diagram.
    edges = [(d, a["id"]) for a in acts for d in a["depends_on"] if d in known]
    if not edges:
        return ""
    ids = sorted({x for ed in edges for x in ed}, key=lambda i: next(k for k, a in enumerate(acts) if a["id"] == i))
    by = {a["id"]: a for a in acts}
    col = {1: 0, 2: 1, 3: 2}
    rows = {1: 0, 2: 0, 3: 0}
    pos = {}
    for i in ids:
        ph = by[i]["phase"]
        pos[i] = (20 + col[ph] * 300, 30 + rows[ph] * 44)
        rows[ph] += 1
    h = 40 + max(rows.values()) * 44
    out = [f'<svg viewBox="0 0 900 {h}" role="img" aria-label="Action dependencies" style="width:100%;height:auto">']
    for ph, c in col.items():
        out.append(f'<text x="{20 + c * 300}" y="16" font-weight="bold">Phase {ph}</text>')
    for a, b in edges:
        x1, y1 = pos[a][0] + 260, pos[a][1] + 14
        x2, y2 = pos[b][0], pos[b][1] + 14
        if x2 <= x1:
            x1, x2 = pos[a][0] + 130, pos[b][0] + 130
            y1, y2 = pos[a][1] + 28, pos[b][1]
        out.append(f'<path d="M{x1} {y1} C{x1 + 30} {y1},{x2 - 30} {y2},{x2} {y2}"/>')
    for i in ids:
        x, y = pos[i]
        t = by[i]["title"]
        t = (t[:34] + "...") if len(t) > 36 else t
        out.append(f'<rect x="{x}" y="{y}" width="260" height="28" rx="6"/><text x="{x + 8}" y="{y + 18}">{e(i)} {e(t)}</text>')
    out.append("</svg>")
    return "".join(out)


def _fn_html(S, C, pf):
    ad = S["adoption"]; pb = ad["blocked_pending"]; o = S["outlook"]
    a, es, ce = e(usd(ad["achievable_now"]["full"])), e(usd(pb["estimate"]["full"])), e(usd(pb["ceiling"]["full"]))
    return ("<div class=fn><b>How each line is calculated</b><br>"
            f"Low = (Achievable now {a} + Pending machine-type estimates {es}) &times; adoption rate {C('pct', pf)} = {C('low', usd(o['low']['usd']))}<br>"
            f"Planning = (Achievable now {a} + Pending machine-type estimates {es} + Pending idle ceiling {ce}) &times; adoption rate {C('pct', pf)} = {C('plan', usd(o['planning']['usd']))}<br>"
            f"Maximum = Achievable now {a} + Pending machine-type estimates {es} + Pending idle ceiling {ce}, at 100% adoption = {e(usd(o['maximum']['usd']))}</div>")


def render_html(cfg, S, facts):
    t = S["totals"]
    _pb = S["adoption"]["blocked_pending"]
    CALC = {"ach": S["adoption"]["achievable_now"]["full"], "est": _pb["estimate"]["full"], "ceil": _pb["ceiling"]["full"]}
    P = [f"<main><h1>{e(cfg['customer'])} Kubernetes Optimization Plan</h1><p class=mut>Prepared {e(long_date(date.fromisoformat(cfg['prepared'])))} by {e(cfg['author'])}. Version {e(cfg['version_tag'])}.</p>"]
    if cfg.get("status"):
        P.append(f"<div class=ctl style='position:static'><b>{e(cfg['status'])}.</b> Figures and wording are open to change. See Feedback requested at the end.</div>")
    ad = S["adoption"]; pf = f"{ad['factor'] * 100:.0f}%"
    pb = ad["blocked_pending"]

    def C(key, text):
        return f"<span data-calc='{key}'>{e(text)}</span>"
    P.append(f"<div class=ctl><label for=adopt><b>Adoption rate</b></label> <input id=adopt type=number min=0 max=100 step=5 value={ad['factor'] * 100:.0f}>% <input id=adoptr type=range min=0 max=100 step=5 value={ad['factor'] * 100:.0f} aria-label='Adoption rate'>"
             f" <span class=mut>Share of recommendations realistically adopted. Savings figures marked with it recalculate; resiliency spend and node idle do not change. Everything else in the report is shown at 100%.</span></div>")

    def card(lbl, val, sub, fit, adj):
        return (f"<div class=card><span class=mut>{e(lbl)}</span><b>{e(val)}</b><span class=sub>{e(sub)}</span>"
                f"<span class=tagline><span class=tag>Source: {e(fit)}</span></span><span class='adj'>{adj}</span></div>")
    P.append("<div class=cards>" + "".join(card(*x) for x in (
        ("Achievable now", usd(t["achievable_now_monthly"]) + "/mo", f"{pct(S['automation_gated']['usd'], t['achievable_now_monthly'], True)} needs automation on", "PodFit", f"At {C('pct', pf)} adoption: <b>{C('ach_adj', usd(ad['achievable_now']['adjusted']))}</b>/mo"),
        ("Resiliency spend", usd(t["resiliency_investment_monthly"]) + "/mo", f"range {usd(t['resiliency_range'].get('low', 0))} to {usd(t['resiliency_range'].get('literal', 0))}", "PodFit", "Spend: not reduced by adoption"),
        ("Pending blockers", usd(t["blocked_pending_monthly"]) + "/mo", "idle ceiling and estimates", "InfraFit", f"At {C('pct', pf)} adoption: <b>{C('pend_adj', usd(pb['adjusted']))}</b>/mo or less"),
        ("Node idle", usd(S["idle"]["total"]) + "/mo", f"of {usd(S['idle']['node_cost'])} node spend", "InfraFit", "Current state, not a saving"))) + "</div>")
    pf2, rows = adoption_rows(S)
    P.append("<h2>How to read the four blocks</h2>" + "".join(f"<p>{e(x)}</p>" for x in MONEY_BASES))
    calc_keys = {"Achievable now": "ach_adj", "Pending: machine-type estimates": "est_adj", "Pending: idle ceiling": "ceil_adj"}
    body = ""
    for a, b, c, d, e_, n in rows:
        k = calc_keys.get(a)
        cell = f"{C(k, e_.replace(' or less', ''))}{' or less' if 'or less' in e_ else ''}" if k else e(e_)
        body += f"<tr><td>{e(a)}</td><td>{e(b)}</td><td>{e(c)}</td><td class=num>{e(d)}</td><td class=num>{cell}</td><td>{e(n)}</td></tr>"
    P.append(f"<h2>Adoption view: 100% versus {C('pct', pf2)}</h2><p class=mut>100% assumes every recommendation is applied. The adoption column applies the rate above to savings only.</p>"
             f"<div class=wrap><table><thead><tr><th>Figure</th><th>Source</th><th>Kind</th><th>At 100%</th><th>At {C('pct', pf2)}</th><th>Note</th></tr></thead><tbody>{body}</tbody></table></div>")
    P.append("<h2>Summary</h2>" + "".join(f"<p>{e(p).replace('**', '')}</p>" for p in exec_summary(cfg, S) if not p.startswith("All figures assume")) +
             f"<p>All figures assume every recommendation is adopted. At {C('pct', pf)} adoption, achievable falls to about {C('ach_adj', usd(ad['achievable_now']['adjusted']))}/mo and pending blockers to about {C('pend_adj', usd(pb['adjusted']))}/mo, and the idle-ceiling part of that is still an upper bound. Resiliency spend is not reduced.</p>")
    P.append("<h2>Planning range</h2><div class=wrap><table><thead><tr><th>State</th><th>Built from</th><th>Monthly savings</th></tr></thead><tbody>" + (f"<tr><td>Low</td><td>Computed and estimated savings only, at {C('pct', pf)} adoption</td><td class=num>{C('low', usd(S['outlook']['low']['usd']))}</td></tr>"
             f"<tr><td>Planning</td><td>Everything, including the idle ceiling, at {C('pct', pf)} adoption</td><td class=num>{C('plan', usd(S['outlook']['planning']['usd']))}</td></tr>"
             f"<tr><td>Maximum</td><td>Everything at 100% adoption, with every idle dollar reclaimed</td><td class=num>{e(usd(S['outlook']['maximum']['usd']))}</td></tr>") + f"</tbody></table></div>" + _fn_html(S, C, pf) + f"<p class=mut>{e(S['outlook']['note'])}</p>")
    if scale_text(S):
        P.append("<h2>The numbers in proportion</h2><p>" + e(scale_text(S)) + "</p>")
    cf = S["confidence"]
    P.append("<h2>What we know, what we think, and what we need from you</h2><p>" + e(cf["takeaway"]) + "</p><div class=trio>" + "".join(
        f"<div class=card><b class=h>{t}</b><ul>" + "".join(f"<li>{e(x)}</li>" for x in cf[k]) + "</ul></div>" for t, k in (("What we know", "know"), ("What we think", "think"), ("What we need from you", "need")) if cf[k]) + "</div>")
    if cf.get("grouping"):
        P.append("<h3>How grouping connects to node sizing</h3><p>" + e(cf["grouping"]) + "</p><p class=fn>Sources (PerfectScale public documentation): " + "; ".join(f'<a href="{u}">{e(n)}</a>' for n, u in GROUPING_SOURCES) + ".</p>")
    P.append("<h2>Why each cluster's idle is what it is</h2><div class=wrap><table><thead><tr><th>Cluster</th><th>Idle</th><th>Auto-provisioned idle</th><th>On shapes where CPU requests are 5x or more of use</th><th>Long-lived pool idle</th><th>GPU pool idle</th><th>Largest driver</th></tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{e(x)}</td>" for x in row) + "</tr>" for row in idle_rows(S)) + "</tbody></table></div>")
    P.append("<h2>Actions</h2><div class=wrap><table class=sort><thead><tr>" + "".join(f"<th>{h}</th>" for h in ("ID", "Phase", "Action", "Fit", "Cluster", "Value / mo", "Basis", "Lane", "Effort", "Confidence", "ROI")) + "<th>At <span data-calc='pct'>" + pf + "</span></th></tr></thead><tbody>")
    for a in S["actions"]:
        v = a["savings_monthly_usd"] or 0
        note = unblock_note(a)
        title_cell = f"<a href='#{a['id']}'>{e(a['title'])}</a>" + (f" <b>{e(note)}</b>" if note else "")
        P.append(f"<tr><td data-v='{a['id'][1:]}'>{e(a['id'])}</td><td>{a['phase']}</td><td>{title_cell}</td><td>{e(a['fit'])}</td><td>{e(a['cluster'])}</td><td class=num data-v='{v:.0f}'>{e(money(a))}</td>"
                 f"<td>{e(BASIS[a['savings_basis']])}</td><td>{e(LANE.get(a['lane']))}</td><td>{a['effort_tier']}</td><td>{a['confidence']}</td><td>{a['roi_score'] if a['roi_score'] is not None else '-'}</td><td class=num data-calc-row='{0 if a['savings_basis'] == 'resiliency_investment' else v:.0f}' data-spend='{1 if a['savings_basis'] == 'resiliency_investment' else 0}'>{e(money(a)) if a['savings_basis'] == 'resiliency_investment' else (e(usd(v * ad['factor'])) + '/mo' if v else '-')}</td></tr>")
    P.append("</tbody></table></div>")
    svg = dep_svg(S)
    if svg:
        P.append("<h2>Dependencies</h2>" + svg)
    P.append("<h2>Action detail</h2>")
    for a in S["actions"]:
        note = unblock_note(a)
        value = f"{money(a)}, {note}" if note else money(a)
        P.append(f"<details id='{a['id']}'><summary>{e(a['id'])} - {e(a['title'])} <span class=mut>({e(value)})</span></summary>"
                 f"<p><span class=tag>{e(LANE.get(a['lane']))}</span><span class=tag>effort {a['effort_tier']}</span><span class=tag>risk {e(risk_text(a['change_risk']))}</span><span class=tag>{e(a['confidence'])} confidence</span></p>"
                 f"<p><b>Scope:</b> {e(a['scope'])}</p><p>{e(a['evidence'])}</p><p class=mut>{e(a['effort_rationale'])}</p><ol>" + "".join(f"<li>{e(s)}</li>" for s in a["steps"]) + "</ol></details>")
    P.append("<h2>Clusters</h2><div class=wrap><table class=sort><thead><tr>" + "".join(f"<th>{h}</th>" for h in ("Cluster", "Window (d)", "Pod cost", "Waste", "Potential", "Node cost", "Node idle")) + "</tr></thead><tbody>")
    for f in facts:
        P.append(f"<tr><td>{e(f['name'])}</td><td>{f['window_days']:.0f}</td><td data-v='{f['cost']:.0f}'>{usd(f['cost'])}</td><td data-v='{f['waste']:.0f}'>{usd(f['waste'])}</td><td data-v='{f['pot']:.0f}'>{usd(f['pot'])}</td><td data-v='{f['pools']['cost']:.0f}'>{usd(f['pools']['cost'])}</td><td data-v='{f['pools']['idle']:.0f}'>{usd(f['pools']['idle'])}</td></tr>")
    P.append("</tbody></table></div><h2>" + DECISIONS_TITLE + "</h2><p>" + e(DECISIONS_LEAD) + "</p>" + "".join(f"<h3>{e(g)}: {e(cap)}</h3><ol>" + "".join(f"<li>{e(x)}</li>" for x in lines) + "</ol>" for g, cap, lines in decision_groups(S)) + ("<h2>Appendix: evidence detail</h2><p class=mut>The statements above, with their basis and how to check each one in PerfectScale.</p><div class=wrap><table><thead><tr><th>Status</th><th>Statement</th><th>Basis</th><th>How to check</th></tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{e(x)}</td>" for x in row) + "</tr>" for row in evidence_rows(S)) + "</tbody></table></div>") + (("<h2>Feedback requested</h2><ol>" + "".join(f"<li>{e(x)}</li>" for x in FEEDBACK_PROMPTS) + "</ol>") if cfg.get("status") else "") + "</main>")
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>{e(cfg['customer'])} Optimization Plan</title>"
            f"<style>{CSS}</style></head><body>{''.join(P)}<script type=application/json id=plan>{json.dumps(CALC)}</script><script>{JS};{JS_CALC};{JS_RESIZE}</script></body></html>")


# ---------------------------------------------------------------- XLSX / CSV
# range_low/range_likely/range_literal surface the resiliency-basis low/likely/literal
# breakdown (model.py's range.low / range.likely / range.literal) that MD/HTML always
# show via money(); without them a FinOps analyst pivoting the sheet has no way to
# tell a resiliency-spend row from a savings row beyond the savings_basis text column,
# and risks summing them together naively. Populated only for resiliency-basis rows.
ACOLS = ["id", "phase", "title", "fit", "value_kind", "cluster", "scope", "savings_basis", "savings_monthly_usd", "obs",
         "range_low", "range_likely", "range_literal",
         "lane", "gated_on_automation", "effort_tier", "confidence", "change_risk", "owner_role", "roi_score", "depends_on", "evidence"]
# Dollar-valued columns needing currency formatting in the XLSX sheet.
_DOLLAR_COLS = ("savings_monthly_usd", "obs", "range_low", "range_likely", "range_literal")


def _acol_value(a, c):
    if c.startswith("range_"):
        r = a.get("range")
        return r.get(c[len("range_"):]) if r else None
    v = a.get(c)
    return ", ".join(v) if isinstance(v, list) else v


def render_tables(cfg, S, facts, out_dir):
    out_dir = Path(out_dir)
    rows = [[_acol_value(a, c) for c in ACOLS] for a in S["actions"]]
    csvp = out_dir / fname(cfg, "optimization-plan", "csv")
    with open(csvp, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(ACOLS); w.writerows(rows)
    made = [csvp]
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError:
        return made
    wb = Workbook(); ws = wb.active; ws.title = "Actions"
    ws.append(ACOLS)
    for r in rows:
        ws.append(r)
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.freeze_panes = "A2"; ws.auto_filter.ref = ws.dimensions
    for col, wd in zip("ABCDEFGHIJKLMNOPQRSTUV", (6, 6, 52, 10, 11, 18, 40, 20, 12, 12, 12, 12, 12, 10, 10, 8, 10, 10, 12, 8, 14, 80)):
        ws.column_dimensions[col].width = wd
    dollar_cols = [i + 1 for i, c in enumerate(ACOLS) if c in _DOLLAR_COLS]
    for r in ws.iter_rows(min_row=2, min_col=min(dollar_cols), max_col=max(dollar_cols)):
        for c in r:
            if c.column in dollar_cols:
                c.number_format = '"$"#,##0'
    w2 = wb.create_sheet("Clusters")
    w2.append(["cluster", "window_days", "scale_factor", "pod_cost", "pod_waste", "potential", "node_cost", "node_idle", "automation_actions"])
    for f in facts:
        w2.append([f["name"], round(f["window_days"], 1), round(f["factor"], 3), round(f["cost"]), round(f["waste"]), round(f["pot"]), round(f["pools"]["cost"]), round(f["pools"]["idle"]), f["audit"]["n"]])
    w3 = wb.create_sheet("Top workloads")
    w3.append(["cluster", "workload", "namespace", "lane", "cpu_now", "cpu_rec", "waste", "potential"])
    for f in facts:
        for r in f["top"]:
            w3.append([f["name"], short_name(r["name"]), r["ns"], r["lane"], r["cpu_cur"], r["cpu_rec"], round(r["waste"]), round(r["pot"])])
    w4 = wb.create_sheet("Node pools")
    w4.append(["cluster", "pool", "machine", "nodes_avg", "idle", "cost", "rec_to", "rec_save"])
    for f in facts:
        for p in f["pools"]["long_lived"]:
            w4.append([f["name"], p["id"], p["machine"], p["nodes_avg"], round(p["idle"]), round(p["cost"]), (p["rec"] or {}).get("to"), round((p["rec"] or {}).get("save", 0))])
    xp = out_dir / fname(cfg, "optimization-plan", "xlsx")
    wb.save(xp)
    return made + [xp]


def write_exec_summary(cfg, plan_json, out):
    """Render the one-page executive summary beside the plan, in both formats.

    Part of the standard output set, not a follow-up command: the .docx is what
    customers edit and forward, the .html is what they link. Both come from the
    plan JSON via the same script, so neither can drift from the plan or from
    each other. A missing node or `docx` module degrades to a warning rather
    than failing the run - the HTML needs no module and is still produced.
    """
    script = Path(__file__).resolve().parent.parent / "exec_summary.js"
    made = []
    if not script.exists():
        print(f"warning: {script.name} not found; executive summary skipped", file=sys.stderr)
        return made
    for ext in ("docx", "html"):
        dest = out / fname(cfg, "executive-summary", ext)
        try:
            r = subprocess.run(["node", str(script), str(plan_json), str(dest)],
                               capture_output=True, text=True, timeout=120)
        except FileNotFoundError:
            print("warning: node not found; executive summary skipped "
                  "(install Node to get the .docx and .html summary)", file=sys.stderr)
            return made
        except subprocess.TimeoutExpired:
            print(f"warning: executive summary ({ext}) timed out", file=sys.stderr)
            continue
        if r.returncode == 0 and dest.exists() and dest.stat().st_size:
            made.append(dest)
        else:
            tail = (r.stderr or "").strip().splitlines()
            print(f"warning: executive summary ({ext}) not written: "
                  f"{tail[-1] if tail else 'exit ' + str(r.returncode)}", file=sys.stderr)
    return made


def write_all(cfg, S, facts):
    out = cfg.out()
    made = []
    def w(name, text):
        p = out / name
        p.write_text(text, encoding="utf-8"); made.append(p)
    w(fname(cfg, "optimization-plan", "md"), render_plan_md(cfg, S))
    w(fname(cfg, "optimization-plan", "html"), render_html(cfg, S, facts))
    plan_json = out / fname(cfg, "optimization-plan", "json")
    w(fname(cfg, "optimization-plan", "json"), json.dumps(S, indent=1, default=str))
    for f in facts:
        w(fname(cfg, f"{f['name']}-findings", "md"), render_cluster_md(cfg, S, f))
    made += render_tables(cfg, S, facts, out)
    made += write_exec_summary(cfg, plan_json, out)
    return made
