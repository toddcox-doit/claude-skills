"""Renderers: Markdown (plan, per-cluster findings), self-contained HTML, XLSX/CSV, JSON."""
import csv
import html
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from .model import short_name
from .util import cores, human_list, long_date, pct, slug, usd

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


IDLE_LEAD = ("Idle is node capacity paid for and not used. The breakdown says which kind of pool it sits "
             "on, because the fix differs: auto-provisioned pools are a provisioning-config question, "
             "GPU pools a scheduling one, long-lived pools a minimum-size and consolidation one.")


def idle_cols(S):
    """Which breakdown columns carry information for this tenant.

    The decomposition only earns a column when it splits something. On a tenant whose
    idle is entirely on long-lived pools, 'Long-lived pool idle' repeats 'Idle' in every
    row and the auto-provisioned and GPU columns are all zero - three columns of nothing,
    which make a reader hunt for a distinction that is not there. Dropped, and said once
    underneath instead.
    """
    C = S["cluster_idle"]
    return {
        "ap": any(c["auto_provisioned"] for c in C),
        "infl": any(c["ap_inflated_share"] for c in C),
        "ll": any(c["long_lived"] != c["idle"] for c in C),
        "gpu": any(c["gpu"] for c in C),
        # the driver column earns its place only when clusters actually differ: one
        # pool kind at ~100% everywhere is a single sentence, not fifteen rows
        "driver": not (len({c["driver"] for c in C}) <= 1 and all(c["driver_share"] >= 0.99 for c in C)),
    }


def idle_headers(S):
    show = idle_cols(S)
    h = ["Cluster", "Idle"]
    if show["ap"]:
        h.append("Auto-provisioned idle")
    if show["infl"]:
        h.append("Of which on shapes where CPU requests are 5x or more of use")
    if show["ll"]:
        h.append("Long-lived pool idle")
    if show["gpu"]:
        h.append("GPU pool idle")
    if show["driver"]:
        h.append("Largest driver")
    return h + ["What closes it"]


def idle_rows(S):
    show = idle_cols(S)
    rows = []
    for c in S["cluster_idle"]:
        r = [c["cluster"], f"{usd(c['idle'])} ({c['idle_pct'] * 100:.0f}%)"]
        if show["ap"]:
            r.append(usd(c["auto_provisioned"]))
        if show["infl"]:
            r.append(f"{c['ap_inflated_share'] * 100:.0f}%")
        if show["ll"]:
            r.append(usd(c["long_lived"]))
        if show["gpu"]:
            r.append(f"{usd(c['gpu'])} ({c['gpu_share'] * 100:.0f}%)")
        if show["driver"]:
            r.append(f"{c['driver']} ({usd(c['driver_usd'])}, {c['driver_share'] * 100:.0f}%)")
        # ids only, deliberately no dollars: these are pending-blocker estimates, and
        # printing one beside an idle measurement invites a subtraction between two
        # different money bases that the plan spends a whole section keeping apart
        ids = c.get("fix_ids") or []
        r.append(", ".join(ids) if ids else "no node-level action - see the pool-consolidation question")
        rows.append(tuple(r))
    return rows


def idle_note(S):
    """Why the breakdown columns are missing, when they are."""
    show = idle_cols(S)
    if all(show.values()):
        return ""
    C = S["cluster_idle"]
    gone = []
    if not show["ap"]:
        gone.append("auto-provisioned pool idle")
    if not show["gpu"]:
        gone.append("GPU pool idle")
    if not show["ll"]:
        gone.append("long-lived pool idle")
    if not show["driver"]:
        gone.append("largest driver")
    tot = sum(c["idle"] for c in C)
    tail = (f" Every dollar of the {usd(tot)}/mo sits on long-lived node pools, so the question for each "
            f"cluster is whether its pools are allowed to give a node back, not which kind of pool to look at. "
            f"'What closes it' names the actions that do that, listed in full in the plan above."
            if not show["ll"] else "")
    return f"Columns not shown because they are zero or identical for every cluster in scope: {human_list(gone)}.{tail}"


GROUP_ORDER = [("Ownership", "who can act on it"), ("Intent", "whether it was deliberate"), ("Access and constraints", "what we need to be able to proceed")]
DECISIONS_TITLE = "Questions to confirm with the customer"
DECISIONS_LEAD = ("These are questions for the customer, not choices for us to make. The answers decide how much of the savings above is reachable and how quickly. "
                  "Within each group, questions that unlock claimed savings come first, ranked by dollars; questions about unclaimed idle follow. Dollar figures are at 100% adoption.")


