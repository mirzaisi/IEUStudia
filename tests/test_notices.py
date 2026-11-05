"""Clear notices: the AI is asked for alert blocks ("> [!WARNING]") and the page, the study pack's HTML and the exported
chat show them as such; the app's notices about a chat's material are kept with the answer they belong to and never
reach the AI; errors and plans get a notice with what to do (notices.js, run in node)."""

import datetime as dt
import json
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

from studia import ai, aierrors, appconfig, chat as chat_mod, cheatsheet, i18n, notices, plans, store, studypack
from studia.chat import ChatService, replay_text
from studia.config import Settings
from studia.domain import Course, Recording, Segment, Transcript
from studia.library import Library
from studia.services import Services

FAKE = str(Path(__file__).with_name("fake_claude.py"))
WEB = Path(i18n.__file__).parent / "web"
DAY1 = "c|2026-10-13|lecture"


def wait_for(cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    appconfig.update({"ai_provider": "claude"})
    monkeypatch.setattr(ai, "_require_claude", lambda: sys.executable)
    monkeypatch.setattr(ai, "_command", lambda model, effort, sp, partial=False: [sys.executable, FAKE, str(sp)])
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="t", code="MATH 240", name="Probability", instructor="Dr X")])
    recs = [Recording(delivery_id=f"nt{i}", course_id="c", folder_id="f", folder_path="", name="n",
                      start=dt.datetime(2026, 10, 13, h, 30), duration_s=2400, viewer_url=f"https://v/r{i}")
            for i, h in enumerate([8, 9])]
    lib.merge_recordings("c", recs, first_sync=True)
    tr = Transcript(delivery_id="nt0", segments=[Segment(0, 5, "Lecture text.", "en", 0.9)], duration_s=2400,
                    speech_s=5, language_seconds={"en": 5}, model="m", device="cpu", engine_version="t",
                    created_at="2026-10-20T10:00:00")
    lib.save_transcript(tr, store.save_transcript(tr))           # part 2 (nt1) is not transcribed (ids of their own: the
                                                                 # transcripts folder is shared with test_chat)
    lib.set_status("nt0", "done")
    events = []
    ns = types.SimpleNamespace(lib=lib, emit=events.append, output_root=tmp_path / "out", settings=Settings())
    ns.day = types.MethodType(Services.day, ns)
    svc = ChatService(ns)
    yield types.SimpleNamespace(svc=svc, lib=lib, events=events, tmp=tmp_path, mp=monkeypatch)
    svc.shutdown()


def ask(env, chat_id, text):
    seq = env.svc.send(chat_id, text)["seq"]
    done = lambda: [e for e in env.events if e.get("type") == "chat" and e["seq"] == seq and e["state"] != "streaming"]  # noqa: E731
    assert wait_for(lambda: done()), "no answer"
    return seq, done()[-1]


def test_every_prompt_that_writes_markdown_asks_for_alert_blocks():
    for prompt in (chat_mod.system_prompt(1, "full", False), ai.SYSTEM_PROMPT, plans.SYSTEM_PREP, studypack.SYSTEM):
        assert notices.ALERTS_MD in prompt and "> [!WARNING]" in prompt
    for prompt in (plans.SYSTEM, cheatsheet.SYSTEM):                   # JSON answers: say it in the field meant for it
        assert notices.ALERTS_JSON in prompt
    assert notices.has_alert("text\n> [!caution]\n> x") and not notices.has_alert("> plain quote")


