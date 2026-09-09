---
name: perfectscale-podfit-deployment-audit-v2.0
description: Audit Deployment-type workloads in a PerfectScale cluster via `pscli` to surface cost-optimization recommendations — no browser required. Use this skill whenever the user wants to look at PerfectScale, PodFit, savings opportunities, or asks "what's it going to take to capture the savings PerfectScale is recommending" for Kubernetes Deployments. Queries the cluster's workloads through the public API, sorts by waste/savings descending, and produces a per-Deployment report of current vs. recommended CPU/memory requests and limits. Trigger this even if the user just says "run the PerfectScale audit" or "check PodFit on <cluster>" — that's what this skill is for.
compatibility: >
  Preferred: pscli (public API service token). No browser dependency for the
  core workflow — the entire v1 UI walkthrough (five phases of navigation,
  click-sort workarounds, row-expand quirks) collapses into two pscli calls.
---

# PerfectScale PodFit Deployment Audit (v2.0, CLI-first)

v1 of this skill drove a Chrome browser through cluster selection, a
non-clickable "Savings Opportunity" column header, virtualized-table
extraction via `read_page`, and row-expand interactions to reveal recommended
values. All of that is one `pscli` call: workload cost/waste/recommendation
data is already structured, and the client-side `-t`/`-s`/`-r`/`-T` flags do
the type filter and sort that the UI couldn't.

## Inputs

1. **Cluster name or UID.** If the user doesn't give one, ask — don't guess.
2. **How many workloads** — default 10, matching v1's behavior.

## Step 1 — Bootstrap pscli

Confirm `pscli` is authenticated (see the **perfectscale-cli** skill). If no
profile authenticates, ask the user for a Read-Only service token (Org
Settings → API Tokens → Generate Token) — the only UI step this skill needs.

## Step 2 — Pull Deployments sorted by waste

```bash
pscli -o jsonl workloads list -c <cluster> -t Deployment -s waste -r desc -T 10 -V all
```

- `-t Deployment` replaces v1's "filter Type = Deployment" step.
- `-s waste -r desc` replaces the entire "Savings Opportunity column isn't
  sortable, extract and sort client-side" workaround — `waste` is the
  supported sort key closest to the UI's "Savings Opportunity" column.
  `-V all` also returns `potential_savings` per workload; cross-check the two
  and note in the report if they diverge materially (rounding aside).
- `-T 10` replaces "take the top 10" — done server-side/client-side by the
  CLI in one pass, no virtualized-table scrolling to miss rows.
- Each workload's `containers[]` already has
  `resources.current`/`resources.recommended` (CPU request/limit cores,
  memory request/limit MiB) — no row-expand or "Detailed (From → To)" view
  toggle needed.
- `optimization_policy` and `resilience_level` are top-level fields on each
  workload — use them for the "optimization policy in effect" note instead
  of reading it off the page header.

## Step 3 — Cluster-level context

```bash
pscli -o json workloads summary -c <cluster>
```

Gives `total_cost`, `total_waste`, `total_potential_saving`,
`risky_workloads`, `waste_workloads` for the report's Summary section
("Cluster context") — replaces reading the top-of-page cost/waste/node-idle
cards.

---

## Output format

```markdown
# PodFit Deployment Audit — [cluster name]
*Sorted by waste, top 10 Deployment workloads (pscli)*

## Summary
- Total Deployments reviewed: [N]
- Combined estimated savings (30-day window): $[sum of potential_savings]
- Largest single opportunity: [workload name] — $[X]
- **Optimization policy in effect:** [per-workload optimization_policy — note if it varies across the top 10]
- **Cluster context:** total_cost=$[X], total_waste=$[Y] (from workloads summary)

## Per-workload recommendations

### 1. [namespace/workload-name] — $[savings] estimated savings

**Container:** [container name(s)]

| Resource | Current | Recommended | Change |
|---|---|---|---|
| CPU request | [cpu_request_cores] | [recommended] | [+/-X% or "no change"] |
| CPU limit | [cpu_limit_cores] | [recommended] | [...] |
| Memory request | [memory_request_mib] | [recommended] | [...] |
| Memory limit | [memory_limit_mib] | [recommended] | [...] |

**Required changes:** [plain-English description of the actual edits needed]

### 2. [next workload]
[…same structure…]
```

Use the same template through #N. After the list, add an **Implementation
notes** section covering:

- **Identical recommendations across multiple workloads** — if three or more
  top-N Deployments share the same recommended values, they likely come from
  the same Helm chart; call this out (fix once at the chart level).
- **Workloads with zero usage data / no recommendation** — if
  `resources.recommended` equals `resources.current` and `waste` is 0, note
  "no right-sizing opportunity" rather than fabricating a change.
- **Unset fields** — a `0` in `cpu_limit_cores`/`memory_limit_mib` means no
  limit is currently set; distinguish that from "recommend no change."

---

## What Not To Do

- Don't fabricate numbers — every figure here traces to a `pscli` field.
- Don't apply changes; this is a read-only audit.
- Don't reach for the browser for this workflow — everything v1 needed the
  UI for (sorting, row expansion, cross-container extraction) is a plain
  field on the `pscli` workload object.
- Don't run the audit on a cluster the user didn't ask for — if the name is
  ambiguous, ask for the exact name or UID (`pscli clusters list` to check).

## Error Recovery

| Symptom | Recovery |
|---|---|
| No profile authenticates | Ask for a Read-Only service token (Org Settings → API Tokens) |
| Cluster name not found | Run `pscli clusters list` and confirm the exact name/UID with the user |
| `waste` and `potential_savings` diverge materially for a workload | Note the discrepancy in Implementation notes rather than silently picking one |
| All top-10 workloads show 0 waste | Tell the user this cluster's Deployments have no material right-sizing opportunity right now |
