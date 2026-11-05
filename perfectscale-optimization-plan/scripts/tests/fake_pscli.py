#!/usr/bin/env python3
"""Stand-in for pscli, serving a collector-output tree (FAKE_PS_SRC). For collector tests only.
Env knobs: FAKE_429_FIRST_N (first N data calls return 429), FAKE_UNEV_FAIL=1 (cluster-wide unevictable errors),
FAKE_UNEV_FAIL_ALL=1 (cluster-wide AND every per-node-group unevictable call errors - total fallback failure),
FAKE_COST_ERROR=1 (every call fails with a non-rate-limit error whose message happens to contain "$429.10",
to prove a dollar figure never false-triggers 429 detection)."""
import json
import os
import sys
from pathlib import Path

src = Path(os.environ["FAKE_PS_SRC"])
args = sys.argv[1:]
state = src / ".fake_state"


def val(flag):
    return args[args.index(flag) + 1] if flag in args else None


def cluster_dir():
    uid = val("-c")
    for d in src.iterdir():
        if d.is_dir() and (d / "cluster.json").exists() and json.load((d / "cluster.json").open()).get("uid") == uid:
            return d
    sys.exit("unknown cluster")


if args[:2] == ["auth", "status"]:
    sys.exit(0)

if os.environ.get("FAKE_COST_ERROR"):
    sys.stderr.write(json.dumps({"error": {"code": "BUDGET_EXCEEDED", "message": "monthly cost is $429.10, over budget"}}))
    sys.exit(1)

n429 = int(os.environ.get("FAKE_429_FIRST_N", "0"))
if n429:
    try:
        done = int(state.read_text() or 0) if state.exists() else 0
    except ValueError:      # another worker is mid-write
        done = 0
    if done < n429:
        state.write_text(str(done + 1))
        sys.stderr.write('{"error":{"code":"RATE_LIMITED","message":"status 429"}}')
        sys.exit(1)

d = cluster_dir()
if "clusters" in args:
    print((d / "cluster.json").read_text())
elif "summary" in args:
    print((d / "summary.json").read_text())
elif "workloads" in args and "list" in args:
    sys.stdout.write((d / "workloads-all.jsonl").read_text())
elif "audit-logs" in args:
    sys.stdout.write((d / "audit.jsonl").read_text())
elif "nodegroups" in args:
    sys.stdout.write((d / "nodegroups.jsonl").read_text())
elif "unevictable" in args:
    g = val("-g")
    if os.environ.get("FAKE_UNEV_FAIL_ALL"):
        sys.stderr.write("500 internal error")
        sys.exit(1)
    if g is None and os.environ.get("FAKE_UNEV_FAIL"):
        sys.stderr.write("500 internal error")
        sys.exit(1)
    for line in (d / "unevictable-full.jsonl").read_text().splitlines():
        if line and (g is None or json.loads(line)["spec"].get("node_group") == g):
            print(line)
else:
    sys.exit(f"fake pscli: unsupported {args}")
