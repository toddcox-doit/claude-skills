# ROI scoring, effort tiers, and phase assignment

This file is the substance of the skill. The collection mechanics come from
other skills; **this scoring model is the only thing here that is new**, and it
is what makes two engineers running the plan on the same tenant produce the same
ranking. Apply it mechanically. Where it says "look it up," look it up.

---

## 1. The score

```
roi_score = (savings_monthly_usd x confidence_multiplier) / effort_weight
```

Read as **dollars per month, per unit of effort**. It is a ranking device, not a
forecast — never present `roi_score` to a customer as a dollar figure. Show it as
a unitless score beside the real dollar figure, which always appears in its own
column.

Round to a whole number, **half up** — `2200 x 0.75 / 4 = 412.5 → 413`. Stated so
that recomputing the payload in Python reproduces it exactly; `round()` there is
half-to-even and would give 412.

### Confidence multiplier

| Confidence | Multiplier | When |
|---|---|---|
| High | 1.0 | Figure came straight from `pscli` or a fully-extracted reasoning payload; reconciled in the source report's B-4.5 pass |
| Medium | 0.75 | Extraction was partial (multi-container workload with one container read), or the 14d table and 30d sidebar disagreed within tolerance |
| Low | 0.5 | Inferred, estimated, or extrapolated from a sample — e.g. a namespace-level projection from three sampled workloads |

Confidence is the **finding's own** badge, carried forward. For a rolled-up
action that collapses several findings, take the **worst** contributing
confidence, never the average. A collapse is only as trustworthy as its weakest
member.

### Pure-resiliency actions

An action whose `savings_basis` is `resiliency_investment` has
`savings_monthly_usd: null` or a negative number — it costs money to fix an OOM.
It gets **no `roi_score`**. Do not synthesize a dollar value for avoided downtime;
you do not have that number and inventing it is the fastest way to lose a
technical room.

Rank resiliency actions separately, by severity: `critical` (active OOM kills in
prod) → `serious` (throttling at level 3, or OOM in non-prod) → `warning`
(headroom below policy floor). Place them in phases by severity, not by score:
critical always lands in Phase 1 regardless of effort, because "we found your
prod service is being OOM-killed and scheduled the fix for month three" is not a
defensible sentence.

---

## 2. Effort tiers

Four tiers. The weight is a divisor, so the gap between tiers has to mean
something — it is deliberately geometric.

| Tier | Weight | Definition | The test |
|---|---|---|---|
| **XS** | 1 | A setting changed inside PerfectScale. No customer repo change, no deploy, no manifest. | Can one person with PerfectScale admin do it in under 10 minutes without opening a PR? |
| **S** | 2 | One manifest or Helm-values change, one team owns it, ships on the normal deploy cycle, no architecture change. | Is it a single PR that a reviewer would approve without a design discussion? |
| **M** | 4 | Multiple workloads or multiple teams, or it needs a maintenance window, or it changes cluster-level config. | Does it need coordination — a meeting, a window, or a second team? |
| **L** | 8 | Architectural change, storage or platform migration, or an app-team redesign. | Would you need to write a design doc? |

The catalog in `action-catalog.md` maps every `action_type` to its **floor**
tier. Escalators below can raise it. Nothing lowers it.

### Escalators (each bumps one tier; **capped at +2 tiers total**, and at L)

Four escalators, deliberately non-overlapping. Earlier drafts listed prod blast
radius and restart-required separately, which double-counts: on a production
workload they are the same cost, which is change management. They are merged.

| Escalator | Fires when | Why |
|---|---|---|
| **Change-managed** | The customer stated a constraint at Gate C that this change trips — a freeze window, a prod approval step, a required CAB — **or** the change is measurably not applicable in place. Counts **once** even when both are true | Approval, scheduling and rollback planning are the work; a prod restart is one cost, not two |
| **Cross-team** | Owner role is an app team rather than the platform team, or more than one app team is affected | Handoffs, backlog priority, someone else's sprint |
| **Unmet prerequisite** | `depends_on` names something **not scheduled in this plan** — an external dependency, a team's in-flight migration, or a prereq deliberately deferred | The real cost includes getting unblocked, and nothing in the plan does it for you |
| **Stateful or data-path** | Kafka, Strimzi, any database, any workload with a local PV or a data-durability concern | Mistakes are not cheaply reversible |

