/* IEUStudia — the Exam room (4.2): one page for each coming quiz or exam, a final known only from the syllabus too, in
 * place of Get ready. It says what the assessment covers and where that comes from (the student can change the weeks
 * and set the exact day), what is still missing with one Get everything button, what the lecturer said about it and
 * stressed in those weeks, how much was practised and condensed (counts, never one percentage), and opens the tools
 * that exist already, set to those weeks: a practice quiz, a cram session of their flashcards (the spaced-repetition
 * schedule left alone), a cheatsheet, a review sheet, a chat, the export for AI chat. Every way in that opened Get
 * ready opens it ("ready-open", St.openReady: Home, the course page, Calendar, the digest, a chat's action, a
 * notification); the course page's top has a "Quiz 3 in 4 days" chip. It prints (examroom.css). The class days read
 * are kept in the library (course.js imports the page's old ticks once). Python's side: examroom.py. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, call, esc, I, SPIN, toast, go, renderView, skeleton, A } = St;
  const $ = (s, el = document) => el.querySelector(s);
  const dmw = (iso) => (iso ? t.date(String(iso).slice(0, 10), "dmw") : "");
  const pad2 = (n) => String(n).padStart(2, "0");
  const localToday = () => { const d = new Date(); return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`; };
  const daysTo = (iso) => Math.round((new Date(String(iso).slice(0, 10) + "T00:00:00") - new Date(localToday() + "T00:00:00")) / 864e5);
  const span = (weeks) => (St.pack ? St.pack.weekSpan(weeks) : (weeks || []).join(", "));
  const score = (x) => t.num(x, x % 1 ? 1 : 0);
  const pctOf = (w) => t("{n}%", { n: t.num(+w, (String(w).split(".")[1] || "").length > 1 ? 2 : (String(w).split(".")[1] || "").length) });
  const canAI = () => { const ai = S.system.ai || {}; return !!ai.available && ai.can_plans !== false; };
  const busyFile = (id) => !!(S.mat && S.mat[id] && ["queued", "downloading"].includes(S.mat[id].state));
  const ER = S.examroom = { cid: "", key: "", room: null, error: "", gone: false, asked: 0, back: null, pick: null };

  // ------------------------------------------------------------------ data
  async function load(cid, key, quiet) {
    const asked = ++ER.asked;
    if (!quiet) { ER.room = null; ER.error = ""; ER.gone = false; }
    if (St.readsReady) await St.readsReady();           // (the page's old read ticks, into the library once: course.js)
    const r = await call("exam_room", cid, key);
    if (asked !== ER.asked) return;
    if (!r.ok) { ER.error = r.error || t("Something went wrong"); ER.gone = !!r.gone; ER.room = null; }
    else {
      ER.room = r.room; ER.error = ""; ER.gone = false; ER.key = r.room.assessment.key;
      if (S.view.name === "exam" && S.view.courseId === cid) S.view.key = ER.key;      // (opened by its name)
    }
    if (S.view.name === "exam" && S.view.courseId === cid) renderView();
  }
  let again = 0;
  function reloadSoon() {          // something of the room changed (a lecture, a file, a plan): read it again, once
    if (S.view.name !== "exam" || !ER.room) return;
    clearTimeout(again);
    again = setTimeout(() => load(ER.cid, ER.key, true), 700);
  }
  // the room of a course's quiz or exam: by its key ("quiz:3", "syllabus:Final Exam:16") or its name ("Quiz 3")
  function open(cid, key) {
    if (!cid || !St.findCourse(cid)) { toast(t("This course is no longer in your list."), "error"); return; }
    if (S.view.name !== "exam") ER.back = { ...S.view };
    if ($("#overlay .modal")) St.closeModal();
    ER.cid = cid; ER.key = String(key || ""); ER.room = null; ER.error = ""; ER.pick = null;
    go({ name: "exam", courseId: cid, key: ER.key });
  }

  // ------------------------------------------------------------------ pieces
  // known only by its week (the exam schedule has the day): still to come until that week (or exam period) is over
  const thisWeek = (w) => !!(w && w.from === "week" && w.date && w.date < localToday() && w.end && localToday() <= w.end);
  function whenParts(w) {
    if (!w.date) return { day: t("Day not known yet"), rel: "" };
    const n = daysTo(w.date);
    const rel = thisWeek(w) ? t("this week") : n < 0 ? t.c("assessment", "done") : n === 0 ? t("today") : n === 1 ? t("tomorrow") : t.n(n, "in {n} day", "in {n} days");
    const others = (w.others || []).length ? ` ${t("or {days}", { days: t.list(w.others.map(dmw), "disjunction") })}` : "";
    return { day: dmw(w.date) + (w.time ? `, ${w.time}` : "") + others, rel };
  }
  const FROM = () => ({ announced: t("the day announced"), yours: t("the day you set"), class: t("its week's class day: the day wasn't announced"),
    week: t("its week starts then: the exam schedule on IEULearn has the day") });
  function originHTML(c, a) {
    const o = a.origin || {};
    if (o.from === "yours") return esc(t("the weeks you set"));
    if (o.from === "announced") {
      return o.announcement ? `<button class="link" data-act="plan-ann" data-cid="${esc(c.id)}" data-ann="${esc(o.announcement.id)}">${esc(t("as announced in “{title}”", { title: o.announcement.title || t("an announcement") }))}</button>`
        : esc(t("as announced"));
    }
    return esc(a.kind === "final" ? t("a guess: every week up to it") : o.since ? t("a guess: the weeks since {name}", { name: o.since }) : t("a guess: the weeks up to it"));
  }
  function shareHTML(sh) {
    if (!sh) return "";
    const tip = sh.number > 1 ? t("{activity}: {pct} in all, {n} of them", { activity: sh.activity, pct: pctOf(sh.weight), n: sh.number }) : sh.activity;
    return `<span class="er-share" title="${esc(tip)}">${esc(t("{pct} of your grade", { pct: pctOf(sh.each) }))}</span>`;
  }
  function headHTML(c, r) {
    const a = r.assessment, w = whenParts(r.when), from = FROM()[r.when.from] || "";
    const los = (a.los || []).map((l) => `<div class="er-lo"><span class="lo-chip">LO${l.no}</span><span>${esc(l.en || l.tr || "")}</span></div>`).join("");
    return `<div class="card er-head">
      <div class="er-eyebrow">${I("cap", "sm")}<span>${t("Exam room")}</span><span class="dim">· ${esc(c.code)}</span><span class="spacer"></span>
        <button class="btn ghost sm noprint" data-act="er-print" title="${esc(t("Print this page, or save it as a PDF"))}">${I("file", "sm")} ${t("Print")}</button></div>
      <div class="er-title"><h1>${esc(a.name)}</h1>${shareHTML(r.share)}</div>
      <div class="er-line">${I("calendar", "sm")}<b>${esc(w.day)}</b>${w.rel ? `<span class="er-rel">${esc(w.rel)}</span>` : ""}${from ? `<span class="dim">· ${esc(from)}</span>` : ""}
        <button class="link noprint" data-act="er-day">${r.when.from === "yours" ? t("Change the day") : t("Set the exact day")}</button></div>
      <div class="er-line">${I("layers", "sm")}<b>${esc(t("Covers {weeks}", { weeks: span(a.weeks) }))}</b><span class="dim">· ${originHTML(c, a)}</span>
        <button class="link noprint" data-act="er-weeks">${t("Change the weeks")}</button></div>
      ${los ? `<div class="er-los">${los}</div>` : ""}</div>`;
  }
  // readiness as three counts: material, practised, condensed (never one percentage)
  function countsHTML(r) {
    const k = r.counts, p = r.practice, cards = p.cards || {};
    const practised = [k.practised.quizzes ? t.n(k.practised.quizzes, "in {n} quiz", "in {n} quizzes") : t("no quiz taken yet"),
      cards.reviews ? t.n(cards.reviews, "{n} flashcard answer", "{n} flashcard answers") : ""].filter(Boolean).join(" · ");
    const making = k.condensed.of - k.condensed.done;
    const tile = (cls, label, big, sub) => `<div class="er-count ${cls}"><span class="er-k">${esc(label)}</span><b>${esc(big)}</b><span class="dim sm">${esc(sub)}</span></div>`;
    return `<div class="er-counts">
      ${tile(k.material.of && k.material.have === k.material.of ? "ok" : "", t("Material"), t("{done} of {n}", { done: k.material.have, n: k.material.of }), t("lectures transcribed and files on this PC"))}
      ${tile(k.practised.answers ? "ok" : "", t("Practised"), t.n(k.practised.answers, "{n} question answered", "{n} questions answered"), practised)}
      ${tile(k.condensed.done ? "ok" : "", t("Condensed"), t.n(k.condensed.done, "{n} sheet ready", "{n} sheets ready"),
        making ? t.n(making, "{n} more being made or stopped", "{n} more being made or stopped") : t("cheatsheets, review sheets and study packs for it"))}</div>`;
  }
  function missingHTML(r) {
    const m = r.material, files = m.missing.filter((id) => !busyFile(id)), fetching = m.missing.length - files.length;
    const busy = [m.busy ? `<div class="dim sm">${SPIN()} ${esc(t.n(m.busy, "{n} lecture is being transcribed now: the queue at the bottom shows how long it takes.", "{n} lectures are being transcribed now: the queue at the bottom shows how long it takes."))}</div>` : "",
      fetching ? `<div class="dim sm">${SPIN()} ${esc(t.n(fetching, "Downloading {n} file…", "Downloading {n} files…"))}</div>` : ""].join("");
    if (m.todo.length || files.length) {
      const what = [m.todo.length ? t.n(m.todo.length, "{n} lecture to transcribe", "{n} lectures to transcribe") : "",
        files.length ? t.n(files.length, "{n} file to download", "{n} files to download") : ""].filter(Boolean).join(" · ");
      return `<div class="card er-missing"><span class="er-ico warn">${I("download")}</span><div class="grow"><b>${esc(t("Still missing: {what}", { what }))}</b>
          <div class="dim sm">${esc(m.todo.length ? t("Lectures are transcribed on this PC, before anything from a full sync; files come from IEULearn.") : t("They come from IEULearn."))}</div>${busy}</div>
        <button class="btn primary noprint" data-act="er-get">${I("download", "sm")} ${t("Get everything")}</button></div>`;
    }
    if (busy) return `<div class="card er-missing"><span class="er-ico">${I("wave")}</span><div class="grow">${busy}</div></div>`;
    if (!m.lectures && !m.files.length) return `<div class="card er-missing"><span class="er-ico">${I("info")}</span><div class="grow dim">${esc(t("No lectures or files are listed for these weeks yet."))}</div></div>`;
    return `<div class="card er-missing ok"><span class="er-ico ok">${I("check")}</span><div class="grow"><b>${esc(t("Everything of these weeks is on this PC."))}</b>
      ${m.silent ? `<div class="dim sm">${esc(t.n(m.silent, "{n} lecture has no speech on Panopto", "{n} lectures have no speech on Panopto"))}</div>` : ""}</div></div>`;
  }
  // the tools, set to these weeks (the first one is the obvious next step once nothing is missing)
  function stepsHTML(c, r) {
    const ai = S.system.ai || {}, m = r.material, cards = r.practice.cards || {};
    const ready = !m.todo.length && !m.missing.length;
    const read = m.days.filter((x) => x.any_done).length;
    const aiTip = (s) => (canAI() ? s : St.aiHint());
    return `<div class="card er-steps noprint"><div class="er-h">${I("zap", "sm")}<b>${t("Get ready for it")}</b><span class="dim sm">${esc(t("each one opens set to these weeks"))}</span></div>
      <div class="er-btns">
        <button class="btn ${ready ? "primary" : ""}" data-act="er-quiz" ${St.aiBtn(canAI(), true)} title="${esc(aiTip(t("Exam-style questions on these weeks, written by {ai}", { ai: St.aiName() })))}">${I("list-checks", "sm")} ${t("Practice quiz for it")}</button>
        ${cards.total ? `<button class="btn" data-act="er-cram" title="${esc(t("Every flashcard of these weeks, due or not. When cards come back stays as it was."))}">${I("layers", "sm")} ${esc(t.n(cards.total, "Cram {n} card", "Cram {n} cards"))}</button>`
          : `<button class="btn" data-act="fc-make" data-cid="${esc(c.id)}" ${St.aiBtn(canAI(), true)} title="${esc(aiTip(t("No flashcards from these weeks yet: {ai} makes them from the files you pick", { ai: St.aiName() })))}">${I("layers", "sm")} ${t("Cards from files")}</button>`}
        <button class="btn" data-act="er-sheet" ${St.aiBtn(canAI(), true)} title="${esc(aiTip(t("The formulas and rules of these weeks on a few pages")))}">${I("sigma", "sm")} ${t("Cheatsheet")}</button>
        <button class="btn" data-act="er-prep" ${St.aiBtn(canAI(), true)} title="${esc(aiTip(t("What to know for it, with practice questions")))}">${I("cap", "sm")} ${t("Review sheet")}</button>
        <button class="btn" data-act="er-ask" ${!ai.available ? St.aiBtn(false) : read ? "" : St.whyOff(t("Transcribe a lecture of these weeks first: then you can ask {ai} about it.", { ai: St.aiName() }))} title="${esc(t.n(read, "A chat about {n} transcribed lecture of these weeks", "A chat about {n} transcribed lectures of these weeks"))}">${I("chat", "sm")} ${t("Ask about these weeks")}</button>
        <button class="btn" data-act="er-export" title="${esc(t("A zip of these weeks for ChatGPT or Claude on the web"))}">${I("archive", "sm")} ${t("Export for AI chat")}</button></div></div>`;
  }
  function markHL(text, hl) {
    if (!hl || !(hl[1] > hl[0])) return esc(text);
    return esc(text.slice(0, hl[0])) + `<mark>${esc(text.slice(hl[0], hl[1]))}</mark>` + esc(text.slice(hl[1]));
  }
  function saidHTML(c, r) {
    if (!r.said.length) return "";
    const row = (a) => {
      const lecture = a.source === "lecture";
      const target = lecture ? `data-day="${esc(a.day_key)}" data-part="${a.part}" data-t="${a.t}"` : `data-cid="${esc(c.id)}" data-ann="${esc(a.announcement_id)}"`;
      const meta = lecture ? `${I("video", "xs")} ${esc(t("Lecture"))} · ${esc(dmw(a.date))}${a.part ? ` · ${esc(t("part {n}", { n: a.part }))}` : ""} · ${esc(a.ts || "")}`
        : `${I("megaphone", "xs")} ${esc(t("Announcement"))} · ${esc(dmw(a.date))}`;
      return `<button class="er-item" data-act="alert-open" ${target}><span class="er-text">${lecture ? "“" : `<b>${esc(a.title || "")}</b>: `}${markHL(a.text || "", a.hl)}${lecture ? "”" : ""}</span>
        <span class="er-meta">${meta}</span></button>`;
    };
    return `<div class="card er-card"><div class="er-h">${I("megaphone", "sm")}<b>${t("What the lecturer said about it")}</b></div>${r.said.map(row).join("")}</div>`;
  }
  function stressedHTML(r) {
    if (!r.stressed.length) return "";
    return `<div class="card er-card"><div class="er-h">${I("star", "sm")}<b>${t("Stressed in class in those weeks")}</b></div>
      ${r.stressed.map((s) => `<button class="er-item" data-act="alert-open" data-day="${esc(s.day_key)}" data-part="${s.part}" data-t="${s.t}" title="${esc(t("Read it in the transcript"))}">
        <span class="er-text">${s.kind === "exam" ? `<span class="pill due">${I("cap")}${esc(t("About the exam"))}</span> ` : ""}“${markHL(s.text || "", s.hl)}”</span>
        <span class="er-meta">${I("video", "xs")} ${s.week ? `${esc(t("Week {n}", { n: s.week }))} · ` : ""}${esc(dmw(s.day))} · ${esc(t("part {n}", { n: s.part }))} · ${esc(s.ts || "")}${s.n > 1 ? ` · ${esc(t.n(s.n, "said {n} time", "said {n} times"))}` : ""}</span></button>`).join("")}</div>`;
  }
  function practiceHTML(c, r) {
    const p = r.practice, w = p.mastery || {}, cards = p.cards || {}, named = (r.assessment.los || []).length > 0;
    const tried = (w.los || []).filter((x) => x.status !== "untested"), untested = named ? (w.los || []).filter((x) => x.status === "untested") : [];
    const topics = (w.topics || []).filter((x) => x.status !== "solid" && x.right < x.tried).slice(0, 4);
    const skill = (key, label, text, x) => (St.mistakes && St.mistakes.skillRow ? St.mistakes.skillRow(c, { mistakes: p.mistakes }, key, label, text, x)
      : `<div class="mq-skill"><div class="mq-skill-h">${label ? `<span class="lo-no">${esc(label)}</span>` : ""}<span class="grow">${esc(text || "")}</span><span class="dim sm">${esc(t("{right} of {n} right", { right: score(x.right), n: x.tried }))}</span></div></div>`);
    const rows = [...tried.map((x) => skill(`er:lo:${x.no}`, `LO${x.no}`, x.text, x)), ...topics.map((x) => skill(`er:tp:${x.name}`, "", x.name, x))].join("");
    const quiz = (q) => `<div class="er-q"><div class="grow"><b>${esc(q.title || t("Practice quiz"))}</b><div class="dim sm">${esc(span(q.weeks))}${q.focus ? ` · ${esc(t("for {name}", { name: q.focus }))}` : ""}${q.best ? ` · ${esc(t("Best: {score} of {total}", { score: score(q.best.score), total: q.best.total }))}` : q.tries ? "" : ` · ${esc(t("not taken yet"))}`}</div></div>
      ${q.status === "done" ? `<button class="btn sm noprint" data-act="qz-play" data-id="${esc(q.id)}">${I("play", "sm")} ${q.tries ? t("Practise again") : t("Practise")}</button>` : `<span class="dim sm">${esc(q.status === "error" ? t("failed") : t("being written"))}</span>`}</div>`;
    const cardLine = cards.total ? [t.n(cards.total, "{n} flashcard from these weeks", "{n} flashcards from these weeks"), cards.due ? t.n(cards.due, "{n} due today", "{n} due today") : "",
      cards.learnt ? t.n(cards.learnt, "{n} learnt", "{n} learnt") : ""].filter(Boolean).join(" · ") : t("No flashcards from these weeks yet.");
    return `<div class="card er-card"><div class="er-h">${I("target", "sm")}<b>${t("How well you know it")}</b></div>
      ${rows ? `<p class="dim sm er-sub">${esc(t("From your answers in its quizzes: counts, with recent ones counting more."))}</p><div class="mq-skills">${rows}</div>`
        : `<p class="dim sm er-sub">${esc(p.quizzes.length ? t("Take one of its quizzes: then this shows, per learning outcome, where you lose points.") : t("No quiz on these weeks yet. Take one and this shows, per learning outcome, where you lose points."))}</p>`}
      ${untested.length ? `<div class="mq-sub">${t("Not tested yet")}</div><div class="mq-skills">${untested.map((x) => `<div class="mq-skill"><div class="mq-skill-h"><span class="lo-no">LO${x.no}</span><span class="grow">${esc(x.text || "")}</span>
          <button class="btn ghost sm noprint" data-act="mq-quiz-lo" data-cid="${esc(c.id)}" data-no="${x.no}" data-text="${esc(x.text || "")}" ${St.aiBtn(canAI(), true)}>${t("Quiz this LO")}</button></div></div>`).join("")}</div>` : ""}
      ${p.missed ? `<div class="er-note">${I("retry", "sm")}<span>${esc(t.n(p.missed, "{n} question you missed is still open.", "{n} questions you missed are still open."))}</span> <button class="link noprint" data-act="er-practice" data-cid="${esc(c.id)}">${t("Go over them")}</button></div>` : ""}
      ${p.quizzes.length ? `<div class="mq-sub">${t("Its quizzes")}</div>${p.quizzes.slice(0, 5).map(quiz).join("")}` : ""}
      <div class="er-note">${I("layers", "sm")}<span>${esc(cardLine)}</span></div></div>`;
  }
  const KIND = () => ({ sheet: [t("Cheatsheet"), "sigma"], prep: [t("Review sheet"), "cap"], pack: [t("Study pack"), "book"], videos: [t("Video plan"), "youtube"] });
  const planState = (s) => ({ done: t.c("plan", "ready"), running: t.c("plan", "running"), queued: t.c("plan", "queued"), error: t.c("plan", "error"), paused: t.c("plan", "paused") })[s] || s;
  function condensedHTML(r) {
    if (!r.condensed.length) return "";
    return `<div class="card er-card"><div class="er-h">${I("book", "sm")}<b>${t("Made for it")}</b></div>
      ${r.condensed.map((p) => { const [name, icon] = KIND()[p.kind] || KIND().videos; return `<button class="er-item row" data-act="plan-open" data-id="${esc(p.id)}">${I(icon, "sm")}
        <span class="er-text"><b>${esc(name)}</b>${p.title ? ` · ${esc(p.title)}` : ""}</span><span class="er-meta">${esc(span(p.weeks))} · ${esc(planState(p.status))}</span></button>`; }).join("")}</div>`;
  }
  function weeksHTML(c, r) {
    const nos = new Set(r.assessment.weeks), m = r.material;
    const weeks = r.weeks.filter((w) => nos.has(w.no));
    if (!weeks.length) return "";
    const day = (x) => `<div class="er-day"><input type="checkbox" class="check noprint" data-act="er-read" data-day="${esc(x.day_key)}" ${x.read ? "checked" : ""} ${x.any_done ? "" : "disabled"} title="${esc(t.c("state", "Read"))}" aria-label="${esc(t.c("state", "Read"))}">
        <span class="er-print-tick">${x.read ? "☑" : "☐"}</span>
        ${x.any_done ? `<button class="link" data-act="open-day" data-day="${esc(x.day_key)}">${esc(x.kind_label || t("Lecture"))} · ${esc(dmw(x.date))}</button>` : `<span>${esc(x.kind_label || t("Lecture"))} · ${esc(dmw(x.date))}</span>`}
        <span class="dim sm">${esc(x.transcribed ? (x.read ? t.c("state", "Read") : t("Transcribed")) : x.busy ? t("being transcribed") : x.live ? t("Being recorded") : x.any_done ? t("partly transcribed") : t("not transcribed"))}</span></div>`;
    const file = (f) => `<div class="er-file"><span class="er-print-tick">${f.downloaded ? "☑" : "☐"}</span>${I("file-text", "xs")}<button class="link" data-act="mat-open" data-id="${esc(f.id)}">${esc(f.name || f.title)}</button>
        <span class="dim sm">${esc(f.downloaded ? "" : busyFile(f.id) ? t("downloading…") : t("not on this PC"))}${f.tied ? ` ${esc(t("· tied to it"))}` : ""}</span></div>`;
    const tied = m.files.filter((f) => f.week == null);
    const has = (w) => m.days.some((x) => x.week === w.no) || m.files.some((f) => f.week === w.no);
    const empty = weeks.filter((w) => !has(w)).map((w) => w.no);          // (a final's weeks still ahead: one line)
    return `<div class="card er-card er-weeks"><div class="er-h">${I("calendar", "sm")}<b>${t("Its weeks")}</b>
        ${m.readable ? `<span class="dim sm">${esc(t("Lectures read: {done} of {n}", { done: m.read, n: m.readable }))}</span>` : ""}</div>
      ${weeks.filter(has).map((w) => `<div class="er-week">
        <div class="er-week-h"><b>${esc(t("Week {n}", { n: w.no }))}</b><span class="grow">${esc(w.topic || "")}</span><span class="dim sm">${esc(dmw(w.start))}</span></div>
        ${m.days.filter((x) => x.week === w.no).map(day).join("")}${m.files.filter((f) => f.week === w.no).map(file).join("")}</div>`).join("")}
      ${empty.length ? `<div class="er-week dim sm er-none">${esc(t("Nothing yet for {weeks}.", { weeks: span(empty) }))}</div>` : ""}
      ${tied.length ? `<div class="er-week"><div class="er-week-h"><b>${esc(t("Course-wide and announcement files"))}</b></div>${tied.map(file).join("")}</div>` : ""}</div>`;
  }
  // the evening before, with the background checks off: offer them once (a reminder comes when the app is closed)
  function trayHTML(r) {
    const app = (S.boot || {}).app || {};
    if (!app.onboarded || app.tray !== false || window.LS_LINUX || r.when.days == null || (r.when.days < 0 && !thisWeek(r.when)) || r.when.days > 1) return "";
    try { if (localStorage.getItem("studia-room-tray") === "1") return ""; } catch (e) { /* private mode */ }
    return `<div class="callout info noprint er-tray">${I("info")}<div class="grow">${esc(t("Background checks are off, so no reminder comes while IEUStudia is closed. Turn them on to hear about new lectures, announcements and your next quiz."))}
      <div class="er-tray-acts"><button class="btn sm" data-act="er-tray-on">${t("Turn them on")}</button><button class="btn ghost sm" data-act="er-tray-no">${t("Not now")}</button></div></div></div>`;
  }

  // ------------------------------------------------------------------ the page
  function view(v) {
    const c = St.findCourse(v.courseId);
    if (!c) return `<div class="view-inner">${St.emptyHTML("book", t("Course not found"), "")}</div>`;
    if (ER.error) {
      return `<div class="view-inner er-page">${St.emptyHTML("cap", ER.gone ? t("This assessment is no longer in the course's plan.") : ER.error, "")}
        <div class="er-center"><button class="btn" data-act="er-back">${I("arrow-left", "sm")} ${t("Back")}</button>${ER.gone ? "" : ` <button class="btn" data-act="er-reload">${t("Try again")}</button>`}</div></div>`;
    }
    const r = ER.room;
    if (!r || r.course.id !== v.courseId) return `<div class="view-inner er-page">${skeleton()}</div>`;
    const right = saidHTML(c, r) + stressedHTML(r), made = condensedHTML(r);
    const cols = right ? [practiceHTML(c, r) + made, right] : made ? [practiceHTML(c, r), made] : [practiceHTML(c, r)];
    return `<div class="view-inner er-page" style="--h:${St.hue ? St.hue(c) : 220}">${headHTML(c, r)}${trayHTML(r)}${countsHTML(r)}${missingHTML(r)}${stepsHTML(c, r)}
      <div class="er-grid ${cols.length === 1 ? "one" : ""}">${cols.map((x) => `<div class="er-col">${x}</div>`).join("")}</div>
      ${weeksHTML(c, r)}${St.sq ? St.sq.line(c) : ""}<p class="dim sm er-foot">${esc(t("Worked out on this PC from your lectures, files, announcements and practice, without AI."))}</p></div>`;
  }
  // the course page's top: the next quiz or exam, as a chip that opens its room ("Quiz 3 in 4 days")
  function chip(c, d) {
    const today = localToday();
    const e = ((d && d.assessments) || []).filter((x) => x.ahead && x.on && x.on.date && (x.on.date >= today || thisWeek(x.on)))
      .sort((a, b) => a.on.date.localeCompare(b.on.date))[0];
    if (!e) return "";
    const n = daysTo(e.on.date);
    if (n > 28) return "";
    const label = n < 0 ? t("{name} this week", { name: e.name }) : n === 0 ? t("{name} today", { name: e.name }) : n === 1 ? t("{name} tomorrow", { name: e.name }) : t.n(n, "{name} in {n} day", "{name} in {n} days", { name: e.name });
    const tip = [t("Open its Exam room: what it covers, what is still missing and how to practise"), e.on.from === "week" ? t("The exam schedule on IEULearn has the day; this counts to its week's start.") : ""].filter(Boolean).join("\n");
    return `<button class="stat er-chip ${n <= 2 ? "soon" : ""}" data-act="ready-open" data-cid="${esc(c.id)}" data-key="${esc(e.key)}" data-name="${esc(e.name)}" title="${esc(tip)}">${I("cap", "xs")}<b>${esc(label)}</b></button>`;
  }

  // ------------------------------------------------------------------ dialogs: the weeks, the day
  function weeksDialog() {
    const r = ER.room; if (!r) return;
    const a = r.assessment;
    ER.pick = new Set(ER.pick || a.weeks);
    const back = a.weeks_from === "yours" ? (a.covers.length ? t("Back to the announced weeks ({weeks})", { weeks: span(a.covers) }) : t("Back to the guessed weeks ({weeks})", { weeks: span(a.guessed) })) : "";
    St.modal(`<h2>${I("layers")} ${t.html("Which weeks does {name} cover?", { name: a.name })}</h2>
      <p class="lead">${esc(t("Every tool here uses these weeks: the quiz, the cards, the sheets and Get everything."))}</p>
      <div class="pk-weeks er-pick">${r.weeks.map((w) => `<button type="button" class="pk-wk ${ER.pick.has(w.no) ? "on" : ""} ${w.current ? "now" : ""}" data-act="er-wk" data-no="${w.no}" title="${esc(w.topic || "")}"><b>${t("W{n}", { n: w.no })}</b><span>${esc(w.topic || "—")}</span></button>`).join("")}</div>
      <div class="actions">${back ? `<button class="btn ghost" data-act="er-weeks-reset">${esc(back)}</button><span class="spacer"></span>` : ""}
        <button class="btn" data-act="modal-close">${t("Cancel")}</button><button class="btn primary" data-act="er-weeks-save" ${ER.pick.size ? "" : "disabled"}>${t("Save")}</button></div>`);
  }
  function dayDialog() {
    const r = ER.room; if (!r) return;
    const w = r.when;
    St.modal(`<h2>${I("calendar")} ${t.html("When is {name}?", { name: r.assessment.name })}</h2>
      <p class="lead">${esc(t("Set the day once you know it (the exam schedule on IEULearn has the midterms' and finals'). The room counts the days to it."))}</p>
      <div class="field"><label for="er-day-in">${t("Day")}</label><input class="input" type="date" id="er-day-in" value="${esc(w.date || "")}"></div>
      <div class="actions">${w.from === "yours" ? `<button class="btn ghost" data-act="er-day-clear">${t("Forget the day I set")}</button><span class="spacer"></span>` : ""}
        <button class="btn" data-act="modal-close">${t("Cancel")}</button><button class="btn primary" data-act="er-day-save">${t("Save")}</button></div>`);
  }
  async function saved(r) {
    if (!r.ok) { toast(r.error, "error"); return; }
    St.closeModal();
    ER.room = r.room; ER.pick = null;
    renderView();
  }

  // ------------------------------------------------------------------ wiring
  St.views.exam = view;
  St.crumbs.exam = (v) => { const c = St.findCourse(v.courseId); return `<button class="btn ghost sm" data-act="er-back">${I("arrow-left", "sm")} ${t("Back")}</button> ${c ? esc(c.code) : ""} <span class="dim">· ${t("Exam room")}</span>`; };
  St.onGo.exam = (v) => {
    ER.cid = v.courseId; ER.key = v.key || "";
    if (!S.cd[v.courseId]) call("course_detail", v.courseId).then((r) => { if (r.ok && !S.cd[v.courseId]) S.cd[v.courseId] = r.course; });   // (its files open from here)
    load(v.courseId, v.key || "");
  };
  St.openReady = open;               // (Get ready's way in: notifications, a chat's action)
  St.examroom = { open, chip, load, state: ER };
  St.inputActs = (St.inputActs || []).concat(["er-read"]);      // its read ticks: app.js passes their clicks on
  // what changes the room: a lecture transcribed or queued, a file in, a quiz or sheet made
  const onCourses = St.onEvents.courses;
  St.onEvents.courses = (e) => { if (onCourses) onCourses(e); reloadSoon(); };
  const onMaterial = St.onEvents.material;
  St.onEvents.material = (e) => {
    if (onMaterial) onMaterial(e);
    if (S.view.name === "exam" && ER.room && ER.room.material.files.some((f) => f.id === e.id)) { if (e.state === "done") reloadSoon(); else renderView(); }
  };
  St.onPracticed = (cid) => { if (cid === ER.cid) reloadSoon(); };        // a flashcard sitting or a quiz try ended (practice.js)
  const onPlan = St.onEvents.plan;
  St.onEvents.plan = (e) => { if (onPlan) onPlan(e); if (e && ["done", "error", "queued", "paused"].includes(e.status)) reloadSoon(); };

  Object.assign(A, {
    "ready-open": (el) => open(el.dataset.cid, el.dataset.key || el.dataset.name),
    "er-back": () => { const b = ER.back; go(b && b.name && b.name !== "exam" ? b : { name: "course", courseId: ER.cid }); },
    "er-reload": () => load(ER.cid, ER.key),
    "er-print": () => window.print(),
    "er-get": async (el) => {
      el.disabled = true;
      const r = await call("exam_get_everything", ER.cid, ER.key);
      if (!r.ok) { el.disabled = false; toast(r.error, "error"); return; }
      const bits = [r.lectures ? t.n(r.lectures, "{n} lecture queued ({parts}), before anything from a full sync", "{n} lectures queued ({parts}), before anything from a full sync",
        { parts: t.n(r.parts, "{n} part", "{n} parts") }) : "",
        r.files ? t.n(r.files, "{n} file downloading", "{n} files downloading") : ""].filter(Boolean);
      if (bits.length || !(r.unknown || r.needs_section)) toast(bits.join(" · ") || t("Nothing new to get: the rest is on its way."), bits.length ? "success" : "info");
      if (St.sectionLeftOut) St.sectionLeftOut((St.findCourse(ER.cid) || {}).code || "", { unknown: r.unknown || 0, needs_section: !!r.needs_section });
      (ER.room ? ER.room.material.missing : []).forEach((id) => { S.mat[id] = { ...(S.mat[id] || {}), state: "queued" }; });
      load(ER.cid, ER.key, true);
    },
    "er-quiz": () => St.practice && St.practice.quizDialog(ER.cid, { mode: "next", key: ER.key }),
    "er-cram": () => St.practice && ER.room && St.practice.study(ER.cid, { weeks: ER.room.assessment.weeks, cram: true }),
    "er-sheet": () => ER.room && A["pack-new"]({ dataset: { id: ER.cid, kind: "sheet", weeks: ER.room.assessment.weeks.join(",") } }),
    "er-prep": () => ER.room && A["plan-new"]({ dataset: { id: ER.cid, kind: "prep", focus: ER.room.assessment.name } }),
    "er-ask": () => {
      const r = ER.room; if (!r) return;
      const days = r.material.days.filter((x) => x.any_done).map((x) => x.day_key);
      St.chatPicker(ER.cid, { days, focus: "full", slides: false, model: (S.settings || {}).ai_model || "sonnet" });
    },
    "er-export": () => ER.room && A["cx-open"]({ dataset: { id: ER.cid, key: ER.key, weeks: ER.room.assessment.weeks.join(",") } }),   // (export.js: "For" it)
    "er-read": async (el) => {
      const r = await call("reads_set", [el.dataset.day], !!el.checked);
      if (!r.ok) { toast(r.error, "error"); return; }
      load(ER.cid, ER.key, true);
    },
    "er-weeks": () => { ER.pick = null; weeksDialog(); },
    "er-wk": (el) => { const no = +el.dataset.no; if (ER.pick.has(no)) ER.pick.delete(no); else ER.pick.add(no); weeksDialog(); },
    "er-weeks-save": async () => saved(await call("exam_set_weeks", ER.cid, ER.key, [...ER.pick].sort((a, b) => a - b))),
    "er-weeks-reset": async () => saved(await call("exam_set_weeks", ER.cid, ER.key, [])),
    "er-day": dayDialog,
    "er-day-save": async () => { const v = ($("#er-day-in") || {}).value || ""; if (!v) { toast(t("Pick a day first."), "info"); return; } saved(await call("exam_set_day", ER.cid, ER.key, v)); },
    "er-day-clear": async () => saved(await call("exam_set_day", ER.cid, ER.key, "")),
    "er-tray-on": () => { try { localStorage.setItem("studia-room-tray", "1"); } catch (e) { /* private mode */ } if (A["bg-on"]) A["bg-on"](); renderView(); },
    "er-tray-no": () => { try { localStorage.setItem("studia-room-tray", "1"); } catch (e) { /* private mode */ } renderView(); },
    "er-practice": (el) => go({ name: "course", courseId: el.dataset.cid, tab: "practice" }),
  });

  // ------------------------------------------------------------------ browser preview (mock)
  St.mocks.exam_room = (cid, key) => {
    const c = St.findCourse(cid) || { id: cid, code: "MATH 240" };
    const d = S.cd[cid] || {};
    const a = ((d.assessments || []).find((x) => x.key === key || x.name === key)) || { key: "quiz:3", kind: "quiz", no: 3, name: "Quiz 3", week: 10, weeks: [8, 9], weeks_from: "announced", guessed: [9, 10], covers: [8, 9], los: [5, 6], sources: [{ id: "a2", title: "Quiz Takvimi" }], files: [], ahead: true };
    const day = (n, k, done, read) => ({ day_key: `${cid}|2026-11-${n}|lecture`, date: `2026-11-${n}`, label: "", kind_label: "Lecture", week: k, transcribed: done, any_done: done, live: false, busy: false, read });
    return { ok: true, room: { course: { id: cid, code: c.code, name: c.display_name || "" },
      assessment: { ...a, los: (a.los || []).map((n) => ({ no: n, en: `Outcome ${n}`, tr: "" })), origin: { from: a.weeks_from, since: "Quiz 2", announcement: a.weeks_from === "announced" ? { id: "a2", title: "Quiz Takvimi" } : null } },
      when: { date: a.on && a.on.date ? a.on.date : "2026-11-30", time: "", from: "class", others: [], days: 3 }, share: { activity: "Quizzes / Studio Critiques", weight: 20, number: 4, each: 5 },
      weeks: (d.weeks || []).map((w) => ({ no: w.no, start: w.start, end: w.end, topic: w.topic_en || "", current: !!w.current, past: !!w.past, exam: !!w.exam })),
      material: { days: [day(9, a.weeks[0], true, true), day(11, a.weeks[0], true, false), day(16, a.weeks[a.weeks.length - 1], false, false)], files: [{ id: "m1", title: "Week08", name: "Week08.pdf", ext: "pdf", downloaded: true, week: a.weeks[0], tied: false }, { id: "m2", title: "Week09", name: "Week09.pdf", ext: "pdf", downloaded: false, week: a.weeks[a.weeks.length - 1], tied: false }],
        lectures: 3, silent: 0, transcribed: 2, busy: 0, readable: 2, read: 1, downloaded: 1, todo: [`${cid}|2026-11-16|lecture`], missing: ["m2"] },
      said: [], stressed: [], practice: { quizzes: [], answers: 0, tried: 0, cards: { total: 12, due: 3, new: 9, learnt: 0, reviews: 0 }, mastery: { los: [], topics: [] }, mistakes: [], missed: 0 },
      condensed: [], counts: { material: { have: 3, of: 5 }, practised: { answers: 0, quizzes: 0, reviews: 0 }, condensed: { done: 0, of: 0 } }, others: [] } };
  };
  St.mocks.exam_set_weeks = (cid, key) => St.mocks.exam_room(cid, key);
  St.mocks.exam_set_day = (cid, key) => St.mocks.exam_room(cid, key);
  St.mocks.exam_get_everything = () => ({ ok: true, lectures: 1, parts: 2, files: 1, unknown: 0, needs_section: false });
  St.mocks.reads_set = () => ({ ok: true, changed: 1 });
  St.mocks.reads_import = (keys) => ({ ok: true, added: (keys || []).length });
})();
