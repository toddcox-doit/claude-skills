"""Cluster snapshots from PerfectScale's own S3 export, as a credential-free second source.

PerfectScale's in-cluster exporter writes a Kubernetes inventory of each cluster every 30 minutes to two hours, and
that inventory lands in an S3 bucket the SE can read with their own AWS profile:

    <s3_prefix>/<tenant id>/<cluster id>/snapshots/year=YYYY/month=MM/day=DD/YYYY-MM-DD-HH-MM_v2.zip

Each zip holds one JSON document: pods, nodes, HPAs, PDBs, controllers, volumes. It has no usage,
cost or recommendations - those still come from the API - but it carries what the API never does:
HPA minReplicas/maxReplicas and every scaling metric, PDBs, node pools and the autoscaler, the
PerfectScale component versions installed, and how replica and node counts move over time.

Two steps, both run by the SE:
  pull    lists the month with `aws s3 ls` and copies the chosen snapshots (`aws s3 cp`) into
          <tenant>/data/snapshots/<cluster>/. The AWS CLI uses the SE's own profile; nothing here
          sees a credential.
  digest  reads those zips and writes <tenant>/data/snapshots/<cluster>/digest.json: only the
          fields the plan uses. Annotations, environment values and images other than PerfectScale's
          are never copied - the raw snapshot carries kubectl last-applied configurations with
          literal environment values, so the zips stay in the tenant folder and only the digest is
          read downstream.

The bucket is not named in the skill: the tenant config holds it, with the tenant id
("snapshots": {"s3_prefix": "s3://<bucket>/raw", "tenant_id": "<uuid>"}).
"""
import json

import re
import statistics
import subprocess
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

UUID_RX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PREFIX_RX = re.compile(r"^s3://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9](/[A-Za-z0-9._=-]+)*$")
KEY_RX = re.compile(r"snapshots/year=(\d{4})/month=(\d{2})/day=(\d{2})/(\d{4}-\d{2}-\d{2}-\d{2}-\d{2})_v(\d+)\.zip$")
LOCAL_RX = re.compile(r"^(\d{4}-\d{2}-\d{2}-\d{2}-\d{2})_v\d+\.zip$")
SAMPLES = ("latest", "daily", "week", "all")
MAX_JSON = 512 * 1024 * 1024          # one unpacked snapshot is ~15 MB; refuse anything absurd
POOL_LABELS = ("cloud.google.com/gke-nodepool", "eks.amazonaws.com/nodegroup", "karpenter.sh/nodepool",
               "agentpool", "kubernetes.azure.com/agentpool", "node.kubernetes.io/instancegroup")
EVICT = "cluster-autoscaler.kubernetes.io/safe-to-evict"


# ---------------------------------------------------------------- config / naming

def settings(cfg_raw):
    """(s3_prefix, tenant_id, [(name, uid)]) from the tenant config, validated."""
    s = cfg_raw.get("snapshots") or {}
    prefix, tid = (s.get("s3_prefix") or "").rstrip("/"), s.get("tenant_id") or ""
    if not PREFIX_RX.match(prefix):
        raise SystemExit('Add "snapshots": {"s3_prefix": "s3://<bucket>/raw", "tenant_id": "<tenant uuid>"} to the '
                         "tenant config (ask the PerfectScale team for the bucket).")
    if not UUID_RX.match(tid):
        raise SystemExit(f"snapshots.tenant_id {tid!r} is not a tenant UUID")
    clusters = [(c["name"], c["uid"]) for c in cfg_raw.get("clusters") or [] if UUID_RX.match(c.get("uid") or "")]
    if not clusters:
        raise SystemExit("no clusters with a uid in the tenant config")
    return prefix, tid, clusters


def stamp(name):
    """'2026-11-02-17-41' (from a key or a local file name) -> aware datetime, or None."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})", name or "")
    return datetime(*map(int, m.groups()), tzinfo=timezone.utc) if m else None


def parse_listing(text):
    """Keys from `aws s3 ls --recursive` output: [(stamp str, key)] sorted oldest first."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and KEY_RX.search(parts[-1]):
            out.append((KEY_RX.search(parts[-1]).group(4), parts[-1]))
    return sorted(set(out))


