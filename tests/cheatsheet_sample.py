"""A real-looking cheatsheet as the AI would answer it (EEE 215, weeks 4-6: circuit analysis), with fractions, matrices,
cases, units and Greek letters: the fake AI of test_cheatsheet.py answers with it, and the layout is checked on it."""

TOPICS = {"title": "Circuit analysis, first-order circuits and phasors", "topics": [
    {"id": "t1", "title": "Basic laws", "items": ["Ohm's law", "power", "KCL", "KVL", "series resistors", "parallel resistors",
                                                  "voltage divider", "current divider", "SI prefixes"],
     "sources": {"files": [{"ref": "F1", "pages": [1, 1]}]}},
    {"id": "t2", "title": "Nodal and mesh analysis", "items": ["nodal analysis", "conductance matrix", "supernode",
                                                                "mesh analysis", "supermesh", "Cramer's rule"],
     "sources": {"files": [{"ref": "F1", "pages": [2, 2]}]}},
    {"id": "t3", "title": "Circuit theorems", "items": ["superposition", "source transformation", "Thevenin equivalent",
                                                         "Norton equivalent", "maximum power transfer"],
     "sources": {"files": [{"ref": "F1", "pages": [2, 2]}]}},
    {"id": "t4", "title": "Operational amplifiers", "items": ["ideal op-amp rules", "inverting amplifier",
                                                               "non-inverting amplifier", "summing amplifier"],
     "sources": {"files": [{"ref": "F1", "pages": [1, 2]}]}},
    {"id": "t5", "title": "Capacitors and inductors", "items": ["capacitor i-v", "inductor i-v", "stored energy",
                                                                 "series and parallel C and L", "continuity"],
     "sources": {"files": [{"ref": "F1", "pages": [1, 1]}]}},
    {"id": "t6", "title": "First-order RC and RL circuits", "items": ["time constant", "natural response", "step response",
                                                                       "unit step function", "general solution"],
     "sources": {"files": [{"ref": "F1", "pages": [2, 2]}]}},
    {"id": "t7", "title": "Sinusoids and phasors", "items": ["sinusoid", "phasor", "impedance", "admittance",
                                                              "rectangular and polar form", "rms value", "complex power"],
     "sources": {"files": [{"ref": "F1", "pages": [1, 2]}]}},
]}


def _f(name, tex, where=(), when="", covers=(), kind="formula", text=""):
    return {"kind": kind, "name": name, "tex": tex, "text": text, "where": [{"sym": s, "means": m} for s, m in where],
            "when": when, "covers": list(covers) or [name]}


