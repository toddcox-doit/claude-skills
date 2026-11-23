---
name: perfectscale-optimization-plan
license: Apache-2.0
description: >
  Produce a sequenced, ROI-ranked PerfectScale optimization plan for a customer
  tenant: a working-session worksheet (Markdown, self-contained HTML, XLSX/CSV,
  JSON) an SE screen-shares with the customer's platform and FinOps teams to
  decide what to do first, second and third. Scores each action as (savings x
  confidence) / effort, assigns owner, prerequisite and verification, and phases
  it 0-30 / 30-90 / 90+ days. The same run renders the one-page executive
  summary and, per cluster, a POV automation kickoff page: top 50
  workloads ranked by cost and resiliency, filtered by controller type, that
  generates PerfectScale automation CRDs. Use for: "optimization plan",
  "roadmap", "where do we start", "highest ROI", "prioritized action plan",
  "quick wins", "90-day plan", "customer working session", "regenerate the
  plan", "executive summary", "one-pager", "POV kickoff", "POV scope",
  "automation CRDs", "generate automation configs". Takes what
  perfectscale-tenant-savings-report-v2.0 finds and orders it by what you do.
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
  Optional public API v2 credentials (scripts/set_api_credentials.sh) add HPA
  triggers and per-workload automation status, which pscli (v1) cannot see.
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
| `pscli`, authenticated for this tenant | `pscli auth status` | Not vendored here — see README.md "Installing `pscli`"; install from [doitintl/perfectscale-cli](https://github.com/doitintl/perfectscale-cli), then `python3 scripts/new_tenant.py auth --profile <name>`: signs pscli in and stores the same token for the v2 step, without putting the secret on a command line |
| Python 3.10+ | `python3 -V` | — |
| Node.js 22+ (only for the .docx one-pager or browser enrichment) | `node -v` | `scripts/browser/cdp-chrome.js` needs the native `WebSocket` global, stable only from Node 22 |
| `docx` npm package (for the .docx one-pager) | `node -e "require('docx')"` from `scripts/` | `cd scripts && npm install`; without it the run still completes and warns |
| `openpyxl` (for XLSX) | `python3 -c "import openpyxl"` | optional: `python3 -m pip install -r scripts/requirements-optional.txt`; CSV is always written |
| Public API v2 credentials (optional: HPA triggers, automation status) | `run_plan.py --config … --check` | `python3 scripts/new_tenant.py auth --profile <profile>`: one entry for pscli and the v2 step, verified before it is kept, secret never echoed. Without it the v2 step is skipped and HPA status reads Unknown |

The optional PodFit eligibility browser pass (`references/eligibility-enrichment.md`)
is macOS-only: `cdp-chrome.js` reads a hard-coded macOS Chrome profile path. On
Linux/Windows, skip it — the plan still runs at medium confidence for PodFit
actions.

`expires_at` in `pscli auth status` can read as past while calls still work.
Probe with a real call before concluding the token is dead.

## Workflow

### 0. Set up the tenant folder

```bash
python3 <skill>/scripts/new_tenant.py onboard --customer "Acme" --profile acme --dir ~/work/Acme_analysis
cd ~/work/Acme_analysis
```

`onboard` creates the folder, asks for the tenant's Read Only API token once
(verified against the API before it is kept, never echoed), signs `pscli` in with
it and stores it for the collector's API v2 step, then ranks every cluster (step 1).
`new_tenant.py auth --profile <name>` re-enters or rotates the token. The secret
reaches `pscli` through its own prompt on a pseudo-terminal (macOS/Linux) or its
`-k` flag (Windows) - never a shared command line. If you run the collector
later without a token, it asks once when a person is at the terminal and skips
quietly when not; it never blocks an unattended run.

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
| `assumptions.native_vpa` | **Ask.** Whether this tenant also runs upstream Kubernetes-native VPA alongside PerfectScale — `pscli` has no field for this and never will, so this is manual-only. `null` is an open assumption; a wrong guess is reported as fact. |
| `stateful_patterns` | Add whatever search or streaming engine *this* tenant runs. The defaults are not exhaustive — see `references/methodology.md`. |
| `held_out_from_automation` | Name or namespace regexes to keep out of the automation ask. Anchor them: `^prod$` does not catch `preprod`. |
| `managed_namespaces` | Platform add-ons to hold out of the ask. |
| `hpa_pinned_namespaces` | **Rarely needed.** Namespace regexes whose pod CPU/memory HPAs the owner confirms are pinned (min = max replicas): treated as having no HPA, so no HPA automation. By default those workloads are automated with HPA automation alongside. Never infer it from flat replica counts. |
| `foreign_tenants` | Every other customer name this folder has held, so the leak scan can catch them. |
| `adoption_factor` | Default `0.7`. Scales savings in the adoption view; never scales spend. |
| `status` | e.g. `"Draft for review"` adds a banner and a feedback section. |
| `thresholds.plain_job_waste_min_usd` | Default `50.0`. Grouping is recommended only when ungrouped jobs are numerous **and** carry this much waste; lower it to surface grouping for high-count, low-cost pods. |
| `thresholds.replicated_group_min` | Default `25`. Namespaces one controller name must repeat across before a custom grouping rule is proposed. Lower it on a tenant with few but large per-customer namespaces. |
| `thresholds.replicated_group_pot_min_usd` | Default `100.0`. …and the savings that name carries must be worth a rule. Both must clear, so a name repeated everywhere for pennies produces no action. |

### 3. Collect

```bash
python3 skill/perfectscale-optimization-plan/scripts/run_plan.py --config config/acme.tenant.json --check     # preflight
python3 skill/perfectscale-optimization-plan/scripts/run_plan.py --config config/acme.tenant.json --collect
```

`--check` changes nothing. It lists, as OK / WARN / BLOCK: pscli sign-in, API v2
credentials (with a live token test), each cluster's data age and whether HPA
data is collected, where automation status will come from, output-folder
conflicts, and the optional renderers. Run it before a customer call.

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

```bash
python3 .../run_plan.py --config config/acme.tenant.json --next-version
```

`--next-version` moves the config to the next version: `version_tag` and
`out_dir` (v5.3 to v5.4, never into a folder that already exists), with
`previous_plan` set to the plan it replaces, so the change log is written. The
config as it was is kept beside it as `config/<tenant>.tenant.<old tag>.json`. Add
`--collect` to pull fresh data first. Earlier versions stay intact for comparison.

### 5a. Open the kickoff pages (double-click), and HPA automation

**Double-click `Open kickoff pages.command`** at the top of the tenant folder
(`new_tenant.py init` writes it; `new_tenant.py launcher --dir <tenant folder>`
adds it to an older folder). Or, from any folder:

```bash
python3 ~/work/Acme_analysis/skill/perfectscale-optimization-plan/scripts/kickoff.py
```

Either opens the current version's kickoff pages in the browser and keeps running
until the Terminal window is closed or Ctrl+C. No `--config` is needed: a tenant
folder's copy of the library finds that folder's one `config/<name>.tenant.json`
by itself (`run_plan.py` does the same). Pass `--config` only if a folder holds
more than one config. `kickoff.py` serves only the reports folder, on 127.0.0.1,
behind a per-run token, and never touches the cluster.

**CRD history.** Opened through the launcher, every CRD set the kickoff page
generates is saved, append-only, under `crds/<cluster>/<date-time>-<kind>/` in the
tenant folder, with a manifest: plan version, selections, a fingerprint per file,
the Kubernetes objects it creates, and what changed against the previous set. An
identical re-generation isn't stored twice, and nothing is ever pruned. The page's
**CRD history** section shows the last three sets of each kind, each with:

- a zip download
- a diff against the previous set, or against what's on screen
- **Mark handed to the customer**
- **Roll back to this**, which lists the exact `kubectl delete` commands for
  objects added since: applying older files never removes them. It also points to
  `stopAllAutomation` / `cleanupAllAutomation` for undoing resource changes
  automation already made.

The customer's own Git repository stays the source of truth for what's applied;
this is the SE-side record. A page opened straight from disk explains that
history needs the launcher.

**HPA automation on the kickoff page.** Workloads whose HPA scales on pod CPU or
memory are automated with PerfectScale's HPA automation alongside, not held back:
PerfectScale sizes the request from the trigger, and HPA automation keeps
minReplicas and the triggers aligned. The page's HPA automation section works
without the customer running anything:

- **What HPA automation will change** is worked out from the data with
  PerfectScale's documented rules (triggers, container-level moves, minReplicas
  hints). Exact minReplicas changes come from PodFit's HPA tab, because the
  public API has no HPA min/max.