def select(stamps, samples, now=None):
    """Which snapshot stamps to keep. latest: the newest; daily: the newest of each day; week: every
    snapshot in the 7 days before the newest; all: everything."""
    stamps = sorted(set(stamps))
    if not stamps:
        return []
    bad = set(samples) - set(SAMPLES)
    if bad:
        raise ValueError(f"unknown sample {sorted(bad)}; use {', '.join(SAMPLES)}")
    if "all" in samples:
        return stamps
    keep = {stamps[-1]}
    if "daily" in samples:
        by_day = {}
        for s in stamps:
            by_day[s[:10]] = s
        keep |= set(by_day.values())
    if "week" in samples:
        last = stamp(stamps[-1])
        keep |= {s for s in stamps if stamp(s) > last - timedelta(days=7)}
    return sorted(keep)


# ---------------------------------------------------------------- pull

def _run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def pull(cfg_raw, root, month, samples=("latest", "daily"), profile=None, dry_run=False, run=_run, log=print):
    """Copy the chosen snapshots of every configured cluster for one month (YYYY-MM) into
    <root>/data/snapshots/<cluster>/. Files already there are not fetched again."""
    if not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", month or ""):
        raise SystemExit(f"--month must be YYYY-MM, not {month!r}")
    prefix, tid, clusters = settings(cfg_raw)
    yy, mm = month.split("-")
    extra = ["--profile", profile] if profile else []
    total = 0
    for name, uid in clusters:
        base = f"{prefix}/{tid}/{uid}/snapshots/year={yy}/month={mm}/"
        listing = parse_listing(run(["aws", "s3", "ls", base, "--recursive"] + extra))
        bucket = prefix.split("/")[2]
        keys = dict(listing)
        chosen = select(list(keys), samples)
        dest = Path(root) / "data" / "snapshots" / name
        todo = [(s, keys[s]) for s in chosen if not (dest / Path(keys[s]).name).exists()]
        log(f"{name}: {len(listing)} snapshots in {month}, {len(chosen)} chosen ({'+'.join(samples)}), "
            f"{len(todo)} to fetch")
        if dry_run:
            continue
        dest.mkdir(parents=True, exist_ok=True)
        for _, key in todo:
            run(["aws", "s3", "cp", f"s3://{bucket}/{key}", str(dest / Path(key).name), "--only-show-errors"] + extra)
        total += len(todo)
    return total


# ---------------------------------------------------------------- reading

def load(path):
    """The snapshot document inside one zip (or a bare .json)."""
    p = Path(path)
    if p.suffix == ".json":
        return json.loads(p.read_text())
    with zipfile.ZipFile(p) as z:
        members = [i for i in z.infolist() if i.filename.endswith(".json") and not i.is_dir()]
        if len(members) != 1 or members[0].file_size > MAX_JSON:
            raise ValueError(f"{p.name}: expected one snapshot JSON inside")
        return json.loads(z.read(members[0]))


def _qty(o):
    """PDB IntOrString ({type:0,intVal:n} / {type:1,strVal:'60%'}) -> '2' / '60%'."""
    if o is None:
        return None
    if isinstance(o, dict):
        return o.get("strVal") if o.get("type") == 1 else str(o.get("intVal"))
    return str(o)


def _metric(m):
    t = m.get("type")
    body = m.get({"Resource": "resource", "Pods": "pods", "External": "external", "Object": "object",
                  "ContainerResource": "containerResource"}.get(t, ""), {}) or {}
    tgt = body.get("target") or {}
    name = body.get("name") or (body.get("metric") or {}).get("name")
    out = {"type": t, "name": name, "target_type": tgt.get("type"),
           "target": tgt.get("averageUtilization") if tgt.get("averageUtilization") is not None
           else tgt.get("averageValue") or tgt.get("value")}
    if t == "ContainerResource":
        out["container"] = body.get("container")
    return out


def _owner(o):
    r = (o.get("ownerReferences") or [{}])[0]
    return r.get("kind"), r.get("name")


def _pool(labels):
    return next((labels[k] for k in POOL_LABELS if labels.get(k)), "(none)")


def _version(image):
    return image.rsplit(":", 1)[1] if ":" in image.rsplit("/", 1)[-1] else None


