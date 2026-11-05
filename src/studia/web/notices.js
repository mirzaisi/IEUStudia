/* IEUStudia — clear notices where the student must know something: in a chat (material that is missing or cut, kept
 * with the answer it belongs to: usage.notices from chat.py; an AI error with what to do) and in plans, review sheets,
 * study packs and cheatsheets (a failed or stopped run: what Resume keeps; a finished one's own warnings at the top).
 * Amber: a warning, red: an error, blue: a note, green: a tip (notices.css). Plugs into app.js through window.Studia. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, call, esc, I, toast, go, A } = St;
  const ICON = { warning: "alert", error: "alert-circle", info: "info", tip: "sparkle" };
  const acts = {};                      // a notice's button -> what it does (kept by a stable key: pages are redrawn)

  // ---------------------------------------------------------------- one notice
  // n: { kind, title, text, detail, actions: [{ label, icon, primary, act, data } | { label, icon, key, run }] }
  function noticeHTML(n) {
    const kind = ICON[n.kind] ? n.kind : "warning";
    const btn = (a) => {
      if (a.run) acts[a.key] = a.run;
      const data = a.run ? { act: "ntc-act", key: a.key } : { act: a.act, ...(a.data || {}) };
      const attrs = Object.entries(data).map(([k, v]) => `data-${k}="${esc(String(v))}"`).join(" ");
      return `<button type="button" class="btn ${a.primary ? "primary" : ""} sm" ${attrs}>${a.icon ? I(a.icon, "sm") + " " : ""}${esc(a.label)}</button>`;
    };
    const buttons = (n.actions || []).filter(Boolean);
    return `<div class="ntc ntc-${kind}${n.cls ? " " + n.cls : ""}" role="${kind === "error" ? "alert" : "note"}">${I(ICON[kind])}<div class="ntc-body">
      ${n.title ? `<div class="ntc-title">${n.title}</div>` : ""}${n.text ? `<div class="ntc-text">${n.text}</div>` : ""}
      ${n.detail ? `<div class="ntc-detail">${esc(n.detail)}</div>` : ""}
      ${buttons.length ? `<div class="ntc-acts">${buttons.map(btn).join("")}</div>` : ""}</div></div>`;
  }
  const listHTML = (items) => (items.length ? `<div class="ntc-list">${items.map(noticeHTML).join("")}</div>` : "");
  St.noticeHTML = noticeHTML;           // other pages' warnings in the same style ("What this chat sees": chatfiles.js)

  // ---------------------------------------------------------------- an AI error: what it means and what to do
  // in this order: "The offline AI model is not downloaded yet" is set-up, not the network; "Claude took too long to
  // answer" reached Claude (only the app's own network texts say to check the internet connection)
  const RX = [
    ["limit", /usage limit|rate limit|\b429\b|too many requests|kullanım sınır/i],
    ["server", /HTTP 5\d\d|\b50[0234]\b|server error|overloaded|internal error|bad gateway|service unavailable/i],
    ["setup", /not installed|turned off|not downloaded|kurulu değil|kapalı|indirilmedi/i],
    ["signin", /sign[- ]?in|signed in|log ?in|continue with ChatGPT|oturum|giriş|ile devam et|\b40[13]\b|unauthori[sz]ed|forbidden/i],
    ["offline", /internet|network|connection|connect|can't reach|timed? ?out|bağlant|ulaşılam/i],
  ];
  const SETTINGS = /Settings → AI|Ayarlar → Yapay zeka/;   // the message itself sends the student to Settings → AI
  // aierrors.py's own plain words for a failure on the service's side (also its 429 "slow down", not the usage limit)
  const PLAIN_SERVICE = /had a problem on its side|asked IEUStudia to slow down|kendi tarafında bir sorun|yavaşlamasını istedi/i;
  const reason = (msg) => (PLAIN_SERVICE.test(msg || "") ? "server" : (RX.find(([, rx]) => rx.test(msg || "")) || ["other"])[0]);
  const signIn = () => ({ label: t("Open Settings → AI"), icon: "settings", key: "signin", run: () => {
    go({ name: "settings" });
    setTimeout(() => { const c = document.getElementById("set-ai"); if (c) c.scrollIntoView({ block: "start" }); }, 60);
  } });
  // { title, text, actions } for an error message; ``again``: the button that tries again (chat), or none (a plan:
  // its Resume is in the page's header); ``answered``: the AI that failed when the message doesn't name it
  function explain(msg, again, answered) {
    const ai = ((String(msg || "").match(/\b(ChatGPT|Claude|Codex)\b/) || [])[1] || answered || St.aiName()), r = reason(msg);   // the one that failed (t.html escapes it)
    const n = {
      limit: { title: t.html("You've reached {ai}'s usage limit", { ai }), actions: [again] },
      server: { title: t.html("{ai}'s servers had a problem", { ai }), text: t.html("This is on {ai}'s side, not yours or your PC's. Wait a minute, then try again.", { ai }), actions: [again] },
      offline: { title: t.html("IEUStudia couldn't reach {ai}", { ai }), actions: [again] },
      signin: { title: t.html("{ai} needs you to sign in again", { ai }), actions: [signIn(), again] },
      setup: { title: t.html("{ai} isn't set up on this PC", { ai }), actions: [signIn()] },
      other: { title: t.html("{ai} couldn't finish", { ai }), actions: SETTINGS.test(msg || "") ? [signIn(), again] : [again] },
    }[r];
    // the message itself usually says what to do (the app's plain explanation replaces the generic one); a bare server
    // error ("HTTP 500") goes under the explanation, small
    if (r === "server" && PLAIN_SERVICE.test(msg || "")) return { kind: "error", ...n, text: esc(msg) };
    return { kind: "error", ...n, ...(r === "server" ? { detail: msg || "" } : { text: esc(msg || "") }) };
  }
  St.explainError = explain;

  async function download(ids) {
    const r = await call("materials_download", ids);
    toast(r.ok ? t.n(ids.length, "Downloading {n} file", "Downloading {n} files") : r.error, r.ok ? "success" : "error");
  }

  // ---------------------------------------------------------------- chats
  // the AI that answered (chat.py keeps it with the notices: usage.notices_ai); older answers: the one chosen now
  const answeredBy = (u) => ({ chatgpt: "ChatGPT", codex: "ChatGPT", claude: "Claude", local: t("Offline model") })[u.notices_ai] || "";
  const chatCourse = () => ((S.chat && S.chat.data) || {}).course_id || "";
  function chatNotice(n, seq, i, answered) {
    const ai = esc(answered || St.aiName()), key = `c${seq}:${i}`;
    switch (n.code) {
      case "days_gone": return { kind: "warning", title: t.n(n.n, "{n} class day of this chat is no longer in IEUStudia, so {ai} couldn't read it.", "{n} class days of this chat are no longer in IEUStudia, so {ai} couldn't read them.", { ai }) };
      case "not_transcribed": {
        const dates = (n.dates || []).map((d) => esc(St.shortDate(d)));
        return { kind: "warning", title: t.n(n.n, "{n} recording of these lectures isn't transcribed yet, so {ai} didn't get it.", "{n} recordings of these lectures aren't transcribed yet, so {ai} didn't get them.", { ai }),
          text: `${dates.length ? t.list(dates) + ". " : ""}${t.n(n.n, "Once it's transcribed, a new chat about these lectures includes it.", "Once they're transcribed, a new chat about these lectures includes them.")}`,
          actions: [{ label: t("Transcribe…"), icon: "wave", key, run: () => St.runAction && St.runAction(dates.length === 1 ? { action: "transcribe", date: n.dates[0] } : { action: "sync_course" }, chatCourse()) }] };
      }
      case "files_missing": return { kind: "warning", title: t.n(n.n, "{n} chosen slide file isn't on this PC, so {ai} didn't get it.", "{n} chosen slide files aren't on this PC, so {ai} didn't get them.", { ai }),
        text: t.n(n.n, "Download it, then start a new chat about these lectures to include it.", "Download them, then start a new chat about these lectures to include them."),
        actions: (n.ids || []).length ? [{ label: t.n(n.ids.length, "Download {n} file", "Download {n} files"), icon: "download", key, run: () => download(n.ids) }] : [] };
      case "files_cut": return { kind: "warning", title: t.html("The chosen slide files were too long, so {ai} got only their first part.", { ai }), text: t("To send them whole, choose fewer files under Slides.") };
      case "slides_trimmed": return { kind: "info", title: n.files   // the chosen slide files' pictures went first
        ? t.html("{ai} got {sent} of the {total} video slides: the chosen slide files used the other {files} of the {max} pictures one chat can take.", { ai, sent: n.sent, total: n.total, files: n.files, max: n.max || 60 })
        : t.html("{ai} got {sent} of the {total} slide pictures from the videos: the most one chat can take.", { ai, sent: n.sent, total: n.total }) };
      case "no_pictures": return { kind: "info", title: t.html("{ai} can't read pictures, so the slides went as text only.", { ai }) };
      case "pages_unreadable": return { kind: "warning", title: t.n(n.n, "{n} page of the slide files couldn't be turned into a picture, so {ai} got only its text.", "{n} pages of the slide files couldn't be turned into pictures, so {ai} got only their text.", { ai }) };
      case "uploads_unreadable": return { kind: "warning", title: t.n(n.n, "{n} page or photo of your own files couldn't be sent as a picture, so {ai} didn't see it.", "{n} pages or photos of your own files couldn't be sent as pictures, so {ai} didn't see them.", { ai }) };
      // "Read everything" (chatread.py): off, so pages went as text only; on, and some pages still couldn't be read
      case "pictures_left": return { kind: "info", title: t.n(n.n, "{ai} didn't see {n} of the {total} pages of your slides and files whole: it went as text only, or was cut.", "{ai} didn't see {n} of the {total} pages of your slides and files whole: they went as text only, or were cut.", { ai, total: t.num(n.total || n.n) }),
        text: t.html("Turn on Read everything and {ai} goes through every page before it answers.", { ai: answered || St.aiName() }),
        actions: [{ label: t("Read everything"), icon: "list-checks", key, run: () => St.setReadAll && St.setReadAll(true) }] };
      case "pages_not_read": return { kind: "warning", title: t.n(n.n, "{n} page couldn't be read even on a second try, so {ai} didn't get it.", "{n} pages couldn't be read even on a second try, so {ai} didn't get them.", { ai }),
        text: (n.labels || []).length ? esc((n.labels || []).join(", ") + (n.n > n.labels.length ? ", …" : "")) : "" };
      // one request's weight (chatlimits): the first message's pictures, and the ones added with later questions
      case "request_full": return { kind: "info", title: t.n(n.n, "{n} page went as text only: one request to {ai} can't carry more pictures.", "{n} pages went as text only: one request to {ai} can't carry more pictures.", { ai }),
        text: n.read ? t("Read everything reads them first, so their notes come with your question.")
          : t("Choose fewer slide files, or turn on Read everything so these pages are read first.") };
      case "pictures_full": return { kind: "info", title: t.html("This chat already holds as many pictures as {ai} can take in one request: the pages that matter for this question went as notes only.", { ai }),
        text: t("A new chat starts afresh.") };
      case "codex_model": return { kind: "info", title: t.html("Codex doesn't offer {model} with your ChatGPT account, so it answered with its default model.", { model: esc(n.model || "") }),
        text: t("IEUStudia uses Codex's default model from now on. Settings → AI no longer offers that one.") };
      default: return null;
    }
  }
  // { before: the material notices, error: the error notice } of an answer
  St.chatNotices = (m, isLast) => {
    const u = m.usage || {}, answered = answeredBy(u);
    const before = listHTML((u.notices || []).map((n, i) => chatNotice(n, m.seq, i, answered)).filter(Boolean));
    if (m.status !== "error") return { before };
    const again = isLast ? { label: t("Try again"), icon: "retry", primary: true, act: "chat-retry", data: { seq: m.seq } } : null;
    return { before, error: noticeHTML(explain(u.error || "", again, answered)) };
  };

  // ---------------------------------------------------------------- plans, review sheets, study packs, cheatsheets
  // a failed or stopped run: what happened and that Resume keeps the finished steps (the Resume button itself is in
  // the page's header, and a stopped run's own message under its steps)
  St.planStateNotice = (p) => {
    const kept = t("Resume goes on from where it stopped: what's done is kept.");
    if (p.status === "error") {
      const n = explain(p.error || "", null);
      return listHTML([{ ...n, text: [n.text, kept].filter(Boolean).join(" ") }]);
    }
    if (p.status === "paused") return listHTML([{ kind: "info", title: kept }]);
    return "";
  };
  // a finished one's own warnings (material it couldn't use, concepts no video covers, videos dropped), at the top
  St.planWarnings = (p) => {
    const r = p.result || {}, sc = p.scope || {}, cid = p.course_id, out = [];
    const run = (a) => () => St.runAction && St.runAction(a, cid);
    const cp = r.completeness || {};
    if ((cp.missing_list || []).length) {
      out.push({ kind: "warning", title: t.n(cp.missing_list.length, "No checked video teaches {n} part of what you need.", "No checked video teaches {n} parts of what you need."),
        text: `${esc(cp.missing_list.join("; "))}. ${t("The Coverage tab shows the rest; check these in the lecture or the slides.")}` });
    }
    const removed = ((r.changes || {}).removed || []).length;
    if (removed) out.push({ kind: "info", title: t.n(removed, "{n} video was dropped from this plan in the update.", "{n} videos were dropped from this plan in the update.") });
    const files = r.materials_missing || [];
    if (files.length) {
      const ids = files.map((f) => (f && f.id) || f).filter((x) => typeof x === "string");
      out.push({ kind: "warning", title: t.n(files.length, "{n} file of these weeks wasn't on this PC, so the AI couldn't read it.", "{n} files of these weeks weren't on this PC, so the AI couldn't read them."),
        text: t.n(files.length, "Download it and update, so nothing that's only there is missed.", "Download them and update, so nothing that's only there is missed."),
        actions: [{ label: t("Download…"), icon: "download", key: `p${p.id}:dl`, run: ids.length ? () => download(ids) : run({ action: "download_files", weeks: sc.weeks || [] }) }] });
    }
    const days = Array.isArray(r.days_missing) ? r.days_missing.length : r.days_missing || 0;
    if (days) {
      out.push({ kind: "warning", title: t.n(days, "{n} lecture of these weeks isn't transcribed, so the AI couldn't use it.", "{n} lectures of these weeks aren't transcribed, so the AI couldn't use them."),
        text: t.n(days, "Transcribe it and update, so nothing that's only there is missed.", "Transcribe them and update, so nothing that's only there is missed."),
        actions: [{ label: t("Transcribe…"), icon: "wave", key: `p${p.id}:tx`, run: run({ action: "transcribe", weeks: sc.weeks || [] }) }] });
    }
    return listHTML(out);
  };

  // a study pack's or cheatsheet's notes (already worded by studypack.js / cheatsheet.js, in this order): files not on
  // this PC, lectures not transcribed, what the AI left out, the PDF not made yet
  St.packNotices = (r, notes) => {
    const files = (r.materials_missing || []).map((f) => f.id).filter(Boolean), days = (r.days_missing || []).map((d) => d.day_key).filter(Boolean);
    const out = [];
    let i = 0;
    if ((r.materials_missing || []).length) {
      out.push({ kind: "warning", title: notes[i++], actions: files.length ? [{ label: t.n(files.length, "Download {n} file", "Download {n} files"), icon: "download", key: `k${r.title || ""}:dl`, run: () => download(files) }] : [] });
    }
    if ((r.days_missing || []).length) {
      out.push({ kind: "warning", title: notes[i++], actions: days.length ? [{ label: t("Transcribe"), icon: "wave", key: `k${r.title || ""}:tx`, run: async () => {
        let n = 0;
        for (const k of days) { const res = await call("transcribe_day", k, false); if (res.ok) n += res.queued || 0; }
        toast(n ? t.n(n, "{n} recording queued", "{n} recordings queued") : t("Those lectures are already transcribed (or not recorded yet)."), n ? "success" : "info");
      } }] : [] });
    }
    if ((r.uncovered || []).length) out.push({ kind: "warning", title: notes[i++] });
    if (!r.pdf) out.push({ kind: "info", title: notes[i++] });
    return listHTML(out);
  };

  Object.assign(A, {
    "ntc-act": (el) => { const f = acts[el.dataset.key]; if (f) f(); },
  });
})();
