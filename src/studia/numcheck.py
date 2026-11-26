"""Numbers the app checks itself (4.2): a result the student types for a worked problem, and the AI's own answer key.

``evaluate`` computes an expression safely: Python's own parser, then only numbers, + - * / ^ (or **), a few functions
(sqrt, sin, cos, tan, asin, acos, atan, exp, ln, log, log10, log2, abs, arg, comb, factorial) and the names pi, e and
j (or i, for √-1). No other names, no attributes, no other calls; the exponent and the result are capped, so
"9^9^9" is refused instead of run.

``parse_quantity`` reads a result as people write it: "3,3 kΩ" (the Turkish decimal comma), "1.2e-3 A",
"1.2×10^-3", "3/8", "4+3j", "3 + j4", "5∠53.13°", or a key's "$v_2 = 4\\,\\mathrm{V}$" (the right side of the last
"="), with the SI prefixes (p n µ u m k M G T) and units (V, A, Ω, W, Hz, s, F, H, J, N, Pa, %, …), compound ones too
("m/s^2"). Several values ("x = 2, y = 3", "±") are no one result: None, and the student marks it.

``compare`` says whether a result is the key's: within 2 %, or equal to as many significant figures as were typed
(two at least), in the same unit; a result typed without a unit is taken as it stands and in the key's own unit
("2.2" and "2200" both match 2.2 kΩ). ``mark`` gives a verdict for the page: "right", "wrong" (``unit``: the unit is
another) or "unknown" (not one result the app could read: the student marks it, and no error is shown).

A quiz's key: the AI gives each worked problem (and short answer that is one number) a "check", {"expr", "value",
"unit"}; ``check_key`` computes the expression and compares it with the result its answer states (else its value):
"verified" when they agree, "unverified" when they don't (quizzes.run asks the AI once to solve those again).

A study pack's worked example: the same fields on a line of its own at the end of its box, "[[check expr = value unit |
of]]" (``check_line``), checked the same way against the result the box ends with (studypack.clean_section)."""

from __future__ import annotations

import ast
import cmath
import math
import operator
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional, Union

Num = Union[float, complex]

MAX_EXPR = 300          # characters in an expression
MAX_NODES = 150         # parts of a parsed expression
MAX_POWER = 1000        # the largest exponent
MAX_ABS = 1e150         # the largest result
TOL = 0.02              # two results within 2 % agree
MIN_DIGITS = 2          # ... or rounded alike to the significant figures typed, two at least
# what the AI is told a check's fields are, in a quiz's key (quizzes.CHECK_RULE) and a study pack's worked example
# (studypack.section_prompt) alike: the calculation ``evaluate`` can do, and the result it is compared with
CHECK_FIELDS = ('"expr" uses only numbers, + - * / ^ ( ), sqrt, sin, cos, tan, asin, acos, atan (radians), exp, '
                'ln, log10, log2, abs, arg, comb, factorial, pi, e and j for the imaginary unit (no letters for '
                'quantities, no units); "value" is its result as a number in "unit"; "unit" like "V", "mA", "kΩ", '
                '"Hz", "%" ("" for none); "of" names that result in a few words, in the language of your explanations '
                '(like "the voltage across R2")')

# ------------------------------------------------------------------ evaluating an expression
_CONST: dict[str, Num] = {"pi": math.pi, "e": math.e, "j": 1j, "i": 1j}


def _real(x: Num) -> float:
    if isinstance(x, complex):
        if abs(x.imag) > 1e-12 * max(1.0, abs(x.real)):
            raise ValueError("not a real number")
        return x.real
    return x


def _f1(real: Callable[[float], float], cplx: Callable[[complex], complex]) -> Callable[[Num], Num]:
    """A function of one number: the real one, the complex one for a complex number or outside the real domain
    (sqrt(-4) = 2j, as in circuits)."""
    def fn(x: Num) -> Num:
        if isinstance(x, complex):
            return cplx(x)
        try:
            return real(x)
        except ValueError:
            return cplx(complex(x))
    return fn


def _whole(x: Num, most: int) -> int:
    v = _real(x)
    if not float(v).is_integer() or not 0 <= v <= most:
        raise ValueError("not a small whole number")
    return int(v)


def _log(x: Num, base: Optional[Num] = None) -> Num:
    ln = _f1(math.log, cmath.log)
    return ln(x) if base is None else ln(x) / ln(base)


