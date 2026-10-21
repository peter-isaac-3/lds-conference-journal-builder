#!/usr/bin/env python3
"""
Conference Study Journal builder.

Reads talk text files from a folder and produces a PDF where:
  * there is a cover page and a table of contents
  * each session starts with a divider page ("Saturday Morning Session", ...)
  * every talk page has the numbered talk text on the left ~2/3 and journal lines down the right ~1/3
  * footnotes are printed at the end of each talk
  * pages are numbered

Usage:
    python build_journal.py "https://www.churchofjesuschrist.org/study/general-conference/2026/10?lang=eng"
    python build_journal.py talks_folder [output.pdf]

Folder layout (names sort alphabetically; leading numbers are hidden):
    talks/
      conference.json                  <- cover info (optional)
      01 Saturday Morning Session/
        01 First Talk.txt
        02 Second Talk.txt
      02 Saturday Afternoon Session/
        ...
"""
import json
import math
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape, unescape

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, NextPageTemplate,
                                PageBreak, PageTemplate, Paragraph, Spacer)
from reportlab.platypus.tableofcontents import TableOfContents

# ---------------------------------------------------------------- fonts
# Bundled Liberation Serif (Times-compatible, SIL Open Font License) so any
# accented letter or typographic punctuation renders; falls back to built-in Times.
_FONT_DIR = Path(__file__).resolve().parent / "fonts"
try:
    for _n, _f in [("JSerif", "Regular"), ("JSerif-Bold", "Bold"),
                   ("JSerif-Italic", "Italic"), ("JSerif-BoldItalic", "BoldItalic")]:
        pdfmetrics.registerFont(TTFont(_n, str(_FONT_DIR / f"LiberationSerif-{_f}.ttf")))
    pdfmetrics.registerFontFamily("JSerif", normal="JSerif", bold="JSerif-Bold",
                                  italic="JSerif-Italic", boldItalic="JSerif-BoldItalic")
    F, FB, FI, FBI = "JSerif", "JSerif-Bold", "JSerif-Italic", "JSerif-BoldItalic"
except Exception:
    F, FB, FI, FBI = "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic"

PAGE_W, PAGE_H = letter
MARGIN = 0.75 * inch
FOOTER_Y = 0.45 * inch

# Page split: talk text on the left (~2/3 of the width), journal lines in a column
# down the right side (~1/3 of the width).
CONTENT_W = PAGE_W - 2 * MARGIN
JOURNAL_W = CONTENT_W / 3                       # width of the journal column
GUTTER = 0.22 * inch                            # space between text and journal column
TEXT_W = CONTENT_W - JOURNAL_W - GUTTER         # width of the text column
JOURNAL_X = PAGE_W - MARGIN - JOURNAL_W         # left edge of the journal column
JOURNAL_TOP = PAGE_H - MARGIN
JOURNAL_BOTTOM = 0.85 * inch
LINE_SPACING = 0.30 * inch

INK = colors.HexColor("#222222")
ACCENT = colors.HexColor("#1F3A5F")
RULE = colors.HexColor("#B8BEC8")

