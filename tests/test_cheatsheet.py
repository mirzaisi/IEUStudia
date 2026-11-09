"""Cheatsheets: the pipeline with the AI replaced by a fake (topics, entries a few topics at a time, the missing items
asked for once more; resume after a failure), the checks of what the AI answers (empty, duplicate and unreadable
entries, items no entry holds), the document's HTML, and its layout on paper through the real Edge print path (A4,
three columns, the sample on one page, a formula too wide for its column made smaller)."""

import copy
import re
import sys
import types
from pathlib import Path

import pytest

import cheatsheet_sample as SAMPLE
from studia import cheatsheet as cs, studypack
from studia.plans import PlanRunner, Scope
from test_studypack import env, wait_done  # noqa: F401 - the study pack's course, files, lecture and fakes

FACTS = {"files": [{"ref": "F1", "pages": 5, "pdf": True}], "lectures": [{"ref": "L1", "parts": [{"part": 1, "slides": 0}]}],
         "span": "weeks 4–6"}


def fake_ai(log, fail_on=None, drop=("supermesh",)):
    """Answers like the AI: the sample's topics, then its entries for the topics asked for - minus the entries of
    ``drop`` (asked for again by the check), plus an empty entry, a duplicate and a formula that doesn't close."""
    hits = []

    def ask(system, prompt, model="sonnet", cancel=None, effort="", **kw):
        usage = {"usage": {"input_tokens": 1000, "output_tokens": 300}, "model": model}
        if prompt.startswith("TASK: List everything"):
            log.append(("topics", prompt))
            return copy.deepcopy(SAMPLE.TOPICS), usage
        if prompt.startswith("TASK: These items are missing"):
            log.append(("check", prompt))
            return {"topics": [{"id": "t2", "entries": [e for e in SAMPLE.ENTRIES["t2"] if e["name"] == "Supermesh"]}]}, usage
        ids = re.findall(r"^(t\d+) · ", prompt, re.M)
        log.append(("entries " + ",".join(ids), prompt))
        if fail_on and fail_on in ids:
            hits.append(1)
            if len(hits) == 1:
                raise RuntimeError("usage limit")
        ans = SAMPLE.answer_for(prompt)
        for g in ans["topics"]:
            g["entries"] = [e for e in g["entries"] if not set(map(str.lower, e["covers"])) & set(drop)]
            if g["id"] == "t1":
                g["entries"] += [{"kind": "formula", "name": "", "tex": "", "text": ""},                        # empty
                                 {**g["entries"][0], "name": "Ohm again", "tex": "v  =  i R"},                   # same formula
                                 {"kind": "formula", "name": "Broken", "tex": r"\frac{a}{b", "covers": []}]     # unreadable
        return ans, usage

    return ask


