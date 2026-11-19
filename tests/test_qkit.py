"""4.2.0 QA of the exam kit, the pickers and the lecture page, found by using the integrated build on the owner's
data: a kit's zip named after its quiz; an announcement that lists students kept out of the zip and the brief; old
Office (.xls, .doc, .ppt) and OpenDocument class lists told by what they hold; a pasted answer that stops part way
(its title kept, and said); a pasted source that names a file of an older zip; on the page, the export dialog's size
counting a two-week unit's files once and its picked weeks scrolled into view, the kit's way of sending a prompt with
the zip, the paste's "stops part way" line, and the lecture's buttons kept on one line (Turkish at 1024 px). No
network and no AI."""

import datetime as dt
import json
import shutil
import struct
import subprocess
import zipfile

import pytest

from studia import kit, materials
from studia.coursesite import Announcement
from studia.library import Library
from test_kit import CARDS_JSON, CHATGPT, TODAY, env, export, names, read  # noqa: F401 - env is a fixture
from test_kit_page import EXPORT_ANSWERS, STAND_IN, WEB, _run
from test_quizme_page import LECTURE, PRACTICE, _page

node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


# ------------------------------------------------------------------ the zip
def test_a_kit_zip_is_named_after_its_quiz(env):
    """Two kits of a course on one day were "MATH 240 - weeks 9-10 - 19 Nov 2026.zip" and "… weeks 1-15 …": nothing
    said which was the Quiz 3 kit and which the final's."""
    assert export(env, weeks=[2, 3], focus="quiz:2")["name"] == "MATH 240 - Quiz 2 - 8 Oct 2026.zip"
    assert export(env, weeks=[1, 2, 3], focus="quiz:2")["name"] == "MATH 240 - Quiz 2 - weeks 1-3 - 8 Oct 2026.zip"
    final = export(env, weeks=[1, 2, 3], focus="syllabus:Final Exam:15")
    assert final["name"] == "MATH 240 - Final Exam - 8 Oct 2026.zip"
    assert read(final, "00 Start here.md").startswith("# Start here: Final Exam")
    # everything up to now (the dialog sends it so when the final's weeks are those begun): no cut "everything up to
    # 19" ("EEE 215 - Final Exam - everything up to 19 Nov 2026" is longer than a zip's name may be)
    assert export(env, focus="syllabus:Final Exam:15")["name"] == "MATH 240 - Final Exam - 8 Oct 2026.zip"
    assert export(env, focus="quiz:2")["name"] == "MATH 240 - Quiz 2 - up to 8 Oct 2026.zip"
    assert export(env, weeks=[2, 3])["name"] == "MATH 240 - weeks 2-3 - 8 Oct 2026.zip"          # (no For: as before)


CLASS = "\n".join(f"{20210602000 + 37 * n} Ali Veli {n}" for n in range(4))


def test_an_announcement_that_lists_students_stays_out_of_the_zip_and_the_brief(env):
    """An announcement naming students by number (a make-up exam list) was copied whole into Announcements.md, and
    its excerpt into "Quizzes, exams and deadlines.md" and the brief's "What the lecturer said": classmates' details
    in the zip the README says to upload."""
    env.lib.merge_announcements("c", [
        Announcement("a1", "Quiz 2", "Dr Özkut", dt.datetime(2026, 10, 1, 10, 0),
                     "Quiz 2 will be in week 4 and will cover weeks 2 and 3.\nIt covers Learning Outcome 2."),
        Announcement("a2", "Quiz 2 make-up", "Dr Özkut", dt.datetime(2026, 10, 2, 9, 0),
                     "These students take the make-up of Quiz 2 in week 4:\n" + CLASS),
        Announcement("a3", "Textbook", "Dr Özkut", dt.datetime(2026, 9, 22, 9, 0),
                     "Our book for the quiz: ISBN 0131103628, ISBN-10: 0073380547 and ISBN 1118539290.")],
        first_sync=True)
    assert materials.text_lists_students([CLASS], materials.ANNOUNCED_MIN)
    out = export(env, weeks=[2, 3], focus="quiz:2")
    anns = read(out, "Announcements.md")
    assert "## Quiz 2 make-up" in anns and "Its text is left out of this zip: it lists students" in anns
    assert "0131103628" in anns                                                  # a book's ISBNs are no class list
    with zipfile.ZipFile(out["path"]) as z:
        text = "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.endswith(".md"))
        prompts = [z.read(n).decode("utf-8") for n in z.namelist() if "/Prompts/" in n]
    assert "20210602000" not in text and "Ali Veli" not in text
    assert prompts and not any("2021060" in p for p in prompts)
    assert "Quiz 2 will be in week 4" in read(out, "00 Start here.md")            # the other announcement still is


