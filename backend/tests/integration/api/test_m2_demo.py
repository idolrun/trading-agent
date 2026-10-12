"""Milestone M2: webhook -> inbox -> dispatcher -> fixture interpretation -> validate ->
decide -> apply -> card on GET /api/v1/cards and `card.created` over SSE; a shadow
submit_order records the order without touching the broker adapter."""

import asyncio
from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth.models import AuthSession
from app.cards.models import Approval
from app.db import in_transaction
from app.execution.intent import Approved, OrderIntent
from app.instruments import OptionContract
from app.interpret.signal import ParsedSignals
from app.main import create_app
from app.marketdata.fake import MarketFixture
from app.markettime import FixedClock
from app.runtime_config.models import Actor
from tests.decide_helpers import CONFIG
from tests.integration.api.conftest import ORIGIN, UA, Api, api_settings, next_event, serve
from tests.integration.pipeline.harness import MARKET, T0, option_entry
from tests.messages import CHANNEL, PROVIDER_A, create, signed


@pytest.fixture
async def live(owner_engine: object, tmp_path: Path) -> AsyncIterator[tuple[Api, Path]]:
    settings = api_settings().model_copy(
        update={"background_enabled": True, "interpretation_fixtures_dir": tmp_path}
    )
    app = create_app(settings)
    app.state.clock = FixedClock(T0 + timedelta(seconds=3))  # 3 s after the post: fresh
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url=ORIGIN,
            headers={"Origin": ORIGIN, "User-Agent": UA},
        ) as client,
    ):
        api = Api(app, client)
        await api.sign_in()
        await app.state.config_service.update(
            CONFIG.model_copy(update={"channels": [CHANNEL]}),
            expected_version=0,
            actor=Actor.CLI,
            reason="m2 demo",
        )
        for path in sorted(MARKET.glob("*.json")):
            app.state.marketdata.load(MarketFixture.model_validate_json(path.read_text()))
        yield api, tmp_path


async def test_m2_webhook_to_card_to_shadow_order(live: tuple[Api, Path]) -> None:
    api, fixtures = live
    payload = create(T0, "Bought GOOGL 357.5C 10/2 @ 0.71", author=PROVIDER_A)
    parsed = ParsedSignals(
        signals=[option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.71")], interpreter="llm"
    )
    (fixtures / f"{payload['message']['id']}.json").write_text(parsed.model_dump_json())

    async with (
        serve(api.app) as url,
        httpx.AsyncClient(
            base_url=url,
            cookies=dict(api.client.cookies),
            headers={"Origin": ORIGIN, "User-Agent": UA},
        ) as sse,
        sse.stream("GET", "/api/v1/events") as events,
    ):
        await asyncio.sleep(0.3)  # SSE subscribed
        secret = api.app.state.settings.ingest_hmac_secret.get_secret_value()
        body, headers = signed(payload, secret, at=T0)
        accepted = await api.client.post("/ingest", content=body, headers=headers)
        assert accepted.json() == {"accepted": True}
        async with asyncio.timeout(5):  # dispatcher wakes on NOTIFY inbox
            created = await next_event(events, "card.created")

    [card] = (await api.client.get("/api/v1/cards")).json()["items"]
    assert card["id"] == created["id"]
    assert (card["action"], card["cardType"], card["state"]) == ("entry", "normal", "pending")
    assert card["proposal"]["qty"] is None  # C105: the user types it
    assert card["proposal"]["price"] == "0.74"

    # Shadow mode (default): the approved entry is recorded and simulated, never sent.
    async with in_transaction(api.app.state.session_factory, None) as db:
        session_id = await db.scalar(select(AuthSession.session_id))
        approval = Approval(
            card_id=card["id"],
            decision="approve",
            approved_proposal={"qty": 13},
            card_version=1,
            session_id=session_id,
        )
        db.add(approval)
    order = await api.app.state.orders.submit_order(
        OrderIntent(
            card_id=card["id"],
            kind="entry",
            side="buy",
            instrument=OptionContract(
                underlying="GOOGL",
                expiry=date(2026, 10, 2),
                strike=Decimal("357.5"),
                right="call",
            ),
            qty=13,
            order_type="limit",
            limit_price=Decimal("0.74"),
            tif="day",
            provider_position_id=card["providerPositionId"],
        ),
        Approved(approval.approval_id),
    )
    assert (order.mode, order.status, order.filled_qty) == ("shadow", "filled", 13)
    assert api.app.state.broker.calls == []  # the (fake) adapter was never called
    [listed] = (await api.client.get("/api/v1/orders")).json()["items"]
    assert (listed["mode"], listed["cardId"]) == ("shadow", card["id"])