- **Tick the prerequisites after asking the customer:** agent v1.0.71+ and
  exporter v1.1.28+ (both required), plus Kubernetes 1.33+ (optional).
- **Restrictions** start at PerfectScale's defaults. Generated configs carry the
  `hpa` block once both required prerequisites are ticked.
- **The read-only commands** that would give the exact HPA and PDB settings are
  folded away at the bottom, in case the customer agrees later.

There is no fixed re-run date: a POV runs 30 days, so a date months out is the
wrong promise. The plan and the summary offer a second look on demand, or once
every cluster has 30 days of observation (the date the least-observed cluster
gets there, from the prepared date), when the run-rate scaling drops away. When
every cluster already has 30 days, it says on demand only. The plan JSON carries
this as `revisit`. `rerun_days` is retired and ignored if an old config has it.

### 6. Ship only on a clean verify

The verifier exits 1 on any failure and reports each one. Fix the cause, not the
output. Then read the executive summary and the plan yourself before sending —
the gates catch leaks and arithmetic, not a wrong story.

**Optional: cross-check against PerfectScale's own system knowledge.** If you
have an internal corpus of PerfectScale's architecture docs and service code
available (never tenant data — it holds none), this is a manual reading pass,
not an automated pipeline step: no AI runs during generation, and none of the
dollar figures change. Read the plan's `inferred`/`not known` claims and
remediation steps against that corpus before sending. Where a claim turns out
to be documented product behavior rather than genuine data uncertainty, that is
a signal to fix the *engine's* rationale text (see `methodology.md`'s
"Calibrating 'inferred' against documented behavior" for the worked example),
not something to patch per-run — a one-time engine fix benefits every future
plan, a per-run edit benefits only this one and drifts from the next run's
output. Keep the plan's own wording gate in mind: code-level findings inform
engine rationale text, they never appear in the customer-facing plan as
filesystem paths or internal service names.

