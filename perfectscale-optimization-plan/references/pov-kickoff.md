# POV automation kickoff

One self-contained HTML page per cluster, written by every run beside the plan
(`<customer>-<cluster>-pov-automation-kickoff-<date>-<tag>.html`). It sets the
scope of a PerfectScale POV on that cluster, from the day automation is switched
on to the technical win, and turns that scope into automation configs the
customer can review and apply. Code: `scripts/psplan/pov.py`, ranking in
`model.pov_rank`.

## The table

The top `pov_top_n` (default 50) workloads, in one table, ranked by one score:

```
score (0-100) = 100 x ( w x savings / largest savings in the candidate pool
                      + (1 - w) x risk severity / 4 )
```

`w` is `pov_savings_weight` (default 0.6). Savings are the workload's
PerfectScale potential savings at the run's monthly run-rate. Risk severity is
the highest risk indicator on the workload or any container, 0-4 (Low, Medium,
High, Critical); an OOM signal never reads below High. Savings lead because the
configs the page generates are cost-optimization configs. Risk still lifts a
workload that is already failing, so the POV starts with the reliability picture
in view rather than discovering it after automation is on. A workload with
neither savings nor risk scores 0 and is left out.

Columns: Rank, Workload, Namespace, **Controller type**, Monthly cost, Potential
savings, Risk, Top issue, Resiliency spend (likely), Score, Review before
automating. Savings and resiliency spend are different money and are never
added together, the same rule as the plan.

"Review before automating" flags what a cost-only config does not handle on its
own: stateful workloads (agree a restart window), JVM workloads from
`jvm_patterns` (move the heap flag with the memory request), MaxSavings policy
on an at-risk workload, a known HPA (a smaller request moves its scaling point),
and DaemonSets (decrease-only).

### Kept out, and counted

- **Platform-managed namespaces** (kube-system and similar, the plan's `managed`
  lane). PerfectScale does not automate kube-system by default, and a platform
  add-on is reconciled by the platform itself.
- **Held out by the tenant config** (`held_out_from_automation`).
- **Kinds no automation CR can target directly**: GroupedJob, GroupedPod, bare
  pods. They need a custom grouping rule first; automation then targets the
  grouped type.

Each is listed under "Kept out of this table" with its workload count and
potential savings, never dropped silently.

## The controller-type pull-down

Lists the kinds in the table, plus any kind only a namespace config would reach
(a DaemonSet that never ranked, bare Jobs), each with its count in the table, all
selected by default. It drives both generators. Unticking a kind hides its rows,
recomputes the scope line (workloads, namespaces, savings, High/Critical count),
and recomputes every namespace's figures in the namespace picker.

## Two generators, independent of each other

Both produce the cluster prerequisite plus their own files, shown with Download,
Copy and Preview, plus **Download all (.zip)**.

| File | Contents |
|---|---|
| `<cluster>-cluster-automation-config.yaml` | The `ClusterAutomationConfig` every cluster needs once (only one is allowed). Every supported kind is `Disabled` at cluster scope, so automation runs only where the other files enable it. Skip it if the cluster already has one. |

**Generate workload CRDs** (top bar) works from the ranked table: every row has
an **In scope** checkbox, ticked by default, with **Select all** and **Clear**
above the table. Only rows that are ticked *and* of a selected kind are written,
one file per workload, so a workload the customer does not want is dropped
before any CR exists rather than deleted from a file afterwards. An unticked row
is struck through and leaves the scope line's counts.

A row with under $5/mo of waste starts **unticked**. $5 is PerfectScale's own
default `workloadMinWasteUSDPerMonth`: automation skips such a workload, and
since these configs only lower requests, a CR would change nothing. Such rows
rank on risk, so they stay in the table, and the review column says to fix the
risk on its own. On real tenants this is typically a handful of
CPU-throttling, restart and HPA-at-max workloads near the bottom of the top 50.
The same rule applies to namespaces: one the table touches is pre-ticked only
if its covered workloads carry at least $5/mo of waste.