_FUNCS: dict[str, tuple[tuple[int, ...], Callable[..., Num]]] = {
    "sqrt": ((1,), _f1(math.sqrt, cmath.sqrt)),
    "sin": ((1,), _f1(math.sin, cmath.sin)), "cos": ((1,), _f1(math.cos, cmath.cos)),
    "tan": ((1,), _f1(math.tan, cmath.tan)), "asin": ((1,), _f1(math.asin, cmath.asin)),
    "acos": ((1,), _f1(math.acos, cmath.acos)), "atan": ((1,), _f1(math.atan, cmath.atan)),
    "exp": ((1,), _f1(math.exp, cmath.exp)), "ln": ((1,), _f1(math.log, cmath.log)), "log": ((1, 2), _log),
    "log10": ((1,), _f1(math.log10, cmath.log10)), "log2": ((1,), lambda x: _log(x, 2.0)),
    "abs": ((1,), abs), "arg": ((1,), lambda x: cmath.phase(complex(x))),
    "comb": ((2,), lambda n, k: float(math.comb(_whole(n, 1000), _whole(k, 1000)))),
    "factorial": ((1,), lambda n: float(math.factorial(_whole(n, 170)))),
}
_OPS: dict[type, Callable[[Any, Any], Any]] = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                                               ast.Div: operator.truediv, ast.Pow: operator.pow}


def _cap(x: Any) -> Num:
    if isinstance(x, complex):
        ok = math.isfinite(x.real) and math.isfinite(x.imag) and abs(x) <= MAX_ABS
    elif isinstance(x, (int, float)) and not isinstance(x, bool):
        x = float(x)
        ok = math.isfinite(x) and abs(x) <= MAX_ABS
    else:
        ok = False
    if not ok:
        raise ValueError("too large")
    return x


def _ev(n: ast.AST) -> Num:
    if isinstance(n, ast.Constant):
        v = n.value
        if isinstance(v, bool) or not isinstance(v, (int, float, complex)):
            raise ValueError("not a number")
        return _cap(v)
    if isinstance(n, ast.Name):
        if n.id in _CONST:
            return _CONST[n.id]
        raise ValueError(f"unknown name {n.id}")
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
        v = _ev(n.operand)
        return -v if isinstance(n.op, ast.USub) else v
    if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
        a, b = _ev(n.left), _ev(n.right)
        if isinstance(n.op, ast.Pow) and abs(b) > MAX_POWER:
            raise ValueError("exponent too large")
        return _cap(_OPS[type(n.op)](a, b))
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords:
        arity, fn = _FUNCS[n.func.id]
        if len(n.args) not in arity:
            raise ValueError("arguments")
        return _cap(fn(*[_ev(a) for a in n.args]))
    raise ValueError(f"not allowed: {type(n).__name__}")


def _tidy(v: Num) -> Num:
    if isinstance(v, complex) and abs(v.imag) <= 1e-12 * max(1.0, abs(v.real)):
        v = v.real
    return 0.0 if v == 0 else v


_SUP = re.compile(r"[⁻⁺]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+")
_SUP_MAP = str.maketrans("⁻⁺⁰¹²³⁴⁵⁶⁷⁸⁹", "-+0123456789")
_NUMBER = r"(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_WHOLE = _NUMBER + r"(?![\d.]|[eE][-+]?\d)"          # a number taken whole ("1.2e-3" is never 1.2·e - 3)


def _supers(s: str) -> str:
    """Superscript powers as ^(…): "m²" → "m^(2)", "10⁻³" → "10^(-3)"."""
    return _SUP.sub(lambda m: "^(" + m.group(0).translate(_SUP_MAP) + ")", s)


def _expr_text(s: str) -> str:
    """An expression as Python reads it: ^ and × and · and π and √, "j4" and "4i" as 4j, and the multiplication
    people leave out ("2pi", "2(3+4)", "(1+j)(2-j)")."""
    s = _supers(s.strip())
    for a, b in (("−", "-"), ("–", "-"), ("×", "*"), ("·", "*"), ("⋅", "*"), ("÷", "/"), ("π", "pi"), ("^", "**")):
        s = s.replace(a, b)
    s = re.sub(r"(?<=\d)\s*[xX]\s*(?=\d)", "*", s)                                     # 1.2 x 10^-3
    s = re.sub(r"√\s*\(", "sqrt(", s)
    s = re.sub(r"√\s*(" + _WHOLE + r"|pi|e)", r"sqrt(\1)", s)
    s = re.sub(r"(?<![\w.])([ij])\s*(" + _WHOLE + r")", r"(\2j)", s)                   # j4, j 4 → (4j)
    s = re.sub(r"(?<![\w.])(" + _WHOLE + r")\s*[ij](?![\w(])", r"(\1j)", s)           # 4i, 4 j → (4j)
    s = re.sub(r"(?<![\w.])([ij])\s*\(", r"\1*(", s)                                   # j(…)
    s = re.sub(r"((?<![\w.])" + _WHOLE + r"|\))\s*(?=[A-Za-z_(])", r"\1*", s)          # 2pi, 2(…), )(
    return s


