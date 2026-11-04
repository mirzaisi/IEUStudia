/* IEUStudia — the student's YouTube choices for video study plans, and how a plan's videos look:
 * - Settings → YouTube videos: the videos' language (English, Turkish or both), the channels you prefer and the ones
 *   you never want to see, and a channel search that shows each channel's name and picture;
 * - a plan's Watch tab as a playlist in the order to study: thumbnail, title, the channel with its picture, length,
 *   views and likes, the weeks and topics it covers, "Play all on YouTube", and per video "Prefer this channel" /
 *   "Never show this channel" (a blocked channel's videos are hidden at once, in every plan).
 * Plugs into app.js through window.Studia; plans.js asks St.yt for the pieces it draws. Pictures come from YouTube's
 * image servers only when they are shown. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, A, I, call, esc, toast, renderView } = St;

  S.yt = { prefs: null, q: "", found: null, searching: false, error: "", showHidden: {} };
  const LANGS = () => [["en", t("English")], ["tr", t("Turkish")], ["both", t("Both")]];
  let loading = null;
  function load() {
    if (!loading) {
      loading = call("yt_prefs").then((r) => {
        loading = null;
        if (r.ok) { S.yt.prefs = r.prefs; if (["settings", "plan"].includes(S.view.name)) renderView(); }
      });
    }
    return loading;
  }
  const prefs = () => { if (!S.yt.prefs) load(); return S.yt.prefs || { lang: "en", allow: [], block: [] }; };
  const listed = (list, it) => (prefs()[list] || []).some((c) => (it.channel_id ? c.id === it.channel_id : c.name === it.channel));

  // 1234567 -> "1.2M" (English) / "1,2 Mn" (Turkish)
  let fmt = null, fmtLang = "";
  const compact = (n) => {
    if (fmtLang !== t.lang()) { fmtLang = t.lang(); fmt = new Intl.NumberFormat(fmtLang === "tr" ? "tr" : "en-US", { notation: "compact", maximumFractionDigits: 1 }); }
    return fmt.format(n || 0);
  };
  // what the student watches of a plan's video: its planned parts, or the whole video when no part is given
  const watchS = (it) => {
    const parts = (it.segments || []).reduce((a, s) => a + Math.max(0, (+s.end || 0) - (+s.start || 0)), 0);
    if (it.whole || parts <= 0) return +it.duration_s || parts || (+it.end > +it.start ? it.end - it.start : 0) || (+it.minutes || 0) * 60;
    return parts;
  };
  // 1 h 25 min · 40 min
  const watchTime = (s) => {
    const m = Math.max(1, Math.round(s / 60)), h = Math.floor(m / 60);
    return !h ? t("{m} min", { m }) : m % 60 ? t("{h} h {m} min", { h, m: m % 60 }) : t("{h} h", { h });
  };
  const clock = (s) => { s = Math.max(0, Math.round(s || 0)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60; return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`; };
  // a channel's picture (lazy: fetched from YouTube's image servers only when it is on screen), else its initial
  const avatar = (url, name, cls = "") => url ? `<img class="yt-av ${cls}" src="${esc(url)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
    : `<span class="yt-av yt-av-none ${cls}" aria-hidden="true">${esc((name || "?").trim().charAt(0).toUpperCase())}</span>`;
  const chData = (c) => esc(JSON.stringify({ id: c.id || c.channel_id || "", name: c.name || c.channel || "", handle: c.handle || "", avatar: c.avatar || c.channel_avatar || "" }));

  // ------------------------------------------------------------------ Settings card
  function chip(c, list) {
    return `<span class="yt-ch">${avatar(c.avatar, c.name)}<span class="yt-ch-name" title="${esc(c.handle || c.name)}">${esc(c.name)}</span>
      <button class="btn ghost icon xs" data-act="yt-ch" data-which="" data-ch="${chData(c)}" title="${list === "allow" ? t("Stop preferring this channel") : t("Show this channel again")}" aria-label="${t("Remove")}">${I("x", "xs")}</button></span>`;
  }
  function foundHTML() {
    const y = S.yt;
    if (y.searching) return `<div class="dim sm">${St.SPIN()} ${t("Searching YouTube…")}</div>`;
    if (y.error) return `<div class="err-txt sm">${I("alert-circle", "sm")} ${esc(y.error)}</div>`;
    if (!y.found) return "";
    if (!y.found.length) return `<div class="dim sm">${t("No channel found. Try its exact name, or paste a link to it.")}</div>`;
    const p = prefs();
    return `<div class="yt-found">${y.found.map((c) => {
      const on = p.allow.some((x) => x.id === c.id) ? "allow" : p.block.some((x) => x.id === c.id) ? "block" : "";
      return `<div class="yt-found-row">${avatar(c.avatar, c.name, "lg")}
        <div class="grow"><b>${esc(c.name)}</b>${c.verified ? ` ${I("check-circle", "xs")}` : ""}
          <div class="dim sm">${[c.handle, c.followers != null ? t.n(c.followers, "{n} subscriber", "{n} subscribers", { n: compact(c.followers) }) : ""].filter(Boolean).map(esc).join(" · ")}</div></div>
        <div class="yt-found-acts">
          <button class="btn sm ${on === "allow" ? "on" : ""}" data-act="yt-ch" data-which="${on === "allow" ? "" : "allow"}" data-ch="${chData(c)}">${I(on === "allow" ? "check" : "star", "sm")} ${on === "allow" ? t("Preferred") : t("Prefer")}</button>
          <button class="btn sm ${on === "block" ? "on" : ""}" data-act="yt-ch" data-which="${on === "block" ? "" : "block"}" data-ch="${chData(c)}">${I("ban", "sm")} ${on === "block" ? t("Blocked") : t("Block")}</button></div></div>`;
    }).join("")}</div>`;
  }
  function card() {
    const p = prefs();
    const row = (title, d, ctl) => `<div class="set-row"><div class="txt"><div class="t">${title}</div><div class="d">${d}</div></div>${ctl}</div>`;
    const list = (key, empty) => (p[key] || []).length ? `<div class="yt-chs">${p[key].map((c) => chip(c, key)).join("")}</div>` : `<div class="dim sm">${empty}</div>`;
    return `<div class="card set-card" id="set-yt"><h3>${t("YouTube videos")}</h3>
      ${row(t("Language of the videos"), t("Video study plans search YouTube in this language and check the videos' captions in it. You can change it for each plan."),
        `<div class="seg">${LANGS().map(([v, l]) => `<button class="${p.lang === v ? "on" : ""}" data-act="yt-lang" data-val="${v}">${l}</button>`).join("")}</div>`)}
      <div class="set-row yt-lists"><div class="txt"><div class="t">${I("star", "sm")} ${t("Preferred channels")}</div>
          <div class="d">${t("Searched for every topic of a plan, and chosen first when they teach it as well as the others.")}</div>${list("allow", t("None yet."))}</div></div>
      <div class="set-row yt-lists"><div class="txt"><div class="t">${I("ban", "sm")} ${t("Blocked channels")}</div>
          <div class="d">${t("Never shown in your plans, also in the ones you already have.")}</div>${list("block", t("None."))}</div></div>
      <div class="yt-search"><form class="yt-search-f" data-yt-search><input class="input" name="q" value="${esc(S.yt.q)}" placeholder="${t("Find a channel: its name, @handle or link")}" autocomplete="off">
        <button class="btn sm" type="submit">${I("search", "sm")} ${t("Search")}</button></form>${foundHTML()}</div></div>`;
  }
  const before = St.settingsMore;
  St.settingsMore = () => card() + (before ? before() : "");

  // ------------------------------------------------------------------ a plan's videos (plans.js draws them with these)
  St.yt = {
    lang: () => prefs().lang,
    langOptions: LANGS,
    hidden: (it) => listed("block", it),
    // "Dr. Trefor Bazett" with its picture; a star when you prefer it
    channel: (it) => `<span class="yt-by">${avatar(it.channel_avatar, it.channel)}<span>${esc(it.channel)}</span>${listed("allow", it) ? `<span class="yt-pref" title="${t("A channel you prefer")}">${I("star", "xs")}</span>` : ""}</span>`,
    // 12:34 · 1.7M views · 23K likes
    stats: (it) => [it.duration_s ? `<span title="${t("Length of the whole video")}">${clock(it.duration_s)}</span>` : "",
      it.view_count ? `<span>${t.n(it.view_count, "{n} view", "{n} views", { n: compact(it.view_count) })}</span>` : "",
      it.like_count ? `<span>${t.n(it.like_count, "{n} like", "{n} likes", { n: compact(it.like_count) })}</span>` : ""].filter(Boolean).join(`<span class="dim">·</span>`),
    // Week 4 · Thevenin and Norton equivalents
    covers: (it, names) => {
      const w = it.weeks || [];
      const weeks = !w.length ? "" : w.length === 1 ? t("Week {n}", { n: w[0] }) : t("Weeks {a}–{b}", { a: w[0], b: w[w.length - 1] });
      const what = (it.concepts || []).map((c) => names[c] || c).filter(Boolean);
      if (!weeks && !what.length) return "";
      return `<div class="yt-covers">${I("cap", "xs")}<span>${weeks ? `<b>${esc(weeks)}</b>${what.length ? " · " : ""}` : ""}${esc(what.join(", "))}</span>
        ${it.chapter ? `<span class="dim">${t.html("· opens at the chapter “{title}”", { title: it.chapter.title })}</span>` : ""}</div>`;
    },
    menu: (it) => {
      if (!it.channel || !it.channel_id) return "";      // a plan made before 3.0 doesn't know the channel's id
      const pref = listed("allow", it);
      const c = { id: it.channel_id, name: it.channel, avatar: it.channel_avatar };
      return `<details class="yt-menu"><summary class="btn ghost icon sm" title="${t("Channel options")}" aria-label="${t("Channel options")}">${I("more", "sm")}</summary>
        <div class="yt-menu-pop">
          <button class="yt-menu-it" data-act="yt-ch" data-which="${pref ? "" : "allow"}" data-ch="${chData(c)}">${I("star", "sm")} ${pref ? t("Stop preferring this channel") : t("Prefer this channel")}</button>
          <button class="yt-menu-it" data-act="yt-ch" data-which="block" data-ch="${chData(c)}">${I("ban", "sm")} ${t("Never show this channel")}</button></div></details>`;
    },
    // above the videos: how many, how long they take to watch, in which order, "Play all on YouTube", and the ones
    // hidden by the block list
    head: (p, items, hiddenItems) => {
      // an alternative is watched instead of another one: not in the playlist; in the time it counts with the optional
      // ones, as the plan's own numbers do (required + optional, plans.py optional_minutes)
      const watch = items.filter((it) => it.role !== "alternative"), ids = watch.map((it) => it.video);
      const shown = S.yt.showHidden[p.id];
      const extra = (it) => it.role === "optional" || it.role === "alternative";
      const secs = (optional) => items.reduce((a, it) => a + (extra(it) === optional ? watchS(it) : 0), 0);
      const req = secs(false), opt = secs(true);
      const total = [req > 0 ? t("about {time} to watch", { time: watchTime(req) }) : "", opt > 0 ? t("+{m} optional", { m: watchTime(opt) }) : ""].filter(Boolean).join(" · ");
      return `<div class="yt-head">${I("youtube", "sm")}<div class="grow"><b>${t.n(items.length, "{n} video in the order to study", "{n} videos in the order to study")}</b>${total ? ` · <span class="yt-total">${esc(total)}</span>` : ""}
          <span class="dim sm"> · ${t("each one opens on YouTube where its part to watch begins")}</span></div>
        ${ids.length > 1 ? `<button class="btn sm" data-act="url" data-url="${esc("https://www.youtube.com/watch_videos?video_ids=" + ids.slice(0, 50).join(","))}" title="${t("Opens them on YouTube as one playlist, in this order (from the start of each video)")}">${I("play", "sm")} ${t("Play all on YouTube")}</button>` : ""}</div>
        ${hiddenItems.length ? `<div class="note-sm">${I("ban", "sm")}<span>${t.n(hiddenItems.length, "{n} video is hidden: its channel is blocked.", "{n} videos are hidden: their channels are blocked.")}
          <button class="link" data-act="yt-show-hidden" data-id="${esc(p.id)}">${shown ? t("Hide them") : t("Show them")}</button></span></div>` : ""}`;
    },
    showHidden: (p) => !!S.yt.showHidden[p.id],
  };

  // ------------------------------------------------------------------ actions
  async function setChannel(ch, which) {
    const r = await call("yt_channel_set", ch, which);
    if (!r.ok) { toast(r.error, "error"); return; }
    S.yt.prefs = r.prefs;
    if (which === "block") toast(t.html("{name} won't be shown in your plans.", { name: ch.name }), "success");
    else if (which === "allow") toast(t.html("{name} is now a preferred channel.", { name: ch.name }), "success");
    renderView();
  }
  async function search(q) {
    S.yt.q = q;
    if (!q.trim()) { S.yt.found = null; renderView(); return; }
    Object.assign(S.yt, { searching: true, error: "", found: null });
    renderView();
    const r = await call("yt_channel_search", q);
    Object.assign(S.yt, { searching: false, found: r.ok ? r.channels : null, error: r.ok ? "" : r.error || t("YouTube search failed.") });
    if (S.view.name === "settings") renderView();
  }
  const planNew = A["plan-new"];          // plans.js: the new-plan dialog starts with the language chosen here
  Object.assign(A, {
    "plan-new": async (el) => { if (!S.yt.prefs) await load(); return planNew(el); },
    "yt-lang": async (el) => { const r = await call("yt_set_lang", el.dataset.val); if (r.ok) { S.yt.prefs = r.prefs; renderView(); } else toast(r.error, "error"); },
    "yt-ch": (el) => { let ch = {}; try { ch = JSON.parse(el.dataset.ch); } catch (e) { return; } setChannel(ch, el.dataset.which || ""); },
    "yt-show-hidden": (el) => { S.yt.showHidden[el.dataset.id] = !S.yt.showHidden[el.dataset.id]; renderView(); },
  });
  document.addEventListener("submit", (e) => {
    const f = e.target.closest && e.target.closest("[data-yt-search]");
    if (!f) return;
    e.preventDefault();
    search(f.querySelector("input").value);
  });

  // ------------------------------------------------------------------ browser preview (mock)
  const pic = (id) => `https://yt3.googleusercontent.com/ytc/${id}=s88-c-k-c0x00ffffff-no-rj`;
  const MOCK = { lang: "en", allow: [{ id: "UC9rTsvTxJnx1DNrDA3Rqa6A", name: "Dr. Trefor Bazett", handle: "@DrTrefor", avatar: pic("AIdro_lAOi_1eAO-NKrsecIaYKpj05qQlLen6MFoxZv0kp-NWw") }],
    block: [{ id: "UCq0s6dTna0C6b0vKkg3mexg", name: "Ace Tutors", handle: "@AceTutors", avatar: "" }] };
  const FOUND = [{ id: "UCEWpbFLzoYGPfuWUMFPSaoA", name: "The Organic Chemistry Tutor", handle: "@TheOrganicChemistryTutor", avatar: pic("AIdro_lOQikQsmqQS3dG9VBvbJ03Eqqw7NshUvDNorj5UAZL6M0"), followers: 11100000, verified: true },
    { id: "UCueu8TZNRIO5dVIAyUgOKNg", name: "The Chemistry Tutor", handle: "@chemistrytutor", avatar: pic("AIdro_kRV9ItOQYhTIrPdT99jGKx5rPRBMFP8bG4iHamLDn_0tA"), followers: 62600, verified: false }];
  // plans.js's mock plan, with what a plan made now knows about each video
  const TREFOR = { channel_id: "UC9rTsvTxJnx1DNrDA3Rqa6A", channel_avatar: MOCK.allow[0].avatar };
  const MORE = { ibINrxJLvlM: { ...TREFOR, view_count: 1731857, like_count: 23295, duration_s: 374, weeks: [4] },
    OByl4RJxnKA: { channel_id: FOUND[0].id, channel_avatar: FOUND[0].avatar, view_count: 1521538, like_count: 19800, duration_s: 1154, weeks: [4, 5] },
    XQoLVl31ZfQ: { ...TREFOR, view_count: 2079068, like_count: 31400, duration_s: 331, weeks: [5] },
    HZGCoVF3YvM: { channel_id: "UCYO_jab_esuFRV4b17AJtAw", channel_avatar: pic("AIdro_nFzZFPLxPZRHcE3SSwzdrbuWqfoWYwLAu0_2iO6blQYAU"), view_count: 6001555, like_count: 148000, duration_s: 911, weeks: [5], chapter: { start: 312, title: "Bayes' rule as a picture" } } };
  const planGet = St.mocks.plan_get;
  if (planGet) {
    St.mocks.plan_get = (id) => {
      const r = planGet(id), res = r.plan && r.plan.result;
      if (res && Array.isArray(res.items)) {           // (a cheatsheet's result.items is a count)
        res.items = res.items.map((it) => ({ ...it, ...(MORE[it.video] || {}) }));
        res.items.push({ n: 5, video: "cqTwHnNbc8g", title: "Bayes' Theorem EXPLAINED with Examples", channel: "Ace Tutors", channel_id: "UCq0s6dTna0C6b0vKkg3mexg", url: "https://www.youtube.com/watch?v=cqTwHnNbc8g",
          start: 0, end: 483, whole: true, segments: [{ start: 0, end: 483 }], minutes: 8, role: "optional", concepts: ["c3"], weeks: [5], view_count: 785057, like_count: 1200, duration_s: 483,
          why: "", teaches: [{ concept: "c3", state: "partial", skills: ["invert a conditional"], missing: [] }], state: "verified", captions: "auto, en", quotes: [], excerpts: [], errors: [] });
      }
      return r;
    };
  }
  St.mocks.yt_prefs = () => ({ ok: true, prefs: JSON.parse(JSON.stringify(MOCK)) });
  St.mocks.yt_set_lang = (lang) => { MOCK.lang = lang; return St.mocks.yt_prefs(); };
  St.mocks.yt_channel_search = () => ({ ok: true, channels: FOUND });
  St.mocks.yt_channel_set = (ch, which) => {
    MOCK.allow = MOCK.allow.filter((c) => c.id !== ch.id); MOCK.block = MOCK.block.filter((c) => c.id !== ch.id);
    if (which) MOCK[which].unshift(ch);
    return St.mocks.yt_prefs();
  };
})();
