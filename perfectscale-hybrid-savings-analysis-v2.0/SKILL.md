---
name: perfectscale-hybrid-savings-analysis-v2.0
description: >
  The most efficient way to analyze PerfectScale Kubernetes cluster cost
  savings trend (before/after automation). Runs a `pscli` current-state
  snapshot first for quick context, then — because the public API has no
  historical cost time-series endpoint — uses Claude in Chrome to auto-export
  the Trends CSV directly from the PerfectScale UI and runs exact
  CSV-precision analysis on it. Trigger this skill whenever a user wants
  PerfectScale cost savings analysis and has Chrome connected, whether or not
  they have already exported a CSV. Triggers on: "analyze savings",
  "PerfectScale cost savings", "how much did we save", "run the savings
  analysis", "trends analysis", "cost eliminated", or any combination of
  PerfectScale + savings intent. Prefer this skill over
  ui-based-savings-analysis or perfectscale-cost-savings-trends when Claude
  in Chrome is available — it is faster, exact, and fully automated.
compatibility: >
  pscli (workloads summary/group-by) supplies a fast current-state snapshot
  with zero browser calls — use it for "what's the waste/cost right now"
  questions. It cannot answer "how did cost change over time," since the
  public API has no historical/trend endpoint (checked against the full
  command surface: clusters, namespaces, workloads, nodegroups, unevictable,
  automation — none return a time series). For that question, Chrome +
  Trends-CSV export remains the only path; this is a genuine gap, not a
  convenience shortcut. Falls back to manual CSV upload if Chrome unavailable.
---

# PerfectScale Hybrid Savings Analysis (v2.0 — pscli snapshot + CSV trend)

Two distinct questions get two distinct data sources:

- **"What's the current waste/cost?"** → `pscli workloads summary`/`group-by`
  — no browser needed. Use this if the user's question doesn't actually
  require a historical before/after comparison.
- **"How much did cost drop after automation / over time?"** → there's no
  CLI answer. Auto-export the Trends CSV via Chrome, then run exact
  CSV-precision analysis on it. **~3–5 tool calls total. No manual export. No
  bar-hovering. Exact figures.**

## Step 0 — pscli current-state snapshot (always run, no browser)

```bash
pscli -o json workloads summary -c <cluster>
```

Gives `total_cost`, `total_waste`, `total_potential_saving`,
`risky_workloads`, `waste_workloads` for *right now*. If the user's question
is purely "what's the current waste," this may be all they need — stop here
and skip Phase 1/2 entirely. If they want the before/after trend
specifically, keep this as context for the final report and continue below.

---

## Decision Tree — Choose Your Path (for the trend question)

```
Is Claude in Chrome connected and a browser tab available?
├── YES → Run Phase 1 (Chrome export) then Phase 2 (CSV analysis)
└── NO  → Check: did the user upload a CSV?
          ├── YES → Skip to Phase 2 directly
          └── NO  → Ask user to either connect Chrome OR export and upload the CSV manually
```

---

## Phase 1 — Auto-Export via Chrome (3–4 tool calls)

### Step 1.1 — Collect Inputs

Ask the user for:
1. **Cluster name** — lower-case (e.g. `nwl-staging-us-west-a`). If already visible in the
   current browser tab's Trends view, read it from the legend and confirm.
2. **Automation start date** — `YYYY-MM-DD`

Derive: `eval-start-date = automation_start_date − 1 day`

### Step 1.2 — Navigate to Trends

```python
# Using Claude in Chrome tools:
# 1. tabs_context_mcp(createIfEmpty=True) — get/create tab
# 2. navigate to https://app.perfectscale.io/trends
# 3. wait 2–3 seconds for load
# 4. screenshot to confirm page loaded
```

If already on the Trends page (user said "I'm already there" or screenshot confirms it),
skip navigation.

### Step 1.3 — Apply Cluster Filter (if needed)

If the graph shows multiple clusters or the wrong cluster:
- Click the **Cluster** filter dropdown (top-right filter bar)
- Type or select the requested cluster name
- Wait for graph to update, take screenshot to confirm

If only one cluster is shown and it matches, skip this step.

### Step 1.4 — Inject Blob Interceptor, Then Click Export

The PerfectScale export is **fully client-side** — it builds the CSV in the browser
and triggers a Blob download. There is no network request and no file lands in the
sandbox's filesystem. The reliable approach is to intercept the Blob data directly
in the page before clicking Export.

