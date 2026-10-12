"""TRD §12 fault test 5 (F063, AC-7): kill -9 in every node of the message graph and of the
approval graph, then restart: no duplicate card, no duplicate order, every message processed.

The kill is in-process: `Killed` (a BaseException, so nothing in the app handles it) is raised
when the target message enters a node (`enter`: nothing of the node ran) or after the node's
work returned (`exit`: its commits are in, its result never reached the checkpoint or the next
node), plus once inside the broker submit (`submit`: the broker accepted the order, the ack was
never stored). The stack is then torn down with its work in flight (every other worker is
cancelled wherever it is: also a kill) and a fresh stack starts on the same database,
checkpointer, broker and clock through the real `startup()` sequence (TRD §9).

Message nodes are wrapped on the class (`_Nodes`) before the graph is compiled; the approval
graph's nodes call `interrupt()` (await_approval) and the service's `revalidate` / `execute`,
which are wrapped on the module / instance. No app code is changed. `fixture_interpret` is left
out: it is the dev/test stand-in for Jev + LLM and never runs in production.
"""

import asyncio
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from langgraph.types import interrupt as real_interrupt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.broker.port import BrokerOrder, OrderRequest
from app.broker.simulated import SimulatedBroker
from app.cards.edits import Edits
from app.cards.models import Card, CardAction, CardEvent, CardState
from app.execution.models import Order
from app.graph.approval_graph import thread_config
from app.graph.message_graph import _Nodes
from app.interpret.models import Signal
from app.marketdata.fake import FakeMarketData
from app.runtime_config.schema import TradingMode
from app.system.models import FlagName
from tests.integration.pipeline.harness import MARKET, T0
from tests.integration.schema.helpers import add_session
from tests.load import synthetic_day as sd
from tests.load.driver import LoadRun, RealtimeClock, Sent, open_run
from tests.load.report import Check, all_ok, info, render_section, upsert
from tests.load.scenarios import CONTRACTS

pytestmark = [pytest.mark.load, pytest.mark.integration]

MESSAGE_NODES = (
    "load_context",
    "find_candidates",
    "jev_interpret",
    "llm_extract",
    "decide_apply",
    "raw_review",
    "notify",
)
APPROVAL_NODES = ("await_approval", "revalidate", "execute")
P = sd.PROVIDER_BASE + 500  # clear of the latency tests' providers

FAST = sd.story_message("entry", P, CONTRACTS[0], path="fast", seed=1)  # SPY 577c @ 5.04
SLOW = sd.story_message("entry", P + 1, CONTRACTS[3], path="slow", seed=2)  # QQQ 493c
_META = sd.story_message("entry", P + 2, CONTRACTS[7], path="slow", seed=3)
# The LLM quotes a price that is not in the text: literal-span verification fails -> raw review
RAW = replace(_META, signal={**_META.signal, "entry_price": {"raw": "99.99"}})
# F070: "Bought SPY 577c 10/2 @ 5.04" alone is settled by the deterministic fast path, which never
# reaches the model nodes these cases crash; " here" keeps them on the Jev / LLM path.
FAST, SLOW, RAW = (replace(m, text=f"{m.text} here") for m in (FAST, SLOW, RAW))
CHAT = sd.story_message("chatter", P + 3, CONTRACTS[0], seed=4)
SCENARIO = (FAST, SLOW, RAW, CHAT)
CARDS = {FAST.text: 1, SLOW.text: 1, RAW.text: 1, CHAT.text: 0}  # cards per message
TARGET = {"llm_extract": SLOW, "raw_review": RAW}  # the rest: the fast entry
OUTCOMES: dict[str, dict[str, str]] = {}  # case -> row, rendered into one report section


class Killed(BaseException):
    """The process dies: not an Exception, so nothing in the app handles it."""


@dataclass
class Kill:
    node: str
    phase: str  # enter | exit | submit
    text: str | None = None  # only this message's run is killed (message graph)
    armed: bool = False
    fired: asyncio.Event = field(default_factory=asyncio.Event)

    def hit(self, node: str, phase: str, text: str | None = None) -> None:
        if not self.armed or (node, phase) != (self.node, self.phase):
            return
        if self.text is not None and text != self.text:
            return
        self.armed = False
        self.fired.set()
        raise Killed(f"{node}:{phase}")