**The unmet-prerequisite escalator does *not* fire for a prerequisite that is
itself an action in this plan**, whatever phase it lands in. That is the whole
point of sequencing: within a 30-day phase you can do A1 on day 1 and its
dependent on day 3. Firing it on in-plan dependencies also makes the model
oscillate — the escalator pushes the action to a later phase, which changes
whether the prerequisite is "unmet," which pushes it back. Scoping it to
out-of-plan dependencies removes the loop and is the more honest reading.

**"It's production" is not by itself a change-managed escalator.** Both halves of
that row are things you can check rather than assume, and you must check them:

- *The approval half* comes from the Gate C constraints answer, not from the
  cluster name. If the customer said "no change freeze, no approval lead time,"
  believe them. Estates that are 90% production are common, and firing the
  escalator on prod-ness alone empties Phase 1 of everything that matters — which
  is not conservatism, it is a plan that recommends nothing for the first thirty
  days.
- *The restart half* is measurable. `automation audit-logs --execution
  inplace-resize` versus `--execution regular-eviction` on the same tenant tells
  you what right-sizing actually does there. A tenant whose history is
  overwhelmingly in-place resizes does not take this escalator on ordinary
  right-sizing, and the rationale should quote the split: *"88% of measured
  actions were in-place resizes, not restarts."*

Where the audit log is empty and the customer named no constraint, do not
escalate — say in `effort_rationale` that neither signal was available, and make
confirming it a step in the action. An escalator you cannot justify to the room
is worse than one you did not apply, because the first question in the session is
always "why is this an M?"

The +2 cap matters. Without it a prod, cross-team, stateful right-sizing lands
at L — "would you need to write a design doc?" — when it is really a
well-understood change that needs a window and another team's sprint. Inflating
effort is not conservatism; it buries real opportunity in Phase 3.

Record every escalator that fired in `effort_rationale`, in one line:
`"S (catalog) -> M: change-managed (prod, restart)"`. In the session, "why is
this an M" is the question you will actually be asked; the answer needs to be in
the artifact, not in your head.

### CLI-only scoring discipline

The default run has no `workloadAutomationStatusV2`, so it cannot separate
**NotEnabled** (a toggle — XS) from **Limited by System** (a blocked workload
needing the underlying cause fixed — M or worse). Both look identical from the
public API: a workload with an unapplied recommendation and no automation
history. Three rules keep that honest without throwing away the plan.

**Rule 1 — floor automation actions at S, never XS, on CLI-only data.**
XS means "one click, ten minutes, done." Assert that only when you know the
workload is merely NotEnabled. When you don't, S is the honest floor: it says
"small, but confirm before you promise it." This is the single most important
rule in the file, because an XS rating is a promise the customer will hold you
to in the next meeting.

Three exceptions, where the API *does* settle it:

- **`workloads muted`** — an explicit, exact signal. A muted workload is
  DisabledByUser: someone deliberately switched it off. That is `restriction_clear_user`,
  genuinely XS to reverse, but it needs the conversation about *why* it was
  muted, so keep `change_risk` at medium and put the question in `steps`.
- **Tenant-wide entitlement** — if automation is not licensed at all, every
  automation action is blocked behind one commercial prerequisite. That is a
  clean XS action with a large `released_value`, and it needs no enum.
- **A policy with automation already on and audit history to prove it** — then
  workloads under it that remain unautomated are eligibility-limited, not
  toggle-limited. Score them M and say the cause is unconfirmed.

**Rule 2 — cap PodFit automation actions at `medium` confidence.**
Not because the *dollars* are uncertain — `workloads -V capacity` is exact — but
because the confidence multiplier is doing double duty as a ranking hedge on
effort. A medium cap costs 25% of score, which is roughly the right penalty for
"the tier might be one step off in either direction." InfraFit actions take no
such cap: `reason_code`, `remediation` and `autoscaler_type` are all public-API
fields, and an unevictable-pod action scored from the CLI is exactly as
trustworthy as one scored from the UI.

