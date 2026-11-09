"""The language IEUStudia speaks: English (the source text, written in the code) or Turkish.

Text the student sees goes through ``t()``: ``t("Can't reach {host}.", host=host)``. The English text is the key, and
``locales/tr/*.json`` maps it to the Turkish; a missing translation shows the English. Placeholders are named, so a
translation may move them. Helpers:

- ``tn(n, "{n} lecture", "{n} lectures")``: the form for a count. Turkish has one (``"{n} ders"``), keyed by the
  singular English.
- ``tc("state", "Open")``: one English word that needs two translations (key ``"state|Open"``).
- ``N_("Study notes")``: marks text kept in a constant (evaluated before the language is known); translate it where it
  is shown, ``t(label)``.
- ``Retold(lambda: t(...))``: a message kept to be shown again later (a failed sign-in's reason); ``retell(msg)``
  writes it in the language of that moment, so a language switch doesn't leave it in the old one.
- ``date_long``, ``date_short``, ``day_month``, ``duration``, ``num``, ``pct``, ``join``: dates and numbers the way the
  language writes them (Turkish: ``14 Ekim 2026 Çarşamba``, ``1 sa 5 dk``, ``31.200``, ``%45``, ``A, B ve C``).

There is one language per PC (``appconfig.language``: "" follows Windows), set at start-up with ``set_language`` in
the app and in the background helper, so messages made in background threads come out in it too. ``STUDIA_LANG``
(en, tr or pseudo) overrides it: tests pin English, translators use ``pseudo`` to find text that skips ``t()``."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Iterable

LANGS = ("en", "tr")
NAMES = {"en": "English", "tr": "Türkçe"}          # each in its own language: never translated
LOCALES = Path(__file__).parent / "locales"
_PH = re.compile(r"\{(\w+)\}")

_lang = "en"
_tables: dict[str, dict[str, str]] = {}
_lock = threading.Lock()


# ------------------------------------------------------------------ which language
def windows_language() -> str:
    """The language Windows shows its own menus in (the display language, not the regional format): "tr" for
    Turkish, otherwise "en"."""
    if sys.platform != "win32":                  # Linux: the desktop's language (LANGUAGE, LC_ALL, LC_MESSAGES, LANG)
        for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
            value = os.environ.get(var, "")
            if value:
                return "tr" if value.split(":")[0].lower().startswith("tr") else "en"
        return "en"
    try:
        import ctypes

        return "tr" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x1F else "en"
    except Exception:  # noqa: BLE001 - not Windows, or no answer: English
        return "en"


def resolve(setting: str = "") -> str:
    """The language to use for ``appconfig.language`` ("" = as Windows; ``STUDIA_LANG`` wins)."""
    env = os.environ.get("STUDIA_LANG", "")
    if env in (*LANGS, "pseudo"):
        return env
    return setting if setting in LANGS else windows_language()


def set_language(code: str) -> str:
    global _lang
    _lang = code if code in (*LANGS, "pseudo") else "en"
    return _lang


def load_language() -> str:
    """Set the language from the app's settings (start-up of the app and of the helper, and after a change)."""
    from . import appconfig

    return set_language(resolve(appconfig.load().language))


def language() -> str:
    return _lang


def table(lang: str | None = None) -> dict[str, str]:
    """English → translation, all of ``locales/<lang>/*.json`` merged (cached)."""
    lang = lang or _lang
    if lang not in _tables:
        with _lock:
            merged: dict[str, str] = {}
            folder = LOCALES / lang
            if lang != "en" and folder.is_dir():
                for f in sorted(folder.glob("*.json")):
                    merged.update({k: v for k, v in json.loads(f.read_text(encoding="utf-8")).items()
                                   if isinstance(v, str) and v and not k.startswith("_")})
            _tables[lang] = merged
    return _tables[lang]


# ------------------------------------------------------------------ text
def _fill(s: str, params: dict[str, Any]) -> str:
    return _PH.sub(lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), s) if params else s


def _lookup(key: str, english: str) -> str:
    if _lang == "en":
        return english
    if _lang == "pseudo":
        return pseudo(english)
    return table().get(key) or english


def t(text: str, /, **params: Any) -> str:
    return _fill(_lookup(text, text), params)


def tn(n: int, one: str, other: str, /, **params: Any) -> str:
    """``tn(3, "{n} lecture", "{n} lectures")``: Turkish keeps the noun singular after a number, so its table has one
    entry, under the singular English."""
    params.setdefault("n", n)
    if _lang in ("en", "pseudo"):
        return _fill(_lookup(one if n == 1 else other, one if n == 1 else other), params)
    return _fill(table().get(one) or (one if n == 1 else other), params)


def tc(context: str, text: str, /, **params: Any) -> str:
    return _fill(_lookup(f"{context}|{text}", text), params)


def N_(text: str) -> str:  # noqa: N802 - the usual name for "mark for translation"
    return text


