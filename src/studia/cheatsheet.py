"""Cheatsheets: every formula, definition, key concept, rule, unit and standard method of the files and lectures the
student picks, on A4 pages in three columns (one or two pages for a few weeks), grouped by topic in the course's order,
with what the symbols mean and when each one applies, maths drawn by KaTeX; no explanations or worked examples (that
is the study pack). Shown in the app and saved as HTML and as a PDF (the study pack's printing: Edge headless, or Qt
WebEngine on Linux without it).

Stages (each one's output is saved with the plan, so a stopped sheet resumes where it stopped):
  gather   - the picked files and lectures (studypack.gather), no AI
  topics   - the topics in the course's order, every item each one must hold and the pages and lecture passages it
             is taught in (AI, JSON): the list the finished sheet is checked against
  entries  - the entries of a few topics at a time, written from those topics' own material (AI, JSON, two requests
             at once, each saved when it is done)
  check    - code drops empty and duplicate entries and formulas it can't read; what the list names and no entry
             holds is asked for once more (AI, one request, only when something is missing)
  assemble - the document (HTML) and its PDF, in <course>/Cheatsheets

A cheatsheet is a plan of kind "sheet" (plans.PlanRunner runs it), so the plan list, events, stop and resume are the
plans'; the material chooser (studypack.options), its page's document (studypack.shown_body) and its PDF
(studypack.make_pdf) are the study pack's.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

from . import aiwait
from .cancel import Cancelled
from .i18n import N_, t, tn
from .notices import ALERTS_JSON
from .outputs.prompts import written_in

STAGES = [("gather", N_("Reading the course material")), ("topics", N_("Listing the formulas and concepts")),
          ("entries", N_("Writing the entries")), ("check", N_("Checking that nothing is missing")),
          ("assemble", N_("Laying out your cheatsheet"))]
LABELS = dict(STAGES)
MAX_TOPICS = 40
MAX_ITEMS = 40                # per topic
BATCH_CHARS = 34_000          # material one entries request is written from (~10k tokens)
BATCH_TOPICS = 6              # topics in one entries request
PARALLEL = 2                  # entries requests at once
KINDS = ("formula", "definition", "rule", "method", "unit", "concept")
TAGS = {"definition": "Def", "rule": "Rule", "method": "Method", "unit": "Unit", "concept": "Idea"}

SYSTEM = ("You are an experienced university lecturer making a cheatsheet for a student: a compact, complete reference "
          "of every formula, definition, rule, unit and standard method the course material requires, built only on "
          "the material you are given, in its notation. Its words are plain and exact, like a good tutor's margin "
          "notes: no filler, no em dashes. You answer with the JSON asked for, maths in LaTeX, and never ask "
          "questions. " + ALERTS_JSON)


def _str(v: Any, limit: int = 400) -> str:
    return " ".join(str(v or "").split())[:limit]


def _norm(s: Any) -> str:
    return re.sub(r"[\W_]+", "", str(s or "").lower())


# ------------------------------------------------------------------ topics (AI)
def topics_prompt(facts: dict[str, Any]) -> str:
    return f"""TASK: List everything that belongs on a cheatsheet for the course material below ({facts['span']}): a compact reference a student uses to revise and to solve the course's problems.
Answer with one JSON object:
{{"title": "a short title naming the topics (not the course code)",
 "topics": [{{"id": "t1", "title": "…", "items": ["Ohm's law", "voltage divider", "…"],
   "sources": {{"files": [{{"ref": "F1", "pages": [3, 9]}}], "lectures": [{{"ref": "L1", "part": 1, "from": "12:00", "to": "41:30"}}]}}}}]}}
Rules:
- "items": EVERYTHING the material requires the student to know or use, each named in a few words: every formula (also the derived forms and special cases the material uses), definition, key concept, theorem, law, rule or property, unit, constant and conversion, standard method or procedure, and the conditions that decide which one applies. Nothing the material requires may be left out; nothing outside the material is added, nor what every student of the course already knows (basic algebra, SI prefixes). Each item once, in the topic where the material teaches it.
- Topics in the course's order (by week, then in the material's order), 2–25 of them, each with 2–30 items.
- "sources": the file pages ([first, last]) and lecture passages (part, from–to as m:ss or h:mm:ss) each topic is taught in. Use only the references (F…, L…) listed below.
- Titles and items in {facts.get('language') or 'English'}.

