"""Savings goal vs what the data supports.

A POV plan states a savings target per cluster (or for the tenant). This module sets each target
against three numbers built only from figures the plan already holds, all at the 30-day run rate:

  conservative  = right-sizing potential x adoption_factor
  realistic     = right-sizing potential + goal_idle_share x node idle
                  (the high end: needs bin-packing so the autoscaler can actually remove nodes)
  maximum       = right-sizing potential + all node idle
                  (PerfectScale's theoretical ceiling: zero pod waste and zero idle, never reached)

and gives each target a verdict. Right-sizing frees requests; it reaches the bill only when nodes
leave, which is why the realistic high end leans on node idle and the conservative end does not.

Config (see references/tenant-config.md):

  "savings_goal": {
    "source": "POV plan v1, 11 Nov 2026, goal #1",
    "baseline_source": "Billing export or DoiT report (to be agreed)",
    "clusters": {"prod-a": {"monthly_usd": 9000},
                 "stg":    {"monthly_usd": [15000, 20000]},
                 "dev":    {"pct": 20, "baseline_usd": 30000}},
    "tenant": {"monthly_usd": 25000}            # optional, instead of or as well as clusters
  }

A range target is judged on its low end (the least the customer said would count) and drawn as a
band. A percentage target needs a baseline: the cluster's `baseline_usd` when given, otherwise the
PerfectScale-measured node cost, and the output says which.
"""

VERDICTS = (
    # key, label - judged on the low end of the goal, in this order of severity
    ("supported", "Supported"),          # within the conservative case
    ("stretch", "Stretch"),              # within the realistic case: needs node scale-down
    ("unlikely", "Unlikely"),            # between the realistic case and the theoretical maximum
    ("not_supported", "Not supported"),  # above the theoretical maximum
)
VERDICT = dict(VERDICTS)


def _num(v, what):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        raise SystemExit(f"config: savings_goal {what} must be a non-negative number, got {v!r}")
    return float(v)


def parse_target(spec, where):
    """-> (basis, lo, hi) with lo <= hi, in the unit given ('usd' per month or 'pct')."""
    if not isinstance(spec, dict):
        raise SystemExit(f"config: savings_goal {where} must be an object such as {{\"monthly_usd\": 9000}}")
    keys = [k for k in ("monthly_usd", "pct") if k in spec]
    if len(keys) != 1:
        raise SystemExit(f"config: savings_goal {where} needs exactly one of 'monthly_usd' or 'pct'")
    basis = "usd" if keys[0] == "monthly_usd" else "pct"
    v = spec[keys[0]]
    if isinstance(v, (list, tuple)):
        if len(v) != 2:
            raise SystemExit(f"config: savings_goal {where} range must be [low, high]")
        lo, hi = _num(v[0], where), _num(v[1], where)
    else:
        lo = hi = _num(v, where)
    if lo > hi:
        lo, hi = hi, lo
    if basis == "pct" and hi > 100:
        raise SystemExit(f"config: savings_goal {where} pct must be 0-100")
    if "baseline_usd" in spec:
        _num(spec["baseline_usd"], f"{where} baseline_usd")
    return basis, lo, hi


def validate(cfg):
    """Fail at config load, naming the bad entry, rather than deep in a render."""
    g = cfg.get("savings_goal")
    if not g:
        return
    if not isinstance(g, dict) or not (g.get("clusters") or g.get("tenant")):
        raise SystemExit("config: savings_goal needs 'clusters' and/or 'tenant'")
    names = {c["name"] for c in cfg["clusters"]}
    for name, spec in (g.get("clusters") or {}).items():
        if name not in names:
            raise SystemExit(f"config: savings_goal names cluster {name!r}, which is not in 'clusters'")
        parse_target(spec, f"cluster {name!r}")
    if g.get("tenant"):
        parse_target(g["tenant"], "tenant")
    share = cfg.get("goal_idle_share", 0.5)
    if not isinstance(share, (int, float)) or not 0 <= share <= 1:
        raise SystemExit("config: goal_idle_share must be between 0 and 1")