# ---------------------------------------------------------------- styles
S = {
    "cover_title": ParagraphStyle("ct", fontName=FB, fontSize=38, leading=44,
                                  alignment=TA_CENTER, textColor=ACCENT),
    "cover_sub": ParagraphStyle("cs", fontName=FI, fontSize=20, leading=26,
                                alignment=TA_CENTER, textColor=INK),
    "cover_kicker": ParagraphStyle("ck", fontName=FI, fontSize=17, leading=22,
                                   alignment=TA_CENTER, textColor=INK, spaceAfter=10),
    "cover_detail": ParagraphStyle("cd", fontName=F, fontSize=12, leading=16,
                                   alignment=TA_CENTER, textColor=INK),
    "cover_name": ParagraphStyle("cn", fontName=F, fontSize=14, leading=20,
                                 alignment=TA_CENTER, textColor=INK),
    "toc_head": ParagraphStyle("th", fontName=FB, fontSize=26, leading=32,
                               alignment=TA_CENTER, textColor=ACCENT, spaceAfter=18),
    "divider": ParagraphStyle("dv", fontName=FB, fontSize=34, leading=42,
                              alignment=TA_CENTER, textColor=ACCENT),
    "talk_title": ParagraphStyle("tt", fontName=FB, fontSize=15, leading=18,
                                 alignment=TA_LEFT, textColor=ACCENT, spaceAfter=2),
    "talk_speaker": ParagraphStyle("ts", fontName=FI, fontSize=10, leading=13,
                                   textColor=INK, spaceAfter=2),
    "talk_date": ParagraphStyle("td", fontName=FB, fontSize=8.5, leading=11,
                                textColor=ACCENT, spaceAfter=8),
    "body": ParagraphStyle("b", fontName=F, fontSize=9.5, leading=12,
                           alignment=TA_JUSTIFY, textColor=INK, spaceAfter=5,
                           leftIndent=17, bulletIndent=0, bulletFontName=FB,
                           bulletFontSize=7.5, bulletColor=ACCENT),
    "list": ParagraphStyle("li", fontName=F, fontSize=9.5, leading=12,
                           alignment=TA_JUSTIFY, textColor=INK, spaceAfter=5,
                           leftIndent=29, bulletIndent=17, bulletFontName=F, bulletFontSize=9.5),
    "subhead": ParagraphStyle("sh", fontName=FB, fontSize=10.5, leading=13,
                              textColor=ACCENT, spaceBefore=4, spaceAfter=3),
    "fn_head": ParagraphStyle("fh", fontName=FB, fontSize=9, leading=12,
                              textColor=ACCENT, spaceBefore=8, spaceAfter=3, keepWithNext=1),
    "fn": ParagraphStyle("f", fontName=F, fontSize=8, leading=10,
                         textColor=INK, spaceAfter=2, leftIndent=12, firstLineIndent=-12),
    "toc0": ParagraphStyle("t0", fontName=FB, fontSize=12, leading=16,
                           spaceBefore=10, textColor=ACCENT),
    "toc1": ParagraphStyle("t1", fontName=F, fontSize=10.5, leading=14,
                           leftIndent=18, textColor=INK),
    "toc_flat": ParagraphStyle("tf", fontName=F, fontSize=10.5, leading=14,
                               spaceBefore=3, textColor=INK),
    # Invisible styles used only so TOC notifications can be triggered
    "h_session": ParagraphStyle("hs", fontSize=1, leading=1, textColor=colors.white),
}


# ---------------------------------------------------------------- parsing
def clean_name(name: str) -> str:
    """Drop leading '01 ', '02_', '3 - ' ordering prefixes and the extension."""
    name = Path(name).stem if name.lower().endswith(".txt") else name
    return re.sub(r"^\d+\s*[-_.\s]\s*", "", name).strip()


def fix_mojibake(text: str) -> str:
    """Repair UTF-8 text that was wrongly decoded as Latin-1/cp1252 (e.g. 'â€™')."""
    if not re.search("[\u00c2\u00c3\u00e2][\u0080-\u00bf\u20ac\u2018-\u203a\u0152\u0153\u0160\u0161\u0178\u017d\u017e\u02c6\u02dc\u2122]", text):
        return text
    for enc in ("latin-1", "cp1252"):
        try:
            return text.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return text


def markup(text: str) -> str:
    """Escape XML and turn [1] footnote markers into superscripts."""
    text = escape(text)
    text = re.sub(r"\[(\d+)\]", r"<super>\1</super>", text)
    text = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", text)
    return text


def parse_talk(path: Path) -> dict:
    raw = fix_mojibake(path.read_text(encoding="utf-8")).replace("\r\n", "\n")
    title = clean_name(path.name)
    speaker = ""
    role = ""
    conference = ""
    fn_marker = re.compile(r"(?im)^\s*-{3,}\s*footnotes\s*-{3,}\s*$")
    parts = fn_marker.split(raw, maxsplit=1)
    body_part = parts[0]
    fn_part = parts[1] if len(parts) > 1 else ""

    # Header lines at the top: "Title: ..." and "Speaker: ..."
    lines = body_part.split("\n")
    while lines and re.match(r"(?i)^(title|speaker|role|conference)\s*:", lines[0]):
        key, _, val = lines.pop(0).partition(":")
        key, val = key.strip().lower(), val.strip()
        if key == "title":
            title = val
        elif key == "speaker":
            speaker = val
        elif key == "role":
            role = val
        elif key == "conference":
            conference = val
    paragraphs = [re.sub(r"\s*\n\s*", " ", p).strip()
                  for p in re.split(r"\n\s*\n", "\n".join(lines)) if p.strip()]

    footnotes = [re.sub(r"\s*\n\s*", " ", f).strip()
                 for f in re.split(r"\n\s*\n|\n(?=\d+\.\s)", fn_part.strip()) if f.strip()]
    return {"title": title, "speaker": speaker, "role": role, "conference": conference, "paragraphs": paragraphs, "footnotes": footnotes}


