"""Load + normalize + classify one cluster's collected data into a JSON-serializable `facts` dict.

Design rules (each one is a lesson from a real run):
  * Costs from the platform are ACTUAL spend over the time a cluster has been connected, even when the
    period says "30d". We measure the observed window and scale to 30 days (run_rate = observed * 30/window),
    and keep the observed numbers beside the scaled ones. Windows under `min_history_days` are flagged early.
  * Never use the group-by rollups for numbers - they do not reconcile to the workload list. Everything is
    summed from the full workload rows and reconciled against the cluster summary.
  * Workloads are classified by how the fix is actually reached (lane), not just by type.
  * Node pools are classified long-lived vs auto-provisioned; per-pool advice only makes sense for the former.
"""
import collections
import hashlib
import json
import re
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from . import __version__
from .util import exists, jl, jload, parse_ts

JOB_TYPES = {"GroupedJob", "GroupedPod", "Job", "CronJob", "<none>", "-", ""}
GROUPED_TYPES = {"GroupedJob", "GroupedPod"}
# One-off executions listed individually: bare Jobs, and pods with no controller
# ("<none>", e.g. CI build agents). These are what a custom grouping rule collapses.
# CronJob is excluded: it is already one durable workload, not per-run noise.
UNGROUPED_JOB_TYPES = {"Job", "<none>", ""}

MANAGED_NS_BASE = {"kube-system", "kube-public", "kube-node-lease"}
MANAGED_NS_PREFIX = {
    "gcp": ("gke-", "gmp-", "anthos", "config-management"),
    "aws": ("amazon-", "aws-", "eks-"),
    "azure": ("azure-", "gatekeeper-", "aks-"),
}
CLOUD_HINT = [("gcp", re.compile(r"^(e2|n1|n2|n2d|c2|c2d|c3|c3d|t2d|t2a|m1|m2|m3|a2|a3|g2|custom|default)-")),
              ("aws", re.compile(r"^[a-z]+\d+[a-z]*\.[a-z0-9]+$")),
              ("azure", re.compile(r"^Standard_", re.I))]

ISSUE = {
    "MemLimitNotSet": "No memory limit set", "OverProvisionedWorkload": "Over-provisioned",
    "OverProvisionedCpuRequest": "Over-provisioned CPU", "OverProvisionedMemRequest": "Over-provisioned memory",
    "UnderProvisionedCpuRequest": "Under-provisioned CPU", "UnderProvisionedMemRequest": "Under-provisioned memory",
    "UnderProvisionedMemLimit": "Under-provisioned memory limit", "CpuLimitNotSet": "No CPU limit set",
    "CpuRequestNotSet": "No CPU request set", "MemRequestNotSet": "No memory request set",
    "NoRequestsSet": "No resource requests set", "CpuThrottling": "CPU throttling", "Throttling": "CPU throttling",
    "OOM": "OOM risk", "OOMRisk": "OOM risk", "OOMKilled": "OOM risk", "GuaranteedQos": "Guaranteed QoS constraints",
    "RestartsObserved": "Restarts observed",
}

# unevictable reason code -> (customer label, fix complexity, what the fix is)
REASON_INFO = {
    "opt_out_annotation": ("Do-not-evict annotation", "Low", "remove the annotation where it is a stale default"),
    "local_node_storage": ("Local node storage (emptyDir / hostPath)", "Medium", "scratch space can be marked safe to evict; durable state needs a volume change"),
    "static_mirror_pod": ("Static / mirror pods", "None", "expected on every node; no action"),
    "pod_disruption_budget": ("Over-restrictive PodDisruptionBudget", "Medium", "set minAvailable below the replica count or use maxUnavailable: 1"),
    "node_affinity": ("Strict node affinity", "Medium", "soften affinity or size a dedicated pool"),
    "anti_affinity": ("Strict anti-affinity", "Medium", "usually intentional HA; visibility only without owner sign-off"),
}

FRAMEWORKS = [
    ("dagster", lambda l, ns, n: any(k.startswith("label_dagster") for k in l) or ns == "dagster" or n.startswith("dagster")),
    ("airflow", lambda l, ns, n: any(k in l for k in ("label_airflow_version", "label_dag_id", "label_task_id", "label_airflow_worker")) or "airflow" in ns),
    ("argo-workflows", lambda l, ns, n: any(k.startswith("label_workflows_argoproj_io") for k in l)),
    ("spark", lambda l, ns, n: any(k in l for k in ("label_spark_role", "label_spark_app_id", "label_spark_app_name"))),
    ("ray", lambda l, ns, n: any(k.startswith("label_ray_io") for k in l)),
]


# ------------------------------------------------------------------ small helpers
def humanize(n):
    s = ISSUE.get(n)
    if s:
        return s
    t = re.sub(r"(?<!^)(?=[A-Z])", " ", n).lower()
    return t[:1].upper() + t[1:]


