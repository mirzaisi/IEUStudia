/* IEUStudia — the weekly digest: what happened this week in each course, what is coming, and what to do next.
 * Built without AI; the AI you chose can add a short written summary on request. Plugs into app.js through window.Studia. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, call, esc, I, SPIN, toast, go, renderView, A } = St;
  // made when drawn: the Turkish table arrives after this file has loaded
  const CAT = () => ({ final: t("Final"), midterm: t("Midterm"), quiz: t("Quiz"), exam: t("Exam"), project: t("Project"), homework: t("Homework"), schedule: t("Schedule") });

  S.digest = { offset: 0, data: null, summary: null, running: false, busyOffset: null, text: "" };   // busyOffset: the week being written

  async function load() {
    const r = await call("digest", S.digest.offset);
    if (!r.ok) { toast(r.error, "error"); return; }
    S.digest.data = r.digest;
    S.digest.summary = r.summary && r.summary.text ? r.summary : null;
    if (S.view.name === "digest") renderView();
  }

  function courseHTML(c) {
    const cat = CAT();
    const lec = c.lectures.map((l) => `<div class="dg-lec"><span class="dg-ico">${I("video", "sm")}</span><div class="grow">
        <div><b>${esc(l.kind)} ${esc(l.label)}</b> ${l.any_done ? `<span class="pill done">${I("check")}${t("Transcribed")}</span>` : `<button class="btn ghost sm" data-act="tx-day" data-day="${esc(l.day_key)}">${I("wave", "sm")} ${t("Transcribe")}</button>`}
          ${l.any_done ? `<button class="btn ghost sm" data-act="open-day" data-day="${esc(l.day_key)}">${I("file-text", "sm")} ${t("Read")}</button>` : ""}</div>
        ${l.stressed.length ? `<ul class="dg-stars">${l.stressed.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : ""}</div></div>`).join("");
    return `<div class="card dg-course"><div class="dg-head" data-act="go" data-view="course" data-id="${esc(c.id)}" tabindex="0" role="button">
        <span class="dg-code">${esc(c.code)}</span><div class="grow"><b>${esc(c.name)}</b>${c.week ? `<div class="dim">${t("Week {n}", { n: c.week.no })} · ${esc(c.week.topic)}</div>` : ""}</div>${I("chevron-right", "sm")}</div>
      ${c.coming.length ? `<div class="dg-coming">${c.coming.map((e) => `<span class="plan-pill ${e.this_week && !e.done ? "now" : ""} ${e.done ? "done" : ""}"><b>${esc(e.name)}</b><span>${e.done ? t("done") : e.this_week ? t("this week") : t("week {n}", { n: e.week })}</span>${e.los.length ? `<span class="lo">LO${e.los.join(", LO")}</span>` : ""}</span>`).join("")}</div>` : ""}
      ${lec ? `<div class="dg-sec">${lec}</div>` : ""}
      ${c.said.length ? `<div class="dg-sec"><div class="dg-lbl">${I("alert", "sm")} ${t("The lecturer said")}</div>${c.said.map((s) => `<div class="dg-said"><span class="cat-chip cat-${esc(s.category)}">${esc(cat[s.category] || s.category)}</span><span>${esc(s.text)}${s.other_section ? ` <span class="dim sm" title="${t("Heard in another section's lecture: dates may differ for yours")}">· ${esc(s.other_section)}</span>` : ""}</span></div>`).join("")}</div>` : ""}
      ${c.new_files.length || c.announcements.length ? `<div class="dg-sec dg-misc">
        ${c.new_files.length ? `<div>${I("layers", "sm")} ${t("New files: {list}", { list: c.new_files.map((f) => esc(f.title)).join(", ") })}</div>` : ""}
        ${c.announcements.length ? `<div>${I("megaphone", "sm")} ${t("Announcements: {list}", { list: c.announcements.map((a) => esc(a.title)).join(" · ") })}</div>` : ""}</div>` : ""}
    </div>`;
  }

  function digestView() {
    const g = S.digest, d = g.data;
    const ai = (S.system || {}).ai || {};
    if (!d) return `<div class="view-inner">${St.skeleton()}</div>`;
    const todo = d.todo.map((item) => {
      const act = item.kind === "transcribe" ? `<button class="btn ghost sm" data-act="tx-day" data-day="${esc(item.day_key)}">${I("wave", "sm")} ${t("Transcribe")}</button>`
        : item.kind === "download" ? `<button class="btn ghost sm" data-act="mat-get" data-id="${esc(item.resource_id)}">${I("download", "sm")} ${t.c("button", "Download")}</button>`
          : item.kind === "prepare" ? (item.name ? `<button class="btn ghost sm" data-act="ready-open" data-cid="${esc(item.course_id)}" data-name="${esc(item.name)}">${I("list-checks", "sm")} ${t("Get ready")}</button>`
            : `<button class="btn ghost sm" data-act="plan-new" data-id="${esc(item.course_id)}">${I("youtube", "sm")} ${t("Make a plan")}</button>`) : "";
      return `<div class="dg-todo"><span class="dg-code">${esc(item.course)}</span><span class="grow">${esc(item.text)}</span>${act}</div>`;
    }).join("");
    const writing = g.running && g.busyOffset === d.week.offset;
    const sum = writing ? `<div class="md">${St.mdToHtml(g.text || "", { streaming: true })}</div><div class="dim">${SPIN()} ${t.html("{ai} is writing…", { ai: St.aiName() })}</div>`
      : g.summary ? `<div class="md">${St.mdToHtml(g.summary.text)}</div><div class="dim sm">${t.html("Written by AI ({model}) — can contain mistakes", { model: g.summary.model || "" })} · ${esc((g.summary.at || "").replace("T", " "))}
          ${g.summary.file ? ` · <button class="link" data-act="dg-file" data-path="${esc(g.summary.file)}">${t("open the Markdown file")}</button>` : ""}</div>` : "";
    const sumTitle = !ai.available ? St.aiHint() : g.running && !writing ? t("Another week's summary is being written") : t("A short written summary by {ai}", { ai: St.aiName() });
    return `<div class="view-inner digest">
      <div class="pl-head"><div><h1>${t("Weekly digest")}</h1><div class="dim">${esc(d.week.label)}</div></div>
        <div class="dg-nav"><button class="btn ghost icon sm" data-act="dg-week" data-d="-1" title="${t("Previous week")}">${I("chevron-left", "sm")}</button>
          <button class="btn ghost sm" data-act="dg-week" data-d="0" ${d.week.offset === 0 ? "disabled" : ""}>${t("This week")}</button>
          <button class="btn ghost icon sm" data-act="dg-week" data-d="1" title="${t("Next week")}">${I("chevron-right", "sm")}</button>
          ${ai.available && St.effortInline && St.isChatGPT() ? St.effortInline("dg", "digest") : ""}
          <button class="btn primary sm" data-act="dg-summary" ${ai.available && !g.running ? "" : "disabled"} title="${sumTitle}">${I("ai", "sm")} ${g.summary ? t("Summarize again") : t.html("Summarize with {ai}", { ai: St.aiName() })}</button></div></div>
      ${sum ? `<div class="card dg-summary">${sum}</div>` : ""}
      ${todo ? `<div class="section-title">${I("list-checks")} ${t("To do")}</div><div class="card dg-todos">${todo}</div>` : ""}
      <div class="section-title">${I("book")} ${t("Your courses this week")}</div>
      ${d.courses.length ? d.courses.map(courseHTML).join("") : St.emptyHTML("calendar", t("A quiet week"), t("Nothing happened in your courses this week yet."))}
    </div>`;
  }

  St.views.digest = digestView;
  const navBefore = St.navExtra;
  St.navExtra = (v) => (navBefore ? navBefore(v) : "") +
    `<button class="nav-item ${v.name === "digest" ? "active" : ""}" data-act="go" data-view="digest">${I("calendar")}<span class="label"><span class="code">${t("Weekly digest")}</span></span></button>`;
  St.crumbs.digest = () => `${I("calendar")} ${t("Weekly digest")}`;
  St.onGo.digest = () => load();
  St.onEvents.digest = (e) => {   // the summary being written may be of a week you have since left
    const g = S.digest;
    if (e.phase === "start" || e.phase === "progress") { g.running = true; g.busyOffset = e.offset; g.text = e.text || ""; }
    else if (e.phase === "done") { g.running = false; g.busyOffset = null; if (e.offset === g.offset) g.summary = e; toast(t("Weekly digest written"), "success"); }
    else if (e.phase === "error") { g.running = false; g.busyOffset = null; toast(e.message || t("The AI could not finish"), "error"); }
    if (S.view.name === "digest") renderView();
  };
  Object.assign(A, {
    "dg-week": (el) => { const d = +el.dataset.d; S.digest.offset = d === 0 ? 0 : S.digest.offset + d; S.digest.data = null; renderView(); load(); },
    "dg-summary": async () => { const r = await call("digest_summary", S.digest.offset, St.effortPick && St.isChatGPT() ? St.effortPick("dg") : ""); if (!r.ok) toast(r.error, "error"); else { S.digest.running = true; S.digest.busyOffset = S.digest.offset; S.digest.text = ""; renderView(); } },
    "dg-file": (el) => call("open_output_file", el.dataset.path),
  });

  // browser preview
  St.mocks.digest = (offset) => ({ ok: true, summary: {}, digest: { week: { start: "2026-10-19", end: "2026-10-25", offset, label: "19 Oct – 25 Oct 2026" },
    todo: [{ kind: "prepare", course: "MATH 240", course_id: "17933", name: "Quiz 2", text: "Quiz 2 is this week (LO3, LO4)" }, { kind: "transcribe", course: "EEE 242", course_id: "17653", day_key: "17653|x|lecture", text: "Transcribe the lecture of Friday 16 October" }],
    courses: [{ id: "17933", code: "MATH 240", name: "Probability for Engineers", week: { no: 5, topic: "Bayes' rule" },
      lectures: [{ day_key: "17933|x|lecture", date: "2026-10-21", label: "Wednesday 21 October", kind: "Lecture", transcribed: true, any_done: true, stressed: ["Next week, we have a quiz.", "This denominator is the total probability — be careful."] }],
      new_files: [{ id: "m1", title: "Week05_Student", downloaded: false }], announcements: [{ id: "a1", title: "Class Rescheduling Announcement" }],
      coming: [{ name: "Quiz 2", week: 5, start: "2026-10-19", end: "2026-10-25", los: [3, 4], this_week: true }, { name: "Midterm", week: 8, start: "2026-11-09", end: "2026-11-15", los: [], this_week: false }],
      said: [{ category: "schedule", text: "Our class on 28 Oct is moved to Saturday 24 Oct, 12:00, M01.", date: "2026-10-19", source: "announcement" }] }] } });
  St.mocks.digest_summary = () => { setTimeout(() => window.__lsEvent({ type: "digest", phase: "done", offset: 0, text: "**A busy week.** Quiz 2 in MATH 240 covers Bayes' rule (LO3–LO4)…", model: "Claude Haiku", at: "2026-10-21T12:00:00" }), 800); return { ok: true }; };
})();