| File | Contents |
|---|---|
| `workloads/<ns>-<kind>-<name>-workload-automation-config.yaml` | One `WorkloadAutomationConfig`, `targetRef` = kind + name, with its rank in the header. Written in rank order. |

**Generate namespace CRDs** (namespace picker) works from the ticked namespaces,
one file per namespace even when several are ticked:

| File | Contents |
|---|---|
| `namespaces/<ns>-namespace-automation-config.yaml` | That namespace's `NamespaceAutomationConfig`, then a `Disabled` `WorkloadAutomationConfig` for each workload kept out of it. |

### The namespace picker

It lists every namespace with something a namespace config can automate, not
only the ones the ranked table touches: a namespace of many small workloads can
carry more savings than any one ranked workload and never reach the top 50. The
namespaces the table touches (with at least $5/mo of waste to act on) are
ticked by default; **Select all**, **Clear**
and **Match the table** re-set the ticks. Per namespace, for the selected kinds:
workloads covered, kinds enabled, potential savings, High/Critical count,
workloads kept out, and workloads ticked in the ranked table. **Only
namespaces that run one of the selected kinds are shown**: unselect
StatefulSet and a namespace of nothing but StatefulSets leaves the picker. A
hidden namespace keeps its tick for when the kind comes back, but is never
counted or generated while hidden. Platform-managed namespaces (kube-system and
similar) follow the same rule and appear last, greyed out, with the checkbox
disabled. A namespace where everything is held out, stateful, JVM, or of a kind
no namespace config can enable is never listed; the page counts them.

A namespace config enables a kind only if that kind is selected **and** an
automatable workload of it runs in the namespace, so a namespace with no
StatefulSets never gets StatefulSet automation switched on for later.

### Kept out of a namespace config

A namespace config covers every workload of its enabled kinds, so each file
also disables, by name, the workloads that should not ride along: held out by
the tenant config (`held_out_from_automation`), stateful (`stateful_patterns`,
the plan's manual lane), and JVM (`jvm_patterns`, where the heap flag has to
move with the memory request). Each exclusion is a `WorkloadAutomationConfig`
with `automationMode: "Disabled"` and the reason as a comment, written only when
its kind is enabled. Workload settings override namespace settings, so the
exclusion holds.

An exclusion uses the same `metadata.name` the workload generator gives that
workload. A workload you deliberately pick in the ranked table (a stateful
broker, say) therefore replaces its namespace exclusion rather than conflicting
with it, provided the workload files are applied after the namespace files.

### Applying

Precedence is PerfectScale's own: workload settings override namespace
settings, which override cluster settings. Namespace files automate whole
namespaces; workload files alone automate only the ticked workloads. Using
both: apply the namespace files first, then the workload files.

Every namespace and workload config carries the **full `operational` block** of
PerfectScale's namespace and workload CR templates (docs.perfectscale.io,
"Configuring Automation for a namespace / workload with CRD"), every field
explicit at its documented default, each with a short comment. It sits in
`spec.automation.operational` for a workload config, and in
`spec.automation.workloadTypes.<kind>.operational` for each kind a namespace
config enables. Placement was checked against the chart's CRD schemas
(`charts/psc-autoscaler/crds` in github.com/perfectscale-io/perfectscale-io.github.io).

| Field | Value | Why |
|---|---|---|
| `automationMode` | `"Enabled"` | automate this scope |
| `automationStrategy` | `"inPlace"` | documented default; `"evictMutate"` recreates pods instead |
| `podResizePendingStrategy` | `"rollingRestart"` | documented default when an in-place resize cannot fit |
| `duringRolloutInProgress` | `pause` | hold new recommendations while concurrent ReplicaSets exist |
| `timeConstraints.wasteMaxAutomationFrequency` | per kind: `30m` (Deployment, Rollout, CronJob, Job), `4h` (DaemonSet), `24h` (StatefulSet) | PerfectScale's documented defaults |
| `timeConstraints.fixResiliencyMaxAutomationFrequency` | `"30m"` | documented default |
| `timeConstraints.maintenanceWindow` | every day `00:00-23:59` | template default; the comment says to narrow it to the customer's change window |
| `timeConstraints.maintenanceWindowIgnoredMinResiliencyLevel` | `None` | never act outside the window |
| `restrictions.workloadMinWasteUSDPerMonth` | `5` | documented default; the table's default ticks use the same $5 |
| `cpuManagement.request` | increase `false`, decrease `true`, min/max cores `-1`, runaway detection 3/day, 6/week | cost optimization; documented bounds and limits |
| `cpuManagement.limit.keepLimit` | **`false`**, with the REVIEW comment | the deliberate change from the template default `true` |
| `memoryManagement.request` | increase `false`, decrease `true`, min/max GiB `-1` | cost optimization |
| `memoryManagement.limit` | increase/decrease `false`, min/max GiB `-1`, leak detection 3/day, 6/week | memory limits left as they are |

