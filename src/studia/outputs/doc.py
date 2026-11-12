"""A document IEUStudia saves for a student to read, print or edit (exports.py): a lecture's transcript, its notes or
AI pack, a chat, a week or a whole course. Its parts (headings, paragraphs with their times, lists, tables, boxes,
formulas, pictures) are written as Word (docx.py), as a page in the app's document look that Edge prints to PDF
(to_html), as Markdown (to_markdown) or as plain text (to_text).

Markdown the AI wrote (study notes, chat answers) stays as it was written in a Markdown file and in the PDF (the study
pack's own renderer draws it); Word and plain text get its parts (read_markdown). Formulas stay LaTeX where something
draws them (Markdown viewers, KaTeX in the PDF) and become readable text in Word and plain text (math_parts,
math_text): x², √(a+b), (a)/(b), α ≤ β."""

from __future__ import annotations

import dataclasses as dc
import html
import re
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from ..doclang import word, writing_in
from .common import md_escape, md_link
from .figures import IMAGE_LINE, caption_parts, image_html, linked_images, local_image


# ------------------------------------------------------------------ the parts
@dc.dataclass
class Run:
    """A piece of a line."""

    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    math: bool = False                # ``text`` is a formula (LaTeX)
    kind: str = ""                    # "time": a passage's [mm:ss] · "tag": a language mark (TR) · "dim": quieter
    link: str = ""                    # a web address: the run is its text


@dc.dataclass
class Heading:
    level: int                        # 1: a lecture in a course's document, a part of a lecture · 2, 3, 4 below
    runs: list[Run]
    page: bool = False                # starts a new page (each lecture of a course's document)


@dc.dataclass
class Para:
    runs: list[Run]
    style: str = ""                   # "" · "meta" (a line about the document) · "small" · "who" (who speaks in a
    lang: str = ""                    # chat) · "question" (what you asked) · "caption"; lang: its language when
                                      # not the document's (a Turkish passage: Word checks its spelling in Turkish)


@dc.dataclass
class Item:
    runs: list[Run]
    level: int = 0
    ordered: bool = False
    number: int = 1
    more: list[Any] = dc.field(default_factory=list)      # the item's blocks after its first line


@dc.dataclass
class Items:
    items: list[Item]


@dc.dataclass
class Table:
    rows: list[list[list[Run]]]       # rows of cells; the first row is the header
    header: bool = True


@dc.dataclass
class Figure:
    path: str                         # a picture on this PC (JPEG or PNG)
    caption: list[Run]


@dc.dataclass
class Rule:
    pass


@dc.dataclass
class Box:
    kind: str                         # an alert: note · tip · important · warning · caution; a study box: definition ·
    title: list[Run]                  # key · example · mistake · check; or quote
    blocks: list[Any]


@dc.dataclass
class Math:
    tex: str                          # a formula on a line of its own (LaTeX, without its $$)


@dc.dataclass
class Code:
    text: str


@dc.dataclass
class Markdown:
    """Markdown the AI wrote: a Markdown file keeps it as written, the PDF draws it as study packs are drawn."""

    md: str
    top: int = 2                      # the level its "#" headings get in this document
    base: str = ""                    # the folder its pictures are linked from (study notes': the lecture's slides)

    def blocks(self) -> list[Any]:
        return read_markdown(self.md, self.top, self.base)


Block = Union[Heading, Para, Items, Table, Figure, Rule, Box, Math, Code, Markdown]


@dc.dataclass
class Doc:
    title: str
    kicker: str = ""                  # above the title: "EEE 215 · Transcript"
    subtitle: str = ""                # under it
    meta: list[list[Run]] = dc.field(default_factory=list)    # lines about it ("Course: …")
    blocks: list[Any] = dc.field(default_factory=list)
    lang: str = "en"                  # its main language (Word checks the spelling in it)


def R(text: str, **kw: Any) -> Run:
    return Run(str(text), **kw)


def label(name: str, value: str) -> list[Run]:
    """A line about the document: ``Course:`` in bold, then its value."""
    return [Run(f"{name}: ", bold=True), Run(value)]


BOX_LABELS = {"note": "Note", "tip": "Tip", "important": "Important", "warning": "Warning", "caution": "Caution",
              "definition": "Definition", "key": "Key idea", "example": "Example", "mistake": "Common mistake",
              "check": "Check yourself", "quote": ""}
_STUDY_BOXES = {"definition": "definition", "key": "key", "example": "example", "mistake": "mistake", "check": "check",
                "note": "note", "theorem": "key", "tip": "note", "warning": "mistake"}
ALERTS = ("note", "tip", "important", "warning", "caution")


def box_label(kind: str) -> str:
    """A box's label ("Definition", "Example"...) in the language of the document being drawn (doclang.writing_in:
    the document's `lang`)."""
    label = BOX_LABELS.get(kind, "")
    return word(label) if label else ""


def plain(runs: Iterable[Run]) -> str:
    """The runs' text, formulas as readable text."""
    return "".join(math_text(r.text).replace("\n", "; ") if r.math else r.text for r in runs)


def turkish(text: str) -> bool:
    """Is this text (the AI's notes or answer) mostly Turkish? Told by its letters that English doesn't have."""
    letters = sum(1 for c in text if c.isalpha())
    special = sum(text.count(c) for c in "ğışçöüĞİŞÇÖÜ")
    return letters > 40 and special / letters > 0.02


# ------------------------------------------------------------------ Markdown the AI wrote → parts
def _math_rx() -> "re.Pattern[str]":
    from ..studypack import _MATH          # the formulas the PDF's renderer sets aside: the same ones here

    return _MATH


_ITEM = re.compile(r"(\s*)([-*+]|\d+[.)])\s+(.*)")
_ESC = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~<])")


def _unmath(src: str) -> tuple[str, bool]:
    """A formula's LaTeX without its delimiters, and whether it is displayed on a line of its own."""
    s = src.strip()
    for a, b, display in (("$$", "$$", True), ("\\[", "\\]", True), ("\\(", "\\)", False), ("$", "$", False)):
        if s.startswith(a) and s.endswith(b) and len(s) >= len(a) + len(b):
            return s[len(a):len(s) - len(b)].strip(), display
    return s, False


