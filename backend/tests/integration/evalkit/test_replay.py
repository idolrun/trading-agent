"""F048: replay over labeled events with fixture interpretations: same inputs, same outputs,
no side effects, no broker; the gate and the sweep record `eval_runs`; import never queues."""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.broker.fake import FakeBroker
from app.cards.models import Card
from app.evalkit import gate
from app.evalkit.gate import run_gate, run_sweep
from app.evalkit.import_export import import_history
from app.evalkit.labels import latest_labels
from app.evalkit.models import EvalRun, EvalRunKind, Split
from app.evalkit.replay import Replayer, measure, parse_model_ref, pin_extract
from app.execution.models import Order
from app.inbox.models import InboxItem, MessageEvent, StageTiming
from app.interpret.models import Signal
from app.interpret.signal import ParsedSignal
from app.llm.validation import validate_llm_config
from app.runtime_config.schema import ChainEntry, LlmConfig, LlmRoles, RoleConfig, RuntimeConfig
from app.system.models import FlagName, SystemFlag
from tests.integration.evalkit.conftest import (
    CHATTER_LABEL,
    GOOGL_LABEL,
    GOOGL_TEXT,
    Corpus,
    Spy,
    at,
    count,
    day_in,
    googl,
    replay_deps,
)
from tests.integration.pipeline.harness import Factory, Harness
from tests.messages import PROVIDER_A, PROVIDER_B, create

REF = "deepseek:ds-test:extract@v1"
TEST_DAY, DEV_DAY = day_in(Split.TEST), day_in(Split.DEV)
CHATTER = ParsedSignal(msg_type="chatter")


async def seed(corpus: Corpus, day: Any, n: int = 1) -> None:
    for i in range(n):
        await corpus.add(GOOGL_TEXT, [googl()], [GOOGL_LABEL], when=at(day, i))
    await corpus.add("gm all", [CHATTER], [CHATTER_LABEL], when=at(day, 30), author=PROVIDER_B)


async def test_replay_is_deterministic_and_writes_nothing(
    h: Harness, corpus: Corpus, db_session: AsyncSession
) -> None:
    await seed(corpus, TEST_DAY, 2)
    await corpus.add(  # a misread: strike 360 on a normal card
        GOOGL_TEXT, [googl("360")], [GOOGL_LABEL], when=at(TEST_DAY, 10), author=PROVIDER_B
    )
    deps = replay_deps(h)
    async with h.factory() as db:
        events = await latest_labels(db)
    before = [await count(db_session, m) for m in (Card, Order, StageTiming, InboxItem, Signal)]
    flags_before = (await db_session.scalars(select(SystemFlag.active))).all()

    first = await Replayer(deps).run(events, Split.TEST)
    second = await Replayer(deps).run(events, Split.TEST)

    assert [replace(c, latency_ms=0) for c in first] == [replace(c, latency_ms=0) for c in second]
    assert len(first) == 4
    metrics = await measure(deps, events, Split.TEST)
    assert metrics["wrong_contracts"] == 1 and metrics["actionable_recall"] == 1.0
    assert metrics["latency_ms"]["fixture"]["n"] == 4

    after = [await count(db_session, m) for m in (Card, Order, StageTiming, InboxItem, Signal)]
    assert after == before == [0, 0, 0, 0, 0]  # no cards, orders, timings, queue rows or signals
    assert (await db_session.scalars(select(SystemFlag.active))).all() == flags_before


async def test_a_failed_slow_path_is_a_miss_and_never_flags_the_system(
    h: Harness, corpus: Corpus, db_session: AsyncSession
) -> None:
    # " here" keeps the entry off the F070 fast path: only a model could read it
    await corpus.add(GOOGL_TEXT + " here", [googl()], [GOOGL_LABEL], when=at(TEST_DAY, 0))
    await corpus.add("gm all", [CHATTER], [CHATTER_LABEL], when=at(TEST_DAY, 30), author=PROVIDER_B)
    deps = replace(replay_deps(h), fixture=None)  # no Jev, and an LLM chain that is not configured
    async with h.factory() as db:
        events = await latest_labels(db)
    metrics = await measure(deps, events, Split.TEST)
    assert metrics["actionable_recall"] == 0.0 and metrics["slow_path_share"] == 1.0
    assert metrics["confusion"]["entry"] == {"none": 1}
    flag = await db_session.get(SystemFlag, FlagName.PARSER_DEGRADED)
    assert flag is not None and not flag.active  # live would have set it (FR-9)
    assert await count(db_session, Card) == 0


