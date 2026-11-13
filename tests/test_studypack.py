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


def test_a_weeks_lectures_come_in_the_order_they_were_taught(env):
    """The course page lists a week's class days newest first; packs, cheatsheets and review sheets read them in
    the order they were taught (L1 Wednesday, L2 Friday), so the cover's dates and the AI's material follow it."""
    rec = Recording(delivery_id="fri", course_id="c", folder_id="f", folder_path="", name="n",
                    start=dt.datetime(2026, 9, 25, 9, 0), duration_s=3000, viewer_url="v")
    env.lib.merge_recordings("c", [rec], first_sync=False)
    tr = Transcript(delivery_id="fri", segments=[Segment(0, 5, "Friday: the total probability rule.", "en", 0.9)],
                    duration_s=3000, speech_s=5, language_seconds={"en": 5}, model="m", device="cpu", engine_version="t",
                    created_at="2026-09-25T12:00:00")
    env.lib.save_transcript(tr, store.save_transcript(tr))
    env.lib.set_status("fri", "done")
    facts = studypack.gather(env.lib, env.svc.settings, "c", Scope(kind="pack", weeks=[1]))
    assert [(x["ref"], x["date"]) for x in facts["lectures"]] == [("L1", "2026-09-23"), ("L2", "2026-09-25")]
    assert facts["pack"].index("Today: conditional probability") < facts["pack"].index("Friday: the total")
    assert "Lectures: 23 Sep, 25 Sep" in studypack.document_body(
        {"title": "T", "intro": "", "chapters": []}, {}, {}, studypack.meta_of(facts), str)
    from studia import plans

    review = plans.gather(env.lib, env.svc.settings, "c", Scope(kind="prep", weeks=[1]))
    assert [x["date"] for x in review["days_used"]] == ["2026-09-23", "2026-09-25"]


def test_unticking_every_file_or_lecture_in_the_chooser_leaves_them_out(env):
    day = studypack.gather(env.lib, env.svc.settings, "c", Scope(kind="pack", weeks=[1]))["lectures"][0]["day_key"]
    lectures_only = studypack.gather(env.lib, env.svc.settings, "c",
                                     Scope.from_json({"kind": "pack", "weeks": [1], "files": [], "days": [day], "picked": True}))
    assert not lectures_only["files"] and not lectures_only["materials_missing"] and len(lectures_only["lectures"]) == 1
    files_only = studypack.gather(env.lib, env.svc.settings, "c",
                                  Scope.from_json({"kind": "pack", "weeks": [1], "files": ["r1"], "days": [], "picked": True}))
    assert [f["ref"] for f in files_only["files"]] == ["F1"] and not files_only["lectures"] and not files_only["days_missing"]


def test_the_outline_sees_every_page_of_weeks_of_slides_and_says_which_files_it_could_not(env):
    """A midterm's weeks of dense slides (200k characters) all go to the outline and the topic list: a topic only on
    a late page is in no chapter otherwise. More than one request takes is cut, and the estimate and the result name
    the files and the pages seen."""
    page = "Cramer's rule and the cofactor matrix adjugate " * 100           # ~4,700 characters
    env.lib.set_material_text("r1", [f"page {n} {page}" for n in range(1, 43)])
    scope = Scope(kind="pack", weeks=[1], files=["r1"])
    facts = studypack.gather(env.lib, env.svc.settings, "c", scope)
    assert "[F1 p.42] page 42" in facts["pack"] and not facts["files_cut"] and "F1" not in facts["cut"]
    assert "=== LECTURE L1" in facts["pack"]                    # and the lecture next to them
    env.lib.set_material_text("r1", [f"page {n} {page}" for n in range(1, 101)])     # a whole term: ~470k
    facts = studypack.gather(env.lib, env.svc.settings, "c", scope)
    [cut] = facts["files_cut"]
    assert cut["ref"] == "F1" and cut["pages"] == 100 and 50 < cut["seen"] < 100 and "F1" in facts["cut"]
    assert f"[F1 p.{cut['seen']}] " in facts["pack"] and f"[F1 p.{cut['seen'] + 1}] " not in facts["pack"]
    assert studypack.estimate(env.lib, env.svc.settings, "c", scope)["files_cut"] == [cut]
    assert "=== LECTURE L1" in facts["pack"]


