"""Pipeline test harness: real PG, FakeMarketData fixtures, fixture interpreter in tmp_path."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.service import AlertService
from app.config import get_settings
from app.db import in_transaction
from app.dispatcher.types import ProcessingContext
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
    ) -> GraphProcessor:
        """The message graph. `fixture=True`: recorded interpretations replace Jev + LLM.
        Otherwise Jev (if given) and the LLM (default: an unconfigured chain -> exhausted)."""
        flags = FlagService(self.factory, self.alerts)
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
            planner=Planner(
                self.factory,
                validation=ValidationService(
                    self.factory, marketdata=self.md, alerts=self.alerts, clock=self.clock
                ),
                marketdata=self.md,
                flags=flags,
            ),
            flags=flags,
            llm=llm or LlmInterpreter(chain, self.md, self.alerts),
            jev=jev,
            notes=notes,
            fixture=FixtureInterpreter(self.fixtures) if fixture else None,
        )
        return GraphProcessor(deps, checkpointer)

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

    async def process(
        self,
        env: Envelope,
        *,
        stale: bool = False,
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
                late=False,
            )
        )
