---
name: perfectscale-infrafit-unevictable-audit-v2.0
description: Audit unevictable pods in a PerfectScale cluster via `pscli` to identify what is blocking the Cluster Autoscaler from scaling down nodes, and generate a detailed remediation report — no browser required. Use this skill whenever the user wants to investigate node idle costs, find what's blocking CAS scale-down or drain, audit unevictable pods on a cluster, understand why nodes can't be consolidated, generate an InfraFit report, or asks anything like "what's blocking autoscaler savings", "why can't nodes be scaled in", "check unevictable pods", or "run the InfraFit audit". Also triggers for "PerfectScale InfraFit", "node idle", or "unevictable pods" in any context.
compatibility: >
  Preferred: pscli (public API service token). UI fallback only for token
  generation and for a snapshot that failed to process.
---

# PerfectScale InfraFit — Unevictable Pods Audit (v2.0, CLI-first)

## Purpose

Identify unevictable pods that block the Cluster Autoscaler (CAS) from
consolidating or draining nodes, and generate a per-pod remediation report.
v1 of this skill drove nine browser phases (Overview → PodFit → InfraFit →
sort → confirm filters → open node group → open Unevictable Pods tab → open
Summarize modal → read screenshots). This version does the same job with
`pscli` and no browser session.

## Inputs to Gather

1. **Cluster name** — if the user doesn't name one, use the cluster with the
   highest node-idle cost (Step 1 below computes this).
2. **Node group** — if the user doesn't name one, use the node group with the
   highest idle cost within that cluster (Step 2).

---

## Step 1 — Bootstrap and resolve the cluster

Confirm `pscli` is authenticated (see the **perfectscale-cli** skill's
bootstrap). If no profile authenticates, the only UI step in this entire
workflow is generating a Read-Only service token (Org Settings → API Tokens
→ Generate Token).

If no cluster was named, rank clusters by idle cost — `clusters list` carries
no cost field, so sum `cost.idle_total` across each cluster's node groups:

```bash
for c in $(pscli -o jsonl clusters list | jq -r .name); do
  idle=$(pscli -o jsonl nodegroups list -c "$c" --all 2>/dev/null | jq -s '[.[].cost.idle_total] | add // 0')
  echo "$idle $c"
done | sort -rn | head -1
```

## Step 2 — Resolve the node group

```bash
pscli -o jsonl nodegroups list -c <cluster> --all | jq -s 'sort_by(-.cost.idle_total)'
pscli -o json  nodegroups get  -c <cluster> -g <node-group>
```

Record: node group name, `autoscaler_type`, `cost.idle_total` (**Node Group
Idle Cost**), and the recommendation summary (`recommendations.type`,
`has_changes`) for the report header. The Step 1 sum is your **Cluster Node
Idle Cost**.

## Step 3 — Pull unevictable pods

```bash
pscli -o jsonl unevictable list -c <cluster> -g <node-group> --all
```

- **202** → snapshot still processing. Wait ~30s, retry once.
- **422** → snapshot failed to process. This is the only remaining UI
  fallback: tell the user to open InfraFit → Snapshot selector (top-right)
  and pick a different snapshot, then re-run.
