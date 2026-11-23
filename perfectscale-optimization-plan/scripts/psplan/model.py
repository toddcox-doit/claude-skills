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
    "RestartsObserved": "Restarts observed", "HPAAtMaxReplicasObserved": "HPA at max replicas",
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

# Optimization Policy (docs.perfectscale.io): a real, documented cost<->resiliency dial, independent
# per CPU and Memory, settable per cluster, namespace, or workload - a workload's own setting overrides
# its cluster's. The ladder is the same four values at every level: MaxSavings / Balanced /
# ExtraHeadroom / MaxHeadroom (there is no separate "MaxEconomy"). Matched case-insensitively since
# collected values are not guaranteed to carry the product's own casing.
POLICY_UNDER = {"maxsavings"}
POLICY_OVER = {"extraheadroom", "maxheadroom"}

# POV automation kickoff (pov.py). Controller kinds a WorkloadAutomationConfig targetRef or a
# NamespaceAutomationConfig workloadTypes key accepts (docs.perfectscale.io, "Configuring Automation
# with CRD"). Anything else - GroupedJob/GroupedPod custom groupings, bare pods ("<none>") - needs a
# custom-type CR built from a grouping rule first, so it is counted but kept out of the ranked table.
POV_CR_TYPES = ("Deployment", "StatefulSet", "DaemonSet", "CronJob", "Job", "Rollout")
# PerfectScale indicator severity runs 0-4; the UI labels 1-4 Low / Medium / High / Critical.
RISK_LEVEL = {0: "none", 1: "low", 2: "medium", 3: "high", 4: "critical"}
# Service-mesh and proxy components: resized like anything else, but with extra headroom (Wave 1b).
MESH_RX = re.compile(r"istio|linkerd|envoy|consul-connect", re.I)


def _hpa_part(metric, targets):
    val = next(iter(targets.values()), None) if isinstance(targets, dict) else targets
    kind = next(iter(targets), "") if isinstance(targets, dict) else ""
    if metric in ("cpu", "memory") and kind == "utilization":
        return f"{'CPU' if metric == 'cpu' else 'memory'} {val:g}%"
    return f"{metric} {val:g}" if isinstance(val, (int, float)) else metric


def hpa_class(r2):
    """(class, detail) for a workload from its public API v2 row; (None, None) without one.

    class: "none" (no HPA), "container" (ContainerResource triggers only - a resize keeps the
    trigger on the container it measures), "pod_resource" (pod-level CPU/memory utilization - a
    smaller request moves the scaling point), "custom" (custom/external metrics only). v2 carries no
    min/max replicas, so an HPA pinned at fixed replicas reads as its trigger type. The caller
    relabels "pod_resource" as "pinned" for namespaces listed in hpa_pinned_namespaces."""
    if not r2:
        return None, None
    t = (r2.get("scaling") or {}).get("hpaTargets") or {}
    conts = r2.get("containers") or {}
    conts = conts.values() if isinstance(conts, dict) else conts
    ct = {k: v for c in conts for k, v in ((c or {}).get("hpaTargets") or {}).items()}
    if not t and not ct:
        return "none", ""
    parts = [_hpa_part(k, v) for k, v in sorted(t.items(), key=lambda kv: (kv[0] not in ("cpu", "memory"), kv[0]))]
    parts += [_hpa_part(k, v) + " (container)" for k, v in sorted(ct.items())]
    if set(t) & {"cpu", "memory"}:
        cls = "pod_resource"
    elif ct:
        cls = "container"
    else:
        cls = "custom"
    return cls, ", ".join(parts)


def pov_wave(x):
    """The Command Center's automation waves. Wave 1: no HPA or container-level triggers. Wave 1b:
    stateful or mesh components (extra headroom). Review: HPA on custom metrics only, or automation
    not supported. Hold: pod-level CPU/memory HPA. Workloads with no HPA data fall in Wave 1/1b and
    the page says the HPA is unknown."""
    if x.get("_auto_status") == "NotSupported":
        return "Review"
    h = x.get("_hpa_class")
    if h == "pod_resource":
        return "Hold"
    if h == "custom":
        return "Review"
    if x["_lane"] == "stateful" or x["type"] == "StatefulSet" or MESH_RX.search(x["name"]):
        return "1b"
    return "1"


