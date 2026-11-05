"""Synthetic tenant fixtures (collector-output format). Deterministic; no customer data.

Tenant 'acme' (AWS, Karpenter, two clusters):
  prod-a  : 35 days of history (full window), no automation ever run. Kafka + Elasticsearch workloads, Airflow
            jobs, missing memory limits, an OOM, PDB / opt-out / local-storage blockers, Karpenter pools with
            recommendations, one extraHeadroom policy, kube-system managed add-ons.
  dev-b   : 8 days of history (early), automation already ran (audit entries), nonprod by name.
"""
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
UIDS = {"prod-a": "11111111-aaaa-4aaa-8aaa-111111111111", "dev-b": "22222222-bbbb-4bbb-8bbb-222222222222"}


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def wl(name, ns, typ, cost, waste, pot, first_days, last=NOW, policy="balanced", inds=None, cont_inds=None, inc=0.0,
       cpu=(1.0, 0.5), mem=(1024, 512), p95=(0.4, 400), p90=(0.3, 380), p100=(0.9, 700), replicas=2, labels=None, max_ind=None):
    inds = inds or []
    return {
        "id": f"{ns}-{typ.lower()}-{name}", "name": name, "namespace": ns, "type": typ, "period": "30d",
        "replicas_counts": {"max_count": replicas, "avg_count": replicas}, "optimization_policy": policy,
        "mute_status": {"is_muted": False}, "cost": cost, "waste": waste, "historical_waste": waste, "potential_savings": pot,
        "cost_increase": inc, "running_minutes": int(first_days * 1440),
        "first_seen": iso(NOW - timedelta(days=first_days)), "last_seen": iso(last),
        "max_indicator": ({"name": max_ind, "type": "waste", "severity": 3} if max_ind else None),
        "indicators": inds,
        "containers": [{"name": "c", "indicators": cont_inds or [],
                        "resources": {"current": {}, "recommended": {}}, "usage": {}}],
        "workload_labels": labels or {},
        "derived": {"current_cpu_request_cores_total": cpu[0], "recommended_cpu_request_cores_total": cpu[1],
                    "current_memory_request_mib_total": mem[0], "recommended_memory_request_mib_total": mem[1],
                    "cpu_usage_p90_cores_sum": p90[0], "cpu_usage_p95_cores_sum": p95[0], "cpu_usage_p100_cores_sum": p100[0],
                    "memory_usage_p90_mib_sum": p90[1], "memory_usage_p95_mib_sum": p95[1], "memory_usage_p100_mib_sum": p100[1]},
    }


def ind(name, typ, sev):
    return {"name": name, "type": typ, "severity": sev}


def pool(pid, machine, nodes, idle, cost, minutes, autoscaler="karpenter", spot=False, rec=None, cpu_req=8.0, cpu_used=2.0):
    return {
        "id": pid, "autoscaler_type": autoscaler, "nodes": {"min": nodes[0], "avg": nodes[1], "max": nodes[2]},
        "running_minutes": minutes,
        "cpu": {"idle": 10, "requested": {"avg": cpu_req}, "used": {"avg": cpu_used}},
        "mem": {"idle": 1000, "requested": {"avg": 8000}, "used": {"avg": 3000}},
        "cost": {"hourly": 1.0, "timeframe": cost, "idle_total": idle},
        "labels": {"instance_type": machine, "is_spot": "true" if spot else "false"},
        "recommendations": ({"type": "karpenter", "has_changes": True, "node_type_recommendations": [
            {"instance_type": rec[0], "estimated_savings": rec[1], "estimated_savings_pct": rec[2]}]} if rec else {"type": "karpenter", "has_changes": False}),
    }


def pod(name, ns, wtype, node_group, code, reason, phase="Running", fix="Remove the annotation"):
    return {"name": name, "namespace": ns, "id": f"pod-{name}", "workload": {"id": f"w-{name}", "name": name.rsplit('-', 1)[0], "type": wtype},
            "reasons": [{"reason": reason, "reason_code": code, "details": "Pod has karpenter.sh/do-not-disrupt annotation" if code == "opt_out_annotation" else reason,
                         "remediation": {"fix_summary": fix, "risk": "low", "confidence": "high"}}],
            "phase": phase, "spec": {"node_group": node_group}}


