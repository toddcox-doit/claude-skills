# DoiT Corporate Template

Official DoiT International corporate slide template for MARP.

## Brand

- **Name:** DoiT
- **Primary font:** Montserrat (loaded via Google Fonts)
- **Colors:**
  - Background: `#000433` (dark navy)
  - Accent: `#FC3165` (pink)
  - Accent green: `#00C48C`
  - Accent amber: `#FFB020`
  - Text: `#FFFFFF` (white)
  - Muted text: `rgba(255, 255, 255, 0.7)`
  - Line/border accent: `rgba(252, 49, 101, 0.5)`
- **Footer text:** "Confidential" (required on every content slide, bottom-right)

## Assets

| File | Usage |
|---|---|
| `assets/bg-title.png` | Title and closing slide background (via `_backgroundImage` directive) |
| `assets/bg-content.png` | Content slide background (via `_backgroundImage` directive) |
| `assets/logo-large.png` | Large logo on title/closing slides (positioned top-left via CSS) |
| `assets/logo-small.png` | Small logo in content slide footer (bottom-right) |

## Slide Types

- **Title slide:** `_class: title` + `_backgroundImage: bg-title.png`. Large logo top-left, main title as `# heading`, subtitle as `p` below.
- **Content slide:** `_class: content` + `_backgroundImage: bg-content.png`. `# heading` top-left, content area below, footer with "Confidential" + small logo.
- **Agenda slide:** A content slide with numbered `.agenda-row` divs.
- **Closing slide:** Same as title slide with "Thank You" heading.
