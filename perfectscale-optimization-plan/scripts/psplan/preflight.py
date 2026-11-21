"""`run_plan.py --check`: will this run get everything it needs? Answered before the customer call,
not discovered in the report.

Each line is OK, WARN (the run works, something is missing or stale) or BLOCK (the run cannot
produce a plan). Read-only: the only network call is an API token exchange to prove the stored
credentials still work.
"""
import importlib.util
import os
import shutil
import time
from pathlib import Path

from . import credentials
from .apiv2 import load_credentials
from .util import exists, jload

OK, WARN, BLOCK = "OK", "WARN", "BLOCK"


def _cred_source(cfg):
    if os.environ.get("PERFECTSCALE_CLIENT_ID") and os.environ.get("PERFECTSCALE_CLIENT_SECRET"):
        return "environment variables"
    if cfg.get("api_credentials_file"):
        return str(cfg["api_credentials_file"])
    return str(credentials.env_path(cfg.get("profile")))


def _age_h(d, manifest, key, fname):
    m = manifest.get(key) or {}
    if m.get("ok") and m.get("ts"):
        return (time.time() - m["ts"]) / 3600
    p = Path(d) / fname
    for cand in (p, p.with_name(p.name + ".gz")):
        if cand.exists():
            return (time.time() - cand.stat().st_mtime) / 3600
    return None


def check(cfg, max_age_hours=24, network=True):
    out = []
    add = lambda lvl, item, detail: out.append((lvl, item, detail))  # noqa: E731
    profile = cfg.get("profile") or "default"
    add(OK, "config", f"{cfg['customer']}: {len(cfg['clusters'])} clusters, pscli profile '{profile}'")

    pscli = cfg.get("pscli") or shutil.which("pscli")
    pscli_ok, msg = credentials.pscli_status(pscli, profile)
    add(OK if pscli_ok else WARN, "pscli", msg if pscli_ok else
        f"{msg}; collecting needs it - run: new_tenant.py auth --profile {profile}")

    creds, why = load_credentials(cfg)
    if not creds:
        add(WARN, "API v2 credentials", f"{why}. Without them HPA data and automation status are skipped")
    elif network:
        ok, m = credentials.check_api(creds, cfg.get("public_api_base"))
        add(OK if ok else WARN, "API v2 credentials",
            f"found in {_cred_source(cfg)}; token exchange " + ("works" if ok else f"failed ({m}) - re-run: new_tenant.py auth --profile {profile}"))
    else:
        add(OK, "API v2 credentials", f"found in {_cred_source(cfg)} (not tested: --no-network)")
    del creds

    any_missing = False
    for c in cfg["clusters"]:
        d = cfg.data(c["name"])
        manifest = jload(d / "collect-manifest.json", {}) or {}
        age = _age_h(d, manifest, "workloads", "workloads-all.jsonl")
        if age is None:
            any_missing = True
            add(BLOCK if not pscli_ok else WARN, f"data: {c['name']}",
                "not collected yet - run with --collect" + ("" if pscli_ok else " (needs the pscli sign-in above)"))
            continue
        lvl = OK if age <= max_age_hours else WARN
        detail = f"workloads {age:.0f}h old" + ("" if lvl == OK else f" (over {max_age_hours}h - re-collect with --collect)")
        v2 = _age_h(d, manifest, "workloads_v2", "workloads-v2.json")
        detail += "; HPA/automation status: " + (f"collected {v2:.0f}h ago" if v2 is not None else "not collected")
        if v2 is None and lvl == OK:
            lvl = WARN
        add(lvl, f"data: {c['name']}", detail)

    if cfg.get("eligibility_file"):
        elig = f"browser export ({cfg['eligibility_file']})"
    elif all(exists(cfg.data(c["name"]) / "workloads-v2.json") for c in cfg["clusters"]):
        elig = "public API v2 - automation confidence high"
    else:
        elig = "unconfirmed for some clusters - automation actions stay at medium confidence"
    add(OK if "unconfirmed" not in elig else WARN, "automation status", elig)

    outd = cfg.out()
    existing = sorted(outd.glob(f"*-{cfg['version_tag']}.*")) if outd.exists() else []
    add(WARN if existing else OK, "output folder",
        f"{len(existing)} '{cfg['version_tag']}' files already in {cfg['out_dir']} - pass --overwrite or bump version_tag"
        if existing else f"{cfg['out_dir']} is clear for '{cfg['version_tag']}'")

    add(OK if shutil.which("node") else WARN, "executive summary",
        "node found" if shutil.which("node") else "node not found - the .docx/.html one-pager will be skipped")
    has_xl = importlib.util.find_spec("openpyxl") is not None
    add(OK if has_xl else WARN, "XLSX", "openpyxl installed" if has_xl else "openpyxl missing - CSV only")
    if any_missing and not pscli_ok:
        add(BLOCK, "summary", "cannot run: data is missing and pscli cannot collect it")
    return out


def report(rows, say=print):
    w = max(len(r[1]) for r in rows)
    for lvl, item, detail in rows:
        say(f"  {lvl:<5}  {item:<{w}}  {detail}")
    blocks = sum(1 for r in rows if r[0] == BLOCK)
    warns = sum(1 for r in rows if r[0] == WARN)
    say("\n" + ("Blocked: fix the BLOCK lines first." if blocks else
                (f"Ready to run, with {warns} warning(s)." if warns else "Ready to run.")))
    return 1 if blocks else 0
