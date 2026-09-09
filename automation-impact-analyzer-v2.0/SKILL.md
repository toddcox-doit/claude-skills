---
name: automation-impact-analyzer-v2.0
description: >
  Analyzes the impact of PerfectScale automation on a Kubernetes namespace (or cluster/workload),
  CLI-first via `pscli` — the browser path is a documented fallback only, not the primary route.
  Use this skill whenever someone asks about automation impact, savings from automation, risk changes
  after enabling automation, before/after automation comparison, what automation has done, or
  whether automation is working. Trigger on phrases like "automation impact", "what did automation
  do", "before and after automation", "automation savings", "automation enabled", "automation
  results", "did automation help", "show me the impact", or any question combining
  "automation" with waste, risk, cost, savings, or namespace-level analysis.
compatibility: >
  Preferred and default: pscli (public API service token) — no browser needed for the standard
  report. UI fallback (Claude-in-Chrome or playwright-cli) only when pscli auth is unavailable, or
  to enrich with two UI-only fields pscli doesn't expose: the Addressed Resiliency Issues chart and
  the cumulative Impact Tracker dollar figure.
---

# Automation Impact Analyzer (v2.0, CLI-first)

This skill produces a customer-ready Markdown report that shows what PerfectScale Automation has
done in a given namespace (or cluster/workload scope) and quantifies the impact in terms of waste
reduction, risk improvement, cost savings, and automation coverage.

v1 defaulted to browser navigation. v2.0 makes `pscli` the default path — the browser is now
strictly an opt-in fallback for auth failures or the two UI-only fields called out above; run the
`pscli` path first every time.

## Preferred data path — pscli (no browser, default)

Requires an authenticated `pscli` profile with access to the target tenant. See the
**perfectscale-cli** skill for bootstrap.

```bash
export PERFECTSCALE_PROFILE=<profile>
pscli auth status
pscli clusters list -o table
```

### pscli Step 1 — Cluster baseline

```bash
pscli -o json workloads summary -c <CLUSTER> -w 30d
pscli -o jsonl workloads group-by risk-severity -c <CLUSTER> -w 30d
pscli -o jsonl workloads list -c <CLUSTER> -w 30d -s waste -r desc -T 20 -V all
```

Capture: total cost, waste, potential savings, risky workload counts, top waste workloads.

### pscli Step 2 — Automation audit log (last 30 days)

```bash
pscli -o jsonl automation audit-logs -c <CLUSTER> --all --since 720h > /tmp/ps-audit-<CLUSTER>.jsonl
```

Analyze entries:

- Count; first/last timestamps
- Unique `(namespace, workload_name)` count
- Execution types: `inplace-resize` (In-Place), `regular-eviction` (Via Restart)
- Sum `container.cpu.cpu_request_change_absolute` (millicores) and memory MiB deltas

If **zero** entries: automation has not acted in the last 30 days — still write a baseline from
Step 1.

### pscli Step 3 — Cross-reference and report

Same report template as below. Note source: `pscli public API`.

## Fallback / enrichment — PerfectScale UI

Use the UI only when pscli is unavailable (auth failure) or for UI-only fields (Addressed
Resiliency Issues chart, Impact Tracker cumulative $).

Data is gathered by navigating the PerfectScale web UI at `https://app.perfectscale.io` using
browser tools. This requires the user to already be logged in to PerfectScale — no additional auth
setup needed.

The two pages you need are:
1. **Automation Audit Log** (`/automation`) — what automation did and when
2. **Podfit** (`/pod-fit/<cluster-uid>`) — current waste, risk, and cost per workload

Use `get_page_text` on each page — it captures the full rendered table data reliably and is
faster than reading the DOM or taking screenshots for data extraction.

> **Note on direct API calls:** The PerfectScale API (`api.app.perfectscale.io`) is cross-origin
> from the app domain and blocks browser-side fetch calls. Do not attempt JS fetch calls —
> use `get_page_text` instead.

## Step 1 — Get a browser tab

```
tabs_context_mcp (createIfEmpty: true)
```

Note the `tabId` — use it for all subsequent calls.

## Step 2 — Navigate to the Automation Audit Log

```
navigate → https://app.perfectscale.io/automation
```

Wait ~2 seconds for data to load, then:

```
get_page_text
```

From this page extract:
- **Total automation actions** (shown in the "Automation Actions (N)" heading)
- **First and last event timestamps** — the earliest timestamp = when automation was first enabled
- **Per-action rows**: date, cluster, namespace, workload, type, container, execution method
  (In-Place vs Via Restart), CPU Δ (milliCores), Memory Δ (MiB)
- **Summary charts**: Addressed Resiliency Issues count, CPU Optimization trend, Memory Optimization trend
- **Timeframe** shown in the header (default: last 30 days)

If you need to scope to a specific namespace, use the Namespace filter dropdown before calling
`get_page_text`:
```
computer → left_click on the Namespace "All" dropdown → select the namespace
wait 1s → get_page_text
```

If there are **zero automation actions**, tell the user automation has not acted on this scope in
the last 30 days and still produce a current-state baseline from Podfit.

