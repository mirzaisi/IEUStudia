"""The services' state on the page (services.js, run in node with a stand-in for app.js): Home warns about a service
that changed on its side once (Hide hides that change, a new one shows again), a course page says it on the tab whose
data comes from the part that changed, Settings → Services lists every service, a sync you asked for says a change
once, and the files and announcements' step makes the top bar say "Synced with problems"."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import health, i18n

WEB = Path(i18n.__file__).parent / "web"

SCRIPT = """
globalThis.window = globalThis;
const stored = {};
globalThis.localStorage = { getItem: (k) => (k in stored ? stored[k] : null), setItem: (k, v) => { stored[k] = String(v); } };
globalThis.document = { documentElement: {}, querySelector: () => null, querySelectorAll: () => [], getElementById: () => null,
  addEventListener() {} };
require(%(i18n)s);
const toasts = [], draws = [], calls = [];
const S = { view: { name: "home" }, sync: {}, boot: { services: %(snap)s }, update: {} };
window.Studia = { S, A: {}, I: () => "", esc: (s) => String(s), toast: (m, l) => toasts.push([l, m]), rel: () => "a minute ago",
  call: (name) => { calls.push(name); return Promise.resolve({ ok: true }); }, renderView: () => draws.push(S.view.name),
  renderTop() {}, go: (v) => { S.view = v; }, mocks: {}, onEvents: {}, settingsMore: () => "<more>" };
require(%(services)s);
const St = window.Studia, out = {};
out.banner = St.svcBanner();
out.notes = { materials: St.svcNote("materials"), weeks: St.svcNote("weeks"), overview: St.svcNote("overview"),
  lectures: St.svcNote("lectures") };
out.card = St.settingsMore();
const key = %(snap)s.services.find((s) => s.state === "changed").key;
St.A["svc-hide"]({ dataset: { key } });
out.hidden = St.svcBanner();
St.onEvents.services(%(again)s);                       // the same part changed again later: a new change
out.again = St.svcBanner();
St.svcToast(["ieulearn"]); St.svcToast(["ieulearn"]); St.svcToast(["panopto"]);
out.toasts = toasts.splice(0);
S.sync = { running: false, error: "", changed: "" };
St.onEvents.content({ phase: "done", changed: ["ieulearn"], quiet: true, errors: [] });
out.sync = S.sync;
St.A["svc-copy"]();
setTimeout(() => { out.calls = calls; out.draws = draws; out.copied = toasts; process.stdout.write(JSON.stringify(out), () => process.exit(0)); }, 30);
"""


def test_the_page_loads_the_services_script_after_the_pages_it_plugs_into():
    page = (WEB / "index.html").read_text(encoding="utf-8")
    assert '<script src="services.js"></script>' in page and 'href="services.css"' in page
    # it wraps course.js's files-and-announcements event and the Settings cards of setup.js and youtube.js
    assert page.index("services.js") > max(page.index(f'src="{f}"') for f in ("course.js", "setup.js", "youtube.js"))


def _snapshot():
    health.ok("panopto", "sessions")
    health.ok("openai", "answers")
    health.changed("ieulearn", "unit_plan", "3 files on the page, 0 read")
    return health.snapshot()


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_changed_service_is_said_once_on_home_where_it_matters_on_a_course_page_and_in_settings(tmp_path):
    snap = _snapshot()
    health.ok("ieulearn", "unit_plan")
    health.changed("ieulearn", "unit_plan", "again")              # a new change of the same part
    again = health.snapshot()
    script = tmp_path / "services.js"
    script.write_text(SCRIPT % {"i18n": json.dumps(str(WEB / "i18n.js")), "services": json.dumps(str(WEB / "services.js")),
                                "snap": json.dumps(snap), "again": json.dumps(again)}, encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8",
                                    check=True).stdout)
    assert "IEULearn changed something on its side." in out["banner"] and "svc-update" in out["banner"]
    assert "can't read new course files" in out["banner"] and "Everything on this PC is safe" in out["banner"]
    assert "What you see here is what IEUStudia read before" in out["notes"]["materials"] and out["notes"]["weeks"]
    assert out["notes"]["overview"] == "" and out["notes"]["lectures"] == ""
    card = out["card"]
    assert card.startswith("<more>") and 'id="set-services"' in card
    for name in ("IEULearn", "Panopto", "ChatGPT", "Claude Code", "Codex CLI", "YouTube", "Hugging Face", "GitHub"):
        assert f">{name}<" in card
    assert card.count("svc-dot ok") == 2 and card.count("svc-dot changed") == 1 and "Not used yet." in card
    assert out["hidden"] == "" and "IEULearn changed something" in out["again"]       # hidden once, shown for a new one
    assert [lvl for lvl, _ in out["toasts"]] == ["warning"] and "Home and Settings" in out["toasts"][0][1]
    assert out["sync"]["partial"] and "IEULearn changed something on its side." in out["sync"]["changed"]
    assert out["calls"] == ["services_copy"] and out["copied"][-1][0] == "success"


NOTE_SCRIPT = """
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.document = { documentElement: {}, querySelector: () => null, querySelectorAll: () => [], getElementById: () => null,
  addEventListener() {} };
