# Action catalog

The lookup table behind every `r_*` rule in `psplan/actions.py`. Every action in a plan must have an
`action_type` from this file — the vocabulary is **closed**. If a finding does not
map, use `other` and write the effort tier's justification explicitly in
`effort_rationale`; do not invent a new type silently, because an unlisted type
is an unauditable effort score.

Catalog tier is a **floor**. Apply the escalators in `roi-scoring.md` § 2 on top.

---

## Action record schema

Every action carries these fields. `Plan.add` in `psplan/actions.py` is the
authoritative form; `render.ACOLS` is the column order the artifacts use.

| Field | Type | Notes |
|---|---|---|
| `id` | string | `A1`, `A2`, … Stable across re-runs so the next plan is a diff |
| `title` | string | Imperative, names the target. "Enable automation on `platform` namespace policy" — not "Automation opportunity" |
| `action_type` | enum | From this file |
| `cluster` | string | |
| `scope` | string | Namespace, workload, or node group |
| `savings_monthly_usd` | number \| null | `null` only for pure resiliency |
| `savings_basis` | enum | `achievable_now` \| `blocked_pending` \| `resiliency_investment` |
| `confidence` | enum | `high` \| `medium` \| `low` |
| `effort_tier` | enum | `XS` \| `S` \| `M` \| `L` — after escalators |
| `effort_rationale` | string | `"S (catalog) -> M: change-managed (prod, restart)"` |
| `change_risk` | enum | `low` \| `medium` \| `high` \| `critical` |
| `owner_role` | enum | See § Owner roles |
| `depends_on` | string[] | Action ids |
| `unblocks` | string[] | Action ids |
| `released_value` | number | Transitive sum over `unblocks`; computed by the engine |
| `is_unblocker` | boolean | Set by the promotion rule |
| `phase` | 1 \| 2 \| 3 | |
| `roi_score` | number \| null | `null` for resiliency actions |
| `steps` | string[] | The "detailed optimization steps" — see § Step writing |
| `verification` | string | The observable check, with its expected direction |
| `evidence` | string | Where this came from, in customer language |

---

## Owner roles

Use the role, never a person's name — the customer assigns the name in the
session, in the `Owner` column. That blank is the point.

| Role | Owns |
|---|---|
| `ps-admin` | PerfectScale settings: policies, automation toggles, custom grouping, restrictions |
| `platform` | Cluster-level: node groups, autoscaler config, PDBs, LimitRanges, admission controllers, storage classes |
| `app-team` | Workload manifests and Helm values in an application repo. Name the namespace so the customer knows which team |
| `finops` | Commitment/RI/savings-plan coverage re-alignment after capacity drops |
| `ps-csm` | Anything requiring PerfectScale: entitlement, licensing, policy design, custom grouping setup |

The `finops` role matters more than it looks. Right-sizing a fleet under a
committed-use discount can cut on-demand spend to zero while the commitment keeps
billing — the customer's realized saving is then far below the platform figure
until coverage is re-cut. Whenever a plan's total capacity reduction is material,
emit a `commitment_realignment` action. Missing it is the single most common way
a technically correct plan produces a disappointing invoice.

---

## Automation-status proxy (CLI-only runs)

`workloadAutomationStatusV2` has no public-API field. On a CLI-only run,
reconstruct it from signals that *are* public. Evaluate in order and stop at the
first match — the earlier rules are exact, the later ones are heuristic.

| # | Signal | Inferred status | Confidence | Action type |
|---|---|---|---|---|
| 1 | Workload appears in `automation audit-logs` within the window | **Automated** | exact | none — already working; count it as proof, not opportunity |
| 2 | Workload appears in `workloads muted` | **DisabledByUser** | exact | `restriction_clear_user`, XS |
| 3 | Workload type is DaemonSet (`group-by type`) | eligible but **held by convention** | exact | `daemonset_rightsize`, S — keep out of the automation ask |
| 4 | Name or namespace matches a known-unsupported family: `kafka`, `strimzi`, `zookeeper`, `opensearch`, `elasticsearch`, `search` | **NotSupported** | high | `rightsize_manual` / `rightsize_stateful` — exclude from the automation business case |
| 5 | Workload type is StatefulSet and carries a local PV or data-path indicator | **likely NotSupported or high-friction** | medium | `rightsize_stateful`, M |
| 6 | Its optimization policy has automation on, with audit history, yet this workload has none | **likely Limited** | medium | `restriction_clear_system`, M — cause unconfirmed |
| 7 | Its optimization policy has no automation history at all | **likely NotEnabled** | medium | `automation_enable_policy` at the policy level, floored at S |
| 8 | Tenant-wide: no audit events on any cluster, and the customer confirms the Automation page shows "Request Access" | **not entitled** | exact | `automation_entitlement`, XS, huge `released_value` |

Rules 1-4 and 8 are exact or near-exact and need no hedging. Rules 5-7 are
inferences: apply `roi-scoring.md` § CLI-only scoring discipline — floor at S,
cap confidence at medium, and end `effort_rationale` with
`(inferred — cause not confirmed)`.