def test_material_notices_are_kept_with_the_answer_and_never_reach_the_ai(env):
    chat = env.svc.create("c", [DAY1])
    seq, e = ask(env, chat["id"], "What is on the quiz?")
    want = [{"code": "not_transcribed", "kind": "warning", "n": 1, "dates": ["2026-10-13"]}]
    assert e["state"] == "done" and e["usage"]["notices"] == want
    assert any(x["state"] == "streaming" and (x.get("usage") or {}).get("notices") == want for x in env.events
               if x.get("type") == "chat" and x["seq"] == seq)                        # shown while it is answering
    msgs = env.svc.view(chat["id"])["messages"]
    assert msgs[1]["usage"]["notices"] == want and msgs[0]["usage"] == {}            # stored with the answer
    env.svc._live[chat["id"]].terminate()                    # a new process: the conversation goes again …
    seq2, e2 = ask(env, chat["id"], "And the homework?")
    assert "replay True" in e2["text"] and "notices" not in e2["usage"]                # … the notice is not repeated
    history = env.lib.chat_messages(chat["id"])
    replay = replay_text(history)
    assert "not_transcribed" not in replay and "2026-10-13" not in replay and "transcribed" not in replay

    class Seeded(ai.ChatGPTSession):                           # ChatGPT gets the conversation from the messages' text
        def __init__(self):
            self.history = []
    s = Seeded()
    s.seed(history)
    assert all("not_transcribed" not in q + a and "notices" not in q + a for q, a in s.history)

    path = env.svc.export(chat["id"])                        # the exported chat keeps it as a GitHub alert
    text = path.read_text(encoding="utf-8")
    assert "> [!WARNING]\n> 1 recording of these lectures is not transcribed, so the AI did not get it (2026-10-13)." in text


def test_a_failed_answer_keeps_its_notices_and_its_error(env):
    chat = env.svc.create("c", [DAY1])
    env.mp.setattr(ai.ClaudeSession, "ask", lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("ChatGPT: HTTP 500")))
    seq, e = ask(env, chat["id"], "q")
    assert e["state"] == "error" and e["error"] == "ChatGPT: HTTP 500" and e["usage"]["notices"][0]["code"] == "not_transcribed"
    usage = env.svc.view(chat["id"])["messages"][1]["usage"]
    assert usage["error"] == "ChatGPT: HTTP 500" and usage["notices"][0]["code"] == "not_transcribed"
    assert "> [!CAUTION]\n> " in env.svc.export(chat["id"]).read_text(encoding="utf-8")


def test_notices_about_slide_files_and_pictures():
    files = {"summary": {"missing": 2, "cut": True, "images_on": True, "files": [{"id": "a"}]},
             "catalog": {"files": [{"id": "a", "downloaded": True}, {"id": "b", "downloaded": False},
                                   {"id": "c", "downloaded": False}, {"id": "d", "downloaded": False}]}}
    send = {"mode": "pick", "files": ["a", "b", "c"]}
    appconfig.update({"ai_provider": "claude"})
    got = ChatService._notices(1, [], files, send, 70, 60)
    assert [n["code"] for n in got] == ["days_gone", "files_missing", "files_cut", "slides_trimmed"]
    assert got[1]["ids"] == ["b", "c"] and got[3] == {"code": "slides_trimmed", "kind": "info", "sent": 60, "total": 70}
    appconfig.update({"ai_provider": "local"})                        # it can't take pictures
    assert [n["code"] for n in ChatService._notices(0, [], None, None, 4, 0)] == ["no_pictures"]
    appconfig.update({"ai_provider": "claude"})
    assert notices.fresh([{"code": "x", "n": 1}, {"code": "x", "n": 1}, {"code": "y"}],
                         [{"usage": {"notices": [{"code": "y"}]}}]) == [{"code": "x", "n": 1}]


def test_the_study_pack_draws_alert_blocks_as_boxes():
    html = studypack.md_html("Text.\n\n> [!WARNING]\n> The slides of week 5 were **missing**.\n\n> [!TIP] Try this\n> $x^2$\n\n> plain")
    assert '<div class="sp-alert sp-alert-warning" role="note">' in html and "<b>Warning</b>" in html
    assert "<strong>missing</strong>" in html and "[!WARNING]" not in html
    assert '<div class="sp-alert sp-alert-tip" role="note">' in html and "<b>Try this</b>" in html and "$x^2$" in html
    assert html.count("<blockquote>") == 1
    css = (WEB / "studypack.css").read_text(encoding="utf-8")
    assert all(f".sp-alert-{k}" in css for k in ("tip", "important", "warning", "caution"))


