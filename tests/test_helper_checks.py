"""The tray helper's checks: when it checks (screen off, waking up, paused), what it writes, the reminders' plan, and
how a notification click reaches the open window."""

import contextlib
import ctypes
import datetime as dt
import sys
import threading
import types

import pytest

from studia import agent, appconfig
from studia.agent import BACK_S, SCREEN_OFF_EVERY, next_check
from studia.config import Settings
from studia.coursesite import Announcement, Resource, Unit
from studia.domain import Course, Recording
from studia.library import Library
from studia.net import NetworkError

H = 1800.0                                   # the default interval: 30 minutes


# ----------------------------------------------------------------------------- when to check
def test_screen_on_checks_every_interval():
    assert next_check(1.7e9, 0.0, H) == 0                         # never checked: now
    assert next_check(10_000.0, 10_000.0 - 600, H) == H - 600      # 10 minutes ago: in 20
    assert next_check(10_000.0, 10_000.0 - H, H) == 0


def test_screen_off_skips_due_checks_but_keeps_a_safety_check():
    last = 100_000.0
    assert next_check(last + H, last, H, screen_on=False) == (SCREEN_OFF_EVERY - 1) * H     # due, but nobody looks
    assert next_check(last + 3 * H, last, H, screen_on=False) == H
    assert next_check(last + SCREEN_OFF_EVERY * H, last, H, screen_on=False) == 0          # the safety check


def test_screen_on_or_waking_up_after_hours_checks_after_wifi_is_back():
    last = 100_000.0
    back = last + 5 * 3600                                       # the screen comes on (or the PC wakes) hours later
    assert next_check(back, last, H, screen_on=True, back_at=back) == BACK_S
    assert next_check(back + BACK_S, last, H, screen_on=True, back_at=back) == 0
    # back soon after a check: the usual interval
    assert next_check(last + 600, last, H, screen_on=True, back_at=last + 600) == H - 600
    # woken up with the screen still off (a maintenance wake at night): nobody to tell
    assert next_check(last + 3 * H, last, H, screen_on=False, back_at=last + 3 * H) == H
    assert next_check(back, last, H, screen_on=False, back_at=back) == BACK_S             # (the safety check)
    assert next_check(last + 60, last, H, back_at=last - 10) == H - 60    # (back before the last check: past)


def test_check_now_and_paused_notifications():
    assert next_check(5.0, 4.0, H, screen_on=False, manual=True) == 0     # "Check IEULearn now": always
    assert next_check(5.0, 4.0, H, manual=True, paused=True) == 0
    assert next_check(10 * H, 0.0, H, paused=True) is None                 # paused: no checks at all
    assert next_check(500.0, 1000.0, H) == H                               # the clock was set back


class _Script:
    """Stands in for the helper's wake event: each wait runs the next step of the test (it may move the clock or
    send power events), and the helper stops when the steps run out."""

    def __init__(self, helper, clock, steps):
        self.helper, self.clock, self.steps, self.waits = helper, clock, list(steps), []
        self.flag = False

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if not self.steps:
            self.helper.stop.set()
            return True
        self.steps.pop(0)()
        return True

    def set(self):
        self.flag = True

    def clear(self):
        self.flag = False

    def is_set(self):
        return self.flag


@pytest.fixture
def helper(monkeypatch):
    from studia import osutil

    appconfig.update({"tray": True, "sync_minutes": 30})
    clock = [1_000_000.0]
    monkeypatch.setattr(agent, "time", types.SimpleNamespace(time=lambda: clock[0], strftime=lambda f: "12:00"))
    monkeypatch.setattr(agent, "ui_running", lambda: False)
    monkeypatch.setattr(osutil.impl(), "background_work", contextlib.nullcontext)   # this test process keeps its priority
    h = agent.Helper(at_login=False)
    h.checks = []
    h._check = lambda: h.checks.append(clock[0]) or False
    h._check_update = lambda: None
    return h, clock


def _run(h, clock, steps):
    h.wake = _Script(h, clock, steps)
    h._loop()
    return h.wake.waits