def allind(w):
    return list(w.get("indicators") or []) + [i for c in (w.get("containers") or []) for i in (c.get("indicators") or [])]


def is_synth(w):
    """Rows the API synthesizes rather than observes: placeholder namespaces, and
    collapsed aggregates (type "-", e.g. "Collapsed Ephemeral Pods") which are a
    rollup of pods already counted elsewhere. Keeping an aggregate alongside its
    members would double-count and break reconciliation to the workload list."""
    n, ns = w.get("name", ""), w.get("namespace", "")
    if str(w.get("type", "")).startswith("-"):
        return True
    return (n.startswith("__") and n.endswith("__")) or (ns.startswith("__") and ns.endswith("__"))


def high_sev(w):
    return any(i["type"] == "risk" and i["severity"] >= 2 for i in allind(w))


def memlimit_sev2(w):
    return any(i["name"] == "MemLimitNotSet" and i["severity"] >= 2 for i in allind(w))


def has_oom(w):
    return any(i["name"] in ("OOM", "OOMKilled", "OOMRisk") for i in allind(w))


def primary_issue(w):
    inds = allind(w)
    if memlimit_sev2(w):
        return humanize("MemLimitNotSet")
    mi = w.get("max_indicator")
    if mi and mi.get("name"):
        return humanize(mi["name"])
    waste = sorted([i for i in inds if i["type"] == "waste"], key=lambda i: -i["severity"])
    if waste:
        return humanize(waste[0]["name"])
    risk = sorted([i for i in inds if i["type"] == "risk"], key=lambda i: -i["severity"])
    return humanize(risk[0]["name"]) if risk else "Over-provisioned"


def display_name(name):
    m = re.match(r"^dagster-0{8}-0{4}-0{4}-(.+?)(?:-0{10})?$", name)
    if m:
        return f"{m.group(1)} (grouped pods)"
    m = re.match(r"^(run|step)_worker-(.+)$", name)
    if m:
        return f"{m.group(2)} ({m.group(1)} worker)"
    return name if len(name) <= 60 else name[:57] + "..."


def short_name(name):
    return display_name(name).split(" (")[0]


def managed_ns(ns, cloud, extra):
    if ns in MANAGED_NS_BASE or ns in extra:
        return True
    return any(ns.startswith(p) for p in MANAGED_NS_PREFIX.get(cloud or "", ()))


def detect_cloud(cfg, cluster_dir, ng):
    c = (jload(cluster_dir / "cluster.json", {}) or {}).get("cloud") or cfg.get("cloud")
    if c:
        return c
    for g in ng[:50]:
        it = (g.get("labels") or {}).get("instance_type", "")
        for name, rx in CLOUD_HINT:
            if rx.match(it):
                return name
    return "unknown"


def owner_key(w):
    l = w.get("workload_labels") or {}
    for k in ("label_team", "label_owner", "label_app_kubernetes_io_team"):
        if l.get(k):
            return l[k]
    cl = l.get("label_dagster_code_location")
    if cl:
        return re.sub(r"-pr-\d+$", "", cl)
    po = l.get("label_app_kubernetes_io_part_of")
    return po or w.get("namespace", "")


def owners(ws, key="waste", n=3):
    c = collections.Counter()
    for w in ws:
        c[owner_key(w)] += w.get(key, 0) or 0
    return [k for k, _ in c.most_common(n) if k]


# ------------------------------------------------------------------ node groups
def load_node_groups(d):
    o = jload(d / "nodegroups.json")
    if isinstance(o, dict):
        return o.get("node_groups") or o.get("nodeGroups") or o.get("data") or []
    if isinstance(o, list):
        return o
    return jl(d / "nodegroups.jsonl")


def sizing_fraction(w, margin, pkey):
    d = w["derived"]
    fr = []
    for cur, rec, use in ((d["current_cpu_request_cores_total"], d["recommended_cpu_request_cores_total"], d[f"cpu_usage_{pkey}_cores_sum"]),
                          (d["current_memory_request_mib_total"], d["recommended_memory_request_mib_total"], d[f"memory_usage_{pkey}_mib_sum"])):
        if rec > cur * 1.0001:
            fr.append(min(1.0, max(0.0, (use * margin - cur) / (rec - cur))))
    return sum(fr) / len(fr) if fr else 1.0


# ------------------------------------------------------------------ the loader
def fingerprint(cfg, cluster, files):
    h = hashlib.sha1()
    h.update(__version__.encode())
    h.update(Path(__file__).read_bytes())          # any code change invalidates cached facts
    for f in files:
        p = Path(f)
        for cand in (p, p.with_name(p.name + ".gz")):
            if cand.exists():
                st = cand.stat()
                h.update(f"{cand.name}:{st.st_size}:{int(st.st_mtime)}".encode())
    relevant = {k: cfg[k] for k in ("exclusions", "held_out_from_automation", "stateful_patterns", "managed_namespaces",
                                    "auto_provisioned_pool_patterns", "gpu_pool_patterns", "thresholds", "scale_to_days",
                                    "min_history_days", "cloud", "eligibility_file")}
    h.update(json.dumps(relevant, sort_keys=True).encode())
    return h.hexdigest()[:16]


