# Skill: perfectscale-automation-blockers

Revision notes and operational findings for future skill iterations.

## Revision history

- **v2.0.0 (2026-06-15)** — Addressed four of the five known limitations recorded on
  2026-06-12: pagination/full-list capture, authentication guard, multi-container
  iteration, and defaulting to JS-based tooltip extraction. See **Known limitations**
  below for current status. The one remaining open item is self-hosted `base_url`
  support.
- **v1.0.0 (2026-06-12)** — Initial skill: navigation flow, automation filter, hover
  tooltips, ranked blocker summary.

---

## Navigation (confirmed flow)

The UI path that works reliably as of 2026-06-12:

**Overview** → `https://app.perfectscale.io/overview`

- Wait for the Clusters table to fully render.
- Click the cluster name text directly. Do not click the row action icons (settings,
  arrows, ellipsis) that appear on hover — those do not navigate into the cluster.

**PodFit / Workloads view**

- URL pattern: `https://app.perfectscale.io/pod-fit/<cluster-uid>?period=30d`
- Workload count and cluster-level stats render immediately. The table may show a brief
  "Preparing data…" state.

**Automation filter**

- Click the Automation column filter button (shows the selected count, e.g. "3", when active).
- The dropdown has a two-level hierarchy:
  - **Automated** (parent checkbox) → auto-selects all three sub-states: Active, Limited
    by User, Limited by System.
  - **Not Automated** (parent checkbox) → sub-states: Disabled by User, Not Enabled, Not Supported.
- Selecting "Automated" alone reduces the list to only automated workloads. Close with Escape.

**Zoom-in view (per-workload dialog)**

- Click the workload name text to open the zoom-in panel.
- URL pattern: `https://app.perfectscale.io/pod-fit/<cluster-uid>/zoom_in/<workload-id>/<container>?period=30d`
- The automation status ("Active", "Limited by User", "Limited by User and System") appears
  in the top-left panel next to the automation icon.
- Close with the ✕ button in the top-right corner of the dialog.

---

## Blocker tooltip extraction — browser automation approach

### What does NOT work reliably

- **CSS hover via computer tool:** Tooltips did not consistently appear from
  coordinate-based hover — the icon hit area is very small and the tooltip dismisses
  before a screenshot can capture it.
- **`aria-labelledby` on restriction divs (static):** The `aria-labelledby` attribute is
  only present on the MUI Tooltip popper while the tooltip is visible; it is not a static
  attribute on the trigger element.
- **React fiber title prop extraction:** The tooltip title is a React element (not a plain
  string); recursive text extraction from the virtual DOM returned empty strings due to
  component indirection.

### What DOES work

Trigger `mouseenter` programmatically, then read the MUI Tooltip popper:

```js
// 1. Dispatch mouseenter on a restriction div
const div = document.querySelector('.automation-restrictions');
div.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
div.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
// 2. After ~200ms, read the rendered tooltip
const tooltipEl = document.querySelector('[role="tooltip"]');
const text = tooltipEl ? tooltipEl.innerText.trim() : '';
```

Use a `setTimeout`-based chain (not `await`) since the `javascript_tool` does not support
top-level await in a for-loop. Add ~200 ms delay before reading and ~80 ms after
`mouseleave` before triggering the next tooltip.

**Alternative (faster): read `aria-labelledby` while tooltip is visible.** Once a tooltip
is shown, the restriction div gains `aria-labelledby` pointing to the tooltip content element:

```js
const elems = Array.from(document.querySelectorAll('[aria-labelledby]'));
elems.forEach(el => {
  const labelEl = document.getElementById(el.getAttribute('aria-labelledby'));
  if (labelEl) console.log(labelEl.innerText.trim());
});
```

This is reliable when called immediately after `mouseenter`. It is the fastest method for
bulk extraction.

> **Note for v2.0.0 alignment:** The confirmed selectors here are `.automation-restrictions`
> (trigger) and `[role="tooltip"]` (popper), plus the `aria-labelledby` fast path. The JS
> snippets currently in `SKILL.md` Steps 4b/4c use placeholder selectors
> (`[data-section="cpu"] .current-recommendations …`). Those should be replaced with the
> confirmed selectors above in a follow-up edit.

---

## CSV export — structure and columns

The PodFit view provides a CSV export (top-right Export button) that contains **one row
per container** (not per workload). A workload with multiple containers produces multiple
rows. The filename follows the pattern:

```
<tenant>_<cluster-name>_<date>_<timeframe>-filtered-by-<active-filters>.csv
```

### Column reference