def test_a_whole_two_hour_lecture_goes_to_the_outline_when_it_fits(env):
    """A one-lecture cheatsheet or pack: the topic list was made from 12,000 characters of it (a video plan's share
    of a day), about a fifth of a two-hour lecture, though the outline has room for all of it."""
    words = "the inductor current cannot change instantly so i of zero plus equals i of zero minus "
    segs = [Segment(i * 10, i * 10 + 9, f"{words}{i}", "en", 0.9) for i in range(700)]     # ~60k characters
    tr = Transcript(delivery_id="r", segments=segs, duration_s=7000, speech_s=6300, language_seconds={"en": 6300},
                    model="m", device="cpu", engine_version="t", created_at="2026-09-23T12:00:00")
    env.lib.save_transcript(tr, store.save_transcript(tr))
    facts = studypack.gather(env.lib, env.svc.settings, "c", Scope(kind="pack", weeks=[1], files=["r1"]))
    assert facts["full_chars"] > 50_000 and not facts["cut"] and not facts["left_out"]
    assert f"{words}699" in facts["pack"] and "(Excerpts:" not in facts["pack"]


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
    assert set(env.rendered) == {("Week01.pdf", 2)}          # the figure, and the page its chapter's writer sees
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


def test_an_old_packs_pdf_is_laid_out_again_with_its_pictures_when_it_is_opened(env, monkeypatch):
    """A pack made before 4.0 printed "1, 1, 1" in its check boxes and cut wide formulas: Open PDF gave the stored
    PDF. It is laid out again from its saved chapters (no AI), its slide pictures as they are beside it."""
    log = []
    ask, ask_text = fake_ai(log)
    runner = PlanRunner(env.svc, ask=ask, ask_text=ask_text)
    pid = runner.create("c", {"kind": "pack", "weeks": [1], "files": ["r1"]})
    runner.start(pid)
    assert wait_done(env.lib, pid)["status"] == "done"
    printed = []

    def printer(html_path, pdf_path):
        printed.append(pdf_path.name)
        pdf_path.write_bytes(b"%PDF new")
        return True

    monkeypatch.setattr(studypack, "print_pdf", printer)
    monkeypatch.setattr("studia.materials.page_image", lambda *a, **k: None)     # the course file has moved since
    r = env.lib.plan(pid)["result"]
    html_path = Path(r["file"])
    pdf_path = html_path.with_suffix(".pdf")
    pdf_path.write_bytes(b"%PDF old")
    old = {k: v for k, v in r.items() if k not in ("layout", "made")}
    env.lib.update_plan(pid, result={**old, "pdf": str(pdf_path)})
    with env.lib._lock:
        env.lib.db.execute("UPDATE plans SET created_at='2026-11-02T10:00:00' WHERE id=?", (pid,))
        env.lib.db.commit()
    log.clear()
    assert studypack.make_pdf(env.svc, env.lib.plan(pid)) == pdf_path and pdf_path.read_bytes() == b"%PDF new"
    assert log == [] and printed and all(p == pdf_path.name + ".part" for p in printed)
    page = html_path.read_text(encoding="utf-8")
    assert "made with IEUStudia on 2 Nov 2026" in page and "Figure 1.2." in page and "c1f2.jpg" in page
    again = env.lib.plan(pid)["result"]
    assert again["layout"] == studypack.layout() and again["made"] == "2026-11-02" and again["figures"] == 2
    assert "made with IEUStudia on 2 Nov 2026" in studypack.shown_body(env.svc, env.lib.plan(pid))


def test_a_pack_without_its_saved_chapters_keeps_its_files(env, monkeypatch):
    """Nothing to lay it out from: the PDF it has is given as it is."""
    monkeypatch.setattr(studypack, "print_pdf", lambda h, p: p.write_bytes(b"%PDF new") or True)
    pid = "old"
    env.lib.create_plan(pid, "c", {"kind": "pack"}, "Old pack")
    pdf = env.tmp / "Old pack.pdf"
    pdf.write_bytes(b"%PDF old")
    env.lib.update_plan(pid, status="done", data={}, result={"kind": "pack", "file": "", "pdf": str(pdf)})
    assert studypack.make_pdf(env.svc, env.lib.plan(pid)) == pdf and pdf.read_bytes() == b"%PDF old"


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


