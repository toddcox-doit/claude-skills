# Eligibility enrichment (optional)

**First choice: the public API v2.** `pscli` reads the public v1 API only, which
does not expose per-workload automation status or HPA triggers. The public v2
workloads endpoint does: `automation.status` (the same `NotEnabled` /
`DisabledByUser` / `NotSupported` values the browser pass reads) and
`scaling.hpaTargets` (the HPA's trigger metrics and targets). When API
credentials are available the collector pulls it for every cluster
(`workloads-v2.json`, see `psplan/apiv2.py`), and the model uses it as the
eligibility source whenever no `eligibility_file` is set. Store credentials once
with `bash scripts/set_api_credentials.sh <profile>` (owner-only
`~/.perfectscale/<profile>.env`), or export `PERFECTSCALE_CLIENT_ID` /
`PERFECTSCALE_CLIENT_SECRET`. The token is the same Read Only service token
`pscli` uses. v2 has no HPA min/max replicas, so a pinned HPA (min == max) looks
like any CPU/memory HPA there.

The browser pass below remains the fallback when no API token is available. A
PodFit browser pass can export per-workload eligibility; save as JSON and point
`eligibility_file` at it. When present, controller potential is restricted to
eligible workloads and automation confidence rises from medium to high. Without
it, the plan states eligibility as unconfirmed.

## HPA triggers (optional, same pass)

The public API v2 now carries HPA triggers (see above), and that is the path
the kickoff page's waves use. The notes below cover the older browser route.
PerfectScale's HPA Triggers view (per-container CPU/Memory HPA trigger
detection, replica scaling history) lives in PodFit's workload zoom-in window;
`pscli` (v1) has no HPA field anywhere. Point `hpa_file` at a JSON export the same shape as `eligibility_file`
(`{cluster uid or name: {workload id: {...}}}`); when present, a right-sizing
action on a matching workload's evidence gets an appended
"HPA-driven: check trigger thresholds before applying" caveat — lowering a
request changes the utilization percentage HPA scales on. Without it, HPA
status is unconfirmed and the plan says so (see `references/methodology.md`).

`scripts/browser/podfit-inpage.js` carries a `CAPTURE_HPA` flag (default
`false`) and a `captureHpaTriggers` stub for this, deliberately **not wired to
a confirmed endpoint** — unlike eligibility above, no browser session against a
real tenant was available to find the actual request the zoom-in modal's HPA
Triggers section makes. Before relying on it: open a workload's zoom-in window
with HPA Triggers visible, find the real request in DevTools Network, and
replace `HPA_ENDPOINT` and the field names in `captureHpaTriggers` with the
confirmed ones, then flip `CAPTURE_HPA` to `true`.

## Producing the file

`scripts/browser/` holds the only tooling that can read eligibility, which lives
in the PodFit UI and has no public-API equivalent. These came from the retired
`perfectscale-executive-1pager` skill (see `~/.claude/skills-archive/ARCHIVE.md`)
and are kept here because this library's `eligibility_file` (and, once
`captureHpaTriggers`'s endpoint is confirmed, `hpa_file`) are their only
remaining consumers.

Chrome must already be running with remote debugging so
`~/Library/Application Support/Google/Chrome/DevToolsActivePort` exists, and the
session must be on the target tenant.

```bash
# 1. confirm the tab is on the right tenant
node scripts/browser/cdp-chrome.js --customer "<Name>" page-text

# 2. optional: Overview KPIs and the cluster table
node scripts/browser/cdp-chrome.js --customer "<Name>" navigate https://app.perfectscale.io/overview
node scripts/browser/cdp-chrome.js --customer "<Name>" eval-file scripts/browser/overview-inpage.js

# 3. per-workload eligibility -> JSON for eligibility_file
#    copy podfit-inpage.js first and set CLUSTERS (name + uid) and EXCLUDE at the top
#    (optional: flip CAPTURE_HPA on, once its endpoint stub is confirmed, for hpa_file too)
node scripts/browser/cdp-chrome.js --customer "<Name>" eval-file /tmp/podfit-inpage.js
```

Eligible = `NotEnabled` + `DisabledByUser`. `NotSupported` stays out of the
automation case. DaemonSets may read as eligible in the API; keep them out of the
automation ask regardless.

Chrome 144+ often breaks `playwright-cli attach --cdp`; prefer the bundled
`cdp-chrome.js` helper above. Keep the browser method out of customer-facing
output.