def test_the_readme_lists_no_course_wide_folder_when_its_only_file_was_left_out(env):
    """EEE 215's only course-wide file is its lab grades: the README still listed "General course files/", a folder
    the zip didn't have."""
    env.lib.set_material_text("r9", ["\n".join(f"{20210600000 + 37 * n} {60 + n % 40}" for n in range(30))])
    out = export(env, weeks=[2, 3])
    assert not any(n.startswith("General course files/") for n in names(out)) and out["students"] == 1
    readme = read(out, "README.md")
    assert "General course files/" not in readme and "Formulas.pdf was left out" in readme


def test_a_zip_of_weeks_not_taught_yet_doesnt_send_the_ai_looking_for_transcripts(env, tmp_path):
    """The Quiz 3 kit (weeks 9-10, not taught yet) held 3 PDFs, but its first message said "with the lecture
    transcripts (with times), the screens shown in class…" and its README listed Lectures/<date>/Transcript.md."""
    from studia.coursesite import Resource, Unit

    from test_kit import _file, units

    env.lib.merge_units("c", units() + [Unit("u4", 4, "Hafta 4", dt.date(2026, 10, 12), dt.date(2026, 10, 18), "", [
        Resource("r4", "Random variables", "Random.pdf", "pdf", "pdf", "u4", "/d/r4")])], first_sync=True)
    env.lib.set_material_file("r4", _file(tmp_path / "files" / "Random.pdf"), 13, "u")
    out = export(env, weeks=[4])
    assert out["lectures"] == 0 and out["files"] == 2
    msg = out["first_message"]
    assert "a folder for each week, with the course files." in msg and "transcripts" not in msg and "screens" not in msg
    readme = read(out, "README.md")
    assert msg in readme and "Transcript.md" not in readme and "Screens/" not in readme
    assert "Course files/: the week's slides" in readme
    whole = export(env, weeks=[3])                                             # a week with its lecture: as before
    assert "the lecture transcripts (with times)" in whole["first_message"]
    assert "Lectures/<date>/Transcript.md" in read(whole, "README.md")


def _ole(*parts):
    """An old Office file's bytes: its compound-file header and the records or text given."""
    return b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504 + b"".join(parts) + b"\x00" * 64


def _number(row, value):
    return b"\x03\x02\x0e\x00" + struct.pack("<HHHd", row, 0, 15, value)


def _rk(row, n):
    return b"\x7e\x02\x0a\x00" + struct.pack("<HHHI", row, 0, 15, (n << 2) | 2)


def _mulrk(row, ns):
    return (b"\xbd\x00" + struct.pack("<H", 6 * len(ns) + 6) + struct.pack("<HH", row, 0)
            + b"".join(struct.pack("<HI", 15, (n << 2) | 2) for n in ns) + struct.pack("<H", len(ns) - 1))


