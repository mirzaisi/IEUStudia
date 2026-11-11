/* IEUStudia — Settings: what students set up beyond the basics, each with a good default. The language of study
 * documents (and each document dialog's line naming it), Practice (how many questions a
 * new quiz starts with, new flashcards a day), when the reminders come and how often the background checks look,
 * and "Back to the defaults" for each group (config.GROUPS says what each puts back; never the speech model, the
 * folder, the AI service, the language or running in the background). The other cards are app.js's and setup.js's;
 * this file adds its rows to them through window.Studia. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, A, I, esc, call, toast } = St;

  // ---- the defaults (asked for once, when Settings is first drawn)
  let loading = false;
  function loadDefaults() {
    if (loading || S.setDefaults) return;
    loading = true;
    call("settings_defaults").then((r) => {
      loading = false;
      if (!r.ok) return;
      S.setDefaults = { defaults: r.defaults, reminders: r.reminders };
      if (S.view.name === "settings") St.renderView();
    });
  }
  const same = (a, b) => JSON.stringify(sorted(a)) === JSON.stringify(sorted(b));
  const sorted = (v) => (v && typeof v === "object" && !Array.isArray(v) ? Object.keys(v).sort().reduce((o, k) => { o[k] = sorted(v[k]); return o; }, {}) : v);
  const now = (where) => (where === "settings" ? S.settings || {} : where === "app" ? (S.boot || {}).app || {} : (S.setDefaults || {}).reminders || {});
  // a group that isn't as a fresh profile has it
  function changed(group) {
    const d = ((S.setDefaults || {}).defaults || {})[group];
    if (!d) return false;
    return Object.entries(d).some(([where, keys]) => Object.entries(keys).some(([k, v]) => !same(now(where)[k], v)));
  }
  St.setChanged = changed;          // (the tests)
  // the button in a card's heading: only when something there differs from the default
  St.resetBtn = (group) => {
    loadDefaults();
    return changed(group) ? `<button class="btn ghost sm set-reset" data-act="set-reset" data-group="${group}" title="${esc(t("Put this group's settings back as they were when you started"))}">${I("refresh", "sm")} ${t("Back to the defaults")}</button>` : "";
  };
  // what each group puts back, said before it does
  const NAME = () => ({ transcription: t("Transcription"), files: t("Files"), ai: t("AI"), practice: t("Practice"),
    notifications: t("Notifications"), appearance: t("Appearance") });
  const WHAT = () => ({
    transcription: t("Transcribe with: Automatic. Capture slides: on. Ask before closing: on. Recordings shorter than 1 minute are hidden. Your speech model stays as it is."),
    files: t("Subtitles: on. Keep downloaded audio: off. Your folder stays where it is."),
    ai: `${t("The models and reasoning levels go back to the recommended ones. Your AI service stays connected.")} ${t("Study documents go back to the course's language.")}`,
    practice: t("A new quiz starts with 10 questions, and up to 20 new flashcards a day."),
    notifications: t("New lectures, announcements and reminders: on. New files: off. Checks every 30 minutes. Reminders 2 days before a quiz or exam, and 1 day and 3 hours before a deadline. Running in the background stays as it is."),
    appearance: t("Theme: as Windows. Reading text: Normal. The language stays as it is."),
  });

  // ---- Practice
  const QUIZ_SIZES = [5, 10, 15, 20], PER_DAY = [10, 20, 30, 50];
  const row = (title, d, ctl, id) => `<div class="set-row"${id ? ` id="${id}"` : ""}><div class="txt"><div class="t">${title}</div><div class="d">${d}</div></div>${ctl}</div>`;

  // ---- AI: the language of study documents (doclang.py): the course's own (the default), English or Turkish. Only
  // with ChatGPT or Claude (the offline model writes English); chats answer in the language you write in.
  const LANG_NAME = () => ({ English: t("English"), Turkish: t("Turkish") });
  St.docLangRow = () => {
    const p = (S.system.ai || {}).provider || ((S.boot || {}).app || {}).ai_provider;
    if (!["chatgpt", "claude", "codex"].includes(p)) return "";
    const v = (S.settings || {}).doc_language || "course";
    const opts = [["course", t("The course's language")], ["en", t("English")], ["tr", t("Turkish")]];
    return row(t("Language of study documents"), t("Notes, quizzes, cards, study packs and the other documents the AI writes. Exam terms stay in the course's language either way."),
      `<div class="seg">${opts.map(([k, l]) => `<button class="${v === k ? "on" : ""}" data-act="set" data-key="doc_language" data-val="${k}" aria-pressed="${v === k}">${esc(l)}</button>`).join("")}</div>`, "set-doclang");
  };
  // each document dialog's quiet line: the language its estimate says the AI will write in ("" before the estimate)
  St.docLangLine = (e) => {
    if (!e || !e.language) return "";
    const L = LANG_NAME(), lang = L[e.language] || e.language;
    const say = e.taught_in && e.taught_in !== e.language ? t("Written in {language}, exam terms in {course}", { language: lang, course: L[e.taught_in] || e.taught_in })
      : t("Written in {language}", { language: lang });
    return `<div class="dim sm doclang-line">${I("globe", "xs")} ${esc(say)} · <button type="button" class="link" data-act="doclang-settings">${t("change in Settings")}</button></div>`;
  };
  St.practiceCard = () => {
    const s = S.settings || {}, q = +s.quiz_questions || 10, per = +s.cards_per_day || 20;
    const days = PER_DAY.includes(per) ? PER_DAY : [...PER_DAY, per].sort((a, b) => a - b);
    return `<div class="card set-card" id="set-practice"><h3>${t("Practice")}${St.resetBtn("practice")}</h3>
      ${row(t("Questions in a new quiz"), t("A practice quiz starts with this many. You can pick another number for each quiz."),
        `<div class="seg">${QUIZ_SIZES.map((n) => `<button class="${q === n ? "on" : ""}" data-act="set" data-key="quiz_questions" data-val="${n}">${n}</button>`).join("")}</div>`)}
      ${row(t("New flashcards a day"), t("For each course. Cards you have started come back when they are due, whatever this is."),
        `<select class="input" style="width:110px" data-act="set-sel" data-key="cards_per_day" aria-label="${esc(t("New flashcards a day"))}">${days.map((n) => `<option value="${n}" ${per === n ? "selected" : ""}>${n}</option>`).join("")}</select>`)}</div>`;
  };

  // ---- Notifications: how often the background checks look, and when the reminders come
  const EVERY = [15, 30, 60, 120];
  St.checkEveryRow = () => {
    const app = (S.boot || {}).app || {}, v = +app.sync_minutes || 30;
    const opts = EVERY.includes(v) ? EVERY : [...EVERY, v].sort((a, b) => a - b);
    return row(t("Check IEULearn every"), t("How often the background checks look for new lectures, files and announcements."),
      `<select class="input" style="width:130px" data-act="app-sel" data-key="sync_minutes" ${app.tray ? "" : "disabled"} aria-label="${esc(t("Check IEULearn every"))}">${opts.map((n) =>
        `<option value="${n}" ${v === n ? "selected" : ""}>${n < 60 ? esc(t("{m} min", { m: n })) : esc(t.n(n / 60, "{n} hour", "{n} hours"))}</option>`).join("")}</select>`);
  };
  // ---- Storage: after the update, the screens of lectures transcribed before are read in the background (screens.py,
  // its screens_reading events): a quiet line while it runs, its count going up, and nothing that pops up
  St.screensReadingRow = () => {
    const r = S.scrReading;
    if (!r) return `<div id="set-scr-reading" hidden></div>`;
    return `<div class="set-row" id="set-scr-reading"><div class="txt"><div class="t">${t("Reading the screens of earlier lectures")}</div>
      <div class="d">${t("So search, chats and quizzes can use the words on their slides and board. It runs in the background, a little at a time, and waits while a lecture is transcribed.")}</div></div>
      <span class="dim sm">${St.SPIN()} ${esc(t("Lectures read: {done} of {n}", { done: t.num(r.done), n: t.num(r.total) }))}</span></div>`;
  };
  St.onEvents.screens_reading = (e) => {
    S.scrReading = e.total && e.done < e.total ? { done: e.done, total: e.total } : null;
    const row = document.getElementById("set-scr-reading");
    if (row) row.outerHTML = St.screensReadingRow();             // (only the line: Settings isn't drawn again)
  };
  St.reminderRows = () => {
    const r = (S.setDefaults || {}).reminders, app = (S.boot || {}).app || {};
    if (!r) return "";
    const off = app.notify_reminders === false;
    const sel = (key, opts, label) => `<select class="input sm" data-act="set-rem" data-key="${key}" ${off ? "disabled" : ""} aria-label="${esc(label)}">${opts.map(([v, l]) => `<option value="${v}" ${+r[key] === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
    return `<div class="set-sub">${row(t("Quizzes and exams"), t("Announced ones, and the exam weeks of the syllabus."),
      `<div class="set-ctl">${sel("exams", [[0, t("Off")], [1, t("The evening before")], [2, t("2 days before")], [3, t("3 days before")], [7, t("A week before")]], t("Quizzes and exams"))}</div>`)}
      ${row(t("Assignments and your entries"), t("A first reminder some days before, and a second one hours before."),
        `<div class="set-ctl">${sel("due_days", [[0, t("Off")], [1, t("1 day before")], [2, t("2 days before")], [3, t("3 days before")]], t("First reminder"))}${sel("due_hours", [[0, t("Off")], [1, t("1 hour before")], [3, t("3 hours before")], [6, t("6 hours before")]], t("Second reminder"))}</div>`)}</div>`;
  };

  Object.assign(A, {
    // from a document dialog's line: Settings → AI, at the row (the dialog closes; nothing has started)
    "doclang-settings": () => {
      St.closeModal();
      St.go({ name: "settings" });
      setTimeout(() => { const r = document.getElementById("set-doclang") || document.getElementById("set-ai"); if (r) r.scrollIntoView({ block: "center" }); }, 60);
    },
    "set-reset": async (el) => {
      const g = el.dataset.group;
      if (!(await St.ask({ title: t("Back to the defaults for {group}?", { group: NAME()[g] || g }), text: WHAT()[g] || "", ok: t("Back to the defaults") }))) return;
      const r = await call("settings_reset", g);
      if (!r.ok) { toast(r.error, "error"); return; }
      if (r.settings) { S.settings = r.settings; St.applyTheme(); }
      if (r.app) S.boot.app = r.app;
      if (r.reminders) { S.setDefaults.reminders = r.reminders; if (S.cal && S.cal.data) S.cal.data.reminders = r.reminders; }
      toast(t("{group}: back to the defaults.", { group: NAME()[g] || g }), "success");
      if (S.view.name === "settings") St.renderView();
      St.renderTop();
    },
    "app-sel": async (el) => {
      const r = await call("app_config", { [el.dataset.key]: +el.value });
      if (!r.ok) { toast(r.error, "error"); return; }
      S.boot.app = r.app;
      if (S.view.name === "settings") St.renderView();
    },
    "set-rem": async (el) => {
      const r = await call("calendar_reminders", { [el.dataset.key]: +el.value });
      if (!r.ok) { toast(r.error, "error"); return; }
      S.setDefaults.reminders = r.reminders;
      if (S.cal && S.cal.data) S.cal.data.reminders = r.reminders;      // the Calendar's Reminders shows the same
      if (S.view.name === "settings") St.renderView();
    },
  });

  // ---- the browser preview (mock)
  St.mocks.settings_defaults = () => ({ ok: true, reminders: { exams: 2, due_days: 1, due_hours: 3 }, defaults: {
    transcription: { settings: { device: "auto", capture_slides: true, ask_before_closing: true, min_duration_s: 60 } },
    files: { settings: { write_srt: true, keep_audio: false } },
    ai: { settings: { ai_model: "sonnet", plan_model: "", verify_model: "haiku", ai_model_light: "haiku", ai_effort: {}, doc_language: "course" } },
    practice: { settings: { quiz_questions: 10, cards_per_day: 20 } },
    notifications: { app: { notify_recordings: true, notify_announcements: true, notify_materials: false, notify_reminders: true, sync_minutes: 30 },
      reminders: { exams: 2, due_days: 1, due_hours: 3 } },
    appearance: { settings: { theme: "system", text_size: 100 } } } });
  St.mocks.settings_reset = (g) => {
    const d = St.mocks.settings_defaults().defaults[g] || {};
    return { ok: true, settings: { ...S.settings, ...(d.settings || {}) }, app: d.app ? { ...S.boot.app, ...d.app } : undefined, reminders: d.reminders };
  };
})();