def test_the_helper_waits_out_the_night_and_checks_soon_after_the_screen_comes_on(helper):
    h, clock = helper
    start = clock[0]

    def advance(s):
        return lambda: clock.__setitem__(0, clock[0] + s)

    waits = _run(h, clock, [
        advance(0),                                  # the first wait (after start-up)
        lambda: (h._power("off"), advance(H)()),     # checked; the screen goes off, half an hour passes
        advance(H), advance(H),                      # 1.5 h: nothing due while nobody looks
        advance(H),                                  # 2 h without a check: the safety check
        advance(3 * H),                              # then the night goes on
        lambda: (advance(600)(), h._power("on")),    # the screen comes on in the morning
        advance(BACK_S),                             # Wi-Fi is back: the check
    ])
    assert h.checks == [start, start + 4 * H, start + 7 * H + 600 + BACK_S]
    assert waits[0] == 20 and BACK_S in waits                    # the start-up delay; then Wi-Fi first


def test_a_resume_checks_at_once_although_the_wait_was_not_over(helper):
    h, clock = helper
    start = clock[0]
    _run(h, clock, [lambda: None,
                    lambda: (clock.__setitem__(0, clock[0] + 9 * 3600), h._power("resume")),   # slept 9 h
                    lambda: clock.__setitem__(0, clock[0] + BACK_S)])
    assert h.checks == [start, start + 9 * 3600 + BACK_S]


def test_paused_notifications_pause_the_checks_and_check_now_still_works(helper):
    h, clock = helper
    start = clock[0]

    def pause():
        h._command(agent.CMD_PAUSE)
        clock[0] += 5 * H

    def check_now():
        h._command(agent.CMD_SYNC)
        clock[0] += 60

    def unpause():
        h._command(agent.CMD_PAUSE)
        clock[0] += 60

    _run(h, clock, [lambda: None, pause, lambda: None, check_now, lambda: clock.__setitem__(0, clock[0] + 2 * H),
                    unpause, lambda: None])
    # the first check; none while paused, except the one asked for; at once after the pause, as a check was due
    assert h.checks == [start, start + 5 * H + 60, start + 7 * H + 120]
    assert not h.paused


def test_the_screen_events_from_the_tray_window(helper):
    h, clock = helper
    h._power("on")                                    # Windows says the current state when the window registers
    assert h.screen_on and not h.wake.is_set() and h.back_at == 0
    h._power("off")
    assert not h.screen_on and not h.wake.is_set()
    clock[0] += 100
    h._power("on")
    assert h.screen_on and h.wake.is_set() and h.back_at == clock[0]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows power messages")
def test_the_tray_window_reads_the_power_broadcasts():
    from studia import tray

    def setting(guid, value):
        s = tray.POWERBROADCAST_SETTING(guid, 4, value)
        return s, ctypes.addressof(s)

    ev = tray.Tray._power_event
    for value, want in ((0, "off"), (1, "on"), (2, "on")):                        # dimmed counts as on
        s, addr = setting(tray.GUID_CONSOLE_DISPLAY_STATE, value)
        assert ev(tray.PBT_POWERSETTINGCHANGE, addr) == want
    other = tray.GUID(1, 2, 3, (ctypes.c_ubyte * 8)())
    s, addr = setting(other, 0)
    assert ev(tray.PBT_POWERSETTINGCHANGE, addr) == ""
    assert ev(tray.PBT_APMRESUMEAUTOMATIC, 0) == "resume" and ev(0x4, 0) == ""             # (PBT_APMSUSPEND)


# ----------------------------------------------------------------------------- what a check writes
def _rec(did, course, day, hour=9, name="Lecture"):
    return Recording(delivery_id=did, course_id=course, folder_id="f", folder_path="", name=name,
                     start=dt.datetime(2026, 10, day, hour, 0), duration_s=3000, viewer_url=f"https://v/{did}")


