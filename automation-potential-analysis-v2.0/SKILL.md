---
name: "automation-potential-analysis-v2.0"
description: "Analyze the automation POTENTIAL of a PerfectScale cluster, CLI-first via `pscli` for everything except automation-eligibility status (no public-API equivalent exists) — find every workload that is NOT yet automated but IS eligible (excluding \"Not Supported\"), then report how many could be automated, the aggregate potential savings, potential vCPU reduction, where that opportunity is concentrated, and which repeated workload names are custom-grouping candidates. Optionally extract manual right-sizing recommendations for Not Supported workloads (e.g. Kafka). Use whenever the user asks about automation potential, \"what could we automate\", \"how much could we save\", non-automated workloads, vCPU reduction, resource reduction, not supported workloads, manual recommendations, or wants a proof-of-value savings estimate. Distinct from automation-impact-analyzer (what automation HAS done)."
---

# Automation Potential Analysis (v2.0, CLI-first where possible)

Produce a customer-ready report of the automation opportunity in one (or more) PerfectScale
clusters: the count of automatable workloads, the aggregate potential savings, the potential vCPU
reduction, where that opportunity is concentrated, and custom-grouping candidates. Optionally
include manual right-sizing recommendations for Not Supported workloads.

**v2.0 vs v1:** v1 read the entire workload list from the browser (Step 2's internal PodFit
fetch) because that was the only data source it used. v2.0 makes `pscli` the default for
everything the public API actually exposes — cluster totals, waste concentration, vCPU/memory
deltas, custom-grouping candidates, risk — and narrows the browser step to the one thing that
remains a genuine, unavoidable gap: `workloadAutomationStatusV2` (the NotEnabled / DisabledByUser
/ NotSupported / Automated / Limited enum) has **no public-API field at all**, confirmed against
the full `pscli` schema (`clusters`, `namespaces`, `workloads`, `nodegroups`, `unevictable`,
`automation` — none carry an automation-eligibility enum). This is not a "prefer CLI, fall back to
UI" situation for that one field; there is no CLI path to it yet.

## Preferred path — pscli baseline (default for everything except eligibility)

`pscli` exposes waste, recommendations, and risk but **not** `workloadAutomationStatusV2`.
Run pscli first, always, for cluster totals, waste concentration, vCPU/memory deltas, and
custom-grouping candidates — do this even on runs where Step 2 (below) is also required, since it
gives you the reconciliation baseline for Step 3's sanity check. Use the browser/internal PodFit
API (Step 2 below) only for the eligibility split, which pscli cannot provide.

```bash
export PERFECTSCALE_PROFILE=<profile>
pscli auth status
pscli -o json workloads summary -c <CLUSTER> -w 30d
pscli -o jsonl workloads list -c <CLUSTER> -V all > /tmp/<CLUSTER>-all.jsonl
pscli -o jsonl workloads group-by namespace -c <CLUSTER> -s waste -r desc -T 15
pscli -o jsonl workloads risky -c <CLUSTER> -S 2 -s severity -r desc
```

**Cannot compute via pscli:** eligible vs NotSupported vs Automated counts — requires UI/internal
API (Step 2). State this gap in the report Data Integrity section.

This skill reads the COMPLETE workload list from PerfectScale's internal PodFit API using the
browser session's token. That matters for two reasons: (1) it works under Backoffice
impersonation with no service token, so it runs against any customer you can open in the browser, and (2) the
PodFit table virtualizes (~20–30 rows render at a time), so scraping the table would silently
miss most workloads on large clusters.

## Inputs

- **cluster** — name or UUID to analyze (the user specifies; support several in one run).
- **period** — timeframe, default `30d`.
- **include_not_supported** — if the user asks about Not Supported workloads or manual
  recommendations, also extract recommendations for those workloads (Step 2b).

## Known API Shape (confirmed 2026-07)

Use these field names directly — no probing required unless the sanity check fails.

**Response envelope:**
```js
raw.data.workloads  // array of workload objects — nested under raw.data, NOT raw directly
raw.data.summary    // cluster-level aggregates
raw.meta            // { dataStatus, requestedMinutes, successfulMinutes }
```

