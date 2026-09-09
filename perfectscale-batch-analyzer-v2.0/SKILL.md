---
name: perfectscale-batch-analyzer-v2.0
description: >
  Batch-analyze the top blocked workloads in a PerfectScale cluster, CLI-first
  for candidate selection via `pscli`. Ranks workloads by waste through the
  public API, then confirms automation-block status and pulls the debug
  reasoning payload for each candidate via the browser (no CLI equivalent
  exists for either), producing a consolidated coaching report with
  cross-workload patterns and a unified action plan. Use when someone says
  "analyze blocked workloads", "batch analysis", "top N workloads", "what's
  blocking savings in the cluster", "automation coaching report", or "what's
  blocked across the cluster".
compatibility: >
  Preferred: pscli for candidate ranking (waste, cost, cluster context) — no
  browser needed for that part. Browser (Backoffice impersonation via
  `backoffice-impersonation` + zoom-in debug modal) remains required to confirm
  "Limited by User/System" status and to extract the
  cpuReasoning/memReasoning/indicatorReasoning chains — both are internal-only
  fields absent from the full pscli schema.
---

# PerfectScale Batch Analyzer (v2.0, CLI-first candidate selection)

## Efficiency principles (read first)

Every step below is designed to minimize round-trips. Key decisions:
- **Rank candidates via `pscli`, not a browser table scrape.** v1 built the
  candidate list by filtering the PodFit table to Limited-by-User/System,
  sorting by Waste, and capturing rows before the virtualized table could lose
  them. v2.0 gets a deterministic, complete, pre-sorted candidate list from
  `pscli workloads list` with zero browser calls — see Step B-2 below. The
  browser is still needed per-candidate to confirm automation-block status
  (no public-API field for it) and to pull the reasoning chain, but it is no
  longer needed to *build the list* at all.
- Use `read_page(filter="all", max_chars=60000)` to extract debug modals — not
  `get_page_text` (which misses React portals) and not JS fiber extraction
  (which is slower and fragile).
- **Navigate and press `q` in separate browser_batch calls** — not the same
  batch. The page must fully render before `q` opens the debug modal.
- Navigate zoom-in pages with `?period=30d` for consistent 30-day waste figures.
  The tab URL may settle on `?period=2w` — this is normal, data is correct.
- Hold all N payloads in memory; produce the full report in a single response
  pass rather than printing per-workload as you go.
- Use direct URL navigation (`/pod-fit/<uuid>/zoom_in/...`) built from `pscli`
  fields (namespace, type, name) rather than clicking through the table.
- **Waste figures differ by source.** `pscli workloads list`'s `waste` field
  is a 30-day figure — matches the zoom-in sidebar, not the PodFit table's
  14-day column. Use the `pscli`/sidebar 30-day figures in all reports.
- **Always use browser mode (B-4) regardless of N.** Browser mode is reliable
  for N up to 10 and headless mode is blocked in this environment (see Step H).

---

## Step 0 — Elicit all inputs before touching the browser

Collect everything you need in one shot before opening any browser tab.
Ask these as **free-text questions in your response** (not via AskUserQuestion):

1. **Customer / tenant name** — as it appears in PerfectScale Backoffice (partial is fine)
2. **Cluster name** — exact name as shown in PerfectScale
3. **How many workloads to analyze?** — enter a number from 1 to 10

Do not open a browser until you have all three answers.

---

## Step B-1 — Bootstrap pscli (if a service token is available for this tenant)

If a `pscli` profile authenticates for this tenant (see the **perfectscale-cli**
skill), Step B-2 below needs no browser at all. If not, skip straight to
Step B-2's fallback and do the ranking from inside the browser session once
impersonation (below) is active — everything after ranking still needs the
browser regardless.

## Step B-1.5 — Impersonate the customer tenant (still required for B-3/B-4)

### Session reuse check (skip impersonation if still active)

Before triggering the full Backoffice impersonation flow (`backoffice-impersonation`),
check whether a prior session is still active. Impersonation sessions last ~60 minutes.

