/* IEUStudia: the services it reads but doesn't control (IEULearn, Panopto, ChatGPT, Claude Code, the Codex CLI,
 * YouTube, Hugging Face, GitHub) and what happens when one of them changes on its side (health.py keeps the state):
 * - Settings → Services: every service, whether it answers as IEUStudia expects, didn't answer, or changed (since
 *   when, and what doesn't work now), and Copy details for the developer (nothing personal in it);
 * - Home: a warning for each service that changed, once: Hide hides that change (a new one shows again);
 * - a course page says it where it matters: its files, announcements, outcomes or lectures are what IEUStudia read
 *   before the change, and stay as they are (only on the courses whose page changed, when that part is read per
 *   course: health.py's "where");
 * - a sync you asked for says a new change once (a toast), and the top bar says "Synced with problems".
 * Plugs into app.js and course.js through window.Studia. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, A, I, call, esc, toast } = St;
  const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { /* private mode */ } return null; };
  const HIDDEN = "studia-svc-hidden";
  const toasted = new Set();        // the changes a toast told about in this window (a new sync doesn't say them again)

  const snap = () => S.svc || (S.boot || {}).services || { services: [], changed: [] };
  const service = (id) => (snap().services || []).find((s) => s.id === id);
  const changed = () => (snap().services || []).filter((s) => s.state === "changed");
  const hidden = () => { try { return JSON.parse(store(HIDDEN) || "[]"); } catch (e) { return []; } };
  const when = (iso) => (iso ? t.date(iso, "dmy") : "");

  // ------------------------------------------------------------------ what each page shows
  // Home: one warning per service that changed, until Hide (for that change). GitHub's: the update check is what
  // doesn't work, so its button opens the download page instead of checking again
  const RELEASES = "https://github.com/mirzaisi/IEUStudia/releases/latest";
  const updateBtn = (s) => (s.id === "github"
    ? `<button class="btn sm" data-act="url" data-url="${RELEASES}">${I("external", "sm")} ${t("Open the download page")}</button>`
    : `<button class="btn sm" data-act="svc-update">${I("download", "sm")} ${t("Check for an update")}</button>`);
  function banner() {
    const gone = new Set(hidden());
    return changed().filter((s) => !gone.has(s.key)).map((s) => `<div class="callout warning svc-warn" data-svc="${esc(s.id)}">${I("alert")}<div class="grow">
        <b>${esc(s.title)}</b> ${(s.whats || []).map(esc).join(" ")}
        <div class="svc-safe">${esc(s.safe)} ${esc(s.todo)}</div>
        <div class="svc-acts">${updateBtn(s)}
          <button class="btn ghost sm" data-act="svc-copy">${I("copy", "sm")} ${t("Copy details")}</button>
          <span class="spacer"></span><button class="btn ghost sm" data-act="svc-hide" data-key="${esc(s.key)}">${t("Hide")}</button></div></div></div>`).join("");
  }
  // a course page's tab: what it shows is from before the change of the part it comes from (a part read per course:
  // only on the courses whose page changed, ``where``; ``course``: the course page's id)
  const TAB_PARTS = { weeks: ["ieulearn.unit_plan"], materials: ["ieulearn.unit_plan"], announcements: ["ieulearn.announcements"],
    alerts: ["ieulearn.announcements"], overview: ["ieulearn.course_page"], lectures: ["panopto.*"] };
  const mine = (p, course) => course == null || p.whole || !(p.where || []).length || p.where.includes(String(course));
  function note(tab, course) {
    const want = TAB_PARTS[tab || "weeks"] || [];
    const hits = [];
    for (const s of changed()) {
      for (const p of s.parts || []) {
        if (p.state === "changed" && want.some((w) => w === `${s.id}.${p.part}` || w === `${s.id}.*`) && mine(p, course)) hits.push({ s, p });
      }
    }
    if (!hits.length) return "";
    const first = hits[0];
    return `<div class="callout warning svc-note">${I("alert")}<div><b>${esc(first.s.title)}</b> ${hits.map(({ p }) => esc(p.what)).join(" ")}
      ${t("What you see here is what IEUStudia read before {date}, and it stays as it is.", { date: when(first.p.since) })}
      <button class="link" data-act="svc-settings">${t("Details")}</button></div></div>`;
  }
  // Settings → Services
  const DOT = { ok: "ok", down: "down", changed: "changed", unknown: "unknown" };
  function status(s) {
    if (s.state === "changed") return t("Changed on its side since {date}.", { date: when(s.since) });
    if (s.state === "down") return t("Didn't answer the last time it was asked ({when}). It is tried again by itself.", { when: St.rel(s.seen) });
    if (s.state === "ok") return t("Works as IEUStudia expects (last answer {when}).", { when: St.rel(s.seen) });
    return (s.parts || []).length ? t("Not checked since IEUStudia was updated.") : t("Not used yet.");
  }
  function card() {
    const all = snap().services || [];
    const rows = all.map((s) => `<div class="set-row svc-row"><span class="svc-dot ${DOT[s.state] || "unknown"}" aria-hidden="true"></span>
      <div class="txt"><div class="t">${esc(s.name)}</div><div class="d">${esc(status(s))}${s.state === "changed"
        ? `<ul class="svc-what">${(s.whats || []).map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}</div></div></div>`).join("");
    const any = all.some((s) => s.state === "changed");
    return `<div class="card set-card" id="set-services"><h3>${t("Services")} <span class="dim sm">${t("what IEUStudia reads, and how it answers")}</span></h3>
      ${any ? `<div class="callout warning">${I("alert")}<div>${esc(changed()[0].safe)} ${esc(changed()[0].todo)}</div></div>` : ""}
      ${rows}
      <div class="set-row"><div class="txt"><div class="t">${t("For the developer")}</div><div class="d">${t("When a service changed, Copy details and send it to IEUStudia's developer (on GitHub: Issues). It says what each service answered, with nothing personal in it.")}</div></div>
        <div style="display:flex;gap:6px">${any ? updateBtn(changed().find((s) => s.id !== "github") || changed()[0]) : ""}
          <button class="btn ghost sm" data-act="svc-copy">${I("copy", "sm")} ${t("Copy details")}</button>
          <button class="btn ghost sm" data-act="url" data-url="${esc(snap().issues || "https://github.com/mirzaisi/IEUStudia/issues")}">${I("external", "sm")} ${t("Issues")}</button></div></div></div>`;
  }

  // ------------------------------------------------------------------ plugged into the other pages
  St.services = true;
  St.svcBanner = banner;
  St.svcNote = note;
  // the lines a sync's result says for the services that changed during it (the top bar's tooltip)
  St.svcLines = (ids) => (ids || []).map((id) => service(id)).filter((s) => s && s.state === "changed").map((s) => `${s.title} ${(s.whats || []).join(" ")}`);
  // a sync you asked for: a change it found is said once in this window (Home keeps saying it until Hide)
  St.svcToast = (ids) => {
    for (const s of (ids || []).map(service)) {
      if (!s || s.state !== "changed" || toasted.has(s.key) || hidden().includes(s.key)) continue;
      toasted.add(s.key);
      toast(`${s.title} ${(s.whats || []).join(" ")} ${t("Home and Settings → Services say what to do.")}`, "warning");
    }
  };
  const before = St.settingsMore;
  St.settingsMore = () => (before ? before() : "") + card();

  St.onEvents.services = (e) => {
    S.svc = e;
    if (S.view.name === "settings") { drawCard(); return; }          // (only its card: Settings has boxes to type in)
    if (["home", "course"].includes(S.view.name) && !document.querySelector(".modal")) St.renderView();
  };
  // Settings → Services says when each service last answered: asked for when Settings opens and every 20 s while it
  // is shown (an answer that changes nothing sends no event, and "3 min ago" moves on), so it is never behind
  const EVERY_MS = 20000;
  let ticker = 0;
  function drawCard() {
    const el = document.getElementById("set-services");
    if (!el || el.querySelector(":active")) return;                  // a button being pressed: the next time
    const a = document.activeElement, again = el.contains(a) && a.dataset && a.dataset.act ? a.dataset.act : "";
    el.outerHTML = card();
    const b = again && document.querySelector(`#set-services [data-act="${again}"]`);
    if (b) b.focus({ preventScroll: true });
  }
  async function refresh() {
    if (S.view.name !== "settings" || document.hidden) return;
    const r = await call("services_state");
    if (!r || !r.ok || S.view.name !== "settings") return;
    S.svc = r;
    drawCard();
  }
  const onGo = St.onGo = St.onGo || {}, goBefore = onGo.settings;
  onGo.settings = (v) => {
    if (goBefore) goBefore(v);
    refresh();
    clearInterval(ticker);
    ticker = setInterval(() => {
      if (S.view.name !== "settings") { clearInterval(ticker); ticker = 0; return; }
      refresh();
    }, EVERY_MS);
  };
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });     // (back to the window)
  const contentBefore = St.onEvents.content;
  St.onEvents.content = (e) => {       // the files and announcements after the recordings: a page that changed there
    if (contentBefore) contentBefore(e);
    if (e.phase !== "done" || !(e.changed || []).length) return;
    const have = S.sync.changed || "", extra = St.svcLines(e.changed).filter((l) => !have.includes(l));
    if (!S.sync.running && extra.length) {           // the top bar's "Synced with problems" (each service once)
      S.sync = { ...S.sync, changed: [have, ...extra].filter(Boolean).join(" "), partial: true };
      St.renderTop();
    }
    if (!e.quiet) St.svcToast(e.changed);
  };

  Object.assign(A, {
    "svc-hide": (el) => {
      store(HIDDEN, JSON.stringify([...hidden(), el.dataset.key].slice(-40)));
      St.renderView();
    },
    "svc-copy": async () => {
      const r = (await call("services_copy")) || { ok: false };
      const done = t("Copied: send it to IEUStudia's developer. It has nothing personal in it.");
      // the clipboard refused: the details are shown to copy by hand (setup.js's Copy failed), as with every other copy
      if (St.copied) { St.copied(r, r.text || "", done); return; }
      toast(r.ok ? done : r.error || t("Could not access the clipboard."), r.ok ? "success" : "error");
    },
    "svc-update": async (el) => {
      if ((S.update || {}).available) { A["update-open"](); return; }
      if (A["update-check"]) await A["update-check"](el);
    },
    "svc-settings": () => {
      St.go({ name: "settings" });
      const c = document.getElementById("set-services"), nav = document.querySelector(".set-nav");
      if (!c) return;
      if (nav) c.style.scrollMarginTop = `${nav.offsetHeight + 8}px`;
      c.scrollIntoView({ block: "start" });
    },
  });

  // ------------------------------------------------------------------ browser preview (mock)
  St.mocks.services_state = () => ({ ok: true, ...(S.boot || {}).services });
  St.mocks.services_copy = () => ({ ok: true, text: "IEUStudia (preview)" });
})();
