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
    busy: {}, autoTested: {}, codes: {}, opened: {} };

  // ------------------------------------------------------------------ the notice (also in Settings → About)
  // a function, not a constant: it is drawn in the language of the moment (also in Settings → About)
  const NOTICE = () => `<ul class="notice">
      <li>${t("<b>Independent student project.</b> IEUStudia is not made, endorsed or supported by İzmir University of Economics, IEULearn or Panopto. Use it at your own risk; it comes without any warranty and the authors take no responsibility for how it is used.")}</li>
      <li>${window.LS_LINUX ? t("<b>Your EkoID password stays with you.</b> It is sent only to learn.ieu.edu.tr to sign you in. On this PC it is stored encrypted with a key in your desktop's keyring, so only your user account can read it. It never goes to any other server: there is no IEUStudia server.") : t("<b>Your EkoID password stays with you.</b> It is sent only to learn.ieu.edu.tr to sign you in. On this PC it is stored encrypted with Windows' own protection, so only your Windows account can read it. It never goes to any other server: there is no IEUStudia server.")}</li>
      <li>${t("<b>Your lectures stay on this PC.</b> Audio is transcribed on your own computer. Recordings, slides and materials belong to your lecturers and the university: keep them for your own study and do not share them.")}</li>
      <li>${t("<b>AI is optional and can make mistakes.</b> Nothing is sent to an AI service until you use an AI feature. Then the lectures and course files you chose are sent to the service you picked (OpenAI or Anthropic, under your own account). Always check AI answers against your lectures and materials.")}</li>
      <li>${t("<b>New versions.</b> About once a day IEUStudia asks GitHub, where it's published, whether there's a new version, and fetches a tiny file there so the number of people using each version can be counted. Nothing about you is sent. You can turn it off in Settings → About.")}</li></ul>`;
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
    const before = aiShape(U.ai), full = JSON.stringify(U.ai);
    U.ai = which ? { ...(U.ai || {}), ...r } : r;           // one AI's status can come back before the full one
    for (const key of Object.keys(VENDOR)) {
      const a = U.ai[key];
      if (a && (a.logged_in || a.signed_in)) delete U.busy["login:" + key];
    }
    autoTest();
    if (aiShape(U.ai) !== before) rerenderAi();            // nothing new: leave the page alone
    else if (JSON.stringify(U.ai) !== full) Object.keys(VENDOR).forEach((key) => drawJobLine(key, (U.ai[key] || {}).job));
  }
  // the AI status without the line of an install under way, which changes with every look while it downloads: that
  // line is written in place (drawJobLine), so the 4-second look doesn't draw the whole page again each time
  const aiShape = (ai) => JSON.stringify(Object.fromEntries(Object.entries(ai || {}).map(([k, v]) =>
    [k, v && v.job && v.job.state === "running" ? { ...v, job: { ...v.job, line: "" } } : v])));
  function drawJobLine(key, job) {
    if (!job || job.state !== "running") return;
    document.querySelectorAll(`.cn-step.busy[data-cn="${key}:tool"] .d`).forEach((el) => { el.textContent = job.line || t("Installing…"); });
  }
  St.loadAi = loadAi;               // (the page tests look once, as the 4-second look does)
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
      const busy = U.busy["login:" + key] || (a.job && a.job.state === "running") || (a.sign_in || {}).state === "waiting"
        || (a.login || {}).state === "waiting" || (a.job && a.job.state === "error" && !a.available);   // (installed, but it didn't start yet)
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
    // a plan that couldn't be made is no "everything is already here" (with "0 GB free" beside its error)
    U.plan = r.ok ? r : { items: [], total_mb: 0, blockers: [r.error], free_gb: null, failed: true };
    U.done = !!r.ok && U.plan.items.length === 0;
    St.render();
  }
  // what is still to download: the plan's items not done, or (no plan could be made) what the choices need
  function leftIds() {
    if (U.plan && U.plan.failed) return [`speech:${U.ch.speech}`, ...(U.ch.ai === "local" ? [`llm:${U.ch.llm}`] : [])];
    return ((U.plan && U.plan.items) || []).filter((it) => (U.dl[it.id] || {}).state !== "done").map((it) => it.id);
  }
  // what waits for those downloads: transcription (the speech model, the graphics card's files), the offline AI, both
  function waitsFor() {
    const ids = leftIds(), ai = ids.some((id) => id.startsWith("llm:")), speech = ids.some((id) => !id.startsWith("llm:"));
    return ai && speech ? "both" : ai ? "ai" : "speech";
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
    if (!U.st) return U.error ? `<div class="callout error">${I("x-circle")}<div>${esc(U.error)}</div>
      <button class="btn sm" data-act="su-state-again">${I("refresh", "sm")} ${t("Try again")}</button></div>` : St.skeleton();
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
      get manual() { return [[t("Install Claude Code (in PowerShell)"), "irm https://claude.ai/install.ps1 | iex"],
        [t("Or in Command Prompt, when PowerShell is blocked"), "curl -fsSL https://claude.ai/install.cmd -o install.cmd && install.cmd && del install.cmd"],
        [t("Sign in"), "claude auth login"]]; } },
    codex: { name: "ChatGPT", tool: "Codex CLI", upgrade: "https://chatgpt.com/pricing",
      get intro() { return t("IEUStudia talks to ChatGPT through OpenAI's <b>Codex CLI</b>, signed in with your ChatGPT account. Pick this if you already use the Codex CLI; IEUStudia can also install it for you. Otherwise plain ChatGPT is simpler, with nothing to install."); },
      get install() { return t("Install Codex CLI"); },
      get source() { return t("Free, installed from OpenAI's official source."); },
      get signinText() { return t("Opens OpenAI's sign-in page in your browser. IEUStudia never sees your password."); },
      get needs() { return t("Needs a ChatGPT plan with Codex."); },
      get manual() { return [[t("Install the Codex CLI (needs Node.js)"), "npm install -g @openai/codex"], [t("Sign in"), "codex login"],
        [t("Or sign in with a code, when the browser can't come back"), "codex login --device-auth"]]; } },
  };
  // ChatGPT: "Continue with ChatGPT" (chatgpt.py), nothing to install
  function chatgptSteps(a) {
    const si = a.sign_in || {}, test = a.test || {}, ready = a.signed_in && a.plan_allowed;
    const waiting = si.state === "waiting" || (U.busy["login:chatgpt"] && !a.signed_in && si.state !== "error");
    const noPlanUse = a.signed_in && !a.plan_allowed;
    const notEligible = test.ok === false && test.code === "plan_not_eligible";
    const switchBtn = (cls) => `<button class="btn${cls ? " " + cls : ""} sm" data-act="ai-switch">${I("users", "sm")} ${t("Use another account or workspace")}</button>`;
    const signinError = si.state === "error" ? esc(si.error) + gptAfterError(si, switchBtn)
      : noPlanUse ? t("You're signed in, but IEUStudia may not use your ChatGPT plan yet. Click Allow it and accept on OpenAI's page.")
      : a.ended ? t("Your ChatGPT sign-in has ended (IEUStudia was disconnected in ChatGPT's settings, or OpenAI ended it). Click Continue with ChatGPT to sign in again.")
      : a.unreadable ? t("Your earlier ChatGPT sign-in can't be read on this PC any more (after a Windows password reset, or with data copied from another PC). Click Continue with ChatGPT to sign in again.") : "";
    return [
      { id: "signin", ok: ready, title: t("Continue with ChatGPT"), okText: a.email ? t.html("Signed in as {email}", { email: a.email }) : t("Signed in"),
        text: t("Opens OpenAI's sign-in page in your browser. Sign in, then allow IEUStudia to use your ChatGPT plan."),
        busy: waiting ? gptWaitText(si) : "", busyMore: si.state === "waiting" ? gptWaitMore(a, si) : "",
        error: signinError,
        btn: noPlanUse ? `<button class="btn primary sm" data-act="ai-login" data-id="chatgpt" data-consent="1">${t("Allow it")}</button>`
          : `<button class="btn primary sm" data-act="ai-login" data-id="chatgpt">${t("Continue with ChatGPT")}</button>` },
      { id: "plan", ok: test.ok === true, title: t.c("subscription", "Your plan"), okText: t("It works with your plan"),
        text: t("Plus and Pro can be used. For Free and Go, OpenAI decides."),
        error: notEligible ? `${esc(test.error)}<div class="cn-wait">${switchBtn("")}</div>` : "",
        unknown: (ready && test.ok !== true && !notEligible) ? t("The test message shows whether your plan can be used in apps.") : "",
        btn: notEligible ? `<button class="btn sm" data-act="url" data-url="https://chatgpt.com/pricing">${I("external", "sm")} ${t("See plans")}</button>` : "" },
      { id: "test", ok: test.ok === true, title: t("Test message"), okText: t("{name} answered in {s} s", { name: "ChatGPT", s: tenths(test.seconds) }),
        text: t("One short message to check that everything works. It uses a little of your plan."),
        busy: U.busy["test:chatgpt"] ? t("Sending a short test message…") : "", error: test.ok === false && !notEligible ? esc(test.error) : "",
        btn: `<button class="btn sm" data-act="ai-test" data-id="chatgpt">${test.ok === false ? t("Try again") : t("Send a test message")}</button>` },
    ];
  }
  // What the sign-in is doing now, in words: chatgpt.py says each step (the page opened, came back, the code traded,
  // checked); time passing without the page opening says where it may be. Plain text (drawn escaped).
  function gptWaitText(si) {
    const since = U.busy["login:chatgpt"], waited = since ? Date.now() - since : 1e9;
    if (si.step === "verifying") return t("Checking your sign-in…");
    if (si.step === "exchanging") return t("Getting your sign-in from OpenAI…");
    if (si.step === "returned") return t("OpenAI sent you back to IEUStudia. Finishing the sign-in…");
    // (if your browser shows an error page after you allow IEUStudia: a security program blocks the way back)
    const blocked = t("If your browser shows an error page (such as “This site can't be reached”) after you allow IEUStudia, copy its address and use Paste the address from your browser (under Trouble?).");
    if (si.opened) {
      // IEUStudia's window was still in front a moment after the browser asked for the page (chatgpt.py): behind it
      const behind = !si.behind ? "" : si.browser ? t("The sign-in page opened in your browser ({browser}), behind this window: click your browser on the taskbar at the bottom of the screen.", { browser: si.browser })
        : t("The sign-in page opened in your browser, behind this window: click your browser on the taskbar at the bottom of the screen.");
      if (waited > 90000) return `${behind || t("Still waiting for you to finish on OpenAI's page. Can't find it? It may be behind this window, minimized, or on another desktop: look in the taskbar, or open it again below.")} ${blocked}`;
      return behind || t("Finish on the sign-in page in your browser (look in the taskbar if it isn't in front). This list notices when you're done.");
    }
    if (si.launched === false) return gptNoBrowser();
    if (si.note === "listen") return t("Finish on OpenAI's page in your browser. IEUStudia can't listen for your browser on this PC, so when your browser shows an error page afterwards, copy its address and use Paste the address below.");
    if (si.state !== "waiting") return t("Opening your browser…");
    // 15 s and IEUStudia's link still not asked for: the taskbar, then Microsoft Edge or the link by hand
    if (waited > 15000) return `${t("Your browser hasn't shown the sign-in page.")} ${gptEdge(si) ? t("If your browser's icon blinks on the taskbar, click it. Still nothing? Click Open in Microsoft Edge, or Copy link and paste it into any browser's address bar.")
      : t("If your browser's icon blinks on the taskbar, click it. Still nothing? Click Copy link and paste it into any browser's address bar.")}`;
    return t("Opening the sign-in page in your browser…");
  }
  // "Open in Microsoft Edge" is offered: Edge is on this PC, the page hasn't opened, a browser could be started (when
  // none could, Edge didn't start either) and Edge wasn't the browser just tried (Open in Microsoft Edge, or Edge as
  // the default browser: "asked your browser (Microsoft Edge)… click Open in Microsoft Edge" offered the same one);
  // not when nothing listens for the way back
  const gptEdge = (si) => !!si.edge && !si.opened && si.launched !== false && si.via !== "edge" && si.browser !== "Microsoft Edge"
    && si.note !== "listen";
  // which way Open the page again took (another one each time while the page doesn't open)
  function gptAsked(si) {
    if (si.way === "edge") return t("IEUStudia asked Microsoft Edge to open it.");
    if (si.way === "shell") return t("IEUStudia asked Windows to open it again (Windows picks the browser).");
    return si.browser ? t.html("IEUStudia asked your default browser ({browser}) to open it again.", { browser: si.browser })
      : t("IEUStudia asked your default browser to open it again.");
  }
  // No browser at all could be started (the default one and Microsoft Edge): the link to paste by hand, and the cure
  const gptNoBrowser = () => t("No browser could be opened on this PC. Click Copy link, open your browser yourself and paste the link into the address bar. To fix it for good, choose a default browser (see Trouble?).");
  // IEUStudia runs as administrator (chatgpt.py's "elevated"): a browser started from it may not show up. Said only
  // then: in front once the page hasn't shown up (15 s, or no browser started at all), else under "Trouble?"
  const gptAdminText = () => t("IEUStudia is running as administrator, and a browser may not open from it. Close IEUStudia and open it again from the Start menu, without Run as administrator.");
  const gptAdmin = (si, slow) => !!si.elevated && !si.opened && (slow || si.launched === false);
  // Under the waiting text: what IEUStudia noticed (no working default browser, https asked for, OpenAI out of reach),
  // the ways out (the one to take now in front), and the less common cases under "Trouble?"
  function gptWaitMore(a, si) {
    const since = U.busy["login:chatgpt"], slow = (since ? Date.now() - since : 1e9) > 15000 && !si.opened;
    const notes = [];
    const apps = window.LS_LINUX ? "" : ` <button class="link" data-act="open-default-apps">${t("Open Default apps")}</button>`;
    const edge = si.via === "edge" && si.launched !== false;       // (Edge too may not have started: then not said)
    // why the first try didn't use the default browser; which browser has the page; which way Open the page again took.
    // Edge "opened" it once the page really opened; before that it was only sent there (it may show nothing either)
    const edgeWhy = (none) => !edge ? (none ? t("Windows has no working default browser.") : t("Your default browser didn't start."))
      : si.opened ? (none ? t("Windows has no working default browser, so the page opened in Microsoft Edge.") : t("Your default browser didn't start, so the page opened in Microsoft Edge."))
      : none ? t("Windows has no working default browser, so IEUStudia sent the page to Microsoft Edge.") : t("Your default browser didn't start, so IEUStudia sent the page to Microsoft Edge.");
    if (!si.again && si.why === "no_default") notes.push(edgeWhy(true) + apps);
    else if (!si.again && si.why === "default_failed") notes.push(edgeWhy(false) + apps);
    else if (si.again && si.launched !== false) notes.push(gptAsked(si));
    else if (si.browser && si.opened) { if (!si.behind) notes.push(t.html("Opened with {browser}.", { browser: si.browser })); }   // (behind: the text above names it)
    // "Opened" once the browser really asked for the page; before that it was only handed to the browser (on the
    // friend's PC the page never came: "Opened with Brave" under "hasn't shown the sign-in page yet" said both)
    else if (si.browser && si.launched) notes.push(slow ? t.html("IEUStudia asked your browser ({browser}) to open it.", { browser: si.browser }) : t.html("Sent to {browser}.", { browser: si.browser }));
    const admin = gptAdmin(si, slow);
    if (admin) notes.push(gptAdminText());
    if (si.note === "https") notes.push(t("Your browser tried to reach IEUStudia with https://, which it doesn't use (a secure-only setting or a security program does this). Click Copy link and paste it into your browser's address bar; if it shows an error page after you allow IEUStudia, use Paste the address below."));
    if (si.reach && si.reach.ok === false) notes.push(t.html("IEUStudia itself can't reach OpenAI from this PC, so the sign-in can't finish yet: {why}", { why: si.reach.why }));
    const paste = si.note === "listen" || si.note === "https";        // the way back is blocked: the address brings it
    const copy = si.launched === false;                               // nothing opens: the link, pasted by hand
    const edgeNow = slow && gptEdge(si);                              // 15 s without the page: Edge, whatever the default
    return `${notes.map((n) => `<div class="cn-note">${n}</div>`).join("")}
      <div class="cn-wait">${paste ? `<button class="btn primary sm" data-act="ai-login-paste">${I("link", "sm")} ${t("Paste the address from your browser")}</button>` : ""}
        ${edgeNow ? `<button class="btn${paste ? "" : " primary"} sm" data-act="ai-login-edge">${I("external", "sm")} ${t("Open in Microsoft Edge")}</button>` : ""}
        <button class="btn sm" data-act="ai-login-again">${I("external", "sm")} ${t("Open the page again")}</button>
        ${si.page ? `<button class="btn ${copy && !paste ? "primary" : "ghost"} sm" data-act="ai-copy" data-text="${esc(si.page)}">${I("copy", "sm")} ${t("Copy link")}</button>` : ""}
        <button class="btn ghost sm" data-act="ai-login-cancel">${t("Cancel")}</button></div>
      ${gptTrouble(a, si, admin)}`;
  }
  // "Trouble?": the less common cases, each with what to do there. The errors OpenAI shows on its own page never reach
  // IEUStudia (it only sees that nothing came back), so they are named here with their cure. (adminShown: the
  // administrator note is in front already.)
  function gptTrouble(a, si, adminShown) {
    const win = !window.LS_LINUX;
    const list = (items) => `<ul class="dim">${items.filter(Boolean).map((x) => `<li>${x}</li>`).join("")}</ul>`;
    const links = win ? `<button class="btn ghost sm" data-act="open-default-apps">${t("Open Default apps")}</button><span class="dim">${t("Do links never open on this PC? Choose a default browser. Windows 11: Start → Settings → Apps → Default apps → choose your browser → Set default. Windows 10: Start → Settings → Apps → Default apps → Web browser → choose your browser.")}</span>`
      : `<span class="dim">${t("Do links never open on this PC? Choose a default browser in your system settings.")}</span>`;
    const browser = [
      win ? t("Your browser may be stuck. Close all its windows, press Ctrl+Shift+Esc to open Task Manager, select your browser under Processes, click End task, then click Open the page again.") : "",
      t("Did your browser ask you to choose a profile? Choose one; if the sign-in page doesn't open then, click Open the page again (Firefox forgets the link in that case)."),
      win ? t("Using more than one desktop? Press Windows key + Tab to find your browser's window.") : "",
      si.elevated && !adminShown ? gptAdminText() : "",
      t("Does your browser show an error for 127.0.0.1 instead of OpenAI's page? Click Copy link and paste it into the address bar: that link goes straight to OpenAI."),
      t("The copied link works only in a browser on this PC, not on your phone."),
    ];
    const openai = [
      // (with a saved registration it may be that one OpenAI no longer takes: a fresh one is the deliberate way out)
      a.registered ? t("“This app is unavailable. Contact the app owner…”: the problem is on OpenAI's side, not your PC. Try again in a few hours. Still the same then? Click Use another account or workspace and choose your account again: IEUStudia then connects anew.")
        : t("“This app is unavailable. Contact the app owner…”: the problem is on OpenAI's side, not your PC. Try again in a few hours."),
      a.registered ? t("It says your organization or workspace doesn't allow this app: click Use another account or workspace and choose your personal account, or ask your workspace's admin.")
        : t("It says your organization or workspace doesn't allow this app: click Open the page again and choose your personal account on OpenAI's page, or ask your workspace's admin."),
      t("It asks for your phone number: enter it and finish that step. If the sign-in doesn't go on afterwards, click Open the page again."),
      t("It keeps asking whether you're human, stays blank or keeps loading: turn off any VPN, proxy, WARP, GoodbyeDPI/Zapret or DNS changer, then click Open the page again. Still the same? Click Copy link and paste it into a private window (Ctrl+Shift+N in Chrome or Edge), or connect this PC to your phone's hotspot."),
      `${t("It says “Sorry, you have been blocked” or warns about suspicious activity: wait up to an hour, then click Open the page again.")} <button class="link" data-act="url" data-url="https://status.openai.com">${t("Is OpenAI down?")}</button>`,
      t("It says ChatGPT isn't available in your country or region: turn off any VPN or proxy, then click Open the page again. ChatGPT isn't offered in every country."),
      t("Still stuck? Your antivirus's web protection may be in the way. Pause it for a few minutes and click Open the page again. If that helps, add chatgpt.com, auth.openai.com and openai.com as exceptions in it and turn it back on."),
    ];
    return `<details class="cn-trouble" ${U.gptTrouble ? "open" : ""}><summary data-act="gpt-trouble">${t("Trouble?")}</summary><div class="cn-tr">
        <b>${t("The sign-in page doesn't show up?")}</b>
        ${list(browser)}
        ${si.edge ? `<div><button class="btn ghost sm" data-act="ai-login-edge">${I("external", "sm")} ${t("Open in Microsoft Edge")}</button><span class="dim">${t("Is Microsoft Family Safety on for this PC? It closes browsers other than Microsoft Edge: use Open in Microsoft Edge.")}</span></div>` : ""}
        <div>${links}</div>
        <b>${t("Does OpenAI's page show an error?")}</b>
        ${list(openai)}
        <div><button class="btn ghost sm" data-act="ai-login-paste">${I("link", "sm")} ${t("Paste the address from your browser")}</button><span class="dim">${t("If your browser shows an error page after you allow IEUStudia, its address still finishes the sign-in.")}</span></div>
        <div><button class="btn ghost sm" data-act="ai-login-details">${I("copy", "sm")} ${t("Copy details")}</button><span class="dim">${t("A short report without passwords or codes, to send when you ask for help.")}</span></div>
        ${a.registered ? `<div><button class="btn ghost sm" data-act="ai-switch">${I("users", "sm")} ${t("Use another account or workspace")}</button><span class="dim">${t("Signs out here, then OpenAI asks again which account and workspace to use.")}</span></div>` : ""}
      </div></details>`;
  }
  // After a failed sign-in (the step's own button tries again): another account when that is the cure, and a report
  function gptAfterError(si, switchBtn) {
    return `<div class="cn-wait">${["workspace", "other_account"].includes(si.code) ? switchBtn("") : ""}
      <button class="btn ghost sm" data-act="ai-login-details">${I("copy", "sm")} ${t("Copy details")}</button></div>`;
  }
  // Under ChatGPT's steps: this PC's clock when it is off (OpenAI's clock says so), another account, and in Settings
  // signing out
  function gptFoot(a) {
    const clock = a.clock_text || (a.sign_in || {}).clock_text;
    return `${clock ? `<div class="callout warning cn-clock">${I("clock")}<div>${esc(clock)}</div></div>` : ""}
      ${a.signed_in ? `<div class="dim su-note">${I("lock", "xs")} ${t("Signed in with ChatGPT.")} ${S.mode === "app" ? `<button class="link" data-act="ai-signout" data-id="chatgpt">${t("Sign out")}</button> · ` : ""}<button class="link" data-act="ai-switch">${t("Use another account or workspace")}</button>${S.mode === "app" ? ` · <button class="link" data-act="url" data-url="https://chatgpt.com/settings/usage">${t("Usage in ChatGPT's settings")}</button>` : ""}</div>` : ""}`;
  }
  // The checklist for one AI: each step is done, busy, failed, unknown, the one to do now, or later.
  function connectSteps(key, a) {
    if (key === "chatgpt") return chatgptSteps(a);
    const v = VENDOR[key], out = [];
    // installing: its line (the download's size, speed and time left, or the time the set-up step takes), with a
    // way out; stopped: the next click goes on where it stopped
    const j = a.job || {}, old = a.too_old, bad = a.problem;      // a copy too old for IEUStudia, or one that won't start
    const why = old ? (key === "claude" ? t("The Claude Code on this PC ({version}) is too old for IEUStudia. Click Update Claude Code: IEUStudia installs the newest one.", { version: old.version })
        : t("The Codex CLI on this PC ({version}) is too old for IEUStudia. Click Install Codex CLI: IEUStudia then uses its own, newer copy (yours stays as it is).", { version: old.version }))
      : bad ? (key === "claude" ? t("Claude Code is on this PC but doesn't start ({error}). Click Install Claude Code to put a working copy in its place.", { error: bad.error })
        : t("The Codex CLI is on this PC but doesn't start ({error}). Click Install Codex CLI: IEUStudia then uses its own copy.", { error: bad.error })) : "";
    out.push({ id: "tool", ok: a.available, title: v.tool, okText: t.html("Version {version}", { version: a.version }),
      text: j.state === "cancelled" ? t("Stopped. Click the button to go on: the download continues where it stopped.") : v.source,
      busy: j.state === "running" ? (j.line || t("Installing…")) : "",
      busyMore: j.state === "running" && !j.stopping ? `<div class="cn-wait"><button class="btn ghost sm" data-act="ai-cli-cancel" data-id="${key}" data-what="install">${t("Cancel")}</button></div>` : "",
      error: j.state === "error" ? esc(j.error) : esc(why),
      btn: `<button class="btn sm primary" data-act="ai-install" data-id="${key}">${j.state === "error" ? `${I("retry", "sm")} ${t("Try again")}`
        : old && key === "claude" ? `${I("download", "sm")} ${t("Update Claude Code")}` : `${I("download", "sm")} ${v.install}`}</button>` });
    out.push(cliSignin(key, a));
    const ps = a.plan_status || {}, test = a.test || {};
    // signed in with an account without the plan: signing in with another one is the other way out
    const another = `<div class="cn-wait"><button class="btn ghost sm" data-act="ai-cli-login" data-id="${key}">${I("users", "sm")} ${t("Sign in with another account")}</button></div>
      <div class="d dim">${key === "claude" ? t("If Anthropic's page signs you straight in to the same account, log out on claude.ai in your browser first.") : t("If OpenAI's page signs you straight in to the same account, log out on chatgpt.com in your browser first.")}</div>`;
    out.push({ id: "plan", ok: ps.ok === true || (ps.ok == null && a.logged_in && test.ok === true), title: t.c("subscription", "Your plan"),
      okText: ps.label ? esc(ps.label) + (ps.note ? `. ${esc(ps.note)}` : "") : t("It works with your plan"),
      text: v.needs, error: ps.ok === false ? esc(ps.note) + another : "", unknown: ps.ok == null && a.logged_in && test.ok !== true ? esc(ps.note || "") : "",
      btn: ps.ok === false ? `<button class="btn sm" data-act="url" data-url="${v.upgrade}">${I("external", "sm")} ${t("See plans")}</button>` : "" });
    out.push({ id: "test", ok: test.ok === true, title: t("Test message"), okText: t("{name} answered in {s} s", { name: v.name, s: tenths(test.seconds) }),
      text: t("One short message to check that everything works. It uses a little of your plan."),
      busy: U.busy["test:" + key] ? t("Sending a short test message… The first one can take up to a minute while {tool} starts.", { tool: v.tool }) : "",
      error: test.ok === false ? esc(test.error) : "",
      btn: `<button class="btn sm" data-act="ai-test" data-id="${key}">${test.ok === false ? t("Try again") : t("Send a test message")}</button>` });
    return out;
  }
  // Signing in to Claude Code or the Codex CLI: their own sign-in runs without a window (ai.py), and the list says what
  // it is doing. While it waits: where to finish, and a way out (the page again, its link, Claude's code from the page,
  // Codex's sign-in with a code, Cancel). When it ends without signing in: why, and the button again.
  function cliSignin(key, a) {
    const v = VENDOR[key], lg = a.login || {}, pending = U.busy["login:" + key];
    // its own status says signed in, but the test message was refused as signed out (the sign-in ended: a password
    // change, a sign-out everywhere, a refused renewal): signing in again is the cure, so this step offers it
    const refused = a.logged_in && (a.test || {}).code === "signed_out";
    const waiting = lg.state === "waiting" || (!!pending && (!a.logged_in || refused));
    const since = lg.started ? lg.started * 1000 : (pending || Date.now()), slow = Date.now() - since > 20000;
    const claude = key === "claude";
    const waitText = lg.state !== "waiting" ? t("Starting the sign-in…")
      : lg.device ? (lg.code ? t("Open the page, sign in with your ChatGPT account and enter the code below. This list notices when you're done.") : t("Getting a sign-in code from OpenAI…"))
      : slow ? (claude ? t("No sign-in page in your browser? Click Open the sign-in page, or copy the link into your browser. If the page then shows a code, paste it below.")
        : t("No sign-in page in your browser? Click Open the sign-in page, or copy the link into your browser's address bar."))
      : claude ? t("Finish on Anthropic's page in your browser (look in the taskbar if it isn't in front). This list notices when you're done.")
        : t("Finish on OpenAI's page in your browser (look in the taskbar if it isn't in front). This list notices when you're done.");
    const page = lg.state === "waiting" && lg.page ? `<button class="btn sm" data-act="ai-cli-open" data-id="${key}">${I("external", "sm")} ${t("Open the sign-in page")}</button>
        <button class="btn ghost sm" data-act="ai-copy" data-text="${esc(lg.page)}">${I("copy", "sm")} ${t("Copy the link")}</button>` : "";
    const device = !claude && lg.device && lg.code ? `<div class="cn-wait"><span>${t.html("Your code: <b>{code}</b>", { code: lg.code })}</span>
        <button class="btn ghost sm" data-act="ai-copy" data-text="${esc(lg.code)}">${I("copy", "sm")} ${t("Copy the code")}</button></div>` : "";
    // the link Claude Code prints leads to a page that shows a code to paste back (when the browser can't return)
    const paste = claude && lg.state === "waiting" && (slow || U.opened[key]) ? `<div class="cn-wait cn-code"><input class="input" style="flex:1;min-width:180px" data-cn-code="${key}" value="${esc(U.codes[key] || "")}"
        placeholder="${esc(t("The code from Anthropic's page"))}" autocomplete="off" spellcheck="false" aria-label="${esc(t("The code from Anthropic's page"))}">
        <button class="btn sm" data-act="ai-cli-code" data-id="${key}">${t("Send the code")}</button></div>` : "";
    const otherWay = !claude && !lg.device && (slow || lg.state === "error") ? ` <button class="link" data-act="ai-cli-login" data-id="${key}" data-device="1">${t("Sign in with a code instead")}</button>` : "";
    // a sign-in with a code that ended (the code expired): Try again gets a new code; the browser is the other way
    const byCode = !claude && lg.device && lg.state === "error";
    const browserWay = byCode ? ` <button class="link" data-act="ai-cli-login" data-id="${key}">${t("Sign in in the browser instead")}</button>` : "";
    return { id: "signin", ok: a.logged_in && !refused && lg.state !== "waiting", title: t("Sign in to {name}", { name: v.name }), okText: a.email ? t.html("Signed in as {email}", { email: a.email }) : t("Signed in"),
      text: v.signinText,
      busy: waiting ? waitText : "",
      busyMore: waiting ? `${device}<div class="cn-wait">${page}<button class="btn ghost sm" data-act="ai-cli-cancel" data-id="${key}" data-what="login">${t("Cancel")}</button>${otherWay}</div>${paste}` : "",
      error: refused ? (claude ? t("Claude Code says it is signed in, but Anthropic no longer accepts that sign-in (it ends after a password change or a sign-out everywhere, for example). Sign in again.")
          : t("The Codex CLI says it is signed in, but OpenAI no longer accepts that sign-in (it ends after a password change or a sign-out everywhere, for example). Sign in again."))
        : !a.logged_in && lg.state === "error" ? esc(lg.error) + otherWay + browserWay : "",
      btn: `<button class="btn sm primary" data-act="ai-cli-login" data-id="${key}"${byCode ? ' data-device="1"' : ""}>${refused ? t("Sign in again") : lg.state === "error" ? t("Try again") : t("Sign in with {name}", { name: v.name })}</button>` };
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
      return `<div class="cn-step ${st}" data-cn="${key}:${s.id}"><span class="cn-ico">${ico}</span><div class="grow"><b>${s.title}</b><div class="d">${detail}</div>
        ${st === "now" && s.hint ? `<div class="d dim">${esc(s.hint)}</div>` : ""}${st === "busy" && s.busyMore ? s.busyMore : ""}</div>${btn}</div>`;
    }).join("");
    const ready = steps.every((s) => s.ok);
    return `<p class="cn-intro">${v.intro}</p><div class="cn-steps">${rows}</div>
      ${ready ? `<div class="callout success">${I("check-circle")}<div>${t.html("<b>{name} is ready.</b> AI features in IEUStudia will use your {name} plan.", { name: v.name })}</div></div>`
        : S.mode === "setup" ? `<div class="dim su-note">${I("info", "xs")} ${t("You can also continue now and finish this later in Settings → AI.")}</div>` : ""}
      ${key === "chatgpt" ? gptFoot(a) : ""}
      ${v.manual ? `<details class="cn-manual"><summary>${t("Rather do it yourself?")}</summary>
        <div class="dim">${t("Run these in PowerShell or Command Prompt (a line that names one works only there), then come back: this list updates by itself.")}</div>
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
    // the offline models: from the AI status, or else from this PC's check (the same list), so the card is never empty
    // while the status loads or when it could not be read
    const loc = (U.ai && U.ai.local && U.ai.local.models) || (U.st.catalog && U.st.catalog.llm) || [];
    const rec = U.st.verdict.recommended_llm;
    const llmBody = rec ? `<div class="presets">${loc.map((m) => `<button class="preset ${U.ch.llm === m.id ? "on" : ""}" data-act="su-llm" data-id="${m.id}" ${m.id === "qwen3-4b" && rec !== "qwen3-4b" ? "disabled" : ""}>
        <div class="n">${esc(m.label)} ${m.id === rec ? `<span class="pill now">${t("Fits this PC")}</span>` : ""}<span class="sz">${gb(m.size_mb)}${m.ready ? ` · ${t("on this PC")}` : ""}</span></div><div class="d">${esc(m.description)}</div></button>`).join("")}</div>
        <div class="dim su-note">${t("Runs entirely on this PC. Fine for short notes and simple questions; study plans, review sheets and study packs need ChatGPT or Claude.")}${U.st.verdict.gpu ? "" : " " + t("Without an NVIDIA graphics card it writes slowly (notes for a lecture take several minutes).")}</div>`
      : `<div class="callout warning">${I("alert")}<div>${t("This PC has too little memory for an offline model (8 GB needed).")}</div></div>`;
    return `<h2>${t("AI help (optional)")}</h2><p class="lead">${t("Study notes, chat about your lectures, video study plans, exam review sheets and study packs. Pick what you have: everything else in IEUStudia works without AI.")}</p>
      ${aiCard("chatgpt", "ChatGPT", t("Uses your ChatGPT plan. Plus and Pro work; Free and Go may, with smaller limits."), connectHTML("chatgpt"))}
      ${aiCard("claude", "Claude", t("Needs a Claude Pro or Max subscription."), connectHTML("claude"))}
      ${aiCard("codex", t("ChatGPT through the Codex CLI"), t("Pick this only if you already use OpenAI's Codex CLI (a tool for programmers) with your ChatGPT plan. Otherwise choose ChatGPT above."), connectHTML("codex"))}
      ${aiCard("local", t("Offline model"), t("No subscription: a small model on this PC for basic notes and questions."), llmBody,
        rec || llmHere(U.st, U.ch.llm) ? "" : t("Not on this PC: it needs 8 GB of memory."))}
      ${aiCard("none", t("No AI"), t("Transcripts, heads-ups, search, calendar and everything else still work."), "")}
      <div class="callout info su-ai-note">${I("info")}<div>${t("<b>AI can make mistakes.</b> Answers are generated from your lectures, but check anything important against the lecture and your materials. Nothing is sent anywhere unless you use an AI feature.")}</div></div>`;
  }
  // what to do when Windows doesn't start IEUStudia at sign-in although it was asked to (a function: the language of
  // the moment)
  const STARTUP_BLOCKED = () => t("Windows doesn't let IEUStudia start by itself when you sign in: your antivirus or Windows' own Startup apps list stops it. Everything else works. To fix it, allow IEUStudia in Task Manager → Startup apps (or in your antivirus), then turn Start with Windows on again in Settings.");
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
        ${sw("notify_reminders", t("Quizzes, exams and deadlines coming up"), t("Before quizzes, exams and assignment deadlines: you can choose when on the Calendar page. Also while IEUStudia is open."))}</div>
      <div class="dim su-note">${I("info", "xs")} ${window.LS_LINUX ? t("You can change all of this later in Settings.") : t("You can change all of this later in Settings, or by right-clicking the IEUStudia icon next to the clock.")}</div>`;
  }
  // ------------------------------------------------------------------ how a download goes: speed, time left, a stall
  // worked out from its progress events (about 4 a second, also while nothing arrives) and kept in the download's own
  // state: the last 8 seconds give the speed. No new byte for 20 s is said (the download keeps trying by itself for
  // minutes), and so is the checking at the end, when the bar is full but the files are still being checked.
  const rate = (bps) => (bps >= 1048576 ? t("{n} MB/s", { n: t.num(bps / 1048576, bps < 10485760 ? 1 : 0, false) }) : t("{n} KB/s", { n: t.num(Math.max(1, bps / 1024), 0, false) }));
  St.pace = (d, done, total) => {
    const now = Date.now(), m = d.marks || (d.marks = []);
    if (!d.began) d.began = now;
    if (m.length && done < m[m.length - 1][1]) { m.length = 0; d.moved = 0; }          // counted afresh (a part started again)
    m.push([now, done]);
    while (m.length > 2 && now - m[0][0] > 8000) m.shift();
    if (!d.moved || done > (d.top || 0)) { d.moved = now; d.top = done; }
    return d;
  };
  St.paceText = (d, done, total) => {
    const now = Date.now(), m = d.marks || [];
    if (!total) return now - (d.began || now) > 15000 ? t("Still connecting. If the server can't be reached, IEUStudia says why in a minute or so.") : "";
    if (done >= total) return t("Finishing: checking the files…");
    const quiet = now - (d.moved || now);
    if (quiet >= 20000) return t("Nothing has arrived for {time}. If the connection dropped, IEUStudia keeps trying and goes on by itself when it's back.", { time: quiet < 60000 ? t("{s} s", { s: Math.round(quiet / 1000) }) : t.dur(quiet / 1000) });
    const [at, from] = m[0] || [now, done], secs = (now - at) / 1000;
    const bps = secs >= 2 ? (done - from) / secs : 0;
    return bps > 0 ? `${rate(bps)} · ${t.left((total - done) / bps)}` : "";
  };
  function dlRows(items) {
    return items.map((it) => {
      const d = U.dl[it.id] || {};
      const pct = d.total ? Math.round((100 * d.done) / d.total) : d.state === "done" ? 100 : 0;
      const state = d.state === "done" ? `<span class="pill done">${I("check")}${t("Done")}</span>` : d.state === "error" ? `<span class="pill failed">${t("Failed")}</span>`
        : d.state === "downloading" ? `<span class="dim">${d.total ? t("{done} of {total}", { done: bytes(d.done), total: bytes(d.total) }) : t("Starting…")}</span>`
        : d.state === "cancelled" ? `<span class="dim">${t("Cancelled")}</span>` : `<span class="dim">${gb(it.size_mb)}</span>`;
      const how = d.state === "downloading" && U.running ? St.paceText(d, d.done || 0, d.total || 0) : "";
      return `<div class="su-dl"><div class="grow"><b>${esc(it.label)}</b>${d.error ? `<div class="err-txt">${esc(d.error)}</div>` : ""}<div class="bar"><i style="width:${pct}%"></i></div>${how ? `<div class="dim su-pace" style="font-size:12px;margin-top:5px">${esc(how)}</div>` : ""}</div>${state}</div>`;
    }).join("");
  }
  function stepDownload() {
    const p = U.plan;
    if (!p) return `<h2>${t.c("noun", "Download")}</h2>${St.skeleton()}`;
    const rows = dlRows(p.items);
    const total = p.total_mb, waits = waitsFor();
    // skipping: what waits for the downloads, as it is (the offline AI's model too, not only transcription), and that
    // Downloads at the top tries again when clicked (nothing tries by itself)
    const skipText = { speech: t("You can skip this for now. Your courses, files, announcements and AI work without these downloads; only transcribing lectures waits for them. Click Downloads at the top of IEUStudia to try again later."),
      ai: t("You can skip this for now. Your courses, files, announcements and transcription work without the offline model; only the offline AI waits for it. Click Downloads at the top of IEUStudia to try again later."),
      both: t("You can skip this for now. Your courses, files and announcements work without these downloads; transcribing lectures and the offline AI wait for them. Click Downloads at the top of IEUStudia to try again later.") }[waits];
    const blockedText = { speech: t("When that's fixed, click Check again. Or skip this for now: everything except transcribing lectures works without these downloads."),
      ai: t("When that's fixed, click Check again. Or skip this for now: everything except the offline AI works without its model."),
      both: t("When that's fixed, click Check again. Or skip this for now: everything except transcribing lectures and the offline AI works without these downloads.") }[waits];
    return `<h2>${t.c("noun", "Download")}</h2><p class="lead">${p.failed ? t("IEUStudia couldn't work out what to download (the reason is below).")
      : !p.items.length ? t("Everything IEUStudia needs is already on this PC.")
      : U.running ? t("You can go on and sign in: the download continues in the background, and a transcription waits for it to finish.")
      : t("These are downloaded once now, so IEUStudia works right away.")}</p>
      ${p.items.length ? `<div class="card su-dls">${rows}</div>` : ""}
      ${p.failed ? "" : `<div class="card su-space"><h3>${I("hard-drive", "sm")} ${t("Disk space")}</h3>
        <div class="su-row"><span class="grow">${t("Downloads now")}</span><b>${gb(total)}</b></div>
        <div class="su-row"><span class="grow">${t("Each transcribed lecture hour (text, slides from the screen recording)")}</span><b>≈ 5–30 MB</b></div>
        <div class="su-row"><span class="grow">${t("Course files you download")}</span><b>${t("as large as the files")}</b></div>
        <div class="su-row"><span class="grow">${t.html("Free on {drive}", { drive: (U.st.facts.disk.data_dir || "C:").slice(0, 3) })}</span><b>${t.num(p.free_gb, 0, false)} GB</b></div></div>`}
      ${(p.blockers || []).length ? `<div class="callout error">${I("x-circle")}<div>${p.blockers.map(esc).join("<br>")}
        <div class="dim" style="margin-top:6px">${blockedText}</div></div></div>`
        : dlStopped() ? `<div class="callout info">${I("info")}<div>${skipText}</div></div>` : ""}`;
  }
  // Cancel download: once pressed, it says it is stopping (until the download has stopped)
  const cancelBtn = (cls) => (U.cancelling ? `<button class="${cls}" disabled>${SPIN()} ${t("Stopping the download…")}</button>`
    : `<button class="${cls}" data-act="su-cancel">${t("Cancel download")}</button>`);
  // tried, and it failed or was cancelled: a way past it (the setup must never end here)
  const dlStopped = () => STEPS[U.step] === "download" && !U.running && !U.done && !!U.plan && U.plan.items.length > 0 && Object.keys(U.dl).length > 0;
  function stepSignin() {
    const ai = { chatgpt: "ChatGPT", claude: "Claude", codex: "ChatGPT (Codex CLI)", local: t("Offline model"), none: t("No AI"), "": t("No AI") }[U.ch.ai];
    const notYet = VENDOR[U.ch.ai] && !(U.ai && U.ai[U.ch.ai] && aiSignedIn(U.ch.ai, U.ai[U.ch.ai]));
    // skipped, or still going on: said here too, so nobody wonders later why a lecture waits
    const dlLeft = !!U.plan && !U.done && (U.plan.items.length > 0 || (U.plan.blockers || []).length > 0);
    return `<h2>${t("Almost done")}</h2><p class="lead">${S.boot && S.boot.signed_in ? t.html("You are signed in as {name}. Finish to open IEUStudia.", { name: (S.profile || {}).name || "" }) : t("Last step: sign in with your IEULearn (EkoID) account.")}</p>
      <div class="card su-summary">${[[t("Transcription"), (U.st.catalog.speech.find((m) => m.id === U.ch.speech) || {}).label || U.ch.speech],
        [t("AI"), notYet ? t("{name}: not connected yet (Settings → AI)", { name: ai }) : ai],
        [t("Background checks"), !U.ch.tray ? t("Off") : U.ch.startup ? (window.LS_LINUX ? t("On, starts at login") : t("On, starts with Windows")) : t("On")],
        ...(dlLeft ? [[t("Downloads"), U.running ? t("Still downloading in the background") : { speech: t("Not finished yet. Click Downloads at the top to try again; lectures are transcribed once it's done."),
          ai: t("Not finished yet. Click Downloads at the top to try again; the offline AI works once it's done."),
          both: t("Not finished yet. Click Downloads at the top to try again; lectures are transcribed and the offline AI works once it's done.") }[waitsFor()]]] : [])].map(([k, v]) => `<div class="su-row"><span class="grow">${k}</span><b>${esc(v)}</b></div>`).join("")}</div>`;
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
    // a download that can't be done now (it failed, was cancelled, or the disk is too full) never ends the setup here:
    // it can be skipped, and Downloads at the top offers it again
    const blocked = k === "download" && !U.running && !!U.plan && (U.plan.blockers || []).length > 0;
    const skip = blocked || dlStopped() ? `<button class="btn" data-act="su-skip-dl">${t("Skip for now")} ${I("arrow-right", "sm")}</button>` : "";
    const next = k === "signin" ? `<button class="btn primary" data-act="su-finish">${S.boot && S.boot.signed_in ? t("Open IEUStudia") : t("Continue to sign in")} ${I("arrow-right", "sm")}</button>`
      : dl && U.running ? `<button class="btn primary" data-act="su-next">${SPIN()} ${t("Continue while it downloads")} ${I("arrow-right", "sm")}</button>`
      : blocked ? `<button class="btn primary" data-act="su-plan-again">${I("refresh", "sm")} ${t("Check again")}</button>`
      : dl ? `<button class="btn primary" data-act="su-download">${
        Object.values(U.dl).some((d) => d.state === "error") ? `${I("retry", "sm")} ${t("Try again")}` : `${I("download", "sm")} ${t("Download {size}", { size: gb(U.plan.total_mb) })}`}</button>`
        : `<button class="btn primary" data-act="su-next" ${canNext() ? "" : "disabled"}>${t("Continue")} ${I("arrow-right", "sm")}</button>`;
    return `<div class="setup"><aside class="su-rail"><div class="brand"><img class="logo-img" src="../assets/logo.svg" alt="">IEUStudia</div>
        <ol>${STEPS.map((key, i) => `<li class="${i === U.step ? "on" : i < U.step ? "done" : ""}"><span>${i < U.step ? I("check", "xs") : i + 1}</span>${stepName(key)}</li>`).join("")}</ol>
        <div class="su-rail-foot">${I("lock", "xs")} ${t("No account with us, no tracking.")}</div></aside>
      <main class="su-main"><div class="su-body">${body}</div>
        <div class="su-foot">${U.step > 0 && !(U.running && k === "download") ? `<button class="btn ghost" data-act="su-back">${I("arrow-left", "sm")} ${t("Back")}</button>`
          : k === "welcome" && St.contact ? St.contact.line() : `<span></span>`}
          <span style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;justify-content:flex-end">${U.running && k === "download" ? cancelBtn("btn ghost") : ""}${skip}${next}</span></div></main></div>`;
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
          <div class="actions"><button class="btn" data-act="su-connect-now" data-id="${U.ch.ai}">${t("Connect it now")}</button><button class="btn primary" data-act="su-next" data-anyway="1">${t("Continue without it")} ${I("arrow-right", "sm")}</button></div>`);
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
        // Windows (an antivirus, or its own Startup apps list) didn't let it start by itself: said, and the setup goes on
        if (U.ch.tray && U.ch.startup && r.startup === false && !window.LS_LINUX) toast(STARTUP_BLOCKED(), "info");
      }
      go(U.step + 1);
    },
    "su-skip-dl": () => {        // a download that can't be done now never holds up the rest of IEUStudia
      const left = leftIds();    // (no plan could be made: what the choices need, so Downloads offers them again)
      if (left.length) call("app_config", { pending_downloads: left });          // offered again: Downloads at the top
      go(U.step + 1);
    },
    "su-plan-again": () => loadPlan(),
    "su-state-again": async () => {          // this PC's check failed (IEUStudia was still starting, say): again, same step
      const at = U.step;
      await St.startSetup();
      go(at);                                 // (failed again: the reason shows here once more, with this button)
    },
    "su-back": () => go(U.step - 1),
    "su-speech": (el) => { U.ch.speech = el.dataset.id; St.render(); },
    "su-llm": (el) => { U.ch.llm = el.dataset.id; St.render(); },
    // "Connect it now" in the not-connected question: really starts it (it used to close the question only, so
    // nothing seemed to happen): ChatGPT's sign-in, or Claude/Codex's next step; then shows that step on screen
    "su-connect-now": (el) => {
      const key = el.dataset.id, a = (U.ai && U.ai[key]) || {};
      St.closeModal();
      // a sign-in or an install already under way is left alone (starting it again would end the browser page the
      // student is on): the button only shows its step
      const under = U.busy["login:" + key] || (a.sign_in || {}).state === "waiting" || (a.login || {}).state === "waiting"
        || (a.job || {}).state === "running";
      if (under) { /* shown below */ }
      else if (key === "chatgpt") A["ai-login"]({ dataset: { id: "chatgpt", ...(a.signed_in && !a.plan_allowed ? { consent: "1" } : {}) } });
      else if (VENDOR[key] && !a.available) A["ai-install"]({ dataset: { id: key } });
      else if (VENDOR[key] && !a.logged_in) A["ai-login"]({ dataset: { id: key } });
      setTimeout(() => {
        const at = $(".su-ai.on .cn-step.busy") || $(".su-ai.on .cn-step.now") || $(".su-ai.on .cn-step.err") || $(".su-ai.on");
        if (at) { at.scrollIntoView({ block: "center", behavior: "smooth" }); at.classList.remove("attention"); void at.offsetWidth; at.classList.add("attention"); }
      }, 60);
    },
    "su-ai": (el) => { U.ch.ai = el.dataset.id; St.render(); if (VENDOR[U.ch.ai]) { loadAi(U.ch.ai); pollAi(true); } },
    "su-bool": (el) => { U.ch[el.dataset.key] = el.checked; if (el.dataset.key === "tray" && !el.checked) U.ch.startup = false; St.render(); },
    "su-download": async () => {
      U.running = true; U.cancelling = false; U.dl = {}; St.render();
      const r = await call("setup_download", U.plan.items.map((x) => x.id));
      if (!r.ok) { U.running = false; toast(r.error, "error"); St.render(); }
    },
    "su-cancel": () => {           // said at once: a download stops at its next step, which can take a few seconds
      U.cancelling = true;
      if (S.mode === "setup") St.render(); else if (document.querySelector(".modal.dl")) dlModal();
      call("setup_cancel");
    },
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
  // the box for the code of Claude's sign-in: what is typed survives the list being drawn again, and Enter sends it
  document.addEventListener("input", (e) => {
    const el = e.target;
    if (el && el.dataset && el.dataset.cnCode) U.codes[el.dataset.cnCode] = el.value;
  });
  document.addEventListener("keydown", (e) => {
    const el = e.target;
    if (e.key === "Enter" && el && el.dataset && el.dataset.cnCode) { e.preventDefault(); A["ai-cli-code"]({ dataset: { id: el.dataset.cnCode } }); }
  });

  // installing Claude Code or the Codex CLI (ai_setup.py)
  St.onEvents.ai_setup = (e) => {
    const key = e.which, before = (U.ai && U.ai[key] && U.ai[key].job) || {};
    if (U.ai && U.ai[key]) U.ai[key].job = e;
    // a progress tick (twice a second): only the line's words change, in place, and only where that AI's list is
    // on screen. Drawing the page again each time would scroll it to the top, close what is open, take the focus and
    // lose a click on Cancel that lands in between; with another AI's card on screen, nothing changes at all.
    if (e.state === "running" && before.state === "running" && !!e.stopping === !!before.stopping) {
      drawJobLine(key, e);
      return;
    }
    if (e.state === "error") toast(e.error, "error");
    if (e.state !== "running") { if (U.ai && U.ai[key]) delete U.ai[key].test; U.autoTested[key] = false; loadAi(key); }
    rerenderAi();
  };
  // Claude Code's or the Codex CLI's sign-in (ai.py): its link found, a code, then done, failed or cancelled
  St.onEvents.ai_cli_login = (e) => {
    const key = e.which;
    if (U.ai && U.ai[key]) U.ai[key].login = { ...e };
    if (e.state !== "waiting") {
      delete U.busy["login:" + key]; delete U.codes[key]; delete U.opened[key]; U.autoTested[key] = false;
      if (e.state === "error") toast(e.error, "error");
      loadAi(key);
    }
    rerenderAi();
  };
  // "Open the page again" (another way each time while the page doesn't open: the default browser, Microsoft Edge,
  // Windows' own open) and "Open in Microsoft Edge"; which way it took is said under the waiting text
  async function gptOpenAgain(api) {
    const r = await call(api);
    if (!r.ok) { toast(r.error, "error"); return; }
    if (r.state !== "waiting") { delete U.busy["login:chatgpt"]; loadAi("chatgpt"); rerenderAi(); return; }   // it ended meanwhile
    if (api === "ai_login_edge" && r.launched === false) {       // only Edge didn't start: the rest stays as it was
      toast(t("Microsoft Edge didn't start on this PC. Click Copy link and paste it into any browser's address bar."), "error");
      return;
    }
    if (U.ai && U.ai.chatgpt) U.ai.chatgpt.sign_in = { ...(U.ai.chatgpt.sign_in || {}), state: "waiting", link: r.link || "", page: r.page || "",
      launched: r.launched !== false, browser: r.browser || "", via: r.via || "", way: r.way || "", again: r.again || 0 };
    if (r.launched === false) toast(gptNoBrowser(), "error");
    rerenderAi();
  }
  // "Continue with ChatGPT" finished in the browser (chatgpt.py)
  St.onEvents.chatgpt = (e) => {
    if (e.state === "waiting") {                   // a step of the sign-in (chatgpt.py): the page opened, came back…
      const { type, ...step } = e;
      if (U.ai && U.ai.chatgpt) U.ai.chatgpt.sign_in = { ...(U.ai.chatgpt.sign_in || {}), ...step };
      rerenderAi();
      return;
    }
    delete U.busy["login:chatgpt"];
    // Paste the address was open: the browser's way back came first, so the dialog has nothing left to do
    if ($("#gpt-paste")) { St.closeModal(); if (e.state === "done") toast(t("Signed in with ChatGPT."), "success"); }
    if (e.state === "error") toast(e.error, "error");
    if (U.ai && U.ai.chatgpt) U.ai.chatgpt.sign_in = { state: e.state, error: e.error || "", code: e.code || "" };
    U.autoTested.chatgpt = false;
    loadAi("chatgpt");
    rerenderAi();
  };
  // Copy link, Copy details, a code or a command: when Windows refuses the clipboard (another program holds it open),
  // the text is shown to copy by hand, or to photograph, with another try. A red toast alone left the link, or the
  // report meant for whoever helps, nowhere. Every other copy button uses it too (St.copied: a lecture moment's link,
  // a chat answer, Copy for AI, the course export's)
  St.copied = copied;
  // (over a dialog that is open, such as the course export's "The zip is ready": a layer of its own, so that one stays
  // as it is under it, with its path and its steps; Close, Escape and a click beside it close only the layer)
  function copied(r, text, done) {
    if (r.ok) { toast(done, "success"); return; }
    if (!text) { toast(r.error, "error"); return; }
    U.copy = { text, done };
    // as many lines as it takes (about 60 characters a line: a sign-in link is some 550), up to 7, then it scrolls
    const rows = Math.min(7, Math.max(2, text.split("\n").reduce((n, line) => n + Math.max(1, Math.ceil(line.length / 60)), 0)));
    // (on Linux it copies with wl-copy, xclip or xsel, or the page itself: not Windows' words there)
    const why = window.LS_LINUX ? t("IEUStudia couldn't use the clipboard just now (it copies with wl-copy, xclip or xsel). Click Try again, or copy it from the box yourself: click in the box, press Ctrl+A, then Ctrl+C.")
      : t("Windows didn't let IEUStudia use the clipboard just now (another program may be using it). Click Try again, or copy it from the box yourself: click in the box, press Ctrl+A, then Ctrl+C.");
    const over = !!$("#overlay .modal");          // (a dialog is open: this one goes over it, as a layer)
    const html = `<h2>${t("Copy failed")}</h2>
      <p class="lead">${why}</p>
      <textarea class="input cn-copybox" id="cn-copybox" readonly spellcheck="false" rows="${rows}">${esc(text)}</textarea>
      <div class="err-txt cn-copyerr" id="cn-copy-err"></div>
      <div class="actions"><button class="btn" data-act="${over ? "cn-copy-close" : "modal-close"}">${t("Close")}</button><button class="btn primary" data-act="ai-copy-again">${t("Try again")}</button></div>`;
    if (over) copyLayer(html); else St.modal(html);
    setTimeout(() => { const box = $("#cn-copybox"); if (box) { box.focus(); box.select(); } }, 30);
  }
  function copyLayer(html) {
    closeCopyLayer();
    const box = document.createElement("div");
    box.className = "backdrop ask-back"; box.id = "copy-fail"; box.dataset.act = "cn-copy-bg";
    box.innerHTML = `<div class="modal" role="alertdialog" aria-modal="true" tabindex="-1">${html}</div>`;
    document.body.appendChild(box);
  }
  function closeCopyLayer() {          // true when there was one (the dialog under it gets the focus back)
    const layer = document.getElementById("copy-fail");
    if (!layer) return false;
    layer.remove();
    const under = $("#overlay .modal"); if (under) under.focus({ preventScroll: true });
    return true;
  }
  St.closeCopyLayer = closeCopyLayer;
  document.addEventListener("keydown", (e) => {        // (before app.js's: Escape there would close the dialog under it)
    const layer = document.getElementById("copy-fail");
    if (!layer) return;
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeCopyLayer(); }
    else if (e.key === "Tab" && St.trapTab) { e.stopPropagation(); St.trapTab(e, layer.querySelector(".modal")); }
  }, true);
  Object.assign(A, {
    "ai-install": async (el) => {
      const which = el.dataset.id;
      const r = await call("ai_install", which);
      if (!r.ok) { toast(r.error, "error"); return; }
      if (U.ai && U.ai[which]) U.ai[which].job = r.job;
      pollAi(true); rerenderAi();
    },
    "ai-cli-cancel": async (el) => {            // Claude Code's or the Codex CLI's install (or sign-in) under way
      const key = el.dataset.id, what = el.dataset.what || "";
      const job = what === "install" && U.ai && U.ai[key] && U.ai[key].job;
      if (job && job.state === "running") {      // said at once: an install stops within a second or two
        U.ai[key].job = { ...job, line: t("Stopping…"), stopping: true };
        rerenderAi();
      }
      const r = await call("ai_cli_cancel", key, what);
      if (!r.ok) { toast(r.error, "error"); return; }
      if (what !== "install") {
        delete U.busy["login:" + key]; delete U.codes[key]; delete U.opened[key];
        if (U.ai && U.ai[key]) U.ai[key].login = {};
        rerenderAi();
      }
      loadAi(key);
    },
    // their own sign-in, started by IEUStudia (device: Codex's sign-in with a code, when the browser can't come back)
    "ai-cli-login": async (el) => {
      const key = el.dataset.id, at = Date.now();
      U.busy["login:" + key] = at; U.autoTested[key] = false; delete U.codes[key]; delete U.opened[key];
      if (U.ai && U.ai[key]) U.ai[key].login = {};
      rerenderAi();
      const r = await call("ai_login", key, false, !!el.dataset.device);
      if (U.busy["login:" + key] === at) delete U.busy["login:" + key];
      if (!r.ok) { toast(r.error, "error"); rerenderAi(); return; }
      if (U.ai && U.ai[key]) { delete U.ai[key].test; U.ai[key].login = { ...r }; }
      setTimeout(rerenderAi, 20500);           // still no page after a while: the list says what to do then
      pollAi(true); rerenderAi();
    },
    "ai-cli-open": async (el) => {
      const r = await call("ai_cli_open", el.dataset.id);
      if (!r.ok) { toast(r.error, "error"); return; }
      U.opened[el.dataset.id] = true; rerenderAi();
      if (!r.launched) toast(r.state === "waiting" ? t("No browser could be opened on this PC. Click Copy the link and paste it into your browser's address bar.") : t("The sign-in isn't waiting any more. Click the button to start it again."), "error");
      if (r.state !== "waiting") loadAi(el.dataset.id);
    },
    "ai-cli-code": async (el) => {             // the code Anthropic's page shows, passed on to Claude Code's sign-in
      const key = el.dataset.id, box = document.querySelector(`[data-cn-code="${key}"]`);
      const code = ((box && box.value) || U.codes[key] || "").trim();
      if (!code) { toast(t("Paste the code from Anthropic's page into the box first."), "info"); return; }
      const r = await call("ai_cli_code", key, code);
      if (!r.ok) { toast(r.error, "error"); return; }
      delete U.codes[key];
      toast(t("Code sent. Claude Code finishes the sign-in now."), "info");
    },
    "ai-login": async (el) => {
      if (el.dataset.id === "claude" || el.dataset.id === "codex") return A["ai-cli-login"](el);
      const key = el.dataset.id, at = Date.now();
      const fresh = key === "chatgpt" && !!el.dataset.fresh;             // another account or workspace: signed out first
      U.busy["login:" + key] = at; U.autoTested[key] = false;        // at once: opening the browser can take a moment
      if (key === "chatgpt" && U.ai && U.ai.chatgpt) {                  // a new try: "Opening your browser…", no old error
        U.ai.chatgpt.sign_in = {};
        if (fresh) Object.assign(U.ai.chatgpt, { signed_in: false, plan_allowed: false, email: "", registered: false });
      }
      rerenderAi();
      const r = await (fresh ? call("ai_switch_account") : call("ai_login", key, !!el.dataset.consent));
      if (!r.ok) { if (U.busy["login:" + key] === at) delete U.busy["login:" + key]; toast(r.error, "error"); if (key === "chatgpt") loadAi("chatgpt"); rerenderAi(); return; }
      if (U.ai && U.ai[key]) {
        delete U.ai[key].test;
        // the steps that came meanwhile (the page may have opened already) stay
        if (key === "chatgpt") U.ai.chatgpt.sign_in = { ...(U.ai.chatgpt.sign_in || {}), state: "waiting", error: "", link: r.link || "", page: r.page || "",
          launched: r.launched !== false, browser: r.browser || "", via: r.via || "", why: r.why || "", edge: !!r.edge, elevated: !!r.elevated };
      }
      setTimeout(() => { if (U.busy["login:" + key] === at) { delete U.busy["login:" + key]; rerenderAi(); } }, 10 * 60000);
      if (key === "chatgpt") [15500, 90500].forEach((ms) => setTimeout(() => {    // time passing changes what is said
        const si = (U.ai && U.ai.chatgpt && U.ai.chatgpt.sign_in) || {};
        if (U.busy["login:chatgpt"] === at && si.state === "waiting") rerenderAi();
      }, ms));
      pollAi(true); rerenderAi();
    },
    "ai-login-again": () => gptOpenAgain("ai_login_again"),
    "ai-login-edge": () => gptOpenAgain("ai_login_edge"),
    // the less common ways (under "Trouble?"): the address pasted back, a report, another account
    "ai-login-paste": () => {
      // (when IEUStudia can't listen on this PC, nothing waits for the browser: the address is the only way back)
      const listens = (((U.ai && U.ai.chatgpt) || {}).sign_in || {}).note !== "listen";
      St.modal(`<h2>${t("Paste the address from your browser")}</h2>
        <p class="lead">${t("After you allow IEUStudia on OpenAI's page, your browser goes to an address that starts with http://127.0.0.1. If it shows an error page there (for example “This site can't be reached”), click its address bar, copy the whole address and paste it here.")}</p>
        <input class="input gpt-paste" id="gpt-paste" placeholder="http://127.0.0.1:…/auth/callback?code=…" autocomplete="off" spellcheck="false">
        <div class="err-txt" id="gpt-paste-err"></div>
        ${listens ? `<p class="dim">${t("IEUStudia keeps waiting for your browser too: whichever comes back first finishes the sign-in.")}</p>` : ""}
        <div class="actions"><button class="btn" data-act="modal-close">${t("Cancel")}</button><button class="btn primary" data-act="ai-login-paste-go">${t("Finish signing in")}</button></div>`);
      setTimeout(() => { const box = $("#gpt-paste"); if (box) box.focus(); }, 30);
    },
    "ai-login-paste-go": async (el) => {
      const box = $("#gpt-paste"), err = $("#gpt-paste-err"), text = box ? box.value.trim() : "";
      if (!text) { if (err) err.textContent = t("Paste the address first (Ctrl+V)."); if (box) box.focus(); return; }
      el.disabled = true; el.innerHTML = `${SPIN()} ${esc(t("Finishing…"))}`;
      const r = await call("ai_login_paste", text);
      if (r.ok && r.state === "refused") {                 // not an address that finishes it: said there, to try again
        el.disabled = false; el.textContent = t("Finish signing in");
        if (err) err.textContent = r.error;
        return;
      }
      const open = !!$("#gpt-paste");             // (the sign-in's end, said as an event, may have closed it already)
      St.closeModal();
      if (!r.ok) toast(r.error, "error");
      else if (r.state === "done" && open) toast(t("Signed in with ChatGPT."), "success");
      loadAi("chatgpt");
    },
    "ai-login-details": async () => {
      const r = await call("ai_login_details");
      copied(r, r.text || "", t("Details copied. Paste them into your message when you ask for help."));
    },
    "ai-switch": () => St.modal(`<h2>${t("Use another account or workspace")}</h2>
      <p class="lead">${t("IEUStudia signs out of ChatGPT here and opens OpenAI's page again. There, pick the account and the workspace to use (one with Plus or Pro). If OpenAI signs you straight in to the wrong account, log out on its page first.")}</p>
      <div class="actions"><button class="btn" data-act="modal-close">${t("Cancel")}</button><button class="btn primary" data-act="ai-switch-go">${t("Sign out and continue")}</button></div>`),
    "ai-switch-go": () => { St.closeModal(); return A["ai-login"]({ dataset: { id: "chatgpt", fresh: "1" } }); },
    "open-default-apps": async () => { const r = await call("open_default_apps"); if (!r.ok) toast(r.error, "error"); },
    "gpt-trouble": (el) => { U.gptTrouble = !(el.parentElement && el.parentElement.open); },     // kept open when drawn again
    "ai-login-cancel": async () => {
      await call("ai_login_cancel");
      delete U.busy["login:chatgpt"];
      if (U.ai && U.ai.chatgpt) U.ai.chatgpt.sign_in = {};
      loadAi("chatgpt"); rerenderAi();
    },
    "ai-signout": async (el) => {
      if (!(await St.ask({ title: t("Sign out of ChatGPT?"), text: t("OpenAI ends IEUStudia's session. You can sign in again any time."), ok: t("Sign out") }))) return;
      const r = await call("ai_sign_out", el.dataset.id);
      if (!r.ok) { toast(r.error, "error"); return; }
      toast(r.revoked ? t("Signed out of ChatGPT.") : t("Signed out here. OpenAI didn't confirm it, so also disconnect IEUStudia in ChatGPT's settings."), r.revoked ? "success" : "info");
      U.autoTested.chatgpt = false; loadAi("chatgpt");
    },
    "ai-test": (el) => runTest(el.dataset.id),
    "ai-copy": async (el) => { const r = await call("copy_text", el.dataset.text); copied(r, el.dataset.text || "", t("Copied")); },
    "cn-copy-close": () => { if (!closeCopyLayer()) St.closeModal(); },
    "cn-copy-bg": (el, e) => { if (e.target === el) closeCopyLayer(); },
    "ai-copy-again": async () => {
      const c = U.copy || { text: "", done: "" }, r = await call("copy_text", c.text);
      if (r.ok) { if (!closeCopyLayer()) St.closeModal(); toast(c.done, "success"); return; }
      const err = $("#cn-copy-err");
      if (err) err.textContent = t("Still not possible. Copy it from the box yourself (Ctrl+A, then Ctrl+C), or take a screenshot of it.");
    },
  });

  St.onEvents.setup = (e) => {
    const tick = e.state === "downloading" && U.running;          // progress of a download already drawn (about 3 a second)
    if (e.id) U.dl[e.id] = { ...(U.dl[e.id] || {}), error: "", ...e };      // only an "error" event carries one
    if (e.id && e.state === "downloading") St.pace(U.dl[e.id], e.done || 0, e.total || 0);    // its speed, time left
    if (tick) U.tickAt = Date.now();
    if (e.state === "downloading") U.running = true;
    if (e.state === "idle") {
      U.running = false; U.cancelling = false;
      // the setup's plan; or, when the page started during the download (no plan), what the chip and Downloads list
      U.done = !!e.ok && (U.plan ? U.plan.items : dlItems()).every((it) => (U.dl[it.id] || {}).state === "done");
      if (U.done && S.boot) S.boot.pending_downloads = [];
      if (!e.ok && Object.values(U.dl).some((d) => d.state === "error")) toast(S.mode !== "setup" ? t("A download stopped. Click Downloads at the top to see why and try again.")
        : STEPS[U.step] === "download" ? t("A download stopped. Its reason is shown above the buttons: try again, or skip it for now.")
        : t("A download stopped. Go Back to the Download step to see why, then try again or skip it for now."), "error");   // (gone on to sign in meanwhile)
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

  // while a download waits for its first byte, or nothing arrives, no event comes: its rows are drawn again each
  // second all the same (what they say changes with the time: "Still connecting", "Nothing has arrived for 40 s")
  setInterval(() => {
    if (!U.running || Date.now() - (U.tickAt || 0) < 900) return;
    document.querySelectorAll(".su-dls").forEach((el) => { el.innerHTML = dlRows(dlItems()); });
  }, 1000);

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
      <div class="actions">${U.running ? `${cancelBtn("btn")}<button class="btn primary" data-act="modal-close">${t("OK")}</button>`
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
      U.running = true; U.cancelling = false; St.renderTop(); dlModal();
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
        : x.available ? t("Claude Code is installed but not signed in.") : x.too_old ? t("The Claude Code on this PC is too old for IEUStudia: update it below.")
        : x.problem ? t("Claude Code is on this PC but doesn't start: see below.") : t("Claude Code is not installed."))
      : p === "codex" ? (x.logged_in ? t("Codex CLI {version} · signed in with ChatGPT", { version: x.version }) : x.available ? t("The Codex CLI is installed but not signed in.")
        : x.too_old ? t("The Codex CLI on this PC is too old for IEUStudia: install IEUStudia's own copy below.")
        : x.problem ? t("The Codex CLI is on this PC but doesn't start: see below.") : t("The Codex CLI is not installed."))
      : p === "local" ? ((x.models || []).find((m) => m.id === app.local_model) || {}).ready ? t("The offline model is on this PC.") : t("The offline model is not downloaded yet.") : t("AI is off, so the AI buttons are greyed out. To chat about your lectures or make study notes and plans, choose an AI service on the right.");
    const needs = a && a.local && p === "local" && !(a.local.models.find((m) => m.id === app.local_model) || {}).ready;
    const steps = VENDOR[p] && a && a[p];
    if (steps && !U.timer) pollAi(true);
    return `<div class="set-row"><div class="txt"><div class="t">${t("AI service")}</div><div class="d">${a ? esc(status) : `${SPIN()} ${t("Checking…")}`}</div></div>
        <select class="input" style="width:260px" data-act="ai-provider">${Object.entries(label).map(([v, l]) => `<option value="${v}" ${(p || "none") === v ? "selected" : ""}>${l}</option>`).join("")}</select></div>
      ${steps ? `<div class="set-connect">${connectHTML(p)}</div>` : ""}
      ${needs ? `<div class="set-row"><div class="txt"><div class="t">${t("Download the offline model")}</div><div class="d">${t("It is downloaded in the setup's AI step.")}</div></div><button class="btn sm" data-act="setup-ai">${I("arrow-right", "sm")} ${t("Set up AI")}</button></div>`
        : a && a.local && p === "local" ? `<div class="set-row"><div class="txt"><div class="t">${t("Another offline model")}</div><div class="d">${t("To switch to another offline model (Basic needs less memory), go through the setup's AI step.")}</div></div><button class="btn sm" data-act="setup-ai">${I("arrow-right", "sm")} ${t("Set up AI")}</button></div>` : ""}
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
    if (!window.LS_LINUX && Date.now() - (U.startupAt || 0) > 30000) loadOnce("startup", loadStartup);
    // asked to start with Windows, but Windows doesn't (its own Startup apps switch is off, or an antivirus said no)
    const startupOff = !window.LS_LINUX && app.tray && app.start_with_windows && U.startupReal === false;
    const use = st ? Object.entries({ speech_models: t("Speech models"), gpu_libraries: t("NVIDIA graphics card files"), offline_ai: t("Offline AI model"), ai_tools: t("Codex CLI (for ChatGPT)"), library: t("Library and transcripts"), cache: t("Temporary files"), your_files: t("Your files (IEUStudia folder)") })
      .filter(([k]) => st.usage[k]).map(([k, l]) => `<div class="su-row"><span class="grow">${l}</span><b>${bytes(st.usage[k])}</b></div>`).join("") : St.skeleton();
    // (plain text: the row shows it escaped, and the question before removing it names it)
    const removable = st ? [...st.catalog.speech.filter((m) => m.ready && m.id !== S.settings.model).map((m) => [`speech:${m.id}`, t("Speech model “{name}”", { name: m.label }), m.size_mb]),
      ...st.catalog.llm.filter((m) => m.ready && !(app.ai_provider === "local" && app.local_model === m.id)).map((m) => [`llm:${m.id}`, t("Offline model “{name}”", { name: m.label }), m.size_mb])] : [];
    return `<div class="card set-card" id="set-bg"><h3>${t("Background and notifications")}${St.resetBtn ? St.resetBtn("notifications") : ""}</h3>
        ${app.tray ? "" : `<div class="callout info bg-hint">${I("info")}<div>${t("Background checks are off, so you get no notifications about new lectures, announcements or coming quizzes.")}</div>
          <button class="btn sm" data-act="bg-on">${t("Turn them on")}</button></div>`}
        ${row(t("Keep running in the background"), window.LS_LINUX ? t("After you close the window, IEUStudia keeps running in the background (about 50 MB of memory). It checks IEULearn now and then and shows notifications. It never transcribes or downloads anything on its own.") : t("After you close the window, IEUStudia stays as a small icon next to the clock (about 50 MB of memory). It checks IEULearn now and then and shows notifications. It never transcribes or downloads anything on its own."), sw("tray"))}
        ${St.checkEveryRow ? St.checkEveryRow() : ""}
        ${row(window.LS_LINUX ? t("Start at login") : t("Start with Windows"), window.LS_LINUX ? t("Start the background checks when you log in.") : t("Start the background checks when you sign in to Windows."), sw("start_with_windows", !app.tray))}
        ${startupOff ? `<div class="callout warning su-startup-off">${I("alert")}<div>${t("Windows doesn't start IEUStudia when you sign in, although this is on: Windows' own Startup apps list has it turned off, or your antivirus blocked it. Click Turn it on again; if that doesn't help, allow IEUStudia in Task Manager → Startup apps or in your antivirus.")}</div>
          <button class="btn sm" data-act="startup-fix">${t("Turn it on again")}</button></div>` : ""}
        ${row(t("New lecture recordings"), t("Of your own section."), sw("notify_recordings", !app.tray))}
        ${row(t("New announcements"), t("Quiz, exam and homework news is marked as a heads-up."), sw("notify_announcements", !app.tray))}
        ${row(t("New files"), t("Slides and PDFs added to a course."), sw("notify_materials", !app.tray))}
        ${row(t("Quizzes, exams and deadlines coming up"), t("Reminders before quizzes, exams, assignment deadlines and your own calendar entries, from the background checks or from IEUStudia itself while it's open."), sw("notify_reminders"))}
        ${St.reminderRows ? St.reminderRows() : ""}</div>
      <div class="card set-card" id="set-storage"><h3>${t("Storage")}</h3>${use}
        ${St.screensReadingRow ? St.screensReadingRow() : ""}
        ${removable.map(([id, l, mb]) => row(esc(l), t("{size} · not in use", { size: gb(mb) }), `<button class="btn ghost sm danger" data-act="del-component" data-id="${esc(id)}" data-name="${esc(l)}">${I("x", "sm")} ${t("Remove")}</button>`)).join("")}</div>
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
            + row(t("Look for new versions by itself"), t("About once a day IEUStudia asks GitHub, where it's published, whether there's a new version, and fetches a tiny file there so the number of people using each version can be counted. Nothing about you is sent. You decide when to update."), sw("check_updates"));
        })()}
        ${St.contact ? St.contact.card() : ""}
        ${St.sq.about()}
        <div class="su-notice-small">${NOTICE()}</div></div>`;
  };
  async function loadStorage() {
    const r = await call("storage");
    if (r.ok) { U.storage = r; if (S.view.name === "settings") St.renderView(); }
  }
  async function loadStartup() {             // whether Windows really starts IEUStudia at sign-in (not only asked to)
    U.startupAt = Date.now();
    const r = await call("app_config");
    if (!r.ok || r.startup === U.startupReal) return;
    U.startupReal = r.startup;
    if (S.view.name === "settings") St.renderView();
  }
  const startupSaid = (r, asked) => {          // after a switch: the truth, and what to do if Windows said no
    U.startupReal = r.startup; U.startupAt = Date.now();
    if (asked && r.startup === false && !window.LS_LINUX) toast(STARTUP_BLOCKED(), "error");
  };
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
      S.boot.app = r.app; startupSaid(r, el.dataset.key === "start_with_windows" && el.checked); St.renderView(); St.renderTop();
      if (el.dataset.key === "tray") toast(el.checked ? t("Background checks are on: the IEUStudia icon is next to the clock, bottom right.") : t("Background checks are off."), "info");
    },
    "bg-on": async () => {          // back as it was before "Turn off background checks" in the tray menu
      const r = await call("app_config", { tray: true, start_with_windows: true });
      if (!r.ok) { toast(r.error, "error"); return; }
      S.boot.app = r.app; startupSaid(r, true); St.renderView(); St.renderTop();
      toast(window.LS_LINUX ? t("Background checks are on.") : t("Background checks are on: the IEUStudia icon is next to the clock, bottom right."), "info");
    },
    "startup-fix": async () => {    // asked again in IEUStudia: Windows' own Startup apps switch is turned on with it
      const r = await call("app_config", { start_with_windows: true });
      if (!r.ok) { toast(r.error, "error"); return; }
      S.boot.app = r.app;
      startupSaid(r, true);
      if (r.startup) toast(t("Done: IEUStudia starts with Windows again."), "success");
      St.renderView();
    },
    "setup-ai": () => { St.startSetup().then(() => go(STEPS.indexOf("ai"))); },
    "del-component": async (el) => {
      // (its name starts its own sentence: "Remove Speech model “Large v3” from this PC?" had a capital mid-sentence)
      if (!(await St.ask({ title: t("Remove it from this PC?"), text: `${el.dataset.name || ""}: ${t("You can download it again later.")}`,
        ok: t("Remove"), danger: true }))) return;
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
        job: mockJob("claude"), test: M.test.claude || {}, login: (M.cli || {}).claude || {} },
      codex: { available: M.codex, version: M.codex ? "0.157.0" : "", logged_in: M.login.codex, plan: M.login.codex ? "chatgpt" : "", email: "", ours: M.codex, model: "",
        models: M.login.codex ? ["gpt-6.1-sol", "gpt-6-sol-mini"] : [], needs: "a ChatGPT plan with Codex",
        plan_status: M.login.codex ? { ok: null, label: "ChatGPT", note: "Codex does not show your plan; the test message below shows whether it works." } : { ok: null, label: "", note: "" },
        job: mockJob("codex"), test: M.test.codex || {}, login: (M.cli || {}).codex || {} },
      local: { model: "qwen3-1.7b", models: [{ id: "qwen3-1.7b", label: "Basic", description: "Short notes and simple questions; runs on most PCs with 8 GB of memory (Qwen3 1.7B).", size_mb: 1745, ready: false },
        { id: "qwen3-4b", label: "Better", description: "Clearly better notes and answers; needs an NVIDIA graphics card with 8 GB of memory (Qwen3 4B).", size_mb: 4045, ready: false }] } };
    return which ? { ok: true, provider: all.provider, chosen: all.chosen, [which]: all[which] } : all;
  };
  St.mocks.ai_install = (which) => {
    const lines = { claude: ["Looking up the latest Claude Code…", "Downloading Claude Code 2.1.318… 120 of 327 MB · 2.4 MB/s · about 2 min left",
      "Downloading Claude Code 2.1.318… 290 of 327 MB · 2.6 MB/s · less than a minute left", "Checking the download…", "Setting up Claude Code… 0:12"],
      codex: ["Looking up the latest Codex CLI on npm…", "Downloading Codex 0.157.0… 31 of 58 MB · 3.1 MB/s · less than a minute left", "Unpacking…"] }[which];
    M.installing = lines.map((line, i) => setTimeout(() => window.__lsEvent({ type: "ai_setup", which, state: "running", line, error: "" }), 400 + 900 * i));
    M.installing.push(setTimeout(() => { M[which] = true; window.__lsEvent({ type: "ai_setup", which, state: "done", line: "Installed", error: "" }); }, 400 + 900 * lines.length));
    return { ok: true, job: { state: "running", line: "Starting…", error: "" } };
  };
  St.mocks.ai_cli_cancel = (which, what) => {
    if (what === "login") {
      clearTimeout(M.cliDone);
      if (M.cli) M.cli[which] = {};
      return { ok: true, stopped: true };
    }
    (M.installing || []).forEach(clearTimeout);
    setTimeout(() => window.__lsEvent({ type: "ai_setup", which, state: "cancelled", line: "", error: "" }), 100);
    return { ok: true, stopped: true };
  };
  St.mocks.ai_login = (which) => {
    if (which === "chatgpt") {          // "Continue with ChatGPT": the browser opens the page, then comes back after a while
      const link = "http://127.0.0.1:52817/sign-in", page = "https://auth.openai.com/api/accounts/authorize?client_id=dynamic_agent_client";
      if (location.hash.includes("nobrowser")) return { ok: true, state: "waiting", link, page, launched: false, edge: true };    // a PC where nothing opens
      // #slow: the browser starts but never shows the page (after 15 s: Open in Microsoft Edge)
      if (location.hash.includes("slow")) return { ok: true, state: "waiting", link, page, launched: true, browser: "Brave", via: "default", why: "", edge: true };
      setTimeout(() => window.__lsEvent({ type: "chatgpt", state: "waiting", step: "opened", opened: true }), 800);
      M.signIn = setTimeout(() => { M.login.chatgpt = true; window.__lsEvent({ type: "chatgpt", state: "done", error: "", email: "student@example.com", code: "" }); }, 2500);
      if (location.hash.includes("edge")) return { ok: true, state: "waiting", link, page, launched: true, browser: "Microsoft Edge", via: "edge", why: "no_default", edge: true };
      return { ok: true, state: "waiting", link, page, launched: true, browser: "Brave", via: "default", why: "", edge: true };
    }
    // Claude Code / the Codex CLI: their sign-in waits for the browser (#nobrowser: it never comes back by itself)
    M.cli = M.cli || {};
    const page = which === "claude" ? "https://claude.ai/oauth/authorize?code=true&client_id=preview" : "https://auth.openai.com/oauth/authorize?client_id=preview";
    M.cli[which] = { state: "waiting", page, code: "", device: false, asks_code: which === "claude", error: "", started: Date.now() / 1000 };
    if (!location.hash.includes("nobrowser")) {
      M.cliDone = setTimeout(() => { M.login[which] = true; M.cli[which] = { ...M.cli[which], state: "done" }; window.__lsEvent({ type: "ai_cli_login", which, ...M.cli[which] }); }, 3000);
    }
    return { ok: true, ...M.cli[which] };
  };
  St.mocks.ai_cli_open = (which) => ({ ok: true, launched: true, ...((M.cli || {})[which] || {}) });
  St.mocks.ai_cli_code = () => ({ ok: true });
  St.mocks.ai_login_again = () => ({ ok: true, state: "waiting", link: "http://127.0.0.1:52817/sign-in", page: "https://auth.openai.com/api/accounts/authorize?client_id=dynamic_agent_client",
    launched: !location.hash.includes("nobrowser"), browser: "Brave", via: "default", way: "default", again: (M.again = (M.again || 0) + 1) });
  St.mocks.ai_login_edge = () => ({ ok: true, state: "waiting", link: "http://127.0.0.1:52817/sign-in", page: "https://auth.openai.com/api/accounts/authorize?client_id=dynamic_agent_client",
    launched: true, browser: "Microsoft Edge", via: "edge", way: "edge", again: (M.again = (M.again || 0) + 1) });
  St.mocks.ai_login_cancel = () => { clearTimeout(M.signIn); return { ok: true }; };
  St.mocks.ai_login_paste = (text) => (/[?&]code=/.test(text) ? (M.login.chatgpt = true, { ok: true, state: "done", error: "", code: "" })
    : { ok: true, state: "refused", error: "This isn't the address IEUStudia needs. After you allow IEUStudia on OpenAI's page, copy the whole address from your browser's address bar: it starts with http://127.0.0.1 and has code= in it." });
  St.mocks.ai_login_details = () => ({ ok: true, text: "IEUStudia (preview), ChatGPT sign-in" });
  St.mocks.ai_switch_account = () => { M.login.chatgpt = false; delete M.test.chatgpt; return St.mocks.ai_login("chatgpt"); };
  St.mocks.open_default_apps = () => ({ ok: true });
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
