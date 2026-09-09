---
name: perfectscale-debug-analyzer-v2.0
description: >
  Builds a Claude-powered interactive coaching tool for analyzing PerfectScale
  workload zoom-in debug JSON payloads. Runs a `pscli` pre-check for quick
  current/recommended/indicator context, but the core reasoning-chain payload
  (cpuReasoning, memReasoning, indicatorReasoning) is internal-only and still
  requires the browser debug modal — confirmed absent from the public API.
  Use this skill whenever someone wants to analyze a PerfectScale debug
  payload, understand why PerfectScale made a specific recommendation, coach
  a customer on workload optimization findings, or create an internal tool
  for the technical team to interpret PerfectScale reasoning chains. Also
  trigger when users say things like "analyze this workload", "explain this
  PerfectScale recommendation", "build the debug analyzer", "create a
  coaching tool for PerfectScale", or upload/paste a JSON file that contains
  PerfectScale recommendation fields like "recommendations", "revisions",
  "mergedRevision", "indicatorReasoning", or "cpuReasoning".
compatibility: >
  pscli covers the quick pre-check (Path B, step B-0) — current/recommended
  resources, usage percentiles, indicators. It does not expose reasoning
  chains; Path B's core extraction (B-1 onward) still requires the browser.
---

# PerfectScale Debug Analyzer (v2.0 — pscli pre-check, browser for reasoning chains)

## Step 0 — Elicit mode before doing anything else

**Before rendering an artifact or fetching data, use `AskUserQuestion` to find out
how the user wants to provide the payload.** Don't skip this even if context clues
suggest a preference — both paths end in the same place, but they start very
differently.

Ask a single question with two options:

```
Question: "How do you want to load the workload debug payload?"
Options:
  - "I have it ready" — I'll paste or upload the JSON now
  - "Pull from tenant" — Fetch it live from a customer's PerfectScale account
```

Then follow the matching execution path below.

---

## Path A — Payload already available

If the user selects "I have it ready":

1. Read `assets/analyzer.jsx` and output its full contents as a React artifact.
   Do not summarize or abbreviate — the full file, verbatim.
2. Tell the user: "The analyzer is ready — paste the debug JSON or drop the file
   into the tool."

That's it for Path A. Nothing else is needed before the artifact renders.

---

## Path B — Fetch payload from tenant

If the user selects "Pull from tenant", ask these three questions in your response
text (not via `AskUserQuestion` — that tool requires multiple-choice options and
is not suited for free-text inputs):

1. **Customer / tenant name** — as it appears in PerfectScale Backoffice (partial is fine)
2. **Cluster name** — exact name as shown in PerfectScale
3. **Workload** — namespace and name, e.g. `payments/api-gateway`

Once you have all three, execute the following sequence:

### B-0. Quick pscli pre-check (optional, no browser)

If a `pscli` profile authenticates for this tenant (see the
**perfectscale-cli** skill), pull a fast baseline before paying for a full
impersonation + browser round-trip:

```bash
pscli -o json workloads show -c <cluster> -m <name> -n <namespace>
```

This gives current vs. recommended CPU/memory, usage percentiles
(`p90`/`p95`/`p100`), `indicators`, `optimization_policy`, and
`resilience_level` — useful context to have on hand, and enough on its own if
the user only wanted the numbers rather than the *why*. It does **not**
include `cpuReasoning`/`memReasoning`/`indicatorReasoning` — the API has no
such field (confirmed against the full `pscli` schema). If the user wants the
reasoning chain (the actual point of this skill), continue to B-1 regardless
of whether B-0 succeeded.

### B-1. Impersonate the customer tenant

Invoke the `perfectscale-gameday:backoffice-impersonation` skill, passing the
customer/tenant name. That skill handles PerfectScale Backoffice impersonation
(tenants search → Actions → Impersonate) and opens a new browser tab with the
customer's PerfectScale app. Wait for it to confirm the session is active before
continuing.

### B-2. Navigate to the workload zoom-in page and open the debug panel

After impersonation, take a screenshot to confirm the PerfectScale dashboard is
visible in the new tab.

1. Use `mcp__claude-in-chrome__tabs_context_mcp` with `createIfEmpty: true` to
   confirm the new tab is active.
