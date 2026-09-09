---
name: perfectscale-tenant-savings-report-v2.0
license: Apache-2.0
description: >
  Produce a comprehensive, customer-ready markdown savings report for an ENTIRE
  PerfectScale customer tenant — every cluster, combining PodFit (workload
  right-sizing, split into a cost-optimization lens and a resiliency/reliability
  lens, with the "why" reasoning and an automation-benefit narrative per
  workload) and InfraFit (every node group: cost optimization, autoscaler
  tuning, and unevictable-pod remediation, pulled CLI-first via `pscli`), plus
  a tenant-wide landscape rollup. Use for: "tenant savings report",
  "comprehensive cluster report", "maximum potential savings", "landscape
  analysis", "proof of value across the account", "analyze every cluster",
  "what's the full savings story for this customer", or when a solutions
  engineer wants a single-tenant, all-cluster PodFit+InfraFit report to build
  customer confidence. Distinct from single-cluster skills
  (podfit-deployment-audit-v2.0, infrafit-improved-v2.0,
  automation-potential-analysis-v2.0) — this one composes them across the
  whole tenant and adds cost/resiliency lens-splitting, per-node-group
  autoscaler tuning, and a tenant rollup that none of them do alone.
compatibility: >
  Cluster enumeration and the entire InfraFit pipeline (Step 3) are CLI-first
  via pscli when a service token is available for the tenant — that's the
  bulk of what required a browser in v1. PodFit's reasoning-chain extraction
  (Step 2c) still requires Backoffice impersonation via `backoffice-impersonation`
  + the browser debug modal; no public-API field carries
  cpuReasoning/memReasoning/indicatorReasoning.
---

# PerfectScale Tenant Savings Report (v2.0, CLI-first where possible)

Composes existing PerfectScale skills into one tenant-wide report. **This skill
does not reimplement mechanics that already exist** — it orchestrates
`perfectscale-cli` (`pscli`) for cluster enumeration and the InfraFit pipeline,
`backoffice-impersonation` + `perfectscale-batch-analyzer-v2.0`'s PodFit
reasoning-extraction technique for the one piece that still needs a browser,
and `infrafit-improved-v2.0`'s CLI-based InfraFit technique by name, and adds
the genuinely new logic: cost/resiliency lens-splitting, per-node-group
autoscaler-tuning guidance, and the tenant-wide rollup. Read
`references/lens-taxonomy.md` and `references/autoscaler-tuning.md` before
writing any report content.

**v2.0 vs v1:** v1 needed the browser for cluster enumeration, PodFit
selection, and the entire InfraFit pipeline — `pscli` was optional
enrichment at best. v2.0 flips that: `pscli` is the default path for cluster
enumeration (Step 1) and all of InfraFit (Step 3); the browser is reserved
for PodFit's per-workload reasoning chain (Step 2c), which has no public-API
equivalent, and as the explicit no-token fallback elsewhere.

**Read-only guarantee:** this skill never applies a change, clicks "Apply
recommendation," or modifies automation settings. State this explicitly near
the top of every generated report.

---

## Step 0 — Elicit inputs before touching anything

Collect everything up front, in one pass (mirror
`perfectscale-batch-analyzer-v2.0`'s Step 0 judgment on free-text vs. `AskUserQuestion`):

**Free text (open-ended/numeric — do not use AskUserQuestion):**
1. Customer/tenant name — as it appears in PerfectScale Backoffice (partial is fine).
2. Where to save the reports — default: current working directory.

**AskUserQuestion (bounded choices):**
3. Cluster scope: "All clusters in the tenant" / "A subset I'll name" / "Just one cluster."
4. "Do you have, or can you generate, a Read-Only PerfectScale service token for
   this tenant?" (Org Settings → API Tokens in the PerfectScale UI → Generate
   Token → Read Only role). Yes / No / Not sure. This decides the enumeration
   path in Step 1 — a token is preferred but never required.