async def test_replay_never_reaches_a_broker(h: Harness, corpus: Corpus) -> None:
    await seed(corpus, TEST_DAY)
    broker = FakeBroker(h.clock)
    deps = replay_deps(h)
    async with h.factory() as db:
        events = await latest_labels(db)
    await Replayer(deps).run(events, Split.TEST)
    assert broker.calls == [] and deps.approvals is None
    for wired in ("approvals", "relink", "notes", "corrections"):
        with pytest.raises(AssertionError, match="never reaches a broker"):
            Replayer(replace(deps, **{wired: object()}))


async def test_book_is_rebuilt_from_earlier_labels_in_post_time_order(
    h: Harness, corpus: Corpus
) -> None:
    exit_label = {"msg_type": "full_exit", "fields": {"underlying": "GOOGL"}}
    exit_signal = ParsedSignal(msg_type="full_exit")
    await corpus.add(GOOGL_TEXT, [googl()], [GOOGL_LABEL], when=at(TEST_DAY, 0))
    await corpus.add("closed", [exit_signal], [exit_label], when=at(TEST_DAY, 1))
    await corpus.add("gm", [CHATTER], [CHATTER_LABEL], when=at(TEST_DAY, 2))
    other = await corpus.add(
        "gm", [CHATTER], [CHATTER_LABEL], when=at(TEST_DAY, 3), author=PROVIDER_B
    )
    spy = Spy(h.fixtures)
    async with h.factory() as db:
        events = await latest_labels(db)
    await Replayer(replay_deps(h, spy)).run(events, Split.TEST)
    books = [book for _, book in spy.seen]
    assert books[0] == []  # nothing before the first message
    assert len(books[1]) == 1 and books[1][0].startswith(
        "GOOGL 357.5C exp 2026-10-02, entered 0.71"
    )
    assert books[2] == []  # closed by the labeled exit
    assert spy.seen[3][0] == str(other.key.message_id) and books[3] == []  # per provider


async def test_only_the_requested_split_is_interpreted(h: Harness, corpus: Corpus) -> None:
    await seed(corpus, DEV_DAY)
    await seed(corpus, TEST_DAY, 2)
    spy = Spy(h.fixtures)
    async with h.factory() as db:
        events = await latest_labels(db)
    await Replayer(replay_deps(h, spy)).run(events, Split.DEV)
    dev_ids = {str(e.envelope.key.message_id) for e in events if e.split == Split.DEV}
    assert {i for i, _ in spy.seen} == dev_ids and len(spy.seen) == 2


async def test_gate_fails_on_a_too_small_dataset_and_records_the_run(
    h: Harness, corpus: Corpus, db_session: AsyncSession
) -> None:
    await seed(corpus, TEST_DAY, 3)
    result = await run_gate(replay_deps(h), REF)
    assert not result.passed and not result.refresh_test_set
    failed = {c.name for c in result.checks if not c.passed}
    assert failed == {"actionable_messages", "non_actionable_messages", "providers"}
    run = await db_session.get(EvalRun, result.run_id)
    assert run is not None
    assert (run.kind, run.model_ref, run.split, run.gate_passed) == (
        EvalRunKind.REPLAY_GATE,
        REF,
        Split.TEST,
        False,
    )
    assert run.metrics["actionable"] == 3 and run.metrics["field_accuracy"] == 1.0
    assert [c["name"] for c in run.metrics["checks"]][:3] == [
        "actionable_messages",
        "non_actionable_messages",
        "providers",
    ]