# ------------------------------------------------------------------ small helpers
def humanize(n):
    """Indicator code -> label. Unmapped codes are split on word boundaries with acronyms kept
    whole ("HPAAtMaxReplicasObserved" -> "HPA at max replicas observed"), never letter by letter."""
    s = ISSUE.get(n)
    if s:
        return s
    words = re.findall(r"[A-Z]+(?=[A-Z][a-z]|[0-9]|$)|[A-Z]?[a-z]+|[0-9]+|[A-Z]+", n) or [n]
    words = [w if (len(w) > 1 and w.isupper()) else w.lower() for w in words]
    t = " ".join(words)
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


def risk_sev(w):
    """Highest risk-indicator severity (0-4) on the workload or any container. An OOM signal is
    never read as less than High, whatever severity the indicator itself carries."""
    s = max([int(i.get("severity") or 0) for i in allind(w) if i.get("type") == "risk"] or [0])
    return max(min(s, 4), 3) if has_oom(w) else min(s, 4)


def pov_rank(w, R, T, top_n=50, savings_weight=0.6):
    """Rank a cluster's workloads for the POV automation kickoff table.

    Score (0-100) = savings_weight x (this workload's potential savings / the largest in the
    candidate pool) + (1 - savings_weight) x (risk severity / 4). Savings lead because the CRs
    generated from the table are cost-optimization configs; risk still lifts a workload that is
    already failing, so the POV starts with the reliability picture in view rather than after it.

    Candidates exclude platform-managed namespaces (PerfectScale does not automate kube-system by
    default, and the plan holds those out the same way), workloads the tenant config holds out of
    automation, and kinds no automation CR can target. Each exclusion is counted, never dropped
    silently. A workload with neither savings nor risk scores 0 and is left out: there is nothing
    for automation to do there.
    """
    pool = []
    held = collections.defaultdict(lambda: {"n": 0, "pot": 0.0})
    held_types = collections.Counter()
    for x in w:
        if x["_lane"] in ("managed", "held_out"):
            k = x["_lane"]
        elif x["type"] not in POV_CR_TYPES:
            k = "unsupported_type"
            held_types[x["type"] or "<none>"] += 1
        else:
            pool.append(x)
            continue
        held[k]["n"] += 1
        held[k]["pot"] += R(max(x["potential_savings"], 0.0))
    top_pot = max([x["potential_savings"] for x in pool] or [0.0])
    sw = min(max(float(savings_weight), 0.0), 1.0)
    rows = []
    for x in pool:
        sev = risk_sev(x)
        share = max(x["potential_savings"], 0.0) / top_pot if top_pot > 0 else 0.0
        score = round(100 * (sw * share + (1 - sw) * sev / 4), 1)
        if score <= 0:
            continue
        risky = (high_sev(x) or has_oom(x)) and x["cost_increase"] > 0
        dd = x["derived"]
        rows.append({
            "name": x["name"], "ns": x["namespace"], "type": x["type"], "lane": x["_lane"],
            "cost": R(x["cost"]), "waste": R(x["waste"]), "pot": R(max(x["potential_savings"], 0.0)),
            "risk_sev": sev, "risk": RISK_LEVEL[sev], "oom": has_oom(x), "issue": primary_issue(x),
            "res_likely": R(x["cost_increase"] * sizing_fraction(x, T["oom_margin"], "p95")) if risky else 0.0,
            "policy": x.get("optimization_policy") or "unset", "jvm": bool(x.get("_jvm")),
            "hpa": x.get("_hpa"), "replicas": x["replicas_counts"]["avg_count"],
            "replicas_max": x["replicas_counts"].get("max_count", x["replicas_counts"]["avg_count"]),
            "hpa_class": x.get("_hpa_class"), "hpa_detail": x.get("_hpa_detail"), "wave": pov_wave(x),
            "auto_status": x.get("_auto_status"), "immature": bool(x.get("_immature")),
            "cpu_cur": dd["current_cpu_request_cores_total"], "cpu_rec": dd["recommended_cpu_request_cores_total"],
            "mem_cur_gib": dd["current_memory_request_mib_total"] / 1024, "mem_rec_gib": dd["recommended_memory_request_mib_total"] / 1024,
            "score": score,
        })
    rows.sort(key=lambda r: (-r["score"], -r["pot"], r["ns"], r["name"]))
    rows = rows[:top_n]
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"rows": rows, "top_n": top_n, "savings_weight": sw, "pool_n": len(pool),
            "held": {k: dict(v) for k, v in held.items()}, "held_types": dict(held_types.most_common()),
            **pov_namespaces(w, R)}


