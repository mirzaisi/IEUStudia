"""Numbers the app checks itself (numcheck.py): a safe calculator for the AI's expressions and the student's results,
results read as people write them (the Turkish decimal comma, prefixes, units, complex numbers, fractions), the
comparison (2 %, or the significant figures typed, in the same unit) and the check of a quiz's answer key."""

import math

import pytest

from studia import numcheck as nc


# ------------------------------------------------------------------ the calculator
@pytest.mark.parametrize("expr, want", [
    ("12*4.7/(4.7+2.2)", 8.1739), ("2^10", 1024), ("2**10", 1024), ("-2^2", -4), ("1.2e-3", 0.0012),
    ("1.2×10^-3", 0.0012), ("1.2 x 10^-3", 0.0012), ("3/8", 0.375), ("2pi", 2 * math.pi), ("2(3+4)", 14),
    ("sqrt(2)/2", 0.70711), ("√2", 1.41421), ("π/4", math.pi / 4), ("ln(e)", 1), ("log10(1000)", 3),
    ("log(8, 2)", 3), ("log2(8)", 3), ("exp(0)", 1), ("sin(pi/2)", 1), ("atan(1)*180/pi", 45), ("abs(3+4j)", 5),
    ("arg(1j)*180/pi", 90), ("comb(10,3)*0.3^3*0.7^7", 0.26683), ("factorial(5)", 120), ("2e", 2 * math.e),
    ("10²", 100), ("10⁻³", 0.001), ("−3 + 5", 2), ("6 ÷ 4", 1.5)])
def test_the_calculator_computes_what_people_write(expr, want):
    assert nc.evaluate(expr) == pytest.approx(want, rel=1e-4)


@pytest.mark.parametrize("expr, want", [("3+4j", 3 + 4j), ("3+j4", 3 + 4j), ("3 - j 4", 3 - 4j), ("4i", 4j),
                                        ("(1+j)(2-j)", 3 + 1j), ("sqrt(-4)", 2j), ("12/(3+4j)", 12 / (3 + 4j))])
def test_complex_numbers_with_j_or_i(expr, want):
    assert nc.evaluate(expr) == pytest.approx(want)


@pytest.mark.parametrize("expr", [
    "__import__('os').system('x')", "(1).real", "().__class__", "lambda: 1", "open('f')", "x + 1", "[1, 2]",
    "1 if 1 else 2", "a.b", "9^9^9", "9**9**9", "10**1000", "2^5000", "1e400", "factorial(500)", "comb(5000, 3)",
    "1/0", "0^-1", "ln(0)", "f'(x)", "", "1" * 400, "+".join(["1"] * 200), "sqrt(1, 2)", "sin(x=1)", "True + 1",
    "'1' + '2'", "[x for x in ()]", "{1: 2}"])
def test_anything_that_is_not_a_plain_calculation_is_refused(expr):
    with pytest.raises(ValueError):
        nc.evaluate(expr)


def test_a_huge_exponent_is_refused_at_once():
    import time

    t0 = time.monotonic()
    for e in ("9^9^9", "(10^10)^(10^10)", "2^1000^1000"):
        with pytest.raises(ValueError):
            nc.evaluate(e)
    assert time.monotonic() - t0 < 1


# ------------------------------------------------------------------ reading a result
@pytest.mark.parametrize("text, value, shown, label", [
    ("3,3 kΩ", 3300, 3.3, "kΩ"), ("8.17 V", 8.17, 8.17, "V"), ("8,17V", 8.17, 8.17, "V"), ("4.7kΩ", 4700, 4.7, "kΩ"),
    ("2.5 mA", 0.0025, 2.5, "mA"), ("0.5 µF", 5e-7, 0.5, "µF"), ("0.5 μF", 5e-7, 0.5, "μF"), ("0.5 uF", 5e-7, 0.5, "uF"),
    ("100 nF", 1e-7, 100, "nF"), ("2 MHz", 2e6, 2, "MHz"), ("1.5 GHz", 1.5e9, 1.5, "GHz"), ("3 kohm", 3000, 3, "kohm"),
    ("1.2e-3 A", 0.0012, 0.0012, "A"), ("1.2×10^-3 A", 0.0012, 0.0012, "A"), ("25%", 0.25, 25, "%"),
    ("9.81 m/s²", 9.81, 9.81, "m/s^(2)"), ("53.1°", math.radians(53.1), 53.1, "°"), ("2 ms", 0.002, 2, "ms"),
    ("1 min", 60, 1, "min"), ("50 Hz", 50, 50, "Hz"), ("12 W", 12, 12, "W"), ("3 J", 3, 3, "J"), ("2 N", 2, 2, "N"),
    ("101 kPa", 101000, 101, "kPa"), ("8.2 v", 8.2, 8.2, "v"), ("0.25", 0.25, 0.25, ""), ("3/8", 0.375, 0.375, ""),
    ("-4.5", -4.5, -4.5, ""), (".5", 0.5, 0.5, "")])
