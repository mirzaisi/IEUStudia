/* IEUStudia — closing the window while lectures are transcribed or the AI writes something you asked for: one calm
 * question that says what stops and how it goes on next time, with "Don't ask again" (Settings → Transcription turns
 * it back on). app.py asks for it with a "close-ask" event (what is running: transcribing, and what the AI makes) and
 * closes the window once you choose Close. */
(function () {
  "use strict";
  const St = window.Studia;
  const { A, I, esc, call, modal, closeModal } = St;
  const PLANS = ["pack", "sheet", "prep", "videos", "quiz", "cards"];     // plans: Resume on their page

  // what the AI is writing, in a sentence of its own (one kind; several: how many)
  function aiLine(kinds) {
    const ai = St.aiName ? St.aiName() : "AI", only = [...new Set(kinds)];
    if (only.length > 1) return t.n(only.length, "{ai} is still working on {n} thing you asked for.", "{ai} is still working on {n} things you asked for.", { ai: esc(ai) });
    return ({ chat: t.html("{ai} is still writing an answer in a chat.", { ai }), notes: t.html("{ai} is still writing study notes.", { ai }),
      digest: t.html("{ai} is still writing the weekly digest's summary.", { ai }), pack: t.html("{ai} is still writing a study pack.", { ai }),
      sheet: t.html("{ai} is still making a cheatsheet.", { ai }), prep: t.html("{ai} is still writing a review sheet.", { ai }),
      videos: t.html("{ai} is still making a study plan.", { ai }), quiz: t.html("{ai} is still writing a practice quiz.", { ai }),
      cards: t.html("{ai} is still making flashcards.", { ai }) })[only[0]] || t.html("{ai} is still working on something you asked for.", { ai });
  }
  St.onEvents["close-ask"] = (e) => {
    call("close_seen");                  // shown: a second close doesn't fall back to Windows' own question
    const ai = (e && e.ai) || [], tx = !e || e.transcribing !== false;
    const lines = [];
    if (tx) lines.push(t("Lectures are still being transcribed. If you close IEUStudia now, the work stops here."),
      t("Nothing that's done is lost: next time, press Continue on the home page and it goes on where it stopped."));
    if (ai.length) {
      lines.push(aiLine(ai), tx ? "" : t("If you close IEUStudia now, it stops."),
        ai.some((k) => PLANS.includes(k)) ? t("What's finished is kept: press Resume on its page next time.") : t("You can ask again next time."));
    }
    modal(`<div id="close-ask"><h2>${I("pause")} ${t("Close IEUStudia?")}</h2>
      <p class="lead">${lines.filter(Boolean).join(" ")}</p>
      <label class="mini-toggle" style="display:flex;margin:10px 0 0"><input type="checkbox" class="switch sm" id="close-noask">${t("Don't ask again")}</label>
      <div class="actions"><button class="btn" data-act="modal-close" autofocus>${t("Keep open")}</button>
        <button class="btn primary" data-act="close-now">${t("Close")}</button></div></div>`);
  };

  Object.assign(A, {
    "close-now": () => {
      const box = document.getElementById("close-noask");
      closeModal();
      call("close_now", !!(box && box.checked));
    },
  });

  // the browser preview: what closing would do (?close in the address shows the question; ?close=ai while a study
  // pack is written)
  St.mocks.close_seen = () => ({ ok: true });
  St.mocks.close_now = () => ({ ok: true });
  if (/[?&]close\b/.test(location.search)) {
    const ai = /[?&]close=ai\b/.test(location.search);
    setTimeout(() => window.__lsEvent({ type: "close-ask", transcribing: !ai, ai: ai ? ["pack"] : [] }), 600);
  }
})();