**Workload-level fields:**
```js
w.name
w.namespace
w.workloadType
w.workloadAutomationStatusV2   // "NotEnabled" | "DisabledByUser" | "NotSupported" |
                                // "Automated" | "AutomatedPartially" | "Limited" |
                                // "AutomatedLimited" | "Disabled"
w.wasteTimeFrameUsd            // SAVINGS FIELD — use this (NOT potentialSavings — doesn't exist)
w.historicalTimeFrameWaste     // different metric, always >= wasteTimeFrameUsd
w.timeframeCost
w.hourlyCost
w.avgReplicas
w.indicators                   // array of { name, severity, type } risk indicators
w.containers                   // OBJECT keyed by container name — NOT an array
                                // iterate with Object.values(w.containers)
```

**Note:** Some field key names are blocked by browser security when enumerated via `Object.keys()`,
but values ARE accessible by known property name (e.g. `w.workloadAutomationStatusV2` works fine).

**Container-level fields (CPU already in cores — no /1000 conversion needed):**
```js
c.cpuCoresRequest              // current CPU request (cores)
c.recommendCpuCoresRequest     // recommended CPU request (cores)
c.cpuCoresLimits               // current CPU limit (0 = no limit)
c.recommendCpuCoresLimits      // recommended CPU limit
c.memMiBRequest                // current memory request (MiB)
c.recommendMemMiBRequest       // recommended memory request (MiB)
c.memMiBLimits                 // current memory limit (0 = no limit)
c.recommendMemMiBLimits        // recommended memory limit
c.cpuCoresMaxUse               // observed max CPU usage
c.indicators                   // per-container risk indicators
```

**API summary fields:**
```js
apiSummary.workloadsPodFitSavingTimeFrame  // sum of wasteTimeFrameUsd across ALL workloads
                                            // including NotSupported — matches UI headline
apiSummary.workloadsHistoricalWasteTimeFrame
apiSummary.clusterCostTimeFrame
apiSummary.idleCost
apiSummary.recommendedCostTimeFrame
```

**Important:** `workloadsPodFitSavingTimeFrame` = sum of `wasteTimeFrameUsd` across ALL workloads
(not just eligible). A large gap between the eligible-only sum and this total usually means
NotSupported workloads (often Kafka StrimziPodSets) carry the majority of savings — always
surface this finding prominently.

**Synthetic entry to exclude:** Filter out `w.namespace === '__deleted-namespaces__'`.

## Step 0 — Precondition: confirm a live session

Navigate to `https://app.perfectscale.io/overview` and read the page. If it redirects to a login
screen, or shows no account/clusters, **stop** and tell the user to log in (or start their
impersonation session) first.

## Step 1 — Open the cluster and capture its UUID

From Overview, identify the target cluster and open its PodFit view. The URL becomes:

```
https://app.perfectscale.io/pod-fit/<CLUSTER_UUID>?period=<PERIOD>
```

Record `<CLUSTER_UUID>` from the URL. Wait for PodFit to finish loading so the session token is
present.

## Step 2 — Pull and analyze the full dataset (one injected script)

Use `javascript_tool` on the PodFit tab. The script below is production-verified — use it
directly rather than re-discovering field names.

```js
const CLUSTER_UUID = '<UUID>';
const PERIOD = '<PERIOD>';
const url = `https://api.app.perfectscale.io/psc-data-provider/api/v1/clusters/uid/${CLUSTER_UUID}/podfit/${PERIOD}?expand_ephemeral_workloads=false`;

const resp = await fetch(url, {
  headers: { Authorization: 'Bearer ' + localStorage.getItem('psc-token') }
});
const raw = await resp.json();
const workloads = raw.data.workloads;   // nested under raw.data
const apiSummary = raw.data.summary;
const totalWorkloads = workloads.length;

const ELIGIBLE = new Set(['NotEnabled', 'DisabledByUser']);
const KNOWN = new Set(['NotEnabled', 'DisabledByUser', 'NotSupported', 'Automated',
  'AutomatedPartially', 'Limited', 'AutomatedLimited', 'Disabled', 'EnabledAutomated']);
const unrecognizedStatuses = new Set();
const statusCounts = {};