=== COURSE MATERIAL ===
{facts['pack']}"""


def clean_topics(raw: Any, facts: dict[str, Any]) -> dict[str, Any]:
    """The AI's list, checked: topics with a title and at least one item, items named once in the whole sheet, known
    references only (studypack.clean_sources), at most MAX_TOPICS topics of MAX_ITEMS items."""
    from .studypack import clean_sources

    if not isinstance(raw, dict):
        raw = {}
    topics, seen = [], set()
    for x in raw.get("topics") or []:
        if not isinstance(x, dict) or not _str(x.get("title")) or len(topics) >= MAX_TOPICS:
            continue
        items = []
        for it in x.get("items") or []:
            name = _str(it.get("name") if isinstance(it, dict) else it, 120)
            if name and _norm(name) and _norm(name) not in seen:
                seen.add(_norm(name))
                items.append(name)
        if items:
            topics.append({"id": f"t{len(topics) + 1}", "title": _str(x.get("title"), 100), "items": items[:MAX_ITEMS],
                           "sources": clean_sources(x.get("sources"), facts)})
    if not topics:
        raise RuntimeError(t("The AI found nothing to put on the cheatsheet. Try again."))
    return {"title": _str(raw.get("title"), 140) or facts["span"].capitalize(), "topics": topics,
            "language": facts.get("language") or ""}


# ------------------------------------------------------------------ entries (AI)
def merge_sources(topics: list[dict[str, Any]]) -> dict[str, list]:
    """The sources of several topics as one: the page ranges of a file joined, each lecture passage once."""
    ranges: dict[str, list[list[int]]] = {}
    for tp in topics:
        for s in tp["sources"]["files"]:
            ranges.setdefault(s["ref"], []).append(list(s["pages"]))
    files = []
    for ref, rs in ranges.items():
        joined: list[list[int]] = []
        for a, b in sorted(rs):
            if joined and a <= joined[-1][1] + 1:
                joined[-1][1] = max(joined[-1][1], b)
            else:
                joined.append([a, b])
        files += [{"ref": ref, "pages": r} for r in joined]
    lectures, seen = [], set()
    for tp in topics:
        for s in tp["sources"]["lectures"]:
            key = (s["ref"], s["part"], s["from"], s["to"])
            if key not in seen:
                seen.add(key)
                lectures.append(s)
    return {"files": files, "lectures": lectures}


def material(lib: Any, course_id: str, facts: dict[str, Any], topics: list[dict[str, Any]]) -> str:
    """The pages and lecture passages some topics are taught in (the gathered material when they name none)."""
    from .studypack import chapter_material

    return chapter_material(lib, course_id, facts, {"sources": merge_sources(topics)})


def make_batches(sizes: list[int], limit: int = BATCH_CHARS, most: int = BATCH_TOPICS) -> list[list[int]]:
    """Consecutive topics (their material ``sizes``) in groups of at most ``most`` with about ``limit`` characters."""
    out: list[list[int]] = []
    cur: list[int] = []
    size = 0
    for i, n in enumerate(sizes):
        if cur and (size + n > limit or len(cur) >= most):
            out.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += n
    if cur:
        out.append(cur)
    return out


_FORMAT = """Answer with one JSON object:
{"topics": [{"id": "t1", "entries": [
  {"kind": "formula", "name": "Voltage divider", "tex": "v_2 = \\\\frac{R_2}{R_1 + R_2}\\\\,v_s", "text": "",
   "where": [{"sym": "v_s", "means": "source voltage (V)"}], "when": "R_1 and R_2 in series, no load on $v_2$", "covers": ["voltage divider"]},
  {"kind": "method", "name": "Nodal analysis", "tex": "", "text": "1) pick a reference node 2) KCL at each other node 3) solve $G\\\\mathbf{v} = \\\\mathbf{i}$", "where": [], "when": "", "covers": ["nodal analysis"]}]}]}
Rules:
- kind: "formula", "definition", "rule" (a theorem, law, property or identity), "method" (a standard procedure), "unit" (a unit, constant or conversion) or "concept" (a key idea in one line).
- name: what it is, in at most 6 words.
- tex: the formula in LaTeX without dollars ("" if there is none). Related forms may share one entry with \\\\begin{aligned}…\\\\end{aligned}; matrices with pmatrix or bmatrix, piecewise definitions with cases, units upright (\\\\,\\\\mathrm{V}, \\\\Omega).
- text: at most 25 plain words, maths in $...$, only what the formula doesn't already say ("" if nothing). A definition: the definition itself. A method: its steps as "1) … 2) … 3) …" (at most 5).
- where: the symbols of tex a student could misread, each with its meaning and unit (sym in LaTeX without dollars; means is plain text of at most 12 words with any maths, units like $\\\\Omega$ too, in $...$), at most 4. A symbol is explained once on the whole sheet: not again in later entries. [] when none.
- when: only a condition under which it holds or is used, without "if", starting in lowercase but keeping names and abbreviations as they are written (RC, RL, RLC, DC, KCL, Thevenin), e.g. "$\\\\alpha > \\\\omega_0$", "no load on the output", "source-free RL circuit"; "" if none. Never a remark or a description.
- covers: the items of the list above that this entry holds, written exactly as in the list.
- Every entry has a name and a tex or a text. No two entries the same. No explanations, derivations or worked examples.
- The material's notation; every backslash doubled as JSON needs."""