def test_old_office_and_opendocument_class_lists_are_told_by_what_they_hold(tmp_path):
    """A class list as an Excel 97-2003 workbook (.xls), an OpenDocument sheet (.ods) or an old Word file (.doc)
    was not read (only .xlsx and .csv were), so it went into the zip for ChatGPT and to every AI tool."""
    from studia.coursesite import Resource, Unit
    from studia.domain import Course

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="e", title="2026-1 EEE 215 X 1", code="EEE 215", name="Circuits", term="T")])
    kinds = {"x1": "xls", "x2": "xls", "x3": "xls", "o1": "ods", "d1": "doc", "p1": "ppt", "s1": "xls"}
    lib.merge_units("e", [Unit("u1", 0, "Hafta 1", dt.date(2026, 9, 21), dt.date(2026, 9, 27), "", [
        Resource(rid, rid, f"{rid}.{ext}", ext, ext, "u1", f"/d/{rid}") for rid, ext in kinds.items()])],
        first_sync=True)
    files = {
        # numbers typed as numbers (NUMBER records) and as text (the shared strings, one byte a letter)
        "x1": _ole(*(_number(r, 20210602000.0 + 41 * r) for r in range(12)),
                   *(f"{20210603000 + 7 * r}".encode() + b"\x00\x02" for r in range(8))),
        # 9-digit numbers Excel keeps as RK values, alone and several in a row (MULRK)
        "x2": _ole(*(_rk(r, 212000000 + 13 * r) for r in range(10)),
                   *(_mulrk(20 + r, [213000000 + 17 * r, 75, 80]) for r in range(11))),
        "x3": _ole(*(_number(r, 20210602000.0 + r) for r in range(5))),                     # five: no class list
        "d1": _ole(*(f"{20210604000 + 3 * r}\t".encode("utf-16-le") for r in range(22))),   # Word's UTF-16 text
        "p1": _ole(b"3.14159265358 2.71828182845 " * 30),                                  # decimals: none
    }
    for rid, data in files.items():
        p = tmp_path / f"{rid}.{kinds[rid]}"
        p.write_bytes(data)
        lib.set_material_file(rid, str(p), len(data), "u")
    ods = tmp_path / "o1.ods"
    with zipfile.ZipFile(ods, "w") as z:
        z.writestr("content.xml", "<office:document-content><table:table>" + "".join(
            f'<table:table-row><table:table-cell office:value-type="float" office:value="{20210605000 + 9 * r}">'
            f"<text:p>{20210605000 + 9 * r}</text:p></table:table-cell></table:table-row>" for r in range(21))
            + "</table:table></office:document-content>")
    lib.set_material_file("o1", str(ods), ods.stat().st_size, "u")
    found = {rid: materials.lists_students(lib, rid) for rid in ("x1", "x2", "x3", "o1", "d1", "p1")}
    assert found == {"x1": True, "x2": True, "x3": False, "o1": True, "d1": True, "p1": False}
    assert materials.left_out(lib, "x1") and not materials.left_out(lib, "x3")
    nums = materials.ole_numbers(files["x2"])
    assert "212000013" in nums and "213000017" in nums and "75" not in nums


def test_a_pasted_answer_that_stops_part_way_keeps_its_title_and_says_so(env):
    """ChatGPT stopped in the middle of question 4: three questions came in under "Weeks 2–3" (its title lost), and
    nothing told the student the rest was missing."""
    export(env, weeks=[2, 3], focus="quiz:2")
    cut = (CHATGPT % {"kit": "00000000"}).split('{"type": "mc", "question": "What is the multiplication rule')[0]
    cut += '{"type": "mc", "question": "What is the multipl'
    r = kit.pasted(env.api, "c", cut)
    assert r["ok"] and r["questions"] == 3 and r["cut"] and r["title"] == "Conditional probability and Bayes"
    whole = kit.pasted(env.api, "c", CHATGPT % {"kit": "00000000"})
    assert whole["ok"] and not whole["cut"]
    cards_cut = (CARDS_JSON % {"kit": "x"}).split('{"front": "Total probability rule"')[0] + '{"front": "Total prob'
    r = kit.pasted(env.api, "c", cards_cut)
    assert r["ok"] and r["kind"] == "cards" and r["cards"] == 2 and r["cut"]
    assert not kit.was_cut("| Front | Back |\n|---|---|\n| a card | its back |")