2. Use `find` to locate the cluster card/row labeled with the cluster name, then
   click it. Wait for PodFit to load (URL contains `/pod-fit/`).
3. Use `find` to locate the workload row matching the namespace/workload name, then
   click it. The URL will change to:
   `/pod-fit/<cluster-uuid>/zoom_in/<workload-id>/<container>?period=30d`
4. **Open the debug panel:** once on the zoom-in page, press `q` on the keyboard:
   ```
   mcp__claude-in-chrome__computer(action="key", text="q", tabId=<tabId>)
   ```
   Wait ~1 second. A debug JSON modal should appear overlaying the page.
   Take a screenshot to confirm it's open.

### B-3. Extract the debug payload (in memory — no file download needed)

Try the approaches in order, stopping as soon as one succeeds.

**Option 1 — `read_page` with `filter: "all"` on the open debug modal (fastest):**

With the debug modal open from step B-2:

```
mcp__claude-in-chrome__read_page(tabId=<tabId>, filter="all", max_chars=60000)
```

> **Why `read_page` and not `get_page_text`?** PerfectScale renders the debug
> modal as a React portal — it mounts outside the `<main>` element. `get_page_text`
> only reads `<main>` content and will miss the modal entirely.
> `read_page(filter: "all")` traverses the full accessibility tree including portals.

Scan the returned accessibility tree for a `dialog` element containing a
`textbox` with children that include `"indicatorReasoning"` or `"cpuReasoning"`.

**⚠️ Heading match verification:** Before accepting the payload, confirm the
dialog heading matches the target workload name. A mismatch means the page
rendered the wrong workload — wait 2s and retry Batch 2. Do not proceed with
mismatched data.

**Multi-container tracking:** Note how many containers the sidebar lists. The
debug modal defaults to the primary container. If the sidebar lists >1 container,
record which one is shown and mark others as "not extracted" in your analysis.

If the dialog and reasoning chain content are present, hold them in memory and
proceed to B-3.5. Do not write to disk.

**Option 2 — React fiber extraction with field-filtered object (fallback):**

If `read_page` doesn't surface the reasoning chains (e.g., the modal isn't open
or the content is empty), extract only the coaching-relevant fields from the
React component tree. This approach avoids the security filter that
blocks raw JSON strings containing URLs or query parameters:

```javascript
// Run via mcp__claude-in-chrome__javascript_tool
const allEls = document.querySelectorAll('*');

function searchFiber(fiber, depth = 0, max = 60) {
  if (!fiber || depth > max) return null;
  for (const prop of [fiber.memoizedProps, fiber.memoizedState]) {
    if (prop && typeof prop === 'object') {
      try {
        const s = JSON.stringify(prop);
        if (s && (s.includes('indicatorReasoning') || s.includes('cpuReasoning'))) {
          return prop;
        }
      } catch(e) {}
    }
  }
  return searchFiber(fiber.child, depth+1, max) || searchFiber(fiber.sibling, depth+1, max);
}

let raw = null;
for (const el of allEls) {
  const k = Object.keys(el).find(k => k.startsWith('__reactFiber'));
  if (k) { raw = searchFiber(el[k]); if (raw) break; }
}

// Return only coaching-relevant fields — avoids triggering the security filter
// on raw URLs/query-strings that appear in the full payload
if (!raw) { 'not found'; }
else {
  const wl = raw.workloadLatestRevision || raw;
  const containers = Object.fromEntries(
    Object.entries(wl.containers || {}).map(([name, c]) => [name, {
      name: c.name,
      indicators: c.indicators,
      restrictions: c.restrictions,
      resourceAutomation: c.resourceAutomation,
      cpuReasoning: c.cpuReasoning,
      memReasoning: c.memReasoning,
      indicatorReasoning: c.indicatorReasoning,
      cpuCoresRequest: c.cpuCoresRequest,
      cpuCoresLimits: c.cpuCoresLimits,
      memMiBRequest: c.memMiBRequest,
      memMiBLimits: c.memMiBLimits,
      recommendCpuCoresRequest: c.recommendCpuCoresRequest,
      recommendCpuCoresLimits: c.recommendCpuCoresLimits,
      recommendMemMiBRequest: c.recommendMemMiBRequest,
      recommendMemMiBLimits: c.recommendMemMiBLimits,
      cpuCoresAvgUse: c.cpuCoresAvgUse,
      cpuCores99p: c.cpuCores99p,
      cpuCoresMaxUse: c.cpuCoresMaxUse,
      cpuThrottleAvgPct: c.cpuThrottleAvgPct,
      memMiBAvgUse: c.memMiBAvgUse,
      memMiB99p: c.memMiB99p,
      memMiBMaxUse: c.memMiBMaxUse,
    }])
  );
  JSON.stringify({
    name: wl.name,
    workloadType: wl.workloadType,
    namespace: wl.namespace,
    optimizationPolicy: wl.optimizationPolicy,
    workloadAutomationStatusV2: wl.workloadAutomationStatusV2,
    maturityThreshold: wl.maturityThreshold,
    maxIndicator: wl.maxIndicator,
    firstSeen: wl.firstSeen,
    lastSeen: wl.lastSeen,
    totalCost: wl.totalCost,
    estimatedTimeframeCost: wl.estimatedTimeframeCost,
    savingPct: wl.savingPct,
    containers
  });
}
```

