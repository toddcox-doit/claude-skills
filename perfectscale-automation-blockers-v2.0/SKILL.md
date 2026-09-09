---
name: perfectscale-automation-blockers-v2.0
description: >
  Investigates why automation recommendations are not being applied to workloads in a
  cluster, CLI-first via `pscli` for preflight/scoping — the UI zoom-in tooltip walkthrough
  is required only for the per-container restriction text itself, which has no public-API
  equivalent. Use this skill whenever the user asks about automation blockers, why
  PerfectScale is not applying changes, why workloads show "Limited by User" or "Limited by
  System" status, or wants a summary of automation limitation reasons across a cluster. Also
  triggers when the user wants to audit PerfectScale automation health, find OOM-risk
  workloads with blocked automation, or understand what is preventing right-sizing from
  taking effect.
version: 2.0.1-v2.0
compatibility: >
  Preferred: pscli for scoping (does automation execute at all? which workloads are
  risky/wasteful?). UI zoom-in tooltip extraction remains required for the actual blocker
  reason text and `workloadAutomationStatusV2` Limited states — confirmed absent from the
  full pscli schema, not a "prefer CLI" gap but a hard one.
---

# Skill: Identify PerfectScale Automation Blockers (v2.0, CLI-first preflight)

## Overview

This skill identifies why automation recommendations are not being applied across
workloads in a cluster. **Always run the pscli preflight first** — it scopes the problem
(is automation running at all? which workloads carry risk/waste?) with zero browser calls.
UI zoom-in tooltips remain required for per-container restriction reasons: confirmed against
the full `pscli` command surface (`clusters`, `namespaces`, `workloads`, `nodegroups`,
`unevictable`, `automation`), no command exposes restriction tooltip text or the
`workloadAutomationStatusV2` Limited-by-User/System states. This is a hard gap, not a
convenience shortcut — don't skip the preflight, but don't expect it to replace Steps 0–6.

---

## Preferred preflight — pscli (no browser, always run first)

```bash
export PERFECTSCALE_PROFILE=<profile>
pscli auth status
pscli -o json workloads summary -c <CLUSTER> -w 30d
pscli -o jsonl automation audit-logs -c <CLUSTER> --all --since 720h
pscli -o jsonl workloads risky -c <CLUSTER> -S 2 -s severity -r desc
```

Interpret:

- **Zero audit-log entries** → automation has not applied changes recently; blockers analysis
  may be limited to workloads already on an automation policy (UI filter required).
- **summary.risky_workloads / waste_workloads** → scope for follow-up; cross-reference against
  `workloads risky` to prioritize which workloads' zoom-in panels to check first in Step 4,
  rather than iterating the full workload list blind.
- If `summary.workloads` is small (say, under ~15), consider going straight to Step 4's
  per-workload loop without the CSV-export step in Step 4 — the coverage risk that step
  guards against only matters at scale.

pscli does **not** expose automation restriction tooltips or `workloadAutomationStatusV2`
Limited states. Proceed to UI Steps 0–6 below for per-container blocker text.

---

## Changelog

- **2.0.1** — Replaced placeholder tooltip selectors in Steps 4b/4c with the confirmed
  live-DOM selectors (`.automation-restrictions` trigger, `[role="tooltip"]` popper, and
  the `aria-labelledby` fast path). No behavioral steps changed.
- **2.0.0** — Added authentication guard (Step 0), deterministic full-list capture via
  CSV export / scroll-loop (Step 4), per-container iteration (Step 4a-i), JS-based
  tooltip extraction as the default (Step 4b/4c), and a Validation section.
- **1.0.0** — Initial version: filter automated workloads, hover tooltips, rank blockers.

---

## Inputs

| Input | Description | Required | Default |
|-------|-------------|----------|---------|
| `cluster_name` | The name of the cluster to analyze | No | Auto-detect (use the only available cluster) |
| `timeframe` | The timeframe to use for analysis | No | 30 days |

---

## Outputs

| Output | Description |
|--------|-------------|
| `blockers_summary` | Ranked list of automation blocker reasons, ordered most to least common, with workload/container counts |
| `per_workload_details` | Detailed CPU and Memory blocker reasons per individual workload and container |
| `critical_flags` | Workloads flagged as critical risks (e.g., OOM restarts with automation blocked) |
| `coverage_report` | Count of workloads and containers captured vs. expected, to confirm completeness |

---

## Steps

### Step 0 — Authentication Guard (Precondition)

Before doing anything else, confirm the browser session is authenticated. Do NOT
proceed if the page is the login screen — scraping it silently produces empty/garbage
results.

1. Navigate to `https://app.perfectscale.io/overview`.
2. Run a check via `javascript_tool` (or read the page) to evaluate the session:

