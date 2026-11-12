"""After a language switch the page reloads before the next look at this PC is saved: the saved look's speech models
(the window's start-up data) are given their names in the language shown now, not in the one shown when it was saved."""

from studia import i18n
from studia.api import _presets_in_language


def test_the_saved_speech_models_get_their_names_in_the_language_now():
    saved = [{"id": "small", "label": "Small", "description": "The one for PCs without an NVIDIA graphics card",
              "size_mb": 484, "downloaded": True}, {"id": "gone-model", "label": "Old"}, "not a model"]
    was = i18n.language()
    try:
        i18n.set_language("tr")
        got = _presets_in_language(saved)
        assert got[0]["label"] == "Küçük" and got[0]["description"] != saved[0]["description"]
        assert got[0]["size_mb"] == 484 and got[0]["downloaded"] is True
        assert got[1:] == saved[1:]                     # (one this version doesn't know, as it was)
        i18n.set_language("en")
        assert _presets_in_language(got)[0]["label"] == "Small"
    finally:
        i18n.set_language(was)
