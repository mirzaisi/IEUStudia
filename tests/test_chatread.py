"""Sending all of a chat's chosen slides and files: each AI's own limits (chatlimits), "Read everything" in batches
(chatread: limits respected, every page read exactly once, notes kept per file and kind of question, Stop and errors
go on from where they were), what each AI's requests look like, and what the AI and the student are told."""

import dataclasses as dc
import json
import shutil
import threading
import types

import pytest

from studia import ai, appconfig, chatfiles, chatgpt, chatinputs, chatlimits, chatread, chatslides
from studia.cancel import Cancelled
from test_chat import DAY1, ask, env, final, wait_for  # noqa: F401 - the chat fixture (fake Claude Code)
from test_chatfiles import pdf, send


# ---------------------------------------------------------------------------------------------------- helpers
def page(i, file="f", text="", pic=True, tokens=1000, size=100_000, group="course"):
    return chatread.Page(label=f"[{file}.pdf p.{i}]", group=group, file=file, name=f"{file}.pdf", page=i, text=text,
                         picture=f"/nowhere/{file}-{i}.jpg" if pic else None, tokens=tokens if pic else 0,
                         bytes=size if pic else 0,
                         source=f"/nowhere/{file}.pdf")


def lim(**kw):
    base = chatlimits.Limits("claude", "sonnet", 1_000_000, 850_000, 500, True, 30 * 2 ** 20, batch_tokens=120_000,
                             batch_pictures=60)
    return dc.replace(base, **kw)


class Reader:
    """A stand-in for the AI reading a batch: notes under every label it was given (or all but ``skip``)."""

    def __init__(self, skip=(), fail_at=0, stop_at=0, on_stop=None):
        self.calls, self.skip, self.fail_at, self.stop_at, self.on_stop = [], set(skip), fail_at, stop_at, on_stop

    def __call__(self, system, content, cancel):
        labels = content[-1]["text"].split("each to be noted under its label: ")[1].split(", ")
        self.calls.append({"labels": labels, "images": sum(b["type"] == "image" for b in content), "system": system})
        n = len(self.calls)
        if n == self.fail_at:
            raise RuntimeError("ChatGPT had a problem on its side. Try again in a few minutes.")
        if n == self.stop_at and self.on_stop:
            self.on_stop()
        text = "\n\n".join(f"### {x}\nNotes of {x}: $E = mc^2$, a capacitor diagram." for x in labels if x not in self.skip)
        return {"text": text, "usage": {"input_tokens": 1000, "output_tokens": 100}}


@pytest.fixture
def notes_home(tmp_path, monkeypatch):
    from studia import paths

    home = paths.app_dir() / "reads-test"         # the tests' own app folder (conftest), a corner of it for this test
    (home / "cache").mkdir(parents=True, exist_ok=True)
    return home


# ---------------------------------------------------------------------------------------------------- batches
def test_batches_respect_the_limits_and_cover_every_page_exactly_once():
    pages = [page(i, tokens=900 + 300 * (i % 4), size=80_000 + 10_000 * (i % 5), text="x" * (i * 37 % 4000))
             for i in range(1, 140)] + [page(i, "notes", text="y" * 2500, pic=False) for i in range(1, 30)]
    one = lim(batch_tokens=40_000, batch_pictures=12, request_bytes=900_000)
    out = chatread.batches(pages, one, pictures=True, per=3.1)
    assert [p.key for b in out for p in b] == [p.key for p in pages]          # in order, each once
    for b in out:
        pics = [p for p in b if p.picture is not None]
        assert len(pics) <= 12 and sum(p.bytes for p in pics) <= 900_000 and len(b) <= chatread.BATCH_PAGES
        cost = sum(chatread.page_text_tokens(p, 3.1) + p.tokens + chatread.note_max(3.1) for p in b)
        assert cost <= 40_000 or len(b) == 1
    # an AI that can't see pictures (the offline model): text only, short batches
    local = lim(provider="local", pictures=0, batch_pictures=0, batch_tokens=1500, request_bytes=0)
    text = chatread.batches(pages, local, pictures=False, per=3.1)
    assert [p.key for b in text for p in b] == [p.key for p in pages]
    assert all(len(b) <= chatread.LOCAL_BATCH_PAGES for b in text)


def test_notes_are_parsed_by_label_and_kept_short():
    text = ("### [a.pdf p.1]\nOhm's law $V = IR$.\n\n## [A.PDF  p.2]:\nA circuit with two resistors.\n"
            "### [elsewhere p.9]\nnot asked\n### [a.pdf p.3]\n" + "long " * 1000)
    got = chatread.parse(text, ["[a.pdf p.1]", "[a.pdf p.2]", "[a.pdf p.3]", "[a.pdf p.4]"])
    assert got["[a.pdf p.1]"] == "Ohm's law $V = IR$." and got["[a.pdf p.2]"] == "A circuit with two resistors."
    assert len(got["[a.pdf p.3]"]) <= chatread.NOTE_CHARS + 2 and "[a.pdf p.4]" not in got and len(got) == 3


def test_notes_are_counted_at_their_real_size_and_shortened_to_the_room_left():
    assert chatread.ranges([1, 2, 3, 5, 7, 8]) == "p.1–3, 5, 7–8"
    # a dense page with a figure: its notes can take all NOTE_CHARS (about 390 tokens), and the budget says so
    dense = page(1, text="Thevenin equivalent " * 80)
    assert chatread.note_cost(dense, 3.1) == chatread.note_max(3.1) >= chatread.NOTE_CHARS / 3.1
    assert chatread.note_cost(page(2, text="", pic=False), 3.1) < 100              # a blank page: short notes
    # notes that don't fit the room left are shortened evenly, each page keeping its label
    pages = [page(i) for i in range(1, 41)]
    notes = {p.key: f"Page {p.page}: " + "dense notes with $V = IR$ " * 45 for p in pages}
    whole = ai.text_tokens(chatread.notes_text(pages, notes), 3.1)
    assert whole > 12_000
    fitted, short = chatread.fit_notes(pages, notes, 6_000, 3.1)
    text = chatread.notes_text(pages, fitted)
    assert short and ai.text_tokens(text, 3.1) <= 6_000 and all(p.label in text for p in pages)
    assert all(fitted[p.key].startswith(f"Page {p.page}: dense notes") for p in pages)
    assert chatread.fit_notes(pages, notes, whole + 10, 3.1) == (notes, False)


