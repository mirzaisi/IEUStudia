/* IEUStudia — the lecture player (4.0.0): a part of a lecture played in the app, streamed from Panopto through the
 * app's own sign-in (player.py's proxy on 127.0.0.1). The video; beside it the screen of the current second (the
 * lecture's captured screens, with their titles and text when the screen timeline has them); below, the transcript
 * with the passage being said highlighted and kept in view (not while you scrolled it in the last 5 s). Click a
 * passage or a screen to jump there; speed 1x to 2x; Space plays or pauses, ← → go 5 s, J and L 10 s (never while
 * typing); it goes on where you stopped (per lecture, on this PC; else where Panopto last had you); "Open in Panopto
 * at this moment". The sound-only podcast shows the screens as its picture; the copy on this PC plays when Panopto
 * can't be reached. Opened by Play on a lecture's transcript page (app.js), from a search hit, and by
 * Studia.player.open(dayKey, part, seconds) (the screens' "Play from here"). Plugs in through window.Studia as the
 * "player" view. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, call, esc, I, SPIN, toast, A } = St;
  const D = typeof document !== "undefined" ? document : null;
  const $ = (s) => (D && D.querySelector ? D.querySelector(s) : null);
  const said = (p) => (St.said ? St.said(p) : p);
  const SPEEDS = [1, 1.25, 1.5, 1.75, 2];
  const FOLLOW_AFTER_MS = 5000;        // the transcript follows again this long after you scrolled it
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage may be unavailable */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* storage may be unavailable */ } },
  };
  const now = () => Date.now();
  let P = null;                        // the open player (one at a time)

  // ------------------------------------------------------------------ small helpers
  function clock(sec) {                // 34:10 · 1:02:05
    const s = Math.max(0, Math.floor(+sec || 0)), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    return (h ? `${h}:${String(m).padStart(2, "0")}` : String(m)) + `:${String(r).padStart(2, "0")}`;
  }
  function lastAtOrBefore(list, sec, key) {      // the last item that started by ``sec`` (binary search), -1 if none
    let lo = 0, hi = list.length - 1, at = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (list[mid][key] <= sec) { at = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return at;
  }
  const partOf = (p) => ((p.day && p.day.parts) || []).find((x) => x.index === p.part) || null;
  const speed = () => { const v = +store.get("studia-play-speed"); return SPEEDS.includes(v) ? v : 1; };
  const soundOnly = () => store.get("studia-play-sound") === "1";      // chosen to save data (a phone hotspot)
  const resumeKey = (id) => `studia-play:${id}`;
  function saved(id) {
    try { const v = JSON.parse(store.get(resumeKey(id)) || "null"); return v && isFinite(v.t) ? v : null; } catch (e) { return null; }
  }
  const isAudio = (p) => !!p.info && (p.info.kind === "audio" || p.noPicture);

  // ------------------------------------------------------------------ open and close
  async function open(dayKey, part, seconds) {
    if (!dayKey) return;
    const at = seconds == null || seconds === "" || !isFinite(+seconds) ? null : Math.max(0, +seconds);
    part = +part || 0;
    if (P && P.dayKey === dayKey && S.view.name === "player") {          // this lecture is open: there, or its other part
      if (!part || part === P.part) { if (at != null) jump(at); return; }
      if (!partOf({ day: P.day, part })) return;
      keep(P);
      P.part = part; P.at = at;
      St.renderTop();
      load();
      return;
    }
    const day = S.day && S.day.day_key === dayKey ? S.day : P && P.dayKey === dayKey ? P.day : null;
    const from = S.view.name === "player" ? S.view.from : { ...S.view, scroll: ($("#view") || {}).scrollTop || 0 };
    stop();
    const p = P = { dayKey, part: part || +store.get(`studia-play-part:${dayKey}`) || 1, at, day, info: null, screens: [],
      paras: [], cur: -1, scr: -1, loading: true, error: "", userAt: 0, savedAt: 0, retried: false, video: makeVideo() };
    p.watch = setInterval(watch, 1000);
    St.go({ name: "player", dayKey, from });
    if (!p.day) {
      const r = await call("get_day", dayKey);
      if (P !== p) return;
      if (!r.ok) { p.loading = false; p.error = r.error || t("This lecture is no longer in your library."); St.renderView(); return; }
      p.day = r.day;
    }
    if (!partOf(p)) p.part = ((p.day.parts || [])[0] || {}).index || 1;
    load();
  }

  function stop() {                    // the player closes: where it was is kept, its stream ends
    const p = P;
    if (!p) return;
    keep(p);
    P = null;
    clearInterval(p.watch);
    clearTimeout(p.waitTimer);
    const v = p.video;
    if (v) { try { v.pause(); v.removeAttribute("src"); v.load(); } catch (e) { /* already gone */ } if (v.remove) v.remove(); }
    if (p.info && p.info.token) call("player_close", p.info.token);
  }

  async function load(avoid) {         // the part's address from the app (player_open), then it plays
    const p = P;
    if (!p) return;
    const part = partOf(p);
    if (!p.info || !part || p.info.delivery_id !== part.id) {     // another part (or the first): its words at once
      if (p.info && p.info.token) call("player_close", p.info.token);
      p.info = null; p.screens = []; p.cur = -1; p.scr = -1;
      p.paras = ((part || {}).paragraphs || []).map((x, i) => ({ ...x, i }));
      const v = p.video;
      if (v && v.getAttribute("src")) { v.pause(); v.removeAttribute("src"); v.load(); }
    }
    p.loading = true; p.error = ""; p.noPicture = false;
    St.renderView();
    const ask = avoid || (soundOnly() ? "video" : "");
    const r = await call("player_open", p.dayKey, p.part, ask);
    if (P !== p || partOf(p) !== part) { if (r && r.ok && r.token) call("player_close", r.token); return; }   // closed, or another part asked for
    const old = p.info;
    if (!r.ok) {
      p.loading = false; p.error = r.error || t("The lecture couldn't be loaded from Panopto. Try again, or open it in Panopto.");
      St.renderView();
      return;
    }
    if (old && old.token && old.token !== r.token) call("player_close", old.token);
    p.info = r;
    p.screens = (r.screens || []).slice().sort((a, b) => a.t0 - b.t0);
    p.cur = -1; p.scr = -1; p.startedAt = null;
    if (ask !== "video") p.soundChecked = false;     // (the sound-only one asked for and the video came: not again)
    let at = p.at;                                      // asked for (a search hit, Play from here) > yours > Panopto's
    if (at == null) {
      const mine = saved(r.delivery_id);
      at = mine ? mine.t : +r.start || 0;
      if (r.duration && at > r.duration - 20) at = 0;   // (watched to the end: from the start again)
    }
    p.at = null; p.seekTo = at; p.loading = true;
    if (r.note) toast(r.note, "info");
    St.renderView();
    const v = p.video;
    v.src = at > 0 ? `${r.url}#t=${Math.floor(at)}` : r.url;          // it starts there (the fragment stays here)
    v.playbackRate = speed();
    const go = v.play();
    if (go && go.catch) go.catch(() => { /* not allowed to start by itself: its own Play button does */ });
  }

  function watch() {                   // the view was left (the sidebar, another page): the player closes
    if (P && S.view.name !== "player") stop();
  }

  function keep(p) {                   // where you are, for the next time (per lecture), and which part
    const v = p && p.video;
    if (!p || !p.info || !v || !p.info.delivery_id || !isFinite(v.currentTime) || p.seekTo != null) return;
    const dur = v.duration || p.info.duration || 0;
    if (dur && v.currentTime > dur - 20) store.del(resumeKey(p.info.delivery_id));
    else if (v.currentTime > 5) store.set(resumeKey(p.info.delivery_id), JSON.stringify({ t: Math.floor(v.currentTime), at: now() }));
    store.set(`studia-play-part:${p.dayKey}`, String(p.part));
    p.savedAt = now();
  }

  // ------------------------------------------------------------------ the video element (one per open player)
  function makeVideo() {
    if (!D || !D.createElement) return null;
    const v = D.createElement("video");
    v.className = "lp-video";
    v.controls = true;
    v.preload = "auto";
    v.setAttribute("playsinline", "");
    v.setAttribute("controlslist", "nodownload noremoteplayback");
    v.addEventListener("loadedmetadata", () => {
      const p = P;
      if (!p || p.video !== v) return;
      if (p.seekTo != null) { if (Math.abs((v.currentTime || 0) - p.seekTo) > 2) v.currentTime = p.seekTo; p.seekTo = null; }
      const flat = !v.videoWidth;                       // sound only (a kept audio file, a podcast without picture)
      if (flat !== !!p.noPicture) { p.noPicture = flat; St.renderView(); }
    });
    v.addEventListener("timeupdate", tick);
    v.addEventListener("waiting", () => waiting(true));
    for (const ev of ["playing", "canplay", "seeked"]) v.addEventListener(ev, () => waiting(false));
    v.addEventListener("playing", () => { if (P) P.retried = false; });
    v.addEventListener("pause", () => { if (P && P.video === v) keep(P); });
    v.addEventListener("ratechange", () => { const b = $("#lp-speeds"); if (b) b.innerHTML = speedButtons(v.playbackRate); });
    v.addEventListener("error", () => failed(v));
    v.addEventListener("ended", ended);
    return v;
  }

  function waiting(on) {               // "Loading…" while the video waits for more of the lecture
    const p = P;
    if (!p) return;
    clearTimeout(p.waitTimer);
    if (!on) { p.loading = false; setWait(""); return; }
    p.waitTimer = setTimeout(() => {
      if (P !== p) return;
      setWait(t("Loading the lecture…"));
      p.waitTimer = setTimeout(() => { if (P === p) setWait(t("Still loading: the internet connection seems slow.")); }, 12000);
    }, 700);
  }
  function setWait(text) {
    const el = $("#lp-wait");
    if (!el) return;
    el.innerHTML = text ? `${SPIN()}<span>${esc(text)}</span>` : "";
    el.classList.toggle("on", !!text);
  }

  function failed(v) {                 // the stream broke off: once more from where it was (a fresh address)
    const p = P;
    if (!p || p.video !== v || !p.info || !v.getAttribute("src")) return;
    const code = v.error ? v.error.code : 0;
    if (p.retried) {
      p.error = t("The lecture stopped loading. Check your internet connection, then press Try again. You can also open it in Panopto.");
      p.loading = false;
      St.renderView();
      return;
    }
    p.retried = true;
    p.at = isFinite(v.currentTime) && v.currentTime > 0 ? v.currentTime : null;
    load(p.info.kind === "video" && code >= 3 ? "video" : "");      // a picture it can't show: the sound-only podcast
  }

  function ended() {                   // on to the next part of the class, from its start
    const p = P;
    if (!p) return;
    if (p.info && p.info.delivery_id) store.del(resumeKey(p.info.delivery_id));    // watched to the end: afresh next time
    const parts = (p.day.parts || []).map((x) => x.index), next = parts[parts.indexOf(p.part) + 1];
    if (next) { p.part = next; p.at = 0; load(); toast(t("Part {n}", { n: next }), "info"); }
  }

  // ------------------------------------------------------------------ following the video
  function tick() {
    const p = P;
    if (!p || !p.video) return;
    const sec = p.video.currentTime || 0;
    const i = lastAtOrBefore(p.paras, sec + 0.3, "t");
    if (i !== p.cur) {
      const box = $("#lp-tx");
      const old = box && box.querySelector(".lp-para.now");
      if (old) old.classList.remove("now");
      p.cur = i;
      const el = box && i >= 0 ? box.querySelector(`.lp-para[data-i="${i}"]`) : null;
      if (el) { el.classList.add("now"); follow(el, box, false); }
    }
    const k = Math.max(0, lastAtOrBefore(p.screens, sec + 0.3, "t0"));
    if (p.screens.length && k !== p.scr) { p.scr = k; drawScreen(); }
    if (p.info && p.info.kind === "video" && !p.soundChecked && sec - (p.startedAt ?? sec) > 3) soundCheck(p);
    if (p.startedAt == null && !p.video.paused) p.startedAt = sec;
    if (now() - p.savedAt > 5000 && !p.video.paused) keep(p);
  }
  function follow(el, box, instant) {  // the passage being said in view (a third down), unless you scrolled lately
    const p = P;
    if (!p || !el || !box || now() - p.userAt < FOLLOW_AFTER_MS) return;
    const top = Math.max(0, el.offsetTop - box.clientHeight / 3);
    if (Math.abs(box.scrollTop - top) < 4) return;
    if (box.scrollTo) box.scrollTo({ top, behavior: instant ? "instant" : "smooth" });
    else box.scrollTop = top;
  }
  function soundCheck(p) {             // a podcast without a sound track: its sound-only version, from here
    p.soundChecked = true;
    const v = p.video;
    if (v.webkitAudioDecodedByteCount === 0 && v.webkitVideoDecodedByteCount > 0) {
      p.at = v.currentTime;
      toast(t("This recording's video has no sound: playing its sound-only version, with the slides as the picture."), "info");
      load("video");
    }
  }
  function drawScreen() {
    const el = $("#lp-screen");
    if (el) el.innerHTML = screenHTML(P);
    const pic = $("#lp-pic");
    if (pic) pic.outerHTML = pictureHTML(P);
  }
  function jump(sec, play = true) {
    const p = P;
    if (!p || !p.video) return;
    const v = p.video;
    if (p.seekTo != null || !v.getAttribute("src") || v.readyState < 1) p.seekTo = Math.max(0, sec);   // (once it has loaded)
    else v.currentTime = Math.max(0, Math.min(sec, (v.duration || sec + 1) - 0.5));
    p.userAt = 0;                      // the transcript follows the jump at once
    if (play && v.paused) { const go = v.play(); if (go && go.catch) go.catch(() => {}); }
  }
  function skip(by) { const p = P, v = p && p.video; if (v) jump((p.seekTo != null ? p.seekTo : v.currentTime || 0) + by, false); }

  // ------------------------------------------------------------------ the view
  function screenAt(p) { return p.screens.length ? p.screens[Math.max(0, p.scr)] : null; }
  function screenCaption(s) {
    return `${t("Slide {n}", { n: s.n })} · ${clock(s.t0)}${s.t1 > s.t0 ? `–${clock(s.t1)}` : ""}`;
  }
  function screenHTML(p) {             // beside the video: the screen of this second, its title and text, and steps
    const s = screenAt(p);
    if (!s) return "";
    const k = Math.max(0, p.scr);
    const nav = `<span class="lp-steps"><button class="btn ghost icon sm" data-act="lp-screen-step" data-by="-1" ${k > 0 ? "" : "disabled"} title="${esc(t("Previous slide"))}" aria-label="${esc(t("Previous slide"))}">${I("chevron-left", "sm")}</button><button class="btn ghost icon sm" data-act="lp-screen-step" data-by="1" ${k < p.screens.length - 1 ? "" : "disabled"} title="${esc(t("Next slide"))}" aria-label="${esc(t("Next slide"))}">${I("chevron-right", "sm")}</button></span>`;
    const pic = isAudio(p) ? "" : s.image ? `<img src="${esc(s.image)}" alt="${esc(t("Slide {n}", { n: s.n }))}" data-act="lp-jump" data-t="${s.t0}" role="button" tabindex="0" title="${esc(t("Play from the start of this slide"))}"${s.w ? ` width="${+s.w}" height="${+s.h}"` : ""}>` : "";
    return `${pic}<div class="lp-cap"><div class="lp-cap-row"><span class="lp-when">${esc(screenCaption(s))}</span>${nav}</div>
      ${s.title ? `<div class="lp-title">${esc(s.title)}</div>` : ""}${s.text ? `<div class="lp-text">${esc(s.text)}</div>` : ""}</div>`;
  }
  function pictureHTML(p) {            // the picture of a recording with sound only: the screen of this second
    const s = screenAt(p);
    if (s && s.image) return `<img id="lp-pic" class="lp-pic" src="${esc(s.image)}" alt="${esc(t("Slide {n}", { n: s.n }))}">`;
    const d = p.day || {};
    return `<div id="lp-pic" class="lp-pic none">${I("wave")}<span>${esc((d.course || {}).code || "")}</span><small>${esc(t("Sound only"))}</small></div>`;
  }
  const times = (x) => (t.num ? t.num(x, Number.isInteger(x) ? 0 : Number.isInteger(x * 10) ? 1 : 2) : String(x)) + "×";   // 1.25× · 1,25×
  function speedButtons(rate) {
    return SPEEDS.map((x) => `<button class="${Math.abs(x - rate) < 0.01 ? "on" : ""}" data-act="lp-speed" data-v="${x}" aria-pressed="${Math.abs(x - rate) < 0.01}">${esc(times(x))}</button>`).join("");
  }
  function partsHTML(p) {
    const parts = (p.day && p.day.parts) || [];
    if (parts.length < 2) return "";
    return `<span class="seg sm lp-parts" role="group" aria-label="${esc(t("Parts"))}">${parts.map((x) => `<button class="${x.index === p.part ? "on" : ""}" data-act="lp-part" data-part="${x.index}" aria-pressed="${x.index === p.part}">${esc(t("Part {n}", { n: x.index }))}</button>`).join("")}</span>`;
  }
  function helpHTML(p) {
    const r = p.info || {};
    const bits = [t("Playing here doesn't count in Panopto's viewing statistics.")];
    if (r.local) bits.push(t("It plays from the copy on this PC: no internet data used."));
    else if (r.bytes && r.duration) bits.push(t("It uses about {mb} MB of internet data an hour (mind it on a phone hotspot).", { mb: t.num ? t.num(Math.max(10, Math.round(r.bytes / r.duration * 3600 / 1e6 / 10) * 10)) : Math.round(r.bytes / r.duration * 3.6e-3) }));
    bits.push(t("Keys: Space plays or pauses, ← and → go 5 s, J and L go 10 s."));
    return `${I("info", "xs")}<span>${bits.map(esc).join(" ")}</span>`;
  }
  function txHTML(p) {                 // the part's transcript, with where each screen starts
    if (!p.paras.length) return `<div class="lp-none dim">${esc(t("This part has no transcript yet."))}</div>`;
    let h = "", k = 0;
    for (const para of p.paras) {
      while (k < p.screens.length && p.screens[k].t0 <= para.t + 0.5) {
        const s = p.screens[k++];
        h += `<button class="lp-shot" data-act="lp-jump" data-t="${s.t0}" title="${esc(t("Play from the start of this slide"))}">${I("presentation", "xs")}<span>${esc(screenCaption(s))}${s.title ? ` · ${esc(s.title)}` : ""}</span></button>`;
      }
      h += `<div class="lp-para ${para.tag ? "tr" : ""}" data-act="lp-jump" data-t="${para.t}" data-i="${para.i}" role="button" tabindex="0" title="${esc(t("Play from this moment"))}"><span class="lp-ts">${esc(para.ts || clock(para.t))}</span><span class="lp-words">${esc(para.text)}</span></div>`;
    }
    return h;
  }
  function stageHTML(p) {
    let over = "";
    if (p.error) {
      over = `<div class="lp-err" role="alert">${I("alert")}<b>${esc(t("This lecture can't play here right now"))}</b><p>${esc(p.error)}</p>
        <div class="lp-err-acts"><button class="btn sm" data-act="lp-retry">${I("retry", "sm")} ${esc(t("Try again"))}</button>${viewerUrl(p) ? `<button class="btn ghost sm" data-act="lp-panopto">${I("external", "sm")} ${esc(t("Open in Panopto"))}</button>` : ""}</div></div>`;
    }
    const first = p.loading && !p.info;
    return `${isAudio(p) && !p.error ? pictureHTML(p) : ""}<div id="lp-wait" class="lp-wait ${first ? "on" : ""}">${first ? `${SPIN()}<span>${esc(t("Getting the lecture from Panopto… This takes a few seconds."))}</span>` : ""}</div>${over}`;
  }
  function viewHTML() {
    const p = P;
    if (!p) {
      return `<div class="view-inner"><div class="empty"><div class="art">${I("play")}</div><h3>${esc(t("The player was closed"))}</h3>
        <div><button class="btn sm" data-act="lp-back">${I("arrow-left", "sm")} ${esc(t("Back"))}</button></div></div></div>`;
    }
    if (!p.day) {                      // the lecture itself is still being read (or is gone): only what is happening
      return `<div class="lp no-math"><div class="lp-top solo"><div class="lp-stage ${p.error ? "failed" : ""}" id="lp-stage">${stageHTML(p)}</div></div></div>`;
    }
    const side = !p.error && p.screens.length > 0;            // (sound only: the slide is the picture, its words here)
    const rate = p.video && p.info ? p.video.playbackRate || speed() : speed();
    const ready = !!p.info && !p.error;
    return `<div class="lp no-math">
      <div class="lp-top ${side ? "" : "solo"}">
        <div class="lp-stage ${isAudio(p) ? "audio" : ""} ${p.error ? "failed" : ""}" id="lp-stage">${stageHTML(p)}</div>
        ${side ? `<div class="lp-screen" id="lp-screen" aria-live="polite">${screenHTML(p)}</div>` : ""}
      </div>
      <div class="lp-bar">
        <span class="lp-speed-l">${esc(t("Speed"))}</span><span class="seg sm" id="lp-speeds" role="group" aria-label="${esc(t("Speed"))}">${speedButtons(rate)}</span>
        ${partsHTML(p)}<span class="lp-right">
        ${p.info && p.info.local ? "" : `<button class="btn ghost sm lp-sound ${soundOnly() ? "on" : ""}" data-act="lp-sound" aria-pressed="${soundOnly()}" title="${esc(t("Only the sound, with the slides as the picture: it uses much less internet data (good on a phone hotspot)"))}">${I("wave", "sm")} ${esc(t("Sound only"))}</button>`}
        ${viewerUrl(p) && !p.error ? `<button class="btn sm" data-act="lp-panopto" ${ready ? "" : "disabled"} title="${esc(t("Opens Panopto's own player in your browser, at this moment of the lecture"))}">${I("external", "sm")} ${esc(t("Open in Panopto at this moment"))}</button>` : ""}</span>
      </div>
      <div class="lp-help">${helpHTML(p)}</div>
      <div class="lp-tx" id="lp-tx">${txHTML(p)}</div>
    </div>`;
  }
  function viewerUrl(p) {
    const part = partOf(p);
    return (p.info && p.info.viewer_url) || (part && part.viewer_url) || "";
  }

  St.views.player = () => viewHTML();
  St.after.player = () => {           // the same video element in the page drawn again: it plays on
    const p = P, stage = $("#lp-stage");
    if (!p || !stage || !p.video) return;
    if (p.video.parentNode !== stage) stage.insertBefore(p.video, stage.firstChild);
    const box = $("#lp-tx");
    p.cur = -1;
    if (box && p.video.getAttribute("src")) {
      const i = lastAtOrBefore(p.paras, (p.seekTo ?? p.video.currentTime) + 0.3, "t");
      const el = i >= 0 ? box.querySelector(`.lp-para[data-i="${i}"]`) : null;
      if (el) { el.classList.add("now"); p.cur = i; p.userAt = 0; follow(el, box, true); }
    }
  };
  St.crumbs.player = () => {
    const d = (P && P.day) || null;
    const where = d ? [d.date_label, (d.parts || []).length > 1 ? t("Part {n}", { n: P.part }) : ""].filter(Boolean).join(" · ") : "";
    return `<button class="btn ghost sm" data-act="lp-back">${I("arrow-left", "sm")} ${esc(t("Back"))}</button> ${d ? esc(d.title) : esc(t("Play"))}${where ? ` <span class="dim">· ${esc(where)}</span>` : ""}`;
  };

  // ------------------------------------------------------------------ keys and scrolling
  function typing(el) {
    return !!(el && el.closest && el.closest("input, textarea, select, [contenteditable]:not([contenteditable=false])"));
  }
  if (D && D.addEventListener) {
    window.addEventListener("keydown", (e) => {
      const p = P;
      if (!p || S.view.name !== "player" || e.ctrlKey || e.altKey || e.metaKey || e.defaultPrevented || typing(e.target)) return;
      const overlay = $("#overlay");
      if ((overlay && overlay.innerHTML) || $("#menu") || !p.video || !p.info) return;   // a dialog or a menu is open
      const v = p.video, key = e.key;
      if (key === " " || key === "Spacebar") {
        if (v.paused) { const go = v.play(); if (go && go.catch) go.catch(() => {}); } else v.pause();
      } else if (key === "ArrowLeft") skip(-5);
      else if (key === "ArrowRight") skip(5);
      else if (key === "j" || key === "J") skip(-10);
      else if (key === "l" || key === "L") skip(10);
      else return;
      e.preventDefault();
      e.stopPropagation();
    }, true);
    const scrolled = (e) => {          // you scroll the transcript: it stops following for a while
      const box = e.target && e.target.closest ? e.target.closest("#lp-tx") : null;
      if (P && box && (e.type !== "mousedown" || e.target === box)) P.userAt = now();
    };
    for (const ev of ["wheel", "touchstart", "mousedown"]) D.addEventListener(ev, scrolled, { capture: true, passive: true });
    D.addEventListener("keydown", (e) => { if (["PageUp", "PageDown", "Home", "End", "ArrowUp", "ArrowDown"].includes(e.key)) scrolled(e); }, true);
    window.addEventListener("pagehide", () => keep(P));
  }

  // ------------------------------------------------------------------ actions
  Object.assign(A, {
    "lp-play": (el) => open((S.day || {}).day_key, +(el && el.dataset.part) || 0),
    "lp-jump": (el) => jump(+el.dataset.t),
    "lp-screen-step": (el) => {
      const p = P;
      if (!p || !p.screens.length) return;
      const k = Math.max(0, Math.min(p.screens.length - 1, Math.max(0, p.scr) + +el.dataset.by));
      p.scr = k; drawScreen(); jump(p.screens[k].t0);
    },
    "lp-speed": (el) => {
      const x = +el.dataset.v;
      if (!SPEEDS.includes(x)) return;
      store.set("studia-play-speed", String(x));
      if (P && P.video) P.video.playbackRate = x;
      const b = $("#lp-speeds"); if (b) b.innerHTML = speedButtons(x);
    },
    "lp-part": (el) => {
      const p = P, part = +el.dataset.part;
      if (!p || part === p.part) return;
      keep(p);
      p.part = part; p.at = null;
      St.renderTop();
      load();
    },
    "lp-panopto": () => {               // Panopto's own player, in the browser, where you are (this one pauses)
      const p = P;
      if (!p) return;
      const url = viewerUrl(p);
      if (!url) return;
      const v = p.video, sec = v && isFinite(v.currentTime) ? Math.floor(v.currentTime) : 0;
      if (v && !v.paused) v.pause();
      said(call("open_url", `${url}${url.includes("?") ? "&" : "?"}start=${sec}`));
    },
    "lp-sound": () => {                 // sound only (much less data), or the picture again, from this moment
      const p = P;
      store.set("studia-play-sound", soundOnly() ? "0" : "1");
      if (!p || p.error) { St.renderView(); return; }
      const v = p.video;
      keep(p);
      p.at = v && isFinite(v.currentTime) && v.currentTime > 0 ? v.currentTime : p.at;
      load();
    },
    "lp-retry": () => { if (P) { P.retried = false; P.at = P.video && P.video.currentTime > 0 ? P.video.currentTime : P.at; load(); } },
    "lp-back": () => {                  // back where you came from; the transcript at the moment you were at
      const p = P, v = p && p.video, part = p && partOf(p);
      const at = v && isFinite(v.currentTime) && v.currentTime > 0 ? v.currentTime : null;
      const f = S.view.from;
      stop();
      if (f && f.name === "day" && St.openDay) {
        S.view = f;
        St.openDay(f.dayKey, false, at != null && part ? { t: at, id: part.id } : null);
        return;
      }
      St.go({ ...(f || { name: "home" }), jumpWeek: false });
      if (f && f.scroll) { const view = $("#view"); if (view) view.scrollTo({ top: f.scroll, behavior: "instant" }); }
    },
    "lp-hit": (el) => {                 // Play beside a moment of a lecture in the search results
      const it = (S.search.items || [])[+el.dataset.i], m = it && (it.moments || [])[+el.dataset.m];
      if (m) open(it.day_key, m.part, m.t);
    },
  });

  St.player = {
    open,
    close: stop,
    /** The Play button of a lecture's transcript page (its toolbar, beside Export). */
    btn: (disabled) => `<button class="btn sm lp-btn" data-act="lp-play" ${disabled ? "disabled" : ""} title="${esc(t("Play the lecture here, with its slides and transcript following along"))}">${I("play", "sm")} ${esc(t("Play"))}</button>`,
    /** Play on a part's heading (a class recorded in several parts). */
    partBtn: (part) => `<button class="btn ghost sm" data-act="lp-play" data-part="${+part.index}" title="${esc(t("Play this part here, with its slides and transcript following along"))}">${I("play", "sm")} ${esc(t("Play"))}</button>`,
    /** A lecture's moment in the search results, with Play beside it. */
    hitRow: (button, i, k) => `<div class="lp-hitrow">${button}<button class="btn ghost icon sm lp-hit" data-act="lp-hit" data-i="${i}" data-m="${k}" title="${esc(t("Play from this moment"))}" aria-label="${esc(t("Play from this moment"))}">${I("play", "sm")}</button></div>`,
    /** A search hit opened while the player is open: it plays there (true), else the transcript opens. */
    hit(it, k) {
      if (!P || S.view.name !== "player" || !it || it.kind !== "lecture") return false;
      const m = (it.moments || [])[k] || (it.moments || [])[0];
      if (!m) return false;
      open(it.day_key, m.part, m.t);
      return true;
    },
    /** Is a lecture playing on the player's page (a screen found by search then plays there)? */
    isOpen: () => !!P && S.view.name === "player",
    get state() { return P; },
  };
})();
