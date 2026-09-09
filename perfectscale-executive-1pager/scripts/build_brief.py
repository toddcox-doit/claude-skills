#!/usr/bin/env python3
"""Build a customer-facing PerfectScale executive brief PDF from JSON.

Usage:
  python3 build_brief.py payload.json out.pdf
"""

import json
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)

INK = colors.HexColor("#1A2332")
MUTED = colors.HexColor("#5C6B7A")
RULE = colors.HexColor("#D7DDE4")
WASH = colors.HexColor("#F3F5F7")
GREEN = colors.HexColor("#1F6B4A")
AMBER_W = colors.HexColor("#F8EEE4")
NAVY = colors.HexColor("#1A2332")
WHITE = colors.white
GREEN_W = colors.HexColor("#E7F1EC")


def styles():
    b = getSampleStyleSheet()
    return {
        "kicker": ParagraphStyle("k", parent=b["Normal"], fontName="Times-Bold",
            fontSize=8, textColor=GREEN, spaceAfter=3),
        "title": ParagraphStyle("t", parent=b["Title"], fontName="Times-Bold",
            fontSize=14, leading=17, textColor=INK, alignment=TA_LEFT, spaceAfter=4),
        "sub": ParagraphStyle("s", parent=b["Normal"], fontName="Times-Italic",
            fontSize=8, leading=10.5, textColor=MUTED, spaceAfter=7),
        "h": ParagraphStyle("h", parent=b["Heading2"], fontName="Times-Bold",
            fontSize=10, leading=12.5, textColor=INK, spaceBefore=6, spaceAfter=3),
        "b": ParagraphStyle("b", parent=b["Normal"], fontName="Times-Roman",
            fontSize=8, leading=10.6, textColor=INK, spaceAfter=4),
        "th": ParagraphStyle("th", parent=b["Normal"], fontName="Times-Bold",
            fontSize=6.5, leading=8.2, textColor=WHITE),
        "td": ParagraphStyle("td", parent=b["Normal"], fontName="Times-Roman",
            fontSize=6.5, leading=8.4, textColor=INK),
        "tdr": ParagraphStyle("tdr", parent=b["Normal"], fontName="Times-Roman",
            fontSize=6.5, leading=8.4, textColor=INK, alignment=TA_RIGHT),
        "call_t": ParagraphStyle("ct", parent=b["Normal"], fontName="Times-Bold",
            fontSize=8.5, leading=11, textColor=INK, spaceAfter=2),
        "call_b": ParagraphStyle("cb", parent=b["Normal"], fontName="Times-Roman",
            fontSize=8, leading=10.6, textColor=INK),
        "stat": ParagraphStyle("st", parent=b["Normal"], fontName="Times-Bold",
            fontSize=12, leading=14, textColor=INK, alignment=TA_CENTER),
        "lab": ParagraphStyle("lb", parent=b["Normal"], fontName="Times-Roman",
            fontSize=6.5, leading=8.5, textColor=MUTED, alignment=TA_CENTER),
        "ask": ParagraphStyle("ask", parent=b["Normal"], fontName="Times-Roman",
            fontSize=8, leading=10.6, textColor=WHITE),
        "ask_t": ParagraphStyle("at", parent=b["Normal"], fontName="Times-Bold",
            fontSize=9, leading=11, textColor=WHITE, spaceAfter=3),
        "tiny": ParagraphStyle("tiny", parent=b["Normal"], fontName="Times-Italic",
            fontSize=6.2, leading=8, textColor=MUTED, spaceBefore=3, spaceAfter=2),
    }


def ptable(headers, rows, st, widths, right=None, highlight_last=False):
    right = set(right or [])
    data = [[Paragraph(h, st["th"]) for h in headers]]
    for r in rows:
        data.append([
            Paragraph(str(cell).replace("\n", "<br/>"), st["tdr"] if i in right else st["td"])
            for i, cell in enumerate(r)
        ])
    t = Table(data, colWidths=widths, repeatRows=1)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, WASH]),
        ("GRID", (0, 0), (-1, -1), 0.25, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
        ("TOPPADDING", (0, 0), (-1, -1), 2.8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.8),
    ]
    if highlight_last:
        cmds.append(("BACKGROUND", (0, -1), (-1, -1), GREEN_W))
    t.setStyle(TableStyle(cmds))
    return t