workloads.forEach(w => {
  const s = w.workloadAutomationStatusV2;
  statusCounts[s] = (statusCounts[s] || 0) + 1;
  if (!KNOWN.has(s)) unrecognizedStatuses.add(s);
});

const eligible = workloads.filter(w =>
  ELIGIBLE.has(w.workloadAutomationStatusV2) &&
  w.namespace !== '__deleted-namespaces__'
);
const notSupportedCount = statusCounts['NotSupported'] || 0;

let totalPotentialSavings = 0, withSavings = 0;
let vcpuReductionCores = 0, vcpuAddedCores = 0;
let totalCurrentCpu = 0, totalRecommendedCpu = 0;
let memReductionMiB = 0, memAddedMiB = 0;
let totalCurrentMem = 0, totalRecommendedMem = 0;
let workloadsWithCpuReduction = 0;

// Tier split accumulators (clean = no high-sev indicators, highSev = any indicator sev >= 2)
let cleanCount = 0, cleanSavings = 0;
let cleanCpuRed = 0, cleanCpuAdd = 0;
let cleanMemRed = 0, cleanMemAdd = 0;
let highSevCount = 0, highSevSavings = 0;
let highSevCpuRed = 0, highSevCpuAdd = 0;
let highSevMemRed = 0, highSevMemAdd = 0;

const byNamespace = {}, byType = {}, nameCount = {};
const workloadList = [];

eligible.forEach(w => {
  const savings = w.wasteTimeFrameUsd ?? 0;  // wasteTimeFrameUsd is the savings field
  totalPotentialSavings += savings;
  if (savings > 0) withSavings++;

  let wCpuRed = 0, wCpuAdd = 0, wCurCpu = 0, wRecCpu = 0;
  let wMemRed = 0, wMemAdd = 0, wCurMem = 0, wRecMem = 0;

  // containers is an OBJECT keyed by container name — use Object.values()
  Object.values(w.containers || {}).forEach(c => {
    const curCpu = c.cpuCoresRequest ?? 0;           // already in cores, no /1000
    const recCpu = c.recommendCpuCoresRequest ?? curCpu;
    const curMem = c.memMiBRequest ?? 0;
    const recMem = c.recommendMemMiBRequest ?? curMem;
    wCurCpu += curCpu; wRecCpu += recCpu;
    wCurMem += curMem; wRecMem += recMem;
    if (curCpu - recCpu > 0) wCpuRed += curCpu - recCpu;
    if (recCpu - curCpu > 0) wCpuAdd += recCpu - curCpu;
    if (curMem - recMem > 0) wMemRed += curMem - recMem;
    if (recMem - curMem > 0) wMemAdd += recMem - curMem;
  });

  vcpuReductionCores += wCpuRed;
  vcpuAddedCores += wCpuAdd;
  totalCurrentCpu += wCurCpu; totalRecommendedCpu += wRecCpu;
  memReductionMiB += wMemRed;
  memAddedMiB += wMemAdd;
  totalCurrentMem += wCurMem; totalRecommendedMem += wRecMem;
  if (wCpuRed > 0) workloadsWithCpuReduction++;

  const ns = w.namespace || 'unknown';
  const wt = w.workloadType || 'unknown';
  if (!byNamespace[ns]) byNamespace[ns] = { count: 0, savings: 0, cpuRed: 0 };
  byNamespace[ns].count++; byNamespace[ns].savings += savings; byNamespace[ns].cpuRed += wCpuRed;
  if (!byType[wt]) byType[wt] = { count: 0, savings: 0, cpuRed: 0 };
  byType[wt].count++; byType[wt].savings += savings; byType[wt].cpuRed += wCpuRed;

  const name = w.name || 'unknown';
  nameCount[name] = (nameCount[name] || 0) + 1;

  const indicators = w.indicators || [];
  const highSevRisks = indicators.filter(ind => ind.severity >= 2);
  const isHighSev = highSevRisks.length > 0;

  // Tier split
  if (isHighSev) {
    highSevCount++; highSevSavings += savings;
    highSevCpuRed += wCpuRed; highSevCpuAdd += wCpuAdd;
    highSevMemRed += wMemRed; highSevMemAdd += wMemAdd;
  } else {
    cleanCount++; cleanSavings += savings;
    cleanCpuRed += wCpuRed; cleanCpuAdd += wCpuAdd;
    cleanMemRed += wMemRed; cleanMemAdd += wMemAdd;
  }

  workloadList.push({
    name, namespace: ns, type: wt,
    status: w.workloadAutomationStatusV2,
    savings: Math.round(savings * 100) / 100,
    cpuReductionCores: Math.round(wCpuRed * 1000) / 1000,
    currentCpu: Math.round(wCurCpu * 1000) / 1000,
    recommendedCpu: Math.round(wRecCpu * 1000) / 1000,
    highSevRisks: highSevRisks.length,
  });
});