def test_a_result_as_written(text, value, shown, label):
    q = nc.parse_quantity(text)
    assert q is not None, text
    assert (q.value, q.shown, q.label) == (pytest.approx(value), pytest.approx(shown), label)


@pytest.mark.parametrize("text, value", [
    ("$v_2 = 4\\,\\mathrm{V}$", 4), ("$V_{out} \\approx 8.17\\ \\text{V}$", 8.17), ("**8.17 V**", 8.17),
    ("$I = 2.5 \\times 10^{-3}$ A", 0.0025), ("$\\frac{3}{8}$", 0.375), ("$R = 4.7\\,k\\Omega$", 4700),
    ("$P(X = 3) \\approx 0.267$", 0.267), ("v = 12·2/6 = 4 V", 4), ("2.5 mA (clockwise)", 0.0025),
    ("Answer: 12 V.", 12), ("Cevap: 3,3 kΩ", 3300), ("$\\theta = 53.13^\\circ$", math.radians(53.13)),
    ("$\\sqrt{2}/2$", 0.7071), ("$10^{3}$ Hz", 1000)])
def test_a_key_written_in_latex_and_words(text, value):
    q = nc.parse_quantity(text)
    assert q is not None, text
    assert q.value == pytest.approx(value, rel=1e-3)


def test_complex_results_and_polar_form():
    assert nc.parse_quantity("4+3j").value == pytest.approx(4 + 3j)
    assert nc.parse_quantity("3 + j4 Ω").value == pytest.approx(3 + 4j)
    q = nc.parse_quantity("5∠53.13°")
    assert q.value == pytest.approx(3 + 4j, rel=1e-4)
    q = nc.parse_quantity("10∠-30° V")
    assert q.value == pytest.approx(10 * complex(math.cos(-math.pi / 6), math.sin(-math.pi / 6))) and q.label == "V"
    assert nc.parse_quantity("2∠1.5708 rad").value == pytest.approx(2j, abs=1e-4)


@pytest.mark.parametrize("text", ["$\\mathbf{V}_C = -j5\\angle 0^\\circ$ V", "$\\mathbf{I} = j2\\angle 30^\\circ$ A",
                                  "(3+4j)∠30°", "-j5∠0° V", "3 km^400", "3 mm^-400"])
def test_a_complex_magnitude_or_a_huge_unit_power_is_no_result_not_an_error(text):
    """A complex magnitude in polar form raised "not a real number" and "km^400" an OverflowError, through
    quizzes.clean_quiz: the whole quiz was lost after it was paid for."""
    assert nc.parse_quantity(text) is None
    assert nc.unit("km^400") is None and nc.unit("mm^-400") is None
    nc.stated(text)                                                                    # (no error)
    assert "numeric" not in nc.check_key({"type": "problem", "model": text})
    nc.check_key({"type": "problem", "model": text, "check": {"expr": "3", "unit": "km"}})
    nc.check_key({"type": "short", "model": "3 km", "check": {"expr": "3", "unit": "km^400"}})


@pytest.mark.parametrize("text", ["x = 2, y = 3", "x1 = 2; x2 = 3", "x = 2 or x = -3", "x = 2 ve y = 3", "±2", "2, 3",
                                  "2 and 3", "f(x) = 3x^2 + 2", "about 8 V", "The current is 2.5 mA.", "", "   ",
                                  "1,5,3", "$\\alpha$", "1500 rpm", "8 bananas"])
def test_no_one_result_is_none(text):
    assert nc.parse_quantity(text) is None


