---
name: perfectscale-cost-savings-trends
description: >
  Analyze PerfectScale Kubernetes cluster cost savings from Trends CSV data exports.
  Use this skill whenever a user uploads a daily_cost_by_clusters CSV file and wants
  to evaluate cost savings from PerfectScale automation. Triggers when user mentions:
  "cost savings", "PerfectScale trends", "cluster cost analysis", "automation ROI",
  "cost eliminated", "daily cost by cluster", or uploads a CSV that contains cluster
  cost data. Also trigger when asked to compare pre- vs post-automation costs, measure
  the impact of PerfectScale recommendations, or produce a cost savings summary for
  a specific cluster. Always use this skill for PerfectScale cost analysis — even if
  the user just says "analyze the cluster costs" or "show me the savings."
compatibility: claude.ai, Claude Code, Cowork
---

# PerfectScale Cost Savings — Trends Analysis Skill

Analyze daily cluster cost data exported from PerfectScale's **Trends** tab to
quantify cost eliminated after automation was enabled.

---

## Step 1 — Collect Inputs

Prompt the user for two values (can be done in a single `ask_user_input_v0` call
if the tool is available, otherwise ask inline):

1. **Cluster name** — lower-case, as it appears in the `clusters` column of the CSV
   (e.g. `nwl-staging-us-central-k8s`)
2. **Automation start date** — `YYYY-MM-DD` format (the day PerfectScale automation
   was switched on)

Derive:
```
eval-start-date = automation_start_date − 1 day
```

---

## Step 2 — Load and Validate the CSV

Expected upload path: `/mnt/user-data/uploads/daily_cost_by_clusters.csv`

Required columns (exact names, case-sensitive):
- `start time`   — ISO-8601 timestamp, e.g. `2026-04-20T00:00:00Z`
- `end time`
- `clusters`     — cluster identifier string
- `cost`         — numeric daily cost in USD

```python
import pandas as pd
from datetime import datetime, timedelta

df = pd.read_csv("/mnt/user-data/uploads/daily_cost_by_clusters.csv")

# Parse dates
df["date"] = pd.to_datetime(df["start time"]).dt.date

# Filter to requested cluster
cluster_name = "<user-provided>"        # substitute actual value
cluster_df = df[df["clusters"] == cluster_name].copy()

if cluster_df.empty:
    raise ValueError(f"Cluster '{cluster_name}' not found. Available: {df['clusters'].unique()}")
```

---

## Step 3 — Drop the Last Row

The bottom row of PerfectScale Trends exports is a summary/aggregate artifact and
must be excluded from analysis.

```python
cluster_df = cluster_df.iloc[:-1].reset_index(drop=True)
# ✅ Confirm: "Bottom summary row removed — {n} rows remain for analysis."
```

Always include a confirmation note in your output that this step was performed.

---

## Step 4 — Compute the Variables

```python
from datetime import date

eval_start_date = datetime.strptime(automation_start_date, "%Y-%m-%d").date() - timedelta(days=1)

# Filter automation period: rows AFTER eval-start-date
auto_df = cluster_df[cluster_df["date"] > eval_start_date].copy()

if auto_df.empty:
    raise ValueError("No data found after eval-start-date. Check the automation start date.")

# Lowest cost in automation period
min_idx       = auto_df["cost"].idxmin()
lowest_cost   = auto_df.loc[min_idx, "cost"]
lowest_cost_date = auto_df.loc[min_idx, "date"]

# Cost on eval-start-date (baseline)
baseline_row = cluster_df[cluster_df["date"] == eval_start_date]
if baseline_row.empty:
    raise ValueError(f"No data row found for eval-start-date {eval_start_date}.")
eval_start_cost = baseline_row.iloc[0]["cost"]

# Cost eliminated = baseline cost − lowest automation cost
cost_eliminated = eval_start_cost - lowest_cost

# Percentage eliminated = standard savings percentage formula:
#   (cost_eliminated / eval_start_cost) * 100
percentage_eliminated = (cost_eliminated / eval_start_cost) * 100
```

> **Formula note:** `percentage_eliminated = (cost_eliminated / eval_start_cost) × 100`
> expresses savings as a percentage of the baseline cost — the standard, human-readable
> interpretation. For example, $284 saved against a $560 baseline = ~50.7% savings.

