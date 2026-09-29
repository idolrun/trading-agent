"""Default-run coverage of the load-suite helpers (F063): generator, fakes' answers, percentile,
report rendering. The load tests themselves (tests/load, `-m load`) need PostgreSQL."""

import random
import uuid
from collections import Counter
from pathlib import Path

import pytest

from app.interpret.candidates import find_candidates
from app.interpret.jev.mapping import map_answers
from app.interpret.llm_extract.schema import LlmParsedSignals, to_parsed_signals
from app.interpret.route import route
from app.runtime_config.schema import RuntimeConfig
from tests.jev_helpers import CFG
from tests.llm_helpers import ctx
from tests.load import synthetic_day as sd
from tests.load.conftest import _selects_load
from tests.load.driver import RunResult, pct
from tests.load.fakes import jev_answers, llm_signal
from tests.load.report import OD19, Check, all_ok, latency_checks, render_section, upsert
from tests.load.scenarios import Lognormal

DAY = sd.generate(7, 400, 1200, 12)
BURSTS = [
    ("cross", sd.cross_burst(7, 50, 5.0, 10)),
    ("single", sd.single_burst(7, 5, 3.0)),
    ("tight", sd.single_burst(7, 5, 3.0, tight=True)),
]


def test_generator_is_deterministic() -> None:
    assert sd.generate(7, 400, 1200, 12) == DAY
    assert sd.generate(8, 400, 1200, 12) != DAY
    assert sd.single_burst(7, 5, 3.0) == BURSTS[1][1]


def test_day_shape() -> None:
    assert len(DAY) == 400
    assert [m.idx for m in DAY] == list(range(400))
    assert [m.offset_s for m in DAY] == sorted(m.offset_s for m in DAY)
    assert 0 <= DAY[0].offset_s and DAY[-1].offset_s <= 1200
    feed = sum(m.kind in ("chatter", "commentary") for m in DAY) / len(DAY)
    assert 0.55 <= feed <= 0.65
    assert {"entry", "add", "trim", "exit", "stop_move"} <= set(sd.mix(DAY))
    assert len({m.provider for m in DAY}) > 5
    replies = [m for m in DAY if m.reply_to is not None]
    assert replies
    for m in replies:
        parent = DAY[m.reply_to]  # type: ignore[index]
        assert parent.idx < m.idx and parent.provider == m.provider and parent.kind == "entry"


def test_bursts_shape() -> None:
    cross, single, tight = (b for _, b in BURSTS)
    assert len(cross) == 50 and max(m.offset_s for m in cross) <= 5.0
    assert Counter(m.provider for m in cross) == {sd.PROVIDER_BASE + i: 5 for i in range(10)}
    assert [m.idx for m in cross] == list(range(50))
    assert [m.offset_s for m in cross] == sorted(m.offset_s for m in cross)
    for m in cross:
        if m.reply_to is not None:
            assert cross[m.reply_to].provider == m.provider and m.reply_to < m.idx
    assert [m.kind for m in single] == ["entry", "stop_set", "tp_set", "add", "trim"]
    assert {m.provider for m in single} == {sd.PROVIDER_BASE}
    assert single[-1].offset_s == 3.0 and tight[-1].offset_s <= 3.0 * 0.15 + 0.001
    assert {m.path for m in single} == {"slow"}


def test_one_text_one_plan() -> None:
    """The fakes look a message up by its text: equal text must mean equal plan."""
    seen: dict[str, tuple[str, str]] = {}
    for m in [*DAY, *(m for _, b in BURSTS for m in b)]:
        plan = (m.kind, str(sorted(m.signal.items())))
        if m.path == "fast":
            assert seen.setdefault(m.text, plan) == plan
    assert sd.path_for("Bought spy 578c 10/2 @ 4.61", "entry") == sd.path_for(
        "Bought spy 578c 10/2 @ 4.61", "entry"
    )


def book_for(m: sd.DayMessage) -> dict[str, str]:
    return {} if m.target is None else {str(uuid.uuid4()): f"{m.target} exp 2026-10-02 entered 1"}