def test_cheatsheet_pipeline_end_to_end(env):  # noqa: F811
    log = []
    runner = PlanRunner(env.svc, ask=fake_ai(log))
    pid = runner.create("c", {"kind": "sheet", "weeks": [1], "files": ["r1"]})
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "done"
    plan = env.lib.plan(pid)
    r, data = plan["result"], plan["data"]
    assert r["kind"] == "sheet" and plan["title"] == r["title"] == SAMPLE.TOPICS["title"]
    asked = [k for k, _ in log]
    assert asked[0] == "topics" and asked[-1] == "check" and len(asked) == 4      # two batches of topics, one repair
    assert sorted(asked[1:3]) == ["entries t1,t2,t3,t4,t5,t6", "entries t7"]
    first = dict(log)["entries t1,t2,t3,t4,t5,t6"]
    assert "t1 · Basic laws: Ohm's law; power; KCL" in first and "[F1 p.1] Conditional probability" in first
    assert "supermesh" in dict(log)["check"] and "Ohm's law" not in dict(log)["check"].split("\n", 3)[1]
    assert [x["title"] for x in r["topics"]] == [x["title"] for x in SAMPLE.TOPICS["topics"]]
    assert r["entries"] == sum(len(v) for v in SAMPLE.ENTRIES.values()) and r["uncovered"] == []
    assert r["repaired"] == 1 and r["duplicates"] == 1 and r["empty"] == 2          # the broken formula had no text
    assert plan["message"].startswith(f"{r['entries']} entries")
    folder = env.tmp / "out" / "MATH 240" / "Cheatsheets"
    page = next(folder.glob("*.html")).read_text(encoding="utf-8")
    assert "Cheatsheet</span><b>Circuit analysis" in page and 'src="katex/katex.min.js"' in page and "file:" not in page and "StudiaSheetFit" in page
    assert "Week01.pdf" in page and "lecture 23 Sep" in page                       # the material it was made from
    assert page.index("Basic laws") < page.index("Nodal and mesh") < page.index("Sinusoids and phasors")
    assert page.index('id="t2"') < page.index("Supermesh")                         # the repaired entry in its topic
    assert env.printed and r["pdf"] == ""                                           # the fake printer makes none
    assert studypack.shown_body(env.svc, plan).startswith('<header class="cs-head">')
    assert plan["usage"]["entries"]["input_tokens"] == 2000 and plan["usage"]["check"]["input_tokens"] == 1000
    info = studypack.progress_info(data, "sheet")
    assert info["stages_done"] == ["gather", "topics", "entries", "check"] and all(c["done"] for c in info["chapters"])


def test_two_cheatsheets_with_the_same_title_on_the_same_day_keep_their_own_files(env):  # noqa: F811
    runner = PlanRunner(env.svc, ask=fake_ai([]))
    ids = [runner.create("c", {"kind": "sheet", "weeks": [1], "files": ["r1"]}) for _ in range(2)]
    for pid in ids:
        runner.start(pid)
        assert wait_done(env.lib, pid)["status"] == "done"
    files = [env.lib.plan(pid)["result"]["file"] for pid in ids]
    assert files[0] != files[1] and files[1].endswith(" (2).html")
    assert len(list((env.tmp / "out" / "MATH 240" / "Cheatsheets").glob("*.html"))) == 2


def test_a_stopped_cheatsheet_resumes_where_it_stopped(env):  # noqa: F811
    log = []
    runner = PlanRunner(env.svc, ask=fake_ai(log, fail_on="t7", drop=()))
    pid = runner.create("c", {"kind": "sheet", "weeks": [1]})
    runner.start(pid)
    p = wait_done(env.lib, pid)
    assert p["status"] == "error" and "usage limit" in p["error"]
    data = env.lib.plan(pid)["data"]
    assert "topics" in data and len(data["batches"]) == 2 and set(data["entries"]) <= {"0"}
    log.clear()
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "done"
    asked = [k for k, _ in log]
    assert "topics" not in asked and asked.count("entries t7") == 1 and "check" not in asked     # nothing was missing
    assert asked.count("entries t1,t2,t3,t4,t5,t6") == (0 if data["entries"] else 1)


def _made_on(lib, pid, day):
    with lib._lock:
        lib.db.execute("UPDATE plans SET created_at=? WHERE id=?", (f"{day}T18:29:00", pid))
        lib.db.commit()


