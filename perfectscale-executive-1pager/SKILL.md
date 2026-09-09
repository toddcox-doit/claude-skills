---
name: perfectscale-executive-1pager
description: >
  Produce a customer-shareable PerfectScale executive 1-pager (Markdown + PDF) for a
  technical buyer with purchase authority. Before starting, confirm a PerfectScale
  API key is available or pscli is authorized and logged in, and use or ask for the
  number of clusters to analyze. Ranks clusters by Overview Potential Max
  Savings, splits findings into PodFit (applications) vs InfraFit (idle nodes), and
  states how PerfectScale automation creates ROI. Use whenever someone asks for a
  PerfectScale 1-pager, executive brief, buying-decision brief, technical-exec readout,
  POV one-pager, top-N clusters by Potential Max Savings, PodFit vs InfraFit ROI
  summary, or a shareable PDF/MD for a customer executive who can approve the
  PerfectScale purchase. Use this instead of a long tenant dump when the audience is
  a decision-maker. Distinct from perfectscale-tenant-savings-report (full estate
  deep-dive) and automation-impact-analyzer (what automation already did in one
  namespace).
---

# PerfectScale executive 1-pager

Customer-facing brief for a **technical executive with buying authority**. Default
output is Markdown + PDF. Scope is the **top N clusters by Overview Potential
Max Savings** — N comes from the user (see Gate B). N≤3 is a true single page;
N>3 is two pages.

This skill is tenant-agnostic. Do not hard-code a previous customer.

## Pre-flight (do not start until this is answered)

**Stop. Do not open Overview, call APIs, run `pscli`, or write the brief until
both gates below are satisfied.** Ask in the first reply if anything is missing.
One message, both questions, then wait.

### Gate A — API access

Prompt the user to confirm **one** of:

- a PerfectScale **API key / service token is available** (client id + secret), or
- **`pscli` is already authorized and logged in** for this tenant.

Do not proceed on a vague “I think so.” After they confirm, **verify** before
any analysis:

```bash
pscli auth status
```

If that fails and they have a token, log in (never echo the secret):

```bash
pscli auth login --client-id "$CLIENT_ID" --client-secret "$CLIENT_SECRET"
pscli auth status
```

If they cannot confirm either, stop and tell them how to create a Read-Only
token (PerfectScale → Organization Settings → API Tokens). A browser session
is still required later for Overview ranking; it does **not** replace this gate.

### Gate B — How many clusters

The user must **answer or include** the number of clusters to analyze (top N by
Overview Potential Max Savings).

- If the prompt already has N (“top 9”, “top 3”, “all 13”), use that N. Do not
  re-ask.
- If N is missing, ask. You may note that **9** is typical for a 2-page
  technical-exec brief and **3** is a true single page — do **not** assume 9.
- N cannot exceed the tenant cluster count; after auth, `pscli clusters list`
  (or Overview) is the cap.

Optional in the same first message, only if still unknown:

- Customer / tenant name (as it should appear on the brief)
- Workload exclusions (substrings in name or namespace). Default: none.
- Output directory. Default: `reports/` in the current workspace.

## Inputs (after pre-flight)

1. Confirmed API key **or** verified `pscli auth status`.
2. **N** = number of clusters to analyze (from the user prompt or their answer).
3. Customer / tenant name.
4. Workload exclusions (optional).
5. Output directory (optional).

## Voice (customer artifacts only)

Read [references/brief-voice.md](references/brief-voice.md) before writing copy.

Hard rules for PDF and MD:

- Audience is the buyer. No internal method notes (no impersonation, Backoffice,
  pscli, `idleCost`, CDP, “we did not run unevictable zoom-in”).
- Say **POV**, never “science project” or “science experiment” — including in
  negation (“not a science project” is still a fail). Use POV only.
- Run-rates are **illustrative, not a savings guarantee**.
- Overview cluster totals (Potential Max Savings, Cost, Pod Waste, Node Idle)
  stay as the platform published them. Workload analysis (ready-to-automate,
  not-supported, top apps) applies exclusions.
- If any workloads were excluded, **footnote every cluster section** with count
  and $ omitted, at the customer’s request. Clusters with zero matches still
  say “none found.”

## Ranking metric

**Authoritative sort is Overview → Potential Max Savings (high to low).**

Do not rank by `pscli` `total_potential_saving`. That is workload-only and
undercounts InfraFit idle. Overview Potential Max Savings is the combined
application + idle opportunity the customer sees.

InfraFit on this brief **is Overview Node Idle** (and the PodFit API `idleCost`
when you need a precise dollar). A full unevictable-pod InfraFit zoom-in is
out of scope unless the user asks for it — and even then, keep that method
out of the customer PDF.

## Access

Need a browser session on `https://app.perfectscale.io` for the **target tenant**.

1. If the open tab is the wrong tenant, run the **backoffice-impersonation** skill
   (PerfectScale Backoffice → tenants search → Actions → Impersonate).
2. Confirm the Overview header shows the customer name (and `IMPERSONATING` when
   using support impersonation).
3. For in-page API calls, use
   `sessionStorage['psc-impersonation-token'] || localStorage['psc-token']`.
   Impersonation tokens live in **sessionStorage**. Do not decode JWTs.
4. Chrome 144+ often breaks `playwright-cli attach --cdp`. Prefer the bundled
   CDP helper:

```bash
node <this-skill>/scripts/cdp-chrome.js --customer "<Name>" page-text
node <this-skill>/scripts/cdp-chrome.js --customer "<Name>" navigate https://app.perfectscale.io/overview
node <this-skill>/scripts/cdp-chrome.js --customer "<Name>" eval-file <this-skill>/scripts/overview-inpage.js
node <this-skill>/scripts/cdp-chrome.js --customer "<Name>" eval-file /tmp/podfit-inpage.js
```

