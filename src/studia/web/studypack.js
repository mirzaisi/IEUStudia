/* IEUStudia — study packs: a complete teaching document the AI writes from the files and lectures the student picks
 * (studypack.py), with worked examples, formulas, diagrams and the course's slides, shown here and saved as a PDF.
 * A pack is a plan of kind "pack": the plan list, its events, Stop and Resume are plans.js's; this file adds the
 * dialog that picks the material (with what it will cost) and the pack's own page. Plugs in through window.Studia. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, call, esc, I, SPIN, toast, modal, closeModal, go, renderView, skeleton, A, fmtK } = St;
  const $ = (s, el = document) => el.querySelector(s);
  const isPack = (p) => ((p && p.scope) || {}).kind === "pack";
  const STAGES = () => [["gather", t("Course material")], ["outline", t("Chapters")], ["sections", t("Writing")], ["figures", t("Figures")], ["assemble", t("Document")]];
  const weekSpan = (weeks) => {
    if (!weeks || !weeks.length) return t("all weeks so far");
    const runs = [];
    weeks.forEach((w) => { const r = runs[runs.length - 1]; if (r && w === r[1] + 1) r[1] = w; else runs.push([w, w]); });
    return runs.map(([a, b]) => (a === b ? t("week {n}", { n: a }) : t("weeks {a}–{b}", { a, b }))).join(", ");
  };
  // the files the plan of the chapters (a cheatsheet's topic list) saw only the first pages of: a whole term of a
  // heavy course is more than one request takes (studypack.gather)
  const cutNote = (cut) => t.html("Too long for one request, so the AI plans from the first pages of these files only: {files}. Pick fewer weeks so it sees every page.",
    { files: cut.map((f) => (f.seen ? t("{name} (pages 1–{seen} of {pages})", { name: f.name, seen: f.seen, pages: f.pages }) : f.name)).join(", ") });
  S.packDoc = {};            // the document's HTML by plan id (it is big: asked for once)
  let P = null;              // the dialog's state

  // ------------------------------------------------------------------ the dialog: pick the material
  // the same dialog makes a cheatsheet (kind "sheet", cheatsheet.js): only its texts and the scope's kind differ
  const sheet = () => P.kind === "sheet";
  const heading = () => sheet() ? `${I("sigma")} ${t("New cheatsheet")}` : `${I("book")} ${t("New study pack")}`;
  async function packDialog(courseId, kind) {
    const courses = S.courses.filter((c) => c.code);
    courseId = courseId || (S.view.courseId) || (courses[0] || {}).id;
    if (!courseId) { toast(t("Sync your courses first."), "error"); return; }
    P = { kind: kind === "sheet" ? "sheet" : "pack", courseId, opts: null, weeks: new Set(), files: new Set(), days: new Set(), model: "", effort: (P && P.effort) || "", difficulties: (P && P.difficulties) || "", est: null };
    modal(`<div class="cp-scroll"><h2>${heading()}</h2>${skeleton()}</div>`);
    $(".modal").classList.add("pd", "pk");
    const r = await call("pack_options", courseId);
    if (!r.ok) { toast(r.error, "error"); closeModal(); return; }
    P.opts = r;
    P.model = r.model || "sonnet";
    const cur = r.weeks.find((w) => w.current) || [...r.weeks].reverse().find((w) => w.past) || r.weeks[0];
    if (cur) pickWeek(cur.no, true);
    draw();
    estimate();
  }
  function pickWeek(no, on) {
    const w = P.opts.weeks.find((x) => x.no === no); if (!w) return;
    if (on) P.weeks.add(no); else P.weeks.delete(no);
    const kept = new Set(P.opts.weeks.filter((x) => P.weeks.has(x.no)).flatMap((x) => x.files.map((f) => f.id)));   // a unit for two weeks
    w.files.forEach((f) => { if (on && f.ready) P.files.add(f.id); else if (!on && !kept.has(f.id)) P.files.delete(f.id); });
    w.days.forEach((d) => { if (on && d.ready && d.mine) P.days.add(d.day_key); else if (!on) P.days.delete(d.day_key); });
  }
  function costHTML() {
    const ai = P.opts.ai || {}, e = P.est;
    if (!(ai.available && ai.can_plans !== false)) return `<span class="err-txt">${I("alert-circle", "sm")} ${(S.system.ai || {}).provider === "local" ? t("Plans, review sheets and study packs need ChatGPT or Claude (the offline model is too small).") : t("Set up ChatGPT or Claude in Settings → AI first.")}</span>`;
    if (!P.files.size && !P.days.size) return `<span class="dim">${t("Pick at least one file or lecture.")}</span>`;
    if (!e) return `<span class="dim">${SPIN()} ${t("Estimating…")}</span>`;
    const more = (St.effortCostNote ? St.effortCostNote(e, scope()) : "")    // effort.js: a heavy level, a big read
      + ((e.files_cut || []).length ? `<div class="dim sm">${I("info", "xs")} ${cutNote(e.files_cut)}</div>` : "");
    if (sheet()) return `<div class="pk-heavy light">${I("info", "sm")}<div><b>${t.html("Uses part of your {ai} plan.", { ai: St.aiName() })}</b>
        ${t.n(e.requests, "About {k} tokens in {n} request, {minutes}.", "About {k} tokens in {n} requests, {minutes}.", { k: fmtK(e.main_input + e.main_output), minutes: e.minutes })}
        <div class="dim sm">${t("If your plan's limit is reached it stops; resume it later and what is written is kept.")}</div></div></div>${more}`;
    return `<div class="pk-heavy">${I("alert", "sm")}<div><b>${t.html("This uses a lot of your {ai} plan.", { ai: St.aiName() })}</b>
        ${t.html("About {k} tokens in {n} requests ({chapters} chapters), {minutes}.", { k: fmtK(e.main_input + e.main_output), n: e.requests, chapters: e.chapters, minutes: e.minutes })}
        <div class="dim sm">${t("If your plan's limit is reached it stops; resume it later and the finished chapters are kept.")}</div></div></div>${more}`;
  }
  function draw() {
    const r = P.opts, courses = S.courses.filter((c) => c.code);
    const old = $(".modal .cp-scroll"), keep = old ? old.scrollTop : 0;
    const picked = r.weeks.filter((w) => P.weeks.has(w.no));
    const missing = [...new Set(picked.flatMap((w) => w.files.filter((f) => !f.downloaded).map((f) => f.id)))];   // (a file of two weeks once)
    const todo = picked.flatMap((w) => w.days.filter((d) => !d.ready && d.mine).map((d) => d.day_key));
    const ai = r.ai || {};
    const models = [["sonnet", t("Sonnet (recommended)")], ["haiku", t("Haiku (lightest)")], ["opus", t("Opus (best, heavy)")]];
    const item = (kind, id, on, ready, label, sub) => `<label class="pk-item ${ready ? "" : "off"}">
        <input type="checkbox" class="check" data-act="pk-item" data-kind="${kind}" data-id="${esc(id)}" ${on ? "checked" : ""} ${ready ? "" : "disabled"}>
        ${I(kind === "file" ? "file-text" : "video", "sm")}<span class="grow">${esc(label)}</span><span class="dim sm">${sub}</span></label>`;
    const material = picked.map((w) => `<div class="pk-week"><div class="pk-week-h">${t("Week {n}", { n: w.no })} <span class="dim">${esc(w.topic || "")}</span></div>
        ${w.files.map((f) => item("file", f.id, P.files.has(f.id), f.ready, f.name, f.ready ? t.n(f.pages, "{n} page", "{n} pages") : f.downloaded ? t("not read yet") : t("not on this PC"))).join("")}
        ${w.days.filter((d) => d.mine || P.days.has(d.day_key)).map((d) => item("day", d.day_key, P.days.has(d.day_key), d.ready, `${d.label} · ${d.kind}`, d.ready ? t("transcribed") : t("not transcribed"))).join("")}
        ${!w.files.length && !w.days.length ? `<div class="dim sm pk-none">${t("Nothing for this week yet.")}</div>` : ""}</div>`).join("");
    modal(`<div class="cp-scroll">
      <h2>${heading()}</h2>
      <p class="pk-lead">${sheet() ? t("Every formula, definition, rule, unit and standard method of the material you pick, on one or two A4 pages for a few weeks: grouped by topic, with what the symbols mean and when each one applies. Read it here or save it as a PDF.")
        : t("A complete document that teaches everything in the material you pick: clear explanations in order, worked examples, formulas, diagrams and your own slides. Read it here or save it as a PDF.")}</p>
      <div class="field"><label>${t("Course")}</label><select class="input" data-act="pk-course">${courses.map((c) => `<option value="${esc(c.id)}" ${c.id === P.courseId ? "selected" : ""}>${esc(c.code)} — ${esc(c.display_name)}</option>`).join("")}</select></div>
      <div class="field"><label class="cp-label">${t("Weeks")} <span class="dim sm">${t("{n} picked", { n: P.weeks.size })}</span></label>
        <div class="pk-weeks">${r.weeks.map((w) => `<button type="button" class="pk-wk ${P.weeks.has(w.no) ? "on" : ""} ${w.current ? "now" : ""}" data-act="pk-week" data-no="${w.no}" title="${esc(w.topic || "")}"><b>${t("W{n}", { n: w.no })}</b><span>${esc(w.topic || "—")}</span></button>`).join("")}</div></div>
      ${picked.length ? `<div class="field"><label class="cp-label">${t("Material")} <span class="dim sm">${t.n(P.files.size, "{n} file", "{n} files")} · ${t.n(P.days.size, "{n} lecture", "{n} lectures")}</span></label>
        <div class="pk-material">${material}</div></div>` : ""}
      ${missing.length || todo.length ? `<div class="pd-gaps">
        ${missing.length ? `<div class="pd-gap">${I("file")}<span class="grow">${t.n(missing.length, "{n} file of these weeks is not on this PC.", "{n} files of these weeks are not on this PC.")}</span><button class="btn sm" data-act="pk-download">${I("download", "sm")} ${t("Download {n}", { n: missing.length })}</button></div>` : ""}
        ${todo.length ? `<div class="pd-gap">${I("video")}<span class="grow">${t.n(todo.length, "{n} class day of these weeks is not transcribed.", "{n} class days of these weeks are not transcribed.")}</span><button class="btn sm" data-act="pk-transcribe">${I("wave", "sm")} ${t("Transcribe {n}", { n: todo.length })}</button></div>` : ""}</div>` : ""}
      <div class="cp-opts">
        ${St.isChatGPT() ? "" : `<div class="cp-opt"><div class="t">${t("Model")}</div><select class="input" data-pk="model">${models.map(([k, l]) => `<option value="${k}" ${P.model === k ? "selected" : ""}>${l}</option>`).join("")}</select></div>`}
        ${St.effortField ? St.effortField("pk", "plan", P.effort, { attrs: 'data-pk="effort"' }) : ""}
        ${sheet() ? "" : `<div class="cp-opt"><div class="t">${t("What do you find hard?")}</div><input class="input" data-pk="difficulties" value="${esc(P.difficulties)}" placeholder="${t("e.g. when to use Bayes vs. total probability")}"></div>`}
      </div></div>
      <div class="cp-foot"><div class="cp-cost" id="pk-cost">${costHTML()}</div>
        <div class="cp-btns"><button class="btn" data-act="modal-close">${t("Cancel")}</button>
          <button class="btn primary" data-act="pk-go" ${(P.files.size || P.days.size) && ai.available && ai.can_plans !== false && !P.starting ? "" : "disabled"}>${sheet() ? `${I("sigma", "sm")} ${t("Make cheatsheet")}` : `${I("book", "sm")} ${t("Write study pack")}`}</button></div></div>`);
    $(".modal").classList.add("pd", "pk");
    const nb = $(".modal .cp-scroll"); if (nb) nb.scrollTop = keep;
  }
  let estTimer = null;
  function estimate() {
    clearTimeout(estTimer);
    P.est = null;
    const box = $("#pk-cost"); if (box) box.innerHTML = costHTML();
    if (!P.files.size && !P.days.size) return;
    const asked = P;
    estTimer = setTimeout(async () => {
      const r = await call("plan_estimate", P.courseId, scope());
      if (asked !== P) return;
      P.est = r.ok ? r.estimate : null;
      const el = $("#pk-cost"); if (el) el.innerHTML = costHTML();
    }, 300);
  }
  function readInputs() {
    document.querySelectorAll(".modal [data-pk]").forEach((el) => { P[el.dataset.pk] = el.value; });
  }
  // the dialog's go button while its pack is being started: disabled, saying so; given back as it was if that failed
  function goBusy(el, on) {
    if (!el) return;
    if (on) { el.dataset.label = el.innerHTML; el.innerHTML = `${SPIN()} ${t("Starting…")}`; }
    else if (el.dataset.label) { el.innerHTML = el.dataset.label; delete el.dataset.label; }
    el.disabled = on;
  }
  const scope = () => ({ kind: P.kind, weeks: [...P.weeks].sort((a, b) => a - b), files: [...P.files], days: [...P.days], picked: true,
    difficulties: P.difficulties, model: P.model, effort: P.effort || "" });

  // ------------------------------------------------------------------ the pack's page
  function stateHTML(p, stages = STAGES(), note = "") {
    const done = new Set(p.stages_done || []);
    const cur = stages.findIndex(([k]) => k === p.stage);
    const busy = ["running", "queued"].includes(p.status);
    const steps = stages.map(([k, l], i) => {
      const st = done.has(k) || cur > i ? "done" : i === cur && busy ? "now" : i === cur ? "stopped" : "";
      return `<div class="pl-step ${st}"><span class="dot">${st === "done" ? I("check", "xs") : st === "now" ? SPIN() : i + 1}</span>${l}</div>`;
    }).join("");
    const chapters = (p.chapters || []).map((c, i) => `<li class="${c.done ? "done" : ""}">${c.done ? I("check", "xs") : `<span class="pk-n">${i + 1}</span>`}<span>${esc(c.title)}</span></li>`).join("");
    const notice = St.planStateNotice ? St.planStateNotice(p) : "";      // failed or stopped: what to do, with Resume (notices.js)
    return `${notice}<div class="card pl-progress"><div class="pl-steps">${steps}</div>
      <div class="pl-msg">${p.status === "error" ? (notice ? "" : `<span class="err-txt">${I("alert-circle", "sm")} ${esc(p.error)}</span>`) : esc(p.message || "")}</div>
      ${busy ? `<div class="bar wide"><i style="width:${Math.round(100 * (p.progress || 0))}%"></i></div>` : ""}
      ${chapters ? `<ol class="pk-chapters">${chapters}</ol>` : ""}
      <div class="note-sm">${I("info", "sm")}<span>${note || t("Runs in the background, so you can keep using the app. If it stops (for example at your plan's limit), resume it later: the finished chapters are kept.")}</span></div></div>`;
  }
  function notesHTML(r) {
    const notes = [];
    const miss = (r.materials_missing || []).length, todo = (r.days_missing || []).length;
    if (miss) notes.push(t.n(miss, "{n} picked file was not on this PC, so the pack doesn't use it.", "{n} picked files were not on this PC, so the pack doesn't use them."));
    if (todo) notes.push(t.n(todo, "{n} picked lecture was not transcribed, so the pack doesn't use it.", "{n} picked lectures were not transcribed, so the pack doesn't use them."));
    if ((r.uncovered || []).length) notes.push(t.html("The plan of the chapters left out: {topics}.", { topics: r.uncovered.join(", ") }));
    if ((r.files_cut || []).length) notes.push(cutNote(r.files_cut));
    if (!r.pdf) notes.push(t("The PDF wasn't made yet: Save PDF makes it."));
    if (St.packNotices) return St.packNotices(r, notes);       // coloured, with Download / Transcribe
    return notes.map((n) => `<div class="note-sm">${I("info", "sm")}<span>${n}</span></div>`).join("");
  }
  function packView(v) {
    const p = S.planCache[v.planId] || (S.plans || []).find((x) => x.id === v.planId);
    const c = St.findCourse(p.course_id) || {};
    const sc = p.scope || {}, r = p.result || {};
    const busy = ["running", "queued"].includes(p.status), ready = p.status === "done";
    const chips = [weekSpan(sc.weeks), (sc.files || []).length ? t.n(sc.files.length, "{n} file", "{n} files") : "", (sc.days || []).length ? t.n(sc.days.length, "{n} lecture", "{n} lectures") : "",
      ready && r.figures ? t.n(r.figures, "{n} figure", "{n} figures") : ""].filter(Boolean);
    let h = `<div class="view-inner plan-page pk-page">
      <div class="pl-top"><div class="grow"><div class="dim">${esc(c.code || "")} · ${t("study pack")}</div>
        <h1>${esc(p.title || (busy ? t("Writing your study pack…") : t("Study pack")))}</h1>
        <div class="pl-chips">${chips.map((x) => `<span class="chip">${esc(x)}</span>`).join("")}</div></div>
        <div class="pl-acts">
          ${ready ? `<button class="btn primary sm" data-act="pk-pdf" data-how="save" data-id="${esc(p.id)}">${I("download", "sm")} ${t("Save PDF…")}</button>
            <button class="btn sm" data-act="pk-pdf" data-how="open" data-id="${esc(p.id)}">${I("file-text", "sm")} ${t("Open PDF")}</button>
            <button class="btn ghost sm" data-act="pk-pdf" data-how="folder" data-id="${esc(p.id)}">${I("folder-open", "sm")} ${t("Show in folder")}</button>` : ""}
          ${busy ? `<button class="btn sm" data-act="plan-stop" data-id="${esc(p.id)}">${I("stop", "sm")} ${t("Stop")}</button>` : ""}
          ${["error", "paused"].includes(p.status) ? `<button class="btn primary sm" data-act="plan-resume" data-id="${esc(p.id)}">${I("retry", "sm")} ${t("Resume")}</button>` : ""}
          <button class="btn ghost icon sm" data-act="plan-delete" data-id="${esc(p.id)}" title="${t("Delete this study pack")}">${I("x", "sm")}</button></div></div>`;
    if (!ready) return h + stateHTML(p) + `</div>`;
    const doc = S.packDoc[p.id];
    const toc = (r.chapters || []).map((ch, i) => `<li><button type="button" data-act="pk-goto" data-to="${esc(ch.id)}"><span class="pk-n">${i + 1}</span><span>${esc(ch.title)}</span></button></li>`).join("");
    return h + notesHTML(r) + `<div class="pk-layout"><aside class="pk-toc card"><div class="pk-toc-h">${t("Contents")}</div><ol>${toc}</ol></aside>
      <div class="card pk-paper"><article class="sp-doc" id="pk-doc" lang="en">${doc != null ? doc : skeleton()}</article></div></div></div>`;
  }
  const loading = new Set();
  async function loadDoc(id) {
    if (S.packDoc[id] != null || loading.has(id)) return;
    loading.add(id);
    const r = await call("pack_doc", id);
    loading.delete(id);
    if (!r.ok) { toast(r.error, "error"); return; }
    S.packDoc[id] = r.html || `<p class="dim">${t("The document is empty.")}</p>`;
    if (S.view.name === "plan" && S.view.planId === id) renderView();
  }

  // ------------------------------------------------------------------ wiring (after plans.js)
  const planView = St.views.plan;
  St.views.plan = (v) => {
    const p = S.planCache[v.planId] || (S.plans || []).find((x) => x.id === v.planId);
    return isPack(p) ? packView(v) : planView(v);
  };
  const after = St.after.plan;
  St.after.plan = (v) => {
    if (after) after(v);
    const p = S.planCache[v.planId];
    if (isPack(p) && p.status === "done") loadDoc(p.id);
  };
  const onPlan = St.onEvents.plan;
  let refresh = null;
  St.onEvents.plan = (e) => {
    onPlan(e);
    const cached = S.planCache[e.id];
    if (!isPack(cached) || e.status !== "running" || !["outline", "sections", "figures"].includes(e.stage)) return;
    clearTimeout(refresh);                       // the chapter list fills in as they are written
    refresh = setTimeout(async () => {
      const r = await call("plan_get", e.id);
      if (!r.ok) return;
      S.planCache[e.id] = r.plan;
      if (S.view.name === "plan" && S.view.planId === e.id) renderView();
    }, 400);
  };

  St.pack = { stateHTML, weekSpan, loadDoc, cutNote };       // for cheatsheet.js

  Object.assign(A, {
    "pack-new": (el) => packDialog(el && el.dataset.id, el && el.dataset.kind),
    "pk-course": (el) => packDialog(el.value, P && P.kind),
    "pk-week": (el) => { readInputs(); const no = +el.dataset.no; pickWeek(no, !P.weeks.has(no)); draw(); estimate(); },
    "pk-item": (el) => {
      readInputs();
      const set = el.dataset.kind === "file" ? P.files : P.days;
      if (el.checked) set.add(el.dataset.id); else set.delete(el.dataset.id);
      draw(); estimate();
    },
    "pk-download": async () => {
      readInputs();
      const ids = [...new Set(P.opts.weeks.filter((w) => P.weeks.has(w.no)).flatMap((w) => w.files.filter((f) => !f.downloaded).map((f) => f.id)))];
      const r = await call("materials_download", ids);
      if (r.ok) toast(t.n(ids.length, "Downloading {n} file — pick it once it is in", "Downloading {n} files — pick them once they are in"), "success");
      else toast(r.error || t("Download failed"), "error");
    },
    "pk-transcribe": async () => {
      readInputs();
      const days = P.opts.weeks.filter((w) => P.weeks.has(w.no)).flatMap((w) => w.days.filter((d) => !d.ready && d.mine).map((d) => d.day_key));
      let n = 0, error = "";
      for (const k of days) { const r = await call("transcribe_day", k, true); if (r.ok) n += r.queued || 0; else error = error || r.error || ""; }
      if (!n && error) { toast(error, "error"); return; }       // not "nothing left": it couldn't be queued
      toast(n ? t.n(n, "{n} recording queued for transcription", "{n} recordings queued for transcription") : t("Nothing left to transcribe there"), n ? "success" : "info");
    },
    "pk-go": async (el) => {
      // one click writes one pack (or cheatsheet): a double click, or a second click while the window is slow to
      // answer (a full sync), used to start two runs and use the student's AI plan twice
      if (!P || P.starting) return;
      readInputs();
      const asked = P;
      asked.starting = true; goBusy(el, true);
      const r = await call("plan_create", P.courseId, scope());
      asked.starting = false;
      if (!r.ok) { goBusy($(".modal [data-act='pk-go']"), false); toast(r.error, "error"); return; }
      closeModal();
      S.plans = [r.plan, ...(S.plans || []).filter((p) => p.id !== r.plan.id)];
      S.planCache[r.plan.id] = { ...r.plan, stages_done: [], chapters: [] };
      go({ name: "plan", planId: r.plan.id });
    },
    "pk-goto": (el) => { const to = document.getElementById(el.dataset.to); if (to) to.scrollIntoView({ behavior: "smooth", block: "start" }); },
    "pk-pdf": async (el) => {
      el.disabled = true;
      const r = await call("pack_pdf", el.dataset.id, el.dataset.how);
      el.disabled = false;
      if (!r.ok) toast(r.error, "error");
      else if (r.saved) toast(t("Saved: {file}", { file: r.saved }), "success");
    },
  });
  document.addEventListener("change", (e) => { if (P && e.target.closest && e.target.closest(".modal [data-pk]")) { readInputs(); estimate(); } });

  // ------------------------------------------------------------------ browser preview (mock)
  const mockDoc = `<header class="sp-cover"><div class="sp-kicker">MATH 240 · Study pack</div><h1>Conditional probability and Bayes' theorem</h1>
    <div class="sp-sub">Weeks 4–5 · made with IEUStudia on 4 Nov 2026</div><p class="sp-intro">This pack teaches everything in weeks 4–5: conditional probability, the multiplication rule, the law of total probability and Bayes' theorem. Read the chapters in order; try each worked example before reading its solution.</p>
    <div class="sp-sources">Files: Week04.pdf, Week05_Student.pdf</div><div class="sp-sources">Lectures: 2026-10-14, 2026-10-21</div></header>
    <nav class="sp-toc"><h2>Contents</h2><ol><li><a href="#c1"><span class="n">1</span><span class="t">Conditional probability</span><span class="p">3</span></a></li><li><a href="#c2"><span class="n">2</span><span class="t">The law of total probability</span><span class="p">6</span></a></li><li><a href="#c3"><span class="n">3</span><span class="t">Bayes' theorem</span><span class="p">9</span></a></li></ol></nav>
    <section class="sp-ch" id="c1"><h2><span class="n">1</span> Conditional probability</h2><p>Often we learn something that changes the odds. If you know a student passed the quiz, the chance they studied with videos is different from the chance for any student. Conditional probability makes that precise.</p>
    <div class="sp-box sp-definition"><div class="sp-box-h"><b>Definition</b> · Conditional probability</div><p>For events $A$ and $B$ with $P(B) > 0$, the probability of $A$ given $B$ is</p><div class="sp-math">$$P(A\\mid B) = \\frac{P(A\\cap B)}{P(B)}.$$</div></div>
    <p>Knowing $B$ happened shrinks the sample space to $B$; we then ask how much of $B$ is also in $A$.</p>
    <figure class="sp-fig sp-diagram"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 240" class="sp-svg" role="img"><rect x="20" y="20" width="600" height="200" rx="10" fill="none" stroke="#1f2937" stroke-width="2"></rect><circle cx="260" cy="120" r="80" fill="#dbeafe" fill-opacity="0.8" stroke="#2563eb" stroke-width="2"></circle><circle cx="380" cy="120" r="80" fill="#fee2e2" fill-opacity="0.7" stroke="#dc2626" stroke-width="2"></circle><text x="210" y="125" font-family="Segoe UI, Arial, sans-serif" font-size="16" fill="#1f2937">A</text><text x="420" y="125" font-family="Segoe UI, Arial, sans-serif" font-size="16" fill="#1f2937">B</text><text x="298" y="125" font-family="Segoe UI, Arial, sans-serif" font-size="14" fill="#1f2937">A ∩ B</text><text x="36" y="44" font-family="Segoe UI, Arial, sans-serif" font-size="14" fill="#1f2937">S</text></svg><figcaption><b>Figure 1.1.</b> Given $B$, only the red circle is left; $A\\mid B$ is the overlap's share of it.</figcaption></figure>
    <h3>Worked example</h3><div class="sp-box sp-example"><div class="sp-box-h"><b>Example</b> · A two-way table</div><p>Of 40 students who passed, 12 studied with videos. What is $P(\\text{videos}\\mid\\text{passed})$?</p><p><strong>Solution.</strong> Passing is the condition, so the sample space is the 40 who passed: $P = 12/40 = 0.3$.</p></div>
    <div class="sp-box sp-mistake"><div class="sp-box-h"><b>Common mistake</b></div><p>$P(A\\mid B)$ is not $P(B\\mid A)$: the condition decides the sample space.</p></div>
    <div class="sp-box sp-check"><div class="sp-box-h"><b>Check yourself</b></div><ol><li>If $A\\subset B$, what is $P(A\\mid B)$? <em>Answer:</em> $P(A)/P(B)$.</li><li>Can $P(A\\mid B)$ be larger than $P(A)$? <em>Answer:</em> yes, when $B$ makes $A$ more likely.</li></ol></div></section>
    <section class="sp-ch" id="c2"><h2><span class="n">2</span> The law of total probability</h2><p>Split the sample space into cases $B_1, \\dots, B_n$ that don't overlap and cover everything. Then</p><div class="sp-math">$$P(A) = \\sum_{i=1}^{n} P(A\\mid B_i)\\,P(B_i).$$</div>
    <figure class="sp-fig sp-slide"><img src="../assets/icon.png" alt="Course slide"><figcaption><b>Figure 2.1.</b> The tree from the week 5 slides. <span class="sp-src">(Week05_Student.pdf, page 12)</span></figcaption></figure></section>
    <section class="sp-ch" id="c3"><h2><span class="n">3</span> Bayes' theorem</h2><div class="sp-box sp-key"><div class="sp-box-h"><b>Key idea</b></div><div class="sp-math">$$P(B_j\\mid A) = \\frac{P(A\\mid B_j)P(B_j)}{\\sum_i P(A\\mid B_i)P(B_i)}$$</div></div>
    <div class="sp-table"><table><thead><tr><th></th><th>Test +</th><th>Test −</th></tr></thead><tbody><tr><td>Disease</td><td>0.0095</td><td>0.0005</td></tr><tr><td>No disease</td><td>0.099</td><td>0.891</td></tr></tbody></table></div></section>`;
  const mockPack = (status) => ({ id: status === "done" ? "pk1" : "pk2", course_id: "17933", title: status === "done" ? "Conditional probability and Bayes' theorem" : "", status, stage: status === "done" ? "done" : "sections",
    progress: status === "done" ? 1 : 0.46, message: status === "done" ? "3 chapters · 2 figures" : "Writing chapter 3 of 5 (sonnet)…", error: "", parent_id: "", created_at: new Date().toISOString(),
    scope: { kind: "pack", weeks: [4, 5], files: ["a", "b"], days: ["d1", "d2"], model: "sonnet" }, stages_done: status === "done" ? [] : ["gather", "outline"],
    chapters: [{ id: "c1", title: "Conditional probability", done: true }, { id: "c2", title: "The multiplication rule", done: true }, { id: "c3", title: "The law of total probability", done: false }, { id: "c4", title: "Bayes' theorem", done: false }, { id: "c5", title: "Independence", done: false }],
    result: status === "done" ? { kind: "pack", title: "Conditional probability and Bayes' theorem", pdf: "C:/x.pdf", figures: 2, chapters: [{ id: "c1", title: "Conditional probability" }, { id: "c2", title: "The law of total probability" }, { id: "c3", title: "Bayes' theorem" }],
      uncovered: [], materials_missing: [{ id: "m", title: "Week05_Teacher" }], days_missing: [] } : {} });
  const basePlanList = St.mocks.plan_list, basePlanGet = St.mocks.plan_get, baseEstimate = St.mocks.plan_estimate;
  St.mocks.plan_list = () => { const r = basePlanList(); return { ...r, plans: [mockPack("done"), mockPack("running"), ...r.plans] }; };
  St.mocks.plan_get = (id) => (id === "pk1" || id === "pk2" ? { ok: true, plan: mockPack(id === "pk1" ? "done" : "running") } : basePlanGet(id));
  St.mocks.plan_estimate = (cid, sc) => (sc && sc.kind === "pack" ? { ok: true, estimate: { main_model: "sonnet", main_input: 118400, main_output: 49500, requests: 13, chapters: 6, files: 3, lectures: 2, materials_missing: [], days_missing: [], minutes: t("about {a}–{b} minutes", { a: 14, b: 28 }) } } : baseEstimate(cid, sc));
  St.mocks.pack_doc = () => ({ ok: true, html: mockDoc });
  St.mocks.pack_pdf = (id, how) => ({ ok: true, saved: how === "save" ? "C:\\Users\\you\\Downloads\\Conditional probability and Bayes.pdf" : "" });
  St.mocks.pack_options = (cid) => ({ ok: true, course: { id: cid, code: "MATH 240" }, model: "sonnet", ai: { available: true },
    weeks: [3, 4, 5, 6].map((n) => ({ no: n, start: `2026-10-${String(n * 7 - 16).padStart(2, "0")}`, current: n === 5, past: n < 5, topic: ["Counting", "Conditional probability", "Bayes' rule", "Random variables"][n - 3],
      files: [{ id: "f" + n, name: `Week0${n}.pdf`, pages: 30 + n, ext: "pdf", ready: n < 6, downloaded: n < 6 }, ...(n === 5 ? [{ id: "g5", name: "Week05_Teacher.pdf", pages: 0, ext: "pdf", ready: false, downloaded: false }] : [])],
      days: n < 6 ? [{ day_key: "d" + n, date: `2026-10-${String(n * 7 - 14).padStart(2, "0")}`, label: `Wed ${n * 7 - 14} Oct`, kind: "Lecture", ready: n !== 5, mine: true }] : [] })) });
})();