def evaluate(expr: Any) -> Num:
    """The value of an expression (a float, or a complex number when it has an imaginary part); ValueError for
    anything that isn't a plain calculation or is too large."""
    s = _expr_text(str(expr if expr is not None else ""))
    if not s or len(s) > MAX_EXPR:
        raise ValueError("empty or too long")
    try:
        tree = ast.parse(s, mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise ValueError("not an expression") from exc
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise ValueError("too long")
    try:
        return _tidy(_ev(tree.body))
    except (ArithmeticError, ValueError, TypeError, RecursionError) as exc:
        raise ValueError(str(exc) or "not computable") from exc


# ------------------------------------------------------------------ units
_PREFIX = {"p": 1e-12, "n": 1e-9, "µ": 1e-6, "μ": 1e-6, "u": 1e-6, "m": 1e-3, "c": 1e-2, "k": 1e3, "K": 1e3,
           "M": 1e6, "G": 1e9, "T": 1e12}
_DEG = math.pi / 180
# a unit as written: (the unit it is counted in, its factor); those in _PREFIXED take a prefix ("kΩ", "mA", "µF")
_BASE: dict[str, tuple[str, float]] = {
    "V": ("V", 1), "A": ("A", 1), "Ω": ("Ω", 1), "ohm": ("Ω", 1), "ohms": ("Ω", 1), "Ohm": ("Ω", 1), "W": ("W", 1),
    "Hz": ("Hz", 1), "s": ("s", 1), "F": ("F", 1), "H": ("H", 1), "J": ("J", 1), "N": ("N", 1), "Pa": ("Pa", 1),
    "C": ("C", 1), "K": ("K", 1), "m": ("m", 1), "g": ("g", 1), "S": ("S", 1), "T": ("T", 1), "Wb": ("Wb", 1),
    "eV": ("J", 1.602176634e-19), "VA": ("VA", 1), "var": ("var", 1), "VAR": ("var", 1), "Wh": ("J", 3600),
    "bit": ("bit", 1), "bits": ("bit", 1), "B": ("B", 1), "rad": ("rad", 1), "°": ("rad", _DEG),
    "deg": ("rad", _DEG), "%": ("", 0.01), "dB": ("dB", 1), "h": ("s", 3600), "min": ("s", 60), "L": ("L", 1),
    "mol": ("mol", 1), "°C": ("°C", 1), "°F": ("°F", 1),
    # written out (English, Turkish)
    "volt": ("V", 1), "volts": ("V", 1), "amp": ("A", 1), "amps": ("A", 1), "ampere": ("A", 1), "amperes": ("A", 1),
    "amper": ("A", 1), "watt": ("W", 1), "watts": ("W", 1), "hertz": ("Hz", 1), "sec": ("s", 1), "secs": ("s", 1),
    "second": ("s", 1), "seconds": ("s", 1), "saniye": ("s", 1), "sn": ("s", 1), "farad": ("F", 1),
    "henry": ("H", 1), "joule": ("J", 1), "joules": ("J", 1), "newton": ("N", 1), "newtons": ("N", 1),
    "pascal": ("Pa", 1), "coulomb": ("C", 1), "coulombs": ("C", 1), "meter": ("m", 1), "meters": ("m", 1),
    "metre": ("m", 1), "metres": ("m", 1), "radian": ("rad", 1), "radians": ("rad", 1), "degree": ("rad", _DEG),
    "degrees": ("rad", _DEG), "derece": ("rad", _DEG), "hour": ("s", 3600), "hours": ("s", 3600),
    "saat": ("s", 3600), "minute": ("s", 60), "minutes": ("s", 60), "dakika": ("s", 60), "dk": ("s", 60),
}
_PREFIXED = {"V", "A", "Ω", "ohm", "ohms", "Ohm", "W", "Hz", "s", "F", "H", "J", "N", "Pa", "C", "m", "g", "S", "T",
             "Wb", "eV", "VA", "var", "VAR", "Wh", "bit", "bits", "B", "L", "mol"}
_LOWER = {"v": "V", "mv": "mV", "kv": "kV", "a": "A", "ma": "mA", "ua": "uA", "w": "W", "mw": "mW", "kw": "kW",
          "hz": "Hz", "khz": "kHz", "kohm": "kΩ", "kohms": "kΩ", "pa": "Pa", "kpa": "kPa"}
_TOKEN = re.compile(r"([A-Za-zΩµμ°%]+)(?:\^\(?([-+]?\d+)\)?)?$")


@dataclass(frozen=True)
class Unit:
    dims: tuple[tuple[str, int], ...]      # what it is counted in: (("V", 1),), (("m", 1), ("s", -2)); () for none
    factor: float                          # its size in those (kΩ: 1000)
    label: str                             # as written


def _base(name: str, known: tuple[str, ...] = ()) -> Optional[tuple[str, float]]:
    if name in _BASE:
        return _BASE[name]
    if name in known:                       # a key's own unit the app doesn't know ("rpm"): only itself
        return (name, 1.0)
    if name[:1] in _PREFIX and name[1:] in _PREFIXED:
        unit, f = _BASE[name[1:]]
        return unit, f * _PREFIX[name[:1]]
    low = _LOWER.get(name.lower())
    return _base(low) if low and low != name else None


def unit(label: str, known: tuple[str, ...] = ()) -> Optional[Unit]:
    """A unit as written ("kΩ", "m/s^2", "N·m", "%"), or None when it isn't one the app knows."""
    s = _supers(str(label or "").strip())
    if not s or len(s) > 40:
        return None
    if s in known:
        return Unit(((s, 1),), 1.0, s)
    dims: dict[str, int] = {}
    factor = 1.0
    for k, part in enumerate(s.split("/")):
        tokens = [x for x in re.split(r"\s*[·*⋅.]\s*|\s+", part.strip()) if x]
        if not tokens:
            return None
        for tok in tokens:
            m = _TOKEN.match(tok)
            if not m:
                return None
            b = _base(m.group(1), known)
            if b is None:
                return None
            power = int(m.group(2) or 1) * (-1 if k else 1)
            if b[0]:
                dims[b[0]] = dims.get(b[0], 0) + power
            try:
                factor *= b[1] ** power
            except OverflowError:                      # ("km^400": no unit anyone writes)
                return None
    return Unit(tuple(sorted((u, p) for u, p in dims.items() if p)), factor, s)


# ------------------------------------------------------------------ reading a result
@dataclass(frozen=True)
class Quantity:
    value: Num                  # in the unit's own measure (prefix applied: 3.3 kΩ is 3300)
    shown: Num                  # the number as written (3.3)
    dims: tuple[tuple[str, int], ...] = ()
    given: bool = False         # a unit was written
    digits: int = 0             # significant figures typed (0: an expression, a fraction)
    label: str = ""
    alt: Optional["Quantity"] = None      # "1,234" read the other way (a thousands comma)


_CMD = {"Omega": "Ω", "mu": "µ", "pi": "π", "times": "×", "cdot": "·", "div": "÷", "approx": "≈", "pm": "±",
        "angle": "∠", "degree": "°", "circ": "°", "left": "", "right": "", "quad": " ", "qquad": " ", "infty": "∞"}


def plain(text: Any) -> str:
    """A result as plain text: LaTeX ($…$, \\frac, \\text{…}, \\Omega, ^{…}) and bold taken off, spaces tidied."""
    s = str(text if text is not None else "")
    s = re.sub(r"(^|\s)\*\*|\*\*(?=\s|$|[.,;])", r"\1", s)
    for a in ("\\(", "\\)", "\\[", "\\]", "$"):
        s = s.replace(a, " ")
    s = re.sub(r"(?<=\d)\{,\}(?=\d)", ",", s)              # LaTeX's comma in a number ("12{,}000", "3{,}5")
    for _ in range(4):                                   # (nested ones from the inside out)
        s = re.sub(r"\\(?:text|mathrm|mathbf|mathit|operatorname|textrm|mbox|rm|boxed)\s*\{([^{}]*)\}", r"\1", s)
        s = re.sub(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"((\1)/(\2))", s)
        s = re.sub(r"\\sqrt\s*\[([^\]]*)\]\s*\{([^{}]*)\}", r"((\2)^(1/(\1)))", s)
        s = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"sqrt(\1)", s)
    s = re.sub(r"\^\s*\{?\s*\\circ\s*\}?", "°", s)
    s = re.sub(r"\\([A-Za-z]+)", lambda m: _CMD.get(m.group(1), m.group(0)), s)
    s = re.sub(r"\\[,;:! ]", " ", s).replace("\\%", "%").replace("~", " ")
    s = re.sub(r"[   ]", " ", s)
    # digits grouped by threes with (thin) spaces: "12 000", "$12\,000$" → 12000
    s = re.sub(r"(?<![\d.,])(\d{1,3})((?: \d{3})+)(?![\d.,])", lambda m: m.group(1) + m.group(2).replace(" ", ""), s)
    s = re.sub(r"\^\s*\{([^{}]*)\}", r"^(\1)", s)
    s = re.sub(r"_\s*\{([^{}]*)\}", r"_\1", s)
    s = _supers(s.replace("{", "(").replace("}", ")"))
    s = re.sub(r"^\s*(?:final answer|answer|result|cevap|sonuç|yanıt)\s*[:=]\s*", "", s, flags=re.I)
    return re.sub(r"\.+$", "", " ".join(s.split()).strip(" ;:")).strip()