- `pods: []` with `summary.total_pods: 0` → report "no unevictable pods
  found" and check whether the idle cost is a bin-packing issue instead
  (suggest the node group's `nodegroups get` recommendation).
- `summary` also carries `total_nodes`, `autoscaler_type`, `unevictable_pods`
  — use these directly for the Executive Summary instead of counting
  screenshots.

Each pod object already has everything v1 had to scrape from report text:
`name`, `namespace`, `workload`, `reasons[]` (each with `reason_code`,
`reason`, `details`, `remediation.{fix_summary,risk,confidence,current_spec,
recommended_spec,yaml_diff}`), `spec.node`, `spec.node_group`,
`blocked_node_count`, `blocked_cost_hourly`.

---

## Output Format

```markdown
## Unevictable Pods Report — [cluster-name]

**Generated:** [now]
**Cluster:** [cluster name/UID]
**Node Group:** [node-group-name]
**Autoscaler:** [autoscaler_type]

---

### Executive Summary

| Metric | Value |
|---|---|
| Nodes Analyzed | [summary.total_nodes] |
| Total Unevictable Pods | [summary.unevictable_pods] |
| Node Group Idle Cost | $[cost.idle_total]/hr |
| Cluster Node Idle Cost | $[Step 1 sum]/hr |

**Core Problem:** [one sentence: what's blocking CAS and what it prevents]

---

### Pod-by-Pod Analysis

**Pod N: `namespace/pod-name`**
*(reason.reason, verbatim from the API)*

[2–3 sentences: what this pod/controller is, why the flagged condition makes
CAS mark it unevictable, whether the data at risk is actually important]

**Impact:** blocked_cost_hourly for this pod's node, and what it blocks.

**Remediation:** use `reason.remediation.fix_summary` / `yaml_diff` directly
if present; otherwise fall back to the pattern-specific fix syntax below.

---
[Repeat per pod — or per pattern if there are more than ~15 unevictable pods;
list namespaces and skip individual names above 50]
---

### Combined Impact & Priority

[Root-cause pattern, total blocked cost, fix complexity, risk level]

### Recommended Actions (in order)

1. [Specific action with exact kubectl/annotation syntax]
2. [Next action]
3. [Verification: watch CAS/Karpenter logs to confirm scale-down]
```

---

## Analysis Guidance

### Common Unevictable Pod Patterns

Group by `reason_code` first (discrete field, no parsing needed). For codes
not yet catalogued, match keywords in `reason`/`details` the same way v1 did:

**emptyDir volumes (most common)** — `reason`/`details` mentions `emptyDir`
and "Cluster Autoscaler treats as local storage".
- CAS conservatively blocks eviction of any pod with a local volume, even
  ephemeral scratch space. The data is almost never important (sockets, temp
  files, caches).
- **Fix**: prefer `remediation.fix_summary`/`yaml_diff` if present; otherwise
  `cluster-autoscaler.kubernetes.io/safe-to-evict: "true"` on the pod template.
- **Caveat**: verify replicas ≥ 2 before annotating, to avoid availability gaps.

**hostPath volumes** — `reason`/`details` mentions `hostPath`. More
legitimately local. Assess read-only (usually safe) vs read-write (needs
care). DaemonSets are usually fine since they reschedule on any node.

**PodDisruptionBudgets (PDBs)** — `reason_code` such as
`pod_disruption_budget`, or text mentioning "protected by
PodDisruptionBudget". Check whether `maxUnavailable`/`minAvailable` is too
restrictive; `maxUnavailable === 0` is a total block. Fix: relax the PDB or
add replicas so it allows at least one disruption.

**Static pods / mirror pods** — text mentions "mirror pod" or
`config.mirror`. Managed directly by kubelet; not evictable by the scheduler.
Fix requires removing the static pod manifest from the node or migrating to
a Deployment.

### Well-Known System Pods and Their Safe Annotations

| Pod | Why Unevictable | Safe to annotate? |
|---|---|---|
| `ebs-csi-controller-*` | emptyDir `socket-dir` (CSI gRPC socket) | ✅ Yes — socket is rebuilt on restart |
| `metrics-server-*` | emptyDir `tmp` (scratch) | ✅ Yes — stateless, rebuilds on restart |
| `coredns-*` | emptyDir cache | ✅ Yes — DNS cache rebuilds |
| `kube-proxy-*` | hostPath iptables | ✅ Yes if DaemonSet (reschedules) |
| `cluster-autoscaler-*` | emptyDir or hostPath | ⚠️ Only if ≥ 2 replicas |
| `cert-manager-*` | emptyDir | ✅ Yes — stateless |

### Remediation Syntax Reference

**Add safe-to-evict annotation (kubectl patch):**
```bash
kubectl patch deployment <name> -n <namespace> \
  --type=json \
  -p='[{"op":"add","path":"/spec/template/metadata/annotations","value":{"cluster-autoscaler.kubernetes.io/safe-to-evict":"true"}}]'
```

**Add via Helm values (if Helm-managed):**
```yaml
podAnnotations:
  cluster-autoscaler.kubernetes.io/safe-to-evict: "true"
```

**Verify CAS can now scale down (watch CAS logs):**
```bash
kubectl logs -n kube-system -l app=cluster-autoscaler --tail=50 -f | grep -i "scale down\|evict\|drain"
```

---

## What Not To Do

- Don't apply automation changes — this is a read-only audit skill.
- Don't fabricate pod names or reasons not present in the `pscli` output.
- Don't recommend the safe-to-evict annotation for pods with PVC/PV without
  checking whether the data is replicated elsewhere.
- Don't skip the cluster/node-group resolution step — multi-cluster accounts
  can otherwise silently audit the wrong target.
- Don't reach for the browser unless it's one of the two genuine gaps: token
  generation, or a snapshot that returned 422 (failed to process).

## Error Handling

**"No unevictable pods found"** — report clearly; the idle cost may be a
bin-packing issue (too few workloads to fill nodes) rather than unevictable
pods. Point at the node group's `nodegroups get` recommendation instead.

**`unevictable list` returns 422** — the one remaining UI dependency: tell
the user to pick a different snapshot via InfraFit's Snapshot selector, then
re-run.

**No profile authenticates** — ask for a Read-Only service token (Org
Settings → API Tokens → Generate Token), then say "go" when ready.