async def test_a_passing_gate_run_is_accepted_by_config_validation(
    h: Harness, corpus: Corpus, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate, "MIN_ACTIONABLE", 3)
    monkeypatch.setattr(gate, "MIN_NON_ACTIONABLE", 1)
    monkeypatch.setattr(gate, "MIN_PROVIDERS", 2)
    await seed(corpus, TEST_DAY, 3)
    result = await run_gate(replay_deps(h), REF)
    assert result.passed, result.checks

    async def check(entry: ChainEntry) -> list[str]:
        cfg = LlmConfig(roles=LlmRoles(extract=RoleConfig(chain=[entry])))
        errors = await validate_llm_config(cfg, db_session)
        return [e for e in errors if "no passing gate run" in e]

    entry = parse_model_ref(REF).model_copy(update={"gate_run": result.run_id})
    assert await check(entry) == []
    other = entry.model_copy(update={"prompt": "extract@v2"})  # a different prompt: not gated
    assert len(await check(other)) == 1


async def test_three_failed_gate_runs_on_one_test_set_ask_for_a_refresh(
    h: Harness, corpus: Corpus
) -> None:
    await seed(corpus, TEST_DAY)
    deps = replay_deps(h)
    flags = [(await run_gate(deps, REF)).refresh_test_set for _ in range(3)]
    assert flags == [False, False, True]
    await seed(corpus, day_in(Split.TEST, TEST_DAY + timedelta(days=1)))  # refreshed test set
    assert not (await run_gate(deps, REF)).refresh_test_set


async def test_sweep_replays_the_dev_split_only(
    h: Harness, corpus: Corpus, db_session: AsyncSession
) -> None:
    await seed(corpus, DEV_DAY)
    await seed(corpus, TEST_DAY, 2)
    spy = Spy(h.fixtures)
    run = await run_sweep(replay_deps(h, spy), REF)
    async with h.factory() as db:
        events = await latest_labels(db)
    dev_ids = {str(e.envelope.key.message_id) for e in events if e.split == Split.DEV}
    assert {i for i, _ in spy.seen} == dev_ids  # the test split is never touched
    stored = await db_session.get(EvalRun, run.eval_run_id)
    assert stored is not None and stored.kind == EvalRunKind.THRESHOLD_SWEEP
    assert stored.split == Split.DEV and stored.gate_passed is None
    assert len(stored.metrics["points"]) == 9
    assert stored.thresholds["best"] is not None and "t_link" in stored.thresholds


def test_pin_extract_leaves_only_the_model_under_test() -> None:
    pinned = pin_extract(RuntimeConfig(), parse_model_ref("openai_compat:oss:latest:extract@v1"))
    [entry] = pinned.llm.roles.extract.chain
    assert (entry.provider, entry.model, entry.prompt) == (
        "openai_compat",
        "oss:latest",
        "extract@v1",
    )
    assert entry.model_ref == "openai_compat:oss:latest:extract@v1"


async def test_import_is_idempotent_backfilled_and_never_queued(
    factory: Factory, db_session: AsyncSession
) -> None:
    now = datetime(2026, 9, 29, 12, tzinfo=at(TEST_DAY).tzinfo)
    payloads = [
        create(at(DEV_DAY, 1), "bought spy 770c .95", author=PROVIDER_A),
        create(at(DEV_DAY, 2), "gm", author=PROVIDER_B),
        {"type": "message.create", "message": {"id": "nope"}},
    ]
    first = await import_history(factory, payloads, now)
    again = await import_history(factory, payloads, now)
    assert (first.inserted, first.duplicates, first.unmappable) == (2, 0, 1)
    assert (again.inserted, again.duplicates, again.unmappable) == (0, 2, 1)
    rows = (await db_session.scalars(select(MessageEvent))).all()
    assert len(rows) == 2 and all(r.backfilled for r in rows)
    assert await count(db_session, InboxItem) == 0 and await count(db_session, StageTiming) == 0
    ids = (await db_session.scalars(select(col(MessageEvent.provider_id)))).all()
    assert sorted(ids) == [PROVIDER_A, PROVIDER_B]