def load_conference(folder: Path):
    cfg = {"title": "General Conference", "subtitle": "Study Journal", "name": ""}
    cfg_file = folder / "conference.json"
    if cfg_file.exists():
        cfg.update(json.loads(cfg_file.read_text(encoding="utf-8")))
    sessions = []
    for sdir in sorted(p for p in folder.iterdir() if p.is_dir()):
        talks = [parse_talk(f) for f in sorted(sdir.glob("*.txt"))]
        if talks:
            sessions.append({"name": clean_name(sdir.name), "talks": talks})
    loose = sorted(folder.glob("*.txt"))
    if cfg.get("layout") == "collection" or (loose and not sessions):
        # A speaker/topic collection: one flat list of talks, no session pages.
        cfg["layout"] = "collection"
        sessions = [{"name": None, "talks": [parse_talk(f) for f in loose]}]
    if not sessions or not sessions[0]["talks"]:
        sys.exit(f"No talks (.txt files) found in {folder}")
    return cfg, sessions


# ---------------------------------------------------------------- logo
def _arc_text(c, text, cx, cy, radius, center_deg, span_deg, font, size, top=True):
    """Draw text along a circular arc, letter-spaced to fill span_deg."""
    widths = [pdfmetrics.stringWidth(ch, font, size) for ch in text]
    arc_len = radius * math.radians(span_deg)
    track = (arc_len - sum(widths)) / max(len(text) - 1, 1)
    start = math.radians(center_deg + span_deg / 2 if top else center_deg - span_deg / 2)
    c.setFont(font, size)
    pos = 0.0
    for ch, w in zip(text, widths):
        s = pos + w / 2
        th = start - s / radius if top else start + s / radius
        c.saveState()
        c.translate(cx + radius * math.cos(th), cy + radius * math.sin(th))
        c.rotate(math.degrees(th) - (90 if top else 270))
        c.drawCentredString(0, 0, ch)
        c.restoreState()
        pos += w + track


def draw_logo(c, cx, cy, r):
    """The 'Priestcraft Free' seal: ringed badge with arced lettering and an open book."""
    u = r / 72.0
    c.saveState()
    c.setStrokeColor(ACCENT)
    c.setFillColor(ACCENT)
    c.setLineWidth(2.4)
    c.circle(cx, cy, r)
    c.setLineWidth(0.8)
    c.circle(cx, cy, r * 0.93)
    c.setLineWidth(1.6)
    c.circle(cx, cy, r * 0.62)

    size = r * 0.185
    mid = r * 0.775
    cap = size * 0.66
    _arc_text(c, "PRIESTCRAFT", cx, cy, mid - cap / 2, 90, 128, FB, size, top=True)
    _arc_text(c, "FREE", cx, cy, mid + cap / 2, 270, 74, FB, size, top=False)
    for ang in (0, math.pi):                      # small diamonds between the words
        x, y, d = cx + mid * math.cos(ang), cy + mid * math.sin(ang), 3.6 * u
        p = c.beginPath()
        p.moveTo(x - d, y); p.lineTo(x, y + d); p.lineTo(x + d, y); p.lineTo(x, y - d); p.close()
        c.drawPath(p, stroke=0, fill=1)

    # open book
    y0 = cy - 15 * u
    c.setFillColor(colors.white)
    c.setLineWidth(1.5)
    for sgn in (-1, 1):
        p = c.beginPath()
        p.moveTo(cx, y0)
        p.lineTo(cx + sgn * 27 * u, y0 + 6 * u)
        p.lineTo(cx + sgn * 27 * u, y0 + 26 * u)
        p.lineTo(cx, y0 + 20 * u)
        p.close()
        c.drawPath(p, stroke=1, fill=1)
        c.setLineWidth(0.7)
        for t in (0.25, 0.5, 0.75):
            for x0, x1 in ((0.15, 0.85),):
                c.line(cx + sgn * 27 * u * x0, y0 + 6 * u * x0 + 20 * u * t,
                       cx + sgn * 27 * u * x1, y0 + 6 * u * x1 + 20 * u * t)
        c.setLineWidth(1.5)
    # rising sun / light above the book
    sy = cy + 20 * u
    c.setFillColor(ACCENT)
    c.circle(cx, sy, 5.5 * u, stroke=0, fill=1)
    c.setLineWidth(1.4)
    for deg in range(30, 151, 30):
        a = math.radians(deg)
        c.line(cx + 9 * u * math.cos(a), sy + 9 * u * math.sin(a),
               cx + 14 * u * math.cos(a), sy + 14 * u * math.sin(a))
    c.restoreState()