| Column | Description |
|--------|-------------|
| Workload Name | Kubernetes workload name |
| Type | Resource type (Deployment, DaemonSet, StatefulSet, etc.) |
| Namespace | Kubernetes namespace |
| Automation Status | Current automation state: Active, UserLimitReached, MixedLimitReached |
| Running (h) | Total container running hours in the timeframe |
| Cost/h | Hourly cost per replica |
| Pod Cost | Total pod cost over the timeframe |
| Pod Waste | Amount of the pod cost attributable to unused resources |
| Cost Increase Needed | Additional cost required to reach the recommended (safer) resource allocation |
| Potential Pod Savings | Maximum savings achievable from right-sizing this container |
| Container | Container name within the workload |
| Optimization Policy | Active policy: maxSavings, balanced, extraHeadroom, maxHeadroom |
| Label 1 / Label 2 | Custom workload labels (if configured) |
| CPU Request | Current CPU request (cores) |
| Recommended CPU Request | PerfectScale recommended CPU request |
| CPU Limits | Current CPU limit (cores); 0 means no limit set |
| Recommended CPU Limits | Recommended CPU limit; 0 means PerfectScale recommends removing the limit |
| Memory Request | Current memory request (Mi) |
| Recommended Memory Request | Recommended memory request |
| Memory Limits | Current memory limit (Mi); 0 means no limit set |
| Recommended Memory Limits | Recommended memory limit |
| CPU Request (Detailed) | Current CPU request as reported in cluster manifest |
| CPU Limit (Detailed) | Current CPU limit as reported in cluster manifest |
| Memory Request (Detailed) | Current memory request as reported in cluster manifest |
| Memory Limit (Detailed) | Current memory limit as reported in cluster manifest |
| CPU Request (Total Impact) | Total CPU request delta across all replicas (cores) |
| CPU Limit (Total Impact) | Total CPU limit delta across all replicas |
| Memory Request (Total Impact) | Total memory request delta across all replicas (Mi) |
| Memory Limit (Total Impact) | Total memory limit delta across all replicas |
| CPU Request (Single Instance Impact) | Per-replica CPU request delta |
| CPU Limit (Single Instance Impact) | Per-replica CPU limit delta |
| Memory Request (Single Instance Impact) | Per-replica memory request delta |
| Memory Limit (Single Instance Impact) | Per-replica memory limit delta |
| CPU Request (% Change) | Percentage change for CPU request recommendation |
| CPU Limit (% Change) | Percentage change for CPU limit recommendation; NaN% means the limit would be removed (recommended = 0) |
| Memory Request (% Change) | Percentage change for memory request recommendation |
| Memory Limit (% Change) | Percentage change for memory limit recommendation |
| GPU Requests | GPU resource requests (if applicable) |
| GPU Utilization | GPU utilization (if applicable) |
| HPA Active | Whether HPA is active on the workload (yes / no) |
| HPA CPU Utilization (%) | HPA CPU trigger threshold |
| HPA Memory Utilization (%) | HPA memory trigger threshold |
| HPA Custom Metric | Whether the HPA uses a custom metric (yes / no) |

### Inferring blocker types from the CSV

The CSV does not include tooltip text. However, blocker categories can be reliably inferred
from column combinations:

| Pattern | Inferred Blocker |
|---------|------------------|
| CPU Limit (% Change) = NaN% AND Automation Status ≠ Active | CPU limit removal is disabled by configuration |
| Memory Limit (% Change) is a large negative value AND Automation Status ≠ Active | Decreasing memory limits is disabled by configuration |
| Memory Limit (% Change) is a positive value AND Automation Status ≠ Active | Increasing memory limits is disabled by configuration |
| Memory Request (% Change) is a positive value AND Automation Status ≠ Active | Increasing memory requests is disabled by configuration |
| CPU Request (% Change) is a positive value AND Automation Status ≠ Active | Increasing CPU requests is disabled by configuration |
| Automation Status = MixedLimitReached (all cases) | Resource change is insignificant (system safeguard) is also active alongside user policy block |
| Java workload namespace + large memory limit decrease blocked | Suspect MaxHeapSize / -Xmx interference |

### Critical flag detection

Flag a workload as CRITICAL when all of the following are true:

- Memory Request (% Change) is positive (memory increase needed)
- Automation Status = UserLimitReached or MixedLimitReached (increase is blocked)
- The workload has non-zero Cost Increase Needed or a workload name/context suggesting
  memory pressure

---

## Known limitations

### Resolved in v2.0.0 (2026-06-15)

- **Pagination handling** — RESOLVED. The skill now builds the complete workload list via
  the CSV export (preferred) or a deterministic scroll loop with a deduped set and a
  captured-vs-expected coverage check, rather than relying on visual scrolling.
- **Authentication guard** — RESOLVED. Step 0 now checks the URL / page title / login-form
  presence and stops with a clear message if the session is unauthenticated.
- **Multi-container workloads** — RESOLVED. Step 4a-i iterates over the Container dropdown
  in the zoom-in view, cross-referencing the per-workload container count from the CSV, and
  records findings keyed by `workload / container`.
- **Tooltip extraction fragility** — RESOLVED (approach). The skill now defaults to
  JS-based tooltip extraction with coordinate hover as a last-resort fallback. *Follow-up:*
  replace the placeholder selectors in `SKILL.md` with the confirmed `.automation-restrictions`
  / `[role="tooltip"]` / `aria-labelledby` selectors documented above.

### Open / future work

- **Self-hosted deployments** — NOT yet addressed. The skill still hardcodes
  `https://app.perfectscale.io`. Add a `base_url` input for self-hosted tenants and thread
  it through all navigation steps and the Step 0 auth check.