def read_markdown(md: str, top: int = 2, base: str = "") -> list[Any]:
    """The AI's Markdown as parts: headings (``#`` at level ``top``), paragraphs, lists (nested), tables, quotes and
    alerts ("> [!WARNING]"), ":::kind Title" boxes, code, rules, formulas and pictures on a line of their own (one on
    this PC, linked from ``base``: a figure; else left out), read as the PDF's renderer reads them
    (studypack.md_html)."""
    keep: list[str] = []
    code: list[str] = []

    def hold(m: re.Match) -> str:
        text = m.group(0)
        if "\n" in text:            # a formula over lines in a quote: without the "> " of its next lines
            q = re.match(r"[ \t]*((?:>[ \t]?)+)", m.string[m.string.rfind("\n", 0, m.start()) + 1:m.start()])
            if q:
                text = re.sub(r"\n[ \t]*(?:>[ \t]?){1,%d}" % q.group(1).count(">"), "\n", text)
        keep.append(text)
        return f"\x00{len(keep) - 1}\x00"

    md = (md or "").replace("\x00", "").replace("\x01", "").replace("\r\n", "\n")
    md = re.sub(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```[ \t]*$",
                lambda m: (code.append(m.group(1)), f"\x01{len(code) - 1}\x01")[1], md, flags=re.S | re.M)
    md = _math_rx().sub(hold, md)
    return _blocks(md.split("\n"), keep, code, top, base)


def _blocks(lines: list[str], keep: list[str], code: list[str], top: int, base: str = "") -> list[Any]:
    out: list[Any] = []
    para: list[str] = []
    i = 0

    def flush() -> None:
        if para:
            out.append(Para(_runs(" ".join(x.strip() for x in para), keep)))
            para.clear()

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            flush()
            i += 1
            continue
        m = re.fullmatch(r":::\s*(\w+)\s*(.*)", s)
        if m and m.group(1).lower() in _STUDY_BOXES:
            flush()
            j = i + 1
            while j < len(lines) and lines[j].strip() != ":::":
                j += 1
            from ..studypack import box_head

            label, title = box_head(m.group(1).lower(), m.group(2))
            number = label[len(box_label(_STUDY_BOXES[m.group(1).lower()])):].strip()     # "Example 4": "4"
            if number:
                title = f"{number}: {title}" if title else number
            out.append(Box(_STUDY_BOXES[m.group(1).lower()], _runs(title, keep),
                           _blocks(lines[i + 1:j], keep, code, top, base)))
            i = j + 1
            continue
        if s == ":::" or re.fullmatch(r"\[\[\s*FIGURE\s+[\w-]+\s*\]\]", s, re.I):
            i += 1
            continue
        m = IMAGE_LINE.fullmatch(s) if base else None
        if m:                          # a picture on a line of its own (a lecture slide in study notes)
            flush()
            path = local_image(m.group(2), base)
            if path is not None:
                cap, source = caption_parts(m.group(1))
                out.append(Figure(str(path), _runs(cap, keep) + ([Run(f" ({source})", kind="dim")] if source else [])))
            i += 1
            continue
        m = re.fullmatch(r"\x01(\d+)\x01", s)
        if m:
            flush()
            out.append(Code(code[int(m.group(1))].rstrip("\n")))
            i += 1
            continue
        m = re.fullmatch(r"\x00(\d+)\x00", s)
        if m and keep[int(m.group(1))].lstrip().startswith(("$$", "\\[")):
            flush()
            out.append(Math(_unmath(keep[int(m.group(1))])[0]))
            i += 1
            continue
        m = re.fullmatch(r"(#{1,6})\s+(.*?)\s*#*", s)
        if m:
            flush()
            out.append(Heading(min(6, top + len(m.group(1)) - 1), _runs(m.group(2), keep)))
            i += 1
            continue
        if re.fullmatch(r"(?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,}", s):
            flush()
            out.append(Rule())
            i += 1
            continue
        if s.startswith(">"):
            flush()
            j = i
            while j < len(lines) and lines[j].strip().startswith(">"):
                j += 1
            inner = [re.sub(r"^\s*>\s?", "", x) for x in lines[i:j]]
            m = re.fullmatch(r"\[!(\w+)\]\s*(.*)", inner[0].strip())
            if m and m.group(1).lower() in ALERTS:
                kind = m.group(1).lower()
                title = _runs(m.group(2), keep) if m.group(2) else []          # (its label, when it is drawn)
                out.append(Box(kind, title, _blocks(inner[1:], keep, code, top, base)))
            else:
                out.append(Box("quote", [], _blocks(inner, keep, code, top, base)))
            i = j
            continue
        if s.startswith("|") and i + 1 < len(lines) and re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?",
                                                                       lines[i + 1].strip()):
            flush()
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                j += 1

            def cells(row: str) -> list[list[Run]]:
                return [_runs(c.strip(), keep) for c in re.split(r"(?<!\\)\|", row.strip().strip("|"))]

            out.append(Table([cells(lines[i])] + [cells(r) for r in lines[i + 2:j]]))
            i = j
            continue
        if _ITEM.match(line):
            flush()
            j = i
            while j < len(lines):
                if lines[j].strip():
                    if not (_ITEM.match(lines[j]) or lines[j].startswith((" ", "\t"))):
                        break
                    j += 1
                    continue
                k = j                  # a blank line: the list goes on when the next line is an item or indented
                while k < len(lines) and not lines[k].strip():
                    k += 1
                if k == len(lines) or not (_ITEM.match(lines[k]) or re.match(r"(?: {2,}|\t)\S", lines[k])):
                    break
                j = k
            out.append(_list(lines[i:j], keep, code, top, base))
            i = j
            continue
        para.append(line)
        i += 1
    flush()
    return out


def _list(lines: list[str], keep: list[str], code: list[str], top: int, base: str = "") -> Items:
    """List lines as items with their level (by indentation); a line without a marker continues the item above."""
    raw: list[tuple[int, bool, int, list[str]]] = []
    for line in lines:
        m = _ITEM.fullmatch(line)
        if m:
            mark = m.group(2)
            raw.append((len(m.group(1).replace("\t", "    ")), mark[0].isdigit(),
                        int(mark[:-1]) if mark[0].isdigit() else 1, [m.group(3)]))
        elif raw:
            raw[-1][3].append(line.strip())
    indents: list[int] = []
    items = []
    for indent, ordered, number, text in raw:
        while indents and indent < indents[-1]:
            indents.pop()
        if not indents or indent > indents[-1]:
            indents.append(indent)
        while len(text) > 1 and not text[-1]:
            text = text[:-1]
        first, rest = text[0], text[1:]
        more = _blocks(rest, keep, code, top, base) if rest else []
        if more and isinstance(more[0], Para) and rest and rest[0]:          # the first line wrapped onto the next
            runs = _runs(" ".join([first] + rest[:_para_len(rest)]), keep)
            more = _blocks(rest[_para_len(rest):], keep, code, top, base)
        else:
            runs = _runs(first, keep)
        items.append(Item(runs, len(indents) - 1, ordered, number, more))
    return Items(items)


def _para_len(lines: list[str]) -> int:
    """How many of these lines go on the paragraph above them (up to a blank line or something else)."""
    n = 0
    for line in lines:
        s = line.strip()
        if not s or s.startswith((">", "|", ":::", "#", "\x01")) or re.fullmatch(r"\x00\d+\x00", s):
            break
        n += 1
    return n


# marks put in the text while it is read (Unicode's private use: never in what the AI wrote)
_B0, _B1, _I0, _I1, _L0, _L1, _C0 = (chr(0xE100 + i) for i in range(7))
_MARKS = re.compile(f"([{_B0}{_B1}{_I0}{_I1}{_L0}]|{_L1}\\d+{_L1}|{_C0}\\d+{_C0}|\x00\\d+\x00)")


def _runs(text: str, keep: list[str]) -> list[Run]:
    """One line of the AI's Markdown as runs: **bold**, *italic*, `code`, [links](…) and the formulas set aside,
    found as the PDF's renderer finds them (bold first, then italic, so one can be inside the other)."""
    codes: list[str] = []
    links: list[str] = []
    s = _ESC.sub(lambda m: chr(0xE000 + ord(m.group(1))), text)
    s = re.sub(r"`([^`]+)`", lambda m: (codes.append(m.group(1)), f"{_C0}{len(codes) - 1}{_C0}")[1], s)
    s = re.sub(r"^\[[xX]\]", "☑", s.replace("[ ]", "☐"))
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
               lambda m: (links.append(m.group(2)), f"{_L0}{m.group(1)}{_L1}{len(links) - 1}{_L1}")[1], s)
    s = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", _B0 + r"\1" + _B1, s)
    s = re.sub(r"__(?=\S)(.+?)(?<=\S)__", _B0 + r"\1" + _B1, s)
    s = re.sub(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])", _I0 + r"\1" + _I1, s)
    s = re.sub(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])", _I0 + r"\1" + _I1, s)
    out: list[Run] = []
    bold = italic = 0
    link_from = -1
    for piece in _MARKS.split(s):
        if not piece:
            continue
        if piece in (_B0, _B1):
            bold = max(0, bold + (1 if piece == _B0 else -1))
        elif piece in (_I0, _I1):
            italic = max(0, italic + (1 if piece == _I0 else -1))
        elif piece == _L0:
            link_from = len(out)
        elif piece.startswith(_L1):
            url = _unesc(links[int(piece.strip(_L1))])
            for r in out[max(0, link_from):]:
                r.link = url
            link_from = -1
        elif piece.startswith(_C0):
            out.append(Run(_unesc(codes[int(piece.strip(_C0))]), bold=bool(bold), italic=bool(italic), code=True))
        elif piece.startswith("\x00"):
            out.append(Run(_unmath(keep[int(piece.strip(chr(0)))])[0], bold=bool(bold), italic=bool(italic),
                           math=True))
        else:
            out.append(Run(_unesc(piece), bold=bool(bold), italic=bool(italic)))
    return [r for r in out if r.text]