def test_a_numbered_list_keeps_its_numbers_across_answers_formulas_and_text():
    """A "Check yourself" box of questions, each with its answer after a blank line, read 1, 1, 1."""
    box = md_html(":::check\n1. Is $R$ enough?\n\n   **Answer.** No.\n\n2. What is $\\omega_d$?\n\n   **Answer.** 980 rad/s."
                  "\n\n3. Which root?\n\n   **Answer.** The one closest to zero.\n:::")
    assert box.count("<ol") == 1 and box.count("<li>") == 3
    assert "<li><p>Is $R$ enough?</p><p><strong>Answer.</strong> No.</p></li>" in box   # the answer under its question
    one_line = md_html("1. Q one? A: yes.\n\n2. Q two? A: no.\n\n3. Q three? A: maybe.")
    assert one_line == "<ol><li>Q one? A: yes.</li><li>Q two? A: no.</li><li>Q three? A: maybe.</li></ol>"
    steps = md_html("1. First step.\n\n   $$x = 1$$\n\n2. Second step.\n\n$$y = 2$$\n\n3. Third step.")
    assert steps.count("<ol") == 2 and '<ol start="3"><li>Third step.</li></ol>' in steps
    assert '<li><p>First step.</p><div class="sp-math">$$x = 1$$</div></li>' in steps
    split = md_html("1. a\n2. b\n\nA paragraph between them.\n\n3. c\n4. d")
    assert '<ol><li>a</li><li>b</li></ol><p>A paragraph between them.</p><ol start="3"><li>c</li><li>d</li></ol>' == split
    assert md_html("- a\n\n- b") == "<ul><li>a</li><li>b</li></ul>"


def test_a_formula_over_lines_in_a_quote_or_an_alert_loses_the_quote_marks():
    """"> " of each line of a $$ … $$ in a quote stayed in the formula: stray ">" signs, and "=" read as ">="."""
    alert = md_html("> [!CAUTION]\n> The slides write\n> $$\n> W = CV^2\n> $$\n> without the half.")
    assert '<div class="sp-math">$$\nW = CV^2\n$$</div>' in alert and "&gt;" not in alert
    quote = md_html("> **Solution.** It is\n>\n> $$\n> W_C=\\frac{1}{2}CV^2\n> =18\\ \\mu\\mathrm{J}.\n> $$")
    assert "$$\nW_C=\\frac{1}{2}CV^2\n=18\\ \\mu\\mathrm{J}.\n$$" in quote and "&gt;" not in quote
    nested = md_html("> > $$\n> > a\n> > > 0\n> > $$")
    assert "$$\na\n&gt; 0\n$$" in nested                     # a relation in it stays
    plain = md_html("$$\nf(x) = x^2 + 1\n> 0\n$$")
    assert '<div class="sp-math">$$\nf(x) = x^2 + 1\n&gt; 0\n$$</div>' == plain   # not in a quote: as written


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


@pytest.mark.skipif(sys.platform != "win32", reason="the layout is checked with Windows' fonts (Segoe UI)")
def test_a_formula_or_code_line_wider_than_the_page_goes_on_the_next_line_on_paper(tmp_path):
    """On paper a worked example's long chain lost its end ("…(0.125×10" without "^-6) = 10^3 rad/s"), a long code
    line was cut, and a scrollbar was printed under each."""
    import pypdfium2 as pdfium

    from studia.export import find_browser

    if not find_browser():
        pytest.skip("no Edge or Chrome to print with")
    chain = (r"$$\alpha=\frac{R}{2L}=\frac{8}{2(0.02)}=200\ \mathrm{rad/s},\qquad \omega_0=\frac{1}{\sqrt{LC}}"
             r"=\frac{1}{\sqrt{(0.02)(8)(0.125\times10^{-6})}}=10^3\ \mathrm{rad/s},\qquad \frac{dv}{dt}(0^+)"
             r"=\frac{i_C(0^+)}{C}=98\,000\ \mathrm{V/s}$$")
    code = "```\nresult = solve_linear_system(coefficient_matrix_of_the_mesh_equations, source_vector_of_the_mesh_equations)\n```"
    outline = {"title": "T", "intro": "", "chapters": [{"id": "c1", "title": "Alpha", "figures": []}]}
    meta = {"course": "X 1", "span": "Week 1", "files": [], "lectures": [], "date": "4 Nov 2026"}
    page = tmp_path / "p.html"
    page.write_text(studypack.document_html(studypack.document_body(
        outline, {"c1": f"Text.\n\n{chain}\n\n{code}\n"}, {}, meta, str), "T"), encoding="utf-8")
    pdf = tmp_path / "p.pdf"
    assert studypack.print_pdf(page, pdf)
    doc = pdfium.PdfDocument(str(pdf))
    try:
        w = doc[2].get_size()[0]
        tp = doc[2].get_textpage()
        text = "".join(tp.get_text_range().split())
        assert "98000V/s" in text and "source_vector_of_the_mesh_equations)" in text      # all of it
        right = max(tp.get_charbox(i)[2] for i in range(tp.count_chars()))
        assert right < w - 16 / 25.4 * 72 + 2                                              # within the right margin
    finally:
        doc.close()


