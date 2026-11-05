---
name: perfectscale-optimization-plan
license: Apache-2.0
description: >
  Produce a sequenced, ROI-ranked PerfectScale optimization plan for a customer
  tenant — a working-session worksheet (Markdown + self-contained HTML, plus
  XLSX/CSV and JSON) that a solutions engineer screen-shares with the customer's
  platform/K8s and FinOps team to decide what to do first, second, and third.
  Converts findings into discrete Actions, scores each as (monthly savings x
  confidence) / effort, assigns owner + prerequisite + verification method,
  groups into Phase 1 (0-30d quick wins) / Phase 2 (30-90d structural) /
  Phase 3 (90d+ govern), and promotes unblocker actions whose own dollar value
  understates what they release. Use for: "optimization plan", "optimization
  strategy", "optimization roadmap", "detailed optimization steps",
  "optimization session", "where do we start", "what's the highest ROI",
  "prioritized action plan", "how do we capture these savings", "sequence the
  work", "quick wins", "90-day plan", "phased rollout plan", "customer working
  session", "drive the conversation with the customer", "regenerate the plan",
  "re-run the analysis for this tenant". Distinct from
  perfectscale-tenant-savings-report-v2.0 (finds and reports the opportunity,
  organized by cluster — this skill consumes that and organizes by what you DO,
  in order). Also covers the executive one-pager: `scripts/exec_summary.js`
  renders a one-page buying-decision summary (.docx) straight from the plan
  JSON, so use this skill for "executive brief", "exec summary", "one-pager" or
  "buying-decision brief" too. The separate perfectscale-executive-1pager skill
  was retired on 2026-10-21 and now lives in `~/.claude/skills-archive`.
compatibility: >
  CLI-first via `pscli`. A public-API service token is the only hard
  prerequisite — the default path produces a complete, scored, sequenced plan
  with no browser at all. InfraFit actions score at full confidence from
  `nodegroups` (autoscaler_type, recommendations) and `unevictable`
  (reason_code, remediation); PodFit actions score at medium confidence from
  `workloads -V policy/capacity/risk`, `group-by optimization-policy`, `muted`,
  and `automation audit-logs --execution`. Browser (backoffice-impersonation +
  the zoom-in debug modal) is OPTIONAL enrichment that upgrades PodFit actions
  to full confidence: `workloadAutomationStatusV2` and the Limited-by-User/System
  blocker text have no public-API equivalent, and neither does the
  cpuReasoning/memReasoning narrative. Offer it; never require it.
  Pure Python 3.10+, standard library only (openpyxl optional, for XLSX).
---

# PerfectScale optimization plan (library: psplan v2)

Every other PerfectScale skill organizes its output by **where a finding lives** —
cluster, then PodFit/InfraFit, then node group. That is the right shape for a
diagnosis and the wrong shape for a conversation. A customer's platform team does
not experience their estate as a cluster list; they experience it as a queue of
work they have to justify, schedule, and staff.

This skill produces the other shape: **an ordered queue of Actions**, each one a
thing a named person can do in a sprint, scored by return per unit of effort,
grouped into phases, with the dependency structure made explicit.

Unlike earlier versions, the scoring model and the plan's prose are **executed,
not followed by hand**: one command turns pscli data into a phased action plan
(MD, HTML with a live adoption control, XLSX/CSV, JSON), per-cluster findings, a
change log against the previous plan, and an independent verification pass. A
second command renders the one-page executive summary from that same plan JSON,
as .docx or .html.

**Read-only guarantee:** this skill never applies a change, never clicks "Apply
recommendation," never modifies an automation or policy setting. The generated
plan states this.

## Where the code lives

`scripts/` in this skill folder: the library `psplan/`, the entry points
(`new_tenant.py`, `rank_clusters.py`, `run_plan.py`, `exec_summary.js`),
`browser/` for the two things the public API cannot see, and `tests/`.

**This folder is the source of truth.** Each tenant gets its own working folder
holding `config/`, `data/`, `reports/` and a *copy* of the library under
`skill/`. Never run from the skill folder, never let tenant data back into it,
and never copy one tenant's folder to start another — `new_tenant.py init`
copies only the library.

## Prerequisites