def verdict(lo, conservative, realistic, maximum):
    if lo <= conservative:
        return "supported"
    if lo <= realistic:
        return "stretch"
    if lo <= maximum:
        return "unlikely"
    return "not_supported"


def _row(name, spec, rightsizing, idle, node_cost, factor, share, where):
    basis, lo, hi = parse_target(spec, where)
    baseline = spec.get("baseline_usd")
    baseline_from = "agreed baseline" if baseline is not None else "PerfectScale-measured node cost"
    baseline = float(baseline) if baseline is not None else node_cost
    if basis == "pct":
        pct_lo, pct_hi = lo, hi
        lo, hi = baseline * lo / 100, baseline * hi / 100
    else:
        pct_lo = pct_hi = None
    cons = rightsizing * factor
    real = rightsizing + share * idle
    mx = rightsizing + idle
    v = verdict(lo, cons, real, mx)
    return {
        "name": name, "basis": basis, "goal_low": round(lo), "goal_high": round(hi),
        "goal_pct": [pct_lo, pct_hi] if basis == "pct" else None,
        "baseline_usd": round(baseline), "baseline_from": baseline_from,
        "node_cost": round(node_cost), "rightsizing": round(rightsizing), "idle": round(idle),
        "conservative": round(cons), "realistic": round(real), "maximum": round(mx),
        "goal_share_of_bill": (lo / node_cost) if node_cost else None,
        "verdict": v, "verdict_label": VERDICT[v],
        # how far the low end of the goal sits above the realistic case; 0 when it fits
        "gap_to_realistic": round(max(0.0, lo - real)),
        "coverage_realistic": (real / lo) if lo else None,
        "idle_share": share,
    }


def _usd(v):
    return "$" + f"{round(v):,}"


def _goal_text(r):
    return _usd(r["goal_low"]) + ("" if r["goal_high"] == r["goal_low"] else "-" + _usd(r["goal_high"])) + "/mo"


def sentence(r):
    """One plain sentence per goal, saying exactly what reaching it would take."""
    rs, idle, lo = r["rightsizing"], r["idle"], r["goal_low"]
    s = f"The {_goal_text(r)} goal "
    v = r["verdict"]
    if v == "supported":
        s += f"fits inside the conservative case of {_usd(r['conservative'])}/mo"
    elif lo <= rs:
        s += (f"needs {lo / rs * 100:.0f}% of the right-sizing recommendations applied, above the "
              f"{r['conservative'] / rs * 100:.0f}% conservative case")
    else:
        need = (lo - rs) / idle * 100 if idle else float("inf")
        if v == "stretch":
            s += (f"needs every right-sizing recommendation applied and {need:.0f}% of node idle removed, "
                  "which takes bin-packing so the autoscaler can remove nodes")
        elif v == "unlikely":
            s += (f"needs every right-sizing recommendation applied and {need:.0f}% of node idle removed, more than "
                  f"the {_share(r)} the realistic case assumes; it sits {_usd(r['gap_to_realistic'])}/mo above that case")
        else:
            s += (f"is above the most the data shows: every recommendation applied and every idle node removed "
                  f"comes to {_usd(r['maximum'])}/mo, {_usd(lo - r['maximum'])}/mo short")
    return s + "."


def _share(r):
    return f"{r['idle_share'] * 100:.0f}%"