## Step 3 — Navigate to Podfit

```
navigate → https://app.perfectscale.io/pod-fit
```

The page loads the default cluster. If the user has multiple clusters, use the cluster selector
dropdown at the top-left to pick the right one.

Wait ~2 seconds, then:
```
get_page_text
```

If you need to scope to a namespace, use the Namespace filter in the workloads table.

From this page extract:
- **Current Risks** count and severity (shown in the top-left card)
- **Pod Waste** and **Node Idle** (Unused Resources card)
- **Cost / period** and **Potential Optimized Cost**
- **Automation status** (Active/Inactive, policy name shown in the header)
- **Per-workload rows**: name, namespace, type, automation icon (✓ = automated), cost, waste,
  savings opportunity, risk mitigation, CPU/memory current → recommended values

To see risk details per workload, click the "Labels and Policies" or check the risk column. A
red dot next to a workload name = High risk.

## Step 4 — Cross-reference and calculate

**Automation coverage:**
- Workloads that appear in audit log rows = "automated workloads"
- Workloads with the automation icon in Podfit = "automation-enabled workloads"
- Workloads with high waste but no audit log entries = "not yet automated" (next candidates)

**Before vs. After:**
The audit log timeframe (shown in the header) tells you the window. Since the UI defaults to
30 days, the "before" is only knowable if the first audit event is within that window:

- **First event within the window**: automation was enabled ~N days ago. CPU/Memory graph in
  the Automation page shows a visible cliff — describe it ("resources dropped from X to Y on
  date Z, consistent with the first automation pass").
- **Events span the full window**: automation has been running ≥30 days. Describe the current
  state as "post-automation steady state"; use the remaining Podfit waste as "headroom still
  to capture."

**Resource savings from audit log:**
Sum the CPU Δ and Memory Δ columns across all actions to get total resources freed.
- CPU in milliCores → divide by 1000 for vCPUs
- Memory in MiB → divide by 1024 for GiB
- The Podfit waste columns give the dollar value of remaining over-provisioning

**Risk delta:**
Compare risk indicators between automated and non-automated workloads. The "Addressed
Resiliency Issues" counter on the Automation page is the clearest signal — a non-zero count
means automation prevented OOM/throttling events.

## Step 5 — Write the report

Produce a Markdown report with this structure:

```markdown
# PerfectScale Automation Impact — <Namespace> / <Cluster>

_Report generated: <date>_

## Summary
One paragraph: when automation was enabled, how many workloads it acted on, and the headline
number (CPU freed, waste reduced, cost saved). Lead with the most compelling figure.

## Automation Activity (Last 30 Days)
| Metric | Value |
|---|---|
| First automation event | <date> |
| Total automation actions | <N> |
| Workloads touched | <N> of <total> |
| Execution types | <In-Place: N, Via Restart: N> |
| Resiliency issues addressed | <N> |
| Automation coverage | <N>% of workloads on an automation policy |
| Total CPU freed | ~<X> vCPUs (<Y> millicores) |
| Total Memory freed | ~<X> GiB (<Y> MiB) |

Activity narrative: describe the timeline shape (e.g., "burst of Via Restart actions on day 1
followed by In-Place tuning — consistent with automation converging on optimal values").

## Waste & Cost Impact
| Metric | Value |
|---|---|
| Current pod waste | $<X>/<period> |
| Current cluster cost | $<X>/<period> |
| Waste ratio | <X>% |
| Potential remaining savings | $<X>/mo |
| Impact Tracker (cumulative saved) | $<X> |

Narrative: which workloads hold the most remaining waste, what automation has already closed.

## Risk Impact
| Risk Level | Automated Workloads | Non-Automated Workloads |
|---|---|---|
| High | <N> | <N> |
| Risk mitigation flagged | <N> | <N> |
| No risk | <N> | <N> |

Narrative: did automation correlate with lower risk? Call out any workload where automation
is still tuning (multiple actions in short window) or where risk persists despite automation.

## Top Automated Workloads
| Workload | Actions | Net CPU Δ | Net Memory Δ | Notes |
|---|---|---|---|---|
| ... | ... | ... | ... | ... |

## What's Not Yet Automated
Workloads with significant waste or savings opportunity but no audit log entries — the next
enablement candidates. Include the waste amount and savings opportunity from Podfit.

## Next Steps
2–3 concrete, actionable items with the specific workload name and dollar figure attached.
```

## Tone and framing

This report is often shared with a customer as part of a Proof of Value readout. Write clearly
and confidently, but be honest about data limits (e.g., "the UI shows the last 30 days; if
automation was enabled before that window, the exact start date isn't available"). Quantify
wherever possible; qualify when estimating.

## Hard limits (be upfront)

- The Automation page defaults to **30 days** — if automation was enabled before that, the first
  event in the log marks at least 30 days of activity, not the actual start date.
- Podfit also shows **7 or 30 days** depending on the timeframe selector — note which one you used.
- If the user asks for longer-term historical comparison, explain the UI limitation and produce
  the best available report from the current window.