**Rule 3 — say so in the row.** Any action whose tier came from the proxy gets
`effort_rationale` ending in `(inferred — cause not confirmed)`, and its
`steps[0]` is a check that resolves it, phrased for the customer's own console:
"Open PodFit → this workload → check whether automation status reads Not Enabled
or Limited. If Limited, this is an M, not an S, and we'll re-sequence it."

That last one converts the gap into an agenda item. A customer who confirms four
statuses live in the session has just given you the enrichment pass for free, and
it plays as collaboration rather than as a caveat.

### Measure the restart escalator, don't assume it

`automation audit-logs --execution inplace-resize` vs. `--execution
regular-eviction` settles the restart-required escalator empirically for any
cluster with automation history. Use it before applying that escalator by
judgment:

- History is predominantly **`inplace-resize`** → right-sizing on this cluster
  does not restart pods. **Do not** apply the restart escalator to right-sizing
  actions here. This routinely moves work from Phase 2 into Phase 1, and it is
  the most valuable single inference the CLI path makes.
- History is predominantly **`regular-eviction`** → apply it.
- **No history** → fall back to judgment, and note the assumption in
  `effort_rationale`.

The escalator still applies regardless of audit history to StatefulSets, to
instance-type changes (which drain nodes), and to anything the customer names as
requiring a change window.

### The tier is about effort, not risk

Do not encode "this feels scary" as effort. Blast radius and reversibility are
their own column (`change_risk`, using the four status levels: `low`,
`medium`, `high`, `critical`), rendered with an icon and a label so the meaning
never rests on color alone. Risk gates **which phase** an action can enter; it
does not change the score. Conflating them makes the ranking mush.

---

## 3. Dependency edges and promotion

The reason this is a plan and not a sorted list.

### Building the graph

Populate `unblocks: [action_id, ...]` from `action-catalog.md` § Dependency
edges. Then compute, for each action:

```
released_value = sum of savings_monthly_usd over the transitive closure of unblocks
```

Transitive, not immediate. An entitlement action that unblocks three policy
actions, each of which unblocks a set of workload right-sizings, carries all of
it. Do not double-count a node reached by two paths — it is a set union over
action ids, then a sum.

### The promotion rule

> **If `released_value >= 2 x savings_monthly_usd`, promote the action one phase
> earlier and label it `unblocker`.**

For an action with zero or null savings of its own — an entitlement toggle, a
`LimitRange` change, a PDB fix that saves nothing directly — the ratio is
infinite and promotion is automatic. That is the point. A naive ROI sort scores a
$0 action at zero and drops it to the bottom of the list, and then the customer
executes the plan top-down and stalls on action 4 because action 31 was its
prerequisite.

A promoted action's row shows **both** numbers: its own savings, and
"releases $X/mo." Never present `released_value` as the action's own savings —
that is the same overstatement as summing achievable-now with blocked-pending,
just wearing a different hat.

### Invariants

- **No action may sit in an earlier phase than any of its prerequisites.** If
  promotion would violate this, promote the prerequisite instead and re-check.
- **The graph is acyclic.** If A unblocks B and B unblocks A, the catalog was
  misapplied — one of them is a co-requisite, not a prerequisite. Merge them into
  a single action rather than shipping a plan whose order cannot be executed.
- Promotion is **one phase**, applied once. An action does not climb from Phase 3
  to Phase 1 on a single large `released_value`. If it genuinely belongs in Phase
  1, its effort tier or its phase rule is wrong — fix that, not the promotion.

---

## 4. Phase assignment

### Phase 1 — Prove it (days 0-30)

Admits an action when **all** hold:

- Effort tier XS or S (after escalators)
- `change_risk` is `low` or `medium`
- No **out-of-plan** prerequisite. An in-plan prerequisite is fine provided it
  lands in this phase or an earlier one — order within the phase handles it
- Not blocked by a Gate C constraint in this window

Plus, unconditionally: any `resiliency_investment` action at `critical` severity,
regardless of effort or risk. Ordered by `roi_score` descending, with promoted
unblockers first within the phase.