Rule 6 vs. rule 7 is the distinction the browser buys you, and it is worth
knowing what it is worth: rule 7 is an XS-to-S toggle, rule 6 is an M with an
unknown root cause. If a plan's Phase 1 leans heavily on rule-7 actions, that is
exactly when to spend ten minutes on the enrichment pass — not the whole estate,
just those.

**The `group-by optimization-policy` collapse comes first.** Run the proxy at
the policy level before the workload level. Fourteen workloads under one policy
with no automation history is *one* rule-7 action worth the sum of their
savings — not fourteen actions that individually look marginal and collectively
flood Phase 1.

## PodFit — workload right-sizing and automation

| `action_type` | Description | Floor | Default owner | Default risk |
|---|---|---|---|---|
| `automation_entitlement` | Automation not licensed/enabled for the tenant — the Automation page reads "Request Access" | XS | `ps-csm` | low |
| `automation_enable_policy` | Turn automation on for an optimization policy covering a namespace or a workload set | XS | `ps-admin` | medium |
| `automation_enable_workload` | Turn automation on for a single workload (use only when no policy covers it) | XS | `ps-admin` | medium |
| `restriction_clear_user` | Clear a user-set restriction — `DisabledByUser`, `IncreaseDisabled`, a manual bound | XS | `ps-admin` | low |
| `restriction_clear_system` | Resolve a system restriction (Limited by System). Not a toggle — needs the underlying cause fixed | M | `platform` | medium |
| `policy_headroom_tune` | Adjust ExtraHeadroom or a policy floor that is holding recommendations back | XS | `ps-admin` | medium |
| `custom_grouping` | Create a custom group so N identical workload copies share one policy | S | `ps-csm` | low |
| `rightsize_manual` | Apply recommended requests/limits by hand — for `NotSupported` workloads, or where automation is deliberately held | S | `app-team` | medium |
| `rightsize_stateful` | Manual right-sizing of a StatefulSet, Kafka/Strimzi, or a database | M | `app-team` | high |
| `limit_remove_cpu` | Remove a CPU limit where throttling level 3 says the limit is the problem | S | `app-team` | medium |
| `oom_remediate` | Raise memory to stop OOM kills. `resiliency_investment` — it costs money | S | `app-team` | high |
| `limitrange_adjust` | A namespace LimitRange or admission floor is blocking recommendations below it | S | `platform` | medium |
| `daemonset_rightsize` | Right-size a DaemonSet. Held out of the automation ask by convention; multiplies by node count | S | `platform` | medium |

**Note on `automation_enable_policy` risk.** Default `medium`, not `low`, even
though it is an XS toggle: it changes running workloads. Escalate to `high` in a
prod cluster where PerfectScale reports eviction rather than in-place resize.
Effort stays XS — one click is one click — and that gap is exactly why risk and
effort are separate columns.

---

## InfraFit — node groups, autoscaler, unevictable pods

| `action_type` | Description | Floor | Default owner | Default risk |
|---|---|---|---|---|
| `nodegroup_instance_change` | Move a node group to the recommended instance type | S | `platform` | high |
| `nodegroup_consolidate` | Merge or retire an underused node group | M | `platform` | high |
| `autoscaler_tune_ca` | Cluster Autoscaler flags: `scale-down-utilization-threshold`, `scale-down-unneeded-time`, `skip-nodes-with-local-storage`, `skip-nodes-with-system-pods` | M | `platform` | medium |
| `autoscaler_tune_karpenter` | Karpenter NodePool: `consolidationPolicy`, `consolidateAfter`, `disruption.budgets`, instance-type breadth | M | `platform` | medium |
| `unevictable_antiaffinity` | Relax a required anti-affinity rule to preferred, or widen its topology key | M | `app-team` | medium |
| `unevictable_pdb` | Fix an over-restrictive PDB — `minAvailable` equal to replica count, or a zero-disruption budget | S | `app-team` | medium |
| `unevictable_karpenter_optout` | Remove `karpenter.sh/do-not-disrupt` where it is no longer justified | S | `app-team` | medium |
| `unevictable_emptydir` | Remove a dependency on emptyDir that blocks eviction | M | `app-team` | medium |
| `unevictable_hostpath` | Remove a hostPath mount | L | `app-team` | high |
| `unevictable_localpv` | Migrate a local PV to network storage | L | `platform` | critical |
| `unevictable_mirror_pod` | Address a static/mirror pod pinning a node | L | `platform` | high |
| `unevictable_system_pod` | Move a kube-system or operator pod off a node the autoscaler needs to drain | M | `platform` | high |

**Note on `nodegroup_instance_change` risk.** An S-effort change — a value in a
node group definition — with `high` risk, because it drains and replaces nodes.
Almost always escalates to M via the restart-required escalator, and it should:
the work is not editing the value, it is the change window.

---

## Cross-cutting

