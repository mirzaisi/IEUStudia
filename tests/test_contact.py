"""How to reach the maker (fx-contact): Instagram and LinkedIn in Settings → About (a small card with a note), on the
setup's welcome step (one line) and at the bottom of the sidebar (two small buttons), drawn inline in the app's own
style and opened through the app's link opening; and a Contact section in the README. (app.js, contact.js, setup.js
run in node against stand-ins for the page.)"""

import html
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n

WEB = Path(i18n.__file__).parent / "web"
README = Path(i18n.__file__).parents[2] / "README.md"
IG, IN = "https://www.instagram.com/mirzaisi", "https://www.linkedin.com/in/mirzaisi/"

HARNESS = """
const web = %(web)s;
const stub = () => new Proxy(function () {}, { get: (o, k) => k === Symbol.toPrimitive ? () => "" : k === "length" ? 0 : stub(),
  apply: () => stub(), construct: () => stub() });
globalThis.window = globalThis;
const toasts = [];
const el = () => ({ innerHTML: "", style: { setProperty() {} }, dataset: {}, classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
  scrollTo() {}, querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, remove() {}, focus() {},
  appendChild() {}, getBoundingClientRect: () => ({ top: 0, bottom: 0 }) });
const els = { "#root": el(), "#top": el(), "#view": el(), "#nav": el(), "#sfoot": el(), "#queue": el(), "#overlay": el(),
  "#toasts": { appendChild: (b) => toasts.push(b) } };
const listeners = {};
globalThis.document = new Proxy({}, { get: (o, k) => k === "compatMode" ? "CSS1Compat"
  : k === "createElement" ? () => el()
  : k === "body" ? el()
  : k === "documentElement" ? { dataset: {}, style: { setProperty() {} } }
  : k === "querySelector" ? (s) => els[s] || null
  : k === "querySelectorAll" ? () => []
  : k === "addEventListener" ? (n, f) => { (listeners[n] = listeners[n] || []).push(f); } : stub() });
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.navigator = { userAgent: "node", platform: "Win32" };
globalThis.location = { hash: "", reload() {} };
globalThis.CSS = { escape: (s) => s };
globalThis.addEventListener = () => {}; globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.requestAnimationFrame = (f) => f();
const later = setTimeout;
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;      // no boot, no polling
for (const f of %(files)s) require(web + "/" + f);
const St = globalThis.Studia, S = St.S, A = St.A;
St.sq = St.sq || { about: () => "", card: () => "", line: () => "" };      // (the page's other cards: not drawn here)
const tick = (ms = 30) => new Promise((r) => later(r, ms));
const out = {};
(async () => {
  S.boot = { app: { onboarded: true, check_updates: true }, version: "3.2.5" }; S.courses = [];
%(body)s
  console.log(JSON.stringify(out));
  process.stdout.write("", () => process.exit(0));      // after the output is out (Linux pipes)
})().catch((e) => { console.error(e); process.exit(1); });
"""
FILES = ["i18n.js", "icons.js", "app.js", "contact.js", "setup.js"]


