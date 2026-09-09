# Example Report: PDB Pattern (Northwind Logistics / nwl-prod-us-east-a / gpu-pool-a)

Illustrative output for a Karpenter-managed GPU cluster. Synthetic example
data — use as a reference when evaluating report quality.

---

## InfraFit Analysis — nwl-prod-us-east-a

**Generated:** 2026-06-22 15:59:39
**Cluster/Tenant:** nwl-prod-us-east-a / Northwind Logistics
**Node Group:** gpu-pool-a
**Instance Type:** Mixed (g6.2xlarge, g6.8xlarge, g6.xlarge) — NVIDIA L4 GPU nodes
**Optimization Policy:** Balanced
**Timeframe:** 14 days
**Node Autoscaler:** Karpenter

---

### Quick Reference

| Pattern | Pods | % | Blocker Type | Fix Complexity |
|---|---|---|---|---|
| PodDisruptionBudget (`maxUnavailable=0`) | 101 | 100% | Zero disruption budget | Medium — relax PDB or scale replicas |

**Core Problem:** Every unevictable pod in the `gpu-pool-a` group is protected by a PDB
with `maxUnavailable=0`, preventing Karpenter from consolidating $8.4K/mo of idle
GPU capacity across 101 nodes.

---

### Executive Summary

| Metric | Value |
|---|---|
| Visible Nodes | 349 |
| Visible Pods | 4,540 |
| Unevictable Pods | 101 |
| Node Group Idle Cost | $8.4K/mo |
| Node Group Potential Savings | No recommendation available |
| Cluster Node Idle Cost | $19.7K/mo |

---

### Top Nodes by Unevictable Pod Count

All 101 affected nodes carry exactly 1 unevictable pod each — uniformly distributed.
This confirms PDB coverage is per-shard (one StatefulSet replica per node), not a
misconfiguration on a subset of nodes.

| Node | Instance Type | Unevictable / Total | Dominant Reason |
|---|---|---|---|
| ip-10-0-12-101.ec2.internal | g6.8xlarge | 1 / 13 | PDB |
| ip-10-0-12-102.ec2.internal | g6.8xlarge | 1 / 13 | PDB |
| ip-10-0-12-103.ec2.internal | g6.8xlarge | 1 / 13 | PDB |

---

### Pattern Analysis

#### Pattern 1: PodDisruptionBudget — 101 pods (100%) ⚠️ Primary blocker

All 101 pods are protected by PDBs with `maxUnavailable=0` (`total_block: true`).
With zero disruption budget, Karpenter cannot terminate any of these nodes during
consolidation, regardless of how idle the node is.

**PDB breakdown by name:**

| PDB Name | Namespace | Pod Count | maxUnavailable | total_block |
|---|---|---|---|---|
| inference-shard-4 | ml-inference | 47 | 0 | ✅ Yes |
| inference-shard-3 | ml-inference | 32 | 0 | ✅ Yes |
| batch-worker | batch-jobs | 14 | 0 | ✅ Yes |
| workflow-worker | workflow | 8 | 0 | ✅ Yes |

**Affected namespaces:** `batch-jobs`, `ml-inference`, `workflow`

**Fix — Step 1: Audit replica counts before relaxing any PDB:**

```bash
kubectl get statefulsets,deployments -n ml-inference \
  -o custom-columns=NAME:.metadata.name,REPLICAS:.spec.replicas
kubectl get statefulsets,deployments -n batch-jobs \
  -o custom-columns=NAME:.metadata.name,REPLICAS:.spec.replicas
kubectl get statefulsets,deployments -n workflow \
  -o custom-columns=NAME:.metadata.name,REPLICAS:.spec.replicas
```

**Fix — Step 2: Relax PDB for workloads with ≥ 2 replicas:**

```bash
kubectl patch pdb inference-shard-4 \
  -n ml-inference \
  --type=merge -p='{"spec":{"maxUnavailable": 1}}'
```

**Fix — Step 3: Scale single-replica shards before relaxing their PDB:**

```bash
kubectl scale statefulset inference-shard-4 \
  -n ml-inference --replicas=2
kubectl rollout status statefulset/inference-shard-4 \
  -n ml-inference
# Then relax PDB as above
```

---

### Combined Impact & Priority

All 101 pods share the same root cause: `maxUnavailable=0` PDBs on GPU-pinned
StatefulSet shards. The uniform 1-pod-per-node pattern confirms these are shards where
each replica occupies a dedicated GPU node.

**Risk tiering:**
- `batch-jobs` — 🟢 Low risk. Batch workload; brief disruption acceptable. Fix first.
- `workflow` — 🟢 Low-Medium risk. Workers resume workflows on restart.
- `ml-inference` inference shard (single replica) — 🔴 High risk. Scale to 2 replicas first.
- `ml-inference` inference shard (≥ 2 replicas) — 🟡 Medium risk. Relax PDB directly.

---

### Recommended Actions

1. **Audit all replica counts** across the three namespaces (command above)
2. **Relax PDB for `batch-jobs` and `workflow`** immediately — safe for these workload types
3. **For single-replica inference shards:** scale to 2 replicas, then relax PDB
4. **Verify Karpenter resumes consolidation:**
   ```bash
   kubectl logs -n karpenter -l app.kubernetes.io/name=karpenter \
     --tail=200 -f | grep -i "consolidat\|disrupt\|nodeclaim\|terminat"
   ```
5. **Monitor NodeClaims** for terminations:
   ```bash
   watch kubectl get nodeclaims
   ```