TURKISH = "Şu ağaçtaki ığdır, çünkü İstanbul'da ölçü üç. ÇĞİÖŞÜ çğıöşü. Sınav için bu tanım doğru değil."
ASCII_TR = ("icin", "cunku", "Sinav", "tanim", "dogru", "degil", "Istanbul", "agac", "olcu")


def _printed(tmp_path, page_html: str) -> Path:
    """``page_html`` printed as the app prints it (print_pdf: Edge or Chrome headless); skipped without either."""
    from studia.export import find_browser

    if not find_browser():
        pytest.skip("no Edge or Chrome to print with")
    page, pdf = tmp_path / "p.html", tmp_path / "p.pdf"
    page.write_text(page_html, encoding="utf-8")
    assert studypack.print_pdf(page, pdf)
    return pdf


def _fonts(pdf: Path) -> dict[str, bool]:
    """The fonts the PDF's text is drawn with, and whether each is embedded: {name: embedded}. A font left out is
    looked up on the reader's computer, where it may be another one or lack the Turkish letters. (Some fonts have no
    name in the file: what matters is the flag.)"""
    import ctypes

    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    out: dict[str, bool] = {}
    doc = pdfium.PdfDocument(str(pdf))
    try:
        for page in doc:
            for obj in page.get_objects(max_depth=5):
                if obj.type != pdfium_c.FPDF_PAGEOBJ_TEXT:
                    continue
                font = pdfium_c.FPDFTextObj_GetFont(obj.raw)
                size = pdfium_c.FPDFFont_GetBaseFontName(font, None, 0)
                name = ctypes.create_string_buffer(size)
                pdfium_c.FPDFFont_GetBaseFontName(font, name, size)
                out[name.value.decode("utf-8", "replace")] = bool(pdfium_c.FPDFFont_GetIsEmbedded(font))
    finally:
        doc.close()
    return out


