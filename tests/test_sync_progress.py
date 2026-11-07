"""A sync says what it is doing and how far it is (which step, how many of your courses), the files and announcements
after it too; and when IEULearn or Panopto is slow, it says what it still waits for and for how long, instead of a
spinner that could be a hang."""

import threading
import time

from studia.api import Api, stall_text
from test_page_feedback import node, run
from test_sync import _service, site  # noqa: F401 - the fake IEULearn and Panopto (a fixture)


def test_a_sync_counts_the_courses_it_has_looked_at(site, tmp_path):
    lib, sync = _service(tmp_path)
    said = []
    sync.sync(progress=said.append)                      # the first sync: each course opened, then listed
    assert said[0] == "Checking IEULearn and Panopto…"
    assert "Connecting to Panopto: 2 of 2 courses…" in said and said[-1] == "Looking for new recordings: 2 of 2 courses…"
    said.clear()
    sync.sync(progress=said.append)                      # later: listed while the dashboard loads
    assert "Looking for new recordings: 1 of 2 courses…" in said and "Looking for new recordings: 2 of 2 courses…" in said


def test_the_files_and_announcements_count_too(site, tmp_path, monkeypatch):
    from test_sync import _course_pages

    _course_pages(monkeypatch)
    lib, sync = _service(tmp_path)
    sync.sync()
    said = []
    sync.sync_content(progress=said.append)
    assert said == ["Loading course content…", "Course files and announcements: 1 of 2 courses…",
                    "Course files and announcements: 2 of 2 courses…"]


def test_what_a_slow_sync_waits_for_is_known(site, tmp_path):
    lib, sync = _service(tmp_path)
    assert sync.slow(0.0) == []
    with sync._asking("Panopto", "EEE 215"), sync._asking("Panopto", "MATH 240"), sync._asking("IEULearn"):
        time.sleep(0.02)
        waits = sync.slow(0.01)
        assert {w[0]: w[1] for w in waits} == {"Panopto": ["EEE 215", "MATH 240"], "IEULearn": []}
        assert sync.slow(60.0) == []
    assert sync.slow(0.0) == []                          # answered: nothing waits any more


def test_a_sync_that_seems_to_stand_still_says_what_it_waits_for(monkeypatch):
    from studia import api as api_mod

    monkeypatch.setattr(api_mod, "STALL_S", 0.05)
    pushed, done = [], threading.Event()
    moved = {"at": time.monotonic() - 1, "type": "content", "offline": False}
    watcher = threading.Thread(target=Api._watch, args=(lambda s: [("IEULearn", ["EEE 215"], 42.4)], moved,
                                                        pushed.append, done, 0.01))
    watcher.start()
    time.sleep(0.15)
    done.set()
    watcher.join(2)
    assert pushed and pushed[0] == {"type": "content", "phase": "progress", "slow": True,
                                    "message": "IEULearn is slow to answer (EEE 215): still waiting, 42 s…"}
    pushed.clear()
    done.clear()
    moved.update(offline=True)                           # waiting for the internet says so itself
    watcher = threading.Thread(target=Api._watch, args=(lambda s: [("Panopto", [], 30.0)], moved, pushed.append, done,
                                                        0.01))
    watcher.start()
    time.sleep(0.1)
    done.set()
    watcher.join(2)
    assert pushed == []
    assert stall_text("Panopto", [], 30.0) == "Panopto is slow to answer: still waiting, 30 s…"


@node
def test_the_top_bar_shows_how_far_the_files_and_announcements_are(tmp_path):
    out = run(tmp_path, """
  ev({ type: "sync", phase: "start", message: "Syncing…" });
  ev({ type: "sync", phase: "progress", message: "Looking for new recordings: 3 of 6 courses…" });
  out.listing = top();
  ev({ type: "sync", phase: "done", message: "Up to date", new_count: 0, errors: [] });
  ev({ type: "content", phase: "start" });
  ev({ type: "content", phase: "progress", message: "Course files and announcements: 2 of 6 courses…" });
  out.content = top();
  ev({ type: "content", phase: "progress", message: "IEULearn is slow to answer (EEE 215): still waiting, 40 s…", slow: true });
  out.slow = top();
  ev({ type: "content", phase: "done", new_materials: 0, new_announcements: 0, errors: [] });
  out.done = top();
""")
    assert "Looking for new recordings: 3 of 6 courses…" in out["listing"]
    assert "Course files and announcements: 2 of 6 courses…" in out["content"]
    assert "IEULearn is slow to answer (EEE 215): still waiting, 40 s…" in out["slow"]
    assert "Synced" in out["done"] and "courses…" not in out["done"]
