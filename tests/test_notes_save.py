"""AI study notes and an output folder that can't take them (a drive not plugged in, Windows' ransomware protection,
an old file held open by Word): the AI is not asked before the folder is known to take a file, and an answer that
can't be saved is kept in the profile's own folder instead of being lost."""

import datetime as dt
import pathlib

import pytest

from studia import ai, paths, store
from studia.domain import Course, Recording, Segment, Transcript
from studia.services import Services

DAY = "c|2026-09-23|lecture"


@pytest.fixture
def svc():
    s = Services()
    lib = s.lib
    lib.upsert_courses([Course(id="c", title="2026-1 EEE 215 P 1", code="EEE 215", name="Circuits", term="T")])
    lib.merge_recordings("c", [Recording(delivery_id="d1", course_id="c", folder_id="f", folder_path="", name="n",
                                         start=dt.datetime(2026, 9, 23, 9), duration_s=3000, viewer_url="v")],
                         first_sync=True)
    tr = Transcript("d1", [Segment(0, 5, "Today: Ohm's law.", "en", 0.9)], 3000, 5, {"en": 5}, "m", "cpu", "t",
                    "2026-09-23T12:00:00")
    lib.save_transcript(tr, store.save_transcript(tr))
    lib.set_status("d1", "done")
    yield s
    s.shutdown()
    s.lib.close()


@pytest.fixture
def asked(monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "run_claude", lambda prompt, text, images, **k: calls.append(prompt) or
                        {"text": "## Ohm\n\nV = IR.", "usage": {}, "model": "m"})
    return calls


def test_a_folder_that_cant_be_made_stops_before_the_ai(svc, asked, tmp_path):
    (tmp_path / "Q").write_text("not a drive")                         # mkdir fails there, as on a missing drive
    svc.update_settings({"output_dir": str(tmp_path / "Q" / "IEUStudia")})
    with pytest.raises(OSError):
        svc.ai_notes(DAY, "notes", False, "sonnet")
    assert asked == []


def test_a_folder_that_refuses_files_stops_before_the_ai(svc, asked, monkeypatch):
    real = pathlib.Path.write_bytes

    def guarded(self, data):                                           # Controlled folder access: the folder is
        if self.parent.name == "AI notes":                             # there, writing in it is denied
            raise PermissionError(13, "Access is denied", str(self))
        return real(self, data)

    monkeypatch.setattr(pathlib.Path, "write_bytes", guarded)
    with pytest.raises(PermissionError):
        svc.ai_notes(DAY, "notes", False, "sonnet")
    assert asked == []


def test_an_answer_that_cant_be_saved_is_kept(svc, asked, monkeypatch):
    from studia import services

    def held(path, text):                                              # the old notes open in Word
        raise PermissionError(13, "Access is denied", str(path))

    monkeypatch.setattr(services, "atomic_write", held)
    with pytest.raises(RuntimeError) as err:
        svc.ai_notes(DAY, "notes", False, "sonnet")
    assert len(asked) == 1
    copies = list((paths.profile_dir() / "Unsaved notes").glob("*.md"))
    assert len(copies) == 1 and "V = IR." in copies[0].read_text(encoding="utf-8")
    assert str(copies[0]) in str(err.value) and "Windows did not let IEUStudia use" in str(err.value)


def test_notes_are_written_as_before(svc, asked):
    path = svc.ai_notes(DAY, "notes", False, "sonnet")
    assert path.read_text(encoding="utf-8").startswith("## Ohm") and not path.with_name(path.name + ".tmp").exists()
    assert not (paths.profile_dir() / "Unsaved notes").exists()
