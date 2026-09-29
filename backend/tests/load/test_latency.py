"""TRD §12 load tests 1-3 (F063): latency under a replayed day, a cross-provider burst and a
single-provider slow-path burst. Run with `-m load` (see README.md); the report is written before
the assertions so a failing run still leaves its numbers in docs/reviews/load-<date>.md."""

from pathlib import Path

import pytest

from tests.load import scenarios as sc
from tests.load import synthetic_day as sd
from tests.load.driver import RunResult, open_run
from tests.load.report import (
    OD19,
    Check,
    all_ok,
    latency_checks,
    render_details,
    render_section,
    upsert,
)

pytestmark = [pytest.mark.load, pytest.mark.integration]

OUTCOMES: dict[str, bool] = {}  # tests 1 and 2 -> passed, for the OD-19 verdict of test 3


async def replay(
    tmp_path: Path, items: list[sd.DayMessage], *, speed: float = 1.0, force_slow: bool = False
) -> RunResult:
    async with open_run(tmp_path, sd.by_text(items), force_slow=force_slow) as run:
        return await run.run(items, speed=speed)


def failures(checks: list[Check]) -> str:
    return "; ".join(
        f"{c.metric} = {c.value} (needs {c.threshold})" for c in checks if c.ok is False
    )


@pytest.mark.parametrize("speed", sc.SPEEDS, ids=lambda s: f"{s:g}x")
async def test_1_day_replay(speed: float, tmp_path: Path, owner_engine: object) -> None:
    items = sd.generate(sc.SEED, sc.DAY_MESSAGES, sc.DAY_SPAN_S, sc.DAY_PROVIDERS)
    notes = [
        f"Synthetic day: {sc.DAY_MESSAGES} messages over {sc.DAY_SPAN_S:g} s "
        f"({sc.DAY_MESSAGES / sc.DAY_SPAN_S:.2f} msg/s on average; the PRD average is 0.1), "
        f"{sc.DAY_PROVIDERS} providers; mix {dict(sd.mix(items))}.",
    ]
    if speed <= 1:
        items = sd.take_slice(items, sc.SLICE_1X_S)
        notes.append(
            f"1x replays the first {sc.SLICE_1X_S:g} s of the day ({len(items)} messages, includes "
            "the busy open): a full-length 1x day is hours of wall time (LOAD_DAY_SPAN_S / "
            "LOAD_1X_SLICE_S raise it)."
        )
    res = await replay(tmp_path, items, speed=speed)
    checks = latency_checks(res, queue_wait_asserted=False)
    OUTCOMES[f"day-{speed:g}x"] = all_ok(checks)
    upsert(
        f"day-{speed:g}x",
        render_section(
            f"Test 1: day replay at {speed:g}x", checks, render_details(res), tuple(notes)
        ),
    )
    assert all_ok(checks), failures(checks)


async def test_2_cross_provider_burst(tmp_path: Path, owner_engine: object) -> None:
    items = sd.cross_burst(sc.SEED, sc.CROSS_MESSAGES, sc.CROSS_WINDOW_S, sc.CROSS_PROVIDERS)
    res = await replay(tmp_path, items)
    checks = latency_checks(res, queue_wait_asserted=True)
    OUTCOMES["cross"] = all_ok(checks)
    upsert(
        "cross-burst",
        render_section(
            f"Test 2: {sc.CROSS_MESSAGES} messages in {sc.CROSS_WINDOW_S:g} s across "
            f"{sc.CROSS_PROVIDERS} providers",
            checks,
            render_details(res),
            (f"Mix {dict(sd.mix(items))}; arrival is uniform over the window.",),
        ),
    )
    assert all_ok(checks), failures(checks)


@pytest.mark.parametrize("pack", [False, True], ids=["spread", "pack"])
async def test_3_single_provider_slow_burst(
    pack: bool, tmp_path: Path, owner_engine: object
) -> None:
    """5 messages inside 3 s from one provider, slow path forced. `spread`: evenly over the 3 s;
    `pack`: an entry with its stop and targets "posted together" (PRD §12), all inside the first
    0.45 s: the worst arrival the requirement allows. Both are asserted."""
    items = sd.single_burst(sc.SEED, sc.SINGLE_MESSAGES, sc.SINGLE_WINDOW_S, tight=pack)
    res = await replay(tmp_path, items, force_slow=True)
    checks = latency_checks(res, queue_wait_asserted=False)
    notes = [
        "Slow path forced: every message gets a Jev answer (with its latency) below t_field, so "
        "each goes to the LLM: the worst case in TRD §2.3 (Jev + LLM serial per provider).",
        f"Offsets (s): {[m.offset_s for m in items]}; kinds {[m.kind for m in items]}.",
    ]
    if not all_ok(checks):
        others = all(OUTCOMES.get(k) for k in (*(f"day-{s:g}x" for s in sc.SPEEDS), "cross"))
        notes.append(
            OD19
            if others
            else f"{OD19}, if tests 1 and 2 pass (they did not all run and pass in this session)"
        )
    variant = "pack (posted together)" if pack else "spread over the window"
    upsert(
        f"single-burst-{'pack' if pack else 'spread'}",
        render_section(
            f"Test 3, {variant}: {sc.SINGLE_MESSAGES} messages in {sc.SINGLE_WINDOW_S:g} s "
            "from one provider, slow path forced",
            checks,
            render_details(res),
            tuple(notes),
        ),
    )
    assert all_ok(checks), failures(checks)