If you have a tabId from a previous run for this customer, attempt:
```
mcp__claude-in-chrome__navigate(url="https://app.perfectscale.io/pod-fit", tabId=<priorTabId>)
```
Take a screenshot. If the customer's PerfectScale dashboard loads, the session is
still active — skip the impersonation skill and note "Session reused" in the log.

### Full impersonation (when session is expired or no prior tabId)

Invoke the `perfectscale-gameday:backoffice-impersonation` skill (PerfectScale
Backoffice → Actions → Impersonate), passing the customer/tenant name. Wait for
it to confirm the session is active and return the new PerfectScale tab ID before
continuing.

---

## Step B-2 — Rank candidates by waste (pscli, no browser)

### B-2a. Pull a broad waste-ranked candidate list

```bash
pscli -o jsonl workloads list -c <cluster> -s waste -r desc -T 30 -V all
```

Pull more than N (30 is a reasonable net) because some top-waste workloads
won't turn out to be automation-blocked — you'll confirm that per-candidate in
B-3/B-4, not here. This single call replaces v1's entire B-2a–B-2e (navigate,
click cluster, open Automation filter, sort by Waste, capture the virtualized
table before it clears) — no risk of losing rows to virtualization, no filter
state to preserve across navigation.

Record per workload: `name`, `namespace`, `type`, `waste`, `potential_savings`,
`optimization_policy`, and container count (`len(containers)`) for the
multi-container tracking used later in B-4.

### B-2b. Cluster-level context

```bash
pscli -o json workloads summary -c <cluster>
```

Use `risky_workloads`/`waste_workloads`/`total_waste` for the B-5 executive
summary instead of reading top-of-page cards.

### B-2c. Plan-limit banner — no CLI equivalent, confirm in the browser

`pscli` has no field for PerfectScale's own plan-enforcement banner ("You've
reached the maximum number of automated clusters allowed in your plan"). Once
impersonation is active (B-1.5), navigate to `/pod-fit` for this cluster and
scan a `read_page` for that exact string:

- If present, note it as the **likely root cause of all "Limited by System"
  automation blocks** on this cluster — a PerfectScale plan restriction, not
  a customer-set configuration. Include this prominently in the B-5 executive
  summary and recommend a plan upgrade or explicit automated-cluster allowlist
  entry as the fix.

### Fallback — no pscli profile available

If no service token authenticates for this tenant, do B-2a/B-2b from inside
the browser instead: navigate to `/pod-fit`, open the Automation column filter
("Limited by User" + "Limited by System"), sort by the Waste column, and
capture the table via `read_page(filter="all", max_chars=60000)` immediately
after sorting (navigating away clears the filter). This is v1's original
mechanism — keep it only as the no-token fallback, not the default.

---

## Step B-3 — Construct zoom-in URLs

For each workload from Step B-2 (top N that turn out to be automation-blocked —
confirm status per workload as you go through B-4, since `pscli` has no
automation-status field), build:

```
https://app.perfectscale.io/pod-fit/<cluster-uuid>/zoom_in/<namespace>-<type_lowercase>-<name>/<name>?period=30d
```

Type mapping:
- Deployment → `deployment`
- StatefulSet → `statefulset`
- DaemonSet → `daemonset`
- Job → `job`
- CronJob → `cronjob`
- StrimziPodSet → `strimzipodset`

**Note:** URLs redirect `?period=30d` → `?period=2w` after navigation — normal,
data is still 30-day. Table waste = 14d; zoom-in sidebar waste = 30d.

---

## Step B-4 — Browser mode extraction (serial, N up to 10)

For each zoom-in URL, run **two separate browser_batch calls**:

**Batch 1 — navigate only:**
```
mcp__claude-in-chrome__browser_batch([
  {"tool": "navigate", "params": {"url": "<zoom_in_url>", "tabId": <tabId>}}
])
```

**Batch 2 — open modal and extract:**
```
mcp__claude-in-chrome__browser_batch([
  {"tool": "computer", "params": {"action": "key", "text": "q", "tabId": <tabId>}},
  {"tool": "read_page", "params": {"tabId": <tabId>, "filter": "all", "max_chars": 60000}}
])
```

