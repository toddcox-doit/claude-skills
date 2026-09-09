# Cost-Optimization vs. Resiliency/Reliability Lens Taxonomy

PerfectScale's zoom-in debug payload (the same payload `perfectscale-debug-analyzer-v2.0`
and `perfectscale-batch-analyzer-v2.0` already extract via `read_page`/React-fiber
walk) produces **one blended recommendation** per container. This skill splits
that single payload into two report lenses by tagging its existing fields and
reasoning-chain phrases against the fixed taxonomy below. **Do not invent new
phrase categories** — this list is the full inventory reused from
`perfectscale-debug-analyzer-v2.0`'s and `perfectscale-batch-analyzer-v2.0`'s own
"Reasoning chain interpretation guide" sections.

**This split is inferred by this skill, not a first-class PerfectScale product
field.** State that caveat once per report (Methodology appendix) so a
reviewer doesn't mistake it for something the product itself outputs.

## Cost-optimization lens — signals

Trigger on any of:
- Reasoning-chain phrase `wrt quantile` (initial proposal based on observed usage percentile — the core rightsizing signal)
- `below half of the limit` (request raised only to stay ≥50% of limit — still a cost-shaped adjustment, not a safety one)
- `reverting small cpu/memory increment` / `the change is lower than 5m / 5Mi` (noise-floor decisions — report only if material; usually omit from customer-facing tables)
- `request is less than 1/2 of the max cpu use` (spike-protection sizing — still cost-lens, it's about right-sizing to observed peak, not incident response)
- `proposed limit is lower than the latest limit` (PerfectScale being conservative on a decrease — note as "recommendation held, not applied")
- `do not allow decrease after decrease` / `revision is too young for decreases` (HPA/stability gating on a decrease — still cost-lens, explains why a savings recommendation hasn't landed yet)
- Any case where `recommendCpuCoresRequest/Limits` or `recommendMemMiBRequest/Limits` is **lower** than the current value with no accompanying OOM/throttle indicator

Present as: "Why we recommend lowering [resource] from X to Y: observed usage
(p90/p99/max, whichever the payload exposes) never approached the current
value; the delta represents $W/mo of headroom being paid for and unused."

## Resiliency/reliability lens — signals

Trigger on any of:
- Any `indicators` entry for OOM (out-of-memory) at any severity
- `throttling level: 1/2/3` in `cpuReasoning` (level 3 specifically implies removing the CPU limit)
- `bumping up ... due to OOM rate` (explicit OOM-history-driven increase — always resiliency-lens, always worth flagging severity)
- `making aggressive increase` (proactive safety bump)
- `non-adjustable limit` / `below minimum` (a cluster-level floor — often a LimitRange/admission-controller constraint; frame as a platform-level constraint, not a workload-specific one)
- `reduce resources because init observed` — borderline; treat as resiliency-lens only if the init-container behavior is itself a stability concern (e.g., crash-looping); otherwise cost-lens
- Active `restrictions` such as `IncreaseDisabled` — flag as "reliability recommendation automation currently cannot apply" (matters for the blocked-pending split, not just the lens split)
- `bypassing UI recommendation due to missing indicators` — not a lens signal itself; note as "no active signal to justify a change; current value held"

Present as: "Why we flag this container as at-risk: [N] OOM events over 30
days (rate: X/day); PerfectScale recommends raising [resource] to Y to
eliminate this — this is a reliability fix, not a cost saving, and may
slightly increase spend."

## Mixed-signal workloads

A single container frequently carries both a cost signal (e.g., CPU request
too high) and a resiliency signal (e.g., memory limit too low, causing OOMs)
simultaneously. **Report both lenses for that workload** — do not force a
single bucket. Label it "mixed-signal" in the coaching card so the reader
understands one workload can appear in both the Cost-Optimization Findings
table and the Resiliency/Reliability Findings table.

## Fields to surface alongside either lens (trust-building)

Regardless of lens, always show the underlying usage telemetry the
recommendation is based on when it's present in the payload (`cpuCores99p`,
`memMiB99p`, `cpuCoresMaxUse`, or equivalent), next to the recommended value —
this lets a skeptical reviewer verify PerfectScale isn't recommending below
observed peak usage, without needing to trust the recommendation blindly.