def _units(title="Week01"):
    return [Unit("u1", 0, "Hafta 1", dt.date(2026, 10, 5), dt.date(2026, 10, 11), "",
                 [Resource("m1", title, "Week01.pdf", "pdf", "pdf", "u", "d"),
                  Resource("m2", "Week01b", "Week01b.pdf", "pdf", "pdf", "u2", "d")]),
            Unit("u2", 1, "Hafta 2", dt.date(2026, 10, 12), dt.date(2026, 10, 18), "")]


def test_a_check_with_nothing_new_writes_nothing_to_the_library(tmp_path):
    lib = Library(tmp_path / "lib.db", app_version="2.5.0")
    lib.upsert_courses([Course(id="a", title="t", code="MATH 240", name="Probability")])
    recs = [_rec("a1", "a", 5), _rec("a2", "a", 12), _rec("a3", "a", 12, hour=9)]      # a3: a2's duplicate
    lib.merge_recordings("a", recs, first_sync=True)
    lib.merge_units("a", _units(), first_sync=True)
    before = lib.db.total_changes
    assert lib.merge_recordings("a", recs, first_sync=False) == []
    assert lib.merge_units("a", _units(), first_sync=False) == []
    assert lib.db.total_changes == before                                    # the same listing: no row written
    lib.close()
    again = Library(tmp_path / "lib.db", app_version="2.5.0")
    assert again.db.total_changes == 0 and again.get_meta("app_version") == "2.5.0"   # the same version: not rewritten
    again.close()
    newer = Library(tmp_path / "lib.db", app_version="2.6.0")
    assert newer.db.total_changes == 1 and newer.get_meta("app_version") == "2.6.0"
    newer.close()


def test_what_changed_is_still_written(tmp_path):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="a", title="t", code="MATH 240", name="Probability")])
    lib.merge_recordings("a", [_rec("a1", "a", 5), _rec("a2", "a", 12)], first_sync=True)
    lib.merge_units("a", _units(), first_sync=True)
    lib.merge_recordings("a", [_rec("a1", "a", 5, name="Lecture 1 (edited)")], first_sync=False)   # a2 gone
    assert {r.delivery_id: (r.name) for r in lib.recordings("a")} == {"a1": "Lecture 1 (edited)"}
    assert lib.merge_recordings("a", [_rec("a1", "a", 5, name="Lecture 1 (edited)"), _rec("a2", "a", 12)],
                                first_sync=False) == []                           # back again: not news
    assert {r.delivery_id for r in lib.recordings("a")} == {"a1", "a2"}
    lib.set_material_file("m1", str(tmp_path / "w.pdf"), 4, "u")
    lib.clear_new_materials("a")
    units = _units(title="Week 1 slides")
    units[0].resources[0].url = "u-new"                                         # a new version of a downloaded file
    units[1].title = "Hafta 2: Bayes"
    lib.merge_units("a", units, first_sync=False)
    m1 = lib.get_material("m1")
    assert m1["title"] == "Week 1 slides" and m1["url"] == "u-new" and m1["is_new"] == 1
    assert [u["title"] for u in lib.units("a")] == ["Hafta 1", "Hafta 2: Bayes"]
    lib.merge_units("a", units[:1], first_sync=False)                          # m2 still there; u2 left the plan
    assert [u["unit_id"] for u in lib.units("a")] == ["u1"]
    lib.merge_units("a", [Unit("u1", 0, "Hafta 1", resources=[units[0].resources[1]])], first_sync=False)
    assert lib.get_material("m1")["gone"] == 1 and lib.get_material("m2")["gone"] == 0
    lib.close()


