"""The transcripts kept in memory belong to their folder: the same recording id in another profile's (or home's) folder
is another transcript, or none."""
from studia import store
from studia.domain import Segment, Transcript


def _tr(did: str, text: str) -> Transcript:
    return Transcript(delivery_id=did, segments=[Segment(0.0, 5.0, text, "en", 0.9)], duration_s=60, speech_s=5,
                      language_seconds={}, model="m", device="cpu", engine_version="t",
                      created_at="2026-11-09T12:00:00")


def test_a_transcript_kept_in_memory_is_not_read_from_another_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIA_HOME", str(tmp_path / "one"))
    store.save_transcript(_tr("r1", "first home"))
    assert store.load_transcript("r1").segments[0].text == "first home"

    monkeypatch.setenv("STUDIA_HOME", str(tmp_path / "two"))      # another home: r1 was never transcribed there
    assert store.load_transcript("r1") is None
    store.save_transcript(_tr("r1", "second home"))
    assert store.load_transcript("r1").segments[0].text == "second home"

    monkeypatch.setenv("STUDIA_HOME", str(tmp_path / "one"))
    assert store.load_transcript("r1").segments[0].text == "first home"
    store.forget("r1")                                              # forgets only this folder's copy
    monkeypatch.setenv("STUDIA_HOME", str(tmp_path / "two"))
    assert store.load_transcript("r1").segments[0].text == "second home"
