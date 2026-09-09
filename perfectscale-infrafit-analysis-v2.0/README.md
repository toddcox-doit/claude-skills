# perfectscale-infrafit-analysis — Skill Notes

## What this skill does

Drives Chrome through the PerfectScale UI to surface **unevictable pods** data for the
cluster with the highest Node Idle cost. It navigates Overview → InfraFit → node group
detail → Unevictable Pods tab, triggers the Summarize report, and groups the results by
root cause.

Assumes the user is already logged in (directly or via a Backoffice impersonation session from `backoffice-impersonation`).

---

## Example run: Northwind Logistics (illustrative)

### Navigation steps completed

| Step | Result |
|---|---|
| Overview loaded | northwind logistics — 6 clusters, Node Idle total $14,200 |
| Sorted by Node Idle | `nwl-prod-us-west-a` ranked #1 at $6.2K (30d) |
| Clicked cluster | Landed on PodFit; InfraFit link found and clicked |
| InfraFit loaded | 1 node group (`<none>`), Autoscaler: Unknown |
| Last Seen → Current | Confirmed via `lastSeen=true` in URL |
| Timeframe → 14 days | 14d option available; confirmed via `period=2w` in URL |
| Idle Cost sort | Only 1 row — no reordering needed |
| Node group clicked | Drilled into `<none>` detail page |
| Unevictable Pods tab | Opened successfully |

### Issue encountered: empty snapshots

All snapshots available for June 12 (08:00 and 06:00) returned:

```
Visible Nodes: 0 / Visible Pods: 0 / Unevictable Pods: 0
"No nodes data available"
```

**Root cause:** the node group is labelled `<none>` — PerfectScale flagged it as an
unrecognized node group ("Unrecognized node groups detected. Configure a Customization
profile to add the missing node group labels."). Because the node group has no labels the
agent cannot associate nodes to it, so snapshot data is absent.

The skill began iterating through older snapshots (June 11) but was interrupted before a
snapshot with data was found.

### Account context (from Overview)

| Metric | Value |
|---|---|
| Clusters | 6 |
| Current High Risks | 2,240 |
| Pod Waste | $13,050 / 30d |
| Cost / 30d | $31,900 |
| Potential Max Savings | 68% — $21,700/mo / $260,400/yr |
| Total Saved (all time) | $5,300 |
| Node Idle (account) | $14,200 / 30d |

---

## Known issues & proposed improvements

### 1. Limit snapshot retries (priority: high)

**Problem:** when the latest snapshot has no node data, the skill starts iterating through
older snapshots one by one with no upper bound. On a cluster with many snapshots this
can run indefinitely before the user interrupts.

**Improvement — add to Step 9 error handling:**

> If Unevictable Pods = 0 and Visible Nodes = 0, open the Snapshot selector and try up to
> **3 previous snapshots** (stepping back in 2-hour increments). For each attempt, select
> the snapshot, click Apply, and check the counters. If all 3 are empty, stop and report:
> - The snapshot timestamps tried
> - That no node data was found
> - Whether the "Unrecognized node groups" warning banner is visible (if so, mention it
>   as the likely cause and link to the Customization Profile action)
>
> Do not keep iterating beyond 3 attempts without user confirmation.

### 2. Handle unrecognized node groups

**Problem:** if the node group is named `<none>` and the "Unrecognized node groups
detected" banner is present, node data will be absent in all snapshots for that group.
Continuing to try snapshots wastes time.

**Improvement:** at Step 8 (before clicking the node group), check whether the
"Unrecognized node groups detected" warning banner is visible. If it is, proactively
warn the user that snapshot data may be empty and explain the fix (configure a
Customization Profile to label the node groups).

### 3. Tool name casing

The SKILL.md body references `mcp__claude-in-chrome__find` (camelCase) but the actual
registered tool name is `mcp__claude-in-chrome__find` (kebab-case lowercase). Update
all references in Steps 4 and 8.
