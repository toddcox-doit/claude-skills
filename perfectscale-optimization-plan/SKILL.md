---
name: perfectscale-optimization-plan
license: Apache-2.0
description: >
  Produce a sequenced, ROI-ranked PerfectScale optimization plan for a customer
  tenant — a working-session worksheet (Markdown + self-contained HTML) that a
  solutions engineer screen-shares with the customer's platform/K8s and FinOps
  team to decide what to do first, second, and third. Converts findings into
  discrete Actions, scores each as (monthly savings x confidence) / effort,
  assigns owner + prerequisite + verification method, groups into Phase 1
  (0-30d quick wins) / Phase 2 (30-90d structural) / Phase 3 (90d+ govern), and
  promotes unblocker actions whose own dollar value understates what they
  release. Use for: "optimization plan", "optimization strategy", "optimization
  roadmap", "detailed optimization steps", "optimization session", "where do we
  start", "what's the highest ROI", "prioritized action plan", "how do we
  capture these savings", "sequence the work", "quick wins", "90-day plan",
  "phased rollout plan", "customer working session", "drive the conversation
  with the customer". Distinct from perfectscale-tenant-savings-report-v2.0
  (finds and reports the opportunity, organized by cluster — this skill consumes
  that and organizes by what you DO, in order) and perfectscale-executive-1pager
  (buying-decision brief for one exec — this is the technical execution plan for
  the team who has to do the work).
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
---

# PerfectScale Optimization Plan (working-session worksheet)

Every other PerfectScale skill in this library organizes its output by **where a
finding lives** — cluster, then PodFit/InfraFit, then node group. That is the
right shape for a diagnosis and the wrong shape for a conversation. A customer's
platform team does not experience their estate as a cluster list; they experience
it as a queue of work they have to justify, schedule, and staff.

This skill produces the other shape: **an ordered queue of Actions**, each one a
thing a named person can do in a sprint, scored by return per unit of effort,
grouped into phases, with the dependency structure made explicit.

**Read `references/roi-scoring.md` and `references/action-catalog.md` before
scoring anything, and `references/plan-voice.md` before writing any prose.** The
scoring model and the effort tiers are the entire substance of this skill; the
collection mechanics are borrowed wholesale from skills that already do them.

**Read-only guarantee:** this skill never applies a change, never clicks "Apply
recommendation," never modifies an automation or policy setting. State this in
the generated plan.

---

## Data paths — CLI first, browser only if it's already there

**`pscli` is the default and it is sufficient.** A token-only run produces a
complete plan: every action, scored, sequenced, phased. The browser adds
precision to one part of it and nothing else. Never gate the plan on a browser
session, and never suggest the CLI-only output is a degraded consolation prize —
for InfraFit it is not degraded at all.

| | CLI-only (default) | + browser enrichment |
|---|---|---|
| **InfraFit** — node groups, autoscaler, unevictable | **Full confidence.** `reason_code`, `remediation.fix_summary`/`yaml_diff` and `autoscaler_type` are all public-API fields. There is nothing the browser adds here | unchanged |
| **PodFit dollars** — waste, cost, current vs recommended | **Full confidence.** `workloads -V capacity` carries the deltas | unchanged |
| **PodFit effort** — is this a toggle or a two-sprint change? | **Medium confidence** via the proxy in `references/action-catalog.md` § Automation-status proxy | **Full confidence.** `workloadAutomationStatusV2` resolves NotEnabled vs. Limited exactly |
| **Blocker reasons** — why automation isn't applying | Not available. Affected actions carry an explicit "cause not confirmed" note | Limited-by-User/System reason text |
| **The "why" narrative** — per-workload evidence | Usage percentiles and indicators from `-V usage`/`-V risk` | cpuReasoning / memReasoning / indicatorReasoning |
| **Runtime** | Minutes | 1-2 hrs, and exceeds one ~60-min impersonation session |

The practical read: **run CLI-only first, always.** It is minutes of work and it
answers the sequencing question. Add the browser pass only when the customer
pushes back on a specific PodFit action's effort rating, or when the session
needs the per-workload "why" to build trust. That is a much better use of an
impersonation session than a blanket two-hour sweep.

---

## Step 0 — Gates (do not collect anything until all are satisfied)

Ask everything in one message, then wait. Do not proceed on a vague "I think so."

### Gate A — pscli (the only hard requirement)

