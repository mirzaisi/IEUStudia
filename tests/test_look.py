"""How the documents IEUStudia writes read and look (4.0): the tutor-like house style in every AI prompt, the
pictures found on a course file's pages (offered to a study pack's outline, shown cropped to the figure), the lecture
slides study notes show beside their text (in the app's notes, the PDF and Word exports and a saved Markdown copy),
and the AI pack's PDF in the app's document look."""

import re
import types
from pathlib import Path

import pytest

from studia import cheatsheet, digest, plans, studypack
from studia.outputs import doc as D
from studia.outputs import figures, prompts
from studia.outputs.docx import picture_size


# ------------------------------------------------------------------ the prompts
def test_every_document_prompt_asks_for_the_house_style_in_the_courses_language():
    for kind in ("notes", "exam", "flashcards"):
        p = prompts.ai_prompt(kind, "Turkish")
        assert prompts.STYLE in p and "Write in Turkish, the language the course is taught in, headings included." in p
    assert "Write in English" in prompts.ai_prompt("notes") and prompts.ai_prompt("plain") == ""
    assert prompts.STYLE in studypack.SYSTEM and prompts.STYLE in plans.SYSTEM_PREP
    facts = {"span": "week 3", "pack": "", "files": [], "lectures": [], "stressed": [], "language": "Turkish",
             "weeks": [{"no": 3}], "course": {"code": "EEE 215", "name": "Circuits"}}
    assert "Titles and captions in Turkish." in studypack.outline_prompt(plans.Scope(kind="pack"), facts)
    assert "Titles and items in Turkish." in cheatsheet.topics_prompt(facts)
    assert "Write in Turkish" in plans.prep_prompt(plans.Scope(kind="prep"), facts)
    sheet = {"title": "T", "language": "Turkish"}
    assert "Write in Turkish" in cheatsheet.entries_prompt(sheet, [{"id": "t1", "title": "A", "items": ["x"]}], "", "w")
    outline = {"title": "T", "language": "Turkish", "chapters": [{"id": "c1", "title": "A", "teaches": ["x"],
                                                                  "summary": "", "figures": []}]}
    assert "Write in Turkish" in studypack.section_prompt(outline, outline["chapters"][0], "", plans.Scope(kind="pack"))


def test_the_instructions_themselves_have_no_em_dash():
    """The model copies the prompt's own style: an em dash in it comes back in the documents."""
    facts = {"span": "week 3", "pack": "", "files": [{"ref": "F1", "week": 3, "name": "W3.pdf", "pages": 9, "pdf": True,
                                                     "pictures": [2, 3]}],
             "lectures": [], "stressed": [], "weeks": [{"no": 3}], "course": {"code": "EEE 215", "name": "Circuits"}}
    outline = {"title": "T", "chapters": [{"id": "c1", "title": "A", "teaches": ["x"], "summary": "",
                                           "figures": [{"id": "c1f1", "kind": "diagram", "brief": "b", "caption": "c"}]}]}
    texts = [prompts.STYLE, prompts.TRANSCRIPT_NOTES, *(prompts.ai_prompt(k) for k in ("notes", "exam", "flashcards")),
             prompts.slides_note(3, 9), studypack.outline_prompt(plans.Scope(kind="pack"), facts),
             studypack.section_prompt(outline, outline["chapters"][0], "", plans.Scope(kind="pack")),
             studypack.figure_prompt(outline["chapters"][0], ""), studypack._file_head(facts["files"][0]),
             cheatsheet.topics_prompt(facts), plans.prep_prompt(plans.Scope(kind="prep"), facts),
             digest.digest_prompt({"week": {"start": "2026-11-09", "end": "2026-11-15"}, "courses": [], "todo": []})]
    assert not [t[max(0, t.index("—") - 40):t.index("—") + 20] for t in texts if "—" in t]


def test_slides_sent_with_notes_say_how_to_show_a_figure_but_not_for_flashcards():
    note = prompts.slides_note(20, 40)
    assert "20 of the 40 slides are attached" in note and "![what it shows, in a few words](Part n, slide m)" in note
    assert f"At most {prompts.NOTE_FIGURES_MAX}" in note
    cards = prompts.slides_note(4, 4, figures=False)
    assert "Refer to a slide as (Part n, slide m)." in cards and "![" not in cards and "attached, spread" not in cards