const r2 = v => Math.round(v * 100) / 100;
const topBySavings = [...workloadList].sort((a, b) => b.savings - a.savings).slice(0, 10);
const dupNames = Object.entries(nameCount).filter(([,c]) => c > 1)
  .sort((a, b) => b[1] - a[1]).slice(0, 20);
const topNS = Object.entries(byNamespace).sort((a, b) => b[1].savings - a[1].savings).slice(0, 10);
const topTypes = Object.entries(byType).sort((a, b) => b[1].savings - a[1].savings);
const highSevWorkloads = workloadList.filter(w => w.highSevRisks > 0);

return {
  totalWorkloads, apiSummary, statusCounts,
  analysis: {
    eligibleCount: eligible.length,
    withSavings,
    totalPotentialSavings: r2(totalPotentialSavings),
    vcpuReductionCores: r2(vcpuReductionCores),
    vcpuAddedCores: r2(vcpuAddedCores),
    vcpuNetCores: r2(vcpuAddedCores - vcpuReductionCores),
    workloadsWithCpuReduction,
    totalCurrentCpuCores: r2(totalCurrentCpu),
    totalRecommendedCpuCores: r2(totalRecommendedCpu),
    memReductionMiB: r2(memReductionMiB),
    memAddedMiB: r2(memAddedMiB),
    memNetMiB: r2(memAddedMiB - memReductionMiB),
    totalCurrentMemMiB: r2(totalCurrentMem),
    totalRecommendedMemMiB: r2(totalRecommendedMem),
    notSupportedCount,
    unrecognizedStatuses: [...unrecognizedStatuses],
    cpuFieldNamesUsed: {
      currentField: 'containers[key].cpuCoresRequest',
      recommendedField: 'containers[key].recommendCpuCoresRequest'
    },
    tierSplit: {
      clean: {
        count: cleanCount,
        savings: r2(cleanSavings),
        cpuFreedCores: r2(cleanCpuRed),
        cpuAddedCores: r2(cleanCpuAdd),
        cpuNetCores: r2(cleanCpuAdd - cleanCpuRed),
        memFreedMiB: r2(cleanMemRed),
        memAddedMiB: r2(cleanMemAdd),
        memNetMiB: r2(cleanMemAdd - cleanMemRed),
      },
      highSev: {
        count: highSevCount,
        savings: r2(highSevSavings),
        cpuFreedCores: r2(highSevCpuRed),
        cpuAddedCores: r2(highSevCpuAdd),
        cpuNetCores: r2(highSevCpuAdd - highSevCpuRed),
        memFreedMiB: r2(highSevMemRed),
        memAddedMiB: r2(highSevMemAdd),
        memNetMiB: r2(highSevMemAdd - highSevMemRed),
      }
    },
    topBySavings, topNS, topTypes, dupNames,
    highSevWorkloads: highSevWorkloads.slice(0, 20),
    highSevCount: highSevWorkloads.length,
  }
};
```

## Step 2b — Not Supported workloads (optional)

Run when: the user asks about Not Supported workloads or manual recommendations, OR when
NotSupported workloads account for >20% of the total savings (`workloadsPodFitSavingTimeFrame`).

PerfectScale computes recommendations for NotSupported workloads even though it cannot automate
them. These are valuable for manual right-sizing.

```js
// Assumes workloads array already fetched in Step 2
const notSupported = workloads.filter(w =>
  w.workloadAutomationStatusV2 === 'NotSupported' &&
  w.namespace !== '__deleted-namespaces__'
);

