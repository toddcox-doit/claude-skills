#!/usr/bin/env python3
"""Set up a tenant working folder, and write its config from an approved ranking.

Two steps, in this order.

  # 1. create the folder and copy the library into it
  python3 new_tenant.py init --customer "Acme" --profile acme --dir ~/work/Acme_analysis

  # 2. after running the ranking and agreeing the scope, write the config
  python3 new_tenant.py config --dir ~/work/Acme_analysis --top 10
  python3 new_tenant.py config --dir ~/work/Acme_analysis --clusters prod-a,prod-b

`init` copies only the reusable library: scripts, templates, references. It never
copies another tenant's config, data or reports.

`config` reads `data/rank/ranking.json` so cluster names and UIDs are taken from
what the API actually returned rather than retyped. Clusters are written in
descending savings order, and that order carries through every output.
"""
import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent          # the skill folder
COPY = ["scripts", "templates", "references"]
SKIP = {"__pycache__", ".DS_Store"}


def slug(s):
    return "".join(c if c.isalnum() else "-" for c in s.lower()).strip("-")


def cmd_init(a):
    root = Path(a.dir).expanduser().resolve()
    dest = root / "skill" / LIB.name
    if dest.exists() and not a.force:
        raise SystemExit(f"{dest} already exists; pass --force to refresh the library in place")
    for sub in ("config", "data", "reports"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    dest.mkdir(parents=True, exist_ok=True)
    for d in COPY:
        src = LIB / d
        if not src.exists():
            continue
        shutil.copytree(src, dest / d, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(*SKIP))
    for f in ("SKILL.md",):
        if (LIB / f).exists():
            shutil.copy2(LIB / f, dest / f)
    # a stray __pycache__ from the source tree would shadow edits made here
    for p in dest.rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)

    print(f"created {root}")
    print(f"  library -> skill/{LIB.name}/ (scripts, templates, references)")
    print(f"  empty   -> config/ data/ reports/")
    print()
    print("Next:")
    print(f"  cd {root}")
    print(f"  export PERFECTSCALE_PROFILE={a.profile or '<profile>'}")
    print(f"  pscli auth status                      # confirm the right tenant")
    print(f"  python3 skill/{LIB.name}/scripts/rank_clusters.py "
          f"--customer \"{a.customer}\" --top 10")
    print(f"  # agree the scope, then:")
    print(f"  python3 skill/{LIB.name}/scripts/new_tenant.py config --dir . --top 10")
    return 0


def cmd_config(a):
    root = Path(a.dir).expanduser().resolve()
    ranking = root / "data" / "rank" / "ranking.json"
    if not ranking.exists():
        raise SystemExit(f"no ranking at {ranking}. Run rank_clusters.py first.")
    D = json.loads(ranking.read_text())
    rows = D["rows"]

    if a.clusters:
        want = [c.strip() for c in a.clusters.split(",") if c.strip()]
        chosen, missing = [], []
        for w in want:
            hit = [r for r in rows if r["name"] == w or r["uid"] == w]
            if not hit:
                missing.append(w)
            elif len(hit) > 1:
                raise SystemExit(f"'{w}' matches {len(hit)} clusters; use the UID instead")
            else:
                chosen.append(hit[0])
        if missing:
            raise SystemExit(f"not in the ranking: {', '.join(missing)}")
        chosen.sort(key=lambda r: -r["potential_max"])      # savings order, always
    else:
        chosen = rows[:a.top]

    tmpl = json.loads((LIB / "templates" / "tenant.example.json").read_text())
    customer = a.customer or root.name.replace("_analysis", "").replace("-", " ")
    cfg = dict(tmpl)
    # Never inherit a sample answer: an unasked question must stay null so the
    # plan states it as an open assumption instead of reporting a guess as fact.
    cfg["assumptions"] = {"scope": (tmpl.get("assumptions") or {}).get("scope", "entire cluster"),
                          "delivery": None, "gitops": None}
    cfg["intake"] = {"change_freeze": None, "approval_lead_time": None, "namespace_owners": None}
    cfg.update({
        "customer": customer,
        "prepared": date.today().isoformat(),
        "profile": a.profile or slug(customer),
        "cloud": a.cloud,
        "clusters": [{"name": r["name"], "uid": r["uid"]} for r in chosen],
        "data_dir": "data/plan-run",
        "out_dir": "reports/v1",
        "version_tag": "v1",
        "foreign_tenants": [t.strip() for t in (a.foreign or "").split(",") if t.strip()],
        "previous_plan": None,
    })
    dest = root / "config" / f"{slug(customer)}.tenant.json"
    if dest.exists() and not a.force:
        raise SystemExit(f"{dest} already exists; pass --force to overwrite")
    dest.write_text(json.dumps(cfg, indent=2) + "\n")

    early = [r for r in chosen if r.get("early")]
    scaled = [r for r in chosen if r.get("factor", 1) > 1]
    print(f"wrote {dest}")
    print(f"  {len(chosen)} clusters, in descending savings order:")
    for r in chosen:
        print(f"    {r['name']:<34} ${r['potential_max']:,.0f}/mo")
    if scaled:
        print(f"  scaled to a 30-day run rate (short window): {', '.join(r['name'] for r in scaled)}")
    if early:
        print(f"  EARLY BASELINE, under 14 days of history: {', '.join(r['name'] for r in early)}")
    print()
    print("Before collecting, review in the config:")
    print("  assumptions.gitops        ask, do not guess; null is stated as an open assumption")
    print("  stateful_patterns         add any search or streaming engine this tenant runs")
    print("  held_out_from_automation  name or namespace regexes to keep out of the ask")
    print("  managed_namespaces        platform add-ons to hold out")
    print("  foreign_tenants           other customers this folder has held, for the leak scan")
    print()
    print("Then:")
    print(f"  python3 skill/{LIB.name}/scripts/run_plan.py --config {dest.relative_to(root)} --collect")
    print(f"  python3 skill/{LIB.name}/scripts/run_plan.py --config {dest.relative_to(root)}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="create the folder and copy the library in")
    i.add_argument("--customer", required=True)
    i.add_argument("--dir", required=True)
    i.add_argument("--profile")
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_init)

    c = sub.add_parser("config", help="write the tenant config from the ranking")
    c.add_argument("--dir", required=True)
    c.add_argument("--customer")
    c.add_argument("--profile")
    c.add_argument("--cloud", default=None, help="aws | gcp | azure; detected from the data if omitted")
    c.add_argument("--top", type=int, default=10)
    c.add_argument("--clusters", help="explicit comma-separated names or UIDs, instead of --top")
    c.add_argument("--foreign", help="comma-separated other tenant names, for the leak scan")
    c.add_argument("--force", action="store_true")
    c.set_defaults(fn=cmd_config)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