def test_an_old_sheets_pdf_is_laid_out_again_from_its_saved_data_when_it_is_opened(env, monkeypatch):  # noqa: F811
    """A 3.2.1 sheet kept printing its symbols' meanings over the next column: Open PDF, Save PDF and Show in folder
    gave the stored PDF, and a deleted one was printed again from the stored HTML (its stylesheet inside)."""
    log = []
    runner = PlanRunner(env.svc, ask=fake_ai(log))
    pid = runner.create("c", {"kind": "sheet", "weeks": [1], "files": ["r1"]})
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "done"
    printed = []

    def printer(html_path, pdf_path):                    # Edge: a PDF of what the HTML holds now
        printed.append(pdf_path.name)
        pdf_path.write_bytes(b"%PDF new " + html_path.read_bytes()[-40:])
        return True

    monkeypatch.setattr(studypack, "print_pdf", printer)
    plan = env.lib.plan(pid)
    r = plan["result"]
    html_path, pdf_path = Path(r["file"]), Path(r["file"]).with_suffix(".pdf")
    html_path.write_text("<style>.cs-w > span { white-space: nowrap; }</style> 3.2.1", encoding="utf-8")
    pdf_path.write_bytes(b"%PDF old")
    old = {k: v for k, v in r.items() if k not in ("layout", "made")}      # as a sheet made before 4.0 has it
    env.lib.update_plan(pid, result={**old, "pdf": str(pdf_path)})
    _made_on(env.lib, pid, "2026-11-06")
    log.clear()
    got = studypack.make_pdf(env.svc, env.lib.plan(pid))
    assert got == pdf_path and pdf_path.read_bytes().startswith(b"%PDF new") and log == []      # no AI
    page = html_path.read_text(encoding="utf-8")
    assert "cs-wrap" in page and "nowrap; }</style> 3.2.1" not in page and "· 6 Nov 2026</div>" in page
    assert printed == [pdf_path.name + ".part"]                                  # the old one replaced when whole
    again = env.lib.plan(pid)["result"]
    assert again["layout"] == cs.layout() and again["made"] == "2026-11-06" and again["file"] == str(html_path)
    assert sorted(p.name for p in html_path.parent.iterdir() if p.suffix in (".html", ".pdf", ".part")) == \
        sorted([html_path.name, pdf_path.name])                                  # the same names, no copies
    assert "· 6 Nov 2026</div>" in studypack.shown_body(env.svc, env.lib.plan(pid))   # the app's page: its day too
    printed.clear()
    assert studypack.make_pdf(env.svc, env.lib.plan(pid)) == pdf_path and printed == []   # laid out: as it is
    html_path.unlink()
    pdf_path.unlink()                                                            # both deleted: made again
    assert studypack.make_pdf(env.svc, env.lib.plan(pid)) == pdf_path and html_path.is_file() and printed


def test_an_old_sheets_pdf_stays_when_it_cant_be_printed_again(env, monkeypatch):  # noqa: F811
    runner = PlanRunner(env.svc, ask=fake_ai([]))
    pid = runner.create("c", {"kind": "sheet", "weeks": [1], "files": ["r1"]})
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "done"
    r = env.lib.plan(pid)["result"]
    pdf_path = Path(r["file"]).with_suffix(".pdf")
    pdf_path.write_bytes(b"%PDF old")
    env.lib.update_plan(pid, result={**r, "pdf": str(pdf_path), "pages": 2, "layout": "1-old"})
    monkeypatch.setattr(studypack, "print_pdf", lambda h, p: False)             # Edge busy
    assert studypack.make_pdf(env.svc, env.lib.plan(pid)) == pdf_path and pdf_path.read_bytes() == b"%PDF old"
    kept = env.lib.plan(pid)["result"]
    assert kept["layout"] == "1-old" and kept["pdf"] == str(pdf_path) and kept["pages"] == 2   # tried again next time


def test_the_topics_are_checked():
    raw = {"title": "", "topics": [
        {"id": "a", "title": "One", "items": ["Ohm's law", {"name": "KCL"}, "", "ohm’s  law"],
         "sources": {"files": [{"ref": "F1", "pages": [4, 99]}, {"ref": "F9"}], "lectures": [{"ref": "L1", "part": 1, "from": "1:00"}]}},
        {"id": "b", "title": "", "items": ["no title"]},
        {"id": "c", "title": "Only repeats", "items": ["KCL"]},
        {"id": "d", "title": "Two", "items": ["KVL"]}]}
    out = cs.clean_topics(raw, FACTS)
    assert out["title"] == "Weeks 4–6" and [x["id"] for x in out["topics"]] == ["t1", "t2"]
    one = out["topics"][0]
    assert one["items"] == ["Ohm's law", "KCL"]                        # "ohm’s  law" is the same item
    assert one["sources"] == {"files": [{"ref": "F1", "pages": [4, 5]}], "lectures": [{"ref": "L1", "part": 1, "from": 60, "to": None}]}
    with pytest.raises(RuntimeError):
        cs.clean_topics({"topics": [{"title": "x", "items": []}]}, FACTS)


