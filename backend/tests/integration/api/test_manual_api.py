"""F047: manual operations, kill switch and corrections. Every manual order is a card approved
by the user's own action and revalidated like a signal order (C42); AC-10 step-up at API level."""

import uuid
from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlmodel import col

from app.cards.models import Approval, Card, CardSource, CardState
from app.db import in_transaction
from app.evalkit.models import EvalLabel, LabelSource
from app.evalkit.split import split_for
from app.execution.models import Order, OrderKind, Stop
from app.instruments import Instrument, OptionContract, Stock
from app.interpret.models import LinkMethod, LinkOutcome, MessageLink
from app.ledger import repo as ledger
from app.ledger.lots import apply_fill
from app.ledger.models import Lot, LotEvent, PositionChange, ProviderPosition, ProviderPositionEvent
from app.main import create_app
from app.manual.models import ManualCorrection
from app.marketdata.fake import MarketFixture
from app.markettime import FixedClock
from app.pipeline.apply import signal_id_for
from app.runtime_config.models import Actor
from app.runtime_config.schema import TradingMode
from app.system.models import FlagName
from tests.decide_helpers import CONFIG
from tests.integration.api.conftest import ORIGIN, UA, Api, api_settings
from tests.integration.pipeline.harness import (
    MARKET,
    T0,
    Harness,
    follow_up,
    opened_position,
    option_entry,
)
from tests.messages import CHANNEL, PROVIDER_A, PROVIDER_B
from tests.webauthn_authenticator import SoftAuthenticator

GOOGL = OptionContract(
    underlying="GOOGL", expiry=date(2026, 10, 2), strike=Decimal("357.5"), right="call"
)
GOOGL_PUT = OptionContract(
    underlying="GOOGL", expiry=date(2026, 10, 2), strike=Decimal("357.5"), right="put"
)
GOOGL_360 = OptionContract(
    underlying="GOOGL", expiry=date(2026, 10, 2), strike=Decimal("360"), right="call"
)
GOOGL_STOCK = Stock(symbol="GOOGL")
ENTRY = {
    "instrument": {
        "instrument": "option",
        "underlying": "GOOGL",
        "expiry": "2026-10-02",
        "strike": "357.5",
        "right": "call",
    },
    "qty": 1,
    "limitPrice": "0.71",
}


@pytest.fixture
async def m(owner_engine: object) -> AsyncIterator[Api]:
    """Signed-in app on a fixed regular-hours clock, paper mode (broker = the scriptable fake),
    complete config and the synthetic option chains."""
    app = create_app(api_settings())
    app.state.clock = FixedClock(T0 + timedelta(seconds=5))
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url=ORIGIN,
            headers={"Origin": ORIGIN, "User-Agent": UA},
        ) as client,
    ):
        api = Api(app, client, SoftAuthenticator("test", ORIGIN))
        await api.enroll()
        await api.login()
        for path in sorted(MARKET.glob("*.json")):
            app.state.marketdata.load(MarketFixture.model_validate_json(path.read_text()))
        await app.state.config_service.update(
            CONFIG.model_copy(update={"channels": [CHANNEL], "trading_mode": TradingMode.PAPER}),
            expected_version=0,
            actor=Actor.CLI,
            reason="test",
        )
        yield api


async def step_up(api: Api) -> None:
    options = await api.client.post("/api/v1/auth/step-up/options")
    r = await api.client.post(
        "/api/v1/auth/step-up/verify",
        json={"credential": api.authenticator.get(options.json())},
    )
    assert r.status_code == 200, r.text


async def post(api: Api, path: str, body: dict[str, Any], *, up: bool = False) -> Any:
    if up:
        await step_up(api)
    return await api.client.post(f"/api/v1{path}", json=body)


async def seed_lot(
    api: Api,
    contract: Instrument,
    qty: int,
    *,
    provider: int | None = PROVIDER_A,
    avg: str = "0.70",
) -> uuid.UUID:
    """A lot (tracked when `provider` is given, else foreign) that the paper broker also holds."""
    position_id = None
    async with in_transaction(api.app.state.session_factory, None) as db:
        if provider is not None:
            option = isinstance(contract, OptionContract)
            position = ProviderPosition(
                provider_id=provider,
                instrument="option" if option else "stock",
                underlying=contract.underlying
                if isinstance(contract, OptionContract)
                else contract.symbol,
                expiry=contract.expiry if isinstance(contract, OptionContract) else None,
                strike=contract.strike if isinstance(contract, OptionContract) else None,
                option_right=contract.right if isinstance(contract, OptionContract) else None,
                status="open",
            )
            db.add(position)
            await db.flush()
            position_id = position.provider_position_id
        lot = await ledger.record_lot(
            db,
            apply_fill(None, "buy", qty, Decimal(avg)),
            instrument=contract,
            provider_position_id=position_id,
            provider_id=provider,
        )
    api.app.state.broker.seed_position(contract, qty, Decimal(avg))
    return lot.lot_id


