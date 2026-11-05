/* IEUStudia UI. Talks to Python through window.pywebview.api (real) or an in-file mock (browser). */
(function () {
  "use strict";

  // ------------------------------------------------------------------ helpers
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const I = (n, c = "") => (window.LSIcons ? LSIcons.svg(n, c) : "");
  const SPIN = () => (window.LSIcons ? LSIcons.spinner("spin") : "…");
  const fmtDur = t.dur;                                   // 1 h 5 min · 1 sa 5 dk
  const pct = (p) => `${Math.round((p || 0) * 100)}%`;    // a CSS width: not text (text uses t.pct)
  const scaleX = (p) => `scaleX(${Math.round((p || 0) * 100) / 100})`;   // the queue's bars: the same, as a transform
  const pctText = (p) => t.pct(Math.round((p || 0) * 100) / 100);   // 45% · %45, rounded like the width
  const leftText = t.left;
  const jobDetail = (j) => [j.detail, leftText(etaNow(j))].filter(Boolean).join(" · ");   // "Transcribing passage 40 of 600 · about 25 min left" (counting down)
  const rel = t.ago;
  const initials = (n) => t.upper((n || "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join(""));   // in Turkish i → İ

  // ------------------------------------------------------------------ state
  const S = {
    mode: "loading", boot: null, profile: null, profiles: [], profileId: "main", settings: {}, courses: [], queue: [], lastSync: null,
    system: { gpu: null, presets: [], ai: { available: false }, engine: {} },
    view: { name: "new" }, selected: new Set(), expanded: new Set(),
    sync: { running: false, message: "", error: "" }, engine: null,
    day: null, dayFind: "", ai: { running: false, text: "", dayKey: null },
    lib: null, search: { q: "", results: null }, signin: { busy: false, error: "" }, exporting: null,
    chat: { list: null, id: null, data: null, draft: null, input: "", sending: false, scroll: null }, limits: {},
    update: {},      // a new version: updates.status(), plus state/done/total/error while it downloads
  };
  let REAL = false;
  // Extension points: views/events/actions from the other UI files (course.js, plans.js) plug in here.
  const Studia = window.Studia = { views: {}, onEvents: {}, mocks: {}, after: {}, onGo: {}, crumbs: {} };

  // ------------------------------------------------------------------ API bridge
  function call(name, ...args) {
    if (REAL) return window.pywebview.api[name](...args).catch((e) => ({ ok: false, error: String(e) }));
    const fn = Mock[name] || Studia.mocks[name];
    return Promise.resolve(fn ? fn(...args) : { ok: false, error: "not in mock" });
  }
  window.__lsEvent = (e) => { try { onEvent(e); } catch (err) { console.error(err); reportError("event " + (e && e.type), err); } };

  // script errors go to the app's log (they are otherwise invisible in the desktop window)
  let reported = 0;
  function reportError(kind, err) {
    if (!REAL || reported >= 20) return;
    reported++;
    const message = err && err.message ? err.message : String(err);
    call("log_client_error", { kind, message: message.slice(0, 500), stack: String((err && err.stack) || "").slice(0, 2000), view: S.view && S.view.name });
  }
  window.addEventListener("error", (e) => reportError("error", e.error || `${e.message} (${e.filename}:${e.lineno}:${e.colno})`));
  window.addEventListener("unhandledrejection", (e) => reportError("promise", e.reason));

  // ------------------------------------------------------------------ theme
  function applyTheme() {
    const theme = S.settings.theme || "system";
    try { localStorage.setItem("studia-theme", theme); } catch (e) { /* storage may be unavailable */ }
    const dark = theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    document.documentElement.style.setProperty("--read", String((+S.settings.text_size || 100) / 100));   // reading text
  }
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);

  // ------------------------------------------------------------------ events from Python
  function onEvent(e) {
    switch (e.type) {
      case "courses":
        setCourses(e.courses); renderSidebar();
        if (Studia.onEvents.courses) Studia.onEvents.courses(e);
        else if (["course", "new"].includes(S.view.name)) renderView();
        break;
      case "job": onJob(e); break;
      case "queue": {
        const had = new Map((S.queue || []).map((j) => [j.id, j]));
        if (S.paused && !e.paused && S.pausedAt) had.forEach((j) => { if (j._etaAt) j._etaAt += performance.now() - S.pausedAt; });   // the time left stood still while paused
        S.queue = e.jobs || []; S.queue.forEach((j) => stamp(j, had.get(j.id)));
        S.batch = e.batch || {}; S.batch._at = performance.now();
        if (!!e.paused !== !!S.paused) S.pausedAt = e.paused ? performance.now() : 0;
        S.paused = !!e.paused; renderQueue(); break;
      }
      case "engine": S.engine = e.phase === "ready" ? null : e; renderQueue(); break;
      case "sync": onSync(e); break;
      case "system":
        S.system = { ...S.system, ...e }; if (S.view.name === "settings") renderView(); renderTop();
        renderSidebar();                                    // "Ask {ai}": the start-up snapshot may name another AI
        if (S.view.name === "day") renderDayActions();
        if (S.view.name === "chat" && !$("#chat-input")) renderView();   // Claude availability is now known
        break;
      case "toast": toast(e.message, e.level); break;
      case "open": openTarget(e.target); break;
      case "update": onUpdate(e); break;
      case "export": onExport(e); break;
      case "ai": onAi(e); break;
      case "chat": onChat(e); break;
      default: if (Studia.onEvents[e.type]) Studia.onEvents[e.type](e);
    }
  }
  function onSync(e) {
    if (e.phase === "start") { S.sync = { running: true, message: e.message, error: "" }; }
    else if (e.phase === "progress") { S.sync.message = e.message; }
    else if (e.phase === "done") {
      S.sync = { running: false, message: e.message, error: "" }; S.lastSync = e.last_sync || new Date().toISOString();
      if (e.profile) S.profile = e.profile;
      if (e.new_count) toast(t.n(e.new_count, "{n} new recording found", "{n} new recordings found"), "success");
      if (!e.quiet) (e.errors || []).forEach((m) => toast(m, "warning"));   // the app's own half-hourly sync stays quiet
      renderSidebar();
      if (S.view.name === "home") renderView();          // its greeting line said "Syncing…"
    } else if (e.phase === "error") {
      S.sync = { running: false, message: "", error: e.message };
      if (S.view.name === "home" && S.mode === "app") renderView();
      if (e.quiet) { renderTop(); return; }          // the app's own half-hourly sync: the top bar shows it, nothing pops up
      if (e.need_login) { S.mode = "signin"; S.signin.error = e.message; render(); return; }
      toast(e.message || t("Sync failed"), "error");
    }
    renderTop();
  }
  function findRec(id) {
    for (const c of [...S.courses, ...(S.earlier || [])]) for (const d of c.days) for (const r of d.recordings) if (r.id === id) return { c, d, r };
    return null;
  }
  function onJob(e) {
    const q = !e.final && (S.queue || []).find((j) => j.id === e.id);
    if (q) { const old = { eta_s: q.eta_s, _etaAt: q._etaAt }; Object.assign(q, e); stamp(q, old); updateQueueBar(q); if (S.qpOpen) qpUpdate(q); }
    if (e.final) qpFinished(e);
    const hit = findRec(e.id);
    if (hit) {
      const r = hit.r;
      if (e.final) {
        r.progress = null; r.detail = null;
        if (e.stage === "done") {
          r.status = "done"; r.is_new = false;
          const parts = hit.d.recordings.filter((x) => x.part && !x.hidden && ownRec(hit.c, x));   // its parts (your section's)
          if (!parts.some((x) => ["queued", "downloading", "transcribing"].includes(x.status))) {   // its last one
            const whole = parts.every((x) => x.status === "done") || !r.part;
            const v = { course: hit.c.code, date: shortDate(hit.d.date), part: r.part };
            toast(hit.d.kind === "lab"
              ? (whole ? t("{course} · {date}: the lab is ready to read", v) : t("{course} · {date}: part {part} of the lab is ready to read", v))
              : (whole ? t("{course} · {date}: the lecture is ready to read", v) : t("{course} · {date}: part {part} of the lecture is ready to read", v)),
              "success", { label: t("Read"), act: "open-day", data: { day: hit.d.day_key } });
          }
        }
        else if (e.stage === "failed" && e.status === "done") { r.status = "done"; toast(t("{title} could not be transcribed again: {error}. The earlier transcript is kept.", { title: e.title, error: (e.error || t("it failed")).replace(/[.\s]+$/, "") }), "warning"); }
        else if (e.stage === "failed") { r.status = "failed"; r.error = e.error; r.no_sound = !!e.no_sound; toast(`${e.title}: ${e.error || t("failed")}`, "error"); }
        else if (e.stage === "processing") { r.status = "processing"; r.error = e.error; toast(e.error || t("Not ready on Panopto yet"), "warning"); }
        if (S.view.name === "day" && S.day && S.view.dayKey === hit.d.day_key) openDay(S.view.dayKey, true);
        refreshNewDay(hit, false);
      } else {
        const before = r.status;
        r.status = { queued: "queued", downloading: "downloading", transcribing: "transcribing", writing: "transcribing" }[e.stage] || r.status;
        r.progress = e.progress; r.detail = e.detail; r.eta_s = e.eta_s;
        const busy = (s) => ["queued", "downloading", "transcribing"].includes(s);
        refreshNewDay(hit, busy(r.status) === busy(before));
        if (busy(r.status) !== busy(before)) {   // day buttons depend on it: redraw the whole card once
          const card = document.querySelector(`.day[data-day="${CSS.escape(hit.d.day_key)}"]`);
          if (card) { card.outerHTML = dayCardHTML(hit.d, hit.c); syncChecks(); }
          return;
        }
      }
      updateRow(r);
    } else if (e.final && e.local) {
      toast(e.stage !== "done" ? `${e.title}: ${e.error || ({ failed: t("failed"), cancelled: t("cancelled") }[e.stage] || e.stage)}`
        : e.unwritten ? t("Transcribed {title}, but its file could not be written to your IEUStudia folder yet: IEUStudia tries again the next time it starts.", { title: e.title })
        : t("Transcribed {title} (see \"Local files\" in your IEUStudia folder)", { title: e.title }), e.stage !== "done" ? "error" : e.unwritten ? "warning" : "success");
    }
  }
  function onExport(e) {
    if (e.phase === "start") { S.exporting = e.day_key; toast(t("Making the PDF, with the slides next to the transcript…"), "info"); }
    else if (e.phase === "done") { S.exporting = null; toast(t("PDF ready — shown in Explorer. You can also upload it to ChatGPT or Claude."), "success"); if (S.view.name === "day") openDay(S.view.dayKey, true); }
    else { S.exporting = null; toast(e.message || t("Export failed"), "error"); }
    if (S.view.name === "day") renderDayActions();
  }
  function onAi(e) {   // study notes being written ("start" may be coalesced away)
    if (e.phase === "start" || e.phase === "progress") S.ai = { running: true, text: e.text || "", dayKey: e.day_key, kind: e.kind || "notes", label: e.label || t("Study notes") };
    else if (e.phase === "done") {
      S.ai = { running: false, text: "", dayKey: e.day_key, kind: e.kind, label: e.label };
      toast(t("{label} saved next to the transcript.", { label: e.label || t("Study notes") }), "success");
      if (S.day && S.day.day_key === e.day_key) { S.day.notes = [{ kind: e.kind, label: e.label, md: e.text, cards: e.cards }, ...(S.day.notes || []).filter((n) => n.kind !== e.kind)]; S.notesKind = e.kind; }
    } else if (e.phase === "error") { S.ai.running = false; if (e.cancelled) toast(t("Stopped."), "info"); else toast(e.message || t("The AI could not finish"), "error"); }
    if (S.view.name === "day") renderNotes();
  }
  function onChat(e) {   // every event is a full snapshot of one answer
    if (e.limits && Object.keys(e.limits).length) S.limits = e.limits;
    const c = S.chat.data;
    const here = c && c.id === e.chat_id;
    if (here) {
      let m = c.messages.find((x) => x.seq === e.seq);
      if (!m) { m = { seq: e.seq, role: "assistant", text: "", status: "streaming", usage: {} }; c.messages.push(m); c.messages.sort((a, b) => a.seq - b.seq); }
      m.text = e.text || ""; m.status = e.state;
      if (e.usage) m.usage = e.usage;
      if (e.error) m.usage = { ...(m.usage || {}), error: e.error };
      c.running = e.state === "streaming";
      if (S.view.name === "chat") {
        if (!renderChatMsg(m)) renderChatMsgs();
        if (e.state !== "streaming") { renderComposerBtn(); renderChatHead(); }
      }
    }
    if (e.state !== "streaming") {
      if (e.state === "error" && !(here && S.view.name === "chat")) toast(e.error || t("{ai} could not answer", { ai: aiName() }), "error");
      loadChats();
    }
  }

  // ------------------------------------------------------------------ start-up screen (markup lives in index.html)
  function setSplash(msg) { const el = $("#splash-status"); if (el) el.textContent = msg; }
  function dismissSplash() {   // keep it on top of the freshly rendered app while it fades out
    const sp = $("#splash");
    if (!sp || sp.classList.contains("out")) return;
    document.body.appendChild(sp);
    requestAnimationFrame(() => { sp.classList.add("out"); setTimeout(() => sp.remove(), 320); });
  }
  function startupError(msg, logs = true) {
    const sp = $("#splash");
    if (!sp) { $("#root").innerHTML = `<div class="signin"><div class="card signin-card"><h3>${t("Something went wrong")}</h3><p>${esc(msg)}</p></div></div>`; return; }
    sp.querySelector(".splash-bar").remove();
    setSplash("");
    sp.insertAdjacentHTML("beforeend", `<div class="splash-error">${esc(msg)}</div>
      <div class="splash-actions"><button class="btn primary" data-act="reload">${I("retry", "sm")} ${t("Try again")}</button>${logs ? `<button class="btn" data-act="logs">${I("terminal", "sm")} ${t("Open logs")}</button>` : ""}</div>`);
  }

  // ------------------------------------------------------------------ shell
  function render() {
    applyTheme();
    const root = $("#root");
    if (S.mode === "loading") { if (!$("#splash")) root.innerHTML = `<div class="signin"><div class="empty">${SPIN()}</div></div>`; return; }
    dismissSplash();
    if (S.mode === "setup") { root.innerHTML = Studia.setupHTML ? Studia.setupHTML() : ""; return; }
    if (S.mode === "signin") { root.innerHTML = signinHTML(); setTimeout(() => $("#si-user") && $("#si-user").focus(), 30); return; }
    root.innerHTML = `
      <div class="app">
        <aside class="sidebar">
          <button class="brand" data-act="go" data-view="home" title="${t("Home")}"><img class="logo-img" src="../assets/logo.svg" alt="">IEUStudia</button>
          <nav class="nav" id="nav"></nav>
          <div class="sidebar-footer" id="sfoot"></div>
        </aside>
        <main class="main">
          <header class="topbar" id="top"></header>
          <section class="view" id="view"></section>
          <footer class="queuebar hidden" id="queue"></footer>
        </main>
      </div>`;
    renderSidebar(); renderTop(); renderView(); renderQueue();
  }

  function courseSub(c) {   // two pages of one course (FENG 495 twice): tell them apart by section or lecturer
    const twins = S.courses.filter((x) => x.code === c.code).length > 1;
    return twins ? `${c.section ? t("Section {tag}", { tag: c.section }) : c.instructor || c.id} · ${c.display_name}` : c.display_name;
  }
  function newCount() { return S.courses.reduce((n, c) => n + (c.counts.new || 0), 0); }
  const courseNew = (c) => (c.counts.new || 0) + ((c.content || {}).materials_new || 0) + ((c.content || {}).announcements_new || 0);
  // the courses on your IEULearn dashboard; S.earlier: ones that left it but hold your transcripts, files, chats or plans
  function setCourses(list) { S.courses = (list || []).filter((c) => !c.inactive); S.earlier = (list || []).filter((c) => c.inactive); }
  const findCourse = (id) => S.courses.find((x) => x.id === id) || (S.earlier || []).find((x) => x.id === id);

  function renderSidebar() {
    const nav = $("#nav"); if (!nav) return;
    const v = S.view;
    const nn = S.courses.reduce((n, c) => n + courseNew(c), 0);
    let h = `<button class="nav-item ${v.name === "home" || v.name === "new" ? "active" : ""}" data-act="go" data-view="home">${I("home")}<span class="label"><span class="code">${t("Home")}</span></span>${nn ? `<span class="count new">${nn}</span>` : ""}</button>`;
    h += `<div class="nav-section">${t("Courses")}</div>`;
    if (!S.courses.length) h += `<div class="nav-item" style="cursor:default"><span class="label"><span class="sub">${S.sync.running ? t("Loading your courses…") : t("No courses yet")}</span></span></div>`;
    const item = (c, dim) => {
      const active = v.name === "course" && v.courseId === c.id;
      const n = dim ? 0 : courseNew(c);
      const badge = n ? `<span class="count new" title="${t("New recordings, materials and announcements")}">${n}</span>` : "";
      const sub = courseSub(c);
      return `<button class="nav-item ${active ? "active" : ""} ${dim ? "earlier" : ""}" data-act="go" data-view="course" data-id="${esc(c.id)}" title="${esc(c.code + " — " + sub)}">
        <span class="label"><span class="code">${esc(c.code)}</span><span class="sub">${esc(sub)}</span></span>${badge}</button>`;
    };
    h += S.courses.map((c) => item(c, false)).join("");
    if ((S.earlier || []).length) h += `<div class="nav-section" title="${t("No longer on your IEULearn dashboard. What you saved from them stays here.")}">${t("Earlier courses")}</div>` + S.earlier.map((c) => item(c, true)).join("");
    // the Study group stays in view at the bottom when many courses make the list scroll
    h += `<div class="nav-study"><div class="nav-section">${t("Study")}</div>
      <button class="nav-item ${v.name === "chat" ? "active" : ""}" data-act="go" data-view="chat">${I("chat")}<span class="label"><span class="code">${t.html("Ask {ai}", { ai: aiName() })}</span></span></button>
      ${Studia.navExtra ? Studia.navExtra(v) : ""}
      <button class="nav-item ${v.name === "library" ? "active" : ""}" data-act="go" data-view="library">${I("search")}<span class="label"><span class="code">${t("Search")}</span></span></button></div>`;
    nav.innerHTML = h;
    const p = S.profile || {};
    const others = S.profiles.filter((x) => !x.active).length;
    $("#sfoot").innerHTML = `
      <button class="profile" data-act="menu-profile" title="${others ? t("Switch profile, add an account or sign out") : t("Add another account or sign out")}">
        <div class="avatar">${esc(initials(p.name))}</div>
        <div class="who"><div class="name">${esc(p.name || t("Signed in"))}</div><div class="num">${esc(p.student_number || "")}</div></div>
        ${I("chevrons-up-down", "sm")}</button>
      <button class="btn ghost icon ${v.name === "settings" ? "on" : ""}" data-act="go" data-view="settings" title="${t("Settings")}">${I("settings")}</button>`;
  }

  function revealCourse() {   // the course just opened is not left hidden under the sidebar's Study group
    const nav = $("#nav"), on = nav && nav.querySelector(".nav-item.active[data-view=course]"), study = nav && nav.querySelector(".nav-study");
    if (on && study) { const over = on.getBoundingClientRect().bottom - study.getBoundingClientRect().top; if (over > 0) nav.scrollTop += over + 8; }
  }

  function crumbs() {
    const v = S.view;
    if (v.name === "new") return `${I("sparkle")} ${t("New recordings")}`;
    if (v.name === "home") return `${I("home")} ${t("Home")}`;
    if (v.name === "library") return `${I("search")} ${t("Search")}`;
    if (Studia.crumbs && Studia.crumbs[v.name]) return Studia.crumbs[v.name](v);
    if (v.name === "settings") return `${I("settings")} ${t("Settings")}`;
    if (v.name === "chat") return `${I("chat")} ${t.html("Ask {ai}", { ai: aiName() })}`;
    if (v.name === "course") { const c = findCourse(v.courseId); return c ? `${esc(c.code)} <span class="dim">· ${esc(c.display_name)}</span>` : ""; }
    if (v.name === "day" && S.day) return `<button class="btn ghost sm" data-act="back">${I("arrow-left", "sm")} ${t("Back")}</button> ${esc(S.day.title)} <span class="dim">· ${esc(S.day.date_label)}</span>`;
    return "";
  }

  function renderTop() {
    const top = $("#top"); if (!top) return;
    const g = S.system.gpu;
    const gpu = g == null ? "" : gpuUsed(g) ? `<span class="chip ok" title="${t.html("Lectures are transcribed on your NVIDIA graphics card: {name} · {mb} MB", { name: g.name, mb: g.vram_mb })}"><span class="dot"></span>GPU · ${esc((g.name || "").replace(/NVIDIA GeForce /i, "").replace(/ Laptop GPU/i, ""))}</span>`
      : gpuFits(g) && !g.libraries && S.settings.device !== "cpu" ? `<span class="chip" title="${t("Your NVIDIA graphics card could transcribe much faster after a one-time download from NVIDIA: Settings → About → Run setup again.")}"><span class="dot"></span>${t("Graphics card not set up")}</span>` : "";
    const syncLabel = S.sync.running ? `${SPIN()} ${esc(S.sync.message || t("Syncing…"))}`
      : S.sync.error ? `${I("alert-circle", "sm")} ${t("Sync failed — retry")}` : `${I("refresh", "sm")} ${S.lastSync ? t("Synced {when}", { when: rel(S.lastSync) }) : t("Not synced yet")}`;
    const up = S.update || {};
    const app = (S.boot || {}).app || {};          // background checks turned off (the tray menu, or Settings): said calmly
    const bgOff = app.onboarded && app.tray === false && S.view.name !== "settings"
      ? `<button class="btn ghost sm bg-off" data-act="bg-settings" aria-label="${t("Background checks off")}" title="${t("You get no notifications about new lectures, announcements or coming quizzes. Click to turn them on again in Settings.")}">${I("info", "sm")} <span class="lbl">${t("Background checks off")}</span></button>` : "";
    top.innerHTML = `<div class="crumbs">${crumbs()}</div>
      ${Studia.downloadChip ? Studia.downloadChip() : ""}
      ${up.available ? `<button class="btn sm update-btn" data-act="update-open" title="${t.html("IEUStudia {version} is available: see what's new and update", { version: up.version })}">${I("download", "sm")} ${t.html("Update to {version}", { version: up.version })}</button>` : ""}
      ${bgOff}
      <button class="btn ghost sm ${S.sync.error && !S.sync.running ? "warn" : ""}" data-act="sync" ${S.sync.running ? "disabled" : ""} title="${S.sync.error && !S.sync.running ? t.html("{error} Click to try again.", { error: S.sync.error }) : t("Check IEULearn now for new lectures, files and announcements (F5)")}">${syncLabel}</button>
      <button class="btn ghost sm" data-act="full-sync" ${S.catchup && S.catchup.running ? "disabled" : ""} title="${t("Sync every course, then transcribe all your untranscribed lectures and download the materials (asks first)")}">${S.catchup && S.catchup.running ? SPIN() : I("zap", "sm")} ${t("Full sync")}</button>
      ${gpu}
      <button class="btn ghost icon sm" data-act="menu-more" title="${t("More")}">${I("more")}</button>`;
  }
  setInterval(() => { if (!S.sync.running) renderTop(); refreshAppConfig(); }, 30000);
  // Background checks turned off (or on) in the tray helper's menu while this window is open: when the window comes
  // back (and every 30 s while it is seen), the machine-wide settings are read again (a small file) and the top bar's
  // chip and Settings follow.
  let appCfgAt = 0;
  async function refreshAppConfig() {
    if (S.mode !== "app" || !S.boot || document.hidden || performance.now() - appCfgAt < 3000) return;
    appCfgAt = performance.now();
    const r = await call("app_config");
    if (!r.ok || !r.app || JSON.stringify(r.app) === JSON.stringify(S.boot.app || {})) return;
    S.boot.app = r.app; renderTop();
    if (S.view.name !== "settings") return;
    if (document.querySelector(":active")) document.addEventListener("pointerup", () => setTimeout(renderView, 0), { once: true });   // the click lands first
    else renderView();
  }
  window.addEventListener("focus", refreshAppConfig);
  document.addEventListener("visibilitychange", refreshAppConfig);

  // ------------------------------------------------------------------ views
  function renderView() {
    const el = $("#view"); if (!el) return;
    const v = S.view;
    if (Studia.views[v.name]) el.innerHTML = Studia.views[v.name](v);   // home, course pages, plans (course.js …)
    else if (v.name === "course") el.innerHTML = courseHTML(findCourse(v.courseId));
    else if (v.name === "new") el.innerHTML = newHTML();
    else if (v.name === "day") el.innerHTML = dayHTML();
    else if (v.name === "library") { el.innerHTML = libraryHTML(); renderSearchResults(); }
    else if (v.name === "settings") el.innerHTML = settingsHTML();
    else if (v.name === "chat") el.innerHTML = chatHTML();
    el.classList.toggle("chat-mode", v.name === "chat");
    syncChecks();
    if (Studia.after[v.name]) Studia.after[v.name](v);
    if (Studia.views[v.name] && Studia.renderMath) Studia.renderMath(el);   // formulas in plans, review sheets, study packs, course pages
  }

  function go(view) {
    if (S.qpOpen) closePane();     // a sidebar or toast button: the page is not drawn under the pane
    saveChatScroll();
    if (S.view.name === "day" && view.name !== "day") S.day = null;
    if (!["course", "new", "home"].includes(view.name)) S.selected.clear();
    if (view.name === "chat" && view.chatId) { S.chat.id = view.chatId; S.chat.draft = null; }
    S.view = view;
    $("#view").scrollTo({ top: 0, behavior: "instant" });   // before rendering (an after-hook may scroll elsewhere); not animated
    renderSidebar(); renderTop(); renderView();
    revealCourse();
    if (Studia.onGo[view.name]) Studia.onGo[view.name](view);
    if (view.name === "library") loadLibrary();
    if (view.name === "chat") {
      loadChats();
      if (S.chat.id && !S.chat.draft && (!S.chat.data || S.chat.data.id !== S.chat.id)) openChat(S.chat.id);
      else { restoreChatScroll(); focusComposer(); }
    }
  }

  // ---- recording rows
  function statusHTML(r) {
    const prog = r.progress != null ? r.progress : null;
    const bar = (indet) => `<div class="bar ${indet ? "indet" : ""}"><i style="width:${pct(prog)}"></i></div>`;
    switch (r.status) {
      case "queued": return `<span class="pill">${I("clock")}${t("Queued")}</span><span class="detail">${esc(r.detail || "")}</span>`;
      case "downloading": return `<span class="pill active">${I("download")}${prog != null ? t("Downloading {pct}", { pct: pctText(prog) }) : t("Downloading")}</span>${bar(prog == null)}<span class="detail">${esc(jobDetail(r))}</span>`;
      case "transcribing": return `<span class="pill active">${I("wave")}${prog ? t("Transcribing {pct}", { pct: pctText(prog) }) : t("Transcribing")}</span>${bar(!prog)}<span class="detail">${esc(jobDetail(r))}</span>`;
      case "done": return `<span class="pill done">${I("check")}${t("Transcribed")}</span>`;
      case "failed":
        if (r.no_sound || /no sound|no speech/i.test(r.error || "")) return `<span class="pill processing" title="${esc(r.error || "")}">${I("ban")}${t("No sound on Panopto")}</span><span class="detail" title="${esc(r.error || "")}">${t("microphone was off")}</span>`;
        return `<span class="pill failed" title="${esc(r.error || "")}">${I("alert")}${t("Failed")}</span><span class="detail" title="${esc(r.error || "")}">${esc(r.error || "")}</span>`;
      case "processing": return `<span class="pill processing" title="${t("Panopto is still preparing this recording. Try again later.")}">${I("hourglass")}${t("Not ready on Panopto yet")}</span>`;
      case "live": return `<span class="pill processing" title="${t("It can be transcribed once Panopto has finished it.")}">${I("video")}${t("Being recorded")}</span>`;
    }
    if (r.duplicate) return `<span class="pill">${t("Duplicate")}</span>`;
    if (r.too_short) return `<span class="pill">${t("Too short")}</span>`;
    if (r.is_new) return `<span class="pill new">${I("sparkle")}${t("NEW")}</span>`;
    return `<span class="detail">${t("Not transcribed")}</span>`;
  }
  function selectable(r) { return !r.duplicate && !r.too_short && !["queued", "downloading", "transcribing", "live"].includes(r.status); }
  // what looks off about a recording (anomalies.py): a small mark, its texts shown on hover and focus
  function oddMark(texts) {
    const tip = esc((texts || []).join("\n"));
    return tip ? `<span class="odd-mark" tabindex="0" role="img" data-tip="${tip}" aria-label="${tip}">${I("alert-circle", "sm")}</span>` : "";
  }
  function recHTML(r, d) {
    const dim = r.duplicate || r.too_short || r.hidden;
    const warn = r.warn_level && r.warn_level !== "info"
      ? `<span class="warn-icon ${r.warn_level === "error" ? "error" : ""}" title="${esc(r.warnings.map((w) => w.message).join("\n"))}">${I("alert", "sm")}</span>` : "";
    const busy = ["queued", "downloading", "transcribing"].includes(r.status);
    const acts = [
      busy ? `<button class="btn ghost icon sm" data-act="cancel" data-id="${r.id}" title="${t("Cancel")}">${I("x", "sm")}</button>` : "",
      r.status === "failed" ? `<button class="btn ghost icon sm" data-act="retry" data-id="${r.id}" title="${t("Try again")}">${I("retry", "sm")}</button>` : "",
      r.status === "done" ? `<button class="btn ghost icon sm" data-act="open-day" data-day="${esc(d.day_key)}" title="${t("Read the transcript")}">${I("file-text", "sm")}</button>` : "",
      `<button class="btn ghost icon sm" data-act="url" data-url="${esc(r.viewer_url)}" title="${t("Watch on Panopto")}">${I("external", "sm")}</button>`,
    ].join("");
    return `<div class="rec ${dim ? "dim" : ""}" data-rid="${r.id}">
      <input type="checkbox" class="check" data-act="sel" data-id="${r.id}" ${selectable(r) ? "" : "disabled"} aria-label="${t("Select")}">
      <span class="part">${r.part ? t("Part {n}", { n: r.part }) : "—"}</span>
      <span class="time">${esc(r.time)}–${esc(r.end_time)}</span>
      <span class="dur">${fmtDur(r.duration_s)}</span>
      <span class="state">${statusHTML(r)}${warn}${oddMark((r.anomalies || []).map((a) => a.text))}</span>
      <span class="acts">${acts}</span></div>`;
  }
  function updateRow(r) {
    const row = document.querySelector(`.rec[data-rid="${r.id}"]`);
    if (!row) return;
    const st = row.querySelector(".state");
    const marks = Array.from(st.querySelectorAll(".warn-icon, .odd-mark"), (el) => el.outerHTML).join("");
    st.innerHTML = statusHTML(r) + marks;
    const cb = row.querySelector(".check");
    if (cb) { cb.disabled = !selectable(r); if (cb.disabled) { cb.checked = false; S.selected.delete(r.id); } }
    if (r.status === "done" || r.status === "failed") {
      const hit = findRec(r.id);
      if (hit && (S.view.name === "course" || S.view.name === "new")) { const card = row.closest(".day"); if (card) card.outerHTML = dayCardHTML(hit.d, hit.c); syncChecks(); }
    }
    updateSelectionUI();
  }

  // ---- day cards
  function dayCardHTML(d, c) {
    const dt = new Date(d.date + "T00:00:00");
    const selIds = d.recordings.filter(selectable).map((r) => r.id);
    const todo = d.recordings.filter((r) => selectable(r) && !["done", "processing"].includes(r.status) && !r.hidden && !r.no_sound);
    const others = d.recordings.filter((r) => r.mine === false && c.show_mode === "mine");
    const status = d.transcribed ? `<span class="pill done">${I("check")}${t("Transcribed")}</span>`
      : d.is_new ? `<span class="pill new">${I("sparkle")}${t("New")}</span>` : d.any_done ? `<span class="pill">${t("Partly transcribed")}</span>` : "";
    const warn = d.warn_level && d.warn_level !== "info" ? `<span class="warn-icon ${d.warn_level === "error" ? "error" : ""}" title="${t("See the notes on the transcript page")}">${I("alert", "sm")}</span>` : "";
    return `<div class="card day" data-day="${esc(d.day_key)}">
      <div class="day-head">
        <input type="checkbox" class="check" data-act="sel-day" data-day="${esc(d.day_key)}" ${selIds.length ? "" : "disabled"} aria-label="${t("Select day")}">
        <div class="day-date"><div class="d">${dt.getDate()}</div><div class="m">${t.date(dt, "mon")}</div></div>
        <div class="day-title"><div class="t">${esc(d.label)} ${d.kind === "lab" ? `<span class="pill lab">${I("lab")}${t("Lab")}</span>` : ""} ${status} ${warn}</div>
          <div class="s">${t.n(d.parts, "{n} part", "{n} parts")} · ${fmtDur(d.duration_s)}${d.mine === false ? ` · ${t("other section")}` : ""}</div></div>
        <div class="day-actions">
          ${d.any_done ? `<button class="btn sm" data-act="open-day" data-day="${esc(d.day_key)}">${I("file-text", "sm")} ${t("Open transcript")}</button>` : ""}
          ${todo.length ? `<button class="btn sm ${d.any_done ? "" : "primary"}" data-act="tx-day" data-day="${esc(d.day_key)}">${I("wave", "sm")} ${d.any_done ? t("Transcribe rest") : t("Transcribe day")}</button>` : ""}
        </div>
      </div>
      ${d.recordings.filter((r) => !others.includes(r)).map((r) => recHTML(r, d)).join("")}
      ${others.length ? others.map((r) => recHTML(r, d)).join("") : ""}
    </div>`;
  }

  function courseHTML(c, opts = {}) {   // opts.bare: only the recordings (inside the course page's Lectures tab)
    if (!c) return emptyHTML("book", t("Course not found"), "");
    const wrap = (inner) => (opts.bare ? inner : `<div class="view-inner">${heroHTML(c)}${inner}</div>`);
    if (!c.has_panopto) return wrap(emptyHTML("video", t("No Panopto recordings"), t("This course has no Panopto folder on IEULearn.")));
    const mineMode = c.show_mode === "mine" && c.available_sections.length;
    const days = c.days.filter((d) => !mineMode || d.mine !== false);
    const otherDays = mineMode ? c.days.filter((d) => d.mine === false) : [];
    const exp = S.expanded.has(c.id);
    const sections = c.available_sections.length > 1 && c.my_section;     // the header's chip picks your section
    let h = `
      <div class="toolbar">
        ${sections ? `<span class="dim sm">${mineMode ? t.html("Section {tag}'s lectures", { tag: c.my_section }) : t("Every section's lectures")}</span>
          <button class="btn ghost sm" data-act="show-mode" data-mode="${mineMode ? "all" : "mine"}" data-id="${c.id}">${mineMode ? t("Show all sections") : t("Only my section")}</button>` : ""}
        <span class="spacer"></span>
        ${c.counts.new ? `<button class="btn ghost sm" data-act="mark-seen" data-id="${c.id}">${t("Mark as seen")}</button>` : ""}
        <button class="btn sm" data-act="select-new" data-id="${c.id}" ${c.counts.new ? "" : "disabled"}>${I("list-checks", "sm")} ${t("Select new")}</button>
        <button class="btn primary sm" data-act="tx-selected" id="tx-sel" disabled>${I("wave", "sm")} ${t("Transcribe selected")}</button>
      </div>`;
    if (!c.days.length) h += emptyHTML("inbox", S.sync.running ? t("Looking for recordings…") : t("No recordings yet"), t("New lectures appear here after Panopto has processed them."));
    h += days.map((d) => dayCardHTML(d, c)).join("");
    if (otherDays.length) {
      h += `<button class="group-toggle card" style="margin-top:6px" data-act="toggle-others" data-id="${c.id}">${I(exp ? "chevron-down" : "chevron-right", "sm")} ${t.n(otherDays.length, "Other sections · {n} class day (same material, different section)", "Other sections · {n} class days (same material, different section)")}</button>`;
      if (exp) h += otherDays.map((d) => dayCardHTML(d, c)).join("");
    }
    return wrap(h);
  }
  function heroHTML(c) {
    const code = esc(c.code).replace(" ", "<br>");
    const ai = S.system.ai || {};
    const ask = (c.counts.done_any ?? c.counts.done) ? `<button class="btn grad sm" data-act="ask-course" data-id="${c.id}" ${ai.available ? "" : "disabled"} title="${ai.available ? t.html("Chat with {ai} about this course's lectures", { ai: aiName() }) : aiHint()}">${I("chat", "sm")} ${t.html("Ask {ai}", { ai: aiName() })}</button>` : "";
    return `<div class="card hero"><div class="badge">${code}</div><div class="grow">
      <h1>${esc(c.display_name)}</h1>
      <div class="meta">${c.instructor ? `<span>${I("user", "sm")}${esc(c.instructor)}</span>` : ""}${c.term ? `<span>${I("calendar", "sm")}${esc(c.term)}</span>` : ""}${(c.my_section || c.section) ? `<span>${I("users", "sm")}${t.html("Section {tag}", { tag: c.available_sections.length && c.my_section ? c.my_section : c.section })}</span>` : ""}</div>
      <div class="stats"><div class="stat">${t.html("<b>{n}</b>recordings", { n: c.counts.total })}</div><div class="stat">${t.html("<b>{n}</b>transcribed", { n: c.counts.done })}</div>
        ${c.counts.new ? `<div class="stat">${t.html("<b>{n}</b>new", { n: c.counts.new })}</div>` : ""}${c.counts.processing ? `<div class="stat">${t.html("<b>{n}</b>processing", { n: c.counts.processing })}</div>` : ""}</div>
      </div><div class="hero-acts">${ask}<button class="btn sm" data-act="open-folder" data-id="${c.id}" title="${t("Open this course's transcript folder")}">${I("folder-open", "sm")} ${t("Folder")}</button></div></div>`;
  }
  const otherSection = (c, d) => c.show_mode === "mine" && d.mine === false;
  const ownRec = (c, r) => !(c.show_mode === "mine" && r.mine === false);   // not another section's (when you follow yours)
  const newOwn = (c, d) => d.recordings.filter((r) => r.is_new && !r.duplicate && !r.too_short && ownRec(c, r));
  function newRecs() {   // the class days with new recordings of your section (other sections' stay on the course page)
    return S.courses.map((c) => {
      const days = c.days.filter((d) => d.recordings.some((r) => r.is_new && !r.duplicate && !r.too_short));
      const own = days.filter((d) => !otherSection(c, d) && newOwn(c, d).length);
      return { c, days: own, others: days.length - own.length };
    }).filter((g) => g.days.length);
  }
  const newToDo = (g) => g.days.flatMap((d) => newOwn(g.c, d).filter((r) => selectable(r) && r.status !== "done" && !r.hidden).map((r) => r.id));
  function newDayStateHTML(d) {
    const busy = d.recordings.find((r) => ["queued", "downloading", "transcribing"].includes(r.status));
    return busy ? statusHTML(busy) : d.transcribed ? `<span class="pill done">${I("check")}${t("Transcribed")}</span>`
      : d.any_done ? `<span class="pill">${t("Partly transcribed")}</span>` : `<span class="pill new">${I("sparkle")}${t("New")}</span>`;
  }
  function newDayRowHTML(d, c) {   // Home: one row per class day
    const busy = d.recordings.some((r) => ["queued", "downloading", "transcribing"].includes(r.status));
    const todo = d.recordings.some((r) => ownRec(c, r) && selectable(r) && !["done", "processing"].includes(r.status) && !r.hidden && !r.no_sound);
    return `<div class="new-day" data-day="${esc(d.day_key)}"><span class="nd-date">${shortDate(d.date)}</span>
      <span class="nd-what">${d.kind === "lab" ? t("Lab") : t("Lecture")} · ${t.n(d.parts, "{n} part", "{n} parts")} · ${fmtDur(d.duration_s)}</span>
      <span class="nd-state">${newDayStateHTML(d)}</span><span class="nd-acts">
      ${d.any_done ? `<button class="btn ghost sm" data-act="open-day" data-day="${esc(d.day_key)}">${I("file-text", "sm")} ${t("Read")}</button>` : ""}
      ${todo && !busy ? `<button class="btn sm" data-act="tx-day" data-day="${esc(d.day_key)}">${I("wave", "sm")} ${t("Transcribe")}</button>` : ""}</span></div>`;
  }
  function refreshNewDay(hit, stateOnly) {    // stateOnly: progress, so the row's buttons stay where they are
    const row = document.querySelector(`.new-day[data-day="${CSS.escape(hit.d.day_key)}"]`);
    if (!row) return;
    if (stateOnly) row.querySelector(".nd-state").innerHTML = newDayStateHTML(hit.d);
    else row.outerHTML = newDayRowHTML(hit.d, hit.c);
  }
  function newHTML(opts = {}) {   // opts.embedded: only the toolbar and day rows (inside Home), "" if nothing is new
    const groups = newRecs();
    if (opts.embedded && !groups.length) return "";
    if (opts.embedded) {
      const ids = groups.flatMap(newToDo);
      const days = groups.reduce((n, g) => n + g.days.filter((d) => d.recordings.some((r) => ids.includes(r.id))).length, 0);
      return `<div class="toolbar" style="margin-top:4px"><span class="spacer"></span><button class="btn ghost sm" data-act="mark-seen">${t("Mark all as seen")}</button>
          ${ids.length ? `<button class="btn primary sm" data-act="tx-new" title="${t.n(ids.length, "{n} recording", "{n} recordings")}">${I("wave", "sm")} ${days > 1 ? t("Transcribe all {n} class days", { n: days }) : t("Transcribe all")}</button>` : ""}</div>
        <div class="card new-days">${groups.map((g) => `<div class="nd-course"><span class="pill new">${esc(g.c.code)}</span> ${esc(g.c.display_name)}
          ${g.others ? `<span class="dim sm">· ${t.n(g.others, "+{n} class day of other sections on the course page", "+{n} class days of other sections on the course page")}</span>` : ""}</div>
          ${g.days.map((d) => newDayRowHTML(d, g.c)).join("")}`).join("")}</div>`;
    }
    const tools = `<div class="toolbar"><span class="spacer"></span>
        ${groups.length ? `<button class="btn ghost sm" data-act="mark-seen">${t("Mark all as seen")}</button>
        <button class="btn sm" data-act="select-new">${I("list-checks", "sm")} ${t("Select all new")}</button>` : ""}
        <button class="btn primary sm" data-act="tx-selected" id="tx-sel" disabled>${I("wave", "sm")} ${t("Transcribe selected")}</button></div>`;
    let h = `<div class="view-inner"><div class="card hero"><div class="badge">${I("sparkle", "lg")}</div><div class="grow">
      <h1>${t("New recordings")}</h1><div class="meta"><span>${t("Recordings that appeared since your last visit. Nothing is transcribed until you choose it.")}</span></div></div></div>
      ${tools}`;
    if (!groups.length) h += emptyHTML("check-circle", S.sync.running ? t("Checking for new recordings…") : t("You're all caught up"),
      t("Recordings that appear on Panopto after this sync show up here. Pick a course on the left to browse everything already there."));
    for (const g of groups) {
      h += `<div class="course-group-title"><span class="pill new">${esc(g.c.code)}</span> ${esc(g.c.display_name)}</div>`;
      h += g.days.map((d) => dayCardHTML(d, g.c)).join("");
    }
    return h + `</div>`;
  }
  function emptyHTML(icon, title, text) {
    return `<div class="empty"><div class="art">${I(icon)}</div><h3>${esc(title)}</h3><div>${esc(text)}</div></div>`;
  }

  // ---- selection
  function syncChecks() {
    $$(".rec .check").forEach((cb) => { cb.checked = S.selected.has(cb.dataset.id); });
    updateSelectionUI();
  }
  function updateSelectionUI() {
    $$(".day").forEach((card) => {
      const boxes = $$(".rec .check:not(:disabled)", card);
      const n = boxes.filter((b) => b.checked).length;
      const dc = $(".day-head .check", card);
      if (dc) { dc.checked = n > 0 && n === boxes.length; dc.indeterminate = n > 0 && n < boxes.length; }
      card.classList.toggle("has-sel", n > 0);
    });
    const btn = $("#tx-sel");
    if (btn) { btn.disabled = S.selected.size === 0; btn.innerHTML = `${I("wave", "sm")} ${S.selected.size ? t("Transcribe {n} selected", { n: S.selected.size }) : t("Transcribe selected")}`; }
  }

  // ---- day / transcript view
  async function openDay(dayKey, refresh = false) {
    if (!refresh) {
      if (S.qpOpen) closePane();
      saveChatScroll();
      S.view = { name: "day", dayKey, from: S.view.name === "day" ? S.view.from : { ...S.view, scroll: $("#view").scrollTop } }; S.day = null; S.dayFind = "";
      renderTop(); renderSidebar(); $("#view").classList.remove("chat-mode"); $("#view").innerHTML = `<div class="view-inner">${skeleton()}</div>`;
    }
    const res = await call("get_day", dayKey);
    if (!res.ok) {
      toast(res.error, "error");
      if (!refresh && S.view.name === "day" && S.view.dayKey === dayKey) go(S.view.from || { name: "new" });   // no dead end
      return;
    }
    if (S.view.name !== "day" || S.view.dayKey !== dayKey) return;
    const keep = $("#view").scrollTop;
    S.day = res.day;
    if (!refresh) S.notesKind = null;
    if (S.ai.dayKey !== dayKey && !S.ai.running) S.ai = { running: false, text: "", dayKey: null };
    renderTop(); renderView();
    $("#view").scrollTo({ top: refresh ? keep : 0, behavior: "instant" });
  }
  function skeleton() {
    return `<div class="skel" style="height:34px;width:45%;margin-bottom:10px"></div><div class="skel" style="height:16px;width:30%;margin-bottom:24px"></div>` +
      Array.from({ length: 6 }, () => `<div class="skel" style="height:18px;margin:12px 0;width:${60 + Math.random() * 38}%"></div>`).join("");
  }
  function dayHTML() {
    const d = S.day; if (!d) return `<div class="view-inner">${skeleton()}</div>`;
    const en = d.languages.en || 0, tr = d.languages.tr || 0;
    const warns = d.parts.flatMap((p) => p.warnings.filter((w) => w.level !== "info").map((w) => ({ ...w, part: p.index })));
    let h = `<div class="view-inner">
      <div class="tx-head"><div style="flex:1;min-width:0"><h1>${esc(d.title)} · ${esc(d.date_label)}</h1>
        <div class="sub">${esc(d.subtitle)}</div>
        <div class="langs" style="margin-top:8px">${(en || tr) ? `<span class="langbar"><i class="en" style="width:${en * 100}%"></i><i class="tr" style="width:${tr * 100}%"></i></span>
          ${t("English {pct}", { pct: pctText(en) })}${tr ? ` · <span style="color:var(--teal)">${t("Turkish {pct}", { pct: t.pct(Math.max(1, Math.round(tr * 100)) / 100) })}</span>` : ""}` : ""}
          · ${t.n(d.parts.length, "{n} part", "{n} parts")}</div></div>
        <input class="input find" placeholder="${t("Find in transcript  (Ctrl+F)")}" data-act="find" value="${esc(S.dayFind)}"></div>
      <div class="toolbar" id="day-actions">${dayActionsHTML()}</div>`;
    if (warns.length) h += `<div class="callout warning">${I("alert")}<div><b>${warns.some((w) => w.code === "not_written") ? t("Notes") : t("Transcript quality")}</b><ul>${warns.map((w) => `<li>${t("Part {n}", { n: w.part })}: ${esc(w.message)}</li>`).join("")}</ul></div></div>`;
    const odd = new Map();          // each note once, with the parts it is about
    d.parts.forEach((p) => (p.anomalies || []).forEach((a) => { if (!odd.has(a.text)) odd.set(a.text, []); if (!odd.get(a.text).includes(p.index)) odd.get(a.text).push(p.index); }));
    const partsLabel = (ns) => ns.length === 1 ? t("Part {n}", { n: ns[0] })
      : ns.every((n, i) => !i || n === ns[i - 1] + 1) ? t("Parts {a}–{b}", { a: ns[0], b: ns[ns.length - 1] }) : t("Parts {list}", { list: t.list(ns) });
    if (odd.size) h += `<div class="callout warning">${I("alert-circle")}<div><b>${t("Worth checking")}</b><ul>${[...odd].map(([text, ns]) => `<li>${partsLabel(ns)}: ${esc(text)}</li>`).join("")}</ul></div></div>`;
    h += `<div id="notes-slot">${notesHTML()}</div><div id="marks-slot">${marksHTML()}</div>`;
    if (d.highlights.length) {
      h += `<div class="card hl-card"><h3>${I("star")} ${t("Highlights")} <span class="count">${d.highlights.length}</span></h3>` +
        d.highlights.map((x) => `<div class="hl-item" data-act="jump" data-part="${x.part}" data-t="${x.t}" tabindex="0" role="button"><span class="when">${t("Part {n}", { n: x.part })} · ${esc(x.ts)}</span>
          <span class="tags">${esc(x.tags.join(", "))}</span><span class="para-text">${esc(x.text)}</span></div>`).join("") + `</div>`;
    }
    for (const p of d.parts) {
      h += `<div class="part-h" id="part-${p.index}"><h2>${t("Part {n}", { n: p.index })}</h2><span class="rng">${esc(p.time_range)}${p.slides ? ` · ${t.n(p.slides, "{n} slide", "{n} slides")}` : ""}</span>
        <span style="flex:1"></span><button class="btn ghost sm" data-act="url" data-url="${esc(p.viewer_url)}">${I("play", "sm")} ${t("Watch")}</button></div>`;
      if (!p.paragraphs.length) { h += `<div class="empty" style="padding:20px">${p.status === "done" ? t("No speech was recognised in this part.") : p.status === "live" ? t("Being recorded. It can be transcribed once Panopto has finished it.") : t("Not transcribed yet.")}</div>`; continue; }
      let slide = null;
      for (const para of p.paragraphs) {
        if (para.slide_image && para.slide !== slide) {
          slide = para.slide;
          const size = para.slide_w ? ` width="${+para.slide_w}" height="${+para.slide_h}"` : "";   // space reserved before it loads
          h += `<div class="slide" data-act="zoom" data-src="${esc(para.slide_image)}" tabindex="0" role="button" title="${t("Show it larger")}"><img loading="lazy" src="${esc(para.slide_image)}"${size} alt="${t("Slide {n}", { n: para.slide })}"><span class="cap">${t("Slide {n}", { n: para.slide })}</span></div>`;
        }
        h += paraHTML(para, p);
      }
    }
    return h + dayNavHTML() + `<div style="height:45vh"></div></div>`;   // room to centre the last paragraphs on a jump
  }
  function dayNavHTML() {   // the course's lectures before and after this one (your section's)
    const d = S.day, c = d && findCourse(d.course.id);
    if (!c) return "";
    const days = c.days.filter((x) => x.parts && !otherSection(c, x)).sort((a, b) => (a.date + a.kind).localeCompare(b.date + b.kind));
    const i = days.findIndex((x) => x.day_key === d.day_key);
    if (i < 0 || days.length < 2) return "";
    const what = (x) => (x.kind === "lab" ? t("{date} lab", { date: shortDate(x.date) }) : t("{date} lecture", { date: shortDate(x.date) }));
    const btn = (x, back) => !x ? "<span></span>" : x.any_done
      ? `<button class="btn ghost" data-act="open-day" data-day="${esc(x.day_key)}">${back ? I("arrow-left", "sm") : ""} ${what(x)} ${back ? "" : I("arrow-right", "sm")}</button>`
      : `<button class="btn ghost" data-act="tx-day" data-day="${esc(x.day_key)}" title="${t("Not transcribed yet")}">${I("wave", "sm")} ${x.kind === "lab" ? t("Transcribe the {date} lab", { date: shortDate(x.date) }) : t("Transcribe the {date} lecture", { date: shortDate(x.date) })}</button>`;
    return `<div class="day-nav">${btn(days[i - 1], true)}${btn(days[i + 1], false)}</div>`;
  }
  // lower case for finding, with the Turkish i's as one letter ("İşte" = "işte", "SINAV" = "sınav"); same length
  const fold = (s) => s.replace(/[İIı]/g, "i").toLowerCase();
  function markFind(text, q) {
    const f = fold(text), k = fold(q);
    let out = "", i = 0, j;
    while ((j = f.indexOf(k, i)) >= 0) { out += esc(text.slice(i, j)) + `<mark>${esc(text.slice(j, j + k.length))}</mark>`; i = j + k.length; }
    return out + esc(text.slice(i));
  }
  function paraHTML(para, p) {
    const q = S.dayFind.trim();
    const text = q.length > 1 ? markFind(para.text, q) : esc(para.text);
    const hidden = q.length > 1 && !fold(para.text).includes(fold(q));
    const marks = [para.tag ? `<span class="pill tr">${esc(para.tag)}</span>` : "", para.star ? `<span class="pill processing" title="${esc((para.tags || []).join(", "))}">${I("star")}</span>` : "",
      para.uncertain ? `<span class="pill" title="${t("Unclear audio — check the recording")}">${I("alert")}${t("unclear")}</span>` : ""].join("");
    const mk = para.mark;
    return `<div class="para ${para.tag ? "tr" : ""} ${para.star ? "star" : ""} ${para.uncertain ? "uncertain" : ""} ${mk ? "marked" : ""}" data-part="${p.index}" data-t="${para.t}" data-e="${para.e ?? para.t}" ${hidden ? 'style="display:none"' : ""}>
      <span class="ts-col"><span class="ts" data-act="ts" data-url="${esc(p.viewer_url)}" data-t="${para.t}" tabindex="0" role="button" title="${t("Open in Panopto at this moment")}">${esc(para.ts)}</span>
        <button class="ts-link" data-act="copy-moment" data-part="${p.index}" data-t="${para.t}" title="${t("Copy a link to this moment (for classmates: they open it with their own IEU login)")}">${I("link", "xs")}</button>
        <button class="ts-mark ${mk ? "on" : ""}" data-act="mark" data-part="${p.index}" data-t="${para.t}" title="${mk ? t("Remove your mark") : t("Mark this passage to come back to it")}">${I("bookmark", "xs")}</button></span>
      <div class="para-text">${marks ? `<span class="marks">${marks}</span>` : ""}${text}</div></div>`;
  }
  function dayActionsHTML() {
    const d = S.day; if (!d) return "";
    const ai = S.system.ai || {};
    const hasText = d.parts.some((p) => p.paragraphs.length);
    return `<button class="btn primary sm" data-act="menu-copy" ${hasText ? "" : "disabled"}>${I("copy", "sm")} ${t("Copy for AI")} ${I("chevron-down", "sm caret")}</button>
      <button class="btn sm" data-act="export" ${hasText && S.exporting !== d.day_key ? "" : "disabled"} title="${t("PDF with slides next to the transcript + ZIP, for ChatGPT / Claude")}">
        ${S.exporting === d.day_key ? SPIN() : I("download", "sm")} ${t("Save as PDF")}</button>
      <button class="btn grad sm" data-act="ask-day" ${hasText && ai.available ? "" : "disabled"} title="${ai.available ? t.html("Chat with {ai} about this lecture ({plan})", { ai: aiName(), plan: aiPlan() }) : aiHint()}">${I("chat", "sm")} ${t.html("Ask {ai}", { ai: aiName() })}</button>
      <button class="btn sm" data-act="claude" ${hasText && ai.available && !S.ai.running ? "" : "disabled"} title="${ai.available ? t.html("Write study notes with {plan}", { plan: aiPlan() }) : aiHint()}">${I("ai", "sm")} ${t("Study notes")}</button>
      <span class="spacer"></span>
      ${d.files.pdf ? `<button class="btn ghost sm" data-act="file" data-what="pdf">${I("file", "sm")} PDF</button>` : ""}
      <button class="btn ghost sm" data-act="file" data-what="md" ${d.files.md ? "" : "disabled"} title="${t("The transcript as a text file (Markdown) in your IEUStudia folder")}">${I("file-text", "sm")} ${t("Text file")}</button>
      <button class="btn ghost sm" data-act="file" data-what="folder">${I("folder-open", "sm")} ${t("Folder")}</button>`;
  }
  function renderDayActions() { const el = $("#day-actions"); if (el) el.innerHTML = dayActionsHTML(); }
  function notesHTML() {   // the AI notes of this day: the kind being written, else the chosen (or newest) kind
    const d = S.day; if (!d) return "";
    const notes = d.notes || [];
    const writing = S.ai.running && S.ai.dayKey === d.day_key;
    const shown = writing ? { kind: S.ai.kind, label: S.ai.label, md: S.ai.text } : notes.find((n) => n.kind === S.notesKind) || notes[0];
    if (!shown) return "";
    const kinds = !writing && notes.length > 1 ? `<span class="seg sm">${notes.map((n) => `<button class="${n.kind === shown.kind ? "on" : ""}" data-act="notes-kind" data-val="${n.kind}">${esc(n.label)}</button>`).join("")}</span>` : "";
    const cards = !writing && shown.kind === "flashcards" ? (shown.cards || []).length : 0;
    return `<div class="card notes-card"><h3>${I("ai")} ${esc(shown.label || t("Study notes"))} ${writing ? `<span class="pill active">${SPIN()} ${t.html("{ai} is writing…", { ai: aiName() })}</span>
      <button class="btn ghost sm" data-act="ai-cancel">${t("Stop")}</button>` : `<span class="pill done">${I("check")}${t("Saved")}</span>${kinds}
      ${cards >= 3 ? `<button class="btn sm" data-act="cards-practise" title="${t("One card at a time: think of the answer, then check it")}">${I("list-checks", "sm")} ${t("Practise {n} cards", { n: cards })}</button>` : ""}
      <button class="btn ghost sm" data-act="file" data-what="notes:${esc(shown.kind)}">${I("file-text", "sm")} ${t("Open file")}</button>`}</h3>
      <div class="notes-body md">${shown.md ? mdToHtml(shown.md, { cites: true, streaming: writing }) : `<div class="skel" style="height:14px;width:60%"></div>`}</div></div>`;
  }
  function renderNotes() { const el = $("#notes-slot"); if (el) el.innerHTML = notesHTML(); renderDayActions(); }
  function marksHTML() {   // the passages you marked, in the lecture's order
    const d = S.day, m = (d && d.marks) || [];
    if (!m.length) return "";
    const ai = S.system.ai || {};
    return `<div class="card hl-card marks-card"><h3>${I("bookmark")} ${t("Your marks")} <span class="count">${m.length}</span><span class="spacer"></span>
        ${ai.available ? `<button class="btn ghost sm" data-act="marks-ask" title="${t("Start a chat about this lecture that asks about these passages")}">${I("chat", "sm")} ${t.html("Ask {ai} about them", { ai: aiName() })}</button>` : ""}</h3>` +
      m.map((x) => `<div class="hl-item mk-item" data-act="jump" data-part="${x.part}" data-t="${x.t}" tabindex="0" role="button">
        <span class="when">${t("Part {n}", { n: x.part })} · ${esc(x.ts)}</span><span class="para-text">${esc(x.text)}${x.note ? `<span class="mk-note">${I("pencil", "xs")} ${esc(x.note)}</span>` : ""}</span>
        <span class="mk-acts"><button class="btn ghost icon sm" data-act="mark-note" data-id="${esc(x.id)}" data-t="${x.t}" title="${x.note ? t("Change the note") : t("Add a note")}">${I("pencil", "sm")}</button>
        <button class="btn ghost icon sm" data-act="mark-del" data-id="${esc(x.id)}" data-t="${x.t}" title="${t("Remove the mark")}">${I("x", "sm")}</button></span></div>`).join("") + `</div>`;
  }
  function renderMarks(part, at) {   // the marks card, and the paragraph whose mark changed (at: its time in s)
    const el = $("#marks-slot"); if (el) el.innerHTML = marksHTML();
    const p = S.day && S.day.parts.find((x) => x.index === part);
    const para = p && p.paragraphs.find((x) => Math.abs(x.t - at) < 0.05);
    const node = para && document.querySelector(`.para[data-part="${part}"][data-t="${para.t}"]`);
    if (node) node.outerHTML = paraHTML(para, p);
  }
  async function setMark(part, at, on, note) {   // at: the passage's time in s; true once saved and shown
    const d = S.day, p = d && d.parts.find((x) => x.index === part);
    if (!p) return false;
    const para = p.paragraphs.find((x) => Math.abs(x.t - at) < 0.05);
    const r = on ? await call("mark_set", p.id, at, para ? para.text : "", note) : await call("mark_delete", p.id, at);
    if (!r.ok) { toast(r.error, "error"); return false; }
    if (S.day !== d) return false;
    const here = (x) => x.part === part && Math.abs(x.t - at) < 0.05;
    // the listed mark keeps its saved time and passage, also when no paragraph starts there any more (transcribed again)
    const prev = (d.marks || []).find(here);
    d.marks = (d.marks || []).filter((x) => !here(x));
    if (on) {
      const old = (para && para.mark) || prev || {};
      if (para) para.mark = { note: note != null ? note : old.note || "" };
      d.marks.push({ part, id: p.id, t: at, ts: prev ? prev.ts : para ? para.ts : "", text: prev ? prev.text : para ? para.text : "",
        note: note != null ? note : old.note || "" });
      d.marks.sort((a, b) => a.part - b.part || a.t - b.t);
    } else if (para) para.mark = null;
    renderMarks(part, at);
    return true;
  }

  // ---- formulas in answers and notes: KaTeX, through math.js (window.StudiaMath); without it they stay as written
  const MathR = () => window.StudiaMath;
  const texToHtml = (s, display) => MathR() ? MathR().tex(s, display) : esc(s);

  // ---- Markdown (Claude's answers and notes): escaped first, then our own tags only
  // Citations: "[P2 14:05]", "[L2 P1 05:10]", "[P1 00:18, 06:26; P2 03:00]", "[P1 37:31–37:55]", "(Part 2, 14:05)".
  // A bracket becomes buttons only if it holds nothing but lecture/part/time tokens.
  const TSP = "\\d{1,2}:\\d{2}(?::\\d{2})?";
  const CITE_TOKEN = new RegExp(`L(\\d+)|P(?:art)?\\s*(\\d+)|(${TSP})(?:\\s*[–-]\\s*(${TSP}))?`, "g");
  const CITE_FILLER = /^(?:[\s,;·–-]|and|at|ve|&amp;)*$/;
  function citeBtn(l, p, ts, ts2) {
    const secs = ts.split(":").reduce((a, x) => a * 60 + +x, 0);
    return `<button class="cite" data-act="cite" data-l="${l || ""}" data-p="${p}" data-t="${secs}" title="${t("Open the transcript at this moment")}">${I("play", "xs")}${l ? `L${l} · ` : ""}${t("Part {n}", { n: p })} · ${ts}${ts2 ? `–${ts2}` : ""}</button>`;
  }
  function citeGroup(content, hold) {
    let l = "", p = "", last = 0, out = "", n = 0, located = false, m;
    CITE_TOKEN.lastIndex = 0;
    while ((m = CITE_TOKEN.exec(content))) {
      if (!CITE_FILLER.test(content.slice(last, m.index))) return null;
      last = CITE_TOKEN.lastIndex;
      if (m[1]) { l = m[1]; p = ""; located = true; }
      else if (m[2]) { p = m[2]; located = true; }
      else { out += (n++ ? " " : "") + hold(citeBtn(l, p || "1", m[3], m[4])); }
    }
    return n && located && CITE_FILLER.test(content.slice(last)) ? out : null;
  }
  function mdInline(s, opts) {
    const keep = [];
    const hold = (html) => `\u0000${keep.push(html) - 1}\u0000`;
    // `code` first (formulas are never looked for in it), then the formulas ($…$, \(…\), $$…$$, \[…\]); the rest is escaped
    let out = String(s).split(/(`[^`\n]+`)/).map((x, k) => k % 2 ? hold(`<code>${esc(x.slice(1, -1))}</code>`)
      : MathR() ? MathR().split(x).map((p) => typeof p === "string" ? esc(p) : hold(MathR().html(p))).join("") : esc(x)).join("");
    const emph = (x) => x.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>").replace(/__([^_\n]+)__/g, "<b>$1</b>")
      .replace(/(^|[^\w*])\*([^*\s](?:[^*\n]*?[^*\s])?)\*(?![\w*])/g, "$1<i>$2</i>")
      .replace(/(^|[^\w])_([^_\s](?:[^_\n]*?[^_\s])?)_(?!\w)/g, "$1<i>$2</i>")
      .replace(/~~([^~\n]+)~~/g, "<s>$1</s>");
    out = out.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, txt, url) => hold(`<a class="ext" data-act="url" data-url="${url}" title="${url}">${emph(txt)}</a>`));
    if (opts.cites) out = out.replace(/\[([^[\]\n]{1,160})\]|\(([^()\n]{1,160})\)/g, (m, a, b) => citeGroup(a != null ? a : b, hold) ?? m);
    out = emph(out);
    for (let k = 0; k < 4 && out.includes("\u0000"); k++) out = out.replace(/\u0000(\d+)\u0000/g, (_, n) => keep[+n]);   // held parts may hold others
    return out;
  }
  function mdToHtml(md, opts = {}) {
    if (opts.streaming && MathR()) md = MathR().settled(md);           // a formula still being written shows once it is closed
    try { return mdRender(md, opts); } catch (e) { console.error(e); return `<p>${esc(md).replace(/\n/g, "<br>")}</p>`; }
  }
  // "> [!WARNING]" blocks (GitHub's alerts; the AI is asked to write them, notices.py): a coloured box with an icon and its name
  const MD_ALERT = /^\[!(note|tip|important|warning|caution)\][+-]?\s*(.*)$/i;
  const ALERT_KIND = () => ({ note: ["info", t("Note")], tip: ["sparkle", t("Tip")], important: ["star", t("Important")],
    warning: ["alert", t("Warning")], caution: ["alert-circle", t("Caution")] });
  function mdAlert(kind, title, body) {
    const [icon, name] = ALERT_KIND()[kind];
    return `<div class="md-alert md-alert-${kind}" role="note"><div class="md-alert-h">${I(icon, "sm")}<b>${title || name}</b></div>${body}</div>`;
  }
  function mdRender(md, opts) {
    const lines = String(md || "").replace(/\r\n?|[\u2028\u2029]/g, "\n").split("\n");
    const inline = (s) => mdInline(s, opts);
    const isItem = (l) => /^\s*([-*+]|\d+[.)])\s+/.test(l);
    let out = "", para = [], i = 0;
    const lists = [];    // open lists: {type, indent}
    const flushPara = () => { if (para.length) { out += `<p>${para.map(inline).join("<br>")}</p>`; para = []; } };
    const closeLists = (n = 0) => { while (lists.length > n) out += `</li></${lists.pop().type}>`; };
    const flushAll = () => { flushPara(); closeLists(); };
    while (i < lines.length) {
      const raw = lines[i], line = raw.replace(/\s+$/, "");
      let m;
      if ((m = line.match(/^\s*(```|~~~)/))) {                       // fenced code
        flushAll();
        const buf = [];
        for (i++; i < lines.length && !lines[i].trim().startsWith(m[1]); i++) buf.push(lines[i]);
        out += `<pre><code>${esc(buf.join("\n"))}</code></pre>`; i++; continue;
      }
      if ((m = line.match(/^\s*(\$\$|\\\[|\\begin\{((?:equation|align|alignat|gather|multline)\*?|aligned|gathered|cases|[pbBvV]?matrix)\})/))) {
        // display math: $$ … $$, \[ … \] or an environment, on one line or several (also inside a list item)
        const env = m[2], close = env ? `\\end{${env}}` : m[1] === "$$" ? "$$" : "\\]";
        const first = line.trim().slice(env ? 0 : 2), j = first.indexOf(close, env ? m[1].length : 0);
        const cut = (x, k) => x.slice(0, env ? k + close.length : k);       // an environment keeps its \begin … \end
        let body = null, after = "", k = i;
        if (j >= 0) { body = cut(first, j); after = first.slice(j + close.length).trim(); }
        else for (k = i + 1; k < lines.length; k++) {
          const q = lines[k].indexOf(close);
          if (q >= 0) { body = [first, ...lines.slice(i + 1, k), cut(lines[k], q)].join("\n"); after = lines[k].slice(q + close.length).trim(); break; }
        }
        const inList = lists.length && /^\s/.test(raw);
        if (body == null && opts.streaming) {                           // still being written: drawn once it is closed
          if (inList) flushPara(); else flushAll();
          out += `<div class="math-block pending"></div>`; i = lines.length; continue;
        }
        if (body != null && (j < 0 || !after || /^[.,;:!?]$/.test(after))) {   // (text after it on that line: an inline formula)
          if (inList) flushPara(); else flushAll();
          out += `<div class="math-block">${texToHtml(body, true)}</div>`;
          if (after && !/^[.,;:!?]$/.test(after)) para.push(after);
          i = k + 1; continue;
        }
      }
      if (!line.trim()) {                                             // blank: a list may continue after it
        flushPara();
        let j = i + 1; while (j < lines.length && !lines[j].trim()) j++;
        if (!lists.length || j >= lines.length || !(isItem(lines[j]) || /^\s{2,}\S/.test(lines[j]))) closeLists();
        i++; continue;
      }
      if ((m = line.match(/^(#{1,6})\s+(.*)$/))) { flushAll(); const n = Math.min(3, m[1].length); out += `<h${n}>${inline(m[2])}</h${n}>`; i++; continue; }
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flushAll(); out += "<hr>"; i++; continue; }
      if (line.includes("|") && i + 1 < lines.length && lines[i + 1].includes("-") && /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(lines[i + 1])) {   // table
        flushAll();
        const cells = (l) => l.trim().replace(/^\|/, "").replace(/(?<!\\)\|$/, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
        let rows = "";
        const head = cells(line);
        for (i += 2; i < lines.length && lines[i].includes("|") && lines[i].trim(); i++) rows += `<tr>${cells(lines[i]).map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`;
        out += `<div class="table-wrap"><table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
        continue;
      }
      if ((m = line.match(/^\s*>\s?(.*)$/))) {                        // quote
        flushAll();
        const buf = [];
        for (; i < lines.length && (m = lines[i].replace(/\s+$/, "").match(/^\s*>\s?(.*)$/)); i++) buf.push(m[1]);
        const writing = opts.streaming && i >= lines.length, al = buf[0].trim().match(MD_ALERT);
        if (al) { out += mdAlert(al[1].toLowerCase(), al[2] ? inline(al[2]) : "", mdRender(buf.slice(1).join("\n"), { ...opts, streaming: writing })); continue; }
        if (writing && buf.length === 1 && /^\[(!\w*\]?)?$/.test(buf[0].trim())) continue;   // "> [!WARN" still being written
        // its own Markdown inside (a worked solution: lines, display formulas, lists)
        out += `<blockquote>${mdRender(buf.join("\n"), { ...opts, streaming: writing })}</blockquote>`; continue;
      }
      if ((m = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/))) {         // list item (nested by indentation)
        flushPara();
        const indent = m[1].replace(/\t/g, "    ").length, type = /\d/.test(m[2]) ? "ol" : "ul";
        while (lists.length && indent < lists[lists.length - 1].indent) out += `</li></${lists.pop().type}>`;
        const top = lists[lists.length - 1];
        const open = () => { const st = type === "ol" ? parseInt(m[2], 10) : 1; out += `<${type}${st > 1 ? ` start="${st}"` : ""}><li>`; lists.push({ type, indent }); };
        if (!top || indent > top.indent) open();
        else if (top.type !== type) { out += `</li></${lists.pop().type}>`; open(); }
        else out += "</li><li>";
        out += inline(m[3].replace(/^\[([ xX])\]\s+/, (s, x) => (x.trim() ? "☑ " : "☐ ")));
        i++; continue;
      }
      if (lists.length && /^\s{2,}\S/.test(raw)) { out += `<p class="li-p">${inline(line.trim())}</p>`; i++; continue; }
      if (lists.length) closeLists();
      para.push(line.trim()); i++;
    }
    flushAll();
    return out;
  }

  // ---- library
  async function loadLibrary() {
    const res = await call("library");
    if (res.ok) { S.lib = res.courses; if (S.view.name === "library") renderView(); }
  }
  let searchTimer = null;
  function runSearch(q) {
    S.search.q = q; clearTimeout(searchTimer);
    if (q.trim().length < 2) { S.search.results = null; renderSearchResults(); return; }
    searchTimer = setTimeout(async () => {
      const res = await call("search", q);
      if (res.ok && S.search.q === q) { S.search.results = res.results; S.search.materials = res.materials || []; renderSearchResults(); }
    }, 220);
  }
  function libraryHTML() {
    let h = `<div class="view-inner"><div class="search-big">${I("search")}<input class="input" placeholder="${t("Search your lectures and course files… (English or Turkish)")}" data-act="search" value="${esc(S.search.q)}"></div>
      <div id="search-results"></div>`;
    if (S.lib == null) h += skeleton();
    else if (!S.lib.length) h += emptyHTML("library", t("No transcripts yet"), t("Transcribed lectures appear here, grouped by course."));
    else for (const c of S.lib) {
      const ask = (S.system.ai || {}).available ? `<span class="spacer"></span><button class="btn ghost sm" data-act="ask-course" data-id="${esc(c.course_id)}">${I("chat", "sm")} ${t.html("Ask {ai}", { ai: aiName() })}</button>` : "";
      h += `<div class="course-group-title"><span class="pill new">${esc(c.code)}</span> ${esc(c.name)}${ask}</div><div class="lib-grid">` +
        c.days.map((d) => `<div class="card lib-day" data-act="open-day" data-day="${esc(d.day_key)}" tabindex="0" role="button"><div class="t">${esc(d.label)}</div>
          <div class="s">${esc(d.kind_label)} · ${t.n(d.parts, "{n} part", "{n} parts")} · ${fmtDur(d.duration_s)} ${d.transcribed ? "" : `· ${t("partly")}`}</div></div>`).join("") + `</div>`;
    }
    return h + `</div>`;
  }
  function renderSearchResults() {
    const el = $("#search-results"); if (!el) return;
    const r = S.search.results, m = S.search.materials || [];
    if (r == null) { el.innerHTML = ""; return; }
    if (!r.length && !m.length) { el.innerHTML = `<div class="card" style="padding:16px;margin-bottom:16px;color:var(--text-2)">${t.html("No matches for “{q}”.", { q: S.search.q })}</div>`; return; }
    const snip = (s) => esc(s).replace(/\u0002([^\u0003]*)\u0003/g, "<em>$1</em>");     // the words found
    el.innerHTML = (r.length ? `<div class="section-title">${I("wave")} ${t("In your lectures")} <span class="dim sm">${r.length}</span></div><div class="card" style="margin-bottom:18px">` + r.map((x) => `<button class="result" data-act="open-hit" data-day="${esc(x.day_key)}" data-id="${esc(x.delivery_id)}" data-t="${x.t}">
      <span class="where"><b>${esc(x.course_code)}</b> ${esc(shortDate(x.date))} · ${esc(x.time)} · ${t.html("at {ts}", { ts: x.ts })}</span>
      <span class="snippet">${snip(x.snippet)}</span></button>`).join("") + `</div>` : "") +
      (m.length ? `<div class="section-title">${I("layers")} ${t("In your course files")} <span class="dim sm">${m.length}</span></div><div class="card" style="margin-bottom:18px">` + m.map((x) => `<button class="result" data-act="open-mat-hit" data-id="${esc(x.resource_id)}" title="${t("Open the file")}">
      <span class="where"><b>${esc(x.course_code)}</b> ${esc(x.title)} · ${t("page {n}", { n: x.page })}</span>
      <span class="snippet">${snip(x.snippet)}</span></button>`).join("") + `</div>` : "");
    if (Studia.renderMath) Studia.renderMath(el);                       // formulas in course files' text
  }

  // ---- settings
  function settingsHTML() {
    const s = S.settings, sys = S.system, p = S.profile || {};
    const slowHere = (id) => sys.gpu && !gpuUsed(sys.gpu) && id !== "small";      // hours per lecture on a processor
    const small = ((sys.presets || []).find((x) => x.id === "small") || { label: "Small" }).label;   // its name as its card shows it
    const presets = (sys.presets || []).map((x) => `<button class="preset ${x.id === s.model ? "on" : ""}" data-act="set" data-key="model" data-val="${x.id}">
      <div class="n">${esc(x.label)} <span class="sz">${t.num(+(x.size_mb / 1000).toFixed(1), 1)} GB${x.downloaded ? " · ✓" : ""}</span></div><div class="d">${esc(x.description)}</div>
      ${slowHere(x.id) ? `<div class="d warn-txt">${I("alert", "xs")} ${t.html("Very slow on this PC (no NVIDIA graphics card in use): {model} is the one for it.", { model: small })}</div>` : ""}</button>`).join("");
    const g = sys.gpu;
    const row = (title, d, ctl) => `<div class="set-row"><div class="txt"><div class="t">${title}</div><div class="d">${d}</div></div>${ctl}</div>`;
    const sw = (key) => `<input type="checkbox" class="switch" data-act="set-bool" data-key="${key}" ${s[key] ? "checked" : ""}>`;
    const profs = S.profiles.length ? S.profiles : [{ id: "main", main: true, active: true, has_login: true, ...p }];
    const sections = [["set-profiles", t("Profiles")], ["set-tx", t("Transcription")], ["set-files", t("Files")], ["set-ai", t("AI")], ...(Studia.yt ? [["set-yt", "YouTube"]] : []),
      ["set-bg", t("Notifications")], ["set-storage", t("Storage")], ["set-about", t("About")], ["set-look", t("Appearance")]];
    return `<div class="view-inner" style="max-width:820px">
      <nav class="set-nav">${sections.map(([id, l]) => `<button class="btn ghost sm" data-act="set-jump" data-id="${id}">${l}</button>`).join("")}</nav>
      <div class="card set-card" id="set-profiles"><h3>${t("Profiles")} <span class="dim sm">${t("one per IEULearn account")}</span></h3>
        ${profs.map((x) => `<div class="set-row prof-row"><div class="avatar">${esc(initials(x.name || x.username))}</div>
          <div class="txt"><div class="t">${esc(x.name || x.username || t("New profile"))} ${x.active ? `<span class="pill">${t("This profile")}</span>` : ""}</div>
            <div class="d">${esc([x.student_number, x.username].filter(Boolean).join(" · ") || t("Not signed in yet"))}${x.active ? ` · ${t("password stored encrypted on this PC")}` : x.has_login ? "" : ` · ${t("signed out")}`}</div></div>
          <div class="prof-acts">${x.active ? `<button class="btn danger sm" data-act="signout">${I("logout", "sm")} ${t("Sign out")}</button>`
            : `<button class="btn sm" data-act="profile-switch" data-id="${esc(x.id)}">${t("Switch")}</button>${x.main ? "" : `<button class="btn ghost icon sm" data-act="profile-remove" data-id="${esc(x.id)}" title="${t("Remove this profile from this PC")}">${I("x", "sm")}</button>`}`}</div></div>`).join("")}
        ${row(t("Add another account"), t("Someone else uses this PC, or you have a second IEULearn account? Each profile keeps its own courses, transcripts, notes and settings; switch from the menu at the bottom left."),
          `<button class="btn sm" data-act="profile-add">${I("plus", "sm")} ${t("Add account")}</button>`)}</div>
      <div class="card set-card" id="set-tx"><h3>${t("Transcription")}</h3>
        <div class="presets">${presets || `<div class="skel" style="height:70px"></div>`}</div>
        ${row(t("Transcribe with"), !g ? t("Checking…") : s.device === "cpu" && g.available ? t.html("Processor only, as chosen here: your {name} is not used.", { name: g.name || t("NVIDIA graphics card") })
            : gpuUsed(g) ? t.html("Your NVIDIA graphics card: {name} ({mb} MB)", { name: g.name, mb: g.vram_mb })
            : gpuFits(g) && !g.libraries ? t.html("Your {name} needs a one-time download from NVIDIA before it can transcribe (About → Run setup again, below). Until then the processor does it, more slowly.", { name: g.name || t("NVIDIA graphics card") })
            : g.available ? t.html("Your {name} can't transcribe (too little graphics memory or an old driver): the processor does it, slower.", { name: g.name || t("NVIDIA graphics card") })
            : t("No NVIDIA graphics card found: the processor transcribes (slower)."),
          `<select class="input" style="width:170px" data-act="set-sel" data-key="device">${["auto", "cuda", "cpu"].map((v) => `<option value="${v}" ${s.device === v ? "selected" : ""}>${{ auto: t("Automatic"), cuda: t("Graphics card"), cpu: t("Processor only") }[v]}</option>`).join("")}</select>`)}
        ${row(t("Languages"), t("English with Turkish passages: each passage is transcribed in the language actually spoken and tagged [TR]."), `<span class="chip">${t("English + Turkish")}</span>`)}
        ${row(t("Capture slides"), t("Takes the slides and board from Panopto's screen recording and shows them next to the transcript (adds about a minute of downloading per lecture)."), sw("capture_slides"))}
        ${row(t("Ask before closing"), t("Asks first when you close IEUStudia while lectures are being transcribed. Either way, they go on where they stopped next time."), sw("ask_before_closing"))}
        ${row(t("Hide very short recordings"), t("Recordings shorter than this (usually started by mistake) are greyed out."), `<select class="input" style="width:110px" data-act="set-sel" data-key="min_duration_s">${[30, 60, 120, 300].map((v) => `<option value="${v}" ${+s.min_duration_s === v ? "selected" : ""}>${v < 60 ? t("{n} s", { n: v }) : t("{m} min", { m: v / 60 })}</option>`).join("")}</select>`)}
      </div>
      <div class="card set-card" id="set-files"><h3>${t("Files")}</h3>
        ${row(t("Save transcripts and notes in"), `<span class="path">${esc(s.output_dir)}</span>`, `<div style="display:flex;gap:6px"><button class="btn sm" data-act="pick-folder">${t("Change…")}</button><button class="btn ghost sm" data-act="open-output">${I("folder-open", "sm")}</button></div>`)}
        ${row(t("Subtitles (.srt)"), t("Also write a subtitle file per recording."), sw("write_srt"))}
        ${row(t("Keep downloaded audio"), t("Keep the audio files (≈40 MB per hour) after transcription."), sw("keep_audio"))}
      </div>
      <div class="card set-card" id="set-ai"><h3>${t("AI (optional)")}</h3>
        ${Studia.settingsAI ? Studia.settingsAI() : ""}
        ${Studia.aiLevelsHTML ? Studia.aiLevelsHTML(sys, s) : ""}
      </div>
      ${Studia.settingsMore ? Studia.settingsMore() : ""}
      <div class="card set-card" id="set-look"><h3>${t("Appearance")}</h3>
        ${row(t.lang() === "en" ? "Language" : `${t("Language")} · Language`, t("IEUStudia starts in the language Windows uses. Pick one here to keep it."), langSeg())}
        ${row(t("Theme"), "", `<div class="seg">${[["system", t.c("theme", "System")], ["light", t.c("theme", "Light")], ["dark", t.c("theme", "Dark")]].map(([v, l]) => `<button class="${s.theme === v ? "on" : ""}" data-act="set" data-key="theme" data-val="${v}">${l}</button>`).join("")}</div>`)}
        ${row(t("Reading text"), t("Transcripts, notes and chat. Ctrl and + or − change it too, anywhere in IEUStudia."), `<div class="seg">${TEXT_SIZES().map(([v, l]) => `<button class="${+(s.text_size || 100) === v ? "on" : ""}" data-act="set-size" data-val="${v}">${l}</button>`).join("")}</div>`)}
      </div></div>`;
  }

  const TEXT_SIZES = () => [[90, t("Smaller")], [100, t("Normal")], [115, t("Larger")], [130, t("Largest")]];   // a function: labels in the current language
  // "" follows Windows; the names of the languages are in their own language
  const langSeg = () => `<div class="seg">${[["", t("As Windows ({name})", { name: t.windows() === "tr" ? "Türkçe" : "English" })], ["en", "English"], ["tr", "Türkçe"]]
    .map(([v, l]) => `<button class="${t.setting() === v ? "on" : ""}" data-act="set-lang" data-val="${v}">${esc(l)}</button>`).join("")}</div>`;
  Studia.langSeg = langSeg;

  // ---- chat with Claude
  // functions, not arrays: the text is in the language of the moment. A suggestion becomes the student's own question
  // when clicked (the AI answers in the language it is asked in).
  const SUGGEST_ONE = () => [
    t("What will most likely be on the quiz or exam from this lecture?"),
    t("What did the lecturer emphasise the most?"),
    t("Summarise this lecture in 10 bullet points."),
    t("Were any homework, deadlines or announcements mentioned?"),
    t("Explain the hardest part of this lecture step by step."),
    t("Give me 5 practice questions with answers."),
  ];
  const SUGGEST_MANY = () => [
    t("What are the likely quiz topics, based on these lectures?"),
    t("List every announcement about quizzes, exams and homework."),
    t("Which topics did the lecturer stress the most?"),
    t("Make a one-page revision sheet of these lectures."),
    t("In which order should I study these topics, and why?"),
    t("Give me 8 practice questions in the lecturer's style."),
  ];
  const MODELS = () => [["haiku", "Haiku", t("lightest")], ["sonnet", "Sonnet", t("recommended")], ["opus", "Opus", t("best, uses the most")]];
  const aiName = () => ((S.system.ai || {}).label || t("AI"));          // ChatGPT, Claude or Offline model
  // a noun phrase that sentences take as {plan} (the Turkish ones keep it in the nominative)
  const aiPlan = () => ({ chatgpt: t("your own ChatGPT plan"), claude: t("your own Claude plan"), codex: t("your own ChatGPT plan"), local: t("the offline model on this PC") }[(S.system.ai || {}).provider] || t("the AI you chose"));
  const aiHint = () => { const ai = S.system.ai || {};   // why an AI button is greyed out
    return ai.provider === "local" ? t("Download the offline model in Settings → AI to use this")
      : ai.label ? t("Connect {ai} in Settings → AI to use this", { ai: ai.label }) : t("Turn on AI in Settings → AI to use this"); };
  // ChatGPT ("Continue with ChatGPT" or the Codex CLI) has no model names to pick from, only how much it reasons (effort.js)
  const isChatGPT = (ai) => ["chatgpt", "codex"].includes((ai || S.system.ai || {}).provider);
  const gpuUsed = (g) => !!g && !!g.available && !!g.libraries && S.settings.device !== "cpu";    // does the graphics card transcribe?
  const gpuFits = (g) => !!g && g.available && (g.fits != null ? g.fits : (g.vram_mb || 0) >= 3500);   // what the setup asks of a card
  const modelsFor = () => (isChatGPT() ? [] : MODELS());
  const dec = (x, digits) => t.num(+x.toFixed(digits), digits);    // rounded as toFixed rounds, written the language's way: 11.4 · 11,4
  const fmtK = (n) => (n >= 1000 ? t("{n}k", { n: dec(n / 1000, n >= 100000 ? 0 : 1) }) : t.num(Math.round(n || 0)));   // 31.2k · 31,2 bin
  const shortDate = (iso) => t.date(iso, "short");      // Wed 14 Oct · 14 Eki Çar
  function resetAt(ts) {                                 // 14:05, or Wed 14:05 · Çar 14:05 on another day
    if (!ts) return "";
    const d = new Date(ts * 1000), hm = t.date(d, "time");
    return d.toDateString() === new Date().toDateString() ? hm : `${t.date(d, "weekday")} ${hm}`;
  }
  function limitsHTML() {
    if ((S.system.ai || {}).provider !== "claude") return "";       // only Claude Code reports them
    const L = S.limits || {};
    const one = (k, five) => {
      const w = L[k]; if (!w || w.used == null) return "";
      const p = Math.min(100, Math.round(w.used * 100)), pc = t.pct(p / 100);
      const tip = (five ? t("5-hour limit: {pct} used", { pct: pc }) : t("weekly limit: {pct} used", { pct: pc }))
        + (w.resets_at ? " · " + t("resets {when}", { when: resetAt(w.resets_at) }) : "");
      return `<span class="lim ${p >= 90 ? "hot" : p >= 70 ? "warm" : ""}" title="${esc(tip)}"><span class="lim-bar"><i style="width:${p}%"></i></span>${esc(five ? t("{pct} 5-hour", { pct: pc }) : t("{pct} weekly", { pct: pc }))}</span>`;
    };
    const parts = [one("five_hour", true), one("seven_day", false)].filter(Boolean);
    if (!parts.length) return "";
    const tip = L.at ? t("Your Claude subscription usage, as last reported by Claude Code, {when}", { when: rel(new Date(L.at * 1000).toISOString()) })
      : t("Your Claude subscription usage, as last reported by Claude Code");
    return `<span class="limits" title="${esc(tip)}">${I("zap", "sm")}${parts.join("")}</span>`;
  }
  function tokensText(u) {
    const cached = u.cache_read_input_tokens || 0;
    const read = (u.input_tokens || 0) + (u.cache_creation_input_tokens || 0) + cached;
    if (!read && !u.output_tokens) return "";
    const p = { k: fmtK(read), out: fmtK(u.output_tokens || 0), pct: t.pct(Math.round((cached / read) * 100) / 100) };
    return cached ? t("{k} read ({pct} from cache) · {out} written", p) : t("{k} read · {out} written", p);
  }

  function chatHTML() {
    return `<div class="chat-layout"><aside class="chat-list">
        <button class="btn grad sm block" data-act="chat-new" ${(S.system.ai || {}).available ? "" : "disabled"}>${I("plus", "sm")} ${t("New chat")}</button>
        <div class="chat-items" id="chat-items">${chatItemsHTML()}</div></aside>
      <section class="chat-main" id="chat-main">${chatMainHTML()}</section>${Studia.chatSideHTML ? Studia.chatSideHTML() : ""}</div>`;
  }
  function chatItemsHTML() {
    const l = S.chat.list;
    if (l == null) return `<div class="skel" style="height:46px;margin:8px 2px"></div><div class="skel" style="height:46px;margin:8px 2px"></div>`;
    if (!l.length) return `<div class="chat-none">${t("Your chats appear here.")}</div>`;
    return l.map((c) => `<button class="chat-item ${S.chat.id === c.id && !S.chat.draft ? "on" : ""}" data-act="chat-open" data-id="${esc(c.id)}">
      <span class="t">${c.running ? SPIN() : ""}${esc(c.title || t("New chat"))}</span><span class="s"><b>${esc(c.course_code)}</b> · ${esc(c.scope_label)}</span>
      <span class="s">${esc(rel(c.updated_at))} · ${esc((S.system.ai || {}).provider === "local" ? aiName() : isChatGPT() && Studia.effortName ? Studia.effortName(c.effort) : levelLabel(c.model))}</span></button>`).join("");
  }
  function chatMainHTML() {
    const d = S.chat.draft, c = S.chat.data;
    if (!(S.system.ai || {}).available) {
      const ai = S.system.ai || {};
      const [title, text, btn] = ai.provider === "local" ? [t("The offline model isn't downloaded yet"), t("Download it to chat about your lectures on this PC. Everything else in IEUStudia works without it."), t("Download it")]
        : ai.label ? [t("{ai} isn't connected yet", { ai: ai.label }), t("Connect your {ai} plan to chat about your lectures. Everything else in IEUStudia works without it.", { ai: ai.label }), t("Connect {ai}", { ai: ai.label })]
        : [t("AI is off"), t("Pick ChatGPT (with your ChatGPT plan), Claude (your Claude Pro or Max plan) or the offline model to chat about your lectures. Everything else in IEUStudia works without it."), t("Turn on AI")];
      return `<div class="chat-scroll">${emptyHTML("chat", title, text)}<div style="text-align:center"><button class="btn primary" data-act="setup-ai">${I("ai", "sm")} ${btn}</button></div></div>`;
    }
    if (!d && !S.chat.id) {
      return `<div class="chat-scroll"><div class="chat-empty"><div class="art">${I("chat")}</div><h3>${t.html("Ask {ai} about your lectures", { ai: aiName() })}</h3>
        <div class="sub">${t.html("Pick one lecture or several and ask anything: likely quiz topics, explanations, practice questions… It uses {plan}, only when you ask. AI can make mistakes — check important answers.", { plan: aiPlan() })}</div>
        <button class="btn grad" data-act="chat-new">${I("plus", "sm")} ${t("New chat")}</button>${limitsHTML() ? `<div class="limits-row">${limitsHTML()}</div>` : ""}</div></div>`;
    }
    if (!d && (!c || c.id !== S.chat.id)) return `<div class="chat-scroll"><div class="chat-msgs-inner">${skeleton()}</div></div>`;
    return `<header class="chat-head" id="chat-head">${chatHeadHTML()}</header>
      <div class="chat-scroll" id="chat-msgs"><div class="chat-msgs-inner" id="chat-msgs-inner">${chatMsgsHTML()}</div></div>
      <form class="composer" data-form="chat">${Studia.chatToolsHTML ? Studia.chatToolsHTML() : ""}${Studia.chatFilesRowHTML ? Studia.chatFilesRowHTML() : ""}<div class="composer-inner">
        ${Studia.chatAttachBtn ? Studia.chatAttachBtn() : ""}<textarea id="chat-input" rows="1" placeholder="${esc(composerPlaceholder())}">${esc(S.chat.input)}</textarea>
        ${Studia.effortChip ? Studia.effortChip() : ""}
        <span id="composer-btn">${composerBtnHTML()}</span></div>
        <div class="composer-note">${t.html("Enter to send · Shift+Enter for a new line · uses {plan} · AI can make mistakes", { plan: aiPlan() })}</div></form>`;
  }
  function chatHeadHTML() {
    const d = S.chat.draft, c = S.chat.data, x = d || c;
    const days = d ? d.labels : c.days;
    const chips = days.slice(0, 6).map((day) => `<button class="day-chip ${day.gone ? "gone" : ""}" data-act="chat-day" data-key="${esc(day.key)}" title="${esc(t("{day} — open the transcript", { day: day.long || day.label }))}">${days.length > 1 ? `<b>L${day.n}</b>` : ""}${esc(day.label)} · ${esc(day.kind)}</button>`).join("")
      + (days.length > 6 ? `<span class="dim">${t("+{n} more", { n: days.length - 6 })}</span>` : "");
    const model = x.model || "sonnet";
    const sel = isChatGPT() ? "" : `<select class="input model-sel" data-act="${d ? "draft-model" : "chat-model"}" title="${esc(t("Model"))}">${modelsFor().map(([v, l, h]) => `<option value="${v}" ${v === model ? "selected" : ""}>${l} · ${h}</option>`).join("")}</select>`;
    // the video's slides that go: next to the chosen slide files, fewer than were captured (chatfiles.js reads it)
    const fit = d && (d.slides && d.videoFit != null ? d.videoFit : Math.min(d.slidesTotal, 60));
    const slides = d ? (d.slidesTotal ? `<label class="mini-toggle" title="${esc(t("Attach the slide images (≈{k} more tokens)", { k: fmtK(fit * 800) }))}"><input type="checkbox" class="switch sm" data-act="draft-slides" ${d.slides ? "checked" : ""}>${t("Slides ({n})", { n: fit })}</label>` : "")
      : (c.slides ? `<span class="pill">${I("monitor", "sm")}${t.n(c.images, "{n} slide", "{n} slides")}</span>` : "");
    const focus = x.focus === "highlights" ? `<span class="pill processing">${I("star", "sm")}${t("Highlights only")}</span>`
      : x.focus === "course" ? `<span class="pill processing">${I("library", "sm")}${t("Whole course")}</span>` : "";
    const tokens = d ? d.tokens : c.context_tokens;
    const title = d || !c.title ? t("New chat") : c.title;
    return `<div class="ch-top"><div class="ch-title"><h2 title="${esc(title)}">${esc(title)}</h2>${d ? "" : `<button class="btn ghost icon sm" data-act="chat-rename" title="${esc(t("Rename"))}">${I("pencil", "sm")}</button>`}</div>
        ${sel}${Studia.chatSideBtn ? Studia.chatSideBtn() : ""}${d ? `<button class="btn ghost sm" data-act="draft-change">${I("list-checks", "sm")} ${t("Change…")}</button>` : `<button class="btn ghost icon sm" data-act="chat-menu" title="${esc(t("More"))}">${I("more")}</button>`}</div>
      <div class="ch-scope"><span class="pill new">${esc(x.course_code || "")}</span>${chips}${focus}${slides}
        ${tokens ? `<span class="ch-meta" title="${esc(t("{ai} reads this with your first question", { ai: aiName() }))}">${t("~{k} tokens", { k: fmtK(tokens) })}</span>` : ""}<span class="spacer"></span>${limitsHTML()}</div>`;
  }
  function chatMsgsHTML() {
    const c = S.chat.data, d = S.chat.draft;
    const msgs = d ? [] : c.messages;
    if (!msgs.length) {
      const multi = (d ? d.labels : c.days).length > 1;
      return `<div class="chat-empty"><div class="art">${I("chat")}</div><h3>${multi ? t("Ask anything about these lectures") : t("Ask anything about this lecture")}</h3>
        <div class="sub">${multi ? t.html("{ai} reads the transcripts with your first question; follow-up questions build on them and cost much less.", { ai: aiName() })
          : t.html("{ai} reads the transcript with your first question; follow-up questions build on it and cost much less.", { ai: aiName() })}</div>
        <div class="suggest">${(multi ? SUGGEST_MANY() : SUGGEST_ONE()).map((s) => `<button data-act="chat-suggest" data-text="${esc(s)}">${esc(s)}</button>`).join("")}</div></div>`;
    }
    return msgs.map((m) => chatMsgHTML(m)).join("");
  }
  function chatMsgHTML(m) {
    const c = S.chat.data, isLast = !!c && c.messages.length > 0 && c.messages[c.messages.length - 1].seq === m.seq;
    if (m.role === "user") return `<div class="msg user" data-seq="${m.seq}"><div class="bubble">${esc(m.text)}</div></div>`;
    const u = m.usage || {}, streaming = m.status === "streaming", tok = tokensText(u);
    const acts = Studia.actionCards ? Studia.actionCards(m.text, m.seq, streaming) : { text: m.text, html: "" };   // buttons the AI suggested
    const body = acts.text ? mdToHtml(acts.text, { cites: true, streaming }) + acts.html : streaming ? `<div class="typing"><i></i><i></i><i></i></div>` : "";
    const notes = Studia.chatNotices ? Studia.chatNotices(m, isLast) : {};    // the app's notices with this answer (notices.js)
    const foot = streaming ? `<span class="dim">${SPIN()} ${t.html("{ai} is answering…", { ai: aiName() })}</span>`
      : `${m.status === "stopped" ? `<span class="pill">${t("Stopped")}</span>` : ""}${tok ? `<span class="tok" title="${esc(t("How much text this answer read and wrote, in tokens (a word or part of a word each). Text it already read recently in this chat counts much less."))}">${tok}</span>` : ""}${m.text ? `<button class="btn ghost sm" data-act="chat-copy" data-seq="${m.seq}">${I("copy", "sm")} ${t("Copy")}</button>` : ""}`;
    const retry = isLast ? `<div style="margin-top:8px"><button class="btn sm" data-act="chat-retry" data-seq="${m.seq}">${I("retry", "sm")} ${t("Try again")}</button></div>` : "";
    const err = m.status === "error" ? notes.error || `<div class="callout error">${I("alert-circle")}<div>${esc(u.error || t("{ai} could not answer.", { ai: aiName() }))}${retry}</div></div>` : "";
    return `<div class="msg ai" data-seq="${m.seq}"><div class="ai-av">${I("ai")}</div><div class="ai-body">${notes.before || ""}${body ? `<div class="md">${body}</div>` : ""}${err}<div class="msg-foot">${foot}</div></div></div>`;
  }
  function composerPlaceholder() {
    const d = S.chat.draft, c = S.chat.data;
    const multi = d ? d.labels.length > 1 : !!(c && c.days.length > 1);
    if (c && !d && c.messages.length) return t("Ask a follow-up question…");
    return multi ? t("Ask anything about these lectures…") : t("Ask anything about this lecture…");
  }
  function composerBtnHTML() {
    const c = S.chat.data;
    if (!S.chat.draft && c && c.running) return `<button type="button" class="btn sm" data-act="chat-stop">${I("stop", "sm")} ${t("Stop")}</button>`;
    return `<button type="submit" class="btn grad sm" ${S.chat.sending ? "disabled" : ""}>${S.chat.sending ? SPIN() : I("arrow-right", "sm")} ${t("Send")}</button>`;
  }
  function renderComposerBtn() { const el = $("#composer-btn"); if (el) el.innerHTML = composerBtnHTML(); const ta = $("#chat-input"); if (ta) ta.placeholder = composerPlaceholder(); }
  function renderChatHead() { const el = $("#chat-head"); if (el) el.innerHTML = chatHeadHTML(); if (Studia.chatSideRender) Studia.chatSideRender(); }
  function renderChatMsgs() { const el = $("#chat-msgs-inner"); if (el) { el.innerHTML = chatMsgsHTML(); scrollChatBottom(true); } }
  function renderChatMsg(m) {
    const box = $("#chat-msgs"), node = box && box.querySelector(`.msg[data-seq="${m.seq}"]`);
    if (!node) return false;
    const near = box.scrollHeight - box.scrollTop - box.clientHeight < 140;
    node.outerHTML = chatMsgHTML(m);
    if (near) box.scrollTop = box.scrollHeight;
    return true;
  }
  function renderChatMain() {
    const el = $("#chat-main"); if (el) el.innerHTML = chatMainHTML();
    if (Studia.chatSideRender) Studia.chatSideRender();       // "What this chat sees" (chatfiles.js)
    const it = $("#chat-items"); if (it) it.innerHTML = chatItemsHTML();
  }
  function scrollChatBottom(force) { const box = $("#chat-msgs"); if (box && (force || box.scrollHeight - box.scrollTop - box.clientHeight < 140)) box.scrollTop = box.scrollHeight; }
  function saveChatScroll() { if (S.view.name === "chat") { const box = $("#chat-msgs"); S.chat.scroll = box ? box.scrollTop : null; } }
  function restoreChatScroll() { const box = $("#chat-msgs"); if (box) box.scrollTop = S.chat.scroll != null ? S.chat.scroll : box.scrollHeight; S.chat.scroll = null; }
  function autosize(ta) { ta.style.height = "auto"; ta.style.height = Math.min(200, ta.scrollHeight + 2) + "px"; }
  function focusComposer() { setTimeout(() => { const ta = $("#chat-input"); if (ta && S.view.name === "chat") { autosize(ta); ta.focus(); } }, 30); }

  async function loadChats() {
    const r = await call("chat_list");
    if (!r.ok) return;
    S.chat.list = r.chats || [];
    if (r.limits && Object.keys(r.limits).length) S.limits = r.limits;
    const el = $("#chat-items"); if (el) el.innerHTML = chatItemsHTML();
  }
  async function openChat(id) {
    if (S.qpOpen) closePane();     // a chat in the sidebar
    if (id !== S.chat.id) S.chat.scroll = null;
    S.chat.id = id; S.chat.draft = null;
    if (S.view.name !== "chat" || S.view.chatId !== id) { S.view = { name: "chat", chatId: id }; renderSidebar(); renderTop(); }
    if (!$("#chat-main")) renderView(); else renderChatMain();
    const r = await call("chat_get", id);
    if (S.chat.id !== id) return;
    if (!r.ok) {
      toast(r.error, "error"); S.chat.id = null; S.chat.data = null;
      if (S.view.name === "chat") { S.view = { name: "chat" }; renderChatMain(); }
      loadChats(); return;
    }
    S.chat.data = r.chat;
    if (r.chat.limits && Object.keys(r.chat.limits).length) S.limits = r.chat.limits;
    if (S.view.name === "chat") { renderChatMain(); restoreChatScroll(); focusComposer(); }
  }
  function startDraft(d) {
    if (S.qpOpen) closePane();
    const inChat = S.view.name === "chat" && $("#chat-main");
    S.chat.draft = { tokens: 0, slidesTotal: 0, ...d }; S.chat.id = null; S.chat.data = null; S.chat.scroll = null;
    S.view = { name: "chat" };          // never keep an older chat's id: Back would reopen it instead of this draft
    renderSidebar(); renderTop();
    if (inChat) renderChatMain(); else { renderView(); loadChats(); }
    focusComposer(); estimateDraft();
  }
  async function estimateDraft() {
    const d = S.chat.draft; if (!d) return;
    const r = await call("chat_estimate", d.course_id, d.days, d.focus, d.slides, d.send, d.uploads || "");
    if (r.ok && S.chat.draft === d) { d.tokens = r.tokens; d.slidesTotal = r.slides_total; renderChatHead(); }
  }
  async function sendChat(textArg) {
    const box = $("#chat-input");
    const text = String(textArg != null ? textArg : box ? box.value : "").trim();
    if (!text || S.chat.sending) return;
    if (Studia.slashCommand && Studia.slashCommand(text)) {            // "/plan quiz 2", "/ready", … run here, not sent
      S.chat.input = ""; if (box) { box.value = ""; autosize(box); }
      return;
    }
    if (!S.chat.draft && S.chat.data && S.chat.data.running) { toast(t("{ai} is still answering. Wait for it, or press Stop.", { ai: aiName() }), "info"); return; }
    if (S.chat.draft && Studia.chatUploading && Studia.chatUploading()) { toast(t("Wait until your files are added, then send."), "info"); return; }
    S.chat.sending = true; renderComposerBtn();
    let id = S.chat.id;
    if (S.chat.draft) {
      const d = S.chat.draft;
      const r = await call("chat_create", d.course_id, d.days, d.focus, d.slides, d.model, d.effort || "", d.send, d.uploads || "");
      if (S.chat.draft !== d) { S.chat.sending = false; return; }          // navigated away meanwhile
      if (!r.ok) { S.chat.sending = false; renderComposerBtn(); toast(r.error, "error"); return; }
      S.chat.draft = null; S.chat.id = id = r.chat.id; S.chat.data = r.chat;
      if (S.view.name === "chat") S.view = { name: "chat", chatId: id };
    }
    const r = await call("chat_send", id, text);
    S.chat.sending = false;
    if (!r.ok) { toast(r.error, "error"); if (S.view.name === "chat" && S.chat.id === id) { renderChatHead(); renderComposerBtn(); } return; }
    if (S.chat.input.trim() === text) S.chat.input = "";          // sent: but keep anything typed meanwhile
    const ta = $("#chat-input");
    if (ta && ta.value.trim() === text) { ta.value = ""; autosize(ta); }
    const c = S.chat.data;
    if (c && c.id === id) {
      if (!c.messages.some((m) => m.seq === r.user_seq)) c.messages.push({ seq: r.user_seq, role: "user", text, status: "done", usage: {} });
      if (!c.messages.some((m) => m.seq === r.seq)) c.messages.push({ seq: r.seq, role: "assistant", text: "", status: "streaming", usage: {} });
      c.messages.sort((a, b) => a.seq - b.seq);
      c.running = c.messages.some((m) => m.status === "streaming");
      if (!c.title) c.title = text.length > 64 ? text.slice(0, 61).trimEnd() + "…" : text;
    }
    if (S.view.name === "chat" && S.chat.id === id) { renderChatHead(); renderChatMsgs(); renderComposerBtn(); focusComposer(); }
    loadChats();
  }

  async function chatPicker(courseId, preset) {
    const cr = await call("chat_courses");
    if (!cr.ok) { toast(cr.error, "error"); return; }
    if (cr.limits && Object.keys(cr.limits).length) S.limits = cr.limits;
    if (!cr.courses.length) { toast(t("Transcribe a lecture first, then you can ask {ai} about it.", { ai: aiName() }), "info"); return; }
    const st = { course: cr.courses.some((c) => c.id === courseId) ? courseId : cr.courses[0].id, scope: null,
      sel: new Set(preset ? preset.days : []), focus: (preset && preset.focus) || "full", slides: !!(preset && preset.slides),
      model: (preset && preset.model) || S.settings.ai_model || "sonnet", keep: !!preset };
    const daysOf = () => (st.scope ? st.scope.days : []);
    const whole = () => st.focus === "course";              // every lecture's highlights + the course files, searched per question
    const chosen = () => daysOf().filter((d) => whole() || st.sel.has(d.day_key));
    const tokOf = (d) => (st.focus === "full" ? d.tokens : d.hl_tokens);
    const slideCount = () => chosen().reduce((n, d) => n + d.slides, 0);
    const total = () => 3000 + chosen().reduce((n, d) => n + tokOf(d), 0) + (st.slides ? Math.min(60, slideCount()) * 800 : 0);
    const pickDefault = () => {   // newest days, as many as fit comfortably
      st.sel = new Set(); let sum = 0;
      for (const d of daysOf()) { if (st.sel.size && sum + tokOf(d) > 60000) break; st.sel.add(d.day_key); sum += tokOf(d); }
    };
    const draw = () => {
      const old = $(".cp-days"), keepScroll = old ? old.scrollTop : 0;
      const oldBody = $(".cp-scroll"), keepBody = oldBody ? oldBody.scrollTop : 0;
      const ai = S.system.ai || {}, who = aiName();
      const max = (st.scope && st.scope.max_tokens) || 150000, tot = total(), over = tot > max, sc = slideCount();
      const images = ai.can_images !== false && ai.provider !== "local";
      const modes = [["full", "file-text", t("Chosen class days"), t("Full transcripts of the days you pick. Best for questions about specific lectures.")],
        ["highlights", "star", t("Highlights only"), t("Only what the lecturer stressed, from the days you pick. Lightest.")],
        ["course", "library", t("Whole course"), t("Every lecture's highlights, plus matching pages of your slides and PDFs for each question.")]];
      const days = daysOf(), code = (cr.courses.find((c) => c.id === st.course) || {}).code;
      const list = st.scope == null ? `<div class="skel" style="height:120px;margin:6px"></div>`
        : !days.length ? `<div class="chat-none">${t("No transcribed class days in this course yet.")}</div>`
        : `<div class="cp-days">${days.map((d) => { const on = st.sel.has(d.day_key); return `<label class="cp-day ${on ? "on" : ""}"><input type="checkbox" class="check" data-act="cp-day" data-key="${esc(d.day_key)}" ${on ? "checked" : ""}>
            <span class="cp-d"><b>${esc(d.label)}</b> <span class="dim">· ${esc(d.kind_label)} · ${t.n(d.parts, "{n} part", "{n} parts")}</span></span>
            <span class="cp-meta">${d.stars ? `<span title="${esc(t("Moments the lecturer stressed"))}">${I("star", "xs")} ${d.stars}</span>` : ""}<span title="${esc(t("How much text {ai} reads for this day", { ai: who }))}">${t("≈{k} tokens", { k: fmtK(tokOf(d)) })}</span></span></label>`; }).join("")}</div>`;
      const models = modelsFor();
      const used = limitsHTML();
      modal(`<div class="cp-scroll">
        <h2>${I("chat")} ${t.html("Ask {ai} about your lectures", { ai: who })}</h2>
        <p class="lead">${t.html("Pick a course and what {ai} should read. It reads it once, with your first question; follow-up questions build on it. Sizes are in tokens: the small pieces of text AI plans count, a word or part of a word each.", { ai: who })}</p>
        <div class="field"><label>${t("Course")}</label><select class="input" data-act="cp-course">${cr.courses.map((c) => `<option value="${esc(c.id)}" ${c.id === st.course ? "selected" : ""}>${esc(c.code)} · ${esc(c.name)} (${t.n(c.days, "{n} transcribed day", "{n} transcribed days")})</option>`).join("")}</select></div>
        <div class="field"><label>${t.html("What {ai} reads", { ai: who })}</label><div class="cp-modes">${modes.map(([v, ic, name, d]) => `<button type="button" class="cp-mode ${st.focus === v ? "on" : ""}" data-act="cp-focus" data-val="${v}">
            <span class="cp-mode-t">${I(ic, "sm")} ${name}</span><span class="cp-mode-d">${d}</span></button>`).join("")}</div></div>
        ${whole() ? `<div class="cp-whole">${I("check-circle", "sm")}<div>${code
            ? t.n(days.length, "<b>All {n} transcribed class day</b> of {course}, plus its learning outcomes, weekly plan, announced quizzes and latest announcements.", "<b>All {n} transcribed class days</b> of {course}, plus its learning outcomes, weekly plan, announced quizzes and latest announcements.", { course: esc(code) })
            : t.n(days.length, "<b>All {n} transcribed class day</b> of this course, plus its learning outcomes, weekly plan, announced quizzes and latest announcements.", "<b>All {n} transcribed class days</b> of this course, plus its learning outcomes, weekly plan, announced quizzes and latest announcements.")}</div></div>`
          : `<div class="field"><label class="cp-label">${t("Class days")} <span class="dim sm">${t("{n} of {total} picked", { n: st.sel.size, total: days.length })}</span><span class="cp-tools"><button type="button" class="btn ghost sm" data-act="cp-last">${t("Last 3")}</button><button type="button" class="btn ghost sm" data-act="cp-all">${t("All")}</button><button type="button" class="btn ghost sm" data-act="cp-none">${t("None")}</button></span></label>${list}</div>`}
        <div class="cp-opts">
          ${ai.provider === "local" ? `<div class="cp-opt"><div class="t">${I("cpu", "xs")} ${t("Offline model")}</div><div class="d">${t("Runs on this PC. For each question it reads the parts of these lectures that match it.")}</div></div>`
            : isChatGPT(ai) ? "" : `<div class="cp-opt"><div class="t">${t("Model")}</div><div class="seg">${models.map(([v, l, h]) => `<button class="${st.model === v ? "on" : ""}" data-act="cp-model" data-val="${v}" title="${h}">${l}</button>`).join("")}</div></div>`}
          ${Studia.effortField ? Studia.effortField("cp", "chat", preset && preset.effort, { big: ai.provider === "local" ? "" : Studia.effortBig.chat(tot, whole()) }) : ""}
          ${images ? `<label class="cp-opt cp-switch"><div><div class="t">${t("Include slide images")}</div><div class="d">${sc ? t.n(Math.min(60, sc), "{n} slide · formulas and figures · ≈{k} more tokens", "{n} slides · formulas and figures · ≈{k} more tokens", { k: fmtK(Math.min(60, sc) * 800) }) : t("No slides were captured for these days.")}</div></div>
            <input type="checkbox" class="switch" data-act="cp-slides" ${st.slides && sc ? "checked" : ""} ${sc ? "" : "disabled"}></label>` : ""}</div>
        ${over ? `<div class="callout error">${I("alert-circle")}<div>${t("That is more than one chat can hold (≈{k} tokens). Pick fewer days, or Highlights only.", { k: fmtK(max) })}</div></div>` : ""}
        </div>
        <div class="cp-foot"><div class="cp-cost"><div>${ai.provider === "local" ? t("<b>≈{k} tokens</b> on your first question · runs on this PC", { k: fmtK(tot) })
            : t.html("<b>≈{k} tokens</b> on your first question · from your {ai} plan", { k: fmtK(tot), ai: who })}</div>
          ${used ? `<div class="cp-limits">${used}</div>` : ""}<div class="dim sm">${I("info", "xs")} ${t("Nothing is sent until you ask. AI can make mistakes — check important answers.")}</div></div>
          <div class="cp-btns"><button class="btn" data-act="modal-close">${t("Cancel")}</button><button class="btn grad" data-act="cp-go" ${chosen().length && !over ? "" : "disabled"}>${I("chat", "sm")} ${t("Start chat")}</button></div></div>`);
      $(".modal").classList.add("cp");
      const nl = $(".cp-days"); if (nl) nl.scrollTop = keepScroll;
      const nb = $(".cp-scroll"); if (nb) nb.scrollTop = keepBody;
    };
    const load = async () => {
      const course = st.course;
      st.scope = null; draw();
      const r = await call("chat_scope", course);
      if (st.course !== course) return;                 // the course was changed again meanwhile
      st.scope = r.ok ? r : { days: [], max_tokens: 150000 };
      if (st.keep) st.keep = false; else pickDefault();
      if ($(".modal.cp")) draw();                      // still open
    };
    window.__cp = {
      course: (v) => { st.course = v; st.keep = false; st.slides = false; load(); },
      day: (k, on) => { if (on) st.sel.add(k); else st.sel.delete(k); draw(); },
      all: () => { st.sel = new Set(daysOf().map((d) => d.day_key)); draw(); },
      last: () => { st.sel = new Set(daysOf().slice(0, 3).map((d) => d.day_key)); draw(); },
      none: () => { st.sel = new Set(); draw(); },
      focus: (v) => { st.focus = v; draw(); },
      slides: (v) => { st.slides = v; draw(); },
      model: (v) => { st.model = v; draw(); },
      go: () => {
        const list = chosen().sort((a, b) => a.start.localeCompare(b.start));
        if (!list.length || total() > ((st.scope && st.scope.max_tokens) || 150000)) return;
        const course = cr.courses.find((c) => c.id === st.course) || {};
        closeModal();
        startDraft({ course_id: st.course, course_code: course.code, days: list.map((d) => d.day_key),
          labels: list.map((d, i) => ({ key: d.day_key, n: i + 1, label: d.label, kind: d.kind_label, long: d.label })),
          focus: st.focus, slides: st.slides && slideCount() > 0, model: st.model, effort: Studia.effortPick ? Studia.effortPick("cp") : "",
          uploads: (preset && preset.uploads) || "" });        // Change…: the files already added stay with the new chat
      },
    };
    await load();
  }

  // ---- queue bar
  // A queue event while a button in the bar (or the pane) is pressed would swap it for a new one and the click would
  // be lost ("Hide" did nothing): the bar is drawn again once the button is let go.
  function whilePressed(box, redraw) {
    if (!box.querySelector(":active")) return false;
    if (!box.held) {
      box.held = true;
      const go = () => { if (!box.held) return; box.held = false; ["pointerup", "pointercancel", "keyup"].forEach((k) => document.removeEventListener(k, later)); redraw(); };
      const later = () => setTimeout(go, 0);                       // after the click
      ["pointerup", "pointercancel", "keyup"].forEach((k) => document.addEventListener(k, later));
    }
    return true;
  }
  function renderQueue() {
    const el = $("#queue"); if (!el) return;
    if (whilePressed(el, renderQueue)) return;
    requestAnimationFrame(() => document.body.style.setProperty("--queue-h", el.classList.contains("hidden") ? "0px" : `${el.offsetHeight}px`));
    renderPane();
    qpClock();
    const jobs = S.queue || [];
    const eng = S.engine && ["download", "load"].includes(S.engine.phase) ? S.engine : null;
    if (jobs.length < 2) S.qList = false;         // the list of lectures: shut with the last but one (the next batch starts small)
    if (!jobs.length && !eng) { el.classList.add("hidden"); el.classList.remove("open"); el.innerHTML = ""; return; }
    el.classList.remove("hidden");
    const again = el.contains(document.activeElement) ? focusKey(document.activeElement) : "";
    const cur = jobs.find((j) => j.stage === "transcribing") || jobs.find((j) => j.stage === "writing") || jobs.find((j) => j.stage === "downloading") || jobs[0];   // the one on the card, not one waiting for its slides
    S.queueShown = eng ? null : cur && cur.id;
    const label = (j) => ({ queued: t("Waiting"), downloading: t("Downloading"), transcribing: t("Transcribing"), writing: t("Finishing") }[j.stage] || j.stage);
    const open = !!S.qList;
    el.classList.toggle("open", open);
    const paused = !!S.paused;
    el.classList.toggle("paused", paused);
    let title = cur ? `${paused ? t("Paused") : label(cur)} · ${esc(cur.title)}` : t("Preparing");
    let detail = cur ? esc(jobDetail(cur)) : "";
    let p = cur ? cur.progress : 0;
    if (eng) { title = engTitle(eng); detail = eng.message && eng.message !== title ? esc(eng.message) : ""; p = eng.progress || 0; }
    const more = jobs.length > 1 ? `<button class="btn ghost sm" data-act="queue-toggle" aria-expanded="${open}">${open ? t("Hide") : t("+{n} more", { n: jobs.length - 1 })}</button>` : "";
    const b = S.batch || {};
    const batch = b.total > 1 ? `<span class="q-batch" title="${esc(t("Lectures finished in this batch"))}">${esc(qBatchText(b))}</span>` : "";
    el.innerHTML = `<div class="q-main"><div class="q-icon">${paused ? I("pause") : SPIN()}</div><div class="q-text"><div class="q-title">${title}</div><div class="q-detail">${paused ? t("Your PC may sleep until you continue.") : detail}</div></div>
      ${batch}<div class="bar ${p || paused ? "" : "indet"}"><i class="glide" style="transform:${scaleX(p)}"></i></div>${more}
      <button class="btn ghost icon sm" data-act="queue-expand" aria-controls="qpane" aria-expanded="${!!S.qpOpen}" title="${esc(t("Show each lecture's steps and details"))}">${I("chevron-up", "sm")}</button>
      ${cur && !eng ? `<button class="btn ${paused ? "primary" : "ghost"} sm" data-act="queue-pause" title="${esc(paused ? t("Go on transcribing") : t("Pause: the lecture being transcribed stops after the passages it is on. Nothing is lost."))}">${I(paused ? "play" : "pause", "sm")}<span class="lbl"> ${paused ? t("Continue") : t("Pause")}</span></button>` : ""}
      ${cur ? `<button class="btn ghost sm" data-act="cancel-all" title="${esc(t("Cancel everything in the queue"))}">${I("stop", "sm")}<span class="lbl"> ${t("Cancel")}</span></button>` : ""}</div>
      ${open ? `<div class="q-list">${jobs.map((j, i) => `<div class="q-item" data-id="${esc(j.id)}"><span class="n">${esc(j.title)}</span><span class="pill">${label(j)}</span>
        <div class="bar"><i class="glide" style="transform:${scaleX(j.progress)}"></i></div>
        ${j.stage === "queued" && !j.local && i > 1 ? `<button class="btn ghost sm" data-act="queue-first" data-id="${esc(j.id)}" title="${esc(t("Transcribe this one before the others still waiting"))}">${t("Do next")}</button>` : j.first ? `<span class="dim sm">${t("next")}</span>` : ""}
        <button class="btn ghost icon sm" data-act="cancel" data-id="${j.id}" title="${esc(t("Cancel this one"))}">${I("x", "sm")}</button></div>`).join("")}</div>` : ""}`;
    if (again) { const f = el.querySelector(again) || el.querySelector("[data-act='queue-expand']"); if (f) f.focus({ preventScroll: true }); }   // its button was drawn again
  }

  const qBatchText = (b) => t("{done} of {total} done", { done: b.finished, total: b.total }) + (batchLeft(b) ? " · " + t("about {time} left", { time: fmtDur(batchLeft(b)) }) : "");

  // A job's progress between stage changes: only the text and the bars change, the buttons stay where they are.
  function updateQueueBar(j) {
    const el = $("#queue"); if (!el) return;
    if (S.queueShown === j.id) {
      const d = el.querySelector(".q-main .q-detail"), bar = el.querySelector(".q-main > .bar");
      if (d && !S.paused) d.textContent = jobDetail(j);
      if (bar) { bar.classList.toggle("indet", !j.progress); bar.firstElementChild.style.transform = scaleX(j.progress); }
    }
    const item = el.querySelector(`.q-item[data-id="${CSS.escape(j.id)}"] .bar i`);
    if (item) item.style.transform = scaleX(j.progress);
  }

  // ---- the jobs pane: the queue bar grown into a panel over the page, each lecture with its steps. A queue event
  // (a lecture starts, ends or moves) draws its lists again; a job's progress changes only that lecture's lines.
  // The steps in the order they happen. "offline" / "signin": not downloaded yet, waiting for the internet connection
  // or a new sign-in; "place": downloaded, waiting for room to decode its audio; "turn": decoded, waiting for the
  // speech model (the graphics card, or the processor on a PC without a usable one).
  const QORDER = ["wait", "offline", "signin", "download", "place", "decode", "speech", "turn", "model", "transcribe", "slides", "write"];
  function qStepLabel(s, j) {
    if (s === "turn") {
      // the device setting, also as Python last reported it (gpu.used follows a change made elsewhere)
      const g = S.system.gpu, dev = j.device || (S.settings.device === "cpu" || (g && g.used === false) ? "cpu" : g == null ? "" : gpuUsed(g) ? "cuda" : "cpu");
      return dev === "cuda" ? t("Waiting for the graphics card") : dev === "cpu" ? t("Waiting for the processor") : t("Waiting to be transcribed");
    }
    return { wait: t("Waiting"), offline: t("Waiting for the internet connection"), signin: t("Waiting for you to sign in"), download: t("Downloading"), place: t("Waiting to prepare the audio"), decode: t("Decoding"), speech: t("Finding speech"),
      model: t("Loading the speech model"), transcribe: t("Transcribing"), slides: t("Slides"), write: t("Writing files") }[s] || s;
  }
  const stepOf = (j) => j.step || { downloading: "download", transcribing: "transcribe", writing: "write" }[j.stage] || "wait";
  const mmss = (s) => { s = Math.max(0, Math.floor(s)); const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = String(s % 60).padStart(2, "0"); return h ? `${h}:${String(m).padStart(2, "0")}:${x}` : `${m}:${x}`; };
  // Live times, from the snapshot and the page's clock (never a call to Python): an event's times are as old as the
  // event (_at, when it came; _etaAt, when the engine's time left last changed); a time left stands still while paused.
  const since = (at, still) => (at ? ((still && S.paused && S.pausedAt ? Math.max(at, S.pausedAt) : performance.now()) - at) / 1000 : 0);
  const ageNow = (j) => (j.age_s || 0) + since(j._at);
  const etaNow = (j) => (j.eta_s == null ? null : Math.max(0, j.eta_s - since(j._etaAt, true)));
  const batchLeft = (b) => (b && b.left_s ? Math.max(0, b.left_s - since(b._at, true)) : 0);
  function stamp(j, old) {       // a job's snapshot just came (old: what the page had of it)
    const now = performance.now();
    j._at = now; j._etaAt = old && old.eta_s === j.eta_s && old._etaAt ? old._etaAt : now;
  }
  function stepTimes(j, end) {   // seconds in each step (a step it came back to adds up), the steps in the order first seen
    const all = j.steps || [], spent = {}, seen = [];
    if (end == null) end = ageNow(j);
    all.forEach(([s, at], k) => { spent[s] = (spent[s] || 0) + Math.max(0, (k + 1 < all.length ? all[k + 1][1] : end) - at); if (!seen.includes(s)) seen.push(s); });
    return { spent, seen };
  }
  const mbText = (b) => t.num(b / 1e6, b < 1e7 ? 1 : 0, false);
  function jobWhat(j) {          // course, lecture and part, from the library's lists (a local file has only its name)
    const hit = findRec(j.id);
    if (!hit) return { course: j.course_code || "", name: "", lecture: j.course_code ? (j.title || "").replace(`${j.course_code} · `, "") : j.title, part: "" };
    const { c, d, r } = hit;
    return { course: c.code, name: c.display_name || "", lecture: [d.label, r.time && r.end_time ? `${r.time}–${r.end_time}` : r.time].filter(Boolean).join(" · "),
      part: r.part && d.parts > 1 ? t("Part {n}", { n: r.part }) : "" };
  }
  const jobName = (j) => { const w = jobWhat(j); return [w.course, w.lecture, w.part].filter(Boolean).join(" · ") || j.title; };
  // The steps it went through in the order it did them (with their times), the one it is on (its time live), then
  // those still to come. A wait it may skip (for a place, for the speech model, for the connection or a sign-in)
  // shows once it waits there.
  function qpStepsHTML(j, ended) {
    const now = ended ? "" : stepOf(j), { spent, seen } = stepTimes(j, ended ? j.age_s || 0 : null);
    const after = (s) => QORDER.indexOf(s) > QORDER.indexOf(now) && !(s in spent) && !["place", "turn", "offline", "signin"].includes(s)
      && (s !== "download" || !j.local) && (s !== "slides" || j.slides);
    const list = ended ? seen : [...seen.filter((s) => s !== now), now, ...QORDER.filter(after)];
    return list.map((s) => {
      const st = s === now ? "now" : s in spent ? "done" : "todo";
      const time = st === "now" ? ` <span class="t">${mmss(spent[s] || 0)}</span>` : spent[s] >= 1 ? ` <span class="t">${mmss(spent[s])}</span>` : "";
      return `<li class="qp-step ${st}"${st === "now" ? ' aria-current="step"' : ""}>${st === "done" ? I("check", "xs") : ""}${esc(qStepLabel(s, j))}${time}</li>`;
    }).join("");
  }
  function qpDetail(j) {
    const s = stepOf(j);
    if (s === "download" && j.dl) return [t("{done} of {total} MB", { done: mbText(j.dl[0]), total: mbText(j.dl[1]) }), j.dl[2] ? t("{speed} MB/s", { speed: t.num(j.dl[2] / 1e6, 1) }) : ""].filter(Boolean).join(" · ");
    if (s === "transcribe" && j.progress) return [t("{pct} done", { pct: pctText(j.progress) }), jobDetail(j)].filter(Boolean).join(" · ");
    return jobDetail(j);
  }
  const deviceText = (d) => (d === "cuda" ? t("on the graphics card") : d === "cpu" ? t("on the processor") : "");
  const qpAge = (j) => t("{time} so far", { time: mmss(ageNow(j)) });
  const qpMeta = (j) => [j.model ? t("Speech model: {model}", { model: j.model }) : "", deviceText(j.device)].filter(Boolean).join(" · ");
  const qpNotesHTML = (j) => (j.notes || []).map((n) => `<div class="qp-note">${I("info", "xs")}<span>${esc(n)}</span></div>`).join("")
    + (j.anomalies || []).map((a) => `<div class="qp-anom">${I("alert", "xs")}<span>${esc(a.text)}</span></div>`).join("");
  function qpJobHTML(j) {
    const jobs = S.queue || [], i = jobs.indexOf(j), s = stepOf(j), w = jobWhat(j);
    const ctl = `${j.stage === "queued" && !j.local && i > 1 ? `<button class="btn ghost sm" data-act="queue-first" data-id="${esc(j.id)}" title="${esc(t("Transcribe this one before the others still waiting"))}">${t("Do next")}</button>` : j.first ? `<span class="dim sm">${t("next")}</span>` : ""}
      <button class="btn ghost icon sm" data-act="cancel" data-id="${esc(j.id)}" title="${esc(t("Cancel this one"))}" aria-label="${esc(t("Cancel this one"))}">${I("x", "sm")}</button>`;
    const name = `<span class="qp-c">${esc(w.course)}</span>${w.name ? ` <span class="dim">· ${esc(w.name)}</span>` : ""}`;
    const lec = esc([w.lecture, w.part].filter(Boolean).join(" · "));
    if (s === "wait") return `<li class="qp-job wait" data-id="${esc(j.id)}" data-step="wait"><span class="qp-n">${name} <span class="dim">· ${lec}</span></span><span class="qp-wt">${esc(t("{time} waiting", { time: mmss(stepTimes(j).spent.wait || 0) }))}</span>${ctl}</li>`;
    const meta = qpMeta(j);
    return `<li class="qp-job" data-id="${esc(j.id)}" data-step="${s}">
      <div class="qp-jh"><div class="qp-n">${name}<div class="qp-lec">${lec}</div></div>${ctl}</div>
      <ol class="qp-steps">${qpStepsHTML(j)}</ol>
      <div class="qp-now"><div class="bar ${j.progress || s !== "download" && s !== "transcribe" ? "" : "indet"}"><i class="glide" style="transform:${scaleX(j.progress)}"></i></div><span class="qp-det">${esc(qpDetail(j))}</span></div>
      <div class="qp-meta"><span class="qp-age">${esc(qpAge(j))}</span><span class="qp-mm">${meta ? ` · ${esc(meta)}` : ""}</span></div><div class="qp-notes">${qpNotesHTML(j)}</div></li>`;
  }
  function qpUpdate(j) {         // one lecture's progress: its own lines only (a new step draws the lecture again)
    const card = $(`#qpane .qp-job[data-id="${CSS.escape(j.id)}"]`); if (!card) return;
    if (card.dataset.step !== stepOf(j)) {
      const again = card.contains(document.activeElement) ? focusKey(document.activeElement) : "";
      card.outerHTML = qpJobHTML(j);
      const f = again && $(`#qpane .qp-job[data-id="${CSS.escape(j.id)}"] ${again}`); if (f) f.focus({ preventScroll: true });
      return;
    }
    if (card.classList.contains("wait")) return;
    const bar = card.querySelector(".qp-now .bar");
    bar.classList.toggle("indet", !j.progress && ["download", "transcribe"].includes(stepOf(j))); bar.firstElementChild.style.transform = scaleX(j.progress);
    card.querySelector(".qp-det").textContent = qpDetail(j);
    card.querySelector(".qp-age").textContent = qpAge(j);
    const meta = qpMeta(j); card.querySelector(".qp-mm").textContent = meta ? ` · ${meta}` : "";
    const steps = qpStepsHTML(j), ol = card.querySelector(".qp-steps"); if (ol.innerHTML !== steps) ol.innerHTML = steps;
    const notes = qpNotesHTML(j), nb = card.querySelector(".qp-notes"); if (nb.innerHTML !== notes) nb.innerHTML = notes;
  }
  // The queue's clock: once a second while there is a queue (the bar or the pane) and the window is seen, only the
  // times that move change (each lecture's time so far and its step's time, the waiting rows, the times left);
  // nothing is drawn again.
  let qpTimer = 0;
  function qpClock() {
    const on = (!!S.qpOpen || !!(S.queue || []).length) && !document.hidden;
    if (on && !qpTimer) qpTimer = setInterval(qpTick, 1000);
    else if (!on && qpTimer) { clearInterval(qpTimer); qpTimer = 0; }
  }
  const setText = (el, s) => { if (el && el.textContent !== s) el.textContent = s; };
  function qpTick() {
    if (!(S.qpOpen || (S.queue || []).length) || document.hidden) { qpClock(); return; }
    const byId = new Map((S.queue || []).map((j) => [j.id, j])), p = S.qpOpen && $("#qpane");
    if (p) qpTickPane(p, byId);
    const q = $("#queue"), cur = S.queueShown && byId.get(S.queueShown);   // the bar (under the pane: right when it closes)
    if (q && cur && !S.paused) setText(q.querySelector(".q-main .q-detail"), jobDetail(cur));
    if (q && (S.batch || {}).total > 1) setText(q.querySelector(".q-batch"), qBatchText(S.batch));
  }
  function qpTickPane(p, byId) {
    p.querySelectorAll(".qp-jobs > .qp-job").forEach((card) => {
      const j = byId.get(card.dataset.id); if (!j) return;
      if (card.classList.contains("wait")) { setText(card.querySelector(".qp-wt"), t("{time} waiting", { time: mmss(stepTimes(j).spent.wait || 0) })); return; }
      setText(card.querySelector(".qp-step.now .t"), mmss(stepTimes(j).spent[stepOf(j)] || 0));
      setText(card.querySelector(".qp-age"), qpAge(j));
      if (j.eta_s != null) setText(card.querySelector(".qp-det"), qpDetail(j));
    });
    setText(p.querySelector(".qp-sumt"), qpSumText());
  }
  document.addEventListener("visibilitychange", () => { qpClock(); if (!document.hidden) qpTick(); });

  function qpFinished(e) {       // the lectures finished in this session, newest first, with what they took
    if (!["done", "failed", "processing", "cancelled"].includes(e.stage)) return;
    const keep = ["id", "stage", "error", "day", "local", "age_s", "steps", "audio_s", "passages", "transcribe_s", "model", "device", "notes", "anomalies", "slides"];
    const x = Object.fromEntries(keep.map((k) => [k, e[k]]));
    S.qDone = [{ ...x, name: jobName(e), kept: e.status === "done", ended: new Date() }, ...(S.qDone || []).filter((y) => y.id !== e.id)].slice(0, 60);
  }
  function qpSumText() {
    const b = S.batch || {}, jobs = S.queue || [], left = batchLeft(b);
    const parts = b.total > 1 ? [t("{n} done", { n: b.done != null ? b.done : b.finished }), b.failed ? t("{n} failed", { n: b.failed }) : "", t("{n} left", { n: b.total - b.finished })]
      : [jobs.length ? t.n(jobs.length, "{n} lecture in the queue", "{n} lectures in the queue") : t("Nothing is waiting.")];
    if (left) parts.push(t("ends around {time} ({left})", { time: t.date(new Date(Date.now() + left * 1000), "time"), left: t.left(left) }));
    return parts.filter(Boolean).join(" · ");
  }
  function qpSummary() {
    const eng = S.engine && ["download", "load"].includes(S.engine.phase) ? S.engine : null;
    return `<span class="qp-sumt">${esc(qpSumText())}</span>` + (S.paused ? ` <span class="pill">${t("Paused")}</span>` : "")
      + (eng ? `<div class="qp-eng">${SPIN()} ${engTitle(eng)}${eng.message && eng.message !== engTitle(eng) ? ` · ${esc(eng.message)}` : ""}</div>` : "");
  }
  function engTitle(eng) {       // the engine's message can repeat it (then it is left out)
    return eng.phase === "download" ? t("Downloading the speech model (one time)") : t("Loading the speech model");
  }
  const qpSpeed = (x) => (x.stage === "done" && x.audio_s > 0 && x.transcribe_s >= 1 ? x.audio_s / x.transcribe_s : 0);
  function qpFinLine(x) {        // the short line: what came of it, how long it took, how fast
    const total = mmss(x.age_s || 0), speed = qpSpeed(x);
    if (x.stage === "cancelled") return t("Cancelled after {time}", { time: total });
    if (x.stage !== "done") return t("{time} in all", { time: total });
    return [t("{time} in all", { time: total }), x.audio_s ? t("{audio} of audio", { audio: fmtDur(x.audio_s) }) : "",
      speed ? t("{x}× real time", { x: t.num(speed, 1) }) : ""].filter(Boolean).join(" · ");
  }
  function qpFinDetailHTML(x) {  // everything it took, step by step
    const row = (k, v) => (v ? `<dt>${k}</dt><dd>${v}</dd>` : "");
    const speed = qpSpeed(x), dev = deviceText(x.device);
    const result = x.stage === "done" ? t("Done") : x.stage === "cancelled" ? t("Cancelled")
      : `${x.stage === "processing" ? t("Not ready on Panopto yet") : t("Failed")}${x.error ? `: ${esc(x.error)}` : ""}${x.kept ? ` ${t("The earlier transcript is kept.")}` : ""}`;
    return `<ol class="qp-steps">${qpStepsHTML(x, true)}</ol><dl class="qp-dl">
      ${row(t("Result"), result)}
      ${row(t("Total time"), esc(mmss(x.age_s || 0)))}
      ${row(t("Audio length"), x.audio_s ? esc(fmtDur(x.audio_s)) : "")}
      ${row(t("Speed"), speed ? esc(t("{x}× real time", { x: t.num(speed, 1) }) + " · " + t("transcribing took {time}", { time: mmss(x.transcribe_s) })) : "")}
      ${row(t("Speech model"), x.model ? esc([x.model, dev].filter(Boolean).join(" · ")) : "")}
      ${row(t("Passages"), x.passages ? esc(t("{done} of {n}", { done: x.passages[0], n: x.passages[1] })) : "")}
      ${row(t("Finished"), esc(t.date(x.ended, "time")))}</dl>
      <div class="qp-notes">${qpNotesHTML(x)}</div>`;
  }
  function qpDoneHTML() {
    const list = S.qDone || [], open = S.qFinOpen || (S.qFinOpen = new Set());
    if (!list.length) return "";
    return `<h3>${t("Finished in this session")}</h3><ul class="qp-fins">${list.map((x, k) => {
      const icon = x.stage === "done" ? "check-circle" : x.stage === "cancelled" ? "x-circle" : "alert-circle";
      const why = x.stage === "done" ? (x.local ? "" : `<button class="btn ghost sm" data-act="qp-open" data-day="${esc(x.day)}">${I("file-text", "sm")} ${t("Open transcript")}</button>`)
        : x.stage === "cancelled" ? ""
        : `<span class="qp-why">${esc(x.error || t("failed"))}${x.kept ? ` ${t("The earlier transcript is kept.")}` : ""}</span>`;
      const on = open.has(x.id);
      return `<li class="qp-fin ${x.stage}${on ? " open" : ""}"><div class="qp-fh">${I(icon, "sm")}<span class="qp-n">${esc(x.name)}<span class="qp-fs">${esc(qpFinLine(x))}</span></span>${why}
        <button class="btn ghost icon sm qp-more" data-act="qp-fin" data-id="${esc(x.id)}" aria-expanded="${on}" aria-controls="qpf-${k}" title="${esc(t("Details"))}" aria-label="${esc(t("Details: {name}", { name: x.name }))}">${I("chevron-down", "sm")}</button></div>
        <div class="qp-fd" id="qpf-${k}"${on ? "" : " hidden"}>${on ? qpFinDetailHTML(x) : ""}</div></li>`;
    }).join("")}</ul>`;
  }
  function qpFinToggle(el) {     // a finished lecture's details: opened in place (the list isn't drawn again)
    const li = el.closest(".qp-fin"), box = li && li.querySelector(".qp-fd"), x = (S.qDone || []).find((y) => y.id === el.dataset.id);
    if (!box || !x) return;
    const on = !S.qFinOpen.has(x.id);
    if (on) { S.qFinOpen.add(x.id); box.innerHTML = qpFinDetailHTML(x); } else S.qFinOpen.delete(x.id);
    box.hidden = !on; li.classList.toggle("open", on); el.setAttribute("aria-expanded", String(on));
  }
  function renderPane() {
    const p = $("#qpane");
    if (!p) S.qpOpen = false;                     // the page was drawn again (e.g. another profile)
    if (!p || !S.qpOpen || whilePressed(p, renderPane)) return;
    const jobs = S.queue || [], paused = !!S.paused;
    const again = p.contains(document.activeElement) ? focusKey(document.activeElement) : "";
    // its buttons only when they change; the last one takes it back down to the bar (the bar's own button is under it)
    const ctl = `${jobs.length ? `<button class="btn ${paused ? "primary" : "ghost"} sm" data-act="queue-pause">${I(paused ? "play" : "pause", "sm")} ${paused ? t("Continue") : t("Pause")}</button>
      <button class="btn ghost sm" data-act="cancel-all">${I("stop", "sm")} ${t("Cancel")}</button>` : ""}
      <button class="btn ghost icon sm" data-act="qp-close" title="${esc(t("Minimize (Esc)"))}" aria-label="${esc(t("Minimize"))}" aria-controls="qpane" aria-expanded="true">${I("chevron-down", "sm")}</button>`;
    if (p.ctl !== ctl) { p.querySelector(".qp-ctl").innerHTML = ctl; p.ctl = ctl; }
    p.querySelector(".qp-sum").innerHTML = qpSummary();
    if (p.drawn !== S.queue || p.drawnDone !== S.qDone) {   // the lists only when they changed (not for the model's progress)
      p.querySelector(".qp-jobs").innerHTML = jobs.map(qpJobHTML).join("");
      p.querySelector(".qp-done").innerHTML = qpDoneHTML();
      p.drawn = S.queue; p.drawnDone = S.qDone;
    }
    const f = again && p.querySelector(again);
    if (again) (f || p.querySelector("[data-act='qp-close']")).focus({ preventScroll: true });   // its control may be gone
  }
  function openPane() {
    let p = $("#qpane");
    if (!p) {
      p = document.createElement("section");
      p.id = "qpane"; p.className = "qpane"; p.setAttribute("aria-label", t("Transcription queue")); p.tabIndex = -1;
      p.innerHTML = `<div class="qp-head"><h2>${I("list-checks")} ${t("Transcription queue")}</h2><div class="qp-ctl"></div></div>
        <div class="qp-body"><div class="qp-sum"></div><ol class="qp-jobs"></ol><div class="qp-done"></div></div>`;
      $(".main").appendChild(p);
      void p.offsetWidth;                         // drawn closed first, so opening it animates
    }
    S.qpOpen = true; S.qpFrom = document.activeElement;
    p.drawn = null;                               // its lectures moved on while it was closed
    renderPane();
    p.classList.add("open");
    qpClock();
    ["#top", "#view", "#queue"].forEach((s) => { const x = $(s); if (x) x.inert = true; });   // Tab stays in the pane (the sidebar and toasts stay live: going elsewhere closes it)
    p.querySelector("[data-act='qp-close']").focus({ preventScroll: true });
  }
  function closePane() {
    const p = $("#qpane");
    S.qpOpen = false;
    ["#top", "#view", "#queue"].forEach((s) => { const x = $(s); if (x) x.inert = false; });
    if (p) p.classList.remove("open");
    qpClock();
    renderQueue();
    const back = $("#queue:not(.hidden) [data-act='queue-expand']") || (S.qpFrom && S.qpFrom.isConnected ? S.qpFrom : null);
    if (back) back.focus({ preventScroll: true });
  }

  // ---- sign-in
  function signinHTML() {
    const s = S.signin;
    const others = (S.profiles || []).filter((x) => !x.active && x.has_login);
    return `<div class="signin"><form class="card signin-card" data-form="signin" autocomplete="on">
      <div class="brand"><img class="logo-img" src="../assets/logo.svg" alt="">IEUStudia</div>
      <p class="lead">${t("Sign in with your IEULearn (EkoID) account. IEUStudia finds your courses and their Panopto recordings.")}</p>
      <div class="field"><label for="si-user">${t("EkoID / username")}</label><input id="si-user" class="input" name="username" autocomplete="username" value="${esc((S.profile || {}).username || "")}"></div>
      <div class="field"><label for="si-pass">${t("Password")}</label><div class="input-wrap"><input id="si-pass" class="input" type="password" name="password" autocomplete="current-password">
        <button type="button" class="btn ghost icon sm" data-act="peek" title="${esc(t("Show password"))}">${I("eye", "sm")}</button></div></div>
      ${s.error ? `<div class="err">${I("alert-circle", "sm")}<span>${esc(s.error)}</span></div>` : ""}
      <button class="btn primary block" type="submit" ${s.busy ? "disabled" : ""}>${s.busy ? SPIN() + " " + t("Signing in…") : t("Sign in")}</button>
      <div class="note">${I("lock", "sm")}<span>${window.LS_LINUX ? t("Your password is sent only to learn.ieu.edu.tr. On this PC it is stored encrypted with a key in your desktop's keyring, so only your user account can read it. It never goes to any other server: IEUStudia has none.") : t("Your password is sent only to learn.ieu.edu.tr. On this PC it is stored encrypted with Windows' own protection, so only your Windows account can read it. It never goes to any other server: IEUStudia has none.")}</span></div>
      <div class="note">${I("info", "sm")}<span>${t("IEUStudia is an independent student project, not affiliated with İzmir University of Economics, IEULearn or Panopto. Use it at your own risk.")}</span></div>
      ${others.length ? `<div class="si-others"><div class="lbl">${t("Or open another profile on this PC")}</div>${others.map((x) => `<button type="button" class="si-prof" data-act="profile-switch" data-id="${esc(x.id)}">
        <span class="avatar">${esc(initials(x.name || x.username))}</span><span class="who"><b>${esc(x.name || x.username)}</b><span>${esc(x.student_number || x.username)}</span></span>${I("chevron-right", "sm")}</button>`).join("")}</div>` : ""}
    </form></div>`;
  }

  // ---- profiles: several IEULearn accounts on this PC (switching restarts the app with that account's data)
  function addAccountDialog(err = "", busy = false, user = "") {
    modal(`<form data-form="add-account" autocomplete="off">
      <h2>${t("Add another account")}</h2>
      <p class="lead">${t("Sign in with another IEULearn (EkoID) account. It gets a profile of its own — its own courses, transcripts, notes and settings — and you can switch between profiles from the menu at the bottom left.")}</p>
      <div class="field"><label for="aa-user">${t("EkoID / username")}</label><input id="aa-user" class="input" name="username" autocomplete="off" value="${esc(user)}"></div>
      <div class="field"><label for="aa-pass">${t("Password")}</label><div class="input-wrap"><input id="aa-pass" class="input" type="password" name="password" autocomplete="new-password">
        <button type="button" class="btn ghost icon sm" data-act="peek" data-for="aa-pass" title="${esc(t("Show password"))}">${I("eye", "sm")}</button></div></div>
      ${err ? `<div class="err">${I("alert-circle", "sm")}<span>${esc(err)}</span></div>` : ""}
      <div class="note">${I("lock", "sm")}<span>${window.LS_LINUX ? t("The password is only sent to learn.ieu.edu.tr. On this PC it is stored encrypted for each profile separately, so only your user account can read it.") : t("The password is only sent to learn.ieu.edu.tr. On this PC it is stored encrypted for each profile separately, so only this Windows account can read it.")}</span></div>
      <div class="actions"><button type="button" class="btn" data-act="modal-close">${t("Cancel")}</button>
        <button class="btn primary" type="submit" ${busy ? "disabled" : ""}>${busy ? SPIN() + " " + t("Signing in…") : t("Sign in and switch")}</button></div></form>`);
    setTimeout(() => { const f = $(user && !busy ? "#aa-pass" : "#aa-user"); if (f && !busy) f.focus(); }, 30);
  }
  async function switchProfile(x, force = false) {
    if (!x || x.active) return;
    const r = await call("profile_switch", x.id, force);
    if (!r.ok) {
      if (r.busy) { if (confirm(t("A transcription is still running. Cancel it and switch profiles?"))) switchProfile(x, true); return; }
      toast(r.error || t("Could not switch profiles"), "error");
      return;
    }
    if (r.same) return;
    closeMenu(); closeModal();
    $("#overlay").innerHTML = `<div class="switching" role="status"><img class="logo-img" src="../assets/logo.svg" alt="">
      <div class="t">${x.name || x.username ? t.html("Opening {name}…", { name: x.name || x.username }) : t("Opening the profile…")}</div><div class="d">${t("IEUStudia restarts with this account's courses and transcripts.")}</div>
      <div class="splash-bar"><i></i></div></div>`;
    if (!REAL) setTimeout(() => { location.hash = "profile=" + x.id; location.reload(); }, 900);   // browser preview
  }

  // ------------------------------------------------------------------ overlays
  function toast(msg, level = "info", action) {   // action: {label, act, data}: a button (the toast then stays longer)
    const icon = { success: "check-circle", error: "x-circle", warning: "alert", info: "info" }[level] || "info";
    const box = document.createElement("div");
    const attrs = action ? Object.entries(action.data || {}).map(([k, v]) => `data-${k}="${esc(v)}"`).join(" ") : "";
    box.className = `toast ${level}`;
    box.innerHTML = `${I(icon)}<div>${esc(msg)}</div>${action ? `<button class="btn sm" data-act="${esc(action.act)}" ${attrs}>${esc(action.label)}</button>` : ""}`;
    if (action) box.querySelector("button").addEventListener("click", () => box.remove());
    $("#toasts").appendChild(box);
    setTimeout(() => { box.style.transition = "opacity .3s"; box.style.opacity = "0"; setTimeout(() => box.remove(), 320); }, action ? 12000 : level === "error" ? 7000 : 4200);
  }
  let modalFrom = null;            // what had the focus before a dialog opened: it gets it back
  const focusKey = (el) => el && el.dataset && el.dataset.act      // the same control after the dialog is drawn again
    ? Object.entries(el.dataset).map(([k, v]) => `[data-${k.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase())}="${CSS.escape(v)}"]`).join("") : "";
  function modal(html) {
    const old = $("#overlay .modal"), inside = !!old && old.contains(document.activeElement);
    const again = inside ? focusKey(document.activeElement) : "";
    if (!old) modalFrom = document.activeElement;
    $("#overlay").innerHTML = `<div class="backdrop" data-act="modal-bg"><div class="modal" role="dialog" aria-modal="true" tabindex="-1">${html}</div></div>`;
    const m = $("#overlay .modal"), same = again ? m.querySelectorAll(again) : [];
    const el = m.querySelector("[autofocus]") || (same.length === 1 ? same[0] : null);
    if (el) el.focus({ preventScroll: true });
    else if (!old || inside) m.focus({ preventScroll: true });     // Tab goes on inside the dialog, not in the page behind
  }
  function closeModal() {
    const had = $("#overlay").innerHTML;
    $("#overlay").innerHTML = "";
    if (had && modalFrom && modalFrom.isConnected) modalFrom.focus({ preventScroll: true });
    modalFrom = null;
    const hook = Studia.onModalClose; Studia.onModalClose = null;   // a dialog waiting for an answer: closed = "no"
    if (hook) hook();
  }

  // ---- a new version (the top bar's button, Settings → About, or the tray helper's notification)
  function onUpdate(e) {
    const was = (S.update || {}).state;
    S.update = { ...S.update, ...e };
    if (e.state === "downloading" && was === "downloading") { updateProgress(); return; }   // only the bar moves
    if ($("#update-modal") || e.state === "installing" || (e.state === "downloading" && was !== "downloading")) updateModal();
    else if (e.state === "error") toast(e.error, "error");
    renderTop();
    if (S.view.name === "settings") renderView();
  }
  const updatePc = (u) => (u.total ? Math.round((u.done / u.total) * 100) : 0);
  const updateText = (u, pc) => (u.total ? t("Downloading the new version · {pct} of {mb} MB", { pct: t.pct(pc / 100), mb: t.num(Math.round(u.total / 1e6)) }) : t("Downloading the new version…"));
  // the download goes on: its bar and line change in place (drawing the dialog again replays its opening animation)
  function updateProgress() {
    const m = $("#update-modal"), bar = m && m.querySelector(".up-bar"), txt = m && m.querySelector(".up-txt");
    if (!bar || !txt) return;
    const u = S.update, pc = updatePc(u);
    bar.classList.toggle("indet", !u.total); bar.firstElementChild.style.width = `${pc}%`;
    txt.textContent = updateText(u, pc);
  }
  function updateModal() {
    const u = S.update || {};
    const pc = updatePc(u);
    const body = u.state === "installing"
      ? `<div class="callout info">${I("info")}<div>${t("Installing. IEUStudia closes now and opens again by itself in about a minute.")}</div></div>`
      : u.state === "downloading"
        ? `<div class="bar up-bar ${u.total ? "" : "indet"}"><i style="width:${pc}%"></i></div>
           <div class="dim sm up-txt" style="margin-top:6px">${updateText(u, pc)}</div>`
        : `${u.state === "error" ? `<div class="callout error">${I("alert-circle")}<div>${esc(u.error)}</div></div>` : ""}
           ${u.notes ? `<div class="update-notes md">${mdToHtml(u.notes)}</div>` : ""}`;
    const busy = u.state === "downloading" || u.state === "installing";
    modal(`<div id="update-modal"><h2>${t.html("IEUStudia {version} is available", { version: u.version })}</h2>
      <p class="lead">${t.html("You have {version}. Updating keeps your courses, transcripts and settings, and takes about a minute.", { version: u.current || (S.boot || {}).version || "" })}</p>
      ${body}
      <div class="actions">${busy ? (u.state === "downloading" ? `<button class="btn" data-act="update-cancel">${t("Cancel")}</button>` : "")
        : `<button class="btn ghost" data-act="update-page">${I("external", "sm")} ${t("Release page")}</button><span class="spacer"></span>
           <button class="btn" data-act="modal-close">${t("Later")}</button>
           ${u.can_install ? `<button class="btn primary" data-act="update-go">${I("download", "sm")} ${u.state === "error" ? t("Try again") : t("Update now")}</button>` : ""}`}</div></div>`);
  }
  function menu(anchor, items) {
    closeMenu();
    const r = anchor.getBoundingClientRect();
    const m = document.createElement("div");
    m.className = "menu"; m.id = "menu";
    m.innerHTML = items.map((it) => it === "-" ? "<hr>" : `<button data-menu="${it.id}">${it.icon ? I(it.icon) : ""}<span>${esc(it.label)}${it.desc ? `<span class="d">${esc(it.desc)}</span>` : ""}</span></button>`).join("");
    document.body.appendChild(m);
    const w = m.offsetWidth, hgt = m.offsetHeight;
    m.style.left = Math.min(window.innerWidth - w - 8, r.left) + "px";
    m.style.top = (r.bottom + hgt + 8 > window.innerHeight ? r.top - hgt - 6 : r.bottom + 6) + "px";
    m.onclick = (e) => { const b = e.target.closest("[data-menu]"); if (b) { closeMenu(); const it = items.find((x) => x.id === b.dataset.menu); it && it.run(); } };
  }
  function closeMenu() { const m = $("#menu"); if (m) m.remove(); }

  const NOTE_KINDS = () => [["notes", t("Study notes")], ["exam", t("Exam focus")], ["flashcards", t("Flashcards")]];
  const levelLabel = (v) => { const row = modelsFor().find(([k]) => k === v); return row ? row[1] : v; };   // "Sonnet", "Standard"
  async function claudeDialog() {
    const d = S.day; if (!d) return;
    const ai = S.system.ai || {}, local = ai.provider === "local";
    const images = ai.can_images !== false && !local;          // the offline model reads text only
    let tpl = "notes", slides = false;
    const draw = async () => {
      const est = await call("ai_estimate", d.day_key, slides && images);
      const e = est.ok ? est.estimate : { input: 0, output: 0, images: 0 };
      const have = (d.notes || []).find((n) => n.kind === tpl);
      const how = local ? `<div><b>${t("Offline model")}</b><span>${t("runs on this PC")}</span></div>`
        : isChatGPT(ai) ? "" : `<div><b>${esc(levelLabel(S.settings.ai_model || "sonnet"))}</b><span>${t("model (Settings → AI)")}</span></div>`;
      modal(`<h2>${I("ai")} ${t.html("Study notes with {ai}", { ai: aiName() })}</h2>
        <p class="lead">${t.html("Uses {plan}. Nothing runs until you press Generate. AI can make mistakes — check the notes against the lecture.", { plan: aiPlan() })}</p>
        <div class="field"><label>${t("What should it write?")}</label><div class="seg">
          ${NOTE_KINDS().map(([k, l]) => `<button class="${tpl === k ? "on" : ""}" data-act="cl-tpl" data-val="${k}">${l}</button>`).join("")}</div></div>
        ${Studia.effortField ? `<div class="cp-opts">${Studia.effortField("notes", "chat", "", { big: local ? "" : Studia.effortBig.notes(e.input) })}</div>` : ""}
        ${images ? `<div class="set-row" style="border:0;padding:6px 0"><div class="txt"><div class="t">${t("Include slide images")}</div><div class="d">${t("Lets the AI read the slides and board (more accurate formulas, uses more of your plan).")}</div></div>
          <input type="checkbox" class="switch" data-act="cl-slides" ${slides ? "checked" : ""} ${d.parts.some((p) => p.slides) ? "" : "disabled"}></div>` : ""}
        <div class="est">${local ? "" : `<div><b>~${t("{n}k", { n: dec(e.input / 1000, 1) })}</b><span>${t("tokens it reads")}</span></div><div><b>~${t("{n}k", { n: dec(e.output / 1000, 1) })}</b><span>${t("tokens it writes")}</span></div>`}${how}</div>
        <div class="callout info">${I("info")}<div>${have ? { notes: t("This lecture already has study notes. Generating them again replaces the saved ones."),
              exam: t("This lecture already has exam-focus notes. Generating them again replaces the saved ones."),
              flashcards: t("This lecture already has flashcards. Generating them again replaces the saved ones.") }[tpl]
          : local ? t("The offline model works on this PC: a lecture takes a few minutes with an NVIDIA graphics card, longer on the processor. The notes are saved next to the transcript.")
          : slides && images ? t("Kept small to save your plan: a short instruction and a compact text version of the transcript, plus {n} smaller copies of the slides. Tokens are the small pieces of text AI plans count, a word or part of a word each. The notes are saved next to the transcript.", { n: e.images })
          : t("Kept small to save your plan: a short instruction and a compact text version of the transcript. Tokens are the small pieces of text AI plans count, a word or part of a word each. The notes are saved next to the transcript.")}</div></div>
        <div class="actions"><button class="btn" data-act="modal-close">${t("Cancel")}</button>${have ? `<button class="btn" data-act="cl-open">${I("file-text", "sm")} ${t("Show them")}</button>` : ""}
          <button class="btn grad" data-act="cl-go">${I("ai", "sm")} ${have ? t("Generate again") : t("Generate")}</button></div>`);
    };
    window.__cl = { set tpl(v) { tpl = v; draw(); }, set slides(v) { slides = v; draw(); },
      open: () => { closeModal(); S.notesKind = tpl; renderNotes(); const el = $("#notes-slot"); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); },
      go: () => { closeModal(); call("ai_notes", d.day_key, tpl, slides && images, Studia.effortPick ? Studia.effortPick("notes") : "").then((r) => { if (!r.ok) toast(r.error, "error"); }); } };
    await draw();
  }

  function practise() {   // the lecture's flashcards one at a time; the ones you didn't know come back at the end
    const n = S.day && (S.day.notes || []).find((x) => x.kind === "flashcards");
    if (!n || !(n.cards || []).length) return;
    const st = { order: n.cards.map((_, i) => i), pos: 0, shown: false, again: new Set() };
    const draw = () => {
      if (st.pos >= st.order.length) {
        const missed = st.again.size;
        modal(`<div class="fc"><h2>${I("check-circle")} ${t.n(n.cards.length, "All {n} card done", "All {n} cards done")}</h2>
          <p class="lead">${missed ? t.n(missed, "{n} card needed another look. Going through them once more tomorrow helps them stick.", "{n} cards needed another look. Going through them once more tomorrow helps them stick.") : t("You knew every one of them.")}</p>
          <div class="actions"><button class="btn" data-act="modal-close">${t("Close")}</button><button class="btn primary" data-act="fc-restart" autofocus>${I("retry", "sm")} ${t("Start again")}</button></div></div>`);
        return;
      }
      const c = n.cards[st.order[st.pos]];
      modal(`<div class="fc"><div class="fc-top"><b>${I("list-checks", "sm")} ${esc(S.day.title)} · ${t("flashcards")}</b><span class="dim sm">${t("{n} of {total}", { n: st.pos + 1, total: st.order.length })}</span></div>
        <div class="fc-bar"><i style="width:${Math.round((100 * st.pos) / st.order.length)}%"></i></div>
        <div class="fc-q md">${mdToHtml(c.q, { cites: true })}</div>
        ${st.shown ? `<div class="fc-a md">${mdToHtml(c.a, { cites: true })}</div>` : `<div class="fc-hint dim sm">${t("Think of the answer first.")}</div>`}
        <div class="actions">${st.shown ? `<button class="btn" data-act="fc-again">${I("retry", "sm")} ${t("Again later")}</button><button class="btn primary" data-act="fc-knew" autofocus>${I("check", "sm")} ${t("I knew it")}</button>`
          : `<button class="btn ghost" data-act="modal-close">${t.c("flashcards", "Stop")}</button><button class="btn primary" data-act="fc-show" autofocus>${t("Show the answer")}</button>`}</div></div>`);
    };
    window.__fc = {
      show: () => { st.shown = true; draw(); },
      knew: () => { st.pos++; st.shown = false; draw(); },
      again: () => { st.again.add(st.order[st.pos]); st.order.push(st.order[st.pos]); st.pos++; st.shown = false; draw(); },
      restart: () => { st.order = n.cards.map((_, i) => i); st.pos = 0; st.shown = false; st.again = new Set(); draw(); },
    };
    draw();
  }

  window.LSRender = { md: mdToHtml, tex: texToHtml };   // renderer hooks (used by the UI tests)

  // ------------------------------------------------------------------ actions
  const A = {
    go: (el) => { S.navigated = true; go(el.dataset.view === "course" ? { name: "course", courseId: el.dataset.id, tab: el.dataset.tab || undefined } : { name: el.dataset.view }); },
    back: () => {               // where you came from, scrolled where you were
      const f = S.view.from || { name: "home" };
      go({ ...f, jumpWeek: false });
      if (f.scroll) $("#view").scrollTo({ top: f.scroll, behavior: "instant" });
    },
    sync: () => { S.sync = { running: true, message: "", error: "" }; renderTop(); if (S.view.name === "home") renderView(); call("sync"); },
    sel: (el) => { el.checked ? S.selected.add(el.dataset.id) : S.selected.delete(el.dataset.id); updateSelectionUI(); },
    "sel-day": (el) => { const card = el.closest(".day"); $$(".rec .check:not(:disabled)", card).forEach((cb) => { cb.checked = el.checked; el.checked ? S.selected.add(cb.dataset.id) : S.selected.delete(cb.dataset.id); }); updateSelectionUI(); },
    "select-new": (el) => {
      const cs = el.dataset.id ? S.courses.filter((c) => c.id === el.dataset.id) : S.courses;
      cs.forEach((c) => c.days.forEach((d) => {
        if (c.show_mode === "mine" && d.mine === false) return;   // other sections: never auto-selected
        d.recordings.forEach((r) => { if (r.is_new && selectable(r) && r.status !== "done") S.selected.add(r.id); });
      }));
      syncChecks();
    },
    "tx-selected": async () => { const ids = [...S.selected]; if (!ids.length) return; S.selected.clear(); syncChecks(); const r = await call("transcribe", ids); if (!r.ok) toast(r.error, "error"); else toast(t.n(r.queued, "{n} recording added to the queue", "{n} recordings added to the queue"), "info"); },
    "tx-day": async (el) => { const r = await call("transcribe_day", el.dataset.day); if (!r.ok) toast(r.error, "error"); else if (!r.queued) toast(t("Nothing left to transcribe for this day."), "info"); },
    "tx-new": async () => {     // Home: every new recording of your sections
      const ids = newRecs().flatMap(newToDo);
      if (!ids.length) return;
      const r = await call("transcribe", ids);
      if (!r.ok) toast(r.error, "error"); else toast(t.n(r.queued, "{n} recording added to the queue", "{n} recordings added to the queue"), "info");
    },
    retry: (el) => call("transcribe", [el.dataset.id]),
    cancel: (el) => call("cancel", el.dataset.id),
    "cancel-all": () => {
      const n = new Set((S.queue || []).map((j) => j.day || j.id)).size;          // lectures, not their parts
      if (n > 1 && !confirm(t("Cancel all {n} lectures in the queue? To stop for a while instead, use Pause.", { n }))) return;
      call("cancel", null);
    },
    "queue-pause": async () => { const r = await call("queue_pause", !S.paused); if (r.ok) { S.paused = r.paused; renderQueue(); } },
    "queue-first": async (el) => { const r = await call("queue_first", el.dataset.id); if (r.ok) toast(t("Moved to the front of the queue."), "info"); },
    "queue-toggle": () => { S.qList = !S.qList; renderQueue(); },
    "queue-expand": () => (S.qpOpen ? closePane() : openPane()),
    "qp-close": () => closePane(),
    "qp-open": (el) => { closePane(); openDay(el.dataset.day); },
    "qp-fin": (el) => qpFinToggle(el),
    "bg-settings": () => {
      go({ name: "settings" });
      const c = $("#set-bg"), nav = $(".set-nav");
      if (!c) return;
      if (nav) c.style.scrollMarginTop = `${nav.offsetHeight + 8}px`;     // the tabs wrap onto two rows when narrow
      c.scrollIntoView({ block: "start" });
    },
    "open-day": (el) => openDay(el.dataset.day),
    "open-hit": async (el) => { await openDay(el.dataset.day); setTimeout(() => jumpTo(null, +el.dataset.t, el.dataset.id), 60); },
    "open-mat-hit": (el) => call("material_open", el.dataset.id).then((r) => { if (!r.ok) toast(r.error, "error"); }),
    url: (el) => call("open_url", el.dataset.url),
    "update-open": () => updateModal(),
    "update-page": () => call("open_url", (S.update || {}).page || "https://github.com/mirzaisi/IEUStudia/releases/latest"),
    "update-go": async () => {
      S.update = { ...S.update, state: "downloading", done: 0, total: 0, error: "" }; updateModal();
      const r = await call("update_install");
      if (!r.ok) { S.update = { ...S.update, state: "error", error: r.error }; updateModal(); }
    },
    "update-cancel": () => call("update_cancel"),
    "update-check": async (el) => {
      el.disabled = true;
      const r = await call("update_check");
      if (r.ok) {
        S.update = { ...S.update, ...r, state: "" };
        if (r.error) toast(t("Couldn't look for a new version: {error}", { error: r.error }), "error");
        else if (!r.available) toast(t("You have the newest version."), "success");
      }
      else toast(r.error, "error");
      renderTop(); if (S.view.name === "settings") renderView();
    },
    ts: (el) => call("open_url", `${el.dataset.url}&start=${Math.floor(+el.dataset.t)}`),
    "copy-moment": async (el) => {     // for a study group: each classmate opens it on Panopto with their own login
      const d = S.day; if (!d) return;
      const p = d.parts.find((x) => x.index === +el.dataset.part) || {};
      const sec = Math.floor(+el.dataset.t), ts = `${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;
      const iso = d.day_key.split("|")[1] || "";
      const r = await call("copy_text", `${d.course.code} · ${/^\d{4}-\d{2}-\d{2}$/.test(iso) ? shortDate(iso) : d.date_label}${d.parts.length > 1 ? ` · ${t("Part {n}", { n: p.index })}` : ""}, ${ts}: ${p.viewer_url}&start=${sec}`);
      toast(r.ok ? t("Link copied. Classmates open it on Panopto with their own IEU login.") : r.error || t("Copy failed"), r.ok ? "success" : "error");
    },
    jump: (el) => jumpTo(+el.dataset.part, +el.dataset.t),
    zoom: (el) => { $("#overlay").innerHTML = `<div class="lightbox" data-act="modal-close"><img src="${esc(el.dataset.src)}"></div>`; },
    "show-mode": async (el) => { const r = await call("set_course_prefs", el.dataset.id, { show_mode: el.dataset.mode }); if (r.ok) { setCourses(r.courses); renderView(); } },
    "mark-seen": async (el) => { const r = await call("mark_seen", el.dataset.id || null); if (r.ok) { setCourses(r.courses); renderSidebar(); renderView(); } },
    "toggle-others": (el) => { S.expanded.has(el.dataset.id) ? S.expanded.delete(el.dataset.id) : S.expanded.add(el.dataset.id); renderView(); },
    "open-folder": (el) => call("open_course_folder", el.dataset.id),
    "open-output": async () => { const r = await call("open_output"); if (!r.ok) toast(r.error, "error"); },
    file: (el) => call("open_file", S.day.day_key, el.dataset.what),
    export: () => call("export_pack", S.day.day_key),
    claude: () => claudeDialog(),
    "ai-cancel": () => call("ai_cancel"),
    "cl-tpl": (el) => { window.__cl.tpl = el.dataset.val; },
    "cl-slides": (el) => { window.__cl.slides = el.checked; },
    "cl-go": () => window.__cl.go(),
    "cl-open": () => window.__cl.open(),
    "notes-kind": (el) => { S.notesKind = el.dataset.val; renderNotes(); },
    "cards-practise": () => practise(),
    mark: async (el) => {
      const on = !el.classList.contains("on"), first = !((S.day || {}).marks || []).length;
      const saved = await setMark(+el.dataset.part, +el.dataset.t, on);      // "Marked." only once it is saved
      if (saved && on && first) toast(t("Marked. Your marks are listed at the top of this page."), "success");
    },
    "mark-note": (el) => {
      const m = ((S.day || {}).marks || []).find((x) => x.id === el.dataset.id && Math.abs(x.t - +el.dataset.t) < 0.05);
      if (!m) return;
      const note = prompt(t("A note for this passage (for yourself)"), m.note || "");
      if (note != null) setMark(m.part, m.t, true, note.trim());
    },
    "mark-del": (el) => {
      const m = ((S.day || {}).marks || []).find((x) => x.id === el.dataset.id && Math.abs(x.t - +el.dataset.t) < 0.05);
      if (m) setMark(m.part, m.t, false);
    },
    "marks-ask": () => {      // a chat about this lecture that starts with the passages you marked
      const d = S.day; if (!d || !(d.marks || []).length) return;
      const quote = (m) => `"${m.text.slice(0, 140)}${m.text.length > 140 ? "…" : ""}"`;
      const lines = d.marks.map((m) => "- " + (m.note ? t("Part {part}, {time}: {quote} (my note: {note})", { part: m.part, time: m.ts, quote: quote(m), note: m.note })
        : t("Part {part}, {time}: {quote}", { part: m.part, time: m.ts, quote: quote(m) })));
      A["ask-day"]();
      S.chat.input = t("I marked these passages of the lecture. Explain each one clearly, with what I need to know about it:") + "\n" + lines.join("\n");
      const ta = $("#chat-input"); if (ta) { ta.value = S.chat.input; autosize(ta); }
    },
    "fc-show": () => window.__fc.show(), "fc-knew": () => window.__fc.knew(), "fc-again": () => window.__fc.again(),
    "fc-restart": () => window.__fc.restart(),
    "ask-day": () => {
      const d = S.day; if (!d) return;
      const iso = d.day_key.split("|")[1] || "";
      startDraft({ course_id: d.course.id, course_code: d.course.code, days: [d.day_key], focus: "full", slides: false,
        labels: [{ key: d.day_key, n: 1, label: /^\d{4}-\d{2}-\d{2}$/.test(iso) ? shortDate(iso) : d.date_label, kind: d.kind === "lab" ? t("Lab") : t("Lecture"), long: d.date_label }],
        model: S.settings.ai_model || "sonnet" });
    },
    "ask-course": (el) => chatPicker(el.dataset.id),
    "chat-new": () => chatPicker((S.chat.data && S.chat.data.course_id) || (S.chat.draft && S.chat.draft.course_id) || null),
    "chat-open": (el) => openChat(el.dataset.id),
    "chat-suggest": (el) => sendChat(el.dataset.text),
    "chat-stop": () => { if (S.chat.data) call("chat_stop", S.chat.data.id); },
    "chat-day": (el) => { if (el.classList.contains("gone")) toast(t("That class day is no longer in your library."), "warning"); else openDay(el.dataset.key); },
    "chat-copy": async (el) => {
      const m = S.chat.data && S.chat.data.messages.find((x) => x.seq === +el.dataset.seq); if (!m) return;
      const r = await call("copy_text", m.text);
      toast(r.ok ? t("Answer copied (as Markdown).") : r.error || t("Copy failed"), r.ok ? "success" : "error");
    },
    "chat-retry": async (el) => {      // replaces the failed answer instead of asking the question twice
      const c = S.chat.data; if (!c || c.running) return;
      const r = await call("chat_retry", c.id, +el.dataset.seq);
      if (!r.ok) { toast(r.error, "error"); return; }
      if (S.chat.data !== c) return;
      const streamed = c.messages.find((m) => m.seq === r.seq && m.status === "streaming");   // an event may have come first
      c.messages = c.messages.filter((m) => m.seq < r.removed_from);
      c.messages.push({ seq: r.user_seq, role: "user", text: r.question, status: "done", usage: {} },
        streamed || { seq: r.seq, role: "assistant", text: "", status: "streaming", usage: {} });
      c.running = true;
      if (S.view.name === "chat") { renderChatMsgs(); renderComposerBtn(); }
    },
    "chat-rename": async () => {
      const c = S.chat.data; if (!c) return;
      const name = prompt(t("Name this chat"), c.title); if (name == null) return;
      const r = await call("chat_update", c.id, { title: name });
      if (!r.ok) { toast(r.error, "error"); return; }
      c.title = r.chat.title; renderChatHead(); loadChats();
    },
    "chat-model": async (el) => {
      const c = S.chat.data; if (!c) return;
      const r = await call("chat_update", c.id, { model: el.value });
      if (!r.ok) { toast(r.error, "error"); return; }
      c.model = r.chat.model; loadChats();
      if (c.messages.length) toast(t("Next answers use {model}. {ai} reads the lectures again once.", { model: levelLabel(el.value), ai: aiName() }), "info");
    },
    "draft-model": (el) => { if (S.chat.draft) S.chat.draft.model = el.value; },
    "draft-slides": (el) => { const d = S.chat.draft; if (d) { d.slides = el.checked; estimateDraft(); } },
    "draft-change": () => { const d = S.chat.draft; if (d) chatPicker(d.course_id, d); },
    "chat-menu": (el) => menu(el, [
      { id: "rename", icon: "pencil", label: t("Rename"), run: () => A["chat-rename"]() },
      { id: "export", icon: "file-text", label: t("Save as Markdown"), desc: t("In the course folder, under “AI chats”"),
        run: async () => { const r = await call("chat_export", S.chat.data.id); toast(r.ok ? t("Chat saved — shown in Explorer.") : r.error, r.ok ? "success" : "error"); } },
      "-",
      { id: "delete", icon: "x", label: t("Delete chat"), run: async () => {
        if (!confirm(t("Delete this chat? This cannot be undone."))) return;
        const r = await call("chat_delete", S.chat.data.id);
        if (!r.ok) { toast(r.error, "error"); return; }
        S.chat.id = null; S.chat.data = null; S.view = { name: "chat" }; renderChatMain(); loadChats();
      } },
    ]),
    cite: async (el) => {
      const sec = +el.dataset.t, part = +el.dataset.p || 1, L = +el.dataset.l || 1;
      if (S.view.name === "day") { closeModal(); jumpTo(part, sec); return; }   // timestamps in study notes and flashcards
      const c = S.chat.data; if (!c) return;
      const day = c.days[L - 1] || c.days[0];
      if (!day || day.gone) { toast(t("That class day is no longer in your library."), "warning"); return; }
      await openDay(day.key); setTimeout(() => jumpTo(part, sec), 60);
    },
    "cp-course": (el) => window.__cp.course(el.value),
    "cp-day": (el) => window.__cp.day(el.dataset.key, el.checked),
    "cp-all": () => window.__cp.all(), "cp-last": () => window.__cp.last(), "cp-none": () => window.__cp.none(),
    "cp-focus": (el) => window.__cp.focus(el.dataset.val),
    "cp-slides": (el) => window.__cp.slides(el.checked),
    "cp-model": (el) => window.__cp.model(el.dataset.val),
    "cp-go": () => window.__cp.go(),
    "menu-copy": (el) => menu(el, [
      { id: "notes", icon: "book", label: t("Study notes prompt + transcript"), desc: t("Paste into ChatGPT or Claude"), run: () => copyAI("notes") },
      { id: "exam", icon: "star", label: t("Exam focus prompt + transcript"), run: () => copyAI("exam") },
      { id: "flash", icon: "list-checks", label: t("Flashcards prompt + transcript"), run: () => copyAI("flashcards") },
      "-",
      { id: "plain", icon: "copy", label: t("Transcript only"), run: () => copyAI("plain") },
      { id: "course", icon: "library", label: t("Whole course (all lectures)"), desc: t("Study notes prompt + every transcribed day"), run: () => copyAI("notes", "course:" + S.day.course.id) },
    ]),
    "menu-more": (el) => menu(el, [
      { id: "file", icon: "file-audio", label: t("Transcribe a file from this PC…"), desc: t("Any audio/video file"), run: async () => { const r = await call("transcribe_local_file"); if (r.ok && r.queued) toast(t.n(r.queued, "{n} file added to the queue", "{n} files added to the queue")); } },
      { id: "out", icon: "folder-open", label: t("Open IEUStudia folder"), run: () => A["open-output"]() },
      { id: "set", icon: "settings", label: t("Settings"), run: () => go({ name: "settings" }) },
    ]),
    set: async (el) => saveSetting({ [el.dataset.key]: el.dataset.val }),
    "set-size": (el) => saveSetting({ text_size: +el.dataset.val }),
    // the language: Python switches at once; the page reloads to draw everything in it (where you were: Settings)
    "set-lang": async (el) => {
      const r = await call("set_language", el.dataset.val);
      if (!r.ok) { toast(r.error, "error"); return; }
      const was = t.lang();
      t.load(r);
      if (r.lang === was) { if (S.mode === "setup") render(); else renderView(); return; }    // only the choice moves (the setup has no #view)
      if (S.mode === "setup") { render(); Studia.relabelSetup(); return; }     // drawn in it at once; this PC's check re-read, the choices kept
      try { sessionStorage.setItem("studia-view", JSON.stringify(S.view)); } catch (e) { /* storage may be unavailable */ }
      location.reload();
    },
    "set-jump": (el) => { const card = $("#" + el.dataset.id); if (card) card.scrollIntoView({ block: "start", behavior: "smooth" }); },
    "set-bool": async (el) => saveSetting({ [el.dataset.key]: el.checked }),
    "set-sel": async (el) => saveSetting({ [el.dataset.key]: el.dataset.key === "min_duration_s" ? +el.value : el.value }),
    "my-section": async (el) => { const r = await call("set_course_prefs", el.dataset.id, { my_section: el.value, show_mode: "mine" }); if (r.ok) { setCourses(r.courses); renderView(); } },
    "pick-folder": async () => { const r = await call("pick_folder"); if (r.ok && r.path) { await saveSetting({ output_dir: r.path }); if (S.mode === "setup") render(); } },
    signout: async () => {
      if (!confirm(t("Sign out and remove the stored password from this PC? Your courses and transcripts stay here."))) return;
      const r = await call("sign_out");
      if (r.profiles) S.profiles = r.profiles;
      S.mode = "signin"; S.signin = { busy: false, error: "" }; render();
    },
    "menu-profile": (el) => menu(el, [
      ...S.profiles.map((x) => ({ id: "p:" + x.id, icon: x.active ? "check" : "user", label: x.name || x.username || t("New profile"),
        desc: [x.student_number, x.active ? t("open now") : x.has_login ? "" : t("signed out")].filter(Boolean).join(" · "), run: () => switchProfile(x) })),
      ...(S.profiles.length ? ["-"] : []),
      { id: "add", icon: "plus", label: t("Add another account…"), desc: t("Its courses and transcripts stay separate"), run: () => addAccountDialog() },
      { id: "manage", icon: "users", label: t("Manage profiles"), run: () => go({ name: "settings" }) },
      { id: "signout", icon: "logout", label: t("Sign out"), run: () => A.signout() },
    ]),
    "profile-add": () => addAccountDialog(),
    "profile-switch": (el) => switchProfile(S.profiles.find((x) => x.id === el.dataset.id)),
    "profile-remove": async (el) => {
      const x = S.profiles.find((p) => p.id === el.dataset.id); if (!x) return;
      const who = x.name || x.username;
      if (!confirm(who ? t("Remove {name} from this PC?\n\nIts library, transcripts, chats and stored password on this PC are deleted. Files already saved in its IEUStudia folder stay.", { name: who })
        : t("Remove this profile from this PC?\n\nIts library, transcripts, chats and stored password on this PC are deleted. Files already saved in its IEUStudia folder stay."))) return;
      const r = await call("profile_remove", x.id);
      if (!r.ok) { toast(r.error, "error"); return; }
      S.profiles = r.profiles; toast(t("Profile removed"), "success");
      if (S.view.name === "settings") renderView();
      renderSidebar();
    },
    logs: () => call("open_logs"),
    reload: () => location.reload(),
    peek: (el) => { const p = $("#" + (el.dataset.for || "si-pass")); if (p) p.type = p.type === "password" ? "text" : "password"; },
    "modal-close": () => closeModal(),
    "modal-bg": (el, e) => { if (e.target === el) closeModal(); },
  };
  // what the other UI files may use (their actions go into A, their views into Studia.views)
  Object.assign(Studia, { S, call, esc, I, SPIN, fmtDur, rel, pct, toast, modal, closeModal, menu, go, render,
    renderView, renderSidebar, renderTop, openDay, jumpTo, emptyHTML, skeleton, mdToHtml, startDraft, chatPicker, dayCardHTML,
    newHTML, courseHTML, heroHTML, syncChecks, updateSelectionUI, A, shortDate, fmtK, limitsHTML, isReal: () => REAL,
    aiName, aiPlan, aiHint, isChatGPT, modelsFor, afterBoot, openTarget, runSearch, setCourses, findCourse, courseSub,
    otherSection, oddMark, renderChatHead });
  async function saveSetting(patch) {
    const r = await call("update_settings", patch);
    if (!r.ok) { toast(r.error, "error"); return; }
    S.settings = r.settings; applyTheme();
    if (S.view.name === "settings") renderView();
  }
  async function copyAI(template, scope) {
    const r = await call("copy_for_ai", scope || S.day.day_key, template);
    if (r.ok) toast(t("Copied {chars} characters (~{k}k tokens). Paste it into ChatGPT or Claude.", { chars: t.num(r.chars), k: dec(r.tokens / 1000, 1) }), "success");
    else toast(r.error || t("Copy failed"), "error");
  }
  function jumpTo(part, sec, deliveryId) {
    let paras = $$(".para");
    if (deliveryId && S.day) { const p = S.day.parts.find((x) => x.id === deliveryId); if (p) part = p.index; }
    if (part) paras = paras.filter((p) => +p.dataset.part === part);
    let k = 0;                                   // the paragraph containing sec; in a gap, the nearer one
    paras.forEach((p, i) => { if (+p.dataset.t <= sec + 0.5) k = i; });
    const cur = paras[k], next = paras[k + 1];
    if (cur && next && sec > +cur.dataset.e && +next.dataset.t - sec < sec - +cur.dataset.e) k += 1;
    const best = paras[k];
    if (!best) return;
    // Jump at once (not smoothly) and re-align while slide images above it load; stop if the user scrolls.
    let cancelled = false;
    const cancel = () => { cancelled = true; };
    const events = ["wheel", "pointerdown", "keydown", "touchstart"];
    events.forEach((ev) => window.addEventListener(ev, cancel, { once: true, passive: true }));
    const align = () => { if (!cancelled && best.isConnected) best.scrollIntoView({ block: "center", behavior: "instant" }); };
    align();
    [100, 300, 700, 1300, 2200].forEach((ms) => setTimeout(align, ms));
    setTimeout(() => events.forEach((ev) => window.removeEventListener(ev, cancel)), 2300);
    best.classList.remove("flash"); void best.offsetWidth; best.classList.add("flash");
  }

  // ------------------------------------------------------------------ DOM wiring
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#menu") && !e.target.closest("[data-act^='menu-']")) closeMenu();
    const el = e.target.closest("[data-act]");
    if (!el) return;
    // dropdowns act on "change" only: handling their click re-rendered the page and closed the list
    if (el.tagName === "SELECT" || el.tagName === "OPTION") return;
    if (el.tagName === "INPUT" && el.type !== "button" && !["sel", "sel-day", "set-bool", "cl-slides", "cp-day", "cp-slides", "draft-slides", "pd-week", "plan-mark", "cu-pick", "cu-mats", "rd-read", ...(Studia.inputActs || [])].includes(el.dataset.act)) return;
    const fn = A[el.dataset.act];
    if (fn && !el.disabled) fn(el, e);
  });
  document.addEventListener("change", (e) => {
    const el = e.target.closest("select[data-act]");
    if (el && A[el.dataset.act]) A[el.dataset.act](el, e);
  });
  document.addEventListener("input", (e) => {
    const el = e.target;
    if (el.dataset && el.dataset.act === "search") runSearch(el.value);
    if (el.dataset && el.dataset.act === "find") { S.dayFind = el.value; $$(".para").forEach((p) => { /* cheap re-render of paragraphs */ }); refreshFind(); }
    if (el.id === "chat-input") { S.chat.input = el.value; autosize(el); }
  });
  function refreshFind() {
    if (!S.day) return;
    const parts = S.day.parts;
    $$(".para").forEach((node) => {
      const p = parts.find((x) => x.index === +node.dataset.part);
      const para = p && p.paragraphs.find((x) => Math.abs(x.t - +node.dataset.t) < 0.05);
      if (para) node.outerHTML = paraHTML(para, p);
    });
  }
  document.addEventListener("submit", async (e) => {
    const f = e.target;
    if (f.dataset.form === "chat") { e.preventDefault(); sendChat(); return; }
    if (f.dataset.form === "add-account") {
      e.preventDefault();
      const u = f.username.value.trim(), p = f.password.value;
      if (!u || !p) { addAccountDialog(t("Enter the username and password."), false, u); return; }
      addAccountDialog("", true, u);
      const r = await call("profile_add", u, p);
      if (!r.ok) { addAccountDialog(r.error || t("Sign-in failed"), false, u); return; }
      switchProfile(r.profile);
      return;
    }
    if (f.dataset.form !== "signin") return;
    e.preventDefault();
    const u = f.username.value.trim(), p = f.password.value;
    if (!u || !p) { S.signin.error = t("Enter your username and password."); render(); return; }
    S.signin = { busy: true, error: "" }; render();
    const r = await call("sign_in", u, p);
    if (!r.ok) { S.signin = { busy: false, error: r.error || t("Sign-in failed") }; render(); return; }
    S.signin = { busy: false, error: "" };
    if (r.switch_to) { switchProfile(r.profile); return; }           // another person: their own profile
    S.profile = r.profile; S.mode = "app"; S.sync.running = true;
    S.view = { name: "home" }; render();
    if (Studia.onGo.home) Studia.onGo.home(S.view);
  });
  let textSizeTimer = null;
  function textSize(step) {        // Ctrl and +, − or 0: the reading text, a step at a time
    const sizes = TEXT_SIZES().map(([v]) => v), cur = +(S.settings.text_size || 100);
    const i = sizes.indexOf(cur) < 0 ? 1 : sizes.indexOf(cur);
    const next = step === 0 ? 100 : sizes[Math.max(0, Math.min(sizes.length - 1, i + step))];
    if (next === cur) return;
    S.settings.text_size = next; applyTheme();       // at once: the next press steps on from here
    clearTimeout(textSizeTimer);                     // saved once the presses stop (answers may come in any order)
    textSizeTimer = setTimeout(() => saveSetting({ text_size: S.settings.text_size }), 400);
    const size = TEXT_SIZES().find(([v]) => v === next)[1].toLocaleLowerCase(t.lang() === "tr" ? "tr" : "en");
    toast(t("Reading text: {size} (Settings → Appearance)", { size }), "info");
  }
  function trapTab(e, m) {         // Tab and Shift+Tab go round inside an open dialog
    const items = $$("button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])", m)
      .filter((x) => x.offsetParent !== null);
    if (!items.length) { e.preventDefault(); m.focus(); return; }
    const first = items[0], last = items[items.length - 1], at = document.activeElement;
    if (!m.contains(at) || at === m) { e.preventDefault(); (e.shiftKey ? last : first).focus(); }
    else if (e.shiftKey && at === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && at === last) { e.preventDefault(); first.focus(); }
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.target && e.target.id === "chat-input") { e.preventDefault(); sendChat(); return; }
    if (e.key === "Enter" && e.target && ["si-user", "aa-user"].includes(e.target.id)) {    // on to the password first
      const pass = $(e.target.id === "si-user" ? "#si-pass" : "#aa-pass");
      if (pass && !pass.value) { e.preventDefault(); pass.focus(); return; }
    }
    if ((e.key === "Enter" || e.key === " ") && e.target && e.target.matches && e.target.matches("[data-act][role=button]:not(button)")) {
      e.preventDefault(); e.target.click(); return;          // a result, a slide, a highlight… with the keyboard
    }
    if (e.key === "Tab") { const m = $("#overlay .modal"); if (m) trapTab(e, m); }
    if (e.ctrlKey && !e.altKey && S.mode === "app" && ["+", "=", "-", "0"].includes(e.key)) { e.preventDefault(); textSize(e.key === "0" ? 0 : e.key === "-" ? -1 : 1); return; }
    if (e.key === "Escape") {
      const menuOpen = !!$("#menu"); closeMenu();
      if ($("#overlay").innerHTML) closeModal(); else if (S.qpOpen && !menuOpen) closePane();   // the jobs pane, after a dialog
    }
    if (e.key === "F5" || (e.ctrlKey && e.key.toLowerCase() === "r")) { e.preventDefault(); if (S.mode === "app") A.sync(); }
    if (e.ctrlKey && e.key.toLowerCase() === "f" && S.view.name === "day") { e.preventDefault(); const f = $("[data-act='find']"); if (f) f.focus(); }
  });

  // ------------------------------------------------------------------ boot
  async function boot() {
    t.load(await call("i18n"));
    setSplash(t("Opening your library…"));
    const slow = setTimeout(() => setSplash(window.LS_LINUX ? t("Still starting… the first start after the PC boots can take a little longer.") : t("Still starting… the first start after Windows boots can take a little longer.")), 9000);
    const b = await call("get_bootstrap");
    clearTimeout(slow);
    if (!b.ok) { startupError(b.error || t("IEUStudia could not start.")); return; }
    S.boot = b; S.settings = b.settings || {}; S.profile = b.profile; setCourses(b.courses); S.queue = b.queue || []; S.queue.forEach((j) => stamp(j)); S.lastSync = b.last_sync;
    S.profiles = b.profiles || []; S.profileId = b.profile_id || "main";
    S.limits = b.limits || {}; S.update = b.update || {}; S.paused = !!b.paused; S.pausedAt = S.paused ? performance.now() : 0; S.resume = b.resume || null;
    if (b.downloading && Studia.setup) Studia.setup.running = true;            // the setup's download goes on
    if (b.system) S.system = { ...S.system, ...b.system };
    applyTheme();
    const needSetup = REAL ? !(b.app && b.app.onboarded) : location.hash.includes("setup");
    if (needSetup && Studia.startSetup) { Studia.startSetup(); return; }
    if (!b.signed_in) { S.mode = "signin"; render(); return; }
    afterBoot();
  }
  function afterBoot() {
    S.mode = "app";
    let back = null;                // the language was just switched in Settings: back there
    try { back = JSON.parse(sessionStorage.getItem("studia-view") || "null"); sessionStorage.removeItem("studia-view"); } catch (e) { /* none */ }
    S.view = back && back.name === "settings" ? back : { name: "home" };
    render();
    if (Studia.onGo[S.view.name]) Studia.onGo[S.view.name](S.view);
    const age = Date.now() - new Date(S.lastSync || "").getTime();   // the helper (or this window before a language switch)
    if (!(age >= 0 && age < 5 * 60000)) A.sync();                    // synced minutes ago: not again (F5 still syncs)
    if (S.boot && S.boot.open) openTarget(S.boot.open);
    else if (S.boot && S.boot.updated_from) whatsNew();
  }
  function whatsNew() {             // the first start after an update
    const u = S.update || {}, v = S.boot.version;
    if (!(u.notes && u.version === v)) { toast(t("IEUStudia was updated to {version}.", { version: v }), "success"); return; }
    modal(`<h2>${t.html("IEUStudia {version} is installed", { version: v })}</h2>
      <p class="lead">${t.html("Updated from {version}. Everything you had is still here.", { version: S.boot.updated_from })}</p>
      <div class="update-notes md">${mdToHtml(u.notes)}</div>
      <div class="actions"><button class="btn primary" data-act="modal-close">${t("OK")}</button></div>`);
  }
  // what a notification opens: "course:<id>[:<tab>]", "day:<class day>", "ready:<course id>:<assessment>" (Get
  // ready), "week:<course id>:<week>", "update" or "home"
  function openTarget(target) {
    if (S.mode !== "app" || !target) return;
    const [kind, id, ...rest] = String(target).split(":");
    if (kind === "course" && findCourse(id)) {
      go({ name: "course", courseId: id, tab: rest[0] || "announcements" });
      const tabs = $(".tabs");
      if (tabs) tabs.scrollIntoView({ block: "start", behavior: "instant" });        // what it's about, in view
    } else if (kind === "day") openDay(String(target).slice(4));
    else if (kind === "week" && findCourse(id)) go({ name: "course", courseId: id, jumpWeek: +rest[0] || "now" });
    else if (kind === "ready" && findCourse(id) && Studia.openReady) { go({ name: "course", courseId: id }); Studia.openReady(id, rest.join(":")); }
    else if (kind === "update") { go({ name: "home" }); updateModal(); }
    else go({ name: "home" });
  }
  function start() {
    if (window.pywebview && window.pywebview.api && Object.keys(window.pywebview.api).length) { REAL = true; boot(); return; }
    let started = false, late = false;
    window.addEventListener("pywebviewready", () => {
      if (started && !late) return;
      const st = late && $("#splash-status");
      if (st) {                                    // it came after all: back to the start-up screen, and on
        $$("#splash .splash-error, #splash .splash-actions").forEach((el) => el.remove());
        st.insertAdjacentHTML("beforebegin", `<div class="splash-bar"><i></i></div>`);
      }
      started = true; late = false; REAL = true; boot();
    });
    // In the app's window (WebView2) the API comes once the page has loaded, which can take seconds on a slow start:
    // wait for it there, never show the demo data. Only a browser preview has none and shows the demo.
    if (window.chrome && window.chrome.webview) {
      setTimeout(() => {
        if (started) return;
        started = late = true;
        if (document.documentElement.lang === "tr") {    // no table from Python yet: the start-up screen's Turkish
          t.load({ lang: "tr", table: { "IEUStudia's window could not connect to the app. Try again; if it keeps happening, close IEUStudia and open it again.": "IEUStudia'nın penceresi uygulamaya bağlanamadı. Tekrar dene; yine olursa IEUStudia'yı kapatıp yeniden aç.", "Try again": "Tekrar dene", "Open logs": "Günlükleri aç" } });
        }
        startupError(t("IEUStudia's window could not connect to the app. Try again; if it keeps happening, close IEUStudia and open it again."), false);   // (no app to open the logs)
      }, 25000);
      return;
    }
    setTimeout(() => { if (!started && !window.pywebview) { started = true; REAL = false; boot(); } }, 350);
  }
  render();
  start();

  // ------------------------------------------------------------------ mock (browser preview)
  const Mock = (() => {
    const today = new Date();
    const iso = (d) => d.toISOString().slice(0, 10);
    const addDays = (n) => { const d = new Date(today); d.setDate(d.getDate() + n); return d; };
    let seq = 0;
    const rec = (date, time, mins, o = {}) => {
      const [h, m] = time.split(":").map(Number); const end = new Date(2000, 0, 1, h, m + mins);
      return { id: `r${++seq}`, name: o.name || `Lecture ${seq}`, start: `${iso(date)}T${time}:00`, time, end_time: `${String(end.getHours()).padStart(2, "0")}:${String(end.getMinutes()).padStart(2, "0")}`,
        duration_s: mins * 60, part: null, parts: 0, kind: o.kind || "lecture", section_tag: o.sec || "", mine: o.mine ?? null, is_new: !!o.new, duplicate: !!o.dup,
        hidden: false, too_short: mins < 1, status: o.status || "available", progress: null, detail: null, error: o.error || null,
        viewer_url: "https://panopto.ieu.edu.tr/Panopto/Pages/Viewer.aspx?id=demo", warnings: o.warnings || [], warn_level: o.warnings && o.warnings.length ? o.warnings[0].level : null, has_slides: true };
    };
    const day = (cid, date, kind, recs) => {
      let n = 0; recs.forEach((r) => { if (!r.duplicate && !r.too_short) r.part = ++n; }); recs.forEach((r) => (r.parts = n));
      const countable = recs.filter((r) => !r.duplicate && !r.too_short);
      const mine = countable.some((r) => r.mine === true) ? true : countable.length && countable.every((r) => r.mine === false) ? false : null;
      return { day_key: `${cid}|${iso(date)}|${kind}`, date: iso(date), weekday: "", label: date.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" }),
        kind, kind_label: kind === "lab" ? "Lab" : "Lecture", mine, transcribed: countable.length > 0 && countable.every((r) => r.status === "done"),
        any_done: countable.some((r) => r.status === "done"), duration_s: countable.reduce((s, r) => s + r.duration_s, 0), parts: n,
        is_new: countable.some((r) => r.is_new), warn_level: null, recordings: recs };
    };
    const course = (id, code, name, inst, days, o = {}) => {
      const all = days.flatMap((d) => d.recordings.filter((r) => !r.duplicate && !r.too_short));
      return { id, code, name, display_name: name, instructor: inst, term: "2026-2027 Fall Undergrad", section: o.section || "1", has_panopto: o.has_panopto !== false, cover: "", lms_progress: o.progress ?? null,
        show_mode: o.sections ? "mine" : "all", my_section: o.sections ? "1" : null, available_sections: o.sections || [],
        counts: { total: all.length, new: all.filter((r) => r.is_new).length, done: all.filter((r) => r.status === "done").length, done_any: all.filter((r) => r.status === "done").length, processing: all.filter((r) => r.status === "processing").length }, days };
    };
    const quiet = [{ level: "warning", message: "There is a lot of background noise; the transcript may contain errors." }];
    const mockProfiles = [
      { id: "main", name: "Test Student", username: "test.student", student_number: "20230000000", main: true, has_login: true },
      { id: "p-1a2b3c4d", name: "Ayşe Demir", username: "ayse.demir", student_number: "20240000001", main: false, has_login: true },
    ];
    const courses = [
      course("19226", "EEE 215", "Circuits & Electronics for Computer Eng.", "Dr. Ayşe Demir", [
        day("19226", addDays(0), "lecture", [rec(addDays(0), "11:15", 41, { sec: "1", mine: true, status: "processing" })]),
        day("19226", addDays(-6), "lecture", [rec(addDays(-6), "11:17", 42, { sec: "2-3", mine: false, new: true }), rec(addDays(-6), "12:10", 0.6, { sec: "2-3", mine: false }), rec(addDays(-6), "13:06", 38, { sec: "2-3", mine: false, new: true })]),
        day("19226", addDays(-7), "lecture", [rec(addDays(-7), "11:19", 40, { sec: "1", mine: true, new: true }), rec(addDays(-7), "12:10", 42, { sec: "1", mine: true, new: true }), rec(addDays(-7), "13:05", 38, { mine: true, new: true, warnings: quiet })]),
        day("19226", addDays(-7), "lab", [rec(addDays(-7), "15:49", 104, { sec: "1", mine: true, kind: "lab" }), rec(addDays(-7), "15:49", 104, { sec: "1", mine: true, kind: "lab", dup: true })]),
        day("19226", addDays(-14), "lecture", [rec(addDays(-14), "11:15", 43, { mine: true, status: "done" }), rec(addDays(-14), "12:10", 43, { mine: true, status: "done" }), rec(addDays(-14), "13:04", 42, { mine: true, status: "done" })]),
      ], { sections: ["1", "2", "3"], progress: 83 }),
      course("17653", "EEE 242", "Digital Design", "Dr. Burak Kaya", [
        day("17653", addDays(-4), "lecture", [rec(addDays(-4), "14:06", 40, { new: true }), rec(addDays(-4), "14:46", 42, { new: true })]),
        day("17653", addDays(-11), "lecture", [rec(addDays(-11), "14:01", 44, { status: "failed", error: "Download failed: connection reset" }), rec(addDays(-11), "14:45", 45, { status: "done" })]),
      ], { progress: 45 }),
      course("17933", "MATH 240", "Probability for Engineers", "Dr. Cem Yıldız", [
        day("17933", addDays(-6), "lecture", [rec(addDays(-6), "08:28", 47, { status: "done" }), rec(addDays(-6), "09:33", 29, { status: "done" }), rec(addDays(-6), "10:25", 40, { status: "done" })]),
        day("17933", addDays(-13), "lecture", [rec(addDays(-13), "08:30", 46), rec(addDays(-13), "09:28", 40), rec(addDays(-13), "10:21", 37)]),
      ], { progress: 40 }),
      course("16988", "MATH 250", "Linear Algebra for Engineers", "Zubeyir Turkoglu", [
        day("16988", addDays(-4), "lecture", [rec(addDays(-4), "10:17", 46, { new: true }), rec(addDays(-4), "11:18", 10, { new: true }), rec(addDays(-4), "12:00", 37, { new: true })]),
      ], { progress: 46 }),
      course("17855", "FENG 495", "Double Major Graduation Project I", "Izzet Murat Aksit", [], { section: "1", progress: 0 }),
      course("19456", "FENG 495", "Double Major Graduation Project I", "Murat Askar", [], { section: "3", progress: 0 }),
    ];
    const slideSvg = (n) => "data:image/svg+xml;utf8," + encodeURIComponent(`<svg xmlns='http://www.w3.org/2000/svg' width='1600' height='900'><rect width='1600' height='900' fill='#fff'/><rect x='80' y='70' width='1440' height='120' rx='14' fill='#7c3aed'/><text x='120' y='150' font-size='56' font-family='Segoe UI' fill='#fff'>Bayes' theorem — slide ${n}</text><text x='120' y='330' font-size='44' font-family='Cambria Math' fill='#111'>P(A | B) = P(B | A) · P(A) / P(B)</text><path d='M200 520 C 400 420, 600 640, 900 500' stroke='#6d28d9' stroke-width='8' fill='none'/><text x='980' y='560' font-size='48' fill='#6d28d9' font-family='Segoe Print'>= 396 / 3384</text></svg>`);
    const P = (at, text, o = {}) => ({ t: at, ts: `${String(Math.floor(at / 60)).padStart(2, "0")}:${String(Math.floor(at % 60)).padStart(2, "0")}`, text, lang: o.tr ? "tr" : "en", tag: o.tr ? "TR" : "", star: !!o.star, tags: o.star ? ["exam"] : [], uncertain: !!o.unc, slide: o.slide || null, slide_image: o.slide ? slideSvg(o.slide) : null });
    const detail = {
      day_key: "17933|x|lecture", title: "MATH 240 · Lecture", date_label: "Wednesday, 14 October 2026", subtitle: "Probability for Engineers · Dr. Cem Yıldız",
      course: { id: "17933", code: "MATH 240", name: "Probability for Engineers", instructor: "Dr. Cem Yıldız" }, kind: "lecture", languages: { en: 0.93, tr: 0.07 },
      highlights: [{ part: 1, t: 1121, ts: "18:41", text: "…this is exactly the kind of question I will ask in the midterm, so please be careful with the denominator…", tags: ["Exam", "Important"] },
        { part: 2, t: 305, ts: "05:05", text: "Arkadaşlar, bu konu sınavda çıkacak, dikkat edin.", tags: ["Exam"] }],
      parts: [
        { index: 1, id: "a", title: "Part 1", time_range: "08:28–09:15", viewer_url: "https://panopto.ieu.edu.tr", status: "done", slides: 3, warnings: [], paragraphs: [
          P(0, "So good morning everyone. How are you? Today we continue with conditional probability and then Bayes' theorem.", { slide: 1 }),
          P(31, "Let me remind you the multiplication rule: the probability of A intersection B equals P of A given B times P of B. Right?"),
          P(452, "Siz ne dersiniz? Nereden buldunuz bu enerjiyi? Neyse, devam edelim.", { tr: true }),
          P(480, "Now look at this screening example. The condition affects 0.4 percent of the population and the test detects it in 99 percent of the cases.", { slide: 2 }),
          P(1121, "And this is exactly the kind of question I will ask in the midterm, so please be careful with the denominator: P of plus is 3384 over 100000.", { star: true, slide: 3 }),
          P(1190, "Minus four zero minus five.", { unc: true }),
        ] },
        { index: 2, id: "b", title: "Part 2", time_range: "09:33–10:02", viewer_url: "https://panopto.ieu.edu.tr", status: "done", slides: 0, warnings: quiet, paragraphs: [
          P(12, "Okay, welcome back. Let's solve the second problem on the board together."),
          P(305, "Arkadaşlar, bu konu sınavda çıkacak, dikkat edin. Formülü ezberlemeyin, mantığını anlayın.", { tr: true, star: true }),
          P(340, "So the posterior probability is 396 over 3384, which is about 11.7 percent. Surprising, isn't it?"),
        ] },
      ], files: { folder: "C:\\Users\\student\\Documents\\IEUStudia\\MATH 240", md: "x.md", pack: "pack.md", pdf: "", zip: "", notes: "" }, notes: [],
    };
    const emit = (e) => setTimeout(() => window.__lsEvent(e), 0);
    let paused = false;
    function simulate(ids) {
      ids.forEach((id, k) => {
        let p = 0, stage = "downloading";
        setTimeout(() => {
          const iv = setInterval(() => {
            if (paused) return;
            p += stage === "downloading" ? 0.2 : 0.08;
            if (p >= 1 && stage === "downloading") { stage = "transcribing"; p = 0; }
            if (p >= 1 && stage === "transcribing") { clearInterval(iv); emit({ type: "job", id, title: "MATH 250 · lecture", stage: "done", final: true, progress: 1 }); emit({ type: "queue", jobs: [] }); return; }
            emit({ type: "job", id, title: "Lecture", stage, progress: p, detail: stage === "downloading" ? `Downloading audio ${Math.round(p * 30)}/30 MB` : `Transcribing passage ${Math.round(p * 300)} of 300`, eta_s: stage === "transcribing" ? Math.round((1 - p) * 900) : null });
            emit({ type: "queue", paused, jobs: ids.map((x) => ({ id: x, title: `MATH 250 · Fri 16 Oct ${x}`, stage: x === id ? stage : "queued", progress: x === id ? p : 0, detail: x === id ? "Transcribing passages" : "Waiting" })) });
          }, 700);
        }, k * 9000);
      });
    }
    return {
      get_bootstrap: () => { setTimeout(() => emit({ type: "system", gpu: { available: true, name: "NVIDIA GeForce RTX 4070 Laptop GPU", vram_mb: 8188, libraries: true, used: true, fits: true }, ai: { available: true, version: "2.1.287", provider: (location.hash.match(/ai=(\w+)/) || [])[1] || "claude", label: { chatgpt: "ChatGPT", codex: "ChatGPT", local: "Offline model" }[(location.hash.match(/ai=(\w+)/) || [])[1]] || "Claude", can_plans: !location.hash.includes("ai=local"), can_images: !location.hash.includes("ai=local") },
        presets: [{ id: "large-v3-turbo", label: "Fast & accurate", description: "Nearly the best quality, and fast with an NVIDIA graphics card.", size_mb: 1620, downloaded: true },
          { id: "large-v3", label: "Maximum accuracy", description: "The best quality for hard audio and long Turkish passages; the slowest, even with an NVIDIA graphics card.", size_mb: 3090, downloaded: true },
          { id: "medium", label: "Medium", description: "More accurate than Small, but very slow without an NVIDIA GPU (hours per lecture). With one, Fast & accurate is both quicker and more accurate.", size_mb: 1530, downloaded: false },
          { id: "small", label: "Small", description: "The one for PCs without an NVIDIA GPU: fast enough on the processor, a bit less accurate.", size_mb: 484, downloaded: false }] }), 300);
        const signed = !location.hash.includes("signin");
        const pid = (location.hash.match(/profile=([\w-]+)/) || [])[1] || "main";
        const profs = mockProfiles.map((x) => ({ ...x, active: x.id === pid }));
        const me = profs.find((x) => x.active) || profs[0];
        return { ok: true, version: "1.1.0", signed_in: signed, first_run: false, profile: { name: me.name, student_number: me.student_number, username: me.username },
          profiles: profs, profile_id: me.id,
          settings: { output_dir: "C:\\Users\\student\\Documents\\IEUStudia", model: "large-v3-turbo", device: "auto", theme: location.hash.includes("dark") ? "dark" : "light", capture_slides: true, write_srt: true, keep_audio: false, ask_before_closing: true, min_duration_s: 60, ai_model: "sonnet", first_run_done: true },
          last_sync: new Date(Date.now() - 120000).toISOString(), courses: signed ? courses : [], queue: [],
          resume: location.hash.includes("resume") ? { count: 3, days: 2, audio_s: 7800, courses: ["EEE 215", "MATH 250"] } : null,
          update: Mock.update_check() };
      },
      i18n: () => ({ ok: true, ...(window.__I18N_PREVIEW || { lang: "en", table: {} }), setting: "", windows: "en" }),
      set_language: (code) => ({ ok: true, ...(code === "tr" && window.__I18N_PREVIEW ? window.__I18N_PREVIEW : { lang: "en", table: {} }), setting: code, windows: "en" }),
      sync: () => { emit({ type: "sync", phase: "start", message: "Checking IEULearn and Panopto…" }); setTimeout(() => emit({ type: "sync", phase: "done", new_count: 0, message: "Up to date", last_sync: new Date().toISOString() }), 1400); return { ok: true }; },
      transcribe: (ids) => { simulate(ids); return { ok: true, queued: ids.length }; },
      transcribe_day: (k) => { const d = courses.flatMap((c) => c.days).find((x) => x.day_key === k); const ids = d.recordings.filter((r) => !r.duplicate && !r.too_short && r.status !== "done").map((r) => r.id); simulate(ids); return { ok: true, queued: ids.length }; },
      cancel: () => ({ ok: true }), get_day: () => ({ ok: true, day: JSON.parse(JSON.stringify(detail)) }),
      mark_set: () => ({ ok: true }), mark_delete: () => ({ ok: true }), course_marks: () => ({ ok: true, marks: [] }),
      library: () => ({ ok: true, courses: courses.map((c) => ({ course_id: c.id, code: c.code, name: c.display_name, days: c.days.filter((d) => d.any_done) })).filter((c) => c.days.length) }),
      search: (q) => ({ ok: true, results: [{ day_key: "17933|x|lecture", delivery_id: "a", t: 1121, ts: "18:41", snippet: `…the kind of question I will ask in the \u0002${q}\u0003, so please be careful…`, course_code: "MATH 240", date: "2026-10-14", time: "08:28" }],
        materials: [{ resource_id: "m1793302", title: "Week03_Student", page: 12, snippet: `Bayes' rule [Eq. 2.9] … \u0002${q}\u0003 with the total probability`, course_id: "17933", course_code: "MATH 240" }] }),
      queue_pause: (on) => { paused = !!on; emit({ type: "queue", jobs: S.queue, batch: S.batch, paused }); return { ok: true, paused }; },
      queue_first: () => ({ ok: true }), queue_resume: () => ({ ok: true, queued: 3 }), queue_forget: () => ({ ok: true }),
      copy_text: () => ({ ok: true }),
      open_file: () => ({ ok: true }), open_output: () => ({ ok: true }), open_course_folder: () => ({ ok: true }),
      open_url: () => ({ ok: true }), open_logs: () => ({ ok: true }),
      update_check: () => ({ ok: true, current: "2.3.0", available: location.hash.includes("update"), version: "2.4.0", can_install: true,
        notes: "**Transcription**\n- Faster on laptops without a graphics card.\n- The time left is shown for the whole queue.", checked_at: Date.now() / 1000, error: "" }),
      update_install: () => { [0, 25, 60, 100].forEach((p, i) => setTimeout(() => emit({ type: "update", state: p < 100 ? "downloading" : "installing", done: p * 750000, total: 75000000 }), 500 * i)); return { ok: true }; },
      update_cancel: () => ({ ok: true }),
      copy_for_ai: () => ({ ok: true, chars: 41234, tokens: 11450 }), export_pack: (k) => { emit({ type: "export", phase: "start", day_key: k }); setTimeout(() => emit({ type: "export", phase: "done", day_key: k, files: {} }), 1500); return { ok: true }; },
      ai_estimate: (k, s) => ({ ok: true, estimate: { input: s ? 38400 : 11800, output: 3500, images: s ? 33 : 0 }, ai: { available: true }, model: "sonnet" }),
      ai_notes: (k, kind = "notes") => { const label = { notes: "Study notes", exam: "Exam focus", flashcards: "Flashcards" }[kind]; emit({ type: "ai", phase: "start", day_key: k, kind, label }); let i = 0; const txt = "# Overview\nThis lecture covered **conditional probability** and Bayes' theorem.\n\n## Topics\n1. Multiplication rule — $P(A\\cap B)=P(A\\mid B)P(B)$ (Part 1, 00:31)\n2. Screening example (Part 1, 08:00)\n\n## What the lecturer emphasised\n- The denominator in Bayes' theorem — *likely midterm question* (Part 1, 18:41)\n";
        const iv = setInterval(() => { i += 40; emit({ type: "ai", phase: i >= txt.length ? "done" : "progress", day_key: k, kind, label, text: txt.slice(0, i), path: "notes.md" }); if (i >= txt.length) clearInterval(iv); }, 120); return { ok: true }; },
      ai_cancel: () => ({ ok: true }),
      set_course_prefs: (id, patch) => { const c = courses.find((x) => x.id === id); Object.assign(c, patch.show_mode ? { show_mode: patch.show_mode } : {}, patch.my_section ? { my_section: patch.my_section } : {}); return { ok: true, courses }; },
      set_hidden: () => ({ ok: true, courses }), mark_seen: () => { courses.forEach((c) => { c.counts.new = 0; c.days.forEach((d) => { d.is_new = false; d.recordings.forEach((r) => (r.is_new = false)); }); }); return { ok: true, courses }; },
      update_settings: (p) => { const s = Object.assign(Mock._s || (Mock._s = Mock.get_bootstrap().settings), p); return { ok: true, settings: s }; },
      pick_folder: () => ({ ok: true, path: null }), sign_in: () => ({ ok: true, profile: { name: "Test Student", student_number: "20230000000", username: "test.student" } }),
      sign_out: () => ({ ok: true }), transcribe_local_file: () => ({ ok: true, queued: 0 }), finish_first_run: () => ({ ok: true }),
      profiles: () => ({ ok: true, profiles: Mock.get_bootstrap().profiles }),
      profile_add: (u, p) => (p === "wrong" ? { ok: false, error: "Wrong username or password.", need_login: true }
        : { ok: true, created: true, profile: { id: "p-9e8d7c6b", name: u.replace(/[._]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()), username: u, student_number: "20250000123", active: false, main: false, has_login: true } }),
      profile_switch: (id) => ({ ok: true, profile: { id } }),
      profile_remove: (id) => { const i = mockProfiles.findIndex((x) => x.id === id); if (i > 0) mockProfiles.splice(i, 1); return { ok: true, profiles: Mock.get_bootstrap().profiles }; },
      ...mockChat(emit),
    };
  })();

  function mockChat(emit) {   // browser preview of the chat
    const now = () => { const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19); };
    const limits = { five_hour: { used: 0.19, resets_at: Math.floor(Date.now() / 1000) + 7200 }, seven_day: { used: 0.64, resets_at: Math.floor(Date.now() / 1000) + 260000 }, status: "allowed", at: Math.floor(Date.now() / 1000) - 300 };
    const scope = { "17933": [
      { day_key: "17933|x|lecture", date: "2026-10-14", label: "Wed 14 Oct", kind: "lecture", kind_label: "Lecture", parts: 3, stars: 4, slides: 3, tokens: 23800, hl_tokens: 2100, start: "2026-10-14T08:28:00" },
      { day_key: "17933|y|lecture", date: "2026-10-07", label: "Wed 7 Oct", kind: "lecture", kind_label: "Lecture", parts: 3, stars: 2, slides: 0, tokens: 21400, hl_tokens: 1300, start: "2026-10-07T08:30:00" }] };
    const chats = {};
    const answer = "Based on the lecture, these are the most likely quiz topics:\n\n1. **Bayes' theorem** — the lecturer said *\"this is exactly the kind of question I will ask in the midterm\"* [P1 18:41].\n   - Watch the denominator: $P(+) = 3384/100000$.\n2. **Conditional probability** and the multiplication rule $P(A\\cap B)=P(A\\mid B)\\,P(B)$ [P1 00:31].\n3. The screening example (posterior ≈ 11.7%) [P2 05:40, P2 06:10].\n\n| Topic | Emphasised | Where |\n|---|---|---|\n| Bayes' theorem | ⭐⭐ | [P1 18:41] |\n| Multiplication rule | ⭐ | [P1 00:31] |\n\n> **My prediction:** a two-step screening question like the one at [P2 05:05].\n\nIn Turkish he added *\"bu konu sınavda çıkacak\"* — “this topic will be on the exam” (Part 2, 05:05).\n\n$$P(D\\mid +) = \\frac{P(+\\mid D)\\,P(D)}{P(+)} = \\frac{0.99 \\cdot 0.004}{0.03384} \\approx 0.117$$\n\nAlso check: $A = \\begin{bmatrix} 1 & 2 \\\\ 3 & 4 \\end{bmatrix}$, $\\det A = -2$, $x_{1}^2 + \\sqrt{x_2} \\le \\bar{X}$, $v \\in \\mathbb{R}^n$.";
    const label = (c) => (c.days.length === 1 ? `${c.days[0].label} · ${c.days[0].kind}` : `${c.days.length} class days`) + (c.focus === "highlights" ? " · highlights only" : "");
    const summary = (c) => ({ id: c.id, course_id: c.course_id, course_code: c.course_code, title: c.title || (c.messages[0] ? c.messages[0].text.slice(0, 64) : ""), scope_label: label(c), model: c.model, updated_at: c.updated_at, n_messages: c.messages.length, running: c.messages.some((m) => m.status === "streaming") });
    const view = (c) => ({ ...summary(c), days: c.days, focus: c.focus, slides: c.slides, context_tokens: c.tokens, images: c.slides ? 3 : 0, course_name: "MATH 240 – Probability for Engineers", messages: JSON.parse(JSON.stringify(c.messages)), limits });
    const timers = {};
    return {
      chat_courses: () => ({ ok: true, courses: [{ id: "17933", code: "MATH 240", name: "Probability for Engineers", days: 2 }], limits }),
      chat_scope: (cid) => ({ ok: true, course: { id: cid, code: "MATH 240", name: "Probability for Engineers" }, days: scope[cid] || [], max_tokens: 150000, max_slides: 60 }),
      chat_estimate: (cid, days, focus, slides) => ({ ok: true, tokens: (focus === "highlights" ? 2100 : 23800) * days.length + 700 + (slides ? 2400 : 0), images: slides ? 3 : 0, slides_total: 3, days: days.length }),
      chat_list: () => ({ ok: true, chats: Object.values(chats).filter((c) => c.messages.length).sort((a, b) => b.updated_at.localeCompare(a.updated_at)).map(summary), limits }),
      chat_get: (id) => (chats[id] ? { ok: true, chat: view(chats[id]) } : { ok: false, error: "This chat no longer exists." }),
      chat_create: (cid, days, focus, slides, model, effort, send, uploads) => {
        const id = "m" + Math.random().toString(36).slice(2, 8);
        if (uploads && Studia.mocks.chat_adopt) Studia.mocks.chat_adopt(uploads, id);     // the new chat's files become its own
        const known = scope[cid] || [];
        const ds = days.map((k, i) => { const d = known.find((x) => x.day_key === k) || { label: "Wed 14 Oct", kind_label: "Lecture" }; return { key: k, n: i + 1, label: d.label, kind: d.kind_label, long: d.label, gone: false }; });
        chats[id] = { id, course_id: cid, course_code: "MATH 240", title: "", days: ds, focus, slides, model: model || "sonnet", effort: effort || "", tokens: 23800 * days.length, messages: [], updated_at: now() };
        return { ok: true, chat: view(chats[id]) };
      },
      chat_send: (id, text) => {
        const c = chats[id]; const seq = c.messages.length + 2;
        c.messages.push({ seq: seq - 1, role: "user", text, status: "done", usage: {} }, { seq, role: "assistant", text: "", status: "streaming", usage: {} });
        c.updated_at = now();
        let i = 0; const m = c.messages[c.messages.length - 1];
        timers[id] = setInterval(() => {
          i += 28; m.text = answer.slice(0, i);
          const done = i >= answer.length;
          if (done) { clearInterval(timers[id]); m.status = "done"; m.usage = { input_tokens: 9, cache_creation_input_tokens: seq > 2 ? 140 : 24100, cache_read_input_tokens: seq > 2 ? 24100 : 0, output_tokens: 412 }; }
          emit({ type: "chat", chat_id: id, seq, state: m.status, text: m.text, usage: m.usage, limits });
        }, 70);
        return { ok: true, user_seq: seq - 1, seq };
      },
      chat_stop: (id) => {
        const c = chats[id]; const m = c && c.messages[c.messages.length - 1];
        if (m && m.status === "streaming") { clearInterval(timers[id]); m.status = "stopped"; emit({ type: "chat", chat_id: id, seq: m.seq, state: "stopped", text: m.text, usage: {} }); }
        return { ok: true };
      },
      chat_update: (id, patch) => { Object.assign(chats[id], patch.title != null ? { title: patch.title } : {}, patch.model ? { model: patch.model } : {}, patch.effort ? { effort: patch.effort } : {}); return { ok: true, chat: view(chats[id]) }; },
      chat_delete: (id) => { delete chats[id]; return { ok: true }; },
      chat_export: () => ({ ok: true, path: "chat.md" }),
    };
  }
})();