def goal_check(cfg, facts):
    """-> None when no goal is configured, else the block the plan JSON carries as S['goal']."""
    g = cfg.get("savings_goal")
    if not g:
        return None
    factor = float(cfg["adoption_factor"])
    share = float(cfg.get("goal_idle_share", 0.5))
    by = {f["name"]: f for f in facts}
    rows = []
    for name, spec in (g.get("clusters") or {}).items():
        f = by.get(name)
        if f is None:          # configured but not collected this run: say so, never guess
            continue
        r = _row(name, spec, f["pot"], f["pools"]["idle"], f["pools"]["cost"], factor, share, f"cluster {name!r}")
        r["sentence"] = sentence(r)
        rows.append(r)
    missing = [n for n in (g.get("clusters") or {}) if n not in by]
    total = None
    if g.get("tenant"):
        fs = facts
        total = _row("All clusters", g["tenant"], sum(f["pot"] for f in fs), sum(f["pools"]["idle"] for f in fs),
                     sum(f["pools"]["cost"] for f in fs), factor, share, "tenant")
    elif len(rows) > 1:
        # Sum of the per-cluster goals against the sum of the same clusters' data. Each part is a
        # straight sum of run-rate figures, so the total is as sound as its rows.
        spec = {"monthly_usd": [sum(r["goal_low"] for r in rows), sum(r["goal_high"] for r in rows)]}
        fs = [by[r["name"]] for r in rows]
        total = _row("All POV clusters", spec, sum(f["pot"] for f in fs), sum(f["pools"]["idle"] for f in fs),
                     sum(f["pools"]["cost"] for f in fs), factor, share, "total")
    if total:
        total["sentence"] = sentence(total)
    judged = rows or ([total] if total else [])
    worst = max((r["verdict"] for r in judged), key=[k for k, _ in VERDICTS].index, default=None)
    return {
        "source": g.get("source"), "baseline_source": g.get("baseline_source"),
        "adoption_factor": factor, "idle_share": share,
        "rows": rows, "total": total, "missing_clusters": missing, "worst": worst,
        "takeaway": takeaway(rows, total),
        "method": (f"Conservative = right-sizing potential x {factor * 100:.0f}% adoption. "
                   f"Realistic = right-sizing potential + {share * 100:.0f}% of node idle, which needs bin-packing "
                   "so the autoscaler can remove nodes. Maximum = right-sizing potential + all node idle, "
                   "PerfectScale's ceiling of zero waste and zero idle, which no cluster reaches. "
                   "All figures are 30-day run rates from PerfectScale data; right-sizing frees requests and "
                   "reaches the bill only when nodes leave."),
    }


def takeaway(rows, total):
    judged = rows or ([total] if total else [])
    if not judged:
        return "No goal could be checked: none of the clusters named in the goal were collected in this run."
    n = len(judged)
    ok = [r for r in judged if r["verdict"] == "supported"]
    st = [r for r in judged if r["verdict"] == "stretch"]
    bad = [r for r in judged if r["verdict"] in ("unlikely", "not_supported")]
    if total and n > 1:
        tail = (f" Together the goals ask for {_goal_text(total)} against {_usd(total['conservative'])}-"
                f"{_usd(total['realistic'])}/mo the data supports, with {_usd(total['maximum'])}/mo the theoretical ceiling.")
    else:
        r = judged[0]
        tail = (f" The data supports {_usd(r['conservative'])}-{_usd(r['realistic'])}/mo against a "
                f"{_goal_text(r)} goal, with {_usd(r['maximum'])}/mo the theoretical ceiling.")
    if len(ok) == n:
        head = "The savings goal is supported by the data." if n == 1 else "Every cluster's savings goal is supported by the data."
    elif not bad:
        head = ("The savings goal is reachable, but only once bin-packing lets the autoscaler remove nodes."
                if n == 1 else "The goals are reachable, but some only once bin-packing lets the autoscaler remove nodes.")
    elif len(bad) == n:
        head = ("The savings goal is not supported by the data as it stands; reset it, or reframe it as return on investment."
                if n == 1 else
                "No cluster's savings goal is supported by the data as it stands; reset the targets, or reframe them as return on investment.")
    else:
        head = (f"{len(bad)} of {n} cluster goals sit above what the data supports: "
                + ", ".join(r["name"] for r in bad) + ". Reset those targets before the success review.")
    return head + tail