```js
(() => {
  const url = window.location.href.toLowerCase();
  const onLogin = /\/login|\/signin|\/auth/.test(url)
    || !!document.querySelector('input[type="password"]');
  const hasApp = !!document.querySelector('[data-testid="clusters-table"], table'); // Clusters table present
  return { url, onLogin, hasApp, title: document.title };
})();
```

3. If `onLogin` is true, or the Clusters table never appears, **stop** and tell the
   user: "You don't appear to be logged in to PerfectScale in the connected browser.
   Please log in, then re-run." Do not continue.

---

### Step 1 — Navigate to the Overview Page

1. Go to `https://app.perfectscale.io/overview`.
2. Wait for the page to fully load and the **Clusters** table to appear.

---

### Step 2 — Click on the Target Cluster

1. In the **Clusters** table, identify the target cluster using `cluster_name`.
2. If only one cluster exists, click it automatically.
3. Click the cluster name to navigate to the **PodFit / Workloads** view.

---

### Step 3 — Filter for Automated Workloads

1. Click the **Automation** column filter dropdown (default label: "All").
2. Select the **Automated** checkbox — this auto-selects all sub-states: Active,
   Limited by User, and Limited by System.
3. Close the dropdown.
4. Record the resulting **workload count** shown in the table header/badge — this is
   the expected total (`M`) used for the coverage check in Step 4.

---

### Step 4 — Build the Complete Workload List (no missed rows)

The PodFit table uses **virtual scrolling**: only ~20 rows render at a time, so visual
scrolling alone will miss rows below the fold. Use the most reliable available method.

#### Preferred — CSV export as source of truth
1. Use the table's **Export / Download CSV** action to obtain the full workload list.
2. Parse the CSV. Note: **multi-container workloads appear as one row per container**
   in the CSV — group rows by workload name and record the container count per
   workload. This gives you the authoritative set of workloads AND the number of
   containers to expect per workload (used in Step 4a-i).

#### Fallback — deterministic scroll loop
If CSV export is unavailable, capture rows programmatically rather than by eye:

```js
// Run repeatedly between scrolls; accumulate into a deduped set keyed by workload name.
(() => {
  const rows = [...document.querySelectorAll('[role="row"], table tbody tr')]
    .map(r => r.innerText.trim()).filter(Boolean);
  return rows;
})();
```

Loop: read visible rows → push into a `Set` keyed by workload name → scroll the
**table's scroll container** (not `window`) down by one viewport → repeat. Stop when
the set size equals the expected total `M` from Step 3, OR two consecutive scrolls add
no new rows. Cap at a sane max iteration count (e.g., 50) to avoid infinite loops.

Record `captured (N) vs expected (M)` for the coverage report.

---

### Step 4a — Iterate Through Each Workload

For every workload in the complete list:

#### Step 4a — Open the Zoom-in View
1. Click the workload name to open the zoom-in panel.
2. Wait for the panel to fully load.
3. Note the **Automation status** in the top-left (Active / Limited by User / Limited by System).

#### Step 4a-i — Iterate Over Containers
A single workload may have multiple containers; the zoom-in view shows one at a time
via a **Container dropdown**.

1. Locate the **Container dropdown** in the zoom-in panel.
2. Enumerate all options (cross-check against the container count from the CSV in
   Step 4 so none are skipped).
3. For **each** container, select it, then run Steps 4b–4d. Record all findings keyed
   by `workload / container`, not just `workload`.
4. If there is no dropdown (single-container workload), treat it as one container.

#### Step 4b — Check CPU Current Recommendations (JS-based tooltip extraction)
1. In the **CPU Over Time** section, locate the **Current Recommendations** panel.
2. Look for a circular-arrows icon (⟳) next to any CPU Limit or CPU Request value.
3. **Default method:** extract the tooltip via the DOM rather than coordinate hover —
   coordinate hovering is unreliable at small icon sizes. Dispatch `mouseenter` on the
   restriction div, wait ~200 ms, then read the rendered MUI tooltip popper. These
   selectors are confirmed against the live PerfectScale DOM:

```js
// Step 1: dispatch the hover events on the restriction trigger.
const div = document.querySelector('.automation-restrictions');
if (div) {
  div.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
  div.dispatchEvent(new MouseEvent('mouseover',  { bubbles: true }));
}
// Step 2: AFTER ~200 ms (use a setTimeout chain, not await — javascript_tool
// does not support top-level await in a for-loop), read the popper:
const tooltipEl = document.querySelector('[role="tooltip"]');
const text = tooltipEl ? tooltipEl.innerText.trim() : '';
```

   *Faster bulk alternative:* once a tooltip is visible, the restriction div gains an
   `aria-labelledby` pointing at the tooltip content element — read it directly:

```js
Array.from(document.querySelectorAll('[aria-labelledby]')).forEach(el => {
  const labelEl = document.getElementById(el.getAttribute('aria-labelledby'));
  if (labelEl) console.log(labelEl.innerText.trim());
});
```

   Add ~80 ms after `mouseleave` before triggering the next tooltip.