# ---------------------------------------------------------------------------------------------------- reading
def test_reading_keeps_one_set_of_notes_per_file_and_ai_and_goes_on_after_a_stop_or_an_error(notes_home, monkeypatch):
    monkeypatch.setattr(ai, "image_block", lambda p: {"type": "image", "source": {"data": ""}})
    pages = [page(i, "w4", text=f"page {i} text") for i in range(1, 8)] + [page(i, "w5") for i in range(1, 4)]
    one = lim(batch_pictures=3)
    seen = []
    first_reader = Reader()
    r = chatread.read(pages, "What is a capacitor?", one, first_reader, progress=seen.append)
    assert r["read"] == 10 and r["total"] == 4 and r["unread"] == [] and len(r["notes"]) == 10
    assert [(s["done"], s["total"]) for s in seen] == [(0, 4), (1, 4), (2, 4), (3, 4), (4, 4)]
    # one set of notes for every question: worked examples, formulas and what the page stresses for exams
    system = first_reader.calls[0]["system"]
    assert "every later question" in system and "worked example" in system and "⭐" in system
    # read again (a follow-up of any kind, a new chat about the same files, Try again): nothing is asked
    for question in ("Another question", "Is it on the exam?", "Give an example of impedance", "Örneğin bir devre?"):
        again = Reader()
        assert chatread.read(pages, question, one, again)["cached"] == 10 and again.calls == []
    # another AI reads them itself
    other = Reader()
    chatread.read(pages, "q", lim(provider="chatgpt", model=""), other)
    assert sum(len(c["labels"]) for c in other.calls) == 10
    # stopped after the first batch: what was read is kept, the next run reads only the rest
    stop = threading.Event()
    first = Reader(stop_at=1, on_stop=stop.set)
    sonnet = lim(model="opus", batch_pictures=3)
    with pytest.raises(Cancelled):
        chatread.read(pages, "Solve it", sonnet, first, cancel=stop)
    assert len(first.calls) == 1
    rest = Reader()
    chatread.read(pages, "Solve it", sonnet, rest)
    assert sorted(x for c in rest.calls for x in c["labels"]) == sorted(p.label for p in pages[3:])
    # an error on the AI's side: the same
    broken = Reader(fail_at=2)
    codex = lim(provider="codex", model="", batch_pictures=3)
    with pytest.raises(RuntimeError, match="on its side"):
        chatread.read(pages, "q", codex, broken)
    after = Reader()
    chatread.read(pages, "q", codex, after)
    assert sum(len(c["labels"]) for c in after.calls) == 10 - len(broken.calls[0]["labels"])
    # the notes of a student's own file are kept with it (they go when it is removed)
    mine = [dc.replace(page(1, "hw"), group="upload", notes_dir=str(notes_home / "box" / "notes"))]
    chatread.read(mine, "q", one, Reader())
    assert list((notes_home / "box" / "notes").glob("*.json"))


def test_pages_left_out_of_an_answer_are_asked_again_once_then_named(notes_home, monkeypatch):
    monkeypatch.setattr(ai, "image_block", lambda p: {"type": "image", "source": {"data": ""}})
    pages = [page(i) for i in range(1, 6)]
    stubborn = Reader(skip={"[f.pdf p.4]"})
    r = chatread.read(pages, "q", lim(), stubborn)
    assert [c["labels"] for c in stubborn.calls] == [[p.label for p in pages], ["[f.pdf p.4]"]]
    assert r["unread"] == ["[f.pdf p.4]"] and r["read"] == 4
    text = chatread.notes_text(pages, r["notes"])
    assert text.startswith("=== NOTES ON THE PAGES YOU DON'T SEE IN FULL ===") and "## f.pdf" in text
    assert "[f.pdf p.4]" not in text and text.count("Notes of [f.pdf p.") == 4


# ---------------------------------------------------------------------------------------------------- in a chat
def course_files(env, files):
    """Slide files of the course on IEULearn, downloaded: {name: [page texts]} (each a 4:3 PDF, not a real one)."""
    res = [types.SimpleNamespace(id=f"m{i}", title=name, file_name=f"{name}.pdf", kind="resource", ext="pdf",
                                 url=f"https://l/{i}", download_url=f"https://l/{i}?dl") for i, name in enumerate(files)]
    env.lib.merge_units("c", [types.SimpleNamespace(id="u1", position=1, title="Slides", start=None, end=None,
                                                    readings="", resources=res)], first_sync=True)
    for r, texts in zip(res, files.values()):
        f = env.tmp / r.file_name
        f.write_bytes(b"%PDF-1.4")
        env.lib.set_material_file(r.id, str(f), 8, "u")
        env.lib.set_material_text(r.id, texts)
    return [r.id for r in res]


@pytest.fixture
def reading(env, notes_home, monkeypatch):
    """A chat whose AI takes 4 pictures per request (3 per reading batch), with a stand-in reader."""
    log = env.tmp / "claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    real = chatlimits.of
    monkeypatch.setattr(chatlimits, "of", lambda p="", m="": dc.replace(real(p, m), pictures=4, batch_pictures=3))
    monkeypatch.setattr(ai, "image_block", lambda p: {"type": "image", "source": {"type": "base64",
                                                                                  "media_type": "image/jpeg", "data": ""}})
    monkeypatch.setattr(chatslides.PagePicture, "path", lambda self: env.tmp / f"page-{self.page}.jpg")
    reader = Reader()
    monkeypatch.setattr(chatread, "default_request", lambda lim, model: reader)
    # pages without text (figures) go as pictures first, then those with text in order: 4 of the 7
    ids = course_files(env, {"Week04": ["Bayes theorem posterior " * 20, "", "capacitor charging curve " * 15],
                             "Week05": ["random variables " * 20, "", "", "expected value " * 25]})
    return types.SimpleNamespace(log=log, reader=reader, ids=ids,
                                 sent=lambda: [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()])


