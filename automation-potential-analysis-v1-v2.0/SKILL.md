---
name: automation-potential-analysis-v1-v2.0
description: >
  Analyze the automation POTENTIAL of a PerfectScale cluster, CLI-first via `pscli` for cluster
  totals/waste/custom-grouping candidates — find every workload that is NOT yet automated but IS
  eligible (excluding "Not Supported"), then report how many could be automated and the aggregate
  potential savings, where that savings is concentrated, and which repeated workload names are
  custom-grouping candidates. Use this whenever the user asks about automation potential,
  automation opportunity, "what could we automate", "how much could we save by automating",
  non-automated workloads, "what's not automated yet", automation candidates, or wants a
  pre-automation / proof-of-value savings estimate for a customer's cluster. This is
  forward-looking (what automation COULD do) — distinct from the automation-impact-analyzer skill
  (what automation HAS already done). Trigger even if the user just names a cluster and asks "how
  much automation upside is here".
compatibility: >
  Preferred: pscli (public API service token) for cluster totals, waste concentration, and
  duplicate-name grouping candidates — no browser needed for those. Browser tools
  (navigate, get_page_text, javascript_tool, find) with the user logged in to app.perfectscale.io
  are still required for exactly one thing: the eligible/NotSupported/Automated status split,
  which has no public-API equivalent. Works under a Backoffice impersonation
  session (`backoffice-impersonation`) for that piece.
---

# Automation Potential Analysis — v1 lineage (v2.0, CLI-first where possible)

Produce a customer-ready report of the automation opportunity in one (or more) PerfectScale
clusters: the count of automatable workloads, the aggregate potential savings, where that
opportunity is concentrated, and custom-grouping candidates.

**v2.0 vs v1:** v1 pulled the entire workload list from PerfectScale's internal PodFit API via
the browser, for everything — totals, waste, grouping candidates, and the eligibility split alike.
v2.0 uses `pscli` for everything the public API actually exposes, and reserves the browser step
for the one thing confirmed to have no public-API equivalent: `workloadAutomationStatusV2` (the
NotEnabled / DisabledByUser / NotSupported / Automated / Limited enum). Checked against the full
`pscli` command surface (`clusters`, `namespaces`, `workloads`, `nodegroups`, `unevictable`,
`automation`) — none carry an automation-eligibility field.

## Step A — pscli baseline (no browser)

Run this first, always — it gives you the reconciliation baseline for Step 3's sanity check and
answers "where's the savings concentrated" and "what are the grouping candidates" without a
browser session at all:

```bash
export PERFECTSCALE_PROFILE=<profile>
pscli auth status
pscli -o json workloads summary -c <CLUSTER> -w 30d
pscli -o jsonl workloads list -c <CLUSTER> -V all -s waste -r desc > /tmp/<CLUSTER>-all.jsonl
pscli -o jsonl workloads group-by namespace -c <CLUSTER> -s waste -r desc -T 15
pscli -o jsonl workloads group-by type -c <CLUSTER> -s waste -r desc
pscli -o jsonl workloads risky -c <CLUSTER> -S 2 -s severity -r desc
```

From `/tmp/<CLUSTER>-all.jsonl`, derive duplicate-name grouping candidates client-side (group by
`name`, count occurrences > 1, sort by count) — this replaces v1's `nameCount` accumulator in the
browser script, with no accuracy loss since `workloads list` isn't subject to the PodFit table's
virtualized-scroll row limit.

**Cannot compute via pscli:** eligible vs NotSupported vs Automated counts. Proceed to Step B for
that split only; everything else in this report should already be in hand from Step A.

## Step B — Eligibility split (browser, unavoidable)

This skill reads the COMPLETE workload list from PerfectScale's internal PodFit API using the
browser session's token — but now **only** to compute the eligibility split, not for totals or
grouping (those came from Step A). That matters for two reasons: (1) it works under Backoffice
impersonation with no service token, so it runs against any customer you can open in the browser,
and (2) the PodFit table virtualizes (~20–30 rows render at a time), so scraping the table would
silently miss most workloads on large clusters. All the brittle, internal-API details live in
`references/internal-api.md` and the `CONFIG` block of `scripts/podfit_analyze.js` — read those
first if anything fails.

## Inputs

- **cluster** — name or UUID to analyze (the user specifies; support several in one run).
- **period** — timeframe, default `30d`.

## Step 0 — Precondition: confirm a live session

Navigate to `https://app.perfectscale.io/overview` and read the page. If it redirects to a login
screen, or shows no account/clusters, **stop** and tell the user to log in (or start their
impersonation session) first. Scraping a logged-out page silently produces empty results — never
report numbers from an unauthenticated page.

## Step 1 — Open the cluster and capture its UUID

From Overview, identify the target cluster and open its PodFit view (click the cluster row). The
URL becomes:

```
https://app.perfectscale.io/pod-fit/<CLUSTER_UUID>?period=<PERIOD>
```