def _unesc(s: str) -> str:
    return re.sub("[\ue000-\ue07f]", lambda m: chr(ord(m.group(0)) - 0xE000), s)


# ------------------------------------------------------------------ formulas as readable text
GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ϵ", "varepsilon": "ε", "zeta": "ζ", "eta": "η",
    "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "omicron": "ο", "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ", "sigma": "σ", "varsigma": "ς", "tau": "τ",
    "upsilon": "υ", "phi": "ϕ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ",
    "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ",
    "Omega": "Ω", "Alpha": "Α", "Beta": "Β", "Epsilon": "Ε", "Zeta": "Ζ", "Eta": "Η", "Iota": "Ι", "Kappa": "Κ",
    "Mu": "Μ", "Nu": "Ν", "Omicron": "Ο", "Rho": "Ρ", "Tau": "Τ", "Chi": "Χ"}
SYMBOLS = {
    "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓", "ast": "∗", "star": "⋆", "circ": "∘", "bullet": "•",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠", "approx": "≈", "sim": "∼", "simeq": "≃",
    "cong": "≅", "equiv": "≡", "propto": "∝", "ll": "≪", "gg": "≫", "leqslant": "⩽", "geqslant": "⩾", "doteq": "≐",
    "infty": "∞", "partial": "∂", "nabla": "∇", "sum": "∑", "prod": "∏", "coprod": "∐", "int": "∫", "iint": "∬",
    "iiint": "∭", "oint": "∮", "to": "→", "rightarrow": "→", "leftarrow": "←", "gets": "←", "Rightarrow": "⇒",
    "implies": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔", "iff": "⇔", "leftrightarrow": "↔", "mapsto": "↦",
    "longrightarrow": "⟶", "Longrightarrow": "⟹", "longleftarrow": "⟵", "uparrow": "↑", "downarrow": "↓",
    "rightleftharpoons": "⇌", "in": "∈", "notin": "∉", "ni": "∋", "subset": "⊂", "subseteq": "⊆", "supset": "⊃",
    "supseteq": "⊇", "subsetneq": "⊊", "cup": "∪", "cap": "∩", "bigcup": "⋃", "bigcap": "⋂", "setminus": "∖",
    "emptyset": "∅", "varnothing": "∅", "forall": "∀", "exists": "∃", "nexists": "∄", "neg": "¬", "lnot": "¬",
    "land": "∧", "wedge": "∧", "lor": "∨", "vee": "∨", "oplus": "⊕", "otimes": "⊗", "odot": "⊙", "angle": "∠",
    "measuredangle": "∡", "triangle": "△", "degree": "°", "prime": "′", "hbar": "ℏ", "ell": "ℓ", "Re": "ℜ", "Im": "ℑ",
    "aleph": "ℵ", "parallel": "∥", "perp": "⊥", "mid": "∣", "nmid": "∤", "langle": "⟨", "rangle": "⟩",
    "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈", "rceil": "⌉", "vert": "|", "lvert": "|", "rvert": "|", "Vert": "‖",
    "lVert": "‖", "rVert": "‖", "ldots": "…", "dots": "…", "dotsc": "…", "dotsb": "⋯", "cdots": "⋯", "vdots": "⋮",
    "ddots": "⋱", "therefore": "∴", "because": "∵", "top": "⊤", "bot": "⊥", "dagger": "†", "checkmark": "✓",
    "square": "□", "Box": "□", "blacksquare": "■", "lbrace": "{", "rbrace": "}", "backslash": "\\", "colon": ":",
    "surd": "√", "complement": "∁", "imath": "ı", "jmath": "ȷ", "wp": "℘", "S": "§", "P": "¶", "copyright": "©",
    "dag": "†", "euro": "€", "textdegree": "°", "celsius": "°C", "ohm": "Ω", "micro": "μ", "AA": "Å", "o": "ø"}