require(%(i18n)s);
const S = { view: { name: "course" }, sync: {}, boot: { services: %(snap)s }, update: {} };
window.Studia = { S, A: {}, I: () => "", esc: (s) => String(s), toast() {}, rel: () => "a minute ago",
  call: () => Promise.resolve({ ok: true }), renderView() {}, renderTop() {}, go() {}, mocks: {}, onEvents: {} };
require(%(services)s);
const St = window.Studia;
const out = { seven: St.svcNote("materials", "7"), eight: St.svcNote("materials", "8"), banner: St.svcBanner() };
process.stdout.write(JSON.stringify(out), () => process.exit(0));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_course_page_says_a_change_only_for_the_course_whose_page_changed_and_github_offers_its_page(tmp_path):
    """Only EEE 242's plan changed, but every course's Weeks and Materials said "What you see here is what IEUStudia
    read before …". And when GitHub's release answers changed, Home's button ran the same failing check again."""
    health.changed("ieulearn", "unit_plan", "3 files on the page, 0 read", where="7")
    health.ok("ieulearn", "unit_plan", where="8")
    health.changed("github", "releases", "the release API answered HTTP 404")
    script = tmp_path / "note.js"
    script.write_text(NOTE_SCRIPT % {"i18n": json.dumps(str(WEB / "i18n.js")), "services": json.dumps(str(WEB / "services.js")),
                                     "snap": json.dumps(health.snapshot())}, encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8",
                                    check=True).stdout)
    assert "What you see here is what IEUStudia read before" in out["seven"] and out["eight"] == ""
    github = out["banner"].split('data-svc="github"', 1)[1]
    assert "releases/latest" in github and "Open the download page" in github and "svc-update" not in github
    assert "Download the newest IEUStudia from its page on GitHub" in github


COPY_SCRIPT = """
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.document = { documentElement: {}, querySelector: () => null, querySelectorAll: () => [], getElementById: () => null,
  addEventListener() {} };
require(%(i18n)s);
const toasts = [], shown = [];
const S = { view: { name: "settings" }, sync: {}, boot: { services: { services: [], changed: [] } }, update: {} };
window.Studia = { S, A: {}, I: () => "", esc: (s) => String(s), toast: (m, l) => toasts.push([l, m]), rel: () => "",
  call: () => Promise.resolve({ ok: false, text: %(details)s, error: "Could not access the clipboard." }),
  copied: (r, text, done) => shown.push({ ok: r.ok, text, done }),
  renderView() {}, renderTop() {}, go() {}, mocks: {}, onEvents: {} };
require(%(services)s);
window.Studia.A["svc-copy"]().then(() => process.stdout.write(JSON.stringify({ toasts, shown }), () => process.exit(0)));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_copy_details_refused_by_the_clipboard_shows_the_details_to_copy_by_hand(tmp_path):
    """In the real app with the clipboard refused, Settings → Services → Copy details only said "Could not access the
    clipboard." in a red toast: the details for the developer were nowhere (every other copy shows them to copy by
    hand)."""
    health.changed("ieulearn", "unit_plan", "3 files on the page, 0 read")
    details = health.details()
    script = tmp_path / "copy.js"
    script.write_text(COPY_SCRIPT % {"i18n": json.dumps(str(WEB / "i18n.js")), "services": json.dumps(str(WEB / "services.js")),
                                     "details": json.dumps(details)}, encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8",
                                    check=True).stdout)
    assert out["toasts"] == [] and len(out["shown"]) == 1
    assert out["shown"][0]["ok"] is False and out["shown"][0]["text"] == details and "IEULearn: changed" in details
    assert "send it to IEUStudia's developer" in out["shown"][0]["done"]