def test_a_chat_reads_everything_first_with_progress_and_tells_the_ai_what_it_got(env, reading):
    chat = env.svc.create("c", [DAY1], send={"mode": "pick", "files": reading.ids}, read_all=True)
    assert chat["read_all"] is True and chat["read"]["on"] and chat["read"]["pages"] == 3
    assert chat["files"]["page_images"] == 4 and chat["read"]["batches"] == 1
    system = env.svc.context("c", [DAY1], send={"mode": "pick", "files": reading.ids}, model="sonnet",
                             read_all=True)["system"]
    assert "=== WHAT YOU GOT OF THE CHOSEN FILES ===" in system
    assert "- Week04.pdf (3 pages): the text of every page; pictures of p.1–2;" in system
    assert "read for you as notes (they come with each question)" in system and "NOTES ON THE PAGES" in system
    e = ask(env, chat["id"], "What does the capacitor charging curve look like?")
    assert e["state"] == "done" and e["text"].startswith("Answer 1 (images 4")
    assert [c["labels"] for c in reading.reader.calls] == [["[Week04.pdf p.3]", "[Week05.pdf p.1]", "[Week05.pdf p.4]"]]
    assert reading.reader.calls[0]["images"] == 3
    progress = [x["usage"]["reading"] for x in env.events if x.get("seq") == e["seq"] and "reading" in (x.get("usage") or {})]
    assert progress[0]["done"] == 0 and progress[0]["total"] == 1 and progress[-1]["read"] == 3
    assert e["usage"]["read"]["read"] == 3 and e["usage"]["read"]["pages"] == 3 and not e["usage"]["read"]["unread"]
    first = reading.sent()[0]
    notes = [b["text"] for b in first if b["type"] == "text" and "NOTES ON THE PAGES" in b["text"]]
    assert len(notes) == 1 and "[Week05.pdf p.4]\nNotes of [Week05.pdf p.4]" in notes[0]
    assert all("_turn" not in b for b in first)                               # the app's own marks never go
    # follow-ups of any kind (an exam, an example, a problem; English or Turkish): nothing read again, and the notes,
    # in Claude's conversation already, are not sent again (one set serves every question)
    for n, q in enumerate(("And the curve's time constant?", "Will the capacitor be on the exam?",
                           "Give an example of a charging problem", "Örneğin finalde bu çıkar mı?"), start=1):
        e2 = ask(env, chat["id"], q)
        assert len(reading.reader.calls) == 1 and e2["usage"]["read"]["again"]
        assert not any("NOTES ON THE PAGES" in b.get("text", "") for b in reading.sent()[n])
    # a new process (after a break): the notes go again, from what was kept
    env.svc._live[chat["id"]].terminate()
    ask(env, chat["id"], "What was the curve again?")
    assert len(reading.reader.calls) == 1 and any("NOTES ON THE PAGES" in b.get("text", "") for b in reading.sent()[5])
    # "What this chat sees" says how each file goes
    seen = chatinputs.inputs(env.svc, {"chat_id": chat["id"]})
    assert seen["read"]["on"] and [(f["pictures"], f["read"]) for f in seen["files"]["files"]] == [(2, 1), (2, 2)]


def test_read_everything_is_on_for_the_whole_course_or_many_files_and_can_be_turned_off(env, reading):
    many = course_files(env, {f"W{i}": ["text"] for i in range(5)})
    chat = env.svc.create("c", [DAY1], send={"mode": "pick", "files": many})
    assert chat["read_all"] is None and chat["read"]["on"] and chat["read"]["auto"]
    few = env.svc.create("c", [DAY1], send={"mode": "pick", "files": many[:2]})
    assert not few["read"]["on"]
    course = env.svc.create("c", [DAY1], focus="course", send={"mode": "pick", "files": many[:1]})
    assert course["read"]["on"]
    off = env.svc.update(chat["id"], {"read_all": False})
    assert off["read_all"] is False and not off["read"]["on"]
    e = ask(env, chat["id"], "q")
    assert reading.reader.calls == [] and "read" not in e["usage"]
    assert any(n["code"] == "pictures_left" for n in e["usage"]["notices"])     # said, with what would read them
    assert env.svc.update(chat["id"], {"read_all": None})["read"]["auto"]
    appconfig.update({"ai_provider": "local"})                                    # slow there: the student turns it on
    assert not env.svc.context("c", [DAY1], send={"mode": "pick", "files": many})["read"]["on"]


def test_stop_while_reading_keeps_what_was_read_and_the_next_question_goes_on(env, reading, monkeypatch):
    chat = env.svc.create("c", [DAY1], send={"mode": "pick", "files": reading.ids}, read_all=True)
    real = chatlimits.of
    monkeypatch.setattr(chatlimits, "of", lambda p="", m="": dc.replace(real(p, m), pictures=4, batch_pictures=1))
    reading.reader.stop_at, reading.reader.on_stop = 1, lambda: env.svc.stop(chat["id"])
    e = ask(env, chat["id"], "What is on page 3?")
    assert e["state"] == "stopped" and e["usage"]["read"]["stopped"] and e["usage"]["read"]["done"] == 1
    assert len(reading.reader.calls) == 1 and not reading.log.exists()           # Claude got nothing
    reading.reader.stop_at = 0
    e = ask(env, chat["id"], "What is on page 3?")
    assert e["state"] == "done" and len(reading.reader.calls) == 3 and e["usage"]["read"]["cached"] == 1
    # an error while reading (the notes kept are gone, a new process reads them again): said, and Try again goes on
    # from there
    from studia.paths import app_dir

    shutil.rmtree(app_dir() / "cache" / "reads")
    env.svc._live[chat["id"]].terminate()
    reading.reader.fail_at = 4
    failed = ask(env, chat["id"], "Is page 3 on the exam?")
    assert failed["state"] == "error" and "on its side" in failed["error"] and failed["usage"]["read"]["failed"]
    reading.reader.fail_at = 0
    res = env.svc.retry(chat["id"], failed["seq"])
    assert wait_for(lambda: (final(env.events, chat["id"], res["seq"]) or {}).get("state") == "done")
    done = final(env.events, chat["id"], res["seq"])
    assert done["state"] == "done" and done["usage"]["read"]["cached"] == 0, done
    assert len(reading.reader.calls) == 7                                        # the 3 pages read again, once