@pytest.mark.parametrize("m", [m for m in DAY if m.kind != "chatter"][:150] + BURSTS[1][1])
def test_llm_answers_verify_against_the_real_span_check(m: sd.DayMessage) -> None:
    """What the fake LLM returns passes the real conversion + literal-span verification."""
    book = book_for(m)
    out = LlmParsedSignals.model_validate({"signals": [llm_signal(m, book)]})
    context = ctx(m.text, book=[(uuid.UUID(k), v) for k, v in book.items()])
    parsed, errors = to_parsed_signals(out, context)
    assert errors == [] and parsed.signals[0].msg_type == m.signal["msg_type"]
    if m.target is not None:
        assert parsed.signals[0].link_hint.target != "ambiguous" or "links_to" not in m.signal


@pytest.mark.parametrize("m", DAY[:150] + [m for _, b in BURSTS for m in b])
def test_jev_answers_route_as_designed(m: sd.DayMessage) -> None:
    """Confident Jev answers assemble in code (fast); unsure ones go to the slow path."""
    cfg = RuntimeConfig(**CFG.model_dump())
    book = book_for(m)
    context = ctx(m.text, book=[(uuid.UUID(k), v) for k, v in book.items()])
    cands = find_candidates(m.text)
    fast = route(map_answers(jev_answers(m, cands, book, unsure=False), context, cands, cfg), cfg)
    slow = route(map_answers(jev_answers(m, cands, book, unsure=True), context, cands, cfg), cfg)
    assert fast.path == ("feed_only" if m.kind in ("chatter", "commentary") else "assemble"), (
        m.text,
        fast.reasons,
    )
    assert slow.path == "slow_path"


def test_percentile_and_lognormal() -> None:
    assert pct([], 95) == 0.0
    assert pct([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10 and pct([1, 2, 3, 4], 50) == 2
    rng = random.Random(1)  # noqa: S311
    lat = Lognormal(median_ms=900, p95_ms=1800, cap_ms=1950)
    xs = sorted(lat.sample(rng) for _ in range(4000))
    assert max(xs) <= 1.95 and 0.8 < xs[2000] < 1.0 and 1.5 < xs[3800] < 1.95


def result(**over: object) -> RunResult:
    base: dict[str, object] = dict(
        sent=10,
        accepted=10,
        wall_s=1.0,
        e2e_s=[0.5] * 9 + [1.0],
        e2e_by_path={},
        queue_wait_s={1: [0.1, 0.2]},
        insert_ms=[5.0] * 10,
        statuses={"done": 10},
        errors=[],
        unfinished=0,
        cards={"entry/pending": 3},
        pushed=3,
        interpreters={"jev": 10},
        model_outcomes={},
        jev_calls=10,
        llm_calls=0,
    )
    return RunResult(**{**base, **over})  # type: ignore[arg-type]


def test_latency_checks_pass_and_fail() -> None:
    assert all_ok(latency_checks(result(), queue_wait_asserted=True))
    slow = latency_checks(result(e2e_s=[9.0] * 10, insert_ms=[60.0] * 10), queue_wait_asserted=True)
    failed = {c.metric for c in slow if c.ok is False}
    assert failed == {"end-to-end p95", "end-to-end p99", "inbox insert p99"}
    waits = result(queue_wait_s={1: [2.0]})
    assert not all_ok(latency_checks(waits, queue_wait_asserted=True))
    assert all_ok(latency_checks(waits, queue_wait_asserted=False))  # informational there
    lost = latency_checks(result(unfinished=1, statuses={"failed": 1}), queue_wait_asserted=False)
    assert {c.metric for c in lost if c.ok is False} == {
        "failed / poisoned messages",
        "lost (not accepted or never finished)",
    }


def test_report_sections_are_upserted(tmp_path: Path) -> None:
    path = tmp_path / "docs" / "reviews" / "load-test.md"
    a = render_section("Test A", [Check("m", "1", "<= 2", True)])
    b = render_section("Test B", [Check("m", "3", "<= 2", False)], notes=(OD19,))
    upsert("a", a, path)
    upsert("b", b, path)
    upsert("a", a.replace("| 1 |", "| 9 |"), path)  # a re-run replaces, does not duplicate
    text = path.read_text()
    assert text.count("## Test A: PASS") == 1 and "| 9 |" in text and "| 1 |" not in text
    assert "## Test B: FAIL" in text and OD19 in text and text.startswith("# Load and fault")


def test_load_marker_selection() -> None:
    assert _selects_load("load") and _selects_load("load and not live")
    assert not _selects_load("not live") and not _selects_load("not live and not load")