async def rows(api: Api, model: Any, *where: Any) -> list[Any]:
    async with api.app.state.session_factory() as db:
        return list((await db.scalars(select(model).where(*where))).all())


async def orders(api: Api, kind: OrderKind | None = None) -> list[Order]:
    return await rows(api, Order, *([col(Order.kind) == kind] if kind else []))


# --- authentication -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/manual/exit"),
        ("POST", "/manual/stops"),
        ("POST", "/manual/entry"),
        ("POST", "/kill-switch"),
        ("POST", "/flatten"),
        ("POST", "/positions"),
        ("PATCH", f"/positions/{uuid.uuid4()}"),
        ("POST", "/links"),
        ("POST", f"/lots/{uuid.uuid4()}/adjust"),
        ("PUT", "/providers/1/profile"),
        ("GET", "/providers/1/profile"),
    ],
)
async def test_requires_session(api: Api, method: str, path: str) -> None:
    r = await api.client.request(method, f"/api/v1{path}", json={})
    assert r.status_code == 401


# --- manual entry (C42, AC-10) ----------------------------------------------------------


async def test_entry_needs_step_up(m: Api) -> None:
    r = await post(m, "/manual/entry", ENTRY | {"idempotencyKey": "e-1"})
    assert (r.status_code, r.json()["code"]) == (403, "step_up_required")
    assert await rows(m, Card) == [] and await orders(m) == []


async def test_entry_passes_the_signal_checks_then_places_one_limit_order(m: Api) -> None:
    r = await post(m, "/manual/entry", ENTRY | {"idempotencyKey": "e-1"}, up=True)
    assert r.status_code == 200, r.text
    [card] = await rows(m, Card)
    assert (card.source, card.state, card.provider_position_id) == (
        CardSource.MANUAL,
        CardState.SUBMITTED,
        None,
    )
    [approval] = await rows(m, Approval)
    assert approval.card_id == card.card_id and approval.step_up  # the user's action, with step-up
    [order] = await orders(m)
    assert order.approval_id == approval.approval_id and order.kind == OrderKind.ENTRY
    assert (order.qty, order.limit_price, order.provider_position_id) == (1, Decimal("0.71"), None)
    assert r.json() == {
        "cardId": str(card.card_id),
        "state": "submitted",
        "orderIds": [str(order.order_id)],
    }

    # the fill opens a lot of the order's own contract (no provider position: foreign-shaped)
    m.app.state.broker.fill(order.broker_order_id)
    for event in m.app.state.broker.pending_events():
        await m.app.state.broker_consumer.handle(event)
    [lot] = await rows(m, Lot)
    assert (lot.qty, lot.broker_symbol, lot.is_foreign) == (1, GOOGL.occ_symbol(), True)


async def test_entry_gate_breach_is_422_with_the_reason(m: Api) -> None:
    snapshot = m.app.state.config_service.snapshot()
    assert snapshot is not None
    version, config = snapshot
    await m.app.state.config_service.update(
        config.model_copy(update={"max_total_exposure_usd": Decimal("50")}),
        expected_version=version,
        actor=Actor.CLI,
        reason="tight",
    )
    r = await post(m, "/manual/entry", ENTRY | {"idempotencyKey": "e-1"}, up=True)
    assert (r.status_code, r.json()["message"]) == (422, "max exposure")
    assert await orders(m) == []
    [card] = await rows(m, Card)  # the refusal stays auditable
    assert card.state == CardState.INVALIDATED
    again = await post(m, "/manual/entry", ENTRY | {"idempotencyKey": "e-1"}, up=True)
    assert (again.status_code, again.json()["message"]) == (422, "max exposure")
    assert len(await rows(m, Card)) == 1


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"limitPrice": "0.715"}, "price_not_on_tick"),
        ({"instrument": ENTRY["instrument"] | {"strike": "999"}}, "contract_not_in_chain"),
        ({"instrument": {"instrument": "stock", "underlying": "NOPE"}}, "market_data_unavailable"),
        ({"instrument": {"instrument": "option", "underlying": "GOOGL"}}, "an option needs"),
        ({"qty": 0}, "qty"),
    ],
)
async def test_entry_validation_is_422(m: Api, change: dict[str, Any], message: str) -> None:
    r = await post(m, "/manual/entry", ENTRY | change | {"idempotencyKey": "e-1"}, up=True)
    assert r.status_code == 422 and message in r.json()["message"], r.text
    assert await orders(m) == []