# ------------------------------------------------------------------ lecture slides in study notes
def test_figure_lines_become_the_slides_pictures_linked_from_the_notes(tmp_path):
    slides = tmp_path / "EEE 215" / "slides" / "EEE 215 - 2026-10-28 Wed - Lecture"
    slides.mkdir(parents=True)
    for n in (2, 5):
        (slides / f"1215-0{n}.jpg").write_bytes(b"\xff\xd8jpeg")
    notes = tmp_path / "EEE 215" / "AI notes"
    sent = figures.note_slides_for([("[Part 1, slide 2]", slides / "1215-02.jpg"), ("[Part 2, slide 5]", slides / "1215-05.jpg")])
    md = ("## RC circuits (Part 1, 03:00)\n\nThe capacitor discharges.\n\n![The RC circuit after the switch.](Part 1, slide 2)\n\n"
          "More.\n![Again](Part 1, slide 2)\n![Not sent](Part 3, slide 1)\n  ![The step response](part 2 , Slide 5)  \nEnd.")
    out = figures.place_slides(md, sent, notes)
    assert "![The RC circuit after the switch (Part 1, slide 2)](../slides/EEE%20215%20-%202026-10-28%20Wed%20-%20Lecture/1215-02.jpg)" in out
    assert out.count("1215-02.jpg") == 1 and "Not sent" not in out and "Again" not in out     # once; only those sent
    assert "![The step response (Part 2, slide 5)](../slides/" in out and "\n\n\n" not in out
    assert figures.place_slides(md, sent, notes, most=1).count("](../slides/") == 1
    link = re.search(r"\]\(([^)]+1215-05\.jpg)\)", out).group(1)
    assert figures.local_image(link, notes) == (slides / "1215-05.jpg").resolve()


def test_only_a_picture_on_this_pc_is_ever_shown(tmp_path):
    pic = tmp_path / "a b.png"
    pic.write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "x.txt").write_text("x")
    assert figures.local_image("a%20b.png", tmp_path) == pic.resolve()
    assert figures.local_image(pic.as_uri(), None) == pic.resolve()
    for src in ("https://evil.example/x.png", "//server/share/x.png", "\\\\server\\share\\x.png", "x.txt",
                "missing.png", "file://server/share/a%20b.png", "javascript:alert(1)//.png"):
        assert figures.local_image(src, tmp_path) is None, src
    assert figures.local_image("a%20b.png", None) is None                 # relative to nothing: no
    assert figures.caption_parts("RC circuit (Part 2, slide 7)") == ("RC circuit", "Part 2, slide 7")
    html = figures.image_html("A <b> (Part 1, slide 3)", "a%20b.png", tmp_path)
    assert '<figure class="sp-fig sp-frame"><img src="file:///' in html and "&lt;b&gt;" in html
    assert '<span class="sp-src">(Part 1, slide 3)</span>' in html and figures.image_html("x", "y.png", tmp_path) == ""


def test_the_notes_pictures_in_the_pdf_word_and_markdown_exports(tmp_path):
    folder = tmp_path / "AI notes"
    folder.mkdir()
    (tmp_path / "s.jpg").write_bytes(b"\xff\xd8\xff\xe0jpeg")
    md = "## Topic\n\nText.\n\n![The circuit (Part 1, slide 2)](../s.jpg)\n\n![gone](../none.jpg)\n\nMore."
    blocks = D.read_markdown(md, 1, str(folder))
    figs = [b for b in blocks if isinstance(b, D.Figure)]
    assert len(figs) == 1 and Path(figs[0].path) == (tmp_path / "s.jpg").resolve()
    assert D.plain(figs[0].caption) == "The circuit (Part 1, slide 2)"
    page = D.to_html(D.Doc("Notes", blocks=[D.Markdown(md, top=1, base=str(folder))]))
    assert page.count("<figure") == 1 and (tmp_path / "s.jpg").resolve().as_uri() in page and "none.jpg" not in page
    saved = D.to_markdown(D.Doc("Notes", blocks=[D.Markdown(md, top=1, base=str(folder))]))
    assert f"![The circuit (Part 1, slide 2)]({(tmp_path / 's.jpg').resolve().as_uri()})" in saved and "none.jpg" not in saved
    plain = D.to_text(D.Doc("Notes", blocks=[D.Markdown(md, top=1, base=str(folder))]))
    assert "[Picture: The circuit (Part 1, slide 2)]" in plain
    chat = "Look:\n\n![x](https://example.com/x.png)"                   # a chat answer (no folder): as it was
    assert D.to_markdown(D.Doc("Chat", blocks=[D.Markdown(chat)])).count("https://example.com/x.png") == 1
    assert "<img" not in D.to_html(D.Doc("Chat", blocks=[D.Markdown(chat)]))


