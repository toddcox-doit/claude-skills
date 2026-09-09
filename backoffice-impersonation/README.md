# backoffice-impersonation skill

Automates PerfectScale Backoffice tenant impersonation so a support engineer can log
into a customer's PerfectScale environment without manual browser navigation.

## Prerequisites

- Chrome must be open and **already authenticated** in PerfectScale Backoffice
  (`https://backoffice.app.perfectscale.io`). The skill does not handle login. If the
  browser lands on a login page, the automation stops and asks you to authenticate first.
- The Claude-in-Chrome extension must be connected (Claude skill) or `playwright-cli`
  for the Cursor copy.

## How it works

1. You are asked for a tenant name, owner, or ID (unless already provided).
2. Automation drives the browser through Backoffice: `/tenants/` → search → select
   tenant → **Actions** → **Impersonate**.
3. A new tab opens on `app.perfectscale.io` with an **Impersonating** banner.
   Downstream skills continue from there. Session lasts ~60 minutes.

---

## Lessons from live verification (one-time test tenant; not a default)

The flow was verified once against a real tenant during skill development. That
tenant name is **not** a default — always use the skill user's `<TENANT_QUERY>`.

### 1. Tool names must use lowercase hyphens (Claude-in-Chrome)

Tools are `mcp__claude-in-chrome__*` (lowercase, hyphens). Wrong casing fails with
`tool not found`.

### 2. Tab bootstrap is two steps

Call `tabs_context_mcp` to list tabs, then `tabs_create_mcp` only if none are usable.
Do not pass `createIfEmpty`.

### 3. Dex Grant Access may appear before tenants

First navigation can hit Dex **Grant Access**. After granting, you may land on
`auth.app.perfectscale.io/tenants/` (404). Re-goto
`https://backoffice.app.perfectscale.io/tenants/`.

### 4. Search field accessible name includes ellipsis

The textbox role name is **Search by name, owner, or ID...**. After fill, matches
appear near the top of the table (exact name cell works for click).

### 5. Actions is often unlabeled in a11y snapshots

Snapshots may show an unlabeled/expanded button in the tenant header. Locate by
visible text `Actions` (`chakra-menu__menu-button`). Do not click nearby icon buttons
by coordinate guess.

### 6. Impersonate is the 5th Actions item

Verified menu order:

1. View in FrontEgg  
2. View Company in HubSpot  
3. View Deal in HubSpot  
4. View in Grafana  
5. **Impersonate**  
6. Invite User  
…

Prefer click-by-label; use position only as a fallback hint.

### 7. Success looks like a new PerfectScale tab

Impersonation opens a second tab (e.g. `/overview`) with banner:
**Impersonating** / **Viewing as support+TenantName@doit.com** and an expiry timer.

---

## Error recovery (summary)

- Login / Dex → Grant Access if needed, then re-goto Backoffice `/tenants/`.
- No search hits → shorter substring; escalate if still missing.
- Wrong tenant → return to `/tenants/` and refine search. Retry in place.
- Actions / Impersonate missing → locate by label, retry once, then escalate.

## Relationship to `frontegg-impersonation`

This skill is the **default**. `frontegg-impersonation` is the **fallback** and covers the
same goal by a longer route (`portal.frontegg.com` → Accounts → account → PerfectScale
Support user → Impersonate). Prefer this one: four browser steps instead of nine, no
textarea quirk, no 3-dot-menu collision with the help widget.

Fall back only when Backoffice genuinely cannot serve the tenant:

| Condition | Action |
|---|---|
| Login form persists after Grant Access | → Frontegg |
| Tenant absent from `/tenants/` after substring retry | → Frontegg |
| Actions menu opens with no **Impersonate** item | → Frontegg |
| Wrong tenant clicked | retry in place |
| New tab slow to open | retry in place |

The two paths are not identical in what they impersonate. Backoffice impersonates the
**tenant** — banner reads `Viewing as support+TenantName@doit.com`. Frontegg impersonates
the **PerfectScale Support user** inside the account. Both land on `app.perfectscale.io`
with a ~60-minute expiry, but a skill that asserts on exact banner text will see different
strings depending on which path ran. Downstream skills should be told which one succeeded.