Record `<CLUSTER_UUID>` from the URL. If the user gave a cluster name, match it in the Overview
clusters table to get the UUID. Wait for PodFit to finish loading so the session token is present.

## Step 2 — Pull and analyze the full dataset (one injected script)

Use `javascript_tool` on the PodFit tab to run the bundled `scripts/podfit_analyze.js`, then call
its `run(clusterUuid, period)` entry point. It reads the `psc-token` from `localStorage`, fetches
the internal PodFit endpoint, filters to the eligible set, and returns a structured summary:

```js
// paste the full contents of scripts/podfit_analyze.js, then:
return await run("<CLUSTER_UUID>", "<PERIOD>");
```

The returned object contains `totalWorkloads`, `apiSummary`, and `analysis` (counts,
`eligibleCount`, `totalPotentialSavings`, `topBySavings`, `byNamespace`, `byType`,
`duplicateNames`, `unrecognizedStatuses`). Do the arithmetic in the script — do not re-derive it
by hand.

**Filter logic (the core):** eligible = `workloadAutomationStatusV2` ∈ {`NotEnabled`,
`DisabledByUser`}; exclude `NotSupported`; exclude automated states. See `references/internal-api.md`
for the full enum and field map.

## Step 3 — Mandatory sanity check (fail loudly)

In the PodFit UI, open the **Automation** column filter and select **Not Enabled** +
**Disabled by User** (leave **Not Supported** unchecked). Read the header badge "Workloads: N".

- If `analysis.eligibleCount` **equals** the badge → proceed.
- If they **differ**, or `unrecognizedStatuses` is non-empty, or the fetch/shape failed → **STOP**.
  Report the discrepancy plainly (expected vs got, any unrecognized status). This is an
  undocumented internal API; a mismatch means the enum, field map, or endpoint moved — report the
  breakage rather than publishing wrong numbers.

## Step 4 — Reconciliation (state it explicitly)

Raw field sums may not exactly equal the UI footer totals (the app applies display rounding and a
maturity-threshold filter on its footer). Treat the **UI footer as the authoritative headline
number**, present the script's field sums as the analytical breakdown, and **report the delta**
rather than implying they match.

## Step 5 — Write the report

Counting eligible workloads alone is misleading on two axes, and the report must defuse both:

- **Savings:** most eligible workloads often have ~$0 potential, and a single repeated Job can
  dominate the row count. Lead with where the savings actually are.
- **Risk:** low-severity flags (e.g. `CpuRequestNotSet`) are near-universal, so "N workloads at
  risk" is meaningless. Headline only **high-severity** risk (severity ≥ 2 — e.g. OOM,
  RestartsObserved, CpuThrottling); that is the risk automation actually mitigates.

Use this structure:

```markdown
# Automation Potential — <Cluster> (<period>)

_Generated <date>. Source: PerfectScale PodFit. Headline savings per UI footer; breakdown from API fields._

## Summary
One paragraph: total eligible workloads, how many actually carry savings (>$0), and the headline
potential savings (UI-footer figure, with the API-sum delta noted). Lead with the biggest number.

## Eligibility
| Metric | Value |
|---|---|
| Total eligible (Not Enabled + Disabled by User) | <N> |
| of which with savings > $0 | <N> |
| Not Supported (excluded) | <N> |
| Already automated | <N> |
| Total potential savings (API sum) | $<X> |
| UI footer figure / delta | $<X> / <±$Y> |
| High-severity risk workloads (sev ≥ 2) | <N> |

## Top Workloads by Savings
| Workload | Type | Namespace | Savings | High-sev risk |
|---|---|---|---|---|
| ... | ... | ... | $... | OOM / — |

## Risk to Mitigate (high severity only)
Only severity ≥ 2. List each workload with its risk type(s) — these are the resiliency wins
from automating, independent of dollar savings.

| Workload | Namespace | Severity | Risk |
|---|---|---|---|
| ... | ... | 3 | OOM |

Note the severity distribution (e.g. "605 of 611 carry only low-severity `CpuRequestNotSet`
noise; 5 carry high-severity risk") so the reader isn't misled by a large raw risk count.

## Where the Opportunity Is
- By namespace (top contributors): ...
- By workload type: ...

## Custom-Grouping Candidates (repeated names)
| Name | Type | Namespaces | Count | Total Cost |
|---|---|---|---|---|
| ... | ... | <span> | <count> | $... |

Suggest creating a PerfectScale custom grouping for high-count names so they are managed as one
unit instead of hundreds of rows.

## Data Integrity Notes
Sanity check (count vs UI badge): pass/fail. Any unrecognized statuses. Reconciliation delta.
```

## Multiple clusters

If the user names several clusters (or "the whole account"), repeat Steps 1–4 per cluster and add
a short roll-up table (cluster, eligible count, potential savings) before the per-cluster detail.
Re-verify the field map per cluster (Step 3 catches drift).

## Tone

Often shared with a customer as part of a proof-of-value readout. Be confident and concrete —
attach workload names and dollar figures — but honest about the data-integrity notes. Quantify
where possible; qualify when estimating.