PAGE = """
const stub = () => new Proxy(function () {}, { get: (t, k) => k === Symbol.toPrimitive ? () => "" : k === "length" ? 0 : stub(),
  apply: () => stub(), construct: () => stub() });
globalThis.window = globalThis;
globalThis.document = new Proxy({}, { get: (t, k) => k === "compatMode" ? "CSS1Compat" : stub() });
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.navigator = { userAgent: "node", platform: "Win32" };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;
const web = %(web)s, cases = %(cases)s;
for (const f of ["i18n.js", "icons.js", "app.js"]) require(web + "/" + f);
const out = cases.map(([s, o]) => LSRender.md(s, o));
process.stdout.write(JSON.stringify(out), () => process.exit(0));
"""


@pytest.mark.skipif(not __import__("shutil").which("node"), reason="node is not installed")
def test_the_apps_notes_show_a_slide_from_the_notes_folder_and_nothing_from_elsewhere(tmp_path):
    import json
    import subprocess

    web = Path(studypack.__file__).parent / "web"
    base = r"C:\Users\s\Documents\IEUStudia\EEE 215\AI notes\EEE 215 - 2026-10-28 Wed - Lecture - Study notes.md"
    line = "![The RC circuit (Part 2, slide 7)](../slides/EEE%20215%20-%20Lecture/1215-07.jpg)"
    cases = [[f"Text.\n\n{line}\n\nMore.", {"base": base}],
             ["![x](https://evil.example/x.png)\n\n![y](//server/share/y.png)", {"base": base}],
             [line, {}],                                                   # not notes (a chat): as it was
             ["![The step response](Part 2, slide 9)", {"base": base, "streaming": True}]]     # still being written
    script = tmp_path / "page.js"
    script.write_text(PAGE % {"web": json.dumps(web.as_posix()), "cases": json.dumps(cases)}, encoding="utf-8")
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    shown, remote, chat, writing = json.loads(run.stdout)
    assert ('<figure class="md-fig"><img src="file:///C:/Users/s/Documents/IEUStudia/EEE%20215/slides/'
            'EEE%20215%20-%20Lecture/1215-07.jpg"') in shown
    assert "<figcaption>The RC circuit <span class=\"dim\">(Part 2, slide 7)</span></figcaption>" in shown
    assert "<p>Text.</p>" in shown and "<p>More.</p>" in shown
    assert "<img" not in remote and "evil" not in remote and "server" not in remote
    assert "<img" not in chat and "md-fig" not in chat
    assert "<img" not in writing and "The step response" in writing and "(Part 2, slide 9)" in writing


