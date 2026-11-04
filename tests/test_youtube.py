"""YouTube details, ranking, channels and the student's choices (language, preferred and blocked channels) - against
yt-dlp answers saved from real searches (tests/data/youtube), never the network."""

import copy
import json
import sys
import types
from pathlib import Path

import pytest
import yt_dlp
from yt_dlp.utils import DownloadError

from studia import api as api_mod, plans, videos, ytprefs
from studia.plans import Scope, concept_queries, concepts_prompt, finalize
from studia.videos import Channel, VideoHit, VideoInfo

DATA = Path(__file__).with_name("data") / "youtube"


def fixture(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


class FakeYDL:
    """yt_dlp.YoutubeDL answering from saved JSON; records the URLs asked and the options."""

    infos: dict = {}
    files: dict = {}
    calls: list = []
    params: list = []

    def __init__(self, params=None):
        FakeYDL.params.append(params or {})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=True, process=True, **kw):
        FakeYDL.calls.append(url)
        answer = FakeYDL.infos.get(url)
        if answer is None:
            raise DownloadError(f"ERROR: [youtube] {url}: This video is unavailable")
        return copy.deepcopy(answer)

    def urlopen(self, url):
        body = FakeYDL.files[url]
        return types.SimpleNamespace(read=lambda: body.encode("utf-8"), close=lambda: None)