FUNCTIONS = {"sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh", "coth",
             "log", "ln", "lg", "exp", "lim", "limsup", "liminf", "max", "min", "sup", "inf", "det", "dim", "gcd",
             "lcm", "arg", "deg", "ker", "hom", "Pr", "mod", "bmod", "tr", "rank", "sgn", "Var", "Cov", "E"}
ACCENTS = {"hat": "\u0302", "widehat": "\u0302", "bar": "\u0304", "overline": "\u0305", "vec": "\u20d7",
           "overrightarrow": "\u20d7", "dot": "\u0307", "ddot": "\u0308", "tilde": "\u0303", "widetilde": "\u0303",
           "check": "\u030c", "breve": "\u0306", "acute": "\u0301", "grave": "\u0300", "underline": "\u0332",
           "mathring": "\u030a"}
TEXT_CMDS = {"text", "textrm", "textnormal", "textit", "textbf", "textsf", "texttt", "mbox", "hbox", "mathrm", "mathit",
             "mathbf", "mathsf", "mathtt", "operatorname", "boldsymbol", "bm", "pmb", "emph", "textup", "mathnormal"}
DOUBLE = {"N": "ℕ", "Z": "ℤ", "Q": "ℚ", "R": "ℝ", "C": "ℂ", "P": "ℙ", "H": "ℍ", "E": "𝔼", "1": "𝟙"}
SKIP_ARG = {"label", "color", "textcolor", "hspace", "vspace", "phantom", "hphantom", "vphantom", "kern", "mkern"}
NOTHING = {"left", "right", "middle", "big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl", "Bigr", "biggl", "biggr",
           "Biggl", "Biggr", "displaystyle", "textstyle", "scriptstyle", "scriptscriptstyle", "limits", "nolimits",
           "nonumber", "notag", "quad", "qquad", "relax", "strut", "mathstrut", "allowbreak", "nobreak"}
RELATIONS = set("=<>≤≥≠≈∼≃≅≡∝≪≫⩽⩾→←⇒⇐⇔↔↦⟶⟹∈∉∋⊂⊆⊃⊇⊊∣≐⇌:")
BINARY = set("+−±∓×·÷∪∩∧∨⊕⊗")
OPENERS = set("([{⟨|‖,;") | RELATIONS | BINARY
SUP = dict(zip("0123456789+-−=()abcdefghijklmnoprstuvwxyzABDEGHIJKLMNOPRTUVWβγδθφχ′n",
               "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁼⁽⁾ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻᴬᴮᴰᴱᴳᴴᴵᴶᴷᴸᴹᴺᴼᴾᴿᵀᵁⱽᵂᵝᵞᵟᶿᵠᵡ′ⁿ"))
SUB = dict(zip("0123456789+-−=()aehijklmnoprstuvxβγρφχ", "₀₁₂₃₄₅₆₇₈₉₊₋₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓᵦᵧᵨᵩᵪ"))
_TOK = re.compile(r"\\([A-Za-z]+)\*?|\\(.)|(\s+)|(.)", re.S)
_ENVS_LINES = {"align", "aligned", "alignat", "alignedat", "gather", "gathered", "split", "equation", "eqnarray",
               "multline", "flalign"}
_ENVS_MATRIX = {"matrix": ("", ""), "pmatrix": ("(", ")"), "bmatrix": ("[", "]"), "Bmatrix": ("{", "}"),
                "vmatrix": ("|", "|"), "Vmatrix": ("‖", "‖"), "smallmatrix": ("", ""), "array": ("", "")}


@dc.dataclass
class _T:                    # text in a formula
    s: str
    italic: bool = False


@dc.dataclass
class _S:                    # a superscript or subscript
    kind: str                # "sup" | "sub"
    nodes: list[Any]


class _Br:                   # \\: a new line
    pass


class _Col:                  # &: the next column
    pass


def _tokens(tex: str) -> list[tuple[str, str]]:
    out = []
    for m in _TOK.finditer(tex):
        if m.group(1):
            out.append(("cmd", m.group(1)))
        elif m.group(2) is not None:
            out.append(("sym", m.group(2)))
        elif m.group(3):
            out.append(("space", " "))
        else:
            out.append(("ch", m.group(4)))
    return out


