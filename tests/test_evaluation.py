from pathlib import Path

from blastradius.evaluation import Score, run_all, score, total

CASES = Path(__file__).resolve().parents[1] / "eval" / "cases"


def test_score_math():
    s = score(truth={"a", "b", "c"}, flagged={"a", "b", "x"})

    assert s.true_positives == {"a", "b"}
    assert s.false_positives == {"x"}
    assert s.false_negatives == {"c"}
    assert s.precision == 2 / 3
    assert s.recall == 2 / 3


def test_empty_score_is_perfect():
    assert Score().precision == Score().recall == 1.0


def test_accuracy_does_not_regress():
    """A ratchet: raise these numbers when the scanner improves, never lower them."""
    results = run_all(CASES)
    strict, lenient = total(results, "strict"), total(results, "lenient")

    assert len(results) >= 5
    assert strict.precision >= 0.99
    assert strict.recall >= 0.57
    assert lenient.precision >= 0.82
    assert lenient.recall >= 0.90
