---
name: infrafit-improved-v2.0
license: Apache-2.0
description: >
  PerfectScale InfraFit unevictable pods audit — CLI-first via `pscli`. Queries
  the target cluster's node groups and unevictable pods directly through the
  public API, classifies every blocking pod (including a workload-origin
  cross-check for high-volume emptyDir findings), and produces a customer-ready
  remediation report with exact kubectl/Helm fix syntax. Falls back to the
  PerfectScale UI only for auth-token generation and for snapshots that are
  still processing. Use for: nodes that won't drain or scale down, Cluster
  Autoscaler or Karpenter unable to consolidate, idle node costs not dropping,
  PDB or anti-affinity blockers, or why PerfectScale isn't capturing InfraFit
  savings. Triggers on: "unevictable pods", "infrafit", "infra fit", "node
  idle", "nodes won't drain", "CAS blocking", "autoscaler can't scale down",
  "what's blocking savings", "idle costs", "why can't nodes drain", "karpenter
  won't consolidate", "why are nodes idle", "nodes stuck", "blocking the
  autoscaler". Always use this skill when the user mentions idle node costs,
  drain failures, or autoscaler blockers in a PerfectScale context — even if
  they just name a cluster.
compatibility: >
  Preferred: pscli (public API service token) — no browser required for the
  full workflow. UI fallback only for token generation (Org Settings) and for
  clusters whose InfraFit snapshot is still processing.
---

# PerfectScale InfraFit Analysis — v2.0 (CLI-first)

Full-stack skill: bootstrap `pscli` → resolve cluster/node group → pull
unevictable-pod data via the public API → classify → report. No browser
automation, no DOM scraping, no Summarize-modal extraction — `pscli unevictable`
and `pscli nodegroups` return the same underlying data as structured JSON,
already split into per-reason records with remediation detail attached.

> **v1 (`infrafit-improved`) required Claude-in-Chrome** to navigate to
> InfraFit, open the Summarize modal, and run a 4-tier DOM-scraping JS pass to
> reconstruct pod data from report text. That entire mechanism (Phases 1–7 of
> v1) is replaced by two `pscli` calls in this version. The pattern-taxonomy
> fix-syntax reference and report structure are unchanged and still live in
> `references/analysis-reference.md`.

---

## Bootstrap

Follow the **perfectscale-cli** skill's bootstrap: confirm `pscli` is on PATH,
discover/export an authenticated profile (`PERFECTSCALE_PROFILE`). If no
profile authenticates, the **only** UI step in this workflow is generating a
Read-Only service token (Org Settings → API Tokens → Generate Token) — that
step has no CLI equivalent because it's how CLI auth itself is bootstrapped.

---

## Step 1 — Resolve the target cluster

If the user names a cluster, skip to Step 2.

Otherwise, find the cluster with the highest node-idle cost. `pscli clusters
list` doesn't carry cost data, so compute it from node groups:

```bash
for c in $(pscli -o jsonl clusters list | jq -r .name); do
  idle=$(pscli -o jsonl nodegroups list -c "$c" --all 2>/dev/null \
    | jq -s '[.[].cost.idle_total] | add // 0')
  echo "$idle $c"
done | sort -rn | head -5
```

Pick the top result. This entirely replaces v1's Phase 2 (sort the Overview
table by Node Idle, click the first green-dot row).

---

## Step 2 — Resolve the target node group

```bash
pscli -o jsonl nodegroups list -c <cluster> --all \
  | jq -s 'sort_by(-.cost.idle_total)'
```

- `nodegroups list` has no server-side sort flag — sort client-side on
  `cost.idle_total` (descending) as above.
- If the user names a node group, filter to it instead of picking the top row.
- Record for the report: node group name, `autoscaler_type`, `cost.idle_total`
  (**Node Group Idle Cost**), and pull the cluster-level idle sum from Step 1
  as **Cluster Node Idle Cost**.
- Pull full recommendation detail for the chosen node group:

  ```bash
  pscli -o json nodegroups get -c <cluster> -g <node-group>
  ```

  `recommendations.type` is `"standard"` (ranked instance-type replacements,
  with cost/savings) or `"karpenter"` (NodePool config diff, with rationale
  per field, no dollar figure upstream). If `recommendations.has_changes` is
  `false`, note "No new recommendations available" in the report header
  instead of a recommended type — do not fabricate one.

---

## Step 3 — Pull unevictable pods for the node group

```bash
pscli -o jsonl unevictable list -c <cluster> -g <node-group> --all
```

This is the entire replacement for v1's Phases 5–7 (open node group → open
Unevictable Pods tab → click Summarize → 4-tier JS extraction). One call
returns every pod with its reasons, and each reason already carries
`remediation` (fix summary, risk, confidence, current/recommended spec, YAML
diff) — no text-parsing needed.

**Snapshot state handling** (`unevictable list` is served from a
precomputed snapshot, not a live recompute):

