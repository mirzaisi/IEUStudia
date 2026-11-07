"""Opening Panopto through IEULearn (the LTI launch) for courses whose folder isn't known yet: a new library where it
fails for every course used to say "Up to date" with no lectures anywhere; now it says so and what to do. A course
whose page has no Panopto at all is still no error."""

import pytest

from studia import sync as sync_mod
from test_sync import _service, site  # noqa: F401 - the fake IEULearn and Panopto (a fixture)


def _failing(site, which):
    def launch(self, cid):
        site.launches.append(cid)
        if cid in which:
            raise RuntimeError(f"Panopto did not open the folder of course {cid}.")
        return site.folders.get(cid)
    return launch


def test_a_new_library_where_panopto_opens_for_no_course_says_so(site, tmp_path, monkeypatch):
    lib, sync = _service(tmp_path)
    site.panopto = False                                  # (no Panopto sign-in yet: a new student)
    monkeypatch.setattr(sync_mod.IEULearn, "launch_panopto", _failing(site, {"a", "b"}))
    res = sync.sync()
    assert res.errors == ["Panopto didn't open through IEULearn for any of your courses, so no recordings could be "
                          "looked for. Open the Panopto page of one of your courses on learn.ieu.edu.tr in your "
                          "browser once (it may ask you to confirm something), then sync again."]
    assert [c.folder_id for c in lib.courses()] == [None, None] and all(c.has_panopto for c in lib.courses())
    site.launches.clear()
    monkeypatch.setattr(sync_mod.IEULearn, "launch_panopto", _failing(site, set()))
    site.panopto = True                                   # you opened it once in the browser: the next sync works
    res = sync.sync()
    assert res.errors == [] and sorted(site.launches) == ["a", "b"] and lib.has_recordings("a")


def test_one_course_that_doesnt_open_is_named(site, tmp_path, monkeypatch):
    lib, sync = _service(tmp_path)
    monkeypatch.setattr(sync_mod.IEULearn, "launch_panopto", _failing(site, {"b"}))
    res = sync.sync()
    assert res.errors == ["Panopto didn't open through IEULearn for EEE 215: their recordings are looked for again at "
                          "the next sync."]
    assert lib.get_course("a").folder_id == "fa" and lib.has_recordings("a")


def test_a_download_whose_course_panopto_wont_open_names_the_course_and_what_to_do(site, tmp_path, monkeypatch):
    lib, sync = _service(tmp_path)
    sync.sync()
    monkeypatch.setattr(sync_mod.IEULearn, "launch_panopto", _failing(site, {"a"}))
    with pytest.raises(RuntimeError, match=r"Panopto didn't open MATH 240 through IEULearn, so the lecture couldn't be "
                                           r"downloaded\. Try again in a few minutes; if it keeps happening, open"):
        sync.ensure_panopto("a")
    monkeypatch.setattr(sync_mod.IEULearn, "launch_panopto", _failing(site, set()))
    site.panopto = False                                  # it opened, yet Panopto still says signed out
    with pytest.raises(RuntimeError, match="open a course's Panopto page on learn.ieu.edu.tr in your browser once"):
        sync.ensure_panopto("a")


@pytest.mark.parametrize("folders", [{}, {"a": "fa"}])
def test_courses_whose_page_has_no_panopto_stay_quiet(site, tmp_path, folders):
    lib, sync = _service(tmp_path)
    site.folders = folders                                # launch_panopto gives None: no Panopto on the page
    assert sync.sync().errors == []