BASIS_LABEL = {"measured": "Measured", "inferred": "Inferred", "unknown": "Not known"}


NS_TITLE = "Appendix: analysis by namespace"
NS_LEAD = ("The plan above is ordered by what you do; this is the same money ordered by who owns it. "
           "One row per namespace name, summed across every cluster it runs in, so a namespace with one "
           "owner is one conversation rather than fifteen. Ranked by potential savings plus resiliency "
           "spend, because a namespace whose whole problem is under-provisioning is still work to schedule.")
NS_FOOT = ("Potential savings here is PodFit-computed, the same money the actions above draw on - this "
           "table re-cuts it by owner, it does not add to it. Resiliency spend is money to add, never "
           "netted against savings.")
NS_COLS = ["Namespace", "Clusters", "Workloads", "Cost", "Waste", "Potential savings", "At risk", "OOM", "Resiliency spend (likely)"]
MANAGED_TITLE = "Appendix: platform-managed add-ons (held out of this ask)"
NS_LEGEND = ("Platform-managed namespaces (kube-system and similar) are not in this table - "
             f"see '{MANAGED_TITLE}' below.")
MANAGED_LEAD = ("The namespaces below are platform add-ons - kube-system and similar - where the platform "
                "itself reconciles the manifests, so a PerfectScale change would just be reverted. They are "
                "not part of this plan's savings or automation ask for that reason, not because they were "
                "overlooked. Held out here, not deleted from the plan: the same columns as the table above, "
                "including At risk and OOM. A platform add-on that is failing is still a real resiliency risk "
                "- if one shows a finding here, it is already covered by its own action in Action detail above, "
                "the same as any other workload.")


def _ns_row(r):
    return [
        r["ns"],
        str(r["n_clusters"]),
        str(r["n"]),
        usd(r["cost"]),
        usd(r["waste"]),
        usd(r["pot"]),
        str(r["risk_n"]) if r["risk_n"] else "-",
        str(r["oom_n"]) if r["oom_n"] else "-",
        usd(r["res_likely"]) if r["res_likely"] else "-",
    ]


def _ns_split(S, top=40):
    """Platform-managed namespaces get their own section (see MANAGED_LEAD for why), so they
    are split out before the main appendix is ranked and capped - a managed namespace must
    never occupy one of the main table's top-N slots only to be hidden from it.

    Returns (non_managed_rows_all, non_managed_shown_n, non_managed_rest, managed_rows_all).
    """
    nr = S.get("namespace_rollup") or {}
    rows = nr.get("rows", [])
    nm = [r for r in rows if not r["managed"]]
    mg = [r for r in rows if r["managed"]]
    shown_n = min(top, len(nm))
    rest = nm[top:]
    return nm, shown_n, rest, mg


def namespace_rows(S, top=40):
    """Rows for the by-namespace appendix, as display strings, capped for prose. Excludes
    platform-managed namespaces - those get their own section, managed_addon_rows."""
    nm, shown_n, _, _ = _ns_split(S, top)
    return [_ns_row(r) for r in nm[:shown_n]]


def managed_addon_rows(S):
    """Platform-managed namespaces, same columns as namespace_rows, kept in their own section.
    Never capped: the count is small by construction (platform add-ons, not application
    namespaces), so there is no "top N" to rank within it."""
    _, _, _, mg = _ns_split(S)
    return [_ns_row(r) for r in mg]


def namespace_note(S, top=40):
    """The 'and N more' line, scoped to the non-managed table only (platform-managed
    namespaces have their own section and their own total, not this one)."""
    nm, shown_n, rest, _ = _ns_split(S, top)
    cap = (f"Showing the top {shown_n} of {len(nm)} namespaces, ranked by potential savings plus "
           f"resiliency spend. The remaining {len(rest)} hold {usd(sum(r['pot'] for r in rest))}/mo of potential savings and "
           f"{usd(sum(r['res_likely'] for r in rest))}/mo of resiliency spend between them; the spreadsheet lists every namespace."
           if rest else f"All {len(nm)} namespaces in scope are listed.")
    return cap


