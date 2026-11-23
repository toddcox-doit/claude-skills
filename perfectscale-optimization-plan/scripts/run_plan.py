#!/usr/bin/env python3
"""One command: collect -> analyze -> render -> verify -> diff.

  python3 run_plan.py --config config/<tenant>.tenant.json [--collect] [--max-age-hours 24] [--no-verify]
  python3 run_plan.py --config config/<tenant>.tenant.json --check     # preflight only: will the run get what it needs?
  python3 run_plan.py --config config/<tenant>.tenant.json --next-version            # re-render as the next version
  python3 run_plan.py --config config/<tenant>.tenant.json --pin-hpa model-a model-b # save HPA confirmations, re-render
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
    ap.add_argument("--config", help="the tenant config (default: the one config in the tenant folder this copy lives in)")
    ap.add_argument("--collect", action="store_true", help="run the pscli collector first (resumable, rate-limited)")
    ap.add_argument("--max-age-hours", type=float, default=None)
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--serial", action="store_true")
    ap.add_argument("--overwrite", action="store_true", help="allow replacing outputs that already exist for this version tag")
    ap.add_argument("--check", action="store_true", help="preflight only: credentials, data age, enrichments, output folder")
    ap.add_argument("--no-network", action="store_true", help="with --check: skip the API token test")
    ap.add_argument("--next-version", action="store_true",
                    help="move the config to the next version (tag, output folder, previous plan) before rendering")
    ap.add_argument("--pin-hpa", nargs="+", metavar="NAMESPACE",
                    help="save namespaces whose pod CPU/memory HPAs the owner confirmed pinned (min = max), "
                         "then render the next version - the same as ticking 'HPA pinned' on the kickoff page")
    a = ap.parse_args()
    from psplan import versioning
    a.config = str(versioning.find_config(__file__, a.config))   # works from any folder
    if a.pin_hpa or a.next_version:
        try:
            ch = versioning.bump(a.config, pin=a.pin_hpa or ())
        except ValueError as e:
            print(f"--pin-hpa: {e}", file=sys.stderr)
            return 2
        if a.pin_hpa:
            print(f"HPA confirmed pinned: {', '.join(a.pin_hpa)}"
                  + (f" (newly saved: {', '.join(ch['pinned_added'])})" if ch["pinned_added"] else " (already saved)"))
        print(f"version {ch['previous_version']} -> {ch['version']}: outputs go to {ch['out_dir']}; "
              f"previous config kept as {ch['backup']}")
    cfg = Config.load(a.config)
    if a.check:
        from psplan import preflight
        print(f"Preflight for {cfg['customer']} ({a.config}):")
        return preflight.report(preflight.check(cfg, max_age_hours=a.max_age_hours or 24, network=not a.no_network))
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