`pscli auth status` must succeed for this tenant, or the user must have a
Read-Only service token (PerfectScale → Organization Settings → API Tokens →
Generate Token → Read Only):

```bash
pscli auth login --client-id "$CLIENT_ID" --client-secret "$CLIENT_SECRET"
pscli auth status
```

Never echo the secret. If neither works, stop — there is no path to a plan
without API access.

### Gate A2 — browser (optional, ask but do not block)

Ask once, as a yes/no: "Is there a Chrome session already reaching
`app.perfectscale.io` for this tenant? If so I'll upgrade the PodFit effort
ratings from inferred to exact. If not, I'll run CLI-only — the plan is complete
either way."

If yes: use `backoffice-impersonation` to reach the tenant, and
`perfectscale-executive-1pager/scripts/cdp-chrome.js` if
`playwright-cli attach --cdp` fails on Chrome 144+. If the remote debugging port
is missing (`~/Library/Application Support/Google/Chrome/DevToolsActivePort`
does not exist), **do not stop to ask them to relaunch Chrome** — note it and
proceed CLI-only. Offer the enrichment pass afterwards if the plan turns out to
hinge on a contested effort rating.

If no, or unclear: proceed. Do not re-ask.

### Gate B — bounded choices (`AskUserQuestion`)

1. **Cluster scope** — All clusters / A subset I'll name / Just one cluster.
2. **Do you already have a tenant savings report for this customer?** Yes, reuse
   it / No, collect fresh / Not sure, check the output directory. Both paths are
   minutes on the CLI — this question is about avoiding duplicate work and
   reusing an audit trail, not about runtime. See Step 1.
3. **Session length the plan is sized for** — 30 min (Phase 1 only, ~8 actions)
   / 60 min (all three phases, ~15 actions) / 90 min deep-dive (all actions, no
   cap). This sets the action cap in Step 5, not the analysis depth.

### Gate C — free text (do not use `AskUserQuestion`)

4. Customer / tenant name, as it should appear on the artifact. **Default: the
   tenant name as `pscli` reports it** — do not ask if that name is already
   unambiguous. Ask only to confirm capitalization when the customer's brand
   differs from the tenant slug (`northwind` → `Northwind Logistics`), and use their
   capitalization everywhere, including in the header and Appendix B.
5. Output directory. **Default: `reports/<TenantName>_report/`** in the current
   workspace, created if absent. One directory per tenant keeps successive runs
   diffable, which is what makes the Step 3 re-run a comparison instead of a
   re-derivation. All three artifacts go in it, named
   `<tenant-slug>-optimization-plan-<date>.{md,json,html}`.
6. **Known constraints to respect in sequencing.** Ask explicitly: change-freeze
   windows, prod change-approval requirements, teams that own which namespaces,
   anything already in flight. Default "none known" is acceptable — but ask,
   because a plan that puts a prod restart in Phase 1 during a quarter-end freeze
   is worse than no plan. Record the answer verbatim into the payload's
   `constraints` field and reflect it in phase assignment.
7. Workload/namespace exclusions. Default: none.

### Runtime expectation-setting

CLI-only: minutes. A handful of `pscli` calls per cluster, no session to time
out, no impersonation window to race. Say this — it is the reason to run the
plan now rather than scheduling a collection session.

Optional browser enrichment, if the user opted in at Gate A2: add
`workloads_enriched x ~1.25 min`. Cap it at the contested actions rather than
sweeping every workload; a targeted 10-minute pass is worth more than an
exhaustive two-hour one.

---

## Step 1 — Collect (CLI-first)

### Fast path — reuse an existing tenant report

If Gate B Q2 was "reuse" or the output directory holds
`tenant-savings-<tenant>-*.md` files:

1. Read the per-cluster files and the tenant rollup.
2. **Check freshness.** Older than 14 days is stale — cost and waste move. State
   the generation date and age in days and ask whether to re-collect. Never
   silently plan off a month-old report.
3. **Check completeness against what scoring needs**: `reason_code` per
   unevictable pod, `autoscaler_type` per node group, optimization policy and
   mute state per workload, current-vs-recommended deltas. Re-collect just the
   gaps — those are single `pscli` calls, so there is no reason to guess.

### Default path — collect via `pscli`

Everything below is public API. Run it per cluster in the Gate B scope.