class _Parser:
    def __init__(self, tex: str) -> None:
        self.tok = _tokens(tex)
        self.i = 0

    def peek(self, skip_space: bool = True) -> Optional[tuple[str, str]]:
        j = self.i
        while skip_space and j < len(self.tok) and self.tok[j][0] == "space":
            j += 1
        return self.tok[j] if j < len(self.tok) else None

    def take(self) -> Optional[tuple[str, str]]:
        while self.i < len(self.tok) and self.tok[self.i][0] == "space":
            self.i += 1
        if self.i >= len(self.tok):
            return None
        self.i += 1
        return self.tok[self.i - 1]

    def raw_group(self) -> str:
        """The text of a {…} argument as written (an environment's name, a colour)."""
        nxt = self.take()
        if nxt is None:
            return ""
        if nxt != ("ch", "{"):
            return nxt[1]
        depth, out = 1, []
        while self.i < len(self.tok):
            kind, v = self.tok[self.i]
            self.i += 1
            if (kind, v) == ("ch", "{"):
                depth += 1
            elif (kind, v) == ("ch", "}"):
                depth -= 1
                if not depth:
                    break
            out.append(("\\" + v) if kind == "cmd" else v)
        return "".join(out)

    def optional(self) -> Optional[list[Any]]:
        """An [optional] argument (\\sqrt[3]{x})."""
        if self.peek() != ("ch", "["):
            return None
        self.take()
        out: list[Any] = []
        while self.peek() not in (None, ("ch", "]")):
            out += self.atom(text=False)
        self.take()
        return out

    def arg(self, text: bool = False) -> list[Any]:
        """The next argument: a {group} or one token."""
        nxt = self.peek()
        if nxt is None:
            return []
        if nxt == ("ch", "{"):
            self.take()
            return self.until("}", text)
        return self.atom(text)

    def until(self, close: str, text: bool = False) -> list[Any]:
        out: list[Any] = []
        while True:
            nxt = self.peek(skip_space=not text)
            if nxt is None:
                return out
            if nxt == ("ch", close):
                self.take()
                return out
            if text and nxt[0] == "space":
                self.i += 1
                out.append(_T(" "))
                continue
            out += self.atom(text)

    def parse(self) -> list[Any]:
        out: list[Any] = []
        while self.peek() is not None:
            out += self.atom(False)
        return out

    def atom(self, text: bool) -> list[Any]:
        tok = self.take()
        if tok is None:
            return []
        kind, v = tok
        if kind == "ch":
            if v == "{":
                return self.until("}", text)
            if v in "^_" and not text:
                return [self.script("sup" if v == "^" else "sub")]
            if v == "&":
                return [_Col()]
            if v == "~":
                return [_T(" ")]
            if v == "'":
                return [_T("′")]
            if v == "-" and not text:
                return [_T("−")]
            if v == "}":
                return []
            return [_T(v, italic=not text and v.isalpha())]
        if kind == "sym":
            if v == "\\":
                return [_Br()]
            if v in ",;:> ":
                return [_T(" ")]
            if v == "!":
                return []
            if v == "|":
                return [_T("‖")]
            return [_T(v)]
        if kind == "space":
            return [_T(" ")] if text else []
        return self.command(v, text)

    def script(self, kind: str) -> Any:
        nxt = self.peek()
        if nxt in (("cmd", "circ"), ("cmd", "degree")) and kind == "sup":
            self.take()
            return _T("°")
        if nxt == ("cmd", "prime") and kind == "sup":
            self.take()
            return _T("′")
        nodes = self.arg()
        if kind == "sup" and _flat(nodes) in ("\\circ", "∘", "°"):
            return _T("°")
        return _S(kind, nodes)

    def command(self, name: str, text: bool) -> list[Any]:
        if name in GREEK:
            return [_T(GREEK[name], italic=name[0].islower())]
        if name in SYMBOLS:
            return [_T(SYMBOLS[name])]
        if name in FUNCTIONS:
            return [_T(name)] + self.gap()
        if name in ("frac", "dfrac", "tfrac", "cfrac"):
            a, b = self.arg(), self.arg()
            return _wrap_simple(a) + [_T("/")] + _wrap_simple(b)
        if name == "binom" or name in ("dbinom", "tbinom"):
            a, b = self.arg(), self.arg()
            return [_T("C(")] + a + [_T(", ")] + b + [_T(")")]
        if name == "sqrt":
            n = self.optional()
            x = self.arg()
            root = {"2": "√", "3": "∛", "4": "∜"}.get(_flat(n or [_T("2")]))
            head = [_T(root)] if root else [_S("sup", n or []), _T("√")]
            return head + _wrap_simple(x)
        if name == "begin":
            return self.environment(self.raw_group())
        if name == "end":
            self.raw_group()
            return []
        if name in TEXT_CMDS:
            nodes = self.arg(text=name.startswith(("text", "mbox", "hbox", "emph")))
            return [_T(n.s) if isinstance(n, _T) else n for n in nodes] + (self.gap() if name == "operatorname" else [])
        if name in ("mathbb", "mathbbm", "mathds"):
            s = _flat(self.arg())
            return [_T("".join(DOUBLE.get(c, c) for c in s))]
        if name in ("mathcal", "mathscr", "mathfrak"):
            return [_T(_flat(self.arg()))]
        if name in ACCENTS:
            nodes = self.arg()
            s = _flat(nodes)
            mark = ACCENTS[name]
            if name in ("overline", "underline", "bar") and len(s) > 1:
                return [_T("".join(c + mark for c in s))]
            return [_T(s + mark, italic=len(s) == 1 and s.isalpha())]
        if name in ("overset", "underset", "stackrel"):
            self.arg()
            return self.arg()
        if name in ("overbrace", "underbrace", "boxed", "fbox", "cancel", "bcancel", "xcancel", "underbracket"):
            return self.arg()
        if name == "not":
            nxt = self.atom(text)
            s = _flat(nxt)
            negated = {"=": "≠", "∈": "∉", "⊂": "⊄", "⊆": "⊈", "≡": "≢", "<": "≮", ">": "≯", "∣": "∤"}
            return [_T(negated.get(s, s +"\u0338"))]
        if name == "pmod":
            return [_T(" (mod ")] + self.arg() + [_T(")")]
        if name in ("SI", "qty"):
            return self.arg() + [_T(" ")] + self.arg()
        if name in ("si", "unit", "num", "ensuremath", "mathop", "mathrel", "mathbin", "mathord", "operatorname*"):
            return self.arg()
        if name == "ce":
            return [_T(_chem(self.raw_group()))]
        if name == "tag":
            return [_T("  (")] + self.arg(text=True) + [_T(")")]
        if name in SKIP_ARG:
            self.raw_group()
            return [_T(" ")] if name in ("hspace", "kern", "mkern") else []
        if name in NOTHING:
            if name in ("left", "right", "middle") and self.peek() == ("ch", "."):
                self.take()
            return [_T("  ")] if name in ("quad", "qquad") else []
        if name in ("newline", "cr"):
            return [_Br()]
        return [_T(name)]                  # an unknown command: its name, readable enough

    def gap(self) -> list[Any]:
        """The space after a function's name ("sin x"), unless brackets or a script follow ("sin(x)", "lim_x")."""
        nxt = self.peek()
        tight = nxt is None or (nxt[0] == "ch" and nxt[1] in "(^_{[")
        return [] if tight else [_T(" ")]

    def environment(self, env: str) -> list[Any]:
        base = env.rstrip("*")
        if base in ("array", "alignat", "alignedat"):
            self.raw_group()
        depth = 1
        start = self.i
        while self.i < len(self.tok):
            kind, v = self.tok[self.i]
            if kind == "cmd" and v in ("begin", "end"):
                save = self.i
                self.i += 1
                name = self.raw_group()
                if name == env:
                    depth += 1 if v == "begin" else -1
                    if not depth:
                        inner = self.tok[start:save]
                        break
                continue
            self.i += 1
        else:
            inner = self.tok[start:]
        sub = _Parser("")
        sub.tok = inner
        nodes = sub.parse()
        rows: list[list[list[Any]]] = [[[]]]
        for n in nodes:
            if isinstance(n, _Br):
                rows.append([[]])
            elif isinstance(n, _Col):
                rows[-1].append([])
            else:
                rows[-1][-1].append(n)
        rows = [r for r in rows if any(c for c in r)]
        if base in _ENVS_MATRIX:
            a, b = _ENVS_MATRIX[base]
            out: list[Any] = [_T(a)]
            for k, row in enumerate(rows):
                if k:
                    out.append(_T("; "))
                for j, cell in enumerate(row):
                    out += ([_T(", ")] if j else []) + cell
            return out + [_T(b)]
        if base in ("cases", "dcases", "rcases"):
            out = [_T("{ ")]
            for k, row in enumerate(rows):
                rest = [_T(", ")] + [x for c in row[1:] for x in c] if len(row) > 1 else []
                out += ([_T("; ")] if k else []) + row[0] + rest
            return out
        out = []
        for k, row in enumerate(rows):            # align, gather, equation …: each row on its line
            out += ([_Br()] if k else []) + [x for c in row for x in c]
        return out


