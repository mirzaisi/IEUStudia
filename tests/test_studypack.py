"""Study packs: the pipeline with the AI replaced by fakes (outline, chapters, figures; resume after a failure), the
outline and chapter checks, the Markdown the document is made of, and the SVG sanitiser (what the AI draws goes into
the app's own page)."""

import datetime as dt
import re
import sys
import time
import types
from pathlib import Path
from urllib.parse import unquote

import pytest

from studia import store, studypack
from studia.config import Settings
from studia.coursesite import Resource, Unit
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library
from studia.plans import PlanRunner, Scope
from studia.studypack import clean_outline, clean_section, md_html, parse_figures, sanitize_svg

EVIL_SVG = ('<svg width="300" height="120" onload="alert(1)"><script>alert(2)</script>'
            '<defs><marker id="arr"><path d="M0 0L10 5L0 10z"/></marker></defs>'
            '<line x1="10" y1="60" x2="280" y2="60" stroke="#1f2937" marker-end="url(#arr)"/>'
            '<text x="20" y="40" font-size="14" onclick="steal()">P(A&nbsp;|&nbsp;B)</text>'
            '<foreignObject><div xmlns="http://www.w3.org/1999/xhtml">hi</div></foreignObject>'
            '<image href="https://evil.example/x.png"/><a href="javascript:alert(3)"><text>link</text></a></svg>')


def outline_answer():
    return {"title": "Conditional probability and Bayes", "intro": "Everything from week 1.",
            "concepts": [{"name": "Conditional probability", "chapter": "A"}, {"name": "Bayes' theorem", "chapter": "B"},
                         {"name": "Medical tests", "chapter": "Z"}],
            "chapters": [
                {"id": "A", "title": "Conditional probability", "summary": "P(A|B)", "teaches": ["Conditional probability"],
                 "sources": {"files": [{"ref": "F1", "pages": [1, 1]}], "lectures": [{"ref": "L1", "part": 1, "from": "0:00", "to": "5:00"}]},
                 "figures": [{"id": "x", "kind": "diagram", "brief": "a Venn diagram of A and B", "caption": "A and B"},
                             {"id": "y", "kind": "slide", "ref": "F1", "page": 2, "caption": "The slide"},
                             {"id": "z", "kind": "frame", "ref": "L1", "part": 1, "slide": 9, "caption": "no such frame"}]},
                {"id": "B", "title": "Bayes' theorem", "summary": "", "teaches": ["Bayes' theorem"],
                 "sources": {"files": [{"ref": "F9", "pages": [1, 2]}]}, "figures": []}]}


CHAPTER = ("## Conditional probability\n\nWhy it matters: $P(A\\mid B)=\\frac{P(A\\cap B)}{P(B)}$ with a_b and *stress*.\n\n"
           ":::definition Conditional probability\nThe probability of $A$ once $B$ is known.\n:::\n\n"
           "[[FIGURE c1f9]]\n\n### Worked example\n\n$$P(A\\mid B) = \\frac{0.2}{0.5} = 0.4$$\n\n"
           ":::check\n1. What is $P(A|B)$ if $A\\subset B$?\n   - $P(A)/P(B)$\n:::\n" + "More words. " * 30)


def fake_ai(log, fail_on=None):
    def ask(system, prompt, model="sonnet", cancel=None, **kw):
        log.append(("outline", prompt))
        return outline_answer(), {"usage": {"input_tokens": 1000, "output_tokens": 200}, "model": model}

    def ask_text(system, content, model="sonnet", effort="low", cancel=None, on_text=None):
        prompt = content[0]["text"]
        if prompt.startswith("TASK: Draw the figures"):
            log.append(("figures", prompt))
            text = f"=== FIGURE c1f1 ===\n{EVIL_SVG}\nCaption: Two events.\n=== FIGURE c9f9 ===\n<svg></svg>"
        else:
            n = re.match(r"TASK: Write chapter (\d+)", prompt).group(1)
            log.append((f"chapter {n}", prompt))
            if fail_on and fail_on == n:
                fail_on_hit.append(n)
                if len(fail_on_hit) == 1:
                    raise RuntimeError("usage limit")
            text = CHAPTER if n == "1" else "Bayes flips a conditional: $P(B|A)P(A)/P(B)$. " * 20
        return {"text": text, "usage": {"input_tokens": 500, "output_tokens": 300}, "model": model}

    fail_on_hit: list = []
    return ask, ask_text


