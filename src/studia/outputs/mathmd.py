"""Formulas in the Markdown files IEUStudia writes (study notes, chats, study plans, review sheets and study packs): \\(…\\) and \\[…\\],
as ChatGPT often writes them, become $…$ and $$…$$, the form Obsidian, VS Code, Typora and GitHub draw. Code is left
as it is."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

_FENCE = re.compile(r"^\s*(```|~~~)")
_CODE = re.compile(r"(`[^`\n]+`)")
_DISPLAY = re.compile(r"(?<!\\)\\\[(.+?)(?<!\\)\\\]", re.S)
_INLINE = re.compile(r"(?<!\\)\\\((.+?)(?<!\\)\\\)", re.S)


def _convert(text: str) -> str:
    parts = _CODE.split(text)
    for i in range(0, len(parts), 2):                   # the odd ones are `code`
        p = _DISPLAY.sub(lambda m: f"$${m.group(1)}$$", parts[i])
        parts[i] = _INLINE.sub(lambda m: f"${m.group(1).strip()}$" if m.group(1).strip() else m.group(0), p)
    return "".join(parts)


def dollar_math(md: str) -> str:
    """``md`` with \\(…\\) as $…$ and \\[…\\] as $$…$$, outside fenced and inline code."""
    if "\\(" not in md and "\\[" not in md:
        return md
    out: list[str] = []
    text: list[str] = []
    fence = ""
    for line in md.split("\n"):
        m = _FENCE.match(line)
        if fence:
            out.append(line)
            if m and m.group(1) == fence:
                fence = ""
        elif m:
            if text:
                out.append(_convert("\n".join(text)))
            text, fence = [], m.group(1)
            out.append(line)
        else:
            text.append(line)
    if text:
        out.append(_convert("\n".join(text)))
    return "\n".join(out)


KATEX_DIR = "katex"            # next to kept HTML files: a copy of KaTeX and math.js they share


def _copy_katex(web: Path, folder: Path) -> None:
    """KaTeX (its stylesheet, scripts, fonts and licence) and math.js copied to ``folder``/katex, each file only when
    it is missing or differs from the app's (an update)."""
    kt = web / "vendor" / "katex"
    files = [(p, p.relative_to(kt)) for p in kt.rglob("*") if p.is_file() and p.suffix != ".md"]
    for src, rel in [*files, (web / "math.js", Path("math.js"))]:
        dest = folder / KATEX_DIR / rel
        data = src.read_bytes()
        if not dest.is_file() or dest.read_bytes() != data:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(dest)


def katex_tags(folder: Optional[Path] = None) -> str:
    """For an HTML page that is printed to PDF (export.html_to_pdf): KaTeX's stylesheet and scripts, and the call that
    draws every formula in the page's text ($…$, \\(…\\), $$…$$, \\[…\\]) while it loads. Put it at the end of <body>;
    text in <pre>/<code> stays as written. A page that is kept gives its ``folder``: they come from a copy there
    (katex/…, by relative address: the page still works on another PC, moved or synced); else from the app's files."""
    web = Path(__file__).resolve().parent.parent / "web"
    if folder is not None:
        _copy_katex(web, folder)
        css, js, mhchem, math = (f"{KATEX_DIR}/{x}" for x in ("katex.min.css", "katex.min.js", "contrib/mhchem.min.js",
                                                              "math.js"))
    else:
        kt = web / "vendor" / "katex"
        css, js, mhchem, math = ((kt / "katex.min.css").as_uri(), (kt / "katex.min.js").as_uri(),
                                 (kt / "contrib" / "mhchem.min.js").as_uri(), (web / "math.js").as_uri())
    return (f'<link rel="stylesheet" href="{css}">\n'
            f'<script src="{js}"></script>\n'
            f'<script src="{mhchem}"></script>\n'
            f'<script src="{math}"></script>\n'
            "<script>StudiaMath.renderMath(document.body);</script>\n")