def _flat(nodes: Optional[list[Any]]) -> str:
    out = []
    for n in nodes or []:
        if isinstance(n, _T):
            out.append(n.s)
        elif isinstance(n, _S):
            out.append(_script_text(n.kind, _flat(n.nodes)))
    return "".join(out)


def _wrap_simple(nodes: list[Any]) -> list[Any]:
    """A fraction's part or a root's: in brackets when it is more than one number, one letter or symbol (with its
    marks), a dx or a call such as P(B): "(a + b)/(2c)", but "dy/dx" and "1/P(B)"."""
    s = re.sub(r"[̀-ͯ⃐-⃿′″°]", "", _flat(nodes).strip())
    s = re.sub(f"[{''.join(SUP.values())}{''.join(SUB.values())}]", "", s) or s
    if (len(s) <= 1 or re.fullmatch(r"\d+(?:[.,]\d+)?|d\w|[\w]+\([^()]*\)", s)
            or (s.startswith("(") and s.endswith(")") and s.count("(") == 1)):
        return nodes
    return [_T("(")] + nodes + [_T(")")]


def _script_text(kind: str, s: str) -> str:
    """A superscript or subscript as plain text: ², ₙ, or ^(…) / _(…) when Unicode has no small form of it."""
    table = SUP if kind == "sup" else SUB
    s = s.strip()
    if s and all(c in table for c in s):
        return "".join(table[c] for c in s)
    mark = "^" if kind == "sup" else "_"
    return f"{mark}{s}" if len(s) == 1 else f"{mark}({s})"


def _chem(s: str) -> str:
    """mhchem's \\ce{H2O} as H₂O: a number after a letter or a bracket is a subscript."""
    s = re.sub(r"(?<=[A-Za-z)\]])(\d+)", lambda m: "".join(SUB[c] for c in m.group(1)), s)
    return s.replace("->", "→").replace("<=>", "⇌").replace("<->", "↔")


def math_parts(tex: str) -> list[tuple[str, str, bool]]:
    """A formula as pieces of readable text: (text, "" | "sup" | "sub", italic). Letters are italic as in a formula,
    an operator or relation has a space each side, a new line of an aligned formula is "\\n"."""
    try:
        nodes = _Parser(tex or "").parse()
    except RecursionError:
        return [(tex, "", False)]
    out: list[tuple[str, str, bool]] = []
    last = ""

    def put(s: str, pos: str = "", italic: bool = False) -> None:
        if out and out[-1][1] == pos and out[-1][2] == italic:
            out[-1] = (out[-1][0] + s, pos, italic)
        else:
            out.append((s, pos, italic))

    for n in nodes:
        if isinstance(n, _T):
            s = n.s
            if s in RELATIONS or (s in BINARY and last and last not in OPENERS):
                put(f" {s} ")
            elif s in (",", ";"):                 # a list in a formula: "A₁, …, Aₙ", as TeX spaces it
                put(s + " ")
            else:
                put(s, "", n.italic)
            if s.strip():
                last = s.strip()[-1]
        elif isinstance(n, _S):
            put(_flat(n.nodes).strip(), n.kind)
            last = "x"
        elif isinstance(n, _Br):
            put("\n")
            last = ""
        elif isinstance(n, _Col):
            put(" ")
    cleaned = []
    for s, pos, italic in out:
        s = re.sub(r"[ \t]*\n[ \t]*", "\n", re.sub(r"[ \t]{2,}", " ", s))
        if cleaned and cleaned[-1][0].endswith((" ", "\n")) and s.startswith(" "):
            s = s[1:]
        if s:
            cleaned.append((s, pos, italic))
    if cleaned:
        cleaned[0] = (cleaned[0][0].lstrip(" "), *cleaned[0][1:])
        cleaned[-1] = (cleaned[-1][0].rstrip(" "), *cleaned[-1][1:])
    return [c for c in cleaned if c[0]] or [((tex or "").strip(), "", False)]


def math_text(tex: str) -> str:
    """A formula as one line of readable text (``\\n`` between the lines of an aligned one): x², (a+b)/(2c), √x."""
    return "".join(_script_text(pos, s) if pos else s for s, pos, _ in math_parts(tex)).replace(" \n", "\n")


# ------------------------------------------------------------------ Markdown
def _wrap(s: str, mark: str) -> str:
    """``s`` in **bold** or *italic*, its spaces at either end outside the marks (Markdown wants them so)."""
    core = s.strip()
    if not core:
        return s
    return s[:len(s) - len(s.lstrip())] + mark + core + mark + s[len(s.rstrip()):]


def _runs_md(runs: Iterable[Run]) -> str:
    out = []
    for r in runs:
        if r.math:
            out.append(f"${r.text}$")
            continue
        if r.kind == "time":
            out.append(f"**{r.text}**")
            continue
        if r.kind == "tag":
            out.append(f"**[{r.text}]**")
            continue
        s = f"`{r.text}`" if r.code else md_escape(r.text)
        if r.link:
            s = md_link(s, r.link)
        if r.bold:
            s = _wrap(s, "**")
        if r.italic:
            s = _wrap(s, "*")
        out.append(s)
    return "".join(out)