Chrome must be started with remote debugging so
`~/Library/Application Support/Google/Chrome/DevToolsActivePort` exists.
If it does not, stop and ask the user to relaunch Chrome with
`--remote-debugging-port=9222`.

`pscli` (after Gate A) is used for cluster list + automation audit logs.
Ranking still comes from Overview.

## Workflow

Copy this checklist and complete it in order:

```
- [ ] Gate A: API key confirmed or pscli auth status verified
- [ ] Gate B: N clusters known (from prompt or user answer)
- [ ] Tenant session confirmed
- [ ] Overview KPIs + cluster table captured
- [ ] Top N selected by Potential Max Savings
- [ ] PodFit inventory for those N (exclusions applied)
- [ ] Audit / Impact Tracker proof pulled
- [ ] Findings mapped PodFit vs InfraFit
- [ ] MD + PDF written to output dir
- [ ] Customer-doc leak scan (no internal jargon)
```

### 1. Overview

Navigate to `/overview` (30-day timeframe). Run
`scripts/overview-inpage.js` or parse page text.

Capture tenant:

- Cost / 30d, Pod Waste, Node Idle, Potential Max Savings ($ and %),
  Potential Optimized Cost, Current High Risks
- Impact Tracker: days, Total Saved, Total Risks Addressed
- License banner only if it is commercially relevant (trial ending) — phrase
  as “trial window,” not Backoffice fields

Capture **every cluster row**: name, policy, high risks, Potential Max
Savings, Cost, Pod Waste, Node Idle.

Sort by Potential Max Savings descending. Take top N. Record share of
estate Potential Max Savings.

### 2. Cluster UIDs

From `pscli -o json clusters list` or in-page
`/psc-tenant-manager/api/v1/clusters`. Map name → uid.

### 3. PodFit workload analysis (top N only)

Copy `scripts/podfit-inpage.js`, set `CLUSTERS` (name + uid) and `EXCLUDE`
(array of substrings, case-insensitive; `[]` if none), then `eval-file` it.
Save JSON under `data/`.

Eligible = `NotEnabled` + `DisabledByUser`. NotSupported stays out of the
automation business case (search / Kafka / Strimzi called out by name).
DaemonSets (Filebeat, Promtail, kube-proxy, security agents) may be
eligible in the API — still keep them out of the automation ask.

Record per cluster: automated / limited counts, eligible n/$, not-supported
n/$ (search vs kafka split), top 3–5 eligible apps **after exclusion**,
exclusion n/$.

### 4. Proof

- Impact Tracker from Overview
- `pscli automation audit-logs --all` grouped by `cluster_uid` (names are
  often blank). Count in-place vs restart. If all history sits on one
  cluster, that is the proof cluster (usually non-prod).

### 5. Synthesize findings

Every finding needs four columns:

| What we found | Where you see it | What PerfectScale does | Why ROI is positive |

Cover, when true:

- Automation off everywhere / 0 limited → commercial on-switch, not policy
  debugging
- Proof already in Impact Tracker / audit history
- Repeatable app copies across namespaces (one policy per product)
- NotSupported search/Kafka held out of the automation case
- Idle > pod waste on most clusters → InfraFit is how the bill falls
- ExtraHeadroom (or similar) means Potential Max Savings < waste + idle;
  automate *inside* the policy
- DaemonSets out of the automation case

**Flywheel (always):** PodFit shrinks eligible Deployments → pods pack →
InfraFit shows which idle nodes can leave. Neither product finishes the job
alone.

### 6. Write artifacts

1. Write `reports/<Customer>-PerfectScale-Top<N>-Executive-Brief.md` using
   the template in [references/brief-voice.md](references/brief-voice.md).
2. Write a matching JSON payload (schema in that file).
3. Build PDF:

```bash
python3 <this-skill>/scripts/build_brief.py <payload.json> <output.pdf>
```

4. Confirm page count with pypdf: N≤3 → 1 page; N>3 → 2 pages. If it
   overflows, tighten copy — do not silently emit a third page.
5. Grep PDF text + MD for: `science project`, `science experiment`,
   `impersonat`, `pscli`, `idleCost`, `unevictable`. Fix leaks.

## Money math

- 30d × 12 = annual **run-rate**, labeled not a guarantee.
- Do not mix Overview Potential Max Savings (pod + node) with pscli
  workload potential.
- Do not count NotSupported $ as automation ROI.
- Do not count excluded-workload $ as automation ROI.

## Ask (default)

Approve a PerfectScale agreement that **includes automation**, starting with
the ranked clusters — **not a POV on the entire estate**. Sequence:

1. Restore / include automation entitlement (proof cluster + largest prod).
2. Re-enable the proof cluster if it already ran.
3. Open first production on application Deployments only; hold search,
   Kafka, node agents.
4. Remaining top-N in waves.
5. Use InfraFit after each wave to take node idle out.

## Failure modes

- Wrong tenant on Overview → stop, impersonate, restart.
- No Chrome debug port → ask user; do not invent Overview numbers.
- Automation page is “Request Access” → that is the license/entitlement
  gate; say so in buyer language (“automation is off under the trial
  configuration”), not Backoffice field names.
- Zero audit events → still write the brief; proof is remaining waste +
  idle, not a savings curve.
- User has not confirmed API key / pscli login → do not start; re-ask Gate A.
- User has not given N → do not start; re-ask Gate B.