@pytest.fixture
def env(tmp_path, monkeypatch):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 P 1", code="MATH 240", name="Probability", term="T")])
    lib.merge_units("c", [Unit("u1", 0, "Hafta 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "Ch. 2",
                               [Resource("r1", "Week01", "Week01.pdf", "pdf", "pdf", "u", "d"),
                                Resource("r2", "Extra", "Extra.pdf", "pdf", "pdf", "u2", "d2")])], first_sync=True)
    lib.set_material_file("r1", str(tmp_path / "Week01.pdf"), 10, "u")
    (tmp_path / "Week01.pdf").write_bytes(b"%PDF")
    lib.set_material_text("r1", ["Conditional probability: P(A|B) = P(A and B)/P(B)", "Bayes theorem: tree diagram"])
    lib.set_course_info("c", syllabus={"outcomes": ["use Bayes' theorem"],
                                       "weeks": [{"no": 1, "subject": "Conditional probability", "preparation": ""}]})
    rec = Recording(delivery_id="r", course_id="c", folder_id="f", folder_path="", name="n",
                    start=dt.datetime(2026, 9, 23, 9, 0), duration_s=3000, viewer_url="v")
    lib.merge_recordings("c", [rec], first_sync=True)
    tr = Transcript(delivery_id="r", segments=[Segment(0, 5, "Today: conditional probability, given that B happened.", "en", 0.9)],
                    duration_s=3000, speech_s=5, language_seconds={"en": 5}, model="m", device="cpu", engine_version="t",
                    created_at="2026-09-23T12:00:00")
    lib.save_transcript(tr, store.save_transcript(tr))
    lib.set_status("r", "done")
    page = tmp_path / "page.jpg"
    page.write_bytes(b"\xff\xd8\xff\xe0jpeg")
    rendered = []
    monkeypatch.setattr("studia.materials.page_image", lambda path, n, width=0: rendered.append((path.name, n)) or page)
    printed = []
    monkeypatch.setattr(studypack, "print_pdf", lambda h, p: printed.append(h) or False)
    svc = types.SimpleNamespace(lib=lib, settings=Settings(ai_model="sonnet"), emit=lambda e: None,
                                output_root=tmp_path / "out")
    return types.SimpleNamespace(lib=lib, svc=svc, tmp=tmp_path, rendered=rendered, printed=printed)


def wait_done(lib, pid, timeout=10):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        p = lib.plan_brief(pid)
        if p["status"] in ("done", "error", "paused"):
            return p
        time.sleep(0.02)
    raise AssertionError("pack did not finish")


def test_gather_takes_only_the_picked_material(env):
    facts = studypack.gather(env.lib, env.svc.settings, "c", Scope(kind="pack", weeks=[1], files=["r1"]))
    assert [f["ref"] for f in facts["files"]] == ["F1"] and facts["files"][0]["pdf"]
    assert "[F1 p.1] Conditional probability" in facts["pack"] and "=== LECTURE L1 (week 1" in facts["pack"]
    assert facts["lectures"][0]["parts"][0]["part"] == 1 and not facts["materials_missing"]   # Extra wasn't picked
    everything = studypack.gather(env.lib, env.svc.settings, "c", Scope(kind="pack", weeks=[1]))
    assert [m["title"] for m in everything["materials_missing"]] == ["Extra"]
    none = studypack.gather(env.lib, env.svc.settings, "c", Scope(kind="pack", weeks=[1], files=["r1"], days=["x"]))
    assert not none["lectures"]