| `action_type` | Description | Floor | Default owner | Default risk |
|---|---|---|---|---|
| `commitment_realignment` | Re-cut RI / savings-plan / CUD coverage after capacity drops | M | `finops` | medium |
| `policy_default_new_ns` | Default optimization policy applied to new namespaces automatically | S | `ps-admin` | low |
| `automation_default_on` | New eligible workloads get automation without a per-workload decision | S | `ps-admin` | medium |
| `review_cadence` | A recurring InfraFit/PodFit review with a named owner and a date | XS | `platform` | low |
| `plan_rerun` | Regenerate this plan on a set date and diff it | XS | `ps-csm` | low |
| `other` | Escape hatch. Justify the tier in `effort_rationale` | — | — | — |

---

## Dependency edges

Apply every rule that matches. These are the edges that recur across tenants;
add tenant-specific ones from what the data actually shows.

| Prerequisite | Unblocks | Why |
|---|---|---|
| `automation_entitlement` | Every `automation_*` and `policy_*` action, tenant-wide | Nothing automates until it is licensed and on. Usually the single largest `released_value` in the plan |
| `restriction_clear_user` | The right-sizing on that workload | The recommendation exists and is held back |
| `restriction_clear_system` | The right-sizing on that workload | Same, harder cause |
| `limitrange_adjust` | Every right-sizing action in that namespace bounded by the floor | One namespace-level fix releases a whole set |
| `policy_headroom_tune` | Right-sizing under that policy | Explains a Potential Max Savings lower than waste + idle |
| `custom_grouping` | `automation_enable_policy` for the grouped set | Group first, then one policy instead of N |
| `unevictable_*` on a node group | `autoscaler_tune_*` and `nodegroup_consolidate` on that group | Tuning the autoscaler while pods pin the nodes changes nothing. The most common sequencing error in the wild |
| `autoscaler_tune_*` | `commitment_realignment` | Coverage can only be re-cut once capacity settles |
| All PodFit right-sizing in a cluster | `nodegroup_consolidate` for that cluster | Pods must shrink and re-pack before nodes can leave — the PodFit → InfraFit flywheel |
| `automation_enable_policy` | `automation_default_on` | Prove it on a set before making it the default |

The **PodFit → InfraFit flywheel** deserves its own line in the plan's prose, not
just an edge in the graph: right-sizing shrinks pods, pods re-pack onto fewer
nodes, and only then does InfraFit show which nodes can leave. Customers
routinely run the two as independent tracks and conclude that right-sizing "didn't
lower the bill" — because the nodes never left. The plan should say this out
loud, once, near the phase boundary where it bites.

---

## Step writing

`steps` is where "detailed optimization steps" gets delivered. Rules:

1. **2-6 steps.** Fewer is not a step list; more is a runbook, and a runbook does
   not fit in a session.
2. **Every step is executable by the `owner_role` without a follow-up question.**
   Name the console path, the file, the field. "Update the PDB" is not a step;
   "`kubectl -n payments patch pdb checkout-api -p '{\"spec\":{\"minAvailable\":2}}'`"
   is.
3. **Prefer the API's own remediation.** When `remediation.fix_summary` or
   `yaml_diff` is present on an unevictable finding, use it verbatim rather than
   composing your own syntax. Fall back to
   `infrafit-improved-v2.0/references/analysis-reference.md` when it is null.
4. **First step is always verification of current state**, so the owner can
   confirm the finding is still true before changing anything. Between collection
   and execution the estate moves.
5. **Last step is always the action's `verification` method**, with the
   expected direction. An action without a check is a hope.
6. **No internal mechanics.** No `pscli`, no impersonation, no Backoffice. Steps
   are written for the customer's engineer, in their own console and their own
   kubectl.

### Example

```yaml
id: A5
title: "Clear emptyDir eviction blocker on ng-general (4 pods)"
action_type: unevictable_emptydir
cluster: prod-eu-west-a
scope: "node group ng-general — namespaces: analytics, reporting"
savings_monthly_usd: 0
savings_basis: achievable_now
confidence: high
effort_tier: S
effort_rationale: "S (catalog), no escalators — non-prod-adjacent, no restart needed"
change_risk: medium
owner_role: app-team
depends_on: []
unblocks: [A6]
released_value: 6400
is_unblocker: true
phase: 1
roi_score: 0
steps:
  - "Confirm still blocking: PerfectScale → InfraFit → ng-general → Unevictable Pods. Expect 4 pods across analytics and reporting."
  - "For each of the 4 Deployments, check whether the emptyDir volume holds durable state or scratch. Scratch is the common case — a cache directory or a temp path."
  - "Where it is scratch, add `cluster-autoscaler.kubernetes.io/safe-to-evict: \"true\"` to the pod template annotations."
  - "Where it holds durable state, move it to a PVC — this is a separate, larger change; split it out and track it in Phase 3."
  - "Roll the Deployments on the normal deploy cycle. No maintenance window needed."
  - "Verify: unevictable pod count for ng-general falls to zero within 24h. This is the gate for A6 (Karpenter consolidation tuning, $6,400/mo)."
verification: "Unevictable pod count on ng-general → 0"
evidence: "4 pods blocking node drain on ng-general, 30-day window. Blocks $6,400/mo of consolidation savings identified on this node group."
```

Note what the last step does: it names the downstream action and its dollar
value. In a session that is the sentence that gets a $0 chore prioritized.
