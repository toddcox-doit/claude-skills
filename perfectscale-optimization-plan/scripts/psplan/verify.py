"""Independent verifier: recomputes headline numbers from the raw files (not from the model) and runs wording gates."""
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
    # 2. money bases add up from actions, and are not mixed
    A = S["actions"]
    for basis, key in (("achievable_now", "achievable_now_monthly"), ("blocked_pending", "blocked_pending_monthly"), ("resiliency_investment", "resiliency_investment_monthly")):
        s = sum(a["savings_monthly_usd"] or 0 for a in A if a["savings_basis"] == basis)
        chk(abs(s - S["totals"][key]) <= 1, f"totals.{key} equals sum of {basis} actions")
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
    foreign = [n for n in dict.fromkeys(names) if n.lower() not in cfg["customer"].lower()]
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
        chk("nan" not in re.findall(r"\$[^\s|]*", t) and "$nan" not in t and "$inf" not in t, f"{p.name}: no NaN/inf dollars")
    return ok, errs