def entries_prompt(sheet: dict[str, Any], topics: list[dict[str, Any]], mat: str, span: str,
                   missing: Optional[dict[str, list[str]]] = None) -> str:
    if missing:
        head = (f'TASK: These items are missing from the cheatsheet "{sheet["title"]}" ({span}). Write the entries '
                "that hold them, from the course material below (nothing else: the rest is already there).\n"
                + "\n".join(f"{tp['id']} · {tp['title']}: {'; '.join(missing[tp['id']])}"
                            for tp in topics if missing.get(tp["id"])))
    else:
        head = (f'TASK: Write the entries of these topics of the cheatsheet "{sheet["title"]}" ({span}), from their '
                "course material below. A cheatsheet is a compact reference for revising and solving the course's "
                "problems: formulas and short statements only.\nTopics, each with the items it must hold:\n"
                + "\n".join(f"{tp['id']} · {tp['title']}: {'; '.join(tp['items'])}" for tp in topics)
                + "\nEvery item must be in an entry of its topic. Add what else of these topics' material a student "
                  "needs to solve its problems (a formula, special case, condition, unit or shortcut the list missed).")
    return (f"{head}\n{_FORMAT}\n- {written_in(sheet.get('language', ''))}\n\n"
            f"=== THE COURSE MATERIAL OF THESE TOPICS ===\n{mat}")


_CONTROL = {9: "\\t", 12: "\\f", 8: "\\b", 13: "\\r"}      # "\t", "\f"… JSON read where LaTeX meant \times, \frac
_BAD_TEX = re.compile(r"\\(?:begin|end)\{document\}|\\(?:input|include|write|def|newcommand|renewcommand|href|url)"
                      r"(?![A-Za-z])|\$")


def balanced(tex: str) -> bool:
    """Braces, \\begin/\\end and \\left/\\right that close: a formula KaTeX can read as a whole."""
    depth = 0
    for m in re.finditer(r"\\.|[{}]", tex):
        if m.group(0) == "{":
            depth += 1
        elif m.group(0) == "}":
            depth -= 1
            if depth < 0:
                return False
    if depth:
        return False
    if sorted(re.findall(r"\\begin\{([\w*]+)\}", tex)) != sorted(re.findall(r"\\end\{([\w*]+)\}", tex)):
        return False
    return len(re.findall(r"\\left(?![A-Za-z])", tex)) == len(re.findall(r"\\right(?![A-Za-z])", tex))