def _text(state: Any) -> str | None:
    return state["processing"]["envelope"]["text"]  # type: ignore[no-any-return]


def wrap_message_node(name: str, fn: Callable[..., Any], kill: Kill) -> Callable[..., Any]:
    async def node(self: _Nodes, state: Any) -> Any:
        kill.hit(name, "enter", _text(state))
        out = await fn(self, state)
        kill.hit(name, "exit", _text(state))
        return out

    return node


class KillingBroker(SimulatedBroker):
    """Accepts the order, then the process dies before the ack is stored (AC-7)."""

    kill: Kill | None = None

    async def submit(self, req: OrderRequest) -> BrokerOrder:
        order = await super().submit(req)
        if self.kill is not None:
            self.kill.hit("execute", "submit")
        return order


def install_approval_kill(run: LoadRun, kill: Kill, monkeypatch: pytest.MonkeyPatch) -> None:

    def interrupt(value: Any) -> Any:
        kill.hit("await_approval", "enter")
        resumed = real_interrupt(value)  # raises GraphInterrupt until a resume value exists
        kill.hit("await_approval", "exit")
        return resumed

    monkeypatch.setattr("app.graph.approval_graph.interrupt", interrupt)
    for name in ("revalidate", "execute"):
        real = getattr(run.approvals, name)

        async def step(card_id: uuid.UUID, _real: Any = real, _name: str = name) -> Any:
            kill.hit(_name, "enter")
            out = await _real(card_id)
            kill.hit(_name, "exit")
            return out

        monkeypatch.setattr(run.approvals, name, step)


async def send(run: LoadRun, items: tuple[sd.DayMessage, ...]) -> list[Sent]:
    ids: dict[int, int] = {}
    sent = [run.envelope(m, ids) for m in items]
    await asyncio.gather(*(run.accept(s) for s in sent))
    assert all(s.accepted for s in sent)
    return sent


@dataclass
class Effects:
    statuses: dict[int, str]
    cards_by_message: dict[int, int]
    duplicate_cards: int  # cards beyond the first per (signal, action)
    card_states: dict[str, int]
    orders: list[Order]
    submits: Counter[str]  # broker submit calls per client order id
    orphan_broker_orders: int  # at the broker, unknown to the orders table


async def effects(run: LoadRun, sent: list[Sent]) -> Effects:
    ids = [s.message_id for s in sent]
    async with run.factory() as db:
        cards = list((await db.scalars(select(Card))).all())
        orders = list((await db.scalars(select(Order))).all())
        signal_message = dict(
            (await db.execute(select(col(Signal.signal_id), col(Signal.message_id)))).tuples().all()
        )
    per_key = Counter((c.signal_id, c.action) for c in cards if c.signal_id is not None)
    by_message = Counter(signal_message.get(c.signal_id) for c in cards if c.signal_id)
    submits = Counter(req.client_order_id for call, req in run.broker.calls if call == "submit")
    known = {o.client_order_id for o in orders}
    return Effects(
        statuses=await run.inbox_statuses(ids),
        cards_by_message={m: by_message.get(m, 0) for m in ids},
        duplicate_cards=sum(n - 1 for n in per_key.values()),
        card_states=dict(Counter(f"{c.action}/{c.state}" for c in cards)),
        orders=orders,
        submits=submits,
        orphan_broker_orders=len(set(submits) - known),
    )


