"""Independent verifier: recomputes headline numbers from the raw files (not from the model) and runs wording gates."""
import html
import json
import re
from pathlib import Path

from .util import jl, jload

BANNED = [r"\bgenuinely\b", r"\bhonestly\b", r"\bstraightforward\b", r"/Users/", r"/sessions/"]

# Placeholder names that must never survive into a rendered plan. Real tenant
# names belong in the `foreign_tenants` config key, never in this file: the other
# customers a working copy has been run against are the ones at risk of leaking.
PLACEHOLDER_TENANTS = ("Acme", "ExampleCo", "Contoso")

# Absolute filesystem paths: POSIX home/system roots and Windows drive letters.
ABS_PATH = re.compile(r"(?:/Users/|/home/|/Volumes/|/private/var/|/tmp/|[A-Za-z]:\\\\?)[\w.\-]")


def raw_cluster(cfg, c):
    d = cfg.data(c["name"])
    ws = jl(d / "workloads-all.jsonl")
    ws = [w for w in ws if not str(w.get("type", "")).startswith("-")]
    return (sum(w.get("cost") or 0 for w in ws), sum(w.get("waste") or 0 for w in ws), sum(w.get("potential_savings") or 0 for w in ws), len(ws))


def verify(cfg, S, facts, files):
    errs, ok = [], []

    def chk(cond, msg):
        (ok if cond else errs).append(msg)

    # 1. raw recompute of observed totals per cluster (tolerant to exclusions / synthetic drops)
    for f in facts:
        cost, waste, pot, n = raw_cluster(cfg, {"name": f["name"]})
        if f["n_excluded"] == 0 and f["n_synth_dropped"] == 0 and f["n_muted"] == 0:
            chk(abs(cost - f["cost_o"]) < 1 and abs(waste - f["waste_o"]) < 1, f"{f['name']}: cost/waste match raw files")
            chk(abs(pot - f["pot_o"]) < 1, f"{f['name']}: potential matches raw files")
        else:
            chk(cost + 1 >= f["cost_o"], f"{f['name']}: model cost does not exceed raw cost")
        chk(abs(f["cost"] - f["cost_o"] * f["factor"]) < 1, f"{f['name']}: scaling = observed x factor")
        ns_waste = sum(n["waste"] for n in f["namespaces"])
        chk(abs(ns_waste - f["waste"]) <= max(1, 0.01 * f["waste"]), f"{f['name']}: namespace waste sums to cluster waste")
        ns_res = sum(n["res_likely"] for n in f["namespaces"])
        res_total = f["res_oom"]["likely"] + f["res_other"]["likely"]
        chk(abs(ns_res - res_total) <= max(1, 0.01 * res_total), f"{f['name']}: namespace resiliency likely sums to res_oom + res_other")
    # 2. money bases add up from actions, and are not mixed
    A = S["actions"]
    for basis, key in (("achievable_now", "achievable_now_monthly"), ("blocked_pending", "blocked_pending_monthly"), ("resiliency_investment", "resiliency_investment_monthly")):
        s = sum(a["savings_monthly_usd"] or 0 for a in A if a["savings_basis"] == basis)
        chk(abs(s - S["totals"][key]) <= 1, f"totals.{key} equals sum of {basis} actions")
    # 2b. the check above compares the in-memory S against itself, which can never catch a bug between
    # Plan.build() and the artifact actually delivered (a render/serialization bug, or post-write
    # tampering). Re-derive the same headline figures from the rendered plan JSON *on disk* - summing
    # its own leaf-level action rows is independent of whatever path produced its single cached
    # totals field, and will catch an aggregation or on-disk corruption bug even without re-deriving
    # from raw pscli bytes.
    plan_files = [Path(p) for p in files if Path(p).suffix == ".json" and "optimization-plan" in Path(p).name]
    if plan_files:
        rendered = json.loads(plan_files[0].read_text(encoding="utf-8"))
        rA, rT = rendered.get("actions", []), rendered.get("totals", {})
        for basis, key in (("achievable_now", "achievable_now_monthly"), ("blocked_pending", "blocked_pending_monthly")):
            s = sum(a.get("savings_monthly_usd") or 0 for a in rA if a.get("savings_basis") == basis)
            chk(abs(s - (rT.get(key) or 0)) <= 1, f"rendered plan JSON: totals.{key} equals independently summed {basis} actions")
    ad = S["adoption"]
    chk(abs(ad["achievable_now"]["adjusted"] - S["totals"]["achievable_now_monthly"] * ad["factor"]) <= 1, "adoption: achievable adjusted = full x factor")
    pb = ad["blocked_pending"]
    chk(abs(pb["estimate"]["full"] + pb["ceiling"]["full"] - S["totals"]["blocked_pending_monthly"]) <= 2, "adoption: pending splits into estimate + ceiling")
    for basis in ("achievable_now", "blocked_pending", "resiliency_investment"):
        s = sum(S["fit_split"][basis][k]["usd"] for k in ("PodFit", "InfraFit"))
        chk(abs(s - S["totals"][basis + "_monthly"]) <= 2, f"fit split sums to {basis}")
    chk(all(a.get("fit") in ("PodFit", "InfraFit", "Governance") for a in A), "every action tagged PodFit/InfraFit/Governance")
    o = S["outlook"]
    chk(o["low"]["usd"] <= o["planning"]["usd"] <= o["maximum"]["usd"], "outlook states ordered low <= planning <= maximum")
    chk(abs(o["maximum"]["usd"] - (S["totals"]["achievable_now_monthly"] + S["totals"]["blocked_pending_monthly"])) <= 2, "outlook maximum = achievable + pending")
    claimed = [d["usd"] for d in S["decision_detail"] if d["kind"] in ("saving", "idle ceiling") and d["usd"]]
    chk(claimed == sorted(claimed, reverse=True), "claimed-dollar decisions ranked by dollars unlocked")
    seq = [(d["kind"] in ("saving", "idle ceiling") and bool(d["usd"])) for d in S["decision_detail"]]
    chk(seq == sorted(seq, reverse=True), "claimed-dollar decisions listed before idle-at-stake decisions")
    for c in S["cluster_idle"]:
        chk(c["driver_usd"] <= c["idle"] + 1, f"{c['cluster']}: idle driver within idle")
    # 3. no double counting of node idle: claimed pools cannot exceed idle
    chk(S["idle"]["claimed_long_lived"] + S["idle"]["auto_provisioned"] <= S["idle"]["total"] + 1, "claimed idle within total idle")
    chk(S["totals"]["blocked_pending_monthly"] <= S["idle"]["total"] + sum(f["pools"]["ll_rec_total"] for f in facts) + 1, "pending blockers bounded by idle plus recommendations")
    # 4. savings never exceed what PerfectScale computed
    chk(S["totals"]["achievable_now_monthly"] <= S["pod"]["potential"] + 1, "achievable <= pod potential")
    # 5. dependency graph sane: deps exist, earlier in order, not later phase
    pos = {a["id"]: i for i, a in enumerate(A)}
    for a in A:
        for d in a["depends_on"]:
            chk(d in pos and pos[d] < pos[a["id"]] and A[pos[d]]["phase"] <= a["phase"], f"{a['id']} depends on {d} correctly ordered")
    chk(len({a["id"] for a in A}) == len(A), "action ids unique")
    for a in A:
        chk(a["effort_tier"] in ("XS", "S", "M", "L") and a["confidence"] in ("high", "medium", "low") and len(a["steps"]) >= 3 and bool(a["verification"]), f"{a['id']} has tier, confidence, steps, verification")
    # 6. resiliency ranges ordered
    for a in A:
        r = a.get("range")
        if r:
            chk(r["low"] <= r["likely"] <= r["literal"], f"{a['id']} range ordered")
    # 7. wording gates and leak scan over every rendered text file
    names = list(PLACEHOLDER_TENANTS) + [str(n) for n in (cfg["foreign_tenants"] or [])]
    # Exempt a name only when it IS the current customer's own name (exact, case-insensitive match) -
    # not whenever it happens to be a substring of it. A past customer "Acme" must still be flagged
    # for a current customer "Acme Industries"; only "Acme" itself (== "Acme Industries") is exempt.
    foreign = [n for n in dict.fromkeys(names) if n.lower() != cfg["customer"].lower()]
    for p in files:
        p = Path(p)
        if p.suffix not in (".md", ".html", ".json", ".csv"):
            continue
        t = p.read_text(encoding="utf-8")
        for pat in BANNED:
            m = re.search(pat, t, re.I)
            chk(not m, f"{p.name}: no banned wording {pat}")
        for n in foreign:
            chk(not re.search(rf"\b{re.escape(n)}\b", t, re.I), f"{p.name}: no foreign tenant name {n}")
        m = re.search(ABS_PATH, t)
        chk(not m, f"{p.name}: no absolute filesystem path" + (f" ({m.group(0)})" if m else ""))
        if p.suffix == ".html":
            chk(not re.search(r"""(?:\bsrc=|<link\b[^>]*\bhref=)["']https?://""", t), f"{p.name}: HTML self-contained (no external resources; plain <a> links allowed)")
            chk("<script src" not in t, f"{p.name}: no external scripts")
            if "<table" in t:
                # The table controls are standard in rendered HTML, not an extra.
                # Each line below is something that silently stops working if the
                # markup, CSS or script drifts.
                chk("Show columns" in t and "Autofit selected" in t and "Reset to default" in t,
                    f"{p.name}: tables carry the column picker with autofit and reset")
                chk("col-resize" in t, f"{p.name}: columns are drag-resizable")
                chk("tableLayout='fixed'" in t, f"{p.name}: column widths are frozen after measuring")
                chk("beforebegin" in t, f"{p.name}: the table control bar sits above its table")
                chk(not re.search(r"(?:^|[,{;}])\s*th\b[^{};]*\{[^{}]*white-space:\s*nowrap", t),
                    f"{p.name}: header cells wrap instead of overlapping the next column")
            if "optimization-plan" in p.name:
                # Collapsible sections, same contract as the table controls: each line is
                # something that silently stops working if the markup, CSS or script drifts.
                # Scoped to the plan: the executive summary is a deliberate one-pager and
                # has nothing to collapse.
                chk("sechd" in t and "aria-expanded" in t,
                    f"{p.name}: h2 sections are collapsible toggles")
                chk("Expand all" in t and "Collapse all" in t,
                    f"{p.name}: sections carry expand-all and collapse-all")
                chk("hashchange" in t,
                    f"{p.name}: a link into a collapsed section opens it")
                chk(re.search(r"@media print\{[^}]*\.sec\[hidden\]\{display:block", t),
                    f"{p.name}: collapsed sections print expanded")
                # Actions table filters: present, and the page opens on Phase 1 only.
                pm = re.search(r"<div class=menu data-f=phase hidden>(.*?)</div></div>", t, re.S)
                chk("class=afilt" in t and "id=actions" in t and pm is not None, f"{p.name}: Actions table carries phase/topic/cluster/lane filters")
                if pm:
                    on = re.findall(r"value='(\d+)' checked", pm.group(1))
                    chk(on == ["1"], f"{p.name}: Actions table opens on Phase 1 only")
                # the resizer measures with getBoundingClientRect, which reads 0 inside a
                # hidden section, so collapse must initialise after it
                chk(t.find("JS_COLLAPSE") == -1 and t.rfind("var CLOSED=") > t.rfind("var MIN=44"),
                    f"{p.name}: sections collapse after column widths are measured")
        # (A `"nan" not in re.findall(r"\$[^\s|]*", t)` check used to sit here too: findall with that
        # pattern only ever returns tokens starting with "$", so a bare "nan" could never appear in its
        # result - provably dead. The "$nan"/"$inf" checks below are the real, live coverage.)
        chk("$nan" not in t and "$inf" not in t, f"{p.name}: no NaN/inf dollars")
        if "optimization-plan" in p.name and p.suffix in (".md", ".html"):
            # Platform-managed namespaces (kube-system and similar) get their own appendix,
            # structurally separate from the main by-namespace table - not an inline tag a
            # reader could miss. Match the actual table-row markup (not a bare substring -
            # the legend text itself names "kube-system" as an example, which a substring
            # check would wrongly catch), so a managed namespace's own row must never appear
            # in the main table and must appear in its own section's table.
            managed_names = [r["ns"] for r in (S.get("namespace_rollup") or {}).get("rows", []) if r["managed"]]
            if managed_names and "Appendix: platform-managed add-ons" in t:
                mg_mark = "Appendix: platform-managed add-ons"
                main_span = t.split(mg_mark, 1)[0]
                mg_span = t.split(mg_mark, 1)[1]
                for ns in managed_names:
                    row_mark = f"| {ns} |" if p.suffix == ".md" else f">{ns}</button>"
                    chk(row_mark not in main_span, f"{p.name}: managed namespace {ns}'s row is not in the main by-namespace table")
                    chk(row_mark in mg_span, f"{p.name}: managed namespace {ns}'s row appears in the platform-managed add-ons appendix")
        if "pov-automation-kickoff" in p.name and p.suffix == ".html":
            # The POV kickoff page: its ranked rows must match the model, nothing the model held
            # out may appear as a row, and the CR pieces must carry the keepLimit review comment.
            from .pov import pov_fname  # lazy: pov imports render, which this module need not load
            f = next((x for x in facts if pov_fname(cfg, x) == p.name), None)
            chk(f is not None, f"{p.name}: POV kickoff page maps to a cluster in this run")
            if f is None:
                continue
            rows = (f.get("pov") or {}).get("rows", [])
            # The ranked workloads live in one table per wave group; read them all, not just the first.
            body = "".join(re.findall(r"<table class='sort povgrp[^']*'[^>]*>(.*?)</table>", t, re.S))
            chk(body.count("<tr data-type=") == len(rows), f"{p.name}: POV table holds exactly the {len(rows)} ranked rows")
            chk(body.count("class=wlck value=") == len(rows),
                f"{p.name}: every ranked workload has its own in-scope checkbox")
            held = [r for r in rows if r.get("wave") in ("Hold", "Review")]
            chk(all(f"class=wlck value='{r['rank']}' checked" not in body for r in held),
                f"{p.name}: no Hold or Review workload ({len(held)}) is pre-ticked")
            chk(all(r["lane"] not in ("managed", "held_out") for r in rows),
                f"{p.name}: no platform-managed or held-out workload is ranked")
            m = re.search(r"<script type=application/json id=povdata>(.*?)</script>", t, re.S)
            chk(m is not None, f"{p.name}: POV page embeds its CR data")
            if m:
                D = json.loads(m.group(1).replace("<\\/", "</"))
                chk(len(D["rows"]) == len(rows), f"{p.name}: CR data covers every ranked row")
                lim = [b for b in D["type_block"].values()] + [r["wl_doc"] for r in D["rows"]]
                chk(all(any(x.strip().startswith("keepLimit: false  # REVIEW") for x in b) for b in lim),
                    f"{p.name}: every generated CR sets keepLimit false with an inline REVIEW comment")
                chk(all(not any("increaseEnabled: true" in x for x in b) for b in lim),
                    f"{p.name}: generated CRs are cost-only (no request or limit increases)")
                # PerfectScale's namespace/workload templates spell out the whole operational block;
                # a CR carrying only automationMode + restrictions leaves the customer guessing at
                # frequencies, maintenance window and runaway limits.
                need = ("automationStrategy:", "podResizePendingStrategy:", "duringRolloutInProgress:",
                        "wasteMaxAutomationFrequency:", "fixResiliencyMaxAutomationFrequency:", "maintenanceWindow:",
                        "maintenanceWindowIgnoredMinResiliencyLevel:", "workloadMinWasteUSDPerMonth:",
                        "cpuRunawayDetection:", "memoryLeakDetection:", "minimumCores:", "maximumGiB:")
                chk(all(all(any(x.strip().startswith(k) for x in b) for k in need) for b in lim),
                    f"{p.name}: every generated CR carries the full operational block of PerfectScale's templates")
                # Namespace picker: a platform-managed namespace is shown but can never be generated,
                # and every namespace exclusion is a Disabled config, never an enabling one.
                model_ns = (f.get("pov") or {}).get("namespaces", [])
                managed = [n["ns"] for n in model_ns if n["managed"]]
                d_ns = {n["ns"] for n in D.get("namespaces", [])}
                chk(not (d_ns & set(managed)), f"{p.name}: no platform-managed namespace can be generated")
                chk(all(f"class=nsck value='{html.escape(ns)}' disabled" in t for ns in managed),
                    f"{p.name}: platform-managed namespaces are listed with their checkbox disabled")
                chk(d_ns == {n["ns"] for n in model_ns if not n["managed"] and not n.get("held")},
                    f"{p.name}: the namespace picker lists every namespace the model found automatable")
                # A namespace whose every workload is kept out (stateful, JVM, held out) must still be visible
                # - greyed, with its reason - and can never be generated. Dropping it once hid a tenant's
                # largest namespace with no explanation on the page.
                held = [n["ns"] for n in model_ns if n.get("held")]
                chk(not (d_ns & set(held)), f"{p.name}: no fully kept-out namespace can be generated")
                chk(all(f"class=nsck value='{html.escape(ns)}' disabled" in t for ns in held),
                    f"{p.name}: fully kept-out namespaces are listed, greyed, with their checkbox disabled")
                # Workloads whose HPA scales on pod CPU/memory are automated with PerfectScale's HPA automation
                # alongside: each such namespace offers HPA automation, on by default, and the page explains
                # the prerequisites and keeps the read-only commands for a customer who agrees to share specs.
                hpa_ns = [n["ns"] for n in model_ns if not n["managed"] and (n.get("hpa") or {}).get("n")]
                chk(all(f"class=hpaon value='{html.escape(ns)}' checked" in t for ns in hpa_ns),
                    f"{p.name}: every namespace with pod CPU/memory HPAs offers HPA automation, ticked by default")
                chk(not hpa_ns or all(x in t for x in ("<h2>HPA automation</h2>", "id=hpaagent", "id=hpaexporter",
                                                         "kubectl get hpa,pdb --all-namespaces -o json")),
                    f"{p.name}: HPA automation section with prerequisites and the optional read-only commands")
                chk(all(r.get("wave") != "Hold" for r in rows), f"{p.name}: no workload is put on Hold for its HPA")
                ex = [x["doc"] for n in D.get("namespaces", []) for x in n["excl"]]
                chk(all('      automationMode: "Disabled"' in d and not any("Enabled" in y for y in d) for d in ex),
                    f"{p.name}: every namespace exclusion is a Disabled workload config")
    # Savings goal vs data (psplan/goal.py): recomputed from the facts, not read back from the plan,
    # and the executive summary must show it when, and only when, a goal is configured.
    from . import goal as goalmod
    G = S.get("goal")
    es = [Path(p) for p in files if Path(p).suffix == ".html" and "executive-summary" in Path(p).name]
    es_t = es[0].read_text(encoding="utf-8") if es else ""
    title = "Savings goal vs what the data supports"
    if es:
        # Categories are rolled up: every h2 sits in a <details> that starts closed, with expand/collapse
        # all, and printing opens them (a closed <details> would otherwise print as headings only).
        n_h2 = es_t.count("<h2>")
        chk(n_h2 > 0 and es_t.count('<details class="sec"><summary><h2>') == n_h2,
            f"{es[0].name}: every section is a collapsible category")
        chk("<details class=\"sec\" open" not in es_t, f"{es[0].name}: every category starts closed")
        chk("Expand all" in es_t and "beforeprint" in es_t, f"{es[0].name}: categories expand all at once and open for print")
        # A POV runs 30 days: the next look is on demand or at 30-day maturity, never a fixed far date.
        chk("re-measure on" not in es_t.lower() and "revisit these figures on demand" in es_t,
            f"{es[0].name}: promises a revisit on demand or at 30-day maturity, not a fixed re-run date")
    if cfg.get("savings_goal"):
        chk(G is not None, "savings goal: the plan carries the goal check")
        by = {f["name"]: f for f in facts}
        for r in (G or {}).get("rows", []):
            f = by[r["name"]]
            share = float(cfg.get("goal_idle_share", 0.5))
            rs, idle = f["pot"], f["pools"]["idle"]
            chk(abs(r["rightsizing"] - rs) <= 1 and abs(r["idle"] - idle) <= 1,
                f"savings goal {r['name']}: right-sizing and idle match the cluster facts")
            chk(r["conservative"] <= r["realistic"] <= r["maximum"],
                f"savings goal {r['name']}: conservative <= realistic <= maximum")
            want = goalmod.verdict(r["goal_low"], rs * float(cfg["adoption_factor"]), rs + share * idle, rs + idle)
            chk(r["verdict"] == want, f"savings goal {r['name']}: verdict '{r['verdict']}' follows from the numbers")
        if es:
            blk = es_t.split(f"<h2>{title}</h2>", 1)
            chk(len(blk) == 2, f"{es[0].name}: carries the savings goal section")
            if len(blk) == 2:
                body = blk[1].split("<h2>", 1)[0]
                chk(all(f"<b>{html.escape(r['name'])}</b>" in body for r in (G or {}).get("rows", [])),
                    f"{es[0].name}: every goal cluster has its own bar")
    else:
        chk(G is None and title not in es_t, "savings goal: none configured, none shown")
    return ok, errs
