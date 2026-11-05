"""The student's own files in a chat: copied in, read, sent the way each AI takes them (pictures to ChatGPT, Claude
and Codex; text only to the offline model), the limits with plain messages, "What this chat sees" adding up to what
is really sent, and the viewers."""

import base64
import json
import struct
import zipfile
import zlib

import pytest

from studia import ai, appconfig, chatfiles, chatinputs, chatslides
from studia import chat as chat_mod
from test_chat import DAY1, DAY2, ask, env  # noqa: F401 - the chat fixture (fake Claude Code)


def png(w=6, h=4, alpha=True):
    """A small PNG (RGBA, half transparent) written by hand."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + bytes([200, 30, 30, 128 if alpha else 255]) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def pdf(pages):
    """A PDF whose pages hold these texts ("" = a page without text, like a scan or a drawing)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET" if text else ""
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
                    f"/Contents {len(objs)} 0 R >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def docx(text):
    from io import BytesIO

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    return buf.getvalue()


def pptx(slides, picture=b""):
    from io import BytesIO

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, text in enumerate(slides, start=1):
            z.writestr(f"ppt/slides/slide{i}.xml", f"<p:sld><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:sld>")
            if picture and i == 1:
                z.writestr("ppt/media/image1.png", picture)
                z.writestr("ppt/slides/_rels/slide1.xml.rels", '<Relationships><Relationship Id="rId2" '
                           'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                           'Target="../media/image1.png"/></Relationships>')
    return buf.getvalue()


def send(env, spec, name, data, piece=4096):
    """As the page does: the file in pieces, then added."""
    token = "ab12cd34ef56"
    for i in range(0, max(1, len(data)), piece):
        chatfiles.receive(token, base64.b64encode(data[i:i + piece]).decode(), i == 0)
    return chatinputs.upload(env.svc, spec, token, name)


@pytest.fixture
def image_blocks(env):
    """Pictures as sent, without drawing them (a page of a PDF is drawn by pypdfium2 in the real app)."""
    sent = []
    env.mp.setattr(ai, "image_block", lambda p: sent.append(str(p)) or {
        "type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "eA=="}})
    env.mp.setattr(chatslides.PagePicture, "path", lambda self: env.tmp / f"page-{self.page}.jpg")
    return sent


def test_files_are_copied_in_read_and_sent_to_claude_with_the_first_message(env, image_blocks):
    chat = env.svc.create("c", [DAY1])
    spec = {"chat_id": chat["id"]}
    src = env.tmp / "photo.png"
    src.write_bytes(png())
    r = send(env, spec, "My homework photo.png", src.read_bytes())
    assert r["item"]["kind"] == "image" and r["item"]["width"] == 6
    box = chatfiles.folder(chat["id"]) / r["item"]["id"]
    assert (box / "original.png").read_bytes() == png() and (box / "picture.jpg").stat().st_size > 0   # a copy
    send(env, spec, "notes.txt", "Bayes: P(A|B) = P(B|A) P(A) / P(B)\n".encode("utf-8"))
    send(env, spec, "hw.pdf", pdf(["Question 1: find the posterior probability of the disease " * 8, ""]))
    send(env, spec, "lab.docx", docx("Lab report: the capacitor charges"))
    ctx = env.svc.context("c", [DAY1], uploads=chat["id"])
    assert "=== STUDENT'S FILES ===" in ctx["system"] and "Under STUDENT'S FILES" in ctx["system"]
    assert "[notes.txt]\nBayes: P(A|B)" in ctx["system"] and "[hw.pdf p.2] (no text: see the picture)" in ctx["system"]
    assert "[lab.docx p.1]\nLab report" in ctx["system"]
    assert ctx["system"].index("=== STUDENT'S FILES ===") < ctx["system"].index("=== LECTURES ===")
    # the photo, then the PDF's pages (the page without text first in the budget, both fit)
    assert [label for label, _ in ctx["images"]] == ["[My homework photo.png]", "[hw.pdf p.1]", "[hw.pdf p.2]"]
    e = ask(env, chat["id"], "Check my homework")
    assert e["text"].startswith("Answer 1 (images 3")
    assert image_blocks[0].endswith("picture.jpg")
    assert ask(env, chat["id"], "And question 2?")["text"].startswith("Answer 2 (images 0")    # the same process
    # a file removed: the next question starts again with what is left
    chatinputs.remove_upload(env.svc, spec, r["item"]["id"])
    assert ask(env, chat["id"], "Again")["text"].startswith("Answer 1 (images 2, replay True")
    env.svc.delete(chat["id"])
    assert not chatfiles.folder(chat["id"]).exists()


def test_a_new_chat_s_files_become_the_chat_s_at_its_first_question(env):
    draft = {"course_id": "c", "days": [DAY1], "focus": "full", "slides": False, "uploads": "0123456789abcdef"}
    r = send(env, draft, "notes.md", b"# Summary\nThe quiz covers Bayes.")
    assert r["inputs"]["target"] == "draft-0123456789abcdef" and r["inputs"]["uploads"][0]["name"] == "notes.md"
    before = chatinputs.inputs(env.svc, {**draft, "uploads": ""})["material_tokens"]
    assert r["inputs"]["material_tokens"] > before                      # counted in the estimate already
    chat = env.svc.create("c", [DAY1], uploads="0123456789abcdef")
    assert [u["name"] for u in chat["uploads"]] == ["notes.md"] and chat["context_tokens"] == r["inputs"]["material_tokens"]
    assert not (chatfiles.root() / "draft-0123456789abcdef").exists()
    assert "The quiz covers Bayes." in env.svc.context("c", [DAY1], uploads=chat["id"])["system"]


def test_chatgpt_and_codex_get_the_pictures_with_every_question(env, image_blocks):
    appconfig.update({"ai_provider": "chatgpt"})
    chat = env.svc.create("c", [DAY1])
    send(env, {"chat_id": chat["id"]}, "board.jpg", png(alpha=False))
    calls = []

    def request(system, content, *a, **k):
        calls.append(content)
        return {"text": "ok", "usage": {}}

    env.mp.setattr(ai, "_chatgpt_one_shot", request)
    ask(env, chat["id"], "What is on the board?")
    ask(env, chat["id"], "And then?")
    for content in calls:                                  # each turn is a new request: the picture goes every time
        texts = [b.get("text") for b in content if b["type"] == "text"]
        assert "Pictures of the files the student added, in order:" in texts and "[board.jpg]" in texts
        assert sum(b["type"] == "image" for b in content) == 1
    # Codex: the pictures become files passed with -i
    prompt, images = ai._codex_text("sys", calls[0], env.tmp)
    assert len(images) == 1 and "[board.jpg]" in prompt


def test_the_offline_model_reads_the_files_text_only(env):
    appconfig.update({"ai_provider": "local"})
    chat = env.svc.create("c", [DAY1])
    spec = {"chat_id": chat["id"]}
    send(env, spec, "photo.png", png())
    send(env, spec, "notes.txt", b"The exam is on Friday.")
    ctx = env.svc.context("c", [DAY1], uploads=chat["id"])
    assert ctx["images"] == [] and "STUDENT'S FILES" not in ctx["system"]
    assert ctx["upload_text"].startswith("=== STUDENT'S FILES ===") and "The exam is on Friday." in ctx["upload_text"]
    seen = chatinputs.inputs(env.svc, spec)
    photo, notes = seen["uploads"]
    assert photo["sent"] == "none" and notes["sent"] == "text" and not seen["can_images"]
    asked = []

    class Local:
        key, last_used, upload_text = None, 0.0, ""

        def alive(self):
            return True

        def ask(self, content, on_text=None, stop=None):
            asked.append(content)
            return {"text": "ok", "usage": {}, "stopped": False}

        def close(self):
            pass

        terminate = close

    env.mp.setattr(ai, "new_session", lambda *a, **k: Local())
    ask(env, chat["id"], "When is the exam?")
    texts = [b["text"] for b in asked[0]]
    assert texts[-1] == "When is the exam?" and any("The exam is on Friday." in x for x in texts[:-1])
    assert not any(b["type"] == "image" for b in asked[0])


def test_limits_have_plain_messages(env, monkeypatch):
    chat = env.svc.create("c", [DAY1])
    spec = {"chat_id": chat["id"]}
    with pytest.raises(ValueError, match="can't be added"):
        send(env, spec, "virus.exe", b"MZ")
    # HEIC isn't offered (an iPhone's photos are grids of tiles): the student is told how to share it as JPG
    assert "heic" not in json.dumps(chatfiles.limits()).lower()
    with pytest.raises(ValueError, match=r"IMG_1234\.HEIC is a HEIC photo.*Share it as JPG"):
        chatfiles.check(chat["id"], "IMG_1234.HEIC", 3_000_000)
    with pytest.raises(ValueError, match="is empty"):
        chatfiles.check(chat["id"], "a.txt", 0)
    monkeypatch.setitem(chatfiles.MAX_BYTES, "image", 100)
    with pytest.raises(ValueError, match=r"too big .* up to 0.1 MB"):
        send(env, spec, "big.png", png(40, 40) + b"\x00" * 200)
    with pytest.raises(ValueError, match="couldn't be read"):
        send(env, spec, "broken.png", b"not a picture at all")
    with pytest.raises(ValueError, match="no text"):
        send(env, spec, "blank.txt", b"   \n")
    assert chatfiles.listing(chat["id"]) == []                         # nothing half-added stays
    monkeypatch.setattr(chatfiles, "MAX_FILES", 1)
    send(env, spec, "one.txt", b"one")
    with pytest.raises(ValueError, match="up to 1 of your files"):
        send(env, spec, "two.txt", b"two")
    monkeypatch.setattr(chatfiles, "MAX_FILES", 20)
    # a file bigger than the room left is added: its text is cut to what fits (read in batches with "Read everything")
    monkeypatch.setattr(chat_mod, "MAX_CONTEXT_TOKENS", chat["context_tokens"] + 2500)
    r = send(env, spec, "book.txt", b"word " * 4000)
    book = next(u for u in r["inputs"]["uploads"] if u["name"] == "book.txt")
    assert book["cut"] and r["inputs"]["material_tokens"] <= chat["context_tokens"] + 2500
    # refused only when the lectures alone fill the chat
    monkeypatch.setattr(chat_mod, "MAX_CONTEXT_TOKENS", 200)
    with pytest.raises(RuntimeError, match="doesn't fit in this chat"):
        send(env, spec, "three.txt", b"three")
    assert [m["name"] for m in chatfiles.listing(chat["id"])] == ["one.txt", "book.txt"]


def test_the_picture_and_text_budgets(env, monkeypatch):
    monkeypatch.setattr(chatfiles, "MAX_PICTURES", 2)
    monkeypatch.setattr(chatfiles, "MAX_TEXT_TOKENS", 600)
    chat = env.svc.create("c", [DAY1])
    spec = {"chat_id": chat["id"]}
    send(env, spec, "scan.pdf", pdf(["Long typed page about the topic " * 12, "", "Another long page " * 20]))
    send(env, spec, "long.txt", ("A sentence of the long notes. " * 300).encode())
    up = chatfiles.build(chat["id"], can_images=True)
    assert [label for label, _ in up["images"]] == ["[scan.pdf p.1]", "[scan.pdf p.2]"]   # the drawing first, then order
    assert up["pictures_left_out"] == 1
    scan, notes = up["items"]
    assert scan["pictures"] == 2 and not scan["cut"] and notes["cut"]
    assert "(The rest of this file was left out: too long.)" in up["text"]


def test_what_this_chat_sees_adds_up_to_what_is_sent(env, image_blocks):
    chat = env.svc.create("c", [DAY1, DAY2], focus="highlights")
    spec = {"chat_id": chat["id"]}
    send(env, spec, "photo.png", png())
    send(env, spec, "notes.txt", b"My own summary of lecture two.")
    ask(env, chat["id"], "What did they emphasise?")
    seen = chatinputs.inputs(env.svc, spec)
    ctx = env.svc.context("c", [DAY1, DAY2], focus="highlights", uploads=chat["id"])
    assert seen["material_tokens"] == ctx["tokens"] == env.svc.view(chat["id"])["context_tokens"]
    parts = (sum(d["tokens"] for d in seen["days"]) + seen["brief_tokens"] + seen["instructions_tokens"]
             + seen["video"]["tokens"] + sum(u["tokens"] for u in seen["uploads"]))
    assert parts == seen["material_tokens"]
    assert [(d["n"], d["parts"]) for d in seen["days"]] == [(1, 2), (2, 1)] and seen["days"][0]["seconds"] == 4800
    assert seen["conversation"]["turns"] == 1 and seen["total"] == seen["material_tokens"] + seen["conversation"]["tokens"]
    assert seen["uploads"][0]["sent"] == "pictures" and seen["uploads"][1]["sent"] == "text"


def test_the_viewers(env, image_blocks, monkeypatch):
    chat = env.svc.create("c", [DAY1, DAY2], focus="highlights")
    spec = {"chat_id": chat["id"]}
    day = chatinputs.file_view(env.svc, spec, f"day:{DAY2}")
    assert day["kind"] == "transcript" and day["text"].startswith("# L2 · MATH 240") and "⭐" in day["text"]
    with pytest.raises(RuntimeError, match="isn't part of this chat"):
        chatinputs.file_view(env.svc, spec, "day:c|2026-01-01|lecture")
    photo = send(env, spec, "photo.png", png())["item"]
    v = chatinputs.file_view(env.svc, spec, f"upload:{photo['id']}")
    assert v["kind"] == "image" and v["data"].startswith("data:image/jpeg;base64,")
    hw = send(env, spec, "hw.pdf", pdf(["Question one " * 40, ""]))["item"]
    v = chatinputs.file_view(env.svc, spec, f"upload:{hw['id']}")
    assert v == {"kind": "pdf", "title": "hw.pdf", "pages": 2, "pictured": [1, 2]}
    monkeypatch.setattr(chatfiles, "pdf_page", lambda path, page, zoom=1.0: f"page {page} at {zoom}")
    assert chatinputs.file_view(env.svc, spec, f"upload:{hw['id']}", 2, 1.5)["data"] == "page 2 at 1.5"
    notes = send(env, spec, "notes.txt", b"line one\nline two")["item"]
    assert chatinputs.file_view(env.svc, spec, f"upload:{notes['id']}") == {"kind": "text", "title": "notes.txt",
                                                                            "pages": ["line one\nline two"]}
    deck = send(env, spec, "deck.pptx", pptx(["Kirchhoff's laws", "Thevenin"], png(8, 8, alpha=False)))["item"]
    v = chatinputs.file_view(env.svc, spec, f"upload:{deck['id']}")
    assert v["kind"] == "slides" and [s["pictures"] for s in v["slides"]] == [1, 0]
    assert v["slides"][1]["text"] == "Thevenin"
    pics = chatinputs.file_view(env.svc, spec, f"upload:{deck['id']}", 1)["pictures"]
    assert len(pics) == 1 and pics[0].startswith("data:image/jpeg;base64,")
    with pytest.raises(ValueError, match="no longer in this chat"):
        chatinputs.file_view(env.svc, spec, "upload:../../x")


def test_photos_are_turned_upright_and_left_overs_are_cleaned(env, tmp_path):
    import av
    import numpy as np

    from studia.slides import save_jpeg

    src = tmp_path / "phone.jpg"
    save_jpeg(av.VideoFrame.from_ndarray(np.zeros((40, 60, 3), np.uint8), format="rgb24"), src)
    raw = src.read_bytes()
    tiff = b"MM\x00\x2a\x00\x00\x00\x08" + b"\x00\x01" + b"\x01\x12\x00\x03\x00\x00\x00\x01\x00\x06\x00\x00" + b"\x00" * 4
    app1 = b"Exif\x00\x00" + tiff
    src.write_bytes(raw[:2] + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + raw[2:])
    assert chatfiles._exif_turn(src) == 6
    out = tmp_path / "out.jpg"
    assert chatfiles._to_jpeg(src, out) == (40, 60)                    # turned: portrait
    chat = env.svc.create("c", [DAY1])
    send(env, {"chat_id": chat["id"]}, "a.txt", b"text")
    (chatfiles.root() / "0123456789ab").mkdir()                       # a chat that is gone
    chatfiles.clean(lambda cid: env.lib.get_chat(cid) is not None)
    assert chatfiles.folder(chat["id"]).exists() and not (chatfiles.root() / "0123456789ab").exists()
    assert json.loads(next(chatfiles.folder(chat["id"]).iterdir()).joinpath("meta.json").read_text())["name"] == "a.txt"


def test_pdfium_is_used_by_one_thread_at_a_time(tmp_path, monkeypatch):
    """PDFium isn't thread-safe: the viewer's pages (one thread per call), a chat, an upload and a study pack all go
    through one lock, and the same page drawn twice at once doesn't share its file."""
    import os
    import threading

    import pypdfium2 as pdfium

    from studia import cheatsheet, materials, studypack

    doc = tmp_path / "notes.pdf"
    doc.write_bytes(pdf(["Page one about Bayes", "Page two", ""]))
    real, held = pdfium.PdfDocument, []

    def checked(*a, **k):
        held.append(materials.PDFIUM_LOCK._is_owned())
        return real(*a, **k)

    monkeypatch.setattr(pdfium, "PdfDocument", checked)
    assert materials.extract_pages(doc)[0] == "Page one about Bayes"
    assert materials.page_image(doc, 2, 300) is not None and chatfiles.pdf_pages(doc) == 3
    assert cheatsheet.pdf_pages(doc) == 3 and studypack.chapter_pages(doc, []) == {}
    assert len(held) == 5 and all(held)
    errors = []

    def work(i):
        try:
            for _ in range(3):
                assert materials.page_image(doc, 1 + i % 3, 400 + 2 * i) is not None
                assert len(materials.extract_pages(doc)) == 3
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors
    future = doc.stat().st_mtime + 3600                       # every call draws the page again: the same file each time
    os.utime(doc, (future, future))

    def same():
        try:
            for _ in range(4):
                assert materials.page_image(doc, 1, 320) is not None
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=same) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors


def test_a_page_drawn_while_waiting_for_pdfium_is_not_drawn_again(tmp_path, monkeypatch):
    """Two callers that want the same page both miss its picture; the second one finds it once it has the lock and
    doesn't replace the file under the first one's reader."""
    import threading

    import pypdfium2 as pdfium

    from studia import materials

    doc = tmp_path / "notes.pdf"
    doc.write_bytes(pdf(["Page one", "Page two"]))
    out = materials.page_image(doc, 1, 300, folder=tmp_path / "pages")
    data = out.read_bytes()
    out.unlink()
    opened = []
    real = pdfium.PdfDocument
    monkeypatch.setattr(pdfium, "PdfDocument", lambda *a, **k: opened.append(a) or real(*a, **k))

    class Waiting:
        """The lock, with another caller's picture saved while this one waits for it."""
        lock = threading.RLock()

        def __enter__(self):
            out.write_bytes(data)
            return self.lock.__enter__()

        def __exit__(self, *exc):
            return self.lock.__exit__(*exc)

    monkeypatch.setattr(materials, "PDFIUM_LOCK", Waiting())
    assert not out.exists()                                   # missed before the lock
    assert materials.page_image(doc, 1, 300, folder=tmp_path / "pages") == out
    assert opened == [] and out.read_bytes() == data


def test_scanned_pages_say_whether_their_picture_goes(env, monkeypatch):
    monkeypatch.setattr(chatfiles, "MAX_PICTURES", 1)
    chat = env.svc.create("c", [DAY1])
    spec = {"chat_id": chat["id"]}
    send(env, spec, "hw.pdf", pdf(["", ""]))
    up = chatfiles.build(chat["id"], can_images=True)
    assert "[hw.pdf p.1] (no text: see the picture)" in up["text"]
    assert "[hw.pdf p.2] (no text could be read from this page, and its picture isn't sent)" in up["text"]
    (scan,) = up["items"]
    assert scan["text_tokens"] == 0 and scan["sent"] == "pictures" and not scan["unread"]
    # the offline model gets no pictures: nothing of a scan reaches it, and the panel says so
    up = chatfiles.build(chat["id"], can_images=False, local=True)
    (scan,) = up["items"]
    assert scan["sent"] == "none" and scan["unread"] and up["text"] == "" and up["passages"] == []
    appconfig.update({"ai_provider": "local"})
    seen = chatinputs.inputs(env.svc, spec)
    assert seen["uploads"][0]["unread"] and seen["uploads"][0]["tokens"] == 0


def test_the_offline_model_gets_the_passages_of_the_files_that_match_each_question(env):
    appconfig.update({"ai_provider": "local"})
    chat = env.svc.create("c", [DAY1])
    spec = {"chat_id": chat["id"]}
    send(env, spec, "first.txt", ("Opening remarks about the course organisation. " * 200).encode())
    later = ["Filler page about general topics"] * 11 + ["Eigenvalue decomposition of the covariance matrix"]
    send(env, spec, "second.pdf", pdf(later))
    asked = []

    class Local:
        key, last_used = None, 0.0

        def alive(self):
            return True

        def ask(self, content, on_text=None, stop=None):
            asked.append([b["text"] for b in content if b["type"] == "text"])
            return {"text": "ok", "usage": {}, "stopped": False}

        def close(self):
            pass

        terminate = close

    env.mp.setattr(ai, "new_session", lambda *a, **k: Local())
    ask(env, chat["id"], "Explain the eigenvalue decomposition")
    mine = asked[0][0]
    assert mine.startswith("=== STUDENT'S FILES (the passages that match the question) ===")
    assert mine.index("[second.pdf p.12]\nEigenvalue decomposition") < 200      # the best match first
    assert len(mine) <= chatfiles.LOCAL_CHARS + 100 and asked[0][-1] == "Explain the eigenvalue decomposition"
    # a follow-up that matches nothing by itself keeps to the question before it
    ask(env, chat["id"], "And why?")
    assert "[second.pdf p.12]" in asked[1][0]
    # nothing matches: the files' beginning
    assert chatfiles.local_pick(["[a p.1]\nalpha", "[a p.2]\nbeta"], "zzz").endswith("[a p.1]\nalpha\n\n[a p.2]\nbeta")


def test_pictures_of_the_student_s_files_are_counted_by_their_size(env, image_blocks):
    assert chatfiles.picture_tokens(1024, 576) == 786                   # a 16:9 slide: about ai.IMAGE_TOKENS
    assert chatfiles.picture_tokens(2048, 2896) == 1977                 # a portrait A4 page, sent 1024 px wide
    assert chatfiles.picture_tokens(1080, 2340) == 1513                 # a phone screenshot: its long side cut to 1568
    assert chatfiles.picture_tokens(6, 4) == 1
    chat = env.svc.create("c", [DAY1])
    spec = {"chat_id": chat["id"]}
    send(env, spec, "hw.pdf", pdf(["", "Typed answer " * 40]))
    (hw,) = chatfiles.build(chat["id"], can_images=True)["items"]
    page = chatfiles.picture_tokens(1024, 1024 * 792 / 612)              # the test PDF's pages are US Letter
    assert hw["pictures"] == 2 and hw["picture_tokens"] == 2 * page and hw["tokens"] == hw["text_tokens"] + 2 * page
    seen = chatinputs.inputs(env.svc, spec)
    parts = (sum(d["tokens"] for d in seen["days"]) + seen["brief_tokens"] + seen["instructions_tokens"]
             + seen["video"]["tokens"] + sum(u["tokens"] for u in seen["uploads"]))
    assert parts == seen["material_tokens"] and seen["instructions_tokens"] < 3000


def test_a_big_photo_is_made_small_before_anything_else(tmp_path):
    import tracemalloc

    import av
    import numpy as np

    from studia.slides import save_jpeg

    src = tmp_path / "phone.jpg"                                         # 12 MP: as numbers in floats, 800 MB of them
    save_jpeg(av.VideoFrame.from_ndarray(np.full((3000, 4000, 3), 90, np.uint8), format="rgb24"), src, max_width=4000)
    tracemalloc.start()
    try:
        assert chatfiles._to_jpeg(src, tmp_path / "out.jpg") == (4000, 3000)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 40 * 2 ** 20
    with av.open(str(tmp_path / "out.jpg")) as c:
        frame = next(c.decode(video=0))
    assert (frame.width, frame.height) == (1600, 1200)
    # transparency still goes on white: half-transparent red (200, 30, 30) becomes light red
    (tmp_path / "half.png").write_bytes(png(8, 8))
    assert chatfiles._to_jpeg(tmp_path / "half.png", tmp_path / "half.jpg") == (8, 8)
    with av.open(str(tmp_path / "half.jpg")) as c:
        r, g, b = next(c.decode(video=0)).to_ndarray(format="rgb24")[4, 4].tolist()
    assert abs(r - 228) < 8 and abs(g - 142) < 8 and abs(b - 142) < 8


def test_no_copy_of_a_removed_file_stays_behind(env):
    """The pictures drawn from a student's files (sent, or seen in the viewer) are kept with them, and the AI's copies
    of them go with them: a photo of an ID card removed by mistake leaves nothing in the app's caches."""
    from studia.paths import app_dir

    chat = env.svc.create("c", [DAY1])
    spec = {"chat_id": chat["id"]}
    send(env, spec, "id card.png", png(8, 8, alpha=False))
    hw = send(env, spec, "hw.pdf", pdf(["", "Typed answer " * 40]))["item"]
    deck = send(env, spec, "deck.pptx", pptx(["Kirchhoff's laws"], png(8, 8, alpha=False)))["item"]
    ask(env, chat["id"], "Check my homework")                  # every picture sent: drawn and copied for the AI
    assert chatinputs.file_view(env.svc, spec, f"upload:{hw['id']}", 1, 1.5)["data"].startswith("data:image/jpeg")
    assert chatinputs.file_view(env.svc, spec, f"upload:{deck['id']}", 1)["pictures"]
    copies = app_dir() / "cache" / "slides"
    assert len(list(copies.glob("*.jpg"))) == 4                 # the photo, the PDF's two pages, the slide's figure
    assert not list((app_dir() / "cache" / "pages").glob("*.jpg"))
    box = chatfiles.folder(chat["id"])
    assert len(list((box / hw["id"] / "pages").glob("*.jpg"))) == 3 and list((box / deck["id"] / "pages").glob("*.jpg"))
    chatinputs.remove_upload(env.svc, spec, hw["id"])
    assert len(list(copies.glob("*.jpg"))) == 2
    env.svc.delete(chat["id"])
    assert not list(copies.glob("*.jpg")) and not box.exists()
