#!/usr/bin/env python3
"""Set up a tenant working folder, its credentials, and its config from an approved ranking.

Fastest path - one command, one credential entry:

  python3 new_tenant.py onboard --customer "Acme" --profile acme --dir ~/work/Acme_analysis
      creates the folder, asks for the tenant's PerfectScale API token once (pscli and the API v2
      step both use it), and ranks every cluster. Add --write-config to take the top --top
      clusters straight into a config, and --collect to pull their data too.

  python3 new_tenant.py auth --profile acme          # (re)enter the token for an existing tenant
  python3 new_tenant.py launcher --dir ~/work/Acme_analysis   # add the double-click kickoff launcher

Or step by step, in this order.

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
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent          # the skill folder
sys.path.insert(0, str(Path(__file__).resolve().parent))
from psplan import credentials  # noqa: E402
from psplan.apiv2 import load_credentials  # noqa: E402
COPY = ["scripts", "templates", "references"]
SKIP = {"__pycache__", ".DS_Store"}


def slug(s):
    return "".join(c if c.isalnum() else "-" for c in s.lower()).strip("-")


LAUNCHER = "Open kickoff pages.command"     # .command: macOS opens it in Terminal on double-click


def launcher_text():
    return f"""#!/bin/bash
# Double-click to open this customer's POV kickoff pages, with the one-click
# "Update the plan" button. Keep this window open while you use the pages;
# close it, or press Ctrl+C, when you are done.
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 was not found. Install Python 3.10 or newer, then double-click this again."
  read -n 1 -s -r -p "Press any key to close this window."
  exit 1
fi
python3 skill/{LIB.name}/scripts/kickoff.py "$@"
status=$?
if [ $status -ne 0 ]; then
  echo
  read -n 1 -s -r -p "kickoff stopped with an error (see above). Press any key to close this window."