async def test_kill_switch_toggle_blocks_entries_but_not_exits(m: Api) -> None:
    lot_id = await seed_lot(m, GOOGL, 2)
    r = await post(m, "/kill-switch", {"active": True, "reason": "panic"})
    assert (r.status_code, r.json()) == (200, {"active": True})
    assert await m.app.state.flags.is_active(FlagName.KILL_SWITCH)
    blocked = await post(m, "/manual/entry", ENTRY | {"idempotencyKey": "e-1"}, up=True)
    assert (blocked.status_code, blocked.json()["message"]) == (422, "kill switch active")
    exit_ = await post(m, "/manual/exit", {"lotId": str(lot_id), "qty": 1, "idempotencyKey": "x-1"})
    assert exit_.status_code == 200 and len(await orders(m, OrderKind.EXIT)) == 1  # OD-27
    off = await post(m, "/kill-switch", {"active": False, "reason": "all clear"})
    assert off.json() == {"active": False}


# --- manual exit and stops ---------------------------------------------------------------


async def test_exit_is_idempotent_and_checked(m: Api) -> None:
    lot_id = await seed_lot(m, GOOGL, 2)
    body = {"lotId": str(lot_id), "qty": 1, "idempotencyKey": "x-1"}
    first = await post(m, "/manual/exit", body)
    assert first.status_code == 200, first.text
    again = await post(m, "/manual/exit", body)  # a double tap
    assert again.json() == first.json()
    [card] = await rows(m, Card)
    assert card.source == CardSource.MANUAL and len(await orders(m, OrderKind.EXIT)) == 1
    [approval] = await rows(m, Approval)
    assert not approval.step_up

    over = await post(m, "/manual/exit", body | {"qty": 3, "idempotencyKey": "x-2"})
    assert over.status_code == 422 and "exceeds" in over.json()["message"]
    missing = await post(
        m, "/manual/exit", body | {"lotId": str(uuid.uuid4()), "idempotencyKey": "x-3"}
    )
    assert (missing.status_code, missing.json()["code"]) == (404, "not_found")


async def test_foreign_lot_can_be_exited_manually_only(m: Api) -> None:
    lot_id = await seed_lot(m, GOOGL_PUT, 2, provider=None)
    stop = await post(
        m,
        "/manual/stops",
        {"lotId": str(lot_id), "op": "add", "level": "0.4", "idempotencyKey": "s-1"},
    )
    assert stop.status_code == 422 and "read-only" in stop.json()["message"]
    r = await post(m, "/manual/exit", {"lotId": str(lot_id), "qty": 2, "idempotencyKey": "x-1"})
    assert r.status_code == 200, r.text
    [order] = await orders(m, OrderKind.EXIT)
    assert (order.lot_id, order.qty, order.provider_position_id) == (lot_id, 2, None)


async def test_stop_add_move_cancel(m: Api) -> None:
    lot_id = await seed_lot(m, GOOGL, 2)

    async def stop(op: str, key: str, level: str | None = None) -> Any:
        body: dict[str, Any] = {"lotId": str(lot_id), "op": op, "idempotencyKey": key}
        return await post(m, "/manual/stops", body | ({"level": level} if level else {}))

    assert (await stop("move", "s-0", "0.5")).status_code == 422  # nothing to move
    assert (await stop("add", "s-1")).status_code == 422  # needs a level
    assert (await stop("add", "s-1", "0.50")).status_code == 200
    [working] = [s for s in await rows(m, Stop) if s.status == "working"]
    assert (working.level, working.qty) == (Decimal("0.50"), 2)
    assert (await stop("add", "s-2", "0.45")).status_code == 422  # already has one
    assert (await stop("move", "s-3", "0.60")).status_code == 200
    assert [s.level for s in await rows(m, Stop) if s.status == "working"] == [Decimal("0.60")]
    assert (await stop("cancel", "s-4")).status_code == 200
    assert [s for s in await rows(m, Stop) if s.status == "working"] == []
    assert all(c.source == CardSource.MANUAL for c in await rows(m, Card))