def test_unticking_every_file_or_lecture_in_the_chooser_leaves_them_out(env):
    day = studypack.gather(env.lib, env.svc.settings, "c", Scope(kind="pack", weeks=[1]))["lectures"][0]["day_key"]
    lectures_only = studypack.gather(env.lib, env.svc.settings, "c",
                                     Scope.from_json({"kind": "pack", "weeks": [1], "files": [], "days": [day], "picked": True}))
    assert not lectures_only["files"] and not lectures_only["materials_missing"] and len(lectures_only["lectures"]) == 1
    files_only = studypack.gather(env.lib, env.svc.settings, "c",
                                  Scope.from_json({"kind": "pack", "weeks": [1], "files": ["r1"], "days": [], "picked": True}))
    assert [f["ref"] for f in files_only["files"]] == ["F1"] and not files_only["lectures"] and not files_only["days_missing"]


def test_study_pack_pipeline_end_to_end(env):
    log = []
    ask, ask_text = fake_ai(log)
    runner = PlanRunner(env.svc, ask=ask, ask_text=ask_text)
    pid = runner.create("c", {"kind": "pack", "weeks": [1], "files": ["r1"], "difficulties": "Bayes"})
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "done"
    plan = env.lib.plan(pid)
    r, data = plan["result"], plan["data"]
    assert r["kind"] == "pack" and r["title"] == plan["title"] == "Conditional probability and Bayes"
    assert [c["title"] for c in r["chapters"]] == ["Conditional probability", "Bayes' theorem"]
    assert r["uncovered"] == ["Medical tests"] and r["figures"] == 2       # the diagram and the slide page
    assert [k for k, _ in log] == ["outline", "chapter 1", "chapter 2", "figures"] or \
        [k for k, _ in log] == ["outline", "chapter 2", "chapter 1", "figures"]
    chapter1 = dict(log)["chapter 1"]
    assert "[F1 p.1] Conditional probability" in chapter1 and "[[FIGURE c1f1]]" in chapter1
    assert "finds these hard; explain them with extra care: Bayes" in chapter1
    assert "Today: conditional probability" in chapter1                    # the lecture passage it names
    svg = data["figures"]["c1f1"]["svg"]
    assert "script" not in svg and "onload" not in svg and "onclick" not in svg and "evil" not in svg
    assert "foreignObject" not in svg and "javascript" not in svg and 'url(#c1f1-arr)' in svg
    assert data["figures"]["c1f1"]["caption"] == "A and B"                 # the outline's caption wins
    assert set(data["figures"]) == {"c1f1"}                                # c9f9 was never asked for
    assert env.rendered == [("Week01.pdf", 2)]
    html_file = env.tmp / "out" / "MATH 240" / "Study packs"
    page = next(html_file.glob("*.html")).read_text(encoding="utf-8")
    assert "<h1>Conditional probability and Bayes</h1>" in page and 'id="c2"' in page and 'src="katex/katex.min.js"' in page and "file:" not in page
    assert '<img src="' in page and "%20%28images%29/c1f2.jpg" in page and "Figure 1.2." in page
    assert "<script>alert" not in page and env.printed                      # printed (the fake makes no PDF)
    body = studypack.shown_body(env.svc, plan)
    assert "file:///" in body and "Figure 1.1." in body and "<h1>" in body
    assert plan["usage"]["sections"]["input_tokens"] == 1000


def test_two_packs_with_the_same_title_on_the_same_day_keep_their_own_files(env):
    runner = PlanRunner(env.svc, ask=fake_ai([])[0], ask_text=fake_ai([])[1])
    ids = []
    for _ in range(2):
        ids.append(runner.create("c", {"kind": "pack", "weeks": [1], "files": ["r1"]}))
        runner.start(ids[-1])
        assert wait_done(env.lib, ids[-1])["status"] == "done"
    first, second = (env.lib.plan(p) for p in ids)
    assert first["result"]["file"] != second["result"]["file"] and second["result"]["file"].endswith("Bayes (2).html")
    assert second["data"]["stem"].endswith(" (2)") and not first["data"]["stem"].endswith(")")
    folder = env.tmp / "out" / "MATH 240" / "Study packs"
    assert {p.name for p in folder.iterdir() if p.is_dir()} >= {f"{first['data']['stem']} (images)", f"{second['data']['stem']} (images)"}
    assert f"{second['data']['stem']} (images)" in unquote(studypack.shown_body(env.svc, second))
    runner.start(ids[0])                                       # made again (a resume): the same files, not a third name
    assert wait_done(env.lib, ids[0])["status"] == "done" and env.lib.plan(ids[0])["result"]["file"] == first["result"]["file"]