def run(tmp_path, body, files=FILES):
    script = tmp_path / "page.js"
    script.write_text(HARNESS % {"web": json.dumps(WEB.as_posix()), "files": json.dumps(files), "body": body},
                      encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

DRAW = """
  out.icons = [LSIcons.has("instagram"), LSIcons.has("linkedin")];
  S.mode = "app"; S.view = { name: "home" }; St.render();
  out.shell = els["#root"].innerHTML;
  S.mode = "setup"; St.setup.step = 0; out.welcome = St.setupHTML();
  S.mode = "app"; S.view = { name: "settings" }; out.about = St.settingsMore();
  const opened = [];
  St.Mock.open_url = (url) => { opened.push(url); return { ok: true }; };
  for (const m of out.shell.matchAll(/data-act="url" data-url="([^"]+)"/g)) await A.url({ dataset: { url: m[1].replace(/&amp;/g, "&") } });
  out.opened = opened;
  t.load({ lang: "tr", table: %(tr)s });
  S.mode = "setup"; out.welcomeTr = St.setupHTML();
  S.mode = "app"; out.aboutTr = St.settingsMore(); out.sideTr = St.contact.side();
"""


@node
def test_both_links_are_in_the_sidebar_the_welcome_step_and_settings_about(tmp_path):
    out = run(tmp_path, DRAW % {"tr": json.dumps(i18n.table("tr"), ensure_ascii=False)})
    assert out["icons"] == [True, True]
    # the bottom of the sidebar, on every page: two small buttons with tooltips, after the profile's row
    aside = out["shell"].split('<aside class="sidebar">')[1].split("</aside>")[0]
    side = aside.split('id="sfoot"></div>')[1]
    assert 'class="side-contact"' in side and "Questions? Message me" in side
    for url, tip in ((IG, "Message me on Instagram"), (IN, "Message me on LinkedIn")):
        button = re.search(rf'<button [^>]*data-url="{re.escape(url)}"[^>]*>(.*?)</button>', side, re.S)
        assert button and 'data-act="url"' in button.group(0), url          # opened by the app (open_url)
        assert f'title="{tip}"' in button.group(0) and f'aria-label="{tip}"' in button.group(0)
        assert button.group(1).strip().startswith("<svg") and "<img" not in button.group(1)   # drawn inline
    assert out["opened"] == [IG, IN]
    # the setup's welcome step: one short line with the two marks, in the bottom bar beside Continue (under the notice
    # it was below the fold in a small window, 1266x763)
    welcome = out["welcome"].split('class="su-foot"')[1]
    assert 'class="su-contact"' not in out["welcome"].split('class="su-foot"')[0]
    line = welcome.split('class="su-contact"')[1].split("</div>")[0]
    assert "Questions, or something not working? Message me." in line and IG in line and IN in line
    assert line.count("<svg") == 2
    # Settings → About: a small card with the note and both links, named
    card = out["about"].split('id="set-about"')[1].split('class="contact-card"')[1]
    assert "<b>Contact</b>" in card and "Questions, ideas, or something not working? Message me" in card
    assert f'data-url="{IG}"' in card and f'data-url="{IN}"' in card and "Instagram</button>" in card and "LinkedIn</button>" in card
    assert 'title="Opens instagram.com/mirzaisi in your browser"' in card
    # in Turkish ("sen"), the names as they are
    assert "Soru ya da çalışmayan bir şey mi var? Bana yaz." in out["welcomeTr"]
    assert "Bana yaz, duymak isterim." in out["aboutTr"] and "<b>İletişim</b>" in out["aboutTr"]
    side_tr = html.unescape(out["sideTr"])                               # (its tooltips are escaped: &#39;)
    assert 'title="Instagram\'dan bana yaz"' in side_tr and 'title="LinkedIn\'den bana yaz"' in side_tr


def test_the_marks_take_their_brand_colour_on_hover_in_both_themes():
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    light = css.split(':root[data-theme="dark"]')[0]
    dark = css.split(':root[data-theme="dark"] {')[1].split("}")[0]
    for token in ("--instagram", "--linkedin"):
        assert token in light and token in dark
    assert ".btn.ct-btn.ct-ig:hover" in css and "color: var(--instagram)" in css
    assert ".btn.ct-btn.ct-in:hover" in css and "color: var(--linkedin)" in css
    # after .btn.ghost:hover, which would otherwise win (the same weight, later in the file)
    assert css.index(".btn.ct-btn {") > css.index(".btn.ghost:hover")


def test_the_readme_has_a_contact_section_with_both_links():
    text = README.read_text(encoding="utf-8")
    section = text.split("## Contact", 1)[1].split("\n## ", 1)[0]
    assert IG in section and IN in section and "Message me" in section
    for mark in ("docs/contact/instagram.svg", "docs/contact/linkedin.svg"):
        assert mark in section and (README.parent / mark).is_file()