# ---------------------------------------------------------------- document
class JournalDoc(BaseDocTemplate):
    def __init__(self, filename, logo_path=None, **kw):
        self.logo_path = logo_path
        super().__init__(filename, pagesize=letter, leftMargin=MARGIN, rightMargin=MARGIN,
                         topMargin=MARGIN, bottomMargin=MARGIN, **kw)
        w = PAGE_W - 2 * MARGIN
        full = Frame(MARGIN, MARGIN, w, PAGE_H - 2 * MARGIN, id="full", showBoundary=0)
        text = Frame(MARGIN, 0.85 * inch, TEXT_W, PAGE_H - MARGIN - 0.85 * inch, id="text",
                     leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates([
            PageTemplate(id="cover", frames=[full], onPage=self.draw_cover),
            PageTemplate(id="front", frames=[full], onPage=self.draw_footer),
            PageTemplate(id="divider", frames=[full], onPage=self.draw_divider),
            PageTemplate(id="talk", frames=[text], onPage=self.draw_talk_page),
            PageTemplate(id="back", frames=[full], onPage=self.draw_back),
        ])
        self._bookmark = 0

    # ---- page decorations
    def draw_cover(self, c, doc):
        c.saveState()
        c.setStrokeColor(ACCENT)
        c.setLineWidth(3)
        c.rect(0.5 * inch, 0.5 * inch, PAGE_W - inch, PAGE_H - inch)
        c.setLineWidth(0.75)
        c.rect(0.6 * inch, 0.6 * inch, PAGE_W - 1.2 * inch, PAGE_H - 1.2 * inch)
        c.restoreState()

    def draw_back(self, c, doc):
        """Back cover: same border as the front, with the logo in the bottom portion."""
        self.draw_cover(c, doc)
        cx, cy, r = PAGE_W / 2, 2.35 * inch, 1.0 * inch
        if self.logo_path and Path(self.logo_path).exists():
            c.drawImage(str(self.logo_path), cx - r, cy - r, 2 * r, 2 * r,
                        preserveAspectRatio=True, mask="auto", anchor="c")
        else:
            draw_logo(c, cx, cy, r)

    def draw_footer(self, c, doc):
        c.saveState()
        c.setFont(F, 10)
        c.setFillColor(INK)
        c.drawCentredString(PAGE_W / 2, FOOTER_Y, str(doc.page))
        c.restoreState()

    def draw_divider(self, c, doc):
        self.draw_footer(c, doc)
        c.saveState()
        c.setStrokeColor(ACCENT)
        c.setLineWidth(1.5)
        y = PAGE_H / 2 + 0.55 * inch
        c.line(PAGE_W / 2 - 1.5 * inch, y, PAGE_W / 2 + 1.5 * inch, y)
        y = PAGE_H / 2 - 0.9 * inch
        c.line(PAGE_W / 2 - 1.5 * inch, y, PAGE_W / 2 + 1.5 * inch, y)
        c.restoreState()

    def draw_talk_page(self, c, doc):
        self.draw_footer(c, doc)
        c.saveState()
        # vertical separator between the text and the journal column
        c.setStrokeColor(ACCENT)
        c.setLineWidth(1)
        sep_x = JOURNAL_X - GUTTER / 2
        c.line(sep_x, JOURNAL_TOP, sep_x, JOURNAL_BOTTOM - 0.1 * inch)
        c.setFont(FBI, 9)
        c.setFillColor(ACCENT)
        c.drawString(JOURNAL_X, JOURNAL_TOP - 9, "My Thoughts & Impressions")
        # ruled lines
        c.setStrokeColor(RULE)
        c.setLineWidth(0.5)
        y = JOURNAL_TOP - 0.38 * inch
        while y >= JOURNAL_BOTTOM:
            c.line(JOURNAL_X, y, PAGE_W - MARGIN, y)
            y -= LINE_SPACING
        c.restoreState()

    # ---- table of contents hook
    def beforeDocument(self):
        self._bookmark = 0  # keys must be identical on every multiBuild pass

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph):
            level = getattr(flowable, "_toc_level", None)
            if level is not None:
                text = getattr(flowable, "_toc_text", flowable.getPlainText())
                key = f"toc{self._bookmark}"
                self._bookmark += 1
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(unescape(text), key, level=level, closed=False)
                self.notify("TOCEntry", (level, text, self.page, key))


def toc_para(text, style, level, toc_text=None):
    p = Paragraph(text, style)
    p._toc_level = level
    p._toc_text = toc_text or text
    return p


