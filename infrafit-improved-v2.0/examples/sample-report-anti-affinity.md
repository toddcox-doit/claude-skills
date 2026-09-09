# Example Report: Anti-Affinity Pattern (Northwind Logistics / nwl-staging-us-central-k8s)

Illustrative output for a GKE / CAS cluster with a blanket anti-affinity
policy applied to database auth proxy sidecars. Synthetic example data.
196 unevictable pods — pattern-level analysis (> 50 pods).

---

## InfraFit Analysis — nwl-staging-us-central-k8s

**Generated:** 2026-06-22 13:42:01
**Cluster/Tenant:** nwl-staging-us-central-k8s / northwind logistics
**Node Group:** nap-n2d-standard-16-x7k2m4
**Instance Type:** n2d-standard-16 → Recommended: e2-standard-32
**Optimization Policy:** CPU: Max Savings | Memory: Balanced
**Timeframe:** 14 days
**Node Autoscaler:** Cluster Autoscaler (CA)

---

### Quick Reference

| Pattern | Pods | % | Blocker Type | Fix Complexity |
|---|---|---|---|---|
| Strict Anti-Affinity | 155 | 79% | Scheduling constraint | Medium — policy change across 38 ns |
| Mirror Pods (kube-proxy) | 29 | 15% | N/A — not a real blocker | None |
| emptyDir (Kyverno sigstore) | 4 | 2% | Local storage | Low — annotation |
| emptyDir (Tempo) | 3 | 2% | Local storage | Low — annotation |
| emptyDir (misc) | 9 | 5% | Local storage | Low — annotation |

**Core Problem:** 155 pods across all 29 nodes carry `requiredDuringSchedulingIgnoredDuringExecution`
anti-affinity on `kubernetes.io/hostname`, applied as a blanket policy across 38 namespaces,
preventing CAS from draining any node and blocking $1,410/mo in savings.

---

### Executive Summary

| Metric | Value |
|---|---|
| Visible Nodes | 29 |
| Visible Pods | 1,376 |
| Unevictable Pods | 196 |
| Node Group Idle Cost | $720/mo |
| Node Group Potential Savings | $1,410/mo (40%) via switch to e2-standard-32 |
| Cluster Node Idle Cost | $1,605/mo |

---

### Top Nodes by Unevictable Pod Count

| Node | Instance Type | Unevictable / Total | Dominant Reason |
|---|---|---|---|
| gke-nwl-staging-...-a1b2c3d4-n01 | n2d-standard-16 | 17 / 96 | Anti-Affinity |
| gke-nwl-staging-...-a1b2c3d4-n02 | n2d-standard-16 | 17 / 82 | Anti-Affinity |
| gke-nwl-staging-...-e5f6g7h8-n03 | n2d-standard-16 | 12 / 71 | Anti-Affinity |

---

### Pattern Analysis

#### Pattern 1: Strict Anti-Affinity — 155 pods (79%) ⚠️ Primary blocker

Every node has multiple pods whose `requiredDuringSchedulingIgnoredDuringExecution`
anti-affinity rule forbids co-location with sibling pods on the same node. CAS cannot
find a valid reschedule target — all candidate nodes already host a sibling. This is a
scheduling constraint blocker; `safe-to-evict` does nothing here.

When anti-affinity appears across all nodes and many namespaces simultaneously, it
indicates a blanket policy — a single policy-level change unblocks all nodes at once.

**Sibling count breakdown (effective replica count):**

| Siblings | Pods | Replica Count | HA Risk |
|---|---|---|---|
| 1 sibling | 114 pods | 2 replicas | 🟢 Low — safe to convert to `preferred` |
| 2 siblings | 27 pods | 3 replicas | 🟡 Medium — review individually |
| 3 siblings | 4 pods | 4 replicas | 🟡 Medium |
| 4 siblings | 10 pods | 5 replicas | 🔴 High — validate thoroughly |

**Affected namespaces (38):** a mix of application namespaces (`app-alpha`,
`app-beta`, `app-gamma`, `api-gateway`, `batch-jobs`, `reporting`, `search`,
`web-frontend`, ...) plus shared platform namespaces (`argocd`, `dex`,
`istio-ingress`, `istio-monitoring`, `loki`, `monitoring`, `observability`,
`tempo`). The pattern spans both tiers, which is what marks it as a blanket
policy rather than a per-team choice.

**Discovery:**
```bash
kubectl get deployments,statefulsets -A -o json | \
  jq -r '.items[] | select(
    .spec.template.spec.affinity.podAntiAffinity.requiredDuringSchedulingIgnoredDuringExecution != null
  ) | "\(.metadata.namespace)/\(.kind)/\(.metadata.name)"'
```

**Fix (staging):**
```yaml
affinity:
  podAntiAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          labelSelector:
            matchLabels:
              app: my-app
          topologyKey: kubernetes.io/hostname
```

#### Pattern 2: Mirror Pods — 29 pods (15%) ℹ️ Not a real blocker

One `kube-system/kube-proxy-*` mirror pod per node. GKE CAS handles these natively
during drain — they inflate the unevictable count but do not prevent consolidation.
**No action required.**

#### Patterns 3–5: emptyDir — 16 pods (8%) ✅ Easy fixes

All emptyDir volumes are ephemeral (sigstore cache, Tempo ingest buffers, temp files).
Annotate with `safe-to-evict: "true"` after confirming replicas ≥ 2.

---

### Recommended Actions

1. **Batch-convert anti-affinity policy** across all 38 namespaces (unblocks all 29 nodes at once)
2. **Annotate Kyverno, Tempo, and misc emptyDir pods** with `safe-to-evict: "true"`
3. **Verify CAS scale-down resumes:**
   ```bash
   kubectl logs -n kube-system -l app=cluster-autoscaler --tail=100 -f | \
     grep -i "unneeded\|scale down"
   ```