def managed_addon_note(S):
    """Why this total sits outside the plan's achievable figure, mirroring namespace_note's
    reconciliation note but scoped to the managed-only total."""
    _, _, _, mg = _ns_split(S)
    if not mg:
        return ""
    held = round(sum(r["pot"] for r in mg))
    res = round(sum(r["res_likely"] for r in mg))
    cap = f"{len(mg)} platform-managed namespace{'s' if len(mg) != 1 else ''} carrying {usd(held)}/mo of PodFit-computed potential savings"
    cap += f" and {usd(res)}/mo of resiliency spend" if res else ""
    cap += (", none of it counted in this plan's achievable or resiliency-investment totals - the automation "
            "ask deliberately leaves it out, for the reason above.")
    return cap


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
    ih = idle_headers(S)
    L += ["### Why each cluster's idle is what it is", "", IDLE_LEAD, "",
          "| " + " | ".join(ih) + " |", "|" + "---|" * len(ih)]
    L += ["| " + " | ".join(row) + " |" for row in idle_rows(S)]
    L += ([""] + [idle_note(S)] if idle_note(S) else []) + [""]
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
    nm, shown_n, _, mg = _ns_split(S)
    if nm:
        nrows = [_ns_row(r) for r in nm[:shown_n]]
        L += [f"## {NS_TITLE}", "", NS_LEAD, "",
              "| " + " | ".join(NS_COLS) + " |",
              "|" + "---|" * len(NS_COLS)]
        L += ["| " + " | ".join(r) + " |" for r in nrows]
        L += ["", namespace_note(S), "", NS_LEGEND + " " + NS_FOOT, ""]
        # Markdown has no click target, so the cluster lists go in a details block the
        # reader can open: collapsible where the viewer supports it, plain text where
        # it does not, and present on paper either way.
        L += ["<details>", "<summary>Which clusters each namespace runs in</summary>", ""]
        for rec in nm[:shown_n]:
            L.append(f"- `{rec['ns']}` - {', '.join(rec['clusters'])}")
        L += ["", "</details>", ""]
    if mg:
        L += [f"## {MANAGED_TITLE}", "", MANAGED_LEAD, "",
              "| " + " | ".join(NS_COLS) + " |",
              "|" + "---|" * len(NS_COLS)]
        L += ["| " + " | ".join(_ns_row(r)) + " |" for r in mg]
        L += ["", managed_addon_note(S), ""]
        L += ["<details>", "<summary>Which clusters each namespace runs in</summary>", ""]
        for rec in mg:
            L.append(f"- `{rec['ns']}` - {', '.join(rec['clusters'])}")
        L += ["", "</details>", ""]
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
    top_ns = sorted(f["namespaces"], key=lambda r: -(r["waste_o"] + r["res_likely_o"]))[:10]
    if top_ns:
        L += ["", "## Top namespaces", "", "| Namespace | Workloads | Cost | Waste | Potential savings | At risk | Resiliency spend (likely) |", "|---|---|---|---|---|---|---|"]
        for r in top_ns:
            at_risk = f"{r['risk_n']:,} ({r['oom_n']} OOM)" if r["oom_n"] else (f"{r['risk_n']:,}" if r["risk_n"] else "-")
            L.append(f"| {r['ns']} | {r['n']:,} | {usd(r['cost'])} | {usd(r['waste'])} | {usd(r['pot'])} | {at_risk} | {usd(r['res_likely']) if r['res_likely'] else '-'} |")
        L += ["", "Resiliency spend is money to add to stop workloads failing, not a saving - see the optimization plan's resiliency row.", ""]
    L += ["", "## Largest opportunities", "", "| Workload | Namespace | Lane | CPU now to rec | Waste | Potential |", "|---|---|---|---|---|---|"]
    for r in f["top"][:12]:
        L.append(f"| {short_name(r['name'])} | {r['ns']} | {r['lane']} | {cores(r['cpu_cur'])} to {cores(r['cpu_rec'])} | {usd(r['waste'])} | {usd(r['pot'])} |")
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
svg text{fill:var(--fg);font-size:12px}svg rect{fill:var(--card);stroke:var(--line)}svg path{stroke:var(--mut);fill:none}
/* namespace rows expand to a full-width row listing their clusters */
button.nstog{font:inherit;color:var(--acc);background:none;border:0;padding:0;cursor:pointer;text-align:left}
button.nstog::before{content:"\\25B8 ";color:var(--mut)}
button.nstog[aria-expanded=true]::before{content:"\\25BE "}
button.nstog:hover{text-decoration:underline}
tr.nsdet>td{padding-top:0;font-size:13px}
tr.nsdet[hidden]{display:none}
@media print{tr.nsdet[hidden]{display:table-row!important}button.nstog::before{content:""}}
/* collapsible sections: every h2 becomes the toggle for the block beneath it */
/* the adoption control is sticky, so anything scrolled to must clear it - otherwise a
   jumped-to heading lands underneath it and cannot be clicked to expand */