def clean_tex(v: Any) -> str:
    """A formula as LaTeX without its dollars; "" when there is none or it can't be drawn as a whole."""
    s = str(v or "").translate(_CONTROL).strip()
    for _ in range(2):
        for a, b in (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$")):
            if s.startswith(a) and s.endswith(b) and len(s) > len(a) + len(b):
                s = s[len(a):-len(b)].strip()
    s = re.sub(r"^\\displaystyle\s*", "", s)
    # a new row that starts with "[" ("\\ [LC] = s^2") is read by KaTeX as a row gap ("\\[4pt]"): keep it a bracket
    s = re.sub(r"(?<!\\)((?:\\\\)+)(\s*)\[(?!\s*-?[\d.]+\s*(?:pt|em|ex|mm|cm|in|mu|px|bp)\s*\])", r"\1{}\2[", s)
    if not s or len(s) > 900 or _BAD_TEX.search(s) or not balanced(s):
        return ""
    return s


_BARE_TEX = re.compile(r"(?:\\[A-Za-z]+(?:\{[^{}$]*\})*(?:[_^](?:\{[^{}$]*\}|[A-Za-z0-9]))*)+")


_DOLLAR = re.compile(r"(?<!\\)\$")


def _cut(s: str, limit: int) -> str:
    """``s`` in at most ``limit`` characters: all of it when it fits, else cut before the word or the formula the
    limit falls in, with "…" (a formula cut in half would print as its LaTeX)."""
    if len(s) <= limit:
        return s
    end = limit - 1
    dollars = [m.start() for m in _DOLLAR.finditer(s, 0, end)]
    if len(dollars) % 2:                                 # inside a formula: before it
        end = dollars[-1]
    elif not s[end].isspace():                           # inside a word: before it
        end = max(0, s.rfind(" ", 0, end)) or end
    out = s[:end].rstrip(" ,;:(")
    return out + "…" if out else _DOLLAR.sub("", s[:limit - 1]) + "…"     # (one formula longer than it all)


def _line(v: Any, limit: int) -> str:
    """A short text with $...$ maths in at most ``limit`` characters (see :func:`_cut`): its control characters as
    the backslashes they were, dollars that pair up, and LaTeX written outside a formula ("resistance (\\Omega)")
    made one."""
    from .studypack import _MATH

    s = _str(str(v or "").translate(_CONTROL), 10 ** 6)
    if len(_DOLLAR.findall(s)) % 2:
        s = _DOLLAR.sub("", s)                          # a formula that doesn't close: its text, not a broken one
    s = _cut(s, limit)
    if "\\" not in s:
        return s
    bare = lambda part: _BARE_TEX.sub(lambda m: f"\\({m.group(0)}\\)", part)   # noqa: E731
    out, i = [], 0
    for m in _MATH.finditer(s):
        out += [bare(s[i:m.start()]), m.group(0)]
        i = m.end()
    return "".join(out) + bare(s[i:])


def clean_entry(e: Any) -> Optional[dict[str, Any]]:
    """One entry as the AI wrote it, checked; None when it is empty (no name, or neither formula nor text)."""
    if not isinstance(e, dict):
        return None
    covers = [_str(c, 120) for c in (e.get("covers") if isinstance(e.get("covers"), list) else []) if _str(c)][:12]
    tex, text = clean_tex(e.get("tex")), _line(e.get("text"), 320)
    name = _line(e.get("name"), 90) or (covers[0] if covers else "")
    if not name or not (tex or text):
        return None
    kind = str(e.get("kind", "")).strip().lower()
    kind = kind if kind in KINDS else "formula" if tex else "concept"
    where = []
    for w in e.get("where") if isinstance(e.get("where"), list) else []:
        if isinstance(w, dict):
            sym, means = w.get("sym") or w.get("symbol"), w.get("means") or w.get("meaning")
        elif isinstance(w, (list, tuple)) and len(w) == 2:
            sym, means = w
        else:
            sym, _, means = str(w).partition(":")
        sym, means = clean_tex(sym), _line(means, 160)      # (a unit like ($\mathrm{V}$) is 14 of them)
        if sym and means and len(where) < 8:
            where.append({"sym": sym, "means": means})
    when = re.sub(r"^(?:if|when|only if|valid if|valid when)\s+", "", _line(e.get("when"), 200), flags=re.I)
    return {"kind": kind, "name": name, "tex": tex, "text": text, "where": where, "when": when, "covers": covers}


def _key(e: dict[str, Any]) -> str:
    """What makes two entries the same: their formula (spacing and braces around one character ignored), else their
    name and text."""
    if e["tex"]:
        tex = re.sub(r"\s+|\\[,;:! ]|\\left(?![A-Za-z])|\\right(?![A-Za-z])|\\displaystyle", "", e["tex"])
        return "T" + re.sub(r"([_^])\{(\w)\}", r"\1\2", tex)
    return "X" + _norm(e["name"]) + "|" + _norm(e["text"])


def clean_entries(raw: Any, topics: list[dict[str, Any]], seen: set[str], stats: dict[str, int]) -> dict[str, list]:
    """The entries the AI wrote for ``topics``: {topic id: [entry]}, each checked (clean_entry); entries already on
    the sheet (``seen``, updated) and empty ones are dropped and counted in ``stats``. An entry filed under a topic
    that was not asked for goes to the first one."""
    ids = {tp["id"] for tp in topics}
    out: dict[str, list] = {tp["id"]: [] for tp in topics}
    groups = raw.get("topics") if isinstance(raw, dict) else raw
    for g in groups if isinstance(groups, list) else []:
        if not isinstance(g, dict):
            continue
        tid = str(g.get("id", "")).strip()
        tid = tid if tid in ids else topics[0]["id"]
        for e in g.get("entries") if isinstance(g.get("entries"), list) else []:
            ok = clean_entry(e)
            if ok is None:
                stats["empty"] = stats.get("empty", 0) + 1
                continue
            k = _key(ok)
            if k in seen:
                stats["duplicates"] = stats.get("duplicates", 0) + 1
                continue
            seen.add(k)
            out[tid].append(ok)
    return out


def missing_items(topics: list[dict[str, Any]], entries: dict[str, list]) -> dict[str, list[str]]:
    """The items of the list no entry holds (by what the entries say they cover, or their names): {topic id: [item]}."""
    keys = {k for es in entries.values() for e in es for k in [_norm(c) for c in e["covers"]] + [_norm(e["name"])] if k}

    def held(item: str) -> bool:
        k = _norm(item)
        return any(k == x or (len(k) >= 5 and k in x) or (len(x) >= 5 and x in k) for x in keys)

    out = {tp["id"]: [i for i in tp["items"] if not held(i)] for tp in topics}
    return {k: v for k, v in out.items() if v}


# ------------------------------------------------------------------ the document
CSS_FILE = Path(__file__).resolve().parent / "web" / "cheatsheet.css"
_SHORT = re.compile(r"\\(?:frac|dfrac|tfrac|sum|prod|int|iint|oint|lim|begin|binom|over|underbrace|overbrace|sqrt\{.{10,})")
# A formula too wide for its column is made smaller, down to 70%; one that would need more is broken over lines after
# its = and + (KaTeX's own break points: class cs-wrap) at its size, and only a piece still too wide is made smaller,
# down to 55%. Nothing is cut off. (web/cheatsheet.js fit() is the same for the app's page.)
FIT_JS = ("(function(){function fit(){document.querySelectorAll('.cs-doc .cs-tex, .cs-doc .cs-x').forEach(function(el){"
          "el.style.fontSize='';el.classList.remove('cs-wrap');var w=el.clientWidth,s=el.scrollWidth;if(!w||s<=w+1)"
          "return;if(100*w/s<70&&el.classList.contains('cs-tex')){el.classList.add('cs-wrap');s=el.scrollWidth;"
          "if(s<=w+1)return;}el.style.fontSize=Math.max(55,Math.floor(100*w/s))+'%';});}fit();"
          "if(document.fonts&&document.fonts.ready)document.fonts.ready.then(fit);window.StudiaSheetFit=fit;})();")


# How a sheet is drawn: raised when the code draws it differently (entry_html, document_body), as changes of the
# stylesheet and of FIT_JS are seen by themselves. A finished sheet laid out with another is laid out again from its
# saved data, no AI, before its PDF is opened or saved (studypack.make_pdf).
LAYOUT = 3           # 2 (4.0): conditions keep their names, wide formulas are broken over lines instead of cut;
                     # 3 (4.0): a formula keeps the punctuation after it on its line


def layout() -> str:
    return f"{LAYOUT}-{hashlib.sha1(CSS_FILE.read_bytes() + FIT_JS.encode()).hexdigest()[:12]}"


def _esc(s: Any) -> str:
    return html.escape(str(s or ""), quote=False)


def _rich(text: str) -> str:
    """A short text: escaped, **bold** and *italic*, its $...$ formulas as written (KaTeX draws them)."""
    from .studypack import _MATH, _inline

    keep: list[str] = []

    def hold(m: re.Match) -> str:
        keep.append(m.group(0))
        return f"\x00{len(keep) - 1}\x00"

    return _inline(_MATH.sub(hold, text.replace("\x00", "")), keep)


# a condition that starts with one of these words reads on after "if" in lowercase ("Series RLC circuit" → "if series
# RLC circuit"); any other capital stays: a name ("Thevenin resistance", "Bayes' theorem") or a symbol ("A is
# invertible")
_COMMON_START = set("""series parallel source-free no not all each every both any either the an linear nonlinear ideal
    initial steady small large constant zero finite infinite for in at on with without under after before between using
    only one two three several independent dependent discrete continuous mutually positive negative real complex square
    sinusoidal natural forced open short first second higher lower low high equal same different given known there its
    this these those sum product""".split())


def _condition(when: str) -> str:
    m = re.match(r"([A-Z][a-z]+(?:-[a-z]+)*)(?![\w'’])", when)
    return when[0].lower() + when[1:] if m and m.group(1).lower() in _COMMON_START else when


def entry_html(e: dict[str, Any], explained: Optional[set[str]] = None) -> str:
    """One entry: its kind (not for a formula), name, formula (in the line when it is short), text, symbols and
    conditions. A symbol already ``explained`` (by an entry before it in its topic) is not explained again."""
    tag = TAGS.get(e["kind"])
    explained = set() if explained is None else explained
    where = [w for w in e["where"] if re.sub(r"\s+", "", w["sym"]) not in explained]
    explained.update(re.sub(r"\s+", "", w["sym"]) for w in where)
    when = _condition(e["when"])
    inline = e["tex"] and len(e["tex"]) <= 34 and not _SHORT.search(e["tex"])
    out = [f'<div class="cs-e cs-{e["kind"]}"><div class="cs-h">'
           + (f'<i class="cs-k">{tag}</i>' if tag else "") + f'<b class="cs-n">{_rich(e["name"])}</b>'
           + (f' <span class="cs-x">\\({_esc(e["tex"])}\\)</span>' if inline else "") + "</div>"]
    if e["tex"] and not inline:
        out.append(f'<div class="cs-tex">\\[{_esc(e["tex"])}\\]</div>')
    if e["text"]:
        out.append(f'<div class="cs-t">{_rich(e["text"])}</div>')
    if where:
        out.append('<div class="cs-w">' + " · ".join(f'<span>\\({_esc(w["sym"])}\\) {_rich(w["means"])}</span>'
                                                      for w in where) + "</div>")
    if when:
        out.append(f'<div class="cs-if"><b>if</b> {_rich(when)}</div>')
    return "".join(out) + "</div>"


def _day(iso: str) -> str:
    try:
        return f"{dt.date.fromisoformat(iso[:10]):%d %b}".lstrip("0")
    except ValueError:
        return iso


def material_line(meta: dict[str, Any]) -> str:
    """What the sheet was made from, short: the weeks, the files (the first few by name) and the lectures' dates."""
    files = [f["name"] for f in meta.get("files") or []]
    days = [_day(x["date"]) for x in meta.get("lectures") or []]
    parts = [meta["span"]]
    if files:
        parts.append(", ".join(files[:5]) + (f" and {len(files) - 5} more files" if len(files) > 5 else ""))
    if days:
        parts.append(("lecture " if len(days) == 1 else "lectures ") + ", ".join(days[:8])
                     + (f" and {len(days) - 8} more" if len(days) > 8 else ""))
    return " · ".join(parts)


def document_body(sheet: dict[str, Any], entries: dict[str, list], meta: dict[str, Any]) -> str:
    """The sheet: a title line (course, title, material, date), then its topics in order, each a heading and its
    entries (a heading is kept with its first entry: never alone at the foot of a column)."""
    out = [f'<header class="cs-head"><div class="cs-title"><span class="cs-course">{_esc(meta["course"])}</span>'
           f'<span class="cs-kind">Cheatsheet</span><b>{_esc(sheet["title"])}</b></div>'
           f'<div class="cs-meta">{_esc(material_line(meta))} · {_esc(meta["date"])}</div></header><div class="cs-cols">']
    n = 0
    for tp in sheet["topics"]:
        es = entries.get(tp["id"]) or []
        if not es:
            continue
        n += 1
        explained: set[str] = set()                       # symbols are explained once per topic
        out.append(f'<section class="cs-topic" id="{tp["id"]}"><div class="cs-keep"><h2><span class="n">{n}</span>'
                   f'{_esc(tp["title"])}</h2>{entry_html(es[0], explained)}</div>'
                   f'{"".join(entry_html(e, explained) for e in es[1:])}</section>')
    return "".join(out) + "</div>"


def document_html(body: str, title: str, folder: Optional[Path] = None) -> str:
    """The sheet as a page of its own (the file and the PDF): A4 in three columns, KaTeX (a copy in ``folder``, the
    kept file's, so it works anywhere; else the app's), and formulas too wide for their column made smaller (the page
    is laid out at the PDF's width, so what fits on screen fits on paper)."""
    from .outputs.mathmd import katex_tags

    css = CSS_FILE.read_text(encoding="utf-8")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
            f'<style>{css}</style></head><body class="cs-page"><article class="cs-doc">{body}</article>'
            f"{katex_tags(folder)}<script>{FIT_JS}</script></body></html>")