def test_image_links_of_a_title_with_a_hash_still_find_their_images(env):
    ask, ask_text = fake_ai([])
    runner = PlanRunner(env.svc, ask=lambda *a, **kw: ({**ask(*a, **kw)[0], "title": "C# 100% classes"}, ask(*a, **kw)[1]),
                        ask_text=ask_text)
    pid = runner.create("c", {"kind": "pack", "weeks": [1], "files": ["r1"]})
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "done"
    html_path = Path(env.lib.plan(pid)["result"]["file"])
    srcs = re.findall(r'<img src="([^"]+)"', html_path.read_text(encoding="utf-8"))
    assert srcs and all("#" not in s and (html_path.parent / unquote(s)).is_file() for s in srcs)


def test_a_stopped_pack_resumes_where_it_stopped(env):
    log = []
    ask, ask_text = fake_ai(log, fail_on="2")
    runner = PlanRunner(env.svc, ask=ask, ask_text=ask_text)
    pid = runner.create("c", {"kind": "pack", "weeks": [1]})
    runner.start(pid)
    p = wait_done(env.lib, pid)
    assert p["status"] == "error" and "usage limit" in p["error"]
    data = env.lib.plan(pid)["data"]
    assert "outline" in data and set(data["sections"]) <= {"c1"}
    log.clear()
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "done"
    asked = [k for k, _ in log]
    assert "outline" not in asked and asked.count("chapter 2") == 1 and asked.count("chapter 1") == (0 if data["sections"] else 1)


def test_the_outline_is_checked_against_the_material():
    facts = {"files": [{"ref": "F1", "pages": 5, "pdf": True}, {"ref": "F2", "pages": 3, "pdf": False}],
             "lectures": [{"ref": "L1", "parts": [{"part": 1, "slides": 4}]}], "span": "week 1"}
    raw = {"title": "", "chapters": [
        {"id": "x", "title": "", "teaches": ["dropped: no title"]},
        {"id": "b", "title": "Two", "teaches": ["t"], "sources": {"files": [{"ref": "F1", "pages": [4, 99]}, {"ref": "F7"}],
                                                                  "lectures": [{"ref": "L1", "part": 3, "from": "1:00", "to": "0:30"}]},
         "figures": [{"kind": "slide", "ref": "F2", "page": 1}, {"kind": "slide", "ref": "F1", "page": 6},
                     {"kind": "slide", "ref": "F1", "page": 5}, {"kind": "frame", "ref": "L1", "part": 1, "slide": 5},
                     {"kind": "frame", "ref": "L1", "part": 1, "slide": 4}, {"kind": "diagram", "brief": ""},
                     {"kind": "diagram", "brief": "axes"}, {"kind": "diagram", "brief": "one too many"}]},
        {"id": "a", "title": "Three"}],
        "concepts": [{"name": "t", "chapter": "b"}, {"name": "u", "chapter": "a"}, {"name": "lost", "chapter": "q"}]}
    o = clean_outline(raw, facts)
    assert o["title"] == "Week 1" and [c["id"] for c in o["chapters"]] == ["c1", "c2"]
    c = o["chapters"][0]
    assert c["sources"]["files"] == [{"ref": "F1", "pages": [4, 5]}]
    assert c["sources"]["lectures"] == [{"ref": "L1", "part": 0, "from": 60, "to": None}]
    assert [(g["id"], g["kind"]) for g in c["figures"]] == [("c1f1", "slide"), ("c1f2", "frame"), ("c1f3", "diagram")]
    assert [k["chapter"] for k in o["concepts"]] == ["c1", "c2", ""] and o["uncovered"] == ["lost"]
    with pytest.raises(RuntimeError):
        clean_outline({"chapters": [{"title": ""}]}, facts)


def test_a_chapter_is_cleaned():
    ch = {"title": "Conditional probability", "figures": [{"id": "c1f1"}, {"id": "c1f2"}]}
    md = clean_section("```markdown\n## Conditional probability\n## Intro\nText [[FIGURE c1f1]] [[FIGURE c1f1]]\n"
                       "[[FIGURE zz]]\n\n:::check\n1. Q?\n:::\n```", ch)
    assert md.startswith("### Intro") and md.count("[[FIGURE c1f1]]") == 1 and "zz" not in md
    assert md.index("[[FIGURE c1f2]]") < md.index(":::check")