**Free text, asked with an explicit expectation-setting note:**
5. "How many top workloads per cluster should I analyze for PodFit? Enter 1–10.
   Reliable extraction is capped at 10 per cluster (~1–1.5 min/workload,
   per `perfectscale-batch-analyzer-v2.0`'s documented rate) — a higher number isn't
   supported."

Do not proceed to Step 1 until all five are answered.

---

## Step 1 — Resolve tenant access and enumerate clusters

1. **If a token was confirmed in Step 0 Q4:** follow `perfectscale-cli/SKILL.md`'s
   bootstrap (`pscli auth login --client-id --client-secret`, or discover an
   existing profile). Run `pscli clusters list -o jsonl` — this is the
   **authoritative** cluster list for the tenant. Record cluster name + UID for
   each. With a token, Step 3 (InfraFit) runs entirely off this and needs no
   browser at all — `pscli`'s `nodegroups`/`unevictable` commands cover it.
2. **Invoke `backoffice-impersonation` regardless of token availability** — it's
   still needed for Step 2c's PodFit reasoning-chain extraction
   (`cpuReasoning`/`memReasoning`/`indicatorReasoning` have no public-API
   field), and as the fallback path for Steps 1 and 3 if no token is
   available. Record the resulting tabId.
3. **If no token was available:** enumerate clusters from the browser Overview
   table/cluster-switcher dropdown instead of `pscli`. Record this in the
   report's Methodology appendix as a **best-effort, not authoritative**
   enumeration (a token-based `clusters list` may find inactive/decommissioned
   clusters the active UI switcher doesn't surface). This also forces Step 3
   into its browser fallback (`infrafit-improved` v1) for this run.
4. Apply the Step 0 Q3 scope filter (all / named subset / one) to the enumerated
   list.
5. **If the resulting cluster count exceeds 8:** stop. Estimate total runtime
   (below) and use `AskUserQuestion` to ask the operator how many/which
   clusters to actually run — never auto-cap silently.

   Runtime estimate to show depends on whether a `pscli` token is available:

   - **With a token** (Step 3 is CLI-only): `(clusters × top_N × 1.25 min)`
     for PodFit's browser-bound reasoning extraction + a few seconds per
     cluster for InfraFit. Example: 5 clusters × N=10 ≈ 62 min total — driven
     entirely by PodFit; InfraFit is no longer the long pole.
   - **Without a token** (Step 3 falls back to the browser): add
     `(clusters × avg_node_groups × 4 min)` for InfraFit on top of the PodFit
     estimate. Example: 5 clusters × N=10 ≈ 62 min PodFit + 5 × ~3 groups × 4
     min ≈ 60 min InfraFit ≈ **~2 hours total** — state plainly that this
     exceeds one ~60-minute Backoffice impersonation session and that the skill checkpoints
     per-cluster (Step 4) so a mid-run timeout only costs re-impersonation,
     not lost work.

---

## Step 2 — Per-cluster PodFit pipeline

For **each** cluster in the confirmed list, in order:

### 2a. Session check before starting this cluster
Before starting each new cluster (not just once at the top of the run), check
whether the Backoffice impersonation session is still active by navigating to
`https://app.perfectscale.io/pod-fit` with the prior tabId and screenshotting.
If expired, re-invoke `backoffice-impersonation` and note "re-impersonated before
cluster N" in the run log.

### 2b. Top-N workload selection
- **If a token is available:** `pscli workloads list -c <cluster> -V all -s
  waste -r desc -T <N> -o jsonl` — fast, structured, no browser round-trip for
  selection itself (the browser is still needed for reasoning in 2c).
- **Fallback / cross-check:** the in-page `fetch()` technique from
  `automation-potential-analysis-v2.0` (`GET
  https://api.app.perfectscale.io/psc-data-provider/api/v1/clusters/uid/<UUID>/podfit/<period>`,
  `Authorization: Bearer <localStorage['psc-token']>`, run via the JS tool
  *inside* the already-authenticated `app.perfectscale.io` tab — same-origin
  from the page's own context, not the cross-origin extension-side fetch that
  `automation-impact-analyzer-v2.0` correctly warns against). Sanity-check the top-N
  against the UI's PodFit table row count before trusting it, exactly as that
  skill's own Step 3 does.
- Sort by **waste descending across all workloads**, not filtered to
  Limited-by-User/System — this report covers the full landscape, not just
  blocked workloads. (This is the one deliberate deviation from
  `perfectscale-batch-analyzer-v2.0`'s own default: skip its B-2c automation-status
  filter step entirely; keep everything else.)

### 2c. Reasoning extraction (reuse `perfectscale-batch-analyzer-v2.0` B-3 → B-4.5 verbatim)
Follow that skill's documented steps exactly:
- B-3: construct zoom-in URLs (`/pod-fit/<uuid>/zoom_in/<ns>-<type>-<name>/<name>?period=30d`).
- B-4: two separate `browser_batch` calls per workload (navigate, then `key: q`
  + `read_page(filter="all", max_chars=60000)`), heading-match verification,
  React-fiber fallback if `read_page` is thin, multi-container caveat noted per
  card.
- B-4.5: mandatory reconciliation before any report text is written —
  completeness check (N extracted == N requested), waste sanity check (14d
  table vs. 30d sidebar), integrity spot-checks on the top 2 workloads
  (reasoning-chain terminal vs. sidebar, OOM/throttle direction consistency),
  bash-verified arithmetic for every headline figure.

### 2d. NEW — split the one blended payload into two lenses
For each workload's extracted payload, apply the keyword/field taxonomy in
`references/lens-taxonomy.md` to partition `cpuReasoning` / `memReasoning` /
`indicatorReasoning` / `indicators` / `restrictions` into:
- **Cost-optimization lens** → its own table row + coaching-card subsection.
- **Resiliency/reliability lens** → its own table row + coaching-card subsection.
A workload with signals in both is a **mixed-signal workload** — report both
lenses for it, never force a single bucket. State once, in the Methodology
appendix, that this split is *inferred from reasoning-chain text*, not a native
PerfectScale product field — this is the one piece of genuinely novel
interpretation logic in this skill and the one most worth a customer-facing
caveat.

### 2e. NEW — automation-benefit narrative per workload
Ground every claim in data actually present in the payload:
- `workloadAutomationStatusV2` → "currently requires a human to apply this
  change; automation would apply it on every future revision without a ticket."
- `bypass: [RA, Maturity]` → if `RA: false`, state automation is off and name
  what enabling it unlocks; if already `true`, frame as an automation win, not
  a gap.
- If the workload falls in `perfectscale-automation-blockers-v2.0`' Limited-by-User/System
  taxonomy, name the specific blocker reason using that skill's language rather
  than inventing new phrasing, and flag this workload's savings as
  **blocked-pending**, not freely achievable (feeds Step 4's rollup split).
- No unsupported MTTR/toil numbers — only claims traceable to revision
  cadence, restart counts, or OOM history actually present in the payload.

---

## Step 3 — Per-cluster InfraFit pipeline (CLI-first — reuses `infrafit-improved-v2.0`)

This step no longer needs the browser session at all when a `pscli` token is
available for the tenant — it's the biggest change from v1, which spent most
of its browser time here (Phases 1–8 of `infrafit-improved` v1: navigate,
filter Last-Seen=Current, open each node group, click Summarize, DOM-scrape
the report). `infrafit-improved-v2.0` replaced that whole mechanism with two
`pscli` calls per node group; this step just loops it across every node group
in the cluster instead of only the top one.

### 3a. Enumerate node groups (pscli, no browser)

```bash
pscli -o jsonl nodegroups list -c <cluster> --all
```

`nodegroups list` recomputes the full node-group set on every request rather
than returning a historical table, so — unlike the UI's node-group table —
there is no separate "Last-Seen=Current" filter to apply here: state this
assumption plainly in the Methodology appendix rather than asserting it as
confirmed, since it hasn't been independently verified against the UI's
Last-Seen column. If in doubt, cross-check row count against the UI table's
Current-filtered count once per run.

Skip any node group whose `id` is empty/unresolved — flag it in the report
rather than silently including or crashing on it (this was the documented
failure mode in the retired `perfectscale-infrafit-analysis-v2.0` v1 README).

### 3b. Loop over every node group — reuse `infrafit-improved-v2.0` Steps 2–5

For **each** node group from 3a, run `infrafit-improved-v2.0`'s Steps 2–5
verbatim:
- Step 2: `pscli nodegroups get -c <cluster> -g <group>` for the
  recommendation (instance type change or Karpenter NodePool diff).
- Step 3: `pscli unevictable list -c <cluster> -g <group> --all` for pods.
  Apply the same snapshot-state handling (202 = wait and retry, 422 = the one
  remaining UI fallback — tell the user to pick a different snapshot via
  InfraFit's Snapshot selector — and stop for *that node group only*, don't
  abort the whole loop).
- Steps 4–5: classify by `reason_code` (with the keyword-matching fallback),
  generate the pattern-level report content.

### 3c. Node-group cost optimization gets its own subsection

`infrafit-improved-v2.0` Step 2 already captures the recommendation (current
instance type → recommended type → savings, or the Karpenter NodePool diff).
Here, give it a dedicated **"Node Group Cost Optimization"** table per
cluster, one row per node group, separate from the unevictable-pods
remediation section.

### 3d. Autoscaler-tuning recommendations

Read `autoscaler_type` from each node group's `pscli nodegroups list`/`get`
response (`karpenter`, `cluster_autoscaler`, or another value). Branch per
`references/autoscaler-tuning.md` and tie every recommendation to a specific
finding from this cluster's own idle-cost/pattern data — never generic advice
with no number attached. If `autoscaler_type` is neither CA nor Karpenter
(cloud-managed autoscaling, e.g. GKE Autopilot/AKS node auto-provisioning, or
an unrecognized value), say tuning is provider-managed and redirect to
workload-level fixes only.

### 3e. Unevictable-pod remediation

Reuse `infrafit-improved-v2.0`'s classification taxonomy (anti-affinity,
Karpenter opt-out, mirror pod, emptyDir, hostPath, PDB, local PV, other) and
prefer each reason's own `remediation.fix_summary`/`yaml_diff` from the API;
fall back to the exact kubectl/Helm fix syntax in
`infrafit-improved-v2.0/references/analysis-reference.md` when `remediation`
is null. Scale report depth to pod count per that skill's table (≤10
pod-by-pod / 11–50 pattern+namespace / >50 pattern-only), per node group.

### Fallback — no pscli token for this tenant

If no service token authenticates, fall back to `infrafit-improved-v2.0`'s
own browser fallback isn't applicable (v2.0 has none beyond token generation
and failed snapshots) — in this no-token case, use `infrafit-improved`
**v1**'s full browser workflow (Phases 1–8) instead, looped over every
Last-Seen=Current row as v1 of this report did. Note in the Methodology
appendix that this cluster's InfraFit section came from the browser fallback,
not `pscli`.