def meta_of(facts: dict[str, Any], made: Optional[dt.date] = None) -> dict[str, Any]:
    c = facts["course"]
    return {"course": " · ".join(x for x in (c.get("code"), c.get("name")) if x), "span": facts["span"].capitalize(),
            "files": facts["files"], "lectures": facts["lectures"],
            "date": f"{made or dt.date.today():%d %b %Y}".lstrip("0")}


def files_for(s: Any, plan: dict[str, Any], title: str, data: Optional[dict[str, Any]] = None) -> tuple[Path, Path]:
    """Where a cheatsheet's files go: <course>/Cheatsheets/<date> - <title>.html and .pdf (studypack.file_stem: with
    ``data``, a name no other cheatsheet has, kept there)."""
    from .outputs.writer import course_dir
    from .studypack import file_stem

    course = s.lib.get_course(plan["course_id"])
    folder = course_dir(s.output_root, course, s.lib.courses(True)) / "Cheatsheets"
    stem = file_stem(plan, title, folder, (".html", ".pdf"), data)
    return folder / f"{stem}.html", folder / f"{stem}.pdf"


def pdf_pages(pdf: Path) -> int:
    try:
        import pypdfium2 as pdfium

        from .materials import PDFIUM_LOCK

        with PDFIUM_LOCK:
            doc = pdfium.PdfDocument(str(pdf))
            try:
                return len(doc)
            finally:
                doc.close()
    except Exception:  # noqa: BLE001 - only shown as a number
        return 0