_SEVERAL = re.compile(r",(?!\d)|;|\n|\b(?:and|or|ve|veya)\b", re.I)


def _result_side(s: str) -> Optional[str]:
    """The result of "v = 12·2/6 = 4 V" (after the last "=" or "≈"); None for several results ("x = 2, y = 3")."""
    parts = re.split(r"\s*(?:=|≈|≃|≅)\s*", s)
    if len(parts) == 1:
        return s
    if any(_SEVERAL.search(p) for p in parts[1:-1]):
        return None
    return parts[-1]


_SEP_RUN = re.compile(r"\d[\d.,]*\d")


def _in_call(s: str, at: int, names: tuple[str, ...] = ("comb", "log")) -> bool:
    """Is position ``at`` inside the brackets of a function taking two numbers (its comma separates them)?"""
    opens: list[bool] = []
    for i, ch in enumerate(s[:at]):
        if ch == "(":
            opens.append(s[:i].rstrip().endswith(names))
        elif ch == ")" and opens:
            opens.pop()
    return bool(opens and opens[-1])


def _separators(s: str, dots: bool = False) -> tuple[Optional[str], Optional[str]]:
    """Decimal and thousands separators as Python reads them: "3,3" → 3.3, "1.234,5" → 1234.5, "1,234.5" → 1234.5,
    "1.234.567" → 1234567. "1,234" could be either: 1.234 (a Turkish decimal comma), and the other reading as well;
    with ``dots`` (an answer key's), so could "1.250" (a Turkish thousands dot). (None, None) for a number written in
    a way no one writes."""
    out, alt, ambiguous, pos = [], [], 0, 0
    for m in _SEP_RUN.finditer(s):
        run = m.group(0)
        if "," not in run and run.count(".") <= 1 and not (dots and re.fullmatch(r"[1-9]\d{0,2}\.\d{3}", run)):
            continue
        if "," in run and _in_call(s, m.start()):
            continue
        one = two = None
        if "," in run and "." in run:
            dec = "," if run.rfind(",") > run.rfind(".") else "."
            th = "." if dec == "," else ","
            one = run.replace(th, "").replace(dec, ".")
        else:
            sep = "," if "," in run else "."
            groups = run.split(sep)
            if len(groups) > 2:
                if all(len(g) == 3 for g in groups[1:]):
                    one = "".join(groups)
                else:
                    return None, None
            elif sep == ",":
                one = run.replace(",", ".")
                if len(groups[1]) == 3 and groups[0] != "0":
                    two = run.replace(",", "")
                    ambiguous += 1
            elif dots:                                     # "1.250": 1.25, or 1250
                one, two = run, run.replace(".", "")
                ambiguous += 1
            else:
                continue
        out.append(s[pos:m.start()] + one)
        alt.append(s[pos:m.start()] + (two or one))
        pos = m.end()
    first = "".join(out) + s[pos:]
    return first, ("".join(alt) + s[pos:]) if ambiguous == 1 else None


