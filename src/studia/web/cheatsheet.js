/* IEUStudia — cheatsheets: every formula, definition, rule, unit and standard method of the files and lectures the
 * student picks, compact, in columns (cheatsheet.py), shown here and saved as an A4 PDF. A cheatsheet is a plan of kind
 * "sheet": the plan list, its events, Stop and Resume are plans.js's; the dialog that picks the material, the progress
 * card and Save PDF are the study pack's (studypack.js); this file adds the sheet's own page. Plugs in through
 * window.Studia. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, call, esc, I, skeleton, A } = St;
  const isSheet = (p) => ((p && p.scope) || {}).kind === "sheet";
  const STAGES = () => [["gather", t("Course material")], ["topics", t("Topics")], ["entries", t("Entries")], ["check", t("Check")], ["assemble", t("Layout")]];

  // formulas too wide for their column are made smaller (as in the PDF: cheatsheet.py FIT_JS)
  function fit() {
    document.querySelectorAll(".cs-doc .cs-tex, .cs-doc .cs-x").forEach((el) => {
      el.style.fontSize = "";
      const w = el.clientWidth, s = el.scrollWidth;
      if (w && s > w + 1) el.style.fontSize = Math.max(55, Math.floor(100 * w / s)) + "%";
    });
  }
  let fitTimer = null;
  window.addEventListener("resize", () => { clearTimeout(fitTimer); fitTimer = setTimeout(fit, 150); });

  function notesHTML(r) {
    const notes = [];
    const miss = (r.materials_missing || []).length, todo = (r.days_missing || []).length;
    if (miss) notes.push(t.n(miss, "{n} picked file was not on this PC, so the cheatsheet doesn't use it.", "{n} picked files were not on this PC, so the cheatsheet doesn't use them."));
    if (todo) notes.push(t.n(todo, "{n} picked lecture was not transcribed, so the cheatsheet doesn't use it.", "{n} picked lectures were not transcribed, so the cheatsheet doesn't use them."));
    if ((r.uncovered || []).length) notes.push(t.html("The AI left these out even when asked a second time: {items}.", { items: r.uncovered.join(", ") }));
    if (!r.pdf) notes.push(t("The PDF wasn't made yet: Save PDF makes it."));
    return notes.map((n) => `<div class="note-sm">${I("info", "sm")}<span>${n}</span></div>`).join("");
  }
  function sheetView(v) {
    const p = S.planCache[v.planId] || (S.plans || []).find((x) => x.id === v.planId);
    const c = St.findCourse(p.course_id) || {};
    const sc = p.scope || {}, r = p.result || {};
    const busy = ["running", "queued"].includes(p.status), ready = p.status === "done";
    const chips = [St.pack.weekSpan(sc.weeks), (sc.files || []).length ? t.n(sc.files.length, "{n} file", "{n} files") : "", (sc.days || []).length ? t.n(sc.days.length, "{n} lecture", "{n} lectures") : "",
      ready && r.entries ? t.n(r.entries, "{n} entry", "{n} entries") : "", ready && r.pages ? t.n(r.pages, "{n} page", "{n} pages") : ""].filter(Boolean);
    const h = `<div class="view-inner plan-page pk-page cs-page-app">
      <div class="pl-top"><div class="grow"><div class="dim">${esc(c.code || "")} · ${t("cheatsheet")}</div>
        <h1>${esc(p.title || (busy ? t("Making your cheatsheet…") : t("Cheatsheet")))}</h1>
        <div class="pl-chips">${chips.map((x) => `<span class="chip">${esc(x)}</span>`).join("")}</div></div>
        <div class="pl-acts">
          ${ready ? `<button class="btn primary sm" data-act="pk-pdf" data-how="save" data-id="${esc(p.id)}">${I("download", "sm")} ${t("Save PDF…")}</button>
            <button class="btn sm" data-act="pk-pdf" data-how="open" data-id="${esc(p.id)}">${I("file-text", "sm")} ${t("Open PDF")}</button>
            <button class="btn ghost sm" data-act="pk-pdf" data-how="folder" data-id="${esc(p.id)}">${I("folder-open", "sm")} ${t("Show in folder")}</button>` : ""}
          ${busy ? `<button class="btn sm" data-act="plan-stop" data-id="${esc(p.id)}">${I("stop", "sm")} ${t("Stop")}</button>` : ""}
          ${["error", "paused"].includes(p.status) ? `<button class="btn primary sm" data-act="plan-resume" data-id="${esc(p.id)}">${I("retry", "sm")} ${t("Resume")}</button>` : ""}
          <button class="btn ghost icon sm" data-act="plan-delete" data-id="${esc(p.id)}" title="${t("Delete this cheatsheet")}">${I("x", "sm")}</button></div></div>`;
    if (!ready) return h + St.pack.stateHTML(p, STAGES(), t("Runs in the background, so you can keep using the app. If it stops (for example at your plan's limit), resume it later: what is written is kept.")) + `</div>`;
    const doc = S.packDoc[p.id];
    const topics = (r.topics || []).map((x, i) => `<button type="button" data-act="pk-goto" data-to="${esc(x.id)}"><span class="pk-n">${i + 1}</span><span>${esc(x.title)}</span></button>`).join("");
    return h + notesHTML(r) + (topics ? `<div class="cs-topics">${topics}</div>` : "")
      + `<div class="card cs-paper"><article class="cs-doc" id="cs-doc" lang="en">${doc != null ? doc : skeleton()}</article></div></div>`;
  }

  // ------------------------------------------------------------------ wiring (after plans.js and studypack.js)
  const planView = St.views.plan;
  St.views.plan = (v) => {
    const p = S.planCache[v.planId] || (S.plans || []).find((x) => x.id === v.planId);
    return isSheet(p) ? sheetView(v) : planView(v);
  };
  const after = St.after.plan;
  St.after.plan = (v) => {
    if (after) after(v);
    const p = S.planCache[v.planId];
    if (!isSheet(p) || p.status !== "done") return;
    St.pack.loadDoc(p.id);
    setTimeout(fit, 0);                               // once its formulas are drawn (renderView draws them next)
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(fit);
  };
  const onPlan = St.onEvents.plan;
  let refresh = null;
  St.onEvents.plan = (e) => {
    onPlan(e);
    const cached = S.planCache[e.id];
    if (!isSheet(cached) || e.status !== "running" || !["topics", "entries", "check"].includes(e.stage)) return;
    clearTimeout(refresh);                            // the topics fill in as they are written
    refresh = setTimeout(async () => {
      const r = await call("plan_get", e.id);
      if (!r.ok) return;
      S.planCache[e.id] = r.plan;
      if (S.view.name === "plan" && S.view.planId === e.id) St.renderView();
    }, 400);
  };
  Object.assign(A, { "sheet-new": (el) => A["pack-new"]({ dataset: { id: el && el.dataset.id, kind: "sheet" } }) });

  // ------------------------------------------------------------------ browser preview (mock)
  const mockDoc = "<header class=\"cs-head\"><div class=\"cs-title\"><span class=\"cs-course\">EEE 215 · Circuits &amp; Electronics for Computer Eng.</span><span class=\"cs-kind\">Cheatsheet</span><b>Circuit analysis, first-order circuits and phasors</b></div><div class=\"cs-meta\">Weeks 4–6 · Week04_Nodal_Mesh.pdf, Week05_Theorems.pdf, Week06_FirstOrder.pdf · lectures 14 Oct, 21 Oct, 28 Oct · 4 Nov 2026</div></header><div class=\"cs-cols\"><section class=\"cs-topic\" id=\"t1\"><div class=\"cs-keep\"><h2><span class=\"n\">1</span>Basic laws</h2><div class=\"cs-e cs-rule\"><div class=\"cs-h\"><i class=\"cs-k\">Rule</i><b class=\"cs-n\">Ohm's law</b> <span class=\"cs-x\">\\(v = iR\\)</span></div><div class=\"cs-w\"><span>\\(R\\) resistance ($\\Omega$)</span></div><div class=\"cs-if\"><b>if</b> passive sign convention</div></div></div><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">Power</b></div><div class=\"cs-tex\">\\[p = vi = i^2R = \\frac{v^2}{R}\\]</div><div class=\"cs-w\"><span>\\(p\\) power (W), absorbed if $p&gt;0$</span></div></div><div class=\"cs-e cs-rule\"><div class=\"cs-h\"><i class=\"cs-k\">Rule</i><b class=\"cs-n\">KCL</b></div><div class=\"cs-tex\">\\[\\sum_{n=1}^{N} i_n = 0\\]</div><div class=\"cs-w\"><span>\\(i_n\\) currents entering a node</span></div></div><div class=\"cs-e cs-rule\"><div class=\"cs-h\"><i class=\"cs-k\">Rule</i><b class=\"cs-n\">KVL</b></div><div class=\"cs-tex\">\\[\\sum_{m=1}^{M} v_m = 0\\]</div><div class=\"cs-w\"><span>\\(v_m\\) voltages around a closed loop</span></div></div><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">Series / parallel R</b></div><div class=\"cs-tex\">\\[R_{eq} = R_1 + R_2 + \\dots \\qquad \\frac{1}{R_{eq}} = \\frac{1}{R_1} + \\frac{1}{R_2} + \\dots\\]</div></div><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">Voltage divider</b></div><div class=\"cs-tex\">\\[v_2 = \\frac{R_2}{R_1 + R_2}\\, v_s\\]</div><div class=\"cs-w\"><span>\\(v_s\\) source voltage (V)</span></div><div class=\"cs-if\"><b>if</b> $R_1$, $R_2$ in series, no load</div></div><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">Current divider</b></div><div class=\"cs-tex\">\\[i_1 = \\frac{R_2}{R_1 + R_2}\\, i_s\\]</div><div class=\"cs-if\"><b>if</b> two resistors in parallel</div></div><div class=\"cs-e cs-unit\"><div class=\"cs-h\"><i class=\"cs-k\">Unit</i><b class=\"cs-n\">SI prefixes</b></div><div class=\"cs-tex\">\\[\\mathrm{p} = 10^{-12},\\ \\mathrm{n} = 10^{-9},\\ \\mu = 10^{-6},\\ \\mathrm{m} = 10^{-3},\\ \\mathrm{k} = 10^{3},\\ \\mathrm{M} = 10^{6}\\]</div></div></section><section class=\"cs-topic\" id=\"t2\"><div class=\"cs-keep\"><h2><span class=\"n\">2</span>Nodal and mesh analysis</h2><div class=\"cs-e cs-method\"><div class=\"cs-h\"><i class=\"cs-k\">Method</i><b class=\"cs-n\">Nodal analysis</b></div><div class=\"cs-t\">1) pick a reference node 2) KCL at each other node, currents as $(v_a - v_b)/R$ 3) solve for the node voltages</div></div></div><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">Conductance matrix</b></div><div class=\"cs-tex\">\\[\\begin{bmatrix} G_{11} &amp; -G_{12} \\\\ -G_{21} &amp; G_{22} \\end{bmatrix}\\begin{bmatrix} v_1 \\\\ v_2 \\end{bmatrix} = \\begin{bmatrix} i_1 \\\\ i_2 \\end{bmatrix}\\]</div><div class=\"cs-w\"><span>\\(G_{kk}\\) sum of conductances at node $k$ (S)</span> · <span>\\(G_{kj}\\) conductance between nodes $k$ and $j$</span> · <span>\\(i_k\\) source current into node $k$</span></div><div class=\"cs-if\"><b>if</b> only independent current sources</div></div><div class=\"cs-e cs-concept\"><div class=\"cs-h\"><i class=\"cs-k\">Idea</i><b class=\"cs-n\">Supernode</b></div><div class=\"cs-t\">A voltage source between two non-reference nodes: KCL around both nodes together, plus $v_a - v_b = V_s$.</div></div><div class=\"cs-e cs-method\"><div class=\"cs-h\"><i class=\"cs-k\">Method</i><b class=\"cs-n\">Mesh analysis</b></div><div class=\"cs-t\">1) mesh currents $i_1 \\dots i_n$ clockwise 2) KVL in each mesh 3) solve $R\\mathbf{i} = \\mathbf{v}$</div></div><div class=\"cs-e cs-concept\"><div class=\"cs-h\"><i class=\"cs-k\">Idea</i><b class=\"cs-n\">Supermesh</b></div><div class=\"cs-t\">A current source shared by two meshes: KVL around the outer loop, plus $i_2 - i_1 = I_s$.</div></div><div class=\"cs-e cs-rule\"><div class=\"cs-h\"><i class=\"cs-k\">Rule</i><b class=\"cs-n\">Cramer's rule</b></div><div class=\"cs-tex\">\\[x_k = \\frac{\\Delta_k}{\\Delta}, \\quad \\Delta = \\det A = \\begin{vmatrix} a &amp; b \\\\ c &amp; d \\end{vmatrix} = ad - bc\\]</div><div class=\"cs-w\"><span>\\(\\Delta_k\\) $\\det A$ with column $k$ replaced by the right-hand side</span></div><div class=\"cs-if\"><b>if</b> $\\Delta \\neq 0$</div></div></section><section class=\"cs-topic\" id=\"t3\"><div class=\"cs-keep\"><h2><span class=\"n\">3</span>Circuit theorems</h2><div class=\"cs-e cs-rule\"><div class=\"cs-h\"><i class=\"cs-k\">Rule</i><b class=\"cs-n\">Superposition</b></div><div class=\"cs-t\">Response = sum of the responses to each independent source alone (V sources shorted, I sources opened).</div></div></div><div class=\"cs-e cs-rule\"><div class=\"cs-h\"><i class=\"cs-k\">Rule</i><b class=\"cs-n\">Source transformation</b></div><div class=\"cs-tex\">\\[v_s = i_s R \\quad\\Longleftrightarrow\\quad i_s = \\frac{v_s}{R}\\]</div><div class=\"cs-if\"><b>if</b> the same $R$ in series (V source) or in parallel (I source)</div></div><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">Thevenin equivalent</b></div><div class=\"cs-tex\">\\[V_{Th} = v_{oc}, \\qquad R_{Th} = \\frac{v_{oc}}{i_{sc}}\\]</div><div class=\"cs-w\"><span>\\(v_{oc}\\) open-circuit voltage</span> · <span>\\(i_{sc}\\) short-circuit current</span></div><div class=\"cs-if\"><b>if</b> with dependent sources: apply a test source, $R_{Th} = v_o / i_o$</div></div><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">Norton equivalent</b> <span class=\"cs-x\">\\(I_N = i_{sc}, \\qquad R_N = R_{Th}\\)</span></div></div><div class=\"cs-e cs-rule\"><div class=\"cs-h\"><i class=\"cs-k\">Rule</i><b class=\"cs-n\">Maximum power transfer</b></div><div class=\"cs-tex\">\\[R_L = R_{Th} \\;\\Rightarrow\\; p_{max} = \\frac{V_{Th}^2}{4R_{Th}}\\]</div></div></section><section class=\"cs-topic\" id=\"t4\"><div class=\"cs-keep\"><h2><span class=\"n\">4</span>Operational amplifiers</h2><div class=\"cs-e cs-rule\"><div class=\"cs-h\"><i class=\"cs-k\">Rule</i><b class=\"cs-n\">Ideal op-amp</b> <span class=\"cs-x\">\\(i_+ = i_- = 0, \\quad v_+ = v_-\\)</span></div><div class=\"cs-if\"><b>if</b> negative feedback</div></div></div><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">Inverting amplifier</b></div><div class=\"cs-tex\">\\[v_o = -\\frac{R_f}{R_1}\\, v_i\\]</div></div><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">Non-inverting amplifier</b></div><div class=\"cs-tex\">\\[v_o = \\left(1 + \\frac{R_f}{R_1}\\right) v_i\\]</div></div><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">Summing amplifier</b></div><div class=\"cs-tex\">\\[v_o = -\\left(\\frac{R_f}{R_1}v_1 + \\frac{R_f}{R_2}v_2 + \\frac{R_f}{R_3}v_3\\right)\\]</div></div></section><section class=\"cs-topic\" id=\"t5\"><div class=\"cs-keep\"><h2><span class=\"n\">5</span>Capacitors and inductors</h2><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">Capacitor</b></div><div class=\"cs-tex\">\\[i = C\\frac{dv}{dt}, \\qquad v(t) = \\frac{1}{C}\\int_{t_0}^{t} i\\,d\\tau + v(t_0)\\]</div><div class=\"cs-w\"><span>\\(C\\) capacitance (F)</span></div></div></div><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">Inductor</b></div><div class=\"cs-tex\">\\[v = L\\frac{di}{dt}, \\qquad i(t) = \\frac{1}{L}\\int_{t_0}^{t} v\\,d\\tau + i(t_0)\\]</div><div class=\"cs-w\"><span>\\(L\\) inductance (H)</span></div></div><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">Stored energy</b></div><div class=\"cs-tex\">\\[w_C = \\tfrac12 C v^2, \\qquad w_L = \\tfrac12 L i^2\\]</div><div class=\"cs-w\"><span>\\(w\\) energy (J)</span></div></div><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">Series / parallel C and L</b></div><div class=\"cs-tex\">\\[\\begin{aligned} C_{par} &amp;= C_1 + C_2, &amp; \\tfrac{1}{C_{ser}} &amp;= \\tfrac{1}{C_1} + \\tfrac{1}{C_2} \\\\ L_{ser} &amp;= L_1 + L_2, &amp; \\tfrac{1}{L_{par}} &amp;= \\tfrac{1}{L_1} + \\tfrac{1}{L_2} \\end{aligned}\\]</div></div><div class=\"cs-e cs-rule\"><div class=\"cs-h\"><i class=\"cs-k\">Rule</i><b class=\"cs-n\">Continuity</b></div><div class=\"cs-t\">$v_C$ and $i_L$ cannot jump: $v_C(0^+) = v_C(0^-)$, $i_L(0^+) = i_L(0^-)$; in dc steady state C is open, L is a short.</div></div></section><section class=\"cs-topic\" id=\"t6\"><div class=\"cs-keep\"><h2><span class=\"n\">6</span>First-order RC and RL circuits</h2><div class=\"cs-e cs-definition\"><div class=\"cs-h\"><i class=\"cs-k\">Def</i><b class=\"cs-n\">Time constant</b></div><div class=\"cs-tex\">\\[\\tau = RC \\quad\\text{or}\\quad \\tau = \\frac{L}{R}\\]</div><div class=\"cs-w\"><span>\\(R\\) Thevenin resistance seen by C or L</span></div></div></div><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">Natural response</b> <span class=\"cs-x\">\\(v(t) = V_0\\, e^{-t/\\tau}\\)</span></div><div class=\"cs-if\"><b>if</b> no source after $t = 0$</div></div><div class=\"cs-e cs-definition\"><div class=\"cs-h\"><i class=\"cs-k\">Def</i><b class=\"cs-n\">Unit step</b></div><div class=\"cs-tex\">\\[u(t - t_0) = \\begin{cases} 0, &amp; t &lt; t_0 \\\\ 1, &amp; t &gt; t_0 \\end{cases}\\]</div></div><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">Step response</b></div><div class=\"cs-tex\">\\[v(t) = v(\\infty) + \\big[v(0^+) - v(\\infty)\\big]\\, e^{-t/\\tau}, \\quad t &gt; 0\\]</div><div class=\"cs-if\"><b>if</b> a dc source switched at $t = 0$</div></div><div class=\"cs-e cs-method\"><div class=\"cs-h\"><i class=\"cs-k\">Method</i><b class=\"cs-n\">Solving a first-order circuit</b></div><div class=\"cs-t\">1) $v(0^+)$ from $t&lt;0$ 2) $v(\\infty)$ with C open / L short 3) $\\tau$ from $R_{Th}$ 4) put into the step response</div></div></section><section class=\"cs-topic\" id=\"t7\"><div class=\"cs-keep\"><h2><span class=\"n\">7</span>Sinusoids and phasors</h2><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">Sinusoid</b></div><div class=\"cs-tex\">\\[v(t) = V_m \\cos(\\omega t + \\phi), \\quad \\omega = 2\\pi f = \\frac{2\\pi}{T}\\]</div><div class=\"cs-w\"><span>\\(V_m\\) amplitude</span> · <span>\\(\\omega\\) angular frequency (rad/s)</span> · <span>\\(\\phi\\) phase</span> · <span>\\(T\\) period (s)</span></div></div></div><div class=\"cs-e cs-definition\"><div class=\"cs-h\"><i class=\"cs-k\">Def</i><b class=\"cs-n\">Phasor</b></div><div class=\"cs-tex\">\\[V_m\\cos(\\omega t + \\phi) \\;\\leftrightarrow\\; \\mathbf{V} = V_m\\angle\\phi = V_m e^{j\\phi}\\]</div></div><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">Rectangular and polar</b></div><div class=\"cs-tex\">\\[z = x + jy = r\\angle\\phi, \\quad r = \\sqrt{x^2 + y^2}, \\quad \\phi = \\tan^{-1}\\frac{y}{x}\\]</div><div class=\"cs-if\"><b>if</b> add $180^\\circ$ to $\\phi$ when $x&lt;0$</div></div><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">Impedance</b></div><div class=\"cs-tex\">\\[Z_R = R, \\quad Z_L = j\\omega L, \\quad Z_C = \\frac{1}{j\\omega C}\\]</div><div class=\"cs-w\"><span>\\(Z\\) impedance ($\\Omega$)</span></div></div><div class=\"cs-e cs-definition\"><div class=\"cs-h\"><i class=\"cs-k\">Def</i><b class=\"cs-n\">Admittance</b></div><div class=\"cs-tex\">\\[Y = \\frac{1}{Z} = G + jB\\]</div><div class=\"cs-w\"><span>\\(Y\\) admittance (S)</span></div></div><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">RMS value</b></div><div class=\"cs-tex\">\\[V_{rms} = \\sqrt{\\frac{1}{T}\\int_0^T v^2\\,dt} = \\frac{V_m}{\\sqrt{2}}\\]</div><div class=\"cs-if\"><b>if</b> the last form for a sinusoid only</div></div><div class=\"cs-e cs-formula\"><div class=\"cs-h\"><b class=\"cs-n\">Complex power</b></div><div class=\"cs-tex\">\\[\\mathbf{S} = \\mathbf{V}_{rms}\\mathbf{I}_{rms}^{*} = P + jQ, \\quad \\mathrm{pf} = \\cos(\\theta_v - \\theta_i)\\]</div><div class=\"cs-w\"><span>\\(P\\) real power (W)</span> · <span>\\(Q\\) reactive power (VAR)</span> · <span>\\(|\\mathbf{S}|\\) apparent power (VA)</span></div></div><div class=\"cs-e cs-unit\"><div class=\"cs-h\"><i class=\"cs-k\">Unit</i><b class=\"cs-n\">Units</b></div><div class=\"cs-tex\">\\[1\\,\\mathrm{S} = 1\\,\\Omega^{-1}, \\quad 1\\,\\mathrm{F} = 1\\,\\mathrm{C/V}, \\quad 1\\,\\mathrm{H} = 1\\,\\mathrm{V\\,s/A}\\]</div></div></section></div>";
  const topics = [["t1", "Basic laws", 8], ["t2", "Nodal and mesh analysis", 6], ["t3", "Circuit theorems", 5], ["t4", "Operational amplifiers", 4],
    ["t5", "Capacitors and inductors", 5], ["t6", "First-order RC and RL circuits", 5], ["t7", "Sinusoids and phasors", 8]];
  const mockSheet = (status) => {
    const done = status === "done";
    return { id: done ? "cs1" : "cs2", course_id: "19226", title: done ? "Circuit analysis, first-order circuits and phasors" : "", status, stage: done ? "done" : "entries",
      progress: done ? 1 : 0.5, message: done ? "41 entries · 1 page" : "Writing the entries: part 2 of 3 (sonnet)…", error: "", parent_id: "", created_at: new Date().toISOString(),
      scope: { kind: "sheet", weeks: [4, 5, 6], files: ["a", "b", "c"], days: ["d1", "d2", "d3"], model: "sonnet" }, stages_done: done ? [] : ["gather", "topics"],
      chapters: topics.map(([id, title], i) => ({ id, title, done: i < 4 })),
      result: done ? { kind: "sheet", title: "Circuit analysis, first-order circuits and phasors", pdf: "C:/x.pdf", pages: 1, entries: 41, items: 41,
        topics: topics.map(([id, title, n]) => ({ id, title, entries: n })), uncovered: [], materials_missing: [], days_missing: [{ day_key: "d4" }] } : {} };
  };
  const baseList = St.mocks.plan_list, baseGet = St.mocks.plan_get, baseEstimate = St.mocks.plan_estimate, baseDoc = St.mocks.pack_doc;
  St.mocks.plan_list = () => { const r = baseList(); return { ...r, plans: [mockSheet("done"), mockSheet("running"), ...r.plans] }; };
  St.mocks.plan_get = (id) => (id === "cs1" || id === "cs2" ? { ok: true, plan: mockSheet(id === "cs1" ? "done" : "running") } : baseGet(id));
  St.mocks.plan_estimate = (cid, sc) => (sc && sc.kind === "sheet" ? { ok: true, estimate: { main_model: "sonnet", main_input: 31800, main_output: 9600, requests: 3, chapters: 1, files: 3, lectures: 3, materials_missing: [], days_missing: [], minutes: t("about {a}–{b} minutes", { a: 4, b: 8 }) } } : baseEstimate(cid, sc));
  St.mocks.pack_doc = (id) => (id === "cs1" ? { ok: true, html: mockDoc } : baseDoc(id));
})();