def test_entries_are_checked():
    topics = [{"id": "t1", "items": ["a"]}, {"id": "t2", "items": ["b"]}]
    stats, seen = {}, set()
    raw = {"topics": [
        {"id": "t1", "entries": [
            {"kind": "law", "name": "Area", "tex": "$$A = \\pi r^2$$", "where": [{"sym": "$r$", "means": "radius (m)"},
                                                                           ["\\theta", "angle"], "x: unknown", {"sym": "", "means": "?"}],
             "when": "If r > 0", "covers": ["area"]},
            {"name": "Product", "tex": "a\times b \frac{1}{2}", "text": "costs $5 and"},       # JSON read \t and \f
            {"name": "Area again", "tex": "A=\\pi  r^{2}"},                                   # spacing aside: Area
            {"name": "Broken", "tex": "\\left( x", "text": ""},
            {"name": "Dollars inside", "tex": "x $y$"},
            {"name": "", "text": "no name, no covers"},
            "not an entry"]},
        {"id": "zz", "entries": [{"name": "Moved", "text": "to the first topic", "kind": "?"}]},
        {"id": "t2", "entries": [{"name": "Area", "tex": "A = \\pi r^2"}]}]}
    out = cs.clean_entries(raw, topics, seen, stats)
    names = [e["name"] for e in out["t1"]]
    assert names == ["Area", "Product", "Moved"] and out["t2"] == []
    area, prod, moved = out["t1"]
    assert area["kind"] == "formula" and area["tex"] == "A = \\pi r^2" and area["when"] == "r > 0"
    assert area["where"] == [{"sym": "r", "means": "radius (m)"}, {"sym": "\\theta", "means": "angle"}, {"sym": "x", "means": "unknown"}]
    assert prod["tex"] == "a\\times b \\frac{1}{2}" and prod["text"] == "costs 5 and"     # a dollar that doesn't close
    assert moved["kind"] == "concept"
    assert stats == {"duplicates": 2, "empty": 4}
    assert cs.missing_items([{"id": "t1", "items": ["area", "Product rule", "volume"]}], out) == {"t1": ["volume"]}


def test_latex_outside_a_formula_in_the_text_is_drawn():
    e = cs.clean_entry({"kind": "formula", "name": "Mesh", "tex": "[R]\\mathbf{i} = \\mathbf{v}", "text": "costs \\$5, $x$ in \\mu\\mathrm{F}",
                        "where": [{"sym": "R", "means": "branch resistance (\\Omega)"}, {"sym": "x", "means": "$x_1$ to \\omega_0^2 (k\\Omega)"}],
                        "when": "\\alpha > 0"})
    assert [w["means"] for w in e["where"]] == ["branch resistance (\\(\\Omega\\))", "$x_1$ to \\(\\omega_0^2\\) (k\\(\\Omega\\))"]
    assert e["text"] == "costs \\$5, $x$ in \\(\\mu\\mathrm{F}\\)" and e["when"] == "\\(\\alpha\\) > 0"
    page = cs.entry_html(e)
    assert "(⁠\\(\\Omega\\)⁠)" in page and "(\\Omega)" not in page      # (kept on one line with its brackets)


