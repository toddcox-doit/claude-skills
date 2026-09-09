#!/usr/bin/env python3
"""
PerfectScale headless batch extractor
Parallel zoom-in debug modal extraction via Playwright.

Input  (stdin):  JSON { "storage_state": {...}, "workloads": [{name, namespace, type, url}] }
Output (stdout): JSON array of extraction results

Exit codes:
  0  all workloads extracted (some may have per-workload errors)
  1  fatal: playwright not installed, bad input, etc.
"""
import asyncio
import json
import sys


# ---------------------------------------------------------------------------
# Per-workload extractor
# ---------------------------------------------------------------------------
async def extract_one(ctx, workload: dict) -> dict:
    name  = workload["name"]
    url   = workload["url"]
    page  = await ctx.new_page()
    try:
        # Navigate; wait for the React shell to hydrate
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_selector('[role="main"]', timeout=20_000)

        # Give the SPA a moment to finish rendering the zoom-in sidebar
        await page.wait_for_load_state("networkidle", timeout=15_000)

        # Ensure keyboard focus lands on the page (not the browser chrome)
        await page.click("body", timeout=5_000)

        # Open the debug modal
        await page.keyboard.press("q")

        # Wait for the dialog; retry once if the first press didn't land
        try:
            await page.wait_for_selector('[role="dialog"]', timeout=8_000)
        except Exception:
            await page.keyboard.press("q")
            await page.wait_for_selector('[role="dialog"]', timeout=8_000)

        # Wait for the JSON textbox rendered inside the dialog
        await page.wait_for_selector('[role="dialog"] [role="textbox"]', timeout=8_000)

        debug_json  = await page.inner_text('[role="dialog"] [role="textbox"]')
        dialog_text = await page.inner_text('[role="dialog"]')

        return {
            "name":        name,
            "namespace":   workload.get("namespace", ""),
            "type":        workload.get("type", ""),
            "url":         url,
            "debug_json":  debug_json,
            "dialog_text": dialog_text,
            "status":      "ok",
        }

    except Exception as exc:
        return {
            "name":        name,
            "namespace":   workload.get("namespace", ""),
            "type":        workload.get("type", ""),
            "url":         url,
            "debug_json":  None,
            "dialog_text": None,
            "status":      f"error: {exc}",
        }
    finally:
        await page.close()


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------
async def run(storage_state: dict, workloads: list) -> list:
    from playwright.async_api import async_playwright          # noqa: PLC0415

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx     = await browser.new_context(storage_state=storage_state)

        # All workloads in parallel
        results = await asyncio.gather(
            *[extract_one(ctx, w) for w in workloads],
            return_exceptions=False,
        )

        await browser.close()

    return list(results)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(json.dumps({"fatal": f"invalid JSON on stdin: {exc}"}))
        sys.exit(1)

    required = {"storage_state", "workloads"}
    missing  = required - set(payload.keys())
    if missing:
        print(json.dumps({"fatal": f"missing required keys: {missing}"}))
        sys.exit(1)

    results = asyncio.run(run(payload["storage_state"], payload["workloads"]))
    print(json.dumps(results, indent=2))
