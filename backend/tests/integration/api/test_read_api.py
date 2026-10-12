"""F030: read API over data produced by the real pipeline (fixtures -> cards, M2)."""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.alerts.models import AlertKind, Severity
from app.db import in_transaction
from app.execution.models import Fill, Order
from app.instruments import OptionContract, Stock
from app.ledger import repo as ledger
from app.ledger.lots import apply_fill
from app.markettime import FixedClock
from tests.integration.api.conftest import Api
from tests.integration.pipeline.harness import T0, Harness, follow_up, opened_position, option_entry
from tests.messages import PROVIDER_A, PROVIDER_B

READS = [
    "/api/v1/feed",
    "/api/v1/cards",
    f"/api/v1/cards/{uuid.uuid4()}",
    "/api/v1/positions",
    f"/api/v1/positions/{uuid.uuid4()}",
    "/api/v1/lots",
    f"/api/v1/lots/{uuid.uuid4()}",
    "/api/v1/orders",
    f"/api/v1/orders/{uuid.uuid4()}",
    "/api/v1/fills",
    "/api/v1/alerts",
    "/api/v1/health/details",
]
GOOGL = OptionContract(
    underlying="GOOGL", expiry=date(2026, 10, 2), strike=Decimal("357.5"), right="call"
)


@pytest.mark.parametrize("path", READS)
async def test_every_read_requires_a_session(api: Api, path: str) -> None:
    r = await api.client.get(path)
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"


