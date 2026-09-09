---
name: doit-slides
description: Dynamically generate pixel-perfect, interactive HTML slide presentations using the official DoiT corporate template. Use when the user asks to create a presentation, generate DoiT slides, or make a slide deck.
---

# DoiT Slides Skill

This skill enables AI agents (like Claude Code or Gemini CLI) to dynamically generate pixel-perfect, interactive HTML slide presentations using the official DoiT corporate template.

## Trigger
Use this skill when the user asks to:
- "Create a presentation about [Topic]"
- "Generate DoiT slides for [Topic]"
- "Make a slide deck on [Topic]"

## Instructions

1. **Understand the Request:**
   - The user will provide a topic or raw content.
   - There is no need for a questionnaire; intelligently extrapolate a structured narrative from the topic provided.

2. **Generate the Presentation Structure:**
   - Always include a **Title Slide** (Slide 1).
   - Usually include an **Agenda Slide** (Slide 2).
   - Follow with **Content Slides** combining bullet points, 2-column scenario grids, or metric highlight boxes as appropriate.
   - End with a **Thank You / Closing Slide** (Typically matching the Title slide format or a specific thank you format).

3. **Build the HTML:**
   - Use the base architecture and CSS provided in `html-template.md`.
   - Ensure the structure maintains the exact `1920x1080` scaling wrapper and intersection observer animations (`.reveal` class) to keep it perfectly aligned and interactive.
   - Adhere strictly to the **DoiT 2026 brand**:
     - **Typography:** `Georgia` (serif) for all headlines, titles, and big numbers; `Inter` for body copy, eyebrows, and labels. Give headlines a **pink italic** emphasis phrase via `<em>…</em>`.
     - **Weights — never bold:** the brand uses no bold text anywhere. Inter 400 for body/labels/eyebrows/agenda titles, Inter 500 (Medium) only for card and timeline titles, Inter 300 (Light) for cover-meta values. Never `font-weight: 600`+; emphasize with pink, not weight.
     - **Color:** brand pink `--pink` (#FC3264) for eyebrows, emphasis, numbers, icons, and lines; dark ink `--ink` (#1C1E35) and body gray `--body` (#8892A4) for text on white.
     - **Icons:** Material Symbols (the official DoiT icon set) — **never emoji**. Use `<span class="material-symbols-outlined">icon_name</span>` inside icon tiles; the font is loaded from Google Fonts in the template head.
     - **Cover and section-divider slides** use the full-bleed indigo→pink **gradient** background with white text. **All other content slides are white** with dark text — pick the `blank`, `blue-glow`, or `spark` (pink-glow) background variant per the layout cheat-sheet in `html-template.md`.
   - **The DoiT logo is baked into every background image (top-left).** Do **not** overlay a separate logo, and do **not** add a "Confidential" footer — both belonged to the retired dark-navy brand.
   - Lead every content slide with a short uppercase **eyebrow**, then a Georgia headline. Match the layout to the content using the cheat-sheet (stat columns, two-column, card grid, comparison table, timeline, media split).

4. **Speaker Notes:**
   - Add speaker notes to any slide by including a `<div class="notes">` element inside the `.slide` div.
   - Example:
     ```html
     <div class="slide slide-content-bg" id="slide3">
       <h1 class="reveal">Content Title</h1>
       <!-- content here -->
       <div class="notes">
         <p>Mention that revenue grew by 200% year over year.</p>
         <ul>
           <li>Key point 1</li>
           <li>Key point 2</li>
         </ul>
       </div>
     </div>
     ```
   - Keep the notes concise and formatted cleanly with paragraphs and bullet points.

5. **Asset Management:**
   - Every generated presentation must have an `assets/` directory accompanying the `.html` file.
   - Copy the required DoiT branding backgrounds from this skill's `assets/` directory to the new presentation's `assets/` directory (the logo is baked into each background — there are no separate logo files):
     - `bg-title.png` (gradient — Title / cover + closing slide)
     - `bg-section.png` (gradient — section-divider slides)
     - `bg-content.png` (white, blank — default content slide)
     - `bg-content-blue.png` (white with blue corner glow — data/table slides)
     - `bg-content-spark.png` (white with pink corner glow — product/media slides)

6. **Finalizing the Output:**
   - Write the generated presentation to a new directory named after the topic (e.g., `[topic-name]/index.html`).
   - Use your command tools to copy the required assets from the skill directory to the new directory.
   - Let the user know the presentation has been generated and provide the path to open it in their browser.
   - **Crucial:** Always return a quick help summary to the user at the end of the process, specifically mentioning how to use the PDF export and Presenter Mode:
     - **PDF Export:** Mention that they can export to PDF by opening the HTML in their browser and using "Print to PDF" (`Cmd+P` or `Ctrl+P`), making sure "Background graphics" is checked and "Landscape" layout is selected.
     - **Presenter Mode (Speaker Notes):** Inform the user they can open the presentation in two tabs, share one with the audience, and press the **\`S\`** key on the other tab to open the Speaker Notes panel (which automatically syncs navigation).
     - **Deep links:** The URL tracks the current slide via `?page=N` (falling back to `#page=N` for `file://`), so reloading — or sharing the URL — reopens the deck on the same slide.

## Supporting Files

- `html-template.md`: Contains the foundational HTML, CSS, JavaScript, and reference structures for all slide layouts used in the DoiT corporate template.
- `assets/`: Contains the five required background images (the DoiT logo is baked into each — there are no separate logo files).