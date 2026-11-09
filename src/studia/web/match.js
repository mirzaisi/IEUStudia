/* IEUStudia: finding words on the page (in a transcript, among a course's files, chats and study plans) the way the
 * search finds them (search.py): whole words in their English and Turkish forms, numbers however they are written,
 * words of the same meaning in both languages, typos, "words in quotes", and the last word as a prefix while it is
 * typed. The rules come from Python once (Match.load(call("search_rules"))); before that a word is matched whole.
 * With them come the library's own words ("known"): a whole word typed is never taken for the start of a longer one
 * because the short list searched here (one lecture, one course's files) doesn't hold it ("exam" never finds "examples").
 *   const q = Match.query("quiz one", texts)     the texts searched (their words are what the forms are found among)
 *   q.match(text)  ->  { every, some, marks: [[start, end], ...] }   (places in Match.prep(text))
 *   Match.html(text, marks)                      the text, escaped, with the marked words in <mark>
 * tests/test_match_page.py checks that this file and search.py find the same. */
(function () {
  "use strict";
  let R = { slots: [], abbr: {}, ordTail: [], apos: ["'", "’", "ʼ", "`", "´"], irregular: {}, esAfter: [],
    nameEndings: [], soft: {}, shortRootEndings: [], minRoot: 3, numbers: {}, joins: [], nearNumber: 2, synonyms: [],
    stop: [], few: 5, known: [], q: { synonym: 0.88, form: 0.92, prefix: 0.7, typo1: 0.55, typo2: 0.45, name: 0.8 } };
  let D = null;                     // what the rules give, ready to use (made by derive)
  const memo = { fold: new Map(), chain: new Map(), roots: new Map(), toks: new Map() };
  const VOWELS = new Set(["a", "e", "i", "o", "u"]);
  const QUOTES = "\"“”„«»";
  const WORD = /[\p{L}\p{Nl}\p{No}]+|\p{Nd}+/gu;
  const DIGITS = /^\p{Nd}+$/u, ASCII_DIGITS = /^[0-9]+$/;
  const GAP = /^[\s.#:(-]{0,3}$/, GAP1 = /^[#-]?$/;
  const isUp = (c) => /\p{Lu}/u.test(c), isLow = (c) => /\p{Ll}/u.test(c);
  const hasVowel = (s) => [...s].some((c) => VOWELS.has(c));

  function derive() {
    const plurals = new Map();
    for (const [p, s] of Object.entries(R.irregular)) plurals.set(s, (plurals.get(s) || []).concat(p));
    const synFirst = new Map();
    R.synonyms.forEach((g, gi) => g.forEach((m) => synFirst.set(m[0], (synFirst.get(m[0]) || []).concat([[gi, m]]))));
    const hard = {};
    for (const [k, v] of Object.entries(R.soft)) hard[v] = k;
    const forms = new Map();                                           // a number: its spellings
    for (const [w, [n, q]] of Object.entries(R.numbers)) forms.set(n, Object.assign(forms.get(n) || {}, { [w]: q }));
    D = { plurals, synFirst, hard, forms, ordTail: new Set(R.ordTail), ordGap: new Set(["", ...R.apos]),
      joins: new Set(R.joins), stop: new Set(R.stop), known: new Set(R.known || []) };
  }
  derive();

  // ---------------------------------------------------------------- words
  function fold(word) {
    let f = memo.fold.get(word);
    if (f !== undefined) return f;
    f = word.replace(/[İIı]/g, "i").toLowerCase();
    if (/[^\x00-\x7f]/.test(f)) f = f.normalize("NFKD").replace(/\p{M}/gu, "");
    if (/[^\p{L}\p{N}]/u.test(f)) f = f.replace(/[^\p{L}\p{N}]/gu, "");
    if (memo.fold.size > 60000) memo.fold.clear();
    memo.fold.set(word, f);
    return f;
  }
  const prep = (text) => String(text || "").normalize("NFC");

  function camel(raw, start) {       // "ProbabilityWeek" (PDF text glued it): its parts; "LaTeX", "iPhone" stay
    const out = [];
    let prev = 0;
    for (let i = 2; i < raw.length - 1; i++) {
      if (isUp(raw[i]) && isLow(raw[i - 1]) && isLow(raw[i - 2]) && isLow(raw[i + 1])) { out.push([raw.slice(prev, i), start + prev]); prev = i; }
    }
    out.push([raw.slice(prev), start + prev]);
    return out;
  }

  // the words of a prepped text: [folded word, start, end]
  function tokens(text) {
    const out = [];
    WORD.lastIndex = 0;
    let m;
    while ((m = WORD.exec(text))) {
      const raw = m[0], s = m.index;
      if (/^\p{Nd}/u.test(raw)) { out.push([ASCII_DIGITS.test(raw) ? (raw.replace(/^0+/, "") || "0") : raw, s, s + raw.length]); continue; }
      for (const [piece, ps] of camel(raw, s)) {
        const f = fold(piece);
        const last = out[out.length - 1];
        if (!f || (D.ordTail.has(f) && last && DIGITS.test(last[0]) && D.ordGap.has(text.slice(last[2], ps)))) continue;
        out.push([f, ps, ps + piece.length]);
      }
    }
    for (let i = 0; i < out.length - 1; i++) {
      const [w, s, e] = out[i];
      if (R.abbr[w] && DIGITS.test(out[i + 1][0]) && (w.length === 1 ? GAP1 : GAP).test(text.slice(e, out[i + 1][1]))) out[i] = [R.abbr[w], s, e];
    }
    return out;
  }
  function tokensOf(text) {          // (a transcript's paragraphs are looked at on every key press)
    let got = memo.toks.get(text);
    if (!got) {
      if (memo.toks.size > 4000) memo.toks.clear();
      got = tokens(prep(text));
      memo.toks.set(text, got);
    }
    return got;
  }
  const words = (text) => tokens(prep(text)).map((x) => x[0]);

  // ---------------------------------------------------------------- forms: English plurals, Turkish endings
  function state(word) {
    let last = "";
    for (let i = word.length - 1; i >= 0; i--) if (VOWELS.has(word[i])) { last = word[i]; break; }
    return [last === "a" ? true : last === "e" ? false : null, last === "o" || last === "u", word.length > 0 && VOWELS.has(word[word.length - 1])];
  }
  function fits(need, vowelEnd, third, ki) {
    if (need === "ki") return ki;
    if (need === "3") return third;
    if (need.endsWith("-3") && third) return false;
    if (need.startsWith("V") && !vowelEnd) return false;
    if (need.startsWith("C") && vowelEnd) return false;
    return true;
  }
  function expand(tpl, rest, back, rnd) {
    let ways = [[0, back, rnd]];
    for (const ch of tpl) {
      const nxt = [];
      for (const [i, b, r] of ways) {
        if (i >= rest.length) continue;
        const c = rest[i];
        if (ch === "N") { if (c === (r ? "u" : "i")) nxt.push([i + 1, b, r]); }
        else if (ch === "W") { if ((c === "a" || c === "e") && (b === null || b === (c === "a"))) nxt.push([i + 1, c === "a", false]); }
        else if (ch === "D") { if (c === "d" || c === "t") nxt.push([i + 1, b, r]); }
        else if (c === ch) nxt.push(VOWELS.has(c) ? [i + 1, false, false] : [i + 1, b, r]);
      }
      ways = nxt;
      if (!ways.length) break;
    }
    return ways;
  }
  function chain(back, rnd, vowelEnd, rest, slot = 0, third = false, ki = false) {
    if (!rest) return true;
    const key = `${back}|${rnd}|${vowelEnd}|${rest}|${slot}|${third}|${ki}`;
    const got = memo.chain.get(key);
    if (got !== undefined) return got;
    let ok = false;
    search: for (let s = slot; s < R.slots.length; s++) {
      for (const [tpl, need, sets] of R.slots[s]) {
        if (!fits(need, vowelEnd, third, ki)) continue;
        for (const [used, b, r] of expand(tpl, rest, back, rnd)) {
          if (chain(b, r, VOWELS.has(rest[used - 1]), rest.slice(used), s + 1, sets === "3", sets === "ki")) { ok = true; break search; }
        }
      }
    }
    memo.chain.set(key, ok);
    return ok;
  }
  function endingOk(root, rest) {
    if (!rest || rest === "s") return true;
    if (rest === "es") return R.esAfter.some((e) => root.endsWith(e));
    if (root.length <= R.minRoot && !R.shortRootEndings.some((e) => rest.startsWith(e))) return false;
    const [b, r, v] = state(root);
    return chain(b, r, v, rest);
  }
  function roots(word) {
    let out = memo.roots.get(word);
    if (out) return out;
    out = new Set([word]);
    if (word.length > R.minRoot && !DIGITS.test(word)) {
      if (word.endsWith("ies") && word.length > 4) out.add(word.slice(0, -3) + "y");
      if (word.endsWith("es") && R.esAfter.some((e) => word.slice(0, -2).endsWith(e))) out.add(word.slice(0, -2));
      if (word.endsWith("s") && !word.endsWith("ss")) out.add(word.slice(0, -1));
      if (R.irregular[word]) out.add(R.irregular[word]);
      for (let k = R.minRoot; k < word.length; k++) {
        const head = word.slice(0, k), rest = word.slice(k);
        if (rest.length === 1 && VOWELS.has(rest) && head.length <= R.minRoot) continue;
        if (rest !== "s" && rest !== "es" && hasVowel(head) && endingOk(head, rest)) out.add(head);
      }
    }
    for (const r of [...out]) if (r.length > R.minRoot && D.hard[r[r.length - 1]]) out.add(r.slice(0, -1) + D.hard[r[r.length - 1]]);
    memo.roots.set(word, out);
    return out;
  }

  // ---------------------------------------------------------------- the words searched: forms, prefixes, typos
  function distance(a, b, limit) {
    if (Math.abs(a.length - b.length) > limit) return limit + 1;
    let prev2 = [], prev = Array.from({ length: b.length + 1 }, (_, j) => j);
    for (let i = 1; i <= a.length; i++) {
      const cur = [i];
      let best = i;
      for (let j = 1; j <= b.length; j++) {
        let v = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] !== b[j - 1] ? 1 : 0));
        if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) v = Math.min(v, prev2[j - 2] + 1);
        cur[j] = v;
        best = Math.min(best, v);
      }
      if (best > limit) return limit + 1;
      prev2 = prev; prev = cur;
    }
    return Math.min(prev[b.length], limit + 1);
  }
  class Vocab {
    constructor(df, rows) {
      this.df = df; this.rows = Math.max(1, rows);
      this.terms = [...df.keys()].sort();
      this.byFirst = new Map();
      for (const w of this.terms) if (w.length >= 4 && !DIGITS.test(w)) this.byFirst.set(w[0], (this.byFirst.get(w[0]) || []).concat(w));
      this.fam = new Map();
    }
    prefixed(p) {
      let lo = 0, hi = this.terms.length;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (this.terms[mid] < p) lo = mid + 1; else hi = mid; }
      const out = [];
      for (let i = lo; i < this.terms.length && this.terms[i].startsWith(p); i++) out.push(this.terms[i]);
      return out;
    }
    family(word) {
      let out = this.fam.get(word);
      if (out) return out;
      out = new Map();
      const add = (v, q) => { if (q > (out.get(v) || 0)) out.set(v, q); };
      if (this.df.has(word)) out.set(word, 1);
      if (!DIGITS.test(word) && word.length >= 2) {
        for (const r of roots(word)) {
          const turkish = hasVowel(r);
          for (const v of this.prefixed(r)) {
            const rest = v.slice(r.length);
            if (v !== word && (turkish ? endingOk(r, rest) : rest === "s" || rest === "es")) add(v, R.q.form);
          }
          const last = r[r.length - 1];
          if (r.length > R.minRoot && R.soft[last]) {
            const soft = r.slice(0, -1) + R.soft[last];
            for (const v of this.prefixed(soft)) { const rest = v.slice(soft.length); if (rest && VOWELS.has(rest[0]) && endingOk(soft, rest)) add(v, R.q.form); }
          }
          if (r.endsWith("y") && this.df.has(r.slice(0, -1) + "ies")) add(r.slice(0, -1) + "ies", R.q.form);
          for (const p of D.plurals.get(r) || []) if (this.df.has(p)) add(p, R.q.form);
          if (r.length >= 4) for (const stem of new Set([r, r.endsWith("e") ? r.slice(0, -1) : r])) for (const end of R.nameEndings) if (this.df.has(stem + end)) add(stem + end, R.q.name);
        }
      }
      this.fam.set(word, out);
      return out;
    }
    near(word) {
      const limit = word.length <= 7 ? 1 : 2;
      const pool = new Set(this.byFirst.get(word[0]) || []);
      if (word.length > 1) for (const w of this.byFirst.get(word[1]) || []) if (w.slice(1, 2) === word[0]) pool.add(w);
      const found = [];
      for (const w of pool) {
        if (w !== word && Math.abs(w.length - word.length) <= limit && (this.df.get(w) || 0) >= 2) {
          const d = distance(word, w, limit);
          if (d <= limit) found.push([d, -this.df.get(w), w]);
        }
      }
      found.sort((a, b) => a[0] - b[0] || a[1] - b[1] || (a[2] < b[2] ? -1 : a[2] > b[2] ? 1 : 0));
      return found.map((x) => [x[0], x[2]]);
    }
  }

  // ---------------------------------------------------------------- the query: concepts
  const alt = (seq, q = 1) => ({ seq, q });
  const concept = (typed, alts, o = {}) => ({ typed, alts, number: !!o.number, stop: !!o.stop, open: !!o.open, phrase: !!o.phrase, head: -1, weight: 1, fixed: "" });
  const mapOf = (obj) => new Map(Object.entries(obj));
  const formsOf = (v, w) => { const f = v.family(w); return f.size ? f : new Map([[w, 1]]); };
  function numberOf(w) {
    if (R.numbers[w]) return R.numbers[w][0];
    return ASCII_DIGITS.test(w) && w.length < 7 ? parseInt(w, 10) : null;
  }
  function numberForms(n, typed) { const out = new Map([[String(n), 1], ...Object.entries(D.forms.get(n) || {})]); out.set(typed, 1); return out; }
  function prefixForms(v, w) {
    const forms = new Map(v.prefixed(w).map((x, i) => [x, i]).sort((a, b) => (v.df.get(b[0]) - v.df.get(a[0])) || a[1] - b[1]).slice(0, 40).map((x) => [x[0], R.q.prefix]));
    for (const [k, q] of v.family(w)) forms.set(k, q);
    return forms;
  }
  function synonymAt(ws, i) {
    let best = null;
    const cands = [...(D.synFirst.get(ws[i]) || [])];
    for (const r of [...roots(ws[i])].sort()) if (r !== ws[i]) cands.push(...(D.synFirst.get(r) || []));
    for (const [gi, member] of cands) {
      const n = member.length;
      if (i + n <= ws.length && member.every((m, k) => m === ws[i + k] || roots(ws[i + k]).has(m)) && (!best || n > best[2])) best = [gi, member, n];
    }
    return best;
  }
  function typo(c, v, word) {
    const near = v.near(word);
    let own = 0;
    for (const a of c.alts) for (const w of a.seq[0].keys()) own += v.df.get(w) || 0;
    if (!near.length || (v.df.get(near[0][1]) || 0) <= 3 * own) return;
    c.fixed = near[0][1];
    for (const [d, w] of near.filter((x) => x[0] === near[0][0]).slice(0, 3)) c.alts.push(alt([formsOf(v, w)], d === 1 ? R.q.typo1 : R.q.typo2));
  }
  function pieces(text) {
    const out = [];
    let buf = "", i = 0;
    while (i < text.length) {
      if (QUOTES.includes(text[i])) {
        let j = -1;
        for (let k = i + 1; k < text.length; k++) if (QUOTES.includes(text[k])) { j = k; break; }
        out.push([false, buf, false]); buf = "";
        if (j < 0) { out.push([true, text.slice(i + 1), true]); return out; }
        out.push([true, text.slice(i + 1, j), false]);
        i = j + 1;
        continue;
      }
      buf += text[i]; i++;
    }
    out.push([false, buf, false]);
    return out;
  }
  const sameKeys = (a, b) => a.length === b.length && a.every((x, k) => x === b[k]);
  function parse(query, v, typing = true) {
    const text = prep(query), ps = pieces(text);
    const trailing = typing && /[\p{L}\p{N}]$/u.test(text);
    let lastPiece = -1;
    ps.forEach((p, k) => { if (tokens(p[1]).length) lastPiece = k; });
    const out = [];
    const sum = (m) => { let n = 0; for (const w of m.keys()) n += v.df.get(w) || 0; return n; };
    const unfinished = (w) => {      // still being typed; or a word of its own, but rare next to the words it starts
      if (w.length <= 2) return true;
      if (D.known.has(w)) return false;          // a word of the library's own: whole here too, where the list lacks it
      if (!v.df.has(w)) return true;
      const fam = v.family(w), own = sum(fam);
      return own < R.few && own * 50 < v.prefixed(w).filter((x) => !fam.has(x)).reduce((n, x) => n + v.df.get(x), 0);
    };
    ps.forEach(([quoted, piece, stillOpen], k) => {
      const ws = tokens(piece).map((x) => x[0]);
      if (!ws.length) return;
      const openLast = trailing && k === lastPiece;
      if (quoted) {
        const seq = ws.map((w, j) => {
          const n = numberOf(w);
          if (n !== null) return numberForms(n, w);
          if (j === ws.length - 1 && stillOpen && openLast && !v.df.has(w) && !D.known.has(w)) { const f = prefixForms(v, w); return f.size ? f : new Map([[w, 1]]); }
          return formsOf(v, w);
        });
        out.push(concept(piece.trim(), [alt(seq)], { phrase: true }));
        return;
      }
      const first = out.length;
      let i = 0;
      while (i < ws.length) {
        const w = ws[i], isOpen = openLast && i === ws.length - 1;
        const syn = synonymAt(ws, i);
        if (syn) {
          const [gi, member, n] = syn, typed = ws.slice(i, i + n);
          // (a member that is a common word, "mean", only as typed: "average" never finds "what does it mean")
          const alts = R.synonyms[gi].filter((m) => sameKeys(m, member) || !m.every((x) => D.stop.has(x)))
            .map((m) => alt(m.map((x) => formsOf(v, x)), sameKeys(m, member) ? 1 : R.q.synonym));
          if (!sameKeys(typed, member)) alts.unshift(alt(typed.map((x) => formsOf(v, x)), 1));
          const grows = openLast && i + n === ws.length && unfinished(typed[typed.length - 1]);
          if (grows) alts.push(alt([...typed.slice(0, -1).map((x) => formsOf(v, x)), prefixForms(v, typed[typed.length - 1])]));
          out.push(concept(typed.join(" "), alts, { open: grows }));
          i += n;
          continue;
        }
        const n = numberOf(w);
        if (n !== null) out.push(concept(w, [alt([numberForms(n, w)])], { number: true }));
        else if (isOpen && unfinished(w)) {
          const f = prefixForms(v, w);
          const c = concept(w, [alt([f.size ? f : new Map([[w, 1]])])], { open: true });
          if (w.length >= 5 && !v.prefixed(w).length && !v.family(w).size) typo(c, v, w);
          out.push(c);
        } else {
          const fam = v.family(w);
          const c = concept(w, [alt([fam.size ? fam : new Map([[w, 1]])])], { stop: D.stop.has(w) });
          if (!c.stop && w.length >= 5 && sum(fam) <= 2) typo(c, v, w);
          if (!fam.size && !c.fixed && w.length >= 3) c.alts.push(alt([prefixForms(v, w)]));
          out.push(c);
        }
        i++;
      }
      for (let j = first; j < out.length; j++) {
        if (!out[j].number) continue;
        for (const h of [j - 1, j + 1, j - 2, j + 2]) if (h >= first && h < out.length && !out[h].number && !out[h].stop) { out[j].head = h; break; }
      }
    });
    const real = out.filter((c) => !c.stop);
    for (const c of out) {
      if (c.stop && real.length) { c.weight = 0.15; continue; }
      let df = 0;                    // as rare as its words are together, its synonyms too (search.py)
      for (const a of c.alts) if (a.q >= R.q.synonym) for (const pos of a.seq) for (const w of pos.keys()) df = Math.max(df, v.df.get(w) || 0);
      c.weight = Math.log(1 + v.rows / (1 + df)) + 0.2;
      if (c.number && c.head >= 0) c.weight *= 0.6;
    }
    return out;
  }
  function fixedQuery(query, concepts) {
    const fixes = new Map(concepts.filter((c) => c.fixed).map((c) => [c.typed, c.fixed]));
    if (!fixes.size) return "";
    const text = prep(query);
    let out = "", pos = 0;
    for (const [w, s, e] of tokens(text)) if (fixes.has(w)) { out += text.slice(pos, s) + fixes.get(w); pos = e; }
    return out + text.slice(pos);
  }

  // ---------------------------------------------------------------- matching a text
  function prepare(concepts) {
    const wanted = new Set();
    for (const c of concepts) {
      c.single = new Map(); c.multi = [];
      for (const a of c.alts) {
        if (a.seq.length === 1) for (const [w, q] of a.seq[0]) { if (q * a.q > (c.single.get(w) || 0)) c.single.set(w, q * a.q); }
        else c.multi.push(a);
        for (const pos of a.seq) for (const w of pos.keys()) wanted.add(w);
      }
    }
    return wanted;
  }
  function isNumberWord(t) { return DIGITS.test(t) || !!R.numbers[t]; }
  function numbered(toks, h, o) {      // the number at o is one of the word at h's ("1. quiz", "quiz 1", "quiz 1 and 2")
    if (h[0] - R.nearNumber <= o[0] && o[0] < h[0]) { const after = h[1] < toks.length ? toks[h[1]] : ""; return !isNumberWord(after); }
    if (h[1] <= o[0] && o[0] < h[1] + R.nearNumber) return true;
    if (o[0] >= h[1]) return toks.slice(h[1], o[0]).every((w) => isNumberWord(w) || D.joins.has(w));
    return false;
  }
  function occurrences(concepts, toks, wanted) {
    const pos = new Map();
    toks.forEach((w, i) => { if (wanted.has(w)) { if (!pos.has(w)) pos.set(w, []); pos.get(w).push(i); } });
    const out = concepts.map((c) => {
      const occ = new Map();
      for (const [w, ps] of pos) { const q = c.single.get(w); if (q) for (const p of ps) if (!occ.has(p) || occ.get(p)[2] < q) occ.set(p, [p, p + 1, q]); }
      for (const a of c.multi) {
        for (const [w, q] of a.seq[0]) {
          for (const p of pos.get(w) || []) {
            let qq = q, end = p + 1;
            for (const pq of a.seq.slice(1)) { if (end >= toks.length || !pq.has(toks[end])) { qq = 0; break; } qq = Math.min(qq, pq.get(toks[end])); end++; }
            if (qq) { qq *= a.q; if (!occ.has(p) || occ.get(p)[2] < qq) occ.set(p, [p, end, qq]); }
          }
        }
      }
      return [...occ.values()].sort((a, b) => b[2] - a[2]);
    });
    concepts.forEach((c, j) => {           // a number counts only next to its word, and then the word only where it has it
      if (!(c.number && c.head >= 0 && out[j].length)) return;
      const heads = out[c.head];
      out[j] = out[j].filter((o) => heads.some((h) => numbered(toks, h, o)));
      if (out[j].length) out[c.head] = heads.filter((h) => out[j].some((o) => numbered(toks, h, o)));
    });
    return out;
  }

  // ---------------------------------------------------------------- what pages use
  function query(q, texts, opts = {}) {
    const df = new Map();
    let rows = 0;
    for (const text of texts || []) {
      rows++;
      for (const w of new Set(tokensOf(text).map((x) => x[0]))) df.set(w, (df.get(w) || 0) + 1);
    }
    const v = new Vocab(df, rows);
    const concepts = parse(q, v, opts.typing !== false);
    const wanted = prepare(concepts);
    const real = concepts.filter((c) => !c.stop);
    const need = real.length ? real : concepts;
    return {
      concepts, empty: !concepts.length, fixed: fixedQuery(q, concepts),
      match(text) {
        const toks = tokensOf(text);
        const occ = occurrences(concepts, toks.map((x) => x[0]), wanted);
        const found = need.filter((c) => occ[concepts.indexOf(c)].length);
        const marks = [];
        for (const lst of occ) for (const [s, e] of lst) if (e - 1 < toks.length) marks.push([toks[s][1], toks[e - 1][2]]);
        marks.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
        const merged = [];
        for (const m of marks) { const last = merged[merged.length - 1]; if (last && m[0] < last[1]) last[1] = Math.max(last[1], m[1]); else merged.push(m.slice()); }
        return { every: !!concepts.length && found.length === need.length, some: found.length > 0, marks: merged };
      },
    };
  }
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESC[c]);
  // the text, escaped, the marked words in <mark> (words found next to each other, "Quiz 1", "Q1", in one); marks are
  // places in its prepped form, the same as in the text as the app keeps it (NFC)
  function html(text, marks) {
    const p = prep(text), joined = [];
    for (const [s, e] of [...(marks || [])].sort((a, b) => a[0] - b[0])) {
      const last = joined[joined.length - 1];
      if (last && s <= last[1] + 2 && /^[\s\-–#.]*$/.test(p.slice(last[1], s))) last[1] = Math.max(last[1], e);
      else if (!last || s >= last[1]) joined.push([s, e]);
    }
    let out = "", pos = 0;
    for (const [s, e] of joined) { out += esc(p.slice(pos, s)) + `<mark>${esc(p.slice(s, e))}</mark>`; pos = e; }
    return out + esc(p.slice(pos));
  }
  function load(rules) {
    if (!rules || !rules.slots) return;
    R = { ...R, ...rules, q: { ...R.q, ...(rules.q || {}) } };
    memo.chain.clear(); memo.roots.clear(); memo.toks.clear();
    derive();
  }
  window.Match = { load, query, html, prep, fold, tokens, words, roots, endingOk, Vocab, parse, distance, ready: () => !!R.slots.length };
})();
