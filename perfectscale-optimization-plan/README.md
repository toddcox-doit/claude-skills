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
| `pscli`, authenticated for the tenant | `pscli auth status` | See [Installing `pscli`](#installing-pscli) below |
| Python 3.10+ | `python3 -V` | [python.org/downloads](https://www.python.org/downloads/) |
| Node.js 22+ (only for the `.docx` summary or browser enrichment) | `node -v` | [nodejs.org](https://nodejs.org/) — 22+ is required because `scripts/browser/cdp-chrome.js` uses the native `WebSocket` global, only stable from Node 22 onward |
| `docx` npm package (for the `.docx` summary) | `node -e "require('docx')"` from `scripts/` | `cd scripts && npm install`; without it the run completes and warns, and you still get the HTML |
| `openpyxl` (for `.xlsx`) | `python3 -c "import openpyxl"` | optional: `python3 -m pip install -r scripts/requirements-optional.txt`; CSV is always written |
| Google Chrome (only for the optional PodFit eligibility browser pass) | — | macOS only today — `scripts/browser/cdp-chrome.js` hard-codes the macOS Chrome profile path; skip this entirely on Linux/Windows, the plan still runs at medium confidence without it |

Nothing above except `pscli` and Chrome is vendored in this skill or needs a
system install — Python's standard library and a local `npm install` cover the
rest. See [Tooling scrub](#tooling-scrub-what-this-skill-needs-that-it-does-not-ship) for the full accounting.

## Install

### 1. Get the code

- **Received a zip from a colleague** (built by `scripts/package.py`)? Unzip it
  anywhere.
- **Cloning the source repo?**
  ```bash
  git clone https://github.com/toddcox-doit/claude-skills.git
  cd claude-skills/perfectscale-optimization-plan
  ```

### 2. Make it available

- **Using Claude Code?** Drop (or symlink) the `perfectscale-optimization-plan/`
  folder into `~/.claude/skills/` and the skill becomes available by name:
  ```bash
  cp -R perfectscale-optimization-plan ~/.claude/skills/
  ```
- **Not using it?** Nothing to install. Call the scripts by path; the examples
  below assume `SKILL=/path/to/perfectscale-optimization-plan`.

### 3. Install `pscli` {#installing-pscli}

This is the one hard external prerequisite — a Go binary, not something this
skill can vendor or install for you.

```bash
# macOS / Linux / WSL / Git Bash
curl -fsSL https://raw.githubusercontent.com/doitintl/perfectscale-cli/main/install.sh | bash
# or, if you also have the sibling perfectscale-cli skill checked out:
bash ../perfectscale-cli/scripts/install.sh

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File ..\perfectscale-cli\scripts\install.ps1 -AddToPath
```

Both fetch the latest release for your OS/arch from
[doitintl/perfectscale-cli](https://github.com/doitintl/perfectscale-cli/releases/latest).
Then authenticate:

```bash
pscli auth login --client-id … --client-secret …   # never echo the secret
pscli auth status                                   # confirm it is the right tenant
```

### 4. Install the optional renderers

```bash
cd $SKILL/scripts
npm install                                          # for the .docx executive summary
python3 -m pip install -r requirements-optional.txt  # for the .xlsx workbook
```

Skip either and the run still completes — you get HTML instead of `.docx`, CSV
instead of `.xlsx`, and a warning telling you what was skipped.

### 5. Verify the toolchain

```bash
python3 -V          # 3.10+
node -v              # 22+ (only needed for step 4/browser enrichment)
pscli auth status    # confirms the right tenant
```

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
LICENSE                   Apache-2.0
scripts/
  package.json            npm manifest for the one runtime dep: docx
  requirements-optional.txt  pip manifest for the one optional dep: openpyxl
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

## Tooling scrub: what this skill needs that it does not ship

An honest accounting of every external dependency, found by reading every
`import`/`require` in `scripts/`, so a new user doesn't discover a gap mid-run:

| Tool | Vendored here? | Why it's missing / how it's resolved |
|---|---|---|
| `pscli` binary | **No** | External Go binary from a separate GitHub repo ([doitintl/perfectscale-cli](https://github.com/doitintl/perfectscale-cli)). The only hard prerequisite — nothing in this skill works without it. See [Installing `pscli`](#installing-pscli). |
| `docx` (npm) | **No**, but now declared | Used only by `scripts/exec_summary.js` for the `.docx` summary. Previously undeclared — no `package.json` existed, so a fresh clone had no record of the dependency or its version. Added `scripts/package.json` pinning `docx@^9`; run `npm install` in `scripts/`. |
| `openpyxl` (pip) | **No**, but now declared | Used only by `scripts/psplan/render.py` for the `.xlsx` workbook. Previously undeclared — no `requirements*.txt` existed. Added `scripts/requirements-optional.txt`. |
| Node.js 22+ | **No** (system install) | `scripts/browser/cdp-chrome.js` calls the global `WebSocket` constructor directly with no `ws` package fallback. That global is only available, unflagged, from Node 22 onward — on Node ≤21 the eligibility-enrichment path fails with `ReferenceError: WebSocket is not defined`. This minimum version was previously undocumented anywhere in the skill. |
| Google Chrome | **No** (system app) | `cdp-chrome.js` reads `~/Library/Application Support/Google/Chrome/DevToolsActivePort` — a **macOS-only** path. The optional browser eligibility pass (`references/eligibility-enrichment.md`) silently has no Linux/Windows equivalent; this was previously undocumented as a platform limitation. |
| Python 3.10+ stdlib | Fully vendored logic, system Python required | Confirmed by scanning every `scripts/**/*.py` import: nothing outside `argparse`, `json`, `csv`, `pathlib`, etc. plus the optional `openpyxl`. No stdlib surprises. |
| `LICENSE` file | **Added** | `SKILL.md` declared `license: Apache-2.0` but no `LICENSE` file existed in the folder, so a recipient of the zip bundle had no actual license text. Added `LICENSE` (Apache-2.0) and included it in `scripts/package.py`'s `SHIP` list so it's in every bundle from now on. |

Net effect of this scrub: `pscli` and Chrome are real external installs you
must do yourself (documented above); everything else now has a manifest
(`package.json`, `requirements-optional.txt`) so `npm install` / `pip install
-r` replace guesswork.

## Where to look when something fails

| Symptom | Cause / fix |
|---|---|
| `refusing to overwrite … existing outputs` | Bump `version_tag` **and** `out_dir`, or pass `--overwrite` |
| `collection incomplete` | Re-run with `--collect`; it resumes |
| Verifier fails on a money total | The plan and the raw files disagree. Fix the model, never the output |
| Verifier fails on a foreign tenant name | Copy carried over from another engagement; fix the source text |
| No `.docx` summary, only `.html` | `cd scripts && npm install` (needs Node 22+; `node -v` to check) |
| A cluster name is ambiguous | Two live clusters share it. Address it by UID |
| A cluster reports nothing | Decommissioned or no longer reporting; valid, not an error |

`references/methodology.md` carries the field notes: how to read the cluster list,
what to check per tenant before trusting the automation total, and the tooling
traps worth knowing.