def test_pages_that_still_cant_be_read_are_told(env, reading):
    reading.reader.skip = {"[Week05.pdf p.4]"}
    chat = env.svc.create("c", [DAY1], send={"mode": "pick", "files": reading.ids}, read_all=True)
    e = ask(env, chat["id"], "q")
    (n,) = [x for x in e["usage"]["notices"] if x["code"] == "pages_not_read"]
    assert n["n"] == 1 and n["labels"] == ["[Week05.pdf p.4]"]


# ---------------------------------------------------------------------------------------------------- each AI's requests
def test_chatgpt_gets_the_notes_and_the_pages_that_matter_with_each_question(env, notes_home, monkeypatch):
    appconfig.update({"ai_provider": "chatgpt"})
    real = chatlimits.of
    monkeypatch.setattr(chatlimits, "of", lambda p="", m="": dc.replace(real(p, m), pictures=3, batch_pictures=3))
    monkeypatch.setattr(ai, "image_block", lambda p: {"type": "image", "source": {"type": "base64",
                                                                                  "media_type": "image/jpeg", "data": "x"}})
    monkeypatch.setattr(chatslides.PagePicture, "path", lambda self: env.tmp / f"page-{self.page}.jpg")
    reader = Reader()
    monkeypatch.setattr(chatread, "default_request", lambda lim, model: reader)
    sent = []
    monkeypatch.setattr(ai, "_chatgpt_one_shot", lambda system, content, *a, **k: sent.append(content)
                        or {"text": f"Answer {len(sent)}", "usage": {}})
    ids = course_files(env, {"Week04": ["", "", "capacitor charging curve", "inductor"]})
    chat = env.svc.create("c", [DAY1], send={"mode": "pick", "files": ids}, read_all=True)
    ask(env, chat["id"], "Explain the capacitor charging curve")
    ask(env, chat["id"], "And the capacitor again?")
    assert len(reader.calls) == 1                                                # kept: read once
    for content in sent:                                  # each request is whole: the notes go every time
        assert sum(b["type"] == "image" for b in content) == 3                   # never more than the AI takes
        assert any("NOTES ON THE PAGES" in b.get("text", "") for b in content)
        assert all("_turn" not in b for b in content)
    # the Responses API's shapes: a PDF as a file, never dropped
    parts = chatgpt._parts([{"type": "text", "text": "hi"}, ai.pdf_block(env.tmp / "Week04.pdf"),
                            {"type": "image", "source": {"media_type": "image/jpeg", "data": "eA=="}}])
    assert [p["type"] for p in parts] == ["input_text", "input_file", "input_image"]
    assert parts[1]["filename"] == "Week04.pdf" and parts[1]["file_data"].startswith("data:application/pdf;base64,")


def test_codex_takes_its_pictures_by_name_in_its_folder(env, monkeypatch, tmp_path):
    monkeypatch.setattr(ai, "codex_cli_info", lambda: {"available": True, "path": "codex.exe", "version": "0.156.1"})
    monkeypatch.setattr(ai, "_codex_model", lambda: "")
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "eA=="}}] * 600
    prompt, images = ai._codex_text("sys", content + [{"type": "text", "text": "q"}], tmp_path)
    cmd = ai._codex_command(tmp_path, "sonnet", "low", images)
    assert cmd.count("-i") == 600 and "slide-599.jpg" in cmd and str(tmp_path) not in cmd[cmd.index("-i"):]
    assert sum(len(x) + 3 for x in cmd) < 32_767                                 # Windows' longest command line


def test_the_offline_model_reads_the_files_text_in_short_batches_and_gets_the_notes_that_match(env, notes_home,
                                                                                             monkeypatch):
    from studia import local_llm

    appconfig.update({"ai_provider": "local"})
    monkeypatch.setattr(local_llm, "budget", lambda key: 2000)
    reader = Reader()
    monkeypatch.setattr(chatread, "default_request", lambda lim, model: reader)
    seen = []
    monkeypatch.setattr(local_llm, "generate", lambda msgs, key, *a, **k: seen.append(msgs)
                        or {"text": "ok", "usage": {"input_tokens": 1, "output_tokens": 1}})
    ids = course_files(env, {"Week04": [f"page {i} about capacitors and their charging" * 3 for i in range(12)]})
    chat = env.svc.create("c", [DAY1], send={"mode": "pick", "files": ids}, read_all=True)
    assert chat["read"]["on"] and chat["read"]["pages"] == 12 and chat["read"]["pictures"] == 0
    ask(env, chat["id"], "How do capacitors charge?")
    assert all(c["images"] == 0 and len(c["labels"]) <= chatread.LOCAL_BATCH_PAGES for c in reader.calls)
    assert sum(len(c["labels"]) for c in reader.calls) == 12
    assert "NOTES ON THE COURSE FILES" in seen[0][0]["content"]


def test_claude_gets_a_pdf_whole_when_its_pictures_dont_fit_but_the_file_does(env, notes_home, monkeypatch):
    log = env.tmp / "claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    real = chatlimits.of
    # a request of 200 KB with its text: the PDF's two page pictures (about 250 KB) don't fit, the file (1 KB) does
    monkeypatch.setattr(chatlimits, "CONVERSATION_BYTES", 0)
    monkeypatch.setattr(chatlimits, "of", lambda p="", m="": dc.replace(real(p, m), request_bytes=200_000))
    chat = env.svc.create("c", [DAY1])
    spec = {"chat_id": chat["id"]}
    send(env, spec, "hw.pdf", pdf(["Question 1: the posterior " * 8, ""]))
    ctx = env.svc.context("c", [DAY1], uploads=chat["id"], model="sonnet")
    (label, whole), = ctx["images"]
    assert isinstance(whole, chatslides.PdfFile) and whole.pages == 2 and label == "[hw.pdf] (the whole PDF, 2 pages)"
    assert "- hw.pdf (2 pages): the text of every page; the whole PDF as a file" in ctx["system"]
    assert chatinputs.inputs(env.svc, spec)["uploads"][0]["native"]
    ask(env, chat["id"], "Check my homework")
    blocks = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert [b["type"] for b in blocks if b["type"] != "text"] == ["document"] and blocks[-2]["title"] == "hw.pdf"
    # ChatGPT's file input wasn't tested on the sign-in endpoint: its pages go as pictures
    appconfig.update({"ai_provider": "chatgpt"})
    assert not any(isinstance(p, chatslides.PdfFile) for _, p in env.svc.context("c", [DAY1], uploads=chat["id"])["images"])


