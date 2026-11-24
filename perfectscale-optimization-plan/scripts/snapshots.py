#!/usr/bin/env python3
"""Cluster snapshots from PerfectScale's S3 export: pull them, then digest them for the plan.

  python3 snapshots.py pull --month 2026-11                     # latest + one a day, every cluster
  python3 snapshots.py pull --month 2026-11 --sample latest     # just the newest snapshot per cluster
  python3 snapshots.py pull --month 2026-11 --sample latest,week --profile <aws profile>
  python3 snapshots.py pull --month 2026-11 --dry-run           # list and count, fetch nothing
  python3 snapshots.py digest                                   # every cluster with snapshots

Needs "snapshots": {"s3_prefix": "s3://<bucket>/raw", "tenant_id": "<uuid>"} in the tenant config,
and the AWS CLI signed in with a profile that can read the bucket. See psplan/snapshots.py.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from psplan import snapshots as S  # noqa: E402
from psplan.versioning import find_config, tenant_root  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="the tenant config (default: the one in the tenant folder this copy lives in)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull", help="copy snapshots from S3 into data/snapshots/<cluster>/")
    p.add_argument("--month", required=True, help="YYYY-MM")
    p.add_argument("--sample", default="latest,daily", help=f"comma list of {', '.join(S.SAMPLES)} (default latest,daily)")
    p.add_argument("--profile", help="AWS CLI profile (default: the CLI's own default)")
    p.add_argument("--dry-run", action="store_true")
    d = sub.add_parser("digest", help="write data/snapshots/<cluster>/digest.json")
    d.add_argument("--cluster", action="append", help="cluster name (default: every cluster with snapshots)")
    a = ap.parse_args(argv)

    cfgp = find_config(__file__, a.config)
    raw, root = json.loads(Path(cfgp).read_text()), tenant_root(cfgp)
    if a.cmd == "pull":
        samples = tuple(s.strip() for s in a.sample.split(",") if s.strip())
        try:
            S.select(["2000-01-01-00-00"], samples)
        except ValueError as e:
            raise SystemExit(str(e))
        n = S.pull(raw, root, a.month, samples, a.profile, a.dry_run)
        if not a.dry_run:
            print(f"fetched {n} snapshot(s). Next: python3 {Path(__file__).name} digest")
        return 0
    names = a.cluster or [c["name"] for c in raw.get("clusters") or []
                          if (root / "data" / "snapshots" / c["name"]).is_dir()]
    if not names:
        raise SystemExit("no snapshots yet - run pull first")
    for n in names:
        S.digest(root, n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