def pov_namespaces(w, R):
    """Every namespace, for the kickoff page's namespace picker - not only the ones the ranked
    table touches: a namespace of many small workloads can carry more savings than any one ranked
    workload and never appear in the top N.

    A NamespaceAutomationConfig covers every workload of its enabled kinds in the namespace, so
    each namespace also lists the workloads that must NOT ride along: held out by the tenant
    config, stateful (the plan's manual lane), or JVM (the heap flag has to move with the memory
    request). The generator writes a Disabled WorkloadAutomationConfig for each one whose kind
    the namespace config enables - workload settings override namespace settings.

    `auto` is broken out per kind so the page can recompute a namespace's figures for whichever
    controller types are selected. Platform-managed namespaces are listed (shown, never
    selectable); a namespace with nothing a namespace config could automate is only counted.
    """
    nsd = {}
    for x in w:
        a = nsd.get(x["namespace"])
        if a is None:
            a = nsd[x["namespace"]] = {"ns": x["namespace"], "managed": x["_lane"] == "managed", "auto": {}, "excl": [],
                                       "types": []}
        if x["type"] not in POV_CR_TYPES:
            continue
        if a["managed"]:
            # shown greyed out only while one of the kinds it runs is selected, like every other row
            if x["type"] not in a["types"]:
                a["types"].append(x["type"])
            continue
        reason = ("held_out" if x["_lane"] == "held_out" else "stateful" if x["_lane"] == "stateful"
                  else "jvm" if x.get("_jvm") else "hpa" if x.get("_hpa_class") == "pod_resource" else None)
        if reason:
            a["excl"].append({"name": x["name"], "type": x["type"], "reason": reason,
                              "pot": R(max(x["potential_savings"], 0.0))})
            if reason == "hpa":
                # What the namespace would add if the reviewer confirms these HPAs are pinned on the
                # kickoff page: the page folds it into `auto` and drops the HPA exclusions.
                h = a.setdefault("hpa_auto", {}).setdefault(x["type"], {"n": 0, "pot": 0.0, "risk": 0, "waste": 0.0})
                h["n"] += 1
                h["pot"] += R(max(x["potential_savings"], 0.0))
                h["waste"] += R(max(x["waste"], 0.0))
                h["risk"] += 1 if risk_sev(x) >= 3 else 0
            continue
        t = a["auto"].setdefault(x["type"], {"n": 0, "pot": 0.0, "risk": 0, "waste": 0.0})
        t["n"] += 1
        t["pot"] += R(max(x["potential_savings"], 0.0))
        t["waste"] += R(max(x["waste"], 0.0))
        t["risk"] += 1 if risk_sev(x) >= 3 else 0
    for a in nsd.values():
        a["excl"].sort(key=lambda e: (e["type"], e["name"]))
        a["pot"] = sum(t["pot"] for t in a["auto"].values())
        # Every automatable workload kept out (all behind a pod CPU/memory HPA, say): there is nothing
        # for a namespace config to enable, but dropping the row hid the largest namespace of a tenant
        # whose workloads all sat behind such HPAs. List it, not selectable, with what holds it back.
        a["held"] = not a["managed"] and not a["auto"] and bool(a["excl"])
        if a["held"]:
            a["held_pot"] = sum(e["pot"] for e in a["excl"])
            a["held_reasons"] = dict(collections.Counter(e["reason"] for e in a["excl"]).most_common())
            a["types"] = sorted({e["type"] for e in a["excl"]}, key=POV_CR_TYPES.index)
    listed = [a for a in nsd.values() if (a["managed"] and a["types"]) or a["auto"] or a["held"]]
    listed.sort(key=lambda a: (a["managed"], a["held"], -(a.get("held_pot") or a["pot"]), a["ns"]))
    hpa = [e for a in nsd.values() if not a["managed"] for e in a["excl"] if e["reason"] == "hpa"]
    hpa_ns = collections.Counter(a["ns"] for a in nsd.values() if not a["managed"] for e in a["excl"] if e["reason"] == "hpa")
    return {"namespaces": listed,
            "ns_nothing_to_automate": sum(1 for a in nsd.values() if not a["managed"] and not a["auto"] and not a["held"]),
            "hpa_held": {"n": len(hpa), "pot": sum(e["pot"] for e in hpa),
                         "namespaces": [ns for ns, _ in hpa_ns.most_common()]}}


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