# ---------------------------------------------------------------------------------------------------- each AI's limits
def test_each_ai_s_limits(tmp_path, monkeypatch):
    haiku, sonnet = chatlimits.of("claude", "haiku"), chatlimits.of("claude", "opus")
    assert (haiku.window, haiku.pictures, haiku.context) == (200_000, 80, 160_000)      # Claude Code keeps the last 80
    assert (sonnet.window, sonnet.pictures, sonnet.context, sonnet.pdf) == (1_000_000, 500, 850_000, True)
    home = tmp_path / "codex"
    home.mkdir()
    (home / "models_cache.json").write_text(json.dumps({"models": [
        {"slug": "gpt-6.1-sol", "visibility": "list", "context_window": 500_000, "effective_context_window_percent": 95},
        {"slug": "gpt-6-sol", "visibility": "list", "context_window": 272_000}]}), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    appconfig.update({"codex_model": ""})
    assert chatlimits.of("codex").window == 258_400                           # the default: the smallest listed
    appconfig.update({"codex_model": "gpt-6.1-sol"})
    # not answered with yet: Codex may turn it down and ask its default with the same request, so that must fit too
    assert chatlimits.of("codex").window == 258_400
    ai._codex_accept("gpt-6.1-sol")
    codex = chatlimits.of("codex")
    assert codex.window == 475_000 and codex.context == 380_000 and not codex.pdf and codex.pictures == 500
    appconfig.update({"codex_model": ""})
    assert chatlimits.of("local").pictures == 0
    # ChatGPT (measured on its sign-in endpoint): 400 pictures read in one request; its file input gives the model a
    # PDF's text only, so PDFs go as page pictures; 16 MB of pictures, which go up again with every question
    gpt = chatlimits.of("chatgpt")
    assert (gpt.pictures, gpt.pdf, gpt.request_bytes) == (400, False, 16 * 2 ** 20)
    assert chatlimits.of("claude", "sonnet").request_bytes <= 28 * 2 ** 20 < chatlimits.CLAUDE_REQUEST


# ---------------------------------------------------------------------------------------------------- one request's weight
FILES = {"Week04": ["Bayes theorem posterior " * 20, "", "capacitor charging curve " * 15],
         "Week05": ["random variables " * 20, "", "", "expected value " * 25]}


def weighed(env, monkeypatch, limit, picture_bytes):
    """A Claude chat whose request may weigh ``limit`` bytes (no room kept for the conversation, to see the edge), whose
    pictures weigh ``picture_bytes`` each as sent (the app counts 127 KB for one), and a log of what Claude got."""
    log = env.tmp / "claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setattr(chatlimits, "CONVERSATION_BYTES", 0)
    real = chatlimits.of
    monkeypatch.setattr(chatlimits, "of", lambda p="", m="": dc.replace(real(p, m), pictures=40, request_bytes=limit))
    size = {"n": picture_bytes}
    monkeypatch.setattr(ai, "image_block", lambda p: {"type": "image", "source": {
        "type": "base64", "media_type": "image/jpeg", "data": "x" * size["n"]}})
    monkeypatch.setattr(chatslides.PagePicture, "path", lambda self: env.tmp / f"page-{self.page}.jpg")
    reader = Reader()
    monkeypatch.setattr(chatread, "default_request", lambda lim, model: reader)
    ids = course_files(env, FILES)

    def conversation(chat_id):
        """What Claude Code's next request carries: the instructions, every message and every answer so far."""
        system = env.svc.context("c", [DAY1], send={"mode": "pick", "files": ids}, model="sonnet", read_all=True)["system"]
        msgs = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
        answers = [m["text"] for m in env.lib.chat_messages(chat_id) if m["role"] == "assistant"]
        return (chatlimits.text_bytes(system)
                + sum(b["size"] + 120 if "size" in b else chatlimits.text_bytes(b.get("text", "")) + 40
                      for m in msgs for b in m)
                + sum(chatlimits.text_bytes(a) for a in answers)), msgs
    return types.SimpleNamespace(ids=ids, reader=reader, size=size, conversation=conversation)


def test_claude_s_whole_conversation_stays_within_one_request_s_size(env, notes_home, monkeypatch):
    """Claude Code sends the whole conversation with every question: the instructions, the first message's pictures,
    the notes and the pictures added with later questions all count against one request's size (Anthropic: 32 MB),
    by their real weight; what would go over is left out, and the student is told."""
    limit = 860_000
    w = weighed(env, monkeypatch, limit, 140_000)          # heavier than counted: 6 fit the estimate, and as sent
    chat = env.svc.create("c", [DAY1], send={"mode": "pick", "files": w.ids}, read_all=True)
    assert chat["files"]["page_images"] == 6 and chat["read"]["pages"] == 1
    e = ask(env, chat["id"], "What is the expected value?")             # its page matters, but no room is left
    assert e["state"] == "done" and e["usage"]["read"]["relevant"] == 0 and e["usage"]["read"]["full"] == 1
    assert [n["code"] for n in e["usage"]["notices"]] == ["pictures_full"]
    for q in ("And the variance?", "Explain the expected value again"):
        ask(env, chat["id"], q)
    weight, msgs = w.conversation(chat["id"])
    assert weight <= limit and sum(b["type"] == "image" for m in msgs for b in m) == 6
    # pictures much heavier than counted: the first message keeps what fits, the rest goes as text only (said)
    w.size["n"] = 300_000
    other = env.svc.create("c", [DAY1], send={"mode": "pick", "files": w.ids}, read_all=True, model="opus")
    e = ask(env, other["id"], "Hello")
    (full,) = [n for n in e["usage"]["notices"] if n["code"] == "request_full"]
    log = [json.loads(x) for x in (env.tmp / "claude.log").read_text(encoding="utf-8").splitlines()]
    sent = sum(b["type"] == "image" for b in log[-1])
    assert sent == 2 and full["n"] == 6 - sent and sum(b.get("size", 0) for b in log[-1]) <= limit


def test_pictures_left_out_for_their_weight_are_named_to_the_ai_and_read_with_read_everything(env, notes_home,
                                                                                             monkeypatch):
    """The AI's list of what it got (built before the pictures' real weight is known) says "pictures of" the pages
    that then don't fit: the first message corrects it, and "Read everything" reads those pages like the rest."""
    from studia import chat as chat_mod

    w = weighed(env, monkeypatch, 860_000, 300_000)
    log = env.tmp / "claude.log"
    chat = env.svc.create("c", [DAY1], send={"mode": "pick", "files": w.ids}, read_all=True, model="opus")
    assert chat["files"]["page_images"] == 6 and chat["read"]["pages"] == 1
    e = ask(env, chat["id"], "Hello")
    (full,) = [n for n in e["usage"]["notices"] if n["code"] == "request_full"]
    assert full["n"] == 4 and full["read"] is True
    first = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()][-1]
    assert sum(b["type"] == "image" for b in first) == 2
    (note,) = [b["text"] for b in first if b["type"] == "text" and b["text"].startswith("Correction to WHAT YOU GOT")]
    # (the fake Claude Code reads its input in the console's code page: "p.1–3" comes back mangled)
    assert note.startswith("Correction to WHAT YOU GOT OF THE CHOSEN FILES: this message could not carry the pictures "
                           "of Week04.pdf p.3; Week05.pdf p.1")
    assert note.endswith("3, so you have only their text. They are read for you as notes (they come with each question).")
    assert chat_mod.text_only_note([page(3, "Week04"), page(1, "Week05"), page(2, "Week05"), page(3, "Week05")],
                                   True) == note.replace(note[note.index("p.1") + 3:note.index("3, so")], "–")
    # before the pictures: ChatGPT and Codex get the first message's part up to its last picture with every question
    at = next(i for i, b in enumerate(first) if b["type"] == "text" and b["text"].startswith("Correction to WHAT"))
    assert at < next(i for i, b in enumerate(first) if b["type"] == "image")
    read = [x for c in w.reader.calls for x in c["labels"]]
    assert sorted(read) == sorted(["[Week04.pdf p.3]", "[Week05.pdf p.1]", "[Week05.pdf p.2]", "[Week05.pdf p.3]",
                                   "[Week05.pdf p.4]"])           # the four left out, and the one that never fitted
    assert e["usage"]["read"]["pages"] == 5                    # and their notes went with the question
    assert any("Notes of [Week05.pdf p.2]" in (b.get("text") or "") for b in first)
    # "Read everything" off: the AI is told it has only their text
    off = env.svc.create("c", [DAY1], send={"mode": "pick", "files": w.ids}, read_all=False, model="opus")
    e = ask(env, off["id"], "Hello")
    (full,) = [n for n in e["usage"]["notices"] if n["code"] == "request_full"]
    assert "read" not in full
    first = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()][-1]
    assert any(b["type"] == "text" and b["text"].startswith("Correction to WHAT YOU GOT")
               and b["text"].endswith("say so plainly (a [!NOTE] block).") for b in first)