# --- flatten (C77, OD-31) -----------------------------------------------------------------


async def test_flatten_options_exits_tracked_option_lots_only(m: Api) -> None:
    a = await seed_lot(m, GOOGL, 2)
    b = await seed_lot(m, GOOGL_360, 1, provider=PROVIDER_B)
    stock = await seed_lot(m, GOOGL_STOCK, 5, avg="355")
    foreign = await seed_lot(m, GOOGL_PUT, 3, provider=None)
    body = {"scope": "options", "idempotencyKey": "f-1"}

    assert (await post(m, "/flatten", body)).status_code == 403  # AC-10
    assert not await m.app.state.flags.is_active(FlagName.KILL_SWITCH)

    r = await post(m, "/flatten", body, up=True)
    assert r.status_code == 200, r.text
    assert r.json()["killSwitch"] is True
    assert await m.app.state.flags.is_active(FlagName.KILL_SWITCH)
    assert {lot["lotId"] for lot in r.json()["lots"]} == {str(a), str(b)}
    assert {lot["state"] for lot in r.json()["lots"]} == {"submitted"}
    sold = {o.lot_id: o.qty for o in await orders(m, OrderKind.EXIT)}
    assert sold == {a: 2, b: 1}  # the stock and the foreign lot are untouched
    assert stock not in sold and foreign not in sold
    assert all(a.step_up for a in await rows(m, Approval))

    again = await post(m, "/flatten", body, up=True)  # replayed key: same cards, no new order
    assert [x["cardId"] for x in again.json()["lots"]] == [x["cardId"] for x in r.json()["lots"]]
    assert len(await orders(m, OrderKind.EXIT)) == 2


async def test_flatten_all_includes_stocks(m: Api) -> None:
    await seed_lot(m, GOOGL, 2)
    stock = await seed_lot(m, GOOGL_STOCK, 5, avg="355")
    m.app.state.marketdata.set_quote(GOOGL_STOCK, Decimal("355.00"), Decimal("355.05"))
    r = await post(m, "/flatten", {"scope": "all", "idempotencyKey": "f-1"}, up=True)
    assert [lot["blocked"] for lot in r.json()["lots"]] == [None, None]
    assert stock in {o.lot_id for o in await orders(m, OrderKind.EXIT)}


# --- lot adjust (F046 resolution) -------------------------------------------------------------


async def test_adjust_needs_step_up_and_a_valid_qty(m: Api) -> None:
    lot_id = await seed_lot(m, GOOGL, 3)
    body = {"qty": 2, "reason": "broker holds 2"}
    r = await post(m, f"/lots/{lot_id}/adjust", body)
    assert (r.status_code, r.json()["code"]) == (403, "step_up_required")
    bad = await post(m, f"/lots/{lot_id}/adjust", body | {"qty": -1}, up=True)
    assert bad.status_code == 422
    missing = await post(m, f"/lots/{uuid.uuid4()}/adjust", body, up=True)
    assert missing.status_code == 404
    assert (await rows(m, Lot))[0].qty == 3


async def test_adjust_resolves_a_reconciliation_mismatch(m: Api) -> None:
    lot_id = await seed_lot(m, GOOGL, 3)
    m.app.state.broker.seed_position(GOOGL, 2, Decimal("0.70"))  # the broker holds one less
    report = await m.app.state.reconciler.run()
    assert not report.clean and await m.app.state.flags.is_active(FlagName.ENTRIES_BLOCKED)

    r = await post(m, f"/lots/{lot_id}/adjust", {"qty": 2, "reason": "broker holds 2"}, up=True)
    assert r.status_code == 200, r.text
    assert r.json()["qty"] == 2
    [event] = await rows(m, LotEvent, col(LotEvent.change) == "reconcile_adjust")
    assert (event.qty_before, event.qty_after) == (3, 2) and "broker holds 2" in event.reason
    assert not await m.app.state.flags.is_active(FlagName.ENTRIES_BLOCKED)  # clean run lifted it


# --- corrections (C73) -------------------------------------------------------------------


