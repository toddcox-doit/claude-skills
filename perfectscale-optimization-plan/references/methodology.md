# Methodology and gotchas

## Measurement
- pscli `workloads list -V all` per cluster UID; rate limits (429) need a global limiter plus exponential backoff, never parallel bursts.
- Costs labelled 30d are actual spend over the connected window. Scale by 30/window_days only when window < min_history_days (default 14). Report observed and scaled side by side.
- Node pool "wall minutes" = running_minutes / avg_nodes; node-minutes overstate short-lived pool life.
- Idle cost is a pool-level upper bound, not a computed saving; never add it to workload potential savings.
- Resiliency ranges: low = p90 x 1.1, likely = p95 x 1.2 sizing, literal = PerfectScale recommendation. Headline the likely figure.
- Managed add-ons (kube-system, gke-managed, etc.) are held out of automation; stateful (Kafka, Elasticsearch) are manual.
- Request inflation: pools where requested/used CPU >= 5x drive machine shape; fix requests before autoscaler settings.
- Stale opt-out annotation: Karpenter `karpenter.sh/do-not-disrupt`, CAS `cluster-autoscaler.kubernetes.io/safe-to-evict: "false"`. Keep on running batch jobs.
- Grouping is recommended only when ungrouped jobs are numerous AND carry meaningful waste (`plain_job_group_min` and `plain_job_waste_min_usd`). High-count, near-zero-cost pods such as CI build agents will not trigger it on the count alone; lower `plain_job_waste_min_usd` if you want the recommendation anyway.

## Reading the cluster list (verify before you scope)
- `clusters list` can return records with an **empty `uid`**. These are registrations that never completed: they cannot be queried and must be excluded from the ranking. Report the count rather than quietly dropping them - a tenant that believes it has N clusters deserves to know why the tool sees fewer.
- **Cluster names are not unique.** Two live clusters can share a name, each with its own UID. Querying by name then fails with `RESOURCE_CONFLICT`. Always address clusters by UID in configs and commands.
- A cluster can return **zero workloads on a clean exit**. That is a real answer (decommissioned, or no longer reporting), not an error - cache it and move on rather than retrying.
- `pscli auth status` can report `expires_at` in the past while calls still succeed. Probe with a real call before concluding the token is dead.
- Detect rate limiting from the error envelope or an explicit 429, never by substring-matching the payload: a cost of `$429.10` is not a throttle.

## Classification to check per tenant
- `stateful_patterns` ships with the common engines, but every tenant runs something it does not list. **Typesense** is a worked example: a search engine that was being counted as an automatable controller until it was added. Scan the top workloads by potential for anything that stores or indexes data before trusting the automation total.
- Synthetic rows: the API emits placeholder namespaces (`__deleted-namespaces__`) and collapsed aggregates (type `-`, e.g. "Collapsed Ephemeral Pods"). Both are dropped; the aggregate in particular double-counts pods that are already listed individually and will break reconciliation against the raw files.
- `held_out_from_automation` matches a workload's **name or namespace**, so a production namespace can be held out wholesale.

## Tooling
- The one-pager needs the `docx` npm package installed globally (`npm install -g docx`); the scripts resolve it from the global roots, so `NODE_PATH` is not required. HTML output needs no module at all.
- Rendering a .docx to PDF with `soffice --convert-to pdf` writes `<name>.pdf` **next to the source**, overwriting a same-named PDF. Always pass `--outdir`.
- `pandoc -t plain` returns empty on some generated .docx files. An empty leak scan from pandoc proves nothing; extract from the archive XML instead.