def assemble(s: Any, plan: dict[str, Any], sheet: dict[str, Any], facts: dict[str, Any],
             entries: dict[str, list], checks: dict[str, Any], data: Optional[dict[str, Any]] = None,
             made: Optional[dt.date] = None) -> dict[str, Any]:
    """Write the document and its PDF; the result the app shows. ``made``: the day it says it was made (today)."""
    from .studypack import _print_to, _write

    made = made or dt.date.today()
    html_path, pdf_path = files_for(s, plan, sheet["title"], data)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    _write(html_path, document_html(document_body(sheet, entries, meta_of(facts, made)), sheet["title"],
                                    html_path.parent))
    pdf_ok = False
    try:
        pdf_ok = _print_to(html_path, pdf_path) and pdf_path.is_file()
    except Exception:  # noqa: BLE001 - the sheet is still shown in the app
        import logging

        logging.getLogger("studia").warning("The cheatsheet's PDF could not be made", exc_info=True)
    shown = [tp for tp in sheet["topics"] if entries.get(tp["id"])]
    return {"kind": "sheet", "title": sheet["title"], "file": str(html_path), "pdf": str(pdf_path) if pdf_ok else "",
            "pages": pdf_pages(pdf_path) if pdf_ok else 0, "layout": layout(), "made": made.isoformat(),
            "topics": [{"id": tp["id"], "title": tp["title"], "entries": len(entries[tp["id"]])} for tp in shown],
            "entries": sum(len(v) for v in entries.values()), "items": sum(len(tp["items"]) for tp in sheet["topics"]),
            "uncovered": [i for v in (checks.get("missing") or {}).values() for i in v],
            "repaired": checks.get("repaired", 0), "duplicates": checks.get("duplicates", 0),
            "empty": checks.get("empty", 0), "files": len(facts["files"]), "lectures": len(facts["lectures"]),
            "cut": facts.get("cut") or [], "files_cut": facts.get("files_cut") or [],
            "materials_missing": facts["materials_missing"], "days_missing": facts["days_missing"]}