def test_the_prompt_pasted_back_by_mistake_adds_nothing(env):
    """The end dialog's Copy put the quiz prompt on the clipboard and said "press Ctrl+V on this course's Practice
    tab": pasted there, its JSON example became a quiz of one question "…", and the cards prompt a card "a short
    question or cue" in the deck."""
    k = export(env, weeks=[2, 3], focus="quiz:2")["kit"]
    for p in k["prompts"]:
        r = kit.pasted(env.api, "c", p["text"])
        assert not r["ok"] and r["error"].startswith("This is the prompt for ChatGPT or Claude, not its answer."), p["key"]
    assert not [p for p in env.lib.plans("c") if (p.get("scope") or {}).get("source") == "import"]
    quiz = next(p["text"] for p in k["prompts"] if p["key"] == "quiz")
    r = kit.pasted(env.api, "c", f"{quiz}\n\nChatGPT said:\n{CHATGPT % {'kit': k['id']}}")     # the whole chat copied
    assert r["ok"] and r["questions"] == 4 and r["kit"] == k["id"]


def test_a_quiz_from_another_courses_zip_says_where_it_goes(env):
    """A quiz from the EEE 215 zip's chat, pasted on MATH 240's Practice tab, was saved under MATH 240 with its
    "F3 p.8" opening MATH 240's F3: the wrong file's page, shown as the question's source."""
    from studia.domain import Course

    env.lib.upsert_courses([Course(id="c", title="2026-1 MATH 240 Olasılık 1", code="MATH 240", name="Probability",
                                   term="2026-2027 Fall"),
                            Course(id="e", title="2026-1 EEE 215 Devreler 1", code="EEE 215", name="Circuits",
                                   term="2026-2027 Fall")])
    kit.save(env.lib, {"id": "e1e2e3e4", "course_id": "e", "made": "2026-10-08", "weeks": [2], "key": "", "focus": "",
                       "refs": {}, "stressed": {}})
    r = kit.pasted(env.api, "c", CHATGPT % {"kit": "e1e2e3e4"})
    assert not r["ok"] and r["error"] == "This was made from your EEE 215 zip. Open EEE 215's Practice tab and paste " \
                                         "it there."
    assert not [p for p in env.lib.plans("c") if (p.get("scope") or {}).get("source") == "import"]
    k = export(env, weeks=[2, 3], focus="quiz:2")["kit"]                      # its own course's: as before
    assert kit.pasted(env.api, "c", CHATGPT % {"kit": k["id"]})["ok"]


def test_a_source_naming_a_file_of_an_older_zip_opens_it(env):
    """A quiz from the chat of an earlier zip, pasted without its kit's id, named "Bayes theorem.pdf, page 2": only
    the newest zip's files were looked up by name, so a file it didn't hold had no source."""
    export(env, weeks=[2, 3], focus="quiz:2")
    export(env, weeks=[1])                                       # the newest kit: week 1's file and the formula sheet
    md = ("## Question 1\nWhat does the total probability rule give in Bayes' theorem?\n\nA) The numerator\n"
          "B) The denominator P(A)\nC) The prior\nD) The likelihood\n\n**Answer:** B\n"
          "**Source:** Bayes theorem.pdf, page 2\n")
    r = kit.pasted(env.api, "c", md)
    assert r["ok"] and r["named"] == 1
    q = env.lib.plan(r["id"])["result"]["questions"][0]
    assert q["source"]["name"] == "Bayes theorem.pdf" and q["source"]["page"] == 2 and not q.get("found")
    named = kit._names(None, env.lib, "c")
    assert ("bayes theorem.pdf", "F3") in named and ("bayes theorem", "F3") in named
    assert ("formulas.pdf", "F4") in named and ("formulas", "F4") not in named        # (a short word alone: no)


