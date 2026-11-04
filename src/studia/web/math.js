/* IEUStudia — formulas in AI answers, notes, study plans and course material, drawn by KaTeX (vendor/katex, MIT, bundled:
 * nothing is loaded from the internet). Written as $…$ or \(…\) in a line, $$…$$ or \[…\] (also with the dollars on
 * lines of their own) or a bare \begin{align}…\end{align} on their own. Money such as "$5 and $10" stays text: inside
 * single dollars there is no space next to them and no digit right after the closing one. A command KaTeX doesn't know
 * is shown as written (\foo) and the rest is still drawn; a formula it can't read at all is shown as its source.
 *
 * StudiaMath.renderMath(el) (also Studia.renderMath in the app) draws the formulas in an element's text, not inside
 * code, pre, inputs or formulas already drawn. A page of its own, such as a document to print, loads
 * vendor/katex/katex.min.css, vendor/katex/katex.min.js (and contrib/mhchem.min.js for \ce) and this file, then calls
 * StudiaMath.renderMath(document.body). */
(function (root) {
  "use strict";
  // common commands of LaTeX packages KaTeX doesn't have (amssymb's \C, siunitx's units, \eqref): close enough
  const MACROS = {
    "\\C": "\\mathbb{C}", "\\Q": "\\mathbb{Q}", "\\ohm": "\\Omega", "\\micro": "\\mu", "\\celsius": "{}^{\\circ}\\mathrm{C}",
    "\\textdegree": "{}^{\\circ}", "\\SI": "#1\\,\\mathrm{#2}", "\\qty": "#1\\,\\mathrm{#2}", "\\si": "\\mathrm{#1}", "\\unit": "\\mathrm{#1}",
    "\\num": "#1", "\\ang": "#1^{\\circ}", "\\eqref": "(\\text{#1})", "\\label": "", "\\per": "/", "\\squared": "^2", "\\cubed": "^3",
    "\\kilo": "k", "\\mega": "M", "\\giga": "G", "\\milli": "m", "\\nano": "n", "\\pico": "p", "\\volt": "V", "\\ampere": "A",
    "\\watt": "W", "\\farad": "F", "\\henry": "H", "\\hertz": "Hz", "\\second": "s", "\\joule": "J", "\\coulomb": "C",
    "\\metre": "m", "\\meter": "m", "\\newton": "N", "\\kilogram": "kg", "\\gram": "g", "\\percent": "\\%",
  };
  // environments that are a displayed formula on their own, without $$ around them
  const ENV = /^\\begin\{(equation\*?|align\*?|aligned|alignat\*?|alignedat|gather\*?|gathered|split|cases|dcases|rcases|[pbBvV]?matrix|smallmatrix|array|CD)\}/;
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const source = (src) => `<span class="math-src">${esc(src)}</span>`;

  const cache = new Map();          // answers are drawn again with every streamed word: the same formula costs nothing
  function draw(src, display) {
    const K = root.katex;
    if (!K) return source(src);
    const macros = Object.assign({}, MACROS);
    for (let n = 0; n < 12; n++) {
      try {
        return K.renderToString(src, { displayMode: !!display, throwOnError: true, strict: "ignore", trust: false,
          maxExpand: 500, maxSize: 30, macros, output: "htmlAndMathml" });
      } catch (e) {
        const m = /Undefined control sequence: (\\[A-Za-z@]+)/.exec((e && e.message) || "");
        if (!m || macros[m[1]] != null) break;
        macros[m[1]] = `\\text{\\textbackslash{}${m[1].slice(1)}}`;      // unknown: shown as written, the rest drawn
      }
    }
    return source(src);
  }
  /** The HTML of one formula (KaTeX's, or its source when it can't be read). */
  function tex(src, display) {
    // backslashes that went through JSON once too often or not at all: \\frac, a form feed + "rac", a tab + "imes" or
    // a lone "o" (\to), a new line + "eq" or "abla" (as ai.py's parse_json; a new line + "e" stays a line of a display)
    src = String(src).replace(/(?<!\\)\\\\(?=[A-Za-z]{2})/g, "\\").replace(/\f/g, "\\f").replace(/\x08/g, "\\b")
      .replace(/\t(?=[a-z]{2}|o(?![A-Za-z]))/g, "\\t")
      .replace(/\n(?=(?:eq|eg|otin|abla|mid|leq|geq|exists|ewline|olimits|onumber|parallel|subseteq|supseteq)(?![A-Za-z]))/g, "\\n").replace(/\r(?=[a-z])/g, "\\r").trim();
    const key = (display ? "D" : "I") + src;
    let html = cache.get(key);
    if (html == null) {
      html = draw(src, display);
      if (cache.size >= 800) cache.delete(cache.keys().next().value);
      cache.set(key, html);
    }
    return html;
  }
  /** One formula as it sits in text: inline, or displayed on a line of its own. */
  const html = (part) => part.display ? `<span class="math-display">${tex(part.tex, true)}</span>` : `<span class="math">${tex(part.tex, false)}</span>`;

  // A "$" opens a formula when no letter or digit is right before it and no space right after it; the first "$" after
  // that closes it, if no space is right before it, no digit right after it and no empty line is between (pandoc's rule).
  function inlineEnd(s, i) {
    if (/[\p{L}\p{N}]/u.test(s[i - 1] || "") || !s[i + 1] || /\s/.test(s[i + 1])) return -1;
    let j = i + 1;
    while ((j = s.indexOf("$", j)) >= 0 && s[j - 1] === "\\") j++;
    if (j < 0 || /\s/.test(s[j - 1]) || /[0-9$]/.test(s[j + 1] || "") || s.slice(i, j).includes("\n\n")) return -1;
    return j;
  }
  /** Text and the formulas in it: ["text", {tex, display, raw}, "text", …]. "\$" is a dollar sign. */
  function split(s) {
    s = String(s == null ? "" : s);
    const out = [];
    let text = "", i = 0;
    const put = (body, display, end) => {
      const raw = s.slice(i, end);
      if (!body.trim()) text += raw;
      else { if (text) out.push(text); text = ""; out.push({ tex: body, display, raw }); }
      i = end;
    };
    while (i < s.length) {
      const c = s[i], d = s[i + 1];
      if (c === "\\") {
        if (d === "\\") { text += "\\\\"; i += 2; continue; }
        if (d === "$") { text += "$"; i += 2; continue; }
        if (d === "(" || d === "[") {
          const j = s.indexOf(d === "(" ? "\\)" : "\\]", i + 2);
          if (j > 0) { put(s.slice(i + 2, j), d === "[", j + 2); continue; }
        }
        const m = d === "b" && ENV.exec(s.slice(i, i + 40));
        if (m) {
          const end = `\\end{${m[1]}}`, j = s.indexOf(end, i);
          if (j > 0) { put(s.slice(i, j + end.length), true, j + end.length); continue; }
        }
      } else if (c === "$" && d === "$") {
        const j = s.indexOf("$$", i + 2);
        if (j > 0) { put(s.slice(i + 2, j), true, j + 2); continue; }
        text += "$$"; i += 2; continue;
      } else if (c === "$") {
        const j = inlineEnd(s, i);
        if (j > 0) { put(s.slice(i + 1, j), false, j + 1); continue; }
      }
      text += c; i++;
    }
    if (text) out.push(text);
    return out;
  }
  /** Plain text with its formulas drawn: the rest is escaped (a question, a checklist line…). */
  const text = (s) => split(s).map((p) => typeof p === "string" ? esc(p) : html(p)).join("");

  /** While an answer streams in: its last line without a formula that hasn't been closed yet (shown once it is). */
  function settled(s) {
    s = String(s || "");
    const nl = s.lastIndexOf("\n") + 1, parts = split(s.slice(nl));
    const last = parts[parts.length - 1];
    if (typeof last !== "string") return s;
    let cut = -1;
    for (let k = 0; k < last.length && cut < 0; k++) {
      if (last[k] === "\\" && (last[k + 1] === "(" || last[k + 1] === "[")) cut = k;
      else if (last[k] === "\\") k++;
      else if (last[k] === "$" && (last[k + 1] === "$" || (last[k + 1] && !/\s/.test(last[k + 1]) && !/[\p{L}\p{N}]/u.test(last[k - 1] || "")))) cut = k;
    }
    return cut < 0 ? s : s.slice(0, s.length - last.length + cut);
  }

  const SKIP = new Set(["SCRIPT", "STYLE", "TEXTAREA", "INPUT", "SELECT", "OPTION", "PRE", "CODE", "KBD", "SAMP", "svg", "SVG", "MATH"]);
  /** Draw the formulas in an element's text (each formula within one text node). Returns how many were drawn. */
  function renderMath(el) {
    if (!el || !root.document) return 0;
    const doc = el.ownerDocument || root.document, nodes = [];
    const walker = doc.createTreeWalker(el, 4 /* text */, {
      acceptNode(n) {
        if (!/[$\\]/.test(n.nodeValue)) return 2;                        // reject: nothing to look at
        for (let p = n.parentNode; p && p !== el.parentNode; p = p.parentNode) {
          if (p.nodeType !== 1) continue;
          if (SKIP.has(p.nodeName) || p.isContentEditable || (p.classList && (p.classList.contains("katex") || p.classList.contains("math-src") || p.classList.contains("no-math")))) return 2;
        }
        return 1;
      },
    });
    for (let n; (n = walker.nextNode());) nodes.push(n);
    let count = 0;
    for (const n of nodes) {
      const parts = split(n.nodeValue);
      if (!parts.some((p) => typeof p !== "string")) continue;
      const box = doc.createElement("span");            // one element where the text was (also as one item of a flex row)
      box.className = "math-text";
      box.innerHTML = parts.map((p) => typeof p === "string" ? esc(p) : html(p)).join("");
      count += parts.length - parts.filter((p) => typeof p === "string").length;
      n.replaceWith(box);
    }
    return count;
  }

  const api = { renderMath, tex, html, text, split, settled };
  root.StudiaMath = api;
  if (root.Studia) root.Studia.renderMath = renderMath;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