fi
exit $status
"""


def write_launcher(root):
    """Put the double-click launcher at the top of a tenant folder. Rewritten every time, so a library
    refresh also refreshes it; executable, since Finder runs a .command file only if it is."""
    p = Path(root) / LAUNCHER
    p.write_text(launcher_text())
    p.chmod(0o755)
    return p


def cmd_launcher(a):
    root = Path(a.dir).expanduser().resolve()
    if not (root / "skill" / LIB.name / "scripts" / "kickoff.py").is_file():
        raise SystemExit(f"{root} has no copy of the library with kickoff.py; refresh it first "
                         f"(new_tenant.py init --dir {root} --customer <name> --force)")
    p = write_launcher(root)
    print(f"wrote {p}\n  double-click it in Finder to open the kickoff pages with the update button")
    return 0


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
    write_launcher(root)

    if getattr(a, "quiet", False):
        print(f"created {root}  (library copied into skill/{LIB.name}/)")
        return 0
    print(f"created {root}")
    print(f"  library -> skill/{LIB.name}/ (scripts, templates, references)")
    print(f"  empty   -> config/ data/ reports/")
    print(f"  {LAUNCHER} -> double-click to open the kickoff pages once a plan has run")
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


def _profile_from_dir(d):
    cfgs = sorted((Path(d).expanduser() / "config").glob("*.tenant*.json"))
    for c in cfgs:
        prof = (json.loads(c.read_text()) or {}).get("profile")
        if prof:
            return prof
    return None


def cmd_auth(a):
    profile = a.profile or (_profile_from_dir(a.dir) if a.dir else None)
    if not profile:
        raise SystemExit("name the tenant's pscli profile: --profile <name> (or --dir of a tenant folder with a config)")
    if not credentials.can_prompt():
        raise SystemExit("auth needs a terminal to ask for the token; run it in a terminal window")
    creds = credentials.setup(profile, login_pscli=not a.api_only)
    if not creds:
        raise SystemExit("no working token stored; nothing changed")
    print(f"Profile '{profile}' is ready: pscli and the API v2 step will both use this token.")
    return 0


def _access_ok(profile):
    ok_pscli, _ = credentials.pscli_status(shutil.which("pscli"), profile)
    creds, _ = load_credentials({"profile": profile})
    return ok_pscli, creds is not None


def cmd_onboard(a):
    root = Path(a.dir).expanduser().resolve()
    if (root / "skill" / LIB.name).exists() and not a.force:
        print(f"{root} already set up; keeping its library (pass --force to refresh it)")
    else:
        cmd_init(argparse.Namespace(customer=a.customer, dir=a.dir, profile=a.profile, force=a.force, quiet=True))
    ok_pscli, ok_api = _access_ok(a.profile)
    if not (ok_pscli and ok_api):
        missing = " and ".join(x for x, ok in (("pscli sign-in", ok_pscli), ("API v2 credentials", ok_api)) if not ok)
        if credentials.can_prompt():
            print(f"\nProfile '{a.profile}' needs {missing}. One token covers both.")
            if not credentials.setup(a.profile, login_pscli=not ok_pscli):
                raise SystemExit("no working token stored; run `new_tenant.py auth --profile "
                                 f"{a.profile}` when you have one, then onboard again")
        elif not ok_pscli:
            raise SystemExit(f"pscli profile '{a.profile}' is not signed in and there is no terminal to ask for a "
                             f"token; run `new_tenant.py auth --profile {a.profile}` in a terminal first")
        else:
            print(f"note: no API v2 credentials for '{a.profile}'; HPA data will be skipped until "
                  f"`new_tenant.py auth --profile {a.profile}` is run")
    lib = root / "skill" / LIB.name / "scripts"
    env = dict(os.environ, PERFECTSCALE_PROFILE=a.profile)
    print("\nRanking every cluster in the tenant ...")
    r = subprocess.run([sys.executable, str(lib / "rank_clusters.py"), "--customer", a.customer,
                        "--profile", a.profile, "--top", str(a.top), "--root", str(root)], env=env)
    if r.returncode:
        raise SystemExit("ranking failed; see the output above")
    if not (a.write_config or a.collect):
        print(f"\nReview the ranking with the customer, then:\n  python3 {lib / 'new_tenant.py'} config --dir {root} "
              f"--top {a.top}\n  (or --clusters a,b,c for an explicit scope)")
        return 0
    cmd_config(argparse.Namespace(dir=str(root), customer=a.customer, profile=a.profile, cloud=None, top=a.top,
                                  clusters=a.clusters, foreign=None, force=a.force))
    if a.collect:
        cfg = root / "config" / f"{slug(a.customer)}.tenant.json"
        print("\nCollecting ...")
        r = subprocess.run([sys.executable, str(lib / "run_plan.py"), "--config", str(cfg), "--collect"], env=env, cwd=root)
        return r.returncode
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

    au = sub.add_parser("auth", help="enter the tenant's API token once: pscli sign-in plus the API v2 step")
    au.add_argument("--profile", help="pscli profile name (else read from --dir's config)")
    au.add_argument("--dir", help="tenant working folder, to read the profile from its config")
    au.add_argument("--api-only", action="store_true", help="store for the API v2 step only; leave pscli as it is")
    au.set_defaults(fn=cmd_auth)

    o = sub.add_parser("onboard", help="folder + credentials + ranking in one go (optionally config and collect)")
    o.add_argument("--customer", required=True)
    o.add_argument("--profile", required=True)
    o.add_argument("--dir", required=True)
    o.add_argument("--top", type=int, default=10, help="clusters to mark, and to take into the config with --write-config")
    o.add_argument("--clusters", help="explicit comma-separated names or UIDs for the config, instead of --top")
    o.add_argument("--write-config", action="store_true", help="write the config from the ranking without stopping to review")
    o.add_argument("--collect", action="store_true", help="also collect the configured clusters (implies --write-config)")
    o.add_argument("--force", action="store_true")
    o.set_defaults(fn=cmd_onboard)

    lp = sub.add_parser("launcher", help=f"add the double-click '{LAUNCHER}' to an existing tenant folder")
    lp.add_argument("--dir", required=True)
    lp.set_defaults(fn=cmd_launcher)

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