| Condition | Meaning | Action |
|---|---|---|
| Normal 200 response | Snapshot ready | Proceed |
| Distinct "processing" error (202) | Snapshot still computing | Tell the user, wait, retry once after ~30s |
| Distinct "failed" error (422) | Snapshot failed to process | **Only place a UI step is still useful**: tell the user to open InfraFit → Snapshot selector (top-right) and pick a different snapshot, then re-run |
| `pods: []`, `summary.total_pods: 0` | No data for this scope | Report "no unevictable pods" — check `nodegroups get`'s idle cost separately; a nonzero idle cost with zero unevictable pods usually means a bin-packing issue, not a blocker (suggest the node group's recommendation instead) |

For per-pod remediation detail beyond what `list` returns (sibling pod names),
use:

```bash
pscli -o json unevictable show -c <cluster> -i <pod-uid>
```

For a flatter one-row-per-pod view with all reasons combined (useful for a
quick top-N-by-cost pass before the full classification below):

```bash
pscli -o jsonl unevictable report -c <cluster> -g <node-group> --all -s blockedCostHourly -r desc
```

Check active mute rules before reporting (muted findings are excluded by
default — `--mute include` to see them, `--mute only` to see only them):

```bash
pscli -o jsonl unevictable muted -c <cluster>
```

---

## Step 4 — Classify every pod/reason

Each element of `unevictable list`'s `pods[]` array has `blocked_node_count`,
`blocked_cost_hourly`, `spec.node`/`spec.node_group`, and a `reasons[]` array;
each reason has `reason_code`, `reason` (human text), `details`, and
`remediation`. Group by `reason_code` first — it's already discrete, so this
replaces v1's entire regex-based text classifier. `--reason` on `unevictable
list`/`report` also lets you pull one category server-side if you already know
which one you're chasing.

For any `reason_code` you haven't seen before, fall back to matching the same
keyword families v1 used against `reason`/`details` text, so classification
never silently drops a pod into an unlabeled bucket:

| Bucket | Match against `reason` / `details` |
|---|---|
| `anti_affinity` | `anti-affinity on topology key 'kubernetes.io/hostname'` — extract sibling count from `colocating with (\d+) sibling` |
| `karpenter_opt` | `do-not-evict`, `do-not-disrupt`, "Opt-Out Annotation" |
| `mirror_pod` | `mirror pod`, `config.mirror` |
| `emptydir` | `emptyDir`, "Cluster Autoscaler treats as local storage" — extract volume name from `emptyDir volume '([^']+)'`; flag `sqlite|\.db$|wal|journal` volume names as `review_needed` |
| `hostpath` | `hostPath` |
| `pdb` | `PodDisruptionBudget`, "can't be disrupted" — extract PDB name/`maxUnavailable`/`minAvailable`; `maxUnavailable === 0` is a **total block**, highest priority |
| `local_pv` | `local` + `PersistentVolume` |
| `other` | anything else — keep verbatim in the report rather than forcing a bucket |

Build per-bucket aggregates: count, percentage of total, affected namespaces,
sample pods (and the sibling/volume/PDB sub-breakdowns above). Build
`top_nodes_by_unevictable` by grouping pods on `spec.node`, sorted by
unevictable count descending, top 5.

### emptyDir workload origin cross-check

The volume name is a signal, not proof. For the `emptydir` bucket, derive the
owning workload from each pod's name (strip the ReplicaSet/pod-generated suffix
— `-[a-z0-9]{5,10}-[a-z0-9]{5}$` or trailing `-\d+`) and group by
`(volume_name, namespace/workload)` into `by_volume_workloads`.

For any emptyDir volume with **more than 10 affected pods**:

1. Cross-reference the `(volume_name, workload)` pairs against the **Well-Known
   System Pods** table in `references/analysis-reference.md`.
2. If the workload prefix is recognized (`istio-proxy`, `envoy`, `coredns`,
   `gtw-*`, `gateway-proxy-*`), state the origin with confidence.
3. If the prefix is **not** recognized, do not assert an origin from the volume
   name alone — `workload-socket` is a common Istio pattern, not a guarantee.
   Mark the volume **UNCONFIRMED** and put the origin-check command in the
   report so the customer can verify:

   ```bash
   kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.spec.containers[*].name}'
   ```

`perfectscale-tenant-savings-report-v2.0` reuses this skill's InfraFit output
in customer-facing deliverables, so an unverified sidecar attribution here
propagates downstream. Confirm it or label it.

---

## Step 5 — Generate the Report

Read `references/analysis-reference.md` for pattern-specific fix syntax and
the Well-Known System Pods table — unchanged from v1. Prefer each reason's own
`remediation.fix_summary` / `remediation.yaml_diff` when present; use the
static reference only to fill in narrative ("why CAS blocks this") or when
`remediation` is null for a given reason.