ENTRIES = {
    "t1": [
        _f("Ohm's law", r"v = iR", [("R", r"resistance ($\Omega$)")], "passive sign convention", ["Ohm's law"], "rule"),
        _f("Power", r"p = vi = i^2R = \frac{v^2}{R}", [("p", "power (W), absorbed if $p>0$")], covers=["power"]),
        _f("KCL", r"\sum_{n=1}^{N} i_n = 0", [("i_n", "currents entering a node")], covers=["KCL"], kind="rule"),
        _f("KVL", r"\sum_{m=1}^{M} v_m = 0", [("v_m", "voltages around a closed loop")], covers=["KVL"], kind="rule"),
        _f("Series / parallel R", r"R_{eq} = R_1 + R_2 + \dots \qquad \frac{1}{R_{eq}} = \frac{1}{R_1} + \frac{1}{R_2} + \dots",
           covers=["series resistors", "parallel resistors"]),
        _f("Voltage divider", r"v_2 = \frac{R_2}{R_1 + R_2}\, v_s", [("v_s", "source voltage (V)")],
           "$R_1$, $R_2$ in series, no load", ["voltage divider"]),
        _f("Current divider", r"i_1 = \frac{R_2}{R_1 + R_2}\, i_s", when="two resistors in parallel", covers=["current divider"]),
        _f("SI prefixes", r"\mathrm{p} = 10^{-12},\ \mathrm{n} = 10^{-9},\ \mu = 10^{-6},\ \mathrm{m} = 10^{-3},\ \mathrm{k} = 10^{3},\ \mathrm{M} = 10^{6}",
           covers=["SI prefixes"], kind="unit"),
    ],
    "t2": [
        _f("Nodal analysis", "", kind="method", covers=["nodal analysis"],
           text="1) pick a reference node 2) KCL at each other node, currents as $(v_a - v_b)/R$ 3) solve for the node voltages"),
        _f("Conductance matrix", r"\begin{bmatrix} G_{11} & -G_{12} \\ -G_{21} & G_{22} \end{bmatrix}\begin{bmatrix} v_1 \\ v_2 \end{bmatrix} = \begin{bmatrix} i_1 \\ i_2 \end{bmatrix}",
           [("G_{kk}", "sum of conductances at node $k$ (S)"), ("G_{kj}", "conductance between nodes $k$ and $j$"),
            ("i_k", "source current into node $k$")], "only independent current sources", ["conductance matrix"]),
        _f("Supernode", "", kind="concept", covers=["supernode"],
           text="A voltage source between two non-reference nodes: KCL around both nodes together, plus $v_a - v_b = V_s$."),
        _f("Mesh analysis", "", kind="method", covers=["mesh analysis"],
           text="1) mesh currents $i_1 \\dots i_n$ clockwise 2) KVL in each mesh 3) solve $R\\mathbf{i} = \\mathbf{v}$"),
        _f("Supermesh", "", kind="concept", covers=["supermesh"],
           text="A current source shared by two meshes: KVL around the outer loop, plus $i_2 - i_1 = I_s$."),
        _f("Cramer's rule", r"x_k = \frac{\Delta_k}{\Delta}, \quad \Delta = \det A = \begin{vmatrix} a & b \\ c & d \end{vmatrix} = ad - bc",
           [("\\Delta_k", "$\\det A$ with column $k$ replaced by the right-hand side")], "$\\Delta \\neq 0$",
           ["Cramer's rule"], "rule"),
    ],
    "t3": [
        _f("Superposition", "", kind="rule", covers=["superposition"],
           text="Response = sum of the responses to each independent source alone (V sources shorted, I sources opened)."),
        _f("Source transformation", r"v_s = i_s R \quad\Longleftrightarrow\quad i_s = \frac{v_s}{R}",
           when="the same $R$ in series (V source) or in parallel (I source)", covers=["source transformation"], kind="rule"),
        _f("Thevenin equivalent", r"V_{Th} = v_{oc}, \qquad R_{Th} = \frac{v_{oc}}{i_{sc}}",
           [("v_{oc}", "open-circuit voltage"), ("i_{sc}", "short-circuit current")],
           "with dependent sources: apply a test source, $R_{Th} = v_o / i_o$", ["Thevenin equivalent"]),
        _f("Norton equivalent", r"I_N = i_{sc}, \qquad R_N = R_{Th}", covers=["Norton equivalent"]),
        _f("Maximum power transfer", r"R_L = R_{Th} \;\Rightarrow\; p_{max} = \frac{V_{Th}^2}{4R_{Th}}",
           covers=["maximum power transfer"], kind="rule"),
    ],
    "t4": [
        _f("Ideal op-amp", r"i_+ = i_- = 0, \quad v_+ = v_-", when="negative feedback", covers=["ideal op-amp rules"], kind="rule"),
        _f("Inverting amplifier", r"v_o = -\frac{R_f}{R_1}\, v_i", covers=["inverting amplifier"]),
        _f("Non-inverting amplifier", r"v_o = \left(1 + \frac{R_f}{R_1}\right) v_i", covers=["non-inverting amplifier"]),
        _f("Summing amplifier", r"v_o = -\left(\frac{R_f}{R_1}v_1 + \frac{R_f}{R_2}v_2 + \frac{R_f}{R_3}v_3\right)",
           covers=["summing amplifier"]),
    ],
    "t5": [
        _f("Capacitor", r"i = C\frac{dv}{dt}, \qquad v(t) = \frac{1}{C}\int_{t_0}^{t} i\,d\tau + v(t_0)",
           [("C", "capacitance (F)")], covers=["capacitor i-v"]),
        _f("Inductor", r"v = L\frac{di}{dt}, \qquad i(t) = \frac{1}{L}\int_{t_0}^{t} v\,d\tau + i(t_0)",
           [("L", "inductance (H)")], covers=["inductor i-v"]),
        _f("Stored energy", r"w_C = \tfrac12 C v^2, \qquad w_L = \tfrac12 L i^2", [("w", "energy (J)")], covers=["stored energy"]),
        _f("Series / parallel C and L", r"\begin{aligned} C_{par} &= C_1 + C_2, & \tfrac{1}{C_{ser}} &= \tfrac{1}{C_1} + \tfrac{1}{C_2} \\ L_{ser} &= L_1 + L_2, & \tfrac{1}{L_{par}} &= \tfrac{1}{L_1} + \tfrac{1}{L_2} \end{aligned}",
           covers=["series and parallel C and L"]),
        _f("Continuity", "", kind="rule", covers=["continuity"],
           text="$v_C$ and $i_L$ cannot jump: $v_C(0^+) = v_C(0^-)$, $i_L(0^+) = i_L(0^-)$; in dc steady state C is open, L is a short."),
    ],
    "t6": [
        _f("Time constant", r"\tau = RC \quad\text{or}\quad \tau = \frac{L}{R}", [("R", "Thevenin resistance seen by C or L")],
           covers=["time constant"], kind="definition"),
        _f("Natural response", r"v(t) = V_0\, e^{-t/\tau}", when="no source after $t = 0$", covers=["natural response"]),
        _f("Unit step", r"u(t - t_0) = \begin{cases} 0, & t < t_0 \\ 1, & t > t_0 \end{cases}", covers=["unit step function"],
           kind="definition"),
        _f("Step response", r"v(t) = v(\infty) + \big[v(0^+) - v(\infty)\big]\, e^{-t/\tau}, \quad t > 0",
           when="a dc source switched at $t = 0$", covers=["step response", "general solution"]),
        _f("Solving a first-order circuit", "", kind="method", covers=["general solution"],
           text="1) $v(0^+)$ from $t<0$ 2) $v(\\infty)$ with C open / L short 3) $\\tau$ from $R_{Th}$ 4) put into the step response"),
    ],
    "t7": [
        _f("Sinusoid", r"v(t) = V_m \cos(\omega t + \phi), \quad \omega = 2\pi f = \frac{2\pi}{T}",
           [("V_m", "amplitude"), ("\\omega", "angular frequency (rad/s)"), ("\\phi", "phase"), ("T", "period (s)")],
           covers=["sinusoid"]),
        _f("Phasor", r"V_m\cos(\omega t + \phi) \;\leftrightarrow\; \mathbf{V} = V_m\angle\phi = V_m e^{j\phi}", covers=["phasor"],
           kind="definition"),
        _f("Rectangular and polar", r"z = x + jy = r\angle\phi, \quad r = \sqrt{x^2 + y^2}, \quad \phi = \tan^{-1}\frac{y}{x}",
           when="add $180^\\circ$ to $\\phi$ when $x<0$", covers=["rectangular and polar form"]),
        _f("Impedance", r"Z_R = R, \quad Z_L = j\omega L, \quad Z_C = \frac{1}{j\omega C}", [("Z", r"impedance ($\Omega$)")],
           covers=["impedance"]),
        _f("Admittance", r"Y = \frac{1}{Z} = G + jB", [("Y", "admittance (S)")], covers=["admittance"], kind="definition"),
        _f("RMS value", r"V_{rms} = \sqrt{\frac{1}{T}\int_0^T v^2\,dt} = \frac{V_m}{\sqrt{2}}", when="the last form for a sinusoid only",
           covers=["rms value"]),
        _f("Complex power", r"\mathbf{S} = \mathbf{V}_{rms}\mathbf{I}_{rms}^{*} = P + jQ, \quad \mathrm{pf} = \cos(\theta_v - \theta_i)",
           [("P", "real power (W)"), ("Q", "reactive power (VAR)"), ("|\\mathbf{S}|", "apparent power (VA)")], covers=["complex power"]),
        _f("Units", r"1\,\mathrm{S} = 1\,\Omega^{-1}, \quad 1\,\mathrm{F} = 1\,\mathrm{C/V}, \quad 1\,\mathrm{H} = 1\,\mathrm{V\,s/A}",
           covers=["units"], kind="unit"),
    ],
}


def answer_for(prompt: str) -> dict:
    """The entries answer for the topics an entries prompt asks for (as the AI would write them)."""
    import re

    ids = re.findall(r"^(t\d+) · ", prompt, re.M)
    return {"topics": [{"id": i, "entries": ENTRIES[i]} for i in ids]}
