"""F036: POST /api/v1/cards/{id}/decision — T4, version checks, AC-14, AC-15, one approval."""

import asyncio
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.cards import repo as cards
from app.cards.models import Approval, Card, CardAction, CardSource, CardState, CardType
from app.db import in_transaction
from app.decide.types import ProposedAction
from app.instruments import OptionContract
from app.ledger import repo as ledger
from app.ledger.lots import apply_fill
from tests.integration.api.conftest import Api
from tests.integration.pipeline.harness import T0, Harness, follow_up, opened_position, option_entry
from tests.messages import PROVIDER_A

GOOGL = OptionContract(
    underlying="GOOGL", expiry=date(2026, 10, 2), strike=Decimal("357.5"), right="call"
)


@pytest.fixture
async def seeded(signed_in: Api, tmp_path: Path) -> tuple[Api, dict[str, str]]:
    """An entry card (superseded by the trim, OD-14) and a blank-qty trim card on a 4-lot."""
    h = await Harness.create(signed_in.app.state.session_factory, tmp_path)
    a1 = await h.message(
        "Bought GOOGL 357.5C 10/2 @ 0.71",
        [option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.71")],
        author=PROVIDER_A,
    )
    await h.process(a1)
    async with in_transaction(h.factory, None) as db:
        await ledger.record_lot(
            db,
            apply_fill(None, "buy", 4, Decimal("0.74")),
            instrument=GOOGL,
            provider_position_id=opened_position(a1),
            provider_id=PROVIDER_A,
        )
    a2 = await h.message(
        "trimming some",
        [follow_up("trim", opened_position(a1))],  # no fraction: the user enters qty (C26)
        at=T0 + timedelta(seconds=10),
        author=PROVIDER_A,
    )
    await h.process(a2)
    items = (await signed_in.client.get("/api/v1/cards")).json()["items"]
    return signed_in, {c["action"]: c["id"] for c in items}


def decision(kind: str, version: int = 1, **edits: object) -> dict[str, object]:
    return {"decision": kind, "version": version, "edits": edits or None}


async def test_requires_session(api: Api) -> None:
    r = await api.client.post(f"/api/v1/cards/{uuid.uuid4()}/decision", json=decision("reject"))
    assert r.status_code == 401


async def test_unknown_card_404(signed_in: Api) -> None:
    r = await signed_in.client.post(
        f"/api/v1/cards/{uuid.uuid4()}/decision", json=decision("reject")
    )
    assert r.status_code == 404 and r.json()["code"] == "not_found"


async def test_not_pending_or_stale_version_409(seeded: tuple[Api, dict[str, str]]) -> None:
    api, ids = seeded
    superseded = await api.client.post(
        f"/api/v1/cards/{ids['entry']}/decision", json=decision("approve")
    )
    assert superseded.status_code == 409 and "invalidated" in superseded.json()["message"]
    stale = await api.client.post(
        f"/api/v1/cards/{ids['trim']}/decision", json=decision("approve", 2, qty=1)
    )
    assert stale.status_code == 409 and stale.json()["code"] == "conflict"


async def test_ac15_blank_qty_trim_not_approvable(seeded: tuple[Api, dict[str, str]]) -> None:
    api, ids = seeded
    url = f"/api/v1/cards/{ids['trim']}/decision"
    assert (await api.client.post(url, json=decision("approve"))).status_code == 422
    over = await api.client.post(url, json=decision("approve", qty=5))  # lot holds 4
    assert over.status_code == 422 and "qty_exceeds_lot" in over.json()["message"]
    r = await api.client.post(url, json=decision("approve", qty=2))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["approval"]["decision"] == "approve" and body["approval"]["edits"] == {"qty": 2}
    assert body["state"] != "pending"


async def test_edit_bumps_version_then_old_version_conflicts(
    seeded: tuple[Api, dict[str, str]],
) -> None:
    api, ids = seeded
    url = f"/api/v1/cards/{ids['trim']}/decision"
    r = await api.client.post(url, json=decision("edit", qty=3))
    assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["state"] == "pending"
    assert (await api.client.post(url, json=decision("approve", 1))).status_code == 409
    assert (await api.client.post(url, json=decision("reject", 2))).status_code == 200


async def test_approve_rejects_identity_edits(seeded: tuple[Api, dict[str, str]]) -> None:
    api, ids = seeded
    r = await api.client.post(
        f"/api/v1/cards/{ids['trim']}/decision", json=decision("approve", qty=1, price="0.70")
    )
    assert r.status_code == 422


async def test_concurrent_approvals_record_one(seeded: tuple[Api, dict[str, str]]) -> None:
    api, ids = seeded
    url = f"/api/v1/cards/{ids['trim']}/decision"
    a, b = await asyncio.gather(
        api.client.post(url, json=decision("approve", qty=1)),
        api.client.post(url, json=decision("approve", qty=1)),
    )
    assert sorted([a.status_code, b.status_code]) == [200, 409]
    async with api.app.state.session_factory() as db:
        count = await db.scalar(select(func.count()).select_from(Approval))
    assert count == 1


async def test_ac14_suggested_underlying_needs_confirmation(signed_in: Api) -> None:
    proposal = ProposedAction(
        action=CardAction.ENTRY,
        card_type=CardType.REVIEW,
        qty=1,
        limit_price=Decimal("0.74"),
        review_reasons=["underlying_missing"],
        missing_fields=["underlying_confirmation"],
        suggested_underlying="GOOGL",
    )
    card = Card(
        card_id=uuid.uuid4(),
        dedupe_key="test:ac14",
        source=CardSource.SYSTEM,
        action=CardAction.ENTRY,
        card_type=CardType.REVIEW,
        state=CardState.PENDING,
        proposal=proposal.model_dump(mode="json"),
        posted_at=T0,
    )
    async with in_transaction(signed_in.app.state.session_factory, None) as db:
        await cards.insert(db, card)
    url = f"/api/v1/cards/{card.card_id}/decision"
    r = await signed_in.client.post(url, json=decision("approve"))
    assert r.status_code == 422 and "underlying_confirmation" in r.json()["message"]
    wrong = await signed_in.client.post(url, json=decision("edit", confirmedUnderlying="GOOG"))
    assert wrong.status_code == 422
    r = await signed_in.client.post(url, json=decision("edit", confirmedUnderlying="GOOGL"))
    assert r.status_code == 200 and r.json()["version"] == 2
    assert r.json()["proposal"]["missingFields"] == []