async def test_position_add_edit_close_is_audited(m: Api) -> None:
    r = await post(
        m,
        "/positions",
        {
            "providerId": str(PROVIDER_A),
            "instrument": "option",
            "underlying": "GOOGL",
            "expiry": "2026-10-02",
            "strike": "357.5",
            "right": "call",
            "entryPrice": "0.71",
            "reason": "missed message",
        },
    )
    assert r.status_code == 200, r.text
    position_id = r.json()["id"]
    assert r.json()["isCorrected"] and r.json()["status"] == "open"

    edited = await m.client.patch(
        f"/api/v1/positions/{position_id}", json={"entryPrice": "0.72", "reason": "typo"}
    )
    assert edited.status_code == 200 and edited.json()["providerEntryPrice"] == "0.72"
    closed = await m.client.patch(
        f"/api/v1/positions/{position_id}", json={"status": "closed", "reason": "provider left"}
    )
    assert closed.json()["status"] == "closed"
    assert (
        await m.client.patch(f"/api/v1/positions/{position_id}", json={"reason": "x"})
    ).status_code == 422

    audits = await rows(
        m, ManualCorrection, col(ManualCorrection.target_type) == "provider_position"
    )
    assert [(a.action, a.reason) for a in audits] == [
        ("add", "missed message"),
        ("edit", "typo"),
        ("close", "provider left"),
    ]
    events = await rows(m, ProviderPositionEvent)
    assert [e.change for e in events] == [
        PositionChange.OPEN,
        PositionChange.CORRECT,
        PositionChange.CLOSE,
    ]
    assert [e.manual_correction_id for e in events] == [a.id for a in audits]
    assert await rows(m, EvalLabel) == []  # not tied to a message


async def test_position_edge_cases_are_422_404(m: Api) -> None:
    body = {
        "providerId": str(PROVIDER_A),
        "instrument": "stock",
        "underlying": "GOOGL",
        "reason": "r",
    }
    assert (
        await post(m, "/positions", body | {"strike": "1"})
    ).status_code == 422  # stocks: no strike
    assert (await post(m, "/positions", body | {"instrument": "option"})).status_code == 422
    assert (await post(m, "/positions", body | {"reason": ""})).status_code == 422
    missing = await m.client.patch(f"/api/v1/positions/{uuid.uuid4()}", json={"reason": "r"})
    assert missing.status_code == 404
    assert await rows(m, ManualCorrection) == []


async def harness(m: Api, tmp_path: Path) -> Harness:
    app = m.app
    return Harness(
        app.state.session_factory,
        app.state.marketdata,
        app.state.config_service,
        app.state.alerts,
        tmp_path,
        app.state.clock,
    )


