# Brief voice and templates

Customer-shareable PerfectScale executive brief. Technical buyer with purchase
authority. Not an internal readout.

## Language

- **POV**, never “science project” or “science experiment” (including
  “not a science project”).
- “Ready to automate” not “NotEnabled”.
- “Not automatable” / “holdout” not `NotSupported` in body copy (table
  labels may say “PodFit — marked not automatable”).
- “Idle nodes” / “InfraFit — Node Idle on Overview” not `idleCost`.
- ExtraHeadroom / Balanced: customer-visible policy names are OK.

## Structure

```markdown
# <Customer> · PerfectScale technical executive brief

**Top <N> clusters by Potential Max Savings · Confidential · <date>**

Figures from the PerfectScale platform, 30 days ending <date>, unless noted.
Run-rate is illustrative, not a savings guarantee.

PREPARED FOR <CUSTOMER> TECHNICAL LEADERSHIP

## The decision: put automation back in the agreement
(or, for N≤3: “A buying decision, not a POV: turn automation back on”)

KPI strip: PodFit waste | InfraFit idle | Combined Potential Max Savings | Already realized

## Why this is on your desk now
Proof + entitlement/trial gate in buyer language.

## Two independent value pools — both visible on Overview
PodFit = applications. InfraFit = idle nodes.

Cluster table: rank, environment, Potential Max Savings, PodFit apps,
InfraFit idle, recommend. Totals row = share of estate.

If exclusions: footnote on **every** cluster row (n / $ or “none found”),
plus one table-level footnote that this was at the customer’s request.
Overview $ columns are unadjusted platform totals.

## What we found, where it lives, and how PerfectScale creates ROI
Four columns. Then the flywheel paragraph.

## The commercial ask
Numbered sequence. Money paragraph with run-rate caveats.
```

Page count: N≤3 → 1 page. N>3 → 2 pages (clusters on page 1, findings + ask
on page 2).

## PDF JSON payload (`build_brief.py`)

```json
{
  "customer": "Acme",
  "header": "Acme  ·  PerfectScale technical executive brief",
  "subheader": "Top 9 by Potential Max Savings  ·  Confidential  ·  26 August 2026",
  "footer": "Figures from the PerfectScale platform, 30 days ending 26 August 2026, unless noted. Run-rate is illustrative, not a savings guarantee.",
  "kicker": "PREPARED FOR ACME TECHNICAL LEADERSHIP",
  "title": "The decision: put automation back in the agreement",
  "lede": "Ranked by Overview <b>Potential Max Savings</b> ...",
  "kpis": [
    {"value": "$35.3k", "label": "Application waste / 30d<br/>PodFit"},
    {"value": "$52.7k", "label": "Idle infrastructure / 30d<br/>InfraFit"},
    {"value": "$48.2k", "label": "Combined opportunity / 30d<br/>Overview"},
    {"value": "$6,140", "label": "Already realized<br/>in 61 days"}
  ],
  "whyTitle": "Why this is on your desk now",
  "whyBody": "HTML allowed (<b>bold</b>).",
  "poolsTitle": "Two independent value pools — both visible on Overview",
  "poolsBody": "HTML allowed.",
  "clusterHeaders": ["#", "Your environment", "Potential Max Savings", "PodFit — applications", "InfraFit — idle nodes", "What we recommend"],
  "clusterRows": [["1", "Ireland production\nprod-eu-west-a", "$12.4k / 30d", "$3.1k ready\n* Kestrel excl. 31 wl / $1.4k", "$9.3k / 30d", "First production target"]],
  "clusterFootnote": "* Footnote (every cluster above): ...",
  "findingsTitle": "What we found, where it lives, and how PerfectScale creates ROI",
  "findingsHeaders": ["What we found — across all N", "Where you see it", "What PerfectScale does", "Why the ROI is positive"],
  "findingsRows": [["...", "PodFit — ...", "...", "..."]],
  "flywheel": "<b>The flywheel:</b> ...",
  "askTitle": "The commercial ask",
  "askHeadline": "Approve a PerfectScale agreement that includes automation — starting with the top 6, not a POV on all 11",
  "askBody": "1) ... <br/><br/><b>How to read the money:</b> ..."
}
```

Use `\n` in table cells for line breaks. Last cluster row may be the totals
row; the builder highlights it.

## Leak scan

After writing, search MD and PDF text for:
`science project`, `science experiment`, `impersonat`, `Frontegg`, `Backoffice`, `pscli`,
`idleCost`, `unevictable`, `Backoffice`, `sessionStorage`.
