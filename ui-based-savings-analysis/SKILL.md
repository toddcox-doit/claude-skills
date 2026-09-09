---
name: ui-based-savings-analysis
description: >
  Analyze PerfectScale Kubernetes cluster cost savings from a screenshot of the
  PerfectScale Trends UI graph. Use this skill whenever a user shares an image,
  screenshot, or photo of the PerfectScale Trends graph and wants to evaluate cost
  savings from automation. Triggers when user mentions: "screenshot", "graph",
  "image", "UI", "trends graph", "PerfectScale chart", "share my screen", or
  uploads any image that appears to show a cost-over-time chart from PerfectScale.
  Also trigger when the user says things like "here's my trends view", "look at
  this graph", or "analyze this chart" alongside a PerfectScale-looking image.
  Always use this skill for image-based PerfectScale cost analysis — even if the
  user just drops a screenshot and says "what are the savings?"
compatibility: claude.ai, Claude Code, Cowork
---

# PerfectScale Cost Savings — UI Graph / Screenshot Analysis Skill

Analyze a screenshot of PerfectScale's **Trends** tab cost graph to quantify cost
eliminated after automation was enabled. This is the image-input counterpart to
`perfectscale-cost-savings-trends` (which reads CSV exports).

---

## Step 1 — Confirm the Image

The user should have uploaded a screenshot of the PerfectScale Trends graph.
Confirm the image is visible in context. If no image is present, ask the user to
share one before proceeding.

Look for these visual elements to confirm it is a PerfectScale Trends graph:
- A time-series bar or line chart with daily cost data on the Y-axis (USD)
- Dates along the X-axis
- A cluster name or selector visible (usually top-left or in a dropdown)
- Optionally: a vertical marker or color change indicating automation start

If the image is ambiguous or not a PerfectScale Trends graph, describe what you
see and ask the user to confirm before proceeding.

---

## Step 2 — Collect Inputs

Prompt the user for:

1. **Cluster name** — lower-case identifier (e.g. `nwl-staging-us-central-k8s`).
   If visible in the screenshot, read it directly and confirm with the user rather
   than asking.
2. **Automation start date** — `YYYY-MM-DD` format (the day PerfectScale automation
   was switched on). If a visual divider or annotation is visible in the graph,
   read the date from the image and confirm rather than asking.

Derive:
```
eval-start-date = automation_start_date − 1 day
```

---

## Step 3 — Extract Data from the Image

Use Claude's vision capability to read the graph carefully. Extract:

### What to read
- **All visible daily cost data points** — date + approximate USD value for each bar/point
- **The cost on eval-start-date** (baseline) — read the bar height for that date
- **The lowest cost bar in the automation period** (after eval-start-date) — its
  date and value

### Reading tips
- Bar chart: estimate each bar's top against the Y-axis gridlines; interpolate
  between labeled gridlines if needed
- Line chart: read the value at each date marker on the X-axis
- If exact values are shown in a tooltip legend or data label in the image, prefer
  those over visual estimation
- If the Y-axis uses K (thousands) notation, convert: 0.5K = $500
- Note the Y-axis scale and units before reading any values
- If the graph is zoomed or only shows a partial date range, note this explicitly

### Uncertainty handling
- If a bar value is hard to read precisely, give a range (e.g. "$270–$290") and
  flag it in the output
- If fewer than 7 automation-period data points are visible, warn the user that
  the sample may be too small for reliable analysis
- Never fabricate a value — if a date is illegible, skip it and note the gap

---

## Step 4 — Compute the Variables

Once values are extracted from the image:

```
eval_start_cost   = cost on eval-start-date (baseline, day before automation)
lowest_cost       = minimum daily cost in the automation period
lowest_cost_date  = date of that minimum
cost_eliminated   = eval_start_cost − lowest_cost
percentage_eliminated = (cost_eliminated / eval_start_cost) × 100
```

---

## Step 5 — Render the Results Table

Present results in this exact table structure:

| LABELS           | eval-start-date | lowest-cost-date | cost-eliminated | percentage-eliminated |
|------------------|-----------------|------------------|-----------------|-----------------------|
| `<cluster-name>` | `<YYYY-MM-DD>`  | `<YYYY-MM-DD>`   | `$X,XXX.XX`     | `XX.XX%`              |

Formatting rules:
- **cost-eliminated**: USD currency, 2 decimal places, comma thousands separator
- **percentage-eliminated**: 2 decimal places with `%` suffix
- **Dates**: `YYYY-MM-DD`

Below the table, always include this note block:
```
📸 Source: PerfectScale Trends UI screenshot (visual estimation)
📅 Automation period: [eval-start-date + 1 day] → [last visible date in graph]
📊 Baseline cost (eval-start-date): $X,XXX.XX
📉 Lowest automation-period cost: $X,XXX.XX on [lowest-cost-date]
⚠️  Precision note: [any values estimated from chart; exact figures available via CSV export]
```

Always include the precision note — screenshot-derived values are inherently
approximate. Recommend the user run the CSV-based skill
(`perfectscale-cost-savings-trends`) for exact figures if precision is critical.

---

## Step 6 — Optional: Describe the Trend

After the table, offer a brief narrative (2–4 sentences) describing what the graph
shows visually — e.g. whether costs dropped sharply after automation, gradually
trended down, showed volatility, or had any notable spikes. This gives the user
context for the numbers.

---

## Edge Cases

| Situation | Handling |
|-----------|----------|
| No image uploaded | Ask user to share a screenshot of the PerfectScale Trends graph |
| Image is blurry or low resolution | Flag affected values as estimates with wider ranges |
| Graph shows multiple clusters overlaid | Ask user which cluster/color to analyze |
| Y-axis labels cut off | Note the ambiguity; ask user to share a higher-quality screenshot |
| Automation start not visually marked | Ask user to confirm the date; note it was user-provided |
| Cost went up after automation | Display as-is; note this in the precision block |
| Graph uses logarithmic Y-axis | Adjust reading strategy accordingly; note in output |
| Only aggregated weekly/monthly view visible | Note that daily granularity is unavailable; analyze visible data points |

---

## Relationship to CSV Skill

This skill and `perfectscale-cost-savings-trends` produce the same output table
and use identical formulas. The difference is the data source:

| | `ui-based-savings-analysis` | `perfectscale-cost-savings-trends` |
|---|---|---|
| **Input** | Screenshot / image of Trends graph | `daily_cost_by_clusters.csv` export |
| **Precision** | Visual estimation (±5–15%) | Exact to 4 decimal places |
| **Speed** | Instant — no export needed | Requires CSV download from PerfectScale |
| **Best for** | Quick sanity checks, demos, async sharing | Formal reporting, audits |

If the user has access to the CSV export, recommend it for authoritative figures.
If they only have a screenshot, this skill delivers a reliable approximation.