def test_decimal_and_thousands_separators():
    assert nc.parse_quantity("1,234.5").value == 1234.5            # English
    assert nc.parse_quantity("1.234,5").value == 1234.5            # Turkish
    assert nc.parse_quantity("1.234.567").value == 1234567
    assert nc.parse_quantity("1,234,567").value == 1234567
    q = nc.parse_quantity("1,234")                                 # a Turkish decimal comma, or a thousands comma
    assert q.value == 1.234 and q.alt.value == 1234
    assert nc.parse_quantity("0,125").alt is None                  # (no one writes 0125)
    assert nc.parse_quantity("comb(10,3)").value == 120            # a comma between a function's numbers stays one
    assert nc.parse_quantity("sqrt(2,25)").value == 1.5             # ... but sqrt takes one: a decimal comma


def test_significant_figures_typed():
    assert [nc.parse_quantity(s).digits for s in ("8.20", "0.012", "1200", "1200.0", "1.2×10^-3", "3/8", "5")] == \
        [3, 2, 2, 5, 2, 0, 1]


def test_a_key_unit_the_app_does_not_know_counts_as_itself():
    ref = nc._from_check({"expr": "1500", "unit": "rpm"})
    assert nc.mark("1500 rpm", ref)["verdict"] == "right" and nc.mark("1500", ref)["verdict"] == "right"
    assert nc.mark("1400 rpm", ref)["verdict"] == "wrong"


# ------------------------------------------------------------------ comparing
def _ref(expr, unit=""):
    return nc._from_check({"expr": expr, "unit": unit})


@pytest.mark.parametrize("typed, verdict, unit", [
    ("8.17 V", "right", False), ("8.2", "right", False), ("8,17", "right", False), ("8174 mV", "right", False),
    ("8.1", "right", False), ("8.17 A", "wrong", True), ("8.17 mV", "wrong", False), ("8", "wrong", False),
    ("9 V", "wrong", False), ("12*4.7/6.9", "right", False), ("about 8 V", "unknown", False),
    ("v = 8.17 V", "right", False)])
def test_a_typed_result_against_a_key(typed, verdict, unit):
    got = nc.mark(typed, _ref("12*4.7/(4.7+2.2)", "V"))
    assert got["verdict"] == verdict and bool(got.get("unit")) == unit


def test_prefixes_and_a_result_typed_without_its_unit():
    ref = _ref("2.2", "kΩ")
    assert [nc.mark(s, ref)["verdict"] for s in ("2.2 kΩ", "2200", "2.2", "2200 Ω", "2,2 kΩ", "0.0022 MΩ")] == \
        ["right"] * 6
    assert nc.mark("2.2 Ω", ref)["verdict"] == "wrong"                    # the prefix forgotten: wrong
    assert nc.mark("2,200 Ω", ref)["verdict"] == "unknown"                # 2.2 Ω or 2200 Ω: the student says


def test_percent_degrees_and_numbers_without_units():
    p = _ref("comb(10,3)*0.3^3*0.7^7")
    assert [nc.mark(s, p)["verdict"] for s in ("0.267", "0.27", "26.7%", "0,2668", "0.3")] == \
        ["right", "right", "right", "right", "wrong"]
    pct = _ref("25", "%")
    assert [nc.mark(s, pct)["verdict"] for s in ("25%", "25", "0.25")] == ["right"] * 3
    deg = _ref("atan(4/3)*180/pi", "°")
    assert [nc.mark(s, deg)["verdict"] for s in ("53.13°", "53.1", "0.9273 rad", "53.13 deg")] == ["right"] * 4
    assert nc.mark("4 V", _ref("4"))["verdict"] == "right"                # the key had no unit: the number counts


def test_complex_results_are_compared_as_complex_numbers():
    z = _ref("12/(3+4j)", "A")
    assert nc.mark("1.44-1.92j A", z)["verdict"] == "right"
    assert nc.mark("2.4∠-53.13° A", z)["verdict"] == "right"
    assert nc.mark("1.44+1.92j A", z)["verdict"] == "wrong"
    assert nc.mark("2.4 A", z)["verdict"] == "wrong"                       # only the magnitude


