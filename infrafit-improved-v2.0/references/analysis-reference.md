## Pattern Taxonomy

Use this to produce the fix text for each group in the report.

---

### Pattern: Strict Anti-Affinity (hostname topology key)

**Parenthetical matches:** `"anti-affinity on topology key 'kubernetes.io/hostname'"`

CAS cannot reschedule because `requiredDuringSchedulingIgnoredDuringExecution`
forbids the pod from sharing a node with a sibling, and all candidate nodes already
host one. This is a **scheduling constraint blocker** — `safe-to-evict` does nothing.

**Reading the sibling count:** The `by_siblings` breakdown tells you the effective
replica count (siblings + 1). Use this to tier your remediation advice:
- **1 sibling (2-replica workloads):** Low HA risk — safe to convert to `preferred` in staging
- **2–3 siblings (3–4 replicas):** Medium — review individually, convert with care
- **4+ siblings (5+ replicas):** High HA intent — validate thoroughly before converting

**Fix (staging):** Convert `required` → `preferred`:
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

**Fix (alternative):** `topologySpreadConstraints` with `whenUnsatisfiable: ScheduleAnyway`

**Discovery:**
```bash
kubectl get deployments,statefulsets -A -o json | \
  jq -r '.items[] | select(
    .spec.template.spec.affinity.podAntiAffinity.requiredDuringSchedulingIgnoredDuringExecution != null
  ) | "\(.metadata.namespace)/\(.kind)/\(.metadata.name)"'
```

---

### Pattern: Karpenter Opt-Out Annotation

**Parenthetical matches:** `"do-not-evict"` or `"do-not-disrupt"` or `"Opt-Out Annotation"`

The pod carries `karpenter.sh/do-not-evict: "true"` or `karpenter.sh/do-not-disrupt: "true"`.
Unlike storage or scheduling blockers, this is **explicit and intentional** — someone
deliberately opted the pod out of Karpenter disruption. It blocks node consolidation
but not voluntary eviction (e.g. `kubectl drain`).

**Key distinction from CAS blockers:**
- `do-not-evict` (deprecated, Karpenter < v0.33): blocks all eviction during consolidation
- `do-not-disrupt` (current): blocks disruption budgeting; Karpenter won't voluntarily
  terminate the node while this pod is present

**When to keep it:** Admission webhooks, single-replica critical controllers, stateful
pods without PDB coverage — these legitimately need protection.

**When to remove it:** Multi-replica deployments where one pod being evicted doesn't
break the service. Check replica count first.

**Fix A — Remove the annotation (if replicas ≥ 2 and pod is not admission-critical):**
```bash
# Check replicas first
kubectl get deployment -n <namespace> -o custom-columns=NAME:.metadata.name,REPLICAS:.spec.replicas

# Remove annotation from pod template
kubectl patch deployment <name> -n <namespace> \
  --type=json \
  -p='[{"op":"remove","path":"/spec/template/metadata/annotations/karpenter.sh~1do-not-evict"}]'

# For do-not-disrupt variant:
kubectl patch deployment <name> -n <namespace> \
  --type=json \
  -p='[{"op":"remove","path":"/spec/template/metadata/annotations/karpenter.sh~1do-not-disrupt"}]'
```

**Fix B — Migrate deprecated `do-not-evict` → `do-not-disrupt` (Karpenter ≥ v0.33):**
```bash
# Check Karpenter version first
kubectl get deployment -n karpenter karpenter -o jsonpath='{.spec.template.spec.containers[0].image}'

# Migrate annotation on pod template
kubectl patch deployment <name> -n <namespace> --type=json -p='[
  {"op":"remove","path":"/spec/template/metadata/annotations/karpenter.sh~1do-not-evict"},
  {"op":"add","path":"/spec/template/metadata/annotations/karpenter.sh~1do-not-disrupt","value":"true"}
]'
```

**Fix C — Protect at the node level instead (preserves pod mobility):**
```bash
# Annotate the specific node to exclude it from consolidation
# instead of pinning pods to it via opt-out annotation
kubectl annotate node <node-name> karpenter.sh/do-not-disrupt=true
```

