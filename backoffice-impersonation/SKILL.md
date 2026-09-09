---
name: backoffice-impersonation
description: >
  Default impersonation path — automates PerfectScale Backoffice tenant impersonation for
  support access. Use this skill whenever the user wants to impersonate a customer tenant,
  log into a customer account, start a support impersonation session, access a customer's
  PerfectScale environment, or perform "log in as customer" actions. Triggers on
  phrases like: "impersonate", "log into customer account", "backoffice impersonation",
  "support session for [account]", "access [company] in backoffice", "impersonate tenant".
  Always use this skill for any PerfectScale customer tenant impersonation — even if the
  user just names a company without explicitly saying "impersonate". Falls back to
  `frontegg-impersonation` only if Backoffice is unreachable, the tenant is missing from
  the Backoffice index, or the Actions menu offers no Impersonate item.
---

# PerfectScale Backoffice Impersonation Workflow

This skill automates logging into a customer tenant via PerfectScale Backoffice
impersonation. It always runs the browser automation in Haiku (fastest model).

**This is the default impersonation path.** It reaches a session in four browser steps
(`/tenants/` → search → tenant → Actions → Impersonate). `frontegg-impersonation` is the
fallback — a nine-step portal flow — and should only be reached via Step 4 below, or when
the user asks for Frontegg by name.

**Tenant selection is mandatory and never hard-coded.** Do not default to Finom,
Demo-saas, Latest-prod, or any other sample tenant from docs or prior runs. Use only
the tenant the skill user provides for this run.

## Preconditions

Chrome must be open and **already authenticated** at
`https://backoffice.app.perfectscale.io`. This skill does not handle login. If the
browser lands on a login page, stop and ask the user to authenticate first.

## Step 1: Resolve tenant (required before any browser work)

1. If the user already named a tenant (name, owner, or ID) in this request, use that
   exact value as `<TENANT_QUERY>`.
2. If they did **not**, stop and prompt with `AskUserQuestion` (or a clear chat
   question): which tenant to search for (name, owner, or ID). **Do not navigate,
   search, or impersonate until they answer.**
3. Never invent, reuse, or fall back to a tenant from conversation history, README
   examples, or previous impersonation sessions.

## Step 2: Delegate to Haiku

Once `<TENANT_QUERY>` is known, spawn an Agent with `model: "haiku"` and pass it:
- The exact `<TENANT_QUERY>` to search for
- The full browser automation instructions below (with that value substituted)

Use this exact pattern:

```
Agent({
  model: "haiku",
  description: "Backoffice impersonation for <TENANT_QUERY>",
  prompt: "<full instructions from Step 3 below, with <TENANT_QUERY> filled in>"
})
```

## Step 3: Haiku agent instructions

The Haiku agent should follow these steps precisely. Use tool names exactly as
`mcp__claude-in-chrome__*` (lowercase, hyphens).

### 3a. Get a browser tab

1. Call `mcp__claude-in-chrome__tabs_context_mcp` to list existing tabs.
2. If no usable tab exists, call `mcp__claude-in-chrome__tabs_create_mcp`.
3. Use the resulting tab ID for every subsequent browser call.

Do **not** pass `createIfEmpty` — that parameter is not supported.

### 3b. Navigate to Backoffice tenants

Navigate to `https://backoffice.app.perfectscale.io/tenants/` and wait ~3 seconds
for the page to render. Take a screenshot to confirm it loaded.

- Success: page title **PerfectScale Backoffice**, Active Tenants tabs, and a textbox
  named **Search by name, owner, or ID...** (ellipsis in the accessible name).
- Dex **Grant Access** consent may appear first — click **Grant Access**, then
  navigate again to `/tenants/` if you land on a 404 (e.g. `auth.app.perfectscale.io`).
- If you see a login form instead, **stop** and report that the user must log into
  Backoffice first.

### 3c. Search for the tenant