def _digits(head: str) -> int:
    """Significant figures in a plain number ("8.20" → 3, "0.012" → 2, "1200" → 2, "1.2×10^-3" → 2); 0 otherwise."""
    m = re.fullmatch(r"\s*[-+]?(\d*)(?:\.(\d*))?\s*(?:(?:[eE][-+]?\d+)|(?:[×*·]\s*10\s*(?:\^|\*\*)\s*\(?[-+]?\d+\)?))?\s*",
                     head)
    if not m or not (m.group(1) or m.group(2)):
        return 0
    whole, frac = m.group(1) or "", m.group(2) or ""
    if m.group(2) is not None:
        return max(1, len((whole + frac).lstrip("0")))
    return max(1, len(whole.lstrip("0").rstrip("0")))


def _unit_split(s: str, known: tuple[str, ...]) -> Optional[tuple[Num, str, Optional[Unit]]]:
    """A number and the unit after it: the value, the number's text, the unit (None when none is written)."""
    try:
        return evaluate(s), s, None
    except ValueError:
        pass
    for i in range(1, len(s)):
        if not re.match(r"[A-Za-zΩµμ°%]", s[i]) or re.match(r"[A-Za-zΩµμ°_]", s[i - 1]):
            continue
        u = unit(s[i:], known)
        if u is None:
            continue
        try:
            return evaluate(s[:i]), s[:i].strip(), u
        except ValueError:
            continue
    return None


