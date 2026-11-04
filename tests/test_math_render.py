"""Formulas: KaTeX (bundled in web/vendor/katex) draws the LaTeX in AI answers, notes and study plans through math.js and
app.js's Markdown, run here in node (app.js with a do-nothing DOM); LaTeX that an AI wrote into JSON survives, the
Markdown files get $…$ formulas and a page printed to PDF can load KaTeX from the app's files."""

import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

from studia import i18n
from studia.ai import parse_json
from studia.outputs.mathmd import dollar_math, katex_tags

WEB = Path(i18n.__file__).parent / "web"

# app.js loads with stand-ins for the page; LSRender.md is its Markdown, StudiaMath the formulas
SCRIPT = """
const stub = () => new Proxy(function () {}, { get: (t, k) => k === Symbol.toPrimitive ? () => "" : k === "length" ? 0 : stub(),
  apply: () => stub(), construct: () => stub() });
globalThis.window = globalThis;
globalThis.document = new Proxy({}, { get: (t, k) => k === "compatMode" ? "CSS1Compat" : stub() });
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.navigator = { userAgent: "node", platform: "Win32" };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;
const web = %(web)s, cases = require(%(cases)s);
for (const f of ["i18n.js", "icons.js", "app.js"]) require(web + "/" + f);
globalThis.katex = require(web + "/vendor/katex/katex.min.js");
require(web + "/math.js");
const M = globalThis.StudiaMath, out = {};
out.tex = cases.tex.map(([s, d]) => M.tex(s, d));
out.text = cases.text.map((s) => M.text(s));
out.split = cases.split.map((s) => M.split(s).map((p) => typeof p === "string" ? p : [p.tex, p.display]));
out.md = cases.md.map(([s, o]) => LSRender.md(s, o || {}));
out.settled = cases.settled.map((s) => M.settled(s));
out.studia = typeof Studia.renderMath;
console.log(JSON.stringify(out));
"""

OWNER = r"""### 1. Basic terminology

A $5\ \mathrm V$ source feeds $2\ \mathrm{k\Omega}$ and $3\ \mathrm{k\Omega}$ in series.

> **Solution.**
> The total resistance is $5\ \mathrm{k\Omega}$, so
> $$
> I=\frac5{5000}=1\ \mathrm{mA}.
> $$
> $$
> W_C=\frac12CV^2=\frac12(10^{-6})(6^2)
> =18\ \mu\mathrm J.
> $$
> Connect the ammeter in series."""

CHATGPT = r"""For a valid discrete PMF:
\[
f_X(x)=P(X=x)\ge0,\qquad \sum_x f_X(x)=1.
\]
Then:
\[
\begin{aligned}
P(B)
&=P(B\mid A)P(A)+P(B\mid A^c)P(A^c)\\
&=0.26
\end{aligned}
\]
and \(P(A\mid B)=\frac{9}{13}\)."""

LIST = r"""1. Find the current:
   $$
   I=\frac{V}{R}
   $$
   so it is $1\ \mathrm{mA}$.
2. Next step with \begin{cases} x & x>0 \\ -x & x\le0 \end{cases} inline"""

ENV = r"""Write KCL:
\begin{align}
5i_1-2i_2&=3,\\
-2i_1+5i_2&=3.
\end{align}
Done."""