def common_ns_prefix(namespaces, min_len=3):
    """The namespace prefix a custom grouping filter can match on, or None.

    Cut at the last separator so the prefix is a real segment boundary
    (`tenant-`, not `tenant-3d`), and require it to cover every namespace in the
    group. Returns None when the namespaces share no usable stem, which is the
    honest answer - the filter then has to be written on the workload name.
    """
    ns = sorted(namespaces)
    if len(ns) < 2:
        return None
    first, last = ns[0], ns[-1]
    i = 0
    while i < min(len(first), len(last)) and first[i] == last[i]:
        i += 1
    p = first[:i]
    cut = max(p.rfind("-"), p.rfind("_"))
    p = p[:cut + 1] if cut >= 0 else p
    return p if len(p.rstrip("-_")) >= min_len else None


# ------------------------------------------------------------------ node groups
def load_node_groups(d):
    o = jload(d / "nodegroups.json")
    if isinstance(o, dict):
        return o.get("node_groups") or o.get("nodeGroups") or o.get("data") or []
    if isinstance(o, list):
        return o
    return jl(d / "nodegroups.jsonl")


# ------------------------------------------------------------------ Karpenter NodePool structural diff
# PerfectScale's public API returns the full Karpenter NodePool custom resource (not a proprietary
# shape) for both `current_config` and `recommended_config` on a Karpenter-managed node group's
# recommendation - the same schema Karpenter itself uses. There is no pre-labeled `changes[]` format
# to lean on (the field exists but was never observed non-empty against a live tenant, so its shape
# is unconfirmed); the classification below is a structural diff on the field paths docs.perfectscale.io
# names for Karpenter optimization, not a passthrough of anything the API itself labels.
#
# Every lookup here is defensive: an unexpected shape (not a dict, not a list, a missing branch) is
# treated as "not found" rather than raised, and `diff_karpenter_config` never lets an exception
# escape - a diff it cannot make is dropped, not a crash. A field present on one side and absent on
# the other is itself a valid, reported change (e.g. `spec.limits.memory` newly added).
KARP_PIN_MAX = 3  # a requirement this narrow on instance-type reads as "pinned", not "selected"
KARP_SELECTOR_KEYS = (
    ("karpenter.k8s.aws/instance-family", "instance_family_modernization"),
    ("karpenter.k8s.aws/instance-category", "instance_category_change"),
    ("karpenter.k8s.aws/instance-generation", "instance_generation_flexibility"),
)


def _karp_get(d, *path):
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _karp_requirements(cfg):
    """key -> sorted values for spec.template.spec.requirements. Tolerant of any shape that is not
    a well-formed requirements list - a malformed entry is dropped silently, not raised."""
    out = {}
    reqs = _karp_get(cfg, "spec", "template", "spec", "requirements")
    if not isinstance(reqs, list):
        return out
    for r in reqs:
        if not isinstance(r, dict):
            continue
        k, vals = r.get("key"), r.get("values")
        if not k or not isinstance(vals, list):
            continue
        out[k] = sorted(str(v) for v in vals)
    return out