def _text(pdf: Path) -> str:
    """The PDF's text, all pages, as a reader copying it out gets it (runs of white space made one space)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        return " ".join("".join(page.get_textpage().get_text_range() for page in doc).split())
    finally:
        doc.close()


def _check_turkish_on_paper(pdf: Path, *upper: str) -> str:
    """Every font embedded, the Turkish sentence as written, no word fallen back to ASCII ("icin" for "için"), and
    each of ``upper`` (words the page's CSS upper-cases) with a dotted İ where Turkish has one."""
    fonts = _fonts(pdf)
    assert fonts and all(fonts.values()), fonts
    text = _text(pdf)
    assert TURKISH in text
    assert not [w for w in ASCII_TR if re.search(rf"\b{w}\b", text)]
    for u in upper:
        assert u in text, f"{u!r} not in {text[:300]!r}"
    return text


def test_a_printed_study_pack_embeds_its_fonts_and_keeps_turkish_letters(tmp_path):
    """A Turkish pack's cover said "BILGISAYAR MÜHENDISLIĞINE GIRIŞ" on paper: the page was lang="en" and the line
    is upper-cased by its CSS, so "i" became an undotted "I". Its box labels ("Kendini sına") are upper-cased too."""
    outline = {"title": "Bilgisayar sistemlerine giriş", "intro": "",
               "chapters": [{"id": "c1", "title": "Çığır açan işlemciler", "figures": []}]}
    meta = {"course": "Bilgisayar Mühendisliğine Giriş", "span": "1. hafta", "files": [], "lectures": [],
            "date": "4 Kas 2026", "language": "Turkish"}
    section = f"{TURKISH}\n\n$$x^2 + y^2 = r^2$$\n\n:::check\nİşlemci saat hızı neden tek başına yetmez?\n:::\n"
    body = studypack.document_body(outline, {"c1": section}, {}, meta, str)
    pdf = _printed(tmp_path, studypack.document_html(body, outline["title"], None, meta["language"]))
    text = _check_turkish_on_paper(pdf, "BİLGİSAYAR MÜHENDİSLİĞİNE GİRİŞ", "KENDİNİ SINA")
    assert "Çığır açan işlemciler" in text and "İçindekiler" in text


def test_a_printed_cheatsheet_embeds_its_fonts_and_keeps_turkish_letters(tmp_path):
    """A Turkish sheet: its topics and notes as written, and its entries' labels upper-cased in Turkish ("BİRİM",
    not "BIRIM")."""
    import cheatsheet_sample as SAMPLE

    from studia import cheatsheet

    note = {"kind": "rule", "name": "Türkçe not", "tex": "", "text": TURKISH, "where": [], "when": "",
            "covers": ["not"]}
    sheet = {"title": "Devre analizi", "topics": [{"id": "t1", "title": "Temel yasalar ve ölçü birimleri",
                                                    "items": ["Ohm yasası", "not"]}]}
    entries = {"t1": [*SAMPLE.ENTRIES["t1"], note]}
    meta = {"course": "EEE 215 · Devre Analizi", "span": "4–6. haftalar", "files": [], "lectures": [],
            "date": "4 Kas 2026", "language": "Turkish"}
    page = cheatsheet.document_html(cheatsheet.document_body(sheet, entries, meta), sheet["title"], None, "Turkish")
    text = _check_turkish_on_paper(_printed(tmp_path, page), "BİRİM", "KURAL")
    assert "Temel yasalar ve ölçü birimleri" in text


def test_a_printed_practice_quiz_embeds_its_fonts_and_keeps_turkish_letters(tmp_path):
    """A Turkish quiz, as quizzes.make_pdf prints it: its questions and explanations as written, and a box in an
    explanation labelled in Turkish ("KENDİNİ SINA")."""
    from studia import quizzes

    questions = [
        {"n": 1, "type": "mc", "q": "Hangisi bir işlemcinin saat hızını ölçer?",
         "options": ["Gigahertz", "Gigabayt", "Bit", "Piksel"], "answer": 0,
         "why": f"{TURKISH}\n\n:::check\nİki çekirdekli işlemci iki kat hızlı mıdır?\n:::\n",
         "wrong": ["", "Bellek ölçüsü.", "", ""]},
        {"n": 2, "type": "short", "q": "Önbellek neden işe yarar?",
         "model": "Sık kullanılan veriyi işlemciye yakın tutar.", "points": ["Erişim süresi kısalır."]},
    ]
    plan = {"status": "done", "title": "Deneme", "created_at": "2026-11-04T10:00:00",
            "result": {"title": "İşlemciler üzerine deneme", "language": "Turkish", "made": "2026-11-04",
                       "course": {"code": "CE 100"}, "coverage": {"weeks": [1, 2]}, "questions": questions}}
    page = studypack.document_html(quizzes.document_body(plan), plan["result"]["title"], None,
                                   "Turkish").replace("</style>", quizzes.QUIZ_CSS + "</style>", 1)
    text = _check_turkish_on_paper(_printed(tmp_path, page), "DENEME SINAVI", "KENDİNİ SINA")
    assert "Hangisi bir işlemcinin saat hızını ölçer?" in text and "Cevaplar" in text


def test_the_pages_keep_the_documents_casing_and_name_a_class_day_once():
    """The document's own language, not the app's: an English "Definition" isn't uppercased as Turkish
    ("DEFİNİTİON") in a Turkish window, and a Turkish "Kendini sına" is "KENDİNİ SINA" (api.pack_doc's lang)."""
    web = Path(studypack.__file__).parent / "web"
    pack, sheet = ((web / f).read_text(encoding="utf-8") for f in ("studypack.js", "cheatsheet.js"))
    assert '<article class="sp-doc" id="pk-doc" lang="${docLang(p.id)}">' in pack
    assert '<article class="cs-doc" id="cs-doc" lang="${St.pack.docLang(p.id)}">' in sheet
    assert 'const docLang = (id) => S.packLang[id] || "en";' in pack            # (English until it is known)
    assert 'S.packLang[id] = r.lang === "tr" ? "tr" : "en";' in pack
    assert 't("lecture")' not in pack and 'd.ready ? t("transcribed")' in pack   # "Lecture · transcribed"