**Do this in two javascript_tool calls + one click:**

**Call A — inject interceptor (before clicking Export):**
```javascript
// Patch URL.createObjectURL to capture the CSV blob's text
const originalCreateObjectURL = URL.createObjectURL;
window._exportedCSV = null;

URL.createObjectURL = function(blob) {
  const url = originalCreateObjectURL(blob);
  blob.text().then(text => { window._exportedCSV = text; });
  return url;
};

'interceptor ready';
```

**Click Export** (top-right corner, ~coordinate [1432, 26]):
```python
# browser_batch:
# 1. left_click Export button
# 2. wait 2 seconds for blob to resolve
```

**Call B — retrieve the captured CSV text:**
```javascript
window._exportedCSV  // returns full CSV string
```

Pass the returned string directly into Phase 2 via `io.StringIO` — no file path needed.

> **Why this works:** PerfectScale calls `URL.createObjectURL(blob)` to generate the
> download link. Patching that function lets us read the blob's text content before
> the browser triggers the file save. The patch is scoped to the page session and
> clears on navigation.

---

## Phase 2 — CSV Analysis (exact precision)

This is identical to the `perfectscale-cost-savings-trends` skill. Reproduced here
for self-containment.

### Step 2.1 — Load and Filter

```python
import pandas as pd
from datetime import datetime, timedelta
from io import StringIO

# csv_data = string captured from window._exportedCSV in Phase 1
# If user uploaded a CSV manually, use: pd.read_csv("/mnt/user-data/uploads/daily_cost_by_clusters.csv")
df = pd.read_csv(StringIO(csv_data))
df["date"] = pd.to_datetime(df["start time"]).dt.date

cluster_name = "<user-provided>"
cluster_df = df[df["clusters"] == cluster_name].copy()

if cluster_df.empty:
    raise ValueError(f"Cluster '{cluster_name}' not found. Available: {list(df['clusters'].unique())}")

cluster_df = cluster_df.reset_index(drop=True)
```

### Step 2.2 — Drop Last Row

```python
rows_before = len(cluster_df)
cluster_df = cluster_df.iloc[:-1].reset_index(drop=True)
# ✅ Always confirm: "Bottom summary row removed — {n} rows remain."
```

### Step 2.3 — Compute Variables

```python
automation_start = datetime.strptime(automation_start_str, "%Y-%m-%d").date()
eval_start_date  = automation_start - timedelta(days=1)

# Baseline
baseline_row = cluster_df[cluster_df["date"] == eval_start_date]
if baseline_row.empty:
    raise ValueError(f"No data for eval-start-date {eval_start_date}.")
eval_start_cost = float(baseline_row.iloc[0]["cost"])

# Automation period
auto_df = cluster_df[cluster_df["date"] > eval_start_date].copy()
if auto_df.empty:
    raise ValueError("No data after eval-start-date. Check automation start date.")

min_idx          = auto_df["cost"].idxmin()
lowest_cost      = float(auto_df.loc[min_idx, "cost"])
lowest_cost_date = auto_df.loc[min_idx, "date"]

# Results
cost_eliminated       = eval_start_cost - lowest_cost
percentage_eliminated = (cost_eliminated / eval_start_cost) * 100
```

### Step 2.4 — Render Results Table

| LABELS | eval-start-date | lowest-cost-date | cost-eliminated | percentage-eliminated |
|--------|-----------------|------------------|-----------------|----------------------|
| `<cluster-name>` | `<YYYY-MM-DD>` | `<YYYY-MM-DD>` | `$X,XXX.XX` | `XX.XX%` |

Always include this note block below the table:
```
✅ Bottom summary row removed — [N] rows used for analysis.
🤖 Source: Auto-exported from PerfectScale Trends via Claude in Chrome
📅 Automation period: [eval-start-date + 1 day] → [last date in dataset]
📊 Baseline cost (eval-start-date): $X,XXX.XX
📉 Lowest automation-period cost: $X,XXX.XX on [lowest-cost-date]
```

---

## Full Reference Implementation

### Phase 1 — Chrome (3 tool calls)

**Tool call 1 — inject interceptor (javascript_tool):**
```javascript
const originalCreateObjectURL = URL.createObjectURL;
window._exportedCSV = null;
URL.createObjectURL = function(blob) {
  const url = originalCreateObjectURL(blob);
  blob.text().then(text => { window._exportedCSV = text; });
  return url;
};
'interceptor ready';
```