def box(title, body, bg, st, title_style="call_t", body_style="call_b"):
    inner = [[Paragraph(title, st[title_style])], [Paragraph(body, st[body_style])]]
    t = Table(inner, colWidths=[7.5 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.4, INK),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def even_widths(n, total=7.5):
    w = total / max(n, 1)
    return [w * inch] * n


def build(payload, out):
    st = styles()
    header = payload.get("header") or f"{payload.get('customer', 'Customer')}  ·  PerfectScale technical executive brief"
    subheader = payload.get("subheader") or "Confidential"
    footer = payload.get("footer") or (
        "Figures from the PerfectScale platform. Run-rate is illustrative, not a savings guarantee."
    )
    two_pages = bool(payload.get("findingsRows"))

    def header_footer(c, doc):
        c.saveState()
        c.setFillColor(NAVY)
        c.rect(0, letter[1] - 0.32 * inch, letter[0], 0.32 * inch, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont("Times-Bold", 8)
        c.drawString(0.5 * inch, letter[1] - 0.2 * inch, header)
        c.setFont("Times-Roman", 8)
        c.drawRightString(letter[0] - 0.5 * inch, letter[1] - 0.2 * inch, subheader)
        c.setFillColor(MUTED)
        c.setFont("Times-Roman", 6.5)
        c.drawString(0.5 * inch, 0.28 * inch, footer)
        if two_pages:
            c.drawRightString(letter[0] - 0.5 * inch, 0.28 * inch, f"{doc.page} of 2")
        c.restoreState()

    doc = SimpleDocTemplate(
        str(out), pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.42 * inch, bottomMargin=0.46 * inch,
        title=payload.get("title") or header,
        author="PerfectScale",
        subject="Decision brief — automation, PodFit, InfraFit, ROI",
    )
    story = []
    if payload.get("kicker"):
        story.append(Paragraph(payload["kicker"], st["kicker"]))
    story.append(Paragraph(payload.get("title") or "The decision: put automation back in the agreement", st["title"]))
    if payload.get("lede"):
        story.append(Paragraph(payload["lede"], st["sub"]))

    kpis = payload.get("kpis") or []
    if kpis:
        n = len(kpis)
        r1 = [Paragraph(k["value"], st["stat"]) for k in kpis]
        r2 = [Paragraph(k["label"], st["lab"]) for k in kpis]
        kt = Table([r1, r2], colWidths=[7.5 * inch / n] * n)
        kt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), WASH),
            ("BOX", (0, 0), (-1, -1), 0.3, RULE),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, RULE),
            ("TOPPADDING", (0, 0), (-1, 0), 5),
            ("BOTTOMPADDING", (0, -1), (-1, -1), 5),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(kt)
        story.append(Spacer(1, 7))

    if payload.get("whyBody"):
        story.append(box(payload.get("whyTitle") or "Why this is on your desk now", payload["whyBody"], AMBER_W, st))

    if payload.get("poolsTitle"):
        story.append(Paragraph(payload["poolsTitle"], st["h"]))
    if payload.get("poolsBody"):
        story.append(Paragraph(payload["poolsBody"], st["b"]))

    cheaders = payload.get("clusterHeaders") or []
    crows = payload.get("clusterRows") or []
    if cheaders and crows:
        widths = payload.get("clusterColInches")
        widths = [w * inch for w in widths] if widths else even_widths(len(cheaders))
        story.append(ptable(
            cheaders, crows, st, widths,
            right=set(payload.get("clusterRightCols") or []),
            highlight_last=True,
        ))
    if payload.get("clusterFootnote"):
        story.append(Paragraph(payload["clusterFootnote"], st["tiny"]))

    fheaders = payload.get("findingsHeaders") or []
    frows = payload.get("findingsRows") or []
    if fheaders and frows:
        if two_pages:
            story.append(PageBreak())
        story.append(Paragraph(payload.get("findingsTitle") or "What we found", st["h"]))
        widths = payload.get("findingsColInches")
        widths = [w * inch for w in widths] if widths else even_widths(len(fheaders))
        story.append(ptable(fheaders, frows, st, widths))

    if payload.get("flywheel"):
        story.append(Paragraph(payload["flywheel"], st["b"]))

    if payload.get("askHeadline"):
        story.append(Paragraph(payload.get("askTitle") or "The commercial ask", st["h"]))
        story.append(box(payload["askHeadline"], payload.get("askBody") or "", NAVY, st,
                         title_style="ask_t", body_style="ask"))

    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    print(out)


def main():
    if len(sys.argv) != 3:
        print("usage: build_brief.py payload.json out.pdf", file=sys.stderr)
        sys.exit(2)
    payload = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2])
    out.parent.mkdir(parents=True, exist_ok=True)
    build(payload, out)


if __name__ == "__main__":
    main()