## What a run produces

Step 3 writes all of this. None of it needs a second command:

| Artifact | Formats |
|---|---|
| Optimization plan | `.md`, `.html`, `.xlsx` - the plan JSON (read by the executive summary, the change log and verify) sits in `data/`; a `.csv` is written only when `openpyxl` is missing |
| Per-cluster findings | `.md`, one per cluster |
| **Executive summary** (one page) | **`.docx` and `.html`** |
| **POV automation kickoff** | **`.html`, one per cluster** (plus a `POV kickoff` sheet in the `.xlsx`) |
| Change log vs `previous_plan` | `.md`, when configured |

The POV automation kickoff page sets the scope of a PerfectScale POV, from the
day automation is switched on to the technical win. It ranks the cluster's top
50 workloads by one cost-and-resiliency score in a single table with a
Controller type column and filters it with a controller-type pull-down. Two
independent buttons write PerfectScale automation configs, each with the
one-per-cluster `ClusterAutomationConfig` prerequisite: **Generate workload
CRDs** writes one file per workload ticked in the ranked table, and
**Generate namespace CRDs** writes one file per namespace ticked in the
namespace picker (every namespace running a selected controller type, not only
the top-50 ones), each
holding its `NamespaceAutomationConfig` plus a `Disabled` workload config for
every held-out, stateful or JVM workload in it. Every config uses the
cost-optimization profile of PerfectScale's published examples, with CPU
`keepLimit: false` and a REVIEW comment on that line. The page applies nothing.
See `references/pov-kickoff.md` for the ranking, the CR shape and how to walk a
customer through it.

The separate perfectscale-executive-1pager skill was retired on 2026-10-21 and
now lives in `~/.claude/skills-archive`; this skill's executive summary replaces it.

