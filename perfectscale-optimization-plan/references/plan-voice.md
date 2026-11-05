# Voice and Markdown template

The audience is the customer's **platform/K8s engineers and FinOps lead**, in a
room, with you screen-sharing. They are technically fluent, they are the people
who will do the work, and they are slightly skeptical — they have seen a vendor
savings number before.

---

## Voice rules

**Write for people who will have to defend this to their own management.**
Every number needs a traceable source; every recommendation needs a "because."

- **Technical register is correct here.** `kubectl` and Helm syntax inline, real
  field names, real namespaces. Do not explain what a PDB is.
- **No product marketing.** They already have the product. The question in the
  room is "what do we do Monday," not "why PerfectScale."
- **Dollar figures are 30-day actuals x 12 for annual, always labeled
  "run-rate, illustrative, not a guarantee."** Never present a projection as a
  commitment.
- **Three separate figures, never one.** Achievable now / blocked pending /
  resiliency investment. Summing them is the single fastest way to lose a
  technical audience, because someone in the room will do the subtraction and
  find the overstatement.
- **Name the uncertainty before they do.** Medium and low confidence get said out
  loud, in the row. A plan that admits its soft numbers is trusted on its hard
  ones.
- **Say "POV," never "science project" or "science experiment"** — including in
  negation. Carried forward from `perfectscale-executive-1pager`, retired
  2026-10-21 (see `~/.claude/skills-archive/ARCHIVE.md`).
- **No internal mechanics.** No impersonation, Backoffice, `pscli`, `frontegg`,
  CDP, `idleCost`. Appendix B says what the data is and how confident it is, not
  how it was extracted.
- **Second person for actions, first person plural for analysis.** "You'll clear
  the emptyDir blocker on ng-general" / "We found 4 pods pinning that group."
- **Effort is stated, never apologized for.** "This is an M — it needs a change
  window and the payments team's sprint" is respectful of their time. Softening
  an L into "quick win" territory gets found out in week two and costs the whole
  plan its credibility.

### Two things that must be said out loud in every plan

1. **The unblocker sentence.** After the headline: how much of the total sits
   behind actions that save nothing themselves. This is the insight the customer
   cannot get from the product UI, and it is usually what changes their Monday.
2. **The PodFit → InfraFit flywheel.** Right-sizing shrinks pods, pods re-pack,
   *then* nodes leave. Say it once at the Phase 1/Phase 2 boundary. Customers who
   miss this run the two as independent tracks and conclude right-sizing "didn't
   lower the bill" — because the nodes never left.

---

## Markdown template

