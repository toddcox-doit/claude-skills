#!/usr/bin/env python3
"""Rank every cluster in a tenant by potential max savings, before you commit to a scope.

  python3 rank_clusters.py --profile <pscli-profile> --customer <Name> [--top 10] [--root .]

Writes `data/rank/ranking.json` plus a markdown table under `reports/`. Serialised
with a global pace and exponential backoff on 429. Resumable: a cluster whose
files already exist is not re-fetched, so re-run it after an interruption.

This is the step that decides which clusters get the full analysis. Run it first
on any tenant with more clusters than you intend to analyse.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

PACE = 1.0            # seconds between calls
MAX_TRIES = 6
SCALE_TO = 30.0       # the API's cost window, in days
MIN_HISTORY = 14.0    # below this a window is flagged as an early baseline


def is_rate_limited(p) -> bool:
    """True only for a real throttle: an error envelope or an explicit 429.

    Never a substring match on the payload - a cost of $429.10 is not a rate limit.
    """
    err = (p.stderr or "").lower()
    if "too many requests" in err or "rate limit" in err or "status 429" in err:
        return True
    if (p.stdout or "").lstrip()[:1] == "{":
        try:
            e = (json.loads(p.stdout).get("error") or {})
            if str(e.get("code", "")).upper() in ("RATE_LIMITED", "TOO_MANY_REQUESTS", "RESOURCE_EXHAUSTED"):
                return True
            if e.get("retryable") and "rate" in str(e.get("message", "")).lower():
                return True
        except (ValueError, AttributeError):
            pass
    return False


def run(args, dest: Path):
    """Run a pscli command, caching to `dest`. Retries only on genuine throttling."""
    if dest.exists():
        return dest.read_text()
    delay = 2.0
    for attempt in range(1, MAX_TRIES + 1):
        time.sleep(PACE)
        p = subprocess.run(["pscli", *args], capture_output=True, text=True)
        if p.returncode == 0 and not is_rate_limited(p):
            # An empty body on a clean exit is a real answer: the cluster reports
            # nothing for this window. Cache it rather than retrying forever.
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(p.stdout)
            return p.stdout
        if attempt == MAX_TRIES:
            raise SystemExit(f"pscli {' '.join(args[:2])} failed after {MAX_TRIES} tries: {(p.stderr or '')[:200]}")
        print(f"    retrying in {delay:.0f}s", file=sys.stderr)
        time.sleep(delay)
        delay *= 2


def ts(v):
    return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc) if v else None


def collect(root: Path):
    rank = root / "data" / "rank"
    rank.mkdir(parents=True, exist_ok=True)
    clusters = json.loads(run(["clusters", "list", "-o", "json"], rank / "clusters.json"))
    real = [c for c in clusters if c.get("uid")]
    ghosts = [c for c in clusters if not c.get("uid")]
    rows = []
    for i, c in enumerate(real, 1):
        uid, name = c["uid"], c["name"]
        d = rank / uid
        print(f"[{i}/{len(real)}] {name} {uid[:8]}", file=sys.stderr)
        wl = [json.loads(l) for l in run(["workloads", "list", "-c", uid, "-o", "jsonl"],
                                         d / "workloads.jsonl").splitlines() if l.strip()]
        sm_txt = run(["workloads", "summary", "-c", uid, "-o", "json"], d / "summary.json")
        ng = [json.loads(l) for l in run(["nodegroups", "list", "-c", uid, "--all", "-o", "jsonl"],
                                         d / "nodegroups.jsonl").splitlines() if l.strip()]
        summary = json.loads(sm_txt) if sm_txt.strip() else {}

        live = [w for w in wl if not (w.get("mute_status") or {}).get("is_muted")]
        firsts = [ts(w.get("first_seen")) for w in live if w.get("first_seen")]
        lasts = [ts(w.get("last_seen")) for w in live if w.get("last_seen")]
        window = max((max(lasts) - min(firsts)).total_seconds() / 86400.0, 0.25) if firsts and lasts else 0.0
        factor = SCALE_TO / window if 0 < window < SCALE_TO else 1.0

        cost = sum(w.get("cost") or 0 for w in live)
        waste = sum(w.get("waste") or 0 for w in live)
        pod = sum(w.get("potential_savings") or 0 for w in live)
        idle = sum(((g.get("cost") or {}).get("idle_total") or 0) for g in ng)
        rec = 0.0
        for g in ng:
            r = g.get("recommendations") or {}
            if r.get("type") == "standard":
                for nt in r.get("node_type_recommendations") or []:
                    rec += max(nt.get("estimated_savings") or 0, 0)

        rows.append({
            "name": name, "uid": uid, "created_at": c.get("created_at"),
            "workloads": len(live), "muted": len(wl) - len(live), "nodegroups": len(ng),
            "window_days": round(window, 1), "factor": round(factor, 3),
            "early": 0 < window < MIN_HISTORY,
            "cost_o": cost, "waste_o": waste, "podfit_o": pod, "idle_o": idle,
            "cost": cost * factor, "waste": waste * factor,
            "podfit": pod * factor, "idle": idle * factor,
            "nodegroup_rec_monthly": rec,
            "potential_max_o": pod + idle, "potential_max": (pod + idle) * factor,
            "api_summary": {k: summary.get(k) for k in
                            ("workloads", "total_cost", "total_waste", "total_potential_saving")},
        })

    rows.sort(key=lambda r: -r["potential_max"])
    for n, r in enumerate(rows, 1):
        r["rank"] = n
    out = {"generated": datetime.now(timezone.utc).isoformat(),
           "clusters_listed": len(clusters), "clusters_with_uid": len(real),
           "clusters_without_uid": len(ghosts),
           "ghost_names": sorted({g["name"] for g in ghosts}),
           "rows": rows}
    (rank / "ranking.json").write_text(json.dumps(out, indent=2))
    return out


def markdown(D, customer, top):
    rows, L = D["rows"], []
    a = L.append
    tot = sum(r["potential_max"] for r in rows) or 1
    a(f"# {customer} - PerfectScale cluster ranking")
    a("")
    a(f"Prepared {date.today().isoformat()} | window 30d | source: PerfectScale public API")
    a("")
    a("## How clusters were ranked")
    a("")
    a("Potential max savings is the sum of two separate bases, shown apart because they")
    a("are not interchangeable and must not be claimed as one number:")
    a("")
    a("- **PodFit potential** - the sum of per-workload potential savings, reconciled to")
    a("  the workload list itself (no group-by rollups), muted workloads excluded.")
    a("- **InfraFit idle** - node group idle cost. A pool-level **upper bound**, not a")
    a("  computed saving; the realisable share depends on what can actually be drained.")
    a("")
    a("The combined figure orders the list. It is a sizing signal for where to look")
    a("first, not a savings commitment. Node group recommendation totals are collected")
    a("but excluded from the ranking, so no cluster is double-counted.")
    a("")
    a("Costs labelled 30d are spend over the window each cluster has been connected.")
    a("Where that window is under 30 days the figures are scaled to a comparable monthly")
    a(f"run rate; a window under {int(MIN_HISTORY)} days is flagged as an early baseline.")
    a("")
    a("## Ranked clusters")
    a("")
    a("Savings columns are scaled monthly run rate.")
    a("")
    a("| # | Cluster | UID | Workloads | History (d) | Cost/mo | PodFit | InfraFit idle | Potential max | Top |")
    a("|---:|---|---|---:|---:|---:|---:|---:|---:|:--:|")
    for r in rows:
        hist = f"{r['window_days']:.1f}" + ("*" if r["factor"] > 1.0 else "") + ("!" if r["early"] else "")
        a("| {rank} | {name} | `{uid}` | {wl} | {hist} | ${cost:,.0f} | ${pod:,.0f} | ${idle:,.0f} | "
          "**${pm:,.0f}** | {m} |".format(rank=r["rank"], name=r["name"], uid=r["uid"], wl=r["workloads"],
                                          hist=hist, cost=r["cost"], pod=r["podfit"], idle=r["idle"],
                                          pm=r["potential_max"], m="**yes**" if r["rank"] <= top else ""))
    a("")
    a("`*` window under 30 days, scaled to a run rate. `!` under "
      f"{int(MIN_HISTORY)} days: early baseline, treat with care.")
    a("")
    scaled = [r for r in rows if r["factor"] > 1.0]
    if scaled:
        a("## Observed versus scaled")
        a("")
        a("| Cluster | History (d) | Factor | Cost obs | Cost scaled | Potential max obs | Potential max scaled |")
        a("|---|---:|---:|---:|---:|---:|---:|")
        for r in scaled:
            a(f"| {r['name']} | {r['window_days']:.1f} | {r['factor']:.3f} | ${r['cost_o']:,.0f} | "
              f"${r['cost']:,.0f} | ${r['potential_max_o']:,.0f} | ${r['potential_max']:,.0f} |")
        a("")
        a("Every other cluster has a full window; observed equals scaled.")
        a("")
    sel = rows[:top]
    a(f"## Proposed top {top} for full analysis")
    a("")
    a("| # | Cluster | UID | Potential max /mo | Share of ranked total |")
    a("|---:|---|---|---:|---:|")
    for r in sel:
        a(f"| {r['rank']} | {r['name']} | `{r['uid']}` | ${r['potential_max']:,.0f} | "
          f"{r['potential_max'] / tot * 100:.1f}% |")
    a("")
    s = sum(r["potential_max"] for r in sel)
    a(f"Top {top} potential max: **${s:,.0f}/mo** ({s / tot * 100:.1f}% of the ranked estate's ${tot:,.0f}/mo).")
    a("")
    a("## Inventory notes")
    a("")
    a(f"The API returned **{D['clusters_listed']} cluster records**: "
      f"**{D['clusters_with_uid']} with a UID** that can be queried, and "
      f"**{D['clusters_without_uid']} with an empty UID** that return no data.")
    if D["clusters_without_uid"]:
        a("")
        a("Empty-UID records are registrations that never completed. They cannot be")
        a("queried and are excluded from the ranking - inferred, cause not confirmed.")
        a(f"Affected names: {', '.join('`' + n + '`' for n in D['ghost_names'])}.")
    dupes = sorted({r["name"] for r in rows if sum(1 for x in rows if x["name"] == r["name"]) > 1})
    if dupes:
        a("")
        a("These names each resolve to more than one live cluster, so they must be")
        a(f"addressed by UID: {', '.join('`' + n + '`' for n in dupes)}. Querying by name")
        a("returns an ambiguity error.")
    empty = [r["name"] for r in rows if r["workloads"] == 0]
    if empty:
        a("")
        a(f"Reporting no workloads and no cost: {', '.join('`' + n + '`' for n in empty)}. "
          "Either decommissioned or no longer reporting - inferred, cause not confirmed.")
    a("")
    a("## Data quality")
    a("")
    a("Per-cluster PodFit totals are recomputed from the workload list and compared")
    a("against the API's own cluster summary:")
    a("")
    a("| Cluster | Workloads (mine / API) | PodFit (mine / API) | Delta |")
    a("|---|---|---|---:|")
    for r in rows:
        api = r["api_summary"].get("total_potential_saving")
        apn = r["api_summary"].get("workloads")
        if api is None:
            continue
        d = r["podfit_o"] - api
        if abs(d) >= 0.01 or apn != r["workloads"]:
            a(f"| {r['name']} | {r['workloads']} / {apn} | ${r['podfit_o']:,.2f} / ${api:,.2f} | ${d:,.2f} |")
    a("")
    a("Rows appear only where the two differ. Sub-dollar deltas are server-side")
    a("aggregation rounding, not a reconciliation gap.")
    a("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--customer", required=True, help="name as it should appear on the ranking")
    ap.add_argument("--profile", help="pscli profile (else PERFECTSCALE_PROFILE)")
    ap.add_argument("--top", type=int, default=10, help="how many clusters to mark for full analysis")
    ap.add_argument("--root", default=".", help="tenant working folder (holds data/ and reports/)")
    a = ap.parse_args()
    if a.profile:
        os.environ["PERFECTSCALE_PROFILE"] = a.profile
    if not os.environ.get("PERFECTSCALE_PROFILE"):
        raise SystemExit("set --profile or PERFECTSCALE_PROFILE")
    root = Path(a.root).resolve()
    D = collect(root)
    out = root / "reports" / f"{a.customer.lower().replace(' ', '-')}-cluster-ranking-{date.today().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown(D, a.customer, a.top))
    print(f"\nranked {len(D['rows'])} clusters; wrote {out}")
    print(f"top {a.top}:")
    for r in D["rows"][:a.top]:
        print(f"  {r['rank']:>2}  {r['name']:<34} ${r['potential_max']:,.0f}/mo  {r['uid']}")
    print(f"\nAgree the list with the customer, then write the config:")
    print(f"  python3 <skill>/scripts/new_tenant.py config --dir {a.root} --top {a.top}")
    print(f"  python3 <skill>/scripts/new_tenant.py config --dir {a.root} --clusters <name>,<name>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