The executive summary is rendered by `scripts/exec_summary.js` from the plan
JSON — the same content model emits both formats, so they cannot drift from the
plan or from each other. Never hand-write either. `NODE_PATH` is not needed: the
`docx` module is resolved from the global npm roots, and the HTML needs no
module at all. If node or `docx` is missing the run still completes and prints a
warning rather than failing; install node to get the summary. Confirm the .docx
still fits one page after any copy change.

When the tenant config carries `savings_goal` (the POV plan's goal #1, per
cluster or tenant-wide), the summary adds **Savings goal vs what the data
supports**: one bar per cluster from $0 showing the conservative, realistic and
theoretical-maximum cases, the goal drawn over them, and a verdict (Supported,
Stretch, Unlikely, Not supported) with a sentence on what the goal would take.
It is page 2 of the .docx, so page 1 stays the one-pager. Read the goal from
the POV plan doc before the run; if the verdict is Unlikely or Not supported,
take it into the next customer call as a target reset or an ROI reframe, not
as a surprise at the success review. See `references/tenant-config.md`.

To re-render just the summary (after editing its copy, say):

```bash
node scripts/exec_summary.js <plan.json> <out.docx>
node scripts/exec_summary.js <plan.json> <out.html>
```

## Sections in the HTML

Every `##` section of the plan is collapsible, so a working session can hide what
it is not discussing. Asserted by the verifier, like the table controls:

- **Click any section heading to collapse it** — or focus it and press Enter or
  Space; it is a real `role=button` with `aria-expanded`.
- **Expand all / Collapse all** sit above the first section.
- **Every section starts collapsed**, so the page opens as a table of contents and
  the reader opens what they came for.
- **The Actions table has pull-down filters** - Phase, Topic (Automation,
  Right-sizing, Resiliency, Policy, Scale-down blockers, Node pools and
  autoscaling, Governance), Cluster and Lane - with a "Showing N of M" count,
  **Phase 1 only** and **Show everything**. It opens on Phase 1, the work to
  start now; the verifier asserts both the filters and that default.
- A link into a collapsed section opens it, so the Actions table's links to
  Action detail keep working, and so does a pasted `#A7` URL.
- Printing ignores all of it: `@media print` forces every section open, since no
  one can expand a PDF.

Collapse initialises **after** the column resizer, and the verifier asserts that
ordering. The resizer measures with `getBoundingClientRect`, which reads 0 inside
a hidden section — collapse first and every table in an appendix freezes at
useless equal-width columns.

The executive summary HTML rolls its categories up the same way, with a lighter
mechanism: the decision, the headline cards and the cost of waiting stay open,
and each category below them (What we recommend first, What we need from you,
What the numbers rest on, the savings goal) is a native `<details>` block that
starts closed, with Expand all / Collapse all. It has no column resizer, so no
ordering constraint; printing opens every category. The `.docx` is unchanged.
The verifier checks that every summary heading is a closed category.

## Analysis by namespace

The plan is ordered by what you do. `Appendix: analysis by namespace` is the same
money ordered by **who owns it** — one row per namespace name, summed across every
cluster it runs in, so a namespace with one owner is one conversation rather than
fifteen. Built by `Plan.namespace_rollup()`; rendered in the plan `.md`, the `.html`
and the plan JSON as `namespace_rollup`.

- Ranked by potential savings **plus** resiliency spend, so a namespace whose whole
  problem is under-provisioning still surfaces. Waste alone would bury it.
- Columns: clusters, workloads, cost, waste, potential savings, at-risk count, OOM
  count, resiliency spend (likely). Platform-managed namespaces are marked `*`.
- The prose appendix is capped at the top 40, with a line stating what the
  remainder holds. The XLSX **Namespaces** sheet carries every namespace, with
  raw numbers rather than display strings, plus the cluster names each one runs
  in — a row missing because it ranked 41st is what would make the sheet
  untrustworthy to someone looking up their own namespace.
- **It re-cuts the actions' dollars, it does not add to them.** The appendix says so
  in its own footnote, and a test asserts that sentence is present — a reader who
  sums this table and the action table has double-counted.

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
  `hpa_file` (per-workload HPA presence/triggers, same PodFit browser pass),
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
- `references/pov-kickoff.md` — the POV automation kickoff page: ranking score,
  what is kept out, the generated CRs, and the keepLimit review.