---

## Step 5 — Render the Results Table

Present results in this exact table structure:

| LABELS              | eval-start-date    | lowest-cost-date   | cost-eliminated | percentage-eliminated |
|---------------------|--------------------|--------------------|-----------------|----------------------|
| `<cluster-name>`    | `<YYYY-MM-DD>`     | `<YYYY-MM-DD>`     | `$X,XXX.XX`     | `X.XXXXX%`           |

Formatting rules:
- **cost-eliminated**: USD currency, 2 decimal places, comma thousands separator
  (`${:,.2f}`.format(cost_eliminated))
- **percentage-eliminated**: 5 decimal places with `%` suffix
  (`{:.5f}%`.format(percentage_eliminated * 100)) — or display raw float if very small
- **Dates**: `YYYY-MM-DD` (strip time component)
- **cluster-evaluated** = the cluster name string, exactly as filtered

Below the table, always include this note block:
```
✅ Bottom summary row removed — [N] data rows used for analysis.
📅 Automation period: [eval-start-date + 1 day] → [last date in dataset]
📊 Baseline cost (eval-start-date): $X,XXX.XX
📉 Lowest automation-period cost: $X,XXX.XX on [lowest-cost-date]
```

---

## Edge Cases

| Situation | Handling |
|-----------|----------|
| Cluster name not found | List available cluster names and ask user to re-enter |
| Multiple clusters in CSV | Filter strictly to the requested cluster before all analysis |
| No rows after eval-start-date | Inform user — automation start date may be too recent |
| cost_eliminated is negative | Display as-is (cost went up); note this in output |
| Tie for lowest cost | Use first occurrence (lowest index) |
| eval-start-date row missing | Warn user; use the closest prior date as fallback |

---

## Full Python Reference Implementation

```python
import pandas as pd
from datetime import datetime, timedelta

# --- Inputs ---
csv_path             = "/mnt/user-data/uploads/daily_cost_by_clusters.csv"
cluster_name         = "nwl-staging-us-central-k8s"   # from user
automation_start_str = "2026-05-11"                  # from user

# --- Load ---
df = pd.read_csv(csv_path)
df["date"] = pd.to_datetime(df["start time"]).dt.date

# --- Filter cluster ---
cluster_df = df[df["clusters"] == cluster_name].copy().reset_index(drop=True)
print(f"Cluster '{cluster_name}': {len(cluster_df)} rows before trim")

# --- Drop last row (summary artifact) ---
cluster_df = cluster_df.iloc[:-1].reset_index(drop=True)
print(f"✅ Bottom summary row removed — {len(cluster_df)} rows remain for analysis.")

# --- Derive eval-start-date ---
automation_start  = datetime.strptime(automation_start_str, "%Y-%m-%d").date()
eval_start_date   = automation_start - timedelta(days=1)

# --- Baseline cost ---
baseline_row      = cluster_df[cluster_df["date"] == eval_start_date]
eval_start_cost   = float(baseline_row.iloc[0]["cost"])

# --- Automation period ---
auto_df           = cluster_df[cluster_df["date"] > eval_start_date].copy()
min_idx           = auto_df["cost"].idxmin()
lowest_cost       = float(auto_df.loc[min_idx, "cost"])
lowest_cost_date  = auto_df.loc[min_idx, "date"]

# --- Calculations ---
cost_eliminated        = eval_start_cost - lowest_cost
percentage_eliminated  = (cost_eliminated / eval_start_cost) * 100

# --- Output ---
print(f"\n| LABELS | eval-start-date | lowest-cost-date | cost-eliminated | percentage-eliminated |")
print(f"|--------|-----------------|------------------|-----------------|----------------------|")
print(f"| {cluster_name} | {eval_start_date} | {lowest_cost_date} | ${cost_eliminated:,.2f} | {percentage_eliminated:.5f}% |")
```

---

## Notes for Skill Users

- The CSV must come from PerfectScale's **Trends → Export** feature; it will always
  have the four columns listed above.
- If the user provides an XLSX instead of CSV, use `pd.read_excel()` with the correct
  sheet name (usually `"daily_cost_by_clusters"`).
- This skill evaluates **one cluster at a time**. If the user wants multiple clusters,
  repeat Steps 2–5 for each.
