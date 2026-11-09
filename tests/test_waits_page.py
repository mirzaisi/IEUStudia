"""What the page says while an AI run is waited for (waits.js in node with a stand-in for app.js): the question going
up with its MB, the AI reading it, thinking (its lines), writing (tokens), a retry with its countdown and try, the time
so far and left (for a plan's step), "taking longer than usual", what to do meanwhile once it has gone on a while, the
pages read first; the top bar's chip while the student is elsewhere, and where it leads; in Turkish."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n

WEB = Path(i18n.__file__).parent / "web"

SCRIPT = """
globalThis.window = globalThis;
globalThis.document = { documentElement: {}, hidden: true, querySelectorAll: () => [], querySelector: () => null };
require(%(i18n)s);
const turkish = %(table)s;
const went = [];
let tops = 0;
const S = { view: { name: "home" }, chat: { data: null }, day: null };
window.__lsEvent = () => {};
window.Studia = { S, A: {}, I: (n) => `[${n}]`, SPIN: () => "(spin)", aiName: () => "Claude",
  esc: (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"),
  go: (v) => went.push(v), openDay: (k) => went.push({ day: k }), renderTop: () => { tops++; } };
require(%(page)s);
const St = window.Studia, now = Date.now() / 1000, MB = 1048576;
const out = {};
const w = (x) => ({ ai: "ChatGPT", where: "chat", since: now - 42, at: now, busy: 1, ...x });
out.up = St.waitHTML(w({ phase: "send", size: 38 * MB, pictures: 120, up: [12 * MB, 38 * MB], eta: 75 }));
out.upNotes = St.waitHTML(w({ where: "notes", phase: "send", size: 3 * MB, pictures: 1, up: [MB / 2, 3 * MB] }));
out.claudeUp = St.waitHTML(w({ ai: "Claude", phase: "send", size: 28 * MB, pictures: 120, read: 120 }));
out.small = St.waitHTML(w({ phase: "send", size: 4000, since: now - 3 }));
out.reading = St.waitHTML(w({ phase: "wait", eta: 30 }));
out.think = St.waitHTML(w({ phase: "think", lines: ["Comparing the two circuits", "Checking the units"], eta: 100 }));
out.writeLine = St.waitLine(w({ phase: "write", tokens: 1240, eta: 20, lines: ["x"] }));
out.retry = St.waitHTML(w({ phase: "retry", retry: { why: "dropped", wait: 10, until: now + 10, n: 2, of: 3 } }));
out.busy = St.waitHTML(w({ ai: "Claude", phase: "retry", retry: { why: "overloaded", wait: 2, until: now - 1, n: 3, of: 11 } }));
out.slow = St.waitHTML(w({ phase: "think", slow: true }));
out.most = St.waitLine(w({ where: "notes", phase: "write", tokens: 300, most: 150 }));
out.plan = St.waitLine(w({ where: "pack", phase: "write", tokens: 900, eta: 240, units: [1, 5] }));
out.between = St.waitLine(w({ where: "pack", phase: "between" }));
out.prepare = St.waitHTML(w({ phase: "prepare" }));
out.asking = St.waitHTML(w({ phase: "prepare", read: 120 }));
out.none = St.waitHTML(null) + St.waitLine(null) + St.waitHTML(w({ where: "pack", phase: "between" }));
out.short = ["send", "think", "retry", "between"].map((phase) => St.waitShort(w({ phase })));
out.bare = St.waitLine(w({ where: "notes", phase: "write", tokens: 50 }), { bare: true });
// the chip: what runs while the student is elsewhere, and where it leads
window.__lsEvent({ type: "ai", phase: "progress", day_key: "c|2026-10-14|lecture", wait: w({ where: "notes", ai: "Claude" }) });
out.chipNotes = St.waitChip();
S.view = { name: "day" }; S.day = { day_key: "c|2026-10-14|lecture" };
out.chipHere = St.waitChip();
S.view = { name: "home" };
window.__lsEvent({ type: "plan", id: "p1", status: "running", kind: "pack", wait: w({ where: "pack" }) });
out.chipTwo = St.waitChip();
window.__lsEvent({ type: "ai", phase: "done", day_key: "c|2026-10-14|lecture" });
out.chipPack = St.waitChip();
St.A["aw-open"]({ dataset: { key: "plan:p1" } });
window.__lsEvent({ type: "plan", id: "p1", status: "done" });
out.chipGone = St.waitChip();
out.tops = tops;
out.went = went;
t.load({ lang: "tr", table: turkish });
out.upTr = St.waitHTML(w({ phase: "send", size: 38 * MB, pictures: 120, up: [12 * MB, 38 * MB], eta: 75 }));
out.thinkTr = St.waitHTML(w({ phase: "think", lines: ["Comparing the two circuits"] }));
out.retryTr = St.waitHTML(w({ phase: "retry", retry: { why: "dropped", wait: 10, until: now + 10, n: 2, of: 3 } }));
process.stdout.write(JSON.stringify(out), () => process.exit(0));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_what_the_page_says_while_an_ai_works(tmp_path):
    script = tmp_path / "waits.js"
    script.write_text(SCRIPT % {"i18n": json.dumps(str(WEB / "i18n.js")), "page": json.dumps(str(WEB / "waits.js")),
                                "table": json.dumps(i18n.table("tr"), ensure_ascii=False)}, encoding="utf-8")
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    # sending: what, to which AI, how far; for Claude (it can't tell how far) its size; a small one plainly
    assert "Sending your question with 120 pictures to ChatGPT: 12 of 38 MB" in out["up"]
    assert "0:42 so far" in out["up"] and "about 1 min left" in out["up"]
    assert "Sending the material with 1 picture to ChatGPT: 0.5 of 3.0 MB" in out["upNotes"]
    assert "Sending your question with 120 pictures to Claude (28 MB)…" in out["claudeUp"]
    assert "Went through all 120 pages first" in out["claudeUp"] and "left" not in out["claudeUp"]   # nothing measured
    assert "Sending your question to ChatGPT…" in out["small"]
    # what to do meanwhile: once it has gone on a while
    assert 'class="aw-hint"' in out["up"] and " hidden" not in out["up"].split('class="aw-hint"')[1][:80]
    assert " hidden>" in out["small"] and "keeps coming in here" in out["small"]
    # reading, thinking (its lines, the newest marked), writing (tokens), its time left
    assert "ChatGPT got it and is reading it…" in out["reading"] and "less than a minute left" in out["reading"]
    assert "ChatGPT is thinking…" in out["think"] and "Thinking: Comparing the two circuits" in out["think"]
    assert '<li class="now">Thinking: Checking the units</li>' in out["think"] and "about 2 min left" in out["think"]
    assert "ChatGPT is writing…" in out["writeLine"] and "1,240 tokens written" in out["writeLine"]
    assert "Thinking:" not in out["writeLine"] and "less than a minute left" in out["writeLine"]
    # a retry: why, the countdown, which try; once the wait is over: now
    assert "The connection dropped. <span" in out["retry"] and ">Trying again in 10 s (2 of 3).</span>" in out["retry"]
    assert 'class="aw-retry" data-until=' in out["retry"] and "[retry]" in out["retry"] and "left" not in out["retry"]
    assert "Claude is very busy right now. <span" in out["busy"] and ">Trying again now (3 of 11)…</span>" in out["busy"]
    assert "taking longer than usual" in out["slow"]
    assert "at most 3 min left" in out["most"]
    # a plan: the time left of its step; between its requests only its time (the plan says its step)
    assert "about 4 min left for this step" in out["plan"] and "900 tokens written" in out["plan"]
    assert out["between"].startswith('<span class="aw-line" data-phase="between"><span class="aw-t">')
    assert "Getting your lectures and slides ready…" in out["prepare"] and out["none"] == ""
    assert "Putting your question together…" in out["asking"] and "Went through all 120 pages first" in out["asking"]
    # the chip: the notes being written, not on their own page; two at once; where it leads; gone when over
    assert "Claude is writing your notes" in out["chipNotes"] and 'data-act="aw-open" data-key="ai"' in out["chipNotes"]
    assert out["chipHere"] == ""
    assert "2 AI tasks running" in out["chipTwo"] and "Writing your study pack" in out["chipPack"]
    assert out["went"] == [{"name": "plan", "planId": "p1"}] and out["chipGone"] == "" and out["tops"] == 4
    # in Turkish
    assert "Sorun ve 120 resim ChatGPT ile paylaşılıyor: 12 / 38 MB" in out["upTr"] and "0:42 geçti" in out["upTr"]
    assert "ChatGPT düşünüyor…" in out["thinkTr"] and "Düşünüyor: Comparing the two circuits" in out["thinkTr"]
    assert "Bağlantı koptu." in out["retryTr"] and "10 sn sonra yeniden deneniyor (2/3)." in out["retryTr"]
    assert out["short"] == ["Sending…", "Thinking…", "Trying again…", ""] and out["bare"].startswith('<span class="aw-line" data-phase="write"><span class="aw-t">')


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_saving_an_export_says_its_time_so_far_and_a_zip_its_time_left(tmp_path):
    """The Export menu's saving toast (test_exportmenu_page's run): the time so far, ticking (waits.js's clock); a
    course's zip, once a lecture is made, the time left at the pace of the lectures made so far."""
    from test_exportmenu_page import SCRIPT as EXPORT

    script = tmp_path / "exportmenu.js"
    script.write_text(EXPORT % {"i18n": json.dumps(str(WEB / "i18n.js")), "page": json.dumps(str(WEB / "exportmenu.js")),
                                "table": json.dumps(i18n.table("tr"), ensure_ascii=False)}, encoding="utf-8")
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout.strip().splitlines()[-1])
    assert 'class="aw-since" data-since="' in out["busy"][0][1] and "0:00 so far" in out["busy"][0][1]
    assert 'class="aw-since"' in out["zipBusy"] and 'class="aw-left"' not in out["zipBusy"]       # nothing made yet
    assert 'class="aw-left" data-eta="' in out["zipStep"]                    # a lecture made: its pace says the rest


def test_the_waits_are_shown_where_the_ai_works():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "Studia.waitChip ? Studia.waitChip()" in app and "Studia.waitHTML(m.wait)" in app
    assert "Studia.waitHTML(S.ai.wait)" in app and "Studia.waitLine(m.wait)" in app
    assert "St.planWait ? St.planWait(p)" in (WEB / "studypack.js").read_text(encoding="utf-8")
    assert "St.waitHTML(g.wait)" in (WEB / "digest.js").read_text(encoding="utf-8")
    page = (WEB / "index.html").read_text(encoding="utf-8")
    assert page.index('src="notices.js"') < page.index('src="waits.js"') < page.index('src="chatfiles.js"')