const r2 = v => Math.round(v * 100) / 100;
const r3 = v => Math.round(v * 1000) / 1000;

const nsResults = notSupported.map(w => {
  let wCurCpu = 0, wRecCpu = 0, wCpuRed = 0, wCpuAdd = 0;
  let wCurMem = 0, wRecMem = 0, wMemRed = 0, wMemAdd = 0;

  Object.values(w.containers || {}).forEach(c => {
    const curCpu = c.cpuCoresRequest ?? 0;
    const recCpu = c.recommendCpuCoresRequest ?? curCpu;
    const curMem = c.memMiBRequest ?? 0;
    const recMem = c.recommendMemMiBRequest ?? curMem;
    wCurCpu += curCpu; wRecCpu += recCpu;
    wCurMem += curMem; wRecMem += recMem;
    if (curCpu - recCpu > 0) wCpuRed += curCpu - recCpu;
    if (recCpu - curCpu > 0) wCpuAdd += recCpu - curCpu;
    if (curMem - recMem > 0) wMemRed += curMem - recMem;
    if (recMem - curMem > 0) wMemAdd += recMem - curMem;
  });

  return {
    name: w.name, namespace: w.namespace, type: w.workloadType,
    waste: r2(w.wasteTimeFrameUsd ?? 0),
    avgReplicas: w.avgReplicas,
    cpuSummary: {
      currentCores: r3(wCurCpu), recommendedCores: r3(wRecCpu),
      deltaCores: r3(wRecCpu - wCurCpu),
      reductionCores: r3(wCpuRed), addedCores: r3(wCpuAdd)
    },
    memSummary: {
      currentMiB: r2(wCurMem), recommendedMiB: r2(wRecMem),
      deltaMiB: r2(wRecMem - wCurMem),
      reductionMiB: r2(wMemRed), addedMiB: r2(wMemAdd)
    },
  };
}).sort((a, b) => b.waste - a.waste);

return {
  count: notSupported.length,
  totalWaste: r2(notSupported.reduce((s, w) => s + (w.wasteTimeFrameUsd ?? 0), 0)),
  totalCpuReduction: r3(nsResults.reduce((s, w) => s + w.cpuSummary.reductionCores, 0)),
  totalCpuAdded: r3(nsResults.reduce((s, w) => s + w.cpuSummary.addedCores, 0)),
  totalCpuNet: r3(nsResults.reduce((s, w) => s + w.cpuSummary.deltaCores, 0)),
  totalMemReductionMiB: r2(nsResults.reduce((s, w) => s + w.memSummary.reductionMiB, 0)),
  totalMemAddedMiB: r2(nsResults.reduce((s, w) => s + w.memSummary.addedMiB, 0)),
  totalMemNetMiB: r2(nsResults.reduce((s, w) => s + w.memSummary.deltaMiB, 0)),
  workloads: nsResults
};
```

**Caution when reporting NotSupported recommendations:** CPU recommendations for stateful workloads
(especially Kafka brokers) can be very aggressive (e.g. 4→0.2 cores, −95%). Always recommend
applying incrementally with health monitoring. For Kafka, watch consumer lag and under-replicated
partitions between each step. Suggest conservative middle-ground values rather than blindly
applying the recommendation.

## Step 3 — Mandatory sanity check (fail loudly)

In the PodFit UI, open the **Automation** column filter and select **Not Enabled** +
**Disabled by User** (leave **Not Supported** unchecked). Read the header badge "Workloads: N".

- If `analysis.eligibleCount` **equals** the badge → proceed.
- If they **differ**, or `unrecognizedStatuses` is non-empty, or the fetch/shape failed → **STOP**.
  Report the discrepancy plainly. This is an undocumented internal API; a mismatch means the
  enum, field map, or endpoint moved.
- Also compare the UI footer savings figure for the filtered view against `totalPotentialSavings`
  — they should match within rounding ($0.01–$1 is normal).
- If `vcpuReductionCores === 0` for all eligible workloads but savings > $0, the CPU field name
  likely changed — note it in Data Integrity.

## Step 4 — Reconciliation (state it explicitly)

Treat the **UI footer as the authoritative headline number**. Present the script's field sums as
the analytical breakdown and report the delta. When the eligible-only savings is significantly
lower than `workloadsPodFitSavingTimeFrame`, identify the NotSupported workloads driving the gap
and call it out prominently — this is often the most important finding.

## Step 5 — Write the report

Counting eligible workloads alone is misleading on two axes:

- **Savings:** most eligible workloads often have ~$0 potential. Lead with where savings actually
  are, not the raw workload count.
- **Risk:** low-severity flags (e.g. `CpuRequestNotSet`) are near-universal. Headline only
  **high-severity** risk (severity ≥ 2); that is the risk automation actually mitigates.

Use this structure:

```markdown
# Automation Potential — <Cluster> (<period>)

