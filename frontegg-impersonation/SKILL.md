---
name: frontegg-impersonation
description: >
  Fallback impersonation path — automates the Frontegg portal impersonation workflow for
  PerfectScale support access. Prefer `backoffice-impersonation` for routine impersonation;
  it is fewer steps and more reliable. Use this skill only when the user explicitly asks for
  Frontegg ("frontegg impersonation", "impersonate via frontegg", "use the frontegg portal"),
  or when `backoffice-impersonation` has already failed because Backoffice is unreachable,
  the tenant is absent from the Backoffice tenants list, or the Actions menu offers no
  Impersonate item. Do not select this skill for a bare "impersonate [company]" request —
  that routes to `backoffice-impersonation`.
---

# Frontegg Impersonation Workflow

This skill automates logging into a customer account via Frontegg's impersonation feature.
It always runs the browser automation in Haiku (fastest model) and always prompts for the
account name before proceeding.

> **This is the fallback path.** `backoffice-impersonation` is the default: it reaches an
> equivalent session in four browser steps against `backoffice.app.perfectscale.io` instead
> of the nine-step portal flow below. Come here when Backoffice is unavailable or the tenant
> cannot be found there — or when the user names Frontegg explicitly.
>
> The two paths differ in what they impersonate. Backoffice impersonates the **tenant**
> (banner reads `Viewing as support+TenantName@doit.com`); Frontegg impersonates the
> **PerfectScale Support user** within the account. Both yield a ~60-minute session on
> `app.perfectscale.io`.

## Step 1: Prompt for account name

Before doing anything in the browser, use `AskUserQuestion` to ask the user which account
to search for. The question should be clear and simple — they just need to type the account
name (or partial name) that appears in Frontegg's accounts list.

## Step 2: Delegate to Haiku

Once you have the account name, spawn an Agent with `model: "haiku"` and pass it:
- The account name to search for
- The full browser automation instructions below

Use this exact pattern:

```
Agent({
  model: "haiku",
  description: "Frontegg impersonation for [account name]",
  prompt: "<full instructions from Step 3 below, with account name filled in>"
})
```

## Step 3: Haiku agent instructions

The Haiku agent should follow these steps precisely. Each step includes the lesson learned
from real usage — understanding the *why* helps recover gracefully if something looks different.

### 3a. Get a browser tab

1. Call `mcp__claude-in-chrome__tabs_context_mcp` to list existing tabs.
2. If no usable tab exists, call `mcp__claude-in-chrome__tabs_create_mcp`.
3. Use the resulting tab ID for every subsequent browser call.

Do **not** pass `createIfEmpty` — that parameter is not supported.

### 3b. Navigate to Frontegg

Navigate to `https://portal.frontegg.com/production/home` and wait ~3 seconds for the
dashboard to fully render. Take a screenshot to confirm it loaded (you should see the
Frontegg sidebar with Home, Accounts, Users).

### 3c. Click Accounts

Click on **Accounts** in the left sidebar. The URL should change to
`/production/backoffice/accounts`. Wait ~2 seconds and take a screenshot to confirm the
accounts list loaded with a search bar.

### 3d. Search for the account

Click in the search bar and type the account name. Wait ~2 seconds for results to filter.
There should be one or a small number of matching rows. Take a screenshot to confirm.

### 3e. Click the account row

Click on the account name in the results table. The URL will change to
`/production/backoffice/accounts/<uuid>`. Wait ~2 seconds for the account detail page
to load — you should see a Users tab with a list of users including "PerfectScale Support".

### 3f. Scroll to find PerfectScale Support

The PerfectScale Support user is often at the bottom of the users list. Scroll down in the
page to make sure it's fully visible before attempting to click anything on that row.
Take a screenshot to confirm you can see the full row including the 3-dot menu icon on the
right side.

### 3g. Click the 3-dot menu on the PerfectScale Support row

**Important:** Do NOT guess coordinates for this — the layout varies by screen size and
can accidentally hit the help widget button in the top-right corner. Instead:

1. Use `mcp__claude-in-chrome__find` with query `"3 dot menu button on PerfectScale Support row"`
   to get a ref ID for the menu button on that specific row.
2. Click it using `mcp__claude-in-chrome__computer` with `action: "left_click"` and `ref: <ref_id>`.

If the find returns multiple results, pick the one associated with the PerfectScale Support row.
Take a screenshot to confirm the dropdown appeared with "Copy user ID" and "Impersonate user" options.

### 3h. Click "Impersonate user"

Use `mcp__claude-in-chrome__find` with query `"Impersonate user menu item"` to get the ref,
then click it. The Impersonation session dialog should appear with an Application/agent
dropdown (pre-filled with "PerfectScale") and a Reason textarea.

### 3i. Fill in the reason

**Critical:** Do NOT use click-then-type for the reason field. The textarea requires
`form_input` to reliably set its value. Follow these steps:

1. Use `mcp__claude-in-chrome__find` with query `"Reason textarea impersonation"` to get
   the textarea ref.
2. Use `mcp__claude-in-chrome__form_input` with `ref: <ref_id>` and
   `value: "logged in by claude for automation"`.
3. Take a screenshot to confirm the text appears in the field and the validation error
   (if any) has cleared.

### 3j. Click "Start impersonation session"

Use `mcp__claude-in-chrome__find` with query `"Start impersonation session button"` to get
the ref, then click it using `mcp__claude-in-chrome__computer` with `action: "left_click"`.

Wait ~3 seconds. The dialog should close and a new tab will open with the customer's
PerfectScale app. Take a final screenshot of the Frontegg tab to confirm the dialog closed
successfully.

### 3k. Confirm success

Report back: which account was accessed, that the impersonation session started successfully,
and that a new PerfectScale tab has opened. Session lasts 60 minutes.

## Error recovery

- **Help widget opened instead of 3-dot menu**: Close it (click the X), scroll to make
  PerfectScale Support fully visible, then use `find` to locate the correct menu button.
- **"Impersonation reason is required" error**: The `type` approach failed. Use `form_input`
  with the textarea ref instead.
- **Account not found**: The search is case-sensitive in some cases. Try a shorter substring
  of the account name. Report back to the user if still not found.
- **Dialog stuck on loading**: Wait 5 more seconds. If still stuck, cancel and retry from
  step 3g.