def checks(case: str, kill: Kill, sent: list[Sent], fx: Effects) -> list[Check]:
    expected = {s.message_id: CARDS[s.item.text] for s in sent}
    wrong_cards = {m: n for m, n in fx.cards_by_message.items() if n != expected[m]}
    unprocessed = {m: s for m, s in fx.statuses.items() if s not in ("done", "stale")}
    orders_per_card = Counter(o.card_id for o in fx.orders)
    resubmits = sum(n - 1 for n in fx.submits.values())
    return [
        Check(
            "kill fired in the node",
            str(kill.fired.is_set()),
            "== True",
            kill.fired.is_set() or case == "none",
        ),
        Check("messages not processed after restart", str(unprocessed), "== {}", not unprocessed),
        Check("messages with a wrong card count", str(wrong_cards), "== {}", not wrong_cards),
        Check("duplicate cards", str(fx.duplicate_cards), "== 0", fx.duplicate_cards == 0),
        Check(
            "orders per card (max)",
            str(max(orders_per_card.values(), default=0)),
            "<= 1",
            all(n <= 1 for n in orders_per_card.values()),
        ),
        Check("broker re-submits of a client order id", str(resubmits), "== 0", resubmits == 0),
        Check(
            "broker orders unknown to the orders table",
            str(fx.orphan_broker_orders),
            "== 0",
            fx.orphan_broker_orders == 0,
        ),
    ]


def record(case: str, rows: dict[str, str], ok: bool) -> None:
    OUTCOMES[case] = {**rows, "result": "PASS" if ok else "**FAIL**"}
    cols = ["graph", "node", "phase", "cards", "orders", "card states", "notes", "result"]
    table = [
        "| " + " | ".join(cols) + " |",
        "|" + "---|" * len(cols),
        *("| " + " | ".join(OUTCOMES[c].get(k, "") for k in cols) + " |" for c in sorted(OUTCOMES)),
    ]
    all_pass = all(r["result"] == "PASS" for r in OUTCOMES.values())
    summary = [
        info("cases run in this session", str(len(OUTCOMES))),
        Check(
            "cases passed",
            str(sum(r["result"] == "PASS" for r in OUTCOMES.values())),
            f"== {len(OUTCOMES)}",
            all_pass,
        ),
    ]
    upsert(
        "kill",
        render_section(
            "Test 5: kill -9 in every graph node, then restart",
            summary,
            "\n".join(table),
            (
                "In-process kill: `Killed(BaseException)` at the node's entry (nothing of it ran) "
                "or exit (its commits are in, its result never reached the checkpoint); "
                "`execute/submit` dies inside the broker submit after the broker accepted. Every "
                "other in-flight worker is cancelled with the stack. Restart = fresh services on "
                "the same DB, checkpointer, broker and clock through the real `startup()`.",
                "Message-graph scenario: a fast entry, a slow (LLM) entry, a slow message whose "
                "LLM answer fails literal-span verification (raw review) and chatter, from four "
                "providers at once; the kill targets the message that routes through the node. "
                "Expected cards per message: 1, 1, 1 (review), 0.",
                "Approval-graph scenario (paper mode, SimulatedBroker): an entry card is "
                "approved; the kill hits the approval run; the restart's startup reconciles, "
                "expires open cards and resumes the approved one.",
            ),
        ),
    )


def row(graph: str, kill: Kill, fx: Effects, notes: str = "") -> dict[str, str]:
    return {
        "graph": graph,
        "node": kill.node or "(no kill)",
        "phase": kill.phase if kill.node else "",
        "cards": str(sum(fx.cards_by_message.values())),
        "orders": str(len(fx.orders)),
        "card states": ", ".join(f"{k} {v}" for k, v in sorted(fx.card_states.items())),
        "notes": notes,
    }


MESSAGE_CASES = [("none", ""), *((n, p) for n in MESSAGE_NODES for p in ("enter", "exit"))]