# ------------------------------------------------------------------ the page (app.js + notices.js in node)
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
for (const f of ["i18n.js", "icons.js", "app.js", "notices.js"]) require(web + "/" + f);
globalThis.katex = require(web + "/vendor/katex/katex.min.js");
require(web + "/math.js");
const St = globalThis.Studia;
St.aiName = () => "Claude";            // the error names the AI that failed: that one is said
const out = {};
out.md = cases.md.map(([s, o]) => LSRender.md(s, o || {}));
out.chat = cases.chat.map(([m, last]) => St.chatNotices(m, last));
out.plans = cases.plans.map((p) => St.planStateNotice(p));
out.warn = cases.warn.map((p) => St.planWarnings(p));
out.explain = (cases.explain || []).map((m) => St.explainError(m, { label: "Again", act: "again" }));
console.log(JSON.stringify(out));
"""


def _run(tmp_path, cases):
    (tmp_path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    script = tmp_path / "notices.js"
    script.write_text(SCRIPT % {"web": json.dumps(WEB.as_posix()), "cases": json.dumps((tmp_path / "cases.json").as_posix())},
                      encoding="utf-8")
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


SLOW_DOWN = aierrors.explain("ChatGPT", 429)
SERVICE_500 = aierrors.explain("ChatGPT", 500, 4)


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_page_draws_alerts_also_while_streaming_and_explains_errors(tmp_path):
    out = _run(tmp_path, {
        "md": [["Answer.\n\n> [!WARNING]\n> Week 5's slides were **not** given to me.\n> - so this is a guess\n\nMore.", None],
               ["> [!note] My own title\n> $x^2$ text", None],
               ["> [!CAUTION]\n> Check this", {"streaming": True}],
               ["Answer.\n\n> [!WAR", {"streaming": True}],
               ["Answer.\n\n> [!WAR", None],
               ["> just a quote\n> [!NOTE] not first", None],
               ["> [!TIP]", {"streaming": True}]],
        "chat": [[{"seq": 4, "status": "done", "text": "x", "usage": {"notices": [
                    {"code": "not_transcribed", "kind": "warning", "n": 2, "dates": ["2026-10-13", "2026-10-20"]},
                    {"code": "files_missing", "kind": "warning", "n": 1, "ids": ["m1"]},
                    {"code": "slides_trimmed", "kind": "info", "sent": 60, "total": 70},
                    {"code": "from_a_newer_version", "kind": "warning"}]}}, True],
                 [{"seq": 6, "status": "error", "text": "", "usage": {"error": "ChatGPT: HTTP 500"}}, True],
                 [{"seq": 6, "status": "error", "text": "", "usage": {"error": "Your ChatGPT sign-in has ended. Settings → AI → Continue with ChatGPT."}}, False],
                 [{"seq": 8, "status": "error", "text": "", "usage": {"error": "You have reached your Claude usage limit."}}, True],
                 [{"seq": 9, "status": "error", "text": "", "usage": {"error": "Can't reach ChatGPT. Check your internet connection and try again."}}, True],
                 [{"seq": 10, "status": "error", "text": "", "usage": {"error": SLOW_DOWN}}, True]],
        "plans": [{"id": "p1", "status": "error", "error": "ChatGPT: HTTP 500"}, {"id": "p2", "status": "paused", "message": "Stopped."},
                  {"id": "p3", "status": "running"}, {"id": "p6", "status": "error", "error": SERVICE_500}],
        "warn": [{"id": "p4", "course_id": "c", "scope": {"weeks": [5, 6]}, "result": {
            "completeness": {"missing_list": ["Bayes' rule (<b>)"]}, "materials_missing": [{"id": "m1", "title": "W5"}],
            "days_missing": 1, "changes": {"removed": [3]}}}, {"id": "p5", "result": {}},
                 {"id": "p7", "course_id": "c", "scope": {"weeks": [5]}, "result": {
                     "materials_missing": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}], "days_missing": 2}}],
    })
    warn, own, streaming, partial, finished, quote, empty = out["md"]
    assert 'class="md-alert md-alert-warning" role="note"' in warn and "<b>Warning</b>" in warn and "[!WARNING]" not in warn
    assert "<b>not</b>" in warn and "<li>so this is a guess</li>" in warn and "<p>More.</p>" in warn
    assert "md-alert-note" in own and "<b>My own title</b>" in own and "katex" in own
    assert "md-alert-caution" in streaming and "Check this" in streaming
    assert "[!WAR" not in partial and "blockquote" not in partial and "<p>Answer.</p>" in partial   # still being written
    assert "<blockquote>" in finished and "[!WAR" in finished                       # not an alert: shown as written
    assert "<blockquote>" in quote and "md-alert" not in quote
    assert "md-alert-tip" in empty

    material, server, signin, limit, offline, slow = out["chat"]
    assert "error" not in material and material["before"].count('class="ntc ') == 3        # an unknown code: left out
    b = material["before"]
    assert "ntc-warning" in b and "ntc-info" in b and "2 recordings of these lectures" in b and 'data-act="ntc-act"' in b
    assert "60 of the 70 slide pictures" in b and "Download 1 file" in b
    assert 'class="ntc ntc-error" role="alert"' in server["error"] and "ChatGPT's servers had a problem" in server["error"]
    assert 'data-act="chat-retry"' in server["error"] and "HTTP 500" in server["error"] and "Wait a minute" in server["error"]
    assert "sign in again" in signin["error"] and "chat-retry" not in signin["error"] and "Open Settings" in signin["error"]
    assert "usage limit" in limit["error"] and "reach ChatGPT" in offline["error"]
    # the app's own plain words for a failure on the service's side (aierrors.py): shown as the text, not under it
    assert "servers had a problem" in slow["error"] and "usage limit" not in slow["error"] and "slow down" in slow["error"]
    assert "ntc-detail" not in slow["error"] and "Wait a minute" not in slow["error"]

    # one Resume (the page's header has it) and one "Stopped" message (under the steps)
    failed, paused, running, service = out["plans"]
    assert "ntc-error" in failed and "plan-resume" not in failed and "servers had a problem" in failed
    assert "Resume goes on from where it stopped" in failed and "HTTP 500" in failed
    assert "ntc-info" in paused and "plan-resume" not in paused and "Stopped" not in paused and running == ""
    assert "Resume goes on from where it stopped" in paused
    assert "servers had a problem" in service and "had a problem on its side and did not answer (error 500)" in service
    assert "ntc-detail" not in service and "Resume goes on from where it stopped" in service and "plan-resume" not in service
    full, nothing, several = out["warn"]
    assert full.count('class="ntc ') == 4 and "Bayes&#39; rule (&lt;b&gt;)" in full and "(<b>)" not in full
    assert "1 video was dropped" in full and "1 file of these weeks" in full and "1 lecture of these weeks" in full
    assert "Download it and update" in full and "Transcribe it and update" in full
    assert "Download them and update" in several and "Transcribe them and update" in several and " it and update" not in several
    assert nothing == ""


# ------------------------------------------------------------------ fixes after the 3.1 review
# every AI error the student can read with its reason: (English text, its placeholders, the reason notices.js gives)
AI_ERRORS = [
    ("You have reached your Claude usage limit — it resets {when}.", {"when": "10:40"}, "limit"),
    ("You have reached your Claude usage limit.", {}, "limit"),
    ("You have reached your ChatGPT usage limit for Codex. Try again later.", {}, "limit"),
    ("You have reached your ChatGPT usage limit for apps. See {url}.", {"url": "https://chatgpt.com"}, "limit"),
    ("Claude Code is not installed on this PC. Settings → AI installs it for you.", {}, "setup"),
    ("The Codex CLI is not installed on this PC (Settings → AI).", {}, "setup"),
    ("AI features are turned off (Settings → AI).", {}, "setup"),
    ("The offline AI model is not downloaded yet (Settings → AI).", {}, "setup"),
    ("Claude Code is not signed in. Settings → AI → Sign in with Claude.", {}, "signin"),
    ("The Codex CLI is not signed in. Sign in under Settings → AI.", {}, "signin"),
    ("ChatGPT is not signed in. Settings → AI → Continue with ChatGPT.", {}, "signin"),
    ("IEUStudia may not use your ChatGPT plan yet. Go to Settings → AI → Continue with ChatGPT and allow it.", {}, "signin"),
    ("Your ChatGPT sign-in has ended. Settings → AI → Continue with ChatGPT.", {}, "signin"),
    ("ChatGPT did not accept the sign-in. Settings → AI → Continue with ChatGPT.", {}, "signin"),
    ("ChatGPT did not answer in time. Check your internet connection and try again.", {}, "offline"),
    ("Can't reach ChatGPT. Check your internet connection and try again.", {}, "offline"),
    ("The connection to ChatGPT ended before the answer was complete. Try again.", {}, "offline"),
    ("The connection to ChatGPT broke off ({error}). Try again.", {"error": "Read timed out"}, "offline"),
    ("{host} took too long to answer. Check your internet connection and try again.", {"host": "chatgpt.com"}, "offline"),
    ("Claude took too long to answer.", {}, "other"),
    ("Claude did not return an answer.", {}, "other"),
    ("Claude did not return a result.", {}, "other"),
    ("Claude Code stopped unexpectedly. Try again.", {}, "other"),
    ("ChatGPT did not return an answer.", {}, "other"),
    ("{ai}'s answer could not be read (it was not valid JSON). Try again.", {"ai": "ChatGPT"}, "other"),
    ("Your Claude plan doesn't include Claude Code. You need Claude Pro or Max.", {}, "other"),
    ("OpenAI doesn't let apps like IEUStudia use your ChatGPT plan. ChatGPT Plus and Pro can be used (for Free and Go, "
     "OpenAI decides).", {}, "other"),
    ("ChatGPT can't check your plan right now. Try again in a few minutes.", {}, "other"),
    ("ChatGPT lists no models for your account.", {}, "other"),
    ("ChatGPT stopped before answering ({why}).", {"why": "max_output_tokens"}, "other"),
    ("Your ChatGPT plan does not offer the chosen model. Settings → AI → ChatGPT model: Default.", {}, "other"),
]
TITLES = {"limit": "usage limit", "server": "servers had a problem", "setup": "set up on this PC",
          "signin": "sign in again", "offline": "couldn't reach", "other": "couldn't finish"}


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_every_ai_error_gets_its_reason_in_english_and_turkish(tmp_path):
    texts, want = [], []
    for lang in ("en", "tr"):
        i18n.set_language(lang)
        try:
            for key, params, reason in AI_ERRORS:
                texts.append(i18n.t(key, **params))
                want.append((lang, key, reason))
            for status, tries in ((500, 4), (429, 1), (0, 1)):        # aierrors.py's own words: on the service's side
                texts.append(aierrors.explain("ChatGPT", status, tries))
                want.append((lang, f"explain {status}", "server"))
        finally:
            i18n.set_language("en")
    n = len(texts) // 2
    assert all(en != tr for en, tr in zip(texts[:n], texts[n:])), "an error text has no Turkish"
    out = _run(tmp_path, {"md": [], "chat": [], "plans": [], "warn": [], "explain": texts})["explain"]
    wrong = [(lang, key, reason, got["title"]) for (lang, key, reason), got in zip(want, out)
             if TITLES[reason] not in got["title"]]
    assert not wrong
    for (lang, key, reason), got in zip(want, out):           # the way out: Settings → AI where the error says so
        settings = any(a and a.get("key") == "signin" for a in got["actions"])
        assert settings == (reason in ("setup", "signin") or "Settings → AI" in key), (lang, key)


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_old_notices_name_the_ai_that_answered_and_uploads_have_their_own(tmp_path):
    out = _run(tmp_path, {"md": [], "plans": [], "warn": [], "chat": [
        [{"seq": 2, "status": "error", "text": "", "usage": {"notices_ai": "chatgpt", "error": "No answer came back.",
                                                              "notices": [{"code": "no_pictures", "kind": "info"}]}}, True],
        [{"seq": 3, "status": "done", "text": "x", "usage": {"notices": [{"code": "no_pictures", "kind": "info"}]}}, True],
        [{"seq": 4, "status": "done", "text": "x", "usage": {"notices_ai": "claude", "notices": [
            {"code": "uploads_unreadable", "kind": "warning", "n": 2},
            {"code": "pages_unreadable", "kind": "warning", "n": 1}]}}, True]]})
    stored, older, uploads = out["chat"]
    assert "ChatGPT can" in stored["before"] and "Claude" not in stored["before"] and "ChatGPT couldn" in stored["error"]
    assert "Claude can" in older["before"]                    # an older answer: the AI chosen now
    b = uploads["before"]
    assert "2 pages or photos of your own files couldn't be sent as pictures, so Claude didn't see them." in b
    assert "1 page of the slide files" in b
    assert notices.markdown({"code": "uploads_unreadable", "kind": "warning", "n": 1}) == (
        "> [!WARNING]\n> 1 page or photo of your own files could not be sent as a picture, so the AI did not see it.")


def test_recordings_the_app_never_transcribes_get_no_notice(env):
    assert [n["code"] for n in env.svc.context("c", [DAY1])["notices"]] == ["not_transcribed"]
    env.svc._s.settings = Settings(min_duration_s=3000)          # part 2 (40 min) is shorter than that: never queued
    assert env.svc.context("c", [DAY1])["notices"] == []
    env.svc._s.settings = Settings()
    env.lib.set_warnings("nt1", [{"code": "no_speech"}])          # no speech on Panopto: never queued either
    assert env.svc.context("c", [DAY1])["notices"] == []
    env.lib.set_warnings("nt1", [])
    env.lib.set_status("nt1", "failed", "The recording has no sound")
    assert env.svc.context("c", [DAY1])["notices"] == []
    env.lib.set_status("nt1", "failed", "Download failed")         # another failure: worth transcribing again
    assert [n["code"] for n in env.svc.context("c", [DAY1])["notices"]] == ["not_transcribed"]


def test_a_missing_upload_is_told_apart_from_the_course_slides(env):
    from studia import chatfiles, chatslides

    class Session:
        notices, upload_text = [], ""

        def ask(self, content, on_text, stop):
            return {"text": "ok", "usage": {}, "stopped": False}

        def alive(self):
            return True

        def terminate(self):
            pass

    env.mp.setattr(chatslides.PagePicture, "path", lambda self: None)
    env.svc._session = lambda chat: (Session(), [("[Slides 1]", chatslides.PagePicture("a.pdf", 1)),
                                                 ("[Your photo]", chatfiles.UploadPicture(str(env.tmp / "gone.jpg"))),
                                                 ("[Your PDF, page 2]", chatfiles.UploadPage("b.pdf", 2))])
    chat = env.svc.create("c", [DAY1])
    seq, e = ask(env, chat["id"], "q")
    assert e["usage"]["notices"] == [{"code": "pages_unreadable", "kind": "warning", "n": 1},
                                     {"code": "uploads_unreadable", "kind": "warning", "n": 2}]
    assert e["usage"]["notices_ai"] == "claude"
    assert env.svc.view(chat["id"])["messages"][1]["usage"]["notices_ai"] == "claude"


def test_an_exported_error_of_several_lines_stays_in_its_alert(env):
    chat = env.svc.create("c", [DAY1])
    env.mp.setattr(ai.ClaudeSession, "ask", lambda self, *a, **k: (_ for _ in ()).throw(
        RuntimeError('Claude: API Error: 400\n{\n  "type": "error"\n\n}')))
    ask(env, chat["id"], "q")
    text = env.svc.export(chat["id"]).read_text(encoding="utf-8")
    block = text[text.index("> [!CAUTION]"):]
    block = block[:block.index("\n\n")]
    assert block == '> [!CAUTION]\n> Claude could not answer: Claude: API Error: 400\n> {\n>   "type": "error"\n>\n> }'
    assert notices.alert("NOTE", "one\ntwo") == "> [!NOTE]\n> one\n> two"


PLANS_PAGE = """
const stub = () => new Proxy(function () {}, { get: (t, k) => k === Symbol.toPrimitive ? () => "" : k === "length" ? 0 : stub(),
  apply: () => stub(), construct: () => stub() });