def test_a_check_writes_what_was_told_only_when_something_was_added(monkeypatch, isolated_home):
    from studia import calendar_export, sync

    found = {"rec": [], "ann": [], "mat": []}
    every, panopto_every = sync.SyncService.HELPER_UNITS_EVERY, sync.SyncService.HELPER_PANOPTO_EVERY

    class FakeSync:
        HELPER_UNITS_EVERY = every
        HELPER_PANOPTO_EVERY = panopto_every

        def __init__(self, lib, settings, log):
            pass

        def has_credentials(self):
            return True

        def sync(self, full_every=None, dashboard=True, partial=lambda ids: None):
            assert full_every == self.HELPER_PANOPTO_EVERY          # the helper probes Panopto's folders
            assert dashboard is False                               # ... and reads the small course list
            if found.get("fail"):                                   # merged, then IEULearn failed (sync.sync says)
                partial(found["rec"])
                raise found["fail"]
            return types.SimpleNamespace(new_ids=found["rec"])

        def sync_content(self, progress=None, units_every=None):
            if found.get("content_fails"):
                raise RuntimeError("500")
            return {"new_announcements": found["ann"], "new_materials": found["mat"]}

    monkeypatch.setattr(sync, "SyncService", FakeSync)
    monkeypatch.setattr(calendar_export, "assessment_events", lambda lib, settings: [])
    saved = []
    monkeypatch.setattr(agent, "_save_seen", saved.append)
    h = types.SimpleNamespace(cfg=appconfig.load(), paused=False, told_rejected=False, last="", untold=[],
                              tray=types.SimpleNamespace(set_tooltip=lambda t: None, notify=lambda *a: None))
    assert agent.Helper._check(h) is False and saved == []                    # nothing new: notified.json stays
    found["ann"] = ["x1"]
    agent.Helper._check(h)
    assert saved and saved[-1]["ann"] == ["x1"]

    told = []
    monkeypatch.setattr(agent, "summarize", lambda lib, settings, cfg, recs, *rest: told.append(recs))
    found.update(ann=[], rec=["r1"], fail=NetworkError("IEULearn isn't responding right now."))
    agent.Helper._check(h)                               # r1 was merged before IEULearn failed: kept, not lost
    found.update(rec=["r2"], fail=None, content_fails=True)
    agent.Helper._check(h)                               # the course content failed: the recordings wait too
    assert told == []
    found.update(rec=[], content_fails=False)
    agent.Helper._check(h)
    assert told == [["r1", "r2"]] and h.untold == []


# ----------------------------------------------------------------------------- the reminders' plan
def _course_lib(tmp_path):
    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="a", title="t", code="MATH 240", name="Probability", term="2026-1"),
                        Course(id="c", title="t", code="EEE 215", name="Circuits", term="2026-1"),
                        Course(id="d", title="t", code="ENG 101", name="English")])
    lib.merge_recordings("a", [_rec("a1", "a", 6, 9), _rec("a2", "a", 8, 11), _rec("a3", "a", 13, 9),
                               _rec("a4", "a", 15, 11), _rec("a5", "a", 20, 9), _rec("a6", "a", 22, 11)], first_sync=True)
    lib.merge_recordings("c", [_rec("c1", "c", 7, 13), _rec("c2", "c", 14, 13)], first_sync=True)
    lib.merge_units("a", [Unit("u1", 0, "Hafta 1", dt.date(2026, 10, 5), dt.date(2026, 10, 11)),
                          Unit("u5", 1, "Hafta 5", dt.date(2026, 11, 3), dt.date(2026, 11, 8)),     # a day late
                          Unit("u9", 2, "Konu 9")], first_sync=True)
    lib.merge_units("c", [Unit("v1", 0, "Konu 1")], first_sync=True)       # no dates: the term's calendar
    lib.set_course_info("a", syllabus={"weeks": [{"no": 5, "subject": "Bayes"}, {"no": 8, "subject": "Midterm Exam"},
                                                 {"no": 14, "subject": "Review"}],
                                       "outcomes": ["count", "Bayes", "limits"]},
                        overview={"weeks": [{"no": 15, "topic": "Final sınavı"}]})
    lib.set_course_info("c", syllabus={"weeks": [{"no": 7, "subject": "MIDTERM"}]})
    lib.set_course_info("d", syllabus={"weeks": [{"no": 8, "subject": "Midterm"}]})   # no dates at all: no events

    def ann(aid, title, body, day):
        return Announcement(aid, title, "Dr", dt.datetime(2026, 10, day, 9), body)

    lib.merge_announcements("a", [ann("q2", "Quiz 2", "Quiz 2 will be held in week 5. It covers LO1-LO2.", 1),
                                  ann("m1", "Midterm 1", "Midterm 1 is in week 8 and covers LO1-LO3.", 2),
                                  ann("q3", "Quiz 3", "Quiz 3 will be in week 9.", 3),
                                  ann("q4", "Quiz 4", "Quiz 4 is in week 11.", 4)], first_sync=True)
    lib.merge_announcements("c", [ann("cq", "Quiz 1", "Quiz 1 will be in week 3.", 1)], first_sync=True)
    lib.merge_announcements("d", [ann("dq", "Quiz 1", "Quiz 1 will be in week 3.", 1)], first_sync=True)
    return lib