async def test_correcting_a_misread_position_feeds_evaluation(m: Api, tmp_path: Path) -> None:
    h = await harness(m, tmp_path)
    a1 = await h.message(
        "Bought GOOGL 357.5C 10/2 @ 0.71",
        [option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.71")],
        author=PROVIDER_A,
    )
    await h.process(a1)
    position_id = opened_position(a1)
    r = await m.client.patch(
        f"/api/v1/positions/{position_id}", json={"strike": "360", "reason": "misread strike"}
    )
    assert r.status_code == 200 and r.json()["strike"] == "360"

    [audit] = await rows(m, ManualCorrection)
    assert (audit.target_type, audit.target_id) == ("provider_position", str(position_id))
    assert Decimal(audit.before["strike"]) == Decimal("357.5") and audit.after["strike"] == "360"
    [event] = await rows(
        m, ProviderPositionEvent, col(ProviderPositionEvent.manual_correction_id).is_not(None)
    )
    assert event.change == PositionChange.CORRECT
    [label] = await rows(m, EvalLabel)
    assert label.source == LabelSource.PROVIDER_CORRECTION and label.session_id == audit.session_id
    assert label.message_id == a1.key.message_id
    assert label.label["signals"][0]["corrected"] == {"strike": "360"}
    assert label.trading_day == date(2026, 9, 28) and label.split == split_for(label.trading_day)


async def test_relink_writes_a_user_correction_link_and_label(m: Api, tmp_path: Path) -> None:
    h = await harness(m, tmp_path)
    a1 = await h.message(
        "Bought GOOGL 357.5C 10/2 @ 0.71",
        [option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.71")],
        at=T0 + timedelta(seconds=1),
        author=PROVIDER_A,
    )
    a3 = await h.message(  # delivered first: the book was still empty, so no link
        "take half",
        [follow_up("trim", opened_position(a1), exit_fraction="half")],
        at=T0 + timedelta(seconds=10),
        author=PROVIDER_A,
    )
    await h.process(a3)
    await h.process(a1)
    signal_id = signal_id_for(a3.key, 0)
    body = {
        "signalId": str(signal_id),
        "providerPositionId": str(opened_position(a1)),
        "reason": "late entry",
    }

    r = await post(m, "/links", body)
    assert r.status_code == 200, r.text
    none, linked = await rows(m, MessageLink, col(MessageLink.signal_id) == signal_id)
    assert none.outcome == LinkOutcome.NONE
    assert (linked.outcome, linked.method) == (LinkOutcome.LINKED, LinkMethod.USER_CORRECTION)
    assert linked.supersedes_link_id == none.id
    [position] = await rows(m, ProviderPosition)
    assert position.remaining_fraction == Decimal("0.5")  # the trim reached the ledger
    [audit] = await rows(m, ManualCorrection)
    assert (audit.target_type, audit.target_id) == ("message_link", str(signal_id))
    assert audit.before is not None and audit.before["provider_position_id"] is None
    assert audit.after["provider_position_id"] == str(opened_position(a1))
    [label] = await rows(m, EvalLabel)
    assert label.label["link"] == {"idx": 0, "provider_position_id": str(opened_position(a1))}
    assert label.event_type == "create" and label.message_id == a3.key.message_id


async def test_cross_provider_link_is_422(m: Api, tmp_path: Path) -> None:
    h = await harness(m, tmp_path)
    a1 = await h.message(
        "Bought GOOGL 357.5C 10/2 @ 0.71",
        [option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.71")],
        at=T0 + timedelta(seconds=1),
        author=PROVIDER_A,
    )
    b1 = await h.message(
        "Bought GOOGL 360C 10/2 @ 0.47",
        [option_entry("GOOGL", "360", "360C", "10/2", "0.47")],
        at=T0 + timedelta(seconds=2),
        author=PROVIDER_B,
    )
    await h.process(a1)
    await h.process(b1)
    body = {
        "signalId": str(signal_id_for(a1.key, 0)),
        "providerPositionId": str(opened_position(b1)),
        "reason": "wrong",
    }
    r = await post(m, "/links", body)
    assert r.status_code == 422 and "own provider" in r.json()["message"]
    unknown = await post(m, "/links", body | {"signalId": str(uuid.uuid4())})
    assert unknown.status_code == 404
    assert await rows(m, ManualCorrection) == [] and await rows(m, EvalLabel) == []


async def test_provider_profile_put_writes_the_store_and_the_audit(m: Api) -> None:
    store = m.app.state.store
    url = f"/api/v1/providers/{PROVIDER_A}/profile"
    r = await m.client.put(
        url, json={"aliases": ["goog"], "lexicon": ["lotto"], "reason": "learned"}
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"aliases": ["goog"], "lexicon": ["lotto"], "habits": []}
    item = await store.aget(("provider", str(PROVIDER_A)), "aliases")
    assert item is not None and item.value == {"items": ["goog"]}  # the shape F035 reads
    assert await store.aget(("provider", str(PROVIDER_A)), "habits") is None

    r = await m.client.put(url, json={"aliases": ["goog", "googl"], "reason": "more"})
    assert r.json()["aliases"] == ["goog", "googl"]
    r = await m.client.get(url)
    assert r.json() == {"aliases": ["goog", "googl"], "lexicon": ["lotto"], "habits": []}
    assert (await m.client.get("/api/v1/providers/0/profile")).status_code == 422
    audits = await rows(
        m, ManualCorrection, col(ManualCorrection.target_type) == "provider_profile"
    )
    assert [(a.target_id, a.action, a.before, a.after) for a in audits] == [
        (
            str(PROVIDER_A),
            "update",
            {"aliases": None, "lexicon": None},
            {"aliases": ["goog"], "lexicon": ["lotto"]},
        ),
        (str(PROVIDER_A), "update", {"aliases": ["goog"]}, {"aliases": ["goog", "googl"]}),
    ]
    assert (await m.client.put(url, json={"reason": "nothing"})).status_code == 422
    assert (
        await m.client.put("/api/v1/providers/0/profile", json={"aliases": [], "reason": "r"})
    ).status_code == 422
    async with m.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(EvalLabel)) == 0
