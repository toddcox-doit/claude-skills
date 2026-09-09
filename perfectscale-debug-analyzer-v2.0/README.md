# PerfectScale Debug Analyzer

An AI-powered coaching tool for analyzing PerfectScale workload zoom-in debug
payloads. Built for the PerfectScale Field Engineering team to accelerate the
Proof of Value process by turning raw recommendation data into customer-ready
coaching narratives.

---

## What it does

Given a PerfectScale workload debug payload (the JSON behind the zoom-in view),
the analyzer produces a full coaching report covering:

- **Per-container recommendations** with current vs. recommended CPU and memory
  (color-coded diff table)
- **Reasoning chain walkthrough** — plain-English explanation of every step
  PerfectScale took to arrive at the recommendation
- **Indicator analysis** — OOM risk, CPU throttling, over/under-provisioning,
  unset requests/limits
- **Automation blockers** — exactly which CRD restrictions are preventing
  automation from acting, and why
- **Customer-ready coaching talking points** — copy-paste language for the call
- **Ordered recommended actions** — concrete next steps prioritized by impact
- **Risk flags** — what to caution the customer about before applying changes
- **Confidence badges** — 🟢 High / 🟡 Medium / 🔴 Low per container card,
  based on internal consistency checks run before the report is written

---

## Two modes

### Mode A — You already have the payload

Trigger the skill and select **"I have it ready"**. The interactive analyzer
artifact renders immediately. You can:

- **Paste** the JSON directly into the text area
- **Upload** the `.json` file by clicking or drag-and-drop

The tool calls Claude to analyze the payload and renders the full report in the
artifact UI.

### Mode B — Pull from a live customer tenant

Trigger the skill and select **"Pull from tenant"**. Claude will ask for:

1. Customer / tenant name (as it appears in PerfectScale Backoffice)
2. Cluster name
3. Workload (namespace/name, e.g. `payments/api-gateway`)

Claude then:
1. Impersonates the customer via PerfectScale Backoffice (`backoffice-impersonation`)
2. Navigates to PodFit → selects the cluster → clicks the workload
3. Presses `q` to open the debug panel
4. **Uses `read_page(filter="all")` to extract the payload** — this reaches React
   portals that `get_page_text` misses
5. Runs the payload integrity check (B-3.5) before writing anything
6. Produces the full coaching report inline — no copy-paste required

After the inline report, Claude offers to open the interactive artifact for
visual exploration or sharing.

---

## Getting the debug payload manually

If you want to grab the payload yourself without using Mode B:

1. Open PerfectScale and navigate to **PodFit** for your cluster
2. Click any workload to open its zoom-in view
3. Press **`q`** on your keyboard — the debug JSON modal opens
4. Copy the full JSON content
5. Use Mode A to paste it into the analyzer

The `q` shortcut works on any workload zoom-in page (`/pod-fit/.../zoom_in/...`).

---

## Reading the coaching report

### Confidence badges

Each container card includes a confidence badge that tells you how closely the
extracted data was verified:

| Badge | Meaning |
|-------|---------|
| 🟢 **High** | Heading match confirmed, reasoning chain terminal matches sidebar recommendations, indicator directions consistent, arithmetic verified via bash |
| 🟡 **Medium** | Minor discrepancy (waste figure within ~10%), fiber fallback used, or one integrity check flagged a non-critical issue |
| 🔴 **Low** | Heading mismatch resolved by retry, reasoning chain contradicts sidebar recommendation object, OOM indicator with decrease recommendation, or arithmetic error found |

A 🔴 Low badge means cross-check those figures against the live UI before
quoting them to the customer.

### Reasoning chain — how to interpret it

The `cpuReasoning` and `memReasoning` arrays describe a sequential pipeline.
Each step either accepts or overrides the previous proposal. The **last step**
sets the final recommendation. Compare it to the sidebar's "Recommended" value —
they should match. If they don't, the sidebar value is authoritative.

| Step phrase | What it means |
|---|---|
| `wrt quantile` | Initial proposal based on observed usage percentile |
| `below minimum` | A safety floor raised the value |
| `below half of the limit` | Request raised to stay ≥ 50% of limit (prevents bad request/limit ratios) |
| `bypass: [RA: true, Maturity: true]` | Value is locked — automation has applied this; it's a win, not a block |
| `bypass: [RA: false, ...]` | Automation is off; recommendation hasn't been accepted yet |
| `non-adjustable limit` | A cluster-level floor is preventing a lower value (often 95 MiB) |
| `throttling level: 3` | Severe CPU throttling — PerfectScale removes the CPU limit entirely |
| `making aggressive increase` | Limit bumped up proactively for safety |
| `proposed limit is lower than the latest limit` | Conservative behavior — PS won't reduce without sufficient signal |
| `reverting small cpu/memory increment` | Delta below minimum change threshold — not applied |
| `the change is lower than 5m / 5Mi` | Delta too small; current value held |
| `bumping up ... due to OOM rate` | OOM history driving a limit increase — active OOM crisis |

### Indicator/recommendation consistency rules

These are checked automatically in B-3.5. If you see a coaching card flag them,
treat the flag as a signal to investigate before the customer call:

- **OOM severity ≥ 2 + recommendation to decrease memory limit** → secondary
  safety rule or data gap; ask the PS team before telling the customer to apply
- **Throttling level 3 + non-zero CPU limit recommendation** → CRD is blocking
  limit removal; the CRD must change before the throttle resolves
- **IncreaseDisabled restriction + recommendation to increase that resource** →
  recommendation is advisory; automation cannot apply it without CRD change

### Automation blockers

When automation status shows **UserLimitReached**, look at the `restrictions`
array in each container. Common patterns:

| Restriction | Effect | Fix |
|---|---|---|
| `KeepLimitEnabled` on CPU | Prevents CPU limit removal even when throttling is critical | Update CRD to allow limit removal |
| `IncreaseDisabled` on CPU request | Caps request increase at a threshold | Raise or remove the CRD threshold |
| `IncreaseDisabled` on mem request | Prevents setting any memory request | Update CRD |
| `DecreaseDisabled` on mem limit | Prevents reducing an existing limit | Update CRD |

All of these are set in the `PerfectScaleConfig` CRD.

---

## Tips for coaching calls

- **Lead with the indicator, not the number.** "Your container is throttled 65% of
  the time" lands better than "we recommend changing CPU limit from 200m to 0."
- **Explain the bypass step.** When `RA: true, Maturity: true` appears, tell the
  customer: "PerfectScale has seen enough data and locked in this recommendation —
  it won't change unless you change the config."
- **CRD blockers are a conversation, not a bug.** Customers often set these
  deliberately (cost guardrails, namespace quotas, org policy). Ask why before
  suggesting they remove them.
- **Throttling level 3 = remove the limit.** This surprises customers. Explain
  that an uncapped CPU request with a well-set memory limit is safe — the scheduler
  won't over-schedule the node because the *request* is still set.
- **Low confidence badge = investigate before the call.** Don't quote Low-confidence
  figures without cross-checking against the live PerfectScale UI first.

---

## File structure

```
perfectscale-debug-analyzer/
├── SKILL.md          — Skill instructions and execution logic
├── README.md         — This file
└── assets/
    └── analyzer.jsx  — Full React component for the interactive artifact
```

---

## Requirements

- **Mode A**: No external connections required
- **Mode B**: Requires Claude in Chrome (connected browser) and Backoffice access
  via the `perfectscale-gameday:backoffice-impersonation` skill
  (PerfectScale Backoffice → tenants → Actions → Impersonate)