def diff_karpenter_config(cur, rec):
    """Structural diff of a Karpenter NodePool current_config vs recommended_config, mapped onto the
    8 recommendation types docs.perfectscale.io names for Karpenter optimization. Returns a list of
    {"kind", "from", "to"} dicts; kind is one of consolidation_policy, consolidate_after, limits_cpu,
    limits_memory, static_nodepool_detection, instance_type_pinning_prevention,
    instance_family_modernization, instance_category_change, instance_generation_flexibility (or the
    generic instance_type_values_changed fallback below).

    #3 (Static NodePool Detection) and #7 (Instance Type Pinning Prevention) are the one genuinely
    ambiguous pair: both start from the same observation (current_config pins
    node.kubernetes.io/instance-type to a handful of values). They are told apart by what the
    recommended side does about it - broadens the *same* key (#3: still a pinned-style list, just a
    bigger one) versus drops that key in favor of a broader selector like family/category (#7: a
    different kind of requirement entirely). That is a reasonable, defensible split on the data
    available, not an attempt to reproduce PerfectScale's own internal classification, which is not
    observable from this API.
    """
    if not isinstance(cur, dict) or not isinstance(rec, dict):
        return []
    changes = []
    try:
        cp_cur = _karp_get(cur, "spec", "disruption", "consolidationPolicy")
        cp_rec = _karp_get(rec, "spec", "disruption", "consolidationPolicy")
        if cp_cur != cp_rec and (cp_cur or cp_rec):
            changes.append({"kind": "consolidation_policy", "from": cp_cur, "to": cp_rec})
    except Exception:
        pass
    try:
        ca_cur = _karp_get(cur, "spec", "disruption", "consolidateAfter")
        ca_rec = _karp_get(rec, "spec", "disruption", "consolidateAfter")
        if ca_cur != ca_rec and (ca_cur or ca_rec):
            changes.append({"kind": "consolidate_after", "from": ca_cur, "to": ca_rec})
    except Exception:
        pass
    for res in ("cpu", "memory"):
        try:
            lc = _karp_get(cur, "spec", "limits", res)
            lr = _karp_get(rec, "spec", "limits", res)
            if lc != lr and (lc or lr):
                changes.append({"kind": f"limits_{res}", "from": lc, "to": lr})
        except Exception:
            pass
    try:
        cur_reqs, rec_reqs = _karp_requirements(cur), _karp_requirements(rec)
        it_key = "node.kubernetes.io/instance-type"
        it_cur, it_rec = cur_reqs.get(it_key), rec_reqs.get(it_key)
        if it_cur != it_rec:
            if it_cur and len(it_cur) <= KARP_PIN_MAX and not it_rec:
                kind = "instance_type_pinning_prevention"          # #7: key dropped for a broader selector
            elif it_cur and it_rec and len(it_rec) > len(it_cur):
                kind = "static_nodepool_detection"                 # #3: same key, broadened
            else:
                kind = "instance_type_values_changed"               # fallback: a change we can't cleanly place
            changes.append({"kind": kind, "from": it_cur, "to": it_rec})
        for key, kind in KARP_SELECTOR_KEYS:
            c, r_ = cur_reqs.get(key), rec_reqs.get(key)
            if c != r_:
                changes.append({"kind": kind, "from": c, "to": r_})
    except Exception:
        pass
    return changes


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
                                    "min_history_days", "cloud", "eligibility_file", "hpa_file",
                                    "jvm_patterns", "nonprod_patterns", "pov_top_n", "pov_savings_weight",
                                    # changes each workload's HPA class: a stale cache would keep pinned workloads on Hold
                                    "hpa_pinned_namespaces")}
    h.update(json.dumps(relevant, sort_keys=True).encode())
    return h.hexdigest()[:16]


