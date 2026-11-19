"""4.2.0 small fixes. Try again, more sensitive, on a lecture where no speech was found: its speech is looked for with
a lower threshold, the checks against invented text stay (and what the model writes over noise, "Thank you." again
and again, is left out), and "no speech" is still said when nothing real comes out; the course page offers it once,
on a lecture with sound (not on a silent one). The quiz printed to solve on paper is in test_quizzes.py and
test_doc_words.py."""

import numpy as np
import pytest

from studia import quality, views
from studia.config import Settings
from studia.domain import Segment
from studia.engine import transcriber as tr
from studia.engine.core import SR, Decoded, Unit, compression_ratio
from studia.quality import NoSpeech

from test_engine_pipeline import FakeDecoder, FindingEngine, finding  # noqa: F401 - finding is a fixture
from test_jobs import env, finals, wait_for  # noqa: F401 - env is a fixture
from test_v4_pages import NO_SPEECH, _lib, _view, node, run

TURKISH_NOISE = "Bu kayıtta konuşma bulunamadı (yalnızca gürültü ya da müzik var). Panopto'da kontrol et."
NO_SOUND = "This recording has no sound on Panopto (the microphone was probably off)."


# ------------------------------------------------------------------ which "no speech" can be listened to again
def test_a_no_speech_lecture_with_sound_can_be_listened_to_again_once():
    assert quality.closer_listen(NO_SPEECH) == "can"                      # (saved before 4.2: by its words)
    assert quality.closer_listen([{"code": "no_speech", "message": TURKISH_NOISE}]) == "can"
    assert quality.closer_listen([{"code": "no_speech", "message": "?", "noise": True}]) == "can"
    assert quality.closer_listen([{"code": "no_speech", "message": NO_SOUND}]) == ""   # silent: nothing to listen to
    assert quality.closer_listen([{"code": "no_speech", "message": "?", "noise": True, "sensitive": True}]) == "tried"
    assert quality.closer_listen([{"code": "quiet", "message": "only noise or music"}]) == "" == quality.closer_listen([])


def _seg(a, b, text):
    return Segment(start=a, end=b, text=text, lang="en", conf=0.8)


def test_real_speech_is_more_than_one_phrase_said_over_and_over():
    # what large-v3 wrote over the owner's near-silent recordings, sure of it, for every 14-second window
    assert not quality.heard_speech([_seg(14 * k, 14 * k + 14, "Thank you.") for k in range(40)])
    assert not quality.heard_speech([_seg(0, 60, "[unclear]")])
    assert not quality.heard_speech([_seg(0, 3, "Today we look at nodal analysis and mesh currents in planar circuits.")])
    assert quality.heard_speech([_seg(0, 5, "Today we look at nodal analysis."),
                                 _seg(6, 11, "Write a current equation at every node."),
                                 _seg(12, 16, "Then solve the system for the voltages.")])


# ------------------------------------------------------------------ the engine
def test_find_speech_listens_more_closely_only_when_asked(monkeypatch):
    seen = []

    def units(audio, cancel, threshold=tr.VAD_THRESHOLD):
        seen.append(threshold)
        return ([(0, 20 * SR)], [Unit(0, 20 * SR)]) if threshold < tr.VAD_THRESHOLD else ([], [])

    monkeypatch.setattr(tr, "_units", units)
    quiet = np.full(30 * SR, 0.005, dtype=np.float32)       # sound (-46 dBFS), no speech at the usual threshold
    with pytest.raises(NoSpeech) as e:
        tr.find_speech(quiet)
    assert e.value.noise and str(e.value).startswith("No speech was found") and seen == [tr.VAD_THRESHOLD]
    s = tr.find_speech(quiet, None, 30.0, sensitive=True)
    assert seen[-1] == tr.SENSITIVE_VAD < tr.VAD_THRESHOLD and s.sensitive and s.units == [Unit(0, 20 * SR)]
    assert [w["level"] for w in s.warnings if w["code"] == "closer"] == ["warning"] and s.gain > 1.0
    with pytest.raises(NoSpeech) as e:                      # silent: "no sound", as before
        tr.find_speech(np.zeros(30 * SR, dtype=np.float32), sensitive=True)
    assert not e.value.noise and "no sound" in str(e.value)
    monkeypatch.setattr(tr, "_units", lambda audio, cancel, threshold=0.5: ([(0, 3 * SR)], [Unit(0, 3 * SR)]))
    with pytest.raises(NoSpeech) as e:                      # still too little, listening more closely
        tr.find_speech(quiet, sensitive=True)
    assert e.value.noise and str(e.value).startswith("Still no speech found, even listening more closely")


