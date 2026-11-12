"""Word documents (.docx) written with the standard library: a small WordprocessingML package that Word and LibreOffice
open as it is. Styles (title, headings, a passage's time, a language mark), lists (numbering.xml), tables, boxes,
pictures (a lecture's slides), links, formulas as readable text (doc.math_parts: superscripts and subscripts as
Word's own), page numbers in the footer, A4. Text keeps its Turkish letters (the XML is UTF-8), and a Turkish passage
is marked Turkish so Word checks its spelling in Turkish."""

from __future__ import annotations

import datetime as dt
import io
import re
import struct
import zipfile
from pathlib import Path
from typing import Any, Iterable, Optional

from . import doc as D

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
CT_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml"
NS = (f'xmlns:w="{W_NS}" xmlns:r="{R_NS}" '
      'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
      'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"')
HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
LANGS = {"en": "en-US", "tr": "tr-TR", "de": "de-DE", "fr": "fr-FR", "es": "es-ES", "it": "it-IT", "ar": "ar-SA",
         "ru": "ru-RU", "az": "az-Latn-AZ"}
TEXT_W = 9638                      # A4 (11906 twips) less two margins of 2 cm (1134 twips)
PIC_W, PIC_H = 5760000, 3960000    # the largest picture: 16 × 11 cm (EMU)
MATH_FONT = "Cambria Math"
BOX_COLOURS = {"note": ("2563EB", "EFF5FF"), "tip": ("047857", "EFFAF5"), "important": ("7C3AED", "F5F1FF"),
               "warning": ("B45309", "FFF8E6"), "caution": ("C81E1E", "FFF1F1"), "definition": ("2563EB", "EFF5FF"),
               "key": ("7C3AED", "F5F1FF"), "example": ("047857", "EFFAF5"), "mistake": ("C2410C", "FFF4ED"),
               "check": ("A16207", "FDF9EC"), "quote": ("C9C9C9", "FFFFFF")}
_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿\ud800-\udfff]")
_STYLES = {"": "", "meta": "Meta", "small": "Small", "caption": "Caption", "who": "Who", "question": "Question"}


