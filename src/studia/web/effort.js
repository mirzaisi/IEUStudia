/* IEUStudia — reasoning levels: how long the AI thinks before it answers (effort.py). Each AI's own levels by their
 * real names ("Low" … "Max" for ChatGPT and Claude, "Thinking off / on" for the offline model): a default per use in
 * Settings → AI, a small chooser wherever AI runs (the chat's composer, a new chat, study notes, plans, review
 * sheets, study packs and cheatsheets), and a calm word before a level or a request that can use a lot of the student's plan — a question once per
 * level and use, a note in the dialogs. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, A, I, esc } = St;
  const ORDER = ["low", "medium", "high", "xhigh", "max"], LOCAL = ["off", "on"], HEAVY = ["high", "xhigh", "max"];
  const GPT = { chat: "medium", plan: "medium", verify: "low", digest: "low" };      // what each use got before
  const DEFAULTS = { chatgpt: GPT, codex: GPT, claude: { chat: "low", plan: "low", verify: "low", digest: "low" },
    local: { chat: "off", plan: "off", verify: "off", digest: "off" } };
  const OK_KEY = "studia-effort-ok";                 // the levels the student already said yes to (per AI and use)
  const BIG_CHAT = 60000, BIG_NOTES = 40000, BIG_PLAN = 100000;       // tokens: "this reads a lot"

  const name = (v) => ({ low: t("Low"), medium: t("Medium"), high: t("High"), xhigh: t("Extra high"), max: t("Max"),
    off: t("Thinking off"), on: t("Thinking on") }[v] || v);
  const about = (v) => ({
    low: t("Quick answers. Uses the least of your plan."),
    medium: t("Thinks a little longer. A good balance for most questions."),
    high: t("Thinks longer about hard questions: slower, and uses more of your plan."),
    xhigh: t("Thinks much longer: an answer can take minutes and uses a lot of your plan."),
    max: t("Thinks as long as it needs: the slowest, and it can use a large part of your plan's limit."),
    off: t("Answers right away."),
    on: t("Thinks first, then answers: can help with hard questions, but every answer takes longer on this PC."),
  }[v] || "");
  const prov = () => (S.system.ai || {}).provider || "";
  const heavy = (v) => HEAVY.includes(v);
  const isLocal = () => prov() === "local";
  // the levels the chosen AI (and ChatGPT model) takes, as the app reports them
  function offered() {
    const ai = S.system.ai || {}, p = prov();
    if ((ai.efforts || []).length) return ai.efforts;
    return p === "local" ? LOCAL : p === "claude" ? ORDER : p === "chatgpt" || p === "codex" ? ["low", "medium", "high"] : [];
  }
  function nearest(v) {                // as effort.nearest: the level, else the nearest lighter one it takes
    const ok = offered(), vocab = isLocal() ? LOCAL : ORDER;
    if (!ok.length || ok.includes(v)) return v;
    const lighter = ok.filter((x) => vocab.indexOf(x) <= Math.max(0, vocab.indexOf(v)));
    return lighter.length ? lighter[lighter.length - 1] : ok[0];
  }
  const setting = (use) => { const p = prov(); return nearest(((S.settings || {}).ai_effort || {})[`${p}.${use}`] || (DEFAULTS[p] || {})[use] || "low"); };
  const usable = () => offered().length > 0;

  // ---- asking once before a heavy level
  function acked() { try { return JSON.parse(localStorage.getItem(OK_KEY) || "[]"); } catch (e) { return []; } }
  function ack(key) { try { localStorage.setItem(OK_KEY, JSON.stringify([...new Set([...acked(), key])])); } catch (e) { /* asked again next time */ } }
  const WHY = () => ({
    chat: t("It applies to your chats and study notes until you change it."),
    here: t("It applies to the next questions in this chat."),
    plan: t("A study plan, review sheet, study pack or cheatsheet makes several long requests, so this adds up."),
    verify: t("Checking videos sends many requests for one plan, each one at this level."),
    digest: t("The weekly digest is a short summary: a light level is plenty."),
  });
  // resolves true when the student keeps the level (asked only the first time for this AI, use and level)
  function confirmHeavy(use, v, extra, prev) {
    const key = `${prov()}.${use}.${v}`;
    if (!heavy(v) || acked().includes(key)) return Promise.resolve(true);
    return new Promise((done) => {
      St.modal(`<h2>${I("alert")} ${t("Use {level} reasoning?", { level: name(v) })}</h2>
        <p class="lead">${esc(about(v))}</p>
        <div class="callout info">${I("info")}<div>${esc(WHY()[use] || "")}${extra ? `<br>${extra}` : ""}</div></div>
        <p class="dim sm">${t("You see this once for each level. You can change it any time.")}</p>
        <div class="actions"><button class="btn" data-act="eff-no">${t("Keep {level}", { level: name(prev || setting(use)) })}</button>
          <button class="btn primary" data-act="eff-yes" autofocus>${t("Use {level}", { level: name(v) })}</button></div>`);
      St.onModalClose = () => done(false);            // Escape or a click outside: the level stays as it was
      window.__eff = { yes: () => { ack(key); done(true); St.closeModal(); }, no: () => St.closeModal() };
    });
  }

  // ---- Settings → AI: the model (Claude) and the reasoning level of each use
  const USES = () => [["chat", t("Chat and study notes"), t("Ask, study notes, exam focus and flashcards."), "ai_model"],
    ["plan", t("Study plans, review sheets and study packs"), t("Works out what to learn from your material and builds the plan, the review sheet or the study pack."), "plan_model"],
    ["verify", t("Checking videos"), t("Reads candidate videos' captions against the course — many small requests, so a light setting is plenty."), "verify_model"],
    ["digest", t("Weekly digest"), t("The short written summary of your week."), "ai_model_light"]];
  const MODELS = () => [["haiku", t("Haiku (lightest)")], ["sonnet", t("Sonnet (recommended)")], ["opus", t("Opus (best, heavy)")]];
  const levelSel = (use, value, attrs) => `<select class="input eff-sel" ${attrs} title="${esc(t("Reasoning: how long the AI thinks before it answers"))}">${offered().map((v) =>
    `<option value="${v}" ${v === value ? "selected" : ""}>${esc(name(v))}</option>`).join("")}</select>`;
  St.aiLevelsHTML = (sys, s) => {
    const ai = sys.ai || {}, p = ai.provider;
    if (!ai.available || !usable()) return "";
    const claude = p === "claude", local = p === "local";
    const row = (title, d, ctl) => `<div class="set-row"><div class="txt"><div class="t">${title}</div><div class="d">${d}</div></div>${ctl}</div>`;
    const model = (key, opts) => `<select class="input" style="width:210px" data-act="set-sel" data-key="${key}">${opts.map(([v, l]) => `<option value="${v}" ${s[key] === v ? "selected" : ""}>${l}</option>`).join("")}</select>`;
    const intro = `<div class="eff-intro dim sm">${I("info", "xs")}<span>${local ? t("Thinking on lets the offline model think before it answers: sometimes better on hard questions, but slower.")
      : t("Reasoning is how long the AI thinks before it answers. Higher levels can help with hard questions, but they are slower and use more of your plan. You can also change it for one chat, or in each dialog, before you start.")}</span></div>`;
    return intro + USES().filter(([use]) => !local || use === "chat").map(([use, title, d, key]) => {
      const models = use === "plan" ? [["", t("Same as chat")], ...MODELS()] : use === "chat" ? MODELS() : MODELS().slice(0, 2);
      const lvl = claude && use === "digest" ? "" : levelSel(use, setting(use), `data-act="eff-set" data-use="${use}"`);
      return row(title, d, `<div class="eff-pair">${claude ? model(key, models) : ""}${lvl}</div>`);
    }).join("");
  };

  // ---- a chooser in a dialog (a new chat, study notes, a plan): starts at the setting, a note when it is heavy
  const picks = {}, info = {};
  function noteHTML(ctx) {
    const x = info[ctx] || {}, v = picks[ctx] || setting(x.use), out = [];
    if (heavy(v)) out.push(`<b>${esc(name(v))}:</b> ${esc(about(v))}${x.use === "plan" || x.use === "verify" ? ` ${esc(WHY()[x.use])}` : ""}`);
    if (x.big) out.push(x.big);
    return out.length ? `<div class="callout warning eff-note">${I("alert")}<div>${out.join("<br>")}</div></div>` : "";
  }
  // ``initial``: the level to start at ("" = the setting); ``more``: {big: a note about a large request, attrs}
  St.effortField = (ctx, use, initial, more) => {
    if (!usable()) return "";
    const fresh = !document.querySelector(`[data-eff-ctx="${ctx}"]`);        // a dialog just opened (not drawn again)
    if (fresh) picks[ctx] = initial && offered().includes(initial) ? initial : "";
    info[ctx] = { use, ...(more || {}) };
    const v = picks[ctx] || setting(use), def = setting(use);
    const label = use === "verify" ? t("Reasoning for checking videos") : isLocal() ? t("Thinking") : t("Reasoning");
    return `<div class="cp-opt eff-opt" data-eff-ctx="${ctx}"><div class="t">${label}</div>
      <select class="input eff-sel" data-act="eff-pick" data-ctx="${ctx}" ${(more || {}).attrs || ""} title="${esc(about(v))}">${offered().map((x) =>
        `<option value="${x}" ${x === v ? "selected" : ""}>${esc(x === def ? t("{level} (default)", { level: name(x) }) : name(x))}</option>`).join("")}</select>
      <div class="eff-note-slot" id="eff-note-${ctx}">${noteHTML(ctx)}</div></div>`;
  };
  // a small chooser beside a button (the weekly digest's summary): kept while the page is drawn again, asked once
  // before a heavy level as the chat's chip
  St.effortInline = (ctx, use) => {
    if (!usable()) return "";
    info[ctx] = { use };
    const v = picks[ctx] && offered().includes(picks[ctx]) ? picks[ctx] : setting(use);
    return `<label class="eff-chip ${heavy(v) ? "hot" : ""}" id="eff-inline-${ctx}" title="${esc(t("Reasoning: how long {ai} thinks before it answers", { ai: St.aiName() }))}\n${esc(name(v))}: ${esc(about(v))}">${I(isLocal() ? "cpu" : "ai", "xs")}
      <select data-act="eff-inline" data-ctx="${ctx}" aria-label="${esc(t("Reasoning"))}">${offered().map((x) =>
        `<option value="${x}" ${x === v ? "selected" : ""}>${esc(name(x))}</option>`).join("")}</select></label>`;
  };
  St.effortPick = (ctx) => picks[ctx] || "";             // what the student chose in that dialog ("" = the setting)
  St.effortName = name;
  // notes about a large request, for the dialogs to show with their estimate
  St.effortBig = {
    chat: (tokens, whole) => [tokens >= BIG_CHAT ? t("That is a lot to read: about {k} tokens with your first question (a long lecture is about 10k). Fewer days or Highlights only use less.", { k: St.fmtK(tokens) }) : "",
      whole ? t("Whole course: each question also sends the matching pages of your slides and PDFs, so every one uses more of your plan than a usual follow-up.") : ""].filter(Boolean).join("<br>"),
    notes: (tokens) => (tokens >= BIG_NOTES ? t("A long lecture: about {k} tokens to read. Without the slide images it uses less.", { k: St.fmtK(tokens) }) : ""),
  };
  // the footer of the plan dialog and of the study pack / cheatsheet dialog (studypack.js): a large plan, the long
  // writing of a review sheet, and a pack or sheet whose many requests each think longer at a heavy level
  St.effortCostNote = (est, scope) => {
    if (!est || !usable()) return "";
    const read = (est.main_input || 0) + (est.verify_input || 0), out = [], kind = (scope || {}).kind;
    const level = nearest((scope || {}).effort || setting("plan"));
    if (kind === "pack" || kind === "sheet") {
      if (kind === "sheet" && read >= BIG_PLAN) out.push(t("This cheatsheet reads a lot: about {k} tokens in all. Fewer weeks or files use less.", { k: St.fmtK(read) }));
      if (heavy(level)) out.push(t("At {level} reasoning every one of its requests also thinks longer, so it uses more than this estimate.", { level: name(level) }));
    } else if (kind === "prep") out.push(t("A review sheet is a long piece of writing: about {k} tokens written, after about {r} read.", { k: St.fmtK(est.main_output || 0), r: St.fmtK(read) }));
    else if (read >= BIG_PLAN) out.push(t("This plan reads a lot: about {k} tokens in all. Fewer weeks use less.", { k: St.fmtK(read) }));
    return out.length ? `<div class="eff-cost dim sm">${I("alert", "xs")} ${out.join(" ")}</div>` : "";
  };

  // ---- the chat's composer: the level of the next questions
  const chatLevel = () => { const d = S.chat.draft, c = S.chat.data; const v = d ? d.effort : c && c.effort; return v ? nearest(v) : setting("chat"); };
  St.effortChip = () => {
    if (!usable() || (!S.chat.draft && !S.chat.data)) return "";
    const v = chatLevel(), ai = St.aiName();
    return `<label class="eff-chip ${heavy(v) ? "hot" : ""}" id="eff-chip" title="${esc(t("Reasoning: how long {ai} thinks before it answers", { ai }))}\n${esc(name(v))}: ${esc(about(v))}">${I(isLocal() ? "cpu" : "ai", "xs")}
      <select data-act="eff-chip" aria-label="${esc(t("Reasoning"))}">${offered().map((x) => `<option value="${x}" ${x === v ? "selected" : ""}>${esc(name(x))}</option>`).join("")}</select></label>`;
  };
  const redrawChip = () => { const el = document.getElementById("eff-chip"); if (el) el.outerHTML = St.effortChip(); };

  Object.assign(A, {
    "eff-yes": () => window.__eff && window.__eff.yes(),
    "eff-no": () => window.__eff && window.__eff.no(),
    "eff-set": async (el) => {                         // Settings
      const use = el.dataset.use, v = el.value;
      if (!(await confirmHeavy(use, v))) { if (S.view.name === "settings") St.renderView(); return; }
      const all = { ...((S.settings || {}).ai_effort || {}), [`${prov()}.${use}`]: v };
      const r = await St.call("update_settings", { ai_effort: all });
      if (!r.ok) { St.toast(r.error, "error"); return; }
      S.settings = r.settings;
      if (S.view.name === "settings") St.renderView();
    },
    "eff-pick": (el) => {                              // a dialog: noted there, nothing asked
      const ctx = el.dataset.ctx;
      picks[ctx] = el.value;
      el.title = about(el.value);
      const slot = document.getElementById(`eff-note-${ctx}`); if (slot) slot.innerHTML = noteHTML(ctx);
    },
    "eff-inline": async (el) => {
      const ctx = el.dataset.ctx, use = (info[ctx] || {}).use, v = el.value;
      if (await confirmHeavy(use, v, "", picks[ctx] || setting(use))) picks[ctx] = v;
      const box = document.getElementById(`eff-inline-${ctx}`); if (box) box.outerHTML = St.effortInline(ctx, use);
    },
    "eff-chip": async (el) => {
      const v = el.value, d = S.chat.draft, c = S.chat.data;
      const extra = !d && c && c.context_tokens ? t("Each question here sends about {k} tokens of lectures.", { k: St.fmtK(c.context_tokens) }) : "";
      if (!(await confirmHeavy("here", v, extra, chatLevel()))) { redrawChip(); return; }
      if (d) { d.effort = v; redrawChip(); return; }
      if (!c) return;
      const r = await St.call("chat_update", c.id, { effort: v });
      if (!r.ok) { St.toast(r.error, "error"); redrawChip(); return; }
      c.effort = r.chat.effort; redrawChip();
      if (c.messages.length && prov() === "claude") St.toast(t("Next answers use {level} reasoning. {ai} reads the lectures again once.", { level: name(v), ai: St.aiName() }), "info");
    },
  });
})();