def _run(tmp_path, cases):
    (tmp_path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    script = tmp_path / "math.js"
    script.write_text(SCRIPT % {"web": json.dumps(WEB.as_posix()), "cases": json.dumps((tmp_path / "cases.json").as_posix())},
                      encoding="utf-8")
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def _drawn(html):
    return "katex" in html and "katex-error" not in html and "math-src" not in html


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_formulas_are_drawn_by_katex_and_money_and_code_stay_text(tmp_path):
    out = _run(tmp_path, {
        "tex": [[r"2\ \mathrm{k\Omega}", False], [r"18\ \mu\mathrm J", False], [r"I=\frac5{5000}=1\ \mathrm{mA}.", True],
                [r"\begin{align} a&=b\\ c&=d \end{align}", True], [r"\begin{cases} 1 & x>0 \\ 0 & \text{else} \end{cases}", False],
                [r"\SI{5}{\kilo\ohm} + \si{\micro\farad} + \C", False], [r"\foo{x}+\alpha", False],
                [r"P(A\mid B)=\frac{P(A\cap B)}{P(B)}", False], ["\\frac{1}{", False], ["\\\\frac{1}{2}", False],
                ["\x0crac{1}{2} \times 3", False]],                       # "\frac", "\times" read as JSON escapes
        "text": [r"Find the current in $2\ \mathrm{k\Omega}$ ($$ I=\frac5{5000}=1\ \mathrm{mA}. $$) and \(x^2\)."],
        "split": ["It costs $5 and $10, or $5-$10.", "US$5 and the $n$th term", r"a \$5 bill and $x$, $y$.",
                  "price: $ 5 $", r"\[x\] and \begin{align}a\end{align}"],
        "md": [[OWNER, None], [CHATGPT, None], [LIST, None], [ENV, None],
               ["Code: `$x$` and\n```\n$$ y $$\n```\nand $z$.", None],
               ["The answer:\n$$\nI=\\frac{V}", {"streaming": True}],
               ["The current is $I=\\frac", {"streaming": True}]],
        "settled": ["The current is $I=\\frac", "ok $x$ and \\(y", "done $x$", "line one $a$\nnow $$b"],
    })
    assert out["studia"] == "function"                                             # Studia.renderMath(el) for other pages
    tex = out["tex"]
    assert all(_drawn(h) for h in tex[:8]), [h[:200] for h in tex[:8] if not _drawn(h)]
    assert "\\</mtext>" in tex[6] and "foo" in tex[6]                                # unknown: shown as written, rest drawn
    assert tex[8].startswith('<span class="math-src">') and "\\frac{1}{" in tex[8]  # can't be read: its source
    assert _drawn(tex[9]) and "<mfrac>" in tex[9]                                    # \\frac from double-escaped JSON
    assert _drawn(tex[10]) and "<mfrac>" in tex[10] and "×" in tex[10]               # \f and \t from unescaped JSON

    text = out["text"][0]
    assert text.count('class="katex"') == 3 and "$" not in text and "\\(" not in text and 'class="math-display"' in text

    split = out["split"]
    assert split[0] == ["It costs $5 and $10, or $5-$10."]
    assert split[1] == ["US$5 and the ", ["n", False], "th term"]
    assert split[2] == ["a $5 bill and ", ["x", False], ", ", ["y", False], "."]
    assert split[3] == ["price: $ 5 $"]
    assert split[4] == [["x", True], " and ", ["\\begin{align}a\\end{align}", True]]

    owner, gpt, lst, env, code, pending, open_inline = out["md"]
    assert "<blockquote>" in owner and owner.count('class="math-block"') == 2 and "$" not in owner
    assert owner.count('class="katex"') == 6 and "\\" not in re.sub(r"<annotation.*?</annotation>", "", owner, flags=re.S)
    assert "math-src" not in owner and "katex-error" not in owner
    assert gpt.count('class="math-block"') == 2 and "\\[" not in gpt and "\\(" not in gpt and "math-src" not in gpt
    assert "<ol><li>" in lst and lst.count("<ol") == 1 and 'class="math-block"' in lst.split("</li>")[0]   # in the item
    assert lst.count('class="math-display"') == 1 and "math-src" not in lst
    assert env.count('class="math-block"') == 1 and "<p>Done.</p>" in env and "math-src" not in env
    assert "<code>$x$</code>" in code and "<pre><code>$$ y $$</code></pre>" in code and code.count('class="katex"') == 1
    assert 'class="math-block pending"' in pending and "\\frac" not in pending     # drawn once it is closed
    assert "$" not in open_inline and "katex" not in open_inline
    assert out["settled"] == ["The current is ", "ok $x$ and ", "done $x$", "line one $a$\nnow "]


def test_page_loads_katex_from_the_app_itself():
    page = (WEB / "index.html").read_text(encoding="utf-8")
    for rel in ("vendor/katex/katex.min.css", "vendor/katex/katex.min.js", "vendor/katex/contrib/mhchem.min.js", "math.js"):
        assert f'"{rel}"' in page and (WEB / rel).is_file()
    assert page.index('src="app.js"') < page.index('src="math.js"')
    css = (WEB / "vendor/katex/katex.min.css").read_text(encoding="utf-8")
    fonts = set(re.findall(r"url\((fonts/[^)]+\.woff2)\)", css))
    assert fonts and all((WEB / "vendor/katex" / f).is_file() for f in fonts)     # the first format each font names
    assert (WEB / "vendor/katex/LICENSE").is_file()


def test_latex_an_ai_wrote_into_json_survives():
    raw = (r'{"q": "Find $\frac{1}{2}$ in $2\ \mathrm{k\Omega}$, $\beta \theta \times \tau \nu \neq \right)$ \underline{x}",'
           r' "a": "line\nThe end \u00e9 \"q\" \\alpha \\frac{1}{2}", "t": "a\tb\n\nnew"}')
    got = parse_json(raw)
    assert got["q"] == r"Find $\frac{1}{2}$ in $2\ \mathrm{k\Omega}$, $\beta \theta \times \tau \nu \neq \right)$ \underline{x}"
    assert got["a"] == 'line\nThe end \u00e9 "q" \\alpha \\frac{1}{2}'            # real escapes as they were
    assert got["t"] == "a\tb\n\nnew"
    assert parse_json('{"a": "plain\\nNew line"}') == {"a": "plain\nNew line"}


@pytest.mark.parametrize("tex", [r"a \ne b", r"x \ni y", r"\lim_{x\to 0} f", r"\sum\nolimits_i a_i", r"a \newline b",
                                 r"a\ne0", r"a \nmid b", r"A \nsubseteq B", r"\nRightarrow", r"x \to\infty", r"a \ne"])
def test_unescaped_latex_starting_with_n_or_t_survives_json(tex):
    assert parse_json('{"q": "Show $' + tex + '$"}') == {"q": f"Show ${tex}$"}


def test_real_new_lines_and_tabs_stay_with_the_wider_repair():
    raw = r'{"a": "Options:\ni) one\nii) two\ne.g. this\nit is\t1\tOK\n\nnext\n"}'
    assert parse_json(raw) == {"a": "Options:\ni) one\nii) two\ne.g. this\nit is\t1\tOK\n\nnext\n"}


def test_markdown_files_get_dollar_formulas_outside_code():
    md = "\n".join([r"Then \( P(A\mid B) \) and", r"\[", r"\begin{aligned} a&=b\\[2pt] c&=d\\ \end{aligned}", r"\]",
                    r"Code `\(x\)` stays.", "```", r"\[ not maths \]", "```", r"Last \(y\)."])
    assert dollar_math(md) == "\n".join([r"Then $P(A\mid B)$ and", "$$", r"\begin{aligned} a&=b\\[2pt] c&=d\\ \end{aligned}",
                                         "$$", r"Code `\(x\)` stays.", "```", r"\[ not maths \]", "```", r"Last $y$."])
    assert dollar_math("No maths, $5.") == "No maths, $5."


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_math_js_repairs_to_and_neq_read_as_json_escapes(tmp_path):
    out = _run(tmp_path, {"tex": [["\\lim_{x\to 0} f", False], ["a \neq b", False], ["\nabla f", False],
                                  ["a\nb", True], ["\\sum\nolimits_i a_i", False]],
                          "text": [], "split": [], "md": [], "settled": []})
    lim, neq, nabla, lines, sums = out["tex"]
    assert _drawn(lim) and "→" in lim and _drawn(neq) and "≠" in neq and _drawn(nabla) and "∇" in nabla
    assert _drawn(sums) and "∑" in sums
    assert _drawn(lines) and "<mi>a</mi>" in lines and "<mi>b</mi>" in lines             # a real new line stays one


def test_a_kept_page_loads_katex_from_a_copy_next_to_it(tmp_path):
    tags = katex_tags(tmp_path / "Study packs")
    assert "file:" not in tags and "StudiaMath.renderMath(document.body)" in tags
    (tmp_path / "Study packs").rename(tmp_path / "moved")                      # another PC, a synced or moved folder
    refs = re.findall(r'(?:href|src)="([^"]+)"', tags)
    assert len(refs) == 4 and all((tmp_path / "moved" / r).is_file() for r in refs)
    css = (tmp_path / "moved" / "katex" / "katex.min.css").read_text(encoding="utf-8")
    assert all((tmp_path / "moved" / "katex" / f).is_file() for f in re.findall(r"url\((fonts/[^)]+\.woff2)\)", css))
    js = tmp_path / "moved" / "katex" / "math.js"
    js.write_text("old", encoding="utf-8")                                     # an app update: copied again
    katex_tags(tmp_path / "moved")
    assert js.read_bytes() == (WEB / "math.js").read_bytes()


def test_a_page_to_print_loads_katex_from_the_app_files():
    tags = katex_tags()
    uris = re.findall(r'(?:href|src)="(file:[^"]+)"', tags)
    assert len(uris) == 4 and "StudiaMath.renderMath(document.body)" in tags
    assert all(Path(url2pathname(urlparse(u).path)).is_file() for u in uris)