def _md_blocks(blocks: Iterable[Any], quote: str = "") -> list[str]:
    lines: list[str] = []
    for b in blocks:
        if isinstance(b, Heading):
            lines += [f"{'#' * min(6, b.level + 1)} {_runs_md(b.runs)}", ""]
        elif isinstance(b, Para):
            text = _runs_md(b.runs)
            lines += [f"**{text}**" if b.style == "who" and not text.startswith("**") else
                      f"*{text}*" if b.style in ("caption", "small") else text, ""]
        elif isinstance(b, Items):
            for it in b.items:
                lead = "  " * it.level + (f"{it.number}. " if it.ordered else "- ")
                lines.append(lead + _runs_md(it.runs))
                pad = " " * len(lead)
                lines += [pad + x if x else "" for x in _md_blocks(it.more)]
            lines.append("")
        elif isinstance(b, Table):
            if b.rows:
                width = max(len(r) for r in b.rows)
                cell = lambda c: _runs_md(c).replace("|", "\\|").replace("\n", " ")   # noqa: E731
                rows = [[cell(c) for c in r] + [""] * (width - len(r)) for r in b.rows]
                lines.append("| " + " | ".join(rows[0]) + " |")
                lines.append("|" + "---|" * width)
                lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
                lines.append("")
        elif isinstance(b, Figure):
            lines += [f"*[{_runs_md(b.caption) or 'Picture'}]*", ""]
        elif isinstance(b, Rule):
            lines += ["---", ""]
        elif isinstance(b, Box):
            title = _runs_md(b.title) if b.title else ""
            if b.kind in ALERTS:
                head = f"[!{b.kind.upper()}]" + (f" {title}" if title and plain(b.title) != box_label(b.kind) else "")
            else:
                head = f"**{box_label(b.kind)}{' · ' + title if title else ''}**" if b.kind != "quote" else ""
            inner = ([head] if head else []) + _md_blocks(b.blocks)
            while inner and not inner[-1]:
                inner.pop()
            lines += [f"> {x}".rstrip() for x in inner] + [""]
        elif isinstance(b, Math):
            lines += ["$$", b.tex, "$$", ""]
        elif isinstance(b, Code):
            lines += ["```", b.text, "```", ""]
        elif isinstance(b, Markdown):
            lines += [linked_images(b.md, b.base).strip("\n"), ""]
    return lines


def to_markdown(doc: Doc) -> str:
    with writing_in(doc.lang):
        return _to_markdown(doc)


def _to_markdown(doc: Doc) -> str:
    lines = [f"# {md_escape(doc.title)}", ""]
    under = " · ".join(x for x in (doc.kicker, doc.subtitle) if x)
    if under:
        lines += [f"*{md_escape(under)}*", ""]
    if doc.meta:
        lines += [_runs_md(m) + "  " for m in doc.meta] + [""]
    lines += _md_blocks(doc.blocks)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


# ------------------------------------------------------------------ plain text
def _runs_text(runs: Iterable[Run]) -> str:
    out = []
    for r in runs:
        if r.math:
            out.append(math_text(r.text).replace("\n", "; "))
        elif r.kind == "tag":
            out.append(f"[{r.text}]")
        else:
            out.append(r.text + (f" ({r.link})" if r.link and r.link != r.text and r.link.startswith("http") else ""))
    return "".join(out)


def _text_blocks(blocks: Iterable[Any], pad: str = "") -> list[str]:
    lines: list[str] = []
    for b in blocks:
        if isinstance(b, Markdown):
            lines += _text_blocks(b.blocks(), pad)
        elif isinstance(b, Heading):
            text = _runs_text(b.runs)
            if lines and lines[-1]:
                lines.append("")
            under = [pad + ("=" if b.level == 1 else "-") * min(72, len(text))] if b.level <= 2 else []
            lines += [pad + text] + under + [""]
        elif isinstance(b, Para):
            lines += [pad + _runs_text(b.runs), ""]
        elif isinstance(b, Items):
            for it in b.items:
                lead = "  " * it.level + (f"{it.number}. " if it.ordered else "• ")
                lines.append(pad + lead + _runs_text(it.runs))
                more = _text_blocks(it.more, pad + " " * len(lead))
                while more and not more[-1]:
                    more.pop()
                lines += more
            lines.append("")
        elif isinstance(b, Table):
            lines += [pad + " | ".join(_runs_text(c) for c in r) for r in b.rows] + [""]
        elif isinstance(b, Figure):
            lines += [pad + f"[Picture: {_runs_text(b.caption)}]", ""]
        elif isinstance(b, Rule):
            lines += [pad + "----------", ""]
        elif isinstance(b, Box):
            head = box_label(b.kind)
            title = _runs_text(b.title)
            if title and title != head:
                head = f"{head}: {title}" if head else title
            inner = _text_blocks(b.blocks, pad + "  ")
            while inner and not inner[-1]:
                inner.pop()
            lines += ([pad + head] if head else []) + inner + [""]
        elif isinstance(b, Math):
            lines += [pad + "    " + x for x in math_text(b.tex).split("\n")] + [""]
        elif isinstance(b, Code):
            lines += [pad + "    " + x for x in b.text.split("\n")] + [""]
    return lines


def to_text(doc: Doc) -> str:
    with writing_in(doc.lang):
        return _to_text(doc)


def _to_text(doc: Doc) -> str:
    lines = [doc.title, "=" * min(72, len(doc.title))]
    under = " · ".join(x for x in (doc.kicker, doc.subtitle) if x)
    if under:
        lines.append(under)
    lines += [_runs_text(m) for m in doc.meta] + [""]
    lines += _text_blocks(doc.blocks)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(x.rstrip() for x in lines)).strip() + "\n"


# ------------------------------------------------------------------ HTML (the PDF): the app's document look
EXTRA_CSS = """
.xp .sp-cover { padding: 4px 0 18px; margin-bottom: 20px; }
.xp .sp-cover h1 { font-size: 1.75em; }
.xp-meta { color: var(--sp-dim); font-size: .88em; margin: .12em 0; }
.xp-meta b { color: var(--sp-ink); font-weight: 600; }
.xp p.xp-pa { margin: 0 0 .5em; }
.xp-ts { color: var(--sp-accent); font-weight: 600; font-variant-numeric: tabular-nums; margin-right: .35em; }
.xp-tag { font-size: .72em; font-weight: 700; color: #047857; border: 1px solid #a7f3d0; border-radius: 3px;
  padding: 0 .3em; margin-right: .3em; vertical-align: .1em; }
.xp-tr { background: #f1faf6; border-radius: 4px; padding: 0 .3em; margin-left: -.3em; margin-right: -.3em; }
.xp-dim, .xp-small, .xp-caption { color: var(--sp-dim); }
.xp-small { font-size: .88em; }
.xp-caption { font-size: .86em; text-align: center; }
.xp-who { font-size: .8em; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--sp-accent);
  margin: 1.4em 0 .3em; break-after: avoid; }
.xp-question { background: var(--sp-soft); border-left: 3px solid var(--sp-line); border-radius: 0 8px 8px 0;
  padding: .5em .8em; white-space: pre-wrap; }
.xp h2.xp-h1 { font-size: 1.45em; margin: 1.4em 0 .5em; padding-bottom: .3em; border-bottom: 1px solid var(--sp-line); }
.xp h3.xp-h2 { font-size: 1.15em; margin: 1.3em 0 .45em; }
.xp a { color: var(--sp-accent); text-decoration: none; }
.xp .math-src { font-family: Consolas, monospace; font-size: .92em; }
@media print {
  .xp .sp-cover { padding-top: 0; }
  .xp h2.xp-page { break-before: page; margin-top: 0; }
}
"""