**Discovery — find all pods with opt-out annotations:**
```bash
kubectl get pods -A -o json | \
  jq -r '.items[] | select(
    .metadata.annotations["karpenter.sh/do-not-evict"] == "true" or
    .metadata.annotations["karpenter.sh/do-not-disrupt"] == "true"
  ) | "\(.metadata.namespace)/\(.metadata.name)"'
```

---

### Pattern: emptyDir Local Storage

**Parenthetical matches:** `"emptyDir"` + `"Cluster Autoscaler treats as local storage"`

CAS treats emptyDir as local state. In practice almost always ephemeral scratch space.

**Volume name risk tiers:**
- **Safe to annotate:** `socket-dir`, `tmp`, `tmp-dir`, `cache`, `empty-cache`,
  `workload-socket`, `sigstore`, `tempo-distributor-store`, `queryfrontend-store`,
  `compactor-store`
- **Verify before annotating (may hold durable state):** `sqlite`, `wal`, `journal`,
  `*.db` — check whether the volume holds a database or write-ahead log.
  Run: `kubectl exec -n <ns> deploy/<name> -- ls -la /<vol-mount>` and confirm
  the data is either replicated or safely reconstructible on restart.

> **`workload-socket` origin check — required when count > 10:**
> The volume name alone does not confirm Istio/Envoy origin. Cross-reference
> `by_volume_workloads['workload-socket']` from Step 4's grouping against the
> Well-Known System Pods table (pod prefixes `gtw-*`, `gateway-proxy-*`). If the
> workload prefixes are unrecognized, include this command in the report:
> ```bash
> kubectl get pods -A -o json | \
>   jq -r '.items[] |
>     select([.spec.volumes[]? | select(.name == "workload-socket")] | length > 0) |
>     "\(.metadata.namespace)/\(.metadata.name): \([.spec.containers[].name] | join(", "))"'
> ```
> - Container name `istio-proxy` or `envoy` → ✅ safe to annotate
> - Any other container name → **UNCONFIRMED** — include the command in the report,
>   do not assert the source

**Fix:**
```bash
kubectl patch deployment <name> -n <namespace> \
  --type=json \
  -p='[{"op":"add","path":"/spec/template/metadata/annotations",
        "value":{"cluster-autoscaler.kubernetes.io/safe-to-evict":"true"}}]'
```

Or Helm: `podAnnotations: { cluster-autoscaler.kubernetes.io/safe-to-evict: "true" }`

**Before annotating:** confirm replicas ≥ 2.

---

### Pattern: hostPath Volume

**Parenthetical matches:** `"hostPath"`

Pod reads/writes the node filesystem directly.
- **Read-only** (`/etc/ssl`, `/var/log`): safe to annotate
- **Read-write**: manual assessment required
- **DaemonSets**: generally safe — reschedule on any node

**Fix (if safe):** same `safe-to-evict` annotation as emptyDir.

---

### Pattern: PodDisruptionBudget (PDB)

**Parenthetical matches:** `"PodDisruptionBudget"` or `"can't be disrupted"`

The pod is protected by a PDB whose current budget allows zero disruptions. The JS
function extracts the PDB name and `maxUnavailable` value into `pdb.by_pdb` — use
this to risk-tier your remediation advice before touching anything.

**The `total_block` flag:** any PDB with `maxUnavailable=0` is flagged
`total_block: true` in the JSON output. This is the worst case — *no* pod in that
group can ever be disrupted, regardless of replica count. Fix these first.

**Risk tiering based on `maxUnavailable` and workload type:**

| Situation | Risk | Recommendation |
|---|---|---|
| `maxUnavailable=0` (total_block), replicas=1 | 🔴 High | Scale to ≥ 2 replicas first, then relax PDB |
| `maxUnavailable=0` (total_block), replicas≥2 | 🟡 Medium | Relax to `maxUnavailable=1` — safe if replicas > 1 |
| Budget exhausted (not zero), batch/offline workload | 🟢 Low | Relax PDB directly |
| Budget exhausted, admission webhook / critical controller | 🔴 High | Scale replicas first |
| Budget exhausted, Temporal/workflow worker | 🟢 Low | Relax — workers resume workflows on restart |
| Budget exhausted, AI inference serving (Triton, vLLM) | 🟡 Medium | Confirm replica count and HA architecture before relaxing |

