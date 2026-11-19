/* IEUStudia — the exam kit's way back (kit.py): what ChatGPT or Claude on the web wrote from an Export for AI chat
 * zip (a practice quiz, or flashcards), pasted on a course's Practice tab, becomes a quiz that plays with scores,
 * opens its sources and feeds the mistakes loop, or cards in the course's deck. "Paste from ChatGPT or Claude"
 * (St.kit.button, on the tab's quizzes card) opens the dialog; Ctrl+V on the tab does too, with what was copied, but
 * only when no text field has the focus and no dialog is open (a paste meant for a field stays there). Reading takes
 * a few seconds (the course's material is read to find each source); nothing goes to an AI. The end says what was
 * read: how many questions or cards, how many open their source, how many were found by their words, how many could
 * not be used, with Undo. Plugs in through window.Studia. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, call, esc, I, SPIN, toast, modal, closeModal, A } = St;
  const doc = typeof document !== "undefined" ? document : null;
  const $ = (s) => (doc && doc.querySelector ? doc.querySelector(s) : null);
  let K = null;            // the dialog: {cid, text, busy, error, done (what quiz_import answered)}

  function button(c) {
    return `<button class="btn sm" data-act="kit-paste" data-cid="${esc(c.id)}" title="${esc(t("A quiz or flashcards ChatGPT or Claude wrote for you: IEUStudia plays them here with scores. Ctrl+V on this tab works too."))}">${I("inbox", "sm")} ${t("Paste from ChatGPT or Claude")}</button>`;
  }

  // ------------------------------------------------------------------ the dialog
  function pasteHTML() {
    return `<div class="cp-scroll"><h2>${I("inbox")} ${t("Paste from ChatGPT or Claude")}</h2>
      <p class="cx-lead">${t("Paste a practice quiz or flashcards that ChatGPT or Claude wrote for you. IEUStudia plays them here with scores, opens where each question comes from, and brings back what you miss.")}</p>
      <textarea class="input kit-text" id="kit-text" rows="10" spellcheck="false" placeholder="${esc(t("Paste the whole answer here (Ctrl+V). A code block, JSON or plain text all work."))}" ${K.busy ? "disabled" : "autofocus"}>${esc(K.text)}</textarea>
      ${K.error ? `<div class="callout error kit-err">${I("alert-circle")}<div>${esc(K.error)}</div></div>` : ""}
      <div class="note-sm">${I("info", "sm")}<span>${t("No quiz to paste yet? Export for AI chat makes a zip with ready prompts for ChatGPT and Claude.")} <button class="link" data-act="cx-open" data-id="${esc(K.cid)}">${t("Export for AI chat")}</button></span></div></div>
      <div class="cp-foot"><div class="cp-cost">${K.busy ? `<span class="dim">${SPIN()} ${t("Reading it and finding where each question comes from (a few seconds)…")}</span>` : ""}</div>
        <div class="cp-btns"><button class="btn" data-act="modal-close">${t("Cancel")}</button>
          <button class="btn primary" data-act="kit-add" ${K.busy ? "disabled" : ""}>${K.busy ? `${SPIN()} ${t("Reading…")}` : `${I("check", "sm")} ${t("Add to Practice")}`}</button></div></div>`;
  }
  function doneHTML() {
    const r = K.done, quiz = r.kind !== "cards";
    const lines = [
      r.named ? t.n(r.named, "{n} opens the page or lecture moment it came from.", "{n} open the page or lecture moment they came from.") : "",
      r.found ? t.n(r.found, "{n} named no source the app could open, so it was found by its words.", "{n} named no source the app could open, so they were found by their words.") : "",
      r.no_source ? t.n(r.no_source, "{n} has no source.", "{n} have no source.") : "",
      quiz && r.unused ? t.n(r.unused, "{n} more couldn't be used (no clear right answer, or asked twice).", "{n} more couldn't be used (no clear right answer, or asked twice).") : "",
      !quiz && r.dupes ? t.n(r.dupes, "{n} was in your deck already, or pasted twice.", "{n} were in your deck already, or pasted twice.") : "",
      // the chat stopped in the middle (its answer was too long): only what was whole before that came in
      r.cut ? (quiz ? t("The answer stops part way, so the rest of the quiz is missing. If the chat shows Continue, press it there, then press Undo here and paste the whole answer again.")
        : t("The answer stops part way, so the rest of the cards are missing. If the chat shows Continue, press it there, then paste the whole answer again: the cards you have already are skipped.")) : "",
    ].filter(Boolean);
    const head = quiz ? t.n(r.questions, "{n} question is ready to play.", "{n} questions are ready to play.")
      : t.n(r.cards, "{n} flashcard joined your deck.", "{n} flashcards joined your deck.");
    return `<div class="cp-scroll"><div class="cx-done">${I("check-circle")}<div class="grow"><h2>${quiz ? t("Added to Practice") : t("Added to your flashcards")}</h2>
        ${quiz && r.title ? `<div class="cx-file"><b>${esc(r.title)}</b></div>` : ""}<div class="kit-head">${esc(head)}</div></div></div>
      ${lines.length ? `<ul class="kit-lines">${lines.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}</div>
      <div class="cp-foot"><div class="cp-cost"><button class="btn ghost sm" data-act="kit-undo">${I("x", "sm")} ${t("Undo")}</button></div>
        <div class="cp-btns"><button class="btn" data-act="modal-close">${t("Done")}</button>
          ${quiz ? `<button class="btn primary" data-act="kit-play" autofocus>${I("play", "sm")} ${t("Practise now")}</button>`
            : `<button class="btn primary" data-act="kit-study" autofocus>${I("play", "sm")} ${t("Study now")}</button>`}</div></div>`;
  }
  function draw() {
    if (!K) return;
    modal(K.done ? doneHTML() : pasteHTML());
    const m = $("#overlay .modal");
    if (m) m.classList.add("pd", "kit");
    St.onModalClose = () => { if (K && K.busy) K.closed = true; else K = null; };   // closed while reading: a toast says how it went
  }
  function open(cid, text) {
    K = { cid: cid || (S.view || {}).courseId || "", text: text || "", busy: false, error: "", done: null };
    draw();
    if (K.text.trim()) add();
  }
  async function add() {
    if (!K || K.busy) return;
    const box = $("#kit-text");
    if (box) K.text = box.value;
    if (!K.text.trim()) { K.error = t("Paste the answer first."); draw(); return; }
    K.busy = true; K.error = ""; draw();
    const asked = K;
    const r = await call("quiz_import", asked.cid, asked.text, "");
    asked.busy = false;
    if (asked !== K) return;
    if (r && r.ok && St.practice && St.practice.refresh) St.practice.refresh(K.cid);
    if (K.closed) {
      K = null;
      if (!r || !r.ok) toast((r && r.error) || t("Something went wrong"), "error");
      else toast(r.kind === "cards" ? t.n(r.cards, "{n} flashcard joined your deck.", "{n} flashcards joined your deck.")
        : t.n(r.questions, "{n} question is ready to play.", "{n} questions are ready to play."), "success");
      return;
    }
    if (!r || !r.ok) { K.error = (r && r.error) || t("Something went wrong"); draw(); return; }
    K.done = r; draw();
  }

  // ------------------------------------------------------------------ Ctrl+V on the Practice tab
  const TYPED = ["", "text", "search", "email", "url", "tel", "password", "number", "date", "time"];
  function typing(el) {
    if (!el || (doc && el === doc.body)) return false;
    const tag = String(el.tagName || "").toUpperCase();
    return tag === "TEXTAREA" || tag === "SELECT" || (tag === "INPUT" && TYPED.includes(String(el.type || "").toLowerCase()))
      || !!el.isContentEditable;
  }
  function onPaste(e) {
    const v = S.view || {};
    if (v.name !== "course" || v.tab !== "practice" || !v.courseId) return;
    if (typing(doc && doc.activeElement) || typing(e.target) || $("#overlay .modal")) return;
    const cb = e.clipboardData;
    const text = cb && cb.getData ? cb.getData("text/plain") || cb.getData("text") || "" : "";
    if (!text.trim()) return;
    if (e.preventDefault) e.preventDefault();
    open(v.courseId, text);
  }
  if (doc && doc.addEventListener) doc.addEventListener("paste", onPaste);

  // ------------------------------------------------------------------ actions
  Object.assign(A, {
    "kit-paste": (el) => open((el && el.dataset && el.dataset.cid) || ""),
    "kit-add": () => add(),
    "kit-undo": async () => {
      if (!K || !K.done) return;
      const r = K.done, cid = K.cid;
      const res = await call(r.kind === "cards" ? "cards_set_delete" : "quiz_delete", r.id);
      if (!res || !res.ok) { toast((res && res.error) || t("Something went wrong"), "error"); return; }
      K = null; closeModal();
      toast(r.kind === "cards" ? t("The pasted cards were taken out again.") : t("The pasted quiz was taken out again."), "info");
      if (St.practice && St.practice.refresh) St.practice.refresh(cid);
    },
    "kit-play": () => { const id = K && K.done ? K.done.id : ""; K = null; closeModal(); if (id && A["qz-play"]) A["qz-play"]({ dataset: { id } }); },
    // (the cards just pasted, all of them: not the course's day of cards, which the new-card limit may have used up)
    "kit-study": () => { const cid = K ? K.cid : "", set = K && K.done ? K.done.id : ""; K = null; closeModal(); if (cid && St.practice && St.practice.study) St.practice.study(cid, set ? { set } : {}); },
  });
  St.kit = { button, open, onPaste };

  // ------------------------------------------------------------------ browser preview (mock)
  if (St.mocks) {
    St.mocks.quiz_import = (cid, text) => (/card|front|ön yüz/i.test(text || "")
      ? { ok: true, kind: "cards", id: "mockc", title: "Flashcards pasted from ChatGPT or Claude", cards: 24, read: 26, dupes: 2, named: 21, found: 2, no_source: 1 }
      : { ok: true, kind: "quiz", id: "mockq", title: "Bayes' theorem and total probability", questions: 9, read: 10, unused: 1, named: 7, found: 1, no_source: 1 });
  }
})();
