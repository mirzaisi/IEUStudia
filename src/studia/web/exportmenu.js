/* IEUStudia — the Export menu on a lecture, a course and a chat (exports.py, api.py's export_*). Pick a format (PDF for
 * reading, Word for editing, Markdown, text, or subtitles with their times), then what to save: a lecture's transcript,
 * its notes or its AI pack; a week or the whole course (one document, or a zip with a file for each lecture); the chat.
 * Windows' save dialog opens in the course's folder with a good name; while the file is made a toast says so and how
 * long it may take; then "Saved" with Open and Show in folder. The copy button beside a row copies it as text. The
 * format used last in each place is remembered for the student (Settings: export_prefs). Plugs in through
 * window.Studia: St.exportBtn(place, id) draws the button. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, call, esc, I, SPIN, toast, A } = St;
  const D = typeof document !== "undefined" ? document : null;
  const $ = (s) => (D && D.querySelector ? D.querySelector(s) : null);
  const said = (p) => (St.said ? St.said(p) : p);
  const DOCS = ["pdf", "docx", "md", "txt"];
  const chosen = {};             // this session's choice in each place (the saved ones come with the settings)
  let M = null;                  // the open menu: {place, id, anchor, weeks, lectures, notes, failed}
  let picking = false;           // the save dialog is open (one at a time: a double click opens one)
  const boxes = {};              // the "Saving…" toasts, by export (a course's zip says how far it is)

  function prefs(place) {
    if (!chosen[place]) {
      const p = ((S.settings || {}).export_prefs || {})[place] || {};
      chosen[place] = { fmt: DOCS.includes(p.fmt) ? p.fmt : "pdf", sub: p.sub === "vtt" ? "vtt" : "srt", zip: !!p.zip,
        notes: p.notes !== false };
    }
    return chosen[place];
  }
  const fmtOf = (c) => (c.fmt === "sub" ? c.sub : c.fmt);
  const formats = (place) => [["pdf", "PDF"], ["docx", "Word"], ["md", "Markdown"], ["txt", t("Text")],
    ...(place === "chat" ? [] : [["sub", t("Subtitles")]])];
  const hint = (fmt) => ({ pdf: t("Best for reading and printing."), docx: t("Best for editing, in Word or LibreOffice."),
    md: t("Headings and formulas kept as text, for Obsidian, Notion or GitHub."), txt: t("Just the words, for any program."),
    sub: t("With their times, to watch the recording with captions in a video player.") }[fmt] || "");

  // ------------------------------------------------------------------ the button (app.js and course.js draw it)
  St.exportBtn = (place, id, small, off) => `<button class="btn ${small ? "ghost icon " : ""}sm xp-btn" data-act="menu-export" data-place="${place}" data-id="${esc(id || "")}" aria-haspopup="menu" ${off ? "disabled" : ""}
    title="${esc(place === "chat" ? t("Export this chat as PDF, Word, Markdown or text") : t("Export as PDF, Word, Markdown, text or subtitles"))}">${I("download", "sm")}${small ? "" : ` ${t("Export")} ${I("chevron-down", "sm caret")}`}</button>`;

  // ------------------------------------------------------------------ what each place offers
  function rows(c) {
    const docs = c.fmt !== "sub";
    if (M.place === "chat") return [{ what: "chat", icon: "chat", label: t("This chat"), desc: t("Your questions and the answers"), copy: true }];
    if (M.place === "day") {
      const d = S.day || { notes: [], parts: [] };
      const fmt = fmtOf(c);
      const out = [{ what: "transcript", icon: "file-text", label: t("Transcript"), desc: docs ? t("Everything that was said, with times") : t("Each part as its own file (a zip when there are several)"), copy: true }];
      if (!docs) return out;
      for (const [k, icon, label] of [["notes", "book", t("Study notes")], ["exam", "star", t("Exam focus")], ["flashcards", "list-checks", t("Flashcards")]]) {
        if ((d.notes || []).some((n) => n.kind === k)) out.push({ what: "notes:" + k, icon, label, desc: t("Written by AI for this lecture"), copy: true });
      }
      out.push({ what: "pack", icon: "presentation", label: t("AI pack"), copy: true,
        desc: fmt === "md" || fmt === "txt" ? t("A prompt and the transcript, ready for ChatGPT or Claude") : t("The slides next to what was said, for ChatGPT or Claude") });
      return out;
    }
    if (!M.weeks) return [];
    const n = (k) => t.n(k, "{n} lecture", "{n} lectures");
    return [{ what: "all", icon: "library", label: t("Whole course"), desc: n(M.lectures) },
      ...M.weeks.map((w) => ({ what: "week:" + w.no, icon: "calendar", label: t("Week {n}", { n: w.no }) + (w.current ? ` · ${t("this week")}` : ""),
        desc: [w.topic, n(w.lectures)].filter(Boolean).join(" · ") }))];
  }
  function rowHTML(r) {
    return `<div class="xp-row"><button class="xp-go" data-act="xp-save" data-what="${esc(r.what)}" role="menuitem">${I(r.icon)}<span class="xp-l">${esc(r.label)}${r.desc ? `<span class="d">${esc(r.desc)}</span>` : ""}</span></button>${r.copy
      ? `<button class="xp-copy" data-act="xp-copy" data-what="${esc(r.what)}" title="${esc(t("Copy as text"))}" aria-label="${esc(t("Copy as text"))}">${I("copy", "sm")}</button>` : ""}</div>`;
  }
  function menuHTML() {
    const c = prefs(M.place);
    const subs = c.fmt === "sub";
    const head = `<div class="xp-head"><b>${t("Export")}</b><span>${t("Pick a format, then what to save.")}</span></div>
      <div class="seg sm xp-fmts" role="group" aria-label="${esc(t("Format"))}">${formats(M.place).map(([k, label]) => `<button class="${c.fmt === k ? "on" : ""}" data-act="xp-fmt" data-fmt="${k}" aria-pressed="${c.fmt === k}">${esc(label)}</button>`).join("")}</div>
      <div class="xp-hint">${esc(hint(c.fmt))}</div>
      ${subs ? `<div class="seg sm xp-subs" role="group" aria-label="${esc(t("Subtitles"))}">${[["srt", t("SRT (most players)")], ["vtt", t("VTT (web players)")]].map(([k, label]) => `<button class="${c.sub === k ? "on" : ""}" data-act="xp-sub" data-sub="${k}" aria-pressed="${c.sub === k}">${esc(label)}</button>`).join("")}</div>` : ""}`;
    let body;
    if (M.place === "course" && M.failed) body = `<div class="xp-none err-txt">${esc(M.failed)}</div>`;
    else if (M.place === "course" && !M.weeks) body = `<div class="xp-none dim">${SPIN()} ${t("Looking at the course's lectures…")}</div>`;
    else if (M.place === "course" && !M.lectures) body = `<div class="xp-none dim">${t("No lecture of this course is transcribed yet.")}</div>`;
    else body = `<div class="xp-rows">${rows(c).map(rowHTML).join("")}</div>`;
    let opts = "";                      // a course's options: under the format, in view however small the window is
    if (M.place === "course" && M.lectures) {
      opts = subs ? `<div class="xp-note dim">${t("Subtitles come as a zip, with a file for each part of each lecture.")}</div>`
        : `${M.notes ? `<label class="xp-opt"><input type="checkbox" class="check" data-act="xp-opt" data-k="notes" ${c.notes ? "checked" : ""}> ${t("With the study notes, exam focus and flashcards")}</label>` : ""}
          <label class="xp-opt"><input type="checkbox" class="check" data-act="xp-opt" data-k="zip" ${c.zip ? "checked" : ""}> ${t("A file for each lecture, in a zip")}</label>`;
    }
    return head + (opts ? `<div class="xp-opts">${opts}</div>` : "") + "<hr>" + body;
  }

  // ------------------------------------------------------------------ the menu
  function close() {
    const m = $("#menu");
    if (m && m.classList && m.classList.contains("xp-menu")) m.remove();
    M = null;
  }
  const isOpen = () => { const m = $("#menu"); return !!(M && m && m.classList && m.classList.contains("xp-menu")); };
  function draw() {
    const m = $("#menu");
    if (!M || !m) return;
    const keep = m.scrollTop;
    m.innerHTML = menuHTML();
    m.scrollTop = keep;
    place();
  }
  function anchor() {        // the button: drawn again since (the page redraws its toolbar), the new one; else none
    if (M.anchor && M.anchor.isConnected !== false) return M.anchor;
    const found = $(`[data-act="menu-export"][data-place="${M.place}"]`);
    if (found) M.anchor = found;
    return found;
  }
  function place() {         // under the button as the app's menus are (above it when there is no room), in the window
    const m = $("#menu"), a = M && anchor();
    if (!m || !a || !a.getBoundingClientRect || typeof window.innerWidth !== "number") return;    // (else: where it is)
    const r = a.getBoundingClientRect(), w = m.offsetWidth, h = m.offsetHeight;
    m.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left)) + "px";
    const below = window.innerHeight - r.bottom - 14, above = r.top - 14;
    m.style.top = (h > below && above > below ? Math.max(8, r.top - h - 6) : r.bottom + 6) + "px";
    m.style.maxHeight = Math.max(160, h > below && above > below ? above : below) + "px";
  }
  async function open(el) {
    if (St.closeMenu) St.closeMenu();
    const m0 = $("#menu"); if (m0) m0.remove();
    const where = el.dataset.place, id = el.dataset.id || "";
    M = { place: where, id, anchor: el, weeks: null, lectures: 0, notes: false, failed: "" };
    if (!D || !D.createElement) return;
    const m = D.createElement("div");
    m.className = "menu xp-menu"; m.id = "menu"; m.tabIndex = -1;
    if (m.setAttribute) m.setAttribute("role", "menu");
    D.body.appendChild(m);
    draw();
    if (m.focus) m.focus({ preventScroll: true });       // Tab goes on into the menu (keyboard), no ring on a click
    if (where !== "course") return;
    const asked = M;
    const r = await call("export_weeks", id);
    if (asked !== M) return;
    if (!r || !r.ok) M.failed = (r && r.error) || t("Export failed");
    else Object.assign(M, { weeks: r.weeks || [], lectures: r.lectures || 0, notes: !!r.notes });
    draw();
  }

  // ------------------------------------------------------------------ saving: where, then made, then "Saved"
  const waitText = (r) => (r.wait <= 8 ? t("This takes a few seconds.") : r.wait < 60 ? t("This takes less than a minute.")
    : t.n(Math.round(r.wait / 60), "This can take about {n} minute.", "This can take about {n} minutes."));
  function addToast(cls, html, ms) {
    const host = $("#toasts");
    if (!host || !D.createElement) return null;
    const box = D.createElement("div");
    box.className = `toast ${cls}`;
    box.innerHTML = html;
    host.appendChild(box);
    if (ms) setTimeout(() => { box.style.transition = "opacity .3s"; box.style.opacity = "0"; setTimeout(() => box.remove(), 320); }, ms);
    return box;
  }
  // the time so far (waits.js's clock ticks it), and for a course's zip the time left at the pace of its lectures so far
  function busyHTML(r, line, left) {
    const at = Date.now() / 1000, since = r.since || (r.since = at);
    const clock = `<span class="aw-since" data-since="${since}">${esc(t("{time} so far", { time: "0:00" }))}</span>`
      + (left > 0 ? ` · <span class="aw-left" data-eta="${Math.max(1, Math.round(left))}" data-at="${at}">${esc(t.left(left))}</span>` : "");
    return `${SPIN()}<div><b>${esc(t("Saving {name}…", { name: r.name }))}</b><div class="xp-sub">${esc(line || waitText(r))}</div><div class="xp-sub aw-t">${clock}</div></div>`;
  }
  function savedHTML(w) {
    return `${I("check-circle")}<div><b>${esc(t("Saved {name}", { name: w.name }))}</b><div class="xp-sub" title="${esc(w.folder)}">${esc(w.folder)}</div>
      <div class="xp-acts"><button class="btn sm" data-act="xp-open" data-path="${esc(w.path)}">${I("file", "sm")} ${t("Open")}</button>
      <button class="btn sm" data-act="xp-show" data-path="${esc(w.path)}">${I("folder-open", "sm")} ${t("Show in folder")}</button></div></div>`;
  }
  async function save(what) {
    if (!M || picking) return;
    const where = M.place, scope = `${where}:${M.id}`, c = prefs(where);
    const opts = where === "course" ? { zip: c.zip, notes: c.notes } : {};
    close();
    picking = true;
    let r;
    try { r = await call("export_pick", scope, what, fmtOf(c), opts); } finally { picking = false; }
    if (!r || !r.ok) { toast((r && r.error) || t("Export failed"), "error"); return; }
    if (!r.token) return;                                   // the save dialog was cancelled: nothing to say
    const box = addToast("info xp-busy", busyHTML(r));
    boxes[r.token] = { box, r };
    let w;
    try { w = await call("export_write", r.token); } finally { delete boxes[r.token]; if (box) box.remove(); }
    if (!w || !w.ok) { toast((w && w.error) || t("Export failed"), "error"); return; }
    if (w.prefs && S.settings) S.settings.export_prefs = w.prefs;
    const shown = addToast("success xp-saved", savedHTML(w), 14000);
    if (!shown) toast(t("Saved {name}", { name: w.name }), "success", { label: t("Open"), act: "xp-open", data: { path: w.path } });
  }
  St.onEvents.xsave = (e) => {                              // a course's zip: how far it is
    const b = boxes[e.token];
    const done = Math.max(0, Math.min(e.n || 0, e.i || 0)), gone = b && b.r.since ? Date.now() / 1000 - b.r.since : 0;
    const left = done && gone ? (gone / done) * ((e.n || 0) - done) : 0;      // (measured: the lectures made so far)
    if (b && b.box && e.n) b.box.innerHTML = busyHTML(b.r, t("Lecture {i} of {n}", { i: Math.min(e.n, e.i + 1), n: e.n }) + " · " + waitText(b.r), left);
  };
  async function copy(what) {
    if (!M) return;
    const scope = `${M.place}:${M.id}`;
    close();
    const r = (await call("export_copy", scope, what)) || { ok: false };
    const done = t("Copied as text ({n} characters).", { n: t.num(r.chars || 0) });
    if (!r.ok && !r.text) { toast(r.error || t("Copy failed"), "error"); return; }
    if (St.copied) St.copied(r, r.text || "", done);
    else toast(r.ok ? done : r.error || t("Copy failed"), r.ok ? "success" : "error");
  }

  // ------------------------------------------------------------------ actions
  St.inputActs = (St.inputActs || []).concat(["xp-opt"]);       // its checkboxes: app.js passes their clicks on
  Object.assign(A, {
    "menu-export": (el) => { if (isOpen() && M.place === el.dataset.place && M.id === (el.dataset.id || "")) { close(); return; } open(el); },
    "xp-fmt": (el) => { if (!M) return; prefs(M.place).fmt = el.dataset.fmt; draw(); },
    "xp-sub": (el) => { if (!M) return; prefs(M.place).sub = el.dataset.sub; draw(); },
    "xp-opt": (el) => { if (!M) return; prefs(M.place)[el.dataset.k] = !!el.checked; draw(); },
    "xp-save": (el) => save(el.dataset.what),
    "xp-copy": (el) => copy(el.dataset.what),
    "xp-open": (el) => { const box = el.closest && el.closest(".toast"); if (box) box.remove(); return said(call("export_open", el.dataset.path, false)); },
    "xp-show": (el) => { const box = el.closest && el.closest(".toast"); if (box) box.remove(); return said(call("export_open", el.dataset.path, true)); },
  });
  St.exportMenu = { open, close, prefs, isOpen };
  if (typeof window.addEventListener === "function") window.addEventListener("resize", () => { if (isOpen()) place(); });

  // ------------------------------------------------------------------ browser preview (mock)
  const mockSaved = (name) => ({ ok: true, path: "C:\\Users\\you\\Documents\\IEUStudia\\MATH 240\\" + name, name, folder: "C:\\Users\\you\\Documents\\IEUStudia\\MATH 240", bytes: 48213 });
  St.mocks.export_weeks = () => ({ ok: true, lectures: 9, notes: true, weeks: [{ no: 7, topic: "The Uniform & the Normal", current: true, lectures: 1 },
    { no: 5, topic: "Mean, Variance & the Named Distributions", lectures: 2 }, { no: 4, topic: "Random Variables, PMF & CDF", lectures: 1 }, { no: 1, topic: "Sample Spaces, Events & Counting", lectures: 1 }] });
  St.mocks.export_pick = (scope, what, fmt) => ({ ok: true, token: "mock" + Date.now(), name: `MATH 240 - 2026-10-14 Wed - Lecture - Transcript.${fmt === "docx" ? "docx" : fmt}`, wait: scope.startsWith("course") ? 40 : 3, lectures: 1, zip: false });
  St.mocks.export_write = () => new Promise((res) => setTimeout(() => res({ ...mockSaved("MATH 240 - 2026-10-14 Wed - Lecture - Transcript.pdf"), prefs: (S.settings || {}).export_prefs }), 1200));
  St.mocks.export_copy = () => ({ ok: true, chars: 41234 });
  St.mocks.export_open = () => ({ ok: true });
})();