**Scale report depth to pod count** (unchanged from v1):

| Unevictable Pod Count | Report depth |
|---|---|
| ≤ 10 | Pod-by-pod analysis for every pod |
| 11–50 | Pattern-level + per-namespace breakdown per pattern |
| > 50 | Pattern-level only; list namespaces; skip individual pod names |

**Report structure:**

```
## InfraFit Analysis — [cluster-name]

**Generated:** [now, local run time — pscli has no report-generation timestamp]
**Cluster:** [cluster name/UID]
**Node Group:** [node-group-name]
**Autoscaler:** [nodegroups.autoscaler_type]
**Recommended Type:** [type + savings from nodegroups get] OR omit if has_changes=false
**Timeframe:** [nodegroups --period, default P30D]
**Data source:** PerfectScale public API (pscli) — snapshot time [unevictable list summary/snapshot_time]

---

### Quick Reference

| Pattern | Pods | % | Blocker Type | Fix Complexity |
|---|---|---|---|---|
[one row per pattern with count > 0]

**Core Problem:** [one sentence: dominant pattern → what it costs → what it blocks]

---

### Executive Summary

| Metric | Value |
|---|---|
| Visible Nodes | [unevictable summary.total_nodes] |
| Visible Pods | [unevictable summary.total_pods] |
| Unevictable Pods | [unevictable summary.unevictable_pods] |
| Node Group Idle Cost | [nodegroups cost.idle_total]/hr |
| Node Group Potential Savings | [nodegroups get recommendation savings] OR "No recommendation available" |
| Cluster Node Idle Cost | [Step 1 sum]/hr |

---

### Top Nodes by Unevictable Pod Count

[Top 5 from Step 4's grouping: node name, instance type if known, count, dominant reason]

---

### Pattern Analysis

[One section per pattern with count > 0, ordered by pod count descending —
same sub-tables as v1: anti-affinity sibling breakdown, PDB by_pdb summary
with total_block flags, emptyDir review_needed pods, karpenter_opt annotation
types. For emptyDir, list the `by_volume_workloads` breakdown and list any
UNCONFIRMED volume separately from the confirmed ones.]

**Fix:** [remediation.fix_summary / yaml_diff from the API, or the static
Pattern Taxonomy fix syntax if remediation is null]

---

### Combined Impact & Priority

[Dominant root cause, per-pod vs policy-level fix, total blocked cost if
fixed, risk level]

### Recommended Actions (priority order)

1. [Most impactful fix — exact commands]
2. [Next fix]
3. [Verification — CAS/Karpenter log-tail command]
```

**After generating the report**, save it to
`infrafit-[cluster-name]-[YYYY-MM-DD].md` in the user's working directory and
tell the user the filename.

---

## What Not To Do

- **Don't fall back to browser automation just because it's familiar** — if
  `pscli` returns data, use it. Reserve the UI for the two genuine gaps: token
  generation and a failed/still-processing snapshot.
- **Don't assert an emptyDir origin from the volume name alone** —
  `workload-socket` is a strong signal, not proof. Cross-reference
  `by_volume_workloads` when the count exceeds 10; if the workload prefix is
  unrecognized, label it UNCONFIRMED and give the kubectl origin-check command.
- **Don't apply `safe-to-evict` to PDB- or anti-affinity-blocked pods** — wrong
  fix for those patterns.
- **Don't strip `do-not-evict`/`do-not-disrupt`** from single-replica
  admission-controller pods.
- **Don't treat `reason_code` values you don't recognize as `other` without
  first checking the keyword table** — the enum may grow; text matching is the
  safety net.
- **Don't run pod-by-pod analysis for > 50 unevictable pods** — pattern-level only.
- **Don't silently drop muted findings from the count** — state how many were
  excluded (`unevictable list --mute include` to see them) so the report's
  totals are honest.

---

## Error Recovery

| Symptom | Recovery |
|---|---|
| `pscli auth status` fails on every known profile | Ask the user for a Read-Only service token (Org Settings → API Tokens in the UI) — the one unavoidable UI step |
| `unevictable list` returns a 202 | Snapshot processing — wait ~30s, retry once |
| `unevictable list` returns a 422 | Snapshot failed — tell the user to pick a different snapshot via the UI's Snapshot selector (InfraFit page, top-right), no CLI equivalent exists |
| `unevictable_pods: 0` but node group idle cost > 0 | Bin-packing issue, not a blocker — point at `nodegroups get`'s recommendation instead |
| `nodegroups get` returns `has_changes: false` | Omit Recommended Type from the header; state "No new recommendations available" |
| emptyDir volume workload prefix unrecognized (count > 10) | Label the volume UNCONFIRMED, include the kubectl origin-check command in the report, don't guess |
| Cluster/node-group name ambiguous (multiple matches) | `pscli` accepts name or UID — ask the user to confirm which, or pass the UID directly |