def build_prod_a():
    r = random.Random(7)
    w = []
    # controllers (long-running)
    for i in range(12):
        w.append(wl(f"api-{i}", "payments", "Deployment", 300, 150 + i, 100 + i, 35, cpu=(2, 1), labels={"label_team": "payments"}, max_ind="OverProvisionedWorkload"))
    w.append(wl("billing", "payments", "Deployment", 400, 220, 140, 35, cpu=(4, 1.5), inds=[ind("OverProvisionedWorkload", "waste", 3)], max_ind="OverProvisionedWorkload", labels={"label_team": "payments"}))
    # missing memory limits, sev>=2
    for i in range(3):
        w.append(wl(f"cache-{i}", "platform", "Deployment", 120, 60, 40, 35, cont_inds=[ind("MemLimitNotSet", "risk", 3)], max_ind="OverProvisionedWorkload"))
    # OOM workload with big literal increase
    w.append(wl("ingest", "data", "Deployment", 500, 90, 30, 35, inc=1800, cpu=(2, 4), mem=(2048, 20000), p95=(1.0, 6000), p90=(0.8, 5000), p100=(2.0, 19000),
                cont_inds=[ind("OOM", "risk", 3), ind("UnderProvisionedMemRequest", "risk", 3)], max_ind="OOM", labels={"label_team": "data"}))
    w.append(wl("worker-low", "data", "Deployment", 100, 10, 0, 35, inc=60, cpu=(1, 2), cont_inds=[ind("UnderProvisionedCpuRequest", "risk", 3)]))
    # stateful (hold out)
    for i in range(4):
        w.append(wl(f"kafka-broker-{i}", "streaming", "StatefulSet", 600, 300, 200, 35, cpu=(8, 4)))
    for i in range(3):
        w.append(wl(f"elasticsearch-data-{i}", "search", "StatefulSet", 500, 250, 170, 35, cpu=(8, 4)))
    # managed add-ons
    for i in range(3):
        w.append(wl(f"coredns-{i}", "kube-system", "Deployment", 20, 12, 9, 35))
    # extraHeadroom policy cohort
    for i in range(4):
        w.append(wl(f"svc-eh-{i}", "payments", "Deployment", 90, 40, 20, 35, policy="extraHeadroom"))
    # airflow jobs (grouped) + plain jobs
    for i in range(6):
        w.append(wl(f"airflow-task-{i}", "airflow", "GroupedJob", 250, 150, 110, 35, cpu=(6, 1.5),
                    labels={"label_airflow_version": "2.8", "label_dag_id": f"dag{i}"}, max_ind="OverProvisionedWorkload"))
    for i in range(250):
        w.append(wl(f"airflow-run-{i:04d}", "airflow", "Job", 2.0, 1.0, 0.0, 3, labels={"label_airflow_version": "2.8"}))
    # managed-runtime (JVM) workload, declared via jvm_patterns - r_jvm
    w.append(wl("jvmsvc-cache", "checkout", "Deployment", 150, 70, 50, 35, cpu=(2, 1)))
    # held out of automation by name, but not stateful - r_stateful's heldout lane should score this
    # rightsize_manual (S), not rightsize_stateful (M)
    w.append(wl("manual-hold-svc", "finance", "Deployment", 200, 100, 60, 35, cpu=(2, 1)))
    return w


def build_dev_b():
    w = []
    for i in range(10):
        w.append(wl(f"web-{i}", "apps", "Deployment", 40, 20, 12, 8, cpu=(1, 0.5)))
    w.append(wl("search-es", "apps", "StatefulSet", 100, 50, 30, 8))
    return w