def test_a_long_symbol_legend_keeps_its_unit_and_a_cut_one_never_prints_latex():
    """Legends were cut at 90 characters mid-word, and a cut inside the unit's $...$ printed the LaTeX source of every
    formula in it (a real sheet: "…; $\\mathrm{A}$ for current ")."""
    long = "inductor voltage, positive at the terminal where the referenced current enters ($\\mathrm{V}$)"
    real = "real sinusoidal response amplitudes ($\\mathrm{V}$ for voltage responses; $\\mathrm{A}$ for current responses)"
    e = cs.clean_entry({"kind": "rule", "name": "Inductor", "tex": "v_L = L\\frac{di}{dt}", "text": "",
                        "where": [{"sym": "v_L", "means": long}, {"sym": "P_1", "means": real}]})
    assert [w["means"] for w in e["where"]] == [long, real]
    assert cs._line("the amplitude of the underdamped response in the series circuit", 40) == "the amplitude of the underdamped…"
    assert cs._line("current $i_1$, then $\\mathrm{A}$ for current responses", 28) == "current $i_1$, then…"
    cut = cs._line("the matrix $I_n=\\begin{bmatrix}\\mathbf{e}_1&\\cdots&\\mathbf{e}_n\\end{bmatrix}$ of columns", 60)
    assert cut == "the matrix…" and "\\begin" not in cs.entry_html({**e, "where": [{"sym": "I_n", "means": cut}]})


def test_a_formula_must_close():
    assert cs.balanced(r"\frac{a}{b} \left( \begin{pmatrix} 1 \\ 2 \end{pmatrix} \right) \{x\}")
    for bad in (r"\frac{a}{b", r"a}{", r"\begin{cases} x", r"\left( x", r"\begin{bmatrix} 1 \end{pmatrix}"):
        assert not cs.balanced(bad), bad
    assert cs.clean_tex(r"\[ \displaystyle x \]") == "x" and cs.clean_tex(r"\newcommand{\x}{1}") == ""
    # a row starting with a bracket is not a row gap (a real answer: "\\[LC]&=\mathrm{s}^2" was KaTeX's error)
    assert cs.clean_tex(r"\begin{aligned}a&=1,\\[LC]&=s^2\\[4pt] b&=2\end{aligned}") == \
        r"\begin{aligned}a&=1,\\{}[LC]&=s^2\\[4pt] b&=2\end{aligned}"


def test_topics_go_in_batches_by_their_material():
    assert cs.make_batches([10_000, 10_000, 20_000, 40_000, 1_000]) == [[0, 1], [2], [3], [4]]
    assert cs.make_batches([100] * 8, most=6) == [[0, 1, 2, 3, 4, 5], [6, 7]]
    merged = cs.merge_sources([
        {"sources": {"files": [{"ref": "F1", "pages": [1, 3]}, {"ref": "F2", "pages": [5, 5]}],
                     "lectures": [{"ref": "L1", "part": 1, "from": 0, "to": 60}]}},
        {"sources": {"files": [{"ref": "F1", "pages": [4, 6]}, {"ref": "F1", "pages": [9, 9]}],
                     "lectures": [{"ref": "L1", "part": 1, "from": 0, "to": 60}]}}])
    assert merged["files"] == [{"ref": "F1", "pages": [1, 6]}, {"ref": "F1", "pages": [9, 9]}, {"ref": "F2", "pages": [5, 5]}]
    assert len(merged["lectures"]) == 1


def sample_sheet(copies=1):
    topics, entries, seen, stats = [], {}, set(), {}
    for k in range(copies):
        for tp in SAMPLE.TOPICS["topics"]:
            tid = f"t{len(topics) + 1}"
            topics.append({**tp, "id": tid, "sources": {"files": [], "lectures": []}})
            raw = [{"id": tid, "entries": [{**e, "tex": e["tex"] + (f"+{k}" if k and e["tex"] else ""),
                                             "text": e["text"] + (f" ({k})" if k and e["text"] else "")}
                                            for e in SAMPLE.ENTRIES[tp["id"]]]}]
            entries.update(cs.clean_entries({"topics": raw}, [topics[-1]], seen, stats))
    return {"title": SAMPLE.TOPICS["title"], "topics": topics}, entries


