# Autoscaler-Tuning Recommendations by Type

`infrafit-improved` already detects and reports which autoscaler a cluster
runs (read off the `Node Autoscaler:` breadcrumb on the InfraFit page —
resolves to `CA` or `Karpenter`; a transient `Unknown` during page load
resolves on its own). It does **not** currently generate tuning guidance —
that's the new logic this file provides. Every recommendation below must be
**tied to a specific finding** from the cluster's own idle-cost figure or
unevictable-pod pattern breakdown (from `infrafit-improved` Phase 7's JSON
output) — never present these as generic advice with no number attached.

## If `Node Autoscaler: CA` (Cluster Autoscaler)

| Flag | Default | When to recommend a change |
|---|---|---|
| `--scale-down-utilization-threshold` | `0.5` | If idle-cost analysis shows nodes sitting just above this threshold without draining (i.e., utilization consistently in the 0.5–0.6 band), recommend lowering it — cite the observed utilization band. |
| `--scale-down-unneeded-time` | `10m` | If unevictable-pod findings show transient-only blockers (e.g., a completed Job or a pod that later became evictable), a shorter unneeded-time surfaces the savings sooner — cite the specific transient pattern found. |
| `--skip-nodes-with-local-storage` | `true` | If the `emptydir` pattern group shows most flagged volumes are **not** in the risky-name set (`sqlite`/`.db`/`wal`/`journal` per the main InfraFit reference's volume risk tiers), recommend a blanket flip to `false` as a complement to per-pod `safe-to-evict` annotations — always repeat the caveat about risky volume names needing the per-pod annotation route instead. |
| `--skip-nodes-with-system-pods` | `true` | Relevant when the `mirror_pod` pattern group has non-trivial count — explain the tradeoff (this flag going `false` unblocks nodes hosting only DaemonSet/mirror pods, but removes a safety rail for genuinely critical system pods). |
| `--balance-similar-node-groups` | unset | If multiple Current node groups in this cluster show lopsided idle cost relative to similar instance-family peers, recommend enabling this — cite the specific node groups and their idle-cost delta. |

## If `Node Autoscaler: Karpenter`

| Setting | When to recommend a change |
|---|---|
| `consolidationPolicy` (NodePool `disruption` block) | If idle cost is high but nodes aren't fully empty (mixed low-utilization pods), recommend `WhenEmptyOrUnderutilized` over `WhenEmpty` — cite the idle-cost figure that would be addressed. |
| `disruption.consolidateAfter` (replaces deprecated `ttlSecondsAfterEmpty`) | If unevictable-pod findings show transient-only blockers, recommend shortening this — cite the pattern. |
| `disruption.budgets` | If the `pdb` pattern group shows PDBs with `maxUnavailable=0` blocking during business hours specifically, recommend a scheduled disruption budget increase during low-traffic windows — cite the PDB name(s) and affected namespace(s) from the pattern breakdown. |
| Karpenter opt-out annotation migration (`do-not-evict` → `do-not-disrupt`) | Reuse `infrafit-improved/references/analysis-reference.md`'s existing Fix A/B/C `kubectl patch` syntax **verbatim** — this is already fully specified there; don't re-derive it. |

## If neither CA nor Karpenter (cloud-managed or unrecognized)

Covers GKE Autopilot, AKS node auto-provisioning, or any breadcrumb value that
doesn't resolve to `CA`/`Karpenter` after the normal transient-`Unknown` load
delay. State plainly: **autoscaler tuning is managed by the cloud provider and
not customer-configurable via flags/NodePool settings** — do not force
CA-shaped or Karpenter-shaped advice onto a cluster where neither config
surface exists. Redirect the tuning conversation entirely to workload-level
fixes (PDB tuning, anti-affinity review, opt-out-annotation cleanup) since
those remain effective regardless of which autoscaler implementation sits
underneath.

If the breadcrumb value is genuinely unrecognized (not `CA`, not `Karpenter`,
not a known cloud-managed label, and not the transient `Unknown` state that
resolves on reload) — per the older `perfectscale-infrafit-analysis-v2.0` skill's
documented failure mode — **flag it explicitly in the report** ("Node
Autoscaler: unrecognized value `<X>` — tuning guidance omitted, contact
PerfectScale support to confirm") rather than guessing or crashing.