def _events_through_course_detail(lib, settings, today):
    """assessment_events as 2.5.0 built them, from views.course_detail."""
    from studia import calendar_export, views

    real = calendar_export._course

    def detail(lib, settings, course):
        return views.course_detail(lib, settings, course.id)

    calendar_export._course = detail
    try:
        return calendar_export.assessment_events(lib, settings, today)
    finally:
        calendar_export._course = real


def test_the_reminders_plan_is_the_same_without_reading_the_lectures(tmp_path, monkeypatch):
    from studia import alerts, calendar_export

    lib = _course_lib(tmp_path)
    s = Settings(min_duration_s=300)
    for today in (dt.date(2026, 10, 1), dt.date(2026, 11, 4), dt.date(2026, 12, 1)):
        want = _events_through_course_detail(lib, s, today)
        with monkeypatch.context() as m:
            m.setattr(alerts, "course_alerts", lambda *a, **k: pytest.fail("reads the lectures"))
            got = calendar_export.assessment_events(lib, s, today)
        assert got == want
    evs = calendar_export.assessment_events(lib, s, dt.date(2026, 10, 1))
    assert {(e["course"], e["name"]) for e in evs} >= {("MATH 240", "Quiz 2"), ("MATH 240", "Midterm 1"),
                                                       ("MATH 240", "Final sınavı"), ("EEE 215", "Quiz 1"),
                                                       ("EEE 215", "MIDTERM")}
    quiz2 = next(e for e in evs if e["uid"] == "a-quiz-2")
    assert quiz2["start"] == "2026-11-03" and quiz2["classes"]                 # the unit's dates, a class day
    assert not any(e["course"] == "ENG 101" for e in evs)
    lib.close()


# ----------------------------------------------------------------------------- a notification click
@pytest.mark.skipif(sys.platform != "win32", reason="a named Windows event (Linux's window polls the file)")
def test_an_open_request_wakes_the_window_at_once(isolated_home):
    from studia.winutil import instance_name, open_request_event, request_open, take_open_request, wait_event

    name = instance_name()
    event = open_request_event(name)
    assert event
    assert not wait_event(event, 0)                                            # nothing asked yet
    threading.Thread(target=request_open, args=(name, "course:a:lectures")).start()
    assert wait_event(event, 10)
    assert take_open_request(name) == "course:a:lectures" and take_open_request(name) == ""
    assert not wait_event(event, 0)                                            # auto-reset
    request_open(name, "home")                                                 # written before anyone waits:
    assert take_open_request(name) == "home"                                   # the file still has it


def test_the_window_shows_the_page_a_click_asked_for(isolated_home, monkeypatch):
    from studia.api import Api
    from studia.osutil import instance_name, request_open

    pushed = []
    got = threading.Event()
    ns = types.SimpleNamespace(_push=lambda e: (pushed.append(e), got.set()))
    request_open(instance_name(), "home")                                      # before the window waits
    threading.Thread(target=Api._watch_open_requests, args=(ns,), daemon=True).start()
    assert got.wait(10) and pushed == [{"type": "open", "target": "home"}]
    got.clear()
    request_open(instance_name(), "course:b:announcements")
    assert got.wait(10) and pushed[-1] == {"type": "open", "target": "course:b:announcements"}