> **Why separate batches?** Navigate is async — the DOM is not rendered when the
> call returns. Pressing `q` in the same batch fires on the old page. Separate
> calls ensure the target page is fully loaded before the modal opens.

> **Tab context URL lag is expected.** The navigate call's tab context often still
> shows the previous page's URL — this is a cosmetic reporting delay, not an
> error. Verify success by checking the dialog heading content, not the tab URL.

From each read_page result, for each workload:

**⚠️ Heading match verification (do this first):**
Confirm the `dialog[heading="<workload-name>"]` matches the expected workload.
If it doesn't match, wait ~2s and retry Batch 2 once. If the second attempt still
mismatches, mark this workload as "extraction failure — heading mismatch" and
continue to the next. Do not silently include the wrong workload's data.

**Multi-container tracking:**
Note how many containers the sidebar lists. The debug modal defaults to the
primary container. If the sidebar shows >1 container, record: "primary container
shown; N-1 additional containers not extracted." Flag this in the coaching card.

**Extract these fields:**
- Dialog `textbox` content: `indicatorReasoning`, `cpuReasoning`, `memReasoning`
  for the primary container
- Sidebar: 30d cost, waste, savings, risk mitigation, automation status,
  current resources, recommendations
- **If sidebar shows "Active"**: mark as "✅ Resolved during session"
- **"All Applied!" savings with blocked status is expected** — see note below

**⚠️ Automation-status gate (new in v2.0 — candidates arrive unfiltered):**
Since Step B-2's `pscli` ranking has no automation-status field, this is the
first point where block status is actually known. If the sidebar shows
neither "Limited by User" nor "Limited by System" (i.e. it's fully `Active`
or otherwise unblocked), this candidate doesn't belong in a *blocked*-workload
batch — drop it and pull the next-highest-waste candidate from the B-2a list
instead, continuing until N confirmed-blocked workloads are extracted. Note
the skip count and which workloads were skipped in the B-5 run stats so the
gap between "candidates pulled" and "candidates used" is visible.

Hold in memory; do not write to disk.

If Batch 2 returns minimal content (page not yet loaded), wait ~2s and retry.
If still missing, fall back to React fiber extraction (see fallback below).

> **"All Applied!" + blocked filter:** A workload can show "Savings Opportunity:
> All Applied!" while still appearing in the Limited by User/System filter. This
> happens when automation HAS applied all current recommendations but the CRD
> restriction remains in place as a future limiter. Treat these as automation
> wins — celebrate the right-sizing that already happened and explain the residual
> waste as an inherent request/limit headroom, not a failure.

> **Debug modal shows the primary container only.** For multi-container workloads,
> the textbox in the debug modal displays the first/primary container's reasoning
> by default. Note this limitation when reporting — if a key container is missing,
> indicate it in the coaching card and offer the single-workload analyzer for a
> deeper dive.

### Browser mode fallback — React fiber extraction

```javascript
const allEls = document.querySelectorAll('*');
function searchFiber(fiber, depth = 0, max = 60) {
  if (!fiber || depth > max) return null;
  for (const prop of [fiber.memoizedProps, fiber.memoizedState]) {
    if (prop && typeof prop === 'object') {
      try {
        const s = JSON.stringify(prop);
        if (s && (s.includes('indicatorReasoning') || s.includes('cpuReasoning'))) return prop;
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
if (!raw) { 'not found'; }
else {
  const wl = raw.workloadLatestRevision || raw;
  const containers = Object.fromEntries(
    Object.entries(wl.containers || {}).map(([name, c]) => [name, {
      name: c.name, indicators: c.indicators, restrictions: c.restrictions,
      cpuReasoning: c.cpuReasoning, memReasoning: c.memReasoning,
      indicatorReasoning: c.indicatorReasoning,
      cpuCoresRequest: c.cpuCoresRequest, cpuCoresLimits: c.cpuCoresLimits,
      memMiBRequest: c.memMiBRequest, memMiBLimits: c.memMiBLimits,
      recommendCpuCoresRequest: c.recommendCpuCoresRequest,
      recommendCpuCoresLimits: c.recommendCpuCoresLimits,
      recommendMemMiBRequest: c.recommendMemMiBRequest,
      recommendMemMiBLimits: c.recommendMemMiBLimits,
    }])
  );
  JSON.stringify({ name: wl.name, namespace: wl.namespace, containers });
}
```

