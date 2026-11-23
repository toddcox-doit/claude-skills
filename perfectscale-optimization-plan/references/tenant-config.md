# Tenant config keys
See `psplan/config.py` DEFAULTS for every key and default. Required: customer, clusters (name + uid). Paths are relative to the folder containing `config/`. `thresholds`, `stateful_patterns`, `nonprod_patterns`, `managed_namespaces`, `auto_provisioned_pool_patterns`, `gpu_pool_patterns` tune classification without code changes.

`foreign_tenants` is the leak-scan list: the names of other customers this working copy has been run against. The verifier fails the run if one of them reaches a rendered file. It defaults to empty, so set it on every run in a folder that has held more than one tenant. No customer name is hard-coded in the library.

`assumptions.native_vpa` records whether this tenant also runs upstream
Kubernetes-native VPA (a distinct object from PerfectScale's own PodFit
right-sizing) alongside PerfectScale. `pscli` has no VPA field anywhere and
never will — a native VPA object is not something the public API would ever
expose — so this can only be answered by asking the customer. Default `null`:
an open assumption, never guessed into a fact. Set `true`/`false`/a short
descriptive string once asked; `true` (or a string) surfaces a one-line
"confirm no conflicting mutation" caveat on the matching right-sizing action's
evidence. Same open-assumption mechanism as `assumptions.gitops`.

`hpa_file` is optional JSON from the PodFit browser pass (per-workload HPA
presence/trigger thresholds); see `references/eligibility-enrichment.md`.
Absent, HPA status is unconfirmed and the plan says so.

`api_credentials_file` (default unset) points at a `KEY=VALUE` file holding
`PS_ID` and `PS_SECRET` for the public API v2 step; it must be readable by its
owner only. Without it the collector also checks `PERFECTSCALE_CLIENT_ID` /
`PERFECTSCALE_CLIENT_SECRET` and then `~/.perfectscale/<profile>.env`
(`new_tenant.py auth --profile <name>` writes that file and signs pscli in with
the same token; `scripts/set_api_credentials.sh` is a bash-only alternative). No credentials: the v2 step
is skipped, never failed, and HPA status reads Unknown. `public_api_base`
overrides the API host (default `https://api.app.perfectscale.io`).

`hpa_pinned_namespaces` (default empty) lists namespace regexes whose pod
CPU/memory HPAs the customer has **confirmed** are pinned (`minReplicas` equals
`maxReplicas`). Without it, a workload behind such an HPA is put on Hold: a
smaller request moves the HPA's scaling point. A namespace where every workload
is on Hold is shown greyed in the kickoff page's namespace picker, with its
savings and the reason, and the "Kept out of this table" list counts it. Listing
the namespace here treats its workloads like ones without an HPA: Wave 1, ticked
by default, and covered by a namespace config. The public API has no HPA
min/max, so set this only after asking. Flat replica counts are a hint, not
proof, because an HPA that never crosses its target looks the same. Anchor the
patterns: `^payments$`.

`pov_top_n` (default 50) is how many workloads each cluster's POV automation
kickoff table holds. `pov_savings_weight` (default 0.6) is the savings share of
its 0-100 ranking score; the rest is risk severity. See
`references/pov-kickoff.md`.

`savings_goal` (default unset) is the POV savings target, set against what the
data supports. The executive summary carries it as "Savings goal vs what the
data supports" (page 2 of the `.docx`, so page 1 stays the one-pager), and the
plan JSON as `goal`. Copy the target from goal #1 of the POV plan doc:

```json
"savings_goal": {
  "source": "POV plan v1, 11 Nov 2026, goal #1",
  "baseline_source": "Billing export or DoiT report (to be agreed)",
  "clusters": {
    "prod-a": {"monthly_usd": 9000},
    "stg":    {"monthly_usd": [15000, 20000]},
    "dev":    {"pct": 20, "baseline_usd": 30000}
  },
  "tenant": {"monthly_usd": 25000}
}
```

Each target is `monthly_usd` (a number, or `[low, high]` for a range) or `pct`.
A percentage needs a baseline: the cluster's `baseline_usd` when given,
otherwise PerfectScale's measured node cost, and the summary says which. Use
`clusters`, `tenant`, or both; with two or more cluster goals and no `tenant`,
an all-clusters row sums them. Cluster names must match `clusters`; a bad name
or value stops the run at config load. A range is judged on its low end.

Each goal is set against three run-rate numbers built from the plan's own data:
conservative = right-sizing potential x `adoption_factor`; realistic =
right-sizing potential + `goal_idle_share` (default 0.5) of node idle, which
needs bin-packing so the autoscaler can remove nodes; maximum = right-sizing
potential + all node idle (PerfectScale's ceiling, never reached). Verdict:
Supported (within conservative), Stretch (within realistic), Unlikely (between
realistic and maximum), Not supported (above maximum). Each row's sentence says
exactly what the goal would take, such as "every right-sizing recommendation
and 56% of node idle". `verify` recomputes the numbers and verdicts from the
facts. Logic: `psplan/goal.py`.