def load_cluster(cfg, cluster, use_cache=True):
    name, uid = cluster["name"], cluster["uid"]
    d = cfg.data(name)
    files = [d / x for x in ("summary.json", "workloads-all.jsonl", "nodegroups.json", "nodegroups.jsonl",
                             "unevictable-full.jsonl", "unevictable-topgroups.jsonl", "audit.jsonl", "cluster.json",
                             "workloads-v2.json")]
    if cfg.get("eligibility_file"):
        files.append(Path(cfg["eligibility_file"]))
    if cfg.get("hpa_file"):
        files.append(Path(cfg["hpa_file"]))
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
    pinned_rx = re.compile("|".join(cfg["hpa_pinned_namespaces"]), re.I) if cfg.get("hpa_pinned_namespaces") else None
    nonprod_rx = re.compile("|".join(cfg["nonprod_patterns"]), re.I) if cfg["nonprod_patterns"] else None
    excl = [e.lower() for e in cfg["exclusions"]]

    # ---- eligibility (optional enrichment)
    elig_map = {}
    if cfg.get("eligibility_file"):
        ej = jload(cfg["eligibility_file"], {}) or {}
        elig_map = ej.get(uid) or ej.get(name) or {}

    # ---- HPA triggers (optional enrichment). Same PodFit-zoom-in-only shape as eligibility above:
    # no public-API field exists for a workload's HPA presence or CPU/Memory trigger thresholds, so
    # this is populated only when a browser pass wrote one. Absent => every workload's "_hpa" stays
    # None and the plan states HPA status as unconfirmed, same as eligibility without eligibility_file.
    hpa_map = {}
    if cfg.get("hpa_file"):
        hj = jload(cfg["hpa_file"], {}) or {}
        hpa_map = hj.get(uid) or hj.get(name) or {}

    # ---- public API v2 (optional; the collector writes it when API credentials exist): HPA
    # triggers, automation status, maturity - keyed by workload id, which matches v1 one-to-one.
    v2 = {r.get("id"): r for r in ((jload(d / "workloads-v2.json", {}) or {}).get("data") or []) if r.get("id")}
    elig_source = "browser" if elig_map else None
    if not elig_map and v2:
        # The same automation-status values the PodFit browser pass reads, now from the API.
        elig_map = {i: (r.get("automation") or {}).get("status") for i, r in v2.items()
                    if (r.get("automation") or {}).get("status")}
        elig_source = "api_v2" if elig_map else None

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
        x["_hpa"] = hpa_map.get(x.get("id"))
        r2 = v2.get(x.get("id"))
        x["_hpa_class"], x["_hpa_detail"] = hpa_class(r2)
        if x["_hpa_class"] == "pod_resource" and pinned_rx and pinned_rx.search(x["namespace"]):
            # Customer-confirmed min == max: the HPA cannot scale, so a resize cannot move it.
            x["_hpa_class"], x["_hpa_detail"] = "pinned", (x["_hpa_detail"] or "CPU/memory") + "; pinned (min = max, confirmed)"
        x["_auto_status"] = (r2.get("automation") or {}).get("status") if r2 else None
        x["_immature"] = bool(r2) and not ((r2.get("maturity") or {}).get("overall") or {}).get("isMature", True)

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
    ns = collections.defaultdict(lambda: {"n": 0, "cost": 0.0, "waste": 0.0, "pot": 0.0, "risk_n": 0, "oom_n": 0, "res_likely": 0.0, "res_literal": 0.0})
    for x in w:
        a = ns[x["namespace"]]
        a["n"] += 1; a["cost"] += x["cost"]; a["waste"] += x["waste"]; a["pot"] += x["potential_savings"]
    # out["namespaces"] is finalized after the resiliency section below, once `ns` also
    # carries each namespace's risk_n/oom_n/res_likely/res_literal.

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
        # Behind a pod CPU/memory HPA (public API v2): a smaller request moves the scaling point, so
        # these savings are not reachable by switching automation on - the POV page puts them on
        # Hold. Counted within the same eligible set as pot_eligible so the two subtract cleanly.
        hh = [x for x in ws if x.get("_hpa_class") == "pod_resource" and (not elig_map or x["_elig"] in elig_ok)]
        lanes[ln] = {"pot_eligible_o": pe, "pot_eligible": (R(pe) if pe is not None else None),
                     "n_hpa_hold": len(hh), "pot_hpa_hold_o": sum(x["potential_savings"] for x in hh),
                     "pot_hpa_hold": R(sum(x["potential_savings"] for x in hh)),
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
    # ---- replicated controller groups (one workload name repeated across many namespaces)
    # A per-tenant / per-customer namespace model produces the same controller name hundreds or
    # thousands of times. Each copy is its own recommendation surface, so without a custom grouping
    # rule the automation ask is N decisions instead of one. The dollars already sit in the
    # controller lane and are claimed by the automation waves - this block never adds a money base,
    # it only names where that money is concentrated and how many objects it is spread over.
    rep = collections.defaultdict(list)
    for x in w:
        if x["_lane"] == "controller":
            rep[x["name"]].append(x)
    rgroups = []
    for nm, xs in rep.items():
        nss = {x["namespace"] for x in xs}
        if len(nss) < T["replicated_group_min"]:
            continue
        pot_g = sum(x["potential_savings"] for x in xs)
        if R(pot_g) < T["replicated_group_pot_min_usd"]:
            continue
        sample = max(xs, key=lambda x: x["potential_savings"])
        sd = sample["derived"]
        rgroups.append({
            "name": nm, "n": len(xs), "ns_count": len(nss), "ns_prefix": common_ns_prefix(nss),
            "types": sorted({x["type"] for x in xs}),
            "cost": R(sum(x["cost"] for x in xs)), "waste": R(sum(x["waste"] for x in xs)),
            "pot": R(pot_g), "pot_o": pot_g,
            "cpu_cur": sd["current_cpu_request_cores_total"], "cpu_rec": sd["recommended_cpu_request_cores_total"],
            "cpu_p95": sd["cpu_usage_p95_cores_sum"], "cpu_p100": sd["cpu_usage_p100_cores_sum"],
            "mem_cur_gib": sd["current_memory_request_mib_total"] / 1024,
            "mem_rec_gib": sd["recommended_memory_request_mib_total"] / 1024,
            "mem_p95_gib": sd["memory_usage_p95_mib_sum"] / 1024,
            "throttled": any(i["name"] == "CpuThrottling" for i in allind(sample)),
        })
    rgroups.sort(key=lambda g: -g["pot"])
    out["replicated"] = {"groups": rgroups, "n_groups": len(rgroups),
                         "instances": sum(g["n"] for g in rgroups), "pot": R(sum(g["pot_o"] for g in rgroups)),
                         "pot_o": sum(g["pot_o"] for g in rgroups),
                         "ctl_pot": lanes["controller"]["pot"]}

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
    out["controllers"] = [{"name": x["name"], "ns": x["namespace"], "type": x["type"], "pot": R(x["potential_savings"]), "pot_o": x["potential_savings"], "owner": owner_key(x),
                           "hpa": x.get("_hpa_class"),
                           # never above its average over the window: a hint (not proof) that an HPA is pinned
                           "flat": x["replicas_counts"].get("max_count", x["replicas_counts"]["avg_count"]) <= x["replicas_counts"]["avg_count"]}
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

    # fold resiliency into the same per-namespace accumulator used for cost/waste/pot above,
    # so a namespace table can show cost, waste and resiliency side by side without a second pass
    for x in res:
        a = ns[x["namespace"]]
        a["risk_n"] += 1
        a["oom_n"] += 1 if has_oom(x) else 0
        a["res_likely"] += x["cost_increase"] * sizing_fraction(x, T["oom_margin"], "p95")
        a["res_literal"] += x["cost_increase"]
    out["namespaces"] = sorted(({"ns": k, "n": v["n"], "cost": R(v["cost"]), "waste": R(v["waste"]), "pot": R(v["pot"]),
                                 "risk_n": v["risk_n"], "oom_n": v["oom_n"], "res_likely": R(v["res_likely"]), "res_literal": R(v["res_literal"]),
                                 "waste_o": v["waste"], "res_likely_o": v["res_likely"]} for k, v in ns.items()), key=lambda r: -r["waste"])

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

    # ---- policy mismatches: a workload's own Optimization Policy fighting its risk profile.
    # This is per-workload, unlike out["policies"]/out["headroom_policy"] above (cluster-aggregate
    # counts only - they cannot see that one specific workload is set differently from its cluster).
    # Under-protected reuses `res`, the same high_sev(x)/has_oom(x) risk population the resiliency
    # section above already built, rather than re-deriving risk from scratch.
    def prow(x):
        return {"name": x["name"], "ns": x["namespace"], "type": x["type"],
                "policy": x.get("optimization_policy"), "cost_increase": R(x["cost_increase"]),
                "cost": R(x["cost"]), "oom": has_oom(x)}
    under_protected = [x for x in res if (x.get("optimization_policy") or "").lower() in POLICY_UNDER]
    over_protected = [x for x in w if (x.get("optimization_policy") or "").lower() in POLICY_OVER
                      and nonprod_rx and (nonprod_rx.search(x["name"]) or nonprod_rx.search(x["namespace"]))]
    out["policy_mismatches"] = {
        "under_protected": [prow(x) for x in sorted(under_protected, key=lambda x: -x["cost_increase"])],
        "over_protected": [prow(x) for x in sorted(over_protected, key=lambda x: -x["cost"])],
    }

    out["api_v2"] = {"provided": bool(v2), "matched": sum(1 for x in w if x.get("_hpa_class") is not None),
                     "hpa_n": sum(1 for x in w if x.get("_hpa_class") not in (None, "none"))}

    # ---- POV automation kickoff ranking (rendered by pov.py, one page per cluster)
    out["pov"] = pov_rank(w, R, T, top_n=int(cfg["pov_top_n"]), savings_weight=cfg["pov_savings_weight"])

    # ---- eligibility summary
    if elig_map:
        by = collections.defaultdict(lambda: {"n": 0, "pot_o": 0.0})
        for x in w:
            s = x["_elig"] or "Unknown"
            by[s]["n"] += 1; by[s]["pot_o"] += x["potential_savings"]
        out["eligibility"] = {"provided": True, "source": elig_source, "by_status": {k: {"n": v["n"], "pot": R(v["pot_o"]), "pot_o": v["pot_o"]} for k, v in by.items()}}
    else:
        out["eligibility"] = {"provided": False}

    # ---- HPA summary (optional enrichment). `hpa_workloads` carries just enough identity (id/name/ns)
    # for a rule to append an evidence caveat on the matching action - this is informational enrichment
    # of existing right-sizing evidence, not a new scoring input, so no dollars are derived from it here.
    if hpa_map:
        hpa_workloads = [{"id": x["id"], "name": x["name"], "namespace": x["namespace"]} for x in w if x.get("_hpa")]
        out["hpa"] = {"provided": True, "n_hpa": len(hpa_workloads), "workloads": hpa_workloads}
    else:
        out["hpa"] = {"provided": False, "n_hpa": 0, "workloads": []}

    # ---- node pools
    ap_rx = [re.compile(p) for p in cfg["auto_provisioned_pool_patterns"]]
    gpu_rx = [re.compile(p) for p in cfg["gpu_pool_patterns"]]
    rec = {}
    pools = []
    for g in ng:
        r = g.get("recommendations") or {}
        rl = r.get("node_type_recommendations") or []
        best = max(rl, key=lambda x: x["estimated_savings"]) if (r.get("has_changes") and rl) else None
        # Karpenter-managed pools carry a structurally different recommendation shape (full NodePool
        # current_config/recommended_config, not a node_type_recommendations list) - see
        # diff_karpenter_config above. Only attempted when the API says there is something to diff;
        # a config shape this doesn't expect yields an empty list rather than raising (never blocks
        # the rest of this pool's record).
        karp_changes = diff_karpenter_config(r.get("current_config"), r.get("recommended_config")) \
            if (r.get("type") == "karpenter" and r.get("has_changes")) else []
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
            "karpenter_changes": karp_changes,
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