def _polar(s: str, known: tuple[str, ...]) -> Optional[tuple[Num, Optional[Unit]]]:
    """ "5∠53.13°" (degrees unless "rad" says otherwise), its unit after it ("5∠-30° V")."""
    mag, _, rest = s.partition("∠")
    m = re.match(r"\s*([-+]?" + _NUMBER + r")\s*(°|deg|rad)?\s*(.*)$", rest)
    if not m:
        return None
    u = unit(m.group(3), known) if m.group(3).strip() else None
    if m.group(3).strip() and u is None:
        return None
    try:
        r, a = _real(evaluate(mag)), float(m.group(1))       # (a complex magnitude, "-j5∠0°": not one it reads)
    except ValueError:
        return None
    return _tidy(r * cmath.exp(1j * (a if m.group(2) == "rad" else a * _DEG))), u


def _quantity(s: str, known: tuple[str, ...]) -> Optional[Quantity]:
    if "∠" in s:
        got = _polar(s, known)
        if got is None:
            return None
        v, u = got
        return Quantity(v * (u.factor if u else 1), v, u.dims if u else (), u is not None, 0, u.label if u else "")
    split = _unit_split(s, known)
    if split is None:
        return None
    v, head, u = split
    return Quantity(_tidy(v * (u.factor if u else 1)), v, u.dims if u else (), u is not None, _digits(head),
                    u.label if u else "")


def parse_quantity(text: Any, known: tuple[str, ...] = (), key: bool = False) -> Optional[Quantity]:
    """One result as written (a number, an expression or a complex number, with its unit), or None: several values,
    a formula, words. ``known``: units the app doesn't know that count as themselves (a key's "rpm"); ``key``: an
    answer key's, where "1.250" may be a Turkish 1250 too."""
    s = plain(text)
    if not s or len(s) > MAX_EXPR or "±" in s:
        return None
    s = _result_side(s)
    if s is None:
        return None
    s = re.sub(r"\s+\([^()\d]*\)\s*$", "", s).strip(" ;:")              # "8.17 V (clockwise)"
    if not s:
        return None
    first, other = _separators(s, key)
    if first is None:
        return None
    q = _quantity(first, known)
    if q is None:
        return None
    if other is not None and other != first:
        alt = _quantity(other, known)
        if alt is not None:
            q = Quantity(q.value, q.shown, q.dims, q.given, q.digits, q.label, alt)
    return q


# ------------------------------------------------------------------ comparing
def _sf(x: float, n: int) -> float:
    return 0.0 if x == 0 else round(x, n - 1 - int(math.floor(math.log10(abs(x)))))


def agree(x: Num, y: Num, digits: int = 0) -> bool:
    """Is ``x`` the result ``y``: within TOL, or ``y`` rounded to the ``digits`` significant figures ``x`` was
    typed with (MIN_DIGITS at least)?"""
    if y == 0:
        return abs(x) <= 1e-12
    if abs(x - y) <= TOL * abs(y):
        return True
    if digits >= MIN_DIGITS and not isinstance(x, complex) and not isinstance(y, complex):
        return math.isclose(_sf(x, digits), _sf(y, digits), rel_tol=1e-9)
    return False