@pytest.mark.parametrize(("node", "phase"), MESSAGE_CASES, ids=lambda v: v or "-")
async def test_5_kill_in_message_graph(
    node: str, phase: str, tmp_path: Path, owner_engine: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    kill = Kill("" if node == "none" else node, phase, text=TARGET.get(node, FAST).text)
    for name in MESSAGE_NODES:
        monkeypatch.setattr(_Nodes, name, wrap_message_node(name, getattr(_Nodes, name), kill))
    clock = RealtimeClock(T0 + timedelta(seconds=5))
    broker: SimulatedBroker | None = None
    plans = sd.by_text(list(SCENARIO))
    common: dict[str, Any] = {"warm_up": False, "trading_mode": TradingMode.PAPER}
    async with open_run(tmp_path, plans, clock=clock, **common) as run:
        broker = run.broker
        kill.armed = node != "none"
        sent = await send(run, SCENARIO)
        if kill.armed:
            async with asyncio.timeout(30):
                await kill.fired.wait()
        else:
            await run.wait_terminal([s.message_id for s in sent], 30)
    # the process is gone; a new one starts on the same database / broker / clock
    async with open_run(tmp_path, plans, clock=clock, broker=broker, restart=True, **common) as run:
        await run.wait_terminal([s.message_id for s in sent], 60)
        await asyncio.sleep(0.3)  # notify's approval starts after the inbox commit
        fx = await effects(run, sent)
        report = run.startup_report
    cs = checks(node, kill, sent, fx)
    notes = f"startup clean={report.clean if report else None}"
    record(f"message/{node}/{phase}", row("message", kill, fx, notes), all_ok(cs))
    assert all_ok(cs), [c for c in cs if c.ok is False]


APPROVAL_CASES = [
    ("none", ""),
    *((n, p) for n in APPROVAL_NODES for p in ("enter", "exit")),
    ("execute", "submit"),
]


@pytest.mark.parametrize(("node", "phase"), APPROVAL_CASES, ids=lambda v: v or "-")
async def test_5_kill_in_approval_graph(
    node: str,
    phase: str,
    tmp_path: Path,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kill = Kill("" if node == "none" else node, phase)
    clock = RealtimeClock(T0 + timedelta(seconds=5))
    plans = sd.by_text([FAST])
    common: dict[str, Any] = {"warm_up": False, "trading_mode": TradingMode.PAPER}
    session_id = await add_session(db_session)
    broker = KillingBroker(FakeMarketData.from_fixtures(MARKET, clock), clock)
    async with open_run(tmp_path, plans, clock=clock, broker=broker, **common) as run:
        broker.kill = kill
        install_approval_kill(run, kill, monkeypatch)
        sent = await send(run, (FAST,))
        await run.wait_terminal([sent[0].message_id], 30)
        async with run.factory() as db:
            card = await db.scalar(select(Card).where(col(Card.action) == CardAction.ENTRY))
        assert card is not None and card.state == CardState.PENDING, card
        kill.armed = node != "none"
        try:
            await run.approvals.decide(
                card.card_id,
                "approve",
                card.version,
                Edits(qty=2),
                session_id=session_id,
            )
        except Killed:
            pass
        monkeypatch.undo()  # the restarted process has the real code
    async with open_run(tmp_path, plans, clock=clock, broker=broker, restart=True, **common) as run:
        await run.consume()  # the broker event stream delivers the fills
        fx = await effects(run, sent)
        async with run.factory() as db:
            state = (await db.get(Card, card.card_id) or card).state
            last = await db.scalar(
                select(CardEvent)
                .where(col(CardEvent.card_id) == card.card_id)
                .order_by(col(CardEvent.id).desc())
            )
        snap = await run.approvals._graph.aget_state(thread_config(card.card_id))
        blocked = await run.flags.is_active(FlagName.ENTRIES_BLOCKED)
        report = run.startup_report
    cs = checks(node, kill, sent, fx)
    cs.append(
        Check(
            "approved card left stuck in `approved`",
            str(state),
            "!= approved",
            state != CardState.APPROVED,
        )
    )
    cs.append(  # resumed after the startup entries gate, not revalidated under the block
        Check(
            "approved entry invalidated by the restart",
            str(state),
            "!= invalidated",
            state != CardState.INVALIDATED or not (report and report.clean),
        )
    )
    notes = (
        f"card {state} ({last.reason_code if last else ''} {last.detail if last else ''}); "
        f"thread next {tuple(snap.next)}; startup clean="
        f"{report.clean if report else None}; entries_blocked after startup={blocked}"
    )
    record(f"approval/{node}/{phase}", row("approval", kill, fx, notes), all_ok(cs))
    assert all_ok(cs), [c for c in cs if c.ok is False]