4. Capture the **full tooltip text including ALL bullet points**.
5. "All recommendations applied successfully" → record as **no blocker**.
6. "Recommendations appear once enough usage data is collected" → **insufficient data**.
7. *Fallback only:* if `.automation-restrictions` / `[role="tooltip"]` find no element,
   fall back to coordinate hover.

#### Step 4c — Check Memory Current Recommendations
1. In the **Memory Over Time** section, locate the **Current Recommendations** panel.
2. Look for the ⟳ icon next to any Memory Limit or Memory Request value.
3. Extract the tooltip using the same **JS-based method** as Step 4b. The same
   `.automation-restrictions` / `[role="tooltip"]` selectors apply; if a panel contains
   multiple restriction divs, iterate over `document.querySelectorAll('.automation-restrictions')`
   and trigger each in turn rather than only the first.
4. Capture the full tooltip text — tooltips may contain multiple bullet-point reasons.
5. "All recommendations applied successfully" → record as **no blocker**.

#### Step 4d — Record Critical Flags
1. Check if the container/workload shows OOM events, high restart counts, or Risk
   Mitigation costs alongside automation blockers.
2. If memory-increase blockers exist alongside OOM restarts → flag as **CRITICAL**.

#### Step 4e — Close the Zoom-in Panel
1. Click the **✕** button to close the panel.
2. Return to the workloads list and proceed to the next workload.

---

### Step 5 — Cross-Reference and Group Blockers

1. Group all recorded tooltip messages by their theme/reason.
2. Count the number of **containers (and distinct workloads)** that share each reason.
3. List the affected `workload / container` names for each reason.
4. Order reasons from most common to least common.

---

### Step 6 — Produce Output Summary

Generate a structured report containing:

1. **Coverage report** — captured vs. expected workloads/containers (confirm completeness).
2. **Ranked summary table** — blocker reasons with counts and affected names.
3. **Detailed per-workload/per-container findings** — CPU and Memory blockers.
4. **Critical flags section** — workloads with active risks (e.g., OOM with blocked automation).
5. **Key recommendations** — how to resolve the most common blockers.

---

## Agent Notes

- **Always run Step 0 first.** Silent landing on the login page is the most common cause
  of empty/incorrect results.
- Prefer the **CSV export** for the workload list; fall back to the scroll loop only if
  export is unavailable. Never rely on a single screen of visible rows.
- **Multi-container workloads** must be iterated container-by-container via the Container
  dropdown — the CSV row count per workload tells you how many to expect.
- Default to **JS-based tooltip extraction**; coordinate hover is a last-resort fallback.
- **Job workloads** with very short running hours (< 1 hour) will almost always show
  "insufficient data" — expected behavior, not a true configuration blocker.
- Tooltips may contain **multiple stacked reasons** — capture ALL bullet points.
- **"Limited by User and System"** means both user thresholds AND system safeguards apply.
- The ⟳ icon appears **only when automation is partially blocked** — if absent, either all
  recommendations were applied or none exist yet.
- Memory and CPU blockers are **independent** per container.

---

## Known Blocker Types

| Reason | Category | Recommended Resolution |
|--------|----------|----------------------|
| Decreasing memory limits is disabled by configuration | User Config | Review and update the automation policy to allow memory limit reductions |
| CPU limit removal is disabled by configuration, retaining the existing limit | User Config | Review and update the automation policy to allow CPU limit removal |
| Resource change is insignificant, keeping recommendation at current value | System Safeguard | Lower the insignificance threshold in automation settings if more aggressive right-sizing is desired |
| Automation restricted: MaxHeapSize is explicitly set — Automation will not reduce below the configured value | JVM / App Config | Align the `-Xmx` JVM flag with PerfectScale recommendations or adjust the minimum threshold |
| Increasing memory limits is disabled by configuration | User Config | **Urgently review** — this blocks automation from fixing OOM issues. Enable memory limit increases |
| Increasing memory requests is disabled by configuration | User Config | Enable memory request increases, especially critical for OOM-affected workloads |
| Recommendations appear once enough usage data is collected | Insufficient Data | Expected for short-lived Jobs. No action needed unless job duration increases significantly |

---

## Validation

Use a known test cluster to regression-check each future edit. Record expected values
here so changes can be verified:

| Check | Expected | Notes |
|-------|----------|-------|
| Auth guard | Stops cleanly when logged out | Test by running while signed out |
| Coverage | `captured N == expected M` | Compare against CSV row/workload counts |
| Container iteration | All containers per workload visited | Verify against CSV container counts |
| Tooltip extraction | Full multi-bullet text captured via JS | No reliance on coordinate hover |
| Ranked summary | Counts match per-workload detail totals | Sanity-check arithmetic |
