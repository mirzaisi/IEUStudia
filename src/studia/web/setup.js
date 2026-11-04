/* IEUStudia — the first-start setup: welcome and notice, this PC's check, the speech model, AI (ChatGPT / Claude /
 * ChatGPT through the Codex CLI / offline / none), the background helper and notifications, the downloads (with
 * sizes), then signing in.
 * Also the AI, background, storage and about cards of Settings. Plugs into app.js through window.Studia. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, call, esc, I, SPIN, toast, A } = St;
  const $ = (s, el = document) => el.querySelector(s);
  const gb = (mb) => (mb >= 1024 ? `${t.num(mb / 1024, 1, false)} GB` : `${t.num(mb, 0, false)} MB`);     // 1.6 GB · 1,6 GB; 1012 MB, not 1,012
  const bytes = (n) => gb(n / 1048576);
  const tenths = (x) => t.num(x, Number.isInteger(x) ? 0 : 1);       // 3 · 3.4 (3,4 in Turkish)

  const STEPS = ["welcome", "pc", "speech", "ai", "background", "download", "signin"];
  // the rail's names: a function, drawn in the language of the moment (the Turkish arrives after this script loads)
  const stepName = (key) => ({ welcome: t("Welcome"), pc: t("This PC"), speech: t("Transcription"), ai: t("AI"),
    background: t("Background"), download: t.c("noun", "Download"), signin: t("Sign in") })[key];
  const U = St.setup = { step: 0, st: null, ai: null, ch: {}, plan: null, dl: {}, running: false, done: false, accepted: false, error: "", timer: null,
    busy: {}, autoTested: {} };

  // ------------------------------------------------------------------ the notice (also in Settings → About)
  // a function, not a constant: it is drawn in the language of the moment (also in Settings → About)
  const NOTICE = () => `<ul class="notice">
      <li>${t("<b>Independent student project.</b> IEUStudia is not made, endorsed or supported by İzmir University of Economics, IEULearn or Panopto. Use it at your own risk; it comes without any warranty and the authors take no responsibility for how it is used.")}</li>
      <li>${window.LS_LINUX ? t("<b>Your EkoID password stays with you.</b> It is sent only to learn.ieu.edu.tr to sign you in. On this PC it is stored encrypted with a key in your desktop's keyring, so only your user account can read it. It never goes to any other server: there is no IEUStudia server.") : t("<b>Your EkoID password stays with you.</b> It is sent only to learn.ieu.edu.tr to sign you in. On this PC it is stored encrypted with Windows' own protection, so only your Windows account can read it. It never goes to any other server: there is no IEUStudia server.")}</li>
      <li>${t("<b>Your lectures stay on this PC.</b> Audio is transcribed on your own computer. Recordings, slides and materials belong to your lecturers and the university: keep them for your own study and do not share them.")}</li>
      <li>${t("<b>AI is optional and can make mistakes.</b> Nothing is sent to an AI service until you use an AI feature. Then the lectures and course files you chose are sent to the service you picked (OpenAI or Anthropic, under your own account). Always check AI answers against your lectures and materials.")}</li>
      <li>${t("<b>New versions.</b> About once a day IEUStudia asks GitHub, where it's published, whether there's a new version. That request says nothing about you or your courses. You can turn it off in Settings → About.")}</li></ul>`;
  St.NOTICE = NOTICE;

  // ------------------------------------------------------------------ flow
  const llmHere = (st, id) => !!((st.catalog.llm || []).find((m) => m.id === id) || {}).ready;   // already downloaded
  St.startSetup = async () => {
    S.mode = "setup"; U.step = 0; U.error = "";
    St.render();
    const r = await call("setup_state");
    if (!r.ok) { U.error = r.error; St.render(); return; }
    U.st = r;
    const v = r.verdict, app = r.app;
    const ready = (id) => (r.catalog.speech.find((x) => x.id === id) || {}).ready;
    U.ch = {
      speech: app.onboarded || ready(r.model) ? r.model : v.recommended_model,
      ai: app.ai_provider === "local" && !v.recommended_llm && !llmHere(r, app.local_model) ? "none" : (app.ai_provider || "chatgpt"),
      llm: app.local_model && app.local_model !== "qwen3-1.7b" ? app.local_model : (v.recommended_llm || "qwen3-1.7b"),
      tray: app.onboarded ? app.tray : true, startup: app.onboarded ? r.startup : true,
      notify_recordings: app.notify_recordings, notify_announcements: app.notify_announcements, notify_materials: app.notify_materials,
      notify_reminders: app.notify_reminders !== false,
    };
    U.accepted = !!app.terms_accepted;
    St.render();
    loadAi();
  };
  // the language changed on the welcome step: this PC's check is read again for the texts Python writes in it (its
  // rows, the models' names), and the AI's status too; the choices so far and the step stay (startSetup resets them)
  St.relabelSetup = async () => {
    if (!U.st || !Object.keys(U.ch).length) return St.startSetup();       // nothing chosen yet: the plain start
    const lang = t.lang();
    const r = await call("setup_state");
    if (!r.ok || t.lang() !== lang || S.mode !== "setup") return;        // the old texts stay; or switched again meanwhile
    U.st = r;
    St.render();
    loadAi();
  };
  async function loadAi(which) {
    const r = await call("ai_status", which || "");
    if (!r.ok) return;
    const before = JSON.stringify(U.ai);
    U.ai = which ? { ...(U.ai || {}), ...r } : r;           // one AI's status can come back before the full one
    for (const key of Object.keys(VENDOR)) {
      const a = U.ai[key];
      if (a && (a.logged_in || a.signed_in)) delete U.busy["login:" + key];
    }
    autoTest();
    if (JSON.stringify(U.ai) !== before) rerenderAi();     // nothing new: leave the page alone
  }
  function rerenderAi() {
    if (S.mode === "setup" && STEPS[U.step] === "ai") St.render();
    if (S.view.name === "settings" && S.mode === "app") St.renderView();
  }
  // the AI being set up: the one picked in the setup, or the one chosen in Settings
  const aiKey = () => (S.mode === "setup" ? U.ch.ai : ((S.boot || {}).app || {}).ai_provider);
  function pollAi(on) {
    clearInterval(U.timer);
    U.timer = null;
    if (!on) return;
    U.timer = setInterval(() => {           // catches the sign-in finishing in the browser, an install, Git appearing
      const key = aiKey();
      const inSetup = S.mode === "setup" && STEPS[U.step] === "ai";
      if (!inSetup && !(S.mode === "app" && S.view.name === "settings")) { pollAi(false); return; }
      if (!VENDOR[key] || (U.ai && U.ai[key] && aiReady(key, U.ai[key]))) return;
      // in Settings only while something is under way (a sign-in or an install), so other settings aren't disturbed
      const a = (U.ai || {})[key] || {};
      const busy = U.busy["login:" + key] || (a.job && a.job.state === "running") || (a.sign_in || {}).state === "waiting";
      if (inSetup || busy) loadAi(key);
    }, 4000);
  }

  function go(n) {
    U.step = Math.max(0, Math.min(STEPS.length - 1, n));
    pollAi(STEPS[U.step] === "ai");
    if (STEPS[U.step] === "download") loadPlan();
    St.render();
    const box = $(".su-body"); if (box) box.scrollTop = 0;
  }
  async function loadPlan() {
    U.plan = null; St.render();
    const r = await call("setup_plan", U.ch.speech, U.ch.ai === "local" ? U.ch.llm : "");
    U.plan = r.ok ? r : { items: [], total_mb: 0, blockers: [r.error], free_gb: 0 };
    U.done = U.plan.items.length === 0;
    St.render();
  }

  // ------------------------------------------------------------------ steps
  const pcRows = () => (U.st ? U.st.verdict.summary : []).map((x) => `<div class="su-row ${x.ok}"><span class="su-ico">${I(x.ok === "yes" ? "check" : x.ok === "warn" ? "alert" : "x", "sm")}</span>
      <b>${esc(x.label)}</b><span>${esc(x.value)}</span></div>`).join("");
  const speedHint = (id, gpu) => {
    const f = gpu ? { "large-v3-turbo": 22, "large-v3": 12, medium: 16, small: 25 }[id] : { "large-v3-turbo": 1, "large-v3": 0.4, medium: 0.2, small: 2 }[id];     // measured on a laptop CPU (small 15–30 min, medium 4 h+)
    const min = Math.round(60 / (f || 1));
    if (min <= 1) return t("about a minute per lecture hour");
    if (min < 60) return t("about {m} min per lecture hour", { m: min });
    return t("about {h} h per lecture hour", { h: tenths(Math.round(min / 6) / 10) });       // 2.5 h, 5 h
  };
  function stepWelcome() {
    return `<div class="su-lang">${St.langSeg ? St.langSeg() : ""}</div>
      <div class="su-hero"><img class="su-logo" src="../assets/logo.svg" alt=""><div><h1>${t("Welcome to IEUStudia")}</h1>
        <p class="lead">${t("Your IEULearn courses, Panopto lectures, materials and announcements in one place. IEUStudia turns the lectures into text on this PC (English with Turkish passages) and picks out what the lecturer says about quizzes, exams and homework. AI help is optional and uses your own ChatGPT or Claude plan.")}</p></div></div>
      <div class="card su-notice"><h3>${I("shield", "sm")} ${t("Before you start")}</h3>${NOTICE()}
        <label class="su-accept"><input type="checkbox" class="check" data-act="su-accept" ${U.accepted ? "checked" : ""}> ${t("I have read this and want to continue")}</label></div>`;
  }
  function stepPc() {
    if (!U.st) return U.error ? `<div class="callout error">${I("x-circle")}<div>${esc(U.error)}</div></div>` : St.skeleton();
    const v = U.st.verdict;
    return `<h2>${t("This PC")}</h2><p class="lead">${t("IEUStudia checked whether this computer can run it.")}</p>
      <div class="card su-rows">${pcRows()}</div>
      ${v.blockers.length ? `<div class="callout error su-block">${I("x-circle")}<div><b>${t("IEUStudia cannot run on this PC.")}</b><ul>${v.blockers.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>
        <div class="dim">${window.LS_LINUX ? t("Nothing was installed or changed. You can close IEUStudia.") : t("Nothing was installed or changed. You can close IEUStudia and uninstall it from Windows Settings → Apps.")}</div></div></div>` : ""}
      ${v.warnings.length ? `<div class="callout warning">${I("alert")}<div><ul>${v.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div></div>` : ""}
      ${!v.blockers.length ? `<div class="callout info">${I("check-circle")}<div>${v.gpu ? t("Your NVIDIA graphics card will transcribe lectures quickly.") : t("Everything works; transcription runs on the processor, so it is slower.")}</div></div>` : ""}`;
  }
  function stepSpeech() {
    const v = U.st.verdict, gpu = v.gpu;
    const cards = U.st.catalog.speech.map((m) => {
      const ok = (v.models[m.id] || {}).ok !== false;
      return `<button class="preset su-pick ${U.ch.speech === m.id ? "on" : ""}" data-act="su-speech" data-id="${m.id}" ${ok ? "" : "disabled"}>
        <div class="n">${esc(m.label)} ${m.id === v.recommended_model ? `<span class="pill now">${t("Recommended")}</span>` : ""}<span class="sz">${gb(m.size_mb)}${m.ready ? ` · ${t("on this PC")}` : ""}</span></div>
        <div class="d">${esc(m.description)}</div><div class="d su-speed">${ok ? `${I("clock", "xs")} ${speedHint(m.id, gpu)}` : esc(v.models[m.id].why)}</div></button>`;
    }).join("");
    return `<h2>${t("Transcription")}</h2><p class="lead">${t("A speech recognition model turns your lectures into text, right here on this PC. The Recommended one suits this PC. It is downloaded once, and you can change it later in Settings.")}</p>
      <div class="presets su-presets">${cards}</div>
      ${gpu && !U.st.catalog.cuda.ready ? `<div class="callout info">${I("cpu")}<div>${t("To use your NVIDIA graphics card, IEUStudia also downloads the support files it needs ({size}, from NVIDIA's official CUDA package).", { size: gb(U.st.catalog.cuda.size_mb) })}</div></div>` : ""}
      <div class="set-row"><div class="txt"><div class="t">${t("Save transcripts and notes in")}</div><div class="d"><span class="path">${esc(S.settings.output_dir || U.st.output_dir)}</span></div></div>
        <button class="btn sm" data-act="pick-folder">${t("Change…")}</button></div>`;
  }
  function aiCard(id, title, sub, body, off = "") {     // off: why this choice can't be used on this PC
    return `<div class="su-ai ${U.ch.ai === id ? "on" : ""} ${off ? "off" : ""}"><button class="su-ai-head" data-act="su-ai" data-id="${id}" ${off ? "disabled" : ""}><span class="su-radio"></span><div><b>${title}</b><div class="d">${off || sub}</div></div></button>
      ${U.ch.ai === id && body && !off ? `<div class="su-ai-body">${body}</div>` : ""}</div>`;
  }
  // ------------------------------------------------------------------ connecting ChatGPT or Claude, one step at a time
  // the texts are getters, read when drawn (in the language of the moment); each is a whole sentence, so the maker's
  // name sits inside it (Anthropic'in, OpenAI'ın) instead of being filled in
  const VENDOR = {
    chatgpt: { name: "ChatGPT",
      get intro() { return t("You sign in on OpenAI's own page and allow IEUStudia to use your ChatGPT plan. Plus and Pro can be used; for Free and Go, OpenAI decides, and the test message shows it. There's nothing to install, and IEUStudia never sees your password. In ChatGPT's settings you can see what it uses, set a limit, or disconnect it."); } },
    claude: { name: "Claude", tool: "Claude Code", upgrade: "https://claude.ai/upgrade",
      get intro() { return t("IEUStudia connects to Claude through <b>Claude Code</b>, a free program from Anthropic that IEUStudia can install for you. It works with your Claude Pro or Max subscription. It isn't the Claude desktop app, and you never have to open it yourself."); },
      get install() { return t("Install Claude Code"); },
      get source() { return t("Free, installed from Anthropic's official source."); },
      get signinText() { return t("Opens Anthropic's sign-in page in your browser. IEUStudia never sees your password."); },
      get needs() { return t("Needs Claude Pro or Max."); },
      get manual() { return [[t("Install Claude Code (in PowerShell)"), "irm https://claude.ai/install.ps1 | iex"], [t("Sign in"), "claude auth login"]]; } },
    codex: { name: "ChatGPT", tool: "Codex CLI", upgrade: "https://chatgpt.com/pricing",
      get intro() { return t("IEUStudia talks to ChatGPT through OpenAI's <b>Codex CLI</b>, signed in with your ChatGPT account. Pick this if you already use the Codex CLI; IEUStudia can also install it for you. Otherwise plain ChatGPT is simpler, with nothing to install."); },
      get install() { return t("Install Codex CLI"); },
      get source() { return t("Free, installed from OpenAI's official source."); },
      get signinText() { return t("Opens OpenAI's sign-in page in your browser. IEUStudia never sees your password."); },
      get needs() { return t("Needs a ChatGPT plan with Codex."); },
      get manual() { return [[t("Install the Codex CLI (needs Node.js)"), "npm install -g @openai/codex"], [t("Sign in"), "codex login"]]; } },
  };
  // ChatGPT: "Continue with ChatGPT" (chatgpt.py), nothing to install
  function chatgptSteps(a) {
    const si = a.sign_in || {}, test = a.test || {}, ready = a.signed_in && a.plan_allowed;
    const waiting = si.state === "waiting" || (U.busy["login:chatgpt"] && !a.signed_in && si.state !== "error");
    const noPlanUse = a.signed_in && !a.plan_allowed;
    const notEligible = test.ok === false && test.code === "plan_not_eligible";
    return [
      { id: "signin", ok: ready, title: t("Continue with ChatGPT"), okText: a.email ? t.html("Signed in as {email}", { email: a.email }) : t("Signed in"),
        text: t("Opens OpenAI's sign-in page in your browser. Sign in, then allow IEUStudia to use your ChatGPT plan."),
        busy: waiting && !noPlanUse ? t("Finish on the page that opened in your browser. This list notices when you're done.") : "",
        error: si.state === "error" ? esc(si.error) : noPlanUse ? t("You're signed in, but IEUStudia may not use your ChatGPT plan yet. Click Allow it and accept on OpenAI's page.") : "",
        btn: noPlanUse ? `<button class="btn sm" data-act="ai-login" data-id="chatgpt" data-consent="1">${t("Allow it")}</button>`
          : `<button class="btn sm" data-act="ai-login" data-id="chatgpt">${t("Continue with ChatGPT")}</button>` },
      { id: "plan", ok: test.ok === true, title: t.c("subscription", "Your plan"), okText: t("It works with your plan"),
        text: t("Plus and Pro can be used. For Free and Go, OpenAI decides."), error: notEligible ? esc(test.error) : "",
        unknown: (ready && test.ok !== true && !notEligible) ? t("The test message shows whether your plan can be used in apps.") : "",
        btn: notEligible ? `<button class="btn sm" data-act="url" data-url="https://chatgpt.com/pricing">${I("external", "sm")} ${t("See plans")}</button>` : "" },
      { id: "test", ok: test.ok === true, title: t("Test message"), okText: t("{name} answered in {s} s", { name: "ChatGPT", s: tenths(test.seconds) }),
        text: t("One short message to check that everything works. It uses a little of your plan."),
        busy: U.busy["test:chatgpt"] ? t("Sending a short test message…") : "", error: test.ok === false && !notEligible ? esc(test.error) : "",
        btn: `<button class="btn sm" data-act="ai-test" data-id="chatgpt">${test.ok === false ? t("Try again") : t("Send a test message")}</button>` },
    ];
  }
  // The checklist for one AI: each step is done, busy, failed, unknown, the one to do now, or later.
  function connectSteps(key, a) {
    if (key === "chatgpt") return chatgptSteps(a);
    const v = VENDOR[key], out = [];
    const running = (j) => j && j.state === "running" ? (j.line || t("Installing…")) : "";
    const failed = (j) => j && j.state === "error" ? j.error : "";
    out.push({ id: "tool", ok: a.available, title: v.tool, okText: t.html("Version {version}", { version: a.version }),
      text: v.source, busy: running(a.job), error: failed(a.job),
      btn: `<button class="btn sm" data-act="ai-install" data-id="${key}">${I("download", "sm")} ${v.install}</button>` });
    out.push({ id: "signin", ok: a.logged_in, title: t("Sign in to {name}", { name: v.name }), okText: a.email ? t.html("Signed in as {email}", { email: a.email }) : t("Signed in"),
      text: v.signinText,
      busy: U.busy["login:" + key] && !a.logged_in ? t("Finish signing in on the page that opened in your browser. A small {tool} window opens too and closes by itself when you're done; this list notices it.", { tool: v.tool }) : "",
      btn: `<button class="btn sm" data-act="ai-login" data-id="${key}">${t("Sign in with {name}", { name: v.name })}</button>` });
    const ps = a.plan_status || {}, test = a.test || {};
    out.push({ id: "plan", ok: ps.ok === true || (ps.ok == null && a.logged_in && test.ok === true), title: t.c("subscription", "Your plan"),
      okText: ps.label ? esc(ps.label) + (ps.note ? `. ${esc(ps.note)}` : "") : t("It works with your plan"),
      text: v.needs, error: ps.ok === false ? esc(ps.note) : "", unknown: ps.ok == null && a.logged_in && test.ok !== true ? esc(ps.note || "") : "",
      btn: ps.ok === false ? `<button class="btn sm" data-act="url" data-url="${v.upgrade}">${I("external", "sm")} ${t("See plans")}</button>` : "" });
    out.push({ id: "test", ok: test.ok === true, title: t("Test message"), okText: t("{name} answered in {s} s", { name: v.name, s: tenths(test.seconds) }),
      text: t("One short message to check that everything works. It uses a little of your plan."),
      busy: U.busy["test:" + key] ? t("Sending a short test message…") : "", error: test.ok === false ? esc(test.error) : "",
      btn: `<button class="btn sm" data-act="ai-test" data-id="${key}">${test.ok === false ? t("Try again") : t("Send a test message")}</button>` });
    return out;
  }
  const aiReady = (key, a) => connectSteps(key, a).every((s) => s.ok);
  // installed and signed in: Ask, notes and plans work (the plan and the test message only confirm it)
  const aiSignedIn = (key, a) => connectSteps(key, a).filter((s) => s.id !== "test" && s.id !== "plan").every((s) => s.ok);
  function connectHTML(key) {
    const a = U.ai && U.ai[key], v = VENDOR[key];
    if (!a) return `<div class="dim">${SPIN()} ${t("Checking this PC…")}</div>`;
    const steps = connectSteps(key, a);
    const now = steps.findIndex((s) => !s.ok && !s.unknown);
    const rows = steps.map((s, i) => {
      const st = s.ok ? "ok" : s.busy ? "busy" : s.error ? "err" : s.unknown ? "unknown" : i === now ? "now" : "later";
      const ico = st === "ok" ? I("check", "sm") : st === "busy" ? SPIN() : st === "err" ? I("x", "sm") : st === "unknown" ? "?" : i + 1;
      const detail = st === "ok" ? s.okText : st === "busy" ? esc(s.busy) : st === "err" ? s.error : st === "unknown" ? s.unknown : s.text;
      const btn = (st === "now" || st === "err") && s.btn ? s.btn : "";
      return `<div class="cn-step ${st}"><span class="cn-ico">${ico}</span><div class="grow"><b>${s.title}</b><div class="d">${detail}</div>
        ${st === "now" && s.hint ? `<div class="d dim">${esc(s.hint)}</div>` : ""}</div>${btn}</div>`;
    }).join("");
    const ready = steps.every((s) => s.ok);
    return `<p class="cn-intro">${v.intro}</p><div class="cn-steps">${rows}</div>
      ${ready ? `<div class="callout success">${I("check-circle")}<div>${t.html("<b>{name} is ready.</b> AI features in IEUStudia will use your {name} plan.", { name: v.name })}</div></div>`
        : `<div class="dim su-note">${I("info", "xs")} ${t("You can also continue now and finish this later in Settings → AI.")}</div>`}
      ${key === "chatgpt" && a.signed_in && S.mode === "app" ? `<div class="dim su-note">${I("lock", "xs")} ${t("Signed in with ChatGPT.")} <button class="link" data-act="ai-signout" data-id="chatgpt">${t("Sign out")}</button> · <button class="link" data-act="url" data-url="https://chatgpt.com/settings/usage">${t("Usage in ChatGPT's settings")}</button></div>` : ""}
      ${v.manual ? `<details class="cn-manual"><summary>${t("Rather do it yourself?")}</summary>
        <div class="dim">${t("Run these in PowerShell, then come back: this list updates by itself.")}</div>
        ${v.manual.map(([l, cmd]) => `<div class="cn-cmd"><span class="dim">${esc(l)}</span><code>${esc(cmd)}</code><button class="btn ghost sm" data-act="ai-copy" data-text="${esc(cmd)}">${I("copy", "sm")}</button></div>`).join("")}</details>` : ""}`;
  }
  async function runTest(key) {
    U.busy["test:" + key] = true; rerenderAi();
    const r = await call("ai_test", key);
    delete U.busy["test:" + key];
    if (U.ai && U.ai[key]) U.ai[key].test = r.ok ? r.test : { ok: false, error: r.error, seconds: 0 };
    rerenderAi();
  }
  function autoTest() {               // once everything else is done, check it really works (once per session)
    const key = aiKey();
    const a = U.ai && U.ai[key];
    if (!a || !VENDOR[key] || U.autoTested[key] || U.busy["test:" + key] || (a.test && a.test.at)) return;
    const before = connectSteps(key, a).filter((s) => s.id !== "test");
    if (before.every((s) => s.ok || s.unknown)) { U.autoTested[key] = true; runTest(key); }
  }
  function stepAi() {
    const loc = (U.ai && U.ai.local && U.ai.local.models) || [];
    const rec = U.st.verdict.recommended_llm;
    const llmBody = rec ? `<div class="presets">${loc.map((m) => `<button class="preset ${U.ch.llm === m.id ? "on" : ""}" data-act="su-llm" data-id="${m.id}" ${m.id === "qwen3-4b" && rec !== "qwen3-4b" ? "disabled" : ""}>
        <div class="n">${esc(m.label)} ${m.id === rec ? `<span class="pill now">${t("Fits this PC")}</span>` : ""}<span class="sz">${gb(m.size_mb)}${m.ready ? ` · ${t("on this PC")}` : ""}</span></div><div class="d">${esc(m.description)}</div></button>`).join("")}</div>
        <div class="dim su-note">${t("Runs entirely on this PC. Fine for short notes and simple questions; study plans, review sheets and study packs need ChatGPT or Claude.")}${U.st.verdict.gpu ? "" : " " + t("Without an NVIDIA graphics card it writes slowly (notes for a lecture take several minutes).")}</div>`
      : `<div class="callout warning">${I("alert")}<div>${t("This PC has too little memory for an offline model (8 GB needed).")}</div></div>`;
    return `<h2>${t("AI help (optional)")}</h2><p class="lead">${t("Study notes, chat about your lectures, video study plans, exam review sheets and study packs. Pick what you have — everything else in IEUStudia works without AI.")}</p>
      ${aiCard("chatgpt", "ChatGPT", t("Uses your ChatGPT plan. Plus and Pro work; Free and Go may, with smaller limits."), connectHTML("chatgpt"))}
      ${aiCard("claude", "Claude", t("Needs a Claude Pro or Max subscription."), connectHTML("claude"))}
      ${aiCard("codex", t("ChatGPT through the Codex CLI"), t("Pick this only if you already use OpenAI's Codex CLI (a tool for programmers) with your ChatGPT plan. Otherwise choose ChatGPT above."), connectHTML("codex"))}
      ${aiCard("local", t("Offline model"), t("No subscription: a small model on this PC — basic notes and questions."), llmBody,
        rec || llmHere(U.st, U.ch.llm) ? "" : t("Not on this PC: it needs 8 GB of memory."))}
      ${aiCard("none", t("No AI"), t("Transcripts, heads-ups, search, calendar and everything else still work."), "")}
      <div class="callout info su-ai-note">${I("info")}<div>${t("<b>AI can make mistakes.</b> Answers are generated from your lectures, but check anything important against the lecture and your materials. Nothing is sent anywhere unless you use an AI feature.")}</div></div>`;
  }
  function stepBackground() {
    const sw = (key, label, d, dis) => `<div class="set-row"><div class="txt"><div class="t">${label}</div><div class="d">${d}</div></div>
      <input type="checkbox" class="switch" data-act="su-bool" data-key="${key}" ${U.ch[key] ? "checked" : ""} ${dis ? "disabled" : ""}></div>`;
    return `<h2>${t("In the background")}</h2><p class="lead">${window.LS_LINUX ? t("IEUStudia can keep checking IEULearn after you close its window and tell you when something new appears. Its notifications then come from your desktop (there is no icon next to the clock on Linux).") : t("IEUStudia can keep checking IEULearn after you close its window and tell you when something new appears. Its icon then stays at the bottom right of the screen, next to the clock (or under the ^ arrow there).")}</p>
      <div class="card set-card">${sw("tray", t("Keep checking after I close IEUStudia"), t("Uses about 50 MB of memory. Checks IEULearn every 30 minutes. It never transcribes or downloads anything on its own."))}
        ${sw("startup", window.LS_LINUX ? t("Start checking when I log in") : t("Start checking when I sign in to Windows"), t("So notifications arrive without opening IEUStudia first."), !U.ch.tray)}</div>
      <div class="card set-card"><h3>${t("Notify me about")}</h3>
        ${sw("notify_recordings", t("New lecture recordings"), t("Of your own section."), !U.ch.tray)}
        ${sw("notify_announcements", t("New announcements"), t("Quiz, exam and homework news is marked as a heads-up."), !U.ch.tray)}
        ${sw("notify_materials", t("New files"), t("Slides and PDFs added to a course."), !U.ch.tray)}
        ${sw("notify_reminders", t("Quizzes and exams coming up"), t("The evening before an announced quiz, and before an exam week. Also while IEUStudia is open."))}</div>
      <div class="dim su-note">${I("info", "xs")} ${window.LS_LINUX ? t("You can change all of this later in Settings.") : t("You can change all of this later in Settings, or by right-clicking the IEUStudia icon next to the clock.")}</div>`;
  }
  function dlRows(items) {
    return items.map((it) => {
      const d = U.dl[it.id] || {};
      const pct = d.total ? Math.round((100 * d.done) / d.total) : d.state === "done" ? 100 : 0;
      const state = d.state === "done" ? `<span class="pill done">${I("check")}${t("Done")}</span>` : d.state === "error" ? `<span class="pill failed">${t("Failed")}</span>`
        : d.state === "downloading" ? `<span class="dim">${d.total ? t("{done} of {total}", { done: bytes(d.done), total: bytes(d.total) }) : t("Starting…")}</span>` : `<span class="dim">${gb(it.size_mb)}</span>`;
      return `<div class="su-dl"><div class="grow"><b>${esc(it.label)}</b>${d.error ? `<div class="err-txt">${esc(d.error)}</div>` : ""}<div class="bar"><i style="width:${pct}%"></i></div></div>${state}</div>`;
    }).join("");
  }
  function stepDownload() {
    const p = U.plan;
    if (!p) return `<h2>${t.c("noun", "Download")}</h2>${St.skeleton()}`;
    const rows = dlRows(p.items);
    const total = p.total_mb;
    return `<h2>${t.c("noun", "Download")}</h2><p class="lead">${!p.items.length ? t("Everything IEUStudia needs is already on this PC.")
      : U.running ? t("You can go on and sign in: the download continues in the background, and a transcription waits for it to finish.")
      : t("These are downloaded once now, so IEUStudia works right away.")}</p>
      ${p.items.length ? `<div class="card su-dls">${rows}</div>` : ""}
      <div class="card su-space"><h3>${I("hard-drive", "sm")} ${t("Disk space")}</h3>
        <div class="su-row"><span class="grow">${t("Downloads now")}</span><b>${gb(total)}</b></div>
        <div class="su-row"><span class="grow">${t("Each transcribed lecture hour (text, slides from the screen recording)")}</span><b>≈ 5–30 MB</b></div>
        <div class="su-row"><span class="grow">${t("Course files you download")}</span><b>${t("as large as the files")}</b></div>
        <div class="su-row"><span class="grow">${t.html("Free on {drive}", { drive: (U.st.facts.disk.data_dir || "C:").slice(0, 3) })}</span><b>${t.num(p.free_gb, 0, false)} GB</b></div></div>
      ${(p.blockers || []).length ? `<div class="callout error">${I("x-circle")}<div>${p.blockers.map(esc).join("<br>")}</div></div>` : ""}`;
  }
  function stepSignin() {
    const ai = { chatgpt: "ChatGPT", claude: "Claude", codex: "ChatGPT (Codex CLI)", local: t("Offline model"), none: t("No AI"), "": t("No AI") }[U.ch.ai];
    const notYet = VENDOR[U.ch.ai] && !(U.ai && U.ai[U.ch.ai] && aiSignedIn(U.ch.ai, U.ai[U.ch.ai]));
    return `<h2>${t("Almost done")}</h2><p class="lead">${S.boot && S.boot.signed_in ? t.html("You are signed in as {name}. Finish to open IEUStudia.", { name: (S.profile || {}).name || "" }) : t("Last step: sign in with your IEULearn (EkoID) account.")}</p>
      <div class="card su-summary">${[[t("Transcription"), (U.st.catalog.speech.find((m) => m.id === U.ch.speech) || {}).label || U.ch.speech],
        [t("AI"), notYet ? t("{name} — not connected yet (Settings → AI)", { name: ai }) : ai],
        [t("Background checks"), !U.ch.tray ? t("Off") : U.ch.startup ? (window.LS_LINUX ? t("On, starts at login") : t("On, starts with Windows")) : t("On")]].map(([k, v]) => `<div class="su-row"><span class="grow">${k}</span><b>${esc(v)}</b></div>`).join("")}</div>`;
  }

  // ------------------------------------------------------------------ frame
  function canNext() {
    const k = STEPS[U.step];
    if (k === "welcome") return true;            // pressing it without the tick points at the tick
    if (k === "pc") return U.st && !U.st.verdict.blockers.length;
    if (k === "speech") return !!U.ch.speech;
    if (k === "ai") return true;
    if (k === "download") return (U.done || U.running) && !(U.plan && (U.plan.blockers || []).length);
    return true;
  }
  St.setupHTML = () => {
    const k = STEPS[U.step];
    const body = !U.st && k !== "welcome" ? stepPc() : { welcome: stepWelcome, pc: stepPc, speech: stepSpeech, ai: stepAi, background: stepBackground, download: stepDownload, signin: stepSignin }[k]();
    const dl = k === "download" && U.plan && U.plan.items.length && !U.done;
    const next = k === "signin" ? `<button class="btn primary" data-act="su-finish">${S.boot && S.boot.signed_in ? t("Open IEUStudia") : t("Continue to sign in")} ${I("arrow-right", "sm")}</button>`
      : dl && U.running ? `<button class="btn primary" data-act="su-next">${SPIN()} ${t("Continue while it downloads")} ${I("arrow-right", "sm")}</button>`
      : dl ? `<button class="btn primary" data-act="su-download" ${(U.plan.blockers || []).length ? "disabled" : ""}>${
        Object.values(U.dl).some((d) => d.state === "error") ? `${I("retry", "sm")} ${t("Try again")}` : `${I("download", "sm")} ${t("Download {size}", { size: gb(U.plan.total_mb) })}`}</button>`
        : `<button class="btn primary" data-act="su-next" ${canNext() ? "" : "disabled"}>${t("Continue")} ${I("arrow-right", "sm")}</button>`;
    return `<div class="setup"><aside class="su-rail"><div class="brand"><img class="logo-img" src="../assets/logo.svg" alt="">IEUStudia</div>
        <ol>${STEPS.map((key, i) => `<li class="${i === U.step ? "on" : i < U.step ? "done" : ""}"><span>${i < U.step ? I("check", "xs") : i + 1}</span>${stepName(key)}</li>`).join("")}</ol>
        <div class="su-rail-foot">${I("lock", "xs")} ${t("No account with us, no tracking.")}</div></aside>
      <main class="su-main"><div class="su-body">${body}</div>
        <div class="su-foot">${U.step > 0 && !(U.running && k === "download") ? `<button class="btn ghost" data-act="su-back">${I("arrow-left", "sm")} ${t("Back")}</button>` : `<span></span>`}
          ${U.running && k === "download" ? `<button class="btn ghost" data-act="su-cancel">${t("Cancel download")}</button>` : ""}${next}</div></main></div>`;
  };

  // ------------------------------------------------------------------ actions
  Object.assign(A, {
    "su-accept": (el) => {     // kept at once: closing the window during the setup does not undo the tick
      U.accepted = el.checked; St.render();
      call("app_config", { terms_accepted: el.checked ? new Date().toISOString().slice(0, 10) : "" });
    },
    "su-next": async (el) => {
      const k = STEPS[U.step];
      if (k === "welcome" && !U.accepted) {
        const box = $(".su-accept");
        if (box) { box.classList.remove("attention"); void box.offsetWidth; box.classList.add("attention"); box.scrollIntoView({ block: "center", behavior: "smooth" }); }
        toast(t("Tick “I have read this and want to continue” first."), "info");
        return;
      }
      if (k === "ai" && VENDOR[U.ch.ai] && !(el && el.dataset.anyway) && !(U.ai && U.ai[U.ch.ai] && aiSignedIn(U.ch.ai, U.ai[U.ch.ai]))) {
        const v = VENDOR[U.ch.ai];
        St.modal(`<h2>${t.html("{name} isn't connected yet", { name: v.name })}</h2>
          <p class="lead">${t("Until it is, chatting about your lectures, study notes and study plans won't work. Everything else does. You can connect it later in Settings → AI.")}</p>
          <div class="actions"><button class="btn" data-act="modal-close">${t("Connect it now")}</button><button class="btn primary" data-act="su-next" data-anyway="1">${t("Continue without it")} ${I("arrow-right", "sm")}</button></div>`);
        return;
      }
      if (el && el.dataset.anyway) St.closeModal();
      if (k === "speech") { await call("update_settings", { model: U.ch.speech }); S.settings.model = U.ch.speech; }
      if (k === "ai") {
        const r = await call("app_config", { ai_provider: U.ch.ai || "none", local_model: U.ch.llm });
        if (!r.ok) { toast(r.error, "error"); return; }
      }
      if (k === "background") {
        const r = await call("app_config", { tray: U.ch.tray, start_with_windows: U.ch.tray && U.ch.startup, notify_recordings: U.ch.notify_recordings,
          notify_announcements: U.ch.notify_announcements, notify_materials: U.ch.notify_materials, notify_reminders: U.ch.notify_reminders });
        if (!r.ok) { toast(r.error, "error"); return; }
      }
      go(U.step + 1);
    },
    "su-back": () => go(U.step - 1),
    "su-speech": (el) => { U.ch.speech = el.dataset.id; St.render(); },
    "su-llm": (el) => { U.ch.llm = el.dataset.id; St.render(); },
    "su-ai": (el) => { U.ch.ai = el.dataset.id; St.render(); if (VENDOR[U.ch.ai]) { loadAi(U.ch.ai); pollAi(true); } },
    "su-bool": (el) => { U.ch[el.dataset.key] = el.checked; if (el.dataset.key === "tray" && !el.checked) U.ch.startup = false; St.render(); },
    "su-download": async () => {
      U.running = true; U.dl = {}; St.render();
      const r = await call("setup_download", U.plan.items.map((x) => x.id));
      if (!r.ok) { U.running = false; toast(r.error, "error"); St.render(); }
    },
    "su-cancel": () => call("setup_cancel"),
    "su-finish": async () => {
      const r = await call("finish_setup");
      if (!r.ok) { toast(r.error, "error"); return; }
      if (U.running) toast(t("The download goes on in the background: see Downloads at the top."), "info");
      pollAi(false);
      S.boot.app = r.app;
      if (S.boot.signed_in) { S.mode = "app"; St.render(); St.afterBoot(); }
      else { S.mode = "signin"; St.render(); }
    },
    "setup-again": () => St.startSetup(),
  });
  // checkbox inputs of the setup (app.js lets these reach their handlers)
  St.inputActs = (St.inputActs || []).concat(["su-accept", "su-bool"]);

  // installing Claude Code or the Codex CLI (ai_setup.py)
  St.onEvents.ai_setup = (e) => {
    const key = e.which;
    if (U.ai && U.ai[key]) U.ai[key].job = e;
    if (e.state === "error") toast(e.error, "error");
    if (e.state !== "running") { if (U.ai && U.ai[key]) delete U.ai[key].test; U.autoTested[key] = false; loadAi(key); }
    rerenderAi();
  };
  // "Continue with ChatGPT" finished in the browser (chatgpt.py)
  St.onEvents.chatgpt = (e) => {
    delete U.busy["login:chatgpt"];
    if (e.state === "error") toast(e.error, "error");
    if (U.ai && U.ai.chatgpt) U.ai.chatgpt.sign_in = { state: e.state, error: e.error || "" };
    U.autoTested.chatgpt = false;
    loadAi("chatgpt");
    rerenderAi();
  };
  Object.assign(A, {
    "ai-install": async (el) => {
      const which = el.dataset.id;
      const r = await call("ai_install", which);
      if (!r.ok) { toast(r.error, "error"); return; }
      if (U.ai && U.ai[which]) U.ai[which].job = r.job;
      pollAi(true); rerenderAi();
    },
    "ai-login": async (el) => {
      const key = el.dataset.id;
      const r = await call("ai_login", key, !!el.dataset.consent);
      if (!r.ok) { toast(r.error, "error"); return; }
      U.busy["login:" + key] = Date.now(); U.autoTested[key] = false;
      if (U.ai && U.ai[key]) {
        delete U.ai[key].test;
        if (key === "chatgpt") U.ai.chatgpt.sign_in = { state: "waiting", error: "" };
      }
      setTimeout(() => { if (U.busy["login:" + key]) { delete U.busy["login:" + key]; rerenderAi(); } }, 10 * 60000);
      pollAi(true); rerenderAi();
    },
    "ai-signout": async (el) => {
      if (!confirm(t("Sign out of ChatGPT in IEUStudia? OpenAI ends the session; you can sign in again any time."))) return;
      const r = await call("ai_sign_out", el.dataset.id);
      if (!r.ok) { toast(r.error, "error"); return; }
      toast(r.revoked ? t("Signed out of ChatGPT.") : t("Signed out here. OpenAI didn't confirm it, so also disconnect IEUStudia in ChatGPT's settings."), r.revoked ? "success" : "info");
      U.autoTested.chatgpt = false; loadAi("chatgpt");
    },
    "ai-test": (el) => runTest(el.dataset.id),
    "ai-copy": async (el) => { const r = await call("copy_text", el.dataset.text); toast(r.ok ? t("Copied") : r.error, r.ok ? "success" : "error"); },
  });

  St.onEvents.setup = (e) => {
    const tick = e.state === "downloading" && U.running;          // progress of a download already drawn (about 3 a second)
    if (e.id) U.dl[e.id] = { ...(U.dl[e.id] || {}), error: "", ...e };      // only an "error" event carries one
    if (e.state === "downloading") U.running = true;
    if (e.state === "idle") {
      U.running = false;
      // the setup's plan; or, when the page started during the download (no plan), what the chip and Downloads list
      U.done = !!e.ok && (U.plan ? U.plan.items : dlItems()).every((it) => (U.dl[it.id] || {}).state === "done");
      if (U.done && S.boot) S.boot.pending_downloads = [];
      if (!e.ok && Object.values(U.dl).some((d) => d.state === "error")) toast(S.mode === "setup" ? t("A download stopped — see the reason above the button, then try again.") : t("A download stopped. Click Downloads at the top to see why and try again."), "error");
      else if (U.done && S.mode === "app") toast(t("Everything IEUStudia needs is downloaded."), "success");
    }
    // a tick changes only the numbers: drawing the pages again would close an open list, take the focus and lose
    // a click that lands in between. The rows hold no buttons.
    if (tick) {
      document.querySelectorAll(".su-dls").forEach((el) => { el.innerHTML = dlRows(dlItems()); });   // the setup's step, the Downloads dialog
      const label = document.querySelector(".dl-chip .dl-label");
      if (label) label.textContent = dlLabel();
      else if (S.mode === "app") St.renderTop();
      return;
    }
    if (S.mode === "setup") St.render();
    else {
      St.renderTop();
      if (document.querySelector(".modal.dl")) dlModal();
      if (S.view.name === "settings") St.renderView();
    }
  };

  // the top bar while the setup's downloads go on (or after they stopped, or were cut off by closing IEUStudia)
  function dlItems() {
    const pending = (S.boot || {}).pending_downloads || [];
    return U.plan && U.plan.items.length ? U.plan.items : pending;
  }
  function dlLabel() {               // the chip's words (a progress tick writes only these)
    const items = dlItems();
    if (!U.running) return items.some((it) => (U.dl[it.id] || {}).state === "error") ? t("Download stopped") : t("Downloads not finished");
    const total = items.reduce((n, it) => n + it.size_mb * 1048576, 0);
    const done = items.reduce((n, it) => { const d = U.dl[it.id] || {}; return n + (d.state === "done" ? it.size_mb * 1048576 : d.done || 0); }, 0);
    return t("Downloading {done} of {total}", { done: bytes(done), total: bytes(total) });
  }
  St.downloadChip = () => {
    const items = dlItems();
    const failed = items.some((it) => (U.dl[it.id] || {}).state === "error");
    const left = items.filter((it) => (U.dl[it.id] || {}).state !== "done");
    if (!U.running && (U.done || !left.length)) return "";
    return `<button class="chip dl-chip ${failed && !U.running ? "warn" : ""}" data-act="dl-open" title="${esc(t("What IEUStudia still downloads, and how far it is"))}">${U.running ? SPIN() : I(failed ? "alert" : "download", "xs")} <span class="dl-label">${dlLabel()}</span></button>`;
  };
  function dlModal() {
    const items = dlItems();
    // finished while the dialog was open (all done, also when the page started during the download)
    const done = !U.running && (U.done || (items.length > 0 && items.every((it) => (U.dl[it.id] || {}).state === "done")));
    St.modal(`<h2>${I("download")} ${t("Downloads")}</h2>
      <p class="lead">${U.running ? t("What IEUStudia needs is downloading. You can keep using it; a transcription waits for the speech model.")
        : done ? t("Everything IEUStudia needs is downloaded.") : t("This download didn't finish. It goes on where it stopped.")}</p>
      ${items.length ? `<div class="card su-dls">${dlRows(items)}</div>` : ""}
      <div class="actions">${U.running ? `<button class="btn" data-act="su-cancel">${t("Cancel download")}</button><button class="btn primary" data-act="modal-close">${t("OK")}</button>`
        : done ? `<button class="btn primary" data-act="modal-close">${t("OK")}</button>`
        : `<button class="btn" data-act="modal-close">${t("Later")}</button><button class="btn primary" data-act="dl-resume">${I("download", "sm")} ${t("Download now")}</button>`}</div>`);
    document.querySelector(".modal").classList.add("dl");
  }
  Object.assign(A, {
    "dl-open": () => dlModal(),
    "dl-resume": async () => {
      const ids = dlItems().filter((it) => (U.dl[it.id] || {}).state !== "done").map((it) => it.id);
      if (!ids.length) { dlModal(); return; }                 // nothing left to download: the dialog says so
      if (!U.plan || !U.plan.items.length) U.plan = { items: dlItems(), total_mb: dlItems().reduce((n, it) => n + it.size_mb, 0), blockers: [] };
      ids.forEach((id) => delete U.dl[id]);                   // a new try: no old error or "Failed" under them
      U.running = true; St.renderTop(); dlModal();
      const r = await call("setup_download", ids);
      if (!r.ok) { U.running = false; toast(r.error, "error"); St.renderTop(); dlModal(); }
    },
  });

  // ------------------------------------------------------------------ Settings cards
  // the AI's status and storage() for Settings: one call at a time, however often Settings is drawn before it answers
  const loading = {};
  const loadOnce = (key, load) => { if (!loading[key]) { loading[key] = true; setTimeout(() => load().finally(() => { delete loading[key]; }), 0); } };
  St.settingsAI = () => {
    const app = (S.boot || {}).app || {};
    const a = U.ai;
    if (!a) loadOnce("ai", () => loadAi());
    const label = { chatgpt: t("ChatGPT (your ChatGPT plan)"), claude: t("Claude (Pro or Max plan)"), codex: t("ChatGPT through the Codex CLI"), local: t("Offline model on this PC"), none: t("No AI") };
    const p = app.ai_provider, x = (a && a[p]) || {};
    const status = p === "chatgpt" ? (x.signed_in && x.plan_allowed ? (x.email ? t("Signed in with ChatGPT as {email}.", { email: x.email }) : t("Signed in with ChatGPT.")) : t("Not connected yet."))
      : p === "claude" ? (x.logged_in ? (x.plan ? t("Claude Code {version} · signed in ({plan})", { version: x.version, plan: x.plan }) : t("Claude Code {version} · signed in", { version: x.version }))
        : x.available ? t("Claude Code is installed but not signed in.") : t("Claude Code is not installed."))
      : p === "codex" ? (x.logged_in ? t("Codex CLI {version} · signed in with ChatGPT", { version: x.version }) : x.available ? t("The Codex CLI is installed but not signed in.") : t("The Codex CLI is not installed."))
      : p === "local" ? ((x.models || []).find((m) => m.id === app.local_model) || {}).ready ? t("The offline model is on this PC.") : t("The offline model is not downloaded yet.") : t("AI is off, so the AI buttons are greyed out. To chat about your lectures or make study notes and plans, choose an AI service on the right.");
    const needs = a && a.local && p === "local" && !(a.local.models.find((m) => m.id === app.local_model) || {}).ready;
    const steps = VENDOR[p] && a && a[p];
    if (steps && !U.timer) pollAi(true);
    return `<div class="set-row"><div class="txt"><div class="t">${t("AI service")}</div><div class="d">${a ? esc(status) : `${SPIN()} ${t("Checking…")}`}</div></div>
        <select class="input" style="width:260px" data-act="ai-provider">${Object.entries(label).map(([v, l]) => `<option value="${v}" ${(p || "none") === v ? "selected" : ""}>${l}</option>`).join("")}</select></div>
      ${steps ? `<div class="set-connect">${connectHTML(p)}</div>` : ""}
      ${needs ? `<div class="set-row"><div class="txt"><div class="t">${t("Download the offline model")}</div><div class="d">${t("It is downloaded in the setup's AI step.")}</div></div><button class="btn sm" data-act="setup-ai">${I("arrow-right", "sm")} ${t("Set up AI")}</button></div>` : ""}
      ${(p === "chatgpt" || p === "codex") && (x.models || []).length ? `<div class="set-row"><div class="txt"><div class="t">${t("ChatGPT model")}</div><div class="d">${t("The default is usually right; pick another only if your plan offers it.")}</div></div>
        <select class="input" style="width:200px" data-act="codex-model"><option value="">${t("Default")}</option>${x.models.map((m) => `<option ${app.codex_model === m ? "selected" : ""}>${esc(m)}</option>`).join("")}</select></div>` : ""}
      <div class="callout info">${I("info")}<div>${t("<b>AI can make mistakes.</b> Check important details against your lectures and materials. AI only runs when you ask.")}</div></div>`;
  };
  St.settingsMore = () => {
    const app = (S.boot || {}).app || {};
    const row = (title, d, ctl) => `<div class="set-row"><div class="txt"><div class="t">${title}</div><div class="d">${d}</div></div>${ctl}</div>`;
    const sw = (key, dis) => `<input type="checkbox" class="switch" data-act="app-bool" data-key="${key}" ${app[key] ? "checked" : ""} ${dis ? "disabled" : ""}>`;
    const st = U.storage;
    if (!st) loadOnce("storage", loadStorage);
    const use = st ? Object.entries({ speech_models: t("Speech models"), gpu_libraries: t("NVIDIA graphics card files"), offline_ai: t("Offline AI model"), library: t("Library and transcripts"), cache: t("Temporary files"), your_files: t("Your files (IEUStudia folder)") })
      .filter(([k]) => st.usage[k]).map(([k, l]) => `<div class="su-row"><span class="grow">${l}</span><b>${bytes(st.usage[k])}</b></div>`).join("") : St.skeleton();
    const removable = st ? [...st.catalog.speech.filter((m) => m.ready && m.id !== S.settings.model).map((m) => [`speech:${m.id}`, t.html("Speech model “{name}”", { name: m.label }), m.size_mb]),
      ...st.catalog.llm.filter((m) => m.ready && !(app.ai_provider === "local" && app.local_model === m.id)).map((m) => [`llm:${m.id}`, t.html("Offline model “{name}”", { name: m.label }), m.size_mb])] : [];
    return `<div class="card set-card" id="set-bg"><h3>${t("Background and notifications")}</h3>
        ${app.tray ? "" : `<div class="callout info bg-hint">${I("info")}<div>${t("Background checks are off, so you get no notifications about new lectures, announcements or coming quizzes.")}</div>
          <button class="btn sm" data-act="bg-on">${t("Turn them on")}</button></div>`}
        ${row(t("Keep running in the background"), window.LS_LINUX ? t("After you close the window, IEUStudia keeps running in the background (about 50 MB of memory). It checks IEULearn every 30 minutes and shows notifications. It never transcribes or downloads anything on its own.") : t("After you close the window, IEUStudia stays as a small icon next to the clock (about 50 MB of memory). It checks IEULearn every 30 minutes and shows notifications. It never transcribes or downloads anything on its own."), sw("tray"))}
        ${row(window.LS_LINUX ? t("Start at login") : t("Start with Windows"), window.LS_LINUX ? t("Start the background checks when you log in.") : t("Start the background checks when you sign in to Windows."), sw("start_with_windows", !app.tray))}
        ${row(t("New lecture recordings"), t("Of your own section."), sw("notify_recordings", !app.tray))}
        ${row(t("New announcements"), t("Quiz, exam and homework news is marked as a heads-up."), sw("notify_announcements", !app.tray))}
        ${row(t("New files"), t("Slides and PDFs added to a course."), sw("notify_materials", !app.tray))}
        ${row(t("Quizzes and exams coming up"), t("A reminder the evening before an announced quiz, and before an exam week: from the background checks, or from IEUStudia itself while it's open."), sw("notify_reminders"))}</div>
      <div class="card set-card" id="set-storage"><h3>${t("Storage")}</h3>${use}
        ${removable.map(([id, l, mb]) => row(l, t("{size} · not in use", { size: gb(mb) }), `<button class="btn ghost sm danger" data-act="del-component" data-id="${esc(id)}">${I("x", "sm")} ${t("Remove")}</button>`)).join("")}</div>
      <div class="card set-card" id="set-about"><h3>${t("About")}</h3>
        ${row(`IEUStudia ${esc((S.boot || {}).version || "")}`, t("Free and open source (MIT License). Lectures are transcribed on this PC. AI runs only through the service you chose, and only when you ask."),
          `<div style="display:flex;gap:6px"><button class="btn ghost sm" data-act="logs">${I("terminal", "sm")} ${t("Logs")}</button><button class="btn sm" data-act="setup-again">${I("refresh", "sm")} ${t("Run setup again")}</button></div>`)}
        ${(() => {
          const up = S.update || {};
          const when = up.checked_at ? St.rel(new Date(up.checked_at * 1000).toISOString()) : "";
          const said = up.available ? t.html("IEUStudia {version} is available. Updating keeps everything you have.", { version: up.version })
            : up.error && !up.checked_at ? t.html("Couldn't look for a new version: {error}", { error: up.error })
            : when ? t.html("You have the newest version (checked {when}).", { when }) : t("Not checked yet.");
          return row(t("New versions"), said, up.available
            ? `<button class="btn sm update-btn" data-act="update-open">${I("download", "sm")} ${t.html("Update to {version}", { version: up.version })}</button>`
            : `<button class="btn ghost sm" data-act="update-check">${I("refresh", "sm")} ${t("Check now")}</button>`)
            + row(t("Look for new versions by itself"), t("About once a day IEUStudia asks GitHub, where it's published, whether there's a new version. You decide when to update."), sw("check_updates"));
        })()}
        ${St.sq.about()}
        <div class="su-notice-small">${NOTICE()}</div></div>`;
  };
  async function loadStorage() {
    const r = await call("storage");
    if (r.ok) { U.storage = r; if (S.view.name === "settings") St.renderView(); }
  }
  Object.assign(A, {
    "ai-provider": async (el) => {
      const v = el.value;
      const r = await call("app_config", { ai_provider: v });
      if (!r.ok) { toast(r.error, "error"); return; }
      S.boot.app = r.app; await loadAi();
      if (v === "local") U.storage = null;
      St.renderView();
    },
    "codex-model": async (el) => { const r = await call("app_config", { codex_model: el.value }); if (r.ok) S.boot.app = r.app; },
    "app-bool": async (el) => {
      const patch = { [el.dataset.key]: el.checked };
      if (el.dataset.key === "tray" && !el.checked) patch.start_with_windows = false;
      const r = await call("app_config", patch);
      if (!r.ok) { toast(r.error, "error"); return; }
      S.boot.app = r.app; St.renderView(); St.renderTop();
      if (el.dataset.key === "tray") toast(el.checked ? t("Background checks are on: the IEUStudia icon is next to the clock, bottom right.") : t("Background checks are off."), "info");
    },
    "bg-on": async () => {          // back as it was before "Turn off background checks" in the tray menu
      const r = await call("app_config", { tray: true, start_with_windows: true });
      if (!r.ok) { toast(r.error, "error"); return; }
      S.boot.app = r.app; St.renderView(); St.renderTop();
      toast(window.LS_LINUX ? t("Background checks are on.") : t("Background checks are on: the IEUStudia icon is next to the clock, bottom right."), "info");
    },
    "setup-ai": () => { St.startSetup().then(() => go(STEPS.indexOf("ai"))); },
    "del-component": async (el) => {
      if (!confirm(t("Remove it from this PC? You can download it again later."))) return;
      const r = await call("delete_component", el.dataset.id);
      if (!r.ok) { toast(r.error, "error"); return; }
      U.storage = r; St.renderView();
    },
  });
  St.inputActs = St.inputActs.concat(["app-bool"]);

  // ------------------------------------------------------------------ browser preview (mock)
  St.mocks.setup_state = () => ({ ok: true, model: "large-v3-turbo", output_dir: "C:\\Users\\student\\Documents\\IEUStudia", frozen: true, startup: false,
    app: { onboarded: false, terms_accepted: "", ai_provider: "", codex_model: "", local_model: "qwen3-1.7b", tray: false, start_with_windows: false, notify_recordings: true, notify_announcements: true, notify_materials: false, notify_reminders: true },
    facts: { disk: { data_dir: "C:\\Users\\student\\AppData\\Local\\IEUStudia", data_free_gb: 182.4 } },
    verdict: { ok: true, blockers: location.hash.includes("weak") ? ["This processor does not support AVX instructions, which the speech recognition needs."] : [], warnings: [], gpu: true, recommended_model: "large-v3-turbo", recommended_llm: "qwen3-4b",
      models: { "large-v3-turbo": { ok: true }, "large-v3": { ok: true }, medium: { ok: true }, small: { ok: true } },
      summary: [{ label: "Windows", value: "Windows 11 24H2 (build 26100)", ok: "yes" }, { label: "Processor", value: "Intel Core i7-13700H · 20 threads", ok: "yes" },
        { label: "Memory", value: "32 GB", ok: "yes" }, { label: "Graphics", value: "NVIDIA GeForce RTX 4070 Laptop GPU · 8 GB · driver 566.14", ok: "yes" }, { label: "Free disk space", value: "182 GB on C:\\", ok: "yes" }] },
    catalog: { speech: [{ id: "large-v3-turbo", label: "Fast & accurate", description: "Nearly the best quality, and fast with an NVIDIA graphics card.", size_mb: 1620, ready: false },
      { id: "large-v3", label: "Maximum accuracy", description: "Slowest, best for hard audio and long Turkish passages.", size_mb: 3090, ready: false },
      { id: "medium", label: "Medium", description: "More accurate than Small, but very slow without an NVIDIA GPU (hours per lecture). With one, Fast & accurate is both quicker and more accurate.", size_mb: 1530, ready: false },
      { id: "small", label: "Small", description: "The one for PCs without an NVIDIA GPU: fast enough on the processor, a bit less accurate.", size_mb: 484, ready: false }],
    cuda: { needed: true, ready: false, size_mb: 560 }, llm: [] } });
  // the preview walks through a fresh PC: signing in with ChatGPT, or Claude Code / the Codex CLI and their sign-in
  // (#ready: ChatGPT and Claude are connected already)
  const connected = location.hash.includes("ready");
  const M = St.mockAi = { claude: connected, codex: false, login: { chatgpt: connected, claude: connected, codex: false }, test: {} };
  const mockJob = (which) => ({ state: M[which] ? "done" : "", line: "", error: "" });
  St.mocks.ai_status = (which) => {
    const all = { ok: true, provider: "none", chosen: "",
      chatgpt: { signed_in: M.login.chatgpt, email: M.login.chatgpt ? "student@example.com" : "", plan_allowed: M.login.chatgpt, registered: M.login.chatgpt, sign_in: {},
        model: "", models: M.login.chatgpt ? ["gpt-6.1-sol", "gpt-6-sol-mini"] : [], needs: "a ChatGPT plan that apps may use (Plus or Pro)", test: M.test.chatgpt || {} },
      claude: { available: M.claude, version: M.claude ? "2.1.290" : "", logged_in: M.login.claude, plan: M.login.claude ? "pro" : "", email: M.login.claude ? "student@example.com" : "",
        needs: "Claude Pro or Max subscription", plan_status: M.login.claude ? { ok: true, label: "Claude Pro", note: "" } : { ok: null, label: "", note: "" },
        job: mockJob("claude"), test: M.test.claude || {} },
      codex: { available: M.codex, version: M.codex ? "0.157.0" : "", logged_in: M.login.codex, plan: M.login.codex ? "chatgpt" : "", email: "", ours: M.codex, model: "",
        models: M.login.codex ? ["gpt-6.1-sol", "gpt-6-sol-mini"] : [], needs: "a ChatGPT plan with Codex",
        plan_status: M.login.codex ? { ok: null, label: "ChatGPT", note: "Codex does not show your plan; the test message below shows whether it works." } : { ok: null, label: "", note: "" },
        job: mockJob("codex"), test: M.test.codex || {} },
      local: { model: "qwen3-1.7b", models: [{ id: "qwen3-1.7b", label: "Basic", description: "Short notes and simple questions; runs on most PCs with 8 GB of memory (Qwen3 1.7B).", size_mb: 1745, ready: false },
        { id: "qwen3-4b", label: "Better", description: "Clearly better notes and answers; needs an NVIDIA graphics card with 8 GB of memory (Qwen3 4B).", size_mb: 4045, ready: false }] } };
    return which ? { ok: true, provider: all.provider, chosen: all.chosen, [which]: all[which] } : all;
  };
  St.mocks.ai_install = (which) => {
    const lines = { claude: ["Downloading Claude Code from claude.ai...", "Setting up Claude Code...", "Claude Code successfully installed!"],
      codex: ["Looking up the latest Codex CLI on npm…", "Downloading Codex 0.157.0… 31 of 58 MB", "Unpacking…"] }[which];
    lines.forEach((line, i) => setTimeout(() => window.__lsEvent({ type: "ai_setup", which, state: "running", line, error: "" }), 400 + 700 * i));
    setTimeout(() => { M[which] = true; window.__lsEvent({ type: "ai_setup", which, state: "done", line: "Installed", error: "" }); }, 400 + 700 * lines.length);
    return { ok: true, job: { state: "running", line: "Starting…", error: "" } };
  };
  St.mocks.ai_login = (which) => {
    if (which === "chatgpt") {          // "Continue with ChatGPT": the browser comes back after a while
      setTimeout(() => { M.login.chatgpt = true; window.__lsEvent({ type: "chatgpt", state: "done", error: "", email: "student@example.com" }); }, 2500);
      return { ok: true, state: "waiting" };
    }
    setTimeout(() => { M.login[which] = true; }, 3000);
    return { ok: true };
  };
  St.mocks.ai_sign_out = () => { M.login.chatgpt = false; delete M.test.chatgpt; return { ok: true, revoked: true }; };
  St.mocks.ai_test = (which) => new Promise((res) => setTimeout(() => { M.test[which] = { ok: true, seconds: 3.4, error: "", at: Date.now() / 1000 }; res({ ok: true, test: M.test[which] }); }, 1500));
  St.mocks.setup_plan = (speech, llm) => {
    const items = [{ id: `speech:${speech}`, label: "Speech model (Fast & accurate)", size_mb: 1620 }, { id: "cuda", label: "NVIDIA GPU libraries (CUDA 12 cuBLAS)", size_mb: 560 }];
    if (llm) items.push({ id: `llm:${llm}`, label: "Offline AI model", size_mb: llm === "qwen3-4b" ? 4045 : 1745 });
    return { ok: true, items, total_mb: items.reduce((a, b) => a + b.size_mb, 0), free_gb: 182.4, blockers: [] };
  };
  St.mocks.setup_download = (ids) => {
    let i = 0, done = 0;
    const tick = setInterval(() => {
      const id = ids[i]; if (!id) { clearInterval(tick); window.__lsEvent({ type: "setup", id: "", state: "idle", ok: true }); return; }
      done += 40; window.__lsEvent({ type: "setup", id, state: done >= 100 ? "done" : "downloading", done: Math.min(done, 100) * 1048576, total: 100 * 1048576 });
      if (done >= 100) { i++; done = 0; }
    }, 120);
    return { ok: true };
  };
  St.mocks.setup_cancel = () => ({ ok: true });
  St.mocks.app_config = (patch) => { S.boot.app = { ...(S.boot.app || {}), ...(patch || {}) }; return { ok: true, app: S.boot.app, startup: !!S.boot.app.start_with_windows, helper: !!S.boot.app.tray }; };
  St.mocks.finish_setup = () => ({ ok: true, app: { ...(S.boot.app || {}), onboarded: true } });
  St.mocks.storage = () => ({ ok: true, usage: { speech_models: 1.7e9, gpu_libraries: 7.8e8, library: 1.2e8, cache: 6e7, your_files: 2.4e9 }, free_gb: 182.4, data_dir: "C:\\Users\\student\\AppData\\Local\\IEUStudia",
    catalog: { speech: [{ id: "large-v3-turbo", label: "Fast & accurate", size_mb: 1620, ready: true }, { id: "small", label: "Small", size_mb: 484, ready: true }], cuda: {}, llm: [] } });
  St.mocks.delete_component = () => St.mocks.storage();
})();
