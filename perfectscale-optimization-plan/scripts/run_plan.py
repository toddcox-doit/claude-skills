#!/usr/bin/env python3
"""One command: collect -> analyze -> render -> verify -> diff.

  python3 run_plan.py --config config/<tenant>.tenant.json [--collect] [--max-age-hours 24] [--no-verify]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from psplan.config import Config  # noqa: E402
from psplan import model, render, verify as V, diffplans  # noqa: E402
from psplan.actions import Plan  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--collect", action="store_true", help="run the pscli collector first (resumable, rate-limited)")
    ap.add_argument("--max-age-hours", type=float, default=None)
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--serial", action="store_true")
    ap.add_argument("--overwrite", action="store_true", help="allow replacing outputs that already exist for this version tag")
    a = ap.parse_args()
    cfg = Config.load(a.config)
    if a.collect:
        from psplan.collect import Collector
        kw = {"max_age_hours": a.max_age_hours} if a.max_age_hours is not None else {}
        if not Collector(cfg, **kw).run():
            print("collection incomplete - re-run with --collect to resume", file=sys.stderr)
            return 2
    existing = sorted(cfg.out().glob(f"*-{cfg['version_tag']}.*"))
    if existing and not a.overwrite:
        print(f"refusing to overwrite {len(existing)} existing '{cfg['version_tag']}' outputs in {cfg['out_dir']}. Bump version_tag/out_dir in the config, or pass --overwrite.", file=sys.stderr)
        return 3
    facts = model.load_all(cfg, parallel=not a.serial)
    trends = {}
    if cfg["trends_csv"]:
        trends = model.trends_summary(cfg.path("trends_csv"))
    S = Plan(cfg, facts, trends).build()
    files = render.write_all(cfg, S, facts)
    if cfg["previous_plan"] and cfg.path("previous_plan").exists():
        p = cfg.out() / render.fname(cfg, "change-log-vs-previous", "md")
        p.write_text(diffplans.diff(cfg.path("previous_plan"), [f for f in files if f.suffix == ".json"][0]))
        files.append(p)
    for f in files:
        print("wrote", f)
    if not a.no_verify:
        ok, errs = V.verify(cfg, S, facts, files)
        print(f"verify: {len(ok)} passed, {len(errs)} failed")
        for e in errs:
            print("  FAIL", e)
        return 1 if errs else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