---

## Step B-4.5 — Pre-report reconciliation (before writing B-5)

Before proceeding to B-5, run these checks on the full set of N extracted payloads.
Do not start writing the report until this step passes.

**Completeness check:**
- Count extracted payloads: should equal N (requested count)
- Each payload must have: workload name, sidebar 30d waste, at least one container
  with reasoning chain data
- List any gaps: "W3 (namespace/workload): heading mismatch, skipped" or
  "W5 (namespace/workload): read_page returned empty, used fiber fallback"

**Waste figure sanity check:**
For each workload, compare the `pscli` `waste` field from B-2a (30-day) to the
sidebar's 30-day waste. They should match closely (small rounding aside) since
both are 30-day figures — if they diverge materially, flag it as suspicious
(possible data gap or the workload changed between the two calls). Note any
anomalies in the coaching card. (If B-2's no-token fallback was used instead,
compare the table's 14-day waste to the sidebar's 30-day figure — the 30-day
figure should be larger or similar; a dramatically lower 30-day figure is the
suspicious case there.)

**Integrity spot-checks (top 2 workloads by waste):**

Check 1 — Reasoning chain terminal matches sidebar:
The last entry in `cpuReasoning` sets the final CPU request recommendation.
Compare it to the sidebar's "Recommended CPU Request" value. Flag any mismatch.

Check 2 — Indicator/recommendation direction:
- If OOM severity ≥ 2: `recommendMemMiBLimits` should be ≥ `memMiBLimits`
- If throttling level 3 in `cpuReasoning`: `recommendCpuCoresLimits` should be 0

**Arithmetic pre-verification (for figures you plan to cite):**
```bash
# Verify total waste sum
python3 -c "wastes=[1234, 567, 890]; print(f'Total: \${sum(wastes):,}')"

# Verify OOM rate for any workload with active OOM
python3 -c "events=228; days=30; print(f'{events/days:.0f} OOM/day')"

# Verify resource change % for the largest recommended change
python3 -c "cur=1024; rec=512; print(f'{(rec-cur)/cur*100:.0f}% ({cur} → {rec} MiB)')"
```

Use bash-verified figures in the report. If a number doesn't add up, note it
rather than silently using the wrong figure.

---

## Step B-5 — Produce the consolidated coaching report inline

Analyze all N payloads together and write the full report in a single response.

### Section 1: Executive Summary

3–5 sentences covering:
- N workloads analyzed, cluster name, customer
- Total waste across selected workloads (30-day, from zoom-in sidebars) —
  use the bash-verified sum from B-4.5
- Total blocked risk mitigation (sum of all "+$X.XX" values from zoom-in sidebars)
- Most common automation blocker type (System vs User vs both)
- Whether the plan limit banner was detected (if yes: call it out as root cause)
- Any workloads with active OOM events — flag as critical
- If any workloads were already resolved (All Applied!), note as automation wins
- Any extraction gaps from B-4.5 (workloads skipped or incomplete)

### Section 2: Priority Matrix

Use **30-day waste figures** sourced from the zoom-in sidebar. Label accordingly.

**OOM escalation rule:** If any workload has `OOM=3` (severity 3) in its
`indicatorReasoning`, pin it to the **top of the matrix** with a ⚠️ CRITICAL
flag, regardless of its waste rank. Active OOM crashes take priority over
cost optimization.

| Rank | Workload | Namespace | Waste (30d) | Risk Mitigation Blocked | Top Indicator | Automation Status |
|------|----------|-----------|-------------|-------------------------|---------------|-------------------|
| 1    | ...      | ...       | $X,XXX      | +$XXX                   | OOM (sev 3) ⚠️ | Limited by System |

Workloads with "All Applied!" and no pending recommendations get a ✅ row at
the bottom labeled "Automation win — residual waste only."

Workloads with extraction failures get a ⚠️ row labeled "Extraction incomplete —
verify manually."

