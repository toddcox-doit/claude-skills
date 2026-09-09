# PerfectScale Batch Analyzer

Batch-analyze the top cost-blocked workloads in a PerfectScale cluster. Filters
PodFit to automation-blocked workloads, sorts by waste, and produces a
consolidated coaching report covering cross-workload patterns and a unified
action plan — in a single run.

---

## What it does

1. **Filters** the PodFit table to workloads with "Limited by User" or
   "Limited by System" automation status — the workloads where PerfectScale
   has recommendations but can't act on them.

2. **Sorts** by Waste descending to surface the highest-cost blockers first.

3. **Asks** how many workloads to analyze (1–10).

4. **Extracts** the debug payload for each selected workload in sequence —
   no manual copy-paste, no file downloads.

5. **Produces** a consolidated coaching report with:
   - Executive summary (total waste, root cause, automation wins)
   - Priority matrix table (OOM-escalated workloads pinned to top)
   - Per-workload coaching cards (condensed, customer-ready)
   - Cross-workload pattern analysis (which CRD rules affect the most workloads)
   - Unified recommended actions (deduplicated, ordered by urgency then $ impact)

6. **Offers** an interactive batch dashboard artifact for visual exploration
   and sharing.

---

## How to trigger

Invoke this skill when someone says:
- "analyze blocked workloads in [cluster]"
- "batch analysis for [customer]"
- "what's blocking savings across the cluster?"
- "top 5 workloads with automation blocked"
- "coaching report for [cluster]"
- "what does the CRD need to change to unblock savings?"

---

## What you need

- **Claude in Chrome** connected (for browser automation)
- **Backoffice access** via the `perfectscale-gameday:backoffice-impersonation` skill
  (PerfectScale Backoffice → tenants → Actions → Impersonate)
- Three inputs: customer/tenant name, cluster name, N (1–10)

---

## Understanding the consolidated report

### Priority Matrix
Workloads ranked by Waste (30d) descending. **Exception:** any workload with
active OOM events (OOM severity 3) is pinned to the top with a ⚠️ CRITICAL
flag, regardless of waste rank. Stopping a crash loop takes priority over
maximizing cost recovery.

### "All Applied!" workloads in the blocked filter
A workload can show "Savings Opportunity: All Applied!" while still appearing
in the Limited by User/System filter. This happens when automation HAS already
applied all current recommendations, but the CRD restriction remains in place
as a future limiter. These are **automation wins** — the right-sizing has been
executed. The residual waste shown is inherent request/limit headroom, not
unresolved savings. Frame them as evidence the system is working.

### Per-Workload Coaching Cards
Each card is a condensed version of the single-workload debug analyzer report.
For a deeper dive on any workload (full reasoning chain, revision history),
use the `perfectscale-debug-analyzer-v2.0` skill on that workload individually.

### Cross-Workload Patterns
This is the unique value of batch analysis. A single plan upgrade might
unblock all "Limited by System" workloads in the cluster simultaneously. A
single CRD rule change might unblock 7 of 10 workloads. The pattern section
surfaces these leverage points — the changes where one fix has disproportionate
impact.

### Unified Recommended Actions
Actions are ordered by: **active OOM resolution first**, then
**total $ unlocked × number of workloads affected**.

This means a plan limit fix that unblocks 20 workloads ranks above a targeted
fix for one workload — and an OOM fix ranks above both, even if the waste dollar
value is smaller.

---

## Automation status reference

| Status | Meaning |
|--------|---------|
| **Limited by User** | A user-set override is explicitly blocking automation for this workload |
| **Limited by System** | PerfectScale's system constraints are preventing action — common causes: plan limit (max automated clusters), CRD policy, insufficient data maturity |
| **Limited by User AND System** | Both a user override AND a system constraint are active; both must be resolved independently |
| **Active** | Automation is running — changes applied automatically |
| **Disabled** | Automation turned off at workload or cluster level |

---

## Common CRD blockers explained

| Restriction | Effect | Typical fix |
|-------------|--------|-------------|
| **Plan limit** ("max automated clusters") | ALL workloads in the cluster show "Limited by System" | Upgrade plan or add cluster to automation quota in PerfectScale subscription settings |
| `IncreaseDisabled` on CPU/mem request | Prevents raising requests — leaves scheduler without accurate data | Remove or raise the threshold in `PerfectScaleConfig` |
| `KeepLimitEnabled` on CPU | Forces a CPU limit to stay set — blocks limit removal even at throttle level 3 | Update CRD to allow limit removal, or raise the limit manually |
| `DecreaseDisabled` on mem limit | Prevents reducing an oversized memory limit | Update CRD or apply manually |
| `IncreaseDisabled` on mem limit | Prevents raising a memory limit — blocks OOM mitigations | Update CRD — **most urgent if OOM indicators are active** |
| Non-adjustable limit floor | Cluster-level minimum (often 95 MiB) cannot be undercut regardless of recommendations | Investigate LimitRange or admission controller policy |

> **Diagnosing "Limited by System":** Check the PodFit page banner for
> "You've reached the maximum number of automated clusters allowed in your plan."
> If visible, the plan limit is the root cause — not a per-workload CRD rule.

---

## Injected sidecar containers

When the debug modal labels a container as `"injected sidecar"`, the resources
for that container are **not set in the application's Kubernetes manifest**.
They are injected at runtime by a service mesh (Istio/Envoy) or operator.
PerfectScale tracks these containers and can recommend right-sizing, but the
recommendation must be applied via the injection mechanism (e.g., Istio
resource annotations), not via the workload's own spec. The CRD restriction
often prevents automation from reaching injected containers.

---

## Efficiency design

The skill is designed to minimize browser round-trips:
- Filter and sort happen once up front, not once per workload
- Zoom-in URLs are constructed from table data — no click-navigate-back loop
- All N payloads are extracted silently before any report text is written
- The debug panel is opened with a single keypress (`q`) per workload
- Each workload requires exactly 2 browser_batch calls (navigate, then q+read)

**Headless mode** (Playwright parallel extraction) is architecturally faster
for large N but is currently blocked by Claude in Chrome security filters —
JWT and base64 data cannot be extracted from browser tabs. Browser serial mode
is used for all runs and performs reliably up to N=10.

Typical run times (browser serial mode):
- N=3: ~2–3 minutes
- N=5: ~4–6 minutes
- N=10: ~8–12 minutes

After each run, the skill appends a **Run stats** block noting what worked,
what was slow, and suggestions for the next run.

---

## File structure

```
perfectscale-batch-analyzer/
├── SKILL.md            — Execution logic and step-by-step instructions
├── README.md           — This file
└── assets/
    ├── batch-report.jsx       — React component for the interactive batch dashboard
    └── headless_extractor.py  — Playwright parallel extraction (blocked; reference only)
```