def shown_body(s: Any, plan: dict[str, Any]) -> str:
    """The sheet's body for the app's page (the same HTML as its file)."""
    from .studypack import made_on

    data = plan.get("data") or {}
    sheet, facts = data.get("topics"), data.get("gather")
    if not sheet or not facts:
        return ""
    return document_body(sheet, _all_entries(data), meta_of(facts, made_on(plan)))


def _all_entries(data: dict[str, Any]) -> dict[str, list]:
    """The entries of every batch and of the repair, by topic, in the list's order."""
    out: dict[str, list] = {tp["id"]: [] for tp in (data.get("topics") or {}).get("topics", [])}
    for part in [*(data.get("entries") or {}).values(), (data.get("check") or {}).get("entries") or {}]:
        for tid, es in part.items():
            out.setdefault(tid, []).extend(es)
    return out


# ------------------------------------------------------------------ the stages (run by plans.PlanRunner)
def run(runner: Any, plan: dict[str, Any], scope: Any, data: dict[str, Any], usage: dict[str, Any], model: str,
        cancel: threading.Event) -> None:
    """Build (or resume) the cheatsheet ``plan``; every finished stage and batch is saved at once."""
    from .ai import ask_json
    from .plans import model_shown
    from .studypack import _level, _usage_add, gather

    pid, s = plan["id"], runner._s
    level = _level(scope, s.settings)
    shown = model_shown(model, level)       # "GPT-6.1-Sol · High", "Sonnet · Low"
    lock = threading.Lock()

    def stage(name: str, progress: float, message: str = "") -> None:
        s.lib.update_plan(pid, status="running", stage=name, progress=round(progress, 3),
                          message=message or t(LABELS[name]) + "…")
        runner._emit(pid)

    def save(**more: Any) -> None:
        with lock:
            s.lib.update_plan(pid, data=data, usage=usage, **more)

    def check() -> None:
        if cancel.is_set():
            raise Cancelled()

    def ask(prompt: str) -> tuple[Any, dict]:
        return (runner._ask or ask_json)(SYSTEM, prompt, model=model, effort=level, cancel=cancel)

    # gather
    if "gather" not in data:
        stage("gather", 0.02)
        data["gather"] = gather(s.lib, s.settings, plan["course_id"], scope)
        save()
    facts = data["gather"]
    if not facts["files"] and not facts["lectures"]:
        raise RuntimeError(t("None of the picked files is on this PC and none of the lectures is transcribed yet."))
    check()
    # topics: what the sheet must hold
    if "topics" not in data:
        stage("topics", 0.06, t("Listing the formulas and concepts ({model})…", model=shown))
        raw, res = ask(topics_prompt(facts))
        _usage_add(usage, "topics", res)
        data["topics"] = clean_topics(raw, facts)
        save(title=data["topics"]["title"])
    sheet = data["topics"]
    topics = sheet["topics"]
    check()
    # entries, a few topics at a time
    if "batches" not in data:
        sizes = [len(material(s.lib, plan["course_id"], facts, [tp])) if tp["sources"]["files"] or
                 tp["sources"]["lectures"] else 4_000 for tp in topics]
        data["batches"] = [[topics[i]["id"] for i in b] for b in make_batches(sizes)]
        save()
    batches: list[list[str]] = data["batches"]
    done: dict[str, dict[str, list]] = data.setdefault("entries", {})
    stats: dict[str, Any] = data.setdefault("checks", {})
    seen = {_key(e) for part in done.values() for es in part.values() for e in es}
    left = [i for i in range(len(batches)) if str(i) not in done]
    by_id = {tp["id"]: tp for tp in topics}
    if left:
        def progress() -> None:
            stage("entries", 0.2 + 0.6 * len(done) / len(batches),
                  t("Writing the entries ({model})…", model=shown) if len(batches) == 1 else
                  t("Writing the entries: part {n} of {total} ({model})…", n=min(len(done) + 1, len(batches)),
                    total=len(batches), model=shown))
            aiwait.of(cancel).units(len(done), len(batches), PARALLEL)      # the parts still to come (time left)

        def write(i: int) -> tuple[int, Any, dict]:
            group = [by_id[x] for x in batches[i]]
            raw, res = ask(entries_prompt(sheet, group, material(s.lib, plan["course_id"], facts, group), facts["span"]))
            return i, raw, res

        def keep(f: Any) -> None:
            i, raw, res = f.result()
            with lock:
                if str(i) not in done:
                    done[str(i)] = clean_entries(raw, [by_id[x] for x in batches[i]], seen, stats)
                    _usage_add(usage, "entries", res)

        progress()
        failed: Optional[BaseException] = None
        with ThreadPoolExecutor(max_workers=PARALLEL, thread_name_prefix="sheet-write") as pool:
            futures = [pool.submit(write, i) for i in left]
            try:
                for f in as_completed(futures):
                    keep(f)
                    save()
                    progress()
            except BaseException as exc:  # noqa: BLE001 - raised again below
                failed = exc
                for f in futures:
                    f.cancel()
        if failed is not None:                       # a batch that was finished meanwhile is kept
            for f in futures:
                if f.done() and not f.cancelled() and f.exception() is None:
                    keep(f)
            save()
            raise failed
    check()
    # check: what the list names and no entry holds, asked for once more
    if "check" not in data:
        missing = missing_items(topics, _all_entries(data))
        fixed: dict[str, list] = {}
        if missing:
            stage("check", 0.84, tn(sum(len(v) for v in missing.values()), "Adding {n} missing item ({model})…",
                                    "Adding {n} missing items ({model})…", model=shown))
            group = [tp for tp in topics if tp["id"] in missing]
            raw, res = ask(entries_prompt(sheet, group, material(s.lib, plan["course_id"], facts, group),
                                          facts["span"], missing))
            _usage_add(usage, "check", res)
            fixed = clean_entries(raw, group, seen, stats)
        data["check"] = {"entries": fixed}
        after = missing_items(topics, _all_entries(data))
        stats["repaired"] = sum(len(v) for v in missing.values()) - sum(len(v) for v in after.values())
        stats["missing"] = after
        save()
    check()
    # assemble
    stage("assemble", 0.92)
    entries = _all_entries(data)
    if not data.get("stem"):
        files_for(s, plan, sheet["title"], data)                  # its file name, kept before anything is written
        save()
    result = assemble(s, plan, sheet, facts, entries, stats, data)
    message = tn(result["entries"], "{n} entry", "{n} entries")
    if result["pages"]:
        message += " · " + tn(result["pages"], "{n} page", "{n} pages")
    save(result=result, title=result["title"], status="done", stage="done", progress=1.0, message=message)


