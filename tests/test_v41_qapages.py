"""4.1.0, deep QA round 2's page findings: a chat's unsent question stays with that chat (PG-1); Save PDF on a study
pack ends in .pdf (PG-2); the app's menus work with the keyboard (PG-3); another quiz keeps the answers of one in
progress (PG-4); AI labels in the page's language (PG-5); the focus comes back to the button a dialog was opened
with, also when the page was drawn again (PG-6); Settings → Storage never offers to remove a speech model IEUStudia
didn't download (the shared Hugging Face folder)."""

import json
import re
import shutil
from pathlib import Path

import pytest

from studia import i18n
from test_chat_page import run as run_chat

WEB = Path(i18n.__file__).parent / "web"
node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


# ------------------------------------------------------------------ PG-1: a chat's unsent question stays with it
@node
def test_a_half_typed_question_stays_with_its_chat(tmp_path):
    out = run_chat(tmp_path, """
  S.system = S.system || {}; S.system.ai = { available: true };
  const main = { innerHTML: "" }; els["#chat-main"] = main;
  const box = () => { const m = /<textarea id="chat-input"[^>]*>([\\s\\S]*?)<\\/textarea>/.exec(main.innerHTML); return m ? m[1] : null; };
  const type = (v) => listeners.input.forEach((f) => f({ target: { id: "chat-input", value: v, dataset: {}, style: {} } }));
  const start = (cid, code, day) => St.startDraft({ course_id: cid, course_code: code, days: [day], focus: "full", slides: false,
    model: "sonnet", labels: [{ key: day, n: 1, label: "Thu 6 Nov", kind: "Lecture" }] });
  start("EEE215", "EEE 215", "EEE215|2026-11-05|lecture"); await tick();
  await A["chat-suggest"]({ dataset: { text: "first EEE question" } }); await tick();
  const a = S.chat.id;
  start("MATH250", "MATH 250", "MATH250|2026-11-06|lecture"); await tick();
  await A["chat-suggest"]({ dataset: { text: "first MATH question" } }); await tick();
  const b = S.chat.id;
  A["chat-open"]({ dataset: { id: a } }); await tick();
  type("Half-typed EEE 215 question about phasors");
  A["chat-open"]({ dataset: { id: b } }); await tick();
  out.inB = box();
  S.day = { day_key: "MATH250|2026-11-06|lecture", course: { id: "MATH250", code: "MATH 250" }, kind: "lecture", date_label: "Thu 6 Nov", marks: [] };
  A["ask-day"](); await tick();
  out.newChat = box();
  draft(); await tick();                                 // a new chat of another course, its question typed
  type("about the new lecture");
  A["draft-change"](); await tick(); window.__cp.go(); await tick();          // its lectures changed: same chat, same question
  out.changed = box();
  St.go({ name: "chat", chatId: a }); await tick();
  out.backInA = box();
  // sent while the student went to another chat: the question doesn't come back in A's box
  const send = St.Mock.chat_send; let release;
  St.Mock.chat_send = (...x) => new Promise((r) => { release = () => r(send(...x)); });
  S.chat.data.running = false;                           // (the stand-in's first answer never ends here: no timers)
  const sending = A["chat-suggest"]({ dataset: { text: "Half-typed EEE 215 question about phasors" } }); await tick();
  A["chat-open"]({ dataset: { id: b } }); await tick();
  release(); await sending; await tick();
  A["chat-open"]({ dataset: { id: a } }); await tick();
  out.afterSend = box();
""")
    assert out["inB"] == "" and out["newChat"] == ""
    assert out["changed"] == "about the new lecture"
    assert out["backInA"] == "Half-typed EEE 215 question about phasors"
    assert out["afterSend"] == ""


# ------------------------------------------------------------------ PG-2: Save PDF on a study pack ends in .pdf
def test_save_pdf_on_a_study_pack_ends_in_pdf_when_a_name_is_typed(tmp_path, monkeypatch):
    from webview.util import parse_file_type

    from studia import studypack
    from studia.api import Api
    from studia.domain import Course
    from studia.services import Services

    a = Api(Services())
    a._push = lambda e: None
    a._s.lib.upsert_courses([Course(id="c", title="2026-1 EEE 215 C 1", code="EEE 215", name="Circuits", term="T")])
    pid = a._s.plans.create("c", {"kind": "cheatsheet"})
    pdf = tmp_path / "packs" / "2026-11-09 - DC Circuit Analysis cheatsheet.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"%PDF-1.7 sheet")
    monkeypatch.setattr(studypack, "make_pdf", lambda s, plan: pdf)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    seen, typed = [], {}

    class Window:                      # the save dialog: the student types a name over the one suggested
        def create_file_dialog(self, kind, directory="", save_filename="", file_types=()):
            for f in file_types:
                parse_file_type(f)
            seen.append((directory, save_filename, file_types))
            return (typed["path"],) if typed["path"] else None

    a._window = Window()
    (tmp_path / "out").mkdir()
    for name, saved in (("EEE 215 formulas", "EEE 215 formulas.pdf"), ("Ch. 3 notes", "Ch. 3 notes.pdf"),
                        ("sheet.PDF", "sheet.PDF")):
        typed["path"] = str(tmp_path / "out" / name)
        r = a.pack_pdf(pid, "save")
        assert r == {"ok": True, "saved": str(tmp_path / "out" / saved)}
        assert (tmp_path / "out" / saved).read_bytes() == b"%PDF-1.7 sheet"
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["Ch. 3 notes.pdf", "EEE 215 formulas.pdf", "sheet.PDF"]
    assert seen[-1] == (str(tmp_path / "Downloads"), pdf.name, ("PDF document (*.pdf)",))       # Downloads, as before
    typed["path"] = str(pdf)                                   # its own file, the name kept: nothing to copy
    assert a.pack_pdf(pid, "save") == {"ok": True, "saved": str(pdf)} and pdf.read_bytes() == b"%PDF-1.7 sheet"
    typed["path"] = ""                                         # cancelled
    assert a.pack_pdf(pid, "save") == {"ok": True, "saved": ""}
    a._s.plans.shutdown()