META = {"course": "EEE 215 · Circuit Analysis I", "span": "Weeks 4–6", "date": "4 Nov 2026",
        "files": [{"name": f"Week0{n}.pdf"} for n in range(1, 8)], "lectures": [{"date": "2026-10-14"}]}


def test_the_document():
    sheet, entries = sample_sheet()
    sheet["topics"].append({"id": "t9", "title": "Empty topic", "items": ["x"]})
    entries["t2"][0] = {**entries["t2"][0], "name": "<img src=x onerror=alert(1)> **bold**"}
    body = cs.document_body(sheet, entries, META)
    assert body.startswith('<header class="cs-head"><div class="cs-title"><span class="cs-course">EEE 215 · Circuit Analysis I')
    assert "Weeks 4–6 · Week01.pdf, Week02.pdf, Week03.pdf, Week04.pdf, Week05.pdf and 2 more files · lecture 14 Oct · 4 Nov 2026" in body
    assert body.count('<section class="cs-topic"') == 7 and "Empty topic" not in body
    assert '<div class="cs-keep"><h2><span class="n">1</span>Basic laws</h2><div class="cs-e cs-rule">' in body
    assert "<b class=\"cs-n\">Ohm's law</b> <span class=\"cs-x\">\\(v = iR\\)</span>" in body      # short: in the line
    assert '<div class="cs-tex">\\[v_2 = \\frac{R_2}{R_1 + R_2}\\, v_s\\]</div>' in body           # a fraction: displayed
    assert '<div class="cs-if"><b>if</b> $R_1$⁠, $R_2$ in series, no load</div>' in body    # (its comma kept with it)
    assert "<span>\\(v_s\\) source voltage (V)</span>" in body
    assert "&lt;img src=x onerror=alert(1)&gt; <strong>bold</strong>" in body and "<img" not in body
    assert '<i class="cs-k">Method</i>' in body and '<i class="cs-k">Unit</i>' in body
    e = {"kind": "formula", "name": "n", "tex": "x", "text": "", "when": "Series RLC circuit", "covers": [],
         "where": [{"sym": "R", "means": "resistance"}, {"sym": "L", "means": "inductance"}]}
    seen = {"R"}
    one = cs.entry_html(e, seen)                       # R was explained above it in its topic
    assert "resistance" not in one and "inductance" in one and seen == {"R", "L"}
    assert "<b>if</b> series RLC circuit" in one and "<b>if</b> RLC" in cs.entry_html({**e, "when": "RLC"})
    page = cs.document_html(body, "T")
    assert page.index("katex.min.js") < page.index("StudiaSheetFit") and "body class=\"cs-page\"" in page


def test_the_estimate_shows_the_cost_up_front(env):  # noqa: F811
    e = cs.estimate(env.lib, env.svc.settings, "c", Scope(kind="sheet", weeks=[1]))
    assert e["requests"] == 3 and e["main_input"] > 3_000 and e["files"] == 1 and e["lectures"] == 1
    from studia.plans import estimate

    assert estimate(env.lib, env.svc.settings, "c", {"kind": "sheet", "weeks": [1]})["requests"] == 3


# ------------------------------------------------------------------ on paper (Edge, as the app prints it)
def printed(tmp_path, sheet, entries, name="s"):
    import pypdfium2 as pdfium

    page = tmp_path / f"{name}.html"
    page.write_text(cs.document_html(cs.document_body(sheet, entries, META), "T"), encoding="utf-8")
    pdf = tmp_path / f"{name}.pdf"
    assert studypack.print_pdf(page, pdf)
    return pdfium.PdfDocument(str(pdf))