def _x(s: str) -> str:
    """Text for XML: without the characters XML can't hold, with &, < and > written out."""
    return _BAD.sub("", s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _a(s: str) -> str:
    return _x(s).replace('"', "&quot;")


def picture_size(data: bytes) -> tuple[int, int]:
    """(width, height) in pixels of a JPEG or PNG, (0, 0) when it can't be told."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            size = struct.unpack(">H", data[i + 2:i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            i += 2 + size
    return 0, 0


class _Writer:
    def __init__(self, doc: D.Doc) -> None:
        self.doc = doc
        self.lang = LANGS.get(doc.lang, "en-US")
        self.rels: list[tuple[str, str, str, bool]] = []          # (id, type, target, external)
        self.media: list[tuple[str, bytes]] = []
        self.nums: list[tuple[int, dict[int, int]]] = []          # an ordered list's number: (numId, starts by level)
        self.pics = 0

    def rel(self, kind: str, target: str, external: bool = False) -> str:
        rid = f"rId{len(self.rels) + 10}"
        self.rels.append((rid, kind, target, external))
        return rid

    # ---------------------------------------------------------------- runs
    @staticmethod
    def r(text: str, *, bold: bool = False, italic: bool = False, style: str = "", font: str = "", vert: str = "",
          lang: str = "") -> str:
        pr = []
        if style:
            pr.append(f'<w:rStyle w:val="{style}"/>')
        if font:
            pr.append(f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:cs="{font}"/>')
        if bold:
            pr.append("<w:b/><w:bCs/>")
        if italic:
            pr.append("<w:i/><w:iCs/>")
        if vert:
            pr.append(f'<w:vertAlign w:val="{"superscript" if vert == "sup" else "subscript"}"/>')
        if lang:
            pr.append(f'<w:lang w:val="{lang}"/>')
        body = []
        for k, line in enumerate(text.split("\n")):
            if k:
                body.append("<w:br/>")
            for j, piece in enumerate(line.split("\t")):
                if j:
                    body.append("<w:tab/>")
                if piece:
                    body.append(f'<w:t xml:space="preserve">{_x(piece)}</w:t>')
        if not body:
            return ""
        return f"<w:r>{'<w:rPr>' + ''.join(pr) + '</w:rPr>' if pr else ''}{''.join(body)}</w:r>"

    def math(self, tex: str, bold: bool = False, inline: bool = True) -> str:
        out = []
        for s, pos, italic in D.math_parts(tex):
            out.append(self.r(s.replace("\n", "; ") if inline else s, bold=bold, italic=italic, font=MATH_FONT,
                              vert=pos))
        return "".join(out)

    def runs(self, runs: Iterable[D.Run], lang: str = "", bold: bool = False) -> str:
        out = []
        for run in runs:
            if run.math:
                out.append(self.math(run.text, run.bold or bold))
                continue
            style = {"time": "Time", "tag": "Tag", "dim": "Dim"}.get(run.kind, "") or ("CodeChar" if run.code else "")
            link = run.link if run.link.startswith(("http://", "https://")) else ""
            x = self.r(run.text, bold=run.bold or bold, italic=run.italic, style="Hyperlink" if link else style,
                       lang=lang)
            if link and x:
                x = f'<w:hyperlink r:id="{self.rel(REL + "hyperlink", link, True)}" w:history="1">{x}</w:hyperlink>'
            out.append(x)
        return "".join(out)

    # ---------------------------------------------------------------- paragraphs and blocks
    @staticmethod
    def p(body: str, style: str = "", *, keep: bool = False, page: bool = False, num: Optional[tuple[int, int]] = None,
          box: Optional[tuple[str, str]] = None, ind: int = 0, jc: str = "", bottom: str = "") -> str:
        pr = []
        if style:
            pr.append(f'<w:pStyle w:val="{style}"/>')
        if keep:
            pr.append("<w:keepNext/>")
        if page:
            pr.append("<w:pageBreakBefore/>")
        if num:
            pr.append(f'<w:numPr><w:ilvl w:val="{num[1]}"/><w:numId w:val="{num[0]}"/></w:numPr>')
        if box or bottom:
            pr.append("<w:pBdr>" + (f'<w:left w:val="single" w:sz="18" w:space="8" w:color="{box[0]}"/>' if box else "")
                      + (f'<w:bottom w:val="single" w:sz="6" w:space="4" w:color="{bottom}"/>' if bottom else "")
                      + "</w:pBdr>")
            if box and box[1] != "FFFFFF":
                pr.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{box[1]}"/>')
        if box or ind:
            pr.append(f'<w:ind w:left="{ind + (220 if box else 0)}"' + (' w:right="160"/>' if box else "/>"))
        if jc:
            pr.append(f'<w:jc w:val="{jc}"/>')
        return f"<w:p>{'<w:pPr>' + ''.join(pr) + '</w:pPr>' if pr else ''}{body}</w:p>"

    def blocks(self, blocks: Iterable[Any], ind: int = 0, box: Optional[tuple[str, str]] = None) -> list[str]:
        out: list[str] = []
        for b in blocks:
            if isinstance(b, D.Markdown):
                from ..doclang import writing, writing_in

                with writing_in(b.lang or writing()):
                    out += self.blocks(b.blocks(), ind, box)
            elif isinstance(b, D.Heading):
                out.append(self.p(self.runs(b.runs), f"Heading{max(1, min(4, b.level))}", page=b.page, box=box,
                                  ind=ind))
            elif isinstance(b, D.Para):
                lang = LANGS.get(b.lang, "") if b.lang and b.lang != self.doc.lang else ""
                if b.style == "who" and self.lang != "en-US":
                    lang = "en-US"                  # an English label in capitals: "WITH", not Turkish "WİTH"
                out.append(self.p(self.runs(b.runs, lang), _STYLES.get(b.style, ""), keep=b.style == "who", box=box,
                                  ind=ind))
            elif isinstance(b, D.Items):
                out += self.items(b, ind, box)
            elif isinstance(b, D.Table):
                out += self.table(b)
            elif isinstance(b, D.Figure):
                out += self.figure(b)
            elif isinstance(b, D.Rule):
                out.append(self.p("", "Rule", bottom="D9D9D9"))
            elif isinstance(b, D.Box):
                colours = BOX_COLOURS.get(b.kind, BOX_COLOURS["note"])
                name = D.box_label(b.kind)
                title = D.plain(b.title)
                if name:
                    head = self.r(name, bold=True)
                    if title and title != name:
                        head += self.r(" · ") + self.runs(b.title)
                else:
                    head = self.runs(b.title, bold=True)
                if head:
                    out.append(self.p(head, "BoxTitle", keep=True, box=colours, ind=ind))
                out += self.blocks(b.blocks, ind, colours) or [self.p("", box=colours, ind=ind)]
            elif isinstance(b, D.Math):
                out.append(self.p(self.math(b.tex, inline=False), "Formula", box=box, ind=ind))
            elif isinstance(b, D.Code):
                out.append(self.p(self.r(b.text.rstrip("\n"), style="CodeChar"), "CodeBlock", box=box, ind=ind))
        return out

    def items(self, b: D.Items, ind: int, box: Optional[tuple[str, str]]) -> list[str]:
        num = 0
        if any(it.ordered for it in b.items):                     # each numbered list counts from its own start
            starts: dict[int, int] = {}
            for it in b.items:
                if it.ordered:
                    starts.setdefault(min(8, it.level), max(0, it.number))
            num = len(self.nums) + 2
            self.nums.append((num, starts))
        out = []
        for it in b.items:
            level = min(8, it.level)
            out.append(self.p(self.runs(it.runs), "ListParagraph", num=(num if it.ordered else 1, level), box=box))
            out += self.blocks(it.more, ind + 720 * (level + 1), box)
        return out

    def table(self, b: D.Table) -> list[str]:
        if not b.rows:
            return []
        cols = max(len(r) for r in b.rows) or 1
        width = TEXT_W // cols
        rows = []
        for i, row in enumerate(b.rows):
            head = b.header and i == 0
            cells = []
            for j in range(cols):
                runs = row[j] if j < len(row) else []
                shade = '<w:shd w:val="clear" w:color="auto" w:fill="F2F2F2"/>' if head else ""
                cells.append(f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shade}</w:tcPr>'
                             f'{self.p(self.runs(runs, bold=head), "TableText")}</w:tc>')
            rows.append(f"<w:tr>{'<w:trPr><w:tblHeader/></w:trPr>' if head else ''}{''.join(cells)}</w:tr>")
        grid = "".join(f'<w:gridCol w:w="{width}"/>' for _ in range(cols))
        return [f'<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="5000" w:type="pct"/>'
                f'<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="0" w:lastColumn="0" '
                f'w:noHBand="0" w:noVBand="1"/></w:tblPr><w:tblGrid>{grid}</w:tblGrid>{"".join(rows)}</w:tbl>',
                self.p("", "AfterTable")]

    def figure(self, b: D.Figure) -> list[str]:
        caption = [self.p(self.runs(b.caption), "Caption")] if b.caption else []
        try:
            data = Path(b.path).read_bytes()
        except OSError:
            return caption
        ext = "jpeg" if data[:2] == b"\xff\xd8" else "png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ""
        if not ext:
            return caption
        w, h = picture_size(data)
        if not (w and h):
            w, h = 16, 9
        scale = min(PIC_W / w, PIC_H / h)
        cx, cy = int(w * scale), int(h * scale)
        self.pics += 1
        n = self.pics
        name = f"image{n}.{ext}"
        self.media.append((name, data))
        rid = self.rel(REL + "image", f"media/{name}")
        alt = _a(D.plain(b.caption))
        drawing = ('<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
                   f'<wp:extent cx="{cx}" cy="{cy}"/>'
                   f'<wp:effectExtent l="0" t="0" r="0" b="0"/><wp:docPr id="{n}" name="Picture {n}" descr="{alt}"/>'
                   '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
                   '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                   f'<pic:pic><pic:nvPicPr><pic:cNvPr id="{n}" name="{name}"/><pic:cNvPicPr/></pic:nvPicPr>'
                   f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
                   f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
                   '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>'
                   "</wp:inline></w:drawing></w:r>")
        return [self.p(drawing, "Picture", keep=True)] + caption

    # ---------------------------------------------------------------- the parts of the package
    def document(self) -> str:
        d = self.doc
        body = []
        if d.kicker:                                 # (English, in capitals: English rules for them)
            body.append(self.p(self.r(d.kicker, lang="en-US" if self.lang != "en-US" else ""), "Kicker"))
        body.append(self.p(self.r(d.title), "Title"))
        if d.subtitle:
            body.append(self.p(self.r(d.subtitle), "Subtitle"))
        for m in d.meta:
            body.append(self.p(self.runs(m), "Meta"))
        body.append(self.p("", "CoverEnd", bottom="161616"))
        body += self.blocks(d.blocks)
        footer = self.rel(REL + "footer", "footer1.xml")
        sect = (f'<w:sectPr><w:footerReference w:type="default" r:id="{footer}"/><w:pgSz w:w="11906" w:h="16838"/>'
                '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="567" w:footer="567" '
                'w:gutter="0"/><w:cols w:space="708"/></w:sectPr>')
        return f"{HEAD}<w:document {NS}><w:body>{''.join(body)}{sect}</w:body></w:document>"

    def numbering(self) -> str:
        bullets = "•◦▪"
        lvl_b = "".join(f'<w:lvl w:ilvl="{i}"><w:start w:val="1"/><w:numFmt w:val="bullet"/>'
                        f'<w:lvlText w:val="{bullets[i % 3]}"/><w:lvlJc w:val="left"/>'
                        f'<w:pPr><w:ind w:left="{720 * (i + 1)}" w:hanging="360"/></w:pPr></w:lvl>' for i in range(9))
        fmts = ("decimal", "lowerLetter", "lowerRoman")
        lvl_n = "".join(f'<w:lvl w:ilvl="{i}"><w:start w:val="1"/><w:numFmt w:val="{fmts[i % 3]}"/>'
                        f'<w:lvlText w:val="%{i + 1}."/><w:lvlJc w:val="left"/>'
                        f'<w:pPr><w:ind w:left="{720 * (i + 1)}" w:hanging="360"/></w:pPr></w:lvl>' for i in range(9))
        nums = ['<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>']
        for num, starts in self.nums:
            over = "".join(f'<w:lvlOverride w:ilvl="{lvl}"><w:startOverride w:val="{start}"/></w:lvlOverride>'
                           for lvl, start in sorted({0: 1, **starts}.items()))
            nums.append(f'<w:num w:numId="{num}"><w:abstractNumId w:val="1"/>{over}</w:num>')
        multi = '<w:multiLevelType w:val="hybridMultilevel"/>'
        return (f'{HEAD}<w:numbering xmlns:w="{W_NS}">'
                f'<w:abstractNum w:abstractNumId="0">{multi}{lvl_b}</w:abstractNum>'
                f'<w:abstractNum w:abstractNumId="1">{multi}{lvl_n}</w:abstractNum>'
                f'{"".join(nums)}</w:numbering>')

    def styles(self) -> str:
        def para(sid: str, name: str, ppr: str = "", rpr: str = "", based: str = "Normal", nxt: str = "",
                 extra: str = "") -> str:
            after = '<w:next w:val="%s"/>' % nxt if nxt else ""
            ppr = f"<w:pPr>{ppr}</w:pPr>" if ppr else ""
            rpr = f"<w:rPr>{rpr}</w:rPr>" if rpr else ""
            return (f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/>'
                    f'<w:basedOn w:val="{based}"/>{after}{extra}{ppr}{rpr}</w:style>')

        def char(sid: str, name: str, rpr: str) -> str:
            return (f'<w:style w:type="character" w:styleId="{sid}"><w:name w:val="{name}"/>'
                    f'<w:basedOn w:val="DefaultParagraphFont"/><w:rPr>{rpr}</w:rPr></w:style>')

        def size(half_points: int) -> str:
            return f'<w:sz w:val="{half_points}"/><w:szCs w:val="{half_points}"/>'

        q = "<w:qFormat/>"
        heading = lambda n, sz, before, ppr="": para(       # noqa: E731
            f"Heading{n}", f"heading {n}", f'<w:keepNext/><w:keepLines/>{ppr}<w:spacing w:before="{before}" '
            f'w:after="100"/><w:outlineLvl w:val="{n - 1}"/>', f'<w:b/><w:bCs/><w:color w:val="161616"/>{size(sz)}',
            nxt="Normal", extra=q)
        border = '<w:pBdr><w:bottom w:val="single" w:sz="4" w:space="2" w:color="E2E2E2"/></w:pBdr>'
        cell = '<w:{0} w:w="{1}" w:type="dxa"/>'
        line = '<w:{0} w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
        styles = [
            f'<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:eastAsia="Calibri" w:hAnsi="Calibri" '
            f'w:cs="Calibri"/>{size(22)}<w:lang w:val="{self.lang}" w:eastAsia="en-US" w:bidi="ar-SA"/></w:rPr>'
            '</w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr>'
            "</w:pPrDefault></w:docDefaults>",
            f'<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>{q}</w:style>',
            '<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont"><w:name w:val="Default '
            'Paragraph Font"/><w:uiPriority w:val="1"/><w:semiHidden/><w:unhideWhenUsed/></w:style>',
            '<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/>'
            '<w:uiPriority w:val="99"/><w:semiHidden/><w:unhideWhenUsed/><w:tblPr><w:tblInd w:w="0" w:type="dxa"/>'
            f'<w:tblCellMar>{cell.format("top", 0)}{cell.format("left", 108)}{cell.format("bottom", 0)}'
            f'{cell.format("right", 108)}</w:tblCellMar></w:tblPr></w:style>',
            '<w:style w:type="numbering" w:default="1" w:styleId="NoList"><w:name w:val="No List"/>'
            '<w:uiPriority w:val="99"/><w:semiHidden/><w:unhideWhenUsed/></w:style>',
            para("Title", "Title", '<w:spacing w:before="0" w:after="60"/>',
                 f'<w:b/><w:bCs/><w:color w:val="161616"/>{size(40)}', nxt="Normal", extra=q),
            para("Subtitle", "Subtitle", '<w:spacing w:after="80"/>', f'<w:color w:val="5C5C5C"/>{size(21)}',
                 nxt="Normal", extra=q),
            para("Kicker", "Kicker", '<w:spacing w:after="40"/>',
                 f'<w:b/><w:bCs/><w:caps/><w:color w:val="1D4ED8"/><w:spacing w:val="10"/>{size(17)}'),
            para("Meta", "Meta", '<w:spacing w:after="20"/>', f'<w:color w:val="5C5C5C"/>{size(19)}'),
            para("CoverEnd", "Cover end", '<w:spacing w:after="240"/>', size(8)),
            heading(1, 30, 360, border), heading(2, 25, 280), heading(3, 23, 220), heading(4, 22, 200),
            para("Small", "Small", "", f'<w:color w:val="5C5C5C"/>{size(19)}'),
            para("Who", "Who", '<w:keepNext/><w:spacing w:before="240" w:after="40"/>',
                 f'<w:b/><w:bCs/><w:caps/><w:color w:val="1D4ED8"/>{size(17)}'),
            para("Question", "Question", '<w:pBdr><w:left w:val="single" w:sz="18" w:space="8" w:color="C9C9C9"/>'
                 '</w:pBdr><w:shd w:val="clear" w:color="auto" w:fill="F4F4F4"/><w:ind w:left="220" w:right="160"/>'),
            para("ListParagraph", "List Paragraph", '<w:spacing w:after="60"/>', extra=q),
            para("BoxTitle", "Box title", '<w:keepNext/><w:spacing w:before="120" w:after="40"/>', size(19)),
            para("Formula", "Formula", '<w:spacing w:before="60" w:after="120"/><w:jc w:val="center"/>'),
            para("CodeBlock", "Code block", '<w:shd w:val="clear" w:color="auto" w:fill="F6F6F6"/>'
                 '<w:spacing w:after="120" w:line="240" w:lineRule="auto"/>'),
            para("Picture", "Picture", '<w:keepNext/><w:spacing w:before="120" w:after="40"/><w:jc w:val="center"/>'),
            para("Caption", "caption", '<w:spacing w:after="160"/><w:jc w:val="center"/>',
                 f'<w:i/><w:iCs/><w:color w:val="5C5C5C"/>{size(18)}', extra=q),
            para("TableText", "Table text", '<w:spacing w:after="0" w:line="252" w:lineRule="auto"/>', size(20)),
            para("AfterTable", "After table", '<w:spacing w:before="0" w:after="0" w:line="160" w:lineRule="exact"/>',
                 size(8)),
            para("Rule", "Rule", '<w:spacing w:before="120" w:after="240"/>', size(8)),
            para("Footer", "footer", '<w:jc w:val="center"/>', f'<w:color w:val="8A8A8A"/>{size(17)}'),
            char("Time", "Time", '<w:b/><w:bCs/><w:color w:val="1D4ED8"/>'),
            char("Tag", "Language mark", f'<w:b/><w:bCs/><w:color w:val="047857"/>{size(16)}'),
            char("Dim", "Quiet text", '<w:color w:val="6B6B6B"/>'),
            char("CodeChar", "Code", '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/>'
                 '<w:noProof/>' + size(19)),
            char("Hyperlink", "Hyperlink", '<w:color w:val="1D4ED8"/><w:u w:val="single"/>'),
            '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:basedOn w:val="TableNormal"/>'
            '<w:uiPriority w:val="39"/><w:tblPr><w:tblBorders>' + "".join(line.format(x) for x in (
                "top", "left", "bottom", "right", "insideH", "insideV")) + "</w:tblBorders><w:tblCellMar>"
            f'{cell.format("top", 40)}{cell.format("left", 100)}{cell.format("bottom", 40)}{cell.format("right", 100)}'
            "</w:tblCellMar></w:tblPr></w:style>",
        ]
        return f'{HEAD}<w:styles xmlns:w="{W_NS}">{"".join(styles)}</w:styles>'

    def footer(self) -> str:
        return (f'{HEAD}<w:ftr xmlns:w="{W_NS}" xmlns:r="{R_NS}"><w:p><w:pPr><w:pStyle w:val="Footer"/></w:pPr>'
                '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                '<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
                '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r>'
                '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p></w:ftr>')

    def package(self, now: Optional[dt.datetime] = None) -> bytes:
        document = self.document()                 # first: it adds the relationships, pictures and lists
        stamp = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rels = [("rId1", REL + "styles", "styles.xml", False), ("rId2", REL + "numbering", "numbering.xml", False),
                ("rId3", REL + "settings", "settings.xml", False)] + self.rels
        external = ' TargetMode="External"'
        doc_rels = "".join(f'<Relationship Id="{i}" Type="{k}" Target="{_a(t)}"{external if ext else ""}/>'
                           for i, k, t, ext in rels)
        types = (f'{HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                 '<Default Extension="xml" ContentType="application/xml"/>'
                 '<Default Extension="jpeg" ContentType="image/jpeg"/>'
                 '<Default Extension="png" ContentType="image/png"/>'
                 f'<Override PartName="/word/document.xml" ContentType="{CT_MAIN}.document.main+xml"/>'
                 f'<Override PartName="/word/styles.xml" ContentType="{CT_MAIN}.styles+xml"/>'
                 f'<Override PartName="/word/numbering.xml" ContentType="{CT_MAIN}.numbering+xml"/>'
                 f'<Override PartName="/word/settings.xml" ContentType="{CT_MAIN}.settings+xml"/>'
                 f'<Override PartName="/word/footer1.xml" ContentType="{CT_MAIN}.footer+xml"/>'
                 '<Override PartName="/docProps/core.xml" '
                 'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                 '<Override PartName="/docProps/app.xml" '
                 'ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>')
        root_rels = (f'{HEAD}<Relationships xmlns="{PKG_RELS}">'
                     f'<Relationship Id="rId1" Type="{REL}officeDocument" Target="word/document.xml"/>'
                     '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/'
                     'metadata/core-properties" Target="docProps/core.xml"/>'
                     f'<Relationship Id="rId3" Type="{REL}extended-properties" Target="docProps/app.xml"/>'
                     "</Relationships>")
        core = (f'{HEAD}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
                'core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" '
                'xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
                'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                f"<dc:title>{_x(self.doc.title)}</dc:title><dc:creator>IEUStudia</dc:creator>"
                f"<cp:lastModifiedBy>IEUStudia</cp:lastModifiedBy><dc:language>{self.lang}</dc:language>"
                f'<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created>'
                f'<dcterms:modified xsi:type="dcterms:W3CDTF">{stamp}</dcterms:modified></cp:coreProperties>')
        app = (f'{HEAD}<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
               'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
               "<Application>IEUStudia</Application></Properties>")
        settings = (f'{HEAD}<w:settings xmlns:w="{W_NS}"><w:zoom w:percent="100"/><w:defaultTabStop w:val="720"/>'
                    '<w:characterSpacingControl w:val="doNotCompress"/><w:compat><w:compatSetting '
                    'w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/>'
                    "</w:compat></w:settings>")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", types)
            z.writestr("_rels/.rels", root_rels)
            z.writestr("docProps/core.xml", core)
            z.writestr("docProps/app.xml", app)
            z.writestr("word/document.xml", document)
            z.writestr("word/_rels/document.xml.rels", f'{HEAD}<Relationships xmlns="{PKG_RELS}">{doc_rels}'
                                                        "</Relationships>")
            z.writestr("word/styles.xml", self.styles())
            z.writestr("word/numbering.xml", self.numbering())
            z.writestr("word/settings.xml", settings)
            z.writestr("word/footer1.xml", self.footer())
            for name, data in self.media:
                z.writestr(f"word/media/{name}", data, compress_type=zipfile.ZIP_STORED)
        return buf.getvalue()


def docx_bytes(doc: D.Doc, now: Optional[dt.datetime] = None) -> bytes:
    """The document as a Word file (its bytes); its boxes' labels in its language."""
    from ..doclang import writing_in

    with writing_in(doc.lang):
        return _Writer(doc).package(now)