def load_cluster(cfg, cluster, use_cache=True):
    name, uid = cluster["name"], cluster["uid"]
    d = cfg.data(name)
    files = [d / x for x in ("summary.json", "workloads-all.jsonl", "nodegroups.json", "nodegroups.jsonl",
                             "unevictable-full.jsonl", "unevictable-topgroups.jsonl", "audit.jsonl", "cluster.json")]
    if cfg.get("eligibility_file"):
        files.append(Path(cfg["eligibility_file"]))
    cache = cfg.data(name).parent / ".cache" / f"{name}.json"
    fp = fingerprint(cfg, cluster, files)
    if use_cache and cache.exists():
        try:
            with cache.open() as fh:
                c = json.load(fh)
            if c.get("_fingerprint") == fp:
                return c
        except ValueError:
            pass
    facts = _load(cfg, cluster, d)
    facts["_fingerprint"] = fp
    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open("w") as fh:
        json.dump(facts, fh, default=float)
    return facts


def _load(cfg, cluster, d):
    T = cfg["thresholds"]
    name, uid = cluster["name"], cluster["uid"]
    summary = jload(d / "summary.json", {}) or {}
    allw = jl(d / "workloads-all.jsonl")
    if not allw:
        raise SystemExit(f"{name}: no workloads-all.jsonl in {d} - run the collector first")
    ng = load_node_groups(d)
    cloud = detect_cloud(cfg, d, ng)
    extra_managed = set(cfg["managed_namespaces"])
    stateful_rx = re.compile("|".join(cfg["stateful_patterns"]), re.I) if cfg["stateful_patterns"] else None
    held_rx = re.compile("|".join(cfg["held_out_from_automation"]), re.I) if cfg["held_out_from_automation"] else None
    jvm_rx = re.compile("|".join(cfg["jvm_patterns"]), re.I) if cfg["jvm_patterns"] else None
    excl = [e.lower() for e in cfg["exclusions"]]

    # ---- eligibility (optional enrichment)
    elig_map = {}
    if cfg.get("eligibility_file"):
        ej = jload(cfg["eligibility_file"], {}) or {}
        elig_map = ej.get(uid) or ej.get(name) or {}

    # ---- filter
    n_synth = n_muted = n_excl = 0
    excl_cost = excl_waste = 0.0
    w = []
    for x in allw:
        if is_synth(x):
            n_synth += 1
            continue
        if (x.get("mute_status") or {}).get("is_muted"):
            n_muted += 1
            continue
        if excl and any(e in x["name"].lower() or e in x["namespace"].lower() for e in excl):
            n_excl += 1; excl_cost += x["cost"]; excl_waste += x["waste"]
            continue
        w.append(x)

    ts_f = [parse_ts(x["first_seen"]) for x in w if x.get("first_seen")]
    ts_l = [parse_ts(x["last_seen"]) for x in w if x.get("last_seen")]
    first, last = min(ts_f), max(ts_l)
    window_days = max((last - first).total_seconds() / 86400.0, 0.25)
    scale_to = cfg["scale_to_days"]
    f = scale_to / window_days if window_days < scale_to else 1.0       # never scale DOWN a full window
    R = lambda v: v * f  # noqa: E731

    # ---- lane classification
    def lane(x):
        if managed_ns(x["namespace"], cloud, extra_managed):
            return "managed"
        if held_rx and (held_rx.search(x["name"]) or held_rx.search(x["namespace"])):
            return "held_out"
        if x["type"] not in JOB_TYPES and stateful_rx and stateful_rx.search(x["name"]):
            return "stateful"
        if x["type"] in JOB_TYPES:
            return "job"
        return "controller"

    for x in w:
        x["_lane"] = lane(x)
        # A cross-cutting flag, not a lane: a JVM service is still a controller.
        # It only changes how the change is made, not who owns it.
        #
        # Long-running workloads only. A one-shot build agent also runs a JVM,
        # but it exits in minutes and is never individually right-sized, so
        # including it buries the services that matter under thousands of pods.
        x["_jvm"] = bool(jvm_rx and x["_lane"] != "job"
                         and (jvm_rx.search(x["name"]) or jvm_rx.search(x["namespace"])))
        st = elig_map.get(x.get("id"))
        x["_elig"] = st

    cost_o = sum(x["cost"] for x in w); waste_o = sum(x["waste"] for x in w); pot_o = sum(x["potential_savings"] for x in w)
    out = {
        "name": name, "uid": uid, "cloud": cloud, "window_days": window_days, "factor": f,
        "first_seen": first.isoformat(), "last_seen": last.isoformat(),
        "early": window_days < cfg["min_history_days"],
        "n_workloads": len(w), "n_synth_dropped": n_synth, "n_muted": n_muted, "n_excluded": n_excl,
        "excluded_cost": R(excl_cost), "excluded_waste": R(excl_waste),
        "cost_o": cost_o, "waste_o": waste_o, "pot_o": pot_o, "cost": R(cost_o), "waste": R(waste_o), "pot": R(pot_o),
        "waste_wl": sum(1 for x in w if x["waste"] > 0),
        "summary": {k: summary.get(k) for k in ("workloads", "total_cost", "total_waste", "total_potential_saving", "waste_workloads", "risky_workloads")},
        "flags": [],
    }
    if out["early"]:
        out["flags"].append("early_baseline")
    if n_synth:
        out["flags"].append("synthetic_rows_dropped")
    if summary.get("total_cost") and abs(summary["total_cost"] - cost_o - 0) > max(0.01 * summary["total_cost"], 1) and not (n_synth or n_muted or n_excl):
        out["flags"].append("summary_mismatch")

    # ---- policy / type / namespace
    out["policies"] = dict(collections.Counter(x.get("optimization_policy") or "unset" for x in w))
    out["types"] = dict(collections.Counter(x["type"] for x in w))
    ns = collections.defaultdict(lambda: {"n": 0, "cost": 0.0, "waste": 0.0, "pot": 0.0})
    for x in w:
        a = ns[x["namespace"]]
        a["n"] += 1; a["cost"] += x["cost"]; a["waste"] += x["waste"]; a["pot"] += x["potential_savings"]
    out["namespaces"] = sorted(({"ns": k, "n": v["n"], "cost": R(v["cost"]), "waste": R(v["waste"]), "pot": R(v["pot"]),
                                 "waste_o": v["waste"]} for k, v in ns.items()), key=lambda r: -r["waste"])

    # ---- risk
    out["high_sev"] = sum(1 for x in w if high_sev(x))
    ml = sorted([x for x in w if memlimit_sev2(x)], key=lambda x: -x["waste"])
    out["memlimit_sev2_n"] = len(ml)
    out["memlimit_sev2"] = [{"name": x["name"], "ns": x["namespace"], "type": x["type"], "waste": R(x["waste"])} for x in ml[:8]]
    out["no_requests_n"] = sum(1 for x in w if any(i["name"] in ("CpuRequestNotSet", "MemRequestNotSet", "NoRequestsSet") for i in allind(x)))
    out["no_requests_ds_n"] = sum(1 for x in w if x["type"] == "DaemonSet" and any(i["name"] in ("CpuRequestNotSet", "MemRequestNotSet", "NoRequestsSet") for i in allind(x)))

    # ---- top workloads
    def row(x):
        dd = x["derived"]
        return {"name": x["name"], "ns": x["namespace"], "type": x["type"], "lane": x["_lane"], "waste": R(x["waste"]), "waste_o": x["waste"],
                "pot": R(x["potential_savings"]), "pot_o": x["potential_savings"], "issue": primary_issue(x),
                "cpu_cur": dd["current_cpu_request_cores_total"], "cpu_rec": dd["recommended_cpu_request_cores_total"],
                "mem_cur_gib": dd["current_memory_request_mib_total"] / 1024, "mem_rec_gib": dd["recommended_memory_request_mib_total"] / 1024,
                "replicas": x["replicas_counts"]["avg_count"], "id": x.get("id"), "owner": owner_key(x)}
    out["top"] = [row(x) for x in sorted(w, key=lambda x: -x["waste"])[:8]]
    out["vcpu_freed_top"] = sum(max(0.0, t["cpu_cur"] - t["cpu_rec"]) for t in out["top"])

    # ---- lanes
    lanes = {}
    for ln in ("controller", "job", "managed", "stateful", "held_out"):
        ws = [x for x in w if x["_lane"] == ln]
        elig_ok = ("NotEnabled", "DisabledByUser", "Eligible")
        pe = sum(x["potential_savings"] for x in ws if x["_elig"] in elig_ok) if elig_map else None
        lanes[ln] = {"pot_eligible_o": pe, "pot_eligible": (R(pe) if pe is not None else None),
                     "n": len(ws), "cost": R(sum(x["cost"] for x in ws)), "waste": R(sum(x["waste"] for x in ws)), "pot": R(sum(x["potential_savings"] for x in ws)),
                     "cost_o": sum(x["cost"] for x in ws), "waste_o": sum(x["waste"] for x in ws), "pot_o": sum(x["potential_savings"] for x in ws),
                     "owners": owners(ws, "potential_savings"), "top": [row(x) for x in sorted(ws, key=lambda x: -x["potential_savings"])[:8]]}
    out["lanes"] = lanes

    # ---- frameworks for job lane
    fw = collections.Counter()
    for x in w:
        if x["_lane"] != "job":
            continue
        l = x.get("workload_labels") or {}
        for fname, fn in FRAMEWORKS:
            if fn(l, x["namespace"], x["name"]):
                fw[fname] += 1
                break
        else:
            fw["generic"] += 1
    out["frameworks"] = dict(fw)
    out["primary_framework"] = fw.most_common(1)[0][0] if fw else "generic"

    # ---- grouping state
    jobs = [x for x in w if x["_lane"] == "job"]
    plain = [x for x in jobs if x["type"] in UNGROUPED_JOB_TYPES]
    pre = collections.Counter()
    codeloc = set()
    for x in plain:
        m = re.match(r"^((?:[a-z0-9]+-)?(?:run|step))[-_]", x["name"])
        pre[m.group(1) if m else "other"] += 1
        cl = (x.get("workload_labels") or {}).get("label_dagster_code_location")
        if cl:
            codeloc.add(re.sub(r"-pr-\d+$", "", cl))
    out["grouping"] = {"grouped_n": sum(1 for x in w if x["type"] in GROUPED_TYPES), "plain_job_n": len(plain),
                       "plain_job_cost": R(sum(x["cost"] for x in plain)), "plain_job_waste": R(sum(x["waste"] for x in plain)),
                       "plain_job_pot": R(sum(x["potential_savings"] for x in plain)), "prefixes": dict(pre.most_common(5)),
                       "code_locations": len(codeloc), "top_plain": [row(x) for x in sorted(plain, key=lambda x: -x["waste"])[:3]],
                       "recommend": len(plain) >= T["plain_job_group_min"] and sum(x["waste"] for x in plain) >= T["plain_job_waste_min_usd"]}
    jv = [x for x in w if x.get("_jvm")]
    out["jvm"] = {"n": len(jv), "pot": R(sum(x["potential_savings"] for x in jv)),
                  "cost": R(sum(x["cost"] for x in jv)),
                  "oom": sum(1 for x in jv if has_oom(x)),
                  "in_automation": sum(1 for x in jv if x["_lane"] == "controller"),
                  "pot_in_automation": R(sum(x["potential_savings"] for x in jv if x["_lane"] == "controller")),
                  "names": sorted({short_name(x["name"]) for x in jv})[:6],
                  "declared": bool(jvm_rx)}
    out["pr_env_labels"] = sum(1 for x in w if any(k.startswith("label_dev_env") or k == "label_pr_number" for k in (x.get("workload_labels") or {})))

    # ---- stateful / held-out detail
    out["stateful"] = {"n": lanes["stateful"]["n"], "pot": lanes["stateful"]["pot"], "names": sorted({short_name(x["name"]) for x in w if x["_lane"] == "stateful"})[:6]}
    out["controllers"] = [{"name": x["name"], "ns": x["namespace"], "type": x["type"], "pot": R(x["potential_savings"]), "pot_o": x["potential_savings"], "owner": owner_key(x)}
                          for x in sorted([x for x in w if x["_lane"] == "controller"], key=lambda x: -x["potential_savings"])]
    out["managed_names"] = sorted({x["name"] for x in w if x["_lane"] == "managed"})[:8]
    out["names_by_type"] = {}  # kept small: only controllers, used for cross-cluster pairing
    out["controller_names"] = sorted({x["name"] for x in w if x["_lane"] == "controller" and x["type"] in ("Deployment", "StatefulSet")})

    # ---- resiliency (spend) with ranges
    res = [x for x in w if (high_sev(x) or has_oom(x)) and x["cost_increase"] > 0]

    def rsum(ws):
        lit = sum(x["cost_increase"] for x in ws)
        lik = sum(x["cost_increase"] * sizing_fraction(x, T["oom_margin"], "p95") for x in ws)
        low = sum(x["cost_increase"] * sizing_fraction(x, T["oom_low_margin"], "p90") for x in ws)
        low, lik = min(low, lik), min(lik, lit)
        return {"n": len(ws), "literal_o": lit, "likely_o": lik, "low_o": low, "literal": R(lit), "likely": R(lik), "low": R(low)}
    oom = [x for x in res if has_oom(x)]
    oth = [x for x in res if not has_oom(x)]
    out["res_oom"] = rsum(oom); out["res_other"] = rsum(oth)

    def rrow(x):
        dd = x["derived"]
        return {"name": x["name"], "ns": x["namespace"], "type": x["type"], "inc": R(x["cost_increase"]),
                "likely": R(x["cost_increase"] * sizing_fraction(x, T["oom_margin"], "p95")),
                "mem_cur_gib": dd["current_memory_request_mib_total"] / 1024, "mem_rec_gib": dd["recommended_memory_request_mib_total"] / 1024,
                "mem_p95_gib": dd["memory_usage_p95_mib_sum"] / 1024, "mem_p100_gib": dd["memory_usage_p100_mib_sum"] / 1024,
                "cpu_cur": dd["current_cpu_request_cores_total"], "cpu_rec": dd["recommended_cpu_request_cores_total"], "replicas": x["replicas_counts"]["avg_count"],
                "owner": owner_key(x)}
    out["res_oom_list"] = [rrow(x) for x in sorted(oom, key=lambda x: -x["cost_increase"])[:8]]
    out["res_other_list"] = [rrow(x) for x in sorted(oth, key=lambda x: -x["cost_increase"])[:8]]
    out["res_oom_owners"] = owners(oom, "cost_increase"); out["res_other_owners"] = owners(oth, "cost_increase")
    # a single group dominating the literal figure is itself a finding
    if oom:
        top1 = max(oom, key=lambda x: x["cost_increase"])
        out["res_oom_top_share"] = top1["cost_increase"] / max(sum(x["cost_increase"] for x in oom), 1e-9)

    # ---- eligibility summary
    if elig_map:
        by = collections.defaultdict(lambda: {"n": 0, "pot_o": 0.0})
        for x in w:
            s = x["_elig"] or "Unknown"
            by[s]["n"] += 1; by[s]["pot_o"] += x["potential_savings"]
        out["eligibility"] = {"provided": True, "by_status": {k: {"n": v["n"], "pot": R(v["pot_o"]), "pot_o": v["pot_o"]} for k, v in by.items()}}
    else:
        out["eligibility"] = {"provided": False}

    # ---- node pools
    ap_rx = [re.compile(p) for p in cfg["auto_provisioned_pool_patterns"]]
    gpu_rx = [re.compile(p) for p in cfg["gpu_pool_patterns"]]
    rec = {}
    pools = []
    for g in ng:
        r = g.get("recommendations") or {}
        rl = r.get("node_type_recommendations") or []
        best = max(rl, key=lambda x: x["estimated_savings"]) if (r.get("has_changes") and rl) else None
        nodes = g.get("nodes") or {}
        avg_nodes = max(nodes.get("avg") or 0, 1e-9)
        ap = any(rx.search(g["id"]) for rx in ap_rx)
        pools.append({
            "id": g["id"], "machine": (g.get("labels") or {}).get("instance_type", "unknown"), "spot": (g.get("labels") or {}).get("is_spot") == "true",
            "autoscaler": g.get("autoscaler_type", "unknown"), "kind": "auto_provisioned" if ap else "long_lived",
            "gpu": any(rx.search(g["id"]) for rx in gpu_rx),
            "nodes_min": nodes.get("min"), "nodes_avg": nodes.get("avg"), "nodes_max": nodes.get("max"),
            # Did the node count ever move over the window? A pool that never
            # flexed will not shed a node when pods shrink, so workload
            # right-sizing on it frees headroom and no money. Observed, not
            # configured: the public API does not expose consolidation policy.
            "flexed": (nodes.get("max") or 0) > (nodes.get("min") or 0),
            "wall_minutes": (g.get("running_minutes") or 0) / avg_nodes, "node_minutes": g.get("running_minutes") or 0,
            "idle_o": g["cost"]["idle_total"], "cost_o": g["cost"]["timeframe"],
            "idle": R(g["cost"]["idle_total"]), "cost": R(g["cost"]["timeframe"]),
            "cpu_req": g["cpu"]["requested"]["avg"], "cpu_used": g["cpu"]["used"]["avg"],
            "mem_req": g["mem"]["requested"]["avg"], "mem_used": g["mem"]["used"]["avg"],
            "rec": ({"save_o": best["estimated_savings"], "save": R(best["estimated_savings"]), "to": best["instance_type"], "pct": best["estimated_savings_pct"]} if best else None),
        })
    ll = sorted([p for p in pools if p["kind"] == "long_lived"], key=lambda p: -p["idle"])
    apv = [p for p in pools if p["kind"] == "auto_provisioned"]
    sm = lambda ps, k: sum(p[k] for p in ps)  # noqa: E731
    out["pools"] = {
        "n": len(pools), "idle": sm(pools, "idle"), "cost": sm(pools, "cost"), "idle_o": sm(pools, "idle_o"), "cost_o": sm(pools, "cost_o"),
        "idle_pct": sm(pools, "idle_o") / sm(pools, "cost_o") if sm(pools, "cost_o") else 0,
        "autoscalers": dict(collections.Counter(p["autoscaler"] for p in pools)),
        "pinned": [p for p in pools if not p["flexed"]],
        "pinned_n": sum(1 for p in pools if not p["flexed"]),
        "pinned_idle": sm([p for p in pools if not p["flexed"]], "idle"),
        "pinned_cost": sm([p for p in pools if not p["flexed"]], "cost"),
        "unknown_autoscaler_n": sum(1 for p in pools if p["autoscaler"] in (None, "", "unknown")),
        "unknown_autoscaler_cost": sm([p for p in pools if p["autoscaler"] in (None, "", "unknown")], "cost"),
        "long_lived": ll, "ll_idle": sm(ll, "idle"), "ll_cost": sm(ll, "cost"), "ll_n": len(ll),
        "ap_n": len(apv), "ap_idle": sm(apv, "idle"), "ap_cost": sm(apv, "cost"), "ap_idle_o": sm(apv, "idle_o"),
        "ap_median_wall_min": statistics.median([p["wall_minutes"] for p in apv]) if apv else 0,
        "ap_median_node_min": statistics.median([p["node_minutes"] for p in apv]) if apv else 0,
        "spot_idle_share": (sum(p["idle_o"] for p in pools if p["spot"]) / sm(pools, "idle_o")) if sm(pools, "idle_o") else 0,
        "gpu_n": sum(1 for p in pools if p["gpu"]), "gpu_idle": sum(p["idle"] for p in pools if p["gpu"]),
        "gpu_idle_share": (sum(p["idle_o"] for p in pools if p["gpu"]) / sm(pools, "idle_o")) if sm(pools, "idle_o") else 0,
        "ap_rec_total": sum((p["rec"] or {}).get("save", 0) for p in apv),
        "ll_rec_total": sum((p["rec"] or {}).get("save", 0) for p in ll),
    }
    mt = collections.defaultdict(lambda: {"groups": 0, "idle": 0.0, "cost": 0.0, "cpu_req": 0.0, "cpu_used": 0.0, "mem_req": 0.0, "mem_used": 0.0})
    for p in apv:
        a = mt[p["machine"]]
        a["groups"] += 1; a["idle"] += p["idle"]; a["cost"] += p["cost"]
        a["cpu_req"] += p["cpu_req"]; a["cpu_used"] += p["cpu_used"]; a["mem_req"] += p["mem_req"]; a["mem_used"] += p["mem_used"]
    shapes = []
    for k, v in sorted(mt.items(), key=lambda kv: -kv[1]["idle"]):
        ratio = v["cpu_req"] / v["cpu_used"] if v["cpu_used"] > 0 else None
        mratio = v["mem_req"] / v["mem_used"] if v["mem_used"] > 0 else None
        shapes.append({"machine": k, "groups": v["groups"], "idle": v["idle"], "cost": v["cost"], "cpu_ratio": ratio, "mem_ratio": mratio,
                       "inflated": bool(ratio and ratio >= T["request_inflation_ratio"])})
    out["shapes"] = shapes[:10]
    out["ap_totals"] = {k: sum(v[k] for v in mt.values()) for k in ("cpu_req", "cpu_used", "mem_req", "mem_used")}
    jw = [x for x in w if (x["type"] in GROUPED_TYPES or x["type"] in ("Job", "CronJob")) and x.get("derived")]
    out["job_req_vs_rec"] = {"n": len(jw),
                             "cpu_cur": sum(x["derived"]["current_cpu_request_cores_total"] for x in jw), "cpu_rec": sum(x["derived"]["recommended_cpu_request_cores_total"] for x in jw),
                             "mem_cur_gib": sum(x["derived"]["current_memory_request_mib_total"] for x in jw) / 1024, "mem_rec_gib": sum(x["derived"]["recommended_memory_request_mib_total"] for x in jw) / 1024}
    out["shapes_inflated_idle_share"] = (sum(s["idle"] for s in shapes if s["inflated"]) / out["pools"]["ap_idle"]) if out["pools"]["ap_idle"] else 0

    # ---- unevictable
    ung = jl(d / "unevictable-full.jsonl") or jl(d / "unevictable-topgroups.jsonl")
    pool_kind = {p["id"]: p["kind"] for p in pools}
    blk = [p for p in ung if p.get("reasons") and p.get("phase") == "Running"]
    by_code = collections.defaultdict(lambda: {"all": 0, "ll": 0, "nm": 0, "by_type": collections.Counter(), "ns": collections.Counter(), "fix": None, "risk": None, "details": None})
    by_pool = collections.defaultdict(collections.Counter)
    for p in blk:
        r0 = p["reasons"][0]
        code = r0.get("reason_code") or re.sub(r"\W+", "_", r0.get("reason", "unknown").lower())
        a = by_code[code]
        a["all"] += 1
        ng_id = (p.get("spec") or {}).get("node_group")
        long_lived = pool_kind.get(ng_id) == "long_lived"
        a["ll"] += 1 if long_lived else 0
        a["nm"] += 0 if managed_ns(p["namespace"], cloud, extra_managed) else 1
        a["by_type"][p["workload"]["type"]] += 1
        a["ns"][p["namespace"]] += 1
        a["label"] = r0.get("reason")
        a["fix"] = a["fix"] or (r0.get("remediation") or {}).get("fix_summary")
        a["risk"] = a["risk"] or (r0.get("remediation") or {}).get("risk")
        a["details"] = a["details"] or r0.get("details")
        by_pool[ng_id][code] += 1
    out["unevictable"] = {
        "listed": len(ung), "blocking": len(blk), "blocking_ll": sum(v["ll"] for v in by_code.values()),
        "partial": bool(not exists(d / "unevictable-full.jsonl") and exists(d / "unevictable-topgroups.jsonl")),
        "codes": {c: {"label": REASON_INFO.get(c, (v["label"],))[0] if c in REASON_INFO else v["label"], "all": v["all"], "ll": v["ll"], "nm": v["nm"],
                      "by_type": dict(v["by_type"]), "ns": dict(v["ns"].most_common(4)), "complexity": REASON_INFO.get(c, (None, "Medium", None))[1],
                      "fix": v["fix"] or REASON_INFO.get(c, (None, None, "review"))[2], "risk": v["risk"], "details": v["details"]} for c, v in by_code.items()},
        "by_pool": {k: dict(v) for k, v in by_pool.items() if k and pool_kind.get(k) == "long_lived"},
    }
    # deployments/statefulsets carrying opt-out on long-lived pools = "stale annotation" candidates
    oo = [p for p in blk if (p["reasons"][0].get("reason_code") == "opt_out_annotation")]
    cand = [p for p in oo if p["workload"]["type"] in ("Deployment", "StatefulSet") and pool_kind.get((p.get("spec") or {}).get("node_group")) == "long_lived"]
    out["optout"] = {"n": len(oo), "by_type": dict(collections.Counter(p["workload"]["type"] for p in oo)),
                     "controller_pods": len(cand), "controller_names": sorted({p["workload"]["name"] for p in cand}),
                     "annotation": (oo[0]["reasons"][0].get("details") if oo else None),
                     "pools": sorted({(p.get("spec") or {}).get("node_group") for p in cand if (p.get("spec") or {}).get("node_group")})}

    # ---- audit
    audit = jl(d / "audit.jsonl")
    ex = collections.Counter(str(a.get("execution") or a.get("execution_type") or a.get("type") or "unknown").lower() for a in audit)
    out["audit"] = {"n": len(audit), "by_exec": dict(ex), "inplace": sum(v for k, v in ex.items() if k.startswith("inplace") or "in-place" in k or "in_place" in k)}

    # ---- policy hygiene
    out["headroom_policy"] = [k for k in out["policies"] if "headroom" in k.lower()]
    return out


