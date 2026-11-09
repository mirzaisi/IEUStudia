/* IEUStudia — Export for AI chat: a course (or every course) as one zip to upload to ChatGPT (course_export.py). The
 * dialog picks the weeks (Everything up to now by default) and what goes in, with the zip's size and time; while the
 * zip is made it says what it is doing (with a bar and Cancel); at the end, where the zip is and how to upload it.
 * One export at a time: a double click starts one, and the dialog opened again shows the one being made. The export
 * goes on with the dialog closed (a toast says when it is ready). Plugs in through window.Studia. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, call, esc, I, SPIN, toast, modal, closeModal, A } = St;
  const $ = (s) => (typeof document !== "undefined" && document.querySelector ? document.querySelector(s) : null);
  const MB = 1048576;
  const said = (p) => (St.said ? St.said(p) : p);
  // Copy path, Copy the first message: refused by the clipboard, the text is shown to copy by hand (setup.js's Copy
  // failed dialog); without it, said in a toast
  async function copied(text, done) {
    const r = (await call("copy_text", text)) || { ok: false };
    if (St.copied) { St.copied(r, text, done); return; }
    toast(r.ok ? done : t("Could not access the clipboard."), r.ok ? "success" : "error");
  }
  let X = null;                  // the dialog: {courseId, opts, picked, inc, download, view, id, starting, cancelling, open}
  let lastInc = null;            // what you put in last time (this session)
  S.cexp = null;                 // the export being made, or the last one: its newest event (also with the dialog closed)

  const size = (n) => (n >= 1024 * MB ? `${t.num(n / 1024 / MB, 1, false)} GB` : n >= 10 * MB ? `${t.num(n / MB, 0, false)} MB`
    : n >= MB ? `${t.num(n / MB, 1, false)} MB` : `${Math.max(1, Math.round((n || 0) / 1024))} KB`);
  const dm = (iso) => (iso ? t.date(iso.slice(0, 10), "dm") : "");
  const range = (a, b) => (a ? (b && b !== a ? `${dm(a)} – ${dm(b)}` : dm(a)) : "");
  const nowWeeks = () => X.opts.weeks.filter((w) => w.now).map((w) => w.no);
  const isNow = () => { const n = nowWeeks(); return n.length > 0 && n.length === X.picked.size && n.every((x) => X.picked.has(x)); };
  const running = () => !!S.cexp && ["start", "progress"].includes(S.cexp.phase);
  const shown = () => {                       // this dialog is the one on screen
    if (!X || !X.open) return false;
    const m = $("#overlay .modal");
    return !m || m.classList.contains("cx");
  };

  // ------------------------------------------------------------------ what it holds, how big, how long
  function est() {
    const o = X.opts, inc = X.inc;
    let bytes = o.base + (inc.notices ? o.notices : 0), screens = 0, files = 0, lectures = 0, untranscribed = 0;
    const down = new Set();                    // (a file of a unit for two weeks is downloaded once)
    for (const w of o.weeks) {
      if (!X.picked.has(w.no)) continue;
      if (inc.transcripts) bytes += w.bytes.text;
      if (inc.screens) { bytes += w.bytes.screen_text + w.bytes.screens; screens += w.screens; }
      if (inc.files) { bytes += w.bytes.files; files += w.files; w.missing.forEach((id) => down.add(id)); }
      lectures += w.lectures; untranscribed += w.untranscribed;
    }
    if (inc.files && X.picked.size) { bytes += o.general.bytes; files += o.general.files; o.general.missing.forEach((id) => down.add(id)); }
    const dl = inc.files && X.download ? down.size : 0;
    return { bytes, secs: 2 + bytes / (40 * MB) + screens * 0.002 + dl * 5, dl, missing: down.size, screens, files, lectures, untranscribed };
  }
  const timeText = (secs) => (secs < 60 ? t("less than a minute") : t.n(Math.round(secs / 60), "about {n} minute", "about {n} minutes"));
  const choice = () => ({ weeks: isNow() ? null : [...X.picked].sort((a, b) => a - b), ...X.inc, download: X.download });

  // ------------------------------------------------------------------ the dialog: pick
  function weekRow(w) {
    const on = X.picked.has(w.no), all = !X.courseId;
    const nf = w.files + w.missing.length;
    const what = [w.lectures ? t.n(w.lectures, "{n} lecture", "{n} lectures") : "", nf ? t.n(nf, "{n} file", "{n} files") : ""].filter(Boolean).join(" · ");
    const name = w.topic || (all ? (w.courses || []).join(", ") : "") || "—";
    return `<label class="pd-wk cx-wk ${on ? "on" : ""} ${w.current ? "now" : ""}" title="${esc(name)}">
      <input type="checkbox" class="check" data-act="cx-week" data-no="${w.no}" ${on ? "checked" : ""}><span class="no">${t("W{n}", { n: w.no })}</span>
      <span class="t"><span class="cx-wt">${esc(name)}</span>${w.current ? ` <span class="pill now">${t("this week")}</span>` : ""}<span class="cx-dates">${esc(range(w.start, w.end))}</span></span>
      <span class="st">${esc(what || t("nothing yet"))}</span></label>`;
  }
  function incRow(k, icon, label, sub) {
    return `<label class="cx-inc ${X.inc[k] ? "on" : ""}"><input type="checkbox" class="check" data-act="cx-inc" data-k="${k}" ${X.inc[k] ? "checked" : ""}>
      ${I(icon, "sm")}<span class="cx-inc-t"><b>${label}</b><span>${sub}</span></span></label>`;
  }
  function pickHTML() {
    const o = X.opts, e = est(), all = !X.courseId;
    const courses = (S.courses || []).filter((c) => c.code && !c.inactive);
    const over = e.bytes > (o.chatgpt_max || 512 * MB);
    const tight = o.free > 0 && e.bytes * 1.1 + 20 * MB > o.free;
    const n = X.picked.size;
    return `<div class="cp-scroll">
      <h2>${I("archive")} ${t("Export for AI chat")}</h2>
      <p class="cx-lead">${all ? t("One zip with a folder for each of your courses and, in each, a folder for each week: what the lecturers said (with times), the screens shown in class with what was said while each was on screen, the course files, the announcements and the quizzes, exams and deadlines. Upload it to ChatGPT and ask about anything in it.")
        : t("One zip with a folder for each week: what the lecturer said (with times), the screens shown in class with what was said while each was on screen, the course files, the announcements and the quizzes, exams and deadlines. Upload it to ChatGPT and ask about anything in it.")}</p>
      <div class="field"><label>${t("Course")}</label><select class="input" data-act="cx-course">
        <option value="" ${all ? "selected" : ""}>${t("All courses")}</option>
        ${courses.map((c) => `<option value="${esc(c.id)}" ${c.id === X.courseId ? "selected" : ""}>${esc(c.code)} · ${esc(c.display_name || c.name || "")}</option>`).join("")}</select></div>
      <div class="field"><label class="cp-label">${t("Weeks")} <span class="dim sm">${isNow() ? t("everything up to now") : t.n(n, "{n} week picked", "{n} weeks picked")}</span>
        <span class="cp-tools"><button type="button" class="btn ghost sm ${isNow() ? "on" : ""}" data-act="cx-now">${t("Everything up to now")}</button><button type="button" class="btn ghost sm" data-act="cx-all">${t("Select all")}</button><button type="button" class="btn ghost sm" data-act="cx-none">${t("Clear")}</button></span></label>
        <div class="pd-weeks2 cx-weeks">${o.weeks.length ? o.weeks.map(weekRow).join("") : `<div class="dim sm cx-none">${t("No weekly plan yet: sync first.")}</div>`}</div></div>
      <div class="field"><label class="cp-label">${t("What goes in")}</label><div class="cx-incs">
        ${incRow("transcripts", "file-text", t("Lecture transcripts"), t("Everything the lecturer said, with times"))}
        ${incRow("screens", "presentation", t("Screens with what was said"), t("The slides and board shown in class, as pictures in the zip, each with what was said while it was on screen. Makes the zip larger."))}
        ${incRow("files", "layers", t("Course files"), t("The slides, PDFs and documents from IEULearn"))}
        ${incRow("notices", "megaphone", t("Announcements and deadlines"), t("The announcements and the quizzes, exams and homework IEUStudia knows about"))}</div></div>
      ${X.inc.files && e.missing ? `<div class="pd-gap cx-dl">${I("download")}<div class="grow"><div>${t.n(e.missing, "{n} course file of these weeks is not on this PC.", "{n} course files of these weeks are not on this PC.")}</div>
        <label class="cx-radio"><input type="radio" name="cx-dl" data-act="cx-dl" value="yes" ${X.download ? "checked" : ""}> ${t("Download first (recommended)")}</label>
        <label class="cx-radio"><input type="radio" name="cx-dl" data-act="cx-dl" value="no" ${X.download ? "" : "checked"}> ${t("Leave out (README.md lists them)")}</label></div></div>` : ""}
      ${e.untranscribed && n ? `<div class="note-sm">${I("info", "sm")}<span>${t.n(e.untranscribed, "{n} lecture of these weeks isn't transcribed yet: the zip lists it in its week.", "{n} lectures of these weeks aren't transcribed yet: the zip lists them in their week.")}</span></div>` : ""}
      </div>
      <div class="cp-foot"><div class="cp-cost" id="cx-est">${n ? `<span>${t("About {size}", { size: size(e.bytes) })} · ${timeText(e.secs)}${e.dl ? ` · ${t.n(e.dl, "{n} file to download first", "{n} files to download first")}` : ""}</span>
          ${over ? `<span class="err-txt">${I("alert-circle", "sm")} ${t("ChatGPT takes files up to 512 MB: leave out the screens or pick fewer weeks.")}</span>` : ""}
          ${tight ? `<span class="err-txt">${I("alert-circle", "sm")} ${t("Not enough free space on the disk for it ({free} free).", { free: size(o.free) })}</span>` : ""}` : `<span class="dim">${t("Pick at least one week.")}</span>`}</div>
        <div class="cp-btns"><button class="btn" data-act="modal-close">${t("Cancel")}</button>
          <button class="btn primary" data-act="cx-go" ${n && !X.starting ? "" : "disabled"}>${X.starting ? `${SPIN()} ${t("Starting…")}` : `${I("archive", "sm")} ${t("Export")}`}</button></div></div>`;
  }

  // ------------------------------------------------------------------ while it works, at the end, if it failed
  function stepText(e) {
    switch (e.step) {
      case "download": return t("Downloading file {i} of {n}: {name}", { i: e.i, n: e.n, name: e.name || "" }) + (e.total ? ` (${size(e.done || 0)} / ${size(e.total)})` : "");
      case "start": return t("Starting…");
      case "week": return e.week ? t("Week {no} ({i} of {n}): transcripts and course files", { no: e.week, i: e.wi, n: e.wn }) : t("Lectures without a week");
      case "screens": return t("Adding screens {i} of {n}", { i: e.i, n: e.n });
      case "files": return t("Adding course files {i} of {n}", { i: e.i, n: e.n });
      case "course": return t("Adding the course overview, announcements and deadlines");
      case "zip": return t("Writing the zip…");
      default: return t("Gathering what goes in…");
    }
  }
  function contextText(e) {
    const bits = [];
    if (e.course && e.cn > 1) bits.push(t("{course} ({i} of {n} courses)", { course: e.course, i: e.ci, n: e.cn }));
    if (["screens", "files"].includes(e.step) && e.week) bits.push(t("week {n}", { n: e.week }) + (e.wn ? ` (${e.wi}/${e.wn})` : ""));
    return bits.join(" · ");
  }
  function runBody() {
    const e = S.cexp || {};
    const f = Math.max(0, Math.min(1, e.fraction || 0));
    return `<div class="cx-msg">${SPIN()} <span>${esc(stepText(e))}</span></div><div class="cx-sub">${esc(contextText(e))}</div>
      <div class="bar wide cx-bar"><i style="width:${Math.round(100 * f)}%"></i></div>
      <div class="cx-sub">${e.estimate ? t("About {size}", { size: size(e.estimate) }) : ""}</div>`;
  }
  function runHTML() {
    const e = S.cexp || {};
    return `<div class="cp-scroll"><h2>${I("archive")} ${e.title ? t.html("Exporting {name}…", { name: e.title }) : t("Exporting…")}</h2>
      <div class="cx-run" id="cx-run">${runBody()}</div>
      <div class="note-sm">${I("info", "sm")}<span>${t("You can close this window: the export goes on, and IEUStudia tells you when the zip is ready.")}</span></div></div>
      <div class="cp-foot"><div class="cp-cost"></div><div class="cp-btns">
        <button class="btn" data-act="cx-cancel" ${X.cancelling ? "disabled" : ""}>${X.cancelling ? `${SPIN()} ${t("Cancelling…")}` : `${I("stop", "sm")} ${t("Cancel")}`}</button></div></div>`;
  }
  function doneHTML() {
    const e = S.cexp || {};
    const notes = [];
    if (e.renamed) notes.push(t("A zip of this name is open in another program, so this one got a new name."));
    if (e.untranscribed) notes.push(t.n(e.untranscribed, "{n} lecture isn't transcribed yet: README.md in the zip lists it.", "{n} lectures aren't transcribed yet: README.md in the zip lists them."));
    if (e.missing) notes.push(t.n(e.missing, "{n} course file isn't in the zip (not on this PC): README.md lists it.", "{n} course files aren't in the zip (not on this PC): README.md lists them."));
    if (e.students) notes.push(t.n(e.students, "{n} course file was left out: it lists students' numbers (grades or groups), so your classmates' details stay private. README.md names it.", "{n} course files were left out: they list students' numbers (grades or groups), so your classmates' details stay private. README.md names them."));
    return `<div class="cp-scroll"><div class="cx-done">${I("check-circle")}<div class="grow"><h2>${t("The zip is ready")}</h2>
        <div class="cx-file"><b>${esc(e.name || "")}</b> · ${size(e.bytes || 0)}</div><div class="cx-path" title="${esc(e.path || "")}">${esc(e.path || "")}</div></div></div>
      ${e.too_big ? `<div class="callout warning">${I("alert")}<div>${t("ChatGPT takes files up to 512 MB, and this zip is bigger. Export again without the screens, or with fewer weeks.")}</div></div>` : ""}
      <div class="cx-how"><div class="cx-how-h">${I("chat", "sm")} ${t("Upload it to ChatGPT")}</div><ol>
        <li>${t("Open ChatGPT and start a new chat.")}</li>
        <li>${t("Drag the zip into the chat (or click + and pick it). There is no need to unzip it.")}</li>
        <li>${t("Send the first message from README.md in the zip, with your question at the end.")} <button class="btn ghost sm" data-act="cx-copy-msg">${I("copy", "sm")} ${t("Copy the first message")}</button></li></ol></div>
      ${notes.map((x) => `<div class="note-sm">${I("info", "sm")}<span>${esc(x)}</span></div>`).join("")}</div>
      <div class="cp-foot"><div class="cp-cost"></div><div class="cp-btns">
        <button class="btn" data-act="cx-copy-path">${I("copy", "sm")} ${t("Copy path")}</button>
        <button class="btn" data-act="cx-show">${I("folder-open", "sm")} ${t("Show in folder")}</button>
        <button class="btn primary" data-act="modal-close">${t("Done")}</button></div></div>`;
  }
  function errorHTML() {
    const e = S.cexp || {};
    return `<div class="cp-scroll"><h2>${I("archive")} ${t("Export for AI chat")}</h2>
      <div class="callout error">${I("alert-circle")}<div><b>${t("The zip couldn't be made.")}</b> ${esc(e.message || t("Export failed"))}</div></div></div>
      <div class="cp-foot"><div class="cp-cost"></div><div class="cp-btns"><button class="btn" data-act="modal-close">${t("Close")}</button>
        <button class="btn primary" data-act="cx-again">${I("retry", "sm")} ${t("Try again")}</button></div></div>`;
  }
  function draw() {
    if (!X) return;
    const body = $(".modal .cp-scroll"), list = $(".cx-weeks");
    const keep = [body ? body.scrollTop : 0, list ? list.scrollTop : 0];
    const html = X.view === "run" ? runHTML() : X.view === "done" ? doneHTML() : X.view === "error" ? errorHTML()
      : X.opts ? pickHTML() : `<div class="cp-scroll"><h2>${I("archive")} ${t("Export for AI chat")}</h2>${St.skeleton ? St.skeleton() : SPIN()}</div>`;
    modal(html);
    const m = $("#overlay .modal");
    if (m) m.classList.add("pd", "cx");
    X.open = true;
    St.onModalClose = () => { if (X) X.open = false; };
    const nb = $(".modal .cp-scroll"), nl = $(".cx-weeks");
    if (nb) nb.scrollTop = keep[0];
    if (nl) nl.scrollTop = keep[1];
  }

  async function openDialog(courseId) {
    const st = await call("course_export_state");          // one being made (also from before the page was drawn again)
    if (st && st.ok && st.running && st.export) S.cexp = S.cexp && S.cexp.id === st.export.id ? { ...S.cexp, ...st.export } : { ...st.export };
    if (running()) {
      X = { ...(X || {}), id: S.cexp.id, courseId: S.cexp.course_id || "", view: "run", cancelling: !!(X && X.cancelling) };
      draw();
      return;
    }
    X = { courseId: courseId || "", opts: null, picked: new Set(), inc: { ...(lastInc || { transcripts: true, screens: true, files: true, notices: true }) },
      download: true, view: "pick", id: "", starting: false, cancelling: false };
    const asked = X;
    draw();
    const r = await call("course_export_options", asked.courseId);
    if (asked !== X) return;
    if (!r || !r.ok) { toast((r && r.error) || t("Export failed"), "error"); closeModal(); X = null; return; }
    X.opts = r;
    nowWeeks().forEach((no) => X.picked.add(no));
    if (X.open) draw();
  }

  // ------------------------------------------------------------------ events from Python
  St.onEvents.course_export = (e) => {
    const before = S.cexp && S.cexp.id === e.id ? S.cexp : {};
    S.cexp = { ...before, ...e };
    const mine = X && (X.id === e.id || (!X.id && X.view === "run"));
    if (mine && !X.id) X.id = e.id;
    if (e.phase === "start" || e.phase === "progress") {
      if (!mine || !shown()) return;
      if (X.view !== "run") { X.view = "run"; draw(); return; }
      const box = $("#cx-run");
      if (box) box.innerHTML = runBody(); else draw();          // only the progress changes: the rest stays put
      return;
    }
    if (mine) X.cancelling = false;
    if (e.phase === "done") {
      if (mine && shown()) { X.view = "done"; draw(); }
      else toast(t("The zip for ChatGPT is ready: {name} ({size})", { name: e.name || "", size: size(e.bytes || 0) }), "success", { label: t("Show in folder"), act: "cx-show", data: { path: e.path || "" } });
    } else if (e.phase === "cancelled") {
      if (mine && shown()) { X.view = "pick"; if (X.opts) draw(); else openDialog(X.courseId); }
      toast(t("Export cancelled: nothing was saved."), "info");
    } else if (e.phase === "error") {
      if (mine && shown()) { X.view = "error"; draw(); } else toast(e.message || t("Export failed"), "error");
    }
  };

  // ------------------------------------------------------------------ actions
  St.inputActs = (St.inputActs || []).concat(["cx-week", "cx-inc", "cx-dl"]);   // checkboxes and radios: app.js passes their clicks on
  Object.assign(A, {
    "cx-open": (el) => openDialog((el && el.dataset && el.dataset.id) || ""),
    "cx-course": (el) => openDialog(el.value),
    "cx-week": (el) => { const no = +el.dataset.no; if (el.checked) X.picked.add(no); else X.picked.delete(no); draw(); },
    "cx-now": () => { X.picked = new Set(nowWeeks()); draw(); },
    "cx-all": () => { X.picked = new Set(X.opts.weeks.map((w) => w.no)); draw(); },
    "cx-none": () => { X.picked = new Set(); draw(); },
    "cx-inc": (el) => { X.inc[el.dataset.k] = !!el.checked; lastInc = { ...X.inc }; draw(); },
    "cx-dl": (el) => { X.download = el.value === "yes"; draw(); },
    "cx-go": async () => {
      // one click, one export: a double click, or a click repeated while the window is slow to answer, starts one
      if (!X || X.starting || !X.picked.size || X.view !== "pick") return;
      const asked = X;
      asked.starting = true; draw();
      const r = await call("course_export_start", asked.courseId, choice());
      asked.starting = false;
      if (asked !== X) return;
      if (!r || !r.ok) { draw(); toast((r && r.error) || t("Export failed"), "error"); return; }
      X.id = r.id;
      if (r.running && r.export) S.cexp = { ...r.export };      // one was being made already: that one is shown
      else if (!S.cexp || S.cexp.id !== r.id) {                 // (its first event may have come already)
        const c = (S.courses || []).find((x) => x.id === X.courseId);
        S.cexp = { type: "course_export", id: r.id, phase: "start", step: "plan", course_id: X.courseId, title: X.courseId ? (c ? c.code : "") : t("All courses") };
      }
      X.view = "run"; draw();
    },
    "cx-cancel": async () => {
      if (!X || X.cancelling) return;
      X.cancelling = true; draw();
      await call("course_export_cancel");
    },
    "cx-again": () => { if (!X) return; X.view = "pick"; if (X.opts) draw(); else openDialog(X.courseId); },
    "cx-show": (el) => said(call("course_export_show", (el && el.dataset && el.dataset.path) || (S.cexp || {}).path || "")),
    "cx-copy-path": () => copied((S.cexp || {}).path || "", t("Copied")),
    "cx-copy-msg": () => copied((S.cexp || {}).first_message || "", t("Copied: paste it into ChatGPT with the zip.")),
  });
  St.cexp = { open: openDialog };

  // ------------------------------------------------------------------ browser preview (mock)
  const mockWeek = (no, topic, now, lec, files, shots) => {
    const iso = (days) => new Date(Date.UTC(2026, 8, 23 + days)).toISOString().slice(0, 10);
    return { no, start: iso(7 * (no - 1)), end: iso(7 * (no - 1) + 6), topic, current: no === 7, now, lectures: lec, untranscribed: no === 7 ? 1 : 0,
      days: lec, files, missing: no === 6 ? ["m6"] : [], links: 0, screens: shots, courses: ["MATH 240"],
      bytes: { text: lec * 20000, screen_text: shots ? lec * 21000 : 0, screens: shots * 140000, files: files * 520000 } };
  };
  St.mocks.course_export_options = (cid) => ({ ok: true, all: !cid, courses: [{ id: "17933", code: "MATH 240", name: "Probability" }], today: "2026-11-07",
    weeks: [mockWeek(1, "Sample Spaces, Events & Counting", true, 1, 1, 80), mockWeek(2, "Combinations & the First Probability Rules", true, 1, 1, 103), mockWeek(3, "Independence, Reliability & Bayes", true, 1, 1, 90),
      mockWeek(4, "Random Variables, PMF & CDF", true, 1, 1, 95), mockWeek(5, "Mean, Variance & the Named Distributions", true, 2, 1, 0), mockWeek(6, "Poisson & the Continuous Half", true, 0, 1, 0),
      mockWeek(7, "The Uniform & the Normal", true, 1, 1, 195), mockWeek(8, "Midterm Exam", false, 0, 0, 0)],
    general: { files: 1, missing: [], bytes: 340000 }, loose: 0, base: 4000, notices: 5000, folder: "C:\\Users\\you\\Documents\\IEUStudia\\MATH 240\\Exports", free: 50e9, chatgpt_max: 512 * MB });
  St.mocks.course_export_state = () => ({ ok: true, running: false, export: null });
  St.mocks.course_export_cancel = () => { mockStop = true; return { ok: true }; };
  St.mocks.course_export_show = () => ({ ok: true });
  let mockStop = false;
  St.mocks.course_export_start = (cid) => {
    const id = "mock" + Date.now(), base = { type: "course_export", id, course_id: cid, title: cid ? "MATH 240" : t("All courses"), estimate: 84 * MB };
    const steps = [{ step: "download", i: 1, n: 1, name: "Week06_Student.pdf", fraction: 0.5 }, { step: "week", course: "MATH 240", ci: 1, cn: 1, week: 1, wi: 1, wn: 7, fraction: 0.05 },
      { step: "screens", i: 40, n: 563, week: 1, wi: 1, wn: 7, fraction: 0.2 }, { step: "screens", i: 300, n: 563, week: 4, wi: 4, wn: 7, fraction: 0.55 },
      { step: "files", i: 6, n: 8, week: 6, wi: 6, wn: 7, fraction: 0.85 }, { step: "zip", fraction: 0.98 }];
    mockStop = false;
    steps.forEach((s, k) => setTimeout(() => { if (!mockStop) window.__lsEvent({ ...base, phase: "progress", ...s }); }, 400 * (k + 1)));
    setTimeout(() => window.__lsEvent(mockStop ? { ...base, phase: "cancelled" } : { ...base, phase: "done", name: "MATH 240 - everything up to 7 Nov 2026.zip", bytes: 84031320,
      path: "C:\\Users\\you\\Documents\\IEUStudia\\MATH 240\\Exports\\MATH 240 - everything up to 7 Nov 2026.zip", untranscribed: 1, missing: 0, renamed: false,
      first_message: "This zip holds my MATH 240 course, exported from IEUStudia: …" }), 400 * (steps.length + 1));
    return { ok: true, id };
  };
})();
