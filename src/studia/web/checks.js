/* IEUStudia — checked answers (4.2), on top of the Practice tools (practice.js) and the mistake loop (mistakes.js). A
 * worked problem (or a short answer that is one number) gets a "Final answer" box: the app reads the result as it is
 * typed ("3,3 kΩ", "1.2e-3 A", "4+3j") and checks it against the key (checks.py, numcheck.py, no AI). The same result
 * gives full marks at once; another one shows the solution with "Partly (method right)" and "I didn't know it"; a
 * result it can't read leaves the marking to the student, as before, with no error. A key the app could not confirm
 * says so under its solution. The end screen's "Mark my written answers" has the AI mark the written answers in one
 * short request; each answer's row says who marked it (you, the app, the AI and what was missing), and the student
 * can change the mark. Plugs into practice.js (St.checks.parts, endBar) and mistakes.js (a row part) through
 * window.Studia. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, call, esc, I, SPIN, toast, A } = St;
  const $ = (s, el = document) => el.querySelector(s);
  const PR = () => S.practice || {};
  const mdLine = (s) => St.mdToHtml(String(s || "")).replace(/^<p>([\s\S]*)<\/p>$/, "$1");
  const canAI = () => { const ai = S.system.ai || {}; return !!ai.available && ai.can_plans !== false; };
  const WRITTEN = ["short", "problem"];
  const marking = {};             // a try being marked: its attempt id → its run (the end screen follows the events)
  const current = (R) => (R && R.quiz && R.pos >= 0 && R.pos < R.order.length ? R.quiz.result.questions[R.order[R.pos]] : null);
  const ckOf = (R, q) => (R && q && R.ck && R.ck.n === q.n ? R.ck : null);

  // ------------------------------------------------------------------ a question: the "Final answer" box
  function keyLine(q) {
    if (q.unverified) return `<div class="ck-key warn">${I("alert", "xs")}<span>${esc(t("The app could not confirm this answer. Check it against its source."))}</span></div>`;
    if (q.verified) return `<div class="ck-key ok">${I("check", "xs")}<span>${esc(t("The app checked the numbers of this answer."))}</span></div>`;
    return "";
  }
  // practice.js's question screen asks for these (a short answer or a worked problem): the box (before the check),
  // the verdict and the key's line (after it), the buttons after a result that isn't the same, the check's label
  function parts(R, q) {
    if (!WRITTEN.includes(q.type)) return null;
    const ck = ckOf(R, q), of = q.verified && (q.check || {}).of;     // (which result, when it asks for several)
    const hint = of ? `${esc(t("The app checks this result:"))} <span class="md">${mdLine(of)}</span>. ${esc(t("Leave it empty to mark it yourself."))}`
      : esc(t("The app checks it for you. Leave it empty to mark it yourself."));
    const box = q.numeric && !R.checked ? `<div class="ck-final"><label for="qz-final">${I("sigma", "sm")} ${esc(t("Final answer"))}</label>
        <input class="input" id="qz-final" autocomplete="off" spellcheck="false" placeholder="${esc(t("Your result, like 3.3 kΩ or 0.25"))}" value="${esc((ck && ck.final) || "")}">
        <span class="dim sm">${hint}</span></div>` : "";
    let verdict = "";
    if (R.checked && ck && ck.final) {
      const mine = `<div class="ck-mine">${esc(t("Your result: {result}", { result: ck.final }))}</div>`;
      verdict = ck.verdict === "right" ? `<div class="qz-fb ok ck-verdict"><div class="qz-fb-h">${I("check-circle", "sm")}<b>${esc(t("Right! The app checked your result."))}</b></div>${mine}</div>`
        : ck.verdict === "wrong" ? `<div class="qz-fb no ck-verdict"><div class="qz-fb-h">${I("x-circle", "sm")}<b>${esc(ck.unit ? t("Not the same as the answer: check the unit.") : t("Not the same as the answer below."))}</b></div>${mine}</div>`
          : `<div class="ck-unknown">${I("info", "xs")}<div>${esc(t("The app couldn't read your result as one number, so mark it yourself."))}${mine}</div></div>`;
    }
    if (R.checked) verdict += keyLine(q);
    const acts = R.checked && !R.answers[q.n] && ck && ck.verdict === "wrong"
      ? `<span class="dim sm qz-self-q">${t("How did you do?")}</span><button class="btn" data-act="qz-self" data-got="0" autofocus>${t("I didn't know it")}</button><button class="btn" data-act="qz-self" data-got="0.5">${t("Partly (method right)")}</button>` : "";
    return { box, verdict, acts, label: q.numeric ? t("Check my answer") : "" };
  }

  // the check: a typed result goes to the app first; without one, the question is marked by the student as before
  const plainCheck = A["qz-check"], plainSelf = A["qz-self"];
  A["qz-check"] = async (el) => {
    const R = PR().run, q = current(R);
    if (!q || R.checked || !q.numeric || !WRITTEN.includes(q.type)) return plainCheck(el);
    const box = $("#qz-final"), final = box ? box.value.trim() : "";
    if (!final) { R.ck = null; return plainCheck(el); }
    if (R.ck && R.ck.n === q.n && R.ck.busy) return undefined;
    const work = $("#qz-text");
    if (work) R.text = work.value.trim();
    R.ck = { n: q.n, final, busy: true };
    if (el && el.tagName === "BUTTON") { el.disabled = true; el.innerHTML = `${SPIN()} ${esc(t("Checking…"))}`; }
    const r = await call("quiz_check_number", R.id, q.n, final);
    if (PR().run !== R || current(R) !== q || R.checked) return undefined;
    Object.assign(R.ck, { busy: false, verdict: r.ok ? r.verdict : "unknown", unit: !!(r.ok && r.unit) });
    if (R.ck.verdict === "right") R.answers[q.n] = { got: 1, mark: "app", app_got: 1, final, text: R.text };
    return plainCheck(el);
  };
  // the student's own mark after a check: the result kept with it, and who decided ("I didn't know it" after the app
  // found it wrong is the app's mark; "Partly (method right)" is the student's)
  A["qz-self"] = (el) => {
    const R = PR().run, q = current(R), ck = ckOf(R, q);
    if (ck && ck.final && !R.answers[q.n]) {
      R.answers[q.n] = ck.verdict === "wrong" ? { final: ck.final, app_got: 0, mark: +el.dataset.got ? "self" : "app" } : { final: ck.final, mark: "self" };
    }
    return plainSelf(el);
  };
  document.addEventListener("keydown", (e) => {               // Enter in the box checks it
    if (e.key !== "Enter" || !e.target || e.target.id !== "qz-final") return;
    e.preventDefault();
    const b = $("#overlay .modal [data-act='qz-check']");
    if (b && !b.disabled) b.click();
  });

  // ------------------------------------------------------------------ the end screen: "Mark my written answers"
  function toMark(R) {
    if (!R || !R.quiz) return [];
    const off = new Set(R.quiz.result.removed || []);
    return R.order.map((i) => R.quiz.result.questions[i]).filter((q) => {
      const a = R.answers[q.n] || {};
      return WRITTEN.includes(q.type) && !off.has(q.n) && !a.skipped && a.mark !== "app" && a.ai_got == null
        && !!(String(a.text || "").trim() || String(a.final || "").trim());
    });
  }
  function endBar(R) {
    const m = R.marking, ai = St.aiName();
    if (m && m.busy) {
      return `<div class="ck-mark busy">${SPIN()}<div class="grow"><b>${esc(t.n(m.n || 1, "{ai} is marking your written answer…", "{ai} is marking your {n} written answers…", { ai }))}</b>
        <div class="dim sm ck-wait">${m.wait && St.waitLine ? St.waitLine(m.wait) : esc(t("Usually under a minute."))}</div></div><button class="btn ghost sm" data-act="ck-stop">${t("Stop")}</button></div>`;
    }
    const done = m && m.marked ? `<p class="ck-done sm">${I("check", "sm")}<span>${esc(t.n(m.marked, "{ai} marked {n} written answer. Open it to see why, or change the mark.", "{ai} marked {n} written answers. Open one to see why, or change its mark.", { ai: m.by || ai }))}</span></p>` : "";
    const todo = toMark(R);
    if (!todo.length || !R.saved || !R.attemptId) return done;
    return `${done}<div class="ck-mark"><button class="btn" data-act="ck-mark" ${St.aiBtn(canAI(), true)} ${canAI() ? "" : `title="${esc(St.aiHint())}"`}>${I("sparkle", "sm")} ${esc(t("Mark my written answers"))}</button>
      <span class="dim sm">${esc(t.n(todo.length, "{ai} compares your answer with the model answer and marks it right, partly or wrong. One short request, usually under a minute.", "{ai} compares your {n} answers with the model answers and marks each right, partly or wrong. One short request, usually under a minute.", { ai }))}</span></div>`;
  }
  const onEnd = (R) => PR().run === R && !!$("#overlay .qz-end");
  function redraw(R) {                       // the end screen drawn again where it was scrolled to
    if (!onEnd(R)) return;
    const box = $("#overlay .qz-results"), top = box ? box.scrollTop : 0;
    St.practice.showRun(R);
    const nb = $("#overlay .qz-results");
    if (nb) nb.scrollTop = top;
  }
  St.onEvents.quizmark = (e) => {
    const R = marking[e.attempt_id];
    if (e.phase === "start" || e.phase === "progress") {
      if (!R) return;
      R.marking = { ...(R.marking || {}), busy: true, n: e.n || (R.marking || {}).n, wait: e.wait || null };
      const line = $("#overlay .ck-mark.busy .ck-wait");
      if (line && e.wait && St.waitLine) line.innerHTML = St.waitLine(e.wait);
      else redraw(R);
      return;
    }
    delete marking[e.attempt_id];
    if (e.phase === "done") {
      if (R) {
        (e.answers || []).forEach((a) => { if (R.answers[a.n] || R.order.some((i) => R.quiz.result.questions[i].n === a.n)) R.answers[a.n] = { ...R.answers[a.n], ...a }; });
        R.marking = { busy: false, marked: e.marked || 0, by: St.aiName() };
      }
      if (R && onEnd(R)) redraw(R);
      else toast(t.n(e.marked || 0, "{ai} marked {n} written answer.", "{ai} marked {n} written answers.", { ai: St.aiName() }), "success");
      if (St.practice) St.practice.refresh(e.course_id);
    } else if (e.phase === "error") {
      if (R) { R.marking = null; redraw(R); }
      if (!e.cancelled) toast(e.message || t("The AI could not finish"), "error");
    }
  };

  // ------------------------------------------------------------------ an answer's row: who marked it, and a change
  function markPart(ctx, q, a) {
    if (!WRITTEN.includes(q.type) || !a || a.skipped) return "";
    const R = PR().run, attempt = a.attempt || (R && R.id === ctx.plan ? R.attemptId : "") || "";
    const g = a.got || 0;
    const who = a.mark === "ai" ? t("Marked by {ai}", { ai: a.ai_by || St.aiName() }) : a.mark === "app" ? t("Marked by the app") : t("Marked by you");
    const what = g >= 1 ? t("right") : g > 0 ? t("partly") : t("wrong");
    // (the result alone is shown as the answer itself: mistakes.js)
    const lines = [a.final && a.text ? `<div class="ck-mine">${esc(t("Your result: {result}", { result: a.final }))}</div>` : "",
      `<div class="ck-who">${esc(who)}: <b>${esc(what)}</b>${a.mark === "ai" && a.ai_why ? `<span class="ck-why md"> · ${mdLine(a.ai_why)}</span>` : ""}</div>`,
      q.unverified ? keyLine(q) : ""].join("");
    const change = attempt ? `<div class="ck-change"><span class="dim sm">${esc(t("Change the mark:"))}</span>${[[0, t("Wrong")], [0.5, t("Partly")], [1, t("Right")]]
      .map(([v, label]) => `<button class="btn ghost sm ${g === v ? "on" : ""}" data-act="ck-set" data-cid="${esc(ctx.cid)}" data-plan="${esc(ctx.plan)}" data-attempt="${esc(attempt)}" data-n="${q.n}" data-got="${v}" aria-pressed="${g === v}">${esc(label)}</button>`).join("")}</div>` : "";
    return `<div class="ck-row">${lines}${change}</div>`;
  }
  if (St.mistakes) St.mistakes.rowParts.splice(1, 0, markPart);        // (after your answer beside the right one)

  Object.assign(A, {
    "ck-mark": async () => {
      const R = PR().run;
      if (!R || !R.attemptId || (R.marking && R.marking.busy)) return;
      R.marking = { busy: true, n: toMark(R).length, wait: null };
      marking[R.attemptId] = R;
      redraw(R);
      const r = await call("quiz_mark", R.id, R.attemptId, "");
      if (!r.ok) { delete marking[R.attemptId]; R.marking = null; toast(r.error, "error"); redraw(R); }
    },
    "ck-stop": () => call("ai_cancel"),
    "ck-set": async (el) => {
      const { cid, plan, attempt } = el.dataset, n = +el.dataset.n, got = +el.dataset.got;
      const r = await call("quiz_mark_set", plan, attempt, n, got);
      if (!r.ok) { toast(r.error, "error"); return; }
      const R = PR().run, a = ((r.attempt || {}).answers || []).find((x) => x.n === n);
      if (R && R.id === plan && R.attemptId === attempt && a) { R.answers[n] = { ...R.answers[n], ...a }; redraw(R); }
      if (St.practice) St.practice.refresh(cid);
    },
  });

  St.checks = { parts, endBar, keyLine, toMark, markPart, state: marking };
})();