def progress_info(data: dict[str, Any]) -> dict[str, Any]:
    """For the plan page while a sheet is being made: the stages done and its topics (written or not)."""
    topics = (data.get("topics") or {}).get("topics", [])
    written = {tid for part in (data.get("entries") or {}).values() for tid in part}
    steps = {"gather": "gather" in data, "topics": bool(topics),
             "entries": bool(data.get("batches")) and all(str(i) in (data.get("entries") or {})
                                                           for i in range(len(data["batches"]))),
             "check": "check" in data}
    return {"stages_done": [k for k, ok in steps.items() if ok],
            "chapters": [{"id": tp["id"], "title": tp["title"], "done": tp["id"] in written} for tp in topics]}


# ------------------------------------------------------------------ the cost, before it starts
def estimate(lib: Any, settings: Any, course_id: str, scope: Any) -> dict[str, Any]:
    """Roughly what a cheatsheet costs: tokens in and out, requests and time."""
    from .ai import PROMPT_OVERHEAD, chars_per_token
    from .plans import main_model
    from .studypack import _level, gather

    per = chars_per_token()
    facts = gather(lib, settings, course_id, scope)
    full = facts["full_chars"]
    batches = max(1, -(-int(full * 0.8) // BATCH_CHARS))           # pages of a file are often shared by topics
    repair = 1
    tokens_in = (facts["chars"] / per + PROMPT_OVERHEAD + 600) \
        + batches * (min(full, BATCH_CHARS) / per + PROMPT_OVERHEAD + 1_400) \
        + repair * (BATCH_CHARS / 2 / per + PROMPT_OVERHEAD + 1_000)
    tokens_out = 1_500 + full / 60 + batches * 4_000 + repair * 1_200
    a = 2 + round(batches * 1.5 / PARALLEL) + repair
    return {"main_model": main_model(scope, settings), "effort": _level(scope, settings),
            "main_input": int(tokens_in), "main_output": int(tokens_out), "requests": 1 + batches + repair, "chapters": batches, "files": len(facts["files"]),
            "lectures": len(facts["lectures"]), "materials_missing": facts["materials_missing"],
            "days_missing": facts["days_missing"], "files_cut": facts["files_cut"],
            "minutes": t("about {a}–{b} minutes", a=a, b=a * 2)}