async def test_link_scores_count_a_wrong_link_apart_from_an_ambiguous_one(
    h: Harness, corpus: Corpus
) -> None:
    """F076/F082: links are scored the way live code decides them (production resolver +
    `resolve_link` over the label ledger). GOOGL and TSLA open:
    - "trim half", the model picks GOOGL: no code evidence -> ambiguous (F077);
    - "trim GOOGL": the stated contract -> correct;
    - "out TSLA": the stated contract -> correct (TSLA closes);
    - "trim half" labeled as TSLA's: code links the sole open GOOGL -> wrong."""
    from app.evalkit.replay import position_id_of
    from tests.integration.pipeline.harness import follow_up, option_entry

    googl_env = await corpus.add(GOOGL_TEXT, [googl()], [GOOGL_LABEL], when=at(TEST_DAY, 0))
    tsla_label = {**GOOGL_LABEL, "fields": {**GOOGL_LABEL["fields"], "underlying": "TSLA"}}
    tsla_env = await corpus.add(
        "Bought TSLA 400C 10/2 @ 3.10",
        [option_entry("TSLA", "400", "400C", "10/2", "3.10")],
        [tsla_label],
        when=at(TEST_DAY, 1),
    )
    googl_id = position_id_of(f"{googl_env.key.message_id}.create.0")
    tsla_id = position_id_of(f"{tsla_env.key.message_id}.create.0")
    for minute, text, pick, msg_type, labeled in (
        (5, "trim half", googl_id, "trim", googl_id),
        (6, "trim GOOGL", googl_id, "trim", googl_id),
        (7, "out TSLA", tsla_id, "full_exit", tsla_id),
        (8, "trim half", googl_id, "trim", tsla_id),
    ):
        await corpus.add(
            text,
            [follow_up(msg_type, pick, exit_fraction="half")],
            [{"msg_type": msg_type, "fields": {}}],
            when=at(TEST_DAY, minute),
            link={"idx": 0, "provider_position_id": str(labeled)},
        )
    async with h.factory() as db:
        events = await latest_labels(db)
    metrics = await measure(replay_deps(h), events, Split.TEST)
    assert (metrics["links_correct"], metrics["links_wrong"], metrics["links_ambiguous"]) == (
        2,
        1,
        1,
    )
    assert metrics["wrong_link_rate"] == pytest.approx(1 / 4)


async def test_replay_resolves_links_like_live(h: Harness, corpus: Corpus) -> None:
    """F082 parity: the replay's resolution is the production resolver over the label ledger,
    so the fast path settles a sole-position follow-up and a reply by code, as live would, and
    a follow-up between two positions with no evidence stays a review."""
    from app.evalkit.replay import position_id_of
    from tests.integration.pipeline.harness import option_entry

    googl_env = await corpus.add(GOOGL_TEXT, [googl()], [GOOGL_LABEL], when=at(TEST_DAY, 0))
    googl_id = position_id_of(f"{googl_env.key.message_id}.create.0")
    trim = {"msg_type": "trim", "fields": {"exit_fraction": "half"}}
    link = {"idx": 0, "provider_position_id": str(googl_id)}
    await corpus.add("trim half", [], [trim], when=at(TEST_DAY, 1), link=link)  # sole: code
    tsla_label = {**GOOGL_LABEL, "fields": {**GOOGL_LABEL["fields"], "underlying": "TSLA"}}
    await corpus.add(
        "Bought TSLA 400C 10/2 @ 3.10",
        [option_entry("TSLA", "400", "400C", "10/2", "3.10")],
        [tsla_label],
        when=at(TEST_DAY, 2),
    )
    await corpus.add(  # two open, a reply to the GOOGL entry: code
        "trim half", [], [trim], when=at(TEST_DAY, 3), link=link, reply_to=googl_env
    )
    await corpus.add("trim half", [], [trim], when=at(TEST_DAY, 4), link=link)  # two, no evidence
    deps = replace(replay_deps(h), fixture=None)  # fast path, no Jev, no LLM: what code alone does
    async with h.factory() as db:
        events = await latest_labels(db)
    cases = await Replayer(deps).run(events, Split.TEST)
    assert [(c.path, c.link, c.outcome) for c in cases] == [
        ("fast", None, "fast_success"),
        ("fast", "correct", "fast_success"),
        ("fast", None, "fast_success"),
        ("fast", "correct", "fast_success"),
        ("slow_path", "ambiguous", "review"),
    ]