```bash
# Enumeration and cluster-level totals
pscli -o jsonl clusters list
pscli -o json  workloads summary -c <cluster>

# PodFit — the four views that between them carry every scoring input
pscli -o jsonl workloads list -c <cluster> -V capacity   # current vs recommended requests
pscli -o jsonl workloads list -c <cluster> -V policy     # optimization policy, resilience, mute
pscli -o jsonl workloads list -c <cluster> -V risk       # severity, risk/waste counts
pscli -o jsonl workloads list -c <cluster> -V usage      # p90/p95/p100 — the trust-building telemetry

# The Step 2 collapse rule, CLI-native — one policy covering N workloads is ONE action
pscli -o jsonl workloads group-by optimization-policy -c <cluster>
pscli -o jsonl workloads group-by type -c <cluster>      # Deployment/StatefulSet/DaemonSet split
pscli -o jsonl workloads muted -c <cluster>              # explicit user-disabled set
pscli -o jsonl workloads risky -c <cluster> -S 2 -s severity -r desc

# Is automation actually executing, and does it restart or resize in place?
pscli -o jsonl automation audit-logs -c <cluster> --all
pscli -o jsonl automation audit-logs -c <cluster> --all --execution inplace-resize
pscli -o jsonl automation audit-logs -c <cluster> --all --execution regular-eviction

# InfraFit — full-confidence scoring, no browser
pscli -o jsonl nodegroups list -c <cluster> --all
pscli -o jsonl nodegroups get  -c <cluster> -g <group>
pscli -o jsonl unevictable list -c <cluster> -g <group> --all
```

Three of these do more scoring work than their names suggest:

- **`audit-logs --execution`** is the restart-required escalator, measured rather
  than assumed. A cluster whose history is all `inplace-resize` tells you
  right-sizing there does *not* restart pods — which drops several actions a
  tier and moves them into Phase 1. A cluster that only ever evicts tells you the
  opposite. Never assume the restart escalator when the audit log can settle it.
- **`group-by optimization-policy`** is the collapse rule. Group first, emit one
  action per policy, and Phase 1 stops filling up with fourteen near-identical
  toggles.
- **`workloads muted`** is the closest public-API signal to DisabledByUser, and
  it is exact — a muted workload is one someone deliberately switched off, which
  is a conversation, not a toggle.

Handle `unevictable` snapshot states as `infrafit-improved-v2.0` does: 202 means
wait and retry; 422 means that node group's snapshot failed — flag it, skip that
group, and keep the loop running.

Also capture, because the plan needs them and the savings report does not
foreground them:

- **Automation entitlement** — is automation licensed for this tenant at all?
  `automation audit-logs` returning empty across every cluster is consistent with
  both "never enabled" and "not entitled," and the two have very different
  Action #1s. Distinguish them from the customer directly if the browser isn't
  available: ask whether the Automation page offers settings or a "Request
  Access" prompt. It takes one question and it changes the shape of the session.
- **Impact Tracker equivalent** — with no browser, derive the baseline from
  `audit-logs`: days since the earliest event, event count, in-place vs.
  eviction split. Label it as automation activity, not as the product's Total
  Saved figure — do not present a derived number as the platform's own.
- **Repeated workload names across namespaces** — sort the workload list by name
  and find the duplicates. One custom group covering N copies is a single action
  with N times the payoff; scored separately it looks like N mediocre ones.

### Optional — browser enrichment (only if Gate A2 said yes)

Run `perfectscale-tenant-savings-report-v2.0` Step 2c on the **contested actions
only** — those whose effort tier the proxy could not resolve, plus the top 2-3
by dollar value where the per-workload "why" will earn trust in the room. For
each: capture `workloadAutomationStatusV2`, the Limited-by-User/System reason if
present, and the reasoning chain. Then upgrade those actions per
`references/roi-scoring.md` § CLI-only scoring discipline, and re-run Steps 3-5,
since a tier change can move an action between phases.

Do not skip that skill's **B-4.5 reconciliation** on anything you enrich.
Half-extracted payloads produce confidently wrong numbers, which in a customer
session is worse than an obviously incomplete one.

---

## Step 2 — Normalize every finding into an Action record

This is the first genuinely new step. A "finding" is an observation; an "Action"
is a unit of work someone can be assigned. The mapping is not one-to-one:

- **Collapse** — 14 workloads under one optimization policy, none automated, all
  fixed by enabling automation on that policy → **one** action. Sum the dollars.
  Scoring N near-identical toggles separately floods Phase 1 with duplicates and
  buries the structural work. `pscli workloads group-by optimization-policy` does
  this grouping for you — run it before the per-workload proxy, not after.
