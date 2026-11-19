/* IEUStudia — doing things from the chat: buttons for what the AI suggests (```ieustudia {json}``` blocks at the end of
 * an answer), "/" commands typed in the composer, and a row of quick actions above it. Nothing runs by itself:
 * every action opens the usual dialog or asks first. Plugs into app.js through window.Studia. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, call, esc, I, toast, go, A } = St;
  const $ = (s, el = document) => el.querySelector(s);

  const ICON = { video_plan: "youtube", prep_pack: "cap", get_ready: "list-checks", transcribe: "wave", download_files: "download",
    study_notes: "ai", open_lecture: "file-text", weekly_digest: "calendar", calendar: "calendar", sync_course: "zap", search: "search" };
  const found = {};                     // "seq:i" -> action
  const chatCourse = () => (S.chat.draft || S.chat.data || {}).course_id || "";
  const course = (cid) => St.findCourse(cid) || {};
  const weeksText = (w) => (!w || !w.length ? "" : w.length === 1 ? t("week {n}", { n: w[0] }) : t("weeks {a}–{b}", { a: Math.min(...w), b: Math.max(...w) }));
  const dayOf = (cid, date) => ((course(cid).days || []).find((d) => d.date === date && d.mine !== false) || (course(cid).days || []).find((d) => d.date === date) || {}).day_key;

  function describe(a, cid) {   // [title, what, button] of an action card
    const code = course(cid).code || t("this course");
    const w = weeksText(a.weeks);
    const focus = a.focus ? t("for {focus}", { focus: a.focus }) : "";
    return ({
      video_plan: [t("Video study plan"), [code, w, focus].filter(Boolean).join(" · "), t("Set it up…")],
      prep_pack: [t("Exam review sheet"), [code, w, focus].filter(Boolean).join(" · "), t("Set it up…")],
      get_ready: [t("Exam room"), `${code} · ${a.focus || t("the next quiz or exam")}`, t("Open the Exam room")],
      transcribe: [t("Transcribe lectures"), `${code} · ${a.date || w || t("missing lectures")}`, t("Transcribe…")],
      download_files: [t("Download files"), `${code} · ${w || t("all weeks")}`, t("Download…")],
      study_notes: [t("Study notes"), `${code} · ${a.date || ""}`, t("Open")],
      open_lecture: [t("Open the lecture"), `${code} · ${a.at ? t("{date} at {time}", { date: a.date || "", time: a.at }) : a.date || ""}`, t("Open")],
      weekly_digest: [t("Weekly digest"), t("What happened this week and what is coming"), t("Open")],
      calendar: [t("Add to calendar"), t("The announced quizzes and exams as a calendar file"), t("Save")],
      sync_course: [t("Sync this course"), t("{course}: check IEULearn, transcribe and download what is missing", { course: code }), t("Review…")],
      search: [t("Search"), a.query || "", t("Search")],
    })[a.action];
  }

  // ---------------------------------------------------------------- "/quiz": a quiz from the chat (chatquiz.py)
  // The answer to "/quiz" (or to "Quiz me on this") is a sentence and the quiz's JSON block: while it is written, the
  // sentence and a "Writing your quiz" card (never the half-written block); once it is done and saved, a card to play
  // it. A quiz that could not be read leaves the answer as it was written, with a line that says so.
  const canQuiz = () => { const ai = S.system.ai || {}; return !!ai.available && ai.can_plans !== false; };
  const messages = () => ((S.chat && S.chat.data) || {}).messages || [];
  function quizTurn(seq) {            // the answer to a "/quiz": what was asked ("/quiz 5 Thevenin"), else ""
    const all = messages(), i = all.findIndex((x) => x.seq === seq), q = i > 0 ? all[i - 1] : null;
    return q && q.role === "user" && /^\s*\/quiz\b/i.test(q.text || "") ? q.text.trim() : "";
  }
  // a quiz stopped or unread under the last answer: the same "/quiz" again in one click (else typed again)
  function quizAgain(asked, m, said, hint) {
    const all = messages(), last = all.length && all[all.length - 1].seq === (m || {}).seq;
    const btn = last && canQuiz() ? `<div style="margin-top:8px"><button class="btn sm" data-act="chat-suggest" data-text="${esc(asked)}">${I("retry", "sm")} ${t("Try again")}</button></div>` : "";
    return `<div class="note-sm">${I("info", "sm")}<span>${esc(btn ? said : hint)}${btn}</span></div>`;
  }
  function quizCard(text, m, streaming, asked) {
    const u = (m && m.usage) || {}, made = u.quiz, cut = text.indexOf("```");
    // the sentence before the quiz (its first paragraph: never a question or its answer, however the AI wrote it)
    const intro = (cut >= 0 ? text.slice(0, cut) : text.replace(/[{[][\s\S]*$/, "")).trim().split(/\n\s*\n/)[0].trim();
    if (streaming) {
      return { text: intro, html: `<div class="act-cards"><div class="act-card qz-chat">${St.SPIN ? St.SPIN() : ""}<div class="grow"><b>${esc(t("Writing your quiz…"))}</b><div class="d">${esc(t("You can play it here when it's done. It's saved to Practice too."))}</div></div></div></div>` };
    }
    // (noCopy: its text is the quiz's JSON, whole or cut, nothing to copy; an unread one keeps its Copy)
    if (made) {
      return { noCopy: true, text: intro, html: `<div class="act-cards"><div class="act-card qz-chat"><span class="act-ico">${I("list-checks", "sm")}</span><div class="grow"><b>${esc(made.title || t("Practice quiz"))}</b>
        <div class="d">${esc(t.n(made.questions, "{n} question", "{n} questions"))} · ${esc(t("saved to Practice"))}</div></div>
        <button class="btn primary sm" data-act="qz-play" data-id="${esc(made.id)}">${I("play", "sm")} ${t("Play quiz")}</button></div></div>` };
    }
    if (m && m.status === "stopped") return { noCopy: true, text: intro, html: quizAgain(asked, m, t("Stopped before the quiz was finished."), t("Stopped before the quiz was finished. Type /quiz to try again.")) };
    return { text, html: u.quiz_error ? quizAgain(asked, m, t("The quiz in this answer could not be read, so it wasn't saved."), t("The quiz in this answer could not be read, so it wasn't saved. Type /quiz to try again.")) : "" };
  }
  // "Quiz me on this" under the last answer: a quiz on what the chat just covered (a "/quiz" sent as a question)
  function quizChip(seq, m, streaming) {
    const all = messages(), last = all[all.length - 1];
    if (streaming || !m || m.status !== "done" || !last || last.seq !== seq || !canQuiz()) return "";
    return `<div class="qz-chip-row"><button class="btn ghost sm" data-act="chat-quiz" title="${esc(t("A few practice questions on this answer, scored and saved to Practice"))}">${I("list-checks", "sm")} ${t("Quiz me on this")}</button></div>`;
  }

  // ---------------------------------------------------------------- AI-suggested actions in answers
  // m: the message (its usage says whether its "/quiz" was saved)
  St.actionCards = (text, seq, streaming, m) => {
    const asked = text ? quizTurn(seq) : "";
    if (asked) return quizCard(text, m, streaming, asked);
    if (!text || !text.includes("```ieustudia")) return { text, html: text ? quizChip(seq, m, streaming) : "" };
    const cards = [];
    const clean = text.replace(/```ieustudia\s*([\s\S]*?)(```|$)/g, (_, body) => {
      if (streaming) return "";
      try {
        const a = JSON.parse(body.trim());
        if (a && describe(a, chatCourse())) cards.push(a);
      } catch (e) { /* not an action */ }
      return "";
    });
    const cid = chatCourse();
    const html = cards.length ? `<div class="act-cards">${cards.map((a, i) => {
      found[`${seq}:${i}`] = a;
      const [title, d, label] = describe(a, cid);
      return `<div class="act-card"><span class="act-ico">${I(ICON[a.action] || "zap", "sm")}</span><div class="grow"><b>${esc(title)}</b><div class="d">${esc(d)}</div></div>
        <button class="btn primary sm" data-act="chat-action" data-key="${seq}:${i}">${esc(label)}</button></div>`;
    }).join("")}</div>` : "";
    return { text: clean.trim(), html: html + quizChip(seq, m, streaming) };
  };

  async function detail(cid) {
    if (!S.cd[cid]) { const r = await call("course_detail", cid); if (r.ok) S.cd[cid] = r.course; }
    return S.cd[cid];
  }
  async function run(a, cid) {
    cid = cid || chatCourse();
    const d = ["transcribe", "download_files", "get_ready", "video_plan", "prep_pack"].includes(a.action) ? await detail(cid) : null;
    const assess = d && a.focus ? ((d.alerts || {}).plan || []).find((x) => x.name.toLowerCase() === String(a.focus).toLowerCase()) : null;
    switch (a.action) {
      case "video_plan": case "prep_pack":
        return A["plan-new"]({ dataset: { id: cid, kind: a.action === "prep_pack" ? "prep" : "videos", focus: assess ? assess.name : "", weeks: (a.weeks || []).join(",") } });
      case "get_ready": {            // its Exam room (examroom.js): the syllabus's exams have one too
        const all = (d || {}).assessments || [], named = a.focus ? all.filter((x) => x.name.toLowerCase() === String(a.focus).toLowerCase()) : [];
        const e = named.find((x) => x.ahead) || named[0] || all.find((x) => x.ahead) || all[all.length - 1];
        if (!e) { toast(t("No quiz or exam has been announced for this course yet."), "info"); return; }
        return A["ready-open"]({ dataset: { cid, key: e.key, name: e.name } });
      }
      case "transcribe": {
        const weeks = new Set(a.weeks || []);
        const keys = a.date ? [dayOf(cid, a.date)].filter(Boolean)
          : (d ? d.weeks.filter((w) => weeks.has(w.no)).flatMap((w) => w.days.filter((x) => x.mine !== false && !x.transcribed && !x.no_sound).map((x) => x.day_key)) : []);
        if (!keys.length) { toast(t("Those lectures are already transcribed (or not recorded yet)."), "info"); return; }
        if (!(await St.ask({ title: t.n(keys.length, "Transcribe {n} class day of {course} on this PC?", "Transcribe {n} class days of {course} on this PC?", { course: course(cid).code || t("this course") }),
          ok: t("Transcribe") }))) return;
        let n = 0;
        const left = { unknown: 0, needs_section: false };     // only your section's lectures (catchup.js)
        for (const k of keys) { const r = await call("transcribe_day", k, !a.date); if (r.ok) { n += r.queued || 0; left.unknown += r.unknown || 0; left.needs_section = left.needs_section || !!r.needs_section; } }   // a day you named queues also when it matches no section
        if (n || !(left.unknown || left.needs_section)) {
          toast(n ? t.n(n, "{n} recording queued", "{n} recordings queued") : t("Those lectures are already transcribed (or not recorded yet)."), n ? "success" : "info");
        }
        if (St.sectionLeftOut) St.sectionLeftOut(course(cid).code || t("this course"), left);
        return;
      }
      case "download_files": {
        const weeks = new Set(a.weeks || []);
        const ids = d ? [...new Set(d.weeks.filter((w) => !weeks.size || weeks.has(w.no)).flatMap((w) => w.units.flatMap((u) => u.materials)).filter((m) => !m.downloaded && !m.gone).map((m) => m.id))] : [];
        if (!ids.length) { toast(t("Those files are already on this PC."), "info"); return; }
        const r = await call("materials_download", ids);
        if (r.ok) toast(t.n(ids.length, "Downloading {n} file", "Downloading {n} files"), "success");
        return;
      }
      case "study_notes": case "open_lecture": {
        const k = a.date && dayOf(cid, a.date);
        if (!k) { toast(t("That lecture was not found in this course."), "info"); return; }
        await St.openDay(k);
        if (a.action === "study_notes") setTimeout(() => A.claude(), 300);
        else if (a.at) {             // "mm:ss", or "h:mm:ss" past the first hour (as the transcript's labels read)
          const secs = String(a.at).split(":").reduce((s, x) => s * 60 + (+x || 0), 0);
          setTimeout(() => St.jumpTo(+a.part || 1, secs), 300);
        }
        return;
      }
      case "weekly_digest": return go({ name: "digest" });
      case "calendar": return A["export-calendar"]();
      case "sync_course": return A["course-sync"]({ dataset: { id: cid } });
      case "search": go({ name: "library" }); setTimeout(() => { const box = $("[data-act='search']"); if (box) { box.value = a.query || ""; St.runSearch(a.query || ""); } }, 200); return;
      default: toast(t("IEUStudia cannot do that yet."), "info");
    }
  }
  St.runAction = run;

  // ---------------------------------------------------------------- "/" commands and quick actions
  // the command words are typed, so they stay English; the descriptions are shown (made when drawn, after the table arrives)
  const CMDS = () => [
    ["quiz", "quiz", t("A practice quiz on this chat, scored and saved to Practice (add how many questions)")],
    ["plan", "video_plan", t("Video study plan for this course (add a quiz name or weeks)")],
    ["prep", "prep_pack", t("Exam review sheet (add a quiz or exam name)")],
    ["ready", "get_ready", t("The Exam room of the next quiz or exam")],
    ["notes", "study_notes", t("Study notes for this lecture")],
    ["transcribe", "transcribe", t("Transcribe lectures (add week numbers)")],
    ["files", "download_files", t("Download the files (add week numbers)")],
    ["sync", "sync_course", t("Sync this course: transcribe and download what is missing")],
    ["digest", "weekly_digest", t("This week in all your courses")],
    ["calendar", "calendar", t("Put the announced quizzes and exams in your calendar")],
    ["search", "search", t("Search your lectures and files")],
  ];
  function parse(text) {
    const m = /^\/(\w+)\s*(.*)$/s.exec(text.trim());
    if (!m) return null;
    const c = CMDS().find(([k]) => k === m[1].toLowerCase());
    if (!c) return null;
    const rest = m[2].trim();
    // the assessment names are English (as on IEULearn): "/prep ara sınav" means the midterm
    const named = rest.replace(/ara\s*s[ıi]nav[ıi]?|vize/gi, "midterm").replace(/s[ıi]nav[ıi]?/gi, "exam")
      .replace(/[öo]dev[iı]?/gi, "homework").replace(/proje(si)?/gi, "project").replace(/\bfinali\b/gi, "final");
    const weeks = (rest.match(/\b\d{1,2}\b/g) || []).map(Number).filter((n) => n > 0 && n < 20);
    const a = { action: c[1] };
    if (weeks.length && /week|hafta|^\d|\s\d/i.test(rest) && !/quiz|exam|midterm|final/i.test(named)) a.weeks = weeks;
    if (/quiz|exam|midterm|final|project/i.test(named)) a.focus = named.replace(/\b(for|the)\b/gi, "").replace(/\s+/g, " ").trim().replace(/^\w/, (x) => x.toUpperCase());
    if (c[1] === "search") a.query = rest;
    if (c[1] === "study_notes") {
      const days = (S.chat.data || S.chat.draft || {}).days || [];
      const day = typeof days[0] === "string" ? days[0] : (days[0] || {}).key;
      if (day) a.date = String(day).split("|")[1];
    }
    return a;
  }
  St.slashCommand = (text) => {
    const a = parse(text);
    if (!a) return false;
    if (a.action === "quiz") {            // "/quiz [n]": sent to the AI as it is (chat.py makes it the quiz's turn)
      if (canQuiz()) return false;
      toast((S.system.ai || {}).provider === "local" ? t("Quizzes need ChatGPT or Claude (the offline model is too small for them).") : St.aiHint(), "info");
      return true;
    }
    run(a);
    return true;
  };
  St.chatToolsHTML = () => {
    const cid = chatCourse(); if (!cid) return "";
    const ai = S.system.ai || {};
    const btn = (cmd, ic, label, title) => `<button type="button" class="btn ghost sm" data-act="chat-cmd" data-cmd="${cmd}" title="${esc(title)}">${I(ic, "sm")} ${label}</button>`;
    const d = S.cd[cid], exams = !d || (d.assessments || []).length > 0;      // (a course with no quiz or exam: no room)
    return `<div class="chat-tools">${St.slidePickHTML ? St.slidePickHTML() : ""}${ai.can_plans !== false ? btn("plan", "youtube", t("Video plan"), t("Find videos that teach these weeks, checked through their transcripts")) + btn("prep", "cap", t("Review sheet"), t("A written exam review sheet: key ideas, formulas and practice")) : ""}
      ${exams ? btn("ready", "cap", t("Exam room"), t("The Exam room of the next quiz or exam")) : ""}${btn("sync", "zap", t("Sync course"), t("Transcribe and download what is missing"))}
      <span class="dim sm" style="align-self:center">${t("or type / for more")}</span></div>`;
  };

  // the "/" menu while typing
  let menuIndex = 0;
  function menuItems(v) { const q = v.slice(1).toLowerCase(); return CMDS().filter(([k]) => k.startsWith(q.split(/\s/)[0])); }
  function drawMenu() {
    const ta = $("#chat-input"); const box = ta && ta.closest(".composer-inner"); if (!box) return;
    let m = $(".cmd-menu", box);
    const v = ta.value;
    const items = v.startsWith("/") && !/\s/.test(v) ? menuItems(v) : [];
    if (!items.length) { if (m) m.remove(); return; }
    if (!m) { m = document.createElement("div"); m.className = "cmd-menu"; box.appendChild(m); }
    menuIndex = Math.min(menuIndex, items.length - 1);
    m.innerHTML = items.map(([k, , d], i) => `<button type="button" class="${i === menuIndex ? "on" : ""}" data-act="chat-cmd-pick" data-cmd="${k}"><span class="k">/${k}</span><span class="d">${esc(d)}</span></button>`).join("");
  }
  document.addEventListener("input", (e) => { if (e.target && e.target.id === "chat-input") { menuIndex = 0; drawMenu(); } });
  document.addEventListener("keydown", (e) => {
    const m = $(".cmd-menu"); if (!m || !e.target || e.target.id !== "chat-input") return;
    const items = menuItems(e.target.value);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); menuIndex = (menuIndex + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length; drawMenu(); }
    else if (e.key === "Tab" || (e.key === "Enter" && items.length && !e.target.value.includes(" "))) {
      e.preventDefault(); e.stopImmediatePropagation();
      e.target.value = `/${items[menuIndex][0]} `; S.chat.input = e.target.value; drawMenu();
    } else if (e.key === "Escape") { m.remove(); }
  }, true);

  Object.assign(A, {
    "chat-action": (el) => { const a = found[el.dataset.key]; if (a) run(a); },
    "chat-cmd": (el) => { const a = parse(`/${el.dataset.cmd}`); if (a) run(a); },
    "chat-cmd-pick": (el) => { const ta = $("#chat-input"); if (ta) { ta.value = `/${el.dataset.cmd} `; S.chat.input = ta.value; ta.focus(); drawMenu(); } },
    "chat-quiz": () => A["chat-suggest"]({ dataset: { text: "/quiz" } }),        // sent as a question (sendChat)
  });
})();
