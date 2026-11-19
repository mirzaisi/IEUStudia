/* IEUStudia — what the material pickers share: the course's files that belong to no week ("Course-wide and
 * announcement files", studypack.general_files: unticked unless tied to the quiz or exam picked, as its posted
 * solutions), a class list's mark ("Left out: it lists students", materials.left_out) with its "Include anyway", and
 * the Download / Transcribe rows for what the picked weeks still miss. The quiz and cards dialogs (practice.js), the
 * study pack and cheatsheet dialog (studypack.js) and the plan dialog (plans.js) use it. Plugs in through
 * window.Studia. */
(function () {
  "use strict";
  const St = window.Studia;
  const { call, esc, I, toast, A } = St;

  // a file that can be ticked: on this PC with its text read, and not a class list
  const usable = (f) => !!f.ready && !f.students;
  // the files tied to an assessment (its key: d.assessments): "For Quiz 3" ticks Quiz 3's posted solutions
  const tied = (opts, key) => (key ? ((opts && opts.general) || []).filter((f) => usable(f) && (f.for || []).includes(key)).map((f) => f.id) : []);
  // what a file's line says on its right: a class list's mark with Include anyway, else ``other``
  function fileSub(f, other) {
    if (!f.students) return other;
    return `${esc(t("Left out: it lists students"))} <button type="button" class="link" data-act="students-ok" data-id="${esc(f.id)}" data-name="${esc(f.name || f.title || "")}">${t("Include anyway")}</button>`;
  }
  // where a course-wide or announcement file comes from, under its name
  const fromWhere = (f) => (f.group === "announcement" ? (f.week ? t("From an announcement, week {n}", { n: f.week }) : t("From an announcement")) : f.unit || t("Course-wide"));
  // the group: one line per file with its tick (``act``: the dialog's own checkbox action, data-kind "file")
  function groupHTML(opts, on, act, busy) {
    const files = (opts && opts.general) || [];
    if (!files.length) return "";
    const line = (f) => {
      const ok = usable(f);
      const sub = f.ready ? t.n(f.pages, "{n} page", "{n} pages") : busy && busy.has(f.id) ? t("downloading…") : f.downloaded ? t("not read yet") : t("not on this PC");
      return `<label class="pk-item ${ok ? "" : "off"}"><input type="checkbox" class="check" data-act="${act}" data-kind="file" data-id="${esc(f.id)}" ${on.has(f.id) ? "checked" : ""} ${ok ? "" : "disabled"}>
        ${I(f.group === "announcement" ? "megaphone" : "file-text", "sm")}<span class="grow">${esc(f.name)} <span class="dim sm">· ${esc(fromWhere(f))}</span></span><span class="dim sm">${fileSub(f, esc(sub))}</span></label>`;
    };
    return `<div class="pk-week pk-gen"><div class="pk-week-h">${t("Course-wide and announcement files")} <span class="dim">${t("Tick the ones you want.")}</span></div>${files.map(line).join("")}</div>`;
  }
  // the files of the picked weeks (and the course-wide ones ticked) not on this PC, and your class days not
  // transcribed (studypack.options: "missing", "todo_days"), as the plan dialog's rows
  function gapsHTML(missing, todo, acts, texts) {
    if (!missing.length && !todo.length) return "";
    return `<div class="pd-gaps">
      ${missing.length ? `<div class="pd-gap">${I("file")}<span class="grow">${(texts && texts.missing) || t.n(missing.length, "{n} file of these weeks is not on this PC.", "{n} files of these weeks are not on this PC.")}</span><button class="btn sm" data-act="${acts.download}">${I("download", "sm")} ${t("Download {n}", { n: missing.length })}</button></div>` : ""}
      ${todo.length ? `<div class="pd-gap">${I("video")}<span class="grow">${t.n(todo.length, "{n} class day of these weeks is not transcribed.", "{n} class days of these weeks are not transcribed.")}</span><button class="btn sm" data-act="${acts.transcribe}">${I("wave", "sm")} ${t("Transcribe {n}", { n: todo.length })}</button></div>` : ""}</div>`;
  }
  // a file let in: its mark goes in every list the open dialog holds
  function allow(opts, id) {
    ((opts && opts.weeks) || []).forEach((w) => (w.files || []).forEach((f) => { if (f.id === id) f.students = false; }));
    ((opts && opts.general) || []).forEach((f) => { if (f.id === id) f.students = false; });
    ((opts && opts.weeks) || []).forEach((w) => { if (Array.isArray(w.students)) w.students = w.students.filter((x) => (x.id || x) !== id); });
  }
  // queue the class days for transcription, saying how many went (as the plan dialog)
  async function transcribe(days) {
    let n = 0, error = "";
    for (const k of days) { const r = await call("transcribe_day", k, true); if (r.ok) n += r.queued || 0; else error = error || r.error || ""; }
    if (!n && error) { toast(error, "error"); return 0; }       // not "nothing left": it couldn't be queued
    toast(n ? t.n(n, "{n} recording queued for transcription", "{n} recordings queued for transcription") : t("Nothing left to transcribe there"), n ? "success" : "info");
    return n;
  }
  async function download(ids) {
    const r = await call("materials_download", ids);
    if (!r.ok) { toast(r.error || t("Download failed"), "error"); return false; }
    toast(t.n(ids.length, "Downloading {n} file. It's ticked here once it's on your PC.", "Downloading {n} files. They're ticked here once they're on your PC."), "success");
    return true;
  }

  // the open dialog (``o``: its state() with opts, weeks, files and fetching; open(); redraw(); refetch(), when its
  // downloads are to be ticked as they come in, as the study pack dialog's are): a class list let in is ticked
  let live = null, timer = null;
  function setLive(o) {
    live = o;
    St.picks.onAllow = (id) => {
      const D = o.state();
      if (!D || !D.opts || !o.open()) return;
      allow(D.opts, id);
      if (D.files && D.files.add) D.files.add(id);
      o.redraw();
    };
  }
  const onMaterial = St.onEvents.material;
  St.onEvents.material = (e) => {
    if (onMaterial) onMaterial(e);
    const o = live, D = o && o.state();
    if (!o || !o.refetch || !D || !D.opts || !o.open() || !D.opts.weeks.some((w) => w.files.some((f) => f.id === e.id))) return;
    if (e.state === "error") { if (D.fetching && D.fetching.delete(e.id)) o.redraw(); return; }      // its Download is back
    if (e.state !== "done") return;
    clearTimeout(timer);
    timer = setTimeout(async () => {             // (a batch of downloads: one refresh)
      const r = await o.refetch();
      if (o.state() !== D || !r || !r.ok || !o.open()) return;
      const was = new Set(D.opts.weeks.flatMap((w) => w.files.filter(usable).map((f) => f.id)));
      D.opts = { ...D.opts, weeks: r.weeks, general: r.general || D.opts.general };
      r.weeks.forEach((w) => w.files.forEach((f) => { if (f.downloaded && D.fetching) D.fetching.delete(f.id); }));
      r.weeks.filter((w) => D.weeks.has(w.no)).forEach((w) => w.files.forEach((f) => { if (usable(f) && !was.has(f.id)) D.files.add(f.id); }));
      o.redraw();
    }, 300);
  };

  St.picks = { usable, tied, fileSub, groupHTML, gapsHTML, allow, transcribe, download, live: setLive, onAllow: null };
  Object.assign(A, {
    // a class list let in, after a warning: the open dialog marks it (St.picks.onAllow) and ticks it again
    "students-ok": async (el) => {
      const yes = await St.ask({ title: t("Include this file?"), ok: t("Include anyway"), danger: true,
        text: t("{name} seems to list students by their numbers (grades or groups). Your classmates' details shouldn't go to the AI. Include it only if it isn't a class list.", { name: el.dataset.name || t("This file") }) });
      if (!yes) return;
      const r = await call("students_include", el.dataset.id);
      if (!r.ok) { toast(r.error, "error"); return; }
      if (St.picks.onAllow) St.picks.onAllow(el.dataset.id);
    },
  });
})();