def test_a_model_codex_turns_down_while_reading_is_said_to_the_student_once(env, notes_home, monkeypatch):
    """Read everything's batches are usually Codex's first request: the refusal happens there (the batch is read again
    with Codex's default), so the chat's own question never sees it; the student is still told, once."""
    w = weighed(env, monkeypatch, 860_000, 10)
    first = []

    def reader(system, content, cancel):
        r = w.reader(system, content, cancel)
        if not first:
            first.append(1)
            return {**r, "model_refused": "gpt-6.1-sol"}
        return r

    monkeypatch.setattr(chatread, "default_request", lambda lim, model: reader)
    chat = env.svc.create("c", [DAY1], send={"mode": "pick", "files": w.ids}, read_all=True)
    e = ask(env, chat["id"], "Hello")
    assert e["state"] == "done" and len(w.reader.calls) == 1
    assert [(n["code"], n["model"]) for n in e["usage"]["notices"]] == [("codex_model", "gpt-6.1-sol")]
    assert "model_refused" not in e["usage"]["read"]
    e = ask(env, chat["id"], "And again?")
    assert not [n for n in e["usage"].get("notices") or [] if n["code"] == "codex_model"]


def test_a_refusal_whose_retry_was_stopped_is_still_told_once_per_chat(env, monkeypatch):
    """Codex turned the chosen model down, then the retry with its default was stopped (or failed): the refusal is
    remembered, so the next answer tells the student, once."""
    from studia.chat import ChatService

    monkeypatch.setattr(ai, "provider", lambda: "codex")
    monkeypatch.setattr(ai, "codex_rejected", lambda: {"gpt-6.1-sol"})
    appconfig.update({"codex_model": "gpt-6.1-sol"})
    got = ChatService._refused({}, [], [])
    assert [(n["code"], n["model"]) for n in got] == [("codex_model", "gpt-6.1-sol")]
    assert ChatService._refused({}, [{"usage": {"notices": got}}], []) == []          # said already in this chat
    monkeypatch.setattr(ai, "provider", lambda: "chatgpt")
    assert ChatService._refused({}, [], []) == []