def write_cluster(root, name, workloads, pools, unev, audit, cloud="aws"):
    d = Path(root) / name
    d.mkdir(parents=True, exist_ok=True)
    cost = sum(x["cost"] for x in workloads); waste = sum(x["waste"] for x in workloads); pot = sum(x["potential_savings"] for x in workloads)
    (d / "summary.json").write_text(json.dumps({"cluster_uid": UIDS[name], "cluster_name": name, "period": "30d", "workloads": len(workloads),
                                                "total_cost": cost, "total_waste": waste, "total_potential_saving": pot,
                                                "waste_workloads": sum(1 for x in workloads if x["waste"] > 0)}))
    (d / "workloads-all.jsonl").write_text("\n".join(json.dumps(x) for x in workloads) + "\n")
    (d / "nodegroups.jsonl").write_text("\n".join(json.dumps(x) for x in pools) + "\n")
    (d / "unevictable-full.jsonl").write_text("\n".join(json.dumps(x) for x in unev) + ("\n" if unev else ""))
    (d / "audit.jsonl").write_text("\n".join(json.dumps(x) for x in audit) + ("\n" if audit else ""))
    (d / "cluster.json").write_text(json.dumps({"cloud": cloud, "name": name, "uid": UIDS[name]}))


def make(root):
    root = Path(root)
    pools_a = [pool("karp-general", "m6i.2xlarge", (4, 12.0, 30), 1500, 4000, 17000, rec=("m7g.2xlarge", 450, 11)),
               pool("karp-batch", "c6i.4xlarge", (0, 6.0, 40), 900, 1500, 9000, spot=True),
               pool("karp-gpu", "p3.2xlarge", (0, 2.0, 6), 1400, 1000, 3000, cpu_req=30, cpu_used=2),
               # never flexes (min == max) and reports no autoscaler - r_pool_flex + r_autoscaler_labels
               pool("karp-unknown-pinned", "m6i.large", (3, 3.0, 3), 80, 200, 6000, autoscaler="unknown")]
    unev_a = ([pod(f"api-pdb-{i}", "payments", "Deployment", "karp-general", "pod_disruption_budget", "Over-restrictive PodDisruptionBudget", fix="Set minAvailable below replicas") for i in range(9)]
              + [pod(f"cache-opt-{i}", "platform", "Deployment", "karp-general", "opt_out_annotation", "Do-not-evict annotation") for i in range(4)]
              + [pod(f"scratch-{i}", "data", "Deployment", "karp-batch", "local_node_storage", "Local Node Storage") for i in range(3)]
              # strict pod/node affinity, both on karp-general - r_unevictable_affinity
              + [pod(f"anti-{i}", "payments", "Deployment", "karp-general", "anti_affinity", "Strict anti-affinity", fix="Review HA requirement") for i in range(5)]
              + [pod(f"nodeaff-{i}", "data", "Deployment", "karp-general", "node_affinity", "Strict node affinity", fix="Loosen node selector") for i in range(2)])
    write_cluster(root, "prod-a", build_prod_a(), pools_a, unev_a, [])
    pools_b = [pool("karp-dev", "m6i.large", (1, 3.0, 8), 80, 200, 4000, rec=("m7g.large", 20, 10))]
    audit_b = [{"execution": "inplace-resize", "cluster_uid": UIDS["dev-b"], "workload": "web-1"} for _ in range(8)] + \
              [{"execution": "regular-eviction", "cluster_uid": UIDS["dev-b"], "workload": "web-2"} for _ in range(2)]
    write_cluster(root, "dev-b", build_dev_b(), pools_b, [], audit_b)
    cfg = {
        "customer": "Acme", "prepared": "2026-10-15", "cloud": "aws", "data_dir": str(root), "out_dir": str(root / "out"),
        "clusters": [{"name": n, "uid": u} for n, u in UIDS.items()],
        "auto_provisioned_pool_patterns": [],
        "jvm_patterns": ["^jvmsvc"],
        "held_out_from_automation": ["^manual-hold-svc$"],
    }
    (root / "acme.tenant.json").write_text(json.dumps(cfg, indent=1))
    return root / "acme.tenant.json"