Phase 1's job is a visible, attributable win inside one billing cycle. If Phase 1
has no action over $1,000/mo, say so plainly rather than padding it — "the
opportunity here is structural, and the first month is about clearing the path"
is a fine and honest opening. Padding Phase 1 with noise is how a plan loses the
room in the first ten minutes.

### Phase 2 — Structural (days 30-90)

- Effort tier M
- Any tier whose prerequisites are cleared by Phase 1
- High-blast-radius work that needs a change window
- Anything a Gate C constraint pushed out of Phase 1

Ordered by `roi_score` descending. This is usually where the money is: node group
right-sizing, autoscaler tuning, and the unevictable remediation that makes both
possible.

### Phase 3 — Compound & govern (day 90+)

- Effort tier L
- Recurring governance, which belongs in every plan even when nothing forces it:
  - Default optimization policy applied to new namespaces automatically
  - Automation on-by-default for new eligible workloads
  - A quarterly InfraFit review, with a named owner
  - A re-run date for this plan
- Anything whose prerequisite chain reaches into Phase 2

Ordered by dependency, then `roi_score`.

Phase 3 is the difference between a one-time cleanup and a cost curve that stays
flat while the estate grows. Say that in the plan. It is also the phase the
customer is most likely to cut, so tie each governance action to a specific
number from **their** estate — "the 14 workloads we found at default requests all
landed in the last two quarters" — never generic best-practice advice.

---

## 5. Worked example

A tenant with automation licensed but never switched on. **This is a CLI-only
run**, so Rules 1 and 2 apply: PodFit automation actions floor at S and cap at
medium confidence; the InfraFit actions (A5, A6) take neither.

| # | Action | Own $/mo | Conf | Catalog → floor | Escalators | Effort | Score | Unblocks |
|---|---|---|---|---|---|---|---|---|
| A1 | Enable automation on `platform` namespace policy (covers 14 workloads) | 0 | Med | XS → S (2) | none | S (2) | 0 | A3, A4 |
| A2 | Right-size `checkout-api` requests | 3,100 | High | S (2) | change-managed (prod, restart) | M (4) | 775 | — |
| A3 | Automate 14 eligible workloads, `platform` ns | 4,800 | Med | XS → S (2) | none — A1 is in-plan | S (2) | 1,800 | — |
| A4 | Automate 6 eligible workloads, `data` ns | 2,200 | Med | XS → S (2) | cross-team | M (4) | 413 | — |
| A5 | Clear `emptyDir` blocker on `ng-general` | 0 | High | S (2) | none | S (2) | 0 | A6 |
| A6 | Karpenter consolidation tuning, `ng-general` | 6,400 | Med | M (4) | none | M (4) | 1,200 | — |

Two things worth tracing. A3's prerequisite A1 is *in this plan*, so the
unmet-prerequisite escalator does not fire — A3 stays S. A2's prod-plus-restart
is **one** escalator, not two, so it lands at M rather than L.

A naive `roi_score` sort gives **A3, A6, A2, A4, A1, A5** — and it is
unexecutable. A3 is first but A1 gates it; A6 is second but A5 gates it. A
customer working that list top-down stalls twice, and both stalls are on $0
actions sitting at the bottom.

Now apply promotion. A1's `released_value` is 4,800 + 2,200 = 7,000 against its
own 0 → infinite ratio → promote, label `unblocker`. A5's is 6,400 against 0 →
same. Both are S with medium risk, so both sit in Phase 1 anyway; promotion
guarantees they sort to the **front** of it rather than the back.

Final plan:

- **Phase 1** — A1 (unblocker, releases $7,000/mo), A5 (unblocker, releases
  $6,400/mo), A3 ($4,800/mo, prereq A1 clears in this same phase)
- **Phase 2** — A6 ($6,400/mo), A2 ($3,100/mo), A4 ($2,200/mo)
- **Phase 3** — governance: default policy for new namespaces, quarterly InfraFit
  review, re-run this plan

Headline: **$16,500/mo achievable**, of which **$13,400/mo sits behind two
actions that cost nothing and save nothing by themselves.**

That last sentence is the whole reason the skill exists, and in a working session
it is the sentence that changes what the customer does on Monday.
