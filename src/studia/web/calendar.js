/* IEUStudia — the Calendar page: the term's quizzes, midterms and finals, IEULearn's assignment deadlines, class days
 * (with make-up and cancelled classes) and your own entries, in a month view and as a list (agenda.py builds them).
 * Each item says how sure its date is and opens where it comes from. Also the reminders' timing, the .ics file, and
 * what Home's Coming up and a course's Coming up strip add from it. Plugs into app.js through window.Studia. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, call, esc, I, SPIN, toast, go, renderView, A } = St;
  const $ = (s, el = document) => el.querySelector(s);
  const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { /* private mode */ } return null; };
  const pad = (n) => String(n).padStart(2, "0");
  const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const day = (s) => new Date(`${s}T00:00:00`);
  const add = (s, n) => { const d = day(s); d.setDate(d.getDate() + n); return iso(d); };
  const monday = (s) => { const d = day(s); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return iso(d); };
  const loc = () => (t.lang() === "tr" ? "tr" : "en-GB");
  const monthName = (ym) => new Intl.DateTimeFormat(loc(), { month: "long", year: "numeric" }).format(day(`${ym}-01`));
  const dm = (s) => t.date(s, "dm"), dmw = (s) => t.date(s, "dmw");
  const span = (a, b) => (a === b ? dm(a) : `${dm(a)} – ${dm(b)}`);

  // view: "month" | "list"; month: "YYYY-MM" shown; sel: the day picked in the month; only: one course; off: the kinds hidden
  S.cal = { data: null, loading: null, view: store("studia-cal-view") === "list" ? "list" : "month", month: "", sel: "", only: "", off: new Set(), past: false };
  const GROUPS = { exams: ["quiz", "midterm", "final", "exam"], deadlines: ["assignment"], classes: ["class", "makeup", "cancelled"], mine: ["own"] };
  const groupOf = (k) => Object.keys(GROUPS).find((g) => GROUPS[g].includes(k)) || "";

  async function load(quiet) {
    if (S.cal.loading) return S.cal.loading;
    S.cal.loading = (async () => {
      const r = await call("calendar");
      S.cal.loading = null;
      if (!r.ok) { if (!quiet) toast(r.error, "error"); return; }
      take(r);
    })();
    return S.cal.loading;
  }
  function take(r) {
    const before = St.calSoon();
    S.cal.data = r;
    if (!S.cal.month) S.cal.month = r.today.slice(0, 7);
    if (!S.cal.sel) S.cal.sel = r.today;
    if (S.view.name === "calendar") { renderView(); focusItem(); }
    else if (S.view.name === "home" && St.calSoon() !== before) renderView();     // its Coming up changed
  }

  // ------------------------------------------------------------------ what an item is, in words
  const courseOf = (it) => (it.course_id ? St.findCourse(it.course_id) : null);
  const hueOf = (it) => { const c = courseOf(it); return c && St.hue ? St.hue(c) : null; };
  const hueAttr = (it) => { const h = hueOf(it); return h == null ? "" : ` style="--h:${h}"`; };
  function kindLabel(it) {
    const lab = it.class_kind === "lab";
    return ({ quiz: t("Quiz"), midterm: t("Midterm"), final: t("Final"), exam: t("Exam"), assignment: t("Assignment"),
      class: lab ? t("Lab") : t("Lecture"), makeup: lab ? t("Make-up lab") : t("Make-up class"),
      cancelled: lab ? t("No lab") : t("No class"), own: t("Your entry"), holiday: t("Public holiday") })[it.kind] || "";
  }
  function title(it) {
    if (["class", "makeup", "cancelled", "holiday"].includes(it.kind)) return kindLabel(it);
    return it.title || kindLabel(it);
  }
  const ICON = { quiz: "target", midterm: "cap", final: "cap", exam: "cap", assignment: "file-text", class: "video", makeup: "plus", cancelled: "ban", own: "bookmark", holiday: "sun" };
  function sureLabel(it) {
    return ({ exact: t("Exact time from IEULearn"), ieulearn_day: t("Day from IEULearn: the time is on IEULearn"), day: t("Day from an announcement"),
      week: t("Week from an announcement"), syllabus: t("Week from the syllabus"), announced: t("From an announcement"),
      recorded: t("Recorded on Panopto"), schedule: t("Usual class time"), yours: t("Your entry"), holiday: t("No classes") })[it.sure] || "";
  }
  const blocksText = (it) => ((it.blocks && it.blocks.length ? it.blocks : it.time ? [[it.time, it.end_time || ""]] : []).map(([a, b]) => (b ? `${a}–${b}` : a)).join(" · "));
  // when it is, for a week's item: the class days it may be on, or the week's days
  const weekWhen = (it) => (it.days && it.days.length && it.days.length <= 2 ? t.list(it.days.map(dmw), "disjunction") : span(it.week.start, it.week.end));
  function stateLabel(it) {
    if (it.kind !== "assignment") return "";
    if (it.done) return it.state === "graded" ? t("Graded") : t("Sent");
    return "";
  }
  const isPast = (it, today, now) => {
    const last = it.date || (it.week && it.week.end) || "";
    if (!last) return false;
    if (last !== today) return last < today;
    return it.kind === "assignment" || it.kind === "own" ? !!it.time && it.time <= now : it.kind === "class" && !!it.end_time && it.end_time <= now;
  };
  const shown = (it) => it.kind === "holiday" || ((!S.cal.only || it.course_id === S.cal.only) && !S.cal.off.has(groupOf(it.kind)));
  function srcTip(it) {
    const s = it.source || {};
    return s.type === "announcement" ? t("Open the announcement") : s.type === "url" ? t("Open it on IEULearn") : s.type === "syllabus" ? t("Open the syllabus")
      : s.type === "day" ? (it.transcribed ? t("Read the transcript") : t("Open the course's lectures")) : s.type === "week" ? t("Show this week of the course")
      : s.type === "entry" ? t("Change this entry") : "";
  }
  const srcIcon = (it) => ({ announcement: "megaphone", url: "external", syllabus: "book", day: "file-text", week: "calendar", entry: "pencil" })[(it.source || {}).type] || "";

  // ------------------------------------------------------------------ the list (also the picked day under the month)
  function rowHTML(it, today, now) {
    const past = isPast(it, today, now), state = stateLabel(it);
    const when = it.date ? `<b>${esc(dmw(it.date))}</b><span>${esc(it.kind === "class" ? "" : it.time || "")}</span>`
      : `<b>${it.week.no ? esc(t("Week {n}", { n: it.week.no })) : esc(dm(it.week.start))}</b><span>${esc(span(it.week.start, it.week.end))}</span>`;
    const meta = [sureLabel(it), it.date ? "" : it.kind === "quiz" && it.days && it.days.length ? t("in class on {days}", { days: weekWhen(it) }) : "",
      it.kind === "class" ? blocksText(it) : "", it.makeup ? t("make-up class") : "", it.los && it.los.length ? `LO${it.los.join(", LO")}` : "",
      it.kind === "own" && it.note ? it.note : "", ["makeup", "cancelled"].includes(it.kind) && it.note ? `“${it.note}”` : ""].filter(Boolean);
    const plan = ["quiz", "midterm", "final"].includes(it.kind) && it.sure !== "syllabus" && !past;
    const acts = [plan ? `<button class="btn sm" data-act="ready-open" data-cid="${esc(it.course_id)}" data-name="${esc(it.title)}">${I("list-checks", "sm")} ${t("Get ready")}</button>` : "",
      it.kind === "own" ? `<button class="btn ghost icon sm" data-act="cal-del" data-id="${esc(it.entry_id)}" title="${t("Delete this entry")}">${I("x", "sm")}</button>` : "",
      srcIcon(it) ? `<button class="btn ghost icon sm" data-act="cal-open" data-id="${esc(it.id)}" title="${esc(srcTip(it))}">${I(srcIcon(it), "sm")}</button>` : ""].join("");
    return `<div class="cal-item k-${esc(it.kind)} s-${esc(it.sure)} ${past ? "past" : ""} ${it.done ? "done" : ""} ${it.date === today ? "today" : ""} ${it.course_id ? "" : "nocourse"}" data-item="${esc(it.id)}"${hueAttr(it)}>
      <div class="ci-when">${when}</div>
      <div class="ci-ico">${I(ICON[it.kind] || "calendar", "sm")}</div>
      <div class="ci-main"><div class="ci-title">${it.code ? `<span class="ci-code">${esc(it.code)}</span>` : ""}<span class="ci-name">${esc(title(it))}</span>
          ${it.kind === "assignment" ? `<span class="ci-kind">${t("due")}${it.time ? ` ${esc(it.time)}` : ""}</span>` : ""}
          ${state ? `<span class="pill done">${I("check")}${esc(state)}</span>` : ""}</div>
        ${meta.length ? `<div class="ci-meta">${meta.map(esc).join(" · ")}</div>` : ""}</div>
      <div class="ci-acts">${acts}</div></div>`;
  }
  // a day's classes: one line with each course's times (seven courses' classes would fill the list otherwise)
  function classesRowHTML(date, cls, today, now) {
    const past = date < today;
    return `<div class="cal-item k-class cal-classes ${past ? "past" : ""} ${date === today ? "today" : ""}">
      <div class="ci-when"><b>${esc(dmw(date))}</b><span></span></div><div class="ci-ico">${I("video", "sm")}</div>
      <div class="ci-main"><div class="ci-cls">${cls.map((it) => `<button class="cal-cls-chip ${isPast(it, today, now) ? "past" : ""} ${it.sure === "schedule" ? "usual" : ""}" data-act="cal-open" data-id="${esc(it.id)}" title="${esc(`${it.code} · ${kindLabel(it)} · ${blocksText(it)} · ${sureLabel(it)}${it.makeup ? " · " + t("make-up class") : ""}`)}"${hueAttr(it)}>
          <b>${esc(it.code)}</b>${it.class_kind === "lab" ? ` ${esc(t("Lab"))}` : ""} <span>${esc(blocksText(it))}</span></button>`).join("")}</div></div><div class="ci-acts"></div></div>`;
  }
  function itemsHTML(list, today, now) {      // one day's (or one week's) items, its classes on one line
    const cls = list.filter((it) => it.kind === "class");
    const rest = list.filter((it) => it.kind !== "class");
    const byDate = {};
    cls.forEach((it) => { (byDate[it.date] = byDate[it.date] || []).push(it); });
    const rows = rest.map((it) => ({ key: (it.date || it.week.start) + (it.date ? "1" : "0") + (it.time || ""), html: rowHTML(it, today, now) }))
      .concat(Object.entries(byDate).map(([d, xs]) => ({ key: `${d}1~`, html: classesRowHTML(d, xs, today, now) })));
    return rows.sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0)).map((r) => r.html).join("");
  }
  function listHTML(items, today, now) {
    const weeks = new Map();
    for (const it of items) {
      const m = monday(it.date || anchor(it));
      if (!weeks.has(m)) weeks.set(m, []);
      weeks.get(m).push(it);
    }
    const thisMon = monday(today), keys = [...weeks.keys()].sort();
    const earlier = keys.filter((k) => k < thisMon);
    let h = earlier.length ? `<div class="cal-earlier"><button class="btn ghost sm" data-act="cal-past">${I(S.cal.past ? "chevron-up" : "chevron-down", "sm")} ${S.cal.past ? t("Hide earlier weeks") : t.n(earlier.length, "Show {n} earlier week", "Show {n} earlier weeks")}</button></div>` : "";
    for (const k of keys) {
      if (k < thisMon && !S.cal.past) continue;
      const label = k === thisMon ? t("This week") : k === add(thisMon, 7) ? t("Next week") : "";
      h += `<div class="cal-week-group ${k === thisMon ? "now" : ""} ${k < thisMon ? "past" : ""}"><div class="cal-wg-head">${label ? `<b>${esc(label)}</b> · ` : ""}${esc(span(k, add(k, 6)))}</div>
        <div class="card cal-list">${itemsHTML(weeks.get(k), today, now)}</div></div>`;
    }
    return h || St.emptyHTML("calendar", t("Nothing here"), t("Nothing matches what is shown. Turn the kinds above back on."));
  }

  // ------------------------------------------------------------------ the month
  const anchor = (it) => add(it.week.start, 3);         // a week's item sits in the calendar week that has most of it
  function chipHTML(it, today, now) {
    const past = isPast(it, today, now);
    const tip = `${it.code ? it.code + " · " : ""}${title(it)}${it.time ? " · " + it.time : ""} · ${sureLabel(it)}`;
    return `<button class="cal-chip k-${esc(it.kind)} s-${esc(it.sure)} ${past ? "past" : ""} ${it.done ? "done" : ""} ${it.course_id ? "" : "nocourse"}" data-act="cal-pick" data-id="${esc(it.id)}" title="${esc(tip)}"${hueAttr(it)}>
      ${it.code ? `<span class="cc">${esc(it.code)}</span>` : ""}<span class="ct">${esc(title(it))}</span>${it.time && it.kind !== "class" ? `<span class="tm">${esc(it.time)}</span>` : ""}${it.done ? I("check", "xs") : ""}</button>`;
  }
  function monthHTML(items, today, now) {
    const ym = S.cal.month, start = monday(`${ym}-01`);
    const lastDay = iso(new Date(+ym.slice(0, 4), +ym.slice(5, 7), 0));
    const byDate = {}, byWeek = {};
    for (const it of items) {
      if (it.date) (byDate[it.date] = byDate[it.date] || []).push(it);
      else { const m = monday(anchor(it)); (byWeek[m] = byWeek[m] || []).push(it); }
    }
    const heads = [0, 1, 2, 3, 4, 5, 6].map((i) => `<div class="cal-wd">${esc(t.date(add(start, i), "weekday"))}</div>`).join("");
    let rows = "";
    for (let m = start; m <= lastDay; m = add(m, 7)) {
      const band = (byWeek[m] || []).map((it) => chipHTML(it, today, now)).join("");
      const now7 = today >= m && today <= add(m, 6);
      const cells = [0, 1, 2, 3, 4, 5, 6].map((i) => {
        const d = add(m, i), list = byDate[d] || [];
        const hol = list.find((x) => x.kind === "holiday");
        const ev = list.filter((x) => !["class", "holiday"].includes(x.kind));
        const cls = list.filter((x) => x.kind === "class");
        const more = Math.max(0, ev.length - 3);
        const label = `${t.date(d, "long")}${list.length ? " · " + t.n(list.length, "{n} item", "{n} items") : ""}`;
        return `<div class="cal-cell ${d.slice(0, 7) !== ym ? "out" : ""} ${d === today ? "today" : ""} ${d < today ? "past" : ""} ${d === S.cal.sel ? "sel" : ""} ${hol ? "holiday" : ""}"
            data-act="cal-day" data-date="${d}" role="button" tabindex="0" aria-label="${esc(label)}">
          <div class="cal-dn"><span>${+d.slice(8)}</span>${hol ? `<em>${esc(t("Holiday"))}</em>` : ""}</div>
          <div class="cal-evs">${ev.slice(0, 3).map((it) => chipHTML(it, today, now)).join("")}${more ? `<span class="cal-more">${esc(t("+{n} more", { n: more }))}</span>` : ""}</div>
          ${cls.length ? `<div class="cal-dots">${cls.map((it) => `<span class="cal-dot ${it.sure === "schedule" ? "usual" : ""}" title="${esc(`${it.code} · ${kindLabel(it)} · ${blocksText(it)}${it.makeup ? " · " + t("make-up class") : ""}`)}"${hueAttr(it)}>${esc(it.code)}</span>`).join("")}</div>` : ""}
        </div>`;
      }).join("");
      rows += `<div class="cal-row ${now7 ? "now" : ""}">${band ? `<div class="cal-band">${band}</div>` : ""}<div class="cal-days">${cells}</div></div>`;
    }
    const sel = S.cal.sel, picked = items.filter((it) => it.date === sel || (!it.date && it.week.start <= sel && sel <= it.week.end));
    return `<div class="card cal-month"><div class="cal-wds">${heads}</div>${rows}</div>
      <div class="cal-day-head">${I("calendar", "sm")} <b>${esc(t.date(sel, "long"))}</b>${sel === today ? ` <span class="pill now">${t("today")}</span>` : ""}
        <span class="spacer"></span><button class="btn ghost sm" data-act="cal-new" data-date="${esc(sel)}">${I("plus", "sm")} ${t("Add on this day")}</button></div>
      <div class="card cal-list">${picked.length ? itemsHTML(picked, today, now) : `<div class="dim cal-none">${t("Nothing on this day.")}</div>`}</div>`;
  }

  // ------------------------------------------------------------------ the page
  function legendHTML(data) {
    const chip = (g, label) => `<button class="fchip ${S.cal.off.has(g) ? "" : "on"}" data-act="cal-kind" data-g="${g}" aria-pressed="${!S.cal.off.has(g)}">${esc(label)}</button>`;
    const courses = (data.courses || []).map((c) => {
      const cv = St.findCourse(c.id), h = cv && St.hue ? St.hue(cv) : null;
      const on = S.cal.only === c.id;
      return `<button class="cal-course ${on ? "on" : ""} ${S.cal.only && !on ? "dim" : ""}" data-act="cal-only" data-id="${esc(c.id)}" title="${esc(on ? t("Show every course") : t("Show only {code}", { code: c.code }))}"${h == null ? "" : ` style="--h:${h}"`}>
        <i></i>${esc(c.code)}${c.twin && c.section ? ` · ${esc(c.section)}` : ""}</button>`;
    }).join("");
    return `<div class="cal-filters">${chip("exams", t("Quizzes and exams"))}${chip("deadlines", t("Assignments"))}${chip("classes", t("Classes"))}${chip("mine", t("Your entries"))}</div>
      ${courses ? `<div class="cal-courses">${courses}</div>` : ""}`;
  }
  function calendarView() {
    const data = S.cal.data;
    if (!data) { if (!S.cal.loading) load(); return `<div class="view-inner">${St.skeleton()}</div>`; }
    const today = data.today, now = nowHM();
    const items = data.items.filter(shown);
    const any = data.items.some((it) => it.kind !== "holiday");
    const head = `<div class="pl-head cal-head"><div><h1>${t("Calendar")}</h1><div class="dim">${t("Quizzes, exams, assignment deadlines and classes of your courses, and your own entries")}</div></div>
      <div class="cal-acts"><button class="btn sm" data-act="cal-remind" title="${t("When IEUStudia reminds you")}">${I("clock", "sm")} ${t("Reminders")}</button>
        <button class="btn primary sm" data-act="cal-new">${I("plus", "sm")} ${t("Add entry")}</button></div></div>`;
    const ics = `<button class="btn ghost sm" data-act="export-calendar" title="${t("Save the calendar as a file (.ics) for Google Calendar, Outlook or your phone")}">${I("download", "sm")} ${t("Add to my calendar app")}</button>`;
    if (!any) {
      return `<div class="view-inner cal">${head}${St.emptyHTML("calendar-days", t("Nothing dated yet"), t("Quizzes and exams your lecturers announce, IEULearn's assignment deadlines and your classes show up here after a sync. You can add your own entries too."))}</div>`;
    }
    const nav = S.cal.view === "month" ? `<div class="cal-nav"><button class="btn ghost icon sm" data-act="cal-month" data-d="-1" title="${t("Previous month")}">${I("chevron-left", "sm")}</button>
        <b class="cal-mname">${esc(monthName(S.cal.month))}</b><button class="btn ghost icon sm" data-act="cal-month" data-d="1" title="${t("Next month")}">${I("chevron-right", "sm")}</button>
        <button class="btn ghost sm" data-act="cal-today" ${S.cal.month === today.slice(0, 7) && S.cal.sel === today ? "disabled" : ""}>${t("Today")}</button></div>` : `<div class="cal-nav"></div>`;
    return `<div class="view-inner cal">${head}
      <div class="cal-bar">${nav}<div class="seg"><button class="${S.cal.view === "month" ? "on" : ""}" data-act="cal-view" data-v="month">${I("calendar-days", "sm")} ${t("Month")}</button>
        <button class="${S.cal.view === "list" ? "on" : ""}" data-act="cal-view" data-v="list">${I("list-checks", "sm")} ${t("List")}</button></div>${ics}</div>
      ${legendHTML(data)}
      ${S.cal.view === "month" ? monthHTML(items, today, now) : listHTML(items, today, now)}
      <div class="note-sm cal-note">${I("info", "sm")}<span>${t("Dashed: only the week is known (from an announcement or the syllabus). The exam schedule on IEULearn has the exact day of midterms and finals.")}</span></div></div>`;
  }
  const nowHM = () => { const d = new Date(); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
  function findItem(id) { return ((S.cal.data || {}).items || []).find((it) => it.id === id); }
  function focusItem() {                      // opened from a notification: that item, picked and in view
    const id = S.view.focus;
    if (!id || !S.cal.data) return;
    const it = findItem(id);
    S.view.focus = null;
    if (!it) return;
    const d = it.date || it.week.start;
    if (S.cal.view === "month" && (S.cal.sel !== d || S.cal.month !== d.slice(0, 7))) { S.cal.sel = d; S.cal.month = d.slice(0, 7); renderView(); }
    const el = $(`.cal-list [data-item="${CSS.escape(id)}"]`);
    if (el) { el.scrollIntoView({ block: "center", behavior: "instant" }); el.classList.add("flash"); }
  }

  // ------------------------------------------------------------------ your own entries
  function entryDialog(it, date, error) {
    const courses = ((S.cal.data || {}).courses || []);
    const v = it || { title: "", course_id: S.cal.only || "", date: date || (S.cal.data || {}).today || iso(new Date()), time: "", note: "" };
    St.modal(`<form data-form="cal-entry" class="cal-form" data-id="${esc(it ? it.entry_id : "")}">
      <h2>${it ? t("Change this entry") : t("Add to your calendar")}</h2>
      <p class="lead">${t("Kept on this PC, in your profile. IEUStudia reminds you before it, as set in Reminders.")}</p>
      ${error ? `<div class="callout error">${I("alert-circle")}<div>${esc(error)}</div></div>` : ""}
      <div class="field"><label for="ce-title">${t("Title")}</label><input class="input" id="ce-title" name="title" maxlength="120" autocomplete="off" value="${esc(v.title)}" placeholder="${t("e.g. Study group, Lab report, EEE 215 midterm")}" autofocus></div>
      <div class="field"><label for="ce-course">${t("Course")}</label><select class="input" id="ce-course" name="course_id"><option value="">${t("No course")}</option>
        ${courses.map((c) => `<option value="${esc(c.id)}" ${c.id === v.course_id ? "selected" : ""}>${esc(c.code)}${c.twin && c.section ? ` · ${esc(c.section)}` : ""} · ${esc(c.name)}</option>`).join("")}</select></div>
      <div class="cal-form-row"><div class="field"><label for="ce-date">${t("Day")}</label><input class="input" type="date" id="ce-date" name="date" value="${esc(v.date)}" required></div>
        <div class="field"><label for="ce-time">${t("Time (optional)")}</label><input class="input" type="time" id="ce-time" name="time" value="${esc(v.time || "")}"></div></div>
      <div class="field"><label for="ce-note">${t("Note (optional)")}</label><textarea class="input" id="ce-note" name="note" rows="2" maxlength="500">${esc(v.note || "")}</textarea></div>
      <div class="actions">${it ? `<button type="button" class="btn ghost danger" data-act="cal-del" data-id="${esc(it.entry_id)}">${I("x", "sm")} ${t("Delete")}</button><span class="spacer"></span>` : ""}
        <button type="button" class="btn" data-act="modal-close">${t("Cancel")}</button><button type="submit" class="btn primary">${it ? t("Save") : t("Add")}</button></div></form>`);
  }
  const field = (f, name) => ((f.elements && f.elements.namedItem(name)) || {});
  async function saveEntry(f) {
    const entry = { id: f.dataset.id || "" };
    ["title", "course_id", "date", "time", "note"].forEach((k) => { entry[k] = field(f, k).value || ""; });
    const btn = f.querySelector("button[type=submit]");
    if (btn) { btn.disabled = true; btn.innerHTML = `${SPIN()} ${t("Saving…")}`; }
    const r = await call("calendar_save", entry);
    if (!r.ok) { entryDialog(entry.id ? { ...entry, entry_id: entry.id } : null, entry.date, r.error); if (!entry.id) fill(entry); return; }
    St.closeModal();
    S.cal.sel = r.entry.date; S.cal.month = r.entry.date.slice(0, 7);
    take(r);
    toast(entry.id ? t("Entry changed") : t("Added to your calendar"), "success");
  }
  function fill(entry) {              // a refused new entry keeps what was typed
    const f = $("form[data-form='cal-entry']");
    if (f) ["title", "course_id", "date", "time", "note"].forEach((k) => { const el = field(f, k); if ("value" in el) el.value = entry[k] || ""; });
  }
  document.addEventListener("submit", (e) => {
    const f = e.target;
    if (!f || !f.dataset || f.dataset.form !== "cal-entry") return;
    e.preventDefault();
    saveEntry(f);
  });

  // ------------------------------------------------------------------ reminders
  function remindDialog() {
    const r = (S.cal.data || {}).reminders || { exams: 2, due_days: 1, due_hours: 3 };
    const on = ((S.boot || {}).app || {}).notify_reminders !== false;
    const sel = (key, opts) => `<select class="input sm" data-act="cal-rem" data-key="${key}">${opts.map(([v, l]) => `<option value="${v}" ${+r[key] === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
    const row = (label, d, ctl) => `<div class="set-row"><div class="txt"><div class="t">${label}</div><div class="d">${d}</div></div><div class="cal-rem-ctl">${ctl}</div></div>`;
    St.modal(`<h2>${I("clock")} ${t("Reminders")}</h2>
      <p class="lead">${t("IEUStudia tells you before these with a Windows notification, or in its window while you use it. Each reminder comes once.")}</p>
      ${on ? "" : `<div class="callout warning">${I("alert")}<div>${t("Reminders are turned off in Settings, so none come now.")} <button class="link" data-act="cal-rem-on">${t("Turn them on")}</button></div></div>`}
      ${row(t("Quizzes and exams"), t("Announced ones, and the exam weeks of the syllabus."), sel("exams", [[0, t("Off")], [1, t("The evening before")], [2, t("2 days before")], [3, t("3 days before")], [7, t("A week before")]]))}
      ${row(t("Assignments and your entries"), t("A first reminder some days before, and a second one hours before."),
        sel("due_days", [[0, t("Off")], [1, t("1 day before")], [2, t("2 days before")], [3, t("3 days before")]]) + sel("due_hours", [[0, t("Off")], [1, t("1 hour before")], [3, t("3 hours before")], [6, t("6 hours before")]]))}
      <div class="dim sm">${t("An entry whose title names a quiz or an exam is reminded like one.")}</div>
      <div class="actions"><button class="btn primary" data-act="modal-close">${t("Done")}</button></div>`);
  }

  // ------------------------------------------------------------------ Home and the course page take their part of it
  // Home's Coming up: what the announcements' plan doesn't hold (course.js shows that): assignments still to send,
  // your own entries, make-up and cancelled classes, from today to the end of next week
  St.calSoon = () => {
    const data = S.cal.data;
    if (!data) return "";
    const today = data.today, last = add(monday(today), 13), now = nowHM();
    const soon = data.items.filter((it) => ["assignment", "own", "makeup", "cancelled"].includes(it.kind) && it.date && it.date >= today && it.date <= last
      && !it.done && !isPast(it, today, now) && (!it.course_id || St.findCourse(it.course_id)));
    return soon.slice(0, 8).map((it) => {
      const rel = it.date === today ? t("today") : it.date === add(today, 1) ? t("tomorrow") : dmw(it.date);
      return `<button class="soon-card cal-soon k-${esc(it.kind)} ${it.date <= add(today, 1) ? "now" : ""}" data-act="cal-goto" data-id="${esc(it.id)}" title="${esc(sureLabel(it))}"${hueAttr(it)}>
        <span class="soon-code">${esc(it.code || t("Your entry"))}</span><b>${esc(title(it))}</b>
        <span class="soon-when">${esc(kindLabel(it))} · ${esc(rel)}${it.time ? ` · ${esc(it.time)}` : ""}</span></button>`;
    }).join("");
  };
  // a course's Coming up strip and its week cards: its assignments (course_detail has them)
  const open = (d) => ((d && d.assignments) || []).filter((a) => !a.done && a.due);
  St.calPills = (c, d) => {
    const today = iso(new Date()), last = add(monday(today), 13);
    return open(d).filter((a) => a.due >= today && a.due <= last).map((a) => `<button class="plan-pill kind-assignment ${a.due <= add(today, 1) ? "now" : ""}" data-act="url" data-url="${esc(a.url)}" title="${t("Open it on IEULearn")}">
      <b>${esc(a.title)}</b><span>${esc(t("due {when}", { when: a.due === today ? t("today") : dmw(a.due) }))}${a.time ? ` · ${esc(a.time)}` : ""}</span></button>`).join("");
  };
  St.calWeekPills = (d, w) => ((d && d.assignments) || []).filter((a) => a.week === w.no && a.due)
    .map((a) => `<span class="pill due kind-assignment ${a.done ? "sent" : ""}" title="${esc(a.done ? t("Sent on IEULearn") : t("Due {when}", { when: dmw(a.due) + (a.time ? ` ${a.time}` : "") }))}">${I(a.done ? "check" : "file-text")}${esc(a.title)}</span>`).join("");

  // ------------------------------------------------------------------ wiring
  St.views.calendar = calendarView;
  St.crumbs.calendar = () => `${I("calendar-days")} ${t("Calendar")}`;
  St.onGo.calendar = (v) => { if (v.focus && S.cal.data) focusItem(); load(!!S.cal.data); };
  const navBefore = St.navExtra;
  St.navExtra = (v) => (navBefore ? navBefore(v) : "") +
    `<button class="nav-item ${v.name === "calendar" ? "active" : ""}" data-act="go" data-view="calendar">${I("calendar-days")}<span class="label"><span class="code">${t("Calendar")}</span></span></button>`;
  const homeBefore = St.onGo.home;
  St.onGo.home = (v) => { if (homeBefore) homeBefore(v); load(true); };
  const contentBefore = St.onEvents.content;
  let again = 0;
  St.onEvents.content = (e) => {        // a sync's files and announcements are in: the deadlines and changes may be new
    if (contentBefore) contentBefore(e);
    if (e.phase !== "done") return;
    clearTimeout(again);
    again = setTimeout(() => { if (["calendar", "home"].includes(S.view.name)) load(true); }, 400);
  };
  Object.assign(A, {
    "cal-view": (el) => { S.cal.view = el.dataset.v; store("studia-cal-view", S.cal.view); renderView(); },
    "cal-month": (el) => {           // another month: its first day picked (today, in this month)
      const [y, m] = S.cal.month.split("-").map(Number), d = new Date(y, m - 1 + +el.dataset.d, 1);
      S.cal.month = iso(d).slice(0, 7);
      const today = S.cal.data.today;
      S.cal.sel = today.slice(0, 7) === S.cal.month ? today : iso(d);
      renderView();
    },
    "cal-today": () => { S.cal.month = S.cal.data.today.slice(0, 7); S.cal.sel = S.cal.data.today; renderView(); },
    "cal-day": (el) => {
      const d = el.dataset.date;
      S.cal.sel = d;
      if (d.slice(0, 7) !== S.cal.month) S.cal.month = d.slice(0, 7);
      renderView();
      const head = $(".cal-day-head");
      if (head && head.getBoundingClientRect().top > window.innerHeight - 120) head.scrollIntoView({ block: "start", behavior: "instant" });
    },
    "cal-pick": (el, e) => {          // a chip in the month: its day, and the item in the list under it
      const it = findItem(el.dataset.id);
      if (!it) return;
      if (e) e.stopPropagation();
      S.cal.sel = it.date || (S.cal.data.today >= it.week.start && S.cal.data.today <= it.week.end ? S.cal.data.today : it.week.start);
      S.view.focus = it.id; renderView(); focusItem();
    },
    "cal-kind": (el) => { const g = el.dataset.g; if (S.cal.off.has(g)) S.cal.off.delete(g); else S.cal.off.add(g); renderView(); },
    "cal-only": (el) => { S.cal.only = S.cal.only === el.dataset.id ? "" : el.dataset.id; renderView(); },
    "cal-past": () => { S.cal.past = !S.cal.past; renderView(); },
    "cal-new": (el) => entryDialog(null, el && el.dataset.date),
    "cal-open": (el) => {
      const it = findItem(el.dataset.id);
      if (!it) return;
      const s = it.source || {};
      if (s.type === "announcement") A["plan-ann"]({ dataset: { cid: it.course_id, ann: s.id } });
      else if (s.type === "url" && s.url) A.url({ dataset: { url: s.url } });
      else if (s.type === "syllabus") { if (s.url) A.url({ dataset: { url: s.url } }); else go({ name: "course", courseId: it.course_id, tab: "overview" }); }
      else if (s.type === "day") { if (it.transcribed) St.openDay(s.day_key); else go({ name: "course", courseId: it.course_id, tab: "lectures" }); }
      else if (s.type === "week") go({ name: "course", courseId: it.course_id, tab: "weeks", jumpWeek: s.week || "now" });
      else if (s.type === "entry") entryDialog(it);
    },
    "cal-goto": (el) => go({ name: "calendar", focus: el.dataset.id }),
    "cal-del": async (el) => {
      const it = ((S.cal.data || {}).items || []).find((x) => x.entry_id === el.dataset.id);
      if (!(await St.ask({ title: t("Delete this entry?"), text: it ? it.title : "", ok: t("Delete"), danger: true }))) return;
      const r = await call("calendar_delete", el.dataset.id);
      if (!r.ok) { toast(r.error, "error"); return; }
      St.closeModal(); take(r); toast(t("Entry deleted"), "success");
    },
    "cal-remind": () => remindDialog(),
    "cal-rem": async (el) => {
      const r = await call("calendar_reminders", { [el.dataset.key]: +el.value });
      if (!r.ok) { toast(r.error, "error"); return; }
      if (S.cal.data) S.cal.data.reminders = r.reminders;
      if (S.setDefaults) S.setDefaults.reminders = r.reminders;      // (Settings → Notifications shows them too)
      toast(t("Reminders changed"), "success");
    },
    "cal-rem-on": async () => {
      const r = await call("app_config", { notify_reminders: true });
      if (r.ok && S.boot) S.boot.app = r.app;
      remindDialog();
    },
  });
  // the keyboard: Enter or Space on a day of the month picks it
  document.addEventListener("keydown", (e) => {
    const el = e.target && e.target.closest && e.target.closest(".cal-cell[data-act='cal-day']");
    if (!el || (e.key !== "Enter" && e.key !== " ") || e.target !== el) return;
    e.preventDefault(); A["cal-day"](el);
  });

  // ------------------------------------------------------------------ browser preview (mock)
  St.mocks.calendar = () => {
    const today = iso(new Date()), m = monday(today);
    const ids = (S.courses || []).map((c) => [c.id, c.code]);
    const [a, b] = [ids[0] || ["17933", "MATH 240"], ids[1] || ids[0] || ["17653", "EEE 242"]];
    const items = [
      { id: "q2", kind: "quiz", course_id: a[0], code: a[1], title: "Quiz 2", date: "", week: { no: 7, start: m, end: add(m, 6) }, days: [add(m, 1), add(m, 3)], sure: "week", los: [3, 4], source: { type: "announcement", id: "a2" } },
      { id: "l3", kind: "assignment", course_id: b[0], code: b[1], title: "Lab 3", date: add(today, 2), time: "15:40", sure: "exact", done: false, source: { type: "url", url: "https://learn.ieu.edu.tr/student" } },
      { id: "c1", kind: "class", class_kind: "lecture", course_id: a[0], code: a[1], date: add(today, 1), time: "08:30", end_time: "11:05", blocks: [["08:30", "11:05"]], sure: "schedule", source: { type: "week", week: 7 } },
      { id: "mid", kind: "midterm", course_id: a[0], code: a[1], title: "Midterm", date: "", week: { no: 8, start: add(m, 7), end: add(m, 13) }, days: [], sure: "week", los: [], source: { type: "announcement", id: "a2" } },
      { id: "own-1", entry_id: "1", kind: "own", course_id: "", code: "", title: "Study group", date: add(today, 3), time: "14:00", note: "Library, 2nd floor", sure: "yours", source: { type: "entry" } },
    ];
    return { ok: true, today, now: "12:00", items, term: { start: add(m, -42), end: add(m, 70) }, courses: ids.map(([id, code]) => ({ id, code, name: code, section: "", twin: false })), reminders: { exams: 2, due_days: 1, due_hours: 3 } };
  };
  St.mocks.calendar_save = (e) => ({ ...St.mocks.calendar(), entry: { ...e, id: e.id || "2" } });
  St.mocks.calendar_delete = () => St.mocks.calendar();
  St.mocks.calendar_reminders = (p) => ({ ok: true, reminders: { exams: 2, due_days: 1, due_hours: 3, ...(p || {}) } });
})();