def test_two_percent_or_the_figures_typed():
    assert nc.agree(101.9, 100) and not nc.agree(102.5, 100)
    assert nc.agree(0.012, 0.0123, 2) and not nc.agree(0.012, 0.0123)      # rounded alike to 2 figures
    assert not nc.agree(1, 1.4, 1)                                         # one figure is too few to tell
    assert nc.agree(0, 0) and not nc.agree(0.1, 0)


def test_no_reference_or_no_result_falls_back_to_the_student():
    assert nc.mark("8 V", None) == {"verdict": "unknown"}
    assert nc.mark("x = 2, y = 3", _ref("2"))["verdict"] == "unknown"
    assert nc.mark("", _ref("2"))["verdict"] == "unknown"


# ------------------------------------------------------------------ a quiz's key
def test_the_check_is_cleaned():
    assert nc.clean_check({"expr": "12*2/6", "value": 4, "unit": "V"}) == {"expr": "12*2/6", "value": 4, "unit": "V"}
    assert nc.clean_check("2+2") == {"expr": "2+2", "unit": ""}
    assert nc.clean_check({"expression": "3/8", "result": "0.375"}) == {"expr": "3/8", "value": "0.375", "unit": ""}
    assert nc.clean_check({"value": 4}) is None and nc.clean_check(None) is None and nc.clean_check(["x"]) is None
    assert nc.clean_check({"expr": "1" * 400}) is None
    assert nc.clean_check({"expr": "1", "value": float("nan")}) == {"expr": "1", "unit": ""}
    assert nc.clean_check({"expr": "4", "unit": "W", "of": "  the power\nin $R_2$ "}) == \
        {"expr": "4", "unit": "W", "of": "the power in $R_2$"}                       # which result, of several


@pytest.mark.parametrize("check, model, state", [
    ({"expr": "12*2/(2+4)", "value": 4, "unit": "V"}, "$v_2 = 4$ V", "ok"),
    ({"expr": "12*2/(2+4)", "value": 4.5, "unit": "V"}, "$v_2 = 4.5$ V", "bad"),          # the AI's arithmetic
    ({"expr": "12*2/(2+4)", "value": 4.5, "unit": "V"}, "So the voltage is 12·2/6 = 4.5 V.", "bad"),
    ({"expr": "12*2/(2+4)", "value": 4.5, "unit": "V"}, "So $v_2 = 4$ V.", "ok"),         # the answer is right
    ({"expr": "3/8"}, "The probability is $\\frac{3}{8} = 0.375$.", "ok"),
    ({"expr": "2.5e-3", "unit": "A"}, "$I = 2.5$ mA", "ok"),                              # other prefixes
    ({"expr": "2.5", "unit": "mA"}, "$I = 2.5 \\times 10^{-3}$ A", "ok"),
    ({"expr": "2.5", "unit": "mA"}, "The current is 2.5 mA, clockwise.", "ok"),
    ({"expr": "2.5", "unit": "mA"}, "$I = 2.5$ A", "bad"),
    ({"expr": "x*2", "unit": "V"}, "4 V", ""),                                            # not computable
    ({"expr": "4", "value": 5}, "see the steps", "bad"),                                  # nothing stated: its value
    ({"expr": "4", "value": 4}, "see the steps", "ok"),
    ({"expr": "4"}, "see the steps", ""),
])
def test_the_key_is_checked_against_its_own_answer(check, model, state):
    assert nc.verify(check, model) == state


@pytest.mark.parametrize("model, expr, unit", [
    ("12,000 Hz", "12000", "Hz"), ("$f = 12{,}000$ Hz", "12000", "Hz"), ("$P = 1{,}200$ W", "120*10", "W"),
    ("1,250 Ω", "1000+250", "Ω"), ("$12\\,000$ Hz", "12000", "Hz"), ("12 000 Hz", "12000", "Hz"),
    ("12 000 Hz", "12000", "Hz"), ("$R_{eş} = 1.250\\,\\Omega$", "1000+250", "Ω"),
    ("$E = 1{,}234{,}567$ J", "1234567", "J"), ("$v = 3{,}5$ V", "3.5", "V")])