def boxes(doc, i, text):
    """Where ``text`` is on page ``i``: (left, bottom, right, top) of its first character, in points."""
    tp = doc[i].get_textpage()
    found = tp.search(text).get_next()
    return tp.get_charbox(found[0]) if found else None


def need_browser():
    from studia.export import find_browser

    if not find_browser():
        pytest.skip("no Edge or Chrome to print with")


@pytest.fixture
def edge():
    """The layout on paper, as the app prints it on Windows: Edge with Segoe UI (another system's fonts are wider)."""
    need_browser()
    if sys.platform != "win32":
        pytest.skip("the layout is checked with Windows' fonts; test_a_sheet_prints_to_a_pdf prints elsewhere")


def test_a_sheet_prints_to_a_pdf(tmp_path):
    need_browser()
    doc = printed(tmp_path, *sample_sheet())
    try:
        assert len(doc) >= 1 and "Basic laws" in doc[0].get_textpage().get_text_range()
    finally:
        doc.close()


def test_the_sample_fits_one_a4_page_in_three_columns(tmp_path, edge):
    doc = printed(tmp_path, *sample_sheet())
    try:
        w, h = doc[0].get_size()
        assert len(doc) == 1 and round(w) == 595 and round(h) == 842                   # A4, one page
        text = doc[0].get_textpage().get_text_range()
        for tp in SAMPLE.TOPICS["topics"]:
            assert tp["title"] in text
        lefts = sorted({round(boxes(doc, 0, t)[0] / (w / 3)) for t in ("Basic laws", "Circuit theorems", "Sinusoids and phasors")})
        assert lefts == [0, 1, 2]                                                      # three columns
        assert "EEE 215" in text and "4 Nov 2026" in text and "1 / 1" in text
    finally:
        doc.close()


def test_a_whole_term_takes_a_few_pages(tmp_path, edge):
    doc = printed(tmp_path, *sample_sheet(4), name="term")                             # ~160 entries
    try:
        assert 3 <= len(doc) <= 5
    finally:
        doc.close()


def test_a_formula_too_wide_for_its_column_is_made_smaller(tmp_path, edge):
    wide = " + ".join(f"\\frac{{a_{{{i}}}}}{{b_{{{i}}}}}" for i in range(1, 16))
    sheet = {"title": "Wide", "topics": [{"id": "t1", "title": "Only", "items": ["x"]}]}
    entries = {"t1": [{"kind": "formula", "name": "Long sum", "tex": wide, "text": "", "where": [], "when": "", "covers": []}]}
    doc = printed(tmp_path, sheet, entries, "wide")
    try:
        w = doc[0].get_size()[0]
        tp = doc[0].get_textpage()
        name = boxes(doc, 0, "Long sum")
        chars = [tp.get_charbox(i) for i in range(tp.count_chars())]
        formula = [c for c in chars if c[3] < name[1] and c[1] > 60 and c[2] > c[0]]   # below its name, above the footer
        assert len(formula) > 40 and max(c[2] for c in formula) < 8 / 25.4 * 72 + (w - 16 / 25.4 * 72) / 3 + 2
        assert "a15" in tp.get_text_range() and "b15" in tp.get_text_range()          # all of it (not cut off)
    finally:
        doc.close()


def test_a_long_symbol_legend_wraps_inside_its_column(tmp_path, edge):
    """A symbol's meaning used to stay on one line and ran over the next column (and off the page in the last one)."""
    long = "inductor voltage, positive where the referenced current enters the terminal of the element (V)"
    sheet = {"title": "S", "topics": [{"id": "t1", "title": "One"}]}
    entries = {"t1": [{"kind": "rule", "name": "Inductor", "tex": "v_L = 0", "text": "",
                       "where": [{"sym": "v_L", "means": long}], "when": "", "covers": []}]}
    doc = printed(tmp_path, sheet, entries, "legend")
    try:
        w = doc[0].get_size()[0]
        tp = doc[0].get_textpage()
        words = [tp.get_charbox(i) for i in range(tp.count_chars())]
        col_right = 8 / 25.4 * 72 + (w - 16 / 25.4 * 72) / 3 + 2
        assert "terminal of the element" in " ".join(tp.get_text_range().split())   # all of it
        name = boxes(doc, 0, "Inductor")
        below = [c for c in words if c[3] < name[1] and c[1] > 60 and c[2] > c[0]]    # below its name, above the footer
        assert len(below) > 40 and max(c[2] for c in below) < col_right               # none of it in the next column
    finally:
        doc.close()