**Inspect PDB and replica counts before acting:**
```bash
# List all PDBs in the affected namespace with current budget
kubectl get pdb -n <namespace> -o custom-columns=\
NAME:.metadata.name,MIN:.spec.minAvailable,MAX:.spec.maxUnavailable,\
ALLOWED:.status.disruptionsAllowed,CURRENT:.status.currentHealthy,\
DESIRED:.status.desiredHealthy

# Confirm replica count for the workload
kubectl get statefulsets,deployments -n <namespace> \
  -o custom-columns=NAME:.metadata.name,REPLICAS:.spec.replicas
```

**Fix A — Relax `maxUnavailable=0` to 1 (for replicas ≥ 2):**
```bash
kubectl patch pdb <pdb-name> -n <namespace> \
  --type=merge -p='{"spec":{"maxUnavailable": 1}}'
```

**Fix B — Scale to 2 replicas first (for single-replica workloads):**
```bash
# Scale up, wait for ready, then relax PDB
kubectl scale statefulset <name> -n <namespace> --replicas=2
kubectl rollout status statefulset/<name> -n <namespace>
kubectl patch pdb <pdb-name> -n <namespace> \
  --type=merge -p='{"spec":{"maxUnavailable": 1}}'
```

**Fix C — Migrate to percentage-based `minAvailable` (more resilient as replicas scale):**
```yaml
# Replace maxUnavailable:0 with a percentage — allows 1 disruption per 5+ replicas
spec:
  minAvailable: "80%"
```

---

### Pattern: Static / Mirror Pod

**Parenthetical matches:** `"kubernetes.io/config.mirror"` or `"mirror pod"` or `"Static"`

Kubelet-managed pod. CAS cannot evict.

**GKE kube-proxy:** 1 per node, always appears in count, **not a true drain blocker**
— GKE CAS handles these natively. Do not include in remediation priority.

**Other static pods:** remove manifest from `/etc/kubernetes/manifests/` or migrate
to a Deployment.

---

### Pattern: Local PersistentVolume

**Parenthetical matches:** `"local"` storage class or `"PersistentVolumeClaim"` with local binding

Pod is node-pinned via local PV. No valid reschedule target exists.

**Fix:** Migrate to network-attached storage, or explicitly exclude the node:
`cluster-autoscaler.kubernetes.io/scale-down-disabled: "true"` on the node annotation.

---

## Well-Known System Pods Reference

| Pod Pattern | Typical Reason | Autoscaler Impact | Annotation Safe? |
|---|---|---|---|
| `kube-proxy-gke-*` | Static/Mirror Pod | ⚠️ NOT a true drain blocker on GKE | N/A |
| `ebs-csi-controller-*` | emptyDir `socket-dir` | Blocks drain | ✅ Socket rebuilds |
| `metrics-server-*` | emptyDir `tmp`/`tmp-dir` | Blocks drain | ✅ Stateless |
| `coredns-*` | emptyDir DNS cache | Blocks drain | ✅ Cache rebuilds |
| `cert-manager-*` | emptyDir | Blocks drain | ✅ Stateless |
| `cluster-autoscaler-*` | emptyDir or hostPath | Blocks drain | ⚠️ Only if ≥ 2 replicas |
| `kyverno-*` | emptyDir `sigstore` | Blocks drain | ✅ Cosign cache rebuilds |
| `policy-reporter-*` | emptyDir `sqlite` | Blocks drain | ⚠️ Verify SQLite is not primary store |
| `tempo-distributor-*` | emptyDir `tempo-distributor-store` | Blocks drain | ✅ Ephemeral ingest buffer |
| `tempo-query-frontend-*` | emptyDir `queryfrontend-store` | Blocks drain | ✅ Ephemeral query cache |
| `tempo-compactor-*` | emptyDir `compactor-store` | Blocks drain | ✅ Ephemeral scratch |
| `gtw-*` / `gateway-proxy-*` (Istio/Gloo) | emptyDir `workload-socket` or Anti-Affinity | Blocks drain | ✅ Socket if emptyDir **and pod prefix matches**; ❌ policy change if anti-affinity. Unknown prefixes → run origin-check before annotating |
| `*-cloudsql-*` with hostname anti-affinity | Strict Anti-Affinity | Blocks all nodes | ❌ Policy change required |
| `argocd-redis-ha-server-*` | Strict Anti-Affinity | Blocks drain | ❌ Policy change required |
| `scaleops-*` (ScaleOps platform) | Karpenter `do-not-evict` opt-out | Blocks Karpenter consolidation | ⚠️ Remove only if replicas ≥ 2; never remove from admission webhook |