---

## Step 4 — Write the per-cluster report (checkpoint immediately)

As soon as Steps 2 and 3 finish for a cluster, write its file — **do not hold
multiple clusters in memory waiting for a final combined write**; this is the
checkpoint that makes a mid-run session timeout cheap to recover from.

File: `tenant-savings-<tenant-slug>-<cluster>-<date>.md` in the Step 0 save
path. Structure:

```markdown
# PerfectScale Savings Report — [Cluster Name]

**Tenant:** [name] · **Cluster UID:** [uid] · **Generated:** [date]
**Node Autoscaler:** CA | Karpenter | Cloud-managed | Unrecognized (flagged)
**Analysis-only — no changes were applied while generating this report.**

## PodFit — Top [N] Workloads by Waste (30-day)

### Cost-Optimization Findings
| Workload | Namespace | Type | Current → Recommended | $ Savings/mo | Why | Confidence |

### Resiliency/Reliability Findings
| Workload | Namespace | Indicator | Why | Recommended Change | Confidence |

### Per-Workload Coaching Detail
[one card per workload: header, confidence badge, resource diff table,
 cost lens, resiliency lens (as applicable), automation-benefit narrative,
 cautions/extraction gaps]

### Cross-Workload Patterns (this cluster)
### Automation Benefit Summary (this cluster)

## InfraFit — Node Groups (pscli `nodegroups list`; browser fallback: Last-Seen=Current only)

### Node Group Cost Optimization
| Node Group | Instance Type | Recommended Type | Savings %/$ |

### Autoscaler Tuning Recommendations ([CA|Karpenter|Cloud-managed])
[branch-specific table, each row tied to a specific finding]

### Unevictable Pods — Pattern Analysis
[per Current node group: Quick Reference table, Pattern Analysis sections
 with exact fix syntax, Combined Impact & Priority — per infrafit-improved
 Phase 8]

## Cluster Reconciliation & Confidence
[B-4.5-style completeness/sanity/arithmetic checks for this cluster; overall
 cluster confidence badge = worst-of-all-findings, not an average]

## Methodology & Data Sources (this cluster)
[what came from pscli vs. browser vs. debug modal; 30-day window; Current-only
 node-group scope; lens-split-is-inferred caveat; any node group skipped/flagged]
```