The `keepLimit` lines look like this in every config:

```yaml
          limit:
            # REVIEW: keepLimit is false so PerfectScale can remove the CPU limit; a right-sized CPU
            # request is then not throttled by a leftover limit. Set it to true if a ResourceQuota on
            # limits.cpu, a LimitRange, or an internal policy requires every container to keep a CPU limit.
            keepLimit: false  # REVIEW: see the note above before applying
```

Before applying, check the namespace for a ResourceQuota on `limits.cpu`: a
quota like that rejects new pods without a CPU limit unless a LimitRange
supplies a default.

Left out on purpose: the deprecated `maintenanceWindowIgnoredForResiliency` and
`maxAutomationFrequency`; the per-container `containers:` override section of
the workload template (optional, for tuning one container); and schema fields no
PerfectScale template uses (`nodeSizeAwareAutomation`, `unresizablePodPolicy`,
`resourceQuotaAwareAutomation`, `injectedSidecars`,
`splitInitRuntimeRecommendations`, `hpa`). Add any of them by hand after the
POV scope is agreed.

Requests only go down. Raising a request to fix a resiliency risk is not enabled
here, so the POV measures savings on their own; turn `increaseEnabled` on once
the cost baseline is agreed. Per-kind cautions from PerfectScale's docs travel
as comments with the config (StatefulSet restarts and dedicated nodes, DaemonSet
decrease-only, Jobs and CronJobs picking the change up on the next pod).

Names are always double-quoted so a namespace like `123` or a workload named
`true` stays a string. Workload config names are `<kind>-<name>` as a DNS-1123
subdomain, so a Deployment and a StatefulSet sharing a name do not collide.

## How it stays correct

- The YAML is built in Python. The page's browser builders (`JS_BUILD`,
  `JS_BUILD_NS`) only reassemble pieces Python rendered, and tests run them under
  node and compare their output byte-for-byte with `pov.bundle()` and
  `pov.bundle_ns()` for several selections. Another test unzips the browser ZIP
  and compares it with the same files.
- `verify.py` checks every kickoff page: its rows match the model's ranking,
  nothing managed or held out is ranked, every config sets `keepLimit: false`
  with the inline REVIEW comment, every config carries the full operational block, no config enables an increase, the namespace
  picker lists exactly the namespaces the model found automatable, every
  platform-managed namespace's checkbox is disabled and it can never be
  generated, and every namespace exclusion is a Disabled config. The page also
  passes the same leak scan, self-containment and table-control checks as the plan.

## Walking a customer through it

1. Open the page on the screen share. Agree the controller types in scope; most
   POVs start with Deployments and add StatefulSets only with the owner present.
2. Read the "Review before automating" column row by row for the top of the
   table; settle each flag or untick the kind.
3. Decide the shape: whole namespaces (tick them in the namespace picker and read
   each one's Kept out count with its owner), named workloads (tick them in the ranked table),
   or both.
4. Generate. Walk the `keepLimit` REVIEW comment with the platform owner and
   confirm the ResourceQuota/LimitRange answer.
5. The customer applies. Read-only on our side: the page writes files and
   nothing else.
6. After applying, check one workload's merged config in PerfectScale (zoom-in,
   Automation Config CR, mergedConfig) before widening the scope.
