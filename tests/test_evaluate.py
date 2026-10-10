from pii_pipeline.evaluate import score
from pii_pipeline.models import Action, Finding, PIIType


def f(t, unit="u", s=0, e=10, action=Action.MASK):
    return Finding(t, unit, s, e, "test", 0.9, action)


def row(res, t):
    return res["per_type"][t.value]


def test_exact_match():
    r = score([f(PIIType.EMAIL)], [f(PIIType.EMAIL)])
    assert row(r, PIIType.EMAIL) == {"tp": 1, "fp": 0, "fn": 0, "review": 0, "review_caught": 0,
                                     "precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert r["overall"]["tp"] == 1 and r["overall"]["f1"] == 1.0


def test_partial_overlap_counts_but_disjoint_does_not():
    assert row(score([f(PIIType.EMAIL, s=5, e=15)], [f(PIIType.EMAIL, s=0, e=10)]), PIIType.EMAIL)["tp"] == 1
    r = score([f(PIIType.EMAIL, s=10, e=20)], [f(PIIType.EMAIL, s=0, e=10)])       # touching, no overlap
    assert row(r, PIIType.EMAIL)["tp"] == 0 and row(r, PIIType.EMAIL)["fp"] == 1 and row(r, PIIType.EMAIL)["fn"] == 1


def test_wrong_type_is_fp_for_predicted_and_fn_for_gold():
    r = score([f(PIIType.PHONE)], [f(PIIType.US_SSN)])
    assert row(r, PIIType.PHONE)["fp"] == 1 and row(r, PIIType.US_SSN)["fn"] == 1
    assert row(r, PIIType.PHONE)["tp"] == 0 and row(r, PIIType.US_SSN)["tp"] == 0


def test_different_unit_does_not_match():
    r = score([f(PIIType.EMAIL, unit="a")], [f(PIIType.EMAIL, unit="b")])
    assert r["overall"]["tp"] == 0 and r["overall"]["fp"] == 1 and r["overall"]["fn"] == 1


def test_duplicates_are_one_tp_and_one_fp():
    r = score([f(PIIType.EMAIL), f(PIIType.EMAIL, s=2, e=8)], [f(PIIType.EMAIL)])
    assert (row(r, PIIType.EMAIL)["tp"], row(r, PIIType.EMAIL)["fp"], row(r, PIIType.EMAIL)["fn"]) == (1, 1, 0)


def test_two_gold_one_prediction():
    r = score([f(PIIType.EMAIL, s=0, e=30)], [f(PIIType.EMAIL, s=0, e=10), f(PIIType.EMAIL, s=12, e=22)])
    assert (row(r, PIIType.EMAIL)["tp"], row(r, PIIType.EMAIL)["fn"]) == (1, 1)     # one prediction matches one gold


def test_review_is_its_own_column_and_never_a_true_positive():
    r = score([f(PIIType.PHONE, action=Action.REVIEW)], [f(PIIType.PHONE)])
    p = row(r, PIIType.PHONE)
    assert p["tp"] == 0 and p["fp"] == 0 and p["fn"] == 1 and p["review"] == 1 and p["review_caught"] == 1
    assert p["recall"] == 0.0 and p["precision"] is None


def test_review_off_gold_is_counted_but_not_caught():
    r = score([f(PIIType.OTHER, unit="x", action=Action.REVIEW)], [f(PIIType.EMAIL)])
    assert row(r, PIIType.OTHER)["review"] == 1 and row(r, PIIType.EMAIL)["review_caught"] == 0


def test_keep_findings_are_ignored_and_whole_unit_spans_overlap():
    r = score([f(PIIType.EMAIL, action=Action.KEEP)], [f(PIIType.EMAIL)])
    assert row(r, PIIType.EMAIL)["tp"] == 0 and row(r, PIIType.EMAIL)["fp"] == 0
    whole = Finding(PIIType.EMAIL, "u", None, None, "t", 0.5, Action.MASK)
    assert score([whole], [f(PIIType.EMAIL, s=3, e=9)])["overall"]["tp"] == 1


def test_undefined_ratios_are_none_and_unsupported_type_is_zero_recall():
    r = score([], [f(PIIType.PERSON)])
    p = row(r, PIIType.PERSON)
    assert p["recall"] == 0.0 and p["precision"] is None and p["f1"] is None
    assert score([], [])["overall"]["precision"] is None