def facts(doc):
    """The fields the plan uses from one snapshot. Nothing else is copied."""
    c, meta = doc.get("cluster") or {}, doc.get("metadata") or {}
    rs_owner = {(r["namespace"], r["name"]): _owner(r) for r in c.get("replicaSets") or []}

    def workload(p):
        k, n = _owner(p)
        if k == "ReplicaSet":
            k2, n2 = rs_owner.get((p["namespace"], n), (None, None))
            return (k2, n2) if k2 else (k, n)
        return k, n

    pods = [p for p in c.get("pods") or [] if p.get("phase") == "Running"]
    pod_labels, no_req, blockers = {}, {}, {}
    for p in pods:
        k, n = workload(p)
        key = f"{k}/{p['namespace']}/{n}"
        pod_labels.setdefault(key, p.get("labels") or {})
        miss = [x["name"] for x in p.get("containers") or [] if not (x.get("resources") or {}).get("requests")]
        if miss and k != "DaemonSet":
            no_req[key] = sorted(set(no_req.get(key, [])) | set(miss))
        evict = (p.get("annotations") or {}).get(EVICT)
        local = [v["name"] for v in p.get("volumes") or [] if v.get("emptyDir") == "disk"
                 and not re.search(r"(socket|certs|token|istio|tmp)", v.get("name", ""))]
        if k not in ("DaemonSet", "Node") and (evict == "false" or (local and evict != "true")):
            b = blockers.setdefault(key, {"pods": 0, "reason": "safe-to-evict=false" if evict == "false"
                                          else "local emptyDir volume: " + ", ".join(local[:3])})
            b["pods"] += 1

    pdbs = []
    for d in c.get("pdbs") or []:
        sel = ((d.get("selector") or {}).get("matchLabels")) or {}
        pdbs.append({"namespace": d["namespace"], "name": d["name"], "minAvailable": _qty(d.get("minAvailable")),
                     "maxUnavailable": _qty(d.get("maxUnavailable")), "disruptionsAllowed": d.get("disruptionsAllowed"),
                     "expectedPods": d.get("expectedPods"), "_sel": sel})

    def pdb_for(ns, key):
        labels = pod_labels.get(key) or {}
        return [p["name"] for p in pdbs if p["namespace"] == ns and p["_sel"]
                and all(labels.get(k) == v for k, v in p["_sel"].items())]

    replicas = {f"{k}/{o['namespace']}/{o['name']}": o.get("replicas")
                for k, lst in (("Deployment", c.get("deployments")), ("StatefulSet", c.get("statefulSets")))
                for o in lst or []}
    hpas = []
    for h in c.get("hpas") or []:
        t = h.get("scaleTarget") or {}
        key = f"{t.get('kind')}/{h['namespace']}/{t.get('name')}"
        hpas.append({"namespace": h["namespace"], "name": h["name"], "target": key,
                     "minReplicas": h.get("minReplicas", 1), "maxReplicas": h.get("maxReplicas"),
                     "replicas": replicas.get(key), "metrics": [_metric(m) for m in h.get("metrics") or []],
                     "behavior": h.get("behavior") or None, "pdbs": pdb_for(h["namespace"], key)})

    pools = {}
    for n in c.get("nodes") or []:
        lab = n.get("labels") or {}
        p = pools.setdefault(_pool(lab), {"nodes": 0, "instance_types": {}, "arch": set(), "cpu_alloc": 0,
                                          "cpu_req": 0, "mem_alloc": 0, "mem_req": 0, "autoscaler": set()})
        p["nodes"] += 1
        it = n.get("instanceType") or lab.get("node.kubernetes.io/instance-type") or "?"
        p["instance_types"][it] = p["instance_types"].get(it, 0) + 1
        p["arch"].add(lab.get("kubernetes.io/arch") or "?")
        p["autoscaler"].add(n.get("nodeAutoscalerType") or "?")
        for f, src, unit in (("cpu_alloc", "allocatable", "cpuMillicores"), ("cpu_req", "allocated", "cpuMillicores"),
                             ("mem_alloc", "allocatable", "memoryBytes"), ("mem_req", "allocated", "memoryBytes")):
            p[f] += (n.get(src) or {}).get(unit) or 0
    for p in pools.values():
        p["arch"], p["autoscaler"] = sorted(p["arch"]), sorted(p["autoscaler"])

    components = sorted({(x["image"].rsplit("/", 1)[-1].split(":")[0], _version(x["image"]))
                         for p in c.get("pods") or [] for x in p.get("containers") or []
                         if "perfectscale" in x.get("image", "").lower()})
    status = meta.get("status") or {}
    for p in pdbs:
        p.pop("_sel")
    return {
        "collected": (meta.get("summary") or {}).get("collection_timestamp"),
        "counts": {k: len(c.get(k) or []) for k in ("pods", "nodes", "deployments", "statefulSets", "daemonSets",
                                                     "cronJobs", "hpas", "pdbs")},
        "namespaces": len({p["namespace"] for p in c.get("pods") or []}),
        "karpenter": status.get("fetch_nodeclaims") == "success" and bool(c.get("nodeClaims")),
        "argo_rollouts": status.get("fetch_rollouts") == "success",
        "perfectscale": [{"component": n, "version": v} for n, v in components],
        "pools": pools, "hpas": hpas, "pdbs": pdbs, "replicas": replicas,
        "no_requests": no_req, "drain_blockers": blockers,
    }


