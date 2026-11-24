#!/usr/bin/env python3
"""Cluster snapshots from PerfectScale's S3 export, pulled and digested for the plan in one command.

  python3 snapshots.py                                   # every cluster, the last 30 days, then digest
  python3 snapshots.py --tenant-id <uuid>                # first pull for a tenant (saved in its config)
  python3 snapshots.py --bucket s3://<bucket>/raw        # first run on this machine (saved for every tenant)
  python3 snapshots.py --days 60                         # a longer trailing window
  python3 snapshots.py --dry-run                         # list and count, fetch nothing
  python3 snapshots.py digest                            # re-digest what is already here

By default it takes the newest snapshot plus one per day over the trailing 30 days: enough to see
how replica and node counts move across a month, the same window the plan scales savings to.
Fewer than 14 days of snapshots (a newly connected cluster) is said so; the trends cover what exists.
The AWS CLI runs with your own profile (--profile to pick one). See psplan/snapshots.py.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from psplan import snapshots as S  # noqa: E402
from psplan.versioning import find_config, tenant_root  # noqa: E402


def save_tenant_id(cfgp, raw, tid):
    """Record the tenant UUID in the tenant config, changing nothing else."""
    tid = tid.strip().lower()
    if not S.UUID_RX.match(tid):
        raise SystemExit(f"--tenant-id {tid!r} is not a tenant UUID")
    if (raw.get("snapshots") or {}).get("tenant_id") == tid:
        return raw
    raw.setdefault("snapshots", {})
    raw["snapshots"] = dict(raw["snapshots"] or {}, tenant_id=tid)
    Path(cfgp).write_text(json.dumps(raw, indent=2) + "\n")
    print(f"saved the tenant id in {Path(cfgp).name}")
    return raw


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-"):
        argv = ["pull"] + argv                       # pulling is the default
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="the tenant config (default: the one in the tenant folder this copy lives in)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull", help="copy the trailing window's snapshots from S3, then digest them")
    p.add_argument("--days", type=int, default=S.DEFAULT_DAYS, help=f"trailing days to read (default {S.DEFAULT_DAYS})")
    p.add_argument("--tenant-id", help="the tenant's UUID; needed once per tenant, then read from the config")
    p.add_argument("--bucket", help="s3://<bucket>/raw; needed once per machine, then read from ~/.perfectscale")
    p.add_argument("--sample", default="latest,daily", help=f"comma list of {', '.join(S.SAMPLES)} (default latest,daily)")
    p.add_argument("--profile", help="AWS CLI profile (default: the CLI's own default)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-digest", action="store_true")
    p.add_argument("--config", dest="config_sub", help=argparse.SUPPRESS)
    d = sub.add_parser("digest", help="write data/snapshots/<cluster>/digest.json from what is already here")
    d.add_argument("--cluster", action="append", help="cluster name (default: every cluster with snapshots)")
    d.add_argument("--days", type=int, default=S.DEFAULT_DAYS)
    d.add_argument("--config", dest="config_sub", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    cfgp = find_config(__file__, a.config or getattr(a, "config_sub", None))
    raw, root = json.loads(Path(cfgp).read_text()), tenant_root(cfgp)
    names_with = lambda: [c["name"] for c in raw.get("clusters") or []  # noqa: E731
                          if (root / "data" / "snapshots" / c["name"]).is_dir()]
    if a.cmd == "pull":
        samples = tuple(s.strip() for s in a.sample.split(",") if s.strip())
        try:
            S.select(["2000-01-01-00-00"], samples)
        except ValueError as e:
            raise SystemExit(str(e))
        if a.bucket:
            print(f"saved the bucket for this machine in {S.save_machine_prefix(a.bucket)}")
        if a.tenant_id:
            raw = save_tenant_id(cfgp, raw, a.tenant_id)
        found = S.pull(raw, root, a.days, samples, a.profile, a.dry_run)
        if a.dry_run or a.no_digest:
            return 0
        names = [n for n in names_with() if found.get(n)]
        if not names:
            raise SystemExit("no snapshots found in that window")
        print()
        for n in names:
            S.digest(root, n, a.days)
        print("\nThe next plan run (run_plan.py --next-version) uses these digests.")
        return 0
    names = a.cluster or names_with()
    if not names:
        raise SystemExit("no snapshots yet - run snapshots.py first")
    for n in names:
        S.digest(root, n, a.days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
