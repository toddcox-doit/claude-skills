# Eligibility enrichment (optional)

The public API cannot say whether automation is available per workload. A PodFit
browser pass can export per-workload eligibility; save as JSON and point
`eligibility_file` at it. When present, controller potential is restricted to
eligible workloads and automation confidence rises from medium to high. Without
it, the plan states eligibility as unconfirmed.

## Producing the file

`scripts/browser/` holds the only tooling that can read eligibility, which lives
in the PodFit UI and has no public-API equivalent. These came from the retired
`perfectscale-executive-1pager` skill (see `~/.claude/skills-archive/ARCHIVE.md`)
and are kept here because this library's `eligibility_file` is their only
remaining consumer.

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
node scripts/browser/cdp-chrome.js --customer "<Name>" eval-file /tmp/podfit-inpage.js
```

Eligible = `NotEnabled` + `DisabledByUser`. `NotSupported` stays out of the
automation case. DaemonSets may read as eligible in the API; keep them out of the
automation ask regardless.

Chrome 144+ often breaks `playwright-cli attach --cdp`; prefer the bundled
`cdp-chrome.js` helper above. Keep the browser method out of customer-facing
output.
