---
name: perfectscale-infrafit-analysis-v2.0
description: >
  Quick-look PerfectScale InfraFit unevictable-pods check via `pscli` — no
  browser required. Deliberately lightweight: which pods are unevictable, why,
  and on which nodes, with no customer-ready report. `infrafit-improved-v2.0`
  is the default for InfraFit work and covers the same ground in full
  (executive summary, pattern tables, fix syntax, emptyDir origin
  cross-check). Use this skill only when the user explicitly asks for a quick
  or terse check, names this skill, or wants the raw unevictable list without
  a report — for example "just list the unevictable pods", "quick infrafit
  check", "which pods are blocking, no report".
compatibility: >
  Preferred: pscli (public API service token). UI fallback only for token
  generation and for a snapshot that failed to process.
---

# PerfectScale InfraFit — Unevictable Pods Analysis (v2.0, CLI-first)

v1 of this skill drove Claude-in-Chrome through 12 UI steps (sort Overview by
Node Idle → click cluster → click InfraFit → set filters → open node group →
open Unevictable Pods tab → click Summarize → copy-to-clipboard → regex-parse
the report text). All of that is replaced by three `pscli` calls.

## Step 1 — Bootstrap pscli

Confirm `pscli` is authenticated (see the **perfectscale-cli** skill). If no
profile authenticates, ask the user for a Read-Only service token (Org
Settings → API Tokens → Generate Token) — the only UI step this skill still
needs.

## Step 2 — Find the cluster and node group

If the user names a cluster, use it directly. Otherwise rank clusters by idle
cost (there's no cost field on `clusters list`, so sum it from node groups):

```bash
for c in $(pscli -o jsonl clusters list | jq -r .name); do
  idle=$(pscli -o jsonl nodegroups list -c "$c" --all 2>/dev/null | jq -s '[.[].cost.idle_total] | add // 0')
  echo "$idle $c"
done | sort -rn | head -1
```

Then pick the node group with the highest idle cost in that cluster:

```bash
pscli -o jsonl nodegroups list -c <cluster> --all | jq -s 'sort_by(-.cost.idle_total)[0]'
```

If the node group's `pods.avg_count`/`nodes.avg` come back as effectively
empty, or the cluster has no node groups yet, tell the user the snapshot has
no data — same meaning as v1's "Node Types: 0" case.

## Step 3 — Pull and classify unevictable pods

```bash
pscli -o jsonl unevictable list -c <cluster> -g <node-group-name> --all
```

- A 202 response means the snapshot is still processing — wait ~30s and
  retry once.
- A 422 means the snapshot failed to process. This is the **one** remaining
  UI step: tell the user to open InfraFit → Snapshot selector (top-right) and
  pick a different snapshot, then re-run.
- Each pod's `reasons[]` already carries a `reason_code`, human-readable
  `reason`/`details`, and a `remediation` object (`fix_summary`, `risk`,
  `confidence`, `current_spec`, `recommended_spec`, `yaml_diff`) — there is no
  text to regex-parse anymore.

Group pods by `reason_code` (or by matching keywords in `reason`/`details` for
any code not yet catalogued): anti-affinity, Karpenter opt-out annotation,
mirror pod, emptyDir, hostPath, PodDisruptionBudget, local PV, other.

## Step 4 — Present the results

```
**Group: "[reason_code or short label]"**
- `namespace/pod-name` — [reason.reason text] — fix: [remediation.fix_summary]
- `namespace/pod-name` — [reason.reason text] — fix: [remediation.fix_summary]
```

If all pods share the same `reason_code`, say so plainly. Always include the
node name (`spec.node`) and node group for context. If the user wants the
fuller customer-ready report format (executive summary, pattern tables, fix
syntax reference), use `infrafit-improved-v2.0`
instead — this skill stays deliberately lightweight, matching its v1 scope.

## Error recovery

| Symptom | Recovery |
|---|---|
| No profile authenticates | Ask for a Read-Only service token (Org Settings → API Tokens) |
| `unevictable list` returns 202 | Snapshot processing — wait, retry once |
| `unevictable list` returns 422 | Only UI fallback: Snapshot selector, pick a different snapshot |
| `nodegroups list` returns an empty array | No InfraFit data for this cluster yet — tell the user, stop |
| Cluster or node group name ambiguous | Ask the user to confirm, or pass the UID directly |
