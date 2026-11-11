/* IEUStudia — Practice: practice quizzes the AI writes from the course material (quizzes.py), the course's flashcards
 * with spaced repetition and their Anki export (cards.py, anki.py), and what the lecturer stressed in class
 * (stressed.py). A "Practice" page in the sidebar's Study group (every course at a glance), a Practice tab on each
 * course page (St.practice.tab, course.js), and a small "cards due" note on Home (St.practice.homeNote). Quizzes and
 * cards made from files run in the background as plans: their events are handled here, not in the plan list.
 * What a student knows is kept by Python in their own profile's library; nothing of it is kept in the page's
 * storage, which every profile on the PC shares. Plugs into app.js through window.Studia. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, call, esc, I, SPIN, toast, modal, closeModal, go, renderView, renderSidebar, skeleton, A } = St;
  const $ = (s, el = document) => el.querySelector(s);
  const LETTERS = "ABCDEF";
  const dmw = (iso) => (iso ? t.date(String(iso).slice(0, 10), "dmw") : "");
  const md = (s) => St.mdToHtml(String(s || ""));
  const mdLine = (s) => md(s).replace(/^<p>([\s\S]*)<\/p>$/, "$1");       // one paragraph, inline (beside a label)
  const math = () => { const m = $("#overlay .modal"); if (m && St.renderMath) St.renderMath(m); };
  const score = (x) => t.num(x, x % 1 ? 1 : 0);
  const P = S.practice = { summary: null, course: {}, failed: {}, busy: {}, week: {}, more: new Set(), stressing: null, run: null, runs: {}, study: null };
  // a quiz in progress (answered, not finished): P.run is the one open last; another quiz opened meanwhile keeps it
  // in P.runs, so each one goes on where it was left
  const runOf = (id) => (P.run && P.run.id === id ? P.run : P.runs[id]) || null;
  const inProgress = (R) => !!R && !R.saved && R.pos >= 0;

  // ------------------------------------------------------------------ data
  let summaryAsked = null;
  async function loadSummary(full) {
    summaryAsked = !!full;
    const r = await call("practice_summary", !!full);
    if (!r.ok) return;
    P.summary = r;
    renderSidebar();
    if (S.view.name === "practice") renderView();
    else if (S.view.name === "home") { const slot = $("#pr-home"); if (slot) slot.outerHTML = homeNote(); }
  }
  async function loadCourse(cid) {
    if (P.busy[cid]) return;
    P.busy[cid] = true;
    const r = await call("practice_course", cid);
    P.busy[cid] = false;
    if (!r.ok) { P.failed[cid] = r.error || t("Something went wrong"); if (onTab(cid)) renderView(); return; }
    delete P.failed[cid];
    P.course[cid] = r;
    if (onTab(cid)) renderView();
  }
  const onTab = (cid) => S.view.name === "course" && S.view.courseId === cid && S.view.tab === "practice";
  // a quiz or card set just started: its row (its bar has the plan's id) scrolled into view, if it is drawn
  const inView = (id) => {
    const bar = id && [...document.querySelectorAll(".pr-prog[data-id]")].find((b) => b.dataset.id === id);
    const row = bar && (bar.closest(".pr-quiz") || bar);
    if (!row || !row.scrollIntoView) return;
    row.style.scrollMarginBottom = "72px";             // (room for its wait line, which comes a moment later)
    row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };
  const refresh = (cid) => { loadCourse(cid); loadSummary(S.view.name === "practice"); };
  const canAI = () => { const ai = S.system.ai || {}; return !!ai.available && ai.can_plans !== false; };
  const weekSpan = (weeks) => (St.pack ? St.pack.weekSpan(weeks) : (weeks || []).join(", "));

  // ------------------------------------------------------------------ the Practice page (sidebar)
  function courseRow(r) {
    const c = St.findCourse(r.id) || { id: r.id, code: r.code, display_name: "" };
    const d = r.deck || {}, last = r.last_try;
    const cards = d.total ? (d.today ? t.n(d.today, "{n} card to study today", "{n} cards to study today") : t("All cards done for today"))
      : t("No flashcards yet");
    const sub = d.total ? t.n(d.total, "{n} card in all", "{n} cards in all") : t("They come from a lecture's flashcards or from files");
    const quiz = last ? `${t("Last quiz: {score} of {total}", { score: score(last.score), total: last.total })} · ${t.ago(last.finished_at)}`
      : r.quizzes ? t.n(r.quizzes, "{n} quiz", "{n} quizzes") : t("No quiz yet");
    // (those that stand out: the course tab counts every moment, those said in passing too)
    const stress = r.stressed == null ? "" : `<div class="pr-row">${I("star", "sm")}<div class="grow">${r.stressed
      ? esc(t.n(r.stressed, "Stressed in class: {n} moment that stands out", "Stressed in class: {n} moments that stand out")) : `<span class="dim">${esc(t("Nothing stressed in the transcribed lectures yet."))}</span>`}</div>
      ${r.stressed ? `<button class="btn ghost sm" data-act="pr-open" data-cid="${esc(r.id)}">${t("See them")}</button>` : ""}</div>`;
    return `<div class="card pr-course" style="--h:${St.hue ? St.hue(c) : 220}">
      <button class="pr-c-head" data-act="pr-open" data-cid="${esc(r.id)}" title="${esc(t("Open this course's Practice tab"))}"><span class="pr-code">${esc(r.code)}</span><span class="grow pr-cname">${esc(c.display_name && St.courseSub ? St.courseSub(c) : c.display_name || "")}</span>${I("chevron-right", "sm")}</button>
      <div class="pr-row">${I("layers", "sm")}<div class="grow"><div>${esc(cards)}</div><div class="dim sm">${esc(sub)}</div></div>${d.today ? `<button class="btn primary sm" data-act="fc-study" data-cid="${esc(r.id)}">${t("Study now")}</button>` : ""}</div>
      <div class="pr-row">${I("list-checks", "sm")}<div class="grow"><div>${esc(quiz)}</div>${r.quizzes_busy ? `<div class="dim sm">${SPIN()} ${t("Writing your quiz…")}</div>` : ""}</div>
        <button class="btn sm" data-act="qz-new" data-cid="${esc(r.id)}" ${canAI() ? "" : "disabled"}>${t("New quiz")}</button></div>
      ${stress}</div>`;
  }
  function practiceView() {
    const sm = P.summary;
    const head = `<div class="pl-head"><div><h1>${t("Practice")}</h1>
      <div class="dim">${t("Practice quizzes from your files and lectures, flashcards that come back just before you would forget them, and what your lecturers stressed in class.")}</div></div>
      <div class="pl-acts"><button class="btn primary" data-act="qz-new" ${St.aiBtn(canAI(), true)} title="${esc(canAI() ? t("Exam-style questions from your files and lectures, written by {ai}", { ai: St.aiName() }) : St.aiHint())}">${I("list-checks", "sm")} ${t("New quiz")}</button></div></div>`;
    if (!sm) return `<div class="view-inner pr-page">${head}${skeleton()}</div>`;
    if (!sm.courses.length) return `<div class="view-inner pr-page">${head}${St.emptyHTML("book", t("No courses yet"), t("Sync your courses first."))}</div>`;
    const due = sm.due ? `<div class="callout info pr-due">${I("layers")}<div>${esc(t.n(sm.due, "{n} flashcard to study today. A few minutes a day is enough: each card comes back just before you would forget it.", "{n} flashcards to study today. A few minutes a day is enough: each card comes back just before you would forget it."))}</div></div>` : "";
    // a course with no transcribed lecture, no file read yet and nothing made: one quiet line, not a card of "nothing yet"
    const some = sm.courses.filter((r) => r.material || (r.deck || {}).total || r.quizzes), none = sm.courses.filter((r) => !some.includes(r));
    const named = (r) => {                       // two courses of one code (FENG 495 twice): by their section
      const c = St.findCourse(r.id) || {};
      return sm.courses.filter((x) => x.code === r.code).length > 1 && c.section ? `${r.code} (${t("Section {tag}", { tag: c.section })})` : r.code;
    };
    const quiet = none.length ? `<p class="dim sm pr-none">${esc(t("Nothing to practise from yet in {list}: transcribe a lecture or download the course's files first.", { list: t.list(none.map(named)) }))}</p>` : "";
    return `<div class="view-inner pr-page">${head}${due}${some.length ? `<div class="pr-grid">${some.map(courseRow).join("")}</div>` : ""}${quiet}</div>`;
  }

  // ------------------------------------------------------------------ Home: cards due today
  function homeNote() {
    const sm = P.summary;
    const rows = sm ? sm.courses.filter((r) => (r.deck || {}).today) : [];
    if (!rows.length) return `<div id="pr-home"></div>`;
    const each = rows.map((r) => `${esc(r.code)}: ${r.deck.today}`).join(" · ");
    return `<div id="pr-home"><div class="card pr-home"><span class="pr-home-ico">${I("layers")}</span><div class="grow"><b>${esc(t.n(sm.due, "{n} flashcard due today", "{n} flashcards due today"))}</b><div class="dim sm">${each}</div></div>
      <button class="btn primary sm" data-act="${rows.length === 1 ? "fc-study" : "go"}" data-cid="${esc(rows[0].id)}" data-view="practice">${t("Study now")}</button></div></div>`;
  }

  // ------------------------------------------------------------------ the course's Practice tab
  function tab(c) {
    const p = P.course[c.id];
    if (!p) {
      if (P.failed[c.id]) return `<div class="callout error">${I("alert-circle")}<div>${esc(P.failed[c.id])} <button class="link" data-act="pr-reload" data-cid="${esc(c.id)}">${t("Try again")}</button></div></div>`;
      loadCourse(c.id);
      return skeleton();
    }
    return `<div class="pr-tab">${quizzesHTML(c, p)}${cardsHTML(c, p)}${stressedHTML(c, p)}</div>`;
  }
  const tabCount = (cid) => (((P.course[cid] || {}).deck || {}).today) || ((((P.summary || {}).courses || []).find((r) => r.id === cid) || {}).deck || {}).today || 0;

  function quizScope(q) {
    const bits = [q.days && q.days.length === 1 && !(q.weeks || []).length ? t("one lecture") : weekSpan(q.weeks)];
    if (q.focus) bits.push(t("for {name}", { name: q.focus }));
    if (q.questions) bits.push(t.n(q.questions, "{n} question", "{n} questions"));
    return bits.join(" · ");
  }
  // a quiz or a card set being made: its bar and step (changed where they are while it runs: inPlace)
  const progHTML = (x, extra = "") => `<div class="pr-prog" data-id="${esc(x.id)}"><span class="bar"><i style="width:${Math.round(100 * (x.progress || 0))}%"></i></span>
    <span class="dim sm">${SPIN()} <span class="pr-msg">${esc(x.message || t("Waiting…"))}</span></span>${extra}</div>`;
  function quizRow(q) {
    const busy = ["running", "queued"].includes(q.status);
    if (busy) {
      return `<div class="pr-quiz busy"><div class="grow"><b>${esc(q.title || t("Writing your quiz…"))}</b><div class="dim sm">${esc(quizScope(q))}</div>
        ${progHTML(q)}${St.planWait ? St.planWait(q) : ""}
        <div class="dim sm">${t("Runs in the background, so you can keep using the app.")}</div></div>
        <button class="btn ghost sm" data-act="plan-stop" data-id="${esc(q.id)}">${I("stop", "sm")} ${t("Stop")}</button></div>`;
    }
    if (q.status !== "done") {
      const why = q.status === "error" ? `<span class="err-txt">${I("alert-circle", "xs")} ${esc(q.error || t("failed"))}</span>` : esc(q.message || t("Stopped"));
      return `<div class="pr-quiz"><div class="grow"><b>${esc(q.title || t("Practice quiz"))}</b><div class="dim sm">${esc(quizScope(q))}</div><div class="sm">${why}</div></div>
        <div class="pr-qacts"><button class="btn sm" data-act="plan-resume" data-id="${esc(q.id)}">${I("retry", "sm")} ${t("Resume")}</button>
        <button class="btn ghost icon sm" data-act="qz-delete" data-id="${esc(q.id)}" title="${esc(t("Delete this quiz"))}">${I("x", "sm")}</button></div></div>`;
    }
    const goingOn = inProgress(runOf(q.id));
    const best = q.best ? `<span class="pr-best ${q.best.score / Math.max(1, q.best.total) >= 0.8 ? "good" : ""}">${esc(t("Best: {score} of {total}", { score: score(q.best.score), total: q.best.total }))}</span>` : "";
    return `<div class="pr-quiz"><div class="grow"><b>${esc(q.title || t("Practice quiz"))}</b><div class="dim sm">${esc(quizScope(q))} · ${esc(dmw(q.created_at))}</div>
        ${q.tries ? `<div class="pr-tries sm">${best}<span class="dim">${esc(t.n(q.tries, "{n} try", "{n} tries"))}${q.last ? ` · ${esc(t("last: {score} of {total}", { score: score(q.last.score), total: q.last.total }))}` : ""}</span></div>` : ""}</div>
      <div class="pr-qacts"><button class="btn primary sm" data-act="qz-play" data-id="${esc(q.id)}">${I("play", "sm")} ${goingOn ? t("Continue") : q.tries ? t("Practise again") : t("Practise")}</button>
        <button class="btn ghost icon sm" data-act="menu-qz" data-id="${esc(q.id)}" data-cid="${esc(q.course_id)}" title="${esc(t("More"))}">${I("more", "sm")}</button></div></div>`;
  }
  function quizzesHTML(c, p) {
    const list = p.quizzes.map(quizRow).join("");
    return `<div class="card pr-card"><div class="pr-h">${I("list-checks")}<b>${t("Practice quizzes")}</b><span class="spacer"></span>
      <button class="btn primary sm" data-act="qz-new" data-cid="${esc(c.id)}" ${St.aiBtn(canAI(), true)} title="${esc(canAI() ? t("Exam-style questions from your files and lectures, written by {ai}", { ai: St.aiName() }) : St.aiHint())}">${I("plus", "sm")} ${t("New quiz")}</button></div>
      ${list ? `<div class="pr-list">${list}</div>` : `<p class="pr-empty">${esc(t("No quizzes yet. Pick the weeks (or the next quiz or midterm) and {ai} writes exam-style questions from your files and lectures, each saying where it comes from. It takes a few minutes.", { ai: St.aiName() }))}</p>`}</div>`;
  }
  // why a card set failed, in the language of now: cards.py keeps its own reasons as the English words t() looks up,
  // with {ai} to fill in (any other reason, such as the AI's own, stays as it was said)
  const setError = (x) => t("Cards from files: {error}", { error: x.error ? t(x.error, { ai: St.aiName() }) : t("failed") });
  function cardsHTML(c, p) {
    const d = p.deck || {};
    const sets = (p.card_sets || []).filter((x) => x.status !== "done");
    const making = sets.map((x) => ["running", "queued"].includes(x.status)
      ? progHTML(x, `<button class="btn ghost sm" data-act="plan-stop" data-id="${esc(x.id)}">${t("Stop")}</button>`) + (St.planWait ? St.planWait(x) : "")
      : `<div class="pr-prog"><span class="sm ${x.status === "error" ? "err-txt" : "dim"}">${esc(x.status === "error" ? setError(x) : t("Cards from files: stopped"))}</span>
          <button class="btn ghost sm" data-act="plan-resume" data-id="${esc(x.id)}">${t("Resume")}</button><button class="btn ghost icon sm" data-act="fc-set-delete" data-id="${esc(x.id)}" title="${esc(t("Delete"))}">${I("x", "sm")}</button></div>`).join("");
    const lectures = t.n(d.lecture_days || 0, "{n} lecture", "{n} lectures");
    const from = !d.total ? "" : d.lecture_days && d.files ? t("From the flashcards of {lectures} and from files ({files} cards). A few minutes a day is enough.", { lectures, files: d.files })
      : d.lecture_days ? t("From the flashcards of {lectures}. A few minutes a day is enough.", { lectures })
        : t("Made from files you picked. A few minutes a day is enough.");
    const empty = `<p class="pr-empty">${esc(t("No flashcards yet. They come from each lecture's flashcards (open a lecture, then Study notes, then Flashcards) and from files you pick here."))}</p>`;
    return `<div class="card pr-card"><div class="pr-h">${I("layers")}<b>${t("Flashcards")}</b>${d.total ? `<span class="dim">${esc(t.n(d.total, "{n} card", "{n} cards"))}</span>` : ""}<span class="spacer"></span>
        ${d.today ? `<span class="pill due">${esc(t.n(d.today, "{n} to study today", "{n} to study today"))}</span>` : d.total ? `<span class="pill done">${I("check")}${t("Done for today")}</span>` : ""}</div>
      ${d.total ? `<p class="pr-sub">${esc(from)}${d.learnt ? ` ${esc(t.n(d.learnt, "{n} card learnt.", "{n} cards learnt."))}` : ""}</p>` : empty}
      ${making}
      <div class="pr-acts"><button class="btn primary" data-act="fc-study" data-cid="${esc(c.id)}" ${d.today ? "" : "disabled"}>${I("play", "sm")} ${d.today ? esc(t.n(d.today, "Study {n} card", "Study {n} cards")) : t("Nothing to study today")}</button>
        <button class="btn" data-act="fc-make" data-cid="${esc(c.id)}" ${St.aiBtn(canAI(), true)} title="${esc(canAI() ? t("{ai} makes cards from the files you pick", { ai: St.aiName() }) : St.aiHint())}">${I("ai", "sm")} ${t("Make cards from files")}</button>
        <button class="btn ghost" data-act="fc-export" data-cid="${esc(c.id)}" ${d.total ? "" : "disabled"} title="${esc(t("Your cards as an Anki deck"))}">${I("download", "sm")} ${t("Export to Anki")}</button></div></div>`;
  }

  // ---- what the lecturer stressed
  const KIND = () => ({ exam: [t("Exam"), "cap"], stressed: [t("Stressed"), "star"], not_needed: [t("Not needed"), "minus"] });
  function markHL(text, hl) {
    if (!hl || !(hl[1] > hl[0])) return esc(text);
    return esc(text.slice(0, hl[0])) + `<mark>${esc(text.slice(hl[0], hl[1]))}</mark>` + esc(text.slice(hl[1]));
  }
  function stItem(it) {
    const [label, icon] = KIND()[it.kind] || KIND().stressed;
    const said = it.n > 1 ? ` · ${esc(t("said {n} times", { n: it.n }))}` : "";
    return `<button class="alert-item st-item" data-act="pr-moment" data-day="${esc(it.day_key)}" data-part="${it.part}" data-t="${it.t}" title="${esc(t("Read it in the transcript"))}">
      <span class="cat-chip st-${esc(it.kind)}">${I(icon, "xs")}${esc(label)}</span>
      <span class="alert-main"><span class="alert-text">“${markHL(it.text, it.hl)}”${it.lang === "tr" ? ` <span class="tag-tr">TR</span>` : ""}</span>
      <span class="alert-meta">${I("video", "xs")} ${esc(t("Lecture"))} · ${esc(dmw(it.day))} · ${esc(t("part {n}", { n: it.part }))} · ${esc(it.ts)}${said}</span></span>
      <span class="alert-go">${I("chevron-right", "sm")}</span></button>`;
  }
  function citeChip(items, id) {
    const it = items.find((x) => x.id === id);
    if (!it) return "";
    return `<button class="cite-chip" data-act="pr-moment" data-day="${esc(it.day_key)}" data-part="${it.part}" data-t="${it.t}" title="${esc(it.text)}">${I("video", "xs")} ${esc(dmw(it.day))} · ${esc(it.ts)}</button>`;
  }
  function summaryHTML(c, s, items) {
    if (P.stressing === c.id) return `<div class="st-sum busy">${SPIN()} ${esc(t("{ai} is ranking what the lecturer stressed…", { ai: St.aiName() }))}</div>`;
    const sm = s.summary;
    if (!sm || !(sm.items || []).length) return "";
    const when = sm.at ? t.date(sm.at.slice(0, 10), "dm") : "";
    return `<div class="st-sum"><div class="st-sum-h">${I("cap", "sm")}<b>${t("Know this for the exam")}</b></div>
      <ol>${sm.items.map((x) => `<li><div class="md">${md(x.point)}</div>${x.why ? `<div class="dim sm">${esc(x.why)}</div>` : ""}${(x.cites || []).length ? `<div class="st-cites">${x.cites.map((id) => citeChip(items, id)).join("")}</div>` : ""}</li>`).join("")}</ol>
      <div class="dim sm">${esc(t("Written by AI ({model}) from the quotes below: check it against them.", { model: St.modelLabel ? St.modelLabel(sm.model) : sm.model || "" }))} ${esc(when)}${sm.new ? ` · ${esc(t.n(sm.new, "{n} new moment since: sum it up again to add it.", "{n} new moments since: sum it up again to add them."))}` : ""}</div></div>`;
  }
  function stressedHTML(c, p) {
    const s = p.stressed || { weeks: [], not_needed: [], counts: {}, total: 0 };
    const items = [...s.weeks.flatMap((w) => w.items), ...s.not_needed];
    const f = P.week[c.id] || "all";
    const key = (w) => (w.no == null ? "other" : String(w.no));
    const shown = s.weeks.filter((w) => f === "all" || key(w) === f);
    const chips = s.weeks.length > 1 ? `<div class="fchips">${[["all", t("All weeks"), s.total], ...s.weeks.map((w) => [key(w), w.no == null ? t("Other") : t("W{n}", { n: w.no }), w.items.length])]
      .map(([k, l, n]) => `<button class="fchip ${f === k ? "on" : ""}" data-act="st-week" data-cid="${esc(c.id)}" data-week="${esc(k)}">${esc(l)} <span>${n}</span></button>`).join("")}</div>` : "";
    const list = shown.map((w) => {         // the moments that stand out; the rest (said in passing) one click away
      const open = P.more.has(`${c.id}|${key(w)}`), items = w.items.filter((it) => open || it.top || w.items.every((x) => !("top" in x)));
      const more = w.items.length - items.length;
      return `<div class="st-week"><div class="st-week-h"><b>${w.no == null ? t("Other") : t("Week {n}", { n: w.no })}</b>${w.topic ? ` <span class="dim">· ${esc(w.topic)}</span>` : ""}</div>
        ${items.map(stItem).join("") || `<div class="dim sm st-quiet">${esc(t("Only said in passing this week."))}</div>`}
        ${more ? `<button class="link sm st-more" data-act="st-more" data-key="${esc(`${c.id}|${key(w)}`)}">${esc(t.n(more, "Show {n} more moment said in passing", "Show {n} more moments said in passing"))}</button>`
          : open && w.items.some((it) => !it.top) ? `<button class="link sm st-more" data-act="st-more" data-key="${esc(`${c.id}|${key(w)}`)}">${esc(t("Show only those that stand out"))}</button>` : ""}</div>`;
    }).join("");
    const nn = s.not_needed.filter((it) => f === "all" || String(it.week == null ? "other" : it.week) === f);
    const ai = St.isChatGPT() && St.effortInline && canAI() ? St.effortInline("st", "chat") : "";
    const sumBtn = s.total ? `${ai}<button class="btn sm" data-act="st-sum" data-cid="${esc(c.id)}" ${St.aiBtn(canAI(), true) || (P.stressing === c.id ? "disabled" : P.stressing ? St.whyOff(t("{ai} is still ranking another course's moments. Try again when it's done.", { ai: St.aiName() })) : "")} title="${esc(canAI() ? t("One short request to {ai}: a ranked list of what to know, each point with the moments it comes from", { ai: St.aiName() }) : St.aiHint())}">${I("ai", "sm")} ${s.summary ? t("Sum it up again") : t("Sum it up with AI")}</button>` : "";
    const left = s.untranscribed ? `<div class="note-sm">${I("info", "sm")}<span>${esc(t.n(s.untranscribed, "{n} lecture of yours isn't transcribed yet, so what was said there isn't here.", "{n} lectures of yours aren't transcribed yet, so what was said there isn't here."))}</span></div>` : "";
    return `<div class="card pr-card"><div class="pr-h">${I("star")}<b>${t("What the lecturer stressed")}</b>${s.total ? `<span class="dim">${esc(t.n(s.total, "{n} moment", "{n} moments"))}</span>` : ""}<span class="spacer"></span>${sumBtn}</div>
      <p class="pr-sub">${esc(t("Found in your transcribed lectures, free and without AI: when the lecturer said something is important, a common mistake, or on the exam. Repeats are shown once. Click one to read it in the transcript."))}</p>
      ${summaryHTML(c, s, items)}${chips}
      ${list || (s.total ? "" : `<p class="pr-empty">${esc(t("Nothing stressed in the transcribed lectures yet."))}</p>`)}
      ${nn.length ? `<div class="st-week st-not"><div class="st-week-h"><b>${t("The lecturer said these are not needed")}</b></div>${nn.map(stItem).join("")}</div>` : ""}
      ${left}</div>`;
  }

  // ------------------------------------------------------------------ opening a source
  async function openMoment(day, part, at) {
    if (P.run && !P.run.saved && P.run.pos >= 0) toast(t("Your quiz waits where you left it."), "info", { label: t("Back to the quiz"), act: "qz-resume", data: {} });
    closeModal();
    await St.openDay(day);
    setTimeout(() => St.jumpTo(part || null, at || 0), 60);
  }
  const V = { cid: "", id: "", page: 1, back: null };      // the page viewer
  async function showPage(cid, id, page, back) {
    Object.assign(V, { cid, id, page: +page || 1, back });
    modal(`<div class="pr-src"><div class="fc-top"><b>${I("file-text", "sm")} ${t("Source")}</b></div>${skeleton()}</div>`);
    $(".modal").classList.add("pr-src-modal");
    const r = await call("practice_source", cid, id, V.page);
    if (V.id !== id) return;
    if (!r.ok) { toast(r.error, "error"); if (back) back(); else closeModal(); return; }
    const body = r.picture ? `<img class="pr-src-img" src="${esc(r.picture)}" alt="${esc(t("Page {n}", { n: r.page }))}">`
      : `<div class="pr-src-text">${esc(r.text || t("This page has no text."))}</div>`;
    modal(`<div class="pr-src"><div class="fc-top"><b>${I("file-text", "sm")} ${esc(r.name)}</b><span class="dim sm">${esc(t("page {n} of {total}", { n: r.page, total: r.pages }))}</span></div>
      <div class="pr-src-body">${body}</div>
      <div class="actions"><button class="btn ghost" data-act="pr-page-go" data-d="-1" ${r.page > 1 ? "" : "disabled"}>${I("chevron-left", "sm")} ${t("Previous page")}</button>
        <button class="btn ghost" data-act="pr-page-go" data-d="1" ${r.page < r.pages ? "" : "disabled"}>${t("Next page")} ${I("chevron-right", "sm")}</button>
        <span class="spacer"></span><button class="btn" data-act="mat-open" data-id="${esc(id)}">${I("external", "sm")} ${t("Open the file")}</button>
        <button class="btn primary" data-act="pr-page-back" autofocus>${back ? t("Back") : t("Close")}</button></div></div>`);
    $(".modal").classList.add("pr-src-modal");
  }
  function sourceHTML(src, cid) {
    if (!src) return "";
    const found = src.found ? ` <span class="dim">${esc(t("(found by its words)"))}</span>` : "";
    if (src.kind === "file") {
      return `<button class="qz-src" data-act="pr-page" data-cid="${esc(cid)}" data-id="${esc(src.id)}" data-page="${src.page}">${I("file-text", "xs")}<span>${esc(t("From {file}, page {page}", { file: src.name, page: src.page }))}${found}</span>${I("chevron-right", "xs")}</button>`;
    }
    const where = src.ts ? t("From the lecture of {day}, part {part}, {time}", { day: dmw(src.date), part: src.part, time: src.ts }) : t("From the lecture of {day}", { day: dmw(src.date) });
    return `<button class="qz-src" data-act="pr-moment" data-day="${esc(src.day_key)}" data-part="${src.part || 1}" data-t="${src.t || 0}">${I("video", "xs")}<span>${esc(where)}${found}</span>${I("chevron-right", "xs")}</button>`;
  }

  // ------------------------------------------------------------------ the new-quiz dialog
  let Q = null;
  const nextExam = (r) => (r.assessments || []).find((e) => e.ahead && (e.weeks || []).length);
  // the week a dialog starts at: this week, or the latest one so far, that has something to read
  function latestReady(weeks, ready) {
    const upTo = weeks.filter((w) => w.past || w.current), pick = [...upTo].reverse().find(ready) || weeks.find(ready);
    return pick || weeks.find((w) => w.current) || upTo[upTo.length - 1] || weeks[0];
  }
  function lectures() {       // the transcribed lectures (your section's), newest first
    return Q.opts.weeks.flatMap((w) => w.days.filter((d) => d.ready && d.mine).map((d) => ({ ...d, week: w.no }))).sort((a, b) => b.date.localeCompare(a.date));
  }
  function pickWeek(no, on) {   // as the study pack's dialog: a week's files on this PC and your section's lectures
    const w = Q.opts.weeks.find((x) => x.no === no); if (!w) return;
    if (on) Q.weeks.add(no); else Q.weeks.delete(no);
    const kept = new Set(Q.opts.weeks.filter((x) => Q.weeks.has(x.no)).flatMap((x) => x.files.map((f) => f.id)));
    w.files.forEach((f) => { if (on && f.ready) Q.files.add(f.id); else if (!on && !kept.has(f.id)) Q.files.delete(f.id); });
    w.days.forEach((d) => { if (on && d.ready && d.mine) Q.days.add(d.day_key); else if (!on) Q.days.delete(d.day_key); });
  }
  function setMode(mode) {
    Q.mode = mode; Q.weeks = new Set(); Q.files = new Set(); Q.days = new Set();
    if (mode === "next") (nextExam(Q.opts) || { weeks: [] }).weeks.forEach((n) => pickWeek(n, true));
    else if (mode === "weeks") {
      const cur = latestReady(Q.opts.weeks, (w) => w.files.some((f) => f.ready) || w.days.some((d) => d.ready && d.mine));
      if (cur) pickWeek(cur.no, true);
    } else {
      const days = lectures();
      Q.day = days.some((d) => d.day_key === Q.day) ? Q.day : (days[0] || {}).day_key || "";
    }
  }
  function qScope() {
    const e = Q.mode === "next" ? nextExam(Q.opts) : null;
    const day = Q.mode === "lecture" ? lectures().find((d) => d.day_key === Q.day) : null;
    return { kind: "quiz", weeks: day ? [day.week] : [...Q.weeks].sort((a, b) => a - b), files: day ? [] : [...Q.files],
      days: day ? [day.day_key] : [...Q.days], picked: true, focus: e ? e.name : "", los: e ? e.los : [], questions: Q.count,
      qtypes: Q.kinds, topics: Q.topics, model: Q.model, effort: Q.effort || "" };
  }
  async function quizDialog(cid) {
    const courses = S.courses.filter((c) => c.code);
    cid = cid || S.view.courseId || (courses[0] || {}).id;
    if (!cid) { toast(t("Sync your courses first."), "error"); return; }
    Q = { cid, opts: null, mode: "", weeks: new Set(), files: new Set(), days: new Set(), day: "", count: +(S.settings || {}).quiz_questions || 10,     // (Settings → Practice)
      kinds: "mixed", topics: "", model: "", effort: (Q && Q.effort) || "", est: null, starting: false };
    modal(`<div class="cp-scroll"><h2>${I("list-checks")} ${t("New practice quiz")}</h2>${skeleton()}</div>`);
    $(".modal").classList.add("pd", "pk", "qz-dlg");
    const r = await call("quiz_options", cid);
    if (!Q || Q.cid !== cid) return;
    if (!r.ok) { toast(r.error, "error"); closeModal(); return; }
    Q.opts = r; Q.model = r.model || "sonnet";
    setMode(nextExam(r) ? "next" : "weeks");
    drawQuiz(); estimateQuiz();
  }
  function quizCost() {
    const e = Q.est, ai = Q.opts.ai || {};
    if (!(ai.available && ai.can_plans !== false)) return `<span class="err-txt">${I("alert-circle", "sm")} ${(S.system.ai || {}).provider === "local" ? t("Quizzes need ChatGPT or Claude (the offline model is too small for them).") : t("Set up ChatGPT or Claude in Settings → AI first.")}</span>`;
    const sc = qScope();
    if (!sc.files.length && !sc.days.length) return `<span class="dim">${t("Pick at least one file or lecture.")}</span>`;
    if (!e) return `<span class="dim">${SPIN()} ${t("Estimating…")}</span>`;
    const more = St.effortCostNote ? St.effortCostNote(e, { kind: "quiz", effort: Q.effort }) : "";    // (a heavy level's note)
    const big = e.main_input + e.main_output >= 40000;        // (a midterm's eight weeks: about 70k)
    return `<div class="pk-heavy light">${I("info", "sm")}<div><b>${big ? t.html("Uses part of your {ai} plan.", { ai: St.aiName() }) : t.html("Uses a little of your {ai} plan.", { ai: St.aiName() })}</b>
      ${esc(t("About {k} tokens in one request, {minutes}.", { k: St.fmtK(e.main_input + e.main_output), minutes: e.minutes }))}
      ${St.docLangLine ? St.docLangLine(e) : ""}</div></div>${more}`;
  }
  function drawQuiz() {
    const r = Q.opts, courses = S.courses.filter((c) => c.code), e = nextExam(r);
    const old = $(".modal .cp-scroll"), keep = old ? old.scrollTop : 0;
    const mode = (k, title, desc, ok = true) => `<button type="button" class="cp-mode ${Q.mode === k ? "on" : ""}" data-act="qz-mode" data-val="${k}" ${ok ? "" : "disabled"}><span class="cp-mode-t">${title}</span><span class="cp-mode-d">${desc}</span></button>`;
    const los = e && e.los.length ? ` · LO${e.los.join(", LO")}` : "";
    const modes = `<div class="cp-modes">${mode("next", esc(e ? t("For {name}", { name: e.name }) : t("The next exam")), esc(e ? t("The weeks it covers: {weeks}", { weeks: weekSpan(e.weeks) }) + los : t("No quiz or midterm announced yet")), !!e)}
      ${mode("weeks", esc(t("Weeks you pick")), esc(t("Their files and lectures")))}
      ${mode("lecture", esc(t("One lecture")), esc(t("A transcribed lecture, with the slides from its video")), lectures().length > 0)}</div>`;
    const item = (kind, id, on, ready, label, sub) => `<label class="pk-item ${ready ? "" : "off"}">
        <input type="checkbox" class="check" data-act="qz-item" data-kind="${kind}" data-id="${esc(id)}" ${on ? "checked" : ""} ${ready ? "" : "disabled"}>
        ${I(kind === "file" ? "file-text" : "video", "sm")}<span class="grow">${esc(label)}</span><span class="dim sm">${sub}</span></label>`;
    const picked = r.weeks.filter((w) => Q.weeks.has(w.no));
    const material = picked.map((w) => `<div class="pk-week"><div class="pk-week-h">${t("Week {n}", { n: w.no })} <span class="dim">${esc(w.topic || "")}</span></div>
        ${w.files.map((f) => item("file", f.id, Q.files.has(f.id), f.ready, f.name, f.ready ? t.n(f.pages, "{n} page", "{n} pages") : f.downloaded ? t("not read yet") : t("not on this PC"))).join("")}
        ${w.days.filter((d) => d.mine || Q.days.has(d.day_key)).map((d) => item("day", d.day_key, Q.days.has(d.day_key), d.ready, `${d.label} · ${d.kind}`, d.ready ? t("transcribed") : t("not transcribed"))).join("")}
        ${!w.files.length && !w.days.length ? `<div class="dim sm pk-none">${t("Nothing for this week yet.")}</div>` : ""}</div>`).join("");
    const where = Q.mode === "lecture"
      ? `<div class="field"><label>${t("Which lecture?")}</label><select class="input" data-act="qz-day">${lectures().map((d) => `<option value="${esc(d.day_key)}" ${d.day_key === Q.day ? "selected" : ""}>${esc(t("Week {n}", { n: d.week }))} · ${esc(d.label)} · ${esc(d.kind)}</option>`).join("")}</select></div>`
      : `<div class="field"><label class="cp-label">${t("Weeks")} <span class="dim sm">${t("{n} picked", { n: Q.weeks.size })}</span></label>
          <div class="pk-weeks">${r.weeks.map((w) => `<button type="button" class="pk-wk ${Q.weeks.has(w.no) ? "on" : ""} ${w.current ? "now" : ""}" data-act="qz-week" data-no="${w.no}" title="${esc(w.topic || "")}"><b>${t("W{n}", { n: w.no })}</b><span>${esc(w.topic || "—")}</span></button>`).join("")}</div></div>
        ${picked.length ? `<div class="field"><label class="cp-label">${t("Material")} <span class="dim sm">${t.n(Q.files.size, "{n} file", "{n} files")} · ${t.n(Q.days.size, "{n} lecture", "{n} lectures")}</span></label><div class="pk-material">${material}</div></div>` : ""}`;
    const kinds = [["mixed", t("Mixed")], ["mc", t("Multiple choice")], ["short", t("Short answers")], ...(r.quant ? [["problems", t("Worked problems")]] : [])];
    const models = [["sonnet", t("Sonnet (recommended)")], ["haiku", t("Haiku (lightest)")], ["opus", t("Opus (best, heavy)")]];
    const sc = qScope(), ready = (sc.files.length || sc.days.length) && canAI() && !Q.starting;
    modal(`<div class="cp-scroll">
      <h2>${I("list-checks")} ${t("New practice quiz")}</h2>
      <p class="pk-lead">${t("Exam-style questions from your course material: multiple choice with why each wrong answer is wrong, short answers, and worked problems for maths and engineering. Every question says where it comes from.")}</p>
      <div class="field"><label>${t("Course")}</label><select class="input" data-act="qz-course">${courses.map((c) => `<option value="${esc(c.id)}" ${c.id === Q.cid ? "selected" : ""}>${esc(c.code)} — ${esc(c.display_name)}</option>`).join("")}</select></div>
      <div class="field"><label>${t("What should it cover?")}</label>${modes}</div>
      ${where}
      <div class="cp-opts">
        <div class="cp-opt"><div class="t">${t("Questions")}</div><span class="seg">${[5, 10, 15, 20].map((n) => `<button type="button" class="${Q.count === n ? "on" : ""}" data-act="qz-count" data-n="${n}">${n}</button>`).join("")}</span></div>
        <div class="cp-opt"><div class="t">${t("Kinds")}</div><select class="input" data-act="qz-kinds">${kinds.map(([k, l]) => `<option value="${k}" ${Q.kinds === k ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></div>
        ${St.isChatGPT() ? "" : `<div class="cp-opt"><div class="t">${t("Model")}</div><select class="input" data-qz="model">${models.map(([k, l]) => `<option value="${k}" ${Q.model === k ? "selected" : ""}>${l}</option>`).join("")}</select></div>`}
        ${St.effortField ? St.effortField("qz", "plan", Q.effort, { attrs: 'data-qz="effort"', why: "" }) : ""}
        <div class="cp-opt qz-wide"><div class="t">${t("Anything to focus on? (optional)")}</div><input class="input" data-qz="topics" value="${esc(Q.topics)}" placeholder="${esc(t("e.g. more on Bayes' theorem, fewer definitions"))}"></div>
      </div></div>
      <div class="cp-foot"><div class="cp-cost" id="qz-cost">${quizCost()}</div>
        <div class="cp-btns"><button class="btn" data-act="modal-close">${t("Cancel")}</button>
          <button class="btn primary" data-act="qz-go" ${ready ? "" : "disabled"}>${Q.starting ? `${SPIN()} ${t("Starting…")}` : `${I("list-checks", "sm")} ${t("Write the quiz")}`}</button></div></div>`);
    $(".modal").classList.add("pd", "pk", "qz-dlg");
    const nb = $(".modal .cp-scroll"); if (nb) nb.scrollTop = keep;
  }
  function readQuiz() { document.querySelectorAll(".modal [data-qz]").forEach((el) => { Q[el.dataset.qz] = el.value; }); }
  let estTimer = null;
  function estimateQuiz() {
    clearTimeout(estTimer);
    Q.est = null;
    const box = $("#qz-cost"); if (box) box.innerHTML = quizCost();
    const sc = qScope();
    if (!sc.files.length && !sc.days.length) return;
    const asked = Q;
    estTimer = setTimeout(async () => {
      const r = await call("quiz_estimate", asked.cid, sc);
      if (asked !== Q) return;
      Q.est = r.ok ? r.estimate : null;
      const el = $("#qz-cost"); if (el) el.innerHTML = quizCost();
    }, 300);
  }

  // ------------------------------------------------------------------ practising a quiz
  async function playQuiz(id, only) {
    const r = await call("quiz_get", id);
    if (!r.ok) { toast(r.error, "error"); return; }
    const quiz = r.quiz, qs = ((quiz.result || {}).questions) || [];
    if (!qs.length) { toast(t("This quiz has no questions."), "error"); return; }
    const kept = runOf(id), resume = kept && !kept.saved && !only;
    if (P.run && P.run.id !== id && inProgress(P.run)) P.runs[P.run.id] = P.run;     // the one left: kept for its Continue
    delete P.runs[id];
    if (resume) P.run = kept;
    else {
      const pick = only && only.length ? qs.filter((q) => only.includes(q.n)) : qs;
      P.run = { id, cid: quiz.course_id, order: pick.map((q) => qs.indexOf(q)), pos: only && only.length ? 0 : -1, answers: {},
        picked: null, checked: false, text: "", only: only || [], started: new Date().toISOString(), saved: false };
    }
    P.run.quiz = quiz; P.run.attempts = r.attempts || [];
    drawRun();
    St.onModalClose = () => { if (onTab(quiz.course_id)) renderView(); };     // closed: its row says Continue
  }
  const KINDS = () => ({ mc: t("Multiple choice"), short: t("Short answer"), problem: t("Worked problem") });
  function introHTML(R) {
    const res = R.quiz.result || {}, cov = res.coverage || {}, k = cov.kinds || {};
    const kinds = [k.mc ? t.n(k.mc, "{n} multiple choice", "{n} multiple choice") : "", k.short ? t.n(k.short, "{n} short answer", "{n} short answers") : "",
      k.problem ? t.n(k.problem, "{n} worked problem", "{n} worked problems") : ""].filter(Boolean).join(" · ");
    const covers = [], left = [];
    const topics = (cov.weeks || []).map((w) => w.topic).filter(Boolean).join("; ");
    const span = weekSpan((cov.weeks || []).map((w) => w.no));             // in the page's language ("7. hafta")
    covers.push(t("{weeks}: {topics}", { weeks: span.charAt(0).toUpperCase() + span.slice(1), topics: topics || "—" }));
    const from = [...(cov.files || []), ...(cov.lectures || []).map((d) => t("the lecture of {day}", { day: dmw(d) }))];
    if (from.length) covers.push(t("Questions from {list}", { list: t.list(from) }));
    if (cov.focus && cov.focus.name) {
      covers.push((cov.los || []).length ? t("For {name}: {los}", { name: cov.focus.name, los: cov.los.map((x) => `LO${x.no} (${t.n(x.n, "{n} question", "{n} questions")})`).join(", ") })
        : t("For {name}", { name: cov.focus.name }));
    }
    if (cov.stressed_total) covers.push(t("Tests {hit} of the {total} moments the lecturer stressed in these weeks", { hit: cov.stressed_hit || 0, total: cov.stressed_total }));
    if ((cov.materials_missing || []).length) left.push(t("Files not on this PC: {list}", { list: cov.materials_missing.join(", ") }));
    if ((cov.days_missing || []).length) left.push(t("Lectures not transcribed: {list}", { list: cov.days_missing.map(dmw).join(", ") }));
    if ((cov.files_cut || []).length) left.push(t("Too long to read whole, so only their first pages: {list}", { list: cov.files_cut.join(", ") }));
    if ((cov.lectures_cut || []).length) left.push(t("Only parts of these lectures fitted: {list}", { list: cov.lectures_cut.map(dmw).join(", ") }));
    if ((cov.topics_missing || []).length) left.push(t("No question on: {list}", { list: cov.topics_missing.join(", ") }));
    const los0 = (cov.los || []).filter((x) => !x.n).map((x) => `LO${x.no}`);
    if (los0.length) left.push(t("No question tests {los}", { los: los0.join(", ") }));
    if (cov.no_source) left.push(t.n(cov.no_source, "{n} question has no source the app could find", "{n} questions have no source the app could find"));
    const tries = (R.attempts || []).slice(0, 4).map((a) => `<li>${esc(t("{score} of {total}", { score: score(a.score), total: a.total }))} <span class="dim">· ${esc(t.ago(a.finished_at))}${a.only.length ? ` · ${esc(t("only the missed ones"))}` : ""}</span></li>`).join("");
    return `<div class="qz qz-intro"><h2>${I("list-checks")} ${esc(R.quiz.title || t("Practice quiz"))}</h2>
      <p class="lead">${esc(t.n(R.order.length, "{n} question", "{n} questions"))}${kinds ? ` · ${esc(kinds)}` : ""}</p>
      <div class="qz-cover"><div class="qz-cover-h">${I("check", "sm")}<b>${t("What it covers")}</b></div><ul>${covers.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
      ${left.length ? `<div class="qz-cover left"><div class="qz-cover-h">${I("info", "sm")}<b>${t("What it left out")}</b></div><ul>${left.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}
      ${tries ? `<div class="qz-tries"><div class="dim sm">${t("Your tries")}</div><ul>${tries}</ul></div>` : ""}
      <p class="dim sm">${t("Written by AI from your course material: if a question looks wrong, open its source to check.")}</p>
      <div class="actions"><button class="btn" data-act="modal-close">${t("Close")}</button><button class="btn primary" data-act="qz-start" autofocus>${I("play", "sm")} ${t("Start")}</button></div></div>`;
  }
  function feedbackMC(q, picked) {
    const right = picked === q.answer;
    const mine = !right && picked != null && (q.wrong || [])[picked] ? `<div class="qz-why-wrong"><b>${esc(t("Why {letter} is wrong:", { letter: LETTERS[picked] }))}</b> ${mdLine(q.wrong[picked])}</div>` : "";
    const others = (q.wrong || []).map((w, i) => (w && i !== q.answer && i !== picked ? `<li><b>${LETTERS[i]}</b>: ${mdLine(w)}</li>` : "")).join("");
    return `<div class="qz-fb ${right ? "ok" : "no"}"><div class="qz-fb-h">${I(right ? "check-circle" : "x-circle", "sm")}<b>${right ? t("Right!") : picked == null ? esc(t("Skipped. The answer is {letter}.", { letter: LETTERS[q.answer] })) : esc(t("Not quite. The answer is {letter}.", { letter: LETTERS[q.answer] }))}</b></div>
      ${q.why ? `<div class="md">${md(q.why)}</div>` : ""}${mine}
      ${others ? `<details class="qz-others"><summary>${t("Why the other options are wrong")}</summary><ul>${others}</ul></details>` : ""}</div>`;
  }
  function modelHTML(q) {
    if (q.type === "problem") {
      return `<div class="qz-fb model"><div class="qz-fb-h">${I("check-circle", "sm")}<b>${t("Solution")}</b></div>
        ${(q.steps || []).length ? `<ol class="qz-steps">${q.steps.map((s) => `<li class="md">${md(s)}</li>`).join("")}</ol>` : ""}
        ${q.model ? `<div class="qz-final"><b>${t("Answer:")}</b> <span class="md">${mdLine(q.model)}</span></div>` : ""}</div>`;
    }
    return `<div class="qz-fb model"><div class="qz-fb-h">${I("check-circle", "sm")}<b>${t("A good answer")}</b></div>${q.model ? `<div class="md">${md(q.model)}</div>` : ""}
      ${(q.points || []).length ? `<div class="dim sm qz-points-h">${t("A full answer says:")}</div><ul class="qz-points">${q.points.map((x) => `<li class="md">${md(x)}</li>`).join("")}</ul>` : ""}
      ${q.why ? `<div class="md">${md(q.why)}</div>` : ""}</div>`;
  }
  function questionHTML(R, q) {
    const n = R.pos + 1, total = R.order.length;
    const head = `<div class="fc-top"><b>${I("list-checks", "sm")} ${esc(R.quiz.title || t("Practice quiz"))}</b><span class="dim sm">${t("Question {n} of {total}", { n, total })}</span></div>
      <div class="fc-bar"><i style="width:${Math.round((100 * R.pos) / total)}%"></i></div>`;
    const done = R.answers[q.n];
    let body = "", acts = "";
    if (q.type === "mc") {
      body = `<div class="qz-opts" role="radiogroup">${q.options.map((o, i) => {
        const st = !R.checked ? (R.picked === i ? "picked" : "") : i === q.answer ? "right" : R.picked === i ? "wrong" : "dim";
        const mark = R.checked && i === q.answer ? I("check", "sm") : R.checked && R.picked === i ? I("x", "sm") : "";
        return `<button type="button" class="qz-opt ${st}" data-act="qz-pick" data-i="${i}" ${R.checked ? "disabled" : ""} role="radio" aria-checked="${R.picked === i}"><span class="qz-l">${LETTERS[i]}</span><span class="md grow">${md(o)}</span>${mark}</button>`;
      }).join("")}</div>${R.checked ? feedbackMC(q, R.picked) : ""}`;
      acts = R.checked ? `<button class="btn primary" data-act="qz-next" autofocus>${R.pos + 1 >= total ? t("See my score") : t("Next question")}</button>`
        : `<button class="btn ghost" data-act="qz-skip">${t("Skip")}</button><button class="btn primary" data-act="qz-check" ${R.picked == null ? "disabled" : ""}>${t("Check my answer")}</button>`;
    } else {
      body = R.checked ? `${R.text ? `<div class="qz-your"><div class="dim sm">${t("Your answer")}</div><div class="qz-your-t">${esc(R.text)}</div></div>` : ""}${modelHTML(q)}`
        : `<textarea class="input qz-input" id="qz-text" rows="${q.type === "problem" ? 5 : 3}" placeholder="${esc(q.type === "problem" ? t("Work it out on paper or here, then look at the solution.") : t("Write your answer (or think it through), then check."))}">${esc(R.text)}</textarea>`;
      acts = !R.checked ? `<button class="btn ghost" data-act="qz-skip">${t("Skip")}</button><button class="btn primary" data-act="qz-check">${q.type === "problem" ? t("Show the solution") : t("Check my answer")}</button>`
        : done ? `<button class="btn primary" data-act="qz-next" autofocus>${R.pos + 1 >= total ? t("See my score") : t("Next question")}</button>`
          : `<span class="dim sm qz-self-q">${t("How did you do?")}</span><button class="btn" data-act="qz-self" data-got="0">${t("I didn't know it")}</button><button class="btn" data-act="qz-self" data-got="0.5">${t("Partly")}</button><button class="btn primary" data-act="qz-self" data-got="1" autofocus>${t("I got it")}</button>`;
    }
    const level = { easy: t("easy"), medium: t("medium"), hard: t("hard") }[q.level] || "";
    return `<div class="qz">${head}<div class="qz-kind">${esc(KINDS()[q.type] || "")}${level ? ` · ${esc(level)}` : ""}</div>
      <div class="qz-text md">${md(q.q)}</div>${body}${R.checked ? sourceHTML(q.source, R.cid) : ""}
      <div class="actions">${acts}</div></div>`;
  }
  function endHTML(R) {
    const qs = R.quiz.result.questions, ids = R.order.map((i) => qs[i].n);
    const got = ids.reduce((s, n) => s + ((R.answers[n] || {}).got || 0), 0);
    const missed = ids.filter((n) => ((R.answers[n] || {}).got || 0) < 1);
    const share = ids.length ? got / ids.length : 0;
    const say = share >= 0.9 ? t("Excellent: you know this well.") : share >= 0.6 ? t("Good. Go over the ones you missed, then try them again.")
      : t("Keep going: read the sources of the ones you missed, then try them again.");
    const rows = R.order.map((i) => {
      const q = qs[i], a = R.answers[q.n] || {}, g = a.got || 0;
      const first = String(q.q).split("\n")[0];                            // its first line, formulas drawn
      return `<li class="${g >= 1 ? "ok" : g > 0 ? "half" : "no"}">${I(g >= 1 ? "check" : g > 0 ? "minus" : "x", "sm")}<span class="qz-res-t md">${mdLine(first)}</span></li>`;
    }).join("");
    return `<div class="qz qz-end"><h2>${I("check-circle")} ${esc(t("You got {score} of {total}", { score: score(got), total: ids.length }))} <span class="dim">${t.pct(share)}</span></h2>
      <p class="lead">${esc(say)}</p><ol class="qz-results">${rows}</ol>
      ${R.saved ? "" : `<p class="dim sm">${SPIN()} ${t("Saving…")}</p>`}
      <div class="actions"><button class="btn" data-act="modal-close">${t("Close")}</button>
        ${missed.length ? `<button class="btn primary" data-act="qz-retry" data-id="${esc(R.id)}" data-only="${missed.join(",")}" autofocus>${I("retry", "sm")} ${esc(t.n(missed.length, "Try {n} missed question", "Try {n} missed questions"))}</button>` : ""}</div></div>`;
  }
  function drawRun() {
    const R = P.run;
    if (!R) return;
    const qs = R.quiz.result.questions;
    if (R.pos < 0) modal(introHTML(R));
    else if (R.pos >= R.order.length) { modal(endHTML(R)); if (!R.saving && !R.saved) saveRun(R); }
    else modal(questionHTML(R, qs[R.order[R.pos]]));
    $(".modal").classList.add("qz-modal");
    math();
  }
  async function saveRun(R) {
    R.saving = true;
    const answers = R.order.map((i) => { const q = R.quiz.result.questions[i], a = R.answers[q.n] || {}; return { n: q.n, got: a.got || 0, chose: a.chose ?? null, skipped: !!a.skipped }; });
    const r = await call("quiz_attempt", R.id, { answers, only: R.only, started_at: R.started });
    R.saving = false;
    if (!r.ok) { toast(r.error, "error"); return; }
    R.saved = true;
    if (P.run === R && R.pos >= R.order.length && $("#overlay .qz-end")) drawRun();
    refresh(R.cid);
  }
  function step(fn) { const R = P.run; if (!R) return; fn(R); drawRun(); }

  // ------------------------------------------------------------------ studying the flashcards
  function ivl(s) {             // when an answer brings the card back: "10 min", "1 day", "3 months"
    const days = Math.round(s / 86400), months = Math.round(s / 2592000);
    if (s < 3600) return t.dur(Math.max(60, s));
    if (s < 86400) return t("{h} h", { h: Math.round(s / 3600) });
    return s < 86400 * 45 ? t.n(days, "{n} day", "{n} days") : t.n(months, "{n} month", "{n} months");
  }
  async function study(cid) {
    const r = await call("cards_session", cid);
    if (!r.ok) { toast(r.error, "error"); return; }
    if (!r.cards.length) { toast(t("Nothing to study in this course today."), "info"); return; }
    P.study = { cid, queue: r.cards.slice(), pos: 0, shown: false, done: 0, again: 0 };
    drawStudy();
  }
  function drawStudy() {
    const X = P.study; if (!X) return;
    // Escape or a click beside it ends the session as Stop does: the counts of what is left today come again
    St.onModalClose = () => { const Y = P.study; P.study = null; if (Y && Y.done) refresh(Y.cid); };
    const c = X.queue[X.pos], code = (St.findCourse(X.cid) || {}).code || "";
    if (!c) {
      modal(`<div class="fc fc-study"><h2>${I("check-circle")} ${t("Done for today")}</h2>
        <p class="lead">${esc(t.n(X.done, "You went through {n} card.", "You went through {n} cards."))} ${esc(t("Each card comes back just before you would forget it, so a few minutes a day keeps it all fresh."))}</p>
        <div class="actions"><button class="btn primary" data-act="fc-close" autofocus>${t("Close")}</button></div></div>`);
      return;
    }
    const left = X.queue.length - X.pos, nx = c.next || {};
    const grade = (g, label, cls) => `<button class="btn fc-g ${cls}" data-act="fc-grade" data-g="${g}" ${g === "good" ? "autofocus" : ""}><b>${label}</b><span>${esc(ivl(nx[g] || 0))}</span></button>`;
    modal(`<div class="fc fc-study"><div class="fc-top"><b>${I("layers", "sm")} ${esc(code)} · ${t("flashcards")}</b><span class="dim sm">${esc(t("{n} left", { n: left }))}${X.done ? ` · ${esc(t("{n} done", { n: X.done }))}` : ""}</span></div>
      <div class="fc-bar"><i style="width:${Math.round((100 * X.pos) / X.queue.length)}%"></i></div>
      <div class="fc-q md">${md(c.q)}</div>
      ${X.shown ? `<div class="fc-a md">${md(c.a)}</div>` : `<div class="fc-hint dim sm">${t("Think of the answer, then show it.")}</div>`}
      <div class="fc-meta">${c.stressed ? `<span class="pill due">${I("star")}${t("Stressed by the lecturer")}</span>` : ""}${!c.state ? `<span class="pill">${t.c("card", "New")}</span>` : ""}${X.shown ? sourceHTML(c.source, X.cid) : ""}</div>
      <div class="actions">${X.shown ? `${grade("again", t("Again"), "again")}${grade("hard", t("Hard"), "hard")}${grade("good", t("Good"), "good")}${grade("easy", t("Easy"), "easy")}`
        : `<button class="btn ghost" data-act="fc-close">${t.c("flashcards", "Stop")}</button><button class="btn primary" data-act="fc-flip" autofocus>${t("Show the answer")}</button>`}</div>
      <div class="fc-keys dim sm">${X.shown ? t("Keys: 1 Again, 2 Hard, 3 Good, 4 Easy") : t("Key: Space shows the answer")}</div></div>`);
    $(".modal").classList.add("fc-modal");
    math();
  }
  async function gradeCard(g) {
    const X = P.study; if (!X || X.grading) return;
    const c = X.queue[X.pos]; if (!c) return;
    X.grading = true;
    const r = await call("cards_grade", X.cid, c.id, g);
    X.grading = false;
    if (!r.ok) { toast(r.error, "error"); return; }
    X.done++;
    const back = (new Date(r.state.due).getTime() - Date.now()) / 1000;
    if (r.state.step !== -1 && back < 20 * 60) {   // again in this sitting: forgotten, a few cards later; a learning
      // step, at the end (a learnt card waits for its study day, even when that starts at 04:00 in a few minutes)
      X.queue.splice(back < 120 ? Math.min(X.queue.length, X.pos + 4) : X.queue.length, 0, { ...c, state: r.state, next: r.state.next });
    }
    X.pos++; X.shown = false;
    drawStudy();
  }

  // ------------------------------------------------------------------ cards from files, Anki
  let M = null;      // the cards-from-files dialog
  async function makeDialog(cid) {
    M = { cid, opts: null, weeks: new Set(), files: new Set(), effort: (M && M.effort) || "", model: "", est: null, starting: false };
    modal(`<div class="cp-scroll"><h2>${I("ai")} ${t("Cards from files")}</h2>${skeleton()}</div>`);
    $(".modal").classList.add("pd", "pk");
    const r = await call("pack_options", cid);
    if (!M || M.cid !== cid) return;
    if (!r.ok) { toast(r.error, "error"); closeModal(); return; }
    M.opts = r; M.model = r.model || "sonnet";
    const cur = latestReady(r.weeks, (w) => w.files.some((f) => f.ready));
    if (cur) mPick(cur.no, true);
    drawMake(); estimateMake();
  }
  function mPick(no, on) {
    const w = M.opts.weeks.find((x) => x.no === no); if (!w) return;
    if (on) M.weeks.add(no); else M.weeks.delete(no);
    const kept = new Set(M.opts.weeks.filter((x) => M.weeks.has(x.no)).flatMap((x) => x.files.map((f) => f.id)));
    w.files.forEach((f) => { if (on && f.ready) M.files.add(f.id); else if (!on && !kept.has(f.id)) M.files.delete(f.id); });
  }
  const mScope = () => ({ kind: "cards", weeks: [...M.weeks].sort((a, b) => a - b), files: [...M.files], days: [], picked: true, model: M.model, effort: M.effort || "" });
  function makeCost() {
    const e = M.est;
    if (!canAI()) return `<span class="err-txt">${I("alert-circle", "sm")} ${t("Set up ChatGPT or Claude in Settings → AI first.")}</span>`;
    if (!M.files.size) return `<span class="dim">${t("Pick at least one file.")}</span>`;
    if (!e) return `<span class="dim">${SPIN()} ${t("Estimating…")}</span>`;
    return `<div class="pk-heavy light">${I("info", "sm")}<div><b>${t.html("Uses a little of your {ai} plan.", { ai: St.aiName() })}</b>
      ${esc(t.n(e.requests, "About {k} tokens in {n} request, {minutes}.", "About {k} tokens in {n} requests, {minutes}.", { k: St.fmtK(e.main_input + e.main_output), minutes: e.minutes }))}
      <div class="dim sm">${esc(t.n(e.pages, "Up to about {n} cards from {pages} page; cards your deck already has are left out.", "Up to about {n} cards from {pages} pages; cards your deck already has are left out.", { n: e.cards, pages: e.pages }))}</div>
      ${St.docLangLine ? St.docLangLine(e) : ""}</div></div>`;
  }
  function drawMake() {
    const r = M.opts, old = $(".modal .cp-scroll"), keep = old ? old.scrollTop : 0;
    const picked = r.weeks.filter((w) => M.weeks.has(w.no));
    const files = picked.map((w) => `<div class="pk-week"><div class="pk-week-h">${t("Week {n}", { n: w.no })} <span class="dim">${esc(w.topic || "")}</span></div>
      ${w.files.map((f) => `<label class="pk-item ${f.ready ? "" : "off"}"><input type="checkbox" class="check" data-act="fm-item" data-id="${esc(f.id)}" ${M.files.has(f.id) ? "checked" : ""} ${f.ready ? "" : "disabled"}>
        ${I("file-text", "sm")}<span class="grow">${esc(f.name)}</span><span class="dim sm">${f.ready ? t.n(f.pages, "{n} page", "{n} pages") : f.downloaded ? t("not read yet") : t("not on this PC")}</span></label>`).join("")
        || `<div class="dim sm pk-none">${t("No files for this week.")}</div>`}</div>`).join("");
    modal(`<div class="cp-scroll"><h2>${I("ai")} ${t("Cards from files")}</h2>
      <p class="pk-lead">${esc(t("{ai} makes flashcards from the files you pick: definitions, formulas, rules and methods, one idea per card, each with the page it comes from. They join this course's deck.", { ai: St.aiName() }))}</p>
      <div class="field"><label class="cp-label">${t("Weeks")}</label><div class="pk-weeks">${r.weeks.map((w) => `<button type="button" class="pk-wk ${M.weeks.has(w.no) ? "on" : ""} ${w.current ? "now" : ""}" data-act="fm-week" data-no="${w.no}" title="${esc(w.topic || "")}"><b>${t("W{n}", { n: w.no })}</b><span>${esc(w.topic || "—")}</span></button>`).join("")}</div></div>
      ${picked.length ? `<div class="field"><label class="cp-label">${t("Files")} <span class="dim sm">${t.n(M.files.size, "{n} file", "{n} files")}</span></label><div class="pk-material">${files}</div></div>` : ""}
      <div class="cp-opts">${St.isChatGPT() ? "" : `<div class="cp-opt"><div class="t">${t("Model")}</div><select class="input" data-fm="model">${[["sonnet", t("Sonnet (recommended)")], ["haiku", t("Haiku (lightest)")], ["opus", t("Opus (best, heavy)")]].map(([k, l]) => `<option value="${k}" ${M.model === k ? "selected" : ""}>${l}</option>`).join("")}</select></div>`}
        ${St.effortField ? St.effortField("fm", "plan", M.effort, { attrs: 'data-fm="effort"', why: t("Long files are read in several requests, each one at this level.") }) : ""}</div></div>
      <div class="cp-foot"><div class="cp-cost" id="fm-cost">${makeCost()}</div><div class="cp-btns"><button class="btn" data-act="modal-close">${t("Cancel")}</button>
        <button class="btn primary" data-act="fm-go" ${M.files.size && canAI() && !M.starting ? "" : "disabled"}>${I("ai", "sm")} ${t("Make cards")}</button></div></div>`);
    $(".modal").classList.add("pd", "pk");
    const nb = $(".modal .cp-scroll"); if (nb) nb.scrollTop = keep;
  }
  let mTimer = null;
  function estimateMake() {
    clearTimeout(mTimer);
    M.est = null;
    const box = $("#fm-cost"); if (box) box.innerHTML = makeCost();
    if (!M.files.size) return;
    const asked = M, sc = mScope();
    mTimer = setTimeout(async () => {
      const r = await call("cards_estimate", asked.cid, sc);
      if (asked !== M) return;
      M.est = r.ok ? r.estimate : null;
      const el = $("#fm-cost"); if (el) el.innerHTML = makeCost();
    }, 300);
  }
  const X2 = { cid: "", weeks: new Set() };      // the Anki dialog
  function exportDialog(cid) {
    const p = P.course[cid] || {}, d = p.deck || {}, code = (St.findCourse(cid) || {}).code || "";
    if (X2.cid !== cid) { X2.cid = cid; X2.weeks = new Set(); }
    const weeks = d.weeks || [];
    modal(`<h2>${I("download")} ${t("Export to Anki")}</h2>
      <p class="lead">${esc(t.n(d.total || 0, "Your {code} flashcards as an Anki deck: {n} card, with where it comes from and tags for the course and the week.", "Your {code} flashcards as an Anki deck: {n} cards, each with where it comes from and tags for the course and the week.", { code }))}</p>
      ${weeks.length > 1 ? `<div class="field"><label class="cp-label">${t("Weeks")} <span class="dim sm">${X2.weeks.size ? t("{n} picked", { n: X2.weeks.size }) : t("all weeks")}</span></label>
        <div class="fchips">${weeks.map((w) => `<button class="fchip ${X2.weeks.has(w) ? "on" : ""}" data-act="ax-week" data-no="${w}">${esc(t("W{n}", { n: w }))}</button>`).join("")}</div></div>` : ""}
      <div class="callout info">${I("info")}<div>${esc(t("Anki opens the .apkg file with a double click and puts the cards in the deck IEUStudia::{code}. Exporting again later updates them instead of adding them twice. No Anki yet? It is free at apps.ankiweb.net.", { code }))}</div></div>
      <div class="actions"><button class="btn ghost" data-act="ax-go" data-how="txt" title="${esc(t("A text file Anki imports (File, then Import)"))}">${t("Text file…")}</button><span class="spacer"></span>
        <button class="btn" data-act="modal-close">${t("Cancel")}</button>
        <button class="btn" data-act="ax-go" data-how="open" title="${esc(t("Opens it in Anki right away (Anki must be installed)"))}">${t("Open in Anki")}</button>
        <button class="btn primary" data-act="ax-go" data-how="apkg" autofocus>${I("download", "sm")} ${t("Save for Anki…")}</button></div>`);
  }

  // ------------------------------------------------------------------ keys in the quiz and the cards
  document.addEventListener("keydown", (e) => {
    const m = $("#overlay .modal"); if (!m || e.ctrlKey || e.altKey || e.metaKey) return;
    const tgt = e.target || {}, typing = tgt.matches && tgt.matches("input, textarea, select");
    const onButton = tgt.tagName === "BUTTON";       // (Enter and Space on a button press it already)
    if (m.querySelector(".fc-study") && P.study && !typing) {
      const X = P.study;
      if (!X.shown && (e.key === " " || e.key === "Enter") && !onButton) { e.preventDefault(); X.shown = true; drawStudy(); }
      else if (X.shown && ["1", "2", "3", "4"].includes(e.key)) { e.preventDefault(); gradeCard(["again", "hard", "good", "easy"][+e.key - 1]); }
      return;
    }
    const R = P.run;
    if (!m.querySelector(".qz") || !R || typing || R.pos < 0 || R.pos >= R.order.length) return;
    const k = e.key.length === 1 ? e.key.toLowerCase() : "", i = k ? ("1234".includes(k) ? "1234".indexOf(k) : "abcd".indexOf(k)) : -1;
    if (!R.checked && i >= 0 && m.querySelector(`.qz-opt[data-i="${i}"]`)) { e.preventDefault(); step((x) => { x.picked = i; }); }
    else if (e.key === "Enter" && !onButton) {
      const btn = m.querySelector(R.checked ? "[data-act='qz-next']" : "[data-act='qz-check']:not(:disabled)");
      if (btn) { e.preventDefault(); btn.click(); }
    }
  });

  // ------------------------------------------------------------------ wiring
  St.practice = { tab, homeNote, tabCount, load: loadCourse };
  St.views.practice = practiceView;
  St.crumbs.practice = () => `${I("target")} ${t("Practice")}`;
  St.onGo.practice = () => loadSummary(true);
  const goHome = St.onGo.home;
  St.onGo.home = (v) => { if (goHome) goHome(v); loadSummary(false); };
  const goCourse = St.onGo.course;
  St.onGo.course = (v) => { if (goCourse) goCourse(v); if (v.tab === "practice") loadCourse(v.courseId); };
  const navBefore = St.navExtra;
  St.navExtra = (v) => {
    const due = (P.summary || {}).due || 0;
    return `<button class="nav-item ${v.name === "practice" ? "active" : ""}" data-act="go" data-view="practice">${I("target")}<span class="label"><span class="code">${t("Practice")}</span></span>${due ? `<span class="count new" title="${esc(t.n(due, "{n} flashcard due today", "{n} flashcards due today"))}">${due}</span>` : ""}</button>`
      + (navBefore ? navBefore(v) : "");
  };
  // a run going on: its bar, step and wait line change where they are (the whole tab drawn again on every event, up
  // to twice a second, took the focus away); false when the row isn't drawn that way yet
  function inPlace(x) {
    const mine = (sel, key) => [...document.querySelectorAll(sel)].filter((b) => b.dataset[key] === x.id);
    const progs = mine(".pr-prog[data-id]", "id"), waits = mine(".pl-wait[data-wait]", "wait");
    if (!progs.length || (x.wait && St.waitLine && !waits.length)) return false;
    progs.forEach((b) => {
      const bar = b.querySelector(".bar i"), msg = b.querySelector(".pr-msg");
      if (bar) bar.style.width = `${Math.round(100 * (x.progress || 0))}%`;
      if (msg) msg.textContent = x.message || t("Waiting…");
    });
    if (x.wait && St.waitLine) waits.forEach((b) => { b.innerHTML = St.waitLine(x.wait); });
    return true;
  }
  const onPlan = St.onEvents.plan;
  St.onEvents.plan = (e) => {
    if (!["quiz", "cards"].includes(e.kind)) { if (onPlan) onPlan(e); return; }
    const p = P.course[e.course_id];
    const row = p && [...(p.quizzes || []), ...(p.card_sets || [])].find((x) => x.id === e.id);
    const going = !!row && row.status === "running" && e.status === "running" && (e.title || row.title) === row.title;
    if (row) Object.assign(row, { status: e.status, stage: e.stage, progress: e.progress, message: e.message, error: e.error, title: e.title || row.title, wait: e.wait || null });
    if (going && (!onTab(e.course_id) || inPlace(row))) return;          // (the Practice page shows only that it runs)
    if (e.status === "done") {
      toast(e.kind === "quiz" ? t("Quiz ready: {title}", { title: e.title || "" }) : t("New cards: {title}", { title: e.title || "" }), "success",
        e.kind === "quiz" ? { label: t("Practise"), act: "qz-play", data: { id: e.id } } : null);
      refresh(e.course_id);
    } else if (e.status === "error") {
      toast(e.kind === "quiz" ? t("Quiz: {error}", { error: e.error || t("failed") }) : setError(e), "error");
    }
    if (onTab(e.course_id) || S.view.name === "practice") {
      if (!row && e.status !== "done") loadCourse(e.course_id);
      renderView();
    }
  };
  St.onEvents.stressed = (e) => {
    if (e.phase === "start") P.stressing = e.course_id;
    else {
      P.stressing = null;
      if (e.phase === "done") { toast(t("Your exam list is ready."), "success"); loadCourse(e.course_id); }
      else if (!e.cancelled) toast(e.message || t("The AI could not finish"), "error");
    }
    if (onTab(e.course_id)) renderView();
  };
  St.inputActs = (St.inputActs || []).concat(["qz-item", "fm-item"]);    // checkboxes: app.js passes their clicks on

  Object.assign(A, {
    "pr-open": (el) => go({ name: "course", courseId: el.dataset.cid, tab: "practice" }),
    "pr-reload": (el) => { delete P.failed[el.dataset.cid]; loadCourse(el.dataset.cid); renderView(); },
    "pr-moment": (el) => openMoment(el.dataset.day, +el.dataset.part || 1, +el.dataset.t || 0),
    "pr-page": (el) => {
      const back = P.run && $("#overlay .qz") ? drawRun : P.study && $("#overlay .fc-study") ? drawStudy : null;
      showPage(el.dataset.cid, el.dataset.id, el.dataset.page, back);
    },
    "pr-page-go": (el) => showPage(V.cid, V.id, V.page + +el.dataset.d, V.back),
    "pr-page-back": () => { if (V.back) V.back(); else closeModal(); },
    // quizzes
    "qz-new": (el) => quizDialog(el && el.dataset.cid),
    "qz-course": (el) => quizDialog(el.value),
    "qz-mode": (el) => { readQuiz(); setMode(el.dataset.val); drawQuiz(); estimateQuiz(); },
    "qz-week": (el) => { readQuiz(); const no = +el.dataset.no; pickWeek(no, !Q.weeks.has(no)); drawQuiz(); estimateQuiz(); },
    "qz-item": (el) => { readQuiz(); const set = el.dataset.kind === "file" ? Q.files : Q.days; if (el.checked) set.add(el.dataset.id); else set.delete(el.dataset.id); drawQuiz(); estimateQuiz(); },
    "qz-day": (el) => { readQuiz(); Q.day = el.value; drawQuiz(); estimateQuiz(); },
    "qz-count": (el) => { readQuiz(); Q.count = +el.dataset.n; drawQuiz(); estimateQuiz(); },
    "qz-kinds": (el) => { readQuiz(); Q.kinds = el.value; },
    "qz-go": async (el) => {
      if (!Q || Q.starting) return;            // one click, one quiz (a double click wrote two and used the plan twice)
      readQuiz();
      const asked = Q, cid = Q.cid;
      asked.starting = true; el.disabled = true; el.innerHTML = `${SPIN()} ${t("Starting…")}`;
      const r = await call("quiz_create", cid, qScope());
      asked.starting = false;
      if (!r.ok) { toast(r.error, "error"); if (asked === Q) drawQuiz(); return; }
      closeModal();
      const p = P.course[cid];
      if (p && r.plan && !p.quizzes.some((x) => x.id === r.plan.id)) p.quizzes.unshift({ ...r.plan, weeks: (r.plan.scope || {}).weeks || [], days: (r.plan.scope || {}).days || [], focus: (r.plan.scope || {}).focus || "", questions: (r.plan.scope || {}).questions || 0, tries: 0 });
      // no toast: the quiz's own row says it is being written and that the app can be used meanwhile (at 1024×700 the
      // toast covered the row's Stop button); the row is brought into view instead
      if (onTab(cid)) renderView(); else go({ name: "course", courseId: cid, tab: "practice" });
      inView(r.plan && r.plan.id);
      loadCourse(cid);
    },
    "qz-play": (el) => playQuiz(el.dataset.id),
    "qz-resume": () => { if (P.run) playQuiz(P.run.id); },
    "qz-start": () => step((R) => { R.pos = 0; R.picked = null; R.checked = false; R.text = ""; }),
    "qz-pick": (el) => step((R) => { if (!R.checked) R.picked = +el.dataset.i; }),
    "qz-check": () => step((R) => {
      const q = R.quiz.result.questions[R.order[R.pos]];
      const box = $("#qz-text"); if (box) R.text = box.value.trim();
      R.checked = true;
      if (q.type === "mc") R.answers[q.n] = { got: R.picked === q.answer ? 1 : 0, chose: R.picked };
    }),
    "qz-skip": () => step((R) => {
      const q = R.quiz.result.questions[R.order[R.pos]];
      R.answers[q.n] = { got: 0, skipped: true, chose: null };
      if (q.type === "mc") { R.checked = true; R.picked = null; return; }       // the answer, then on
      R.pos++; R.picked = null; R.checked = false; R.text = "";
    }),
    "qz-self": (el) => step((R) => {
      const q = R.quiz.result.questions[R.order[R.pos]];
      R.answers[q.n] = { got: +el.dataset.got, text: R.text };
      R.pos++; R.picked = null; R.checked = false; R.text = "";
    }),
    "qz-next": () => step((R) => { R.pos++; R.picked = null; R.checked = false; R.text = ""; }),
    "qz-retry": (el) => playQuiz(el.dataset.id, el.dataset.only.split(",").map(Number).filter(Boolean)),
    "qz-delete": async (el) => {
      if (!(await St.ask({ title: t("Delete this quiz?"), text: t("Its questions and your scores go with it."), ok: t("Delete"), danger: true }))) return;
      const r = await call("quiz_delete", el.dataset.id);
      if (!r.ok) { toast(r.error, "error"); return; }
      if (P.run && P.run.id === el.dataset.id) P.run = null;
      delete P.runs[el.dataset.id];
      Object.values(P.course).forEach((p) => { p.quizzes = (p.quizzes || []).filter((x) => x.id !== el.dataset.id); });
      renderView(); loadSummary(S.view.name === "practice");
    },
    "menu-qz": (el) => {
      const p = P.course[el.dataset.cid] || {}, q = (p.quizzes || []).find((x) => x.id === el.dataset.id) || {};
      const last = q.last && (q.last.answers || []).filter((a) => (a.got || 0) < 1).map((a) => a.n);
      St.menu(el, [
        ...(last && last.length ? [{ id: "missed", icon: "retry", label: t.n(last.length, "Try {n} missed question", "Try {n} missed questions"), run: () => playQuiz(q.id, last) }] : []),
        { id: "pdf", icon: "file-text", label: t("Open as PDF"), desc: t("To print it, or read it on paper"), run: () => pdf(q.id, "open") },
        { id: "save", icon: "download", label: t("Save the PDF…"), run: () => pdf(q.id, "save") },
        "-",
        { id: "del", icon: "x", label: t("Delete"), run: () => A["qz-delete"]({ dataset: { id: q.id } }) }]);
    },
    // flashcards
    "fc-study": (el) => study(el.dataset.cid),
    "fc-flip": () => { if (P.study) { P.study.shown = true; drawStudy(); } },
    "fc-grade": (el) => gradeCard(el.dataset.g),
    "fc-close": () => { const X = P.study; P.study = null; closeModal(); if (X && X.done) refresh(X.cid); },
    "fc-make": (el) => makeDialog(el.dataset.cid),
    "fm-week": (el) => { document.querySelectorAll(".modal [data-fm]").forEach((x) => { M[x.dataset.fm] = x.value; }); const no = +el.dataset.no; mPick(no, !M.weeks.has(no)); drawMake(); estimateMake(); },
    "fm-item": (el) => { if (el.checked) M.files.add(el.dataset.id); else M.files.delete(el.dataset.id); drawMake(); estimateMake(); },
    "fm-go": async (el) => {
      if (!M || M.starting) return;
      document.querySelectorAll(".modal [data-fm]").forEach((x) => { M[x.dataset.fm] = x.value; });
      const asked = M;
      asked.starting = true; el.disabled = true;
      const r = await call("cards_make", asked.cid, mScope());
      asked.starting = false;
      if (!r.ok) { toast(r.error, "error"); if (asked === M) drawMake(); return; }
      closeModal();
      toast(t("Making your cards. It takes a few minutes; you can keep using the app."), "success");
      loadCourse(asked.cid);
    },
    "fc-set-delete": async (el) => { const r = await call("cards_set_delete", el.dataset.id); if (!r.ok) toast(r.error, "error"); Object.keys(P.course).forEach(loadCourse); },
    "fc-export": (el) => exportDialog(el.dataset.cid),
    "ax-week": (el) => { const n = +el.dataset.no; if (X2.weeks.has(n)) X2.weeks.delete(n); else X2.weeks.add(n); exportDialog(X2.cid); },
    "ax-go": async (el) => {
      el.disabled = true;
      const r = await call("cards_export", X2.cid, el.dataset.how, [...X2.weeks]);
      el.disabled = false;
      if (!r.ok) { toast(r.error, "error"); return; }
      if (!r.saved) return;                         // the save dialog was closed
      closeModal();
      toast(el.dataset.how === "open" ? t.n(r.cards || 0, "{n} card sent to Anki.", "{n} cards sent to Anki.")
        : t("Saved: {file}", { file: r.saved }), "success");
    },
    // what the lecturer stressed
    "st-week": (el) => { P.week[el.dataset.cid] = el.dataset.week; renderView(); },
    "st-more": (el) => { const k = el.dataset.key; if (P.more.has(k)) P.more.delete(k); else P.more.add(k); renderView(); },
    "st-sum": async (el) => {
      const cid = el.dataset.cid;
      const r = await call("stressed_summarize", cid, St.effortPick && St.isChatGPT() ? St.effortPick("st") : "");
      if (!r.ok) { toast(r.error, "error"); return; }
      P.stressing = cid; if (onTab(cid)) renderView();
    },
  });
  async function pdf(id, how) {
    toast(t("Making the PDF…"), "info");
    const r = await call("quiz_pdf", id, how);
    if (!r.ok) toast(r.error, "error");
    else if (r.saved) toast(t("Saved: {file}", { file: r.saved }), "success");
  }
  document.addEventListener("change", (e) => {
    if (Q && e.target.closest && e.target.closest(".modal [data-qz]")) { readQuiz(); estimateQuiz(); }
    if (M && e.target.closest && e.target.closest(".modal [data-fm]")) { M[e.target.dataset.fm] = e.target.value; estimateMake(); }
  });
})();