def test_the_markdown_of_a_chapter():
    figs = []
    out = md_html(CHAPTER.replace("c1f9", "c1f1") + "\n\n| a | b |\n|---|---|\n| <x> | $a_1$ |\n\n```\n<script>$x$\n```\n"
                  "\n- one\n  - nested *two*\n- three\n\n1. first\n2. second\n\n> quoted **bold**\n",
                  lambda fid: figs.append(fid) or "<figure>F</figure>")
    assert figs == ["c1f1"] and "<figure>F</figure>" in out
    assert "$P(A\\mid B)=\\frac{P(A\\cap B)}{P(B)}$" in out and "a_b" in out and "<em>stress</em>" in out
    assert '<div class="sp-box sp-definition"><div class="sp-box-h"><b>Definition</b> · Conditional probability</div>' in out
    assert '<div class="sp-math">$$P(A\\mid B) = \\frac{0.2}{0.5} = 0.4$$</div>' in out
    assert "<h3>Conditional probability</h3>" in out and "<h3>Worked example</h3>" in out
    assert "<td>&lt;x&gt;</td><td>$a_1$</td>" in out and "<pre><code>&lt;script&gt;$x$</code></pre>" in out
    assert "<ul><li>one<ul><li>nested <em>two</em></li></ul></li><li>three</li></ul>" in out
    assert "<ol><li>first</li><li>second</li></ol>" in out and "<blockquote><p>quoted <strong>bold</strong></p></blockquote>" in out
    assert "<script" not in out and "Check yourself" in out


def test_figures_are_read_from_the_answer():
    text = ("=== FIGURE c1f1 ===\nHere:\n<svg viewBox='0 0 10 10'><rect width='5' height='5'/></svg>\nCaption: A box.\n"
            "=== FIGURE c1f2 ===\nno svg here\n=== FIGURE c2f1 ===\n<svg><circle r='1'/></svg>")
    got = parse_figures(text, {"c1f1", "c1f2"})
    assert list(got) == ["c1f1"] and got["c1f1"]["caption"] == "A box."


# ------------------------------------------------------------------ the SVG sanitiser
def test_svg_keeps_shapes_text_and_markers():
    out = sanitize_svg(EVIL_SVG, prefix="f1-")
    assert out.startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 120"')
    assert '<marker id="f1-arr">' in out and 'marker-end="url(#f1-arr)"' in out
    assert "P(A | B)" in out and "<text>link</text>" in out           # a link's text stays, the link not
    for bad in ("script", "alert", "onload", "onclick", "foreignObject", "image", "evil", "<a ", "javascript", "<div"):
        assert bad not in out, bad


@pytest.mark.parametrize("svg", [
    '<!DOCTYPE svg [<!ENTITY x "boom">]><svg><text>&x;</text></svg>',
    '<svg><rect width="1" height="1"',                      # not well formed
    '<html><body>no</body></html>',
    '<svg><script>alert(1)</script></svg>',                  # nothing drawn
])
def test_svg_that_is_refused(svg):
    assert sanitize_svg(svg) is None


def test_svg_attributes_and_styles_are_filtered():
    out = sanitize_svg('<svg viewBox="0 0 10 10" xmlns:xlink="http://www.w3.org/1999/xlink" class="modal" style="position:fixed">'
                       '<rect width="5" height="5" fill="url(https://x.example/a)" stroke="url(#g)" '
                       'style="fill: url(http://x); stroke: #123; behavior: url(x.htc); font-size: 14px"/>'
                       '<use xlink:href="#r"/><use href="http://x.example/s.svg#a"/>'
                       '<text x="1" y="2" font-family="\'Segoe UI\', Arial">a&lt;b</text></svg>', prefix="p-")
    assert 'fill="url' not in out and 'stroke="url(#p-g)"' in out and 'style="stroke: #123; font-size: 14px"' in out
    assert '<use href="#p-r"></use><use></use>' in out and "x.example" not in out
    assert "class=\"modal\"" not in out and "position" not in out and "a&lt;b" in out
    assert 'font-family="&#x27;Segoe UI&#x27;, Arial"' in out
    assert sanitize_svg('<svg xlink:href="#a"><path d="M0 0"/></svg>')       # an undeclared xlink is declared