def _load_one(args):
    cfg, cluster = args
    return load_cluster(cfg, cluster)


def load_all(cfg, parallel=True):
    clusters = cfg["clusters"]
    if parallel and len(clusters) > 1 and not __import__("os").environ.get("PSPLAN_SEQUENTIAL"):
        with ProcessPoolExecutor(max_workers=min(len(clusters), 4)) as ex:
            res = list(ex.map(_load_one, [(cfg, c) for c in clusters]))
    else:
        res = [load_cluster(cfg, c) for c in clusters]
    return res


def trends_summary(path):
    """Optional: daily cost CSV -> per-cluster variability. Tolerant column detection."""
    import csv
    p = Path(path)
    if not p.exists():
        return {}
    rows = list(csv.DictReader(p.open()))
    if not rows:
        return {}
    cols = rows[0].keys()
    date_c = next((c for c in cols if re.search(r"date|day", c, re.I)), None)
    cl_c = next((c for c in cols if re.search(r"cluster", c, re.I)), None)
    cost_c = next((c for c in cols if re.search(r"cost", c, re.I)), None)
    if not (date_c and cost_c):
        return {}
    by = collections.defaultdict(list)
    for r in rows:
        try:
            by[r[cl_c] if cl_c else "all"].append((r[date_c][:10], float(r[cost_c])))
        except (ValueError, TypeError):
            continue
    out = {}
    from datetime import date
    for k, series in by.items():
        vals = [v for _, v in series]
        if len(vals) < 5:
            continue
        mean = statistics.mean(vals)
        cv = statistics.pstdev(vals) / mean if mean else 0
        wk = [v for d_, v in series if date.fromisoformat(d_).weekday() < 5]
        we = [v for d_, v in series if date.fromisoformat(d_).weekday() >= 5]
        out[k] = {"days": len(vals), "mean": mean, "cv": cv, "peak_over_mean": max(vals) / mean if mean else 0,
                  "weekend_ratio": (statistics.mean(we) / statistics.mean(wk)) if (we and wk and statistics.mean(wk)) else None,
                  "variable": cv > 0.25}
    return out
