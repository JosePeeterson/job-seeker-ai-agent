"""
Converts markdown-style text resumes (from data/resumes/) to professional PDFs.
Usage:
    python src/resume_to_pdf.py                          # converts all .txt files
    python src/resume_to_pdf.py path/to/resume.txt       # converts a single file
"""

import re
import sys
from pathlib import Path
from typing import List, Tuple, Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm, mm
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame,
    Paragraph, Spacer, HRFlowable, KeepTogether, ListFlowable, ListItem
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
NAVY      = HexColor("#1B2A4A")   # name, section headers
DARK_GRAY = HexColor("#333333")   # body text
MID_GRAY  = HexColor("#666666")   # contact line, sub-bullets
RULE_COLOR = HexColor("#C5CDD9")  # horizontal dividers
ACCENT    = HexColor("#2563EB")   # accent / link colour

PAGE_W, PAGE_H = A4
MARGIN_H = 1.8 * cm   # left/right margin
MARGIN_T = 1.6 * cm   # top margin
MARGIN_B = 1.6 * cm   # bottom margin

# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
def build_styles() -> dict:
    base = dict(fontName="Helvetica", fontSize=10, leading=14,
                textColor=DARK_GRAY, spaceAfter=0, spaceBefore=0)

    name_style = ParagraphStyle(
        "Name",
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=26,
        textColor=NAVY,
        alignment=TA_CENTER,
        spaceAfter=2,
    )
    contact_style = ParagraphStyle(
        "Contact",
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=MID_GRAY,
        alignment=TA_CENTER,
        spaceAfter=4,
    )
    section_style = ParagraphStyle(
        "Section",
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=NAVY,
        spaceBefore=10,
        spaceAfter=2,
        textTransform="uppercase",
        letterSpacing=0.8,
    )
    body_style = ParagraphStyle(
        "Body",
        fontName="Helvetica",
        fontSize=10,
        textColor=DARK_GRAY,
        spaceBefore=0,
        leading=15,
        spaceAfter=4,
    )
    bullet_style = ParagraphStyle(
        "Bullet",
        fontName="Helvetica",
        fontSize=10,
        textColor=DARK_GRAY,
        spaceBefore=0,
        leftIndent=12,
        firstLineIndent=0,
        leading=15,
        spaceAfter=3,
    )
    subbullet_style = ParagraphStyle(
        "SubBullet",
        fontName="Helvetica",
        fontSize=9,
        textColor=MID_GRAY,
        spaceBefore=0,
        leftIndent=24,
        firstLineIndent=0,
        leading=13,
        spaceAfter=2,
    )
    bold_inline = ParagraphStyle(
        "BoldInline",
        fontName="Helvetica-Bold",
        fontSize=10,
        textColor=DARK_GRAY,
        spaceBefore=0,
        leading=15,
        spaceAfter=4,
    )
    return dict(
        name=name_style,
        contact=contact_style,
        section=section_style,
        body=body_style,
        bullet=bullet_style,
        subbullet=subbullet_style,
        bold_inline=bold_inline,
    )


# ---------------------------------------------------------------------------
# Markdown-like inline rendering helpers
# ---------------------------------------------------------------------------
LINK_RE = re.compile(r'\[([^\]]+)\]\([^)]+\)')   # [text](url) -> text
BOLD_RE = re.compile(r'\*\*([^*]+)\*\*')          # **text** -> <b>text</b>

def inline_html(text: str) -> str:
    """Convert inline markdown to ReportLab XML markup."""
    # strip markdown links, keep display text
    text = LINK_RE.sub(r'\1', text)
    # bold
    text = BOLD_RE.sub(r'<b>\1</b>', text)
    # escape & that are not part of XML entities
    text = re.sub(r'&(?!amp;|lt;|gt;|quot;|apos;|#)', '&amp;', text)
    return text


# ---------------------------------------------------------------------------
# Parser: text -> structured sections
# ---------------------------------------------------------------------------
BOLD_HEADER_RE = re.compile(r'^\*\*(.+?)\*\*\s*$')  # entire line is **...**

def is_bullet(line: str) -> bool:
    return line.startswith('* ') or line.startswith('- ')

def is_subbullet(line: str) -> bool:
    stripped = line.strip()
    return (line.startswith('\t+') or line.startswith('    +') or
            line.startswith('\t-') or line.startswith('    -') or
            stripped.startswith('+ '))

def bullet_text(line: str) -> str:
    return re.sub(r'^[\*\-]\s+', '', line).strip()

def subbullet_text(line: str) -> str:
    return re.sub(r'^[\t ]+[\+\-]\s*', '', line).strip()


class ResumeSection:
    def __init__(self, title: str):
        self.title = title
        self.items: List[Tuple[str, str]] = []   # (kind, text)
        # kind: 'body' | 'bullet' | 'subbullet' | 'blank'