@pytest.fixture
async def seeded(signed_in: Api, tmp_path: Path) -> tuple[Api, Harness]:
    h = await Harness.create(signed_in.app.state.session_factory, tmp_path)
    a1 = await h.message(
        "Bought GOOGL 357.5C 10/2 @ 0.71",
        [option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.71")],
        author=PROVIDER_A,
    )
    await h.process(a1)
    b1 = await h.message(
        "Bought TSLA 400C 10/2 @ 3.10",
        [option_entry("TSLA", "400", "400C", "10/2", "3.10")],
        at=T0 + timedelta(seconds=5),
        author=PROVIDER_B,
    )
    await h.process(b1, stale=True)  # FR-1c: ledger only, labelled stale in the feed
    a2 = await h.message(
        "Trim half",
        [follow_up("trim", opened_position(a1), exit_fraction="half")],
        at=T0 + timedelta(seconds=10),
        author=PROVIDER_A,
    )
    async with in_transaction(h.factory, None) as db:
        lot = await ledger.record_lot(
            db,
            apply_fill(None, "buy", 4, Decimal("0.74")),
            instrument=GOOGL,
            provider_position_id=opened_position(a1),
            provider_id=PROVIDER_A,
        )
        await ledger.create_foreign_lot(db, Stock(symbol="AAPL"), 10, Decimal(180), reason="t")
        order = Order(
            client_order_id="cta-test",
            auto_reason="eod_forced_close",
            lot_id=lot.lot_id,
            kind="exit",
            side="sell",
            instrument="option",
            broker_symbol=GOOGL.occ_symbol(),
            qty=1,
            order_type="limit",
            limit_price=Decimal("0.70"),
            tif="day",
            status="filled",
            mode="shadow",
            filled_qty=1,
        )
        db.add(order)
        await db.flush()
        db.add(
            Fill(
                order_id=order.order_id,
                broker_fill_id="f1",
                qty=1,
                price=Decimal("0.70"),
                filled_at=T0,
            )
        )
    await h.process(a2)
    for i in range(3):
        await h.alerts.raise_(AlertKind.CATCH_UP, Severity.WARNING, f"alert {i}")
    return signed_in, h


async def test_cards_positions_lots(seeded: tuple[Api, Harness]) -> None:
    api, _ = seeded
    c = api.client
    cards = (await c.get("/api/v1/cards")).json()
    assert cards["nextCursor"] is None
    by_action = {card["action"]: card for card in cards["items"]}
    assert set(by_action) == {"entry", "trim"}
    entry = by_action["entry"]
    assert entry["providerId"] == str(PROVIDER_A)  # Discord ids as strings
    assert uuid.UUID(entry["signalId"])  # F059: re-link needs the signal
    assert entry["underlying"] == "GOOGL" and entry["contract"]["strike"] == "357.5"
    assert (
        entry["proposal"]["qty"] is None
        and entry["proposal"]["tpLevels"] == []
        and entry["proposal"]["price"] == "0.74"
    )
    assert entry["sourceMessages"][0]["text"] == "Bought GOOGL 357.5C 10/2 @ 0.71"
    text = entry["sourceMessages"][0]["text"]
    spans = {s["field"]: text[s["start"] : s["end"]] for s in entry["sourceMessages"][0]["spans"]}
    assert spans == {
        "underlying": "GOOGL",
        "expiry_token": "10/2",
        "strike": "357.5",
        "right": "357.5C",
        "entry_price": "0.71",
    }
    assert entry["state"] == "invalidated"  # superseded by the trim (7.2)
    assert by_action["trim"]["proposal"]["qty"] is None and by_action["trim"]["lotId"]

    detail = (await c.get(f"/api/v1/cards/{entry['id']}")).json()
    assert [e["reasonCode"] for e in detail["events"]] == ["created", "superseded"]
    assert detail["approval"] is None
    assert (await c.get(f"/api/v1/cards/{uuid.uuid4()}")).status_code == 404
    pending = (await c.get("/api/v1/cards", params={"state": "pending"})).json()["items"]
    assert [card["action"] for card in pending] == ["trim"]
    assert (await c.get("/api/v1/cards", params={"state": "bogus"})).status_code == 422

    positions = (await c.get("/api/v1/positions", params={"providerId": str(PROVIDER_A)})).json()
    [pos] = positions["items"]
    assert (pos["status"], pos["remainingFraction"]) == ("trimmed", "0.5")
    pdetail = (await c.get(f"/api/v1/positions/{pos['id']}")).json()
    assert [e["change"] for e in pdetail["events"]] == ["open", "trim"]
    assert len(pdetail["lots"]) == 1 and len(pdetail["links"]) == 2

    foreign = (await c.get("/api/v1/lots", params={"foreign": "true"})).json()["items"]
    assert [lot["contract"]["underlying"] for lot in foreign] == ["AAPL"]
    tracked = (await c.get("/api/v1/lots", params={"foreign": "false"})).json()["items"]
    [lot] = tracked
    assert lot["avgPriceLabel"] == "system average"
    ldetail = (await c.get(f"/api/v1/lots/{lot['id']}")).json()
    assert [o["clientOrderId"] for o in ldetail["orders"]] == ["cta-test"]


async def test_cards_carry_the_provider_display_name(seeded: tuple[Api, Harness]) -> None:
    api, h = seeded
    before = (await api.client.get("/api/v1/cards")).json()["items"]
    assert {card["providerName"] for card in before} == {None}  # no message carried a name yet
    # The name rides on any later message from the provider, even one that makes no card.
    await h.message(
        "chat", None, at=T0 + timedelta(seconds=20), author=PROVIDER_A, author_name="Alpha Trades"
    )
    after = (await api.client.get("/api/v1/cards")).json()["items"]
    assert {card["providerName"] for card in after} == {"Alpha Trades"}
    detail = (await api.client.get(f"/api/v1/cards/{after[0]['id']}")).json()
    assert detail["providerName"] == "Alpha Trades"


async def test_feed_orders_fills_health(seeded: tuple[Api, Harness]) -> None:
    api, _ = seeded
    c = api.client
    feed = (await c.get("/api/v1/feed")).json()["items"]
    assert [item["text"] for item in feed] == [
        "Trim half",
        "Bought TSLA 400C 10/2 @ 3.10",
        "Bought GOOGL 357.5C 10/2 @ 0.71",
    ]
    assert feed[1]["labels"] == ["stale"] and feed[1]["cardIds"] == []
    assert feed[2]["msgTypes"] == ["entry"] and len(feed[2]["cardIds"]) == 1
    stale = (await c.get("/api/v1/feed", params={"label": "stale"})).json()["items"]
    assert [i["providerId"] for i in stale] == [str(PROVIDER_B)]

    [order] = (await c.get("/api/v1/orders")).json()["items"]
    odetail = (await c.get(f"/api/v1/orders/{order['id']}")).json()
    assert [f["price"] for f in odetail["fills"]] == ["0.7"]
    fills = (await c.get("/api/v1/fills", params={"orderId": order["id"]})).json()["items"]
    assert len(fills) == 1

    api.app.state.health._clock = FixedClock(T0 + timedelta(hours=1))  # the seed's trading day
    health = (await c.get("/api/v1/health/details")).json()
    assert health["status"] == "ok" and health["flags"]["kill_switch"] is False
    assert health["backlogDepth"] == 0 and health["window"] == "day"
    assert health["messages"]["total"] == 3 and health["orders"]["rejected"] == 0
    assert {p["provider"] for p in health["llm"]["providers"]} == set()
    assert (await c.get("/api/v1/health/details", params={"window": "7d"})).json()["window"] == "7d"
    assert (await c.get("/api/v1/health/details", params={"window": "1y"})).status_code == 422


async def test_alerts_pagination_and_acknowledge(seeded: tuple[Api, Harness]) -> None:
    api, _ = seeded
    c = api.client
    first = (await c.get("/api/v1/alerts", params={"limit": 2})).json()
    assert len(first["items"]) == 2 and first["nextCursor"]
    second = (
        await c.get("/api/v1/alerts", params={"limit": 2, "cursor": first["nextCursor"]})
    ).json()
    ids = [a["id"] for a in first["items"] + second["items"]]
    assert len(ids) == len(set(ids)) == 3 and second["nextCursor"] is None
    assert (await c.get("/api/v1/alerts", params={"cursor": "junk"})).status_code == 422

    target = ids[0]
    acked = await c.post(f"/api/v1/alerts/{target}/acknowledge")
    assert acked.status_code == 200 and acked.json()["acknowledged"] is True
    assert (await c.post(f"/api/v1/alerts/{target}/acknowledge")).status_code == 409
    assert (await c.post(f"/api/v1/alerts/{uuid.uuid4()}/acknowledge")).status_code == 404
    open_ = (await c.get("/api/v1/alerts", params={"acknowledged": "false"})).json()["items"]
    assert target not in [a["id"] for a in open_] and len(open_) == 2