---

## Step 5 — Tenant-wide landscape rollup (after all clusters finish)

Write `tenant-savings-<tenant-slug>-<date>.md`. Build every figure from the
already-written per-cluster files' structured numbers — do not recompute from
raw payloads. Structure, modeled on `automation-potential-analysis-v2.0`'s
multi-cluster rollup pattern extended with InfraFit:

```markdown
# PerfectScale Tenant Savings Report — [Tenant Name]

Generated: [date] · Clusters analyzed: [N of M total] · Top-N workloads/cluster: [N]
**Analysis-only — no changes were applied while generating this report.**

## Aggregate Opportunity
| Metric | PodFit (Right-Sizing) | InfraFit (Node/Unevictable) |
| Achievable now | $X/mo | $X/mo |
| Blocked pending remediation | $Y/mo | $Y/mo |
[Never sum "achievable now" + "blocked pending" into one headline number —
 keep them visibly separate so the story doesn't overstate what's freely
 available today. Cross-reference perfectscale-automation-blockers-v2.0' taxonomy
 for what's blocking each "blocked pending" figure.]

## Top 3 Clusters by Combined Opportunity
## Top Blocker Patterns Tenant-Wide (PodFit automation blockers + InfraFit unevictable-pod patterns)
## Tenant Reconciliation
[re-derive each headline figure as a sum of the per-cluster files' own
 bash-verified figures; flag if any total doesn't reconcile]

## Methodology & Data Sources (tenant-wide)
[cluster-enumeration path used — pscli-authoritative vs. browser-best-effort;
 clusters skipped/capped and why; per-cluster session-reuse/re-impersonation log]

## Known Limitations
- Workload analysis capped at N per cluster (extraction reliability ceiling).
- Fixed 30-day window (pscli + zoom-in sidebar convention).
- Node-group scope: `pscli nodegroups list` returns the live node-group set (unverified assumption that this matches the UI's Last-Seen="Current" filter — stated per-run in that cluster's Methodology section); the browser fallback path filters explicitly to Last-Seen="Current" and excludes decommissioned groups by design.
- Cost-lens/resiliency-lens split is inferred from reasoning-chain keywords, not a native product field.
- Cluster-enumeration completeness depends on which path (pscli vs. browser) was used this run — stated above.
- Any cluster or node group skipped this run, named explicitly.
```

