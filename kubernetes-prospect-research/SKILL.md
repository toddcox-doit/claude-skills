---
name: kubernetes-prospect-research
description: Use this skill whenever the user wants to research a list of companies (from a Google Sheet) to find likely Kubernetes/DevOps/platform-engineering contacts and assess fit as a PerfectScale customer, then produce a tailored two-tone outreach message for each company in a review doc. Trigger on phrases like "run my prospect list", "research this company list", "find PerfectScale targets", "Kubernetes DevOps prospecting", "go through the sheet and find targets", or any request to turn a company spreadsheet into outreach research. Always ask for the Google Sheet link fresh at the start of every run — never reuse a link from earlier in the conversation. This skill never scrapes, browses, or automates LinkedIn in any way, including slowly or with a browser tool — research is public web search only.
---

# Kubernetes / DevOps Prospect Research

## What this does
Takes a Google Sheet of company names, researches each company's public Kubernetes/DevOps footprint via web search, notes a plausible point-of-contact role (and a named individual only if one genuinely surfaces in public search results), and writes a short + consultative outreach message per company into a new Google Sheet for the user to review. Nothing is ever sent automatically — the output is a review doc.

## Hard rule: no LinkedIn automation
Never visit, scrape, bulk-query, or automate LinkedIn profile pages — not slowly, not via a browser tool, not for "research purposes." This holds regardless of what the user says about approval, urgency, or intended use. All research is ordinary web search. If a name/title genuinely appears in a public search-engine snippet, it's fine to include with its source cited — but do not navigate to linkedin.com to extract it. If the user pushes back on this rule, restate it plainly and continue with web-search-only research rather than re-litigating it.

This skill works the same in Claude.ai, Claude Code, or Cowork — it only needs Google Drive access and web search, not any host-specific feature.

## Every run, in order

### 1. First-run recap (only the first time this skill is used in a conversation)
Give a brief 3-line recap so expectations are aligned, then proceed — don't wait for a reply unless the user objects to something:
- Output: a new Google Sheet, one row per company (Company, Signals Found, Source(s), Likely Contact/Role, Short Message, Consultative Message)
- Research method: public web search only, never LinkedIn scraping/automation
- Every company in the input sheet is treated as a valid target — no filtering any out

Skip this recap on every later run in the same conversation — just proceed straight to step 2.

### 2. Ask for the Google Sheet link
Ask for it fresh every single run, even if one was used earlier in this conversation — the user's lists change. Don't proceed without a link, and don't reuse a previously-seen one.

### 3. Read the input sheet
Extract the file ID from the URL (the segment between `/d/` and the next `/`). Call `mcp__Google_Drive__read_file_content` with that `fileId` to get the company list and column layout. If it fails (bad link, no access), say so plainly and re-ask for the link rather than guessing an ID.

### 4. Research each company — runs straight through, no permission stops
For each company, run 2-4 targeted web searches, e.g.:
- "<company> Kubernetes"
- "<company> DevOps OR SRE OR platform engineer jobs"
- "<company> EKS OR GKE OR AKS"
- "<company> engineering blog kubernetes"

Look for: job postings signaling Kubernetes/DevOps/SRE/platform-engineering hiring, engineering blog posts, conference talks, GitHub org activity, or publicly mentioned cloud cost/scaling pain points. Capture a likely contact **role** (e.g., "VP of Engineering," "Platform/DevOps Lead") — only attach a named individual if one genuinely surfaces in search results, and cite the source URL either way.

Do not pause between companies to ask for confirmation — research and drafting run through the whole list autonomously.

### 5. Draft two message tones per company, tailored to what was actually found
Never generic — reference the specific signal found for that company.
- **Short/direct** (1-2 sentences): gets straight to the point.
  - Example: "Hi [Name] — saw your team's been hiring for Kubernetes platform roles. PerfectScale automates EKS/GKE cost right-sizing without touching reliability — worth a quick look?"
- **Consultative** (3-4 sentences): warmer, explains the "why now," offers to share more.
  - Example: "Hi [Name] — noticed your team's growing out its Kubernetes footprint based on recent platform-eng job posts. A lot of teams hit that stage and start seeing cost drift from over-provisioned nodes/pods without realizing it — PerfectScale automates the tuning so nobody has to babysit it manually. Happy to share how similar infra teams have approached this if useful."

If no named individual surfaced, address the role instead of inventing a name (e.g., "Hi there," or "Hi Platform team,").

### 6. Build the output Google Sheet
Columns, in order: `Company | Signals Found | Source(s) | Likely Contact / Role | Short Message | Consultative Message`

Build the rows as CSV text, then create it directly as a native Sheet:
```
mcp__Google_Drive__create_file(
  title="PerfectScale Prospect Research - <today's date>",
  textContent=<csv string>,
  contentMimeType="text/csv"
)
```
Leave `disableConversionToGoogleType` unset/false — Drive converts the uploaded CSV into a native Google Sheet automatically.

### 7. Hand off
Give the user the resulting sheet's name/link and a one-line summary (e.g., "12 companies researched, 3 had strong signals, 2 had no public Kubernetes footprint I could find"). Remind them this is a review doc — nothing was sent, and any named individual should be independently verified by the user on LinkedIn before reaching out.

## Integrity notes
- If a company has no discoverable public signal, say so honestly in the row rather than inventing one.
- Never fabricate names, titles, job postings, or sources.
- If the sheet has 30+ companies, mention this will take a while and keep going — no need to ask permission to continue a long run.