async def test_a_labeled_instruction_ended_feed_only_is_counted(h: Harness, corpus: Corpus) -> None:
    """F082: `false_feed_only` = a labeled actionable message the interpreter silenced."""
    exit_label = {"msg_type": "full_exit", "fields": {"exit_fraction": "all"}}
    await corpus.add("gotta go here", [CHATTER], [exit_label], when=at(TEST_DAY, 0))
    await corpus.add("gm", [CHATTER], [CHATTER_LABEL], when=at(TEST_DAY, 1))
    async with h.factory() as db:
        events = await latest_labels(db)
    cases = await Replayer(replay_deps(h)).run(events, Split.TEST)
    from app.evalkit.metrics import compute

    metrics = compute([replace(c, path="feed_only") for c in cases])
    assert (metrics["false_feed_only"], metrics["false_feed_only_rate"]) == (1, 1.0)


async def test_import_labels_expands_open_links_and_is_idempotent(
    h: Harness, corpus: Corpus, db_session: AsyncSession
) -> None:
    """F082: a label file names a position by the message that opened it."""
    from app.evalkit.import_export import import_labels
    from app.evalkit.labels import event_key
    from app.evalkit.models import EvalLabel
    from app.evalkit.replay import position_id_of
    from app.inbox import repo as inbox
    from app.ingest.adapter import to_envelope

    entry = to_envelope(create(at(TEST_DAY, 0), GOOGL_TEXT), at(TEST_DAY, 0))
    trim = to_envelope(create(at(TEST_DAY, 1), "trim half"), at(TEST_DAY, 1))
    async with h.factory() as db:
        for env in (entry, trim):
            await inbox.store_event(db, env)
        await db.commit()
    doc = {
        "source": "assistant",
        "labels": {
            event_key(entry.key): {"signals": [{"idx": 0, **GOOGL_LABEL}]},
            event_key(trim.key): {
                "signals": [{"idx": 0, "msg_type": "trim", "fields": {"exit_fraction": "half"}}],
                "link": {"idx": 0, "providerPositionId": f"open:{event_key(entry.key)}"},
            },
            "1.create.0": {"signals": []},  # no such event
            "x": {"signals": []},
        },
    }
    result = await import_labels(h.factory, doc)
    assert (result.read, result.inserted, result.unchanged, len(result.invalid)) == (4, 2, 0, 2)
    rows = (await db_session.scalars(select(EvalLabel).order_by(col(EvalLabel.labeled_at)))).all()
    assert {r.source for r in rows} == {"assistant"}
    linked = next(r for r in rows if r.message_id == trim.key.message_id)
    expected = str(position_id_of(event_key(entry.key)))
    assert linked.label["link"]["provider_position_id"] == expected
    again = await import_labels(h.factory, doc)
    assert (again.inserted, again.unchanged) == (0, 2)


async def test_an_add_whose_entry_predates_the_corpus_opens_the_position(
    h: Harness, corpus: Corpus
) -> None:
    """ "NOW 10/16 145 calls … added $3.80": the label is an add, its entry was never posted;
    the later "cut NOW swing" links to it and is scored against it."""
    from app.evalkit.replay import position_id_of
    from tests.integration.pipeline.harness import follow_up

    add = {
        "msg_type": "add",
        "instrument": "option",
        "fields": {"underlying": "NOW", "strike": "145", "right": "call", "entry_price": "3.80"},
    }
    added = await corpus.add("added NOW 145c $3.80", [], [add], when=at(TEST_DAY, 0))
    now_id = position_id_of(f"{added.key.message_id}.create.0")
    await corpus.add(
        "cut NOW swing",
        [follow_up("full_exit", now_id, exit_fraction="all")],
        [{"msg_type": "full_exit", "fields": {"exit_fraction": "all"}}],
        when=at(TEST_DAY, 1),
        link={"idx": 0, "provider_position_id": str(now_id)},
    )
    async with h.factory() as db:
        events = await latest_labels(db)
    metrics = await measure(replay_deps(h), events, Split.TEST)
    assert (metrics["links_correct"], metrics["links_wrong"]) == (1, 0)  # the sole position
