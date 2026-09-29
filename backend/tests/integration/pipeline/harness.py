"""Pipeline test harness: real PG, FakeMarketData fixtures, fixture interpreter in tmp_path."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.service import AlertService
from app.broker.simulated import SimulatedBroker
from app.cards.approval_service import ApprovalService
from app.cards.corrections import CorrectionService
from app.config import get_settings
from app.db import in_transaction
from app.dispatcher.types import ProcessingContext
from app.execution.card_executor import CardExecutor
from app.execution.submit import OrderService
from app.graph.message_graph import GraphDeps
from app.graph.processor import GraphProcessor
from app.inbox import repo as inbox
from app.inbox.envelope import Envelope
from app.ingest.adapter import to_envelope
from app.interpret.interpreter import FixtureInterpreter
from app.interpret.jev.client import JevInterpreter
from app.interpret.llm_extract.agent import LlmInterpreter
from app.interpret.note import NoteWriter
from app.interpret.signal import F, LinkHint, LiteralSpan, ParsedSignal, ParsedSignals, SignalFields
from app.ledger.relink import RelinkService
from app.llm.chain import LlmChain
from app.marketdata.fake import FakeMarketData
from app.markettime import FixedClock
from app.pipeline.apply import position_id_for, signal_id_for
from app.pipeline.plan import Planner
from app.runtime_config.models import Actor
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService
from app.system.flags import FlagService
from app.validate.service import ValidationService
from tests.decide_helpers import CONFIG
from tests.messages import CHANNEL, create

T0 = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)  # Monday 10:30 ET
MARKET = Path(__file__).parents[2] / "fixtures" / "market"
Factory = async_sessionmaker[AsyncSession]


def lit(value: Any, raw: str | None = None) -> F[Any]:
    return F(value=value, source=LiteralSpan(raw=raw or str(value)))


def option_entry(
    underlying: str, strike: str, right_raw: str, expiry: str, price: str
) -> ParsedSignal:
    return ParsedSignal(
        msg_type="entry",
        instrument="option",
        fields=SignalFields(
            underlying=lit(underlying),
            strike=lit(Decimal(strike), strike),
            right=lit("call" if right_raw.lower().endswith("c") else "put", right_raw),
            expiry_token=lit(expiry),
            entry_price=lit(Decimal(price), price),
        ),
    )


def follow_up(
    msg_type: str, target: uuid.UUID | str | None, conf: str = "0.93", **fields: Any
) -> ParsedSignal:
    return ParsedSignal(
        msg_type=msg_type,
        fields=SignalFields(**fields),
        link_hint=LinkHint(target=target, confidence=Decimal(conf)),  # type: ignore[arg-type]
    )


def opened_position(env: Envelope, idx: int = 0) -> uuid.UUID:
    return position_id_for(signal_id_for(env.key, idx))


@dataclass
class Harness:
    factory: Factory
    md: FakeMarketData
    config: ConfigService
    alerts: AlertService
    fixtures: Path
    clock: FixedClock

    @classmethod
    async def create(cls, factory: Factory, fixtures: Path, **overrides: Any) -> "Harness":
        clock = FixedClock(T0 + timedelta(seconds=5))
        config = ConfigService(factory, allow_ungated_models=True)
        await config.update(
            CONFIG.model_copy(update={"channels": [CHANNEL], **overrides}),
            expected_version=0,
            actor=Actor.CLI,
            reason="test",
        )
        return cls(
            factory,
            FakeMarketData.from_fixtures(MARKET, clock),
            config,
            AlertService(factory),
            fixtures,
            clock,
        )

    def processor(
        self,
        *,
        fixture: bool = True,
        llm: LlmInterpreter | None = None,
        jev: JevInterpreter | None = None,
        notes: NoteWriter | None = None,
        checkpointer: Any = None,
        approvals: ApprovalService | None = None,
    ) -> GraphProcessor:
        """The message graph. `fixture=True`: recorded interpretations replace Jev + LLM.
        Otherwise Jev (if given) and the LLM (default: an unconfigured chain -> exhausted)."""
        flags = FlagService(self.factory, self.alerts)
        planner = self.planner(flags)
        chain = LlmChain(
            self.factory,
            get_settings(),
            lambda: (self.config.get() or RuntimeConfig()).llm,
            self.clock,
        )
        deps = GraphDeps(
            sessions=self.factory,
            config=self.config,
            clock=self.clock,
            planner=planner,
            flags=flags,
            llm=llm or LlmInterpreter(chain, self.md, self.alerts),
            jev=jev,
            notes=notes,
            fixture=FixtureInterpreter(self.fixtures) if fixture else None,
            approvals=approvals,
            relink=approvals.relink if approvals else None,
            corrections=CorrectionService(self.factory, planner=planner, marketdata=self.md),
        )
        processor = GraphProcessor(deps, checkpointer)
        if deps.relink is not None:
            deps.relink.recheck = processor.recheck
        return processor

    def planner(self, flags: FlagService) -> Planner:
        return Planner(
            self.factory,
            validation=ValidationService(
                self.factory, marketdata=self.md, alerts=self.alerts, clock=self.clock
            ),
            marketdata=self.md,
            flags=flags,
        )

    def approval_service(self, checkpointer: Any, broker: SimulatedBroker) -> ApprovalService:
        """F036 approvals over the shadow broker (`broker`); its FakeBroker base records calls."""
        flags = FlagService(self.factory, self.alerts)
        orders = OrderService(
            self.factory,
            broker=broker,
            simulated=broker,
            flags=flags,
            alerts=self.alerts,
            config=self.config,
        )
        approvals = ApprovalService(
            self.factory,
            checkpointer=checkpointer,
            executor=CardExecutor(self.factory, orders=orders, marketdata=self.md),
            marketdata=self.md,
            config=self.config,
            flags=flags,
            clock=self.clock,
        )
        approvals.relink = RelinkService(
            self.factory, planner=self.planner(flags), config=self.config, clock=self.clock
        )
        return approvals

    async def message(
        self,
        text: str,
        signals: list[ParsedSignal] | None,
        *,
        at: datetime = T0,
        author: int,
        **extra: Any,
    ) -> Envelope:
        """Accept the message into the inbox; write its interpretation (None = no fixture)."""
        env = to_envelope(create(at, text, author=author, **extra), at)
        async with in_transaction(self.factory, None) as db:
            await inbox.accept(db, env)
        if signals is not None:
            parsed = ParsedSignals(signals=signals, interpreter="llm")
            (self.fixtures / f"{env.key.message_id}.json").write_text(parsed.model_dump_json())
        return env

    async def edit(
        self, original: Envelope, text: str, signals: list[ParsedSignal] | None, *, at: datetime
    ) -> Envelope:
        """A `message.update` of `original` (event time = edit time, OD-24)."""
        payload = create(original.posted_at, text, author=original.provider_id)
        payload["type"] = "message.update"
        payload["message"] |= {
            "id": str(original.key.message_id),
            "edited_timestamp": at.isoformat(),
        }
        return await self._accept(payload, at, text, signals)

    async def delete(self, original: Envelope, *, at: datetime) -> Envelope:
        payload = create(original.posted_at, "", author=original.provider_id)
        payload["type"] = "message.delete"
        payload["message"] |= {
            "id": str(original.key.message_id),
            "content": None,
            "deleted_timestamp": at.isoformat(),
        }
        return await self._accept(payload, at, None, None)

    async def _accept(
        self,
        payload: dict[str, Any],
        at: datetime,
        text: str | None,
        signals: list[ParsedSignal] | None,
    ) -> Envelope:
        env = to_envelope(payload, at)
        async with in_transaction(self.factory, None) as db:
            await inbox.accept(db, env)
        if signals is not None:
            parsed = ParsedSignals(signals=signals, interpreter="llm")
            (self.fixtures / f"{env.key.message_id}.json").write_text(parsed.model_dump_json())
        return env

    async def process(
        self,
        env: Envelope,
        *,
        stale: bool = False,
        late: bool = False,
        attempt: int = 1,
        processor: GraphProcessor | None = None,
    ) -> None:
        await (processor or self.processor()).process(
            ProcessingContext(
                envelope=env,
                attempt=attempt,
                started_at=self.clock.now(),
                age_s=(self.clock.now() - env.posted_at).total_seconds(),
                stale=stale,
                late=late,
            )
        )