- **Split** — a mixed-signal workload (cost lens *and* resiliency lens, per
  `perfectscale-tenant-savings-report-v2.0`'s `references/lens-taxonomy.md`) is **two** actions,
  because they have different owners, different urgency, and opposite signs on
  the dollar figure. Never net a resiliency fix's cost increase against a
  right-sizing saving to produce one blended number.
- **Preserve** — one unevictable `reason_code` on one node group is one action
  even when it blocks several nodes; the fix is singular.

Emit one record per action, using the field set and `action_type` vocabulary in
`references/action-catalog.md`. Every field is required; `null` is a valid value
only for `savings_monthly_usd` on a pure-resiliency action.

Set `savings_basis` on every action to one of `achievable_now`,
`blocked_pending`, or `resiliency_investment`. This carries the
`perfectscale-tenant-savings-report-v2.0` rule forward: **never sum achievable-now and
blocked-pending into one headline**, and never present a resiliency investment as
a saving. The HTML builder enforces the three-way split; do not defeat it by
pre-summing.

---

## Step 3 — Score

Apply `references/roi-scoring.md` exactly. In outline:

```
roi_score = (savings_monthly_usd x confidence_multiplier) / effort_weight
```

Effort weight comes from the tier table (XS=1, S=2, M=4, L=8) via the
`action_type` → effort mapping in `references/action-catalog.md` — **look it up,
do not judge it freehand**. The mapping is the reason two engineers running this
skill on the same tenant produce the same ranking.

Confidence multiplier: High 1.0 / Medium 0.75 / Low 0.5, sourced from the
finding's own confidence badge, which for a cluster is worst-of-all-findings and
never an average.

**On a CLI-only run, apply `roi-scoring.md` § CLI-only scoring discipline before
anything else** — floor PodFit automation actions at S, cap them at medium
confidence, and resolve the restart escalator from `audit-logs --execution`
rather than judgment. InfraFit actions take neither the floor nor the cap; they
score at full confidence from public-API fields.

Then, for every action, run the **effort-tier escalators** in
`roi-scoring.md` § Escalators. An action's catalog tier is its floor, not its
answer. Four escalators each bump it one tier — **change-managed** (a Gate C
constraint this change trips, *or* a restart requirement measured from
`audit-logs --execution`, counted once, never twice), **cross-team**, an
**out-of-plan prerequisite**, and **stateful/data-path** — capped at +2 tiers
total. A prerequisite this plan already schedules does not escalate anything;
sequencing is what handles it. **Production alone is not change-managed** — check
the Gate C answer and the audit log rather than inferring it from a cluster name,
or a mostly-prod estate ends up with an empty Phase 1. Record which escalators fired in
`effort_rationale` — in the session, "why is this an M and not an S" is the
question you will actually be asked, and a one-line answer keeps the meeting
moving.

Verify the arithmetic in bash for every score that lands in the top 10. Do not
trust mental math on a number a customer will screenshot.

---

## Step 4 — Build the dependency graph, then promote

The step that separates a plan from a sorted list.

1. Populate each action's `unblocks: [action_id, ...]` from
   `references/action-catalog.md` § Dependency edges. The recurring ones:
   automation entitlement → every PodFit automation action; unevictable-pod
   remediation on a node group → that group's autoscaler tuning and its idle-cost
   recovery; a `LimitRange`/admission floor → every workload right-sizing under
   it; right-sizing a namespace → the node-count reduction that follows it.
2. Compute `released_value` = the summed `savings_monthly_usd` of everything
   transitively downstream.
3. **Promote** per the rule in `roi-scoring.md`: when `released_value >= 2x` the
   action's own savings, move it one phase earlier and label it
   `unblocker`. Its own dollar figure understates it, and a naive ROI sort
   will bury a $0 prerequisite that gates $40k/mo.
4. Detect cycles. If two actions each claim to unblock the other, the graph is
   wrong — recheck the catalog rather than shipping a plan whose sequence cannot
   be executed.

Never place an action in an earlier phase than any of its prerequisites. If
promotion would violate that, promote the prerequisite instead.

---

## Step 5 — Assign phases and apply the session cap

Phase rules in full are in `roi-scoring.md` § Phase assignment. Summary:

| Phase | Window | Admits | Ordered by |
|---|---|---|---|
| **1 — Prove it** | 0-30 days | XS/S effort, low-or-medium blast radius, no out-of-plan prerequisite | `roi_score` desc |
| **2 — Structural** | 30-90 days | M effort; any tier whose prerequisite Phase 1 clears; high-blast-radius work needing a change window | `roi_score` desc |
| **3 — Compound & govern** | 90+ days | L effort; recurring governance (policy defaults for new workloads, automation-on-by-default, quarterly InfraFit review) | dependency order, then `roi_score` |

Then apply the Gate B Q3 session cap **to what is presented in the body**, not to
what is analyzed: 30 min → Phase 1 top 8; 60 min → top 15 across phases,
proportional per phase; 90 min → no cap. Everything below the cap goes to an
appendix table, complete, so the customer can see it was analyzed and not
discarded. State the cap and the full count in the header — "15 of 47 actions
shown; all 47 in Appendix A."

---

## Step 6 — Write the artifacts

Write three files to the Gate C output directory
(`reports/<TenantName>_report/` unless the user named another):

1. `<tenant-slug>-optimization-plan-<date>.md` — the working document.
2. `<tenant-slug>-optimization-plan-<date>.json` — the payload. Schema:
   `assets/plan-payload.schema.json`. This is also what makes the next run a
   diff instead of a re-derivation.
3. `<tenant-slug>-optimization-plan-<date>.html` — the screen-share artifact:

   ```bash
   python3 <this-skill>/scripts/build_plan_html.py <payload.json> <output.html>
   ```

   Self-contained: no CDN, no build step, no network at open time. Light and dark
   both selected, a theme toggle, and a print stylesheet that forces light and
   avoids breaking a row across pages. Validate the payload first — the builder
   fails loudly on a missing required field rather than rendering a blank cell,
   because a blank cell in front of a customer is a credibility event.

Follow the Markdown structure in `references/plan-voice.md`. The section order is
deliberate and load-bearing:

```
Header — tenant, date, scope, read-only statement, actions-shown-of-total
The number — achievable now / blocked pending / resiliency investment, three
             separate figures, never summed
Where it is — 3-4 sentence concentration summary (which clusters, which pattern)
Decisions we need from you today — 3-5 bullets, each a yes/no or a name
Phase 1 / Phase 2 / Phase 3 — the worksheet tables
How we'll know it worked — verification per phase, against the Impact Tracker
                            baseline from Step 1
Appendix A — all actions, uncapped
Appendix B — method, data sources, confidence, limitations
```

**The worksheet columns are the point.** Every phase table carries three empty
columns — `Owner`, `Target`, `Decision` — for the customer to fill in live. An
artifact that is complete when it arrives is a report; one with blanks the
customer fills is a plan they own. Leave them blank in both MD and HTML; the HTML
renders them as editable fields that persist to `localStorage`, so the SE can
screen-share, type, and re-export without a round trip.

---

## Step 7 — Reconcile and leak-scan

Before handing anything over:

- [ ] Every headline figure re-derived in bash as a sum of its action rows.
- [ ] Achievable-now, blocked-pending and resiliency-investment never summed.
- [ ] No action sits in a phase earlier than any of its prerequisites.
- [ ] Dependency graph is acyclic.
- [ ] Every action has a named owner *role* (see catalog) and a verification method.
- [ ] Every promoted action is labeled `unblocker` with its `released_value` shown.
- [ ] Effort tier for each action traced to the catalog, with escalators recorded.
      Escalators capped at +2 tiers; a stated constraint and a measured restart
      requirement count once between them, as `change-managed`.
- [ ] **CLI-only runs**: no PodFit automation action sits at XS unless one of the
      three Rule 1 exceptions is named in `effort_rationale`; no PodFit automation
      action is scored at high confidence; every inferred tier ends
      `(inferred — cause not confirmed)` and its `steps[0]` is the check that
      settles it. InfraFit actions carry neither the floor nor the cap — if one
      does, the tier is wrong, not conservative.
- [ ] Gate C Q6 constraints reflected in the sequencing, and named where they moved something.
- [ ] Appendix A count == total analyzed count.
- [ ] HTML opens with no network access and renders in both themes; print preview is one clean flow.
- [ ] Leak scan on MD + HTML: `impersonat`, `Backoffice`, `pscli`, `frontegg`,
      `psc-token`, `CDP`, `idleCost`, `science project`, `science experiment`.
      Internal mechanics never appear in a customer artifact — Appendix B
      describes *what* the data is and its confidence, not *how* it was extracted.

---

## Step 8 — Verification design (write it, don't just promise it)

Each phase closes with a checkpoint the customer can run themselves. Anchor to
the Step 1 baseline and name the exact surface:

Every surface in the table below is a **UI** surface, and that is deliberate —
the customer verifies in the product they own, not by re-running your tooling.
That holds on a CLI-only run too. The one thing that changes is the *baseline*
you compare against: with no browser you derived it from `automation audit-logs`,
which is automation activity over the window, not the product's Total Saved
figure. Those two are not the same number. Say which one the plan used, in
Appendix B, and set the checkpoint expectation as a direction of travel — "both
rise" — rather than a delta against a number the customer will read differently
off their own screen.

| Action class | Verified by | Expected direction |
|---|---|---|
| Automation enablement | Impact Tracker: Total Saved, Risks Addressed | Both rise within 14 days |
| Workload right-sizing | Trends: cluster daily cost; PodFit pod waste | Cost curve steps down; waste % falls |
| Unevictable remediation | Unevictable pod count on that node group | Falls toward zero |
| Autoscaler tuning | Overview node idle $; node count | Idle % falls; node count tracks demand |
| Node group instance change | Trends: that group's cost | Steps down at the change date |
| Governance/policy | New workloads landing already-optimized | New-workload waste stays flat as the estate grows |

Give each phase a **re-run date** and say the plan should be regenerated then —
the payload JSON from Step 6 makes the next run a diff, which is a much better
second conversation than a fresh report.

---

## What not to do

- Don't apply changes, click "Apply recommendation," or touch a policy setting.
- Don't gate the plan on a browser session, and don't frame the CLI-only output
  as degraded — for InfraFit it is identical, and for PodFit it is a medium-
  confidence tier rather than a missing one.
- Don't rate a PodFit automation action XS on CLI-only data unless `workloads
  muted`, tenant entitlement, or audit history settles it. S is the honest floor.
- Don't apply the restart-required escalator by judgment when
  `audit-logs --execution` can measure it.
- Don't sum achievable-now with blocked-pending, or count a resiliency investment
  as a saving.
- Don't score effort freehand — look it up in the catalog, then apply escalators.
- Don't emit one action per workload when one policy change covers a set. Collapse.
- Don't net a resiliency cost increase against a right-sizing saving.
- Don't let a naive ROI sort bury a $0 unblocker. That is what Step 4 exists for.
- Don't pre-fill Owner / Target / Decision. The customer fills those; that's the session.
- Don't cap the appendix. Cap the body only, and say what the cap was.
- Don't plan off a report older than 14 days without saying so and offering to re-collect.

## Composition — skills this orchestrates by name

- `perfectscale-cli` (`pscli`) — **the default and primary path.** Cluster
  enumeration, all four workload views, policy/type/mute grouping, automation
  audit logs, node groups and unevictable pods. Everything the plan needs to be
  complete.
- `infrafit-improved-v2.0` — the CLI InfraFit technique reused per node group,
  including its snapshot-state handling (202 retry / 422 skip-and-flag) and its
  `reason_code` classification taxonomy. Its
  `references/analysis-reference.md` supplies fix syntax when the API's own
  `remediation` is null.
- `perfectscale-automation-blockers-v2.0` — blocker-reason taxonomy. Its
  Limited-by-User/System categories are a **direct effort-tier input**, not just
  narrative color: user-set restrictions are XS to clear, system restrictions are
  usually M or L. Available only on an enriched run — on CLI-only, the
  automation-status proxy stands in for it.
- `perfectscale-tenant-savings-report-v2.0` — reuse its per-cluster checkpoint
  files when they already exist (Step 1 fast path), and its Step 2c reasoning
  extraction for the optional enrichment pass. Not a prerequisite.
- `backoffice-impersonation` — Backoffice tenant impersonation. **Optional**, only
  for the enrichment pass.
- `automation-potential-analysis-v2.0` — repeated-workload-name detection, which
  drives the Step 2 collapse rule, and its browser technique for the
  automation-eligibility enum if you do enrich.
- `perfectscale-executive-1pager` — `scripts/cdp-chrome.js` for Chrome 144+; and
  the skill to reach for when the audience is one exec rather than the team who
  does the work. The two pair well: 1-pager for the approval, this plan for the
  execution.
