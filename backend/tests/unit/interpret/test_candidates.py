"""F016: candidate finders."""

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.interpret.candidates import Candidates, find_candidates


def texts(c: Candidates) -> dict[str, list[tuple[str, int]]]:
    return {
        "t": [(x.text, x.start) for x in c.tickers],
        "n": [(x.text, x.start) for x in c.numbers],
        "e": [(x.text, x.start) for x in c.expiry_tokens],
        "f": [(x.text, x.start) for x in c.fraction_words],
    }


def test_trd_example() -> None:
    # TRD §4.2 (the doc's offsets 22/26 were off by one; these are the real ones).
    c = find_candidates("spy 770c .95 lotto, sl 769 spy")
    assert texts(c) == {
        "t": [("spy", 0), ("spy", 27)],
        "n": [("770c", 4), (".95", 9), ("769", 23)],
        "e": [],
        "f": [],
    }
    assert [x.id for x in c.tickers] == ["t1", "t2"]
    assert [x.id for x in c.numbers] == ["n1", "n2", "n3"]


def test_strike_without_ticker() -> None:
    # FR-14: "7775c" is a number, never a ticker; the underlying is missing.
    assert texts(find_candidates("7775c 0dte")) == {
        "t": [],
        "n": [("7775c", 0)],
        "e": [("0dte", 6)],
        "f": [],
    }


@pytest.mark.parametrize(
    ("text", "tickers", "numbers", "expiry", "fractions"),
    [
        ("Bought GOOGL 357.5C @ 1.67", ["GOOGL"], ["357.5C", "1.67"], [], []),
        ("Take half off @ 2.10", [], ["2.10"], [], ["half"]),
        ("140/con", [], ["140/con"], [], []),
        ("3.2", [], ["3.2"], [], []),
        ("SPX 7000p 10/16", ["SPX"], ["7000p"], ["10/16"], []),
        ("$TSLA 400C oct 16 exp fri", ["$TSLA"], ["400C"], ["oct 16", "fri"], []),
        ("trim 50% here, 1dte", [], ["50%"], ["1dte"], ["50%"]),
        ("sold 1/3 at 2.5k", [], ["2.5k"], ["1/3"], ["1/3"]),  # 1/3: date-or-fraction
        ("adding 3x meta 712.5p 10/16/26", ["meta"], ["3", "712.5p"], ["10/16/26"], []),
        ("$1,250 target mu", ["mu"], ["1,250"], [], []),  # 6+ letter words are not tickers
    ],
)
def test_examples(
    text: str, tickers: list[str], numbers: list[str], expiry: list[str], fractions: list[str]
) -> None:
    got = texts(find_candidates(text))
    assert [t for t, _ in got["t"]] == tickers
    assert [t for t, _ in got["n"]] == numbers
    assert [t for t, _ in got["e"]] == expiry
    assert [t for t, _ in got["f"]] == fractions


def test_numbers_inside_expiry_tokens_are_not_numbers() -> None:
    c = find_candidates("GOOGL 355c 10/16 0dte")
    assert [x.text for x in c.numbers] == ["355c"]


def test_every_number_in_a_corpus_is_found() -> None:
    corpus = [
        "Bought TSLA 400C @ 2.10",
        "Stop 1.50",
        "tp1 2.4 tp2 3.1 runners",
        "spx 5800p .80 lotto",
        "sl 769 on spy",
        "added 2 more at 1.05",
        "entry 140/con 260 3.2 .95",
    ]
    for text in corpus:
        found = "".join(x.text for x in find_candidates(text).numbers)
        for digits in re.findall(r"\d+", text):
            assert digits in found, (text, digits)


def test_deterministic() -> None:
    text = "spy 770c .95 lotto, sl 769 spy fri half"
    assert find_candidates(text) == find_candidates(text)


@given(st.text(max_size=120))
def test_offsets_are_exact(text: str) -> None:
    c = find_candidates(text)
    for group in (c.tickers, c.numbers, c.expiry_tokens, c.fraction_words):
        for cand in group:
            assert text[cand.start : cand.end] == cand.text


TSLA = (
    "@everyone\n\n$TSLA 400C 10/16/26\n\nEntry: 2.10\nSL: 1.50\nTP1: 3.00\nTP2: 4.00\n\n"
    "Small size. Let's get it!"
)


def test_tp_labels_are_not_numbers() -> None:
    """The 1 of "TP1" and the 2 of "TP2" used to be candidates the model was never sure about,
    which sent every Entry / SL / TP1 / TP2 signal to review."""
    numbers = [x.text for x in find_candidates(TSLA).numbers]
    assert numbers == ["400C", "2.10", "1.50", "3.00", "4.00"]
    assert [x.text for x in find_candidates("tp1 3 tp2 4 T3 5").numbers] == ["3", "4", "5"]
    assert [x.text for x in find_candidates("sl1.50 spy 765p").numbers] == ["1.50", "765p"]


# --- F090: cleaned candidates -----------------------------------------------------------------


def cleaned(text: str) -> Candidates:
    from app.interpret.candidates import clean_candidates

    return clean_candidates(find_candidates(text), text)


def test_tickers_are_not_filtered_by_a_symbol_list() -> None:
    """Replay (F090): filtering word candidates cost feed-only settlements, gained nothing."""
    text = "s while can t spy 770c"
    assert cleaned(text).tickers == find_candidates(text).tickers


def test_mentions_urls_and_times_are_dropped() -> None:
    text = "<@123456789012> spy 770c .95 https://x.com/a/1234567 at 10:30am"
    c = cleaned(text)
    assert [n.text for n in c.numbers] == ["770c", ".95"]
    assert [t.text for t in c.tickers] == ["spy"]  # "at" is a stop word; https/x/com dropped


def test_numbers_past_the_cap_are_counted_not_asked() -> None:
    from app.interpret.candidates import MAX_NUMBERS

    text = " ".join(f"{i}.5" for i in range(MAX_NUMBERS + 3))
    c = cleaned(text)
    assert len(c.numbers) == MAX_NUMBERS and c.numbers_dropped == 3
    assert [n.id for n in c.numbers] == [n.id for n in find_candidates(text).numbers][:MAX_NUMBERS]