```markdown
# PerfectScale Optimization Plan — [Customer]

**Prepared:** [date] · **Scope:** [N] clusters · **Data window:** 30 days
**Showing:** [X] of [Y] actions ([session length] session) · full list in Appendix A
**This is an analysis. No changes were applied to your environment.**

---

## The opportunity

| | Monthly | Annual run-rate |
|---|---|---|
| **Achievable now** | $X | $12X |
| **Blocked pending remediation** | $Y | $12Y |
| **Resiliency investment** (spend, not saving) | $(Z) | $(12Z) |

Annual figures are 30-day run-rates, illustrative, not a savings guarantee.
The three rows are not summed — "blocked pending" needs the remediation in
Phase 1 and 2 before it is available, and the resiliency row is money you
should spend to stop [N] workloads being OOM-killed.

**[The unblocker sentence.]** e.g. "$13,400/mo of the $16,500 sits behind two
actions that save nothing by themselves and take under a day."

## Where it is

[3-4 sentences. Which clusters carry the opportunity and what share. The
dominant pattern — automation never switched on / blockers pinning nodes /
default requests never revisited. One sentence on what that pattern implies.
Concrete, from their data, no generalities.]

## Decisions we need from you today

[3-5 bullets. Each is a yes/no or a name — not a discussion topic. These drive
the session; everything below is the evidence behind them.]

- [ ] Who owns the PerfectScale policy configuration? (Phase 1 needs one name.)
- [ ] Can we enable automation on `[namespace]` in [cluster] this sprint?
- [ ] Which team owns `[namespace]` — we need them for A[N].
- [ ] Is there a change freeze between now and [date]?

---

## Phase 1 — Prove it (days 0-30)

**[N] actions · $X/mo · releases a further $Y/mo**

[One sentence on what this phase is for. If Phase 1 is small in dollars, say so
here and say why — do not pad it.]

| # | Action | Cluster / Scope | $/mo | Effort | Risk | Role | Owner | Target | Decision |
|---|---|---|---|---|---|---|---|---|---|
| A1 | [title] · **unblocker, releases $7,000/mo** | prod-eu-west-a / platform ns | — | XS | ● Low | ps-admin | | | |
| A3 | [title] | prod-eu-west-a / platform ns | $4,800 | S | ▲ Medium | ps-admin | | | |

*Owner, Target and Decision are for you to fill in during this session.*

### A1 — [Full title]

**$0/mo directly · releases $7,000/mo · XS effort · Low risk · ps-admin**
*Effort: XS (catalog), no escalators*

[1-2 sentences of evidence. What we found and where you can see it yourself.]

1. [step]
2. [step]
...

**Verify:** [method] → [expected direction, by when]
**Unblocks:** A3 ($4,800/mo), A4 ($2,200/mo)

[Repeat per action in this phase.]

---

## Phase 2 — Structural (days 30-90)

**[N] actions · $X/mo**

[Same table + detail structure.]

> **Sequencing note — the flywheel.** [The PodFit → InfraFit paragraph, placed
> here because this is the boundary where it bites.]

---

## Phase 3 — Compound & govern (day 90+)

**[N] actions · $X/mo + keeps the curve flat**

[Same structure. Every governance action tied to a specific number from their
estate, never generic best practice.]

---

## How we'll know it worked

| Checkpoint | Date | What to look at | Expected |
|---|---|---|---|
| Phase 1 close | [+30d] | Impact Tracker: Total Saved, Risks Addressed | Both up; $X/mo realized |
| Phase 2 close | [+90d] | Trends: cluster daily cost | Step down at each change date |
| Phase 3 review | [+180d] | New-workload waste % | Flat as the estate grows |

Baseline today: Impact Tracker shows [days] active, $[total] saved, [N] risks
addressed.

**Re-run this plan on [date].** We'll diff it against today's and the second
conversation starts from what moved.

---

## Appendix A — All actions

[Complete table, all Y actions, no cap, same columns minus the worksheet
columns. Sorted by phase then roi_score.]

## Appendix B — Method, confidence and limits

- **Data window:** 30 days ending [date].
- **Scope:** [N] of [M] clusters. [Which, and why any were excluded.]
- **Exclusions:** [count and $ omitted, per cluster, if any were applied.]
- **Data source:** [One of the two lines below — always say which.]
  - *CLI-only:* PerfectScale's public API. Everything here is reproducible from
    your own service token — nothing came from a source you cannot query
    yourself. The API reports that a workload is unautomated but not *why*, so
    those actions are scored conservatively and each one's first step is the
    check that settles it.
  - *Enriched:* PerfectScale's public API, plus a review of per-workload
    automation status in the console. Automation actions are scored against
    confirmed status rather than inferred.
- **Confidence:** [X] actions high, [Y] medium, [Z] low. Medium and low are
  marked in every row. [What made the low ones low.]
- **How actions are ranked:** monthly saving, adjusted for confidence, divided by
  an effort weight (XS=1, S=2, M=4, L=8). Effort is assigned from a fixed rubric,
  then escalated — for change-managed work (a constraint you told us about, or a
  restart requirement measured from your own automation history, counted once
  rather than twice), cross-team ownership, dependencies this plan does
  not itself resolve, and stateful data paths. Escalation is capped at two tiers
  so that a well-understood change never scores as an architectural one. Each
  action's row shows which escalators applied.
- **Cost vs. resiliency split** is inferred from PerfectScale's recommendation
  reasoning, not a native product field.
- **Not included:** [anything analyzed but deliberately left out — NotSupported
  workloads, DaemonSets held out of the automation ask, node groups skipped.]
- **This was an analysis only. Nothing in your environment was changed.**
```

---

## Table conventions

- **Dollars:** `$4,800` — no cents, thousands separators, `—` for a true zero (an
  unblocker's own savings), `n/a` for a resiliency action with no dollar figure.
  A bare `0` reads as a data error.
- **Risk:** icon **and** word, always both — `● Low`, `▲ Medium`, `▲ High`,
  `■ Critical`. Never color alone; the plan gets printed in black and white and
  forwarded to someone with a different display.
- **Effort:** the bare tier letter, `XS`/`S`/`M`/`L`. The rationale goes in the
  per-action detail, not the table.
- **Unblockers:** bold `**unblocker, releases $X/mo**` appended to the title
  cell. Own savings stay in the dollar column. Never put `released_value` in the
  dollar column — that is the same overstatement as summing achievable-now with
  blocked-pending.
- **Worksheet columns** (`Owner`, `Target`, `Decision`) are always the last
  three, always empty, always footnoted the first time they appear.
