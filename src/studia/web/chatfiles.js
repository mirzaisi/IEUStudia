/* IEUStudia — what a chat sends to the AI, and the student's own files in it.
 * "What this chat sees": a panel on the right of a chat (a drawer in a narrow window) that lists, with their sizes,
 * everything that goes with each question: the lectures' transcripts, the course's slide files from IEULearn, the
 * slides captured from the video, the files the student added, the course's info, the conversation (chatinputs.py).
 * Every piece opens in a viewer over the chat (PDF pages drawn by the app, PowerPoint slides as text and pictures,
 * transcripts as sent, photos), so looking never leaves the chat.
 * Adding files: the paperclip in the composer, dropping files on the chat, or pasting a screenshot. The file goes to
 * the app in pieces and is copied into the chat's own folder (chatfiles.py).
 * Plugs into app.js through window.Studia: chatSideHTML (the panel's slot), chatSideRender (drawn after the chat),
 * chatSideBtn (the head's button), chatAttachBtn and chatFilesRowHTML (the composer). */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, call, esc, I, toast, A } = St;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const PATHS = {
    clip: '<path d="m21.4 11.1-8.8 8.8a5.5 5.5 0 0 1-7.8-7.8l8.8-8.8a3.7 3.7 0 0 1 5.2 5.2l-8.8 8.8a1.8 1.8 0 0 1-2.6-2.6l8.1-8.1"/>',
    image: '<rect x="3" y="3" width="18" height="18" rx="2.5"/><circle cx="9" cy="9" r="1.8"/><path d="m21 15-4.5-4.5L6 21"/>',
    "zoom-in": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5M11 8v6M8 11h6"/>',
    "zoom-out": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5M8 11h6"/>',
    panel: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M15 4v16"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
  };
  const ic = (n, c = "") => (PATHS[n] ? `<svg class="i ${c}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${PATHS[n]}</svg>` : I(n, c));
  const CHUNK = 1536 * 1024;            // bytes per piece sent to the app (about 2 MB as text)
  const PREF = "studia-chat-side";       // the panel shown beside the chat (wide windows): "1" / "0"
  const WIDE = () => window.matchMedia("(min-width: 1400px)").matches;      // narrower: a drawer over the chat
  const F = { data: null, sig: "", seq: 0, busy: [], drawer: false, timer: 0, view: null };
  const view = { ref: "", spec: null, zoom: 1, info: null, page: 1, seq: 0 };

  const sideOn = () => { if (!WIDE()) return F.drawer; try { return localStorage.getItem(PREF) !== "0"; } catch (e) { return true; } };
  const fmtK = (n) => St.fmtK(n || 0);
  const mb = (bytes) => (bytes >= 1e6 ? t("{n} MB", { n: t.num(Math.round(bytes / 1e5) / 10) }) : t("{n} KB", { n: t.num(Math.max(1, Math.round(bytes / 1e3))) }));
  const hex = (n) => Array.from(crypto.getRandomValues(new Uint8Array(n / 2)), (b) => b.toString(16).padStart(2, "0")).join("");
  const extOf = (name) => (String(name).match(/\.([^.]+)$/) || ["", ""])[1].toLowerCase();

  // the chat the page shows, as the app takes it: a chat, or a new chat not asked yet (its files wait under a token)
  function spec() {
    const d = S.chat.draft, c = S.chat.data;
    if (d && d.course_id) return { course_id: d.course_id, days: d.days || [], focus: d.focus || "full", slides: !!d.slides, send: d.send || null, uploads: d.uploads || "",
      model: d.model || "", read_all: d.read_all == null ? null : !!d.read_all };
    if (c && c.id === S.chat.id) return { chat_id: c.id };
    return null;
  }
  const sigOf = (sp) => { const c = S.chat.data; return JSON.stringify([sp, c && !S.chat.draft ? [c.messages.length, c.running, c.send, c.slides, c.model, c.read_all] : 0, (S.system.ai || {}).provider]); };

  // ---------------------------------------------------------------- the panel
  St.chatSideHTML = () => `<aside class="cf-side" id="cf-side" aria-label="${esc(t("What this chat sees"))}"></aside>`;
  St.chatSideBtn = () => (spec() ? `<button class="btn ghost sm cf-side-btn ${sideOn() ? "on" : ""}" data-act="cf-side" title="${esc(t("Everything that goes to {ai} with your questions", { ai: St.aiName() }))}" aria-pressed="${sideOn()}">${ic("panel", "sm")}<span class="lbl">${t("What this chat sees")}</span></button>` : "");
  St.chatSideRender = () => {
    const layout = $(".chat-layout"), side = $("#cf-side");
    if (!layout || !side) return;
    const sp = spec(), on = !!sp && sideOn();
    layout.classList.toggle("cf-on", on);
    layout.classList.toggle("cf-drawer", on && !WIDE());
    $$(".cf-side-btn").forEach((b) => { b.classList.toggle("on", on); b.setAttribute("aria-pressed", String(on)); });
    if (!sp) { F.data = null; F.sig = ""; side.innerHTML = ""; return; }
    const sig = sigOf(sp);
    if (sig !== F.sig) { F.sig = sig; F.data = null; clearTimeout(F.timer); F.timer = setTimeout(load, 120); }
    draw();
  };
  async function load() {
    const sp = spec(); if (!sp) return;
    const n = ++F.seq, sig = F.sig;
    const r = await call("chat_inputs", sp);
    if (n !== F.seq || sig !== F.sig) return;          // the chat changed meanwhile: the latest answer counts
    if (!r.ok) { F.data = { error: r.error }; draw(); return; }
    took(r);
  }
  // fresh sizes: the panel, and the token count the chat's head and the reasoning chooser's warning use
  function took(r) {
    F.data = r;
    const d = S.chat.draft, c = S.chat.data;
    const fit = d && d.slides ? r.video.count : null;       // the video's slides that fit next to the slide files
    const x = d || c, readSig = (v) => JSON.stringify(v && [v.on, v.pages]);
    const head = (d ? d.tokens !== r.material_tokens || d.videoFit !== fit : c && c.context_tokens !== r.material_tokens)
      || (x && readSig(x.read) !== readSig(r.read));
    if (d) { d.tokens = r.material_tokens; d.videoFit = fit; d.read = r.read; } else if (c) { c.context_tokens = r.material_tokens; c.uploads = r.uploads; c.read = r.read; }
    if (head && St.renderChatHead) St.renderChatHead();
    draw(); drawRow();
  }

  const weightOf = (tok) => (tok < 15000 ? ["light", t("Light")] : tok < 50000 ? ["medium", t("Medium")] : ["heavy", t("Heavy")]);
  function row({ ref, icon, title, sub, tokens, extra = "", warn = "" }) {
    const inner = `<span class="cf-ic">${ic(icon, "sm")}</span><span class="cf-txt"><span class="cf-t">${title}</span>${sub ? `<span class="cf-s">${sub}</span>` : ""}${warn ? `<span class="cf-w">${I("alert", "xs")} ${warn}</span>` : ""}</span>`
      + `<span class="cf-tok">${tokens != null ? t("≈{k}", { k: fmtK(tokens) }) : ""}</span>`;
    return ref ? `<div class="cf-row"><button type="button" class="cf-open" data-act="cf-view" data-ref="${esc(ref)}" title="${esc(t("Open it here"))}">${inner}</button>${extra}</div>`
      : `<div class="cf-row static"><div class="cf-open">${inner}</div>${extra}</div>`;
  }
  // a warning or note in the notices' style (notices.js), small; the plain callout without it
  const ICONS = { warning: "alert", error: "alert-circle", info: "info" };
  const note = (kind, html) => (St.noticeHTML ? St.noticeHTML({ kind, text: html, cls: "cf-ntc" })
    : `<div class="callout ${kind} cf-note">${I(ICONS[kind])}<div>${html}</div></div>`);
  const group = (title, body, tools = "") => `<section class="cf-group"><div class="cf-gh"><span>${title}</span>${tools}</div>${body}</section>`;
  const sentAs = (u, local) => (u.native ? t("the whole PDF as a file") : u.sent === "pictures" ? t("as a picture") : u.sent === "text+pictures" ? t.n(u.pictures, "as text + {n} page as a picture", "as text + {n} pages as pictures")
    : u.sent === "text" ? t("as text") : local && u.kind === "image" ? t("not sent: the offline model can't see pictures") : t("not sent"));
  // the pages read in batches first ("Read everything"), after how the rest goes
  const readAs = (x) => (x.read ? t.n(x.read, "{n} read first", "{n} read first") : "");
  // Word or PowerPoint pages that are only a figure the app can't send (no text, no picture): said, not hidden
  const blankAs = (x) => (x.blank ? esc(t.n(x.blank, "{n} page is only a figure: it has no text, and its picture can't be sent.", "{n} pages are only a figure: they have no text, and their pictures can't be sent.")) : "");
  const kindName = (u) => ({ image: t("Photo"), pdf: "PDF", doc: (u.ext || "").toUpperCase(), text: (u.ext || "").toUpperCase() || t("Text") }[u.kind] || "");

  function draw() {
    const side = $("#cf-side"); if (!side || !spec()) return;
    const ai = St.aiName(), r = F.data, drawer = !WIDE();
    const head = `<div class="cf-head"><div class="cf-title">${ic("eye", "sm")} ${t("What this chat sees")}</div>
      <button class="btn ghost icon sm" data-act="cf-side" title="${esc(drawer ? t("Close") : t("Hide this panel"))}">${I("x", "sm")}</button></div>`;
    if (!r) { side.innerHTML = head + `<div class="cf-body"><div class="skel" style="height:64px"></div><div class="skel" style="height:120px;margin-top:10px"></div><div class="skel" style="height:90px;margin-top:10px"></div></div>`; return; }
    if (r.error) { side.innerHTML = head + `<div class="cf-body">${note("error", esc(r.error))}</div>`; return; }
    const local = r.provider === "local";
    const [wk, wl] = weightOf(r.total);
    const pct = Math.min(100, Math.round((r.material_tokens / r.max_tokens) * 100));
    // "a lot to read with your first question": until it is asked (later ones read it from the cache); the whole
    // course's note stays, as every question sends more
    const first = !!S.chat.draft || !(S.chat.data && S.chat.data.messages.length);
    const big = St.effortBig && St.effortBig.chat ? St.effortBig.chat(first ? r.material_tokens : 0, r.focus === "course") : "";
    const out = [];
    out.push(`<div class="cf-total"><div class="cf-sum"><span class="cf-big">${t("≈{k} tokens", { k: fmtK(r.total) })}</span><span class="cf-wt ${wk}">${esc(wl)}</span></div>
      <div class="cf-bar ${wk}"><i style="width:${pct}%"></i></div>
      <div class="cf-s">${t.html("Everything below goes to {ai} with your questions. A chat holds up to {max} tokens.", { ai, max: fmtK(r.max_tokens) })}</div></div>`);
    if (big) out.push(note("warning", big));
    if (local) out.push(note("info", t("The offline model has room for only a few pages: for each question it reads the passages of these that match it best, and no pictures.")));
    // the lectures: each one whole, read in parts first (the whole course's that don't fit whole), or its highlights
    const as = (d) => (d.as === "notes" ? t("read in parts first: its notes go with each question")
      : d.as === "highlights" || r.focus === "highlights" ? t("highlights only") : r.focus === "course" ? t("full transcript") : "");
    const days = r.days.map((d) => row({ ref: `day:${d.key}`, icon: "file-text",
      title: `${d.n ? `<b>L${d.n}</b> ` : ""}${esc(d.label)} · ${esc(d.kind)}`,
      sub: [t.n(d.parts, "{n} part", "{n} parts"), d.seconds ? t.dur(d.seconds) : "", as(d)].filter(Boolean).map(esc).join(" · "),
      tokens: d.tokens }));
    const whole = r.days.filter((d) => d.as === "full").length, inParts = r.days.filter((d) => d.as === "notes").length;
    const daysNote = r.focus === "course" && inParts ? note("info", esc(t.n(inParts, "{n} lecture doesn't fit whole next to the rest: {ai} reads it in parts before it answers, and its notes go with every question. The other {whole} go whole.", "{n} lectures don't fit whole next to the rest: {ai} reads them in parts before it answers, and their notes go with every question. The other {whole} go whole.", { ai, whole: t.num(whole) })))
      : r.focus === "course" && r.days.some((d) => d.as === "highlights") ? note("warning", esc(t("Some lectures don't fit whole and Read everything is off: those go as their highlights only.")))
      : "";
    out.push(group(t.n(r.days.length, "Transcript of {n} class day", "Transcripts of {n} class days"), (days.join("") || `<div class="cf-empty">${t("No transcript")}</div>`) + daysNote));
    // the course's slide files
    const f = r.files;
    const slideTools = `<button class="btn ghost sm cf-mini" data-act="sp-open">${t("Change")}</button>`;
    if (f && (f.files.length || f.missing)) {
      const rows = f.files.map((x) => row({ ref: `material:${x.id}`, icon: x.ext === "pptx" ? "presentation" : "file",
        title: esc(x.title), sub: [(x.ext || "").toUpperCase(), t.n(x.pages, "{n} page", "{n} pages"),
          x.native ? t("the whole PDF as a file") : x.pictures ? t.n(x.pictures, "{n} as a picture", "{n} as pictures") : t("as text"), readAs(x)].filter(Boolean).map(esc).join(" · "),
        tokens: x.tokens, warn: blankAs(x) }));
      const warn = [f.missing ? t.n(f.missing, "{n} chosen file isn't on this PC yet, so it isn't sent.", "{n} chosen files aren't on this PC yet, so they aren't sent.") : "",
        f.cut && !(r.read || {}).on ? t("The files are long: only their beginning goes as text.") : ""].filter(Boolean);
      out.push(group(t("Course slides from IEULearn"), rows.join("") + warn.map((w) => note("warning", esc(w))).join(""), slideTools));
    } else if (!local) out.push(group(t("Course slides from IEULearn"), `<div class="cf-empty">${t("None. Choose some with Send slides.")}</div>`, slideTools));
    if (r.video.count) out.push(group(t("Slides from the video"), row({ ref: "video", icon: "monitor", title: esc(t.n(r.video.count, "{n} picture of the lecture's screen", "{n} pictures of the lecture's screen")), sub: esc(t("with the first question")), tokens: r.video.tokens })));
    // the student's own files
    const ups = (r.uploads || []).map((u) => row({ ref: `upload:${u.id}`, icon: u.kind === "image" ? "image" : u.kind === "pdf" ? "file" : u.ext === "pptx" ? "presentation" : "file-text",
      title: esc(u.name), sub: esc([kindName(u), mb(u.size), sentAs(u, local), readAs(u)].filter(Boolean).join(" · ")), tokens: u.tokens,
      warn: u.read ? blankAs(u) : u.unread ? esc(t("No text could be read from it, and none of its pages goes as a picture: nothing of it is sent."))
        : u.cut ? esc(t("Too long: only its beginning is sent.")) : blankAs(u),
      extra: `<button type="button" class="btn ghost icon sm cf-x" data-act="cf-remove" data-id="${esc(u.id)}" title="${esc(t("Remove {name}", { name: u.name }))}">${ic("trash", "sm")}</button>` }));
    const busy = F.busy.map((b) => `<div class="cf-row static"><div class="cf-open"><span class="cf-ic">${St.SPIN()}</span><span class="cf-txt"><span class="cf-t">${esc(b.name)}</span><span class="cf-s">${b.total ? t("Adding… {pct}", { pct: t.pct(Math.min(1, b.done / b.total)) }) : t("Adding…")}</span></span></div></div>`);
    const left = r.upload_pictures_left_out && !(r.read || {}).on ? note("warning", esc(t.n(r.upload_pictures_left_out, "{n} page goes as text only: one request to {ai} takes up to {max} pictures in all.", "{n} pages go as text only: one request to {ai} takes up to {max} pictures in all.", { max: r.limits.pictures, ai }))) : "";
    out.push(group(t("Your files"), (ups.join("") + busy.join("")) || `<div class="cf-empty">${t("Add photos, PDFs, Word, PowerPoint or text files: with the paperclip, by dropping them here, or by pasting a screenshot.")}</div>`,
      `<button class="btn ghost sm cf-mini" data-act="cf-attach">${ic("clip", "sm")} ${t("Add")}</button>`) + left);
    const reading = readGroup(r, ai, local);
    if (reading) out.push(reading);
    // the rest that goes along
    const rest = [row({ icon: "cap", title: esc(t("Course info")), sub: esc(t("Learning outcomes, weekly plan, assessments, announcements")), tokens: r.brief_tokens }),
      row({ icon: "ai", title: esc(t("Instructions for {ai}", { ai })), sub: esc(t("How to answer and cite, what the app can do")), tokens: r.instructions_tokens })];
    if (r.notes_tokens) rest.push(row({ icon: "list-checks", title: esc(t("Notes of the pages read first")), sub: esc(t("Written by {ai} while reading, page by page: with each question", { ai })), tokens: r.notes_tokens }));
    if (r.conversation.turns) rest.push(row({ icon: "chat", title: esc(t("The conversation so far")), sub: esc(t.n(r.conversation.turns, "{n} question and its answer", "{n} questions and their answers")), tokens: r.conversation.tokens }));
    rest.push(row({ icon: "search", title: esc(t("Matching passages")), sub: esc(t("From your other lectures and the course's files, only when they match a question")), tokens: null,
      extra: `<span class="cf-tok cf-upto">${t("up to ≈{k}", { k: fmtK(r.passages_tokens) })}</span>` }));
    out.push(group(t("Also sent"), rest.join("")));
    if (!S.chat.draft && S.chat.data && S.chat.data.messages.length && r.provider === "claude")
      out.push(`<div class="cf-foot dim sm">${I("info", "xs")} ${t.html("After the first question {ai} reads all this from its cache, which costs much less. A change here makes it read everything again once.", { ai })}</div>`);
    const keep = side.querySelector(".cf-body"), top = keep ? keep.scrollTop : 0;
    side.innerHTML = head + `<div class="cf-body">${out.join("")}</div>`;
    const body = side.querySelector(".cf-body"); if (body) body.scrollTop = top;
  }

  // ---------------------------------------------------------------- "Read everything" (chatread.py)
  // what doesn't fit in one request (pages not seen as pictures, text that was cut) is read in batches before an answer
  function readGroup(r, ai, local) {
    const rd = r.read || {};
    if (!rd.pages && rd.on == null) return "";
    const sw = `<label class="cf-read-sw" title="${esc(t("Read everything"))}"><input type="checkbox" class="switch sm" data-act="cf-read" aria-label="${esc(t("Read everything"))}" ${rd.on ? "checked" : ""}></label>`;
    const lines = [];
    if (!rd.pages) lines.push(t.html("Everything fits in one request: {ai} sees every page whole.", { ai }));
    else if (rd.on) {
      lines.push(t.n(rd.pages, "Before it answers, {ai} goes through {n} page that doesn't fit whole, in {b} requests, and writes notes of it.", "Before it answers, {ai} goes through the {n} pages that don't fit whole, in {b} requests, and writes notes of them.", { ai: esc(ai), b: t.num(Math.max(1, rd.batches || 0)) }));
      if (rd.lectures) lines.push(esc(t.n(rd.lecture_days || 1, "Among them: {n} lecture that doesn't fit whole, in {k} pieces of its transcript.", "Among them: {n} lectures that don't fit whole, in {k} pieces of their transcripts.", { k: t.num(rd.lectures) })));
      lines.push(rd.batches ? t("About {k} tokens read and {o} written, once: the notes are kept and serve every question, so follow-ups reuse them.", { k: St.fmtK(rd.tokens || 0), o: St.fmtK(rd.output || 0) })
        : t("All of them were read before: the kept notes go with your questions."));
      if (rd.cached && rd.batches) lines.push(t.n(rd.cached, "{n} page was read before and isn't read again.", "{n} pages were read before and aren't read again."));
      if (local) lines.push(t("The offline model reads slowly: this can take a while on this PC."));
    } else lines.push(t.n(rd.pages, "{n} page doesn't fit whole in one request: {ai} gets it as text only, or not at all. Turn on Read everything to have {ai} go through it first.", "{n} pages don't fit whole in one request: {ai} gets them as text only, or not at all. Turn on Read everything to have {ai} go through them first.", { ai: esc(ai) }));
    const warn = rd.on && St.effortBig && St.effortBig.read ? St.effortBig.read(rd) : "";
    return group(t("Read everything"), `<div class="cf-read">${lines.map((l) => `<div class="cf-read-l">${l}</div>`).join("")}</div>${warn ? note("warning", warn) : ""}`, sw);
  }
  async function setReadAll(on) {
    const d = S.chat.draft, c = S.chat.data;
    if (on && F.data && F.data.read && St.confirmRead && !(await St.confirmRead(F.data.read))) { draw(); return; }
    if (d) { d.read_all = on; F.sig = ""; St.chatSideRender(); return; }
    if (!c) return;
    const r = await call("chat_update", c.id, { read_all: on });
    if (!r.ok) { toast(r.error, "error"); draw(); return; }
    c.read_all = r.chat.read_all; c.read = r.chat.read; c.context_tokens = r.chat.context_tokens;
    F.sig = ""; St.chatSideRender();
    if (St.renderChatHead) St.renderChatHead();
    if (on && c.messages.length) toast(t("{ai} goes through everything before your next answer.", { ai: St.aiName() }), "info");
  }
  St.setReadAll = (on) => { if (!sideOn()) A["cf-side-open"](); setReadAll(on); };
  // a chat answer while the pages are read (usage.reading), and what was read for it (usage.read). The reading says
  // live which pages each part being read has (two at once), how long it has been at it, how many have come and
  // about how long the rest takes; its clocks tick here every second (tickReading), between the app's news
  const clock = (s) => `${Math.floor(Math.max(0, s) / 60)}:${String(Math.floor(Math.max(0, s) % 60)).padStart(2, "0")}`;
  const now = () => Date.now() / 1000;
  const leftText = (secs) => (secs <= 50 ? t("less than a minute left") : t.n(Math.round(secs / 60), "about {n} minute left", "about {n} minutes left"));
  // 1, 2, 3, 5, 7, 8 → "1–3, 5, 7–8"
  const rangesOf = (nums) => {
    const ns = Array.from(new Set(nums)).sort((a, b) => a - b), out = [];
    for (let i = 0; i < ns.length; i++) {
      let j = i;
      while (j + 1 < ns.length && ns[j + 1] === ns[j] + 1) j++;
      out.push(j > i ? `${t.num(ns[i])}–${t.num(ns[j])}` : t.num(ns[i]));
      i = j;
    }
    return out.join(", ");
  };
  // one part being read (``ai``: escaped already; the values below are numbers or escaped, so t() fills them as they are)
  function partLine(x, rd, ai) {
    const which = t("Part {n} of {total}", { n: t.num(x.n), total: t.num(Math.max(x.n, rd.total || 1)) });
    if (x.retry) {
      const wait = `<span data-until="${+x.retry.until || 0}">${Math.max(0, Math.ceil(x.retry.until - now()))}</span>`;
      const why = x.retry.why === "service"
        ? (x.retry.status ? t("{ai} had a problem on its side (error {code}).", { ai, code: +x.retry.status }) : t("{ai} had a problem on its side.", { ai }))
        : t("The connection to {ai} dropped.", { ai });
      return `<span class="cf-read-part warn">${I("retry", "xs")} ${which} · ${why} ${x.retry.split ? t("Asking again in {s} s, in two smaller parts.", { s: wait }) : t("Asking again in {s} s.", { s: wait })}</span>`;
    }
    // which pages: each file's page numbers ("Week05.pdf p. 1–12, 15"), the video's slides, a lecture's pieces
    const files = x.files || [];
    const pages = files.slice(0, 2).map((f) => {
      const nums = (f.pages || []).filter((p) => p > 0);
      if (f.kind === "video") return esc(t.n((f.pages || []).length, "{n} slide from the video", "{n} slides from the video"));
      if (f.kind === "lecture") {                // (named in the page's language: its name is the AI's, in English)
        const name = f.date ? (f.lecture ? `L${f.lecture} · ` : "") + t.date(f.date, "dmw") : f.name;
        return `${esc(name)} (${esc(t.n(nums.length, "{n} piece of its transcript", "{n} pieces of its transcript"))})`;
      }
      return nums.length ? `${esc(f.name)} ${esc(t("p. {pages}", { pages: rangesOf(nums) }))}` : esc(f.name);
    }).join(", ") + (files.length > 2 ? ", …" : "");
    const bits = [which];
    if (pages) bits.push(pages);
    const since = `<span data-since="${+x.started || 0}">${clock(now() - x.started)}</span>`;
    const got = x.noted ? t.n(x.size, "{noted} of {n} page noted", "{noted} of {n} pages noted", { noted: t.num(x.noted) })
      : x.words ? t.n(x.words, "about {n} word written", "about {n} words written") : t("{ai} is looking at the pages", { ai });
    return `<span class="cf-read-part">${bits.join(" · ")} · ${since} · ${got}${x.try > 1 ? ` · ${t("try {n}", { n: t.num(x.try) })}` : ""}</span>`;
  }
  St.chatReadingHTML = (m) => {
    // (an answer the app was closed in while reading: how far it got was kept with it, and is said as a stop)
    const u = m.usage || {}, rd = u.reading, ai = esc(St.aiName());
    const done = u.read || (m.status !== "streaming" && u.reading_kept ? { ...u.reading_kept, stopped: true } : null);
    if (m.status === "streaming" && !m.text && rd && rd.total) {
      const pct = Math.round(((rd.read || 0) / Math.max(1, rd.pages || 1)) * 100);
      const parts = (rd.parts || []).slice().sort((a, b) => a.n - b.n).map((x) => partLine(x, rd, ai)).join("");
      const left = rd.eta != null && rd.at ? `<span data-eta="${rd.eta}" data-at="${rd.at}">${esc(leftText(rd.eta - (now() - rd.at)))}</span> · ` : "";
      const title = rd.lectures ? t("Reading your lectures, slides and files: {read} of {pages} pieces", { read: t.num(rd.read || 0), pages: t.num(rd.pages || 0) })
        : t("Reading your slides and files: {read} of {pages} pages", { read: t.num(rd.read || 0), pages: t.num(rd.pages || 0) });
      return { body: `<div class="cf-reading" role="status">${St.SPIN()}<div class="cf-reading-t"><b>${title}</b>${parts}
        <span class="dim sm">${left}${t("Stop keeps every page read so far: asking again reads only the rest.")}</span>
        <div class="cf-bar light"><i style="width:${pct}%"></i></div></div></div>`, foot: "" };
    }
    if (!done) return { body: "", foot: "" };
    if (done.stopped || done.failed) {
      const rest = Math.max(0, (done.pages || 0) - (done.read || 0));
      const said = (rest ? t("{read} of {pages} pages were read and kept: asking again reads only the {n} still missing.", { read: t.num(done.read || 0), pages: t.num(done.pages || 0), n: t.num(rest) })
        : t("All {pages} pages were read and kept: asking again doesn't read them again.", { pages: t.num(done.pages || 0) }))
        + (done.failed && done.tries > 1 ? " " + t.n(done.tries, "IEUStudia had tried that part {n} time.", "IEUStudia had tried that part {n} times.") : "");
      return { body: done.stopped && !m.text ? `<div class="cf-reading stopped">${I("info", "sm")}<div class="cf-reading-t"><b>${t("Stopped while reading the slides.")}</b><span class="dim sm">${esc(said)}</span></div></div>` : "",
        foot: done.failed ? `<span class="cf-read-foot dim sm">${esc(said)}</span>` : "" };
    }
    if (done.again || !done.pages) return { body: "", foot: "" };
    const what = t.n(done.pages, "Went through {n} page first", "Went through all {n} pages first") + (done.cached ? ` (${t.n(done.cached, "{n} read before", "{n} read before")})` : "");
    return { body: "", foot: `<span class="cf-read-foot dim sm" title="${esc(t("Pages that didn't fit whole: {ai} read them in batches and answered from its notes and the pages that matter most.", { ai: St.aiName() }))}">${I("list-checks", "xs")} ${esc(what)}</span>` };
  };
  // the reading's clocks (how long a part has been read, the wait before asking again, the time left) tick every second
  function tickReading() {
    const els = $$(".cf-reading [data-since], .cf-reading [data-until], .cf-reading [data-eta]");
    const n = now();
    els.forEach((el) => {
      const d = el.dataset;
      if (d.since != null) el.textContent = clock(n - +d.since);
      else if (d.until != null) el.textContent = String(Math.max(0, Math.ceil(+d.until - n)));
      else el.textContent = leftText(+d.eta - (n - +d.at));
    });
  }
  St.tickReading = tickReading;
  setInterval(tickReading, 1000);

  // ---------------------------------------------------------------- the composer: the paperclip and the files row
  St.chatAttachBtn = () => (spec() || S.chat.draft ? `<button type="button" class="btn ghost icon sm cf-attach" data-act="cf-attach" title="${esc(t("Add files or photos (or drop them here, or paste a screenshot)"))}" aria-label="${esc(t("Add files or photos"))}">${ic("clip")}</button>` : "");
  function rowHTML() {
    const c = S.chat.data, ups = (F.data && !F.data.error ? F.data.uploads : c && !S.chat.draft ? c.uploads : null) || [];
    if (!ups.length && !F.busy.length) return "";
    const chips = ups.slice(0, 6).map((u) => `<span class="cf-chip"><button type="button" data-act="cf-view" data-ref="upload:${esc(u.id)}" title="${esc(t("Open it here"))}">${ic(u.kind === "image" ? "image" : "file", "xs")}<span class="n">${esc(u.name)}</span></button><button type="button" class="x" data-act="cf-remove" data-id="${esc(u.id)}" title="${esc(t("Remove {name}", { name: u.name }))}">${I("x", "xs")}</button></span>`);
    if (ups.length > 6) chips.push(`<button type="button" class="cf-chip more" data-act="cf-side-open">${t("+{n} more", { n: ups.length - 6 })}</button>`);
    F.busy.forEach((b) => chips.push(`<span class="cf-chip busy">${St.SPIN()}<span class="n">${esc(b.name)}</span></span>`));
    return chips.join("");
  }
  St.chatFilesRowHTML = () => `<div class="cf-row-chips" id="cf-chips">${rowHTML()}</div>`;
  function drawRow() { const el = $("#cf-chips"); if (el) el.innerHTML = rowHTML(); }

  // ---------------------------------------------------------------- adding files
  function limits() { return (F.data && F.data.limits) || { files: 100, bytes: { image: 15728640, pdf: 41943040, doc: 41943040, text: 5242880 }, kinds: null }; }
  function kindOf(name) {
    const k = limits().kinds, e = extOf(name);
    if (!k) return /^(png|jpe?g|webp|gif|bmp)$/.test(e) ? "image" : e === "pdf" ? "pdf" : /^(docx|pptx)$/.test(e) ? "doc" : e ? "text" : "";
    return Object.keys(k).find((x) => k[x].includes(e)) || "";
  }
  const readPiece = (blob) => new Promise((done, fail) => { const fr = new FileReader(); fr.onload = () => done(String(fr.result).split(",")[1] || ""); fr.onerror = () => fail(fr.error); fr.readAsDataURL(blob); });
  // files being added (checked, sent in pieces, copied in): a new chat's first question waits for them (app.js sendChat),
  // or they would land in the draft's folder after the chat took it
  F.adding = 0;
  St.chatUploading = () => F.adding > 0;
  async function addFiles(list) {
    const files = Array.from(list || []).filter((f) => f && f.size >= 0);
    if (!files.length) return;
    const d = S.chat.draft;
    if (d && S.chat.sending) {                          // the chat is being made: a file added now would miss it
      toast(t("Your first question is on its way. Add your files once it has gone."), "info");
      return;
    }
    if (d && !d.uploads) d.uploads = hex(16);          // a new chat: its files wait under this until its first question
    const sp = spec(); if (!sp) return;
    F.adding++;
    try { await addEach(files, sp); } finally { F.adding--; }
  }
  async function addEach(files, sp) {
    const lim = limits(), added = [];
    for (const f of files) {
      const kind = kindOf(f.name);
      if (/^hei[cf]$/.test(extOf(f.name))) { toast(t("{name} is a HEIC photo, which can't be added. Share it as JPG instead (on an iPhone: Settings → Camera → Formats → Most Compatible), or take a screenshot of it.", { name: f.name }), "error"); continue; }
      if (!kind) { toast(t("{name} can't be added: photos (PNG, JPG, WEBP), PDFs, Word and PowerPoint files and text files (TXT, MD, CSV, code) can.", { name: f.name }), "error"); continue; }
      if (lim.bytes[kind] && f.size > lim.bytes[kind]) { toast(t("{name} is too big ({size}). Files of this kind can be up to {max}.", { name: f.name, size: mb(f.size), max: mb(lim.bytes[kind]) }), "error"); continue; }
      const ok = await call("chat_upload_check", sp, f.name, f.size);
      if (!ok.ok) { toast(ok.error, "error"); continue; }
      const job = { name: f.name, done: 0, total: f.size };
      F.busy.push(job); drawRow(); draw();
      const token = hex(16);
      let err = "";
      try {
        for (let i = 0; i < Math.max(1, f.size); i += CHUNK) {
          const r = await call("chat_upload_part", token, await readPiece(f.slice(i, i + CHUNK)), i === 0);
          if (!r.ok) { err = r.error; break; }
          job.done = Math.min(f.size, i + CHUNK); draw();
        }
      } catch (e) { err = t("{name} couldn't be read. Is it damaged or password-protected?", { name: f.name }); }
      const r = err ? { ok: false, error: err } : await call("chat_upload", sp, token, f.name);
      F.busy = F.busy.filter((x) => x !== job);
      if (!r.ok) { toast(r.error, "error"); drawRow(); draw(); continue; }
      added.push(r.item.name);
      if (JSON.stringify(spec()) === JSON.stringify(sp)) { F.sig = sigOf(sp); took(r.inputs); }    // still this chat
    }
    if (!added.length) return;
    const c = S.chat.data, later = !S.chat.draft && c && c.messages.length;
    const ai = St.aiName(), n = added.length, name = added[0];
    toast(n === 1 ? (later ? t("{name} added. {ai} gets it with your next question.", { name, ai }) : t("{name} added.", { name }))
      : later ? t.n(n, "{n} file added. {ai} gets it with your next question.", "{n} files added. {ai} gets them with your next question.", { ai })
        : t.n(n, "{n} file added.", "{n} files added."), "success");
  }
  function picker() {
    let inp = $("#cf-input");
    if (!inp) {
      inp = document.createElement("input");
      inp.type = "file"; inp.id = "cf-input"; inp.multiple = true; inp.hidden = true;
      inp.accept = ".png,.jpg,.jpeg,.webp,.gif,.bmp,.pdf,.docx,.pptx,.txt,.md,.csv,.tsv,.json,.tex,.py,.m,.c,.cpp,.h,.java,.js,.ts,.html,.css,.sql,.ipynb,.xml,.yaml,.yml,.log,.r,.cs,.go,.rs,.vhd,.v,.sv,.ino,.asm";
      inp.addEventListener("change", () => { const fs = Array.from(inp.files || []); inp.value = ""; addFiles(fs); });
      document.body.appendChild(inp);
    }
    inp.click();
  }
  // dropping files on the chat, pasting a screenshot into the question box
  const inChat = () => S.view.name === "chat" && !!spec() || (S.view.name === "chat" && !!S.chat.draft);
  const hasFiles = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files");
  let dragDepth = 0;
  document.addEventListener("dragenter", (e) => { if (!hasFiles(e) || !inChat()) return; e.preventDefault(); dragDepth++; const m = $("#chat-main"); if (m) m.classList.add("cf-dropping"); });
  document.addEventListener("dragover", (e) => { if (!hasFiles(e) || !inChat()) return; e.preventDefault(); e.dataTransfer.dropEffect = "copy"; });
  document.addEventListener("dragleave", (e) => { if (!hasFiles(e)) return; dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) { const m = $("#chat-main"); if (m) m.classList.remove("cf-dropping"); } });
  document.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();                       // never let the window open the file instead
    dragDepth = 0; const m = $("#chat-main"); if (m) m.classList.remove("cf-dropping");
    if (inChat()) addFiles(e.dataTransfer.files);
  });
  document.addEventListener("paste", (e) => {
    if (!e.target || e.target.id !== "chat-input" || !e.clipboardData) return;
    const files = Array.from(e.clipboardData.files || []);
    if (!files.length) return;                 // text: pasted as usual
    e.preventDefault();
    const now = new Date(), pad = (n) => String(n).padStart(2, "0");
    const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}.${pad(now.getMinutes())}.${pad(now.getSeconds())}`;
    addFiles(files.map((f, i) => (/^image\//.test(f.type) && (!f.name || /^image\.\w+$/i.test(f.name))
      ? new File([f], `${t("Screenshot")} ${stamp}${files.length > 1 ? `-${i + 1}` : ""}.${(f.type.split("/")[1] || "png").replace("jpeg", "jpg")}`, { type: f.type }) : f)));
  });

  // ---------------------------------------------------------------- the viewer (over the chat)
  function shell(title, sub, tools, body) {
    let box = $("#cf-viewer");
    if (!box) { box = document.createElement("div"); box.id = "cf-viewer"; document.body.appendChild(box); }
    box.innerHTML = `<div class="cf-vbg" data-act="cf-vbg"><div class="cf-v" role="dialog" aria-modal="true" aria-label="${esc(title)}" tabindex="-1">
      <div class="cf-vh"><div class="cf-vt"><div class="t" title="${esc(title)}">${esc(title)}</div>${sub ? `<div class="s">${sub}</div>` : ""}</div>
        <div class="cf-vtools">${tools}</div><button class="btn ghost icon sm" data-act="cf-vclose" title="${esc(t("Close"))}" autofocus>${I("x")}</button></div>
      <div class="cf-vb" id="cf-vb">${body}</div></div></div>`;
    const v = $(".cf-v", box); if (v) v.focus({ preventScroll: true });
    return box;
  }
  const closeViewer = () => { const b = $("#cf-viewer"); if (b) b.remove(); view.ref = ""; view.seq++; };
  const zoomTools = () => `<button class="btn ghost icon sm" data-act="cf-zoom" data-d="-1" title="${esc(t("Zoom out"))}">${ic("zoom-out", "sm")}</button><span class="cf-zv" id="cf-zv">${t.pct(view.zoom)}</span><button class="btn ghost icon sm" data-act="cf-zoom" data-d="1" title="${esc(t("Zoom in"))}">${ic("zoom-in", "sm")}</button>`;
  function lazy(root, sel, fetch) {        // fill each item as it scrolls into view
    const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { io.unobserve(e.target); fetch(e.target); } }), { root, rootMargin: "400px 0px" });
    $$(sel, root).forEach((el) => io.observe(el));
    return io;
  }
  async function openView(ref) {
    const sp = spec(); if (!sp) return;
    view.ref = ref; view.spec = sp; view.zoom = 1; view.page = 1; const n = ++view.seq;
    shell(t("Opening…"), "", "", `<div class="cf-vload">${St.SPIN()}</div>`);
    const r = await call("chat_file_view", sp, ref, 0, 1);
    if (n !== view.seq) return;
    if (!r.ok) { closeViewer(); toast(r.error, "error"); return; }
    view.info = r;
    const local = F.data && F.data.provider === "local", ai = St.aiName();
    if (r.kind === "transcript") {
      const lines = String(r.text || "").split("\n").map((l) => /^# /.test(l) ? `<h3>${esc(l.slice(2))}</h3>` : /^## /.test(l) ? `<h4>${esc(l.slice(3))}</h4>`
        : l.trim() ? `<p>${esc(l).replace(/^\[(\d+:\d{2}(?::\d{2})?)\]/, '<span class="cf-ts">$1</span>')}</p>` : "").join("");
      shell(r.title, t.html("Exactly as {ai} gets it: timestamps, ⭐ for what the lecturer emphasised.", { ai }),
        `<button class="btn ghost sm" data-act="cf-day" data-key="${esc(r.day_key)}" title="${esc(t("Leaves the chat"))}">${I("external", "sm")} ${t("Open the class day")}</button>`,
        `<div class="cf-tr">${lines}</div>`);
    } else if (r.kind === "pdf") drawPdf(r, ref, n, local);
    else if (r.kind === "slides") {
      shell(r.title, t.html("PowerPoint, as {ai} reads it: the text of each slide. Its pictures are shown only here, they aren't sent.", { ai }), "",
        `<div class="cf-slides">${r.slides.map((s, i) => `<div class="cf-slide" data-n="${i + 1}" data-pics="${s.pictures}"><div class="cf-sn">${t("Slide {n}", { n: i + 1 })}</div>
          <div class="cf-stext">${s.text ? esc(s.text) : `<span class="cf-none">${t("No text")}</span>`}</div>${s.pictures ? `<div class="cf-spics"><div class="skel" style="height:90px"></div></div>` : ""}</div>`).join("")}</div>`);
      const root = $("#cf-vb");
      lazy(root, ".cf-slide[data-pics]:not([data-pics='0'])", async (el) => {
        const p = await inTurn(() => (n !== view.seq ? null : call("chat_file_view", sp, ref, +el.dataset.n, 1)));
        if (n !== view.seq || !p) return;
        const box = $(".cf-spics", el); if (!box) return;
        box.innerHTML = p.ok ? p.pictures.map((u) => `<img src="${u}" alt="">`).join("") || `<span class="dim sm">${t("Its pictures can't be shown here.")}</span>` : `<span class="dim sm">${esc(p.error)}</span>`;
      });
    } else if (r.kind === "text") {
      shell(r.title, t.html("As {ai} reads it.", { ai }), zoomTools(), `<pre class="cf-text">${esc((r.pages || []).join("\n\n"))}</pre>`);
    } else if (r.kind === "image") {
      shell(r.title, local ? t("The offline model can't see pictures: this photo isn't sent.") : t.html("{ai} sees this photo as it is.", { ai }), zoomTools(),
        `<div class="cf-img"><img src="${r.data}" alt="${esc(r.title)}"></div>`);
    } else if (r.kind === "strip") {
      shell(t("Slides from the video"), t.html("{n} pictures of the lecture's screen go to {ai} with the first question.", { n: t.num(r.items.length), ai }), "",
        `<div class="cf-strip">${r.items.map((label, i) => `<figure class="cf-shot" data-n="${i + 1}"><div class="cf-ph skel"></div><figcaption>${esc(label.replace(/^\[|\]$/g, ""))}</figcaption></figure>`).join("")}</div>`);
      lazy($("#cf-vb"), ".cf-shot", async (el) => {
        const p = await call("chat_file_view", sp, ref, +el.dataset.n, 1);
        if (n !== view.seq) return;
        const ph = $(".cf-ph", el); if (ph) ph.outerHTML = p.ok ? `<button type="button" class="cf-shot-img" data-act="cf-shot"><img src="${p.data}" alt=""></button>` : `<div class="cf-ph dim sm">${esc(p.error)}</div>`;
      });
    }
  }
  function drawPdf(r, ref, n, local) {
    const ai = St.aiName(), pics = new Set(r.pictured || []);
    const sub = local ? t("The offline model reads the text of these pages that matches each question.")
      : pics.size ? t.html("{ai} reads every page's text; the marked pages also go as pictures.", { ai }) : t.html("{ai} reads the text of every page.", { ai });
    const tools = `<button class="btn ghost icon sm" data-act="cf-page" data-d="-1" title="${esc(t("Previous page"))}">${I("chevron-left", "sm")}</button>
      <span class="cf-pg"><input class="input cf-pgin" id="cf-pgin" type="number" min="1" max="${r.pages}" value="1" aria-label="${esc(t("Page"))}"> / ${t.num(r.pages)}</span>
      <button class="btn ghost icon sm" data-act="cf-page" data-d="1" title="${esc(t("Next page"))}">${I("chevron-right", "sm")}</button><span class="cf-sep"></span>${zoomTools()}`;
    const pages = Array.from({ length: r.pages }, (_, i) => `<div class="cf-page" data-n="${i + 1}">
      <div class="cf-pn">${t("Page {n}", { n: i + 1 })}${pics.has(i + 1) ? ` · <span class="cf-pic">${ic("image", "xs")} ${t("also sent as a picture")}</span>` : ""}</div><div class="cf-ph skel"></div></div>`).join("");
    shell(r.title, sub, tools, `<div class="cf-pages">${pages}</div>`);
    const root = $("#cf-vb");
    lazy(root, ".cf-page", (el) => drawPage(el, ref, n));
    root.addEventListener("scroll", () => {          // the page in view
      const mid = root.getBoundingClientRect().top + root.clientHeight / 3;
      const cur = $$(".cf-page", root).find((p) => p.getBoundingClientRect().bottom > mid);
      if (cur) { view.page = +cur.dataset.n; const inp = $("#cf-pgin"); if (inp && document.activeElement !== inp) inp.value = view.page; }
    }, { passive: true });
  }
  // pages are drawn two at a time (the app draws one PDF page at a time anyway: more would only wait there)
  const pageQueue = { busy: 0, wait: [] };
  function inTurn(fn) {
    return new Promise((done, fail) => {
      const run = () => {
        pageQueue.busy++;
        Promise.resolve().then(fn).then(done, fail).finally(() => { pageQueue.busy--; const next = pageQueue.wait.shift(); if (next) next(); });
      };
      if (pageQueue.busy < 2) run(); else pageQueue.wait.push(run);
    });
  }
  async function drawPage(el, ref, n) {
    if (!el || el.dataset.busy) return;
    el.dataset.busy = "1";
    let z = view.zoom;
    const p = await inTurn(() => (n !== view.seq ? null : call("chat_file_view", view.spec, ref, +el.dataset.n, z = view.zoom)))
      .finally(() => { delete el.dataset.busy; });
    if (n !== view.seq || !p) return;
    const ph = $(".cf-ph, img", el);
    if (!ph) return;
    if (!p.ok) { ph.outerHTML = `<div class="cf-ph dim sm">${esc(p.error)}</div>`; return; }
    ph.outerHTML = `<img src="${p.data}" alt="${esc(t("Page {n}", { n: el.dataset.n }))}" data-z="${z}">`;
  }
  function setZoom(dir) {
    const steps = [0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3];
    const i = steps.findIndex((s) => s >= view.zoom - 0.001);
    view.zoom = steps[Math.max(0, Math.min(steps.length - 1, i + dir))];
    const box = $("#cf-vb"); if (!box) return;
    // the page being read stays where it is: as far into it as before (every page above it grows too)
    const cur = view.info && view.info.kind === "pdf" ? box.querySelector(`.cf-page[data-n="${view.page}"]`) : null;
    const into = cur ? (box.getBoundingClientRect().top - cur.getBoundingClientRect().top) / (cur.offsetHeight || 1) : 0;
    box.style.setProperty("--cf-zoom", view.zoom);
    if (cur) box.scrollTop += cur.getBoundingClientRect().top - box.getBoundingClientRect().top + into * cur.offsetHeight;
    const zv = $("#cf-zv"); if (zv) zv.textContent = t.pct(view.zoom);
    if (view.info && view.info.kind === "pdf" && view.zoom > 1.3)       // sharper pages once zoomed in
      $$(".cf-page img", box).filter((img) => +img.dataset.z <= 1.3).forEach((img) => drawPage(img.closest(".cf-page"), view.ref, view.seq));
  }
  function goPage(nr) {
    const box = $("#cf-vb"), el = box && box.querySelector(`.cf-page[data-n="${nr}"]`);
    if (el) { view.page = nr; el.scrollIntoView({ block: "start" }); const inp = $("#cf-pgin"); if (inp) inp.value = nr; }
  }
  document.addEventListener("keydown", (e) => {
    if (!$("#cf-viewer")) return;
    if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); closeViewer(); return; }
    if (e.target && e.target.id === "cf-pgin" && e.key === "Enter") { e.preventDefault(); goPage(Math.max(1, Math.min(+e.target.max, +e.target.value || 1))); return; }
    if (view.info && view.info.kind === "pdf" && !(e.target && e.target.id === "cf-pgin")) {
      if (e.key === "PageDown" || e.key === "ArrowRight") { e.preventDefault(); goPage(Math.min(view.info.pages, view.page + 1)); }
      if (e.key === "PageUp" || e.key === "ArrowLeft") { e.preventDefault(); goPage(Math.max(1, view.page - 1)); }
    }
    if (e.ctrlKey && ["+", "=", "-"].includes(e.key)) { e.preventDefault(); e.stopImmediatePropagation(); setZoom(e.key === "-" ? -1 : 1); }
  }, true);
  window.addEventListener("resize", () => { if (S.view.name === "chat") St.chatSideRender(); });
  St.inputActs = (St.inputActs || []).concat(["cf-read", "cp-read"]);       // switches: app.js passes their clicks on
  const before = St.after.chat;               // the chat page drawn whole
  // the chat view entered (go() and openChat make a new view; a redraw keeps it): its panel is read again, as a file
  // may have been downloaded or removed, or a lecture transcribed, since the same chat was last shown
  St.after.chat = (v) => { if (before) before(v); if (v !== F.view) { F.view = v; F.sig = ""; } St.chatSideRender(); };

  Object.assign(A, {
    "cf-side": () => {
      if (WIDE()) { try { localStorage.setItem(PREF, sideOn() ? "0" : "1"); } catch (e) { /* stays for this window */ } }
      else F.drawer = !F.drawer;
      St.chatSideRender();
    },
    "cf-side-open": () => { if (WIDE()) { try { localStorage.setItem(PREF, "1"); } catch (e) { /* */ } } else F.drawer = true; St.chatSideRender(); },
    "cf-attach": () => picker(),
    "cf-read": (el) => setReadAll(el.checked),
    "cf-view": (el) => openView(el.dataset.ref),
    "cf-vclose": () => closeViewer(),
    "cf-vbg": (el, e) => { if (e.target === el) closeViewer(); },
    "cf-zoom": (el) => setZoom(+el.dataset.d),
    "cf-page": (el) => goPage(Math.max(1, Math.min((view.info || {}).pages || 1, view.page + +el.dataset.d))),
    "cf-shot": (el) => el.closest(".cf-shot").classList.toggle("big"),
    "cf-day": (el) => { closeViewer(); if (St.openDay) St.openDay(el.dataset.key); },
    "cf-remove": async (el) => {
      const sp = spec(); if (!sp) return;
      const r = await call("chat_upload_remove", sp, el.dataset.id);
      if (!r.ok) { toast(r.error, "error"); return; }
      F.sig = sigOf(sp); took(r.inputs);
    },
  });

  // ---------------------------------------------------------------- browser preview (no app behind the page)
  const demo = { files: {}, parts: {} };
  const pageSvg = (title, nr, dark) => "data:image/svg+xml;utf8," + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="850" height="1100" viewBox="0 0 850 1100"><rect width="850" height="1100" fill="${dark ? "#f4f4f4" : "#fff"}"/><text x="70" y="120" font-family="Segoe UI, sans-serif" font-size="38" font-weight="700" fill="#111">${title}</text><text x="70" y="180" font-family="Segoe UI, sans-serif" font-size="24" fill="#444">Page ${nr}</text>${Array.from({ length: 14 }, (_, i) => `<rect x="70" y="${240 + i * 52}" width="${700 - (i % 3) * 120}" height="14" rx="7" fill="#d4d4d4"/>`).join("")}<circle cx="620" cy="900" r="90" fill="none" stroke="#2563eb" stroke-width="6"/></svg>`);
  Object.assign(St.mocks, {
    chat_inputs: (sp) => {
      const c = sp.chat_id ? St.S.chat.data : null, d = St.S.chat.draft;
      const days = (c ? c.days : (d && d.labels) || []).map((x, i, all) => ({ key: x.key, n: all.length > 1 ? i + 1 : 0, label: x.label, long: x.long || x.label, kind: x.kind, parts: 2, chars: 70000, tokens: (c || d).focus === "highlights" ? 2100 : 22600, seconds: 6000,
        as: (c || d).focus === "highlights" ? "highlights" : "full", pieces: 0 }));
      const key = sp.chat_id || sp.uploads || "none";
      const ups = Object.values(demo.files[key] || {});
      // "Read everything": 15 of Week04's 24 pages don't fit as pictures (app.js's preview chat says the same)
      const x = c || d || {}, readAll = sp.chat_id ? x.read_all : sp.read_all;
      const rd = St.mockReadOf ? St.mockReadOf({ focus: x.focus, read_all: readAll, readDone: !!(c && c.messages.some((m) => (m.usage || {}).read)) }) : { on: false, pages: 0 };
      const files = { files: [{ id: "w4", title: "Week04_Student", label: "Week04_Student.pdf", ext: "pdf", pages: 24, pictures: 9, text_tokens: 6100, tokens: 13300, read: rd.on ? 15 : 0 },
        { id: "w5", title: "Bayes examples", label: "Bayes examples.pptx", ext: "pptx", pages: 12, pictures: 0, text_tokens: 2400, tokens: 2400, read: 0 }], missing: 1, cut: false, pages: 36, page_images: 9 };
      const video = (c ? c.slides : d && d.slides) ? 12 : 0;
      const msgs = c ? c.messages.filter((m) => m.text) : [];
      const conv = msgs.reduce((n, m) => n + Math.round(m.text.length / 3.1), 0);
      const notes = rd.on ? rd.output : 0;
      const material = days.reduce((n, y) => n + y.tokens, 0) + 900 + 2100 + 15700 + video * 800 + ups.reduce((n, u) => n + u.tokens, 0) + notes;
      return { ok: true, provider: (St.S.system.ai || {}).provider || "claude", ai: St.aiName(), focus: (c || d || {}).focus || "full", days, brief_tokens: 900, instructions_tokens: 2100, files,
        video: { count: video, tokens: video * 800 }, uploads: ups.map(({ data, text, ...u }) => u), upload_pictures_left_out: 0, conversation: { turns: msgs.filter((m) => m.role === "user").length, tokens: conv },
        passages_tokens: 1800, material_tokens: material, total: material + conv, max_tokens: 850000, can_images: true, limits: { files: 100, pictures: 500, bytes: { image: 15728640, pdf: 41943040, doc: 41943040, text: 5242880 }, kinds: null }, target: key,
        read: rd, read_all: readAll == null ? null : readAll, notes_tokens: notes, window: 1000000 };
    },
    chat_upload_check: (sp, name, size) => (size > 41943040 ? { ok: false, error: t("{name} is too big ({size}). Files of this kind can be up to {max}.", { name, size: mb(size), max: "40 MB" }) } : { ok: true }),
    chat_upload_part: (token, b64, first) => { demo.parts[token] = (first ? "" : demo.parts[token] || "") + b64; return { ok: true, size: demo.parts[token].length }; },
    chat_upload: (sp, token, name) => {
      const key = sp.chat_id || sp.uploads, e = extOf(name), kind = kindOf(name), id = hex(10);
      const data = demo.parts[token] || ""; delete demo.parts[token];
      const size = Math.floor((data.length * 3) / 4);
      let text = ""; try { if (kind === "text") text = decodeURIComponent(escape(atob(data))); } catch (err) { text = ""; }
      const pages = kind === "pdf" ? 3 : kind === "text" ? 1 : 0;
      const u = { id, name, kind, ext: e, size, pages, text_tokens: kind === "image" ? 0 : Math.round((text.length || 6000) / 3.1), pictures: kind === "image" ? 1 : kind === "pdf" ? 3 : 0, cut: false,
        data: kind === "image" ? `data:image/${e === "jpg" ? "jpeg" : e};base64,${data}` : "", text };
      u.tokens = u.text_tokens + u.pictures * 800; u.sent = u.text_tokens && u.pictures ? "text+pictures" : u.pictures ? "pictures" : "text";
      (demo.files[key] = demo.files[key] || {})[id] = u;
      return { ok: true, item: u, inputs: St.mocks.chat_inputs(sp) };
    },
    chat_adopt: (token, id) => { if (demo.files[token]) { demo.files[id] = demo.files[token]; delete demo.files[token]; } },     // app.js's chat_create
    chat_upload_remove: (sp, id) => { delete (demo.files[sp.chat_id || sp.uploads] || {})[id]; return { ok: true, inputs: St.mocks.chat_inputs(sp) }; },
    chat_file_view: (sp, ref, page) => {
      const [kind, id] = ref.split(/:(.*)/);
      const u = (demo.files[sp.chat_id || sp.uploads] || {})[id];
      if (kind === "day") return { ok: true, kind: "transcript", title: "Wednesday, 14 October 2026", day_key: id, text: "# MATH 240 – Probability for Engineers · Lecture · Wednesday, 14 October 2026 · Lecturer: Dr X\n\n## Part 1 (08:30–09:20)\n[00:31] Today we continue with conditional probability and the multiplication rule.\n[18:41] ⭐ This is exactly the kind of question I will ask in the midterm: Bayes' theorem.\n[21:05] [TR] Şimdi bir örnek yapalım.\n\n## Part 2 (09:30–10:20)\n[05:05] ⭐ bu konu sınavda çıkacak — the screening example.\n[06:10] ⚠ The posterior is about eleven point seven percent." };
      if (kind === "video") return page ? { ok: true, kind: "picture", data: pageSvg(`Slide ${page}`, page, true) } : { ok: true, kind: "strip", items: Array.from({ length: 12 }, (_, i) => `[Part ${1 + (i > 5)}, slide ${i + 1}]`) };
      if (kind === "material" && id === "w5") return page ? { ok: true, kind: "pictures", pictures: [pageSvg("Figure", page, true)] } : { ok: true, kind: "slides", title: "Bayes examples", slides: [{ text: "Bayes' theorem\nP(A|B) = P(B|A) P(A) / P(B)", pictures: 1 }, { text: "Screening tests\nSensitivity, specificity", pictures: 0 }, { text: "", pictures: 1 }] };
      if (kind === "material") return page ? { ok: true, kind: "picture", data: pageSvg("Week 4 · Bayes", page) } : { ok: true, kind: "pdf", title: "Week04_Student", pages: 6, pictured: [2, 3, 5] };
      if (!u) return { ok: false, error: t("That file is no longer in this chat.") };
      if (u.kind === "image") return { ok: true, kind: "image", title: u.name, data: u.data };
      if (u.kind === "pdf") return page ? { ok: true, kind: "picture", data: pageSvg(u.name, page) } : { ok: true, kind: "pdf", title: u.name, pages: 3, pictured: [1, 2, 3] };
      return { ok: true, kind: "text", title: u.name, pages: [u.text || "(text)"] };
    },
  });
})();