def test_conditions_keep_their_abbreviations():
    """"in lowercase" made the model write "source-free rl circuit" and "dc steady state"."""
    prompt = cs.ENTRIES_RULES if hasattr(cs, "ENTRIES_RULES") else ""
    source = Path(cs.__file__).read_text(encoding="utf-8")
    assert "in lowercase, without" not in source and "keeping names and abbreviations" in source + prompt


@pytest.mark.parametrize("when, shown", [
    ("Thevenin resistance seen by C", "Thevenin resistance seen by C"),
    ("Norton equivalent known", "Norton equivalent known"),
    ("Kirchhoff's laws at each node", "Kirchhoff's laws at each node"),
    ("Bayes' theorem with a partition", "Bayes' theorem with a partition"),
    ("Gauss-Jordan elimination", "Gauss-Jordan elimination"),
    ("Poisson limit", "Poisson limit"),
    ("A is invertible", "A is invertible"),
    ("Ax = b consistent", "Ax = b consistent"),
    ("DC steady state", "DC steady state"),
    ("Series RLC circuit", "series RLC circuit"),
    ("Source-free RL circuit", "source-free RL circuit"),
    ("No load on the output", "no load on the output"),
])
def test_a_condition_keeps_the_name_it_starts_with(when, shown):
    """"if thevenin resistance…", "if bayes' theorem…": only a common first word is written in lowercase after "if"."""
    e = {"kind": "formula", "name": "n", "tex": "x", "text": "", "where": [], "when": when, "covers": []}
    assert f"<b>if</b> {shown}</div>" in cs.entry_html(e)


def test_a_formula_too_wide_even_made_smaller_is_broken_over_lines_not_cut(tmp_path, edge):
    """The three-event addition rule needs about two columns: at the smallest size it was still cut at the column's
    edge ("…+P(A∩B∩"). It is broken over lines after its signs instead, at its own size."""
    add3 = r"P(A\cup B\cup C)=P(A)+P(B)+P(C)-P(A\cap B)-P(A\cap C)-P(B\cap C)+P(A\cap B\cap C)"
    sheet = {"title": "Wide", "topics": [{"id": "t1", "title": "Only", "items": ["x"]}]}
    entries = {"t1": [{"kind": "rule", "name": "Three events", "tex": add3, "text": "", "where": [], "when": "",
                       "covers": []}]}
    doc = printed(tmp_path, sheet, entries, "add3")
    try:
        w = doc[0].get_size()[0]
        tp = doc[0].get_textpage()
        name = boxes(doc, 0, "Three events")
        chars = [tp.get_charbox(i) for i in range(tp.count_chars())]
        formula = [c for c in chars if c[3] < name[1] and c[1] > 60 and c[2] > c[0]]   # below its name, above the footer
        col_right = 8 / 25.4 * 72 + (w - 16 / 25.4 * 72) / 3 + 2
        assert len(formula) > 40 and max(c[2] for c in formula) < col_right             # none of it past the column
        assert "+P(A∩B∩C)" in "".join(tp.get_text_range().split())                     # all of it
        rows = {round(c[1] / 6) for c in formula}
        assert len(rows) >= 3                                                           # on more than one line
        heights = sorted(c[3] - c[1] for c in formula)
        assert heights[len(heights) // 2] > 5                                           # not shrunk to 4.5 pt
    finally:
        doc.close()