_Generated <date>. Source: PerfectScale PodFit API. Headline savings per UI footer; breakdown from API fields._

## Summary
One paragraph. Lead with the biggest number. Call out eligible vs total savings gap if
NotSupported workloads are material. Example: "Enabling automation on the 345 eligible workloads
could save ~$124/month and reduce CPU requests by ~14.6 vCPUs. The larger $564 UI headline
includes $440 from 20 Not Supported Kafka workloads that require manual right-sizing."

## Eligibility
| Metric | Value |
|---|---|
| Total workloads in cluster | <N> |
| Total eligible (Not Enabled + Disabled by User) | <N> |
| of which with savings > $0 | <N> |
| Not Supported (excluded from automation) | <N> |
| Already automated | <N> |
| Total potential savings — eligible (API sum) | $<X> |
| UI footer figure (filtered to eligible) / delta | $<X> / <±$Y> |
| UI footer figure (all workloads, incl. Not Supported) | $<X> |
| **Potential vCPU reduction (over-prov. subset)** | **<X.X> cores** |
| Net CPU change across all eligible workloads | <±X.X> cores (+ = underprovisioned on net) |
| Net memory change across all eligible workloads | <±X.X> GiB |
| Workloads with CPU reduction | <N> |
| High-severity risk workloads (sev ≥ 2) | <N> |

## Top Workloads by Savings
| Workload | Type | Namespace | Savings/mo | CPU Δ (cores) | High-sev risk |
|---|---|---|---|---|---|

Note: CPU Δ is negative when automation would reduce CPU requests. Show "—" if recommendation
≥ current (workload is underprovisioned on CPU).

## Three-Tier Opportunity Summary

### Savings
| Category | Workloads | Savings/mo |
|---|---|---|
| Supported — eligible, no high-severity risk | <cleanCount> | $<cleanSavings> |
| Supported — eligible, high-severity risk (additional tuning required) | <highSevCount> | $<highSevSavings> |
| Not Supported — manual right-sizing only | <notSupportedCount> | $<nsWaste> |
| **Total cluster opportunity** | **<total>** | **$<total>** |

### vCPU Resource Impact
"Freed" = cores removed from over-provisioned workloads; "Added" = cores added to under-provisioned
workloads. Net = Added − Freed. Positive net means the cluster gains CPU overall after right-sizing.

| Category | Workloads | vCPU Freed (over-prov.) | vCPU Added (under-prov.) | Net vCPU Change |
|---|---|---|---|---|
| Supported — eligible, no high-sev risk | <cleanCount> | <clean.cpuFreedCores> cores | <clean.cpuAddedCores> cores | <clean.cpuNetCores> cores |
| Supported — eligible, high-sev risk (additional tuning required) | <highSevCount> | <highSev.cpuFreedCores> cores | <highSev.cpuAddedCores> cores | <highSev.cpuNetCores> cores |
| **↳ Eligible combined** | **<eligibleCount>** | **<vcpuReductionCores> cores** | **<vcpuAddedCores> cores** | **<vcpuNetCores> cores** |
| Not Supported — manual right-sizing only | <nsCount> | <ns.totalCpuReduction> cores | <ns.totalCpuAdded> cores | **<ns.totalCpuNet> cores** |
| **Total cluster opportunity** | **<N>** | **<grandFreed> cores** | **<grandAdded> cores** | **<grandNet> cores** |