# ------------------------------------------------------------------ the page
EXPORT_PAGE = STAND_IN + r"""
require(%(page)s);
const list = { scrollTop: 0, getBoundingClientRect: () => ({ top: 100 }),
  querySelector: (s) => (s === ".cx-wk.on" ? { getBoundingClientRect: () => ({ top: 420 }) } : null) };
const plain = document.querySelector;
document.querySelector = (sel) => (sel === ".cx-weeks" ? list : plain(sel));
(async () => {
  await A["cx-open"]({ dataset: { id: "c" } }); await tick();
  out.before = list.scrollTop;
  A["cx-for"]({ value: "quiz:2" });
  out.scrolled = list.scrollTop;                                        // to its first picked week
  A["cx-week"]({ dataset: { no: "1" }, checked: true });
  out.kept = list.scrollTop;                                            // a tick after that: where it was
  out.est = document.querySelector("#cx-est") ? "" : shown.html.match(/About ([^<·]+)/)[1].trim();
  A["cx-go"](); await tick();
  St.onEvents.course_export({ type: "course_export", id: "x1", course_id: "c", phase: "done", name: "z.zip", bytes: 1,
    path: "C:/x/z.zip", first_message: "…", kit: { id: "k", focus: "Quiz 2", start: true, prompts: [{ key: "quiz", text: "Q" }] } });
  out.done = shown.html;
  await A["cx-copy-prompt"]({ dataset: { k: "quiz" } });
  out.toasts = toasts;
  t.load({ lang: "tr", table: turkish });
  St.onEvents.course_export({ type: "course_export", id: "x1", course_id: "c", phase: "done", name: "z.zip", bytes: 1,
    path: "C:/x/z.zip", first_message: "…", kit: { id: "k", focus: "Quiz 2", start: true, prompts: [{ key: "quiz", text: "Q" }] } });
  out.doneTr = shown.html;
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})();
"""


@node
def test_the_export_dialog_counts_a_shared_file_once_and_shows_the_picked_weeks(tmp_path):
    """Picking Quiz 3 ticked weeks 9-10 below the list's fold (weeks 1-6 shown, all unticked: "2 weeks picked" but
    none seen); a two-week unit's file was added to the size twice; and the end didn't say a prompt can go with the
    zip, as the first message."""
    mb = 1048576
    answers = json.loads(json.dumps(EXPORT_ANSWERS))
    for w in answers["course_export_options"]["weeks"]:
        w["bytes"] = {"text": 0, "screen_text": 0, "screens": 0, "files": 0}
        w["sizes"] = {}
    weeks = {w["no"]: w for w in answers["course_export_options"]["weeks"]}
    weeks[2]["sizes"] = {"shared": 3 * mb, "w2": mb}
    weeks[3]["sizes"] = {"shared": 3 * mb}
    weeks[2]["bytes"]["files"], weeks[3]["bytes"]["files"] = 4 * mb, 3 * mb
    answers["course_export_options"]["base"] = answers["course_export_options"]["notices"] = 0
    out = _run(tmp_path, EXPORT_PAGE, answers, page="export.js")
    assert out["before"] == 0 and out["scrolled"] == 316 and out["kept"] == 316
    assert out["est"] == "4.0 MB"                                                 # (3 + 1, not 3 + 1 + 3)
    assert "Send one of these with the zip, or later in the same chat." in out["done"]
    assert ["Copied: paste it into the chat with the zip.", "success", ""] in out["toasts"]
    assert "Bunlardan birini zip'le birlikte ya da sonra aynı sohbette gönder." in out["doneTr"]