1. Click/fill the upper-left search textbox ("Search by name, owner, or ID...").
2. Type exactly `<TENANT_QUERY>` (the value from Step 1 — not a hard-coded example).
3. Wait ~2 seconds for results to filter.
4. Take a screenshot to confirm matching tenants appear near the top of the list.

### 3d. Click the tenant

Click the matching tenant name cell/row near the top of the filtered list (prefer
the closest name match). Wait ~2 seconds for the tenant detail page to load
(`https://backoffice.app.perfectscale.io/tenants/<uuid>`). Take a screenshot —
you should see the tenant heading and header action buttons.

### 3e. Open Actions and Impersonate

**Important:** accessibility snapshots often show the Actions control as an
unlabeled button in the tenant header (upper right). Prefer locating by visible
text `"Actions"` (DOM `innerText` / `chakra-menu__menu-button`), not by guessing
coordinates among the icon buttons.

1. Click the **Actions** menu button.
2. Confirm the menu opened. Expected items (order verified live):
   1. View in FrontEgg
   2. View Company in HubSpot
   3. View Deal in HubSpot
   4. View in Grafana
   5. **Impersonate** ← target
   6. Invite User
   …
3. Click **Impersonate** by label (preferred). Position hint: **5th** menu item.
4. Wait ~3–5 seconds. A **new tab** should open on `https://app.perfectscale.io/...`
   (often `/overview`). Switch to that tab.

### 3f. Confirm success

On the PerfectScale tab, confirm the impersonation banner, e.g.
**Impersonating** / **Viewing as support+TenantName@doit.com**, tenant name in the
UI, and a ~60 minute expiry timer. Report success so downstream skills can continue.

## Error recovery

Recover in place first. Only the three cases marked **→ fallback** mean Backoffice
cannot serve this tenant at all; everything else is a retry.

- **Login wall / Dex consent**: Complete Grant Access if shown, then re-goto
  `https://backoffice.app.perfectscale.io/tenants/`. If you still land on a login
  form after that, Backoffice is unreachable for this user. **→ fallback (Step 4)**
- **404 after OAuth**: Re-navigate to the Backoffice `/tenants/` URL (do not stay on
  `auth.app.perfectscale.io`). Retry in place.
- **Tenant not found**: Retry with a shorter substring of the name. If a two- or
  three-character substring still returns nothing, the tenant is not in the Backoffice
  index. **→ fallback (Step 4)**
- **Wrong tenant selected**: Go back to `/tenants/`, clear/refine the search, and
  select the correct row. Retry in place — do **not** fall back; the tenant is present.
- **Actions menu missing / unlabeled in snapshot**: Find the button whose text is
  exactly `Actions` (menu-button class). Wait 2–3s and retry once if needed.
- **Impersonate not in menu**: If the Actions menu opens but has no **Impersonate**
  item, this account lacks Backoffice impersonation permission. Report the menu items
  you see. **→ fallback (Step 4)**
- **Session did not open**: Wait 5 more seconds, check `tab-list` / new tabs for
  `app.perfectscale.io`, and retry from 3e once. Transient — do **not** fall back.

## Step 4: Fallback to Frontegg

Only on a **→ fallback** condition above. Do not attempt this for a retryable error,
and do not attempt it more than once per run.

1. Tell the user, in one line, which Backoffice condition you hit and that you are
   switching paths — e.g. *"`<TENANT_QUERY>` is not in the Backoffice tenants index;
   falling back to Frontegg portal impersonation."* They should never discover the
   path switch only by noticing a different banner.
2. Invoke the `frontegg-impersonation` skill, passing the same `<TENANT_QUERY>` as the
   account name. That skill drives `portal.frontegg.com` and impersonates the
   **PerfectScale Support user** within the account.
3. If Frontegg also fails, stop. Report both failures — the Backoffice condition and
   the Frontegg one — so the user can tell an access problem from a missing tenant.

**What downstream skills should know:** the fallback produces a session on
`app.perfectscale.io` with the same ~60-minute expiry, but the banner reads as the
impersonated support user rather than `support+TenantName@doit.com`. Report which path
succeeded so any skill that asserts on banner text knows what to expect.