@pytest.fixture
def yt(monkeypatch):
    FakeYDL.infos, FakeYDL.files, FakeYDL.calls, FakeYDL.params = {}, {}, [], []
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(videos.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    return FakeYDL


CAPTIONS = json.dumps({"events": [
    {"tStartMs": 0, "dDurationMs": 4000, "segs": [{"utf8": "what is the probability of A given B"}]},
    {"tStartMs": 51000, "dDurationMs": 5000, "segs": [{"utf8": "the conditional probability formula divides by P of B"}]}]})


# ------------------------------------------------------------------ what is read about a candidate
def test_a_search_reads_title_channel_length_views_and_the_channel_id(yt):
    yt.infos["ytsearch8:bayes theorem"] = fixture("search_bayes.json")
    hits = videos.search("bayes theorem")
    assert len(hits) == 8
    h = hits[0]
    assert (h.id, h.title, h.channel, h.channel_id, h.duration_s, h.view_count) == (
        "XQoLVl31ZfQ", "Bayes' Theorem - The Simplest Case", "Dr. Trefor Bazett", "UC9rTsvTxJnx1DNrDA3Rqa6A", 331.0, 2079068)
    assert h.like_count is None                       # a results page never says
    assert yt.params[0]["extract_flat"]


def test_an_inspection_reads_likes_subscribers_tags_chapters_language_and_captions(yt):
    raw = fixture("video_conditional.json")
    yt.infos["https://www.youtube.com/watch?v=ibINrxJLvlM"] = raw
    yt.files[raw["subtitles"]["en"][0]["url"]] = CAPTIONS
    info = videos.inspect("ibINrxJLvlM")
    assert (info.view_count, info.like_count, info.channel_followers) == (1731857, 23295, 625000)
    assert info.channel_id == "UC9rTsvTxJnx1DNrDA3Rqa6A" and info.upload_date == "20171119"
    assert info.categories == ["Education"] and "Probability" in info.tags
    assert [c.title for c in info.chapters][:2] == ["Introduction to conditional probability", "The formula and sample space"]
    assert info.caption_kind == "manual" and info.caption_lang == "en" and info.language == "en"
    assert info.cues[1].start == 51 and "formula" in info.cues[1].text
    assert videos.inspect("ibINrxJLvlM").like_count == 23295 and len(yt.calls) == 1      # cached, likes included
    old = json.loads(videos._info_path("ibINrxJLvlM").read_text(encoding="utf-8"))
    del old["categories"]                             # inspected by an older version: read again for the likes
    videos._info_path("ibINrxJLvlM").write_text(json.dumps(old), encoding="utf-8")
    videos.inspect("ibINrxJLvlM")
    assert len(yt.calls) == 2


# ------------------------------------------------------------------ ranking: views, likes, clickbait
def hit(vid, title, views=None, channel="Some channel", minutes=10.0, channel_id="", likes=None):
    return VideoHit((vid * 11)[:11], title, channel, minutes * 60, "", views, "", "", "q", channel_id, likes)


def test_views_never_lift_a_video_that_is_not_about_the_topic():
    popular = hit("p", "My morning routine", views=50_000_000)
    lesson = hit("l", "Thevenin equivalent circuit", views=20_000)
    assert [h.id[0] for h in videos.rank([popular, lesson], ["thevenin equivalent"])] == ["l", "p"]
    # among videos on the topic, more views rank higher - up to 10 million, no further
    a, b = hit("a", "Thevenin equivalent circuit", views=2_000_000), hit("b", "Thevenin equivalent circuit", views=3_000)
    assert [h.id[0] for h in videos.rank([b, a], ["thevenin equivalent"])] == ["a", "b"]
    assert videos.popularity(10**9, 99) == videos.popularity(10**7, 99) == pytest.approx(videos.VIEWS_WEIGHT * 7, rel=1e-3)


def test_a_clickbait_title_costs_rank():
    assert videos.clickbait("Thevenin's Theorem - Circuit Analysis") == 0
    assert videos.clickbait("Bayes' Theorem EXPLAINED with Examples") == 0         # one word in capitals is fine
    assert videos.clickbait("KVL and KCL: Kirchhoff's laws") == 0                  # acronyms are short
    assert videos.clickbait("You WON'T BELIEVE this circuit TRICK!!") == 3
    assert videos.clickbait("Learn Thevenin in 5 minutes 🔥") == 2
    bait = hit("b", "SHOCKING Thevenin TRICK nobody tells you!!", views=3_000_000)
    plain = hit("p", "Thevenin equivalent circuit", views=300_000)
    assert [h.id[0] for h in videos.rank([bait, plain], ["thevenin"])] == ["p", "b"]


def test_likes_per_view_once_inspected():
    assert videos.reception(1_731_857, 23_295) == 0.5           # 1.3 %: a normal lesson
    assert videos.reception(100_000, 4_000) == 1.0              # 4 %: well liked
    assert videos.reception(2_000_000, 1_000) == -1.0           # 0.05 %: watched a lot, liked little
    assert videos.reception(500, 1) == 0.0 and videos.reception(10_000, None) == 0.0     # too few to tell, hidden
    liked = hit("a", "Thevenin equivalent circuit", views=100_000, likes=4_000)
    meh = hit("b", "Thevenin equivalent circuit", views=150_000, likes=100)
    assert [h.id[0] for h in videos.rank([meh, liked], ["thevenin"])] == ["a", "b"]


def test_preferred_channels_come_first_and_blocked_ones_never():
    p = ytprefs.Prefs("en", allow=[{"id": "UC" + "a" * 22, "name": "Fav"}], block=[{"id": "UC" + "b" * 22, "name": "Nope"}])
    fav = hit("f", "Thevenin equivalent circuit", views=1_000, channel="Fav", channel_id="UC" + "a" * 22)
    big = hit("g", "Thevenin equivalent circuit", views=5_000_000, channel="Big")
    nope = hit("n", "Thevenin equivalent circuit", views=9_000_000, channel="Nope", channel_id="UC" + "b" * 22)
    old = hit("o", "Thevenin equivalent circuit", views=9_000_000, channel="Nope")       # cached before ids were kept
    ranked = videos.rank([big, nope, fav, old], ["thevenin"], allow=p.is_allowed, block=p.is_blocked)
    assert [h.id[0] for h in ranked] == ["f", "g"]


def test_language_of_a_video():
    def info(lang="", caption=""):
        return VideoInfo("x" * 11, "t", language=lang, caption_lang=caption)

    assert videos.in_languages(info("en-US", "en"), ("en",))
    assert not videos.in_languages(info("hi", "hi"), ("en", "tr"))
    assert not videos.in_languages(info("tr", "en"), ("en",))              # spoken Turkish, English subtitles
    assert videos.in_languages(info("", "tr"), ("tr",)) and videos.in_languages(info(), ("tr",))


def test_play_all_link():
    assert videos.playlist_url(["XQoLVl31ZfQ", "OByl4RJxnKA", "XQoLVl31ZfQ", "bad"]) == (
        "https://www.youtube.com/watch_videos?video_ids=XQoLVl31ZfQ,OByl4RJxnKA")
    assert videos.playlist_url([]) == ""


# ------------------------------------------------------------------ channels
def test_channel_search_shows_names_pictures_and_subscribers(yt):
    url = "https://www.youtube.com/results?search_query=chemistry+tutor&sp=EgIQAg%3D%3D"
    yt.infos[url] = fixture("channel_search.json")
    found = videos.search_channels("chemistry tutor")
    assert [c.name for c in found] == ["The Organic Chemistry Tutor", "Organic Chemistry with Victor", "The Chemistry Tutor"]
    c = found[0]
    assert c.id == "UCEWpbFLzoYGPfuWUMFPSaoA" and c.handle == "@TheOrganicChemistryTutor" and c.verified
    assert c.followers == 11_100_000
    assert c.avatar.startswith("https://yt3.googleusercontent.com/ytc/") and c.avatar.endswith("=s88-c-k-c0x00ffffff-no-rj")
    assert videos.search_channels("Chemistry  Tutor") == found and yt.calls == [url]          # cached
    assert videos.channel("UCEWpbFLzoYGPfuWUMFPSaoA") == c and yt.calls == [url]           # remembered


def test_a_channel_by_id_handle_or_link(yt):
    page = fixture("channel_page.json")
    yt.infos["https://www.youtube.com/channel/UC9rTsvTxJnx1DNrDA3Rqa6A"] = page
    yt.infos["https://www.youtube.com/@DrTrefor"] = page
    ch = videos.channel("UC9rTsvTxJnx1DNrDA3Rqa6A")
    assert (ch.name, ch.handle, ch.followers) == ("Dr. Trefor Bazett", "@DrTrefor", 625000)
    assert ch.avatar.endswith("=s88-c-k-c0x00ffffff-no-rj") and "fcrop64" not in ch.avatar      # not the banner
    assert yt.params[-1]["playlist_items"] == "0"                     # no videos listed
    assert videos.search_channels("https://www.youtube.com/@DrTrefor") == [ch]
    assert videos.channel("@nobody-here") is None
    assert videos.channels(["UC9rTsvTxJnx1DNrDA3Rqa6A", "nonsense"]) == {"UC9rTsvTxJnx1DNrDA3Rqa6A": ch}


def test_searching_within_a_preferred_channel(yt):
    cid = "UCEWpbFLzoYGPfuWUMFPSaoA"
    yt.infos[f"https://www.youtube.com/channel/{cid}/search?query=thevenin+theorem"] = {"entries": [
        {"_type": "url", "ie_key": "Youtube", "id": "zTDgziJC-q8", "title": "Thevenin's Theorem - Circuit Analysis",
         "channel": "The Organic Chemistry Tutor", "duration": 563, "view_count": 3349706},
        {"_type": "url", "ie_key": "YoutubeTab", "id": "PL0o_zxa4K1BV9E-N8tSExU1djL6slnjbL", "title": "a playlist"}]}
    out = videos.channel_search_many([(cid, "thevenin theorem")])
    assert [(h.id, h.channel_id) for h in out[(cid, "thevenin theorem")]] == [("zTDgziJC-q8", cid)]


# ------------------------------------------------------------------ the student's choices
def test_prefs_language_and_channel_lists():
    p = ytprefs.load()
    assert (p.lang, p.allow, p.block) == ("en", [], [])
    assert ytprefs.set_lang("both").lang == "both" and ytprefs.set_lang("klingon").lang == "en"
    ch = {"id": "UC9rTsvTxJnx1DNrDA3Rqa6A", "name": "Dr. Trefor Bazett", "avatar": "https://yt3.ggpht.com/x=s88"}
    p = ytprefs.set_channel(ch, "allow")
    assert [c["name"] for c in p.allow] == ["Dr. Trefor Bazett"] and p.is_allowed("UC9rTsvTxJnx1DNrDA3Rqa6A")
    p = ytprefs.set_channel(ch, "block")                         # one list at a time
    assert not p.allow and p.is_blocked("UC9rTsvTxJnx1DNrDA3Rqa6A") and p.is_blocked("", "dr trefor bazett")
    assert not p.is_blocked("UC" + "x" * 22, "Dr. Trefor Bazett")       # an id decides when known
    assert ytprefs.load().block == p.block
    assert not ytprefs.set_channel(ch, None).block
    with pytest.raises(ValueError):
        ytprefs.set_channel({"id": "nope", "name": "x"}, "block")
    assert ytprefs.caption_langs("tr") == ("tr",) and ytprefs.caption_langs("both") == ("en", "tr")


def test_the_api_looks_up_a_blocked_channels_picture(monkeypatch):
    found = Channel("UC9rTsvTxJnx1DNrDA3Rqa6A", "Dr. Trefor Bazett", "@DrTrefor", "https://yt3.ggpht.com/a=s88-c-k-c0x00ffffff-no-rj")
    monkeypatch.setattr(videos, "channel", lambda ref, **kw: found if ref == found.id else None)
    a = api_mod.Api.__new__(api_mod.Api)
    r = a.yt_channel_set({"id": found.id, "name": "Dr. Trefor Bazett", "avatar": ""}, "block")
    assert r["ok"] and r["prefs"]["block"][0]["avatar"] == found.avatar and r["prefs"]["block"][0]["handle"] == "@DrTrefor"
    assert a.yt_channel_set({"id": "x", "name": "y"}, "allow") == {"ok": False, "error": "That isn't a YouTube channel."}
    assert a.yt_set_lang("tr")["prefs"]["lang"] == "tr" and a.yt_prefs()["prefs"]["lang"] == "tr"


# ------------------------------------------------------------------ a plan's language
def test_a_plans_language_drives_its_queries_and_caption_terms():
    assert Scope.from_json({}).video_lang == "en"
    assert Scope.from_json({"allow_tr": True}).video_lang == "both"           # plans made before the choice
    s = Scope.from_json({"video_lang": "tr"})
    assert s.allow_tr and s.video_lang == "tr"
    c = {"queries": ["thevenin theorem", "thevenin example", "thevenin teoremi"], "in_scope": True}
    assert concept_queries(c, Scope.from_json({"video_lang": "both"})) == c["queries"]
    assert concept_queries(c, Scope.from_json({"video_lang": "en"})) == c["queries"][:2]
    assert concept_queries({**c, "in_scope": False}, Scope.from_json({"video_lang": "both", "focus": "Quiz 1"})) == [
        "thevenin theorem", "thevenin teoremi"]                           # an extra: one search per language
    facts = {"pack": "MATERIAL", "weeks": [{"no": 4}]}
    assert "searches in Turkish" in concepts_prompt(Scope.from_json({"video_lang": "tr"}), facts)
    both = concepts_prompt(Scope.from_json({"video_lang": "both"}), facts)
    assert "3 YouTube searches" in both and "in English and the same in Turkish" in both
    assert "Turkish" not in concepts_prompt(Scope.from_json({"video_lang": "en"}), facts).split("=== COURSE")[0]


def test_finalize_keeps_views_likes_weeks_and_opens_unchecked_videos_at_their_chapter():
    concepts = [{"id": "c1", "name": "Thevenin equivalent", "summary": "", "cues": [], "terms": ["thevenin"], "weeks": [4],
                 "skills": [], "in_scope": True}]
    vids = {"AAAAAAAAAAA": {"title": "Circuits 1", "channel": "UConn HKN", "channel_id": "UC" + "c" * 22, "duration_s": 1800,
                            "view_count": 506722, "like_count": 6000, "has_captions": False, "chapters": [
                                {"start": 0, "end": 300, "title": "Intro"}, {"start": 300, "end": 900, "title": "Thevenin equivalents"}]},
            "BBBBBBBBBBB": {"title": "Blocked one", "channel": "Nope", "channel_id": "UC" + "b" * 22, "duration_s": 600}}
    raw = {"items": [{"n": 1, "video": "https://www.youtube.com/watch?v=AAAAAAAAAAA", "concepts": ["c1"], "role": "required"},
                     {"n": 2, "video": "BBBBBBBBBBB", "concepts": ["c1"], "role": "required"}]}
    r = finalize(raw, concepts, {}, vids, [], "", blocked=lambda cid, name: cid == "UC" + "b" * 22)
    assert [it["video"] for it in r["items"]] == ["AAAAAAAAAAA"]           # a link is understood; the blocked one left out
    it = r["items"][0]
    assert (it["view_count"], it["like_count"], it["weeks"], it["channel_id"]) == (506722, 6000, [4], "UC" + "c" * 22)
    assert it["chapter"] == {"start": 300, "title": "Thevenin equivalents"} and it["url"].endswith("&t=300s")
    assert "506.7k views · 6k likes" in plans.plan_markdown({**r, "title": "T"}, {"code": "EEE 215", "name": "x"}, Scope())


def test_a_plan_for_one_language_ranks_titles_in_the_other_lower():
    assert videos.looks_turkish("Thevenin ve Norton Eşdeğer devresi Örnek 1") and videos.looks_turkish("DEVRE ANALIZI DERS 41")
    assert not videos.looks_turkish("Superposition Theorem") and not videos.looks_turkish("Integral calculus")
    en = hit("e", "Superposition theorem", views=1_000_000)
    tr = hit("t", "Süperpozisyon teoremi konu anlatımı", views=20_000)
    terms = ["superposition", "süperpozisyon"]
    assert [h.id[0] for h in videos.rank([en, tr], terms, lang="tr")] == ["t", "e"]
    assert [h.id[0] for h in videos.rank([tr, en], terms, lang="en")] == ["e", "t"]
    assert [h.id[0] for h in videos.rank([tr, en], terms)] == ["e", "t"]          # both: by the usual signs


# the playlist header, drawn by youtube.js in node (app.js and plans.js with a do-nothing DOM)
HEAD_SCRIPT = """
const stub = () => new Proxy(function () {}, { get: (t, k) => k === Symbol.toPrimitive ? () => "" : k === "length" ? 0 : stub(),
  apply: () => stub(), construct: () => stub() });
globalThis.window = globalThis;
globalThis.document = new Proxy({}, { get: (t, k) => k === "compatMode" ? "CSS1Compat" : stub() });
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.navigator = { userAgent: "node", platform: "Win32" };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;
const [web, items, table] = [process.argv[2], JSON.parse(process.argv[3]), require(process.argv[4])];
for (const f of ["i18n.js", "icons.js", "app.js", "plans.js", "youtube.js"]) require(web + "/" + f);
const text = (h) => h.replace(/<[^>]+>/g, " ").replace(/\\s+/g, " ").trim();
const out = [text(Studia.yt.head({ id: "p" }, items, [])), text(Studia.yt.head({ id: "p" }, items.slice(0, 1), []))];
t.load({ lang: "tr", table });
out.push(text(Studia.yt.head({ id: "p" }, items, [])), Studia.yt.head({ id: "p" }, items, []));
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not __import__("shutil").which("node"), reason="node is not installed")
def test_the_playlist_header_says_how_long_the_videos_take_to_watch(tmp_path):
    items = [{"video": "a", "whole": True, "duration_s": 374, "segments": [{"start": 0, "end": 374}]},
             {"video": "b", "whole": False, "duration_s": 1154,                         # two parts: 420 + 374 s
              "segments": [{"start": 0, "end": 420}, {"start": 780, "end": 1154}]},
             {"video": "c", "whole": False, "duration_s": 3000, "segments": []},        # no part given: the whole video
             {"video": "d", "minutes": 4}]                                              # only its minutes known
    every, one, tr, _ = _head(tmp_path, items)
    # 374 + 794 + 3000 + 240 s = 4408 s, 73 min
    assert "4 videos in the order to study · about 1 h 13 min to watch" in every
    assert "1 video in the order to study · about 6 min to watch" in one
    assert "Çalışma sırasıyla 4 video · izlemesi yaklaşık 1 sa 13 dk" in tr


def _head(tmp_path, items):
    import subprocess

    from studia import i18n

    script = tmp_path / "head.js"
    script.write_text(HEAD_SCRIPT, encoding="utf-8")
    table = tmp_path / "tr.json"
    table.write_text(json.dumps(i18n.table("tr")), encoding="utf-8")
    web = Path(i18n.__file__).parent / "web"
    run = subprocess.run(["node", str(script), str(web), json.dumps(items), str(table)],
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.mark.skipif(not __import__("shutil").which("node"), reason="node is not installed")
def test_the_playlist_leaves_out_alternatives_and_counts_optional_videos_apart(tmp_path):
    part = lambda v, role, s: {"video": v, "role": role, "duration_s": 3000, "segments": [{"start": 0, "end": s}]}  # noqa: E731
    items = [part("a", "prerequisite", 360), part("b", "required", 780), part("c", "alternative", 600),
             part("d", "optional", 900)]                    # c is watched instead of b
    every, _, tr, html = _head(tmp_path, items)
    # the alternative counts with the optional ones, as the plan's own "+N optional" does (plans.py optional_minutes)
    assert "4 videos in the order to study · about 19 min to watch · +25 min optional" in every
    assert "izlemesi yaklaşık 19 dk · +25 dk isteğe bağlı" in tr
    assert "video_ids=a,b,d" in html                           # Play all: not both a video and its alternative
