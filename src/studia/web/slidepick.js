/* IEUStudia — "send slides" in the chat: which of the course's slides from IEULearn go with the questions (this
 * lecture's, the course's up to it, all of them, or chosen files), as text and page pictures, plus (optionally) the
 * slides captured from the lecture video. Shows what will be sent and how heavy it is; remembered per chat.
 * Plugs into app.js through window.Studia (the composer's tool row calls St.slidePickHTML). */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, call, esc, I, toast, modal, closeModal, A } = St;
  const $ = (s, el = document) => el.querySelector(s);
  const NONE = { mode: "none", files: [], images: true };
  const cur = () => S.chat.draft || S.chat.data;
  const keysOf = (x) => (x.days || []).map((d) => (typeof d === "string" ? d : d.key));
  const multi = (x) => keysOf(x).length > 1;
  const sendOf = (x) => ({ ...NONE, ...(x.send || {}) });
  const weeksText = (w) => (!w || !w.length ? "" : w.length === 1 ? t("week {n}", { n: w[0] }) : t("weeks {a}–{b}", { a: Math.min(...w), b: Math.max(...w) }));
  const mb = (bytes) => (bytes >= 1e6 ? t("{n} MB", { n: t.num(Math.round(bytes / 1e5) / 10) }) : t("{n} KB", { n: t.num(Math.max(1, Math.round(bytes / 1e3))) }));

  function modeLabel(x) {
    const s = sendOf(x), m = multi(x);
    const files = { lecture: m ? t("These lectures' slides") : t("This lecture's slides"),
      upto: m ? t("Slides up to the last lecture") : t("Slides up to this lecture"), all: t("All the course's slides"),
      pick: t.n(s.files.length, "{n} slide file", "{n} slide files") }[s.mode];
    if (files && x.slides) return t("{files} + video", { files });
    return files || (x.slides ? t("Slides from the video") : t("Send slides"));
  }
  // the composer's button (in the tool row above the text box)
  St.slidePickHTML = () => {
    const x = cur(); if (!x || !x.course_id) return "";
    const on = sendOf(x).mode !== "none" || !!x.slides;
    return `<button type="button" class="btn ${on ? "" : "ghost"} sm sp-chip ${on ? "on" : ""}" data-act="sp-open" title="${esc(t("Choose which slides go with your questions"))}">${I("presentation", "sm")} ${esc(modeLabel(x))}</button>`;
  };
  St.slidePick = { summary: () => (st && st.r ? st.r.summary : (S.chat.data || {}).files || null) };   // the size of the slides sent, for others

  let st = null;          // the open chooser: { x, send, slides, r }
  const weight = (tok) => (tok < 15000 ? ["light", t("Light")] : tok < 50000 ? ["medium", t("Medium")] : ["heavy", t("Heavy")]);

  function filesLine(r, ids) {
    const fs = r.files.filter((f) => ids.includes(f.id));
    if (!fs.length) return t("No slide files for this.");
    const pages = fs.reduce((n, f) => n + (f.pages || 0), 0);
    if (fs.length <= 2) return fs.map((f) => `${f.label}${f.pages ? ` · ${t.n(f.pages, "{n} page", "{n} pages")}` : ""}`).join(", ");
    return `${t.n(fs.length, "{n} file", "{n} files")} · ${t.n(pages, "{n} page", "{n} pages")}`;
  }
  function draw() {
    const r = st.r, x = st.x, who = St.aiName();
    if (!r) { show(`<div class="sp"><h2>${I("presentation")} ${t("Slides for this chat")}</h2><div class="skel" style="height:220px;margin:10px 0"></div></div>`); return; }
    const s = r.summary, send = st.send, m = multi(x);
    const lw = r.lecture_weeks || [];
    const lectureDesc = !r.modes.lecture.length ? (lw.length ? t("No slide files for {weeks}.", { weeks: weeksText(lw) }) : t("The weekly plan doesn't say which files go with this date. Choose them yourself."))
      : (r.closest ? t("Nothing for {weeks}: the closest earlier week ({w}).", { weeks: weeksText(lw), w: (r.file_weeks || [])[0] }) + " " : (lw.length ? `${weeksText(lw).replace(/^./, (c) => c.toUpperCase())}: ` : "")) + filesLine(r, r.modes.lecture)
        + ((r.matched || []).length ? " · " + t("checked against what the lecturer taught") : "");
    const modes = [
      ["none", t("No course slides"), t("Only the lecture transcript.")],
      ["lecture", m ? t("These lectures' slides") : t("This lecture's slides"), lectureDesc],
      ["upto", m ? t("Up to the last lecture") : t("Up to this lecture"), filesLine(r, r.modes.upto)],
      ["all", t("All the course's slides"), filesLine(r, r.modes.all)],
      ["pick", t("Choose files"), send.mode === "pick" ? t.n(send.files.length, "{n} file chosen", "{n} files chosen") : t("Pick the files yourself.")]];
    const ids = send.mode === "pick" ? send.files : send.mode === "none" ? [] : r.modes[send.mode] || [];
    const missing = r.files.filter((f) => ids.includes(f.id) && !f.downloaded);
    const byWeek = [];
    for (const f of r.files) {
      const k = f.weeks.length ? weeksText(f.weeks).replace(/^./, (c) => c.toUpperCase()) : t("Other files");
      let g = byWeek.find((x) => x.k === k); if (!g) byWeek.push(g = { k, fs: [] }); g.fs.push(f);
    }
    const picker = send.mode !== "pick" ? "" : `<div class="sp-files">${byWeek.map((g) => `<div class="sp-wk">${esc(g.k)}</div>${g.fs.map((f) => `<label class="sp-file ${f.downloaded ? "" : "off"}">
        <input type="checkbox" class="check" data-act="sp-file" data-id="${esc(f.id)}" ${send.files.includes(f.id) ? "checked" : ""}>
        <span class="n">${esc(f.title)} <span class="dim">· ${esc(f.ext.toUpperCase())}${f.pages ? ` · ${t.n(f.pages, "{n} page", "{n} pages")}` : ""}</span></span>
        ${f.downloaded ? "" : `<span class="pill">${t("Not on this PC")}</span>`}</label>`).join("")}`).join("")}</div>`;
    const canPics = s.can_images && !s.local;
    const pics = canPics ? `<label class="cp-opt cp-switch"><div><div class="t">${t("Pages as pictures")}</div><div class="d">${t.html("{ai} sees formulas, figures and handwriting as they are (PDF files). Up to {n} pictures per chat; the other pages go as text.", { ai: who, n: s.max_images })}</div></div>
        <input type="checkbox" class="switch" data-act="sp-images" ${send.images ? "checked" : ""}></label>` : "";
    const vt = r.video_total || 0;
    const video = `<label class="cp-opt cp-switch"><div><div class="t">${t("Add the slides from the lecture video")}</div><div class="d">${vt ? t.n(vt, "{n} slide captured from the screen recording: also shows what the lecturer wrote or showed that isn't in the files.", "{n} slides captured from the screen recording: also show what the lecturer wrote or showed that isn't in the files.") : t("No slides were captured from these lectures' videos.")}</div></div>
        <input type="checkbox" class="switch" data-act="sp-video" ${st.slides && vt ? "checked" : ""} ${vt ? "" : "disabled"}></label>`;
    const parts = [];
    if (s.files.length) parts.push(t.n(s.files.length, "{n} file", "{n} files") + " · " + (s.page_images ? t("{pages} pages ({pics} as pictures)", { pages: t.num(s.pages), pics: t.num(s.page_images) }) : t.n(s.pages, "{n} page as text", "{n} pages as text")));
    if (s.video_images) parts.push(t.n(s.video_images, "{n} slide from the video", "{n} slides from the video"));
    const [wk, wl] = weight(s.tokens), over = r.tokens > r.max_tokens;
    const notes = [];
    if (s.local) notes.push(t("The offline model has room for only a few pages: for each question it reads the passages of your files that match it."));
    const rd = r.read || {};
    if (s.cut && !rd.on) notes.push(t("The files are long: only their beginning goes as text. Choose fewer files to send them whole."));
    if (s.page_images && s.text_only_pages > 0) notes.push(t("More pages than pictures: the pages that are mostly figures, formulas or handwriting go as pictures first."));
    if (rd.pages) notes.push(rd.on ? t.n(rd.pages, "Read everything is on: {ai} goes through the {n} page that doesn't fit whole before it answers.", "Read everything is on: {ai} goes through the {n} pages that don't fit whole before it answers.", { ai: esc(who) })
      : t.n(rd.pages, "{n} page doesn't fit whole. Read everything (in What this chat sees) has {ai} go through it first.", "{n} pages don't fit whole. Read everything (in What this chat sees) has {ai} go through them first.", { ai: esc(who) }));
    if (s.video_total > s.video_images) notes.push(t("Only {n} of the video's slides fit next to the files.", { n: s.video_images }));
    if (!S.chat.draft && (x.messages || []).length) notes.push(t.html("{ai} reads the chosen slides again with your next question.", { ai: who }));
    const sum = parts.length ? `<div class="sp-sum"><div class="sp-what">${I("layers", "sm")} ${esc(parts.join(" + "))}</div>
        <div class="sp-size"><span class="sp-w ${wk}">${esc(wl)}</span> ${t("≈{k} tokens · ≈{size}", { k: St.fmtK(s.tokens), size: mb(s.bytes) })}
          <span class="dim">· ${t("this chat ≈{k} of {max} tokens", { k: St.fmtK(r.tokens), max: St.fmtK(r.max_tokens) })}</span></div>
        <div class="sp-bar"><i style="width:${Math.min(100, Math.round((r.tokens / r.max_tokens) * 100))}%"></i></div></div>`
      : `<div class="sp-sum"><div class="sp-what dim">${t("No slides go with your questions.")}</div></div>`;
    show(`<div class="sp"><h2>${I("presentation")} ${t("Slides for this chat")}</h2>
      <p class="lead">${t.html("Choose the slides {ai} gets with your questions. Course slides are the files from IEULearn; the slides from the video are pictures of the lecture's screen.", { ai: who })}</p>
      <div class="cp-modes sp-modes">${modes.map(([v, name, d]) => `<button type="button" class="cp-mode ${send.mode === v ? "on" : ""}" data-act="sp-mode" data-val="${v}">
          <span class="cp-mode-t">${name}</span><span class="cp-mode-d">${esc(d)}</span></button>`).join("")}</div>
      ${picker}
      ${missing.length ? `<div class="callout info">${I("download")}<div>${t.n(missing.length, "{n} of these files isn't on this PC yet, so it can't be sent.", "{n} of these files aren't on this PC yet, so they can't be sent.")}
          <div style="margin-top:6px"><button class="btn sm" data-act="sp-download">${I("download", "sm")} ${t("Download them")}</button></div></div></div>` : ""}
      <div class="cp-opts">${pics}${video}</div>
      ${sum}
      ${notes.map((n) => `<div class="dim sm sp-note">${I("info", "xs")} ${n}</div>`).join("")}
      ${over ? `<div class="callout error">${I("alert-circle")}<div>${t("That is more than one chat can hold. Choose fewer files, or turn off the pictures.")}</div></div>` : ""}
      <div class="cp-btns sp-btns"><button class="btn" data-act="modal-close">${t("Cancel")}</button><button class="btn grad" data-act="sp-save" ${over ? "disabled" : ""}>${I("check", "sm")} ${t("Use these slides")}</button></div></div>`);
  }
  // drawn again in place (a new dialog would replay its opening animation at every click), keeping where it was
  function show(html) {
    const box = $("#overlay .modal.sp-modal");
    if (!box) { modal(html); $(".modal").classList.add("sp-modal"); return; }
    const a = document.activeElement, key = a && box.contains(a) && a.dataset ? [a.dataset.act, a.dataset.val || a.dataset.id || ""] : null;
    const list = $(".sp-files", box), top = box.scrollTop, inner = list ? list.scrollTop : 0;
    box.innerHTML = html;
    box.scrollTop = top;
    const nl = $(".sp-files", box); if (nl) nl.scrollTop = inner;
    const again = key && [...box.querySelectorAll(`[data-act="${key[0]}"]`)].find((el) => (el.dataset.val || el.dataset.id || "") === key[1]);
    (again || box).focus({ preventScroll: true });
  }
  async function load() {
    const x = st.x, mine = st, n = st.seq = (st.seq || 0) + 1;
    const own = S.chat.draft === x ? (x.uploads ? `draft-${x.uploads}` : "") : x.id || "";      // the student's files count too
    const r = await call("chat_slides", x.course_id, keysOf(x), x.focus || "full", st.slides, st.send, own, x.model || "", x.read_all == null ? null : x.read_all);
    if (st !== mine || !$(".modal.sp-modal, .modal .sp")) return;          // closed or reopened meanwhile
    if (n !== st.seq) return;                  // an answer to an earlier choice that came late: the latest one counts
    if (!r.ok) { closeModal(); toast(r.error, "error"); return; }
    st.r = r; draw();
  }
  async function save() {
    const x = st.x, send = st.send, slides = !!(st.slides && st.r && st.r.video_total);
    if (S.chat.draft && S.chat.draft === x) {
      x.send = send; x.slides = slides;
      if (st.r) { x.tokens = st.r.tokens; x.slidesTotal = st.r.video_total; }
      closeModal(); St.renderView(); return;
    }
    const r = await call("chat_update", x.id, { send, slides });
    if (!r.ok) { toast(r.error, "error"); return; }
    closeModal();
    if (S.chat.data && S.chat.data.id === x.id) {
      S.chat.data = { ...r.chat, send: r.chat.send || send, slides: r.chat.slides != null && r.chat.send ? r.chat.slides : slides };
      if (St.renderChatHead) St.renderChatHead();
      const chip = $(".sp-chip"); if (chip) chip.outerHTML = St.slidePickHTML();
    }
  }
  Object.assign(A, {
    "sp-open": () => { const x = cur(); if (!x) return; st = { x, send: sendOf(x), slides: !!x.slides, r: null }; draw(); load(); },
    "sp-mode": (el) => {
      const v = el.dataset.val;
      st.send = { ...st.send, mode: v, files: v === "pick" ? (st.send.mode === "pick" ? st.send.files : (st.r && st.send.mode !== "none" ? (st.r.modes[st.send.mode] || []) : [])).filter((id) => st.r && st.r.files.some((f) => f.id === id && f.downloaded)) : [] };
      if (st.r) draw(); load();
    },
    "sp-file": (el) => { const id = el.dataset.id; const fs = st.send.files.filter((f) => f !== id); if (el.checked) fs.push(id); st.send = { ...st.send, files: fs }; load(); },
    "sp-images": (el) => { st.send = { ...st.send, images: el.checked }; load(); },
    "sp-video": (el) => { st.slides = el.checked; load(); },
    "sp-save": () => save(),
    "sp-download": async () => {
      const r = st.r, send = st.send; if (!r) return;
      const ids = (send.mode === "pick" ? send.files : r.modes[send.mode] || []).filter((id) => r.files.some((f) => f.id === id && !f.downloaded));
      const res = await call("materials_download", ids);
      if (!res.ok) { toast(res.error, "error"); return; }
      toast(t("Downloading. Open this again when the files are on your PC."), "info");
    },
  });
  St.inputActs = (St.inputActs || []).concat(["sp-file", "sp-images", "sp-video"]);

  const css = document.createElement("style");
  css.textContent = `
    .sp-chip.on { border-color: color-mix(in srgb, var(--accent) 35%, var(--border)); background: var(--accent-soft); }
    .sp-chip { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .modal.sp-modal { width: min(620px, calc(100vw - 32px)); }
    .sp h2 { display: flex; align-items: center; gap: 8px; margin: 0 0 6px; }
    .sp .lead { margin: 0 0 12px; color: var(--text-2); font-size: 13px; }
    .sp-modes { display: grid; grid-template-columns: 1fr; gap: 6px; }
    .sp-files { max-height: 230px; overflow: auto; border: 1px solid var(--border); border-radius: var(--radius); padding: 4px 8px; margin: 10px 0 0; }
    .sp-wk { font-size: 11.5px; font-weight: 600; color: var(--text-3); text-transform: uppercase; letter-spacing: .03em; margin: 8px 0 2px; }
    .sp-file { display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 13px; cursor: pointer; }
    .sp-file .n { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .sp-file.off { opacity: .55; }
    .sp .cp-opts { margin-top: 12px; display: grid; gap: 8px; }
    .sp-sum { margin-top: 12px; padding: 10px 12px; border-radius: var(--radius); background: var(--surface-2); border: 1px solid var(--border); font-size: 13px; }
    .sp-what { display: flex; align-items: center; gap: 6px; font-weight: 600; }
    .sp-size { margin-top: 4px; color: var(--text-2); }
    .sp-w { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11.5px; font-weight: 600; }
    .sp-w.light { background: var(--green-soft); color: var(--green); }
    .sp-w.medium { background: var(--amber-soft); color: var(--amber); }
    .sp-w.heavy { background: var(--red-soft); color: var(--red); }
    .sp-bar { height: 5px; border-radius: 3px; background: var(--surface-3); margin-top: 8px; overflow: hidden; }
    .sp-bar i { display: block; height: 100%; background: var(--text-3); }
    .sp-note { margin-top: 6px; display: flex; gap: 6px; align-items: flex-start; }
    .sp-btns { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
  `;
  document.head.appendChild(css);

  // browser preview
  St.mocks.chat_slides = (cid, keys, focus, captured, send) => {
    const files = [1, 2, 3, 4, 5, 6, 7].map((w) => ({ id: "f" + w, title: `Week0${w}_Student`, label: `Week0${w}_Student.pdf`, ext: "pdf", weeks: [w], unit: "", pages: 12 + w * 2, downloaded: w !== 7, size: 900000 }));
    files.push({ id: "f0", title: "Math 240 Formula Sheet 2026-27", label: "Math 240 Formula Sheet 2026-27.pdf", ext: "pdf", weeks: [], unit: "", pages: 2, downloaded: true, size: 200000 });
    const s = { mode: "none", files: [], images: true, ...(send || {}) };
    const modes = { lecture: ["f4"], upto: ["f1", "f2", "f3", "f4"], all: files.map((f) => f.id) };
    const ids = s.mode === "pick" ? s.files : s.mode === "none" ? [] : modes[s.mode];
    const fs = files.filter((f) => ids.includes(f.id) && f.downloaded), pages = fs.reduce((n, f) => n + f.pages, 0);
    const video = captured ? Math.min(3, 60 - Math.min(40, pages)) : 0, pics = s.images ? Math.min(pages, 60 - Math.min(video, 20)) : 0;
    const textTok = pages * 260, tokens = textTok + (pics + video) * 800;
    return { ok: true, files, modes, lecture_weeks: [4], file_weeks: [4], closest: false, matched: ["f4"], send: s, provider: "claude", video_total: 3,
      tokens: 23800 * keys.length + 700 + tokens, max_tokens: 150000,
      read: { on: false, auto: true, pages: Math.max(0, pages - pics), batches: 1, tokens: 20000, output: 2000, cached: 0 },
      summary: { mode: s.mode, images_on: s.images, can_images: true, files: fs.map((f) => ({ id: f.id, title: f.title, ext: f.ext, pages: f.pages })), missing: ids.length - fs.length,
        pages, page_images: pics, text_only_pages: pages - pics, video_images: video, video_total: captured ? 3 : 0, text_tokens: textTok, image_tokens: (pics + video) * 800,
        tokens, bytes: pages * 2000 + pics * 95000 + video * 70000, cut: false, max_images: 60, local: false } };
  };
})();