def test_a_key_written_with_digit_groups_is_confirmed(model, expr, unit):
    """"12,000 Hz", "12{,}000", "12\\,000" and the Turkish "1.250 Ω" were read as 12, two numbers or 1.25: a right
    key was marked unverified, its repair request spent and "could not confirm" shown."""
    assert nc.verify({"expr": expr, "unit": unit}, model) == "ok"
    q = nc.check_key({"type": "problem", "model": model, "check": {"expr": expr, "unit": unit}})
    assert q["verified"] and q["numeric"]


def test_digit_groups_in_a_typed_result():
    ref = _ref("12000", "Hz")
    assert [nc.mark(s, ref)["verdict"] for s in ("12 000 Hz", "12 000", "$12\\,000$ Hz", "12{,}000 Hz")] == \
        ["right", "right", "right", "unknown"]                          # (12{,}000: 12 or 12000, the student says)
    assert nc.verify({"expr": "12000", "unit": "Hz"}, "12,5 Hz") == "bad"   # still a wrong key
    assert nc.parse_quantity("1.250").alt is None                      # a typed 1.250 is 1.25 (only a key's may be more)


def test_check_key_marks_the_question():
    q = nc.check_key({"type": "problem", "model": "$v_2 = 4$ V", "check": {"expr": "12*2/(2+4)", "value": 4, "unit": "V"}})
    assert q["verified"] and q["numeric"] and "unverified" not in q
    q = nc.check_key({"type": "problem", "model": "$v_2 = 4.5$ V", "check": {"expr": "12*2/6", "value": 4.5, "unit": "V"}})
    assert q["unverified"] and "numeric" not in q and nc.reference(q) is None    # never marked against a doubtful key
    q = nc.check_key({"type": "problem", "model": "$f'(x) = 3x^2$"})               # a formula: the student marks it
    assert "numeric" not in q and "verified" not in q
    q = nc.check_key({"type": "short", "model": "$\\tau = RC = 1$ ms"})            # no check: its stated result
    assert q["numeric"] and nc.reference(q).value == pytest.approx(0.001)
    q = nc.check_key({"type": "mc", "model": "4", "check": {"expr": "4"}})
    assert "numeric" not in q and "verified" not in q


# ------------------------------------------------------------------ a study pack's worked example
@pytest.mark.parametrize("line, check", [
    ("[[check 12/4 = 3 mA | the current]]", {"expr": "12/4", "value": "3", "unit": "mA", "of": "the current"}),
    ("[[check 2*pi*50 = 314,16]]", {"expr": "2*pi*50", "value": "314,16", "unit": ""}),
    ("[[check 5*2 = 10V]]", {"expr": "5*2", "value": "10", "unit": "V"}),
    ("[[ CHECK 3000*4=12 000 Hz|the frequency ]]", {"expr": "3000*4", "value": "12 000", "unit": "Hz",
                                                    "of": "the frequency"}),
    ("[[check 1/(2*pi*1000*1e-6) = 1.59e2 Ω | X_C]]", {"expr": "1/(2*pi*1000*1e-6)", "value": "1.59e2", "unit": "Ω",
                                                      "of": "X_C"}),
    ("[[check 5*2]]", {"expr": "5*2", "unit": ""}),
])
def test_a_check_line_is_read_however_the_ai_spaces_it(line, check):
    """The line a worked example ends with carries a quiz key's fields: read into them, it goes through clean_check
    and verify as a quiz's does, so digit groups, a decimal comma and units are read by one set of rules."""
    assert nc.check_line(line) == check


def test_a_check_line_without_an_expression_is_not_one():
    assert nc.check_line("[[check = 3 V]]") is None and nc.check_line("[[check]]") is None
    assert nc.check_line("check 2+2 = 4") is None and nc.check_line("") is None


def test_only_the_last_result_in_its_unit_counts_when_asked():
    """A worked example's paragraph states its steps before its result ("first $a = 6$ V, so $v = 5$ V"): with any
    number counting, the step's 6 V confirmed a wrong 5 V; a number of another unit before it is not a result."""
    ck = {"expr": "3*2", "unit": "V"}
    said = "First $a = 6$ V, so $v = 5$ V."
    assert nc.verify(ck, said) == "ok" and nc.verify(ck, said, last=1) == "bad"
    assert nc.verify(ck, "So $v = 6$ V, and $i = 2$ A.", last=1) == "ok"
    assert nc.verify(ck, "$v_1 = 6$ V and $v_2 = 3$ V.", last=2) == "ok"