# ------------------------------------------------------------------ pictures on a course file's pages
def _pdf(pages: list[str]) -> bytes:
    """A small A4 PDF whose pages draw ``pages`` (content streams), with Helvetica as /F1."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for content in pages:
        data = content.encode("latin-1")
        objs.append(f"<< /Length {len(data)} >>\nstream\n{content}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> "
                    f"/Contents {len(objs)} 0 R >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


def _text(x: float, y: float, s: str, size: int = 11) -> str:
    return f"BT /F1 {size} Tf {x} {y} Td ({s}) Tj ET\n"


RUNNING = "".join(_text(50, 790 - 16 * i, "This is a line of running text that goes across the page width") for i in range(4))
CIRCUIT = ("200 440 m 410 440 l S\n200 540 m 410 540 l S\n200 440 m 200 540 l S\n410 440 m 410 540 l S\n"    # wires
           + "".join(f"{x} 533 30 14 re S\n" for x in (230, 290, 350))                                          # resistors
           + "190 488 20 3 re f\n190 494 20 3 re f\n402 482 16 16 re S\n"                                      # C, a source
           + _text(237, 552, "R1", 9) + _text(297, 552, "R2", 9) + _text(357, 552, "R3", 9) + _text(170, 490, "C", 9))
FRAME = ("50 300 495 400 re S\n" + "".join(_text(60, 680 - 16 * i, "Exercise text that runs along the whole box, line") for i in range(8))
         + "".join(f"60 {500 - 30 * i} m 520 {500 - 30 * i} l S\n" for i in range(6)))
DECOR = "".join(f"{450 + 10 * i} 0 m 595 {842 - 40 * i} l 595 0 l f\n" for i in range(12))
QR = "".join(f"{100 + 4 * (i % 20)} {300 + 4 * (i // 20)} 3 3 re f\n" for i in range(400))


@pytest.fixture
def deck(tmp_path):
    pdf = tmp_path / "Week 6.pdf"
    pdf.write_bytes(_pdf([RUNNING + CIRCUIT, FRAME, RUNNING + DECOR, RUNNING + QR]))
    return pdf


def test_a_drawing_is_found_and_text_boxes_decoration_and_codes_are_not(deck):
    found = figures.page_pictures(deck)
    assert list(found) == [1]                                     # the circuit; not a box of text, a bleed, a QR code
    (box,) = found[1]
    assert 165 <= box[0] <= 172 and 430 <= box[1] <= 441 and 410 <= box[2] <= 420 and 545 <= box[3] <= 565   # with its labels
    assert figures.page_pictures(deck) is found                   # (looked at once)
    assert figures.page_pictures(deck.with_name("none.pdf")) is None
    bad = deck.with_name("bad.pdf")
    bad.write_bytes(b"%PDF broken")
    assert figures.page_pictures(bad) is None                     # unknown: not "no pictures"
    assert figures.pages_text([3, 4, 5, 9, 11, 12]) == "3–5, 9, 11–12"


def test_a_slide_page_is_shown_cropped_to_its_picture(deck, tmp_path):
    out = tmp_path / "fig.jpg"
    assert figures.figure_image(deck, 1, out, 800)
    w, h = picture_size(out.read_bytes())
    assert abs(w - 800) <= 2 and 0.3 < h / w < 0.8                        # the circuit's shape, not the A4 page's (1.41)
    whole = tmp_path / "page.jpg"
    assert figures.figure_image(deck, 2, whole, 400)              # no picture found: the whole page
    w, h = picture_size(whole.read_bytes())
    assert 1.3 < h / w < 1.5
    assert not figures.figure_image(deck, 9, tmp_path / "x.jpg") and not figures.figure_image(
        deck.with_name("bad.pdf"), 1, tmp_path / "y.jpg")
    assert figures.crop_box([(0, 0, 590, 830)], 595, 842) is None                 # most of the page: all of it
    far = figures.crop_box([(50, 50, 150, 120), (300, 600, 560, 800)], 595, 842)  # two far apart: the bigger one
    assert far == (288.0, 588.0, 572.0, 812.0)


def test_the_outline_only_offers_pages_with_a_picture_and_says_which(deck):
    facts = {"files": [{"ref": "F1", "name": "Week 6.pdf", "pages": 4, "pdf": True, "path": str(deck), "week": 6,
                        "pictures": studypack.picture_pages({"pdf": True, "path": str(deck)})},
                       {"ref": "F2", "name": "Old.pdf", "pages": 4, "pdf": True, "path": "", "week": 6}],
             "lectures": [], "span": "week 6", "language": "English"}
    assert "Week 6.pdf, 4 pages, PDF; pages with a figure: 1 ===" in studypack._file_head(facts["files"][0])
    assert "figure" not in studypack._file_head(facts["files"][0], pictures=False)
    raw = {"chapters": [{"id": "A", "title": "RC", "teaches": ["x"],
                         "figures": [{"kind": "slide", "ref": "F1", "page": 1, "caption": "the circuit"},
                                     {"kind": "slide", "ref": "F1", "page": 2, "caption": "a box of text"},
                                     {"kind": "slide", "ref": "F2", "page": 3, "caption": "known before 4.0: any"}]}]}
    out = studypack.clean_outline(raw, facts)
    assert [(g["ref"], g["page"]) for g in out["chapters"][0]["figures"]] == [("F1", 1), ("F2", 3)]
    assert out["language"] == "English"


def test_a_chapters_writer_sees_the_pages_with_figures_it_explains(deck, tmp_path):
    """The circuits and equations of a slide deck are often pictures: written from the text alone, chapters warned
    "I could not see the circuit" and made up their own numbers."""
    from studia import store
    from studia.slides import Slide

    shot = tmp_path / "frame.jpg"
    assert figures.figure_image(deck, 1, shot, 300)                       # (a real JPEG)
    store.save_slides("d1", [Slide(7, 0.0, 9.0, "x", str(shot))])
    facts = {"files": [{"ref": "F1", "name": "Week 6.pdf", "pages": 4, "pdf": True, "path": str(deck), "week": 6,
                        "pictures": [1, 3]}],
             "lectures": [{"ref": "L1", "date": "2026-10-28", "parts": [{"part": 2, "delivery_id": "d1", "slides": 7}]}]}
    ch = {"figures": [{"id": "c1f1", "kind": "frame", "ref": "L1", "part": 2, "slide": 7, "caption": "board"},
                      {"id": "c1f2", "kind": "slide", "ref": "F1", "page": 3, "caption": "the circuit"}],
          "sources": {"files": [{"ref": "F1", "pages": [1, 4]}], "lectures": []}}
    got = studypack.chapter_pictures(facts, ch)
    assert [label for label, _ in got] == ["[L1 part 2, slide 7]", "[F1 p.3]", "[F1 p.1]"]  # its own figures first
    assert len(studypack.chapter_pictures(facts, ch, most=1)) == 1
    blocks = studypack.picture_blocks(got)
    assert [b.get("text") or b["type"] for b in blocks] == ["[L1 part 2, slide 7]", "image", "[F1 p.3]", "image",
                                                             "[F1 p.1]", "image"]
    outline = {"title": "T", "chapters": [{"id": "c1", "title": "RC", "teaches": ["x"], "summary": "", **ch}]}
    prompt = studypack.section_prompt(outline, outline["chapters"][0], "", plans.Scope(kind="pack"),
                                      [label for label, _ in got])
    assert "attached as pictures, labelled [L1 part 2, slide 7], [F1 p.3], [F1 p.1]: read the circuits" in prompt
    assert "attached as pictures" not in studypack.section_prompt(outline, outline["chapters"][0], "",
                                                                  plans.Scope(kind="pack"))


def test_a_figure_marker_in_a_line_of_text_goes_on_a_line_of_its_own():
    ch = {"title": "RC", "figures": [{"id": "c1f1"}, {"id": "c1f2"}]}
    md = studypack.clean_section("As shown here [[FIGURE c1f1]] (the RC circuit)\n\n[[FIGURE c1f2]] — its step response\nText.", ch)
    assert md.startswith("As shown here\n\n[[FIGURE c1f1]]\n") and "\n[[FIGURE c1f2]]\nText." in md
    assert "the RC circuit" not in md and "step response" not in md
    body = studypack.md_html(md, lambda fid: f"<figure>{fid}</figure>")
    assert body.count("<figure>") == 2 and "[[FIGURE" not in body


def test_a_pictures_source_names_the_lecture_by_its_day():
    facts = {"files": [{"ref": "F1", "name": "W6.pdf"}], "lectures": [{"ref": "L1", "date": "2026-10-28"}]}
    assert studypack._source(facts, {"kind": "frame", "ref": "L1", "part": 2, "slide": 7}) == \
        "lecture of 28 Oct 2026, part 2, slide 7"
    assert studypack._source(facts, {"kind": "slide", "ref": "F1", "page": 4}) == "W6.pdf, page 4"


@pytest.mark.parametrize("kind, title, head, rest", [
    ("check", "Check", "Check yourself", ""), ("check", "Check yourself", "Check yourself", ""),
    ("example", "Example 4", "Example 4", ""), ("example", "Example 1: four-mesh network", "Example 1", "Four-mesh network"),
    ("example", "Worked example: RC discharge", "Example", "RC discharge"), ("example", "Example 2.3", "Example 2.3", ""),
    ("mistake", "Common mistake: forgetting the sign", "Common mistake", "Forgetting the sign"),
    ("definition", "Conditional probability", "Definition", "Conditional probability"),
    ("example", "Examples of use", "Example", "Examples of use")])
def test_a_boxs_heading_doesnt_say_the_same_twice(kind, title, head, rest):
    assert studypack.box_head(kind, title) == (head, rest)


def test_box_headings_in_the_pdf_and_in_word():
    md = ":::example Example 1: four-mesh network\nSolve it.\n:::\n\n:::check Check\n1. Why?\n:::"
    out = studypack.md_html(md)
    assert '<div class="sp-box-h"><b>Example 1</b> · Four-mesh network</div>' in out
    assert '<div class="sp-box-h"><b>Check yourself</b></div>' in out
    boxes = [b for b in D.read_markdown(md) if isinstance(b, D.Box)]
    assert [(b.kind, D.plain(b.title)) for b in boxes] == [("example", "1: Four-mesh network"), ("check", "")]


def test_a_formula_keeps_its_punctuation_on_its_line():
    out = studypack.md_html("Find $i(t)$, $\\tau$ and $i(\\tau)$. Lowercase ($x$).")
    assert "$i(t)$⁠," in out and "$i(\\tau)$⁠." in out and "(⁠$x$⁠)." in out


def test_a_picture_line_is_drawn_only_through_the_callback():
    md = "Text.\n\n![A slide (Part 1, slide 2)](x.jpg)\n\nMore."
    assert "<img" not in studypack.md_html(md) and "x.jpg" not in studypack.md_html(md)
    out = studypack.md_html(md, image=lambda alt, src: f"<figure>{alt}|{src}</figure>")
    assert "<figure>A slide (Part 1, slide 2)|x.jpg</figure>" in out and out.count("<p>") == 2


# ------------------------------------------------------------------ the review sheet's PDF
def test_a_review_sheet_prints_in_the_document_look_next_to_its_markdown(tmp_path, monkeypatch):
    from studia.domain import Course

    md_file = tmp_path / "Review sheets" / "2026-11-04 - Weeks 1–7 review sheet.md"
    md_file.parent.mkdir()
    md_file.write_text("x", encoding="utf-8")
    pages = []

    def fake_print(html_path, pdf_path):
        pages.append(Path(html_path).read_text(encoding="utf-8"))
        Path(pdf_path).write_bytes(b"%PDF fake")
        return True

    monkeypatch.setattr(studypack, "print_pdf", fake_print)
    s = types.SimpleNamespace(lib=types.SimpleNamespace(get_course=lambda cid: Course(id="c", title="t", code="EEE 215",
                                                                                     name="Circuits")))
    plan = {"id": "p", "course_id": "c", "status": "done", "created_at": "2026-11-04T18:10:38",
            "result": {"kind": "prep", "title": "Weeks 1–7 review sheet", "file": str(md_file),
                       "markdown": "## Practice\n\n1. Find $R_{eq}$.\n\n> **Solution.** $R_{eq} = 6\\,\\Omega$.\n\n"
                                   "## Last-day checklist\n\n- [ ] Node analysis"}}
    pdf = studypack.make_pdf(s, plan)
    assert pdf == md_file.with_suffix(".pdf") and pdf.read_bytes() == b"%PDF fake"
    page = pages[-1]
    assert "EEE 215 · Review sheet</div><h1>Weeks 1–7 review sheet</h1>" in page and "Made with IEUStudia on 4 Nov 2026" in page
    assert "<blockquote>" in page and "☐ Node analysis" in page and "katex" in page
    assert studypack.make_pdf(s, {**plan, "status": "running"}) is None


# ------------------------------------------------------------------ the AI pack's PDF
def test_the_ai_pack_is_printed_in_the_apps_document_look():
    import datetime as dt

    from studia.domain import Course, Recording, Segment, Transcript
    from studia.export import render_pack_html
    from studia.slides import Slide

    course = Course(id="c", title="t", code="EEE 215", name="Circuits", instructor="Dr. A")
    rec = Recording(delivery_id="a", course_id="c", folder_id="f", folder_path="", name="n",
                    start=dt.datetime(2026, 10, 28, 12, 15), duration_s=600, viewer_url="v")
    segs = [Segment(5, 20, "The capacitor charges, this is important for the exam.", "en", 0.9),
            Segment(130, 150, "Kondansatör doluyor.", "tr", 0.9)]
    tr = Transcript(delivery_id="a", segments=segs, duration_s=600, speech_s=40, language_seconds={"en": 20, "tr": 20},
                    model="m", device="cpu", engine_version="t", created_at="2026-10-28T13:00:00")
    page = render_pack_html(course, [rec], {"a": tr}, {"a": [Slide(1, 0.0, 120.0, "s/1.jpg", "C:/x/1.jpg")]},
                            {"C:/x/1.jpg": "slides/1.jpg"})
    assert '<article class=\'sp-doc xp\'>' in page and "sp-cover" in page and ".sp-doc {" in page
    assert "<figure class='sp-fig sp-frame'><img src='slides/1.jpg'" in page and "#312e81" not in page
    assert '<p class="xp-pa xp-tr"><span class="xp-ts">[02:10]</span><span class="xp-tag">TR</span>' in page
    assert "—" not in re.sub(r"<style>.*?</style>", "", page, flags=re.S)