---

## What not to do
- Don't click "Apply recommendation" or change any automation/policy setting — read-only, always.
- Don't sum "achievable now" and "blocked pending" savings into a single headline number.
- Don't force a workload into only one lens if it has both cost and resiliency signals — report both.
- Don't silently auto-cap cluster count above 8 — always ask.
- Don't hold all clusters in memory for one final write — checkpoint per cluster.
- Don't re-derive the InfraFit `<none>`/unrecognized node-group case as a crash — flag and skip it.
- Don't invent MTTR/toil numbers for the automation-benefit narrative that aren't traceable to the extracted payload.

## Composition — skills this orchestrates by name
- `perfectscale-cli` (`pscli`) — preferred cluster enumeration, PodFit top-N
  selection, and the entire InfraFit pipeline (Step 3), if a service token is
  available for this tenant. This is the default path in v2.0.
- `backoffice-impersonation` — PerfectScale Backoffice tenant impersonation; still mandatory for Step 2's PodFit reasoning
  extraction (no public-API equivalent for `cpuReasoning`/`memReasoning`), and
  as the no-token fallback for Step 1's cluster enumeration and Step 3's
  InfraFit pipeline.
- `perfectscale-batch-analyzer-v2.0` — PodFit top-N extraction + reasoning
  technique (Steps B-3/B-4/B-4.5 reused verbatim; B-2c's automation-status
  filter step deliberately skipped since this report covers the full
  landscape, not just blocked workloads).
- `automation-potential-analysis-v2.0` — in-page `fetch()` technique as a
  cross-check/fallback for full workload-list retrieval when `pscli`'s
  virtualized-scroll-safe list needs independent verification.
- `infrafit-improved-v2.0` — InfraFit extraction technique (Steps 2–5 reused,
  looped over every node group instead of just the top one). Falls back to
  `infrafit-improved` **v1**'s full browser workflow only when no `pscli`
  token is available for the tenant.
- `perfectscale-automation-blockers-v2.0` — blocker-reason taxonomy for the
  "blocked pending" savings split.

See `references/lens-taxonomy.md` and `references/autoscaler-tuning.md` for
the two pieces of new logic that don't come from an existing skill.