| Need | Check | If missing |
|---|---|---|
| `pscli`, authenticated for this tenant | `pscli auth status` | Not vendored here — see README.md "Installing `pscli`"; install from [doitintl/perfectscale-cli](https://github.com/doitintl/perfectscale-cli), then `pscli auth login --client-id … --client-secret …` (never echo the secret) |
| Python 3.10+ | `python3 -V` | — |
| Node.js 22+ (only for the .docx one-pager or browser enrichment) | `node -v` | `scripts/browser/cdp-chrome.js` needs the native `WebSocket` global, stable only from Node 22 |
| `docx` npm package (for the .docx one-pager) | `node -e "require('docx')"` from `scripts/` | `cd scripts && npm install`; without it the run still completes and warns |
| `openpyxl` (for XLSX) | `python3 -c "import openpyxl"` | optional: `python3 -m pip install -r scripts/requirements-optional.txt`; CSV is always written |

The optional PodFit eligibility browser pass (`references/eligibility-enrichment.md`)
is macOS-only: `cdp-chrome.js` reads a hard-coded macOS Chrome profile path. On
Linux/Windows, skip it — the plan still runs at medium confidence for PodFit
actions.

`expires_at` in `pscli auth status` can read as past while calls still work.
Probe with a real call before concluding the token is dead.

## Workflow

### 0. Set up the tenant folder

```bash
python3 <skill>/scripts/new_tenant.py init --customer "Acme" --profile acme --dir ~/work/Acme_analysis
cd ~/work/Acme_analysis
export PERFECTSCALE_PROFILE=acme
pscli auth status          # confirm it is the right tenant before anything else
```

### 1. Rank the clusters, and agree the scope

Skip only if the tenant has few enough clusters to analyse all of them.

```bash
python3 skill/perfectscale-optimization-plan/scripts/rank_clusters.py --customer "Acme" --top 10
```

Ranks every cluster by PodFit potential plus node idle, writes
`data/rank/ranking.json` and a markdown table under `reports/`, and reports
inventory problems it finds: registrations with an empty UID, names shared by
two live clusters, clusters returning no workloads. **Stop here and agree the
list** — the ranking is a sizing signal, not a commitment, and the customer may
have reasons to include or drop a cluster that the numbers do not show.

### 2. Write the config

```bash
python3 skill/perfectscale-optimization-plan/scripts/new_tenant.py config --dir . --top 10
# or, once the list has been edited:
python3 .../new_tenant.py config --dir . --clusters prod-eu,prod-us,stage-eu
```

Names and UIDs come from the ranking rather than being retyped, and clusters are
written in descending savings order, which every output then follows.

Then set by hand, because guessing them is worse than leaving them null:

| Key | Why |
|---|---|
| `assumptions.gitops` | **Ask.** `null` is stated in the plan as an open assumption; a wrong guess is reported as fact. |
| `stateful_patterns` | Add whatever search or streaming engine *this* tenant runs. The defaults are not exhaustive — see `references/methodology.md`. |
| `held_out_from_automation` | Name or namespace regexes to keep out of the automation ask. Anchor them: `^prod$` does not catch `preprod`. |
| `managed_namespaces` | Platform add-ons to hold out of the ask. |
| `foreign_tenants` | Every other customer name this folder has held, so the leak scan can catch them. |
| `adoption_factor` | Default `0.7`. Scales savings in the adoption view; never scales spend. |
| `status` | e.g. `"Draft for review"` adds a banner and a feedback section. |
| `thresholds.plain_job_waste_min_usd` | Default `50.0`. Grouping is recommended only when ungrouped jobs are numerous **and** carry this much waste; lower it to surface grouping for high-count, low-cost pods. |
| `thresholds.replicated_group_min` | Default `25`. Namespaces one controller name must repeat across before a custom grouping rule is proposed. Lower it on a tenant with few but large per-customer namespaces. |
| `thresholds.replicated_group_pot_min_usd` | Default `100.0`. …and the savings that name carries must be worth a rule. Both must clear, so a name repeated everywhere for pennies produces no action. |

### 3. Collect

```bash
python3 skill/perfectscale-optimization-plan/scripts/run_plan.py --config config/acme.tenant.json --collect
```

Resumable, globally rate-limited with backoff on 429, skips files still fresh,
falls back to top unevictable groups if the cluster-wide pull fails. Re-run
until it reports complete.

### 4. Analyse, render, verify

```bash
python3 .../run_plan.py --config config/acme.tenant.json          # add --serial to debug
```

Outputs land in `out_dir` as `<customer>-<stem>-<date>-<tag>.<ext>`. Existing
outputs for a version tag are never replaced: bump `version_tag` **and**
`out_dir` for each new version, or pass `--overwrite` to re-render in place.

### 5. Re-run later, and diff

Set `previous_plan` to the earlier plan JSON, bump `version_tag`/`out_dir`, and
the change log is written automatically. Keep the previous version's folder
intact so the two can be compared.

### 6. Ship only on a clean verify

The verifier exits 1 on any failure and reports each one. Fix the cause, not the
output. Then read the executive summary and the plan yourself before sending —
the gates catch leaks and arithmetic, not a wrong story.

## What a run produces

Step 3 writes all of this. None of it needs a second command:

| Artifact | Formats |
|---|---|
| Optimization plan | `.md`, `.html`, `.json`, `.xlsx`, `.csv` |
| Per-cluster findings | `.md`, one per cluster |
| **Executive summary** (one page) | **`.docx` and `.html`** |
| Change log vs `previous_plan` | `.md`, when configured |

The executive summary is rendered by `scripts/exec_summary.js` from the plan
JSON — the same content model emits both formats, so they cannot drift from the
plan or from each other. Never hand-write either. `NODE_PATH` is not needed: the
`docx` module is resolved from the global npm roots, and the HTML needs no
module at all. If node or `docx` is missing the run still completes and prints a
warning rather than failing; install node to get the summary. Confirm the .docx
still fits one page after any copy change.

To re-render just the summary (after editing its copy, say):

```bash
node scripts/exec_summary.js <plan.json> <out.docx>
node scripts/exec_summary.js <plan.json> <out.html>
```

## Tables in the HTML

Standard in every rendered HTML table, and asserted by the verifier so they
cannot regress silently:

- **Drag any column edge to resize.** The width comes from the columns to the
  right, nearest first, each down to a 44px floor, so the table's overall width
  never changes and nothing reflows sideways.
- **A "Columns" button above each table** opens a checkbox list of columns, plus
  **Autofit selected** (size the shown columns to their content at the same
  total width) and **Reset to default**. Hiding the last visible column is
  refused; hidden columns remember their width.
- **Headers wrap** rather than spilling over the next column when narrowed.
- Widths and the chosen columns persist per table in `localStorage`; every
  access is wrapped, since storage throws in `data:` URLs.
- Sorting still works: a drag is suppressed from firing the header's sort.

## Rules the engine encodes

These are implemented in `psplan/`, not applied by judgment. The references
explain *why* each one is what it is — read them before changing a rule or
adding one.

- The observed window is normalized: an API "30d" cost is spend over the
  *connected* window. Clusters under `min_history_days` are scaled by
  30/window and flagged as early baselines; full windows are never scaled.
- Everything reconciles to the workload list. No group-by rollups.
- Three money bases are never summed: `achievable_now`, `blocked_pending` (idle
  upper bounds or PerfectScale estimates), `resiliency_investment` (spend, with
  a low/likely/literal range).
- Lanes: controller, job, managed, stateful, held_out. Auto-provisioned pools
  (e.g. GKE `nap-`) are reviewed, not claimed.
- One controller name repeated across many namespaces (a per-customer namespace
  model) becomes a `custom_grouping` action carrying **no dollars of its own** —
  that money is already in the automation waves — which the waves then depend
  on, so the ask is one policy decision rather than N.
- Effort XS/S/M/L with at most two escalators; ROI = savings x confidence /
  effort weight (XS 1, S 2, M 4, L 8), rounded half-up; unblockers are promoted
  one phase.
- Optional enrichment: `eligibility_file` (the PodFit browser pass, as JSON),
  `trends_csv` (seasonality), `cluster_settings` (facts the API cannot see).
- Voice and wording gates, enforced by the verifier: no unsupported causal
  claims (label them `inferred - cause not confirmed`), no filler words, no
  other tenant's name, no absolute filesystem path, no NaN dollars, and HTML
  with no external `src`/`href`.

## Extending

A new rule is one `r_*` method in `psplan/actions.py`, added to `Plan.build`,
plus a fixture case in `tests/fixtures.py` and a test. Take the action's
`action_type`, its floor effort tier, its default owner role and its default
risk from `references/action-catalog.md` — the vocabulary there is closed, and
an unlisted type is an unauditable effort score. The engine currently implements
a subset of that catalog; the rest is the map for what to add next.

Run the suite from `scripts/`:

```bash
python3 -W ignore -m unittest discover -s tests
```

## References

- `references/roi-scoring.md` — the scoring model: confidence multipliers,
  effort tiers, the four escalators and the +2 cap, CLI-only scoring discipline,
  dependency edges, the promotion rule, phase assignment, worked example.
- `references/action-catalog.md` — the closed `action_type` vocabulary, the
  action record schema, owner roles, the automation-status proxy for CLI-only
  runs, dependency edges, and the step-writing rules.
- `references/plan-voice.md` — audience, voice rules, the Markdown template and
  the table conventions the renderer implements.
- `references/methodology.md` — definitions and the gotchas that bite
  (rate limits, window normalization, wall minutes, idle as an upper bound).
- `references/tenant-config.md` — the config keys.
- `references/eligibility-enrichment.md` — the optional PodFit eligibility pass.