.sechd,details[id]{scroll-margin-top:104px}
.sechd{cursor:pointer;user-select:none;display:flex;align-items:center;gap:9px}
.sechd::before{content:"";flex:none;border:solid var(--mut);border-width:0 2px 2px 0;padding:3px;margin-bottom:3px;transform:rotate(45deg);transition:transform .15s}
.sechd[aria-expanded=false]::before{transform:rotate(-45deg)}
.sechd:hover{color:var(--acc)}.sechd:hover::before{border-color:var(--acc)}
.sechd:focus-visible{outline:2px solid var(--acc);outline-offset:3px}
.secbar{display:flex;gap:12px;justify-content:flex-end;margin:16px 0 -10px}
.secbar button{font:inherit;font-size:12px;color:var(--acc);background:none;border:0;padding:0;cursor:pointer;text-decoration:underline}
/* a printed plan is read on paper: no one can expand it, so print it whole */
@media print{.sec[hidden]{display:block!important}.sechd::before{display:none}.secbar{display:none}}
/* Actions table filters */
.afilt{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:10px 0 4px;font-size:13px}
.afilt .pd{position:relative;display:inline-block}
.afilt .cfg{font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:6px;padding:3px 9px;cursor:pointer}
.afilt .cfg:hover,.afilt .cfg[aria-expanded=true]{border-color:var(--acc);color:var(--acc)}
.afilt .lnk{font:inherit;color:var(--acc);background:none;border:0;padding:0;cursor:pointer;text-decoration:underline}
.afilt .menu .cnt{color:var(--mut);margin-left:auto;padding-left:10px}
tr.off{display:none}"""
# Sorting keeps a detail row glued to the row it belongs to: the namespace appendix puts
# each namespace's cluster list in a full-width <tr class=nsdet> beneath its row, and a
# plain sort would scatter those across the table.
JS = """document.querySelectorAll('table.sort').forEach(function(t){t.querySelectorAll('th').forEach(function(th,i){th.addEventListener('click',function(){
if(t.dataset.dragged==='1'){t.dataset.dragged='';return}
var b=t.tBodies[0],all=Array.prototype.slice.call(b.rows),d=th.dataset.d==='1'?-1:1;th.dataset.d=d===1?'1':'0';
var det={};all.forEach(function(x){if(x.classList.contains('nsdet'))det[x.getAttribute('data-for')]=x});
var r=all.filter(function(x){return !x.classList.contains('nsdet')});
r.sort(function(x,y){var a=x.cells[i].dataset.v||x.cells[i].textContent,c=y.cells[i].dataset.v||y.cells[i].textContent,na=parseFloat(a),nc=parseFloat(c);
return(isNaN(na)||isNaN(nc)?a.localeCompare(c):na-nc)*d});
r.forEach(function(x){b.appendChild(x);var k=x.getAttribute('data-ns');if(k&&det[k])b.appendChild(det[k])})})})})"""

# Namespace rows expand to the clusters they run in. A full-width row rather than content
# inside the first cell: that column autofits to the bare namespace name, so a list of a
# dozen cluster names crammed into it wraps one word per line.
JS_NSROW = """(function(){
document.querySelectorAll('button.nstog').forEach(function(b){
 b.addEventListener('click',function(){
  var row=b.closest('tr'),k=row&&row.getAttribute('data-ns');if(!k)return;
  var d=row.parentNode.querySelector('tr.nsdet[data-for="'+(window.CSS&&CSS.escape?CSS.escape(k):k)+'"]');
  if(!d)return;
  var open=d.hasAttribute('hidden');
  if(open){d.removeAttribute('hidden')}else{d.setAttribute('hidden','')}
  b.setAttribute('aria-expanded',open?'true':'false')})})
})()"""


# Drag-to-resize columns. The table keeps its overall width: every pixel a column
# gains is taken from its right-hand neighbour, so nothing reflows sideways and no
# horizontal scrollbar appears. Widths persist per table in localStorage.
JS_RESIZE = """(function(){var MIN=44;
function K(t,n){return 'psplan.cols.'+(t.dataset.rsz||'t')+'.'+n}
document.querySelectorAll('table').forEach(function(t,ti){
 if(t.classList.contains('nrsz'))return; // compact tables opt out of the column picker
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


# Runs LAST, after JS_RESIZE: column widths are measured with getBoundingClientRect,
# which reads 0 inside a hidden section, so every table must still be visible when
# the resizer initialises. Collapse happens only once that has finished.
JS_COLLAPSE = """(function(){
var main=document.querySelector('main');if(!main)return;
var CLOSED=/[\s\S]*/; // every section starts collapsed: the reader opens what they came for
var hs=[].slice.call(main.children).filter(function(n){return n.tagName==='H2'});
if(!hs.length)return;
var secs=[];
hs.forEach(function(h,i){
 var box=document.createElement('div');box.className='sec';box.id='sec-'+i;
 var n=h.nextSibling;
 while(n&&!(n.nodeType===1&&n.tagName==='H2')){var nx=n.nextSibling;box.appendChild(n);n=nx;}
 h.parentNode.insertBefore(box,h.nextSibling);
 h.className=(h.className?h.className+' ':'')+'sechd';
 h.setAttribute('role','button');h.tabIndex=0;h.setAttribute('aria-controls',box.id);
 function set(o){h.setAttribute('aria-expanded',o?'true':'false');box.hidden=!o;}
 set(!CLOSED.test(h.textContent.trim()));
 h.addEventListener('click',function(){set(box.hidden)});
 h.addEventListener('keydown',function(ev){if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();set(box.hidden)}});
 secs.push({box:box,set:set});
});
var bar=document.createElement('div');bar.className='secbar';
function mk(t,o){var b=document.createElement('button');b.type='button';b.textContent=t;
 b.addEventListener('click',function(){secs.forEach(function(s){s.set(o)})});return b}
bar.appendChild(mk('Expand all',true));bar.appendChild(mk('Collapse all',false));
hs[0].parentNode.insertBefore(bar,hs[0]);
// A link into a collapsed section (the Actions table points at Action detail) must open
// it. Driven off the anchor's own href rather than location.hash: the click path then
// works even where the hash never updates, and it runs before the browser's own scroll,
// so the target is already visible when that scroll lands.
function revealId(id,scroll){if(!id)return false;
 var t=document.getElementById(id);if(!t)return false;
 secs.forEach(function(s){if(s.box.contains(t))s.set(true)});
 if(t.tagName==='DETAILS')t.open=true;
 if(scroll)t.scrollIntoView();
 return true}
addEventListener('hashchange',function(){revealId(location.hash.slice(1),true)});
main.addEventListener('click',function(ev){
 var a=ev.target&&ev.target.closest&&ev.target.closest('a[href^="#"]');
 if(!a)return;
 revealId(a.getAttribute('href').slice(1),false)});
revealId(location.hash.slice(1),true);
})()"""

# Actions table filters. Runs after JS_RESIZE so column widths are measured on every row, then
# hides the rows outside the chosen phase/topic/cluster/lane (Phase 1 to start).
JS_AFILT = """(function(){var bar=document.querySelector('.afilt'),t=document.getElementById('actions');if(!bar||!t)return;
var rows=[].slice.call(t.tBodies[0].rows),menus=[].slice.call(bar.querySelectorAll('.menu'));
function sel(m){return [].slice.call(m.querySelectorAll('input:checked')).map(function(c){return c.value})}
function apply(){var F={};menus.forEach(function(m){F[m.dataset.f]=sel(m)});var n=0;
 rows.forEach(function(r){var ok=Object.keys(F).every(function(f){return (r.dataset[f]||'').split('|').some(function(x){return F[f].indexOf(x)>=0})});
  r.classList.toggle('off',!ok);if(ok)n++});
 menus.forEach(function(m){var all=m.querySelectorAll('input').length,s=sel(m),lab=m.parentNode.querySelector('.fsum');
  lab.textContent=s.length===all?'All':(s.length?(m.dataset.f==='phase'?s.join(', '):(s.length+' of '+all)):'None')});
 bar.querySelector('.fcount').textContent='Showing '+n+' of '+rows.length+' actions'}
bar.addEventListener('change',apply);
bar.querySelectorAll('button.cfg').forEach(function(b){b.addEventListener('click',function(ev){ev.stopPropagation();
 var m=b.nextElementSibling,o=m.hidden;menus.forEach(function(x){x.hidden=true});m.hidden=!o;b.setAttribute('aria-expanded',o?'true':'false')})});
document.addEventListener('click',function(ev){if(!bar.contains(ev.target))menus.forEach(function(m){m.hidden=true})});
document.addEventListener('keydown',function(ev){if(ev.key==='Escape')menus.forEach(function(m){m.hidden=true})});
bar.querySelectorAll('[data-all],[data-none]').forEach(function(b){b.addEventListener('click',function(){var on=b.hasAttribute('data-all');
 b.closest('.menu').querySelectorAll('input').forEach(function(c){c.checked=on});apply()})});
document.getElementById('freset').addEventListener('click',function(){menus.forEach(function(m){m.querySelectorAll('input').forEach(function(c){c.checked=m.dataset.f!=='phase'||c.value==='1'})});apply()});
document.getElementById('fall').addEventListener('click',function(){bar.querySelectorAll('.menu input').forEach(function(c){c.checked=true});apply()});
apply()})()"""

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


TOPIC_ORDER = ("Automation", "Right-sizing", "Resiliency", "Policy", "Scale-down blockers", "Node pools and autoscaling", "Governance")
TENANT_WIDE = "Tenant-wide"


def action_clusters(a, facts):
    """The clusters an action names, matched on whole names so 'prod' never matches 'prod-a'."""
    hit = [f["name"] for f in facts if re.search(rf"(?<![\w-]){re.escape(f['name'])}(?![\w-])", a.get("cluster") or "")]
    return hit or [TENANT_WIDE]


def action_filters(S, facts):
    """Pull-down filters over the Actions table: phase, topic, cluster, lane. The page opens on
    Phase 1 - the work to start now - and the reader widens it to what interests them."""
    A = S["actions"]

    def menu(key, label, values, checked, text=lambda v: v):
        counts = {v: 0 for v in values}
        for a in A:
            for v in (str(a["phase"]),) if key == "phase" else ((a.get("topic") or "Governance"),) if key == "topic" else \
                    tuple(action_clusters(a, facts)) if key == "cl" else ((LANE.get(a["lane"]) or a["lane"]),):
                if v in counts:
                    counts[v] += 1
        items = "".join(f"<label><input type=checkbox value='{e(v)}'{' checked' if checked(v) else ''}>{e(text(v))}"
                        f"<span class=cnt>{counts[v]}</span></label>" for v in values if counts[v])
        return (f"<span class=pd><button type=button class=cfg aria-haspopup=true aria-expanded=false>{e(label)}: <span class=fsum></span> &#9662;</button>"
                f"<div class=menu data-f={key} hidden><p class=ttl>{e(label)}</p>{items}"
                "<div class=acts><button type=button data-all>All</button><button type=button data-none>None</button></div></div></span>")
    phases = sorted({str(a["phase"]) for a in A})
    topics = [t for t in TOPIC_ORDER if any((a.get("topic") or "Governance") == t for a in A)]
    clusters = [f["name"] for f in facts] + [TENANT_WIDE]
    lanes = [LANE[k] for k in LANE if any(a["lane"] == k for a in A)]
    return ("<div class=afilt>"
            + menu("phase", "Phase", phases, lambda v: v == "1", lambda v: PHASE.get(int(v), f"Phase {v}"))
            + menu("topic", "Topic", topics, lambda v: True)
            + menu("cl", "Cluster", clusters, lambda v: True)
            + menu("lane", "Lane", lanes, lambda v: True)
            + "<span class='mut fcount'></span><button type=button class=lnk id=freset>Phase 1 only</button>"
            "<button type=button class=lnk id=fall>Show everything</button></div>")


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
    P.append("<h2>Why each cluster's idle is what it is</h2><p>" + e(IDLE_LEAD) + "</p><div class=wrap><table class=sort><thead><tr>"
             + "".join(f"<th>{e(h)}</th>" for h in idle_headers(S)) + "</tr></thead><tbody>"
             + "".join("<tr>" + "".join(f"<td>{e(x)}</td>" for x in row) + "</tr>" for row in idle_rows(S))
             + "</tbody></table></div>" + (f"<p class=mut>{e(idle_note(S))}</p>" if idle_note(S) else ""))
    P.append("<h2>Actions</h2>" + action_filters(S, facts) + "<div class=wrap><table id=actions class=sort><thead><tr>" + "".join(f"<th>{h}</th>" for h in ("ID", "Phase", "Action", "Fit", "Cluster", "Value / mo", "Basis", "Lane", "Effort", "Confidence", "ROI")) + "<th>At <span data-calc='pct'>" + pf + "</span></th></tr></thead><tbody>")
    for a in S["actions"]:
        v = a["savings_monthly_usd"] or 0
        note = unblock_note(a)
        title_cell = f"<a href='#{a['id']}'>{e(a['title'])}</a>" + (f" <b>{e(note)}</b>" if note else "")
        P.append(f"<tr data-phase='{a['phase']}' data-topic='{e(a.get('topic') or 'Governance')}' data-lane='{e(LANE.get(a['lane']) or a['lane'])}' "
                 f"data-cl='{e(chr(124).join(action_clusters(a, facts)))}'><td data-v='{a['id'][1:]}'>{e(a['id'])}</td><td>{a['phase']}</td><td>{title_cell}</td><td>{e(a['fit'])}</td><td>{e(a['cluster'])}</td><td class=num data-v='{v:.0f}'>{e(money(a))}</td>"
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
    nm, shown_n, _, mg = _ns_split(S)

    def _ns_table_html(title, lead, records, note):
        body = ""
        for rec in records:
            row = _ns_row(rec)
            # The cluster list gets a full-width row of its own, tied to its namespace by
            # data-ns / data-for. The sort script re-pairs them, so reordering the table
            # cannot separate a namespace from its clusters. data-v keeps the sort keyed on
            # the bare name rather than anything rendered around it.
            body += (f"<tr data-ns='{e(rec['ns'])}'><td data-v='{e(rec['ns'])}'>"
                     f"<button type=button class=nstog aria-expanded=false>{e(rec['ns'])}</button></td>"
                     + "".join(f"<td class=num>{e(c)}</td>" for c in row[1:]) + "</tr>")
            body += (f"<tr class=nsdet data-for='{e(rec['ns'])}' hidden><td colspan={len(NS_COLS)}>"
                     f"<span class=mut>Runs in {rec['n_clusters']} cluster"
                     f"{'' if rec['n_clusters'] == 1 else 's'}: {e(', '.join(rec['clusters']))}</span></td></tr>")
        return ("<h2>" + e(title) + "</h2><p>" + e(lead) + "</p>"
                + "<p class=mut>Click a namespace to see which clusters it runs in.</p>"
                + "<div class=wrap><table class=sort><thead><tr>"
                + "".join(f"<th>{e(h)}</th>" for h in NS_COLS) + "</tr></thead><tbody>"
                + body + "</tbody></table></div><p class=mut>" + e(note) + "</p>")

    ns_html = (_ns_table_html(NS_TITLE, NS_LEAD, nm[:shown_n], namespace_note(S))
               + "<p class=fn>" + e(NS_LEGEND) + " " + e(NS_FOOT) + "</p>") if nm else ""
    managed_html = _ns_table_html(MANAGED_TITLE, MANAGED_LEAD, mg, managed_addon_note(S)) if mg else ""
    P.append("</tbody></table></div>" + "<h2>" + DECISIONS_TITLE + "</h2><p>" + e(DECISIONS_LEAD) + "</p>" + "".join(f"<h3>{e(g)}: {e(cap)}</h3><ol>" + "".join(f"<li>{e(x)}</li>" for x in lines) + "</ol>" for g, cap, lines in decision_groups(S)) + ns_html + managed_html + ("<h2>Appendix: evidence detail</h2><p class=mut>The statements above, with their basis and how to check each one in PerfectScale.</p><div class=wrap><table><thead><tr><th>Status</th><th>Statement</th><th>Basis</th><th>How to check</th></tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{e(x)}</td>" for x in row) + "</tr>" for row in evidence_rows(S)) + "</tbody></table></div>") + (("<h2>Feedback requested</h2><ol>" + "".join(f"<li>{e(x)}</li>" for x in FEEDBACK_PROMPTS) + "</ol>") if cfg.get("status") else "") + "</main>")
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>{e(cfg['customer'])} Optimization Plan</title>"
            f"<style>{CSS}</style></head><body>{''.join(P)}<script type=application/json id=plan>{json.dumps(CALC)}</script><script>{JS};{JS_CALC};{JS_RESIZE};{JS_AFILT};{JS_NSROW};{JS_COLLAPSE}</script></body></html>")


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
    """The action grid as XLSX. A CSV of the same rows is written only when openpyxl is missing:
    with the workbook present it is a duplicate, and one more file in the customer's folder."""
    out_dir = Path(out_dir)
    rows = [[_acol_value(a, c) for c in ACOLS] for a in S["actions"]]
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError:
        csvp = out_dir / fname(cfg, "optimization-plan", "csv")
        with open(csvp, "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(ACOLS); w.writerows(rows)
        return [csvp]
    made = []
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
    # Namespaces: the plan re-cut by who owns it. Unlike the prose appendix this is not
    # capped - the sheet is where someone pivots to find their own namespace, and a row
    # missing because it ranked 41st is the one failure that makes the sheet untrustworthy.
    nr = S.get("namespace_rollup") or {}
    if nr.get("rows"):
        NSCOLS = ["namespace", "platform_managed", "clusters", "cluster_names", "workloads",
                  "cost", "waste", "potential_savings", "at_risk", "oom", "resiliency_likely", "resiliency_literal"]
        wn = wb.create_sheet("Namespaces")
        wn.append(NSCOLS)
        for r in nr["rows"]:
            wn.append([r["ns"], "yes" if r["managed"] else "", r["n_clusters"], ", ".join(r["clusters"]),
                       r["n"], r["cost"], r["waste"], r["pot"], r["risk_n"], r["oom_n"],
                       r["res_likely"], r["res_literal"]])
        for c in wn[1]:
            c.font = Font(bold=True)
        wn.freeze_panes = "A2"; wn.auto_filter.ref = wn.dimensions
        for col, wd in zip("ABCDEFGHIJKL", (30, 16, 9, 46, 10, 10, 10, 16, 8, 6, 16, 16)):
            wn.column_dimensions[col].width = wd
        ns_dollars = [i + 1 for i, c in enumerate(NSCOLS)
                      if c in ("cost", "waste", "potential_savings", "resiliency_likely", "resiliency_literal")]
        for row in wn.iter_rows(min_row=2, min_col=min(ns_dollars), max_col=max(ns_dollars)):
            for c in row:
                if c.column in ns_dollars:
                    c.number_format = '"$"#,##0'
    w3 = wb.create_sheet("Top workloads")
    w3.append(["cluster", "workload", "namespace", "lane", "cpu_now", "cpu_rec", "waste", "potential"])
    for f in facts:
        for r in f["top"]:
            w3.append([f["name"], short_name(r["name"]), r["ns"], r["lane"], r["cpu_cur"], r["cpu_rec"], round(r["waste"]), round(r["pot"])])
    # The POV kickoff table, every cluster in one sheet - the same rows and order as each
    # cluster's kickoff page, for anyone who scopes the POV in a spreadsheet instead.
    from . import pov
    pov_rows = list(pov.pov_sheet_rows(facts))
    if pov_rows:
        wp = wb.create_sheet("POV kickoff")
        wp.append(pov.POV_COLS)
        for r in pov_rows:
            wp.append(r)
        for c in wp[1]:
            c.font = Font(bold=True)
        wp.freeze_panes = "A2"; wp.auto_filter.ref = wp.dimensions
        for col, wd in zip("ABCDEFGHIJKLMN", (18, 6, 9, 40, 24, 14, 30, 12, 14, 9, 28, 14, 7, 48)):
            wp.column_dimensions[col].width = wd
        for row in wp.iter_rows(min_row=2):
            for c in row:
                if pov.POV_COLS[c.column - 1] in ("monthly_cost", "potential_savings", "resiliency_likely"):
                    c.number_format = '"$"#,##0'
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
    # The plan JSON is machine input - the executive summary, the change log on the next run and the
    # verifier read it - not a deliverable, so it sits in data/ beside the reports rather than among them.
    (out / "data").mkdir(exist_ok=True)
    plan_json = out / "data" / fname(cfg, "optimization-plan", "json")
    w(str(Path("data") / fname(cfg, "optimization-plan", "json")), json.dumps(S, indent=1, default=str))
    for f in facts:
        w(fname(cfg, f"{f['name']}-findings", "md"), render_cluster_md(cfg, S, f))
    # POV automation kickoff, one page per cluster. Imported here, not at module top: pov.py
    # reuses this module's CSS and table scripts, so a top-level import would be circular.
    from . import pov
    for f in facts:
        w(pov.pov_fname(cfg, f), pov.render_pov_html(cfg, f))
    made += render_tables(cfg, S, facts, out)
    made += write_exec_summary(cfg, plan_json, out)
    return made