globalThis.window = globalThis;
globalThis.document = new Proxy({}, { get: (t, k) => k === "compatMode" ? "CSS1Compat" : stub() });
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.navigator = { userAgent: "node", platform: "Win32" };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;
const web = %(web)s, plans = %(plans)s;
for (const f of ["i18n.js", "icons.js", "app.js", "notices.js", "plans.js"]) require(web + "/" + f);
const St = globalThis.Studia;
St.S.plans = plans;
plans.forEach((p) => { St.S.planCache[p.id] = p; });
console.log(JSON.stringify({ list: St.views.plans({ name: "plans" }), prep: St.views.plan({ name: "plan", planId: "p2" }) }));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_failed_plan_s_card_has_the_short_reason_and_a_review_sheet_one_missing_file_warning(tmp_path):
    plans_ = [{"id": "p1", "course_id": "c", "status": "error", "error": SERVICE_500, "scope": {"weeks": [5]},
               "created_at": "2026-11-01T10:00:00"},
              {"id": "p2", "course_id": "c", "status": "done", "title": "Quiz 2 review sheet", "created_at": "2026-11-01",
               "scope": {"kind": "prep", "weeks": [5]}, "result": {"markdown": "# Review", "materials_missing": ["m2"]}}]
    script = tmp_path / "plans.js"
    script.write_text(PLANS_PAGE % {"web": json.dumps(WEB.as_posix()), "plans": json.dumps(plans_)}, encoding="utf-8")
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    state = out["list"][out["list"].index('class="err-txt"'):]
    state = state[:state.index("</span>", state.index("</svg>"))]
    assert "ChatGPT&#39;s servers had a problem" in state or "ChatGPT's servers had a problem" in state
    assert "error 500" in state and "error 500" not in state[state.index("</svg>"):]     # the whole text on hover only
    assert out["prep"].count("1 file of these weeks") == 1 and "was not downloaded" not in out["prep"]


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_slides_trimmed_because_the_course_files_took_the_pictures_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "provider", lambda: "claude")      # an AI that takes pictures (whatever this PC has)
    files = {"summary": {"missing": 0, "cut": False, "images_on": True, "files": [{"id": "a"}]},
             "catalog": {"files": [{"id": "a", "downloaded": True}]}, "images": [("[a p.1]", "x")] * 40}
    got = ChatService._notices(0, [], files, {"mode": "pick", "files": ["a"]}, 27, 20)
    assert got == [{"code": "slides_trimmed", "kind": "info", "sent": 20, "total": 27, "files": 40, "max": 60}]
    assert notices.markdown(got[0]) == ("> [!NOTE]\n> The AI got 20 of the 27 video slides: the chosen slide files used "
                                        "the other 40 of the 60 pictures a chat takes.")
    out = _run(tmp_path, {"md": [], "plans": [], "warn": [], "chat": [
        [{"seq": 2, "status": "done", "text": "x", "usage": {"notices": got}}, True],
        [{"seq": 3, "status": "done", "text": "x", "usage": {"notices": [
            {"code": "slides_trimmed", "kind": "info", "sent": 60, "total": 70}]}}, True]]})
    shared, cap = out["chat"]
    assert "20 of the 27 video slides: the chosen slide files used the other 40 of the 60 pictures" in shared["before"]
    assert "the most one chat can take" not in shared["before"] and "the most one chat can take" in cap["before"]