def parse_resume(text: str) -> Tuple[Optional[str], List[str], List[ResumeSection]]:
    """Return (name, contact_lines, sections)."""
    lines = text.splitlines()

    name: Optional[str] = None
    contact_lines: List[str] = []
    sections: List[ResumeSection] = []
    current_section: Optional[ResumeSection] = None
    in_contact = False

    for raw_line in lines:
        line = raw_line.rstrip()

        # Skip LLM preamble lines like "Here is the rewritten CV:"
        if re.match(r'^here is', line, re.IGNORECASE) and ':' in line:
            continue

        header_match = BOLD_HEADER_RE.match(line)

        if header_match:
            title = header_match.group(1).strip()

            # First bold line with no existing name -> candidate's name
            if name is None:
                name = title
                in_contact = False
                continue

            # "Contact Information" section is rendered inline under the name
            if re.search(r'contact', title, re.IGNORECASE):
                in_contact = True
                current_section = None
                continue

            # All other bold lines are section headers
            in_contact = False
            current_section = ResumeSection(title)
            sections.append(current_section)
            continue

        # Contact block: collect bullet items as contact lines
        if in_contact:
            if is_bullet(line):
                contact_lines.append(bullet_text(line))
            continue

        if current_section is None:
            continue

        if not line.strip():
            current_section.items.append(('blank', ''))
        elif is_subbullet(line):
            current_section.items.append(('subbullet', subbullet_text(line)))
        elif is_bullet(line):
            current_section.items.append(('bullet', bullet_text(line)))
        else:
            current_section.items.append(('body', line.strip()))

    return name, contact_lines, sections


# ---------------------------------------------------------------------------
# PDF builder
# ---------------------------------------------------------------------------
def build_pdf(name: str, contact_lines: List[str],
              sections: List[ResumeSection],
              out_path: Path) -> None:

    styles = build_styles()
    content_width = PAGE_W - 2 * MARGIN_H

    frame = Frame(MARGIN_H, MARGIN_B,
                  content_width, PAGE_H - MARGIN_T - MARGIN_B,
                  leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0)

    doc = BaseDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=MARGIN_H, rightMargin=MARGIN_H,
        topMargin=MARGIN_T, bottomMargin=MARGIN_B,
    )
    template = PageTemplate(id="main", frames=[frame])
    doc.addPageTemplates([template])

    story = []

    # --- Name ---
    story.append(Paragraph(inline_html(name), styles["name"]))

    # --- Contact line ---
    if contact_lines:
        contact_html = "  &nbsp;|&nbsp;  ".join(inline_html(c) for c in contact_lines)
        story.append(Paragraph(contact_html, styles["contact"]))

    story.append(Spacer(1, 3 * mm))
    story.append(HRFlowable(width="100%", thickness=1.5,
                             color=NAVY, spaceAfter=2))

    # --- Sections ---
    for section in sections:
        section_block = []

        # Section header + rule
        section_block.append(Spacer(1, 2 * mm))
        section_block.append(Paragraph(section.title, styles["section"]))
        section_block.append(HRFlowable(width="100%", thickness=0.5,
                                         color=RULE_COLOR, spaceAfter=3))

        # Section body
        for kind, text in section.items:
            if kind == 'blank':
                section_block.append(Spacer(1, 1 * mm))
            elif kind == 'body':
                section_block.append(Paragraph(inline_html(text), styles["body"]))
            elif kind == 'bullet':
                bullet_para = Paragraph(
                    f'<bullet>\u2022</bullet>{inline_html(text)}',
                    styles["bullet"]
                )
                section_block.append(bullet_para)
            elif kind == 'subbullet':
                sub_para = Paragraph(
                    f'<bullet>\u25e6</bullet>{inline_html(text)}',
                    styles["subbullet"]
                )
                section_block.append(sub_para)

        # Keep at least the header + first item together to avoid orphan headers
        if len(section_block) >= 2:
            story.append(KeepTogether(section_block[:3]))
            story.extend(section_block[3:])
        else:
            story.extend(section_block)

    doc.build(story)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def convert_resume(txt_path: Path) -> Path:
    text = txt_path.read_text(encoding="utf-8")
    name, contact_lines, sections = parse_resume(text)

    if not name:
        raise ValueError(f"Could not detect candidate name in {txt_path}")

    out_path = txt_path.with_suffix(".pdf")
    build_pdf(name, contact_lines, sections, out_path)
    print(f"  ✓  {txt_path.name}  →  {out_path.name}")
    return out_path


def main():
    resumes_dir = Path(__file__).parent.parent / "data" / "resumes"

    if len(sys.argv) > 1:
        targets = [Path(p) for p in sys.argv[1:]]
    else:
        targets = list(resumes_dir.glob("*.txt"))

    if not targets:
        print("No .txt resume files found in data/resumes/")
        return

    print(f"Converting {len(targets)} resume(s)...\n")
    for txt_path in targets:
        try:
            convert_resume(txt_path)
        except Exception as exc:
            print(f"  ✗  {txt_path.name}  —  {exc}")

    print("\nDone.")


if __name__ == "__main__":
    main()