KIT_PAGE = STAND_IN + r"""
require(%(practice)s);
require(%(kit)s);
(async () => {
  answers.quiz_import = { ok: true, kind: "quiz", id: "p1", title: "Bayes", questions: 3, read: 3, unused: 0, named: 3, found: 0, no_source: 0, cut: true };
  St.kit.open("c", "{json}"); await tick(); await tick();
  out.quiz = shown.html;
  answers.quiz_import = { ok: true, kind: "cards", id: "p2", title: "x", cards: 2, read: 2, dupes: 0, named: 2, found: 0, no_source: 0, cut: true };
  St.closeModal(); St.kit.open("c", "{json}"); await tick(); await tick();
  out.cards = shown.html;
  answers.quiz_import = { ok: true, kind: "quiz", id: "p3", title: "Whole", questions: 10, read: 10, unused: 0, named: 10, found: 0, no_source: 0, cut: false };
  St.closeModal(); St.kit.open("c", "{json}"); await tick(); await tick();
  out.whole = shown.html;
  t.load({ lang: "tr", table: turkish });
  answers.quiz_import = { ok: true, kind: "quiz", id: "p1", title: "Bayes", questions: 3, read: 3, unused: 0, named: 3, found: 0, no_source: 0, cut: true };
  St.closeModal(); St.kit.open("c", "{json}"); await tick(); await tick();
  out.tr = shown.html;
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})();
"""


@node
def test_a_paste_that_stops_part_way_says_what_to_do(tmp_path):
    out = _run(tmp_path, KIT_PAGE, {"practice_course": {"ok": False}}, practice="practice.js", kit="kit.js")
    assert "The answer stops part way, so the rest of the quiz is missing." in out["quiz"]
    assert "then press Undo here and paste the whole answer again." in out["quiz"]
    assert "the rest of the cards are missing" in out["cards"] and "the cards you have already are skipped" in out["cards"]
    assert "stops part way" not in out["whole"]
    assert "Cevap yarıda kesilmiş, sınavın geri kalanı eksik." in out["tr"]


@node
def test_the_lectures_buttons_stay_on_one_line(tmp_path):
    """In Turkish at 1024 px the lecture's buttons took two lines ("Metin dosyası" and "Klasör" alone on the second):
    when the row would wrap, the file buttons show their icons only, each with its name as a tooltip."""
    out = _page(tmp_path, LECTURE + """
  const added = [], removed = [];
  const row = (wrapped) => ({ innerHTML: "", firstElementChild: { offsetTop: 0 }, lastElementChild: { offsetTop: wrapped ? 36 : 0 },
    classList: { add: (c) => added.push(c), remove: (c) => removed.push(c), toggle() {}, contains: () => false } });
  els["#day-actions"] = row(true);
  St.renderView(); await tick();
  out.html = els["#view"].innerHTML;
  out.wrapped = added.slice();
  els["#day-actions"] = row(false); added.length = 0;
  St.renderView(); await tick();
  out.oneLine = { added: added.slice(), removed: removed.length };
""")
    html = out["html"]
    assert 'aria-label="Text file"' in html and '<span class="tb-lbl"> Text file</span>' in html
    assert 'title="Folder" aria-label="Folder"' in html and '<span class="tb-lbl"> Folder</span>' in html
    assert out["wrapped"] == ["tight"] and out["oneLine"]["added"] == [] and out["oneLine"]["removed"] >= 1
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    assert ".toolbar.tight .tb-lbl { display: none; }" in css


@node
def test_quiz_me_on_stressed_moments_says_who_picked_them(tmp_path):
    """With nothing ticked, "Quiz me on these" opened the dialog on "The moments you picked": the app had picked the
    ones that stand out."""
    out = _page(tmp_path, PRACTICE + """
  A["st-quiz"]({ dataset: { cid: "c1" } }); await tick();
  out.none = overlay();
  St.closeModal && St.closeModal();
  A["st-pick"]({ checked: true, dataset: { cid: "c1", id: "s1" } });
  A["st-quiz"]({ dataset: { cid: "c1" } }); await tick();
  out.ticked = overlay();
""")
    assert "What the lecturer stressed" in out["none"] and "The moments you picked" not in out["none"]
    assert "The moments you picked" in out["ticked"] and "1 moment the lecturer stressed" in out["ticked"]
