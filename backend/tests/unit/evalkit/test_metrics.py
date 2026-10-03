"""F048 metrics on a hand-made corpus with known errors (no models, no database)."""

import pytest

from app.evalkit.gate import gate_checks
from app.evalkit.metrics import Case, Sig, canon, compute, percentile

GOOGL = {"underlying": "GOOGL", "expiry_token": "10/2", "strike": "357.5", "right": "call"}


def entry(**over: object) -> Sig:
    return Sig("entry", {**GOOGL, "entry_price": "0.71", **over})


def case(
    expected: list[Sig],
    predicted: list[Sig] | None,
    *,
    provider: int = 1,
    path: str = "assemble",
    ms: float = 10,
) -> Case:
    return Case(
        provider,
        dict(enumerate(expected)),
        None if predicted is None else dict(enumerate(predicted)),
        path,
        ms,
    )


CORPUS = [
    case([entry()], [entry()]),  # right
    case([entry()], [entry(entry_price="0.75")], provider=2),  # one wrong field, contract fine
    case([entry()], [entry(strike="360")], provider=3, path="slow_path"),  # WRONG CONTRACT
    case([entry()], [Sig("chatter")], provider=1),  # actionable missed
    case([entry()], None, provider=2, path="slow_path"),  # raw review: missed
    case([entry()], [Sig("entry", {**GOOGL, "strike": "360"}, normal=False)]),  # inferred card
    case([Sig("chatter")], [Sig("chatter")], provider=4, path="feed_only"),
    case([Sig("watch")], [entry()], provider=4),  # false positive; contract cannot match
    case([], [Sig("chatter")], provider=5, path="feed_only"),  # a labeled non-signal
]


def test_counts_recall_precision_and_wrong_contracts() -> None:
    m = compute(CORPUS)
    assert (m["messages"], m["actionable"], m["non_actionable"], m["providers"]) == (9, 6, 3, 5)
    # labeled actionable signals: 6; answered with the same type: cases 0, 1, 2, 5 -> 4
    assert m["actionable_recall"] == pytest.approx(4 / 6)
    # answered actionable signals: cases 0, 1, 2, 5, 7 -> 5, of which 4 are right
    assert m["actionable_precision"] == pytest.approx(4 / 5)
    assert m["wrong_contracts"] == 1  # only case 2: case 5 is not a normal card, case 7 is chatter


def test_field_accuracy_is_over_normal_cards_only() -> None:
    m = compute(CORPUS[:3])
    # case 0: 5/5 fields, case 1: 4/5, case 2: 4/5 (strike differs)
    assert m["normal_cards"] == 3
    assert m["field_accuracy"] == pytest.approx(13 / 15)
    assert compute([CORPUS[0]])["field_accuracy"] == 1.0


def test_missing_and_extra_fields_are_errors() -> None:
    extra = case([entry()], [entry(stop_level="0.5")])
    assert compute([extra])["field_accuracy"] == pytest.approx(5 / 6)
    assert compute([extra])["wrong_contracts"] == 0
    missing = case([entry()], [Sig("entry", {"underlying": "GOOGL"})])
    m = compute([missing])
    assert m["field_accuracy"] == pytest.approx(1 / 5)
    assert m["wrong_contracts"] == 0  # incomplete is a review card, not a wrong contract


def test_numbers_compare_by_value() -> None:
    assert canon("357.50") == canon(357.5) == canon("357.5")
    assert compute([case([entry(strike=357.5)], [entry(strike="357.50")])])["field_accuracy"] == 1.0


def test_confusion_and_slow_path_share_and_latency() -> None:
    m = compute(CORPUS)
    assert m["confusion"]["entry"] == {"entry": 4, "chatter": 1, "none": 1}
    assert m["confusion"]["watch"] == {"entry": 1}
    assert m["confusion"]["none"] == {"chatter": 1}
    assert m["slow_path_share"] == pytest.approx(2 / 9)
    assert m["latency_ms"]["feed_only"] == {"n": 2, "p50": 10, "p95": 10}


def test_percentiles_are_nearest_rank() -> None:
    values = [float(v) for v in range(1, 101)]
    assert (percentile(values, 50), percentile(values, 95), percentile(values, 100)) == (
        50,
        95,
        100,
    )
    assert percentile([7.0], 95) == 7.0


def test_nothing_to_measure_gives_none_not_a_pass() -> None:
    m = compute([], cost_usd=1.25)
    assert m["field_accuracy"] is None and m["actionable_recall"] is None and m["cost_usd"] == 1.25
    assert not any(c.passed for c in gate_checks(m) if c.name != "wrong_contracts")


def test_gate_needs_size_and_perfect_quality() -> None:
    good = {
        "actionable": 500,
        "non_actionable": 2000,
        "providers": 5,
        "field_accuracy": 1.0,
        "wrong_contracts": 0,
        "actionable_recall": 0.98,
    }
    assert all(c.passed for c in gate_checks(good))
    for key, bad in (
        ("actionable", 499),
        ("non_actionable", 1999),
        ("providers", 4),
        ("field_accuracy", 0.999),
        ("wrong_contracts", 1),
        ("actionable_recall", 0.979),
    ):
        failed = [c.name for c in gate_checks({**good, key: bad}) if not c.passed]
        assert len(failed) == 1, key


def test_link_scores_review_rate_and_path_shares() -> None:
    """F076: wrong links are counted apart from ambiguous ones (a review is recoverable)."""
    entry = {0: Sig("trim")}
    cases = [
        Case(1, entry, entry, "assemble", 10.0, link="correct"),
        Case(1, entry, entry, "slow_path", 20.0, link="wrong"),
        Case(1, entry, entry, "fast", 1.0, link="ambiguous"),
        Case(1, entry, None, "slow_path", 30.0),
    ]
    m = compute(cases)
    assert (m["link_cases"], m["links_correct"], m["links_wrong"], m["links_ambiguous"]) == (
        3,
        1,
        1,
        1,
    )
    assert m["wrong_link_rate"] == pytest.approx(1 / 3)
    assert m["review_rate"] == 0.5  # the raw review and the ambiguous link
    assert m["path_shares"] == {"assemble": 0.25, "fast": 0.25, "slow_path": 0.5}
    assert m["latency_ms_all"]["n"] == 4
