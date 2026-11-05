# PerfectScale optimization plan (psplan)

Turns `pscli` data for a customer tenant into a sequenced, ROI-ranked optimization
plan and a one-page executive summary — both customer-shareable, both verified
before they leave your machine.

**This does not need Claude, Claude Code, or any AI tooling to run.** It is plain
Python (standard library only) and plain Node. An assistant is useful for reading
the findings and drafting the conversation around them; the numbers come from the
programs, which is the point.

---

## What you get from one run

| Artifact | Formats | Audience |
|---|---|---|
| Optimization plan | `.md`, `.html`, `.json`, `.xlsx`, `.csv` | the customer's platform / FinOps team |
| Executive summary, one page | `.docx`, `.html` | a decision-maker |
| Per-cluster findings | `.md`, one per cluster | the engineers who do the work |
| Change log against the previous run | `.md` | you, when re-running later |

The two HTML files are self-contained — no external assets, no server. Email them
and they open in any browser. The plan's HTML carries a live adoption-rate control
and per-table column controls, which makes it a working session tool rather than
a static report.

## Requirements

| Need | Check | If missing |
|---|---|---|
| `pscli`, authenticated for the tenant | `pscli auth status` | `pscli auth login --client-id … --client-secret …` (never echo the secret) |
| Python 3.10+ | `python3 -V` | — |
| Node + `docx` (for the `.docx` summary) | `node -e "require('docx')"` | `npm install -g docx`; without it the run completes and warns, and you still get the HTML |
| `openpyxl` (for `.xlsx`) | `python3 -c "import openpyxl"` | optional; CSV is always written |

## Install

Unzip it anywhere. Then either:

- **Using Claude Code?** Drop the `perfectscale-optimization-plan/` folder into
  `~/.claude/skills/` and the skill becomes available by name.
- **Not using it?** Nothing to install. Call the scripts by path; the examples
  below assume `SKILL=/path/to/perfectscale-optimization-plan`.

## Quickstart

```bash
SKILL=/path/to/perfectscale-optimization-plan

# 0. create a working folder for the tenant (copies the library, nothing else)
python3 $SKILL/scripts/new_tenant.py init --customer "Acme" --profile acme --dir ~/work/Acme_analysis
cd ~/work/Acme_analysis
export PERFECTSCALE_PROFILE=acme
pscli auth status                      # confirm it is the right tenant

# 1. rank every cluster, then agree the scope with the customer
python3 skill/perfectscale-optimization-plan/scripts/rank_clusters.py --customer "Acme" --top 10

# 2. write the config from the agreed list
python3 skill/perfectscale-optimization-plan/scripts/new_tenant.py config --dir . --top 10
#    then edit config/acme.tenant.json: gitops, stateful_patterns, held-out
#    namespaces, foreign_tenants. Leave anything you have not asked as null.

# 3. collect (resumable; re-run until it says complete)
python3 skill/perfectscale-optimization-plan/scripts/run_plan.py --config config/acme.tenant.json --collect

# 4. analyse, render, verify
python3 skill/perfectscale-optimization-plan/scripts/run_plan.py --config config/acme.tenant.json
```

A clean run ends with `verify: N passed, 0 failed` and exit 0. **If it does not,
fix the cause rather than the output** — the verifier recomputes the headline
numbers from the raw files, so a failure means the plan and the evidence disagree.

Full runbook, including re-running and diffing against a previous version, is in
`SKILL.md`.

## Why you can trust the numbers

The analysis is executed, not written by hand. Every run re-checks itself:

- Headline totals are **recomputed from the raw pscli files**, not read back from
  the model that produced them.
- The three money bases — achievable now, blocked pending, resiliency spend — are
  **never summed**, and node idle is never added to either.
- Idle is always labelled an **upper bound**, because no pool reaches zero idle.
- Everything reconciles to the workload list. No group-by rollups.
- Unconfirmed causes are labelled `inferred - cause not confirmed`.
- A leak scan fails the run on another customer's name, an absolute filesystem
  path, or filler words, in every rendered file.

Set `foreign_tenants` in each tenant config to the other customers that folder has
held. That is what lets the leak scan catch a name carried over from a previous
engagement.

## Keeping customer data where it belongs

- A tenant working folder holds `config/`, `data/` and `reports/`. **None of it
  belongs back in this library**, and `new_tenant.py init` copies only the library
  into a new folder — never another tenant's data.
- Never start a tenant by copying a previous tenant's folder.
- `scripts/package.py` rebuilds this bundle and refuses to produce one if a
  customer name, absolute path or credential is found in it.

## Layout

```
SKILL.md                  the runbook, and the rules the engine encodes
README.md                 this file
scripts/
  new_tenant.py           create a tenant folder; write its config from the ranking
  rank_clusters.py        rank every cluster before you commit to a scope
  run_plan.py             collect -> analyse -> render -> verify -> diff
  exec_summary.js         the one-page summary (.docx or .html) from the plan JSON
  psplan/                 the library
  browser/                the two things the public API cannot see (see references)
  tests/                  37 tests; run them after any change
templates/tenant.example.json
references/               methodology, voice, action catalog, ROI scoring, config keys
```

Run the tests after any change:

```bash
cd $SKILL/scripts && python3 -W ignore -m unittest discover -s tests
```

## Where to look when something fails

| Symptom | Cause / fix |
|---|---|
| `refusing to overwrite … existing outputs` | Bump `version_tag` **and** `out_dir`, or pass `--overwrite` |
| `collection incomplete` | Re-run with `--collect`; it resumes |
| Verifier fails on a money total | The plan and the raw files disagree. Fix the model, never the output |
| Verifier fails on a foreign tenant name | Copy carried over from another engagement; fix the source text |
| No `.docx` summary, only `.html` | `npm install -g docx` |
| A cluster name is ambiguous | Two live clusters share it. Address it by UID |
| A cluster reports nothing | Decommissioned or no longer reporting; valid, not an error |

`references/methodology.md` carries the field notes: how to read the cluster list,
what to check per tenant before trusting the automation total, and the tooling
traps worth knowing.