**Option 3 — Ask the user to export manually:**

If both options above fail, take a screenshot of what's visible, describe the
state, and ask the user to copy the debug JSON from the panel and paste it into
the conversation.

### B-3.5 — Payload integrity check (before writing any report)

Before proceeding to B-4, run these consistency checks on the extracted data.
Flag any failure as **🔴 Low confidence** on the affected container and note
the discrepancy in the coaching card.

**Check 1 — Reasoning chain terminal value matches sidebar recommendation:**
The last entry in `cpuReasoning` sets the final recommended CPU request. Compare
it to `recommendCpuCoresRequest` and to the sidebar's displayed "Recommended"
value. Likewise for `memReasoning` → `recommendMemMiBRequest`. If they disagree,
note: "Reasoning chain terminal and recommendations object disagree — treat
sidebar values as authoritative."

**Check 2 — Indicator/recommendation direction consistency:**
- If `OOM` indicator is severity ≥ 2: `recommendMemMiBLimits` should be ≥ current
  `memMiBLimits` (an increase or hold, never a decrease). A lower recommendation
  with active OOM signals a data gap or a secondary safety rule — explain it.
- If `throttling level: 3` appears in `cpuReasoning`: `recommendCpuCoresLimits`
  should be 0 or null (limit removal). If it shows a non-zero limit, a CRD
  restriction is preventing the removal — flag it explicitly.
- If `IncreaseDisabled` restriction is present for a resource and the recommendation
  is to increase that resource, note: "CRD blocks this increase — recommendation
  is advisory only; automation cannot apply it."

**Check 3 — Arithmetic spot-check:**
For any OOM rate or % resource change you plan to cite, verify in bash:

```bash
# OOM rate example: events ÷ days
python3 -c "events=228; days=30; print(f'{events/days:.0f} OOM/day')"

# Resource change % example
python3 -c "cur=512; rec=256; print(f'{(rec-cur)/cur*100:.0f}% change ({cur} → {rec} MiB)')"
```

Use the computed values in the coaching card. If the numbers don't add up,
note the discrepancy instead of silently using the wrong figure.

---

### B-4. Produce the coaching report inline

Once the payload has passed the B-3.5 integrity check, produce the full
structured coaching report in your response. Do not write the payload to disk
and do not render the artifact asking the user to paste.

**Assign a confidence badge to each container card:**

| Badge | When to use |
|-------|-------------|
| 🟢 **High** | Heading matched, reasoning chain terminal = sidebar recommendation, indicator directions consistent, arithmetic verified |
| 🟡 **Medium** | Minor waste-figure discrepancy (within ~10%), fiber fallback used, or one B-3.5 flag |
| 🔴 **Low** | Heading mismatch resolved by retry, reasoning chain contradicts sidebar, OOM with decrease recommendation, or arithmetic error found |

Report sections:

1. **Workload header** — name, namespace, type, automation status, cost, savings %,
   maturity, optimization policy
2. **Per-container analysis** (one card per container, with confidence badge):
   - Indicators and severity (OOM, throttling, over/under-provisioning, unset resources)
   - Current vs. recommended resources (CPU request/limit, mem request/limit)
   - Actual usage telemetry (avg, p99, max, throttle %)
   - Plain-English walkthrough of the reasoning chain
   - Customer-ready coaching talking points
   - Cautions before applying
   - Any B-3.5 integrity flags for this container
