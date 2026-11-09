/* IEUStudia — what an AI run is doing while the student waits, so a wait never looks stuck (aiwait.py's snapshot, sent
 * with the events of chat answers, study notes, the digest, plans, study packs, cheatsheets and review sheets): the
 * question going up (MB of MB when the request can tell), the AI reading it, thinking (its summaries as "Thinking: …"
 * lines), writing (tokens so far), a retry in words; the time so far and about how long is left (from this PC's
 * measured speeds: nothing until there are some, "taking longer than usual" once past them, never a made-up
 * countdown), and what the student can do meanwhile. The clocks tick here every second, never by asking Python.
 * A chip in the top bar says what is still running when the student is elsewhere, and takes them there. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, esc, I, A } = St;
  const BIG = 2 * 1048576;           // aiwait.BIG: a request this heavy takes a while to go up
  const HINT_AFTER_S = 10;           // what the student can do meanwhile: said once a wait has gone on this long
  const now = () => Date.now() / 1000;
  const clock = (s) => {
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = String(s % 60).padStart(2, "0");
    return h ? `${h}:${String(m).padStart(2, "0")}:${x}` : `${m}:${x}`;
  };
  const mb = (b) => t.num(b / 1048576, b < 10 * 1048576 ? 1 : 0, false);
  const aiOf = (w) => w.ai || St.aiName();
  const chat = (w) => w.where === "chat";
  const PLANS = ["videos", "prep", "pack", "sheet", "quiz"];
  const isPlan = (w) => PLANS.includes(w.where) || !["chat", "notes", "digest"].includes(w.where);

  // ---------------------------------------------------------------- what is happening now
  function sending(w) {
    const ai = aiOf(w), n = w.pictures || 0;
    if (w.up && w.up[1]) {                     // it can tell how far it is
      const v = { ai, n, done: mb(w.up[0]), total: mb(w.up[1]) };
      if (chat(w)) return n ? t.n(n, "Sending your question with {n} picture to {ai}: {done} of {total} MB", "Sending your question with {n} pictures to {ai}: {done} of {total} MB", v)
        : t("Sending your question to {ai}: {done} of {total} MB", v);
      return n ? t.n(n, "Sending the material with {n} picture to {ai}: {done} of {total} MB", "Sending the material with {n} pictures to {ai}: {done} of {total} MB", v)
        : t("Sending the material to {ai}: {done} of {total} MB", v);
    }
    if ((w.size || 0) >= BIG) {                // heavy, but its tool doesn't say how far (Claude Code)
      const v = { ai, n, size: mb(w.size) };
      if (chat(w)) return n ? t.n(n, "Sending your question with {n} picture to {ai} ({size} MB)…", "Sending your question with {n} pictures to {ai} ({size} MB)…", v)
        : t("Sending your question to {ai} ({size} MB)…", v);
      return n ? t.n(n, "Sending the material with {n} picture to {ai} ({size} MB)…", "Sending the material with {n} pictures to {ai} ({size} MB)…", v)
        : t("Sending the material to {ai} ({size} MB)…", v);
    }
    return chat(w) ? t("Sending your question to {ai}…", { ai }) : t("Sending the material to {ai}…", { ai });
  }
  // "Trying again in 8 s (2 of 11)." while it waits, "Trying again now…" once the wait is over (the clock redraws it)
  function retryAct(until, n, of) {
    const left = Math.ceil(until - now()), x = n && of ? { n, of } : null;
    return left > 0 ? (x ? t("Trying again in {s} s ({n} of {of}).", { s: left, ...x }) : t("Trying again in {s} s.", { s: left }))
      : (x ? t("Trying again now ({n} of {of})…", x) : t("Trying again now…"));
  }
  function retryText(w) {
    const r = w.retry || {}, ai = aiOf(w);
    const why = { dropped: t("The connection dropped."), overloaded: t("{ai} is very busy right now.", { ai }),
      rate_limit: t("{ai} asked IEUStudia to slow down.", { ai }) }[r.why] || t("{ai} had a problem on its side.", { ai });
    return `${esc(why)} <span class="aw-retry" data-until="${r.until || 0}" data-n="${r.n || 0}" data-of="${r.of || 0}">${esc(retryAct(r.until || 0, r.n, r.of))}</span>`;
  }
  // the words for the step it is on (HTML); "" when the run's own page says it (a plan between its requests)
  function doing(w) {
    const ai = aiOf(w);
    switch (w.phase) {
      case "prepare": return esc(chat(w) ? (w.read ? t("Putting your question together…") : t("Getting your lectures and slides ready…")) : w.where === "notes" ? t("Getting the lecture ready…")
        : w.where === "digest" ? t("Gathering your week…") : "");
      case "send": return esc(sending(w));
      case "wait": return esc(t("{ai} got it and is reading it…", { ai }));
      case "think": return esc(t("{ai} is thinking…", { ai }));
      case "write": return esc(t("{ai} is writing…", { ai }));
      case "retry": return retryText(w);
      default: return "";
    }
  }

  // ---------------------------------------------------------------- the times
  function leftText(w, left) {
    const step = isPlan(w) && w.units;
    if (left > 0) return step ? t("{left} for this step", { left: t.left(left) }) : t.left(left);
    return t("taking longer than usual");
  }
  function timesHTML(w) {
    const parts = [`<span class="aw-since" data-since="${w.since || now()}">${esc(t("{time} so far", { time: clock(now() - (w.since || now())) }))}</span>`];
    if (w.phase === "write" && w.tokens) parts.push(esc(t.n(w.tokens, "{n} token written", "{n} tokens written", { n: t.num(w.tokens) })));
    if (w.phase !== "retry") {
      if (w.eta != null) parts.push(`<span class="aw-left" data-eta="${w.eta}" data-at="${w.at || now()}"${isPlan(w) && w.units ? ' data-step="1"' : ""}>${esc(leftText(w, w.eta - (now() - (w.at || now()))))}</span>`);
      else if (w.slow) parts.push(`<span class="aw-slow">${esc(t("taking longer than usual"))}</span>`);
      else if (w.most) parts.push(esc(w.most < 60 ? t("at most a minute left") : t("at most {time} left", { time: t.dur(w.most) })));
    }
    return parts.join(" · ");
  }
  function linesHTML(w) {
    const lines = w.lines || [];
    if (!lines.length || !["think", "wait", "retry"].includes(w.phase)) return "";
    return `<ul class="aw-lines">${lines.map((x, i) => `<li class="${i === lines.length - 1 ? "now" : ""}">${esc(t("Thinking: {line}", { line: x }))}</li>`).join("")}</ul>`;
  }
  function hintHTML(w) {
    const text = { chat: t("You can use the rest of IEUStudia meanwhile: the answer keeps coming in here. Keep IEUStudia open until it's done."),
      notes: t("You can use the rest of IEUStudia meanwhile: the notes are saved next to the lecture and shown here. Keep IEUStudia open until they're done."),
      digest: t("You can use the rest of IEUStudia meanwhile: the summary appears here when it's done.") }[w.where];
    if (!text) return "";
    const late = now() - (w.since || now()) >= HINT_AFTER_S;
    return `<div class="aw-hint" data-after="${(w.since || now()) + HINT_AFTER_S}"${late ? "" : " hidden"}>${I("info", "xs")}<span>${esc(text)}</span></div>`;
  }
  const readHTML = (w) => (w.read ? `<div class="aw-read">${I("check", "xs")} ${esc(t.n(w.read, "Went through {n} page first", "Went through all {n} pages first"))}</div>` : "");

  // ---------------------------------------------------------------- what the pages use
  // the whole wait: before the answer's words come (a chat's bubble, the notes' card, the digest)
  St.waitHTML = (w) => {
    if (!w) return "";
    const what = doing(w);
    if (!what) return "";
    return `<div class="aw" data-phase="${esc(w.phase)}">${readHTML(w)}<div class="aw-main">${w.phase === "retry" ? I("retry", "sm") : St.SPIN()}<div class="aw-body">
      <div class="aw-now" aria-live="polite">${what}</div>${linesHTML(w)}<div class="aw-t">${timesHTML(w)}</div></div></div>${hintHTML(w)}</div>`;
  };
  // the step in a word or two, where a title already names the work (the notes card's heading)
  St.waitShort = (w) => (!w ? "" : { prepare: t("Getting ready…"), send: t("Sending…"), wait: t("Reading…"), think: t("Thinking…"),
    write: t("Writing…"), retry: t("Trying again…") }[w.phase] || "");
  // one line: under an answer being written, in a plan's progress card and its row in the list (``bare``: its times
  // only, where the step is said next to it)
  St.waitLine = (w, opts = {}) => {
    if (!w) return "";
    const what = opts.bare && w.phase === "write" ? "" : doing(w);
    if (!what && !isPlan(w) && !opts.bare) return "";
    const last = (w.lines || []).slice(-1)[0];
    const think = last && ["think", "wait"].includes(w.phase) ? ` <span class="aw-think">${esc(t("Thinking: {line}", { line: last }))}</span>` : "";
    return `<span class="aw-line" data-phase="${esc(w.phase)}">${what ? `<span class="aw-now">${what}</span>${think} · ` : ""}<span class="aw-t">${timesHTML(w)}</span></span>`;
  };
  // the clocks: the time so far, the time left, a retry's countdown, the hint once it has waited a while
  function tick() {
    const n = now();
    document.querySelectorAll(".aw-since[data-since]").forEach((el) => { el.textContent = t("{time} so far", { time: clock(n - +el.dataset.since) }); });
    document.querySelectorAll(".aw-left[data-eta]").forEach((el) => {
      const left = +el.dataset.eta - (n - +el.dataset.at);
      el.textContent = left > 0 ? (el.dataset.step ? t("{left} for this step", { left: t.left(left) }) : t.left(left)) : t("taking longer than usual");
    });
    document.querySelectorAll(".aw-retry[data-until]").forEach((el) => { el.textContent = retryAct(+el.dataset.until, +el.dataset.n, +el.dataset.of); });
    document.querySelectorAll(".aw-hint[hidden][data-after]").forEach((el) => { if (n >= +el.dataset.after) el.hidden = false; });
    const chip = document.querySelector(".aw-chip .aw-chip-t");
    if (chip && chip.dataset.since) chip.textContent = clock(n - +chip.dataset.since);
  }
  St.waitTick = tick;
  setInterval(() => { if (!document.hidden) tick(); }, 1000);

  // ---------------------------------------------------------------- what is still running, for the top bar
  const running = new Map();          // key → { where, ai, since, go: what opens it, here: whether the page shows it }
  const PRACTICE = ["quiz", "cards"];  // a practice quiz or cards from files: runs as a plan, shown on its course's Practice tab
  function note(e) {
    const before = [...running.keys()].join("|");
    if (e.type === "ai") {
      if (e.phase === "start" || e.phase === "progress") running.set("ai", { where: "notes", ai: (e.wait || {}).ai, since: (e.wait || {}).since, day: e.day_key });
      else running.delete("ai");
    } else if (e.type === "digest") {
      if (e.phase === "start" || e.phase === "progress") running.set("digest", { where: "digest", ai: (e.wait || {}).ai, since: (e.wait || {}).since });
      else running.delete("digest");
    } else if (e.type === "plan") {
      if (e.status === "running" && e.wait) running.set(`plan:${e.id}`, { where: e.kind || e.wait.where || "videos", ai: e.wait.ai, since: e.wait.since, plan: e.id, course: e.course_id });
      else if (e.status !== "running" && e.status !== "queued") running.delete(`plan:${e.id}`);
    } else if (e.type === "chat") {
      if (e.state === "streaming") running.set(`chat:${e.chat_id}`, { where: "chat", ai: (e.wait || {}).ai, since: (e.wait || {}).since, chat: e.chat_id });
      else running.delete(`chat:${e.chat_id}`);
    } else return;
    if ([...running.keys()].join("|") !== before && St.renderTop) St.renderTop();
  }
  const prev = window.__lsEvent;
  window.__lsEvent = (e) => { prev(e); try { note(e); } catch (err) { console.error(err); } };

  function shownHere(x) {             // the page the student looks at already shows it
    const v = S.view || {};
    if (x.where === "notes") return v.name === "day" && S.day && S.day.day_key === x.day;
    if (x.where === "digest") return v.name === "digest";
    if (x.chat) return v.name === "chat" && S.chat && S.chat.data && S.chat.data.id === x.chat;
    if (PRACTICE.includes(x.where)) return v.name === "course" && v.courseId === x.course && v.tab === "practice";
    if (x.plan) return v.name === "plans" || (v.name === "plan" && v.planId === x.plan);
    return false;
  }
  function chipLabel(x) {
    const ai = x.ai || St.aiName();
    return { chat: t("{ai} is answering", { ai }), notes: t("{ai} is writing your notes", { ai }), digest: t("{ai} is writing your digest", { ai }),
      pack: t("Writing your study pack"), sheet: t("Writing your cheatsheet"), prep: t("Writing your review sheet"),
      videos: t("Building your video plan"), quiz: t("Writing your quiz"), cards: t("Making your cards") }[x.where] || t("{ai} is working", { ai });
  }
  St.waitChip = () => {
    const away = [...running.entries()].filter(([, x]) => !shownHere(x));
    if (!away.length) return "";
    const [key, x] = away[away.length - 1];
    const label = away.length > 1 ? t.n(away.length, "{n} AI task running", "{n} AI tasks running") : chipLabel(x);
    const since = Math.min(...away.map(([, y]) => y.since || now()));
    return `<button class="chip aw-chip" data-act="aw-open" data-key="${esc(key)}" title="${esc(t("Show it"))}">${St.SPIN()} <span>${esc(label)}</span>
      <span class="aw-chip-t" data-since="${since}">${clock(now() - since)}</span></button>`;
  };
  A["aw-open"] = (el) => {
    const x = running.get(el.dataset.key);
    if (!x) return;
    if (x.where === "notes" && x.day) St.openDay(x.day);
    else if (x.where === "digest") St.go({ name: "digest" });
    else if (x.chat) St.go({ name: "chat", chatId: x.chat });
    else if (PRACTICE.includes(x.where)) St.go({ name: "course", courseId: x.course, tab: "practice" });
    else if (x.plan) St.go({ name: "plan", planId: x.plan });
  };
})();
