# DoiT MARP Template

Full MARP boilerplate for the DoiT brand. Copy the front matter block verbatim into every new presentation, then build slides below it.

## Front Matter Block

Paste this at the very top of every generated `.md` file. Do not modify it except to add slide-specific content.

```yaml
---
marp: true
html: true
theme: default
paginate: false
backgroundColor: "#000433"
color: "#FFFFFF"
style: |
  @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@300;400;500;600;700&display=swap');

  * { box-sizing: border-box; }

  section {
    font-family: 'Montserrat', sans-serif;
    background-color: #000433;
    color: #FFFFFF;
    padding: 60px 80px;
    width: 1280px;
    height: 720px;
    position: relative;
    background-size: cover;
    background-position: center;
  }

  /* ── Title slide ── */
  section.title {
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    padding-bottom: 100px;
  }
  section.title .logo { position: absolute; top: 70px; left: 102px; width: 127px; }
  section.title h1 { font-size: 48px; font-weight: 700; margin: 0 0 12px 0; line-height: 1.2; color: #FFFFFF; }
  section.title p { font-size: 21px; font-weight: 400; color: rgba(255,255,255,0.7); margin: 0; }

  /* ── Content slide ── */
  section.content h1 { font-size: 40px; font-weight: 700; margin: 0 0 20px 0; color: #FFFFFF; }

  /* ── Typography ── */
  h2 { font-size: 28px; font-weight: 600; color: #FC3165; margin: 0 0 10px 0; }
  h3 { font-size: 22px; font-weight: 600; color: #FC3165; margin: 0 0 10px 0; }
  p { font-size: 19px; line-height: 1.5; margin: 0 0 12px 0; }
  ul { font-size: 18px; line-height: 1.6; padding-left: 24px; margin: 0 0 10px 0; }
  li { margin-bottom: 6px; }
  .intro { font-size: 19px; color: rgba(255,255,255,0.7); margin-bottom: 18px; }

  /* ── Footer ── */
  .footer { position: absolute; bottom: 33px; right: 33px; display: flex; align-items: center; gap: 10px; font-size: 13px; color: rgba(255,255,255,0.7); }
  .footer img { height: 23px; }

  /* ── Agenda ── */
  .agenda-table { width: 100%; margin-top: 20px; }
  .agenda-row { display: flex; justify-content: space-between; align-items: center; padding: 18px 0; border-bottom: 1px solid rgba(252,49,101,0.5); font-size: 24px; }
  .agenda-row:first-child { border-top: 1px solid rgba(252,49,101,0.5); }
  .agenda-num { font-size: 36px; font-weight: 700; color: #FC3165; }

  /* ── Metrics grid ── */
  .metrics-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 16px; }
  .metric-item { background: rgba(255,255,255,0.05); border-left: 4px solid #FC3165; padding: 14px 18px; border-radius: 0 8px 8px 0; }
  .metric-value { font-size: 36px; font-weight: 700; color: #FC3165; margin-bottom: 4px; }
  .metric-desc { font-weight: 600; font-size: 16px; margin-bottom: 3px; }
  .metric-sub { font-size: 13px; color: rgba(255,255,255,0.7); }

  /* ── Callout ── */
  .callout { background: rgba(252,49,101,0.1); border: 2px solid #FC3165; border-radius: 10px; padding: 14px 20px; font-size: 17px; font-weight: 500; margin-top: 14px; }

  /* ── Three-column ── */
  .three-col { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
  .col-box { background: rgba(255,255,255,0.05); padding: 18px; border-radius: 8px; border-top: 3px solid #FC3165; }
  .col-box h3 { font-size: 17px; margin: 0 0 8px 0; }
  .col-box ul { font-size: 14px; padding-left: 18px; }
  .col-box li { margin-bottom: 5px; }

  /* ── Two-column ── */
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .two-col-box { background: rgba(255,255,255,0.05); padding: 18px; border-radius: 8px; }
  .two-col-box h3 { font-size: 18px; margin: 0 0 10px 0; }
  .two-col-box ul { font-size: 14px; padding-left: 18px; }
  .two-col-box li { margin-bottom: 6px; }

  /* ── Four-column component cards ── */
  .four-col { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 12px; margin-bottom: 14px; }
  .comp-card { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; padding: 14px; border-top: 3px solid #FC3165; }
  .comp-card-icon { font-size: 20px; margin-bottom: 6px; }
  .comp-card h4 { font-size: 15px; margin: 0 0 6px 0; font-weight: 600; }
  .comp-card p { font-size: 12px; color: rgba(255,255,255,0.7); margin: 0; line-height: 1.4; }

  /* ── Quote grid ── */
  .quote-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .quote-card { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; padding: 16px 16px 12px 22px; position: relative; }
  .quote-card::before { content: '\201C'; position: absolute; top: 4px; left: 10px; font-size: 28px; color: rgba(252,49,101,0.4); font-family: serif; }
  .quote-card p { font-size: 13px; line-height: 1.5; color: rgba(255,255,255,0.75); font-style: italic; margin: 0 0 6px 0; }
  .quote-role { font-size: 12px; color: #FC3165; font-style: normal; font-weight: 600; }

  /* ── Roadmap div-table (use instead of <table> or markdown tables) ── */
  .roadmap-table { width: 100%; margin-top: 10px; }
  .roadmap-header { display: grid; grid-template-columns: 1.4fr 1.6fr 2fr 0.7fr; padding: 10px 0; border-bottom: 2px solid #FC3165; color: #FC3165; font-weight: 600; font-size: 13px; gap: 10px; }
  .roadmap-row { display: grid; grid-template-columns: 1.4fr 1.6fr 2fr 0.7fr; padding: 9px 0; border-bottom: 1px solid rgba(255,255,255,0.1); font-size: 13px; gap: 10px; align-items: center; }
  .roadmap-row:last-child { border-bottom: none; }
  .roadmap-row > div:first-child { font-weight: 600; color: rgba(255,255,255,0.7); }

  /* ── Bar chart ── */
  .bar-chart { width: 100%; margin: 8px 0; }
  .bar-row { display: flex; align-items: center; margin-bottom: 8px; }
  .bar-label { width: 180px; font-size: 14px; font-weight: 500; text-align: right; padding-right: 14px; flex-shrink: 0; }
  .bar-track { flex: 1; height: 24px; background: rgba(255,255,255,0.06); border-radius: 4px; position: relative; overflow: hidden; }
  .bar-fill-neg { height: 100%; background: #FC3165; border-radius: 4px; position: absolute; top: 0; }
  .bar-fill-neu { height: 100%; background: #FFB020; border-radius: 4px; position: absolute; top: 0; left: 0; }
  .bar-fill-pos { height: 100%; background: #00C48C; border-radius: 4px; position: absolute; top: 0; left: 0; }
  .bar-count { width: 40px; font-size: 13px; font-weight: 600; padding-left: 8px; flex-shrink: 0; }

  /* ── Donut chart legend ── */
  .donut-container { display: flex; align-items: center; justify-content: center; gap: 50px; margin: 10px 0; }
  .donut-legend { display: flex; flex-direction: column; gap: 12px; }
  .legend-item { display: flex; align-items: center; gap: 10px; font-size: 18px; }
  .legend-dot { width: 14px; height: 14px; border-radius: 50%; flex-shrink: 0; }
  .legend-bar { display: flex; gap: 24px; font-size: 13px; margin-top: 8px; }
  .legend-bar-item { display: flex; align-items: center; gap: 6px; }
  .legend-bar-swatch { width: 12px; height: 12px; border-radius: 2px; }

  /* ── Flow pipeline ── */
  .flow-pipeline { display: flex; align-items: flex-start; gap: 0; justify-content: center; }
  .flow-step { display: flex; flex-direction: column; align-items: center; }
  .flow-box { background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.15); border-radius: 10px; padding: 14px 18px; text-align: center; min-width: 140px; }
  .flow-box h4 { font-size: 16px; margin: 0 0 6px 0; font-weight: 600; }
  .flow-box p { font-size: 13px; color: rgba(255,255,255,0.7); margin: 0; line-height: 1.3; }
  .flow-arrow { display: flex; align-items: center; padding: 0 8px; color: rgba(255,255,255,0.3); font-size: 24px; margin-top: 18px; }

  /* ── Timeline ── */
  .timeline { position: relative; padding-left: 36px; }
  .timeline::before { content: ''; position: absolute; left: 12px; top: 0; bottom: 0; width: 3px; background: rgba(252,49,101,0.5); }
  .timeline-item { position: relative; margin-bottom: 24px; }
  .timeline-item::before { content: ''; position: absolute; left: -30px; top: 5px; width: 12px; height: 12px; border-radius: 50%; background: #FC3165; border: 3px solid #000433; }
  .timeline-item h4 { font-size: 18px; margin: 0 0 4px 0; font-weight: 600; }
  .timeline-item p { font-size: 14px; color: rgba(255,255,255,0.7); margin: 0; line-height: 1.4; }

  /* ── Code block ── */
  .code-block { background: rgba(0,0,0,0.4); border-left: 4px solid #FC3165; padding: 16px 20px; border-radius: 0 8px 8px 0; font-family: monospace; font-size: 14px; line-height: 1.6; white-space: pre; overflow: hidden; margin: 10px 0; }
  .code-keyword { color: #FC3165; font-weight: 600; }
  .code-comment { color: rgba(255,255,255,0.4); font-style: italic; }

  /* ── Architecture visual (2-panel: SVG + explanation) ── */
  .arch-visual { display: grid; grid-template-columns: 1.1fr 0.9fr; gap: 24px; align-items: start; }
  .arch-svg-container { background: rgba(0,0,0,0.3); border-radius: 10px; padding: 16px; border: 1px solid rgba(255,255,255,0.08); }
  .arch-explain h3 { font-size: 20px; margin: 0 0 10px 0; font-weight: 700; }
  .arch-explain p { font-size: 15px; line-height: 1.6; color: rgba(255,255,255,0.7); margin: 0 0 12px 0; }
---
```

