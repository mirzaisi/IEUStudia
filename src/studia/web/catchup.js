/* IEUStudia — full sync (every course) and course sync: sync, then transcribe the lectures of your sections that
 * aren't transcribed yet (newest first, so the lecturer's latest heads-ups appear first) and download the materials.
 * No AI is used here: notes, plans and chats stay on demand. Plugs into app.js through window.Studia. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, call, esc, I, SPIN, toast, modal, closeModal, renderTop, fmtDur, A } = St;

  // unknown: the courses whose lectures that match no section you include this time (not remembered)
  S.catchup = { running: null, est: null, picked: null, scope: null, mats: true, unknown: new Set() };

  const speed = (est) => est.speed || (est.gpu ? 12 : 0.6);        // from catchup.py
  const mb = (n) => (n >= 1024 ? `${t.num(n / 1024, 1)} GB` : `${Math.max(1, Math.round(n))} MB`);    // 1.5 GB · 1,5 GB
  const lectures = (n) => t.n(n, "{n} lecture", "{n} lectures");
  const files = (n) => t.n(n, "{n} file", "{n} files");
  const plus = (c) => (S.catchup.unknown.has(c.course_id) ? c.unknown || 0 : 0);
  const plusAudio = (c) => (plus(c) ? c.unknown_audio_s || 0 : 0);
  // what the dialog says are lectures: class days (a lecture's parts together, as the course page counts them); the
  // recordings (parts) are what is queued and what the work is
  const days = (c) => (c.days != null ? c.days : c.lectures) + (plus(c) ? (c.unknown_days != null ? c.unknown_days : c.unknown) : 0);
  const unknownDays = (c) => (c.unknown_days != null ? c.unknown_days : c.unknown);
  const waitingDays = (c) => (c.waiting_days != null ? c.waiting_days : c.waiting);

  function totals() {
    const { est, picked } = S.catchup;
    const rows = est.courses.filter((c) => picked.has(c.course_id));
    const audio = rows.reduce((a, c) => a + c.audio_s + plusAudio(c), 0);
    return { rows, lectures: rows.reduce((a, c) => a + c.lectures + plus(c), 0), days: rows.reduce((a, c) => a + days(c), 0), audio,
      materials: rows.reduce((a, c) => a + c.materials, 0), work: audio / speed(est), audioMb: (audio / 60) * 0.75 };
  }

  // several sections publish here and yours isn't known: ask right here, with the sections found
  function askHTML(c) {
    const list = (c.sections || []).join(", ");
    return `<div class="sync-note">${I("users")}<div>${t.html("<b>{course}</b> publishes the lectures of sections {list} and yours isn’t set yet, so its {lectures} are held back. Which section are you in? IEUStudia remembers it for this course.",
      { course: c.code, list, lectures: lectures(waitingDays(c)) })}
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">${(c.sections || []).map((x) => `<button class="btn sm" data-act="cu-section" data-id="${esc(c.course_id)}" data-sec="${esc(x)}">${t.html("Section {tag}", { tag: x })}</button>`).join("")}</div></div></div>`;
  }

  // your section is known, but some lectures match no section (no tag, no weekday to go by): left out unless you say
  function unknownHTML(c) {
    const p = { course: esc(c.code), tag: esc(c.my_section || "") };
    return `<div class="sync-note">${I("alert")}<div>${t.n(unknownDays(c), "<b>{course}</b>: {n} lecture couldn’t be matched to your section (Section {tag}), so it’s left out.",
      "<b>{course}</b>: {n} lectures couldn’t be matched to your section (Section {tag}), so they’re left out.", p)}
      <label class="mini-toggle" style="display:flex;margin:6px 0 0"><input type="checkbox" class="switch sm" data-act="cu-unknown" data-id="${esc(c.course_id)}" ${S.catchup.unknown.has(c.course_id) ? "checked" : ""}>${t.n(unknownDays(c), "Transcribe it too ({audio})", "Transcribe them too ({audio})", { audio: fmtDur(c.unknown_audio_s) })}</label></div></div>`;
  }

  function rowHTML(c) {
    const what = c.needs_section && c.waiting ? `<span class="r" style="color:var(--amber)">${t("choose your section first ({lectures})", { lectures: lectures(waitingDays(c)) })}</span>`
      : c.lectures + plus(c) || c.materials ? `<span class="r">${c.lectures + plus(c) ? `${lectures(days(c))} · ${fmtDur(c.audio_s + plusAudio(c))}` : t("lectures done")}${c.materials ? ` · ${files(c.materials)}` : ""}</span>`
        : `<span class="r" style="color:var(--green)">${I("check", "xs")} ${t("up to date")}</span>`;
    return `<label class="sync-row"><input type="checkbox" class="check" data-act="cu-pick" data-id="${esc(c.course_id)}" ${S.catchup.picked.has(c.course_id) ? "checked" : ""}>
      <span class="t"><span class="code">${esc(c.code)}</span>${esc(St.findCourse(c.course_id) ? St.courseSub(St.findCourse(c.course_id)) : c.name)}</span>${what}</label>`;
  }

  function bodyHTML() {
    const { est, scope } = S.catchup;
    const one = scope !== "all";
    const tot = totals();
    const c0 = one ? est.courses[0] : null;
    const mats = S.catchup.mats;
    const big = tot.work > 3600 || tot.lectures > 8;
    const sizes = { lectures: lectures(tot.days), audio: fmtDur(tot.audio), work: fmtDur(tot.work), size: mb(tot.audioMb) };
    const none = !one && !est.courses.length;    // no course yet (the first sync still runs, or it failed): sync first
    const summary = none ? t("No courses yet. Sync first to bring them in; then this shows what there is to transcribe.")
      : !tot.lectures ? t("Every lecture of your sections is transcribed already.")
      : est.gpu ? t("<b>{lectures}</b> to transcribe (≈ {audio} of audio): roughly <b>{work}</b> with your graphics card. About {size} of audio is downloaded and deleted afterwards.", sizes)
        : t("<b>{lectures}</b> to transcribe (≈ {audio} of audio): roughly <b>{work}</b> on the processor, which is slow. About {size} of audio is downloaded and deleted afterwards.", sizes);
    const section = (one ? [c0] : est.courses).filter((c) => c && c.needs_section && c.waiting).map(askHTML).join("")
      + (one ? [c0] : tot.rows).filter((c) => c && c.unknown).map(unknownHTML).join("");
    const others = (one ? [c0] : tot.rows).reduce((a, c) => a + (c ? c.other_sections : 0), 0);
    const course = c0 ? c0.code : t("this course");
    const lead = one ? (mats ? t.html("Checks IEULearn and Panopto, then transcribes {course}’s lectures that aren’t transcribed yet (your section, newest first) and downloads the materials. Anything new the sync finds is included.", { course })
      : t.html("Checks IEULearn and Panopto, then transcribes {course}’s lectures that aren’t transcribed yet (your section, newest first). Anything new the sync finds is included.", { course }))
      : mats ? t("Checks IEULearn and Panopto, then transcribes every lecture of your sections that isn’t transcribed yet (newest first, across all courses) and downloads the materials. Anything new the sync finds is included.")
        : t("Checks IEULearn and Panopto, then transcribes every lecture of your sections that isn’t transcribed yet (newest first, across all courses). Anything new the sync finds is included.");
    return `<p class="lead">${lead}</p>
      ${one || none ? "" : `<div class="sync-pick">${est.courses.map(rowHTML).join("")}</div>`}
      ${section}
      <div class="sync-note${big ? "" : " calm"}">${I(big ? "alert" : "info")}<div>${summary}${tot.lectures ? ` ${big ? t("Your PC works hard while this runs, and its fans may get loud.") + " " : ""}${t("You can keep using IEUStudia and cancel any time from the bar at the bottom of the window.")}` : ""}</div></div>
      <div class="sync-note calm">${I("megaphone")}<div>${t("Heads-ups (quizzes, exams and homework the lecturer mentions) appear as each lecture is transcribed. No AI is used here: notes, study plans and chats run only when you ask for them.")}${others ? ` ${t("Lectures of other sections are skipped ({n}).", { n: others })}` : ""}</div></div>
      ${none ? "" : `<label class="mini-toggle" style="margin:10px 0 0"><input type="checkbox" class="switch sm" data-act="cu-mats" ${mats ? "checked" : ""}>${tot.materials ? t("Also download materials ({files})", { files: files(tot.materials) }) : t("Also download materials (none missing)")}</label>`}
      <div class="actions"><button class="btn" data-act="modal-close">${t("Cancel")}</button>
        <button class="btn primary" data-act="cu-go" ${!one && !tot.rows.length && !none ? "disabled" : ""}>${I("zap", "sm")} ${tot.lectures || (mats && tot.materials) ? (one ? t("Sync course") : t("Start full sync")) : t("Sync now")}</button></div>`;
  }
  function draw() {
    const box = document.querySelector("#cu-body");
    if (box) box.innerHTML = bodyHTML();
  }

  async function dialog(courseId) {
    if (S.catchup.running) { toast(t("A full sync is already starting. Once it has checked IEULearn, its lectures appear in the bar at the bottom of the window."), "info"); return; }
    const one = !!courseId;
    modal(`<h2>${I("zap")} ${one ? t("Sync this course") : t("Full sync of every course")}</h2><div id="cu-body">${St.skeleton ? St.skeleton() : SPIN()}</div>`);
    const r = await call("catch_up_estimate", courseId || null);
    if (!document.querySelector("#cu-body")) return;           // closed meanwhile
    if (!r.ok) { closeModal(); toast(r.error, "error"); return; }
    S.catchup.est = r;
    S.catchup.scope = courseId || "all";
    S.catchup.unknown = new Set();
    S.catchup.picked = new Set(r.courses.filter((c) => one || c.lectures || c.materials || !c.needs_section).map((c) => c.course_id));
    draw();
  }

  // the answer to "which section are you in?": remembered as the course's section, then the dialog recomputes
  async function chooseSection(cid, sec) {
    const ok = St.setSection ? await St.setSection(cid, sec)
      : await call("set_course_prefs", cid, { my_section: sec, show_mode: "mine" }).then((r) => { if (r.ok) St.setCourses(r.courses); else toast(r.error, "error"); return r.ok; });
    if (!ok || !document.querySelector("#cu-body")) return;
    const { scope } = S.catchup;
    const r = await call("catch_up_estimate", scope === "all" ? null : scope);
    if (!document.querySelector("#cu-body") || S.catchup.scope !== scope) return;
    if (!r.ok) { toast(r.error, "error"); return; }
    S.catchup.est = r;
    S.catchup.picked.add(cid);
    draw();
  }

  async function start() {
    const { est, picked, scope } = S.catchup;
    if (scope === "all" && !est.courses.length) { closeModal(); A.sync(); return; }    // no course yet: the plain sync brings them in
    const all = scope === "all" && picked.size === est.courses.length;
    const ids = scope === "all" ? (all ? null : [...picked]) : [scope];
    const include = [...S.catchup.unknown].filter((id) => (scope === "all" ? picked.has(id) : id === scope));
    closeModal();
    S.catchup.running = scope;
    renderTop();
    const r = await call("catch_up", ids, S.catchup.mats, include);
    if (!r.ok) { S.catchup.running = null; toast(r.error, "error"); renderTop(); return; }
    toast(scope === "all" ? t("Full sync started: syncing first, then the lectures are queued.") : t("Course sync started: syncing first."), "info");
  }

  St.onEvents.catchup = (e) => {
    if (e.phase === "sync") return;
    S.catchup.running = null;
    renderTop();
    if (e.phase === "error") { toast(e.message || t("Full sync failed"), "error"); return; }
    const parts = [];
    const queued = e.days != null ? e.days : e.lectures;              // class days (their parts are what is queued)
    if (e.lectures) parts.push(t.n(queued, "{n} lecture queued (≈ {audio} of audio)", "{n} lectures queued (≈ {audio} of audio)", { audio: fmtDur(e.audio_s) }));
    if (e.materials) parts.push(t.n(e.materials, "{n} material downloading", "{n} materials downloading"));
    if (e.unknown) parts.push(t.n(e.unknown, "{n} not matched to your section, left out", "{n} not matched to your section, left out"));
    toast(parts.length ? parts.join(" · ") : t("Everything is synced and transcribed."), e.lectures || e.materials ? "success" : "info");
    (e.waiting || []).forEach((code) => toast(t("{course}: choose your section to transcribe its lectures.", { course: code }), "warning"));
  };

  // a chat's or study plan's weeks (transcribe_day with batch): what was left out for your section's sake
  St.sectionLeftOut = (code, left) => {
    if (left.needs_section) toast(t("{course}: choose your section to transcribe its lectures.", { course: code }), "warning");
    else if (left.unknown) toast(t.n(left.unknown, "{course}: {n} lecture couldn’t be matched to your section, so it was left out. If it’s yours, transcribe it from the course page.",
      "{course}: {n} lectures couldn’t be matched to your section, so they were left out. If they’re yours, transcribe them from the course page.", { course: code }), "info");
  };

  Object.assign(A, {
    "full-sync": () => dialog(null),
    "course-sync": (el) => dialog(el.dataset.id),
    "cu-go": () => start(),
    "cu-pick": (el) => { const p = S.catchup.picked; if (el.checked) p.add(el.dataset.id); else p.delete(el.dataset.id); draw(); },
    "cu-mats": (el) => { S.catchup.mats = el.checked; draw(); },
    "cu-section": (el) => chooseSection(el.dataset.id, el.dataset.sec),
    "cu-unknown": (el) => { const u = S.catchup.unknown; if (el.checked) u.add(el.dataset.id); else u.delete(el.dataset.id); draw(); },
  });

  // ------------------------------------------------------------------ browser preview (mock)
  St.mocks.catch_up_estimate = (cid) => {
    const courses = S.courses.filter((c) => !cid || c.id === cid).map((c, i) => {
      const left = Math.max(0, (c.counts.total || 0) - (c.counts.done || 0));
      return { course_id: c.id, code: c.code, name: c.display_name, lectures: left, days: Math.ceil(left / 3), audio_s: left * 2900, materials: i % 2 ? 0 : 4,
        other_sections: i === 0 ? 3 : 0, needs_section: false, waiting: 0, sections: i === 0 ? ["1", "2", "3"] : [],
        my_section: i === 0 ? "3" : null, unknown: i === 0 ? 1 : 0, unknown_audio_s: i === 0 ? 6800 : 0 };
    });
    const audio = courses.reduce((a, c) => a + c.audio_s, 0);
    return { ok: true, courses, lectures: courses.reduce((a, c) => a + c.lectures, 0), audio_s: audio,
      materials: courses.reduce((a, c) => a + c.materials, 0), work_s: audio / 12, speed: 12, gpu: true, audio_mb: (audio / 60) * 0.75, needs_section: [],
      unknown: courses.reduce((a, c) => a + c.unknown, 0) };
  };
  St.mocks.catch_up = () => {
    setTimeout(() => window.__lsEvent({ type: "catchup", phase: "done", scope: "all", lectures: 3, materials: 4, audio_s: 8700, waiting: [], unknown: 0 }), 700);
    return { ok: true };
  };
})();