> Math: Added − Freed = Net (eligible: X − X = ±X ✓ · Not Supported: X − X = ±X ✓ · Total: X − X = ±X ✓)

### Memory Resource Impact
| Category | Workloads | Memory Freed (over-prov.) | Memory Added (under-prov.) | Net Memory Change |
|---|---|---|---|---|
| Supported — eligible, no high-sev risk | <cleanCount> | <clean.memFreedGiB> GiB | <clean.memAddedGiB> GiB | <clean.memNetGiB> GiB |
| Supported — eligible, high-sev risk (additional tuning required) | <highSevCount> | <highSev.memFreedGiB> GiB | <highSev.memAddedGiB> GiB | <highSev.memNetGiB> GiB |
| **↳ Eligible combined** | **<eligibleCount>** | **<memReductionGiB> GiB** | **<memAddedGiB> GiB** | **<memNetGiB> GiB** |
| Not Supported — manual right-sizing only | <nsCount> | <ns.totalMemReductionGiB> GiB | <ns.totalMemAddedGiB> GiB | **<ns.totalMemNetGiB> GiB** |
| **Total cluster opportunity** | **<N>** | **<grandFreedGiB> GiB** | **<grandAddedGiB> GiB** | **<grandNetGiB> GiB** |

> Math: Added − Freed = Net (eligible: X − X = ±X ✓ · Not Supported: X − X = ±X ✓ · Total: X − X = ±X ✓)

**Category definitions:** "No high-severity risk" = eligible workloads with only low-severity
indicators — automation can be enabled directly. "Additional tuning required" = workloads with
high-severity indicators (sev ≥ 2: OOM risk, throttling, restarts) — review in PerfectScale UI
alongside automation enablement. "Not Supported" = workloads PerfectScale cannot automate; manual
right-sizing only. Memory in GiB (divide MiB values by 1024 to convert).

## Risk to Mitigate (high severity only)
Only severity ≥ 2. Note the severity distribution so the reader isn't misled by raw risk count.

| Workload | Namespace | Type | Savings/mo |
|---|---|---|---|

## Where the Opportunity Is
### By Namespace
| Namespace | Eligible workloads | Savings/mo | vCPU reduction |
|---|---|---|---|

### By Workload Type
| Type | Eligible workloads | Savings/mo | vCPU reduction |
|---|---|---|---|

## Not Supported Workloads — Manual Right-Sizing Opportunity
(Include when NotSupported workloads carry material savings, or when user requested it.)

Summarize total waste, CPU/memory reduction potential, and top workloads. Warn that Kafka and
StatefulSet recommendations should be applied incrementally. Include specific resource values
(conservative middle-ground, not blindly applying the aggressive PS number).

| Workload | Namespace | Type | Waste/mo | CPU: current→rec (cores) | CPU Δ | Mem: current→rec (MiB) | Mem Δ |
|---|---|---|---|---|---|---|---|

## Custom-Grouping Candidates (repeated names)
| Name | Count | Namespaces | Action |
|---|---|---|---|

Suggest creating a PerfectScale custom grouping for high-count names.

## Data Integrity Notes
| Check | Result |
|---|---|
| Sanity check (API eligible count vs UI badge) | ✅/❌ — API: N, UI badge: N |
| Savings reconciliation (API sum vs UI filtered footer) | ✅/❌ |
| Unrecognized automation statuses | None / list |
| CPU field names used | containers[key].cpuCoresRequest / containers[key].recommendCpuCoresRequest |
| Eligible vs total savings gap | $X eligible vs $Y total; $Z delta from N NotSupported workloads |
| Data maturity | meta.dataStatus, days observed |
```

## Multiple clusters

If the user names several clusters (or "the whole account"), repeat Steps 1–4 per cluster and add
a short roll-up table (cluster, eligible count, potential savings, vCPU reduction) before the
per-cluster detail.

## Tone

Often shared with a customer as part of a proof-of-value readout. Be confident and concrete —
attach workload names and dollar figures — but honest about data-integrity notes. When NotSupported
workloads carry the majority of savings, lead with that finding rather than burying it.