def _closer(monkeypatch, texts, sensitive=True):
    """Engine.transcribe over passages 6 s apart (5 s each) that decode, in English and sure of it, as ``texts``."""
    monkeypatch.setattr(tr, "detect_gpu", lambda *a, **k: {"vram_mb": 8000})
    units = [Unit(k * 6 * SR, k * 6 * SR + 5 * SR) for k in range(len(texts))]

    class Says(FakeDecoder):
        def generate(self, enc, batch, langs, opts):
            ks = [u.start // (6 * SR) for u in batch]
            return [Decoded(lang, texts[k], -0.2, 0.0, compression_ratio(texts[k]), 8) for k, lang in zip(ks, langs)]

    e = tr.Engine("large-v3", languages=("en",))
    e._device, e._decoder, e._wm = "cpu", Says(), object()
    warned = [{"code": "closer", "level": "warning", "message": "x"}] if sensitive else []
    speech = tr.Speech([(u.start, u.end) for u in units], units, warned, {"speech_s": 5.0 * len(units)}, 1.0, 0.1,
                       sensitive=sensitive)
    return e.transcribe(np.full(6 * SR * len(texts), 0.1, dtype=np.float32), lambda *a: None, None, {}, speech=speech)


def test_a_closer_listen_keeps_only_real_speech_and_says_no_speech_over_noise(monkeypatch):
    with pytest.raises(NoSpeech) as e:
        _closer(monkeypatch, ["Thank you."] * 30)
    assert e.value.noise and str(e.value).startswith("Still no speech found")
    said = ["Today we look at nodal analysis.", "Thank you.", "Write a current equation at every node.",
            "Then solve the system for the voltages."]
    out = _closer(monkeypatch, said)
    assert [s.text for s in out.segments] == [said[0], said[2], said[3]]        # the filler over noise left out
    assert "closer" in [w["code"] for w in out.meta["quality"]["warnings"]]
    assert [s.text for s in _closer(monkeypatch, said, sensitive=False).segments] == said    # a usual try: as before


# ------------------------------------------------------------------ the queue
class CloserEngine(FindingEngine):
    """No speech at the usual threshold; listening more closely finds some (``found``) or none."""

    def __init__(self, found=False):
        super().__init__()
        self.found = found
        self.asked = []
        self.metas = []

    def find_speech(self, audio, cancel, expected_duration, sensitive=False):
        self.asked.append(sensitive)
        if not sensitive:
            raise NoSpeech("No speech was found in this recording (only noise or music). Check it on Panopto.",
                           noise=True)
        if not self.found:
            raise quality.still_no_speech()
        return "speech of " + str(len(audio))

    def transcribe(self, audio, progress, cancel, meta, speech=None):
        self.metas.append(dict(meta))
        return super().transcribe(audio, progress, cancel, meta, speech)


def test_try_again_more_sensitive_listens_more_closely_and_says_so_when_still_none(finding):
    eng = finding["state"]["engine"] = CloserEngine()
    lib = finding["lib"]
    jm = finding["make"]()
    jm.enqueue(["r1"])
    assert wait_for(lambda: finals(finding["events"], "r1"))
    assert quality.closer_listen(lib.warnings("r1")) == "can" and lib.warnings("r1")[0]["noise"] is True
    assert finals(finding["events"], "r1")[-1]["closer"] == "can"
    finding["events"].clear()
    jm.enqueue(["r1"], sensitive=True)
    assert wait_for(lambda: finals(finding["events"], "r1"))
    assert eng.asked == [False, True] and eng.given == []
    details = [e["detail"] for e in finding["events"] if e.get("type") == "job" and e.get("id") == "r1"]
    assert "Listening more closely for speech" in details and "Finding speech" not in details
    rec = lib.get_recording("r1")
    assert rec.status == "failed" and rec.error.startswith("Still no speech found")
    assert quality.closer_listen(lib.warnings("r1")) == "tried" and finals(finding["events"], "r1")[-1]["closer"] == "tried"


def test_a_closer_listen_that_finds_speech_transcribes_the_lecture(finding):
    eng = finding["state"]["engine"] = CloserEngine(found=True)
    jm = finding["make"]()
    jm.enqueue(["r1"], sensitive=True)
    assert wait_for(lambda: finals(finding["events"], "r1"))
    assert finals(finding["events"], "r1")[-1]["stage"] == "done" and eng.asked == [True]
    assert eng.given == ["speech of 16000"] and eng.metas[0]["sensitive"] is True


def test_the_api_asks_for_a_closer_listen_only_when_told(isolated_home, monkeypatch):
    from studia.api import Api
    from studia.jobs import JobManager
    from studia.services import Services

    asked = []
    monkeypatch.setattr(JobManager, "enqueue", lambda self, ids, batch=False, **k: asked.append((list(ids), k)) or 1)
    api = Api(Services())
    api._push = lambda e: None
    from test_v4_pages import _fill

    _fill(api._s.lib)
    assert api.transcribe(["c1"], True) == {"ok": True, "queued": 1}
    assert api.transcribe(["d1"])["ok"]
    assert asked == [(["c1"], {"sensitive": True}), (["d1"], {})]          # (a usual one as before)


# ------------------------------------------------------------------ the course page's data
def test_the_course_page_knows_which_lectures_can_be_listened_to_more_closely(tmp_path):
    lib = _lib(tmp_path)        # 7 Oct: part 3 has no speech; 12 Oct: one part no speech, one no sound (its error)
    days = {d["date"]: d for d in _view(lib)["days"]}
    assert days["2026-10-07"]["closer"] == ["b3"] and days["2026-10-12"]["closer"] == ["c1"]
    assert days["2026-10-05"]["closer"] == [] == days["2026-10-14"]["closer"]
    recs = {r["id"]: r for d in days.values() for r in d["recordings"]}
    assert (recs["c1"]["closer"], recs["c2"]["closer"], recs["d1"]["closer"]) == ("can", "", "")
    queued = {d["date"]: d for d in _view(lib, jobs={"c1": {"id": "c1", "stage": "queued", "progress": 0.0,
                                                            "detail": ""}})["days"]}
    assert queued["2026-10-12"]["closer"] == []                                 # (being listened to now)
    lib.set_warnings("c1", [{**NO_SPEECH[0], "sensitive": True}])
    days = {d["date"]: d for d in _view(lib)["days"]}
    assert days["2026-10-12"]["closer"] == [] and days["2026-10-12"]["recordings"][0]["closer"] == "tried"
    detail = views.course_detail(lib, Settings(min_duration_s=60), "a")
    week_days = {d["date"]: d for w in detail["weeks"] for d in w["days"]}
    assert week_days["2026-10-07"]["closer"] == ["b3"]                          # (the Weeks tab's chips have it)


# ------------------------------------------------------------------ the page
@node
def test_a_lecture_with_no_speech_offers_try_again_more_sensitive_once(tmp_path):
    out = run(tmp_path, """
  St.sq = { card: () => "", line: () => "" };
  const calls = [];
  St.Mock.transcribe = (ids, sensitive) => { calls.push([ids, sensitive]); return { ok: true, queued: ids.length }; };
  const rec = (id, part, extra) => ({ id, part, parts: 2, time: "09:00", end_time: "10:00", duration_s: 3000, status: "failed",
    mine: true, warnings: [], anomalies: [], viewer_url: "u", no_sound: true, closer: "", ...extra });
  const day = { day_key: "e|2026-09-30|lecture", date: "2026-09-30", label: "Wed 30 Sep", kind: "lecture", kind_label: "Lecture", parts: 2,
    transcribed: false, any_done: false, no_sound: true, is_new: false, mine: true, duration_s: 6000, live: false, ready: false, anomalies: [],
    closer: ["r1"], recordings: [rec("r1", 1, { closer: "can", error: "No speech was found in this recording (only noise or music). Check it on Panopto." }),
                                 rec("r2", 2, { error: "This recording has no sound on Panopto (the microphone was probably off)." })] };
  S.courses = [{ id: "e", code: "EEE 215", display_name: "Circuits", has_panopto: true, days: [day], available_sections: [], show_mode: "all",
    content: {}, counts: { total: 2, done: 0, new: 0, processing: 0, done_any: 0, days: 1, days_done: 0, days_no_sound: 1, other_sections: 0 } }];
  S.cd = { e: { id: "e", weeks: [{ no: 2, start: "2026-09-28", end: "2026-10-04", past: true, current: true, exam: false, topic_en: "Nodal analysis",
    topic_tr: "", units: [], days: [day], classes: [] }], general: [], materials: { total: 0, downloaded: 0, new: 0 }, syllabus: {},
    alerts: { items: [], plan: [], counts: {} }, announcements: [], outcomes_en: [], outcomes_tr: [], grading: [] } };
  const page = (tab) => plain(St.views.course({ name: "course", courseId: "e", tab }));
  out.lectures = page("lectures");
  out.weeks = page("weeks");
  const btn = { dataset: { ids: "r1" }, disabled: false };
  await A["tx-closer"](btn); await tick();
  out.calls = calls; out.toasts = told(); out.disabled = btn.disabled;
  out.after = page("lectures");
  Object.assign(day.recordings[0], { closer: "tried", error: "Still no speech found, even listening more closely (only noise or music). Check it on Panopto." });
  day.closer = [];
  out.tried = page("lectures");
""")
    lec = out["lectures"]
    head = lec.split('class="day-actions">')[1].split("</div>")[0]
    assert 'data-act="tx-closer" data-ids="r1"' in head and "Try again, more sensitive</button>" in head
    row1, row2 = lec.split('data-rid="r1"')[1].split("</div>")[0], lec.split('data-rid="r2"')[1].split("</div>")[0]
    assert 'data-act="tx-closer" data-ids="r1" title="Try again, more sensitive"' in row1 and "only noise or music" in row1
    assert 'data-act="retry"' in row2 and "tx-closer" not in row2 and "No sound on Panopto" in row2   # silent: no
    chip = out["weeks"].split('class="lec ')[1].split("</div>")[0]
    assert "No speech found" in chip and 'data-act="tx-closer" data-ids="r1"' in chip
    assert out["calls"] == [[["r1"], True]] and out["disabled"] is True
    assert out["toasts"][-1].startswith("info: Listening again, more closely this time. It takes about as long")
    assert "Try again, more sensitive</button>" not in out["after"]          # asked: not offered again meanwhile
    tried = out["tried"]
    assert "only noise, even listening closely: check it on Panopto" in tried and "tx-closer" not in tried