### Section 3: Per-Workload Coaching Cards

For each workload (priority order), one condensed card:

**Header:** name, type, namespace, cost, waste (30d), automation status, risk level

**Confidence badge:** Assign one per card based on B-4.5 checks:

| Badge | When to use |
|-------|-------------|
| 🟢 **High** | Heading match confirmed, reasoning chain terminal = sidebar, indicator directions consistent, arithmetic verified |
| 🟡 **Medium** | Fiber fallback used, one minor integrity flag, or waste figure discrepancy within ~10% |
| 🔴 **Low** | Heading mismatch resolved by retry, reasoning chain contradicts sidebar, OOM with decrease recommendation, or arithmetic error |

**Resource diff table:** CPU Request / CPU Limit / Mem Request / Mem Limit
— columns: Current | Recommended | Change

**Indicators:** badge list with severity

**Special flags to call out:**
- `"injected sidecar"` label on a container → resources not in the app manifest;
  set via service mesh annotation or operator CRD
- `OOM=3` + active restart count → CRITICAL; include kubectl remediation command
- `bypass: [RA: true, Maturity: true]` → all recommendations locked and applied;
  this is an automation win, not a block
- `Limited by User AND System` → dual blocker; both must be resolved independently

**Top reasoning chain insight:** 2–3 bullets from cpuReasoning/memReasoning
(plain English — skip routine minimum-floor steps unless they're the key finding)

**Automation blockers:** each active restriction with its effect

**Top 3 coaching talking points:** customer-ready language

**Recommended action:** the single most impactful change for this workload

For workloads marked "All Applied!" (automation win): write a 2-sentence note
on what automation changed and the magnitude of the right-sizing. No full
coaching card needed — frame it as evidence the system is working.

For workloads with extraction failures: 1-sentence note with the gap and a
recommendation to use the single-workload debug analyzer for a manual deep dive.

### Section 4: Cross-Workload Patterns

- **Plan limit banner:** if detected, call out as the single root cause for all
  System-level blocks — one subscription change unblocks the cluster
- **CRD restriction patterns:** which types appear in multiple workloads?
- **"All Applied!" workloads:** how many are automation wins vs true blocks?
- **Indicator clustering:** most prevalent indicators and which namespaces
- **Automation status split:** Limited by User / System / both (user AND system)
- **Non-adjustable limit floor:** if 95 MiB floor appears in multiple workloads,
  note the cluster-level LimitRange/admission controller policy as a shared constraint
- **Workload type patterns:** over-provisioning patterns tend to cluster by application type

### Section 5: Unified Recommended Actions

Prioritized, deduplicated action list. Order by: urgency (active OOM) first,
then impact × breadth.

> **Action:** [what to do]
> **Affects:** [N workloads — list names]
> **Unlocks:** [total $ risk mitigation or OOM resolution]
> **How:** [specific kubectl/CRD change or PerfectScale config step]

**Post-report spot-check:** After writing the full report, re-read the 4–5
highest-stakes figures: total waste, total risk mitigation, OOM event counts,
and the largest individual resource change. Confirm each is traceable to the
extracted data or the bash verification from B-4.5. If any cannot be traced,
add: "⚠️ Figure unverified — cross-check with the sidebar before quoting to
the customer."

---

## Step B-6 — Offer the batch report artifact (optional)

> "The consolidated report is above. Want me to open the interactive batch
> dashboard so you can explore each workload visually and share it with the
> team? I can also run a deeper single-workload analysis on any of these using
> the debug analyzer."

If yes, read `assets/batch-report.jsx` and render its full contents verbatim
as a React artifact.

---

## Efficiency tracking

```
**Run stats:**
- Workloads analyzed: N
- Candidate ranking mode: pscli (0 browser calls) / browser fallback (no token)
- Candidates pulled vs. used: [pulled 30, used N, skipped M as not automation-blocked]
- Mode: browser (serial) for confirmation + extraction
- Browser round-trips: plan-limit check: 1 | URL collection: 0 (built from pscli fields) | extraction: N×2 (plus any skipped-candidate re-checks)
- Extraction method: read_page ✓ / fiber fallback (note which per workload)
- Heading match: confirmed for N workloads / mismatches: [count, workload names]
- Extraction retries (page not loaded): [count]
- Extraction failures: [workload names or "none"]
- Multi-container workloads (partial data): [count and names or "none"]
- Session reused from prior run: yes/no
- Workloads resolved during session: [names or "none"]
- Plan limit banner detected: yes/no
- "All Applied!" workloads (automation wins): [count]
- B-4.5 integrity flags: [list or "none"]
- Arithmetic verified via bash: yes/no
- Efficiency observations: [...]
```

---

## Step H — Headless mode (BLOCKED in this environment)

> ⛔ **CRITICAL LIMITATION:** Headless mode via session transplant is **not
> usable** when Claude in Chrome is the browser automation tool. Claude in
> Chrome's security filters block extraction of JWT tokens and base64-encoded
> data from browser tabs. Both of these are required by PerfectScale's auth
> mechanism (localStorage / sessionStorage JWTs, including impersonation tokens).
> Any attempt to extract `localStorage`/`sessionStorage` items
> or base64 strings will return a "BLOCKED" error.
>
> **Use browser mode (B-4) for all N.** It is reliable up to at least N=10
> with ~2 round-trips per workload.
>
> **Alternative paths if headless becomes necessary:**
> - Have Playwright authenticate independently via PerfectScale / Backoffice login
>   (bypasses the need to transplant session state)
> - Use PerfectScale's Public API instead of UI extraction
>   (avoids browser automation entirely for data collection)

The instructions below are preserved for reference if the environment changes.

### H-1. Install Playwright (if not already available)

```bash
pip install playwright --break-system-packages -q
PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1 playwright install chromium 2>&1 | tail -3
```

Note: `--with-deps` requires sudo and will fail in the sandbox. Use the env
var above to skip host validation instead.

### H-2. Extract session state ⛔ BLOCKED

The session state extraction script below is **blocked by Claude in Chrome
security filters** — `localStorage` reads and base64 encoding are rejected.
Do not attempt this step; fall back to browser mode (B-4) immediately.

### H-3 / H-4 — (Skipped — blocked at H-2)

See `assets/headless_extractor.py` for the Playwright script that would run
if session state could be extracted.

---

## Reasoning chain interpretation guide

- **`wrt quantile`** — initial proposal based on observed usage percentile
- **`below minimum`** — a safety floor raised the value
- **`below half of the limit`** — request raised to stay ≥ 50% of limit
- **`bypass: [RA: true, Maturity: true]`** — value locked; mature + accepted;
  automation has applied this recommendation — it's a win, not a block
- **`bypass: [RA: false, ...]`** — automation off; recommendation not accepted
- **`non-adjustable limit`** — cluster-level floor prevents going lower
  (often 95 MiB; set via LimitRange or admission controller)
- **`throttling level: 1/2/3`** — CPU throttling; level 3 = remove CPU limit
- **`making aggressive increase`** — limit bumped proactively for safety
- **`bumping up ... due to OOM rate`** — OOM history driving a limit increase;
  if this appears, the workload has active OOM events — check severity
- **`proposed limit is lower than the latest limit`** — PS is being conservative;
  won't reduce a limit without sufficient signal; current limit preserved
- **`reverting small cpu/memory increment`** — a tiny increase was proposed then
  reverted because the delta is below the minimum change threshold
- **`the change is lower than 5m / 5Mi`** — delta too small to apply; current
  value is held to avoid noisy micro-revisions
- **`do not allow decrease after decrease`** — HPA stability rule
- **`revision is too young for decreases`** — new revision (< 1h); decrease gated
- **`request is less than 1/2 of the max cpu use`** — spike protection
- **`reduce resources because init observed`** — init containers triggered reduction
- **`bypassing UI recommendation due to missing indicators`** — no active signals
  to justify the change; current value is preserved unchanged

## Asset files

- `assets/batch-report.jsx` — multi-workload dashboard React component (B-6)
- `assets/headless_extractor.py` — Playwright parallel extraction script
  (preserved for reference; blocked in Claude in Chrome environment — see Step H)
