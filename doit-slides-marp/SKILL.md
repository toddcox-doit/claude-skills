---
name: doit-slides-marp
description: Generate MARP markdown slide presentations from any branded template. Outputs a single .md file renderable by the MARP CLI or VS Code MARP extension. Supports the same template system as doit-slides — select by name or path, with the DoiT template as the default.
---

# MARP Slides Skill

Generate slide presentations as MARP markdown (`.md`) using branded templates. The output is a single file renderable natively by the MARP CLI or the MARP VS Code extension — no build step, no server required.

## Trigger

Use this skill when the user asks to:
- "Create a MARP presentation about [Topic]"
- "Generate MARP slides for [Topic]"
- "Make a MARP deck on [Topic]"
- "Create slides using the [template-name] template" (when MARP context is clear)
- "Convert this content to a MARP presentation"

## Template Resolution

Templates are self-contained folders with brand assets and a MARP boilerplate. Resolution order:

1. **Explicit template name:** User says "using the doit template" → look for `templates/<template-name>/` in this skill directory.
2. **Explicit path:** User says "using the template in /path/to/folder" → use that folder directly.
3. **Default:** If nothing is specified or the template isn't found, use `templates/doit/`.

### Template Folder Structure

```
my-template/
├── template.md          # Brand manifest: colors, fonts, assets, slide types
├── marp-template.md     # MARP-specific boilerplate, CSS, layout patterns
└── assets/              # Background images and logos
    ├── bg-title.png
    ├── bg-content.png
    ├── logo-large.png
    └── logo-small.png
```

### Reading a Template

1. Read `template.md` — brand colors, fonts, required footer text, available assets.
2. Read `marp-template.md` — full MARP front matter, CSS block, and layout pattern reference.
3. Copy `assets/` to the output presentation's `assets/` directory.

## Instructions

### 1. Resolve the Template

Determine which template to use. Read `template.md` and `marp-template.md` from the resolved folder.

### 2. Understand the Request

The user provides a topic or raw content. Intelligently extrapolate a structured narrative — do not ask what format they want. Always output a MARP `.md` file.

### 3. Generate the Presentation Structure

Every presentation must follow this slide order:
- **Slide 1:** Title slide
- **Slide 2:** Agenda slide (usually)
- **Slides 3–N:** Content slides
- **Last slide:** Closing / Thank You slide

### 4. Build the MARP Markdown

Use the CSS and layout patterns from `marp-template.md`. Follow these rules strictly:

#### Front Matter (required on every presentation)
```yaml
---
marp: true
html: true
theme: default
paginate: false
backgroundColor: "#000433"
color: "#FFFFFF"
style: |
  [paste full CSS block from marp-template.md here]
---
```

**`html: true` is mandatory.** Without it, all `<div>`, `<img>`, and `<svg>` elements are stripped silently — charts, logos, and layout components will not render.

#### Slide Separator and Directives

Each slide starts with `---` followed by per-slide directives:

```markdown
---

<!-- _class: title -->
<!-- _backgroundImage: url('assets/bg-title.png') -->

[slide content]
```

**Always use `_backgroundImage` per slide** — never set `background-image` in CSS on section classes. MARP ignores CSS-based background images on sections.

#### Slide Classes

- `<!-- _class: title -->` + `<!-- _backgroundImage: url('assets/bg-title.png') -->` — for Title and Closing slides
- `<!-- _class: content -->` + `<!-- _backgroundImage: url('assets/bg-content.png') -->` — for all other slides

#### Title Slide Layout

```markdown
<!-- _class: title -->
<!-- _backgroundImage: url('assets/bg-title.png') -->

<img src="assets/logo-large.png" alt="DoiT" class="logo">

# Presentation Title Here

Subtitle or date line here
```

#### Content Slide Layout

```markdown
<!-- _class: content -->
<!-- _backgroundImage: url('assets/bg-content.png') -->

# Slide Heading

<p class="intro">Optional intro sentence in muted color.</p>

[content using layout components — see available patterns below]

<div class="footer"><span>Confidential</span><img src="assets/logo-small.png" alt="Logo"></div>
```

#### Closing Slide Layout

Same as title slide:
```markdown
<!-- _class: title -->
<!-- _backgroundImage: url('assets/bg-title.png') -->

<img src="assets/logo-large.png" alt="DoiT" class="logo">

# Thank You

Closing line here.
```

#### Speaker Notes

Add speaker notes inside MARP HTML comment blocks at the end of each slide, before the next `---`:

```markdown
<!--
Your speaker notes here. Supports paragraphs and lists.
-->
```

### 5. Available Layout Components

Use these HTML/div patterns within slides. All CSS for these is included in the `marp-template.md` style block.

#### Metrics Grid (2×2 stat cards)
```html
<div class="metrics-grid">
  <div class="metric-item">
    <div class="metric-value">48</div>
    <div class="metric-desc">Label</div>
    <div class="metric-sub">Supporting detail</div>
  </div>
</div>
```

#### Three-Column Layout
```html
<div class="three-col">
  <div class="col-box"><h3>Heading</h3><ul><li>Point</li></ul></div>
  <div class="col-box"><h3>Heading</h3><ul><li>Point</li></ul></div>
  <div class="col-box"><h3>Heading</h3><ul><li>Point</li></ul></div>
</div>
```

#### Two-Column Layout
```html
<div class="two-col">
  <div class="two-col-box"><h3>Left</h3><ul><li>Point</li></ul></div>
  <div class="two-col-box"><h3>Right</h3><ul><li>Point</li></ul></div>
</div>
```