def compare(a: Quantity, ref: Quantity) -> str:
    """ "right", "unit" (another unit), "maybe" (right only if "1,234" was a thousands comma) or "wrong"."""
    def one(x: Quantity) -> str:
        if x.given and ref.given and x.dims != ref.dims:
            return "unit"
        if x.given and ref.given:
            pairs = [(x.value, ref.value)]
        elif x.given:                                      # the key has no unit: the number as it stands
            pairs = [(x.value, ref.value), (x.shown, ref.value)]
        else:                                              # typed without one: as it stands, and in the key's unit
            pairs = [(x.value, ref.value), (x.value, ref.shown)]
        return "right" if any(agree(p, q, x.digits) for p, q in pairs) else "wrong"

    got = one(a)
    if got != "right" and a.alt is not None and one(a.alt) == "right":
        return "maybe"
    return got


def _known(ref: Optional[Quantity]) -> tuple[str, ...]:
    return tuple(u for u, _ in (ref.dims if ref else ()) if u not in {b for b, _ in _BASE.values()})


def mark(typed: Any, ref: Optional[Quantity]) -> dict[str, Any]:
    """The app's verdict on a typed result: {"verdict": "right" | "wrong" | "unknown", "unit": True when only the
    unit is another}. "unknown": nothing to compare with, or not one result the app could read."""
    if ref is None:
        return {"verdict": "unknown"}
    q = parse_quantity(typed, _known(ref))
    if q is None:
        return {"verdict": "unknown"}
    c = compare(q, ref)
    if c == "right":
        return {"verdict": "right"}
    if c == "maybe":
        return {"verdict": "unknown"}
    return {"verdict": "wrong", "unit": c == "unit"}


# ------------------------------------------------------------------ a quiz's key
def clean_check(v: Any) -> Optional[dict[str, Any]]:
    """The AI's "check" as kept: {"expr", "value", "unit"} (an expression alone is one too); None without one."""
    if isinstance(v, str):
        v = {"expr": v}
    if not isinstance(v, dict):
        return None
    expr = str(v.get("expr") or v.get("expression") or v.get("formula") or "").strip()
    if not expr or len(expr) > MAX_EXPR:
        return None
    out: dict[str, Any] = {"expr": expr}
    val = v.get("value", v.get("result"))
    if isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val):
        out["value"] = val
    elif isinstance(val, str) and val.strip():
        out["value"] = val.strip()[:60]
    out["unit"] = str(v.get("unit") or v.get("units") or "").strip()[:24]
    of = " ".join(str(v.get("of") or v.get("label") or v.get("what") or "").split())[:100]
    if of:                       # which result it is, when the problem asks for more than one ("the power in R2")
        out["of"] = of
    return out


CHECK_LINE = re.compile(r"\[\[\s*check\s+(.*?)\s*\]\]", re.I | re.S)
_LINE_VALUE = re.compile(r"([-+]?(?:\d(?:[\d.,]|[ \u00a0\u202f](?=\d{3}(?!\d)))*|[.,]\d+)(?:\s*[eE][-+]?\d+)?)\s*(.*)",
                         re.S)


def check_line(text: str) -> Optional[dict[str, Any]]:
    """A "[[check expr = value unit | of]]" line's check, as ``clean_check`` keeps it: "[[check 12/4 = 3 mA | the
    current]]", "[[check 2*pi*50 = 314,16]]", "[[check 5*2 = 10V]]". Without a value ("[[check 5*2]]") the result its
    box states is compared with the expression alone. None for a line it can't read."""
    m = CHECK_LINE.fullmatch((text or "").strip())
    if not m:
        return None
    body, of = m.group(1), ""
    if "|" in body:
        body, of = body.rsplit("|", 1)
    expr, _, right = body.partition("=")
    v = _LINE_VALUE.fullmatch(right.strip())
    value, unit_ = (v.group(1).strip(), v.group(2).strip()) if v else (right.strip(), "")
    return clean_check({"expr": expr.strip(), "value": value, "unit": unit_, "of": of.strip()})


def _key_unit(label: str) -> Optional[Unit]:
    """The key's unit; one the app doesn't know counts as itself (so only the same words match it)."""
    if not label:
        return None
    return unit(label) or unit(label, (label,))


