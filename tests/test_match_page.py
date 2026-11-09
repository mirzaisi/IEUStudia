"""The page finds words as the search does: match.js (find in a transcript, the files filter, the chat and plan
lists), run in node with the rules from search.py, gives the same words, forms, matches and marked words as
search.py on the same texts."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studia import i18n, search

WEB = Path(i18n.__file__).parent / "web"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

TEXTS = [
    "Next week we have the first quiz, so please study.",
    "One more thing about the quiz: bring a pencil.",
    "Quiz 1 and 2 will be about Thevenin. Quiz-1, 1. quiz, Q1 and Q-2; Clear Q 0 1.",
    "## 1. Quiz 2: what the lecturer said about the 2nd homework (HW2) and the 3'üncü ödev",
    "Arkadaşlar ara sınavda Bayes çıkacak. Sınavları, SINAVLARINDA, sınavı",
    "Ara verelim, 10 dakika sonra sınava başlıyoruz.",
    "This is an example of conditional probability; examine the exams, examination.",
    "Probability and conditional events are different. probabilities, probability",
    "The Poisson distribution counts events. Poisson dağılımı ve olasılığı, olasılıkları",
    "MATH 240 Engineering ProbabilityWeek 14 · Week01_Student · Ch. 3 · LaTeX on an iPhone · ﬁnal",
    "Kısa sınav 1 haftaya, birinci quiz; ilk quizimiz; kişi, kısım, kısaca",
    "The Thevenin equivalent; then them; since sine sin; Bayesian bays",
    "Homework 2: three questions on Bayes. Assignment 2 · ödev 2",
    "Midterm Exam · Ara Sınav · vize · mid term · ARA SINAV",
    "What does it mean? That means the switch is here.",
    "The average of the values; örneklemin ortalaması",
]
FAMILY = ["exam", "exams", "sınav", "sınavda", "quiz", "olasılık", "bayes", "the", "kısa", "sin", "probability",
          "ödev", "theve", "midterm", "homework"]
QUERIES = [("exam", False), ("quiz one", False), ("ara sınav", False), ("midterm", True), ("homework 2", False),
           ('"conditional probability"', False), ("poisson dağılımı", False), ("probabilty", False), ("theve", True),
           ("sınavda", False), ("kısa sınav 1", False), ("1. quiz", False), ("quiz 2", False), ("bayes", False),
           ("exa", True), ("vize", True), ('"quiz', True), ("the", False), ("ödevi", True), ("average", False),
           ("ortalama", False), ("mean", False)]

SCRIPT = """
globalThis.window = globalThis;
require(%(match)s);
const data = %(data)s;
Match.load(data.rules);
const out = { tokens: data.texts.map((t) => Match.tokens(Match.prep(t))) };
const df = new Map();
for (const t of data.texts) for (const w of new Set(Match.words(t))) df.set(w, (df.get(w) || 0) + 1);
const v = new Match.Vocab(df, data.texts.length);
out.family = data.family.map((w) => [...v.family(Match.fold(w)).entries()].sort());
out.queries = data.queries.map(([q, typing]) => {
  const m = Match.query(q, data.texts, { typing });
  return { fixed: m.fixed, results: data.texts.map((t) => m.match(t)) };
});
Match.load({ slots: [] });
console.log(JSON.stringify(out));
process.stdout.write("", () => process.exit(0));
"""


def _python():
    texts = [search.prep(t) for t in TEXTS]
    df = {}
    for t in texts:
        for w in set(search.words(t)):
            df[w] = df.get(w, 0) + 1
    vocab = search.Vocab(df, len(texts))
    out = {"tokens": [[list(x) for x in search.tokens(t)] for t in texts],
           "family": [sorted([k, v] for k, v in vocab.family(search.fold(w)).items()) for w in FAMILY], "queries": []}
    for q, typing in QUERIES:
        concepts = search.parse(q, vocab, typing)
        wanted = search.prepare(concepts)
        real = [j for j, c in enumerate(concepts) if not c.stop] or list(range(len(concepts)))
        results = []
        for t in texts:
            toks = search.tokens(t)
            occ = search._occurrences(concepts, [w for w, _, _ in toks], wanted)
            found = [j for j in real if occ[j]]
            spans = sorted((toks[s][1], toks[e - 1][2]) for lst in occ for s, e, _ in lst)
            merged = []
            for a, b in spans:
                if merged and a < merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            results.append({"every": bool(concepts) and len(found) == len(real), "some": bool(found), "marks": merged})
        out["queries"].append({"fixed": search.fixed_query(q, concepts), "results": results})
    return out


def test_the_page_matches_as_the_search_does(tmp_path):
    data = {"rules": search.rules(), "texts": TEXTS, "family": FAMILY, "queries": QUERIES}
    script = tmp_path / "run.js"
    script.write_text(SCRIPT % {"match": json.dumps(str(WEB / "match.js")), "data": json.dumps(data, ensure_ascii=False)},
                      encoding="utf-8")
    got = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8",
                                    check=True).stdout)
    want = _python()
    assert got["tokens"] == want["tokens"]
    for w, a, b in zip(FAMILY, got["family"], want["family"]):
        assert [k for k, _ in a] == [k for k, _ in b], w
        assert all(abs(x - y) < 1e-9 for (_, x), (_, y) in zip(a, b)), w
    for (q, _), a, b in zip(QUERIES, got["queries"], want["queries"]):
        assert a == b, q


def test_the_page_finds_what_it_should(tmp_path):
    """A few of the cases themselves (the comparison above would pass with both wrong)."""
    want = _python()
    q = dict(zip([x for x, _ in QUERIES], want["queries"]))
    every = lambda query: [i for i, r in enumerate(q[query]["results"]) if r["every"]]      # noqa: E731
    assert every("quiz one") == [0, 2, 10]                     # first quiz; Quiz 1 / Q1 / 1. quiz; birinci quiz
    assert every("ara sınav") == [4, 13]                       # not "Ara verelim ... sınava"
    assert every("exam") == [4, 5, 6, 10, 13]                  # sınavda, sınava, exams, kısa sınav (never example)
    text = search.prep(TEXTS[6])
    assert [text[a:b] for a, b in q["exam"]["results"][6]["marks"]] == ["exams", "examination"]
    assert q["probabilty"]["fixed"] == "probability"
    assert 4 not in every("kısa sınav 1") and 10 in every("kısa sınav 1")
    assert every("bayes") == [4, 11, 12]                       # Bayesian, not bays
    assert every("average") == every("ortalama") == [15]        # never the verb: "what does it mean", "this means"
    assert every("mean") == [14, 15]                            # typed: the word itself, and what it means
