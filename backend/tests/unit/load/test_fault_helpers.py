"""Default-run coverage of the fault-test helpers (F063 tests 4-6): the scripted messages route
and verify as the fault scenarios assume, the kill switch fires once, block windows."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.interpret.llm_extract.schema import LlmParsedSignals, to_parsed_signals
from app.interpret.spans import verify_literal_spans
from tests.llm_helpers import ctx
from tests.load import scenarios as sc
from tests.load import synthetic_day as sd
from tests.load.driver import RealtimeClock
from tests.load.fakes import llm_signal
from tests.load.test_failover import blocked_at
from tests.load.test_kill import FAST, RAW, SCENARIO, Kill, Killed
from tests.load.test_outage import AFFORDABLE, plan

T = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)


def parse(m: sd.DayMessage) -> tuple[object, list[str]]:
    book = {} if m.target is None else {str(uuid.uuid4()): f"{m.target} exp 2026-10-02"}
    out = LlmParsedSignals.model_validate({"signals": [llm_signal(m, book)]})
    context = ctx(m.text, book=[(uuid.UUID(k), v) for k, v in book.items()])
    return to_parsed_signals(out, context)


@pytest.mark.parametrize("kind", ["entry", "exit", "trim", "stop_set", "chatter"])
def test_story_messages_verify(kind: sd.Kind) -> None:
    m = sd.story_message(kind, sd.PROVIDER_BASE, sc.CONTRACTS[0], seed=3)
    _parsed, errors = parse(m)
    assert errors == []
    assert m.kind == kind and (m.target is None) == (kind == "chatter")


def test_kill_scenario_raw_message_fails_span_verification() -> None:
    """test_5's raw_review route: the LLM quotes a price the text does not contain."""
    parsed, errors = parse(FAST)
    assert errors == [] and not verify_literal_spans(parsed, FAST.text)  # type: ignore[arg-type]
    raw, raw_errors = parse(RAW)
    assert raw_errors or verify_literal_spans(raw, RAW.text)  # type: ignore[arg-type]
    assert len({m.provider for m in SCENARIO}) == len(SCENARIO)  # all run in parallel


def test_kill_fires_once_on_the_target_only() -> None:
    kill = Kill("decide_apply", "exit", text="a")
    kill.hit("decide_apply", "exit", "a")  # not armed yet
    kill.armed = True
    kill.hit("decide_apply", "enter", "a")
    kill.hit("decide_apply", "exit", "b")
    with pytest.raises(Killed):
        kill.hit("decide_apply", "exit", "a")
    assert kill.fired.is_set() and not kill.armed
    kill.hit("decide_apply", "exit", "a")  # disarmed: the restart runs the real code
    assert not issubclass(Killed, Exception)  # nothing in the app catches it


def test_outage_plan_is_stale_at_recovery() -> None:
    """Every outage message is older than stale_entry_s (30 s) when the backlog arrives, and
    every exit targets the provider's live position."""
    p = plan()
    assert len(p.live) == len(p.fresh) == sc.OUTAGE_PROVIDERS
    assert all(0 < t <= sc.OUTAGE_S - 30 for t, _ in p.outage)
    live_target = {m.provider: m.target for m in p.live}
    exits = [m for _, m in p.outage if m.kind == "exit"]
    assert len(exits) == sc.OUTAGE_PROVIDERS
    assert all(m.target == live_target[m.provider] for m in exits)
    assert AFFORDABLE and all(float(c[3]) * 100 <= 1000 for c in AFFORDABLE)  # 1 contract fits


def test_block_windows() -> None:
    windows = [(T, T + timedelta(seconds=1)), (T + timedelta(seconds=5), None)]
    assert blocked_at(windows, T) and not blocked_at(windows, T + timedelta(seconds=1))
    assert not blocked_at(windows, T - timedelta(seconds=1))
    assert blocked_at(windows, T + timedelta(hours=1))  # still blocked


def test_realtime_clock_advance() -> None:
    clock = RealtimeClock(T)
    clock.advance(timedelta(minutes=10))
    assert timedelta(minutes=10) <= clock.now() - T < timedelta(minutes=10, seconds=5)
