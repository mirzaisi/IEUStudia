/* IEUStudia — the page's language. Text is written in English in the code and shown through t():
 *   t("Can't reach {host}.", { host })              the Turkish table (from Python, locales/tr/*.json) or the English
 *   t.n(n, "{n} lecture", "{n} lectures", {...})    a count: Turkish keeps the noun singular ("{n} ders")
 *   t.c("state", "Open")                            one English word, two meanings (key "state|Open")
 *   t.html("Pick <b>{name}</b>", { name })          a message with markup: the values are escaped
 * Dates and numbers: t.date(iso, "long" | "short" | "dm" | "dmw" | "time" | "weekday"), t.num, t.pct, t.dur,
 * t.ago, t.left, t.list. The table arrives with call("i18n") before the first screen; until then (and in English)
 * t() returns the English. Loaded before every other script. */
(function () {
  "use strict";
  const L = { lang: "en", table: {}, setting: "", windows: "en" };
  const locale = () => (L.lang === "tr" ? "tr" : "en-GB");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fill = (s, p) => (p ? s.replace(/\{(\w+)\}/g, (m, k) => (k in p ? String(p[k]) : m)) : s);
  const PSEUDO = { a: "à", c: "ç", e: "é", i: "î", n: "ñ", o: "ø", r: "ř", s: "š", u: "ü", y: "ý", A: "À", C: "Ç", E: "É", I: "Î", N: "Ñ", O: "Ø", R: "Ř", S: "Š", U: "Ü", Y: "Ý" };
  const pseudo = (s) => `⟦${s.split(/(\{\w+\}|<[^>]+>|&\w+;)/).map((p, i) => (i % 2 ? p : p.replace(/[a-zA-Z]/g, (c) => PSEUDO[c] || c))).join("")}${"·".repeat(Math.max(1, Math.floor(s.length * 0.35)))}⟧`;
  const look = (key, en) => (L.lang === "en" ? en : L.lang === "pseudo" ? pseudo(en) : L.table[key] || en);

  function t(text, p) { return fill(look(text, text), p); }
  t.n = (n, one, other, p) => {
    const q = { n, ...(p || {}) };
    if (L.lang === "tr") return fill(L.table[one] || (n === 1 ? one : other), q);
    const en = n === 1 ? one : other;
    return fill(look(en, en), q);
  };
  t.c = (ctx, text, p) => fill(look(`${ctx}|${text}`, text), p);
  t.html = (text, p) => { const q = {}; for (const k in p || {}) q[k] = esc(p[k]); return fill(look(text, text), q); };

  // ---- dates and numbers: Intl with "en-GB" (the app's English: "Wed 14 Oct") or "tr" ("14 Eki Çar")
  const FMT = {
    long: { weekday: "long", day: "numeric", month: "long", year: "numeric" },   // Wednesday, 14 October 2026 · 14 Ekim 2026 Çarşamba
    short: { weekday: "short", day: "numeric", month: "short" },                 // Wed 14 Oct · 14 Eki Çar
    dmw: { weekday: "short", day: "numeric", month: "short" },
    dm: { day: "numeric", month: "short" },                                      // 14 Oct · 14 Eki
    dmy: { day: "numeric", month: "short", year: "numeric" },                    // 14 Oct 2026 · 14 Eki 2026
    time: { hour: "2-digit", minute: "2-digit", hour12: false },                 // 09:05
    weekday: { weekday: "short" },                                               // Wed · Çar
    mon: { month: "short" },                                                     // Oct · Eki
  };
  const fmts = {};
  const asDate = (x) => (x instanceof Date ? x : new Date(/^\d{4}-\d{2}-\d{2}$/.test(String(x)) ? `${x}T00:00:00` : x));
  t.date = (x, style = "short") => {
    if (!x) return "";
    const d = asDate(x);
    if (isNaN(d)) return "";
    const key = `${locale()}|${style}`;
    const out = (fmts[key] || (fmts[key] = new Intl.DateTimeFormat(locale(), FMT[style] || FMT.short))).format(d);
    return L.lang === "tr" ? out : out.replace(/\bSept\b/, "Sep");     // en-GB writes "Sept"; the app (and i18n.py) "Sep"
  };
  // 31,200.5 · 31.200,5; sizes pass group = false and stay as they were: 1012 MB, not 1,012 MB
  // each formatter made once per language: making one costs far more than using it (a "job" event's progress
  // went through t.pct for every row and took up to 0.7 s of the page's time in a full sync)
  const made = (key, make) => fmts[key] || (fmts[key] = make());
  t.num = (x, digits = 0, group = true) => made(`${locale()}|num|${digits}|${group}`, () => new Intl.NumberFormat(locale(), { minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: group })).format(x || 0);
  t.pct = (f) => made(`${locale()}|pct`, () => new Intl.NumberFormat(locale(), { style: "percent", maximumFractionDigits: 0 })).format(f || 0);   // 45% · %45
  t.list = (items, type = "conjunction") => made(`${locale()}|list|${type}`, () => new Intl.ListFormat(locale(), { type })).format(items.map(String));      // A, B and C · A, B ve C
  t.dur = (s) => {                                                                                                  // 1 h 5 min · 1 sa 5 dk
    const m = Math.round((s || 0) / 60);
    if (m < 60) return t("{m} min", { m });
    return t("{h} h {m} min", { h: Math.floor(m / 60), m: m % 60 });
  };
  t.left = (s) => (s == null ? "" : s < 60 ? t("less than a minute left") : t("about {time} left", { time: t.dur(s) }));
  t.ago = (iso) => {
    if (!iso) return t("never");
    const d = (Date.now() - new Date(iso).getTime()) / 1000;
    if (d < 45) return t("just now");
    if (d < 3600) return t("{n} min ago", { n: Math.round(d / 60) });
    if (d < 86400) return t("{n} h ago", { n: Math.round(d / 3600) });
    return t("{n} d ago", { n: Math.round(d / 86400) });
  };
  t.upper = (s) => String(s).toLocaleUpperCase(locale());       // Turkish i → İ
  t.cmp = (a, b) => String(a).localeCompare(String(b), locale());

  // ---- the language itself
  t.lang = () => L.lang;
  t.setting = () => L.setting;
  t.windows = () => L.windows;
  t.load = (data) => {
    if (!data) return;
    L.lang = data.lang || "en"; L.table = data.table || {}; L.setting = data.setting || ""; L.windows = data.windows || "en";
    document.documentElement.lang = L.lang === "tr" ? "tr" : "en";    // CSS text-transform: uppercase needs it for İ
    try { localStorage.setItem("studia-lang", document.documentElement.lang); } catch (e) { /* storage may be unavailable */ }
  };
  window.t = t;
})();