class Retold(str):
    """A message kept to be shown again later (why a sign-in or an install failed, in Settings → AI): the text it was
    made with, and how to make it (``make``, a function of t() calls), so that after a language switch it is shown in
    the new language (:func:`retell`) instead of the old one until the next try. Anything else uses it as the str it
    was made with: ``Retold(lambda: _failure(which, code))``."""

    make: Callable[[], str]

    def __new__(cls, make: Callable[[], str]) -> "Retold":
        self = super().__new__(cls, make())
        self.make = make
        return self


def retell(text: Any) -> Any:
    """``text`` in the language of the moment: a :class:`Retold` message made again; anything else as it is."""
    if not isinstance(text, Retold):
        return text
    try:
        return str(text.make())
    except Exception:  # noqa: BLE001 - what it needs is gone: the words it was made with
        return str(text)


_PSEUDO = str.maketrans("aceinorsuyACEINORSUY", "àçéîñøřšüýÀÇÉÎÑØŘŠÜÝ")


def pseudo(s: str) -> str:
    """Accented and ~35% longer, with {placeholders} and <tags> intact: text that skipped t() stands out, and a
    layout that can't take longer Turkish words shows it."""
    parts = re.split(r"(\{\w+\}|<[^>]+>|&\w+;)", s)
    body = "".join(p if i % 2 else p.translate(_PSEUDO) for i, p in enumerate(parts))
    return f"⟦{body}{'·' * max(1, len(s) * 35 // 100)}⟧"


# ------------------------------------------------------------------ dates and numbers (CLDR "en-GB" and "tr")
_MONTHS = {"en": ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                  "November", "December"),
           "tr": ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım",
                  "Aralık")}
_MONTHS_SHORT = {"en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
                 "tr": ("Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara")}
_DAYS = {"en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
         "tr": ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")}
_DAYS_SHORT = {"en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
               "tr": ("Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz")}


def _tl() -> str:
    return "tr" if _lang == "tr" else "en"


def date_long(d: dt.date) -> str:
    """``Wednesday, 14 October 2026`` / ``14 Ekim 2026 Çarşamba``."""
    L = _tl()
    if L == "tr":
        return f"{d.day} {_MONTHS[L][d.month - 1]} {d.year} {_DAYS[L][d.weekday()]}"
    return f"{_DAYS[L][d.weekday()]}, {d.day} {_MONTHS[L][d.month - 1]} {d.year}"


def date_short(d: dt.date) -> str:
    """``Wed 14 Oct`` / ``14 Eki Çar``."""
    L = _tl()
    if L == "tr":
        return f"{d.day} {_MONTHS_SHORT[L][d.month - 1]} {_DAYS_SHORT[L][d.weekday()]}"
    return f"{_DAYS_SHORT[L][d.weekday()]} {d.day} {_MONTHS_SHORT[L][d.month - 1]}"


def day_month(d: dt.date, long: bool = False) -> str:
    """``14 Oct`` / ``14 Eki`` (``long``: ``14 October`` / ``14 Ekim``)."""
    return f"{d.day} {(_MONTHS if long else _MONTHS_SHORT)[_tl()][d.month - 1]}"


def weekday(d: dt.date, short: bool = False) -> str:
    return (_DAYS_SHORT if short else _DAYS)[_tl()][d.weekday()]


def num(x: float, digits: int = 0, group: bool = True) -> str:
    """``31,200.5`` / ``31.200,5``; ``group=False`` for sizes, written ``1835`` as before."""
    s = f"{x:,.{digits}f}" if group else f"{x:.{digits}f}"
    return s.translate(str.maketrans(",.", ".,")) if _tl() == "tr" else s


def pct(fraction: float) -> str:
    """``45%`` / ``%45``."""
    n = round((fraction or 0) * 100)
    return f"%{n}" if _tl() == "tr" else f"{n}%"


def duration(seconds: float) -> str:
    """``1 h 5 min`` / ``1 sa 5 dk``; ``<1 min`` / ``<1 dk``."""
    m = round((seconds or 0) / 60)
    h_, min_ = ("sa", "dk") if _tl() == "tr" else ("h", "min")
    if seconds and m < 1:
        return f"<1 {min_}"
    return f"{m // 60} {h_} {m % 60} {min_}" if m >= 60 else f"{m} {min_}"


def join(items: Iterable[str], conj: str = "and") -> str:
    """``A, B and C`` / ``A, B ve C`` (``conj="or"``: ``or`` / ``veya``)."""
    items = [str(x) for x in items]
    word = {"en": {"and": "and", "or": "or"}, "tr": {"and": "ve", "or": "veya"}}[_tl()][conj]
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} {word} {items[-1]}" if items else ""


def page_data() -> dict[str, Any]:
    """What the page needs (api.i18n): the language, the setting and the table (none for English)."""
    from . import appconfig

    setting = appconfig.load().language
    return {"lang": _lang, "setting": setting, "windows": windows_language(), "names": NAMES,
            "table": table() if _lang == "tr" else {}}