# ---------------------------------------------------------------- digest

def _vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def prerequisites(fx, agent_min="1.0.71", exporter_min="1.1.28"):
    """What the snapshot says about the HPA automation prerequisites. The Kubernetes version is not in
    a snapshot, so it stays a question."""
    ps = {c["component"]: c["version"] for c in fx["perfectscale"]}
    exp = ps.get("psc-exporter")
    agent = next((v for n, v in ps.items() if "autoscaler" in n or "agent" in n), None)
    return {"exporter": exp, "exporter_ok": bool(exp) and _vtuple(exp) >= _vtuple(exporter_min),
            "agent": agent, "agent_ok": bool(agent) and _vtuple(agent) >= _vtuple(agent_min),
            "agent_seen": agent is not None, "kubernetes_version": None}


def trends(series):
    """Replica and node movement across snapshots, oldest first: [(stamp, facts)]."""
    if not series:
        return {}
    hpa_t = {}
    for _, fx in series:
        for h in fx["hpas"]:
            if h["replicas"] is not None:
                hpa_t.setdefault(h["target"], {"min": h["minReplicas"], "max": h["maxReplicas"], "obs": []})["obs"].append(h["replicas"])
    hpa = {}
    for k, v in hpa_t.items():
        o = v["obs"]
        hpa[k] = {"samples": len(o), "low": min(o), "median": statistics.median(o), "high": max(o),
                  "at_min_pct": round(100 * sum(x <= v["min"] for x in o) / len(o)),
                  "at_max_pct": round(100 * sum(x >= v["max"] for x in o) / len(o)) if v["max"] else None}
    pools = {}
    for _, fx in series:
        for name, p in fx["pools"].items():
            pools.setdefault(name, []).append(p["nodes"])
    for name in pools:
        o = pools[name]
        pools[name] = {"low": min(o), "median": statistics.median(o), "high": max(o)}
    return {"from": series[0][0], "to": series[-1][0], "snapshots": len(series), "hpa": hpa, "pools": pools,
            "nodes": [fx["counts"]["nodes"] for _, fx in series]}


def hpa_findings(fx, tr):
    """Per HPA: what PerfectScale's HPA automation would do with these exact settings, from its docs -
    raise CPU/memory utilization triggers under 80 to 80, lower minReplicas above 2 to 2 (never below
    the PDB floor), never touch maxReplicas, behaviour or non-resource metrics."""
    out = []
    for h in fx["hpas"]:
        res = [m for m in h["metrics"] if m["type"] in ("Resource", "ContainerResource") and m["name"] in ("cpu", "memory")]
        other = [m["name"] for m in h["metrics"] if m not in res]
        changes = [f"{m['name']} trigger {m['target']}% -> 80%" for m in res
                   if m["target_type"] == "Utilization" and isinstance(m["target"], (int, float)) and m["target"] < 80]
        if (h["minReplicas"] or 1) > 2:
            changes.append(f"minReplicas {h['minReplicas']} -> 2" + (" (or the PDB floor)" if h["pdbs"] else ""))
        t = (tr.get("hpa") or {}).get(h["target"])
        out.append({"namespace": h["namespace"], "hpa": h["name"], "target": h["target"],
                    "min": h["minReplicas"], "max": h["maxReplicas"], "pinned": h["minReplicas"] == h["maxReplicas"],
                    "resource_triggers": {m["name"]: m["target"] for m in res}, "other_metrics": other,
                    "pdbs": h["pdbs"], "changes": changes, "trend": t})
    return out