def test_the_estimate_warns_with_the_size(env):
    e = studypack.estimate(env.lib, env.svc.settings, "c", Scope(kind="pack", weeks=[1]))
    assert e["chapters"] == 3 and e["main_input"] > 10_000 and e["requests"] == 7 and e["files"] == 1
    assert e["lectures"] == 1 and "minutes" in e["minutes"]


def test_a_pack_and_a_sheet_use_the_level_the_student_chose(env, monkeypatch):
    """The dialog's reasoning level reaches every request of a study pack and of a cheatsheet, and their estimates;
    without one the plans level of Settings → AI is used."""
    from studia import ai, cheatsheet

    monkeypatch.setattr(ai, "provider", lambda: "claude")
    env.svc.settings.ai_effort = {"claude.plan": "medium"}
    assert studypack._level(Scope(kind="pack"), env.svc.settings) == "medium"
    assert studypack._level(Scope(kind="pack", effort="high"), env.svc.settings) == "high"
    for kind, mod in (("pack", studypack), ("sheet", cheatsheet)):
        assert mod.estimate(env.lib, env.svc.settings, "c", Scope(kind=kind, weeks=[1], effort="xhigh"))["effort"] == "xhigh"
        assert mod.estimate(env.lib, env.svc.settings, "c", Scope(kind=kind, weeks=[1]))["effort"] == "medium"

    sent = []
    ask, ask_text = fake_ai([])

    def ask_seen(system, prompt, model="sonnet", cancel=None, **kw):
        sent.append(kw.get("effort"))
        return ask(system, prompt, model=model, cancel=cancel)

    def text_seen(system, content, model="sonnet", effort="low", cancel=None, on_text=None):
        sent.append(effort)
        return ask_text(system, content, model, effort, cancel, on_text)

    runner = PlanRunner(env.svc, ask=ask_seen, ask_text=text_seen)
    pid = runner.create("c", {"kind": "pack", "weeks": [1], "files": ["r1"], "effort": "high"})
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "done"
    assert len(sent) == 4 and set(sent) == {"high"}            # the outline, two chapters, the figures


@pytest.mark.skipif(sys.platform != "win32", reason="the pages are counted with Windows' fonts (Segoe UI)")
def test_contents_get_page_numbers_from_the_printed_pdf(tmp_path):
    from studia.export import find_browser

    if not find_browser():
        pytest.skip("no Edge or Chrome to print with")
    outline = {"title": "T", "intro": "", "chapters": [{"id": "c1", "title": "Alpha", "figures": []},
                                                       {"id": "c2", "title": "Beta and gamma", "figures": []}]}
    meta = {"course": "X 1", "span": "Week 1", "files": [], "lectures": [], "date": "4 Nov 2026"}
    sections = {"c1": "Text. " * 900, "c2": "More."}
    page = tmp_path / "p.html"
    page.write_text(studypack.document_html(studypack.document_body(outline, sections, {}, meta, str), "T"), encoding="utf-8")
    pdf = tmp_path / "p.pdf"
    assert studypack.print_pdf(page, pdf)
    pages = studypack.chapter_pages(pdf, ["Alpha", "Beta and gamma"])
    assert pages[0] == 3 and pages[1] > 3                    # cover, contents, then the chapters


def test_the_pages_keep_the_documents_english_casing_and_name_a_class_day_once():
    web = Path(studypack.__file__).parent / "web"
    pack, sheet = ((web / f).read_text(encoding="utf-8") for f in ("studypack.js", "cheatsheet.js"))
    assert '<article class="sp-doc" id="pk-doc" lang="en">' in pack       # "Definition" not uppercased as Turkish
    assert '<article class="cs-doc" id="cs-doc" lang="en">' in sheet
    assert 't("lecture")' not in pack and 'd.ready ? t("transcribed")' in pack   # "Lecture · transcribed"