#### Four-Column Component Cards
```html
<div class="four-col">
  <div class="comp-card"><div class="comp-card-icon">📋</div><h4>Title</h4><p>Description.</p></div>
</div>
```

#### Agenda Rows
```html
<div class="agenda-table">
  <div class="agenda-row"><div>Topic Name</div><div class="agenda-num">01</div></div>
</div>
```

#### Callout Box
```html
<div class="callout">⚠️ Your callout message here.</div>
```

#### Quote Grid
```html
<div class="quote-grid">
  <div class="quote-card">
    <p>Quote text here.</p>
    <span class="quote-role">Role / Context</span>
  </div>
</div>
```

#### Donut Chart (SVG)
```html
<div class="donut-container">
  <svg width="240" height="240" viewBox="0 0 340 340">
    <circle cx="170" cy="170" r="140" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="40"/>
    <!-- Segment: stroke-dasharray="<arc> <remaining>" stroke-dashoffset="<-cumulative>" -->
    <circle cx="170" cy="170" r="140" fill="none" stroke="#FC3165" stroke-width="40"
      stroke-dasharray="387 493" stroke-dashoffset="0"
      transform="rotate(-90 170 170)" stroke-linecap="round"/>
    <text x="170" y="160" fill="#fff" font-size="48" font-weight="700" text-anchor="middle" font-family="Montserrat">159</text>
    <text x="170" y="193" fill="rgba(255,255,255,0.6)" font-size="20" text-anchor="middle" font-family="Montserrat">label</text>
  </svg>
  <div class="donut-legend">
    <div class="legend-item"><div class="legend-dot" style="background:#FC3165;"></div><span><strong>70 Label</strong> (44%)</span></div>
  </div>
</div>
```

**Donut math:** circumference = 2π × 140 ≈ 879.6. Each segment's `stroke-dasharray` first value = `percentage × 879.6`. `stroke-dashoffset` for each subsequent segment = negative sum of all prior arcs.

#### Horizontal Bar Chart
```html
<div class="bar-chart">
  <div class="bar-row">
    <div class="bar-label">Label</div>
    <div class="bar-track">
      <div class="bar-fill-neu" style="width:60%;"></div>
      <div class="bar-fill-neg" style="width:40%; left:60%;"></div>
    </div>
    <div class="bar-count">41</div>
  </div>
</div>
<div class="legend-bar">
  <div class="legend-bar-item"><div class="legend-bar-swatch" style="background:#FFB020;"></div> Neutral</div>
  <div class="legend-bar-item"><div class="legend-bar-swatch" style="background:#FC3165;"></div> Negative</div>
</div>
```

#### Roadmap / Data Table (div-based — never use `<table>`)

**Critical:** Never use HTML `<table>` or Markdown pipe tables. MARP's default theme overrides table CSS and renders them broken. Always use this div-grid pattern:

```html
<div class="roadmap-table">
  <div class="roadmap-header"><div>Col 1</div><div>Col 2</div><div>Col 3</div><div>Col 4</div></div>
  <div class="roadmap-row"><div>Value</div><div>Value</div><div>Value</div><div>Value</div></div>
</div>
```

Adjust `grid-template-columns` in the CSS to match your column count and proportions.

#### Flow Pipeline
```html
<div class="flow-pipeline">
  <div class="flow-step">
    <div class="flow-box"><h4>Step</h4><p>Detail</p></div>
  </div>
  <div class="flow-arrow">→</div>
  <div class="flow-step">
    <div class="flow-box"><h4>Step</h4><p>Detail</p></div>
  </div>
</div>
```

#### Timeline
```html
<div class="timeline">
  <div class="timeline-item"><h4>Phase Title</h4><p>Description of what happens here.</p></div>
  <div class="timeline-item"><h4>Phase Title</h4><p>Description.</p></div>
</div>
```

### 6. Critical MARP Gotchas

These are hard-won lessons — violating them produces invisible or broken output:

| Issue | Wrong | Correct |
|---|---|---|
| Background images | `section.title { background-image: url(...) }` | `<!-- _backgroundImage: url('assets/bg-title.png') -->` per slide |
| Tables | `<table>` or `\| col \| col \|` markdown | `.roadmap-table` div-grid |
| Inline HTML | (missing `html: true`) | Always include `html: true` in front matter |
| Logos | Markdown `![logo](...)` for positioned logos | `<img src="..." class="logo">` with CSS `position: absolute` |

### 7. Finalizing the Output

1. Write the presentation to `<topic-name>/presentation.md`
2. Copy the template's `assets/` folder to `<topic-name>/assets/`
3. Tell the user the output path and rendering options:

**Render / Export options:**
```bash
# Preview in browser (live reload)
marp --html <topic>/presentation.md --preview

# Export to HTML
marp --html <topic>/presentation.md -o <topic>/presentation.html --allow-local-files

# Export to PDF
marp --html <topic>/presentation.md --pdf -o <topic>/presentation.pdf --allow-local-files

# Export to PPTX
marp --html <topic>/presentation.md --pptx -o <topic>/presentation.pptx --allow-local-files
```

Or open the `.md` file directly in VS Code with the **MARP for VS Code** extension installed.

## Supporting Files

- `templates/` — All available templates. Each is a self-contained folder.
- `templates/doit/template.md` — DoiT brand tokens (colors, fonts, assets).
- `templates/doit/marp-template.md` — Full MARP CSS block and layout reference for the DoiT template.
- `templates/doit/assets/` — DoiT brand assets (backgrounds, logos).
