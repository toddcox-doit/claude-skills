# frontegg-impersonation skill

Automates the Frontegg portal impersonation workflow so a support engineer can log
into a customer's PerfectScale environment without manual browser navigation.

## Prerequisites

- Chrome must be open and **already authenticated** in the Frontegg portal
  (`https://portal.frontegg.com/production/home`). The skill does not handle login. If the
  browser lands on a login page, the automation stops and asks you to authenticate first.
- The Claude-in-Chrome extension must be connected.

## How it works

1. You are asked for an account name (or partial name).
2. A Haiku subagent takes over and drives Chrome through the full Frontegg portal flow.
3. A new tab opens with the customer's PerfectScale environment. Session lasts 60 minutes.

---

## Navigation issues and lessons learned

These are specific problems observed during real runs, with the fixes currently in place.

### 1. Tool name casing causes silent failures (fixed in SKILL.md)

The MCP server registers these tools as `mcp__claude-in-chrome__*` (lowercase,
hyphens). SKILL.md previously wrote them as `mcp__Claude_in_Chrome__*`, so any agent
copying the names verbatim got `tool not found` on every call.

**Fixed:** SKILL.md now uses the correct lowercase-hyphen form throughout.

### 2. `tabs_context_mcp` does not accept `createIfEmpty: true`

That parameter does not exist on the tool. Passing it has no effect at best, or causes
a validation error at worst.

**Fixed:** step 3a now uses the two-step pattern — call `tabs_context_mcp` to list
existing tabs, then `tabs_create_mcp` only if none are usable.

### 3. 3-dot menu vs help widget collision

The help widget button (bottom-right or top-right, depending on screen size) sits close to
the 3-dot action menu on the PerfectScale Support user row. Clicking by coordinates
reliably hits the wrong target.

**Fix in place:** the skill uses `find` with the query
`"3 dot menu button on PerfectScale Support row"` to get an element ref, then clicks by
ref. This avoids coordinate guessing entirely.

### 4. Textarea value not accepted via keyboard input

The "Reason" field in the impersonation dialog silently rejects text typed via the browser
`type` action — the button stays disabled and a validation error remains.

**Fix in place:** the skill uses `form_input` with the element ref to set the textarea
value programmatically. This clears the validation error and enables the submit button.

### 5. PerfectScale Support user scrolled out of view

On accounts with many users, the PerfectScale Support row is below the fold. Attempting
to `find` or click the 3-dot menu before scrolling returns stale or incorrect refs.

**Fix in place:** step 3f explicitly scrolls to the bottom of the users list and takes a
screenshot to confirm the full row (including the 3-dot icon) is visible before proceeding.

### 6. Account search is partially case-sensitive

Searching for "Northwind" vs "northwind" can produce different result counts depending on the
Frontegg index. Shorter substrings ("northwind" not "northwind logistics") are more reliable.

**Error recovery documented in SKILL.md:** retry with a shorter substring if the account
is not found on the first attempt.

---

## Known remaining issues

- The impersonation reason is hardcoded to `"logged in by claude for automation"`. If your
  team's Frontegg audit policy requires per-customer or per-engineer labels, this needs to
  be made configurable.