def digest(root, cluster, log=print):
    """Read every snapshot of one cluster under <root>/data/snapshots/<cluster>/ and write digest.json."""
    d = Path(root) / "data" / "snapshots" / cluster
    files = sorted(f for f in d.glob("*") if LOCAL_RX.match(f.name) or f.name == "snapshot.json")
    if not files:
        raise SystemExit(f"no snapshots in {d}")
    series = []
    for f in files:
        fx = facts(load(f))
        s = LOCAL_RX.match(f.name).group(1) if LOCAL_RX.match(f.name) else \
            datetime.fromtimestamp(fx["collected"] or 0, timezone.utc).strftime("%Y-%m-%d-%H-%M")
        series.append((s, fx))
    series.sort(key=lambda x: x[0])
    stamp_l, latest = series[-1]
    tr = trends([(s, fx) for s, fx in series])
    out = {"cluster": cluster, "latest": stamp_l, "snapshots": [s for s, _ in series],
           "prerequisites": prerequisites(latest), "summary": {
               "counts": latest["counts"], "namespaces": latest["namespaces"], "karpenter": latest["karpenter"],
               "argo_rollouts": latest["argo_rollouts"], "perfectscale": latest["perfectscale"],
               "pdbs_zero_disruptions": sum(p["disruptionsAllowed"] == 0 for p in latest["pdbs"]),
               "workloads_without_requests": len(latest["no_requests"]),
               "drain_blockers": len(latest["drain_blockers"])},
           "pools": latest["pools"], "hpa": hpa_findings(latest, tr), "pdbs": latest["pdbs"],
           "no_requests": latest["no_requests"], "drain_blockers": latest["drain_blockers"], "trends": tr}
    (d / "digest.json").write_text(json.dumps(out, indent=1, default=list) + "\n")
    log(report(out))
    return out


def report(dg):
    s, pr = dg["summary"], dg["prerequisites"]
    h = dg["hpa"]
    trig = sum(any(c.startswith(("cpu", "memory")) for c in x["changes"]) for x in h)
    minr = sum(any(c.startswith("minReplicas") for c in x["changes"]) for x in h)
    cpu_req = sum(p["cpu_req"] for p in dg["pools"].values()) / max(1, sum(p["cpu_alloc"] for p in dg["pools"].values()))
    mem_req = sum(p["mem_req"] for p in dg["pools"].values()) / max(1, sum(p["mem_alloc"] for p in dg["pools"].values()))
    tr = dg["trends"]
    lines = [f"{dg['cluster']}: {len(dg['snapshots'])} snapshot(s), {tr.get('from')} .. {dg['latest']} UTC",
             f"  {s['counts']['nodes']} nodes in {len(dg['pools'])} pool(s); requests take {cpu_req:.0%} of allocatable CPU, "
             f"{mem_req:.0%} of memory; Karpenter {'yes' if s['karpenter'] else 'no'}",
             f"  exporter {pr['exporter'] or 'not found'} ({'ok' if pr['exporter_ok'] else 'below 1.1.28 or missing'}); "
             f"automation agent {pr['agent'] or 'not found'}",
             f"  {len(h)} HPAs: {trig} with a CPU/memory trigger HPA automation would raise to 80%, "
             f"{minr} with minReplicas it would lower, {sum(x['pinned'] for x in h)} pinned (min == max), "
             f"{sum(bool(x['other_metrics']) for x in h)} also scaling on other metrics",
             f"  {s['pdbs_zero_disruptions']} of {s['counts']['pdbs']} PDBs allow no disruption now; "
             f"{s['workloads_without_requests']} workloads have containers without requests; "
             f"{s['drain_blockers']} workloads look unevictable"]
    if tr.get("snapshots", 0) > 1:
        # current, unpinned HPAs only: a pinned one is always at max, and a blue/green swap leaves
        # history for HPAs that no longer exist
        live = {x["target"] for x in h if not x["pinned"]}
        busy = sorted(((k, v) for k, v in tr["hpa"].items() if v["at_max_pct"] and k in live),
                      key=lambda kv: -kv[1]["at_max_pct"])[:3]
        lines.append(f"  node count {min(tr['nodes'])}..{max(tr['nodes'])} across the window"
                     + ("; most often at maxReplicas: " + ", ".join(f"{k.split('/')[-1]} {v['at_max_pct']}%" for k, v in busy)
                        if busy else ""))
    return "\n".join(lines)
