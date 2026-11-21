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

`pov_top_n` (default 50) is how many workloads each cluster's POV automation
kickoff table holds. `pov_savings_weight` (default 0.6) is the savings share of
its 0-100 ranking score; the rest is risk severity. See
`references/pov-kickoff.md`.