def _esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def _runs_html(runs: Iterable[Run]) -> str:
    out = []
    for r in runs:
        if r.math:
            out.append(f"\\({_esc(r.text)}\\)")
            continue
        s = f"<code>{_esc(r.text)}</code>" if r.code else _esc(r.text)
        if r.kind == "time":
            s = f'<span class="xp-ts">{s}</span>'
        elif r.kind == "tag":
            s = f'<span class="xp-tag">{s}</span>'
        elif r.kind == "dim":
            s = f'<span class="xp-dim">{s}</span>'
        if r.bold:
            s = f"<strong>{s}</strong>"
        if r.italic:
            s = f"<em>{s}</em>"
        if r.link and r.link.startswith(("http://", "https://")):
            s = f'<a href="{html.escape(r.link)}">{s}</a>'
        out.append(s)
    return "".join(out)


def _items_html(items: list[Item]) -> str:
    out, stack = [], []                       # stack: (level, tag)
    for it in items:
        tag = "ol" if it.ordered else "ul"
        while stack and it.level < stack[-1][0]:
            out.append(f"</li></{stack.pop()[1]}>")
        if stack and it.level == stack[-1][0] and stack[-1][1] != tag:
            out.append(f"</li></{stack.pop()[1]}>")
        if not stack or it.level > stack[-1][0]:
            out.append(f'<{tag} start="{it.number}">' if it.ordered and it.number != 1 else f"<{tag}>")
            stack.append((it.level, tag))
        else:
            out.append("</li>")
        out.append(f"<li>{_runs_html(it.runs)}{_html_blocks(it.more)}")
    while stack:
        out.append(f"</li></{stack.pop()[1]}>")
    return "".join(out)


def _html_blocks(blocks: Iterable[Any]) -> str:
    from ..studypack import md_html

    out = []
    for b in blocks:
        if isinstance(b, Heading):
            n = min(6, b.level + 1)
            cls = f"xp-h{b.level}" + (" xp-page" if b.page else "")
            out.append(f'<h{n} class="{cls}">{_runs_html(b.runs)}</h{n}>')
        elif isinstance(b, Para):
            cls = f"xp-{b.style}" if b.style else "xp-pa"
            if b.lang and b.lang != "en":
                cls += " xp-tr"
            # an English label in capitals keeps English rules in a Turkish document ("WITH", not "WİTH")
            lang = f' lang="{_esc(b.lang)}"' if b.lang else ' lang="en"' if b.style == "who" else ""
            out.append(f'<p class="{cls}"{lang}>{_runs_html(b.runs)}</p>')
        elif isinstance(b, Items):
            out.append(_items_html(b.items))
        elif isinstance(b, Table):
            if b.rows:
                head = "".join(f"<th>{_runs_html(c)}</th>" for c in b.rows[0]) if b.header else ""
                body = "".join("<tr>" + "".join(f"<td>{_runs_html(c)}</td>" for c in r) + "</tr>"
                               for r in b.rows[1 if b.header else 0:])
                out.append(f'<div class="sp-table"><table>{f"<thead><tr>{head}</tr></thead>" if head else ""}'
                           f"<tbody>{body}</tbody></table></div>")
        elif isinstance(b, Figure):
            src = Path(b.path).resolve().as_uri() if b.path else ""
            out.append(f'<figure class="sp-fig sp-frame"><img src="{html.escape(src)}" alt="">'
                       f"<figcaption>{_runs_html(b.caption)}</figcaption></figure>")
        elif isinstance(b, Rule):
            out.append("<hr>")
        elif isinstance(b, Box):
            inner = _html_blocks(b.blocks)
            if b.kind == "quote":
                out.append(f"<blockquote>{inner}</blockquote>")
            elif b.kind in ALERTS:
                from ..studypack import ALERT_SIGNS

                title = _runs_html(b.title) if b.title else box_label(b.kind)
                out.append(f'<div class="sp-alert sp-alert-{b.kind}" role="note"><div class="sp-alert-h">'
                           f'<span class="sp-alert-i" aria-hidden="true">{ALERT_SIGNS[b.kind]}</span><b>{title}</b>'
                           f"</div>{inner}</div>")
            else:
                title = _runs_html(b.title)
                out.append(f'<div class="sp-box sp-{b.kind}"><div class="sp-box-h"><b>{box_label(b.kind)}</b>'
                           f'{" · " + title if title else ""}</div>{inner}</div>')
        elif isinstance(b, Math):
            out.append(f'<div class="sp-math">$${_esc(b.tex)}$$</div>')
        elif isinstance(b, Code):
            out.append(f"<pre><code>{_esc(b.text)}</code></pre>")
        elif isinstance(b, Markdown):
            out.append(md_html(b.md, image=lambda alt, src, base=b.base: image_html(alt, src, base)))
    return "".join(out)


def _to_html(doc: Doc) -> str:
    """The document as a page to print: the study pack's stylesheet (the app's document look), and KaTeX for the
    formulas, from the app's own files (the page is printed where it is made, then thrown away)."""
    from ..studypack import CSS_FILE
    from .mathmd import katex_tags

    css = CSS_FILE.read_text(encoding="utf-8") + EXTRA_CSS
    meta = "".join(f'<div class="xp-meta">{_runs_html(m)}</div>' for m in doc.meta)
    kicker = f'<div class="sp-kicker" lang="en">{_esc(doc.kicker)}</div>' if doc.kicker else ""
    sub = f'<div class="sp-sub">{_esc(doc.subtitle)}</div>' if doc.subtitle else ""
    cover = f'<header class="sp-cover">{kicker}<h1>{_esc(doc.title)}</h1>{sub}{meta}</header>'
    return (f'<!doctype html><html lang="{_esc(doc.lang)}"><head><meta charset="utf-8"><title>{_esc(doc.title)}'
            f"</title><style>{css}</style></head><body class=\"sp-page\"><article class=\"sp-doc xp\">{cover}"
            f"{_html_blocks(doc.blocks)}</article>{katex_tags(None)}</body></html>")


def to_html(doc: Doc) -> str:
    """The document as a page to print (_to_html), its boxes' labels in its language."""
    with writing_in(doc.lang):
        return _to_html(doc)