---

## Remediation Verification

### Cluster Autoscaler (CAS) — GKE, EKS, AKS with CA

```bash
# Watch CAS log for scale-down decisions
kubectl logs -n kube-system -l app=cluster-autoscaler --tail=100 -f | \
  grep -i "scale down\|unneeded\|evict\|drain\|blocked"

# Check CAS status configmap
kubectl get configmap cluster-autoscaler-status -n kube-system -o yaml | \
  grep -A5 "unneeded\|ScaleDown"
```

Expect `node X is unneeded` within ~10 minutes of fix (CAS evaluates every 10s,
scale-down delay is typically 10 minutes after a node is first flagged).

### Karpenter (AKS, EKS with Karpenter)

```bash
# Watch Karpenter logs for disruption/consolidation decisions
kubectl logs -n karpenter -l app.kubernetes.io/name=karpenter --tail=100 -f | \
  grep -i "consolidat\|disrupt\|evict\|terminat\|nodeclaim"

# Check NodeClaims — look for ones in Terminating state (means consolidation is working)
kubectl get nodeclaims

# Check NodePool consolidation policy
kubectl get nodepool -o yaml | grep -A5 "consolidation\|disruption"
```

Expect `disrupting node` log entries within ~30 seconds of Karpenter's next
consolidation loop (default: every 10s, with configurable `consolidateAfter` delay).

---

## What Not To Do

- **Don't split the JS call** — extract+analyze in one pass (Phase 7). Splitting
  risks modal close between calls, losing the extracted text.
- **Don't apply `safe-to-evict` to anti-affinity pods** — wrong fix for the pattern.
- **Don't annotate emptyDir volumes named `sqlite`, `wal`, `journal`, or `*.db`**
  without verifying the data is ephemeral or replicated.
- **Don't remove `do-not-evict`/`do-not-disrupt` from admission webhooks** —
  single-replica admission controllers can break cluster-wide operations if evicted.
- **Don't skip cluster identity check** — multi-tenant orgs often have wrong cluster
  pre-selected.
- **Don't count GKE kube-proxy mirror pods as real blockers** — they're expected and
  CAS handles them natively.
- **Don't run pod-by-pod analysis with > 50 unevictable pods** — use pattern-level.

---

## Error Recovery

| Symptom | Recovery |
|---|---|
| JS returns `EXTRACTION_FAILED` | Virtualized scroll container — run the extended extraction (see Phase 7 Step 2b) |
| JS returns `{}` or missing groups | Modal may not be open; re-click Summarize and retry |
| `Node Autoscaler: Unknown` | Transient load state — wait for spinner to clear, then re-read |
| Summarize button does nothing | Title shows "Node Types: 0" — no snapshot data; use Snapshot selector |
| Timeframe dropdown won't close | `find("14 days option in timeframe dropdown")`, click via ref |
| Last Seen `find` returns combobox | Open dropdown first, then coordinate-click visible "Current" text |
| Login wall after navigation | Tell user to log in, say "go" when ready |
| Help widget opens unexpectedly | Close it (X), retry `find` |
| Wrong cluster in breadcrumb | Use cluster dropdown top-left to correct before proceeding |
| Unevictable Pods = 0 but idle cost > 0 | Bin-packing issue, not unevictable pods; suggest InfraFit Recommendations tab for node type right-sizing |