3. **Automation blockers** — list every active restriction with its effect
4. **Recommended actions** — ordered, concrete next steps
5. **Risk flags** — anything warranting caution

**Post-report spot-check:** After writing the report, re-read the 3–4 highest-stakes
figures (largest resource change, OOM event count, waste dollar amount, savings %).
Confirm each is directly traceable to the extracted payload. If any cannot be
traced, add: "⚠️ Figure unverified — cross-check with the sidebar before quoting
to the customer."

After delivering the inline report, offer the interactive artifact for re-analysis
or sharing:

> "The coaching report is above. Want me to also open the interactive analyzer
> artifact so you can explore the data visually or share it with the team?"

If they say yes, read `assets/analyzer.jsx` and render it as a React artifact.

---

## What this skill produces

An interactive, Claude-powered coaching tool that accepts a PerfectScale workload
zoom-in debug JSON payload (via paste or file upload) and produces a structured
coaching report. Designed for internal technical teams coaching customers on
workload optimization.

The tool analyzes:
- Per-container resource recommendations and the full reasoning chain behind them
- Actual usage telemetry (percentiles, averages, max, throttle data)
- Indicator signals (OOM, throttling, over/under-provisioning, unset resources)
- Automation status and what's blocking it
- Revision history and data maturity
- Workload-level cost, savings potential, and optimization policy

## Output sections in the generated report

Each analysis produces:

1. **Workload header** — name, namespace, type, customer (inferred), automation
   status, current/projected cost, savings %, maturity badge, coaching narrative

2. **Container cards** (one per container) — confidence badge, indicators and waste
   badges, headline finding, current vs. recommended resources (color-coded diff),
   actual usage percentiles, plain-English explanation of the reasoning chain,
   customer-ready coaching talking points, cautions, integrity flags (if any)

3. **Recommended actions** — ordered next steps for the coaching call

4. **Risk flags & automation blockers** — side-by-side

5. **Revision history** — data window, stability, and per-revision notes

6. **Workload-level findings** — HPA state, policy source, node placement, etc.

## Reasoning chain interpretation guide

The debug payload reasoning chains follow a pipeline pattern. Key concepts:

- **`wrt quantile`** — initial proposal based on observed usage percentile
- **`below minimum`** — a safety floor raised the value
- **`below half of the limit`** — request was raised to stay above 50% of limit
  (prevents extreme request/limit ratios that confuse the scheduler)
- **`bypass: [RA: true/false, Maturity: true/false]`** — RA = Recommendation
  Acceptance (user has accepted/automated), Maturity = data window is mature.
  When both are true, the value is locked and downstream steps won't change it.
  RA: false means automation is off and the recommendation hasn't been accepted.
- **`non-adjustable limit`** — a cluster-level floor prevents going lower
- **`proposed limit is lower than the latest limit`** — conservative: won't
  reduce a limit without sufficient signal
- **`bypassing UI recommendation due to missing indicators`** — insufficient
  signal to change the limit; current value is held
- **`throttling level: 1/2/3`** — active CPU throttling detected; level 3 means
  PerfectScale will remove the CPU limit entirely (set to 0) to allow bursting
- **`making aggressive increase`** — limit was bumped up proactively for safety
- **`bumping up ... due to OOM rate`** — OOM history driving a limit increase;
  workload has active OOM events — check severity and restart count
- **`reverting small cpu/memory increment`** — a tiny increase was proposed then
  reverted because the delta is below the minimum change threshold
- **`the change is lower than 5m / 5Mi`** — delta too small to apply; current
  value is held to avoid noisy micro-revisions

## Truncation handling

The API call uses `max_tokens: 8000`. If the response is still truncated (can
happen with very large payloads or many containers), the tool attempts to recover
by trimming to the last safe JSON close point and patching open structures. A
yellow warning banner appears if partial recovery was used, prompting a re-run.

## Asset file

The full React component is in `assets/analyzer.jsx`. Read that file and output
its contents verbatim as a React artifact when this skill is triggered for Path A
or when the user requests the artifact in Path B.