**Tool call 2 — click Export + wait (browser_batch):**
```python
# left_click [1432, 26], wait 2s
```

**Tool call 3 — retrieve CSV (javascript_tool):**
```javascript
window._exportedCSV
```

### Phase 2 — Python Analysis

```python
import pandas as pd
from datetime import datetime, timedelta
from io import StringIO

# --- Inputs ---
cluster_name         = "nwl-staging-us-west-a"    # from user or Chrome legend
automation_start_str = "2026-05-19"           # from user
csv_data             = "<string from window._exportedCSV>"

# --- Load ---
df = pd.read_csv(StringIO(csv_data))
df["date"] = pd.to_datetime(df["start time"]).dt.date

# --- Filter cluster ---
cluster_df = df[df["clusters"] == cluster_name].copy().reset_index(drop=True)
if cluster_df.empty:
    raise ValueError(f"'{cluster_name}' not found. Available: {list(df['clusters'].unique())}")

# --- Drop last row ---
cluster_df = cluster_df.iloc[:-1].reset_index(drop=True)
print(f"✅ Bottom summary row removed — {len(cluster_df)} rows remain for analysis.")

# --- Dates ---
automation_start = datetime.strptime(automation_start_str, "%Y-%m-%d").date()
eval_start_date  = automation_start - timedelta(days=1)

# --- Baseline ---
baseline_row    = cluster_df[cluster_df["date"] == eval_start_date]
eval_start_cost = float(baseline_row.iloc[0]["cost"])

# --- Automation period ---
auto_df          = cluster_df[cluster_df["date"] > eval_start_date].copy()
min_idx          = auto_df["cost"].idxmin()
lowest_cost      = float(auto_df.loc[min_idx, "cost"])
lowest_cost_date = auto_df.loc[min_idx, "date"]

# --- Results ---
cost_eliminated       = eval_start_cost - lowest_cost
percentage_eliminated = (cost_eliminated / eval_start_cost) * 100

print(f"\n| LABELS | eval-start-date | lowest-cost-date | cost-eliminated | percentage-eliminated |")
print(f"|--------|-----------------|------------------|-----------------|----------------------|")
print(f"| {cluster_name} | {eval_start_date} | {lowest_cost_date} | ${cost_eliminated:,.2f} | {percentage_eliminated:.2f}% |")
print(f"\n🤖 Source: Auto-exported from PerfectScale Trends via Claude in Chrome (Blob intercept)")
print(f"📅 Automation period: {eval_start_date + timedelta(days=1)} → {cluster_df['date'].max()}")
print(f"📊 Baseline cost: ${eval_start_cost:,.2f}")
print(f"📉 Lowest cost: ${lowest_cost:,.2f} on {lowest_cost_date}")
```

---

## Edge Cases

| Situation | Handling |
|-----------|----------|
| Chrome not connected | Fall back: ask user to upload CSV manually → use `/mnt/user-data/uploads/` path in Step 2.1 |
| Already on Trends page | Skip navigation, confirm cluster filter matches, inject interceptor, click Export |
| Wrong cluster shown | Apply Cluster filter before injecting interceptor and clicking Export |
| `window._exportedCSV` is null after click | Wait 1–2 more seconds and re-check; re-inject interceptor if page reloaded |
| Multiple clusters in CSV | Filter strictly to requested cluster in Step 2.1 |
| Cluster not in CSV | List available clusters and ask user to re-confirm |
| eval-start-date row missing | Warn user; use closest prior date as fallback |
| cost_eliminated negative | Display as-is; note cost increased post-automation |

---

## Efficiency Summary

| Step | Tool Calls |
|------|-----------|
| Navigate to Trends | 1 |
| Inject Blob interceptor | 1 |
| Click Export + wait | 1 |
| Retrieve CSV from `window._exportedCSV` | 1 |
| Analyze in Python | 1 |
| **Total** | **~5** |

vs. UI skill: ~15–20 hover calls, approximate values, ±$15 precision.
vs. CSV skill: same analysis, but user must manually export — this automates that step.

**No file I/O. No Downloads path hunting. No cross-OS path differences.**
The CSV lives in browser memory and is passed directly to Python via StringIO.