def test_a_codex_chat_fits_codex_s_default_model_until_the_chosen_one_has_answered(env, tmp_path, monkeypatch):
    """A chat is sized before its first request: Codex may then turn the chosen model down and send the same request to
    its default model (a smaller window), so until the chosen model has answered once the smaller window counts."""
    home = tmp_path / "codex"
    home.mkdir()
    (home / "models_cache.json").write_text(json.dumps({"models": [
        {"slug": "gpt-6.1-sol", "visibility": "list", "context_window": 500_000},
        {"slug": "gpt-6-sol", "visibility": "list", "context_window": 272_000}]}), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(ai, "_rejected", set())
    appconfig.update({"ai_provider": "codex", "codex_model": "gpt-6.1-sol"})
    monkeypatch.setattr(ai, "codex_cli_info", lambda: {"available": True, "path": "codex.exe", "version": "0.156.1"})
    assert env.svc.context("c", [DAY1])["lim"].window == 258_400          # the default's (the smallest listed)
    refuse = {"on": True}

    def once(system, content, model, effort, cancel, on_text, timeout_s):
        if refuse["on"] and ai._codex_model() == "gpt-6.1-sol":
            raise ai._codex_failure("unexpected status 400 Bad Request: {\"detail\":\"The 'gpt-6.1-sol' model is not "
                                    "supported when using Codex with a ChatGPT account.\"}")
        return {"text": "Answer", "usage": {}, "model": ai._codex_model() or "Codex's default model"}

    monkeypatch.setattr(ai, "_codex_once", once)
    refuse["on"] = False                             # it answers with it: its own window from now on
    ai._codex_one_shot("s", [{"type": "text", "text": "q"}], "sonnet", "low", None, None, 60)
    assert ai.codex_accepted() == {"gpt-6.1-sol"} and env.svc.context("c", [DAY1])["lim"].window == 475_000
    # another account (or Codex changed its mind): turned down after all, its default's window again
    refuse["on"] = True
    res = ai._codex_one_shot("s", [{"type": "text", "text": "q"}], "sonnet", "low", None, None, 60)
    assert res["model_refused"] == "gpt-6.1-sol" and env.svc.context("c", [DAY1])["lim"].window == 258_400


def test_a_question_stopped_before_it_went_leaves_the_conversation_as_it_was(env, notes_home, monkeypatch):
    """Stop after the pages were read but before the question went (the matching passages are looked up in between):
    the pages that matter for it were not given to Claude, so the next question gives them."""
    from studia import chat as chat_mod

    w = weighed(env, monkeypatch, 860_000, 10)              # light pictures: the pages that matter fit
    chat = env.svc.create("c", [DAY1], send={"mode": "pick", "files": w.ids}, read_all=True)
    ask(env, chat["id"], "Hello")
    real = chat_mod.retrieve
    stopped = []

    def retrieve(*a, **k):
        if not stopped:
            stopped.append(1)
            env.svc.stop(chat["id"])
        return real(*a, **k)

    monkeypatch.setattr(chat_mod, "retrieve", retrieve)
    e = ask(env, chat["id"], "What is the expected value?")
    assert e["state"] == "stopped"
    session = env.svc._live[chat["id"]]                     # the process is kept: it got nothing of that question
    assert not session.pages_sent and session.extra_tokens == ai.text_tokens(
        chatread.notes_text(session.reading["pages"], chatread.cached(session.reading["pages"], session.reading["lim"])),
        ai.chars_per_token("claude"))
    e = ask(env, chat["id"], "What is the expected value?")
    assert e["state"] == "done" and e["usage"]["read"]["relevant"] == 1
    _, msgs = w.conversation(chat["id"])
    assert len(msgs) == 2 and "[Week05.pdf p.4] (in full: it matters for this question)" in [
        b.get("text") for b in msgs[-1]]
    assert session.pages_sent == {p.key for p in session.reading["pages"] if p.label == "[Week05.pdf p.4]"}


class Dense(Reader):
    """Notes as long as a dense page's (1,150 characters each)."""

    def __call__(self, system, content, cancel):
        labels = content[-1]["text"].split("each to be noted under its label: ")[1].split(", ")
        self.calls.append({"labels": labels})
        return {"text": "\n\n".join(f"### {x}\n" + "dense notes, formulas and a figure " * 33 for x in labels),
                "usage": {}}


def test_the_notes_stay_within_the_room_the_chat_keeps_for_them(env, notes_home, monkeypatch):
    """Notes are counted at what they can really take, and a chat whose notes still come out longer than the room left
    in Claude's window gets them shortened to fit, every page keeping its label."""
    from studia import chat as chat_mod

    w = weighed(env, monkeypatch, 28 * 2 ** 20, 10)
    monkeypatch.setattr(chatread, "default_request", lambda lim, model: Dense())
    real = chatlimits.of
    monkeypatch.setattr(chatlimits, "of", lambda p="", m="": dc.replace(real(p, m), pictures=3))
    monkeypatch.setattr(chatread, "note_cost", lambda p, per: 100)              # notes that come out longer than counted
    ctx = env.svc.context("c", [DAY1], send={"mode": "pick", "files": w.ids}, model="sonnet", read_all=True)
    assert ctx["read"]["pages"] == 4 and ctx["parts"]["notes_tokens"] == 400
    monkeypatch.setattr(chat_mod, "MAX_CONTEXT_TOKENS", ctx["tokens"] + 50)      # no room beyond the notes kept
    chat = env.svc.create("c", [DAY1], send={"mode": "pick", "files": w.ids}, read_all=True)
    e = ask(env, chat["id"], "Hello")
    assert e["usage"]["read"]["shortened"]
    _, msgs = w.conversation(chat["id"])
    (notes,) = [b["text"] for b in msgs[0] if "NOTES ON THE PAGES" in b.get("text", "")]
    room = env.svc._live[chat["id"]].reading["room"]
    assert ai.text_tokens(notes, ai.chars_per_token("claude")) <= room
    assert all(f"[{p}]" in notes for p in ("Week04.pdf p.1", "Week04.pdf p.3", "Week05.pdf p.1", "Week05.pdf p.4"))


# ---------------------------------------------------------------------------------------------------- figures
def test_a_powerpoint_slide_that_is_only_a_figure_goes_as_its_picture_and_a_word_one_is_named(env, notes_home,
                                                                                            monkeypatch):
    from test_chatfiles import docx, png, pptx

    log = env.tmp / "claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    sent = []
    monkeypatch.setattr(ai, "image_block", lambda p: sent.append(str(p)) or {
        "type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "eA=="}})
    res = [types.SimpleNamespace(id="m1", title="Circuits", file_name="Circuits.pptx", kind="resource", ext="pptx",
                                 url="https://l/1", download_url="https://l/1?dl")]
    env.lib.merge_units("c", [types.SimpleNamespace(id="u1", position=1, title="Slides", start=None, end=None,
                                                    readings="", resources=res)], first_sync=True)
    deck = env.tmp / "Circuits.pptx"
    deck.write_bytes(pptx(["Figure 3", "Kirchhoff's voltage law: the sum of the voltages around a loop is zero. " * 6],
                          png(40, 30, alpha=False)))
    env.lib.set_material_file("m1", str(deck), deck.stat().st_size, "u")
    env.lib.set_material_text("m1", ["Figure 3", "Kirchhoff's voltage law " * 20])
    chat = env.svc.create("c", [DAY1], send={"mode": "pick", "files": ["m1"]})
    assert chat["files"]["files"][0]["pictures"] == 1 and chat["files"]["files"][0]["blank"] == 0
    ask(env, chat["id"], "What does the figure show?")
    blocks = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert [b["text"] for b in blocks if b.get("type") == "text"][1] == "[Circuits.pptx p.1]"
    assert len(sent) == 1 and sent[0].endswith("-figure.jpg")
    # a Word page with nothing but a figure: no text and no picture to send, so it is named, not hidden
    spec = {"chat_id": chat["id"]}
    from test_chatfiles import send

    send(env, spec, "lab.docx", docx("Lab report"))
    box = chatfiles.folder(chat["id"])
    (item,) = [m for m in chatfiles.listing(chat["id"]) if m["name"] == "lab.docx"]
    (box / item["id"] / "text.json").write_text(json.dumps(["Lab report", ""]), encoding="utf-8")
    seen = chatinputs.inputs(env.svc, spec)
    (lab,) = [u for u in seen["uploads"] if u["name"] == "lab.docx"]
    assert lab["blank"] == 1
    system = env.svc.context("c", [DAY1], send={"mode": "pick", "files": ["m1"]}, uploads=chat["id"])["system"]
    assert "- lab.docx (2 pages): the text of every page; nothing of p.2 (no text, and its figure can't be sent)" in system
    assert "[lab.docx p.2] (no text: only a figure, which the app can't send)" in system


# ---------------------------------------------------------------------------------------------------- Codex and ChatGPT
def test_a_model_codex_turns_down_falls_back_to_its_default_once_and_is_no_longer_offered(env, tmp_path, monkeypatch):
    home = tmp_path / "codex"
    home.mkdir()
    (home / "models_cache.json").write_text(json.dumps({"models": [
        {"slug": "gpt-6.1-sol", "visibility": "list", "context_window": 500_000},
        {"slug": "gpt-6-sol", "visibility": "list", "context_window": 272_000}]}), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(ai, "_rejected", set())
    appconfig.update({"ai_provider": "codex", "codex_model": "gpt-6.1-sol"})
    monkeypatch.setattr(ai, "codex_cli_info", lambda: {"available": True, "path": "codex.exe", "version": "0.156.1"})
    runs = []

    def once(system, content, model, effort, cancel, on_text, timeout_s):
        runs.append(ai._codex_model())
        if ai._codex_model() == "gpt-6.1-sol":
            raise ai._codex_failure("unexpected status 400 Bad Request: {\"detail\":\"The 'gpt-6.1-sol' model is not "
                                    "supported when using Codex with a ChatGPT account.\"}")
        return {"text": "Answer", "usage": {}, "model": ai._codex_model() or "Codex's default model"}

    monkeypatch.setattr(ai, "_codex_once", once)
    assert ai.codex_models() == ["gpt-6.1-sol", "gpt-6-sol"]
    chat = env.svc.create("c", [DAY1])
    e = ask(env, chat["id"], "Hello")
    assert e["state"] == "done" and runs == ["gpt-6.1-sol", ""]
    assert [(n["code"], n["model"]) for n in e["usage"]["notices"]] == [("codex_model", "gpt-6.1-sol")]
    # remembered: not offered in Settings any more, not sent, and said only once
    assert ai.codex_models() == ["gpt-6-sol"] and ai._codex_model() == ""
    assert json.loads(ai._rejected_path().read_text(encoding="utf-8")) == ["gpt-6.1-sol"]
    e = ask(env, chat["id"], "Again")
    assert runs == ["gpt-6.1-sol", "", ""] and not e["usage"].get("notices")
    assert chatlimits.of("codex").window == 258_400                  # its window: the default's (the smallest listed)
    # any other failure is not asked again
    monkeypatch.setattr(ai, "_codex_once", lambda *a: (_ for _ in ()).throw(ai._codex_failure("usage limit reached")))
    with pytest.raises(RuntimeError, match="usage limit"):
        ai._codex_one_shot("s", [{"type": "text", "text": "q"}], "sonnet", "low", None, None, 60)


def test_a_heavy_chatgpt_request_has_time_to_upload_and_a_failed_upload_says_so(monkeypatch):
    import requests

    small = {"input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}]}
    heavy = {"input": [{"role": "user", "content": [{"type": "input_image", "image_url": "x" * 42_000_000}]}]}
    assert chatgpt.upload_timeout(small) == 30 and chatgpt.upload_timeout(heavy) >= 42_000_000 / 150_000
    seen = {}

    class Session:
        def post(self, url, **kw):
            seen.update(kw)
            raise requests.ConnectionError("('Connection aborted.', TimeoutError('The write operation timed out'))")

    monkeypatch.setattr(chatgpt, "_session", lambda: Session())
    monkeypatch.setattr(chatgpt, "access_token", lambda: "token")
    monkeypatch.setattr(chatgpt, "models", lambda refresh=False: [{"slug": "gpt-6.1-sol"}])
    with pytest.raises(RuntimeError, match=r"didn't finish uploading to ChatGPT"):
        chatgpt.one_shot("s", [{"type": "image", "source": {"media_type": "image/jpeg", "data": "x" * 6_000_000}}])
    assert seen["timeout"][0] >= 6_000_000 / 150_000
    with pytest.raises(RuntimeError, match=r"Can't reach ChatGPT"):           # a small one: the connection
        chatgpt.one_shot("s", [{"type": "text", "text": "hi"}])