def build(folder: Path, out: Path, logo_path=None):
    cfg, sessions = load_conference(folder)
    collection = cfg.get("layout") == "collection"
    kind = cfg.get("kind", "topic")
    doc = JournalDoc(str(out), logo_path=logo_path,
                     title=f'{cfg["title"]} {cfg["subtitle"]}', author=cfg.get("name", ""))

    story = []
    # Cover
    story.append(Spacer(1, 2.3 * inch))
    if cfg.get("kicker"):
        story.append(Paragraph(escape(cfg["kicker"]), S["cover_kicker"]))
    story += [Paragraph(escape(cfg["title"]), S["cover_title"]), Spacer(1, 14),
              Paragraph(escape(cfg["subtitle"]), S["cover_sub"])]
    if collection:
        talks = sessions[0]["talks"]
        years = sorted(int(y) for t in talks for y in re.findall(r"\d{4}", t["conference"]))
        detail = f"{len(talks)} talk{'s' if len(talks) != 1 else ''}"
        if years:
            detail += f"  \u00b7  {years[0]}" + (f"\u2013{years[-1]}" if years[-1] != years[0] else "")
        story += [Spacer(1, 22), Paragraph(detail, S["cover_detail"])]
    if cfg.get("name"):
        story += [Spacer(1, 2.1 * inch), Paragraph(escape(cfg["name"]), S["cover_name"])]

    # Table of contents
    toc = TableOfContents()
    toc.levelStyles = [S["toc_flat"]] if collection else [S["toc0"], S["toc1"]]
    toc.dotsMinLevel = 0
    story += [NextPageTemplate("front"), PageBreak(),
              Paragraph("Table of Contents", S["toc_head"]), toc]

    # Sessions & talks (collections have no session pages)
    for s in sessions:
        if not collection:
            story += [NextPageTemplate("divider"), PageBreak(),
                      Spacer(1, 4.65 * inch),
                      toc_para(escape(s["name"]), S["divider"], 0)]
        for t in s["talks"]:
            story += [NextPageTemplate("talk"), PageBreak()]
            title = escape(t["title"])
            parts = [title]
            if t["speaker"] and not (collection and kind == "speaker"):
                parts.append(f' \u2014 {escape(t["speaker"])}')
            if collection and t["conference"]:
                parts.append(f' \u00b7 {escape(t["conference"])}')
            toc_text = "".join(parts)
            story.append(toc_para(title, S["talk_title"], 0 if collection else 1, toc_text))
            byline = ", ".join(x for x in (t["speaker"], t["role"]) if x)
            if byline:
                story.append(Paragraph(escape(byline), S["talk_speaker"]))
            if collection and t["conference"]:
                story.append(Paragraph(escape(f'{t["conference"]} General Conference'), S["talk_date"]))
            elif byline:
                story.append(Spacer(1, 6))
            n = 0
            for para in t["paragraphs"]:
                if para.startswith("## "):
                    story.append(Paragraph(markup(para[3:]), S["subhead"]))
                elif para.startswith("\u2022 "):          # list item: bullet, not numbered
                    story.append(Paragraph(markup(para[2:]), S["list"], bulletText="\u2022"))
                else:
                    n += 1
                    story.append(Paragraph(markup(para), S["body"], bulletText=str(n)))
            if t["footnotes"]:
                story.append(Paragraph("Notes", S["fn_head"]))
                for fn in t["footnotes"]:
                    story.append(Paragraph(markup(fn), S["fn"]))

    # Back cover with the logo
    story += [NextPageTemplate("back"), PageBreak(), Spacer(1, 1)]

    doc.multiBuild(story)
    print(f"Created {out}")
    return doc.page          # number of pages in the finished PDF


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Build a General Conference study journal PDF.")
    ap.add_argument("source", help="a conference page (.../general-conference/2026/10), a speaker page "
                                   "(.../general-conference/speakers/dallin-h-oaks), a topic page "
                                   "(.../general-conference/topics/atonement), or a folder of talks")
    ap.add_argument("output", nargs="?", help="output PDF (default: <conference>_Study_Journal.pdf)")
    ap.add_argument("--name", default="", help="your name, printed on the cover (URL mode)")
    ap.add_argument("--folder", help="where to save downloaded talks (URL mode)")
    ap.add_argument("--oldest-first", action="store_true",
                    help="speaker/topic pages: list talks oldest to newest (default: as on the website)")
    ap.add_argument("--logo", help="optional image (PNG/JPG) to use on the back page "
                                   "instead of the built-in 'Priestcraft Free' seal")
    args = ap.parse_args()

    if args.source.lower().startswith("http"):
        from fetch_conference import download_conference
        folder = download_conference(args.source, Path(args.folder) if args.folder else None,
                                     args.name, args.oldest_first)
    else:
        folder = Path(args.source)
    out = Path(args.output) if args.output else Path(f"{folder.name}_Study_Journal.pdf")
    build(folder, out, args.logo)
