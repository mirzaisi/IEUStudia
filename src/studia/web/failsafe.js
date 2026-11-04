/* IEUStudia — closing the window while lectures are transcribed: one calm question that says the work stops and
 * goes on where it stopped next time, with "Don't ask again" (Settings → Transcription turns it back on). app.py
 * asks for it with a "close-ask" event and closes the window once you choose Close. */
(function () {
  "use strict";
  const St = window.Studia;
  const { A, I, call, modal, closeModal } = St;

  St.onEvents["close-ask"] = () => {
    call("close_seen");                  // shown: a second close doesn't fall back to Windows' own question
    modal(`<div id="close-ask"><h2>${I("pause")} ${t("Close IEUStudia?")}</h2>
      <p class="lead">${t("Lectures are still being transcribed. If you close IEUStudia now, the work stops here.")}
        ${t("Nothing that's done is lost: next time, press Continue on the home page and it goes on where it stopped.")}</p>
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

  // the browser preview: what closing would do (?close in the address shows the question)
  St.mocks.close_seen = () => ({ ok: true });
  St.mocks.close_now = () => ({ ok: true });
  if (/[?&]close\b/.test(location.search)) setTimeout(() => window.__lsEvent({ type: "close-ask" }), 600);
})();
