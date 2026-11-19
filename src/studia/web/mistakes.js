/* IEUStudia — the mistake loop (4.2), on top of the Practice tools (practice.js). A quiz's end screen opens each
 * question: your answer beside the right one, why yours was wrong, and where it was taught (the slide on screen and
 * the transcript around that moment, with a short clip to play in the player, which stops by itself and leads back to
 * your results: St.player.open's "until" and "back"); "Remove this question" for a bad one. The course's Practice tab
 * gets "Mistakes to go over" (missed questions, until a retry gets them right or their flashcard is spaced out) and
 * "Where you lose points" (each learning outcome and topic as counts, never one percentage). A missed multiple-choice
 * question comes back in the flashcards with its options reordered, and the app marks it; any card can be edited,
 * set aside or deleted. The quiz dialog's "My weak spots" writes a quiz around what was missed. Python's side:
 * practice.py, cards.py. An answer's row is built from parts (St.mistakes.rowParts), so a later check (numbers the app
 * checks, written answers the AI marks) can add its own. Plugs into practice.js through window.Studia. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, call, esc, I, SPIN, toast, modal, closeModal, A } = St;
  const $ = (s, el = document) => el.querySelector(s);
  const LETTERS = "ABCDEF";
  const md = (s) => St.mdToHtml(String(s || ""));
  const mdLine = (s) => md(s).replace(/^<p>([\s\S]*)<\/p>$/, "$1");
  const dmw = (iso) => (iso ? t.date(String(iso).slice(0, 10), "dmw") : "");
  const score = (x) => t.num(x, x % 1 ? 1 : 0);
  const math = () => { const m = $("#overlay .modal"); if (m && St.renderMath) St.renderMath(m); };
  const firstLine = (s) => String(s || "").split("\n")[0];
  const PR = () => S.practice || {};
  const canAI = () => { const ai = S.system.ai || {}; return !!ai.available && ai.can_plans !== false; };
  const MQ = { open: new Set(), all: new Set(), mom: null };       // rows opened, lists shown whole, the replay open

  // ------------------------------------------------------------------ an answer's row (the end screen, the lists)
  const opt = (q, i) => (i == null || !(q.options || [])[i] ? "" : `<b>${LETTERS[i]}</b> ${mdLine(q.options[i])}`);
  const tone = (g) => (g >= 1 ? "ok" : g > 0 ? "half" : "no");
  function yoursPart(ctx, q, a) {
    const g = a.got || 0;
    const box = (cls, label, body) => `<div class="mq-box ${cls}"><div class="mq-lab">${esc(label)}</div>${body}</div>`;
    if (q.type === "mc") {
      const mine = a.skipped || a.chose == null ? `<span class="dim">${esc(t("You skipped it."))}</span>` : `<div class="md">${opt(q, a.chose)}</div>`;
      return `<div class="mq-cmp">${box(tone(g), t("Your answer"), mine)}${g >= 1 ? "" : box("ok", t("Right answer"), `<div class="md">${opt(q, q.answer)}</div>`)}</div>`;
    }
    // (a try saved before 4.2 kept no "text": what was written then is not known)
    const typed = a.text ? `<div class="mq-typed">${esc(a.text)}</div>`
      : `<span class="dim">${esc(a.skipped ? t("You skipped it.") : !("text" in a) && !a.final ? t("Not kept: this try is from an older version of IEUStudia.") : t("You didn't write an answer."))}</span>`;
    const good = q.type === "problem"
      ? `${(q.steps || []).length ? `<ol class="qz-steps">${q.steps.map((s) => `<li class="md">${md(s)}</li>`).join("")}</ol>` : ""}${q.model ? `<div class="qz-final"><b>${t("Answer:")}</b> <span class="md">${mdLine(q.model)}</span></div>` : ""}`
      : `${q.model ? `<div class="md">${md(q.model)}</div>` : ""}${(q.points || []).length ? `<div class="dim sm qz-points-h">${t("A full answer says:")}</div><ul class="qz-points">${q.points.map((x) => `<li class="md">${md(x)}</li>`).join("")}</ul>` : ""}`;
    return `<div class="mq-cmp">${box(tone(g), t("Your answer"), typed)}${box("ok", q.type === "problem" ? t("Solution") : t("A good answer"), good)}</div>`;
  }
  function whyPart(ctx, q, a) {
    const mine = q.type === "mc" && a.chose != null && a.chose !== q.answer && (q.wrong || [])[a.chose];
    return `${mine ? `<div class="mq-why no"><b>${esc(t("Why yours is wrong:"))}</b> ${mdLine(q.wrong[a.chose])}</div>` : ""}${q.why ? `<div class="mq-why"><b>${esc(t("Why:"))}</b> ${mdLine(q.why)}</div>` : ""}`;
  }
  function sourcePart(ctx, q) {
    const s = q.source;
    if (q.not_here || (ctx.notHere && ctx.notHere.has(q.n))) return `<div class="mq-src dim sm">${I("info", "xs")} ${esc(t("You said its source was the wrong place, so it has no replay."))}</div>`;
    if (!s) return `<div class="mq-src dim sm">${I("info", "xs")} ${esc(t("The app found no source for this question."))}</div>`;
    const found = q.found ? ` <span class="dim">${esc(t("(found by its words)"))}</span>` : "";
    const data = `data-cid="${esc(ctx.cid)}" data-plan="${esc(ctx.plan)}" data-n="${q.n}"`;
    if (s.kind === "file") {
      return `<div class="mq-src"><button class="btn sm" data-act="pr-page" data-cid="${esc(ctx.cid)}" data-id="${esc(s.id)}" data-page="${s.page}">${I("file-text", "sm")} ${esc(t("See the page"))}</button>
        <span class="dim sm">${esc(t("From {file}, page {page}", { file: s.name, page: s.page }))}${found}</span></div>`;
    }
    return `<div class="mq-src"><button class="btn sm" data-act="mq-replay" ${data} data-day="${esc(s.day_key)}" data-part="${s.part || 1}" data-t="${s.t || 0}">${I("play", "sm")} ${esc(t("Replay where it was taught"))}</button>
      <span class="dim sm">${esc(t("From the lecture of {day}, part {part}, {time}", { day: dmw(s.date), part: s.part || 1, time: s.ts || "" }))}${found}</span></div>`;
  }
  function actsPart(ctx, q) {
    const data = `data-cid="${esc(ctx.cid)}" data-plan="${esc(ctx.plan)}" data-n="${q.n}"`;
    if (ctx.removed && ctx.removed.has(q.n)) return `<div class="mq-acts"><span class="dim sm">${esc(t("Removed from this quiz."))}</span> <button class="link sm" data-act="mq-remove" data-undo="1" ${data}>${t("Undo")}</button></div>`;
    return `<div class="mq-acts"><button class="btn ghost sm" data-act="mq-remove" ${data} title="${esc(t("For a wrong or unclear question: it isn't asked again, and it leaves your cards and your counts."))}">${I("x", "sm")} ${esc(t("Remove this question"))}</button></div>`;
  }
  // ctx: {cid, plan (the quiz's id), meta (a line beside it, in lists), removed, notHere (Sets of question numbers)}
  function row(ctx, q, a) {
    a = a || {};
    const k = `${ctx.plan}|${q.n}`, on = MQ.open.has(k), g = a.got || 0, off = !!(ctx.removed && ctx.removed.has(q.n));
    return `<li class="mq ${off ? "off" : tone(g)} ${on ? "open" : ""}"><button type="button" class="mq-h" data-act="mq-row" data-key="${esc(k)}" aria-expanded="${on}">${I(off ? "minus" : g >= 1 ? "check" : g > 0 ? "minus" : "x", "sm")}<span class="qz-res-t md">${mdLine(firstLine(q.q))}</span>${ctx.meta ? `<span class="dim sm mq-meta" ${ctx.metaTitle ? `title="${esc(ctx.metaTitle)}"` : ""}>${esc(ctx.meta)}</span>` : ""}${I(on ? "chevron-up" : "chevron-down", "xs")}</button>
      ${on ? `<div class="mq-more">${M.rowParts.map((f) => f(ctx, q, a)).join("")}</div>` : ""}</li>`;
  }
  function endRow(R, q) {
    const res = R.quiz.result || {};
    return row({ cid: R.cid, plan: R.id, removed: new Set(res.removed || []), notHere: new Set(res.not_here || []) }, q, R.answers[q.n]);
  }
  // a try saved: its missed questions joined the cards (Undo takes them out again)
  function saved(R, r) {
    R.attemptId = (r.attempt || {}).id || "";
    const ids = r.carded || [];
    if (ids.length) {
      toast(t.n(ids.length, "{n} missed question joins your flashcards: it comes back tomorrow.", "{n} missed questions join your flashcards: they come back tomorrow."), "info",
        { label: t("Undo"), act: "mq-uncard", data: { cid: R.cid, ids: ids.join(",") } });
    }
  }
  // the end screen or the course tab drawn again, where it was scrolled to
  function redraw() {
    const R = PR().run, end = R && $("#overlay .qz-end");
    const box = $("#overlay .qz-results"), top = box ? box.scrollTop : 0;
    if (end) { St.practice.showRun(R); const nb = $("#overlay .qz-results"); if (nb) nb.scrollTop = top; } else St.renderView();
  }

  // ------------------------------------------------------------------ where it was taught (a replay)
  async function replay(d) {
    const R = PR().run, back = R && R.saved && $("#overlay .qz-end") ? () => St.practice.showRun(R) : null;
    const mom = MQ.mom = { d, r: null, back };
    modal(`<div class="mq-mom"><div class="fc-top"><b>${I("video", "sm")} ${t("Where it was taught")}</b></div><p class="dim sm">${SPIN()} ${esc(t("Finding the moment…"))}</p></div>`);
    $(".modal").classList.add("mq-mom-modal");
    const r = await call("practice_moment", d.cid, d.day, +d.part || 1, +d.t || 0);
    if (MQ.mom !== mom) return;
    if (!r.ok) { toast(r.error, "error"); leave(); return; }
    mom.r = r;
    drawMoment();
  }
  function drawMoment() {
    const { r, back } = MQ.mom;
    const where = t("Lecture of {day}, part {part}, {time}", { day: dmw(r.date), part: r.part, time: r.ts });
    const pic = r.slide ? `<figure class="mq-slide">${r.slide.image ? `<img src="${esc(r.slide.image)}" alt="${esc(t("Slide {n}", { n: r.slide.n }))}">` : ""}
        <figcaption class="dim sm">${esc(t("Slide {n}", { n: r.slide.n }))} · ${esc(r.slide.range)}${r.slide.title ? ` · ${esc(r.slide.title)}` : ""}</figcaption></figure>`
      : `<p class="dim sm">${esc(t("No slide was captured at this moment, so here is what was said."))}</p>`;
    const lines = (r.lines || []).map((x) => `<div class="mq-line ${x.at ? "at" : ""}"><span class="mq-ts">${esc(x.ts)}</span><span>${esc(x.text)}</span></div>`).join("");
    const play = St.player && !window.LS_LINUX;           // (the player is untested on Linux: the words there)
    modal(`<div class="mq-mom"><div class="fc-top"><b>${I("video", "sm")} ${t("Where it was taught")}</b><span class="dim sm">${esc(where)}</span></div>
      ${pic}<div class="mq-lines">${lines || `<div class="dim sm">${esc(t("This part has no transcript yet."))}</div>`}</div>
      <div class="actions"><button class="btn ghost sm" data-act="mq-not-here" title="${esc(t("The app picked the wrong moment for this question: it won't offer this replay again."))}">${t("Not the right place")}</button><span class="spacer"></span>
        <button class="btn" data-act="mq-mom-back">${back ? t("Back to your results") : t("Close")}</button>
        ${play ? `<button class="btn primary" data-act="mq-play" autofocus>${I("play", "sm")} ${esc(t("Play this part ({range})", { range: r.clip.range }))}</button>`
          : `<button class="btn primary" data-act="pr-moment" data-day="${esc(r.day_key)}" data-part="${r.part}" data-t="${r.t}" autofocus>${t("Read it in the transcript")}</button>`}</div></div>`);
    $(".modal").classList.add("mq-mom-modal");
  }
  function leave() { const m = MQ.mom; MQ.mom = null; if (m && m.back) m.back(); else closeModal(); }

  // ------------------------------------------------------------------ the course's Practice tab
  function mistakesCard(c, p) {
    const list = p.mistakes || [];
    if (!list.length) return "";
    const all = MQ.all.has(c.id), shown = all ? list : list.slice(0, 5);
    const rows = shown.map((m) => row({ cid: c.id, plan: m.plan_id, meta: dmw(m.at), metaTitle: m.title }, m.q, m.answer)).join("");
    return `<div class="card pr-card mq-card"><div class="pr-h">${I("retry")}<b>${t("Mistakes to go over")}</b><span class="dim">${list.length}</span><span class="spacer"></span>
        <button class="btn sm" data-act="mq-weak" data-cid="${esc(c.id)}" ${St.aiBtn(canAI(), true)} title="${esc(canAI() ? t("A new quiz written around the questions you missed, asking them in new ways") : St.aiHint())}">${I("target", "sm")} ${t("Quiz my weak spots")}</button></div>
      <p class="pr-sub">${esc(t("Questions you missed. Open one to see your answer beside the right one, and where it was taught. It leaves this list when you get it right in a retry, or when its flashcard has come back a few times."))}</p>
      <ol class="mq-list">${rows}</ol>
      ${list.length > 5 ? `<button class="link sm" data-act="mq-all" data-cid="${esc(c.id)}">${all ? esc(t("Show fewer")) : esc(t("Show all {n}", { n: list.length }))}</button>` : ""}</div>`;
  }
  const STATUS = () => ({ shaky: [t("Losing points"), "no"], some: [t("Getting there"), "half"], few: [t("Too few to tell"), "none"],
    solid: [t("Solid"), "ok"], untested: [t("Not tested yet"), "none"] });
  function skillRow(c, p, key, label, text, r) {
    const k = `${c.id}|${key}`, on = MQ.open.has(k), [say, cls] = STATUS()[r.status] || STATUS().some;
    const counts = t("{right} of {n} right", { right: score(r.right), n: r.tried }) + (r.quizzes ? ` · ${t.n(r.quizzes, "from {n} quiz", "from {n} quizzes")}` : "");
    const open = (p.mistakes || []).filter((m) => (r.missed || []).some((x) => x.plan_id === m.plan_id && x.n === m.n));
    const list = on ? `<ol class="mq-list">${open.map((m) => row({ cid: c.id, plan: m.plan_id, meta: dmw(m.at) }, m.q, m.answer)).join("")}</ol>` : "";
    return `<div class="mq-skill ${on ? "open" : ""}"><button type="button" class="mq-skill-h" data-act="mq-row" data-key="${esc(k)}" ${open.length ? `aria-expanded="${on}"` : "disabled"}>
        ${label ? `<span class="lo-no">${esc(label)}</span>` : ""}<span class="grow">${esc(text || "")}</span><span class="dim sm mq-count">${esc(counts)}</span><span class="pill mq-st ${cls}">${esc(say)}</span>${open.length ? I(on ? "chevron-up" : "chevron-down", "xs") : ""}</button>${list}</div>`;
  }
  function weakCard(c, p) {
    const w = p.weak;
    if (!w || !w.attempts) return "";
    const tried = (w.los || []).filter((r) => r.status !== "untested"), untested = (w.los || []).filter((r) => r.status === "untested");
    const topics = (w.topics || []).filter((r) => r.status !== "solid" && r.right < r.tried).slice(0, 6);   // (where points went)
    if (!tried.length && !topics.length) return "";
    const quiz = (r) => `<button class="btn ghost sm" data-act="mq-quiz-lo" data-cid="${esc(c.id)}" data-no="${r.no}" data-text="${esc(r.text || "")}" ${St.aiBtn(canAI(), true)}>${t("Quiz this LO")}</button>`;
    return `<div class="card pr-card mq-card"><div class="pr-h">${I("target")}<b>${t("Where you lose points")}</b></div>
      <p class="pr-sub">${esc(t("Counted from your quiz answers and the flashcards of questions you missed; recent ones count more, and answers you marked yourself count half."))}</p>
      ${tried.length ? `<div class="mq-skills">${tried.map((r) => skillRow(c, p, `lo:${r.no}`, `LO${r.no}`, r.text, r)).join("")}</div>` : ""}
      ${topics.length ? `<div class="mq-sub">${t("By topic")}</div><div class="mq-skills">${topics.map((r) => skillRow(c, p, `tp:${r.name}`, "", r.name, r)).join("")}</div>` : ""}
      ${untested.length ? `<div class="mq-sub">${t("Not tested yet")}</div><div class="mq-skills">${untested.map((r) => `<div class="mq-skill"><div class="mq-skill-h"><span class="lo-no">LO${r.no}</span><span class="grow">${esc(r.text || "")}</span>${quiz(r)}</div></div>`).join("")}</div>` : ""}</div>`;
  }
  function tabHTML(c, p) { return mistakesCard(c, p) + weakCard(c, p); }
  // the Flashcards card: cards set aside, with the way back
  const asideLine = (c, d) => (d && d.aside ? `<div class="note-sm">${I("pause", "sm")}<span>${esc(t.n(d.aside, "{n} card is set aside.", "{n} cards are set aside."))} <button class="link" data-act="fc-bring-back" data-cid="${esc(c.id)}">${t("Bring them back")}</button></span></div>` : "");

  // ------------------------------------------------------------------ a missed multiple-choice question as a card
  const isMC = (c) => !!c && c.origin === "quiz" && !c.edited && (c.quiz || {}).type === "mc" && ((c.quiz || {}).options || []).length >= 2;
  const cardBtn = (c) => (c ? `<button class="btn ghost icon sm fc-more" data-act="fc-card-menu" title="${esc(t("Edit, set aside or delete this card"))}" aria-label="${esc(t("Edit, set aside or delete this card"))}">${I("more", "sm")}</button>` : "");
  function studyHTML(X, c, code) {
    const qz = c.quiz, pick = X.picked, shown = pick != null, right = shown && pick === qz.answer, left = X.queue.length - X.pos;
    const opts = qz.options.map((o, i) => {
      const st = !shown ? "" : i === qz.answer ? "right" : pick === i ? "wrong" : "dim";
      const mark = shown && i === qz.answer ? I("check", "sm") : shown && pick === i ? I("x", "sm") : "";
      return `<button type="button" class="qz-opt ${st}" data-act="fc-pick" data-i="${i}" ${shown ? "disabled" : ""}><span class="qz-l">${LETTERS[i]}</span><span class="md grow">${md(o)}</span>${mark}</button>`;
    }).join("");
    const why = shown ? `<div class="qz-fb ${right ? "ok" : "no"}"><div class="qz-fb-h">${I(right ? "check-circle" : "x-circle", "sm")}<b>${right ? t("Right!") : esc(t("Not quite. The answer is {letter}.", { letter: LETTERS[qz.answer] }))}</b></div>
        ${qz.why ? `<div class="md">${md(qz.why)}</div>` : ""}${!right && (qz.wrong || [])[pick] ? `<div class="qz-why-wrong"><b>${esc(t("Why {letter} is wrong:", { letter: LETTERS[pick] }))}</b> ${mdLine(qz.wrong[pick])}</div>` : ""}</div>` : "";
    const src = shown && c.source && c.source.kind ? (c.source.kind === "file"
      ? `<button class="qz-src" data-act="pr-page" data-cid="${esc(X.cid)}" data-id="${esc(c.source.id)}" data-page="${c.source.page}">${I("file-text", "xs")}<span>${esc(t("From {file}, page {page}", { file: c.source.name, page: c.source.page }))}</span></button>`
      : `<button class="qz-src" data-act="pr-moment" data-day="${esc(c.source.day_key)}" data-part="${c.source.part || 1}" data-t="${c.source.t || 0}">${I("video", "xs")}<span>${esc(t("From the lecture of {day}, part {part}, {time}", { day: dmw(c.source.date), part: c.source.part || 1, time: c.source.ts || "" }))}</span></button>`) : "";
    return `<div class="fc fc-study fc-mc"><div class="fc-top"><b>${I("layers", "sm")} ${esc(code)} · ${t("flashcards")}</b><span class="dim sm">${esc(t("{n} left", { n: left }))}${X.done ? ` · ${esc(t("{n} done", { n: X.done }))}` : ""}</span>${cardBtn(c)}</div>
      <div class="fc-bar"><i style="width:${Math.round((100 * X.pos) / X.queue.length)}%"></i></div>
      <div class="qz-kind">${esc(t("A question you missed"))}${qz.title ? ` · ${esc(qz.title)}` : ""}</div>
      <div class="fc-q md">${md(c.q)}</div><div class="qz-opts">${opts}</div>${why}
      <div class="fc-meta">${src}</div>
      <div class="actions">${shown ? `<span class="dim sm qz-self-q">${esc(right ? t("The app marks it Good.") : t("The app marks it Again: it comes back soon."))}</span><button class="btn primary" data-act="fc-mc-next" autofocus>${t("Next card")}</button>`
        : `<button class="btn ghost" data-act="fc-close">${t.c("flashcards", "Stop")}</button>`}</div>
      <div class="fc-keys dim sm">${shown ? t("Key: Enter goes on") : t("Keys: 1 to 4 pick an answer")}</div></div>`;
  }
  function mcNext() {
    const X = PR().study; if (!X || X.picked == null) return;
    const c = X.queue[X.pos], g = X.picked === c.quiz.answer ? "good" : "again";
    X.picked = null;
    St.practice.gradeCard(g);
  }
  function studyKey(e, X) {             // the keys of a multiple-choice card: 1-4 (or A-D) pick, Enter goes on
    const c = X.queue[X.pos];
    if (!isMC(c)) return false;
    const k = e.key.length === 1 ? e.key.toLowerCase() : "", i = k ? ("1234".includes(k) ? "1234".indexOf(k) : "abcd".indexOf(k)) : -1;
    const onButton = (e.target || {}).tagName === "BUTTON";
    if (X.picked == null && i >= 0 && i < c.quiz.options.length) { e.preventDefault(); X.picked = i; St.practice.drawStudy(); }
    else if (X.picked != null && (e.key === "Enter" || e.key === " ") && !onButton) { e.preventDefault(); mcNext(); }
    return true;
  }
  // a card edited, set aside or deleted (in the session)
  function editCard(X, c) {
    modal(`<h2>${I("pencil")} ${t("Edit this card")}</h2>
      <div class="field"><label for="fc-ed-q">${t("Front")}</label><textarea class="input mq-ed" id="fc-ed-q" rows="3">${esc(c.q)}</textarea></div>
      <div class="field"><label for="fc-ed-a">${t("Back")}</label><textarea class="input mq-ed" id="fc-ed-a" rows="5">${esc(c.a)}</textarea></div>
      <p class="dim sm">${esc(t("What you learnt of it stays. Maths in LaTeX between $ signs, as on the card."))}</p>
      <div class="actions"><button class="btn" data-act="fc-edit-cancel">${t("Cancel")}</button><button class="btn primary" data-act="fc-edit-save">${t("Save")}</button></div>`);
  }
  async function setOff(X, c, off) {
    if (off === "deleted" && !(await St.ask({ title: t("Delete this card?"), text: t("It leaves your deck for good, with what you learnt of it."), ok: t("Delete"), danger: true }))) { St.practice.drawStudy(); return; }
    const r = await call("cards_edit", X.cid, c.id, { off });
    if (!r.ok) { toast(r.error, "error"); return; }
    X.queue = X.queue.filter((x, i) => i < X.pos || x.id !== c.id);
    X.picked = null; X.shown = false;
    St.practice.drawStudy();
    toast(off === "aside" ? t("Set aside. You can bring it back from the Flashcards card.") : t("Card deleted."), "success",
      off === "aside" ? { label: t("Undo"), act: "fc-unaside", data: { cid: X.cid, id: c.id } } : undefined);
    St.practice.refresh(X.cid);
  }

  // ------------------------------------------------------------------ the quiz dialog's "My weak spots"
  const weakFirst = (o) => { const w = (o || {}).weak || {}; return w.missed > 0 && w.attempts >= 2; };
  function weakModeBtn(Q, mode) {
    const w = (Q.opts || {}).weak || {};
    return w.missed ? mode("weak", esc(t("My weak spots")), esc(t.n(w.missed, "{n} question you missed, asked in new ways", "{n} questions you missed, asked in new ways"))) : "";
  }
  function weakWhere(o) {
    const w = (o || {}).weak || {};
    const bits = [t.n(w.missed || 0, "{n} question you missed", "{n} questions you missed"),
      (w.los || []).length ? t("learning outcomes where you lose points: {los}", { los: w.los.map((n) => `LO${n}`).join(", ") }) : "",
      (w.topics || []).length ? t("topics: {list}", { list: w.topics.slice(0, 4).join("; ") }) : ""].filter(Boolean);
    return `<div class="field"><div class="mq-weakbox">${I("target", "sm")}<div><b>${t("Written around what you missed")}</b><div class="sm">${esc(bits.join(" · "))}</div>
      <div class="dim sm">${esc(t("It reads only the pages and lecture minutes those questions come from, so it is smaller than a quiz on whole weeks. Questions you got right twice are not asked again."))}</div></div></div></div>`;
  }

  // ------------------------------------------------------------------ actions
  Object.assign(A, {
    "mq-row": (el) => { const k = el.dataset.key; if (MQ.open.has(k)) MQ.open.delete(k); else MQ.open.add(k); redraw(); math(); },
    "mq-all": (el) => { const c = el.dataset.cid; if (MQ.all.has(c)) MQ.all.delete(c); else MQ.all.add(c); St.renderView(); },
    "mq-replay": (el) => replay({ ...el.dataset }),
    "mq-mom-back": leave,
    "mq-play": () => {
      const m = MQ.mom;
      if (!m || !m.r || !St.player) return;
      MQ.mom = null;
      closeModal();
      St.player.open(m.r.day_key, m.r.part, m.r.clip.from, { until: m.r.clip.until, back: m.back ? { label: t("Back to your results"), run: m.back } : null });
    },
    "mq-not-here": async () => {
      const m = MQ.mom;
      if (!m) return;
      const r = await call("quiz_question", m.d.plan, +m.d.n, "not_here");
      if (!r.ok) { toast(r.error, "error"); return; }
      const R = PR().run;
      if (R && R.id === m.d.plan) R.quiz.result.not_here = r.not_here;
      toast(t("Thanks. This question won't offer that replay again."), "success");
      leave();
      St.practice.refresh(m.d.cid);
    },
    "mq-remove": async (el) => {
      const { cid, plan, n } = el.dataset, undo = el.dataset.undo === "1";
      const r = await call("quiz_question", plan, +n, undo ? "restore" : "remove");
      if (!r.ok) { toast(r.error, "error"); return; }
      const R = PR().run;
      if (R && R.id === plan) R.quiz.result.removed = r.removed;
      if (!undo) toast(t("Removed: it won't be asked again."), "success", { label: t("Undo"), act: "mq-remove", data: { cid, plan, n, undo: "1" } });
      redraw();
      St.practice.refresh(cid);
    },
    "mq-uncard": async (el) => {
      const r = await call("mistakes_uncard", el.dataset.cid, String(el.dataset.ids || "").split(",").filter(Boolean));
      if (!r.ok) { toast(r.error, "error"); return; }
      toast(t("They are out of your flashcards again."), "success");
      St.practice.refresh(el.dataset.cid);
    },
    "mq-weak": (el) => St.practice.quizDialog(el.dataset.cid, { mode: "weak" }),
    "mq-quiz-lo": (el) => St.practice.quizDialog(el.dataset.cid, { los: [+el.dataset.no], topics: `LO${el.dataset.no}${el.dataset.text ? `: ${el.dataset.text}` : ""}`,
      ...(el.dataset.key ? { key: el.dataset.key } : {}) }),          // (the Exam room's: for its own assessment)
    "fc-pick": (el) => { const X = PR().study; if (!X || X.picked != null) return; X.picked = +el.dataset.i; St.practice.drawStudy(); },
    "fc-mc-next": mcNext,
    "fc-card-menu": (el) => {
      const X = PR().study, c = X && X.queue[X.pos];
      if (!c) return;
      St.menu(el, [
        ...(isMC(c) ? [] : [{ id: "edit", icon: "pencil", label: t("Edit this card"), run: () => editCard(X, c) }]),
        { id: "aside", icon: "pause", label: t("Set it aside"), desc: t("It stays in your deck, but doesn't come up until you bring it back"), run: () => setOff(X, c, "aside") },
        "-",
        { id: "del", icon: "x", label: t("Delete this card"), run: () => setOff(X, c, "deleted") }]);
    },
    "fc-edit-cancel": () => St.practice.drawStudy(),
    "fc-edit-save": async () => {
      const X = PR().study, c = X && X.queue[X.pos];
      if (!c) return;
      const q = ($("#fc-ed-q") || {}).value || "", a = ($("#fc-ed-a") || {}).value || "";
      if (!q.trim() || !a.trim()) { toast(t("A card needs a front and a back."), "error"); return; }
      const r = await call("cards_edit", X.cid, c.id, { q, a });
      if (!r.ok) { toast(r.error, "error"); return; }
      X.queue.forEach((x) => { if (x.id === c.id) Object.assign(x, { q: q.trim(), a: a.trim(), edited: true }); });
      St.practice.drawStudy();
      toast(t("Card saved."), "success");
      St.practice.refresh(X.cid);
    },
    "fc-unaside": async (el) => {             // a card just set aside, back in the deck (its toast's Undo)
      const r = await call("cards_edit", el.dataset.cid, el.dataset.id, { off: "" });
      if (!r.ok) { toast(r.error, "error"); return; }
      toast(t("It's back in your deck."), "success");
      St.practice.refresh(el.dataset.cid);
    },
    "fc-bring-back": async (el) => {
      const r = await call("cards_bring_back", el.dataset.cid);
      if (!r.ok) { toast(r.error, "error"); return; }
      toast(t.n(r.back || 0, "{n} card is back in your deck.", "{n} cards are back in your deck."), "success");
      St.practice.refresh(el.dataset.cid);
    },
  });

  const M = St.mistakes = { rowParts: [yoursPart, whyPart, sourcePart, actsPart], row, endRow, saved, tabHTML, asideLine,
    isMC, cardBtn, studyHTML, studyKey, weakFirst, weakModeBtn, weakWhere, replay, state: MQ,
    skillRow };          // (the Exam room's "How well you know it": examroom.js)
})();