## Slide Skeleton Reference

### Title Slide
```markdown
---

<!-- _class: title -->
<!-- _backgroundImage: url('assets/bg-title.png') -->

<img src="assets/logo-large.png" alt="DoiT" class="logo">

# Presentation Title Here

Subtitle · Date · Any secondary info

<!--
Speaker notes go here.
-->
```

### Agenda Slide
```markdown
---

<!-- _class: content -->
<!-- _backgroundImage: url('assets/bg-content.png') -->

# Agenda

<div class="agenda-table">
  <div class="agenda-row"><div>First Topic</div><div class="agenda-num">01</div></div>
  <div class="agenda-row"><div>Second Topic</div><div class="agenda-num">02</div></div>
  <div class="agenda-row"><div>Third Topic</div><div class="agenda-num">03</div></div>
</div>

<div class="footer"><span>Confidential</span><img src="assets/logo-small.png" alt="Logo"></div>

<!--
Speaker notes.
-->
```

### Content Slide (generic)
```markdown
---

<!-- _class: content -->
<!-- _backgroundImage: url('assets/bg-content.png') -->

# Slide Title

<p class="intro">Optional intro in muted color.</p>

[layout component here]

<div class="footer"><span>Confidential</span><img src="assets/logo-small.png" alt="Logo"></div>

<!--
Speaker notes.
-->
```

### Closing Slide
```markdown
---

<!-- _class: title -->
<!-- _backgroundImage: url('assets/bg-title.png') -->

<img src="assets/logo-large.png" alt="DoiT" class="logo">

# Thank You

Closing line or follow-up action.

<!--
Speaker notes.
-->
```

## Known MARP Constraints

| Constraint | Detail |
|---|---|
| **`html: true` is required** | Without it, all `<div>`, `<img>`, `<svg>` are silently stripped. Never omit it. |
| **Backgrounds via directive only** | `_backgroundImage` per slide is the only reliable method. CSS `background-image` on `section` classes is ignored by MARP. |
| **No `<table>` or pipe tables** | MARP's default theme overrides table styles, producing invisible/broken output. Always use `.roadmap-table` div-grid instead. |
| **Logos need `<img>` + CSS** | Markdown `![logo](path)` cannot be positioned; use `<img class="logo">` with `position: absolute` in CSS. |
| **No JS animations** | MARP is static. Entrance animations from the HTML template (`reveal`, `transition-delay`) have no equivalent — omit them. |
| **`position: absolute` works** | As long as the parent `section` has `position: relative` (it does in this CSS), absolute children like `.footer` and `.logo` render correctly. |