def _from_check(ck: dict[str, Any]) -> Optional[Quantity]:
    try:
        v = evaluate(ck.get("expr"))
    except ValueError:
        return None
    u = _key_unit(str(ck.get("unit") or ""))
    return Quantity(_tidy(v * (u.factor if u else 1)), v, u.dims if u else (), u is not None, 0, u.label if u else "")


_MATH_SPAN = re.compile(r"\$\$(.+?)\$\$|\$([^$]+)\$", re.S)
_NUM_IN_TEXT = re.compile(r"(?<![\w.,])[-+]?\d+(?:[.,]\d+)*(?:\s*[eE][-+]?\d+|\s*[×*·]\s*10\s*\^\s*\(?[-+]?\d+\)?)?")
_UNIT_AFTER = re.compile(r"\s*([A-Za-zΩµμ°%][A-Za-zΩµμ°%/^·0-9-]*)")


def stated(model: Any, known: tuple[str, ...] = ()) -> list[Quantity]:
    """The results an answer states: the whole answer when it is one ("$v_2 = 4$ V"), else its formulas, else the
    numbers in its words ("The current is 2.5 mA, clockwise.")."""
    text = str(model or "")
    whole = parse_quantity(text, known, True)
    if whole is not None:
        return [whole]
    out = []
    for m in _MATH_SPAN.finditer(text):
        body = m.group(1) or m.group(2)
        after = _UNIT_AFTER.match(text[m.end():])
        for cand in ([f"{body} {after.group(1)}"] if after else []) + [body]:
            q = parse_quantity(cand, known, True)
            if q is not None:
                out.append(q)
                break
    if out:
        return out
    words = plain(text)
    for m in _NUM_IN_TEXT.finditer(words):
        after = _UNIT_AFTER.match(words[m.end():])
        q = (parse_quantity(f"{m.group(0)} {after.group(1)}", known, True) if after else None) or \
            parse_quantity(m.group(0), (), True)
        if q is not None:
            out.append(q)
    return out


def verify(ck: Optional[dict[str, Any]], model: Any, last: int = 0) -> str:
    """ "ok" when the key's expression, computed here, is the result its answer states (else its value), "bad" when
    it isn't, "" when there is nothing to compare (no expression the app can compute, nothing stated). A result
    written with a comma or dot that could group thousands ("12,000 Hz", "1.250 Ω") is the expression's when either
    reading is: the expression says which one is meant. ``last``: only the last results stated in the expression's
    unit count, that many, as in a worked example's text, whose steps state numbers of the same kind before the
    result ("first $a = 6$ V, so $v = 5$ V")."""
    ref = _from_check(ck or {})
    if ref is None:
        return ""
    found = stated(model, _known(ref))
    if found and last:
        same = [q for q in found if compare(q, ref) != "unit"]
        found = (same or found)[-last:]
    if found:
        return "ok" if any(compare(q, ref) in ("right", "maybe") for q in found) else "bad"
    val = (ck or {}).get("value")
    v = parse_quantity(str(val)) if val is not None else None
    if v is None:
        return ""
    return "ok" if agree(v.shown, ref.shown) else "bad"


def check_key(q: dict[str, Any]) -> dict[str, Any]:
    """A short answer's or worked problem's key, checked: "verified" (the app computed the same result), or
    "unverified" (it didn't); and "numeric" when a typed result can be marked against it. ``q`` is changed."""
    q.pop("verified", None)
    q.pop("unverified", None)
    q.pop("numeric", None)
    if q.get("type") not in ("short", "problem"):
        return q
    try:
        state = verify(q.get("check"), q.get("model")) if q.get("check") else ""
        if state == "ok":
            q["verified"] = True
        elif state == "bad":
            q["unverified"] = True
        if reference(q) is not None:
            q["numeric"] = True
    except (ValueError, ArithmeticError):             # a key it can't read: the student marks it, the quiz stays
        for k in ("verified", "unverified", "numeric"):
            q.pop(k, None)
    return q


def reference(q: dict[str, Any]) -> Optional[Quantity]:
    """What a typed result is compared with: the checked key's computed value, else the answer itself when it is
    one result ("$v_2 = 4$ V"). None for a key the app could not confirm, and for answers that aren't one number."""
    if q.get("type") not in ("short", "problem") or q.get("unverified"):
        return None
    ck = q.get("check")
    if ck and q.get("verified"):
        got = _from_check(ck)
        if got is not None:
            return got
    return parse_quantity(q.get("model") or "")
