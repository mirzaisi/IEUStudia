/* IEUStudia — Home dashboard and course pages (Weeks · Heads-up · Overview · Lectures · Materials · Announcements).
 * Plugs into app.js through window.Studia (views, events, actions). */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, call, esc, I, SPIN, toast, go, renderView, renderSidebar, emptyHTML, skeleton, openDay, jumpTo, A } = St;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  // dates in the page's language (i18n.js): "14 Oct" · "Wed 14 Oct" · "Wed 14 Oct, 09:05" (Turkish "14 Eki Çar")
  const day = (iso) => (iso ? new Date(iso.slice(0, 10) + "T00:00:00") : null);
  const dm = (iso) => (iso ? t.date(iso.slice(0, 10), "dm") : "");
  const dmw = (iso) => (iso ? t.date(iso.slice(0, 10), "dmw") : "");
  const range = (a, b) => (a ? (b && b !== a ? `${dm(a)} – ${dm(b)}` : dm(a)) : "");
  const daysAgo = (iso) => (iso ? (Date.now() - new Date(iso).getTime()) / 864e5 : Infinity);
  const fmtSize = (n) => (n >= 1048576 ? `${t.num(n / 1048576, 1, false)} MB` : n ? `${Math.max(1, Math.round(n / 1024))} KB` : "");   // 1234.6 MB, not 1,234.6
  const when = (iso) => { const d = iso ? new Date(iso) : null; return d && !isNaN(d) ? `${t.date(d, "dmw")}, ${t.date(d, "time")}` : ""; };
  const pctOf = (w) => t("{n}%", { n: t.num(+w, (String(w).split(".")[1] || "").length) });   // as the syllabus gives it: 7.5% · %7,5
  const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { /* private mode */ } return null; };

  S.cd = {};                 // course details by id (from course_detail)
  const loadedAt = {};       // when each course's details arrived
  const loadedFor = {};      // the course's summary (S.courses) they were asked for
  S.mat = {};                // download state by material id: {state, done, total, error, open}
  S.loLang = "en";
  S.ann = new Set();         // expanded announcements
  S.alertCat = "key";        // Heads-up tab filter: "key" (all but "stressed"), a category, or "all"
  S.headsOpen = store("studia-heads") !== "0";
  S.weeksOpen = {};          // past weeks the user expanded, by course: {cid: Set(week numbers)}

  // ------------------------------------------------------------------ data
  const loading = {};
  async function loadCourse(cid, quiet) {
    if (loading[cid]) return loading[cid];
    loading[cid] = (async () => {
      const asked = summaryKey(cid);
      const r = await call("course_detail", cid);
      delete loading[cid];
      if (!r.ok) { if (!quiet) toast(r.error, "error"); return; }
      S.cd[cid] = r.course;
      loadedAt[cid] = Date.now();
      loadedFor[cid] = asked;
      if (summaryKey(cid) !== asked) reloadSoon(cid);          // the course changed while it loaded
      if (R.cid === cid && $(".modal.rd")) drawReady();         // the "get ready" checklist is open: keep it current
      const v = S.view;
      if (v.name === "course" && v.courseId === cid) renderView();
      else if (v.name === "home") redrawHome();
    })();
    return loading[cid];
  }
  const reloadTimers = {};
  function reloadSoon(cid) { clearTimeout(reloadTimers[cid]); reloadTimers[cid] = setTimeout(() => loadCourse(cid, true), 250); }
  // A course's summary without a running job's progress: when it is the same, so are its details (announcements and
  // materials come with the "content" event, which reloads them anyway)
  function summaryKey(cid) {
    const c = S.courses.find((x) => x.id === cid);
    return c ? JSON.stringify(c, (k, v) => (k === "progress" || k === "detail" ? undefined : v)) : "";
  }
  const STALE_MS = 10 * 60000;
  let homeFrame = 0;
  function redrawHome() {       // Home shows every course's details: one redraw per frame, however many arrive
    if (homeFrame) return;
    homeFrame = requestAnimationFrame(() => { homeFrame = 0; if (S.view.name === "home") renderView(); });
  }
  // each file once (a unit for two weeks, "Hafta 6 ve 7", is listed in both)
  const eachOnce = (ms) => [...new Map(ms.map((m) => [m.id, m])).values()];
  function allMaterials(d) { return d ? eachOnce([...d.general.flatMap((u) => u.materials), ...d.weeks.flatMap((w) => w.units.flatMap((u) => u.materials))]) : []; }  function findMaterial(id) {
    for (const cid in S.cd) { const m = allMaterials(S.cd[cid]).find((x) => x.id === id); if (m) return { cid, m }; }
    return null;
  }

  // ------------------------------------------------------------------ pieces
  const EXT_ICON = { pdf: "file", pptx: "presentation", ppt: "presentation", docx: "file-text", doc: "file-text" };
  function matRowHTML(m, marks) {          // marks: the words of the files filter in its title
    const st = S.mat[m.id] || {};
    const busy = st.state === "queued" || st.state === "downloading";
    let status = "";
    const mb = (b) => t.num(b / 1e6, b < 1e7 ? 1 : 0, false);           // (how far, in MB, next to its bar)
    if (busy) status = st.total ? `<span class="bar"><i style="width:${Math.round((100 * st.done) / st.total)}%"></i></span> <span class="dim mat-mb">${t("{done} of {total} MB", { done: mb(st.done), total: mb(st.total) })}</span>` : `${SPIN()} ${st.state === "queued" ? (st.offline ? t("Waiting for the internet connection") : t("Waiting")) : t("Downloading")}`;
    else if (st.state === "error") status = `<span class="err-txt" title="${esc(st.error)}">${I("alert-circle", "sm")} ${t("Failed")}</span>`;
    else if (m.downloaded) status = m.outdated ? `<span class="pill processing">${t("Updated on IEULearn")}</span>`
      : `<span class="dim">${m.pages ? (m.ext === "pptx" ? t.n(m.pages, "{n} slide", "{n} slides") : t.n(m.pages, "{n} page", "{n} pages")) : ""} ${fmtSize(m.size)}</span>`;
    else if (m.is_new) status = `<span class="pill new">${t("New")}</span>`;
    const acts = busy ? "" : m.downloaded
      ? `${m.outdated ? `<button class="btn ghost sm" data-act="mat-get" data-id="${m.id}" title="${t("Download the new version")}">${I("download", "sm")}</button>` : ""}
         <button class="btn ghost icon sm" data-act="mat-reveal" data-id="${m.id}" title="${t("Show in folder")}">${I("folder-open", "sm")}</button>`
      : `<button class="btn ghost sm" data-act="mat-get" data-id="${m.id}">${I("download", "sm")} ${t.c("button", "Download")}</button>`;
    const name = m.file_name || m.title;
    return `<div class="mat ${m.downloaded ? "have" : ""} ${m.gone ? "gone" : ""}" data-mid="${esc(m.id)}">
      <span class="mat-ico ext-${esc(m.ext)}">${I(EXT_ICON[m.ext] || "file", "sm")}</span>
      <button class="mat-name" data-act="mat-open" data-id="${esc(m.id)}" title="${m.downloaded ? t.html("{name}: open", { name }) : t.html("{name}: download and open", { name })}">${marks && marks.length ? Match.html(m.title, marks) : esc(m.title)}</button>
      <span class="mat-ext">${esc((m.ext || "").toUpperCase())}</span><span class="mat-st">${status}</span><span class="mat-acts">${acts}</span></div>`;
  }
  function updateMatRow(id) {
    const hit = findMaterial(id);
    if (!hit) return;
    $$(`.mat[data-mid="${CSS.escape(id)}"]`).forEach((el) => { el.outerHTML = matRowHTML(hit.m); });
  }
  function lectureChipHTML(dd) {
    const label = `${dmw(dd.date)} · ${esc(dd.kind_label)}${dd.parts > 1 ? ` · ${t.n(dd.parts, "{n} part", "{n} parts")}` : ""}`;
    const silent = dd.no_sound && !dd.transcribed;      // nothing to transcribe: its Transcribe queued nothing
    const state = dd.live && !dd.transcribed ? `<span class="pill processing" title="${t("It can be transcribed once Panopto has finished it.")}">${I("video")}${t("Being recorded")}</span>` : dd.transcribed ? `<span class="pill done">${I("check")}${t("Transcribed")}</span>`
      : silent ? St.silentPill() : dd.any_done ? `<span class="pill">${t("Partly")}</span>` : dd.is_new ? `<span class="pill new">${t("New")}</span>` : "";
    return `<div class="lec ${dd.transcribed ? "done" : ""}">
      <span class="lec-ico">${I("video", "sm")}</span><span class="lec-name">${label}</span><span class="lec-state">${St.oddMark(dd.anomalies)}${state}</span>
      <span class="lec-acts">${dd.any_done ? `<button class="btn ghost sm" data-act="open-day" data-day="${esc(dd.day_key)}">${I("file-text", "sm")} ${t("Read")}</button>` : ""}
      ${(dd.live ? !dd.ready : dd.transcribed || silent) ? "" : `<button class="btn ghost sm" data-act="tx-day" data-day="${esc(dd.day_key)}">${I("wave", "sm")} ${t("Transcribe")}</button>`}</span></div>`;
  }

  // ------------------------------------------------------------------ heads-up: what the lecturer said
  // category → [label, icon]; a function, so the labels are looked up when drawn (in the language loaded by then)
  const CATS = () => ({ final: [t("Final"), "cap"], midterm: [t("Midterm"), "cap"], quiz: [t("Quiz"), "target"], exam: [t("Exam"), "cap"], project: [t("Project"), "layers"],
    homework: [t("Homework"), "file-text"], schedule: [t("Schedule"), "calendar"], important: [t("Stressed"), "alert"] });
  const KEY_ORDER = ["quiz", "midterm", "final", "exam", "project", "homework", "schedule"];
  const isKey = (a) => a.category !== "important";
  const RECENT_DAYS = 14;
  function markHL(text, hl) {
    if (!hl || !(hl[1] > hl[0])) return esc(text);
    return esc(text.slice(0, hl[0])) + `<mark>${esc(text.slice(hl[0], hl[1]))}</mark>` + esc(text.slice(hl[1]));
  }
  function catChip(k, n, act) {
    const [label, icon] = CATS()[k] || [k, "alert"];
    const attrs = act ? `data-act="alert-cat" data-cat="${k}"` : "";
    return `<${act ? "button" : "span"} class="cat-chip cat-${k}" ${attrs}>${I(icon, "xs")}${esc(label)}${n != null ? ` <b>${n}</b>` : ""}</${act ? "button" : "span"}>`;
  }
  function alertHTML(a, cid, course) {
    const others = a.categories.filter((k) => k !== a.category && k !== "important").slice(0, 3);
    const lecture = a.source === "lecture", cats = CATS();
    const src = lecture
      ? `${I("video", "xs")} ${t("Lecture")} · ${esc(dmw(a.date))}${a.part ? ` · ${t("part {n}", { n: a.part })}` : ""} · ${esc(a.ts)}${a.other_section ? ` <span class="pill other-sec" title="${t("Heard in another section's lecture: dates and details may differ for your section")}">${esc(a.other_section)}</span>` : ""}`
      : `${I("megaphone", "xs")} ${t("Announcement")} · ${esc(dmw(a.date))}${a.is_new ? ` <span class="pill new">${t("New")}</span>` : ""}`;
    const target = lecture ? `data-day="${esc(a.day_key)}" data-part="${a.part}" data-t="${a.t}"` : `data-cid="${esc(cid)}" data-ann="${esc(a.announcement_id)}"`;
    return `<button class="alert-item" data-act="alert-open" ${target} title="${lecture ? t("Read it in the transcript") : t("Open the announcement")}">
      ${catChip(a.category)}
      <span class="alert-main">
        <span class="alert-text">${course ? `<b class="alert-course">${esc(course.code)}</b>` : ""}${lecture ? "" : `<b>${esc(a.title)}</b>: `}${lecture ? "“" : ""}${markHL(a.text, a.hl)}${lecture ? "”" : ""}${a.lang === "tr" ? ` <span class="tag-tr">TR</span>` : ""}</span>
        <span class="alert-meta">${src}${others.map((k) => `<span class="cat-mini cat-${k}">${esc(cats[k][0])}</span>`).join("")}</span>
      </span>
      <span class="alert-go">${I("chevron-right", "sm")}</span></button>`;
  }
  // the assessment plan the lecturer announced: which week, which learning outcomes
  const pad2 = (n) => String(n).padStart(2, "0");
  const localToday = () => { const now = new Date(); return `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`; };
  const monday = (iso) => { const d = day(iso); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return d; };
  // A quiz announced for a week is held in one of the course's classes that week (the backend lists them from the
  // lecture days): it is done once they are all over. Midterms and finals follow the exam schedule, so for them only
  // the week counts. Before that, how far away it is counts calendar weeks.
  // code is what the code compares ("" no week, "done", "today", "this-week", "next-week", "later"); rel is the words shown.
  function weekRel(d, no, kind = "quiz") {
    const w = no && d.weeks.find((x) => x.no === no);
    if (!w || !w.start) return { w, code: "", rel: "", past: false };
    const now = new Date(), today = localToday(), hm = `${pad2(now.getHours())}:${pad2(now.getMinutes())}`;
    const classes = kind === "quiz" ? w.classes || [] : [], left = classes.filter((c) => c.date > today || (c.date === today && c.end > hm));
    if (w.past || (classes.length && !left.length)) return { w, code: "done", rel: t.c("assessment", "done"), past: true };
    const next = left.length ? left[0].date : w.start > today ? w.start : today;
    const n = Math.round((monday(next) - monday(today)) / 6048e5);
    const code = next === today && left.length ? "today" : n <= 0 ? "this-week" : n === 1 ? "next-week" : "later";
    const rel = code === "today" ? t("today") : code === "this-week" ? t("this week") : code === "next-week" ? t("next week") : t("in {n} weeks", { n });
    return { w, code, rel, past: false, now: n <= 0, days: left.map((c) => c.date) };
  }
  // an assessment of the plan: the day (and time) an announcement gave for it, as the calendar shows it ("Quiz 2 is
  // on Friday, 6 November"), else its week. Done once that day is over.
  function planRel(d, e) {
    if (!e.date) return weekRel(d, e.week, e.kind);
    const today = localToday();
    const w = d.weeks.find((x) => x.start && e.date >= x.start && e.date <= (x.end || x.start)) || (e.week ? d.weeks.find((x) => x.no === e.week) : undefined);
    const at = { w, date: e.date, time: e.time || "" };
    if (e.date < today) return { ...at, code: "done", rel: t.c("assessment", "done"), past: true };
    const n = Math.round((monday(e.date) - monday(today)) / 6048e5);
    const code = e.date === today ? "today" : n <= 0 ? "this-week" : n === 1 ? "next-week" : "later";
    const rel = code === "today" ? t("today") : code === "this-week" ? t("this week") : code === "next-week" ? t("next week") : t("in {n} weeks", { n });
    return { ...at, code, rel, past: false, now: n <= 0, days: [e.date] };
  }
  St.planRel = planRel;              // (the tests)
  const dayAt = (r) => (r.date ? dmw(r.date) + (r.time ? `, ${r.time}` : "") : "");     // Fri 6 Nov, 10:00
  // when it is: the day an announcement gave, the class day(s) still ahead if the course's schedule is known, else
  // the week's dates
  const relWhen = (r) => (r.date ? dayAt(r) : r.days && r.days.length && r.days.length <= 2 ? t.list(r.days.map(dmw), "disjunction") : range(r.w.start, r.w.end));
  function loLabel(los) {
    const runs = [];
    for (const n of los) { const r = runs[runs.length - 1]; if (r && n === r[1] + 1) r[1] = n; else runs.push([n, n]); }
    return runs.map(([a, b]) => (a === b ? `LO${a}` : `LO${a}–LO${b}`)).join(", ");
  }
  function loTitle(d, los) {
    return los.map((n) => [`LO${n}: ${d.outcomes_en[n - 1] || "?"}`, d.outcomes_tr[n - 1] && d.outcomes_tr[n - 1] !== d.outcomes_en[n - 1] ? `   IEULearn LO${n}: ${d.outcomes_tr[n - 1]}` : ""].filter(Boolean).join("\n")).join("\n");
  }
  function upcoming(d) {
    return ((d && d.alerts && d.alerts.plan) || []).filter((e) => !planRel(d, e).past);
  }
  // the syllabus' exam weeks this week and next that no announcement covers (a course whose lecturer posted no
  // assessment plan): Coming up lists them too, as the calendar and the reminders do; a click opens that week
  function examWeeks(d) {
    const plan = (d && d.alerts && d.alerts.plan) || [];
    return ((d && d.weeks) || []).filter((w) => w.exam && w.start && !plan.some((e) => e.kind !== "quiz" && e.week === w.no))
      .map((w) => ({ w, name: w.exam_label || t("Exam"), r: weekRel(d, w.no, "exam") })).filter((x) => x.r.now || x.r.code === "next-week");
  }
  St.examWeeks = examWeeks;          // (the tests)
  const examPill = (c, x) => `<button class="plan-pill ${x.r.now ? "now" : ""} kind-midterm" data-act="exam-week" data-cid="${esc(c.id)}" data-no="${x.w.no}" title="${esc(range(x.w.start, x.w.end))}">
      <b>${esc(x.name)}</b><span>${t("week {n}", { n: x.w.no })}${x.r.rel ? ` · ${esc(x.r.rel)}` : ""}</span></button>`;
  const readyTip = (d, e) => t("Get ready: what to transcribe, download and read") + (e.los.length ? "\n" + loTitle(d, e.los) : "");
  function planPill(c, d, e) {
    const r = planRel(d, e);
    const when = r.date ? esc(dayAt(r)) : e.week ? t("week {n}", { n: e.week }) : "";
    return `<button class="plan-pill ${r.now ? "now" : ""} kind-${e.kind}" data-act="ready-open" data-cid="${esc(c.id)}" data-name="${esc(e.name)}" title="${esc(readyTip(d, e))}">
      <b>${esc(e.name)}</b>${when ? `<span>${when}${r.rel ? ` · ${esc(r.rel)}` : ""}</span>` : ""}${e.los.length ? `<span class="lo">${esc(loLabel(e.los))}</span>` : ""}</button>`;
  }
  function planHTML(c, d) {
    const plan = (d.alerts && d.alerts.plan) || [];
    if (!plan.length) return "";
    return `<div class="card plan"><div class="mg-head"><b>${I("cap", "sm")} ${t("Assessment plan")}</b><span class="dim">${t("as announced on IEULearn; hover an outcome for its text")}</span></div>
      ${plan.map((e) => {
        const r = planRel(d, e);
        return `<div class="plan-row ${r.past ? "past" : ""} ${r.now ? "now" : ""}">
          <span class="plan-name kind-${e.kind}">${esc(e.name)}</span>
          <span class="plan-when">${e.week || r.date ? `${e.week ? t("Week {n}", { n: e.week }) : ""}${r.date ? `${e.week ? " · " : ""}${esc(dayAt(r))}` : r.w && r.w.start ? ` · ${esc(range(r.w.start, r.w.end))}` : ""}` : `<span class="dim">${t("week not announced")}</span>`}${r.rel ? ` <span class="plan-rel">${esc(r.rel)}</span>` : ""}</span>
          <span class="plan-los">${e.los.length ? e.los.map((n) => `<span class="lo-chip" title="${esc(loTitle(d, [n]))}">LO${n}</span>`).join("") : `<span class="dim">${t("no outcomes listed")}</span>`}</span>
          <span class="plan-acts">${e.week && !r.past ? `<button class="btn sm" data-act="ready-open" data-cid="${esc(c.id)}" data-name="${esc(e.name)}">${I("list-checks", "sm")} ${t("Get ready")}</button>` : ""}
          <button class="btn ghost sm plan-src" data-act="plan-ann" data-cid="${esc(c.id)}" data-ann="${esc(e.sources[0].id)}" title="${esc(e.sources.map((s) => s.title).join("\n"))}">${I("megaphone", "xs")}<span>${esc(e.sources[0].title)}</span></button></span></div>`;
      }).join("")}</div>`;
  }
  function headsUpHTML(c, d) {
    const al = d && d.alerts;
    const key = al ? al.items.filter(isKey) : [];
    const next = upcoming(d).slice(0, 3), exams = examWeeks(d);
    const asg = St.calPills ? St.calPills(c, d) : "";          // its assignments due soon (calendar.js)
    if (!key.length && !next.length && !exams.length && !asg) return "";
    const chips = KEY_ORDER.filter((k) => ((al || {}).counts || {})[k]).map((k) => catChip(k, al.counts[k], true)).join("");
    return `<div class="card heads ${S.headsOpen ? "" : "closed"}">
      <div class="heads-head"><span class="heads-ico">${I("alert", "sm")}</span><b>${t("Heads-up from the lecturer")}</b><span class="heads-chips">${chips}</span><span class="spacer"></span>
        <button class="btn ghost sm" data-act="course-tab" data-tab="alerts">${t("See all")} ${I("chevron-right", "sm")}</button>
        <button class="btn ghost icon sm" data-act="heads-toggle" title="${S.headsOpen ? t("Collapse") : t("Expand")}">${I(S.headsOpen ? "chevron-up" : "chevron-down", "sm")}</button></div>
      ${next.length || exams.length || asg ? `<div class="plan-strip"><span class="plan-strip-label">${I("cap", "xs")} ${t("Coming up")}</span>${next.map((e) => planPill(c, d, e)).join("")}${exams.map((x) => examPill(c, x)).join("")}${asg}</div>` : ""}
      ${S.headsOpen && key.length ? `<div class="heads-list">${key.slice(0, 3).map((a) => alertHTML(a, c.id)).join("")}</div>` : ""}</div>`;
  }
  function alertsHTML(c, d) {
    const al = d.alerts || { items: [], counts: {}, total: 0 };
    if (!al.items.length && !(al.plan || []).length) {
      return emptyHTML("alert", t("Nothing flagged yet"), c.counts.done
        ? t("When a transcribed lecture or an announcement mentions a quiz, the midterm, the final, a project or homework, it shows up here.")
        : t("Transcribe lectures: whenever the lecturer mentions a quiz, the midterm, the final, a project or homework, it shows up here (announcements too)."));
    }
    const f = S.alertCat, cats = CATS();
    const keyN = al.items.filter(isKey).length;
    const chips = [["key", t("Assessments & deadlines"), keyN], ...KEY_ORDER.filter((k) => al.counts[k]).map((k) => [k, cats[k][0], al.counts[k]]),
      ...(al.counts.important ? [["important", t("Stressed in class"), al.counts.important]] : []), ["all", t("All"), al.items.length]];
    const list = al.items.filter((a) => (f === "all" ? true : f === "key" ? isKey(a) : a.category === f));
    const groups = [];
    for (const a of list) {
      const dd = (a.date || "").slice(0, 10);
      const w = d.weeks.find((x) => x.start && dd >= x.start && dd <= (x.end || x.start));
      const label = w ? `${t("Week {n}", { n: w.no })} · ${range(w.start, w.end)}` : t("Other");
      let g = groups[groups.length - 1];
      if (!g || g.label !== label) groups.push((g = { label, items: [] }));
      g.items.push(a);
    }
    return `${planHTML(c, d)}<div class="fchips">${chips.map(([k, l, n]) => `<button class="fchip ${f === k ? "on" : ""} ${cats[k] ? `cat-${k}` : ""}" data-act="alert-cat" data-cat="${k}">${esc(l)} <span>${n}</span></button>`).join("")}</div>
      <div class="note-sm">${I("info", "sm")}<span>${t("Found automatically in your transcribed lectures and the IEULearn announcements. Click one to read it in context. Lectures you haven’t transcribed aren’t included.")}</span></div>
      ${list.length ? groups.map((g) => `<div class="card alert-group"><div class="mg-head"><b>${esc(g.label)}</b><span class="dim">${t.n(g.items.length, "{n} item", "{n} items")}</span></div>${g.items.map((a) => alertHTML(a, c.id)).join("")}</div>`).join("")
        : `<div class="dim" style="padding:16px 4px">${t("Nothing in this category.")}</div>`}`;
  }

  // ------------------------------------------------------------------ sections (some lecturers publish every section's lectures)
  const manySections = (c) => (c.available_sections || []).length > 1;
  const mineOnly = (c) => manySections(c) && c.show_mode === "mine" && !!c.my_section;
  const sectionOf = (c) => c.my_section || (manySections(c) ? "" : c.section);
  function sectionChip(c) {
    if (manySections(c)) {
      return `<button class="chip-btn ${c.my_section ? "" : "ask"}" data-act="section-menu" data-id="${esc(c.id)}" title="${t("Lectures of several sections are published for this course: choose yours")}">
        ${I("users", "sm")}${c.my_section ? t.html("Section {tag}", { tag: c.my_section }) : t("Your section?")}${I("chevron-down", "xs")}</button>`;
    }
    return sectionOf(c) ? `<span>${I("users", "sm")}${t.html("Section {tag}", { tag: sectionOf(c) })}</span>` : "";
  }
  function sectionAsk(c) {
    if (!manySections(c) || c.my_section) return "";
    return `<div class="card section-ask">${I("users")}<div class="grow"><b>${t("Which section are you in?")}</b>
      <div class="dim">${t.html("This course publishes the lectures of sections {list}. IEUStudia shows and suggests your section's lectures first; the others stay one click away.", { list: c.available_sections.join(", ") })}</div></div>
      <div class="sa-btns">${c.available_sections.map((x) => `<button class="btn sm" data-act="set-section" data-id="${esc(c.id)}" data-sec="${esc(x)}">${t.html("Section {tag}", { tag: x })}</button>`).join("")}</div></div>`;
  }
  async function setSection(cid, patch) {
    const r = await call("set_course_prefs", cid, patch);
    if (!r.ok) { toast(r.error, "error"); return false; }
    St.setCourses(r.courses); renderSidebar(); renderView();
    reloadSoon(cid);                                   // the weeks' lectures depend on it
    return true;
  }
  St.setSection = (cid, sec) => setSection(cid, { my_section: sec, show_mode: "mine" });     // also the sync dialog's

  // ------------------------------------------------------------------ course page
  // what Home and the course page count as lectures: your section's class days (a 3-hour class is recorded in 3
  // parts: "26 lectures" was 7, views.py); one with no speech on Panopto can't be transcribed, so it is said apart
  // and not counted as still to do
  function lectureCounts(c) {
    const n = c.counts || {};
    if (n.days == null) return { done: n.done || 0, total: n.total || 0, silent: 0 };
    return { done: n.days_done || 0, total: Math.max(0, n.days - (n.days_no_sound || 0)), silent: n.days_no_sound || 0 };
  }
  St.lectureCounts = lectureCounts;
  function courseHero(c, d) {
    const cur = d && d.weeks.find((w) => w.current);
    const ects = d && d.syllabus.ects ? `<span>${I("cap", "sm")}${t.html("{n} ECTS", { n: d.syllabus.ects })}</span>` : "";
    const ai = S.system.ai || {};
    const k = lectureCounts(c);
    return `<div class="card hero course-hero" style="--h:${hue(c)}"><div class="badge">${esc(c.code).replace(" ", "<br>")}</div><div class="grow">
      <h1>${esc(c.display_name)}</h1>
      <div class="meta">${c.instructor ? `<span>${I("user", "sm")}${esc(c.instructor)}</span>` : ""}${c.term ? `<span>${I("calendar", "sm")}${esc(c.term)}</span>` : ""}
        ${sectionChip(c)}${ects}</div>
      <div class="stats">${cur ? `<button class="stat now" data-act="goto-week" data-no="${cur.no}" title="${t("Show this week")}">${I("sparkle", "xs")}<b>${t("Week {n}", { n: cur.no })}</b>${cur.exam ? t("exam week") : t("this week")}</button>` : ""}
        <div class="stat"><b>${d ? d.materials.total : "…"}</b>${t("materials")}${d && d.materials.downloaded ? ` · ${t("{n} saved", { n: d.materials.downloaded })}` : ""}</div>
        ${k.total || k.silent ? `<div class="stat">${t.n(k.total, "<b>{done}</b>of {n} lecture transcribed", "<b>{done}</b>of {n} lectures transcribed", { done: k.done })}</div>`
          : `<div class="stat dim">${t("No lectures recorded yet")}</div>`}
        ${k.silent ? `<div class="stat dim" title="${esc(St.silentTip(k.silent))}">${t.n(k.silent, "<b>{n}</b>lecture has no speech on Panopto", "<b>{n}</b>lectures have no speech on Panopto")}</div>` : ""}</div></div>
      <div class="hero-acts"><button class="btn grad sm" data-act="ask-course" data-id="${c.id}" ${St.aiBtn(ai.available && (c.counts.done_any ?? c.counts.done))} title="${ai.available ? ((c.counts.done_any ?? c.counts.done) ? t.html("Chat with {name} about this course", { name: St.aiName() }) : t("Transcribe a lecture first")) : St.aiHint()}">${I("chat", "sm")} ${t.html("Ask {name}", { name: St.aiName() })}</button>
        <button class="btn sm" data-act="course-sync" data-id="${c.id}" title="${t("Sync, then transcribe this course's lectures that aren't transcribed yet (your section) and download its materials")}">${I("zap", "sm")} ${t("Sync course")}</button>
        <button class="btn sm" data-act="plan-new" data-id="${c.id}" ${St.aiBtn(ai.available && ai.can_plans !== false, true)} title="${ai.available ? t("Find YouTube videos that teach this course's weeks, checked through their captions") : St.aiHint()}">${I("youtube", "sm")} ${t("Video plan")}</button>
        <button class="btn sm" data-act="cx-open" data-id="${c.id}" title="${t("A zip of this course for ChatGPT: the transcripts, the screens with what was said, the course files and the announcements of the weeks you pick, in a folder for each week")}">${I("archive", "sm")} ${t("Export for AI chat")}</button>
        ${St.exportBtn ? St.exportBtn("course", c.id, false, !k.done) : ""}
        <button class="btn sm" data-act="open-folder" data-id="${c.id}" title="${t("Open this course's folder")}">${I("folder-open", "sm")} ${t("Folder")}</button></div></div>`;
  }
  function tabsHTML(c, d, tab) {
    const cn = c.content || {};
    const heads = d && d.alerts ? d.alerts.items.filter((a) => isKey(a) && daysAgo(a.date) <= RECENT_DAYS).length : 0;
    const tabs = [["weeks", t("Weeks"), "calendar", 0], ["alerts", t("Heads-up"), "alert", heads],
      ...(St.practice ? [["practice", t("Practice"), "list-checks", St.practice.tabCount(c.id)]] : []),       // practice.js
      ["overview", t("Overview"), "target", 0], ["lectures", t("Lectures"), "video", c.counts.new],
      ["materials", t("Materials"), "layers", cn.materials_new], ["announcements", t("Announcements"), "megaphone", cn.announcements_new]];
    const tips = { alerts: t("Quizzes, exams, homework and schedule changes your lecturer announced or mentioned in class") };
    return `<div class="tabs" role="tablist">${tabs.map(([k, l, ic, n]) => `<button role="tab" class="tab ${tab === k ? "on" : ""}" data-act="course-tab" data-tab="${k}"${tips[k] ? ` title="${esc(tips[k])}"` : ""}>${I(ic, "sm")}${l}${n ? `<span class="count ${k === "alerts" ? "warn" : "new"}" ${k === "alerts" ? `title="${t("In the last {n} days", { n: RECENT_DAYS })}"` : ""}>${n}</span>` : ""}</button>`).join("")}</div>`;
  }
  function coursePage(v) {
    const c = St.findCourse(v.courseId);
    if (!c) return `<div class="view-inner">${emptyHTML("book", t("Course not found"), "")}</div>`;
    const d = S.cd[c.id];
    const tab = v.tab || "weeks";
    let body;
    if (tab === "lectures") body = St.courseHTML(c, { bare: true });
    else if (tab === "practice" && St.practice) body = St.practice.tab(c, d);      // quizzes, flashcards, stressed
    else if (!d) body = skeleton();
    else if (tab === "alerts") body = alertsHTML(c, d);
    else if (tab === "overview") body = overviewHTML(c, d);
    else if (tab === "materials") body = materialsHTML(c, d);
    else if (tab === "announcements") body = announcementsHTML(c, d);
    else body = weeksHTML(c, d);
    const gone = c.inactive ? `<div class="callout info">${I("info")}<div>${c.past ? t("This course is from an earlier term: IEULearn lists it under Past courses. Its transcripts, files, chats and plans stay here.") : t("This course is no longer on your IEULearn dashboard (dropped, or from an earlier term). Its transcripts, files, chats and plans stay here.")}</div></div>` : "";
    const sq = d && (tab === "weeks" || tab === "overview") ? St.sq.card(c) : "";      // Solvequill, the maker's other app
    const changed = St.svcNote ? St.svcNote(tab, c.id) : "";      // its part on IEULearn or Panopto changed (services.js)
    return `<div class="view-inner">${courseHero(c, d)}${gone}${sectionAsk(c)}${tab === "alerts" ? "" : headsUpHTML(c, d)}${tabsHTML(c, d, tab)}<div class="tab-body">${changed}${body}</div>${sq}</div>`;
  }

  // the files attached to the course's announcements (views.course_detail: a unit of their own in "general")
  const ANN_UNIT = "announcements";
  const annFiles = (d) => d.general.filter((u) => u.id === ANN_UNIT).flatMap((u) => u.materials);
  const courseWide = (d) => d.general.filter((u) => u.id !== ANN_UNIT);
  function weeksHTML(c, d) {
    if (!d.weeks.length && !d.general.length) {
      return emptyHTML("calendar", d.content_at ? t("No weekly plan on IEULearn") : t("Loading the weekly plan…"),
        d.content_at ? t("This course has no unit plan on IEULearn yet.") : t("It appears after the next sync."));
    }
    let h = "";
    const gen = courseWide(d), ann = annFiles(d);
    if (gen.length) {
      h += `<div class="card week general"><div class="week-head"><div class="week-no gen">${I("layers")}</div><div class="week-title"><div class="t">${t("Course-wide")}</div>
        <div class="s">${esc(gen.map((u) => u.title).join(" · "))}</div></div></div>
        <div class="week-body one">${gen.flatMap((u) => u.materials).map(matRowHTML).join("") || `<div class="dim">${t("No files")}</div>`}</div></div>`;
    }
    if (ann.length) {
      h += `<div class="card week general ann-files"><div class="week-head"><div class="week-no gen">${I("megaphone")}</div><div class="week-title"><div class="t">${t("From announcements")}</div>
        <div class="s">${t("Files your lecturer attached to an announcement")}</div></div></div>
        <div class="week-body one">${ann.map(matRowHTML).join("")}</div></div>`;
    }
    for (const w of d.weeks) h += weekCard(c, d, w);
    return h;
  }
  function weekCard(c, d, w) {
    const mats = w.units.flatMap((u) => u.materials);
    w = { ...w, days: mineOnly(c) ? w.days.filter((x) => x.mine !== false) : w.days };
    const unitTitle = w.units.map((u) => u.title).join(" · ");
    const title = w.topic_en || unitTitle || w.topic_tr || t("Week {n}", { n: w.no });
    const sub = [range(w.start, w.end), unitTitle && unitTitle !== title ? `IEULearn: ${unitTitle}` : (w.topic_tr && w.topic_tr !== title ? w.topic_tr : "")].filter(Boolean).join(" · ");
    const due = ((d.alerts && d.alerts.plan) || []).filter((e) => e.week === w.no);
    const duePills = due.map((e) => `<span class="pill due kind-${e.kind}" title="${esc(e.los.length ? loTitle(d, e.los) : t("From “{title}”", { title: e.sources[0].title }))}">${I("cap")}${esc(e.name)}${e.date ? ` · ${esc(dayAt({ date: e.date, time: e.time }))}` : ""}${e.los.length ? ` · ${esc(loLabel(e.los))}` : ""}</span>`).join("");
    const asgPills = St.calWeekPills ? St.calWeekPills(d, w) : "";          // the week's assignments (calendar.js)
    const quiet = !w.current && !mats.length && !w.days.length && !due.length;
    const foldable = w.past && !w.current && !quiet;
    const shut = foldable && !(S.weeksOpen[c.id] && S.weeksOpen[c.id].has(w.no));
    const nm = mats.length, nd = w.days.length;
    const saved = mats.filter((m) => m.downloaded).length, tx = w.days.filter((x) => x.transcribed).length;
    const summary = [!nm ? "" : saved ? t.n(nm, "{n} file ({saved} saved)", "{n} files ({saved} saved)", { saved }) : t.n(nm, "{n} file", "{n} files"),
      !nd ? "" : tx ? t.n(nd, "{n} lecture ({done} transcribed)", "{n} lectures ({done} transcribed)", { done: tx }) : t.n(nd, "{n} lecture", "{n} lectures")].filter(Boolean).join(" · ");
    return `<div class="card week ${w.current ? "current" : ""} ${w.past ? "past" : ""} ${w.exam ? "exam" : ""} ${quiet ? "quiet" : ""} ${shut ? "shut" : ""}" id="week-${w.no}">
      <div class="week-head" ${foldable ? `data-act="week-toggle" data-no="${w.no}" tabindex="0" role="button" aria-expanded="${!shut}" title="${shut ? t("Show this week's files and lectures") : t("Hide this week's files and lectures")}"` : ""}>
        <div class="week-no">${t.html("<b>{n}</b><span>week</span>", { n: w.no })}</div>
        <div class="week-title"><div class="t">${esc(title)} ${w.current ? `<span class="pill now">${I("sparkle")}${t("This week")}</span>` : ""} ${duePills || (w.exam ? `<span class="pill processing">${I("cap")}${t("Exam")}</span>` : "")}${asgPills}</div>
          <div class="s">${esc(sub)}${shut && summary ? ` · <span class="week-sum">${esc(summary)}</span>` : ""}</div></div>
        ${foldable ? `<span class="week-fold">${I(shut ? "chevron-down" : "chevron-up", "sm")}</span>` : ""}
      </div>
      ${w.readings && !quiet && !shut ? `<div class="week-read">${I("book", "sm")}<span>${esc(w.readings)}</span></div>` : ""}
      ${quiet || shut ? "" : `<div class="week-body">
        <div class="week-col"><div class="col-title">${I("layers", "sm")} ${t("Materials")}</div>${mats.map(matRowHTML).join("") || `<div class="dim none">${t("No files for this week")}</div>`}</div>
        <div class="week-col"><div class="col-title">${I("video", "sm")} ${t("Lectures")}</div>${w.days.map(lectureChipHTML).join("") || `<div class="dim none">${w.past || w.current ? t("No recordings") : t("Not yet")}</div>`}</div>
      </div>`}</div>`;
  }

  function overviewHTML(c, d) {
    const los = S.loLang === "tr" ? d.outcomes_tr : d.outcomes_en;
    const total = d.grading.reduce((n, g) => n + (+g.weight || 0), 0) || 100;
    const info = [[t("Lecturers"), d.syllabus.lecturers], [t("Coordinator"), d.syllabus.coordinator], [t("Assistants"), d.syllabus.assistants],
      [t("Prerequisites"), d.syllabus.prerequisites], [t("Language"), d.syllabus.language], [t("Semester"), d.syllabus.semester],
      [t("ECTS"), d.syllabus.ects], [t("Textbook"), d.syllabus.textbooks], [t("Suggested readings"), d.syllabus.readings]].filter(([, v]) => v && v.trim());
    const colors = [1, 2, 3, 4, 5, 6].map((n) => `var(--gc${n})`);   // grading parts: tones from styles.css
    return `<div class="ov-grid">
      <div class="card ov-card">
        <h3>${I("target")} ${t("Learning outcomes")} <span class="count">${los.length}</span>
          ${d.outcomes_tr.length && d.outcomes_en.length ? `<span class="spacer"></span><div class="seg sm"><button class="${S.loLang === "en" ? "on" : ""}" data-act="lo-lang" data-val="en">English</button><button class="${S.loLang === "tr" ? "on" : ""}" data-act="lo-lang" data-val="tr">Türkçe</button></div>` : ""}</h3>
        ${los.length ? `<ol class="lo-list">${los.map((text, i) => `<li><span class="lo-no">LO${i + 1}</span><span>${esc(text)}</span></li>`).join("")}</ol>
          ${d.outcomes_tr.length && d.outcomes_en.length ? `<div class="note-sm">${I("info", "sm")} ${t("The English syllabus and IEULearn number some outcomes differently; when a lecturer says “LO5”, check which list they mean.")}</div>` : ""}`
        : `<div class="dim">${t("No learning outcomes found for this course.")}</div>`}
      </div>
      <div class="ov-side">
        ${d.grading.length ? `<div class="card ov-card"><h3>${I("cap")} ${t("Grading")}</h3>
          <div class="grade-bar">${d.grading.map((g, i) => `<i style="width:${(100 * g.weight) / total}%;background:${colors[i % colors.length]}" title="${esc(g.activity)} ${pctOf(g.weight)}"></i>`).join("")}</div>
          <div class="grade-list">${d.grading.map((g, i) => `<div><span class="dot" style="background:${colors[i % colors.length]}"></span><span class="g-name">${esc(g.activity)}${g.number > 1 ? ` <span class="dim">× ${g.number}</span>` : ""}</span><b>${pctOf(g.weight)}</b></div>`).join("")}</div></div>` : ""}
        ${d.description || d.objectives ? `<div class="card ov-card"><h3>${I("book")} ${t("About")}</h3>${d.objectives ? `<p>${esc(d.objectives)}</p>` : ""}${d.description && d.description !== d.objectives ? `<p class="dim">${esc(d.description)}</p>` : ""}</div>` : ""}
        ${info.length ? `<div class="card ov-card"><h3>${I("info")} ${t("Course details")}</h3><dl class="info-list">${info.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
          ${d.syllabus.url ? `<button class="btn ghost sm" data-act="url" data-url="${esc(d.syllabus.url)}">${I("external", "sm")} ${t("Full syllabus")}</button>` : ""}</div>` : ""}
      </div></div>`;
  }

  function matGroups(d) {
    const gen = courseWide(d), ann = annFiles(d);
    return [...(gen.length ? [{ label: t("Course-wide"), mats: gen.flatMap((u) => u.materials) }] : []),
      ...d.weeks.filter((w) => w.units.some((u) => u.materials.length)).map((w) => ({ label: `${t("Week {n}", { n: w.no })} · ${w.topic_en || w.units.map((u) => u.title).join(" · ")}`, sub: range(w.start, w.end), mats: w.units.flatMap((u) => u.materials) })),
      ...(ann.length ? [{ label: t("From announcements"), sub: t("Files your lecturer attached to an announcement"), mats: ann }] : [])];
  }
  St.matGroups = matGroups; St.weeksHTML = weeksHTML;       // (the tests)
  function materialsHTML(c, d) {
    const groups = matGroups(d);
    const all = eachOnce(groups.flatMap((g) => g.mats));
    const missing = all.filter((m) => !m.downloaded && !(S.mat[m.id] && ["queued", "downloading"].includes(S.mat[m.id].state)));
    const f = (S.matFilter || {})[c.id] || "";
    let h = `<div class="toolbar"><span class="dim" id="mat-count">${t.n(all.length, "{n} file on IEULearn · {local} on this PC", "{n} files on IEULearn · {local} on this PC", { local: all.filter((m) => m.downloaded).length })}</span><span class="spacer"></span>
      ${all.length > 3 ? `<label class="mat-filter">${I("search", "sm")}<input class="input sm" type="search" data-act="mat-filter" data-id="${esc(c.id)}" value="${esc(f)}" placeholder="${t("Filter files")}" aria-label="${t("Filter files by name")}" autocomplete="off" spellcheck="false"></label>` : ""}
      ${d.materials.new ? `<button class="btn ghost sm" data-act="course-seen" data-id="${c.id}" data-what="materials">${t("Mark as seen")}</button>` : ""}
      <button class="btn sm" data-act="mat-all" data-id="${c.id}" ${missing.length ? "" : "disabled"}>${I("download", "sm")} ${missing.length ? t("Download all {n}", { n: missing.length }) : t("Download all")}</button></div>`;
    if (!all.length) return h + emptyHTML("layers", t("No materials yet"), t("Slides and documents your lecturer posts on IEULearn appear here."));
    return h + `<div id="mat-list">${matListHTML(c, d)}</div>`;
  }
  // the files, or those whose name has the words typed in the filter (matched as the search does: match.js)
  function matListHTML(c, d) {
    const groups = matGroups(d), f = ((S.matFilter || {})[c.id] || "").trim();
    const name = (m) => [m.title, m.file_name].filter(Boolean).join(" · ");
    const q = f.length >= 2 ? Match.query(f, eachOnce(groups.flatMap((g) => g.mats)).map(name)) : null;
    let h = "", shown = 0;
    for (const g of groups) {
      const rows = g.mats.map((m) => [m, q ? q.match(m.title) : null, q ? q.match(name(m)) : null]).filter(([, a, b]) => !q || (a && a.every) || (b && b.every));
      if (!rows.length) continue;
      shown += rows.length;
      h += `<div class="card mat-group"><div class="mg-head"><b>${esc(g.label)}</b>${g.sub ? `<span class="dim">${esc(g.sub)}</span>` : ""}</div>${rows.map(([m, a]) => matRowHTML(m, a && a.marks)).join("")}</div>`;
    }
    if (q && !shown) {
      return `<div class="card mat-none">${I("search", "sm")} ${t.html("No file's name has “{q}”.", { q: f })}
        <button class="btn ghost sm" data-act="mat-search" data-id="${esc(c.id)}" data-q="${esc(f)}">${t("Search inside the files")} ${I("chevron-right", "sm")}</button></div>`;
    }
    return h + (q ? `<div class="mat-more"><button class="btn ghost sm" data-act="mat-search" data-id="${esc(c.id)}" data-q="${esc(f)}">${I("search", "sm")} ${t.html("Find “{q}” inside this course's files", { q: f })}</button></div>` : "");
  }
  (St.inputs = St.inputs || {})["mat-filter"] = (el) => {
    S.matFilter = { ...(S.matFilter || {}), [el.dataset.id]: el.value };
    const c = St.findCourse(el.dataset.id), d = S.cd[el.dataset.id], box = $("#mat-list");
    if (c && d && box) box.innerHTML = matListHTML(c, d);
  };

  function announcementsHTML(c, d) {
    if (!d.announcements.length) return emptyHTML("megaphone", t("No announcements"), t("Announcements your lecturer posts on IEULearn appear here."));
    const anyNew = d.announcements.some((a) => a.is_new);
    return `<div class="toolbar"><span class="spacer"></span>${anyNew ? `<button class="btn ghost sm" data-act="course-seen" data-id="${c.id}" data-what="announcements">${t("Mark as seen")}</button>` : ""}</div>` +
      d.announcements.map((a) => annCard(a, null, c.id)).join("");
  }
  // an announcement's text with its web addresses as links: the app keeps a link's address after its text, and an
  // attached file's ("Quiz1 - Solutions.pdf 142 KB (https://learn.ieu.edu.tr/…/file/0)"), so a Teams link opens
  function linkText(s, cid) {
    return String(s || "").split(/(https?:\/\/[^\s<>"]+)/).map((p, i) => {
      if (!(i % 2)) return esc(p);
      const url = p.replace(/[).,;:!?'"\]]+$/, "");
      const label = url.length > 60 ? url.replace(/^https?:\/\//, "").slice(0, 45) + "…" : url;
      // a file attached to the announcement: opened through IEUStudia (its IEULearn session), as the course's files
      const file = cid && url.match(/^https:\/\/learn\.ieu\.edu\.tr\/student\/announcements\/(\d+)\/file\/(\d+)\b/);
      const act = file ? `data-act="ann-file" data-id="${esc(`ann-${cid}-${file[1]}-${file[2]}`)}"` : `data-act="url"`;
      return `<a class="ext" role="link" tabindex="0" ${act} data-url="${esc(url)}" title="${esc(url)}">${esc(label)}</a>${esc(p.slice(url.length))}`;
    }).join("");
  }
  St.linkText = linkText;            // (the tests)
  function annCard(a, course, cid) {
    const open = S.ann.has(a.id) || a.body.length < 420;
    const flash = S.flashAnn && S.flashAnn.id === a.id && Date.now() < S.flashAnn.until;
    return `<div class="card ann ${a.is_new ? "is-new" : ""} ${flash ? "flash" : ""}" data-ann-id="${esc(a.id)}">
      <div class="ann-head">${I("megaphone")}<div class="grow"><div class="t">${esc(a.title)} ${a.is_new ? `<span class="pill new">${t("New")}</span>` : ""}</div>
        <div class="s">${course ? `<b>${esc(course.code)}</b> · ` : ""}${esc(a.author)}${a.posted_at ? ` · ${esc(when(a.posted_at))}` : ""}</div></div></div>
      <div class="ann-body ${open ? "" : "clamp"}">${linkText(a.body, cid || (course && course.id) || a.course_id)}</div>
      ${open ? "" : `<button class="btn ghost sm" data-act="ann-more" data-id="${esc(a.id)}">${t("Show all")}</button>`}</div>`;
  }

  // ------------------------------------------------------------------ home
  // a course's colour (Home cover, course page stripe and badge): clear, well-separated hues (no muddy yellows),
  // handed out in course-id order so this term's courses all differ
  const HUES = [217, 330, 158, 262, 20, 188, 290, 142];
  function hue(c) {
    const ids = S.courses.map((x) => x.id).sort();
    return HUES[Math.max(0, ids.indexOf(c.id)) % HUES.length];
  }
  function courseCard(c) {
    const d = S.cd[c.id];
    const w = d && (d.weeks.find((x) => x.current) || d.weeks.find((x) => x.start && !x.past));
    const cn = c.content || {};
    const fresh = (c.counts.new || 0) + (cn.materials_new || 0) + (cn.announcements_new || 0);
    const heads = d && d.alerts ? d.alerts.items.filter((a) => isKey(a) && daysAgo(a.date) <= RECENT_DAYS).length : 0;
    const exam = d && d.weeks.find((x) => x.exam && !x.past);
    const next = upcoming(d)[0];
    const k = lectureCounts(c), done = k.total ? Math.round((100 * k.done) / k.total) : 0;
    const barTip = t("Lectures transcribed in IEUStudia") + (k.silent ? ` · ${t.n(k.silent, "{n} more has no speech on Panopto", "{n} more have no speech on Panopto")}` : "");
    const topic = w ? (w.topic_en || w.units.map((u) => u.title).join(" · ") || w.topic_tr || "") : "";
    return `<button class="cc" data-act="go" data-view="course" data-id="${esc(c.id)}" title="${t.html("Open {code}", { code: c.code })}">
      <span class="cc-cover" style="--h:${hue(c)}${c.cover ? `;background-image:url('${esc(c.cover)}')` : ""}">
        <span class="cc-scrim"></span><span class="cc-code">${esc(c.code)}${sectionOf(c) ? ` · ${esc(sectionOf(c))}` : ""}</span>
        ${fresh ? `<span class="cc-new">${t("{n} new", { n: fresh })}</span>` : ""}
        <span class="cc-name">${esc(c.display_name)}</span></span>
      <span class="cc-body">
        <span class="cc-info">${c.instructor ? `<span title="${t("Instructor")}">${I("user", "xs")}${esc(c.instructor)}</span>` : ""}${c.term ? `<span title="${t("Term")}">${I("calendar", "xs")}${esc(c.term)}</span>` : ""}</span>
        <span class="cc-week">${w ? `<b>${w.current ? t("This week · Week {n}", { n: w.no }) : t("Next · Week {n}", { n: w.no })}</b><span>${esc(topic)}</span>` : `<span class="dim">${d ? t("No weekly plan yet") : t("Loading…")}</span>`}</span>
        <span class="cc-bars">
          ${c.has_panopto && k.total ? `<span class="cc-bar" title="${esc(barTip)}"><span>${I("wave", "xs")}${t.n(k.total, "{done}/{n} lecture transcribed", "{done}/{n} lectures transcribed", { done: k.done })}</span><i><b style="width:${done}%"></b></i></span>` : ""}
          ${c.lms_progress != null ? `<span class="cc-bar lms" title="${t("Your progress on IEULearn (İlerleme)")}"><span>${I("zap", "xs")}${t("IEULearn progress {pct}", { pct: t.pct(c.lms_progress / 100) })}</span><i><b style="width:${Math.min(100, c.lms_progress)}%"></b></i></span>` : ""}
        </span>
        <span class="cc-foot">${heads ? `<span class="cc-heads">${I("alert", "xs")}${t("{n} heads-up", { n: heads })}</span>` : ""}
          ${next ? `<span class="exam-chip" title="${esc(next.los.length ? loTitle(d, next.los) : next.sources[0].title)}">${I("cap", "xs")}${esc(next.name)}${nextWhen(d, next)}${next.los.length ? ` · ${esc(loLabel(next.los))}` : ""}</span>`
            : exam ? `<span class="exam-chip">${I("cap", "xs")}${esc(exam.exam_label || exam.topic_en || exam.topic_tr || t("Exam"))} · ${t("week {n}", { n: exam.no })}</span>` : ""}
          <span class="spacer"></span><span class="cc-go">${I("arrow-right", "sm")}</span></span>
      </span></button>`;
  }
  // the course card's next assessment: " · Fri 6 Nov" (" · today") when an announcement gave its day, else " · next
  // week" (or its week)
  function nextWhen(d, e) {
    const r = planRel(d, e);
    if (r.date) return ` · ${esc(r.code === "today" ? r.rel : dmw(r.date))}`;
    return e.week ? ` · ${esc(r.rel || t("week {n}", { n: e.week }))}` : "";
  }
  // the first days: what to do, in three steps (until the first lecture is transcribed, or "Got it")
  function howToHTML() {
    const search = (S.system.ai || {}).available
      ? t.html("Each paragraph jumps to its moment in the recording. Search all your lectures at once, or ask {name} about them.", { name: St.aiName() })
      : t("Each paragraph jumps to its moment in the recording. Search all your lectures at once.");
    const step = (n, title, text) => `<div class="howto-step"><span class="howto-n">${n}</span><div><b>${title}</b><span>${text}</span></div></div>`;
    return `<div class="card howto"><div class="howto-head"><b>${t("How IEUStudia works")}</b>
        <button class="btn ghost sm" data-act="howto-done">${t("Got it")}</button></div>
      <div class="howto-steps">
        ${step(1, t("It finds your lectures"), t("Every sync checks IEULearn and Panopto for your courses, recordings, files and announcements."))}
        ${step(2, t("You pick what to transcribe"), t("Open a course, go to Lectures and click Transcribe. It runs on this PC; nothing is transcribed until you choose it."))}
        ${step(3, t("Then read, search and study"), search)}
      </div></div>`;
  }
  function homeView() {
    const now = new Date(), hr = now.getHours();
    const first = ((S.profile || {}).name || "").split(" ")[0];
    const greet = hr < 5 ? t("Good night") : hr < 12 ? t("Good morning") : hr < 18 ? t("Good afternoon") : t("Good evening");
    const courses = S.courses.filter((c) => c.code);
    const heads = courses.flatMap((c) => (((S.cd[c.id] || {}).alerts || {}).items || []).filter((a) => isKey(a) && daysAgo(a.date) <= RECENT_DAYS).map((a) => ({ a, c })))
      .sort((x, y) => (y.a.date || "").localeCompare(x.a.date || ""));
    const newMats = courses.flatMap((c) => allMaterials(S.cd[c.id]).filter((m) => m.is_new).map((m) => ({ c, m })));
    const anns = courses.flatMap((c) => ((S.cd[c.id] || {}).announcements || []).map((a) => ({ a, c })))
      .sort((x, y) => (y.a.posted_at || "").localeCompare(x.a.posted_at || ""));
    const newAnns = anns.filter((x) => x.a.is_new);
    const once = new Set();      // a post sent to several of your courses: one card
    const latest = (newAnns.length ? newAnns : anns).filter(({ a }) => {
      const k = `${a.title}|${a.author}|${a.posted_at}`;
      return !once.has(k) && once.add(k);
    }).slice(0, 3);
    const recs = St.newHTML({ embedded: true });
    const soon = courses.flatMap((c) => {
      const d = S.cd[c.id];
      return d ? [...upcoming(d).map((e) => ({ c, d, e, r: planRel(d, e) })).filter((x) => x.r.now || x.r.code === "next-week"),
        ...examWeeks(d).map((x) => ({ c, d, e: { name: x.name, los: [] }, r: x.r, w: x.w }))] : [];
    }).sort((x, y) => (y.r.now ? 1 : 0) - (x.r.now ? 1 : 0));
    const twin = (c) => S.courses.filter((x) => x.code === c.code).length > 1;     // FENG 495 twice: which one
    const dateLine = t("{weekday}, {date}", { weekday: t.date(now, "weekday"), date: t.date(now, "dmy") });   // Tue, 27 Oct 2026 · 27 Eki 2026 Sal
    // a sync (or its files and announcements) waiting for the connection: why, and Stop waiting, on the page itself
    const wait = S.sync.running ? S.sync : S.content || {};
    const waitLabel = wait.why === "internet" ? t("IEULearn doesn't answer…") : t("Waiting for the internet…");
    let h = `<div class="view-inner home">
      <div class="home-hero"><div><h1>${greet}${first ? `, ${esc(first)}` : ""}</h1><div class="dim">${dateLine}${S.sync.running ? ` · ${SPIN()} ${esc(wait.offline ? waitLabel : S.sync.message || t("Syncing…"))}` : ""}</div>
        <div class="home-acts"><button class="btn sm" data-act="go" data-view="digest">${I("calendar", "sm")} ${t("Weekly digest")}</button>
          <button class="btn sm" data-act="plan-new">${I("youtube", "sm")} ${t("New study plan")}</button>
          <button class="btn sm" data-act="full-sync" title="${t("Sync every course, then transcribe your untranscribed lectures and download the materials (asks first)")}">${I("zap", "sm")} ${t("Full sync")}</button>
          <button class="btn sm" data-act="go" data-view="calendar" title="${t("Quizzes, exams, assignment deadlines and classes of your courses, and your own entries")}">${I("calendar-days", "sm")} ${t("Calendar")}</button>
          <button class="btn sm" data-act="cx-open" data-id="" title="${t("A zip of all your courses for ChatGPT: the transcripts, the screens with what was said, the course files and the announcements of the weeks you pick, in a folder for each course and week")}">${I("archive", "sm")} ${t("Export for AI chat")}</button></div></div>
        <img class="home-logo" src="../assets/logo.svg" alt=""></div>`;
    if (wait.offline) {
      h += `<div class="callout warning home-wait">${I("wifi-off")}<div><b>${esc(waitLabel)}</b> ${esc(wait.message || "")}
        <div style="margin-top:8px">${S.stopping ? `<button class="btn sm" disabled>${SPIN()} ${t("Stopping…")}</button>` : `<button class="btn sm" data-act="sync-stop">${I("stop", "sm")} ${t("Stop waiting")}</button>`}</div></div></div>`;
    }
    if (St.svcBanner) h += St.svcBanner();                   // a service that changed on its side, once (services.js)
    if (S.sync.error && !S.sync.running) {
      h += `<div class="callout warning">${I("alert")}<div><b>${S.sync.partial ? t("Not everything could be checked.") : t("IEUStudia couldn't check IEULearn.")}</b> ${esc(S.sync.error)}
        ${t("Your courses and transcripts on this PC still work.")} <button class="link" data-act="sync">${t("Try again")}</button></div></div>`;
    }
    if (S.resume && S.resume.count) {
      const r = S.resume, n = r.days || r.count;                         // lectures (class days), not their parts
      h += `<div class="card resume">${I("wave")}<div class="grow"><b>${t.n(n, "{n} lecture was still waiting to be transcribed when IEUStudia closed", "{n} lectures were still waiting to be transcribed when IEUStudia closed")}</b>
        <div class="dim">${esc(r.courses.join(", "))} · ${t("{time} of recordings", { time: St.fmtDur(r.audio_s) })}</div></div>
        <button class="btn ghost sm" data-act="resume-forget">${t("Forget them")}</button><button class="btn primary sm" data-act="resume-go">${I("wave", "sm")} ${t("Continue")}</button></div>`;
    }
    if (courses.length && !courses.some((c) => (c.counts || {}).done_any) && store("studia-howto") !== "seen") h += howToHTML();
    if (St.practice) h += St.practice.homeNote();        // flashcards due today (practice.js)
    const later = St.calSoon ? St.calSoon() : "";       // assignments, your entries, make-up classes (calendar.js)
    if (soon.length || later) {
      h += `<div class="section-title">${I("cap")} ${t("Coming up")} <span class="dim sm">${t("this week and next")}</span><span class="spacer"></span>
          <button class="btn ghost sm" data-act="go" data-view="calendar">${I("calendar-days", "sm")} ${t("Calendar")} ${I("chevron-right", "sm")}</button></div>
        <div class="soon">${soon.map(({ c, d, e, r, w }) => `<button class="soon-card ${r.now ? "now" : ""}" ${w ? `data-act="exam-week" data-cid="${esc(c.id)}" data-no="${w.no}" title="${esc(t("Week {n}", { n: w.no }))}"` : `data-act="ready-open" data-cid="${esc(c.id)}" data-name="${esc(e.name)}" title="${esc(readyTip(d, e))}"`}>
          <span class="soon-code">${esc(c.code)}${twin(c) && c.section ? ` · ${esc(c.section)}` : ""}</span><b>${esc(e.name)}</b><span class="soon-when">${esc(r.rel)}${r.code === "today" ? (r.time ? ` · ${esc(r.time)}` : "") : r.date || r.w ? ` · ${esc(relWhen(r))}` : ""}</span>
          ${e.los.length ? `<span class="lo">${esc(loLabel(e.los))}</span>` : ""}</button>`).join("")}${later}</div>`;
    }
    h += `<div class="section-title">${I("book")} ${t("My courses")} <span class="dim sm">${courses.length}</span></div>`;
    if (courses.length) h += `<div class="cc-grid">${courses.map(courseCard).join("")}</div>`;
    else if (S.sync.running || (!S.lastSync && !S.sync.error)) h += `<div class="cc-grid">${skeleton()}</div>`;
    else h += `${emptyHTML("book", t("No courses yet"), t("IEUStudia lists the courses on your IEULearn dashboard after a sync."))}
      <div style="text-align:center;margin:-8px 0 20px"><button class="btn sm" data-act="sync">${I("refresh", "sm")} ${t("Sync now")}</button></div>`;
    if (heads.length) {
      h += `<div class="section-title">${I("alert")} ${t("Heads-up")} <span class="dim sm">${t("from your lecturers · last {n} days", { n: RECENT_DAYS })}</span></div>
        <div class="card heads home-heads"><div class="heads-list">${heads.slice(0, 5).map(({ a, c }) => alertHTML(a, c.id, c)).join("")}</div>
        ${heads.length > 5 ? `<div class="heads-more dim">${t("{n} more on the course pages", { n: heads.length - 5 })}</div>` : ""}</div>`;
    }
    if (recs) h += `<div class="section-title">${I("sparkle")} ${t("New recordings")} <span class="dim sm">${t("nothing is transcribed until you choose it")}</span></div>${recs}`;
    if (newMats.length) {
      h += `<div class="section-title">${I("layers")} ${t("New materials")} <span class="spacer"></span><button class="btn ghost sm" data-act="course-seen" data-id="" data-what="materials">${t("Mark as seen")}</button></div>
        <div class="card mat-group">${newMats.map(({ c, m }) => `<div class="mg-course"><span class="pill new">${esc(c.code)}</span></div>${matRowHTML(m)}`).join("")}</div>`;
    }
    if (latest.length) {
      h += `<div class="section-title">${I("megaphone")} ${newAnns.length ? t("New announcements") : t("Latest announcements")}
        ${newAnns.length ? `<span class="spacer"></span><button class="btn ghost sm" data-act="course-seen" data-id="" data-what="announcements">${t("Mark as seen")}</button>` : ""}</div>` +
        latest.map(({ a, c }) => annCard(a, c)).join("");
    }
    return h + `</div>`;
  }

  // ------------------------------------------------------------------ get ready for a quiz or exam
  // A checklist built from what IEUStudia already knows, without AI: the weeks the assessment covers, your
  // section's lectures and the files of those weeks, what the lecturer said about it and stressed in class.
  const R = { cid: null, name: null };
  // the days you read, per profile (every profile's page shares one browser storage): read when first needed, as
  // the profile is known only once the app has started; the first profile keeps the key it always had
  const readKey = () => (S.profileId === "main" ? "studia-read" : `studia-read:${S.profileId}`);
  let readSet = null, readFor = "";
  const readDays = { has: (k) => reads().has(k) };
  function reads() {
    if (!readSet || readFor !== S.profileId) {
      readFor = S.profileId;
      try { const v = JSON.parse(store(readKey()) || "[]"); readSet = new Set(Array.isArray(v) ? v : []); } catch (e) { readSet = new Set(); }
    }
    return readSet;
  }
  function setRead(dayKey, on) {
    const r = reads();
    if (on) r.add(dayKey); else r.delete(dayKey);
    store(readKey(), JSON.stringify([...r].slice(-3000)));
  }
  St.after.day = (v) => { if (S.day && v.dayKey && !readDays.has(v.dayKey)) setRead(v.dayKey, true); };   // opened = read

  function coveredWeeks(d, e) {     // as the study-plan dialog: a quiz covers the weeks since the previous assessment
    const plan = ((d.alerts || {}).plan || []).filter((x) => x.week).sort((a, b) => a.week - b.week);
    const i = plan.findIndex((x) => x.name === e.name);
    const midBefore = plan.slice(0, Math.max(0, i)).filter((x) => x.kind === "midterm").pop();   // Midterm 2: since Midterm 1
    const start = e.kind === "final" ? 1 : e.kind === "midterm" ? (e.no >= 2 && midBefore ? midBefore.week + 1 : 1)
      : (i > 0 ? plan[i - 1].week : 0) + 1;
    const out = [];
    for (let n = Math.min(start, e.week); n <= e.week; n++) out.push(n);
    return out;
  }
  function readiness(c, d, e) {
    const nos = new Set(e.week ? coveredWeeks(d, e) : []);
    const weeks = d.weeks.filter((w) => nos.has(w.no));
    // your section's lectures; one with no speech on Panopto is not one to transcribe or read (said apart)
    const all = weeks.flatMap((w) => w.days.filter((x) => x.mine !== false && x.parts));
    const days = all.filter((x) => !(x.no_sound && !x.transcribed));
    const mats = eachOnce(weeks.flatMap((w) => w.units.flatMap((u) => u.materials))).filter((m) => !m.gone);
    const inWeeks = (a) => { const dd = (a.date || "").slice(0, 10); return weeks.some((w) => w.start && dd >= w.start && dd <= (w.end || w.start)); };
    const kinds = e.kind === "quiz" ? ["quiz"] : [e.kind, "exam"];
    const sources = new Set(e.sources.map((s) => s.id));
    const items = (d.alerts || {}).items || [];
    const said = items.filter((a) => (a.source === "announcement" ? sources.has(a.announcement_id)
      : inWeeks(a) && (a.categories || [a.category]).some((k) => kinds.includes(k)))).slice(0, 6);
    const stressed = items.filter((a) => a.source === "lecture" && a.category === "important" && inWeeks(a)).slice(0, 6);
    const cv = St.findCourse(c.id) || { days: [] };
    const todo = days.filter((x) => !x.transcribed).flatMap((x) => ((cv.days.find((y) => y.day_key === x.day_key) || {}).recordings || [])
      .filter((r) => r.part && r.mine !== false && r.status !== "done" && !r.no_sound && !["queued", "downloading", "transcribing", "live"].includes(r.status)).map((r) => r.id));
    const busy = days.filter((x) => ((cv.days.find((y) => y.day_key === x.day_key) || {}).recordings || []).some((r) => ["queued", "downloading", "transcribing"].includes(r.status)));
    const todoDays = new Set(days.filter((x) => ((cv.days.find((y) => y.day_key === x.day_key) || {}).recordings || []).some((r) => todo.includes(r.id))).map((x) => x.day_key)).size;
    const marks = (R.marks || []).filter((m) => weeks.some((w) => w.start && m.date >= w.start && m.date <= (w.end || w.start)));
    return { weeks, days, mats, said, stressed, todo, todoDays, marks, busy: busy.length, silent: all.length - days.length,
      transcribed: days.filter((x) => x.transcribed).length, read: days.filter((x) => x.any_done && readDays.has(x.day_key)).length,
      readable: days.filter((x) => x.any_done).length, downloaded: mats.filter((m) => m.downloaded).length,
      missing: mats.filter((m) => !m.downloaded && !(S.mat[m.id] && ["queued", "downloading"].includes(S.mat[m.id].state))).map((m) => m.id) };
  }
  function step(done, title, detail, action) {
    return `<div class="rd-step ${done ? "done" : ""}"><span class="rd-tick">${I(done ? "check" : "minus", "sm")}</span>
      <div class="grow"><b>${title}</b>${detail ? `<div class="dim">${detail}</div>` : ""}</div>${action || ""}</div>`;
  }
  // a study plan's state in words (the status itself stays a code)
  const planState = (s) => ({ done: t.c("plan", "ready"), running: t.c("plan", "running"), queued: t.c("plan", "queued"), error: t.c("plan", "error"), paused: t.c("plan", "paused") })[s] || s;
  function readyHTML() {
    const c = St.findCourse(R.cid), d = S.cd[R.cid];
    const e = d && ((d.alerts || {}).plan || []).find((x) => x.name === R.name);
    if (!c || !d || !e) return `<h2>${I("cap")} ${t("Get ready")}</h2><p class="lead">${t("This assessment is no longer in the course's plan.")}</p>
      <div class="actions"><button class="btn" data-act="modal-close">${t("Close")}</button></div>`;
    const r = readiness(c, d, e), rel = planRel(d, e);
    const plans = (R.plans || []).filter((p) => p.course_id === c.id && (p.scope || {}).focus === e.name);
    const prep = plans.find((p) => (p.scope || {}).kind === "prep"), video = plans.find((p) => (p.scope || {}).kind !== "prep");
    const left = r.todoDays;                                       // lectures (class days), not their parts
    const steps = [
      [r.days.length && r.transcribed === r.days.length, t("Lectures transcribed: {done} of {n}", { done: r.transcribed, n: r.days.length }),
        [r.busy ? t.n(r.busy, "{n} lecture transcribing now", "{n} lectures transcribing now") : r.days.length ? t("your section, in the weeks it covers") : r.silent ? "" : t("no recorded lectures in these weeks yet"),
          !r.silent ? "" : r.days.length ? t.n(r.silent, "{n} more has no speech on Panopto", "{n} more have no speech on Panopto") : t.n(r.silent, "{n} lecture has no speech on Panopto", "{n} lectures have no speech on Panopto")].filter(Boolean).join(" · "),
        r.todo.length ? `<button class="btn sm" data-act="rd-transcribe" title="${t.n(r.todo.length, "{n} recording", "{n} recordings")}">${I("wave", "sm")} ${left > 1 ? t.n(left, "Transcribe {n} lecture", "Transcribe {n} lectures") : t("Transcribe it")}</button>` : ""],
      [r.mats.length && !r.missing.length, t("Files on this PC: {done} of {n}", { done: r.downloaded, n: r.mats.length }), r.mats.length ? t("slides and PDFs of those weeks") : t("no files listed for these weeks"),
        r.missing.length ? `<button class="btn sm" data-act="rd-download">${I("download", "sm")} ${t("Download {n}", { n: r.missing.length })}</button>` : ""],
      [r.readable && r.read === r.readable, t("Lectures read: {done} of {n}", { done: r.read, n: r.readable }), t("ticked when you open a transcript, or tick them below"), ""],
      [!!(prep && prep.status === "done") || !!(video && video.status === "done"), t("A review sheet or video plan for it"),
        plans.length ? plans.map((p) => esc((p.scope || {}).kind === "prep" ? t("review sheet: {status}", { status: planState(p.status) }) : t("video plan: {status}", { status: planState(p.status) }))).join(" · ")
          : t("made by AI when you ask (ChatGPT or Claude)"),
        `<span class="rd-acts">${plans.length ? `<button class="btn sm" data-act="plan-open" data-id="${esc(plans[0].id)}">${I("arrow-right", "sm")} ${t("Open")}</button>`
          : `<button class="btn sm" data-act="plan-new" data-id="${esc(c.id)}" data-kind="prep" data-focus="${esc(e.name)}">${I("cap", "sm")} ${t("Review sheet")}</button>
             <button class="btn sm" data-act="plan-new" data-id="${esc(c.id)}" data-kind="videos" data-focus="${esc(e.name)}">${I("youtube", "sm")} ${t("Video plan")}</button>`}</span>`],
    ];
    const done = steps.filter((s) => s[0]).length;
    const read = r.days.filter((x) => x.any_done);
    const first = r.weeks.length && r.weeks[0].no, last = r.weeks.length && r.weeks[r.weeks.length - 1].no;
    return `<h2>${I("cap")} ${t.html("Get ready: {name}", { name: e.name })} <span class="dim sm">${esc(c.code)}</span></h2>
      <p class="lead">${e.week || rel.date ? `${e.week ? t("Week {n}", { n: e.week }) : ""}${rel.date ? `${e.week ? " · " : ""}${esc(dayAt(rel))}` : rel.w && rel.w.start ? ` · ${esc(range(rel.w.start, rel.w.end))}` : ""}${rel.rel ? ` · <b>${esc(rel.rel)}</b>` : ""}` : t("Week not announced")}
        ${r.weeks.length ? ` · ${r.weeks.length === 1 ? t("covers week {n}", { n: first }) : t("covers weeks {from}–{to}", { from: first, to: last })}` : ""}
        · <button class="link" data-act="plan-ann" data-cid="${esc(c.id)}" data-ann="${esc(e.sources[0].id)}">${t("as announced")}</button></p>
      ${e.los.length ? `<div class="rd-los">${e.los.map((n) => `<div><span class="lo-chip">LO${n}</span> ${esc(d.outcomes_en[n - 1] || d.outcomes_tr[n - 1] || "")}</div>`).join("")}</div>` : ""}
      <div class="rd-meter"><div class="bar"><i style="width:${Math.round((100 * done) / steps.length)}%"></i></div><span>${t("{done} of {n} ready", { done, n: steps.length })}</span></div>
      <div class="rd-steps">${steps.map((s) => step(...s)).join("")}</div>
      ${read.length ? `<div class="rd-sec"><div class="col-title">${I("file-text", "xs")} ${t("Lectures to read")}</div>${read.map((x) => `<div class="rd-day">
          <input type="checkbox" class="check" data-act="rd-read" data-day="${esc(x.day_key)}" ${readDays.has(x.day_key) ? "checked" : ""} title="${t.c("state", "Read")}">
          <button class="link" data-act="open-day" data-day="${esc(x.day_key)}">${esc(x.kind_label || t("Lecture"))} · ${esc(dmw(x.date))}</button>
          <span class="dim">${x.transcribed ? "" : t("partly transcribed")}</span></div>`).join("")}</div>` : ""}
      ${r.weeks.length ? `<div class="rd-sec"><div class="col-title">${I("list-checks", "xs")} ${t("Topics")}</div>${r.weeks.map((w) => `<div class="rd-topic"><b>${t("Week {n}", { n: w.no })}</b> ${esc(w.topic_en || w.topic_tr || w.units.map((u) => u.title).join(" · ") || "—")}</div>`).join("")}</div>` : ""}
      ${r.said.length ? `<div class="rd-sec"><div class="col-title">${I("megaphone", "xs")} ${t("What the lecturer said about it")}</div>${r.said.map((a) => alertHTML(a, c.id)).join("")}</div>` : ""}
      ${r.stressed.length ? `<div class="rd-sec"><div class="col-title">${I("star", "xs")} ${t("Stressed in class in those weeks")}</div>${r.stressed.map((a) => alertHTML(a, c.id)).join("")}</div>` : ""}
      ${r.marks.length ? `<div class="rd-sec"><div class="col-title">${I("bookmark", "xs")} ${t("Passages you marked in those weeks")}</div>${r.marks.map((m) => `<button class="alert-item" data-act="alert-open" data-day="${esc(m.day_key)}" data-part="${m.part}" data-t="${m.t}" title="${t("Read it in the transcript")}">
          <span class="alert-main"><span class="alert-text">“${esc(m.text)}”${m.note ? ` <span class="dim">· ${esc(m.note)}</span>` : ""}</span>
          <span class="alert-meta">${I("video", "xs")} ${esc(dmw(m.date))} · ${t("part {n}", { n: m.part })} · ${esc(m.ts)}</span></span><span class="alert-go">${I("chevron-right", "sm")}</span></button>`).join("")}</div>` : ""}
      ${St.sq.line(c)}
      <div class="actions"><button class="btn" data-act="modal-close">${t("Close")}</button></div>`;
  }
  function drawReady() {
    const box = $(".modal.rd");
    if (box) box.innerHTML = readyHTML();
  }
  // leaving the checklist for a transcript, an announcement or a plan closes it (the app's click handler then acts)
  document.addEventListener("click", (e) => {
    const el = e.target.closest(".modal.rd [data-act]");
    if (el && ["open-day", "plan-ann", "alert-open", "plan-open"].includes(el.dataset.act)) St.closeModal();
  }, true);
  async function openReady(cid, name) {
    R.cid = cid; R.name = name; R.plans = null; R.marks = [];
    St.modal(`<h2>${I("cap")} ${t("Get ready")}</h2>${skeleton()}`);
    $(".modal").classList.add("rd");
    if (!S.cd[cid]) await loadCourse(cid, true);
    const [pl, mk] = await Promise.all([call("plan_list", cid), call("course_marks", cid)]);
    R.plans = pl.ok ? pl.plans : [];
    R.marks = mk.ok ? mk.marks : [];
    drawReady();
  }

  // ------------------------------------------------------------------ wiring
  St.views.course = coursePage;
  St.hue = hue;                      // (the search's results: a course's colour on its code)
  St.views.home = homeView;
  St.onGo.course = (v) => {
    if (!v.tab && v.jumpWeek === undefined) { v.jumpWeek = "now"; St.after.course(v); }   // this week, not week 1
    if (!S.cd[v.courseId] || Date.now() - (loadedAt[v.courseId] || 0) > 20000) loadCourse(v.courseId);
  };
  St.onGo.home = () => S.courses.forEach((c) => { if (!S.cd[c.id]) loadCourse(c.id, true); });
  St.after.course = (v) => {
    if (v.annFocus) {          // opened from a heads-up item: scroll to that announcement once it is on screen
      const el = $(`.ann[data-ann-id="${CSS.escape(v.annFocus)}"]`);
      if (el) { v.annFocus = null; el.scrollIntoView({ block: "center", behavior: "instant" }); }
      return;
    }
    if (v.jumpWeek === "now") {                   // the week the course is in (or the next one), once it's loaded
      const d = S.cd[v.courseId];
      if (!d) return;
      const w = d.weeks.find((x) => x.current) || d.weeks.find((x) => x.start && !x.past);
      v.jumpWeek = w && !(w.no === 1 && !d.general.length) ? w.no : null;   // week 1 is at the top anyway
    }
    if (v.jumpWeek && (v.tab || "weeks") === "weeks") {
      const el = $(`#week-${v.jumpWeek}`);
      if (el) { v.jumpWeek = null; el.scrollIntoView({ block: "start", behavior: "instant" }); }
    }
  };
  St.openReady = openReady;
  // only a course that changed (or whose details are old): every lecture of a full sync that ends sends the courses
  const outdated = (cid) => !S.cd[cid] || summaryKey(cid) !== loadedFor[cid] || Date.now() - (loadedAt[cid] || 0) > STALE_MS;
  St.onEvents.courses = () => {
    const v = S.view;
    if (v.name === "course") { if (outdated(v.courseId)) reloadSoon(v.courseId); }
    else if (v.name === "home") {
      S.courses.forEach((c) => { if (outdated(c.id)) reloadSoon(c.id); });
      redrawHome();
    }
    else if (v.name === "new") renderView();
  };
  St.onEvents.content = (e) => {
    if (e.phase !== "done") return;
    const nm = e.new_materials, na = e.new_announcements;
    if (nm && na) {
      toast(t("{materials} and {announcements} on IEULearn", { materials: t.n(nm, "{n} new material", "{n} new materials"),
        announcements: t.n(na, "{n} new announcement", "{n} new announcements") }), "success");
    } else if (nm) toast(t.n(nm, "{n} new material on IEULearn", "{n} new materials on IEULearn"), "success");
    else if (na) toast(t.n(na, "{n} new announcement on IEULearn", "{n} new announcements on IEULearn"), "success");
    if ((e.errors || []).length) {       // a course page IEULearn didn't give: said in the top bar and on Home (aloud if you asked)
      const msg = `${t("Some courses' files and announcements couldn't be checked. They're checked again at the next sync.")} ${e.errors.join(" ")}`;
      if (!e.quiet) toast(msg, "warning");
      if (!S.sync.running) {
        S.sync = { ...S.sync, error: [S.sync.error, msg].filter(Boolean).join(" "), partial: true };
        St.renderTop(); redrawHome();
      }
    }
    S.courses.forEach((c) => { if (S.cd[c.id] || S.view.name === "home") reloadSoon(c.id); });
  };
  St.onEvents.material = (e) => {
    if (!e.id) return;
    const prev = S.mat[e.id] || {};
    S.mat[e.id] = { ...prev, ...e };
    const hit = findMaterial(e.id);
    if (e.state === "done" && hit) {
      hit.m.downloaded = true; hit.m.outdated = false; hit.m.is_new = false; hit.m.pages = e.pages || hit.m.pages;
      if (prev.open) openMaterial(e.id);                  // a failure (moved meanwhile, blocked) is said there
      delete S.mat[e.id];
      // its row shows it already; the course's details once its last file is in (a full sync downloads dozens)
      const ids = new Set(allMaterials(S.cd[hit.cid]).map((m) => m.id));
      if (!Object.keys(S.mat).some((id) => ids.has(id) && ["queued", "downloading"].includes(S.mat[id].state))) reloadSoon(hit.cid);
    } else if (e.state === "error" && !e.quiet) {        // quiet: the rest of a batch that stopped for the same reason
      toast(`${hit ? hit.m.title : t.c("noun", "Download")}: ${e.error || t.c("download", "failed")}`, "error");
    }
    updateMatRow(e.id);
  };

  // open a downloaded file (or show it in its folder); one deleted or moved meanwhile is said so and can be
  // downloaded again (its row gets the Download button back; the course's details are read again)
  function openMaterial(id, reveal) {
    return call("material_open", id, reveal).then((r) => {
      if (r.ok) return;
      toast(r.error, "error");
      const hit = r.missing && findMaterial(id);
      if (!hit) return;
      hit.m.downloaded = false; hit.m.outdated = false;
      updateMatRow(id); reloadSoon(hit.cid);
    });
  }
  function download(ids, open) {
    ids.forEach((id) => { S.mat[id] = { ...(S.mat[id] || {}), state: "queued", open: open || (S.mat[id] || {}).open }; updateMatRow(id); });
    return call("materials_download", ids).then((r) => { if (!r.ok) toast(r.error, "error"); });
  }
  Object.assign(A, {
    "howto-done": () => { store("studia-howto", "seen"); St.renderView(); },
    "resume-go": async () => {
      const n = (S.resume || {}).days || 0;
      const r = await call("queue_resume");
      S.resume = null; renderView();
      if (!r.ok) toast(r.error, "error");
      else if (r.queued) {
        toast(n > 1 ? t.n(n, "{n} lecture back in the queue", "{n} lectures back in the queue") : n ? t("The lecture back in the queue")
          : t.n(r.queued, "{n} recording back in the queue", "{n} recordings back in the queue"), "success");
      }
    },
    "resume-forget": async () => { await call("queue_forget"); S.resume = null; renderView(); },
    "mat-search": (el) => {          // the files filter found no name: the words inside the course's files instead
      S.search = { ...S.search, q: el.dataset.q, course: el.dataset.id, kind: "file" };
      go({ name: "library" });
      St.runSearch(el.dataset.q, true);
    },
    "course-tab": (el) => {
      const v = S.view, scrolled = $("#view").scrollTop > 0;
      go({ name: "course", courseId: v.courseId, tab: el.dataset.tab });
      // the tab's content starts below the course header: on a small screen, or when you had scrolled down to the
      // tabs, show the tabs at the top so the click visibly does something
      const tabs = $(".tabs");
      if (tabs && (scrolled || tabs.getBoundingClientRect().bottom > window.innerHeight - 160)) tabs.scrollIntoView({ block: "start", behavior: "instant" });
    },
    "exam-week": (el) => go({ name: "course", courseId: el.dataset.cid, tab: "weeks", jumpWeek: +el.dataset.no }),
    "goto-week": (el) => {
      const v = S.view;
      if ((v.tab || "weeks") === "weeks") { v.jumpWeek = +el.dataset.no; renderView(); }
      else go({ name: "course", courseId: v.courseId, tab: "weeks", jumpWeek: +el.dataset.no });
    },
    "week-toggle": (el) => {
      const cid = S.view.courseId, no = +el.dataset.no, kb = document.activeElement === el;
      const open = S.weeksOpen[cid] || (S.weeksOpen[cid] = new Set());
      if (open.has(no)) open.delete(no); else open.add(no);
      renderView();
      const head = kb && $(`#week-${no} .week-head`);                   // the keyboard stays where it was
      if (head) head.focus({ preventScroll: true });
    },
    "lo-lang": (el) => { S.loLang = el.dataset.val; renderView(); },
    "mat-get": (el) => download([el.dataset.id], false),
    "mat-open": (el) => {
      const hit = findMaterial(el.dataset.id); if (!hit) return;
      if (hit.m.downloaded) openMaterial(el.dataset.id, false);
      else download([el.dataset.id], true);
    },
    "mat-reveal": (el) => openMaterial(el.dataset.id, true),
    // an announcement's attached file: downloaded (with IEUStudia's IEULearn session) and opened as a course file;
    // one the app doesn't know yet (until the next sync) opens in the browser, as before
    "ann-file": (el) => (findMaterial(el.dataset.id) ? A["mat-open"](el) : A.url(el)),
    "mat-all": (el) => {
      const d = S.cd[el.dataset.id]; if (!d) return;
      const ids = allMaterials(d).filter((m) => !m.downloaded && !(S.mat[m.id] && ["queued", "downloading"].includes(S.mat[m.id].state))).map((m) => m.id);
      if (ids.length) download(ids, false);
      renderView();
    },
    "course-seen": async (el) => {
      const r = await call("course_mark_seen", el.dataset.id || "", el.dataset.what || "all");
      if (r.ok) { St.setCourses(r.courses); renderSidebar(); }
      (el.dataset.id ? [el.dataset.id] : Object.keys(S.cd)).forEach((cid) => reloadSoon(cid));
    },
    "ann-more": (el) => { S.ann.add(el.dataset.id); renderView(); },
    "alert-cat": (el) => {
      S.alertCat = el.dataset.cat;
      const v = S.view;
      if (v.name === "course" && v.tab === "alerts") renderView();
      else go({ name: "course", courseId: v.courseId, tab: "alerts" });
    },
    "alert-open": async (el) => {
      if (el.dataset.day) {
        const part = +el.dataset.part || null, at = +el.dataset.t || 0;
        await openDay(el.dataset.day);
        setTimeout(() => jumpTo(part, at), 60);
        return;
      }
      S.ann.add(el.dataset.ann);
      S.flashAnn = { id: el.dataset.ann, until: Date.now() + 1500 };
      go({ name: "course", courseId: el.dataset.cid, tab: "announcements", annFocus: el.dataset.ann });
    },
    "heads-toggle": () => { S.headsOpen = !S.headsOpen; store("studia-heads", S.headsOpen ? "1" : "0"); renderView(); },
    "plan-ann": (el) => A["alert-open"](el),            // an assessment's announcement ("plan-open" opens a study plan)
    "set-section": (el) => setSection(el.dataset.id, { my_section: el.dataset.sec, show_mode: "mine" }),
    "ready-open": (el) => openReady(el.dataset.cid, el.dataset.name),
    "export-calendar": async () => {
      const r = await call("export_calendar");
      if (!r.ok) { toast(r.error, "info"); return; }
      toast(t.n(r.count, "Saved {n} date to a calendar file. Double-click it to add it to Outlook or Windows Calendar, or import it in Google Calendar (Settings → Import).",
        "Saved {n} dates to a calendar file. Double-click it to add them to Outlook or Windows Calendar, or import it in Google Calendar (Settings → Import)."), "success");
    },
    "rd-read": (el) => { setRead(el.dataset.day, el.checked); drawReady(); },
    "rd-transcribe": async () => {
      const c = St.findCourse(R.cid), d = S.cd[R.cid];
      const e = d && ((d.alerts || {}).plan || []).find((x) => x.name === R.name);
      const rd = c && e ? readiness(c, d, e) : null, ids = rd ? rd.todo : [];
      if (!ids.length) return;
      const r = await call("transcribe", ids);
      if (!r.ok) { toast(r.error, "error"); return; }
      const n = rd.todoDays;
      toast(t.n(n, "{n} lecture queued ({parts}), before anything from a full sync", "{n} lectures queued ({parts}), before anything from a full sync",
        { parts: t.n(r.queued, "{n} part", "{n} parts") }), "success");
      reloadSoon(R.cid);
    },
    "rd-download": () => {
      const c = St.findCourse(R.cid), d = S.cd[R.cid];
      const e = d && ((d.alerts || {}).plan || []).find((x) => x.name === R.name);
      const ids = c && e ? readiness(c, d, e).missing : [];
      if (ids.length) download(ids, false).then(drawReady);
    },
    "section-menu": (el) => {
      const c = St.findCourse(el.dataset.id); if (!c) return;
      St.menu(el, [...c.available_sections.map((x) => ({ id: "s" + x, icon: x === c.my_section ? "check" : "users", label: t("Section {tag}", { tag: x }),
        desc: x === c.my_section ? t("your section") : "", run: () => setSection(c.id, { my_section: x, show_mode: "mine" }) })), "-",
        { id: "all", icon: "layers", label: c.show_mode === "all" ? t("Showing all sections") : t("Show all sections' lectures"),
          desc: t("other sections cover the same material"), run: () => setSection(c.id, { show_mode: c.show_mode === "all" ? "mine" : "all" }) }]);
    },
  });

  // ------------------------------------------------------------------ browser preview (mock)
  const mockWeeks = [["Sample Spaces, Events & Counting", "Örnek uzay ve olaylar", "2026-09-23"], ["Combinations & the First Probability Rules", "Olaylar ve sayma metotları", "2026-09-30"],
    ["Independence, Reliability & Bayes", "Sayma metotları, bir olayın olasılığı", "2026-10-07"], ["Random Variables, PMF & CDF", "Ekleme kuralları, koşullu olasılık", "2026-10-14"],
    ["Mean, Variance & the Named Distributions", "Bayes’ kuralı", "2026-10-21"], ["Poisson & the Continuous Half", "Rassal değişken konsepti", "2026-10-28"],
    ["The Uniform & the Normal", "Kesikli rassal değişkenler", "2026-11-04"], ["Midterm", "Ara Sınav", "2026-11-11"]];
  const today = new Date().toISOString().slice(0, 10);
  const hlOf = (text, cue) => { const i = text.indexOf(cue); return i < 0 ? [0, 0] : [i, i + cue.length]; };
  function mockAlerts(c) {
    const lect = (c.days || []).filter((dd) => dd.any_done);
    const ago = (n, hm) => { const d = new Date(); d.setDate(d.getDate() - n); return `${d.toISOString().slice(0, 10)}T${hm}`; };
    const items = [
      { category: "schedule", categories: ["schedule"], source: "announcement", title: "Class Rescheduling Announcement", text: "Our class originally scheduled for October 28 has been rescheduled to Saturday, October 24, at 12:00 in classroom M01.", cue: "rescheduled", lang: "en", announcement_id: "a1", date: ago(2, "15:53"), is_new: true },
      ...(lect.length ? [
        { category: "quiz", categories: ["quiz"], source: "lecture", text: "I think time is up. So please continue to study. Next week, we have a quiz.", cue: "quiz", lang: "en", day_key: lect[0].day_key, part: 3, t: 2609, ts: "43:29", date: `${lect[0].date}T10:58` },
        { category: "homework", categories: ["homework"], source: "lecture", text: "So, let's take a break and the other tools are homework for you.", cue: "homework", lang: "en", day_key: lect[0].day_key, part: 2, t: 1717, ts: "28:37", date: `${lect[0].date}T09:58` },
        { category: "final", categories: ["final", "midterm", "quiz"], source: "lecture", text: "…And the midterm or quizzes doesn't matter in final exam. If you see your result, accept this.", cue: "final exam", lang: "en", day_key: lect[0].day_key, part: 2, t: 931, ts: "15:31", date: `${lect[0].date}T09:44` },
        { category: "important", categories: ["important"], source: "lecture", text: "This is very important: the events must be mutually exclusive before you add the probabilities.", cue: "important", lang: "en", day_key: lect[0].day_key, part: 1, t: 610, ts: "10:10", date: `${lect[0].date}T08:38` }] : []),
      { category: "quiz", categories: ["quiz", "midterm", "exam"], source: "announcement", title: "Quiz Takvimi ve Uygulama Kuralları", text: "Quiz ve ara sınav takvimi aşağıdaki gibidir. Quizlerin kapsadığı öğrenme çıktıları (LO) tabloda belirtilmiştir.", cue: "Quiz", lang: "tr", announcement_id: "a2", date: ago(16, "10:05"), is_new: false },
    ].map((a) => ({ ...a, hl: hlOf(a.text, a.cue) })).sort((x, y) => y.date.localeCompare(x.date));
    const counts = {};
    items.forEach((a) => { counts[a.category] = (counts[a.category] || 0) + 1; });
    const src = [{ id: "a2", title: "Quiz Takvimi ve Uygulama Kuralları", date: ago(16, "10:05") }];
    const plan = [{ kind: "quiz", no: 1, name: "Quiz 1", week: 3, los: [1, 2], sources: src }, { kind: "quiz", no: 2, name: "Quiz 2", week: 5, los: [3], sources: src },
      { kind: "midterm", no: 0, name: "Midterm", week: 8, los: [], sources: src }, { kind: "quiz", no: 3, name: "Quiz 3", week: 10, los: [5, 6, 7], sources: src }];
    return { items, counts, total: items.length, plan };
  }
  St.mocks.course_detail = (cid) => {
    const c = St.findCourse(cid) || {};
    const wds = new Set((c.days || []).map((dd) => day(dd.date).getDay()));        // the weekdays it meets on
    const weeks = mockWeeks.map(([en, tr, start], i) => {
      const end = new Date(Date.parse(start + "T00:00:00Z") + 6 * 864e5).toISOString().slice(0, 10);
      const classes = [0, 1, 2, 3, 4, 5, 6].map((k) => new Date(Date.parse(start + "T00:00:00Z") + k * 864e5).toISOString().slice(0, 10))
        .filter((x) => wds.has(day(x).getDay())).map((x) => ({ date: x, end: "11:20" }));
      const mats = i === 7 ? [] : [{ id: `m${cid}${i}`, title: `Week${String(i + 1).padStart(2, "0")}_Student`, file_name: `Week${i + 1}.pdf`, ext: "pdf", kind: "pdf", size: 460000, downloaded: i < 2, outdated: false, is_new: i === 4, pages: i < 2 ? 15 : 0, text: "", gone: false }];
      if (i === 2) mats.push({ id: `p${cid}${i}`, title: "Bayes worked examples", file_name: "bayes.pptx", ext: "pptx", kind: "presentation", size: 2300000, downloaded: false, outdated: false, is_new: false, pages: 0, text: "", gone: false });
      return { no: i + 1, start, end, topic_en: en, topic_tr: tr, readings: i === 7 ? "" : `Montgomery & Runger, Sections 2.${i + 1}`, exam: i === 7,
        units: [{ id: `u${i}`, title: tr, readings: "", materials: mats }], current: start <= today && today <= end, past: end < today, classes,
        days: (c.days || []).filter((dd) => dd.date >= start && dd.date <= end).map((dd) => ({ day_key: dd.day_key, date: dd.date, label: dd.label, kind_label: dd.kind_label, parts: dd.parts, transcribed: dd.transcribed, any_done: dd.any_done, is_new: dd.is_new, mine: dd.mine, duration_s: dd.duration_s })) };
    });
    return { ok: true, course: { id: cid, code: c.code, name: c.name, display_name: c.display_name, instructor: c.instructor, term: c.term, section: c.section, has_panopto: c.has_panopto, counts: c.counts, my_section: c.my_section, cover: c.cover || "", lms_progress: c.lms_progress,
      description: "In this course some important theorems about probability are investigated.", objectives: "This course aims to introduce students the theory of probability and its applications to engineering problems.",
      outcomes_en: ["use fundamental concepts such as sample space, events and counting techniques.", "explain concepts of probability.", "use conditional probability, the total probability rule and Bayes' theorem.", "compute discrete and continuous random variables.", "investigate the advantages of joint probability distributions.", "apply discrete and continuous distributions.", "examine the relationship between two random variables.", "find mean and variance of random variables."],
      outcomes_tr: ["örnek uzay, olay ve sayma teknikleri gibi olasılığın temel kavramlarını kullanabilecektir.", "olasılık konseptini inceleyebilecektir.", "koşullu olasılık, toplam olasılık formülü ve Bayes teoremini kullanabilecektir."],
      grading: [{ activity: "Quizzes", number: 4, weight: 20 }, { activity: "Midterm", number: 1, weight: 30 }, { activity: "Final Exam", number: 1, weight: 50 }],
      syllabus: { url: "https://ce.ieu.edu.tr/en/syllabus/type/read/id/MATH+240", ects: "6", semester: "Fall", prerequisites: "MATH 154 (at least FD)", language: "English", coordinator: c.instructor, lecturers: c.instructor, assistants: "", textbooks: "Montgomery & Runger, Applied Statistics and Probability for Engineers, 7th ed.", readings: "" },
      weeks, general: [{ id: "g", title: "Formula Sheet", readings: "", materials: [{ id: `f${cid}`, title: "Formula Sheet 2026-27", file_name: "formula.pdf", ext: "pdf", kind: "pdf", size: 120000, downloaded: true, outdated: false, is_new: false, pages: 2, text: "done", gone: false }] }],
      announcements: [{ id: "a1", title: "Class Rescheduling Announcement", author: c.instructor, posted_at: "2026-10-19T15:53", body: "Dear Students,\n\nOur class originally scheduled for October 28 has been rescheduled to Saturday, October 24, at 12:00 in classroom M01.\n\nBest regards", is_new: true },
        { id: "a2", title: "Quiz Takvimi ve Uygulama Kuralları", author: c.instructor, posted_at: "2026-10-05T10:05", body: "Sevgili Öğrenciler,\n\nQuiz ve ara sınav takvimi aşağıdaki gibidir. Quizlerin kapsadığı öğrenme çıktıları (LO) tabloda belirtilmiştir.\n\nQuiz 1 — 3. hafta — LO1, LO2\nQuiz 2 — 5. hafta — LO3\nAra sınav — 8. hafta — LO1–LO4", is_new: false }],
      materials: { total: 9, downloaded: 3, new: 1 }, content_at: "2026-10-21T08:00:00", alerts: mockAlerts(c) } };
  };
  St.mocks.export_calendar = () => ({ ok: true, count: 7, path: "C:\\Users\\student\\Documents\\IEUStudia\\IEUStudia - calendar.ics" });
  St.mocks.materials_download = (ids) => {
    ids.forEach((id, k) => {
      let done = 0;
      const total = 460000;
      setTimeout(() => {
        const iv = setInterval(() => {
          done += 92000;
          if (done >= total) { clearInterval(iv); window.__lsEvent({ type: "material", id, state: "done", pages: 15, path: "x.pdf" }); return; }
          window.__lsEvent({ type: "material", id, state: "downloading", done, total });
        }, 150);
      }, k * 900);
    });
    return { ok: true, queued: ids.length };
  };
  St.mocks.material_open = () => ({ ok: true });
  St.mocks.course_mark_seen = () => ({ ok: true, courses: S.courses });
})();
