/* IEUStudia — video study plans: YouTube videos that really teach the course (checked through their captions),
 * ordered for the student's time, with a concept checklist, a coverage table, supplements and practice.
 * Plugs into app.js through window.Studia (views, events, actions). */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, call, esc, I, SPIN, toast, modal, closeModal, go, renderView, renderSidebar, emptyHTML, skeleton, A, fmtK } = St;
  const $ = (s, el = document) => el.querySelector(s);
  const dmw = (iso) => t.date(String(iso || "").slice(0, 10), "dmw");    // Wed 14 Oct · 14 Eki Çar (the day written, not moved by the time zone)
  const ts = (s) => { s = Math.max(0, Math.round(s || 0)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60; return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`; };
  const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  // labels are made when drawn: the Turkish table arrives after this file has loaded
  const STAGES = () => [["gather", t("Course material")], ["concepts", t("What to learn")], ["search", t("YouTube search")], ["inspect", t("Captions")], ["verify", t("Verification")], ["assemble", t("Your plan")]];
  const ROLE = () => ({ required: t("Required"), prerequisite: t("Prerequisite"), optional: t("Optional"), alternative: t("Alternative") });
  const STATE = () => ({ verified: t("Verified"), partial: t("Partly on video"), supplement: t("Written supplement"), unverified: t("Not checked"), missing: t("Missing"), skipped: t("Not in this plan") });
  const HOW = () => ({ video: ["check", t("Taught in a checked video part")], optional: ["check", t("Only in an optional video")], supplement: ["book", t("Explained in the written supplement")], missing: ["x", t("Not covered")] });
  // lectures of these weeks that the material sent to the AI had no room for (a long scope with many files)
  const leftOut = (n) => t.n(n, "{n} lecture did not fit in what the AI read — topics only there may be missing.",
    "{n} lectures did not fit in what the AI read — topics only there may be missing.");
  const ytAt = (vid, sec) => `https://www.youtube.com/watch?v=${encodeURIComponent(vid)}${sec > 0 ? `&t=${Math.floor(sec)}s` : ""}`;
  // captions with "[m:ss]" marks: each mark opens the video at that moment
  const stamped = (vid, text) => esc(text || "").replace(/\[(\d+(?::\d{2}){1,2})\]/g, (m, st) =>
    `<button class="pl-ts" data-act="url" data-url="${esc(ytAt(vid, st.split(":").reduce((a, x) => a * 60 + +x, 0)))}">${st}</button>`);
  const hours = (h) => t.num(h, Number.isInteger(h) ? 0 : 1);           // h already rounded to tenths: 2 · 1.5 (1,5)
  const fmtMin = (m) => (m >= 60 ? t("{h} h", { h: hours(Math.round(m / 6) / 10) }) : t("{m} min", { m }));
  const weekSpan = (weeks) => {
    if (!weeks || !weeks.length) return t("all weeks so far");
    const runs = [];
    weeks.forEach((w) => { const r = runs[runs.length - 1]; if (r && w === r[1] + 1) r[1] = w; else runs.push([w, w]); });
    return runs.map(([a, b]) => (a === b ? t("week {n}", { n: a }) : t("weeks {a}–{b}", { a, b }))).join(", ");
  };

  S.plans = null;           // list (briefs)
  S.planCache = {};         // full plans by id
  S.planTab = "watch";

  // ------------------------------------------------------------------ data
  async function loadPlans() {
    const r = await call("plan_list");
    if (!r.ok) { toast(r.error, "error"); return; }
    S.plans = r.plans;
    if (S.view.name === "plans") renderView();
    renderSidebar();
  }
  async function loadPlan(id, quiet) {
    const r = await call("plan_get", id);
    if (!r.ok) { if (!quiet) toast(r.error, "error"); return; }
    S.planCache[id] = r.plan;
    if (S.view.name === "plan" && S.view.planId === id) renderView();
  }
  const running = () => (S.plans || []).filter((p) => ["running", "queued"].includes(p.status)).length;
  const courseOf = (id) => St.findCourse(id) || {};
  // a review sheet made before the rename (2.6.x "prep packs"): its saved title read with the new name (not saved)
  const planTitle = (p) => {
    const x = p.title || "", m = (p.scope || {}).kind === "prep" && x.match(/^(.+?) (?:prep pack|için hazırlık paketi)$/);
    return m ? t("{what} review sheet", { what: m[1] }) : x;
  };

  // ------------------------------------------------------------------ list view
  function stateLine(p) {
    if (p.status === "running" || p.status === "queued") return `<span class="pl-run">${SPIN()} ${esc(p.message || t("Working…"))}</span><span class="bar"><i style="width:${Math.round(100 * (p.progress || 0))}%"></i></span>`;
    if (p.status === "done") return `<span class="dim">${esc((p.scope || {}).kind === "prep" ? t("Review sheet ready") : p.message || t("Ready"))}</span>`;
    if (p.status === "error") {       // an AI's error: its short title (notices.js), the whole text on hover and on the plan's page;
      // any other (YouTube, IEULearn, a file) is already plain and short
      const ai = p.error && St.explainError && /\b(ChatGPT|Claude|Codex)\b/.test(p.error);
      const short = ai ? St.explainError(p.error, null).title : esc(p.error || t("Failed"));
      return `<span class="err-txt" title="${esc(p.error || "")}">${I("alert-circle", "sm")} ${short}</span>`;
    }
    if (p.status === "paused") return `<span class="dim">${I("stop", "sm")} ${esc(p.message || t("Stopped"))}</span>`;
    return `<span class="dim">${esc(p.message || "")}</span>`;
  }
  function plansView() {
    const list = S.plans;
    const head = `<div class="pl-head"><div><h1>${t("Study plans")}</h1>
      <div class="dim">${t("YouTube videos that really teach your course (checked through their captions, not their titles), review sheets before a quiz, and study packs that teach your material from start to finish.")}</div></div>
      <div class="pl-acts"><button class="btn" data-act="sheet-new">${I("sigma", "sm")} ${t("Cheatsheet")}</button>
      <button class="btn" data-act="pack-new">${I("book", "sm")} ${t("Study pack")}</button>
      <button class="btn primary" data-act="plan-new">${I("plus", "sm")} ${t("New plan")}</button></div></div>`;
    if (list == null) return `<div class="view-inner">${head}${skeleton()}</div>`;
    if (!list.length) {
      return `<div class="view-inner">${head}${emptyHTML("youtube", t("No study plans yet"),
        t("Pick a course and the weeks (or an upcoming quiz). IEUStudia reads your slides and lectures, finds videos on YouTube, checks what they actually teach, and builds a plan with a checklist and practice."))}</div>`;
    }
    const groups = {};
    list.forEach((p) => { (groups[p.course_id] = groups[p.course_id] || []).push(p); });
    let h = `<div class="view-inner">${head}`;
    for (const cid of Object.keys(groups)) {
      const c = courseOf(cid);
      h += `<div class="section-title">${esc(c.code || t("Course"))} <span class="dim sm">${esc(c.display_name || "")}</span></div><div class="pl-list">`;
      h += groups[cid].map((p) => `<div class="card pl-card ${p.status}" data-act="plan-open" data-id="${esc(p.id)}" tabindex="0" role="button">
        <div class="pl-title">${I(p.status !== "done" ? "hourglass" : ({ prep: "cap", pack: "book", sheet: "sigma" })[(p.scope || {}).kind] || "youtube", "sm")}<b>${esc(planTitle(p) || (p.status === "done" ? t("Study plan") : t("New plan")))}</b>
          <span class="pill">${({ prep: t("Review sheet"), pack: t("Study pack"), sheet: t("Cheatsheet") })[(p.scope || {}).kind] || t("Videos")}</span>${p.parent_id ? `<span class="pill">${t.c("noun", "Update")}</span>` : ""}</div>
        <div class="pl-scope">${esc(weekSpan((p.scope || {}).weeks))}${(p.scope || {}).focus ? ` · ${t.html("for {focus}", { focus: p.scope.focus })}` : ""}${["prep", "pack", "sheet"].includes((p.scope || {}).kind) ? "" : ` · ${t("{h} h", { h: hours(Math.round(((p.scope || {}).minutes || 0) / 60 * 10) / 10) })}`} · ${esc(dmw(p.created_at))}</div>
        <div class="pl-state">${stateLine(p)}</div></div>`).join("");
      h += `</div>`;
    }
    return h + `</div>`;
  }

  // ------------------------------------------------------------------ plan view
  function planView(v) {
    const p = S.planCache[v.planId] || (S.plans || []).find((x) => x.id === v.planId);
    if (!p) return `<div class="view-inner">${skeleton()}</div>`;
    const c = courseOf(p.course_id);
    const sc = p.scope || {};
    const chips = [weekSpan(sc.weeks), sc.focus ? t("for {focus}", { focus: sc.focus }) : "", sc.los && sc.los.length ? sc.los.map((n) => "LO" + n).join(", ") : "",
      sc.kind === "prep" ? "" : t("{h} h of viewing", { h: hours(Math.round((sc.minutes || 0) / 60 * 10) / 10) }), sc.deadline ? t("by {date}", { date: dmw(sc.deadline) }) : ""].filter(Boolean);
    const busy = p.status === "running" || p.status === "queued";
    let h = `<div class="view-inner plan-page">
      <div class="pl-top"><div class="grow"><div class="dim">${esc(c.code || "")} · ${sc.kind === "prep" ? t("exam review sheet") : t("video study plan")}</div>
        <h1>${esc(planTitle(p) || (busy ? t("Building your plan…") : t("Study plan")))}</h1>
        <div class="pl-chips">${chips.map((x) => `<span class="chip">${esc(x)}</span>`).join("")}</div></div>
        <div class="pl-acts">
          ${p.status === "done" ? `<button class="btn sm" data-act="plan-update" data-id="${esc(p.id)}">${I("refresh", "sm")} ${t("Update")}</button>
            <button class="btn ghost sm" data-act="plan-file" data-id="${esc(p.id)}">${I("file-text", "sm")} ${t("Open as Markdown")}</button>` : ""}
          ${busy ? `<button class="btn sm" data-act="plan-stop" data-id="${esc(p.id)}">${I("stop", "sm")} ${t("Stop")}</button>` : ""}
          ${["error", "paused"].includes(p.status) ? `<button class="btn primary sm" data-act="plan-resume" data-id="${esc(p.id)}">${I("retry", "sm")} ${t("Resume")}</button>` : ""}
          <button class="btn ghost icon sm" data-act="plan-delete" data-id="${esc(p.id)}" title="${t("Delete this plan")}">${I("x", "sm")}</button></div></div>`;
    if (p.status === "done" && St.planWarnings) h += St.planWarnings(p);       // what it couldn't use or cover (notices.js)
    if (p.status !== "done") h += progressHTML(p);
    else if (sc.kind === "prep") h += prepHTML(p);
    else h += resultHTML(p);
    return h + `</div>`;
  }
  function prepHTML(p) {
    const r = p.result || {};
    const miss = St.planWarnings ? 0 : (r.materials_missing || []).length;      // notices.js warns at the top instead
    return `${miss ? `<div class="note-sm">${I("info", "sm")}<span>${t.n(miss, "{n} file of these weeks was not downloaded, so the review sheet could not use it.", "{n} files of these weeks were not downloaded, so the review sheet could not use them.")}</span></div>` : ""}
      ${r.days_left_out ? `<div class="note-sm">${I("info", "sm")}<span>${leftOut(r.days_left_out)}</span></div>` : ""}
      <div class="note-sm">${I("youtube", "sm")}<span>${t("A review sheet is written only, with no videos. For YouTube videos of these weeks, make a video study plan.")}</span>
        <button class="btn sm" data-act="plan-new" data-id="${esc(p.course_id)}" data-kind="videos" data-focus="${esc((p.scope || {}).focus || "")}" data-weeks="${esc(((p.scope || {}).weeks || []).join(","))}">${I("youtube", "sm")} ${t("Video plan")}</button></div>
      <div class="card pl-prep md">${St.mdToHtml(r.markdown || "")}</div>`;
  }
  function progressHTML(p) {
    const stages = (p.scope || {}).kind === "prep" ? [["gather", t("Course material")], ["write", t("Writing the pack")]] : STAGES();
    const cur = stages.findIndex(([k]) => k === p.stage);
    const done = new Set(p.stages_done || []);
    const steps = stages.map(([k, l], i) => {
      const st = done.has(k) || (cur > i) ? "done" : i === cur && ["running", "queued"].includes(p.status) ? "now" : i === cur ? "stopped" : "";
      return `<div class="pl-step ${st}"><span class="dot">${st === "done" ? I("check", "xs") : st === "now" ? SPIN() : i + 1}</span>${l}</div>`;
    }).join("");
    const chip = (c) => `<span class="chip ${c.focus ? "on" : ""} ${c.in_scope === false ? "extra" : ""}" title="${esc([c.summary, ...(c.skills || []).map((s) => "• " + s)].filter(Boolean).join("\n"))}">${esc(c.name)}</span>`;
    const cs = p.concepts || [], extras = cs.filter((c) => c.in_scope === false);
    const concepts = cs.filter((c) => c.in_scope !== false).map(chip).join("");
    const notice = St.planStateNotice ? St.planStateNotice(p) : "";      // failed or stopped: what to do (notices.js; Resume is in the header)
    return `${notice}<div class="card pl-progress"><div class="pl-steps">${steps}</div>
      <div class="pl-msg">${p.status === "error" ? (notice ? "" : `<span class="err-txt">${I("alert-circle", "sm")} ${esc(p.error)}</span>`) : esc(p.message || "")}</div>
      ${["running", "queued"].includes(p.status) ? `<div class="bar wide"><i style="width:${Math.round(100 * (p.progress || 0))}%"></i></div>` : ""}
      ${concepts ? `<div class="pl-concepts"><div class="dim sm">${(p.scope || {}).focus ? t.html("Concepts to cover for {focus} (hover for their skills)", { focus: p.scope.focus }) : t("Concepts to cover (hover for their skills)")}</div>${concepts}</div>` : ""}
      ${extras.length ? `<div class="pl-concepts"><div class="dim sm">${(p.scope || {}).extras ? t.html("Also in these weeks, not on {focus} — optional videos", { focus: (p.scope || {}).focus || t("the focus") })
        : t.html("Also in these weeks, not on {focus} — listed, no videos", { focus: (p.scope || {}).focus || t("the focus") })}</div>${extras.map(chip).join("")}</div>` : ""}
      <div class="note-sm">${I("info", "sm")}<span>${t("Nothing is downloaded from YouTube — IEUStudia reads each candidate's captions and chapters to check what it really teaches. You can keep using the app meanwhile.")}</span></div></div>`;
  }
  function teachHTML(teach, names) {
    const name = esc(names[teach.concept] || teach.concept);
    if (teach.state === "unchecked") return `<div class="pl-teach unchecked"><b>${name}</b> <span class="dim">— ${t("not checked in this video's captions")}</span></div>`;
    const sk = [...(teach.skills || []).map((s) => `<span class="sk video">${I("check", "xs")}${esc(s)}</span>`),
      ...(teach.missing || []).map((s) => `<span class="sk missing" title="${t("Not taught in this video")}">${I("x", "xs")}${esc(s)}</span>`)].join("");
    return `<div class="pl-teach"><b>${name}</b>${teach.state === "partial" && !(teach.skills || []).length ? ` <span class="dim">— ${t("only partly")}</span>` : ""}${sk ? `<div class="sks">${sk}</div>` : ""}</div>`;
  }
  function checkedHTML(it) {
    const q = it.quotes || [], parts = it.excerpts || [];
    if (it.state === "unchecked") return `<div class="pl-checked unverified">${I("alert", "xs")}<span>${it.concepts.length === 1 ? t("Not checked: its captions were not read for this concept.") : t("Not checked: its captions were not read for these concepts.")}</span></div>`;
    if (it.state === "unverified" || !it.captions && !parts.length) {
      return `<div class="pl-checked ${it.state}">${I(it.state === "verified" ? "check-circle" : "alert", "xs")}<span>${it.evidence ? t.html("Checked: {what} — “{quote}”", { what: it.inspected, quote: it.evidence }) : t.html("Checked: {what}", { what: it.inspected })}</span></div>`;
    }
    const kind = (it.captions || "").split(",")[0];     // whose captions: "auto" (YouTube's) or "manual" (the creator's)
    const caps = it.state === "verified"
      ? { auto: t("Checked in its automatic captions"), manual: t("Checked in its creator's captions") }[kind] || t("Checked in its captions")
      : { auto: t("Its automatic captions"), manual: t("Its creator's captions") }[kind] || t("Its captions");
    return `<div class="pl-checked ${it.state}">${I(it.state === "verified" ? "check-circle" : "alert", "xs")}<span>${caps}${q.length ? ` — ${t("what it says:")}` : it.evidence ? ` — “${esc(it.evidence)}”` : ""}</span></div>
      ${q.map((x) => `<div class="pl-quote"><button class="pl-ts" data-act="url" data-url="${esc(x.url || ytAt(it.video, x.t))}" title="${t("Open at {time}", { time: ts(x.t) })}">${ts(x.t)}</button><q>${esc(x.text)}</q></div>`).join("")}
      ${parts.length ? `<details class="pl-parts"><summary>${parts.length === 1 ? t("Read the caption part the AI checked") : t("Read the caption parts ({n}) the AI checked", { n: parts.length })}</summary>
        ${parts.map((x) => `<div class="pl-part"><div class="pl-part-h">${ts(x.start)}–${ts(x.end)}</div><div class="pl-part-t">${stamped(it.video, x.text)}</div></div>`).join("")}</details>` : ""}`;
  }
  function itemHTML(p, it, names, goal) {
    const marks = p.marks || {};
    const done = marks["v:" + it.n];
    const segs = (it.segments || []).length ? it.segments : [{ start: it.start, end: it.end, url: it.url }];
    const part = it.whole ? t("whole video") : segs.length > 1 ? t.n(segs.length, "{n} part", "{n} parts") : `${ts(segs[0].start)}–${ts(segs[0].end)}`;
    const teaches = it.teaches || null;
    return `<div class="pl-item ${done ? "watched" : ""} role-${it.role} ${it.extra ? "extra" : ""}">
      <label class="pl-check" title="${t("Watched")}"><input type="checkbox" data-act="plan-mark" data-id="${esc(p.id)}" data-key="v:${it.n}" ${done ? "checked" : ""}></label>
      <button class="pl-thumb" data-act="url" data-url="${esc(it.url)}" title="${t.html("Open on YouTube at {time}", { time: ts(segs[0].start) })}"><img src="https://i.ytimg.com/vi/${encodeURIComponent(it.video)}/mqdefault.jpg" alt="" loading="lazy"><span class="len">${esc(part)}</span></button>
      <div class="pl-info"><div class="pl-it-title"><span class="n">${it.n}</span><button class="link" data-act="url" data-url="${esc(it.url)}">${esc(it.title)}</button>${St.yt ? St.yt.menu(it) : ""}</div>
        <div class="pl-it-meta">${St.yt ? St.yt.channel(it) : esc(it.channel)} · ${t("{m} min", { m: it.minutes })} · <span class="role">${esc(ROLE()[it.role] || it.role)}</span>${it.alternative_to ? ` ${t("instead of {n}", { n: it.alternative_to })}` : ""}${it.extra ? ` <span class="pill extra" title="${t.html("A topic of these weeks that {goal} does not ask for", { goal })}">${t.html("Extra — not on {goal}", { goal })}</span>` : ""}</div>
        ${St.yt ? `<div class="yt-stats">${St.yt.stats(it)}</div>${St.yt.covers(it, names)}` : ""}
        ${!it.whole ? `<div class="pl-segs">${I("play", "xs")}<span>${t("Watch")}</span>${segs.map((s) => `<button class="pl-seg" data-act="url" data-url="${esc(s.url || ytAt(it.video, s.start))}">${ts(s.start)}–${ts(s.end)}</button>`).join(`<span class="dim">${t("then")}</span>`)}</div>` : ""}
        ${teaches ? `<div class="pl-teaches">${teaches.map((x) => teachHTML(x, names)).join("")}</div>`
          : `<div class="pl-it-concepts">${it.concepts.map((c) => `<span class="chip">${esc(names[c] || c)}</span>`).join("")}</div>`}
        ${it.why ? `<div class="pl-why">${esc(it.why)}</div>` : ""}
        ${it.limits ? `<div class="pl-limits">${I("info", "xs")}<span>${esc(it.limits)}</span></div>` : ""}
        ${(it.errors || []).map((e) => `<div class="pl-err">${I("alert", "xs")}<span><b>${t("Possible mistake at {time}:", { time: `<button class="pl-ts" data-act="url" data-url="${esc(e.url || ytAt(it.video, e.t))}">${ts(e.t)}</button>` })}</b> ${esc(e.text)} <span class="dim">${t("(spotted in the captions by AI)")}</span></span></div>`).join("")}
        ${checkedHTML(it)}</div></div>`;
  }
  function completenessHTML(r) {
    const cp = r.completeness;
    if (!cp) return "";
    const ext = cp.extras || [];
    const withVid = ext.filter((x) => (x.items || []).length);
    const rows = [];
    if ((cp.missing_list || []).length) rows.push(`<div class="pl-cp-row missing">${I("x", "xs")}<span><b>${t("Not covered:")}</b> ${esc(cp.missing_list.join("; "))}</span></div>`);
    if ((cp.supplement_list || []).length) rows.push(`<div class="pl-cp-row supplement">${I("book", "xs")}<span><b>${t("Only in the written supplements:")}</b> ${esc(cp.supplement_list.join("; "))}</span></div>`);
    if (ext.length) {
      const rest = ext.length - withVid.length;
      const others = withVid.length;       // some extras have a video: the rest are "the others"
      const tail = cp.extras_searched ? (others ? t("No checked video for the others.") : t("No checked video for them."))
        : others ? t("This plan does not cover the others — update it with “Extras” on to add videos.") : t("This plan does not cover them — update it with “Extras” on to add videos.");
      rows.push(`<div class="pl-cp-row extra">${I("layers", "xs")}<span><b>${t.html("Also in these weeks, not on {goal}:", { goal: cp.goal })}</b> ${ext.map((x) => esc(x.name) + ((x.items || []).length ? ` <span class="dim">${t("(optional {items})", { items: "#" + x.items.join(", #") })}</span>` : "")).join(", ")}.
        ${rest ? tail : ""}</span></div>`);
    }
    const miss = (r.materials_missing || []).length, dm = r.days_missing || 0;
    if (miss || dm) {
      const lost = [miss ? t.n(miss, "{n} file was not downloaded", "{n} files were not downloaded") : "",
        dm ? t.n(dm, "{n} class day was not transcribed", "{n} class days were not transcribed") : ""].filter(Boolean);
      const based = t("Based on {files} and {lectures} of these weeks.", { files: t.n(r.materials_used || 0, "{n} file", "{n} files"), lectures: t.n(r.days_used || 0, "{n} lecture", "{n} lectures") });
      rows.push(`<div class="pl-cp-row src">${I("file", "xs")}<span>${based} ${lost.length > 1 ? t("{a} and {b} — topics only there may be missing.", { a: lost[0], b: lost[1] }) : t("{a} — topics only there may be missing.", { a: lost[0] })}</span></div>`);
    }
    if (r.days_left_out) rows.push(`<div class="pl-cp-row src">${I("file", "xs")}<span>${leftOut(r.days_left_out)}</span></div>`);
    if (cp.over_budget) rows.push(`<div class="pl-cp-row over">${I("clock", "xs")}<span>${t("The required videos take {time} — {n} min more than the {budget} you planned.", { time: fmtMin(cp.required_minutes), n: cp.over_budget, budget: fmtMin(cp.budget) })}</span></div>`);
    const good = cp.level ? ["full", "mostly"].includes(cp.level) : cp.complete;
    return `<div class="pl-complete ${good ? "ok" : "no"}"><div class="pl-cp-head">${I(good ? "check-circle" : "alert", "sm")}<b>${esc(cp.headline)}</b></div>${rows.join("")}</div>`;
  }
  function coverageHTML(r, names) {
    const cov = r.coverage || [];
    const goal = (r.completeness || {}).goal || t("the focus");
    const extras = cov.filter((c) => c.in_scope === false);
    const state = STATE(), how = HOW();
    const row = (cv) => `<tr><td><b>${esc(cv.name || names[cv.concept] || cv.concept)}</b>${cv.note ? `<div class="dim sm">${esc(cv.note)}</div>` : ""}</td>
      <td><span class="st st-${cv.state}">${esc(state[cv.state] || cv.state)}</span></td>
      <td>${(cv.skills || []).map((s) => `<div class="sk ${s.how}" title="${esc((how[s.how] || [])[1] || "")}">${I((how[s.how] || ["info"])[0], "xs")}<span>${esc(s.text)}${(s.items || []).length ? ` <span class="dim">#${s.items.join(", #")}</span>` : s.how === "supplement" ? ` <span class="dim">${t("supplement")}</span>` : ""}</span></div>`).join("") || `<span class="dim">—</span>`}</td>
      <td>${(cv.items || []).map((n) => `<span class="n">${n}</span>`).join(" ") || "—"}</td></tr>`;
    const table = (rows) => `<table><thead><tr><th>${t("Concept")}</th><th>${t("State")}</th><th>${t("Skills")}</th><th>${t("Videos")}</th></tr></thead><tbody>${rows.map(row).join("")}</tbody></table>`;
    return `<div class="card pl-coverage">
      <div class="pl-legend dim sm">${Object.entries(how).map(([k, [ic, l]]) => `<span class="sk ${k}">${I(ic, "xs")}${l}</span>`).join("")}</div>
      ${extras.length ? `<div class="pl-cov-h">${t.html("On {goal}", { goal })}</div>` : ""}${table(cov.filter((c) => c.in_scope !== false))}
      ${extras.length ? `<div class="pl-cov-h">${t.html("Also in these weeks — not on {goal}", { goal })}</div>${table(extras)}` : ""}
      ${r.gaps ? `<div class="note-sm">${I("info", "sm")}<span>${t.html("Not in your material: {gaps}", { gaps: r.gaps })}</span></div>` : ""}</div>`;
  }
  function resultHTML(p) {
    const r = p.result || {};
    const names = {}; (r.concepts || []).forEach((c) => { names[c.id] = c.name; });
    const marks = p.marks || {};
    const watched = (r.items || []).filter((it) => marks["v:" + it.n]).length;
    const ticked = (r.checklist || []).filter((x) => marks["c:" + x.concept]).length;
    const cp = r.completeness;
    const goal = (cp || {}).goal || (p.scope || {}).focus || t("the focus");
    const tab = S.planTab;
    const tabs = [["watch", t("Watch"), "youtube", (r.items || []).length], ["checklist", t("Checklist"), "list-checks", (r.checklist || []).length],
      ["coverage", t("Coverage"), "target", 0], ["practice", t("Practice"), "pencil", (r.practice || []).length], ["supplements", t("Supplements"), "book", (r.supplements || []).length]]
      .filter(([k, , , n]) => k === "coverage" || n);
    const miss = (r.materials_missing || []).length;
    let h = `<div class="card pl-summary">${completenessHTML(r)}<p>${esc(r.summary || "")}</p>
      <div class="pl-stats"><div><b>${t("{m} min", { m: r.total_minutes || 0 })}</b>${t("required")}${r.optional_minutes ? ` · ${t("+{m} optional", { m: r.optional_minutes })}` : ""}</div>
        <div><b>${watched}/${(r.items || []).length}</b>${t("watched")}</div><div><b>${ticked}/${(r.checklist || []).length}</b>${t("concepts ticked")}</div>
        ${cp ? `<div><b>${cp.video + cp.optional}/${cp.skills}</b>${t("skills on video")}</div>` : `<div><b>${(r.coverage || []).filter((c) => c.state === "verified").length}/${(r.coverage || []).length}</b>${t("verified")}</div>`}</div>
      ${!cp && miss ? `<div class="note-sm">${I("info", "sm")}<span>${t.n(miss, "{n} file of these weeks were not downloaded, so the plan could not read them.", "{n} files of these weeks were not downloaded, so the plan could not read them.")}</span></div>` : ""}
      <div class="note-sm">${I("info", "sm")}<span>${t("The videos were checked by AI through their captions, and AI can make mistakes — the quotes under each video show what it actually says.")}</span></div>
      ${changesHTML(r.changes)}</div>
      <div class="tabs" role="tablist">${tabs.map(([k, l, ic, n]) => `<button class="tab ${tab === k ? "on" : ""}" data-act="plan-tab" data-tab="${k}">${I(ic, "sm")}${l}${n ? `<span class="count">${n}</span>` : ""}</button>`).join("")}</div>`;
    if (tab === "checklist") {
      h += `<div class="card pl-checklist">${(r.checklist || []).map((x) => `<label class="pl-cl ${marks["c:" + x.concept] ? "done" : ""}">
        <input type="checkbox" data-act="plan-mark" data-id="${esc(p.id)}" data-key="c:${esc(x.concept)}" ${marks["c:" + x.concept] ? "checked" : ""}>
        <div><b>${esc(x.name || names[x.concept] || x.concept)}</b><div>${esc(x.explain)}</div>
        ${(x.recognize || []).length ? `<ul class="pl-cues">${x.recognize.map((q) => `<li>${esc(q)}</li>`).join("")}</ul>` : ""}</div></label>`).join("")}</div>`;
    } else if (tab === "coverage") {
      h += coverageHTML(r, names);
    } else if (tab === "practice") {
      h += (r.practice || []).map((q, i) => `<div class="card pl-q"><div class="q"><span class="n">${i + 1}</span>${esc(q.question)}</div>
        ${q.source ? `<div class="dim sm">${t.html("From {source}", { source: q.source })}${names[q.concept] ? ` · ${esc(names[q.concept])}` : ""}</div>` : ""}
        <details><summary>${t("Show the answer")}</summary><div class="md">${St.mdToHtml(q.answer || "")}</div></details></div>`).join("");
    } else if (tab === "supplements") {
      h += (r.supplements || []).map((s) => `<div class="card pl-sup"><h3>${esc(s.name || names[s.concept] || s.concept)}</h3><div class="md">${St.mdToHtml(s.text)}</div></div>`).join("");
    } else {
      if ((r.schedule || []).length) {
        h += `<div class="pl-schedule">${r.schedule.map((s) => `<div class="pl-day"><b>${esc(s.day)}</b><span>${s.items.map((n) => `#${n}`).join(" ")} · ${t("{m} min", { m: s.minutes })}</span></div>`).join("")}</div>`;
      }
      const all = r.items || [], hidden = St.yt ? all.filter(St.yt.hidden) : [];     // blocked channels (youtube.js)
      const items = St.yt && !St.yt.showHidden(p) ? all.filter((it) => !hidden.includes(it)) : all;
      h += `${St.yt ? St.yt.head(p, items, hidden) : ""}<div class="pl-items">${items.map((it) => itemHTML(p, it, names, goal)).join("")}</div>`;
    }
    return h;
  }
  function changesHTML(ch) {
    if (!ch || !((ch.added || []).length || (ch.replaced || []).length || (ch.removed || []).length)) return "";
    const li = (x) => (typeof x === "object" && x ? `${esc(x.old || "")}${x.new ? ` → #${esc(x.new)}` : ""}${x.why ? ` — ${esc(x.why)}` : ""}` : esc(x));
    return `<div class="pl-changes"><b>${t("What changed since the previous version")}</b><ul>
      ${(ch.added || []).length ? `<li>${t("Added: {items}", { items: ch.added.map((n) => `#${esc(n)}`).join(", ") })}</li>` : ""}
      ${(ch.replaced || []).map((x) => `<li>${t("Replaced: {item}", { item: li(x) })}</li>`).join("")}
      ${(ch.removed || []).map((x) => `<li>${t("No longer needed: {item}", { item: li(x) })}</li>`).join("")}</ul></div>`;
  }

  // ------------------------------------------------------------------ new plan / update dialog
  const D = { opts: null, courseId: null, scope: null, est: null, parent: null };
  const weekEnd = (weeks, no) => { const w = weeks.find((x) => x.no === no) || {}; return w.end || w.start || ""; };   // the quiz may be any day that week
  async function newPlanDialog(courseId, parent, kind, focus, weeks) {
    const courses = S.courses.filter((c) => c.code);
    D.courseId = courseId || (S.view.name === "course" ? S.view.courseId : null) || (courses[0] || {}).id;
    D.parent = parent || null;
    D.est = null;
    modal(`<h2>${I("youtube")} ${parent ? t("Update the study plan") : t("New video study plan")}</h2>${skeleton()}`);
    const r = await call("plan_options", D.courseId);
    if (!r.ok) { closeModal(); toast(r.error, "error"); return; }
    D.opts = r;
    const cur = r.weeks.find((w) => w.current);
    const next = (focus && r.assessments.find((a) => a.name === focus)) || r.assessments.find((a) => !r.weeks.find((w) => w.no === a.week && w.past));
    const base = parent ? { ...parent.scope } : null;
    D.scope = base || (next ? { weeks: next.weeks, focus: next.name, los: next.los, minutes: 180, deadline: weekEnd(r.weeks, next.week) }
      : { weeks: cur ? [cur.no] : [], focus: "", los: [], minutes: 180, deadline: "" });
    D.scope = { kind: kind || "videos", topics: "", difficulties: "", allow_tr: false, extras: false, model: r.model || "sonnet", ...D.scope };
    // a ChatGPT plan from before reasoning levels: its Quick / Standard / Thorough, as plans.levels() reads it
    const legacy = { haiku: "low", sonnet: "medium", opus: "high" }[base && base.model];
    if (base && !base.effort && legacy && St.isChatGPT()) D.scope.effort = legacy;
    if (!["en", "tr", "both"].includes(D.scope.video_lang)) D.scope.video_lang = D.scope.allow_tr ? "both" : St.yt ? St.yt.lang() : "en";
    if (!D.scope.deadline) { const d = new Date(); d.setDate(d.getDate() + 7); D.scope.deadline = isoDay(d); }
    if (weeks && weeks.length && !parent) D.scope.weeks = weeks.filter((n) => r.weeks.some((w) => w.no === n));   // asked from the chat
    drawDialog();
    estimate();
  }
  function costHTML() {
    const e = D.est, ai = (D.opts && D.opts.ai) || {};
    if (!(ai.available && ai.can_plans !== false)) return `<span class="err-txt">${I("alert-circle", "sm")} ${(S.system.ai || {}).provider === "local" ? t("Plans, review sheets and study packs need ChatGPT or Claude (the offline model is too small).") : t("Set up ChatGPT or Claude in Settings → AI first.")}</span>`;
    if (!e) return `<span class="dim">${SPIN()} ${t("Estimating…")}</span>`;
    return `<div>${t.html("<b>≈{k} tokens</b> from your {ai} plan", { k: fmtK(e.main_input + (e.verify_input || 0)), ai: St.aiName() })} · ${t("{files} files and {lectures} lectures read", { files: e.materials_used.length, lectures: e.days_used.length })}${e.minutes ? ` · ${esc(e.minutes)}` : ""}</div>
      ${St.effortCostNote ? St.effortCostNote(e, D.scope) : ""}<div class="dim sm">${I("info", "xs")} ${t("Runs in the background. AI can make mistakes — the plan shows the caption quotes it relied on.")}</div>`;
  }
  function drawDialog() {
    const r = D.opts, sc = D.scope, courses = S.courses.filter((c) => c.code);
    const oldBody = $(".modal .cp-scroll"), keepBody = oldBody ? oldBody.scrollTop : 0;
    const oldWeeks = $(".pd-weeks2"), keepWeeks = oldWeeks ? oldWeeks.scrollTop : 0;
    const sel = new Set(sc.weeks);
    const weeks = r.weeks.filter((w) => w.start || w.topic);
    const picked = weeks.filter((w) => sel.has(w.no));
    const missing = [...new Set(picked.flatMap((w) => w.missing))];      // (a file of a unit for two weeks once)
    const todo = picked.flatMap((w) => w.todo_days || []);
    const ai = r.ai || {};
    const prep = sc.kind === "prep";
    const models = [["sonnet", t("Sonnet (recommended)")], ["haiku", t("Haiku (lightest)")], ["opus", t("Opus (best, heavy)")]];
    const kinds = [["videos", "youtube", t("Video study plan"), t("Finds YouTube videos for these weeks and checks each one's transcript (captions) to see what it really teaches.")],
      ["prep", "cap", t("Exam review sheet"), t("A short written review: key ideas per learning outcome, a formula sheet, practice questions with solutions.")],
      ["pack", "book", t("Study pack"), t("A complete PDF that teaches everything in the files and lectures you pick, with worked examples and figures.")],
      ["sheet", "sigma", t("Cheatsheet"), t("Every formula, definition and method of the files and lectures you pick, compact on one or two A4 pages (PDF).")]];
    const missText = t.n(missing.length, "{n} file of these weeks is not on this PC. The plan is better when it can read them.", "{n} files of these weeks are not on this PC. The plan is better when it can read them.");
    const todoText = t.n(todo.length, "{n} class day of these weeks is not transcribed: the plan then knows only the slides and PDFs.", "{n} class days of these weeks are not transcribed: the plan then knows only the slides and PDFs.");
    modal(`<div class="cp-scroll">
      <h2>${I(prep ? "cap" : "youtube")} ${D.parent ? (prep ? t("Update the review sheet") : t("Update the study plan")) : t("New study plan")}</h2>
      ${D.parent ? "" : `<div class="field"><div class="cp-modes">${kinds.map(([v, ic, label, desc]) => `<button type="button" class="cp-mode ${sc.kind === v ? "on" : ""}" data-act="pd-kind" data-val="${v}">
        <span class="cp-mode-t">${I(ic, "sm")} ${label}</span><span class="cp-mode-d">${desc}</span></button>`).join("")}</div></div>
      <div class="field"><label>${t("Course")}</label><select class="input" data-act="pd-course">${courses.map((c) => `<option value="${esc(c.id)}" ${c.id === D.courseId ? "selected" : ""}>${esc(c.code)} — ${esc(c.display_name)}</option>`).join("")}</select></div>`}
      ${r.assessments.length ? `<div class="field"><label>${t("Prepare for")}</label><div class="pd-picks">${r.assessments.map((a, i) => `<button class="pd-pick ${sc.focus === a.name ? "on" : ""}" data-act="pd-assess" data-i="${i}">
        <b>${esc(a.name)}</b><span>${t("week {n}", { n: a.week })}${a.los.length ? ` · LO${a.los.join(", LO")}` : ""}</span></button>`).join("")}
        <button class="pd-pick ${!sc.focus ? "on" : ""}" data-act="pd-assess" data-i="-1"><b>${t("Just these weeks")}</b><span>${t("no quiz in mind")}</span></button></div></div>` : ""}
      <div class="field"><label class="cp-label">${t("Weeks")} <span class="dim sm">${t("{n} picked", { n: sel.size })}</span><span class="cp-tools">${weeks.some((w) => w.current) ? `<button type="button" class="btn ghost sm" data-act="pd-thisweek">${t("This week")}</button>` : ""}<button type="button" class="btn ghost sm" data-act="pd-clear">${t("Clear")}</button></span></label>
        <div class="pd-weeks2">${weeks.map((w) => `<label class="pd-wk ${sel.has(w.no) ? "on" : ""} ${w.current ? "now" : ""}" title="${esc(w.topic)}">
          <input type="checkbox" class="check" data-act="pd-week" data-no="${w.no}" ${sel.has(w.no) ? "checked" : ""}><span class="no">${t("W{n}", { n: w.no })}</span>
          <span class="t">${esc(w.topic || "—")}${w.current ? ` <span class="pill now">${t("this week")}</span>` : ""}</span>
          <span class="st"><span class="${!w.files ? "" : w.files_ready === w.files ? "ok" : "miss"}" title="${t("Slides and PDFs on this PC / listed on IEULearn")}">${I("file", "xs")}${w.files ? `${w.files_ready}/${w.files}` : "–"}</span>
            <span class="${!w.lectures ? "" : w.lectures_done === w.lectures ? "ok" : "miss"}" title="${t("Lectures transcribed / recorded")}">${I("video", "xs")}${w.lectures ? `${w.lectures_done}/${w.lectures}` : "–"}</span></span></label>`).join("")}</div></div>
      ${missing.length || todo.length ? `<div class="pd-gaps">
        ${missing.length ? `<div class="pd-gap">${I("file")}<span class="grow">${missText}</span><button class="btn sm" data-act="pd-download">${I("download", "sm")} ${t("Download {n}", { n: missing.length })}</button></div>` : ""}
        ${todo.length ? `<div class="pd-gap">${I("video")}<span class="grow">${todoText}</span><button class="btn sm" data-act="pd-transcribe">${I("wave", "sm")} ${t("Transcribe {n}", { n: todo.length })}</button></div>` : ""}</div>` : ""}
      <div class="cp-opts">
        ${prep ? "" : `<div class="cp-opt"><div class="t">${t("Time for videos")}</div><select class="input" data-pd="minutes">${[60, 120, 180, 300, 480, 720].map((m) => `<option value="${m}" ${+sc.minutes === m ? "selected" : ""}>${t("{h} h", { h: hours(m / 60) })}</option>`).join("")}</select></div>`}
        <div class="cp-opt"><div class="t">${t("Deadline")}</div><input class="input" type="date" data-pd="deadline" value="${esc(sc.deadline || "")}"></div>
        ${St.isChatGPT() ? "" : `<div class="cp-opt"><div class="t">${t("Model")}</div><select class="input" data-pd="model">${models.map(([k, l]) => `<option value="${k}" ${sc.model === k ? "selected" : ""}>${l}</option>`).join("")}</select></div>`}
        ${St.effortField ? St.effortField("pd", "plan", sc.effort, { attrs: 'data-pd="effort"' }) + (prep ? "" : St.effortField("pdv", "verify", sc.verify_effort, { attrs: 'data-pd="verify_effort"' })) : ""}
        ${prep ? "" : `<div class="cp-opt"><div class="t">${t("Language of the videos")}</div><select class="input" data-pd="video_lang">${(St.yt ? St.yt.langOptions() : [["en", "English"]]).map(([v, l]) => `<option value="${v}" ${sc.video_lang === v ? "selected" : ""}>${l}</option>`).join("")}</select></div>`}
        ${prep || !sc.focus ? "" : `<label class="cp-opt cp-switch"><div><div class="t">${t("Extras")}</div><div class="d">${t.html("Also videos for the topics of these weeks that {focus} doesn't ask for (optional; a longer search).", { focus: sc.focus })}</div></div><input type="checkbox" class="switch" data-pd="extras" ${sc.extras ? "checked" : ""}></label>`}
      </div>
      ${prep ? "" : `<div class="pd-how dim sm">${I("info", "xs")}<span>${sc.focus ? t.html("Only what {focus} needs is required; every video is checked through its captions for the skills each topic needs, and the plan says plainly what is not covered.", { focus: sc.focus })
        : t("every video is checked through its captions for the skills each topic needs, and the plan says plainly what is not covered.")}</span></div>`}
      <details class="pd-more" ${sc.topics || sc.difficulties ? "open" : ""}><summary>${t("Fine-tune (optional)")}</summary>
        <div class="pd-grid">
          <div class="field"><label>${t("Extra topics to recap")}</label><input class="input" data-pd="topics" value="${esc(sc.topics)}" placeholder="${t("e.g. counting, combinations")}"></div>
          <div class="field"><label>${t("What do you find hard?")}</label><input class="input" data-pd="difficulties" value="${esc(sc.difficulties)}" placeholder="${t("e.g. when to use Bayes vs. total probability")}"></div></div></details>
      </div>
      <div class="cp-foot"><div class="cp-cost" id="pd-cost">${costHTML()}</div>
        <div class="cp-btns"><button class="btn" data-act="modal-close">${t("Cancel")}</button>
          <button class="btn primary" data-act="pd-go" ${sel.size && ai.available && ai.can_plans !== false ? "" : "disabled"}>${I(prep ? "cap" : "youtube", "sm")} ${prep ? (D.parent ? t("Write it again") : t("Write review sheet")) : D.parent ? t("Update plan") : t("Build plan")}</button></div></div>`);
    $(".modal").classList.add("pd");
    const nb = $(".modal .cp-scroll"); if (nb) nb.scrollTop = keepBody;
    const nw = $(".pd-weeks2"); if (nw) nw.scrollTop = keepWeeks;
  }
  let estTimer = null;
  function estimate() {
    clearTimeout(estTimer);
    D.est = null;
    const box = $("#pd-cost"); if (box) box.innerHTML = costHTML();
    estTimer = setTimeout(async () => {
      const r = await call("plan_estimate", D.courseId, D.scope);
      D.est = r.ok ? r.estimate : null;
      const el = $("#pd-cost"); if (el) el.innerHTML = costHTML();      // only the footer: typing elsewhere is not interrupted
    }, 250);
  }
  function readInputs() {
    document.querySelectorAll(".modal [data-pd]").forEach((el) => {
      const k = el.dataset.pd;
      D.scope[k] = el.type === "checkbox" ? el.checked : k === "minutes" ? +el.value : el.value;
    });
  }

  // ------------------------------------------------------------------ wiring
  St.views.plans = plansView;
  St.views.plan = planView;
  St.crumbs = St.crumbs || {};
  St.crumbs.plans = () => `${I("youtube")} ${t("Study plans")}`;
  St.crumbs.plan = (v) => { const p = S.planCache[v.planId] || {}; return `<button class="btn ghost sm" data-act="go" data-view="plans">${I("arrow-left", "sm")} ${t("Plans")}</button> ${esc(planTitle(p) || t("Study plan"))}`; };
  St.onGo.plans = () => loadPlans();
  St.onGo.plan = (v) => loadPlan(v.planId);
  St.navExtra = (v) => {
    const n = running();
    return `<button class="nav-item ${v.name === "plans" || v.name === "plan" ? "active" : ""}" data-act="go" data-view="plans">${I("youtube")}<span class="label"><span class="code">${t("Study plans")}</span></span>${n ? `<span class="count">${SPIN()}</span>` : ""}</button>`;
  };
  St.onEvents.plan = (e) => {
    if (S.plans) {
      const i = S.plans.findIndex((p) => p.id === e.id);
      const brief = { ...(i >= 0 ? S.plans[i] : {}), ...e, type: undefined };
      if (i >= 0) S.plans[i] = brief; else S.plans.unshift(brief);
    }
    const cached = S.planCache[e.id];
    if (cached) Object.assign(cached, { status: e.status, stage: e.stage, progress: e.progress, message: e.message, error: e.error, title: e.title || cached.title });
    const kind = e.kind || (((S.plans || []).find((x) => x.id === e.id) || cached || {}).scope || {}).kind;
    if (e.status === "done") {
      toast(kind === "prep" ? t("Review sheet ready: {title}", { title: e.title || "" }) : kind === "pack" ? t("Study pack ready: {title}", { title: e.title || "" }) : kind === "sheet" ? t("Cheatsheet ready: {title}", { title: e.title || "" })
        : t("Study plan ready: {title}", { title: e.title || "" }), "success");
      loadPlan(e.id, true);
    }
    else if (e.status === "error") {
      const error = e.error || t("failed");
      toast(kind === "prep" ? t("Review sheet: {error}", { error }) : kind === "pack" ? t("Study pack: {error}", { error }) : kind === "sheet" ? t("Cheatsheet: {error}", { error })
        : t("Study plan: {error}", { error }), "error");
    }
    else if (cached && e.stage && !(cached.stages_done || []).includes(e.stage) && ["concepts", "search"].includes(e.stage)) loadPlan(e.id, true);
    if (S.view.name === "plans" || (S.view.name === "plan" && S.view.planId === e.id)) renderView();
    renderSidebar();
  };

  Object.assign(A, {
    "plan-new": (el) => newPlanDialog(el && el.dataset.id, null, el && el.dataset.kind, el && el.dataset.focus,
      el && el.dataset.weeks ? el.dataset.weeks.split(",").map(Number).filter(Boolean) : null),
    "pd-kind": (el) => {
      if (["pack", "sheet"].includes(el.dataset.val)) { A["pack-new"]({ dataset: { id: D.courseId, kind: el.dataset.val } }); return; }    // studypack.js
      readInputs(); D.scope.kind = el.dataset.val; drawDialog(); estimate(); },
    "plan-open": (el) => go({ name: "plan", planId: el.dataset.id }),
    "plan-tab": (el) => { S.planTab = el.dataset.tab; renderView(); },
    "plan-stop": (el) => call("plan_cancel", el.dataset.id),
    "plan-resume": async (el) => { const r = await call("plan_resume", el.dataset.id); if (!r.ok) toast(r.error, "error"); },
    "plan-file": async (el) => { const r = await call("plan_open_file", el.dataset.id); if (!r.ok) toast(r.error, "error"); },
    "plan-delete": async (el) => {
      if (!confirm(t("Delete this study plan? (Its Markdown file in your IEUStudia folder stays.)"))) return;
      await call("plan_delete", el.dataset.id);
      delete S.planCache[el.dataset.id];
      S.plans = (S.plans || []).filter((p) => p.id !== el.dataset.id);
      go({ name: "plans" });
    },
    "plan-update": (el) => { const p = S.planCache[el.dataset.id]; if (p) newPlanDialog(p.course_id, p, (p.scope || {}).kind); },
    "plan-mark": async (el) => {
      const p = S.planCache[el.dataset.id]; if (!p) return;
      const r = await call("plan_mark", el.dataset.id, el.dataset.key, el.checked);
      if (r.ok) { p.marks = r.marks; renderView(); }
    },
    "pd-course": async (el) => { D.courseId = el.value; newPlanDialog(el.value); },
    "pd-assess": (el) => {
      readInputs();
      const a = D.opts.assessments[+el.dataset.i];
      if (a) Object.assign(D.scope, { weeks: a.weeks, focus: a.name, los: a.los, deadline: weekEnd(D.opts.weeks, a.week) || D.scope.deadline });
      else Object.assign(D.scope, { focus: "", los: [] });
      drawDialog(); estimate();
    },
    "pd-week": (el) => {
      readInputs();
      const n = +el.dataset.no, s = new Set(D.scope.weeks);
      if (el.checked) s.add(n); else s.delete(n);
      D.scope.weeks = [...s].sort((a, b) => a - b);
      drawDialog(); estimate();
    },
    "pd-thisweek": () => { readInputs(); const w = D.opts.weeks.find((x) => x.current); if (w) { D.scope.weeks = [w.no]; drawDialog(); estimate(); } },
    "pd-clear": () => { readInputs(); D.scope.weeks = []; drawDialog(); estimate(); },
    "pd-transcribe": async () => {
      readInputs();
      const sel = new Set(D.scope.weeks);
      const days = D.opts.weeks.filter((w) => sel.has(w.no)).flatMap((w) => w.todo_days || []);
      let n = 0;
      const left = { unknown: 0, needs_section: false };     // only your section's lectures (catchup.js)
      for (const k of days) { const r = await call("transcribe_day", k, true); if (r.ok) { n += r.queued || 0; left.unknown += r.unknown || 0; left.needs_section = left.needs_section || !!r.needs_section; } }
      if (n || !(left.unknown || left.needs_section)) {
        toast(n ? t.n(n, "{n} recording queued for transcription — build the plan once they are done, or now with what is there", "{n} recordings queued for transcription — build the plan once they are done, or now with what is there")
          : t("Nothing left to transcribe there"), n ? "success" : "info");
      }
      if (St.sectionLeftOut) St.sectionLeftOut((St.findCourse(D.courseId) || {}).code || t("this course"), left);
    },
    "pd-download": async () => {
      readInputs();
      const sel = new Set(D.scope.weeks);
      const ids = [...new Set(D.opts.weeks.filter((w) => sel.has(w.no)).flatMap((w) => w.missing))];
      const r = await call("materials_download", ids);
      if (r.ok) toast(t.n(ids.length, "Downloading {n} file — the plan will read them once they are in", "Downloading {n} files — the plan will read them once they are in"), "success");
    },
    "pd-go": async () => {
      readInputs();
      const r = D.parent ? await call("plan_update", D.parent.id, D.scope) : await call("plan_create", D.courseId, D.scope);
      if (!r.ok) { toast(r.error, "error"); return; }
      closeModal();
      S.plans = [r.plan, ...(S.plans || []).filter((p) => p.id !== r.plan.id)];
      S.planCache[r.plan.id] = { ...r.plan, stages_done: [], concepts: [] };
      S.planTab = "watch";
      go({ name: "plan", planId: r.plan.id });
    },
  });
  document.addEventListener("change", (e) => { if (e.target.closest && e.target.closest(".modal [data-pd]")) { readInputs(); estimate(); } });

  // ------------------------------------------------------------------ browser preview (mock)
  const mockPlan = () => ({
    id: "pl1", course_id: "17933", title: "Bayes & conditional probability for Quiz 2", status: "done", stage: "done", progress: 1,
    message: "4 videos · 38 min", error: "", parent_id: "", created_at: new Date().toISOString(),
    scope: { weeks: [4, 5], focus: "Quiz 2", los: [3, 4], minutes: 120, deadline: isoDay(new Date(Date.now() + 5 * 864e5)), topics: "", difficulties: "when to use Bayes", allow_tr: false, model: "sonnet" },
    marks: { "v:1": true }, usage: {}, stages_done: ["gather", "concepts", "search", "inspect", "verify"], concepts: [],
    result: { title: "Bayes & conditional probability for Quiz 2", summary: "Three videos teach conditional probability, the total probability rule and Bayes' theorem with the tree diagrams your quiz uses; reading P(A|B) off a two-way table is explained in a written supplement. Independence is in week 5 but not on Quiz 2, so it is only listed.",
      total_minutes: 29, optional_minutes: 15, materials_missing: [{ id: "x", title: "Week05_Student", week: 5 }], materials_used: 4, days_used: 3, days_missing: 1,
      completeness: { goal: "Quiz 2 (LO3, LO4)", complete: true, headline: "Complete for Quiz 2 (LO3, LO4): all 8 skills found in your material are covered — 7 in checked video parts, 1 only in the written supplements.", level: "mostly",
        skills: 8, video: 7, optional: 0, supplement: 1, missing: 0, missing_list: [], supplement_list: ["read P(A|B) off a two-way table (Conditional probability)"],
        extras: [{ name: "Independence", items: [], state: "skipped" }], extras_searched: false, focused: true, budget: 120, required_minutes: 29, over_budget: 0 },
      items: [
        { n: 1, video: "ibINrxJLvlM", title: "Intro to Conditional Probability", channel: "Dr. Trefor Bazett", url: "https://www.youtube.com/watch?v=ibINrxJLvlM", start: 0, end: 374, whole: true, segments: [{ start: 0, end: 374 }], minutes: 6, role: "prerequisite", concepts: ["c1"], why: "Defines P(A|B) with the same Venn picture as your week 4 slides.", limits: "No two-way tables — see the supplement.",
          teaches: [{ concept: "c1", state: "partial", skills: ["define P(A|B) = P(A∩B)/P(B)", "use the multiplication rule"], missing: ["read P(A|B) off a two-way table"] }], state: "verified", captions: "manual, en",
          quotes: [{ t: 41, text: "the probability of A given that B has happened" }, { t: 196, text: "so P of A and B is P of B times P of A given B" }],
          excerpts: [{ start: 30, end: 210, text: "[0:30] so let's think about what it means to condition on an event [0:41] the probability of A given that B has happened, we write it P of A bar B [1:12] and the idea is that B becomes our new sample space…" }], errors: [], inspected: "captions (manual, en): 1 part read (0:30–3:30)" },
        { n: 2, video: "OByl4RJxnKA", title: "Bayes' Theorem of Probability With Tree Diagrams & Venn Diagrams", channel: "The Organic Chemistry Tutor", url: "https://www.youtube.com/watch?v=OByl4RJxnKA", start: 0, end: 1154, whole: false, segments: [{ start: 0, end: 420 }, { start: 780, end: 1154, url: "https://www.youtube.com/watch?v=OByl4RJxnKA&t=780s" }], minutes: 13, role: "required", concepts: ["c2", "c3"], why: "Worked problems with trees — exactly the quiz style.", limits: "",
          teaches: [{ concept: "c2", state: "full", skills: ["split by a partition", "draw the tree"], missing: [] }, { concept: "c3", state: "full", skills: ["invert a conditional", "the medical-test question"], missing: [] }], state: "verified", captions: "auto, en",
          quotes: [{ t: 95, text: "let's draw a tree diagram for this problem" }, { t: 842, text: "the probability that he has the disease given that he tested positive" }],
          excerpts: [{ start: 60, end: 300, text: "[1:00] here is the first problem [1:35] let's draw a tree diagram for this problem [2:05] seventy percent of the parts come from factory A…" }],
          errors: [{ t: 1012, text: "Writes 0.95 × 0.01 = 0.0095 but reads it as 0.095 — the result 0.088 is still right." }], inspected: "captions (auto, en): 2 parts read" },
        { n: 3, video: "XQoLVl31ZfQ", title: "Bayes' Theorem - The Simplest Case", channel: "Dr. Trefor Bazett", url: "https://www.youtube.com/watch?v=XQoLVl31ZfQ&t=80s", start: 80, end: 331, whole: false, segments: [{ start: 80, end: 331, url: "https://www.youtube.com/watch?v=XQoLVl31ZfQ&t=80s" }], minutes: 4, role: "required", concepts: ["c3"], why: "Derives Bayes from the multiplication rule.", limits: "",
          teaches: [{ concept: "c3", state: "partial", skills: ["invert a conditional"], missing: ["the medical-test question"] }], state: "verified", captions: "manual, en",
          quotes: [{ t: 102, text: "Bayes' theorem lets us flip the conditional" }], excerpts: [], errors: [], inspected: "captions (manual, en): 1 part read" },
        { n: 4, video: "HZGCoVF3YvM", title: "Bayes theorem, the geometry of changing beliefs", channel: "3Blue1Brown", url: "https://www.youtube.com/watch?v=HZGCoVF3YvM", start: 0, end: 911, whole: true, segments: [{ start: 0, end: 911 }], minutes: 15, role: "optional", concepts: ["c3"], why: "Builds intuition if the formula still feels arbitrary.",
          teaches: [{ concept: "c3", state: "partial", skills: ["invert a conditional"], missing: ["the medical-test question"] }], state: "verified", captions: "manual, en", quotes: [{ t: 312, text: "the evidence should not determine beliefs, but update them" }], excerpts: [], errors: [], inspected: "captions (manual, en)" }],
      schedule: [{ day: "Thu 22 Oct", items: [1, 2], minutes: 19 }, { day: "Fri 23 Oct", items: [3], minutes: 4 }],
      concepts: [{ id: "c1", name: "Conditional probability", in_scope: true }, { id: "c2", name: "Total probability rule", in_scope: true }, { id: "c3", name: "Bayes' theorem", in_scope: true }, { id: "c4", name: "Independence", in_scope: false }],
      checklist: [{ concept: "c1", name: "Conditional probability", explain: "The probability of A once you know B happened: P(A|B) = P(A∩B)/P(B).", recognize: ["“given that…”", "a two-way table with one row/column fixed"] },
        { concept: "c3", name: "Bayes' theorem", explain: "Turns P(B|A) into P(A|B) using the prior P(A).", recognize: ["the question gives P(test+|disease) and asks P(disease|test+)"] }],
      coverage: [
        { concept: "c1", name: "Conditional probability", in_scope: true, state: "partial", items: [1], note: "", skills: [{ text: "define P(A|B) = P(A∩B)/P(B)", items: [1], how: "video" }, { text: "use the multiplication rule", items: [1], how: "video" }, { text: "read P(A|B) off a two-way table", items: [], how: "supplement" }] },
        { concept: "c2", name: "Total probability rule", in_scope: true, state: "verified", items: [2], note: "", skills: [{ text: "split by a partition", items: [2], how: "video" }, { text: "draw the tree", items: [2], how: "video" }] },
        { concept: "c3", name: "Bayes' theorem", in_scope: true, state: "verified", items: [2, 3, 4], note: "", skills: [{ text: "invert a conditional", items: [2, 3, 4], how: "video" }, { text: "the medical-test question", items: [2], how: "video" }] },
        { concept: "c4", name: "Independence", in_scope: false, state: "skipped", items: [], note: "week 5, LO5", skills: [{ text: "check P(A∩B) = P(A)P(B)", items: [], how: "missing" }] }],
      supplements: [{ concept: "c1", name: "Conditional probability", text: "**From a two-way table:** P(A|B) is the count in the A-and-B cell divided by the total of the B column.\n\nExample: 12 of the 40 students who passed (B) studied with videos (A), so P(A|B) = 12/40 = 0.3." }],
      practice: [{ concept: "c3", question: "1% of people have a disease; a test is 95% sensitive and 90% specific. What is P(disease | positive)?", answer: "P = 0.95·0.01 / (0.95·0.01 + 0.10·0.99) ≈ **0.088**", source: "Week05_Student.pdf p.12" }],
      gaps: "", changes: { added: [], replaced: [], removed: [] } } });
  St.mocks.plan_list = () => ({ ok: true, plans: [mockPlan(), { id: "pl2", course_id: "16988", title: "", status: "running", stage: "inspect", progress: 0.47, message: "Reading captions: Inverse of a matrix (5/9)", error: "", parent_id: "", created_at: new Date().toISOString(), scope: { weeks: [5], focus: "Quiz 1", minutes: 120 } }] });
  St.mocks.plan_get = (id) => ({ ok: true, plan: id === "pl2" ? { ...St.mocks.plan_list().plans[1], stages_done: ["gather", "concepts", "search"], concepts: [{ id: "c1", name: "Inverse of a matrix", summary: "Find A⁻¹ and decide whether it exists.", focus: true, skills: ["row-reduce [A | I]", "the 2×2 formula", "spot a singular matrix"] }, { id: "c2", name: "Elementary matrices", summary: "" }, { id: "c3", name: "Leontief models", summary: "", in_scope: false }] } : mockPlan() });
  St.mocks.plan_options = (cid) => ({ ok: true, course: { id: cid }, model: "sonnet", ai: { available: true },
    weeks: [3, 4, 5, 6].map((n) => ({ no: n, start: `2026-10-${String(n * 7 - 16).padStart(2, "0")}`, end: "", current: n === 5, past: n < 5, topic: ["Counting", "Conditional probability", "Bayes' rule", "Random variables"][n - 3], exam: false, files: 1, files_ready: n < 5 ? 1 : 0, missing: n < 5 ? [] : ["m" + n], lectures: 1, lectures_done: n < 5 ? 1 : 0 })),
    assessments: [{ kind: "quiz", no: 2, name: "Quiz 2", week: 5, los: [3, 4], sources: [], weeks: [4, 5] }, { kind: "midterm", no: 0, name: "Midterm", week: 8, los: [], sources: [], weeks: [1, 2, 3, 4, 5, 6, 7, 8] }] });
  St.mocks.plan_estimate = () => ({ ok: true, estimate: { main_model: "sonnet", verify_model: "haiku", main_input: 31200, main_output: 8000, verify_input: 14800, verify_output: 3600, concepts: 8, weeks: [], materials_used: [1, 2], materials_missing: [], days_used: [1, 2], minutes: "about 2–5 minutes" } });
  St.mocks.plan_create = () => ({ ok: true, plan: St.mocks.plan_list().plans[1] });
  St.mocks.plan_update = St.mocks.plan_create;
  St.mocks.plan_mark = (id, key, done) => ({ ok: true, marks: { "v:1": true, [key]: done } });
  ["plan_cancel", "plan_resume", "plan_delete", "plan_open_file"].forEach((k) => { St.mocks[k] = () => ({ ok: true }); });
})();
