"""Normalized usage → cost, and the `model_calls` audit writer (TRD §4.8 rule 8)."""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.messages.ai import UsageMetadata, add_usage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import transaction
from app.inbox.models import EventType
from app.llm.models import CallOutcome, ModelCall, ModelProvider, ModelRole
from app.runtime_config.schema import Price

MTOK = Decimal(1_000_000)
COST_QUANTUM = Decimal("0.000001")  # numeric(12,6)


@dataclass(frozen=True)
class CallRef:
    """What a model call is about: a message event, a card (note), or an eval run."""

    message_id: int | None = None
    event_type: EventType | None = None
    event_seq: int | None = None
    card_id: uuid.UUID | None = None
    eval_run_id: uuid.UUID | None = None


def total_usage(messages: Iterable[BaseMessage]) -> UsageMetadata | None:
    """Sum over every model turn of an agent run (tool calls mean several turns)."""
    total: UsageMetadata | None = None
    for m in messages:
        if isinstance(m, AIMessage) and m.usage_metadata:
            total = m.usage_metadata if total is None else add_usage(total, m.usage_metadata)
    return total


def cached_tokens(usage: UsageMetadata) -> int:
    return usage.get("input_token_details", {}).get("cache_read", 0)


def cost_usd(usage: UsageMetadata, price: Price) -> Decimal:
    """`input_tokens` includes cache reads (LangChain's normalization), so they are billed
    at the cached rate and the rest at the input rate."""
    # ponytail: Anthropic cache *writes* (1.25x input) are billed as plain input.
    cached = cached_tokens(usage)
    cost = (
        (usage["input_tokens"] - cached) * price.input_per_mtok
        + cached * price.cached_input_per_mtok
        + usage["output_tokens"] * price.output_per_mtok
    ) / MTOK
    return cost.quantize(COST_QUANTUM)


async def record_model_call(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    ref: CallRef,
    provider: ModelProvider,
    model: str,
    role: ModelRole,
    attempt: int,
    prompt_version: str | None,
    request: dict[str, Any],
    response: dict[str, Any] | None,
    outcome: CallOutcome,
    latency_ms: int,
    started_at: datetime,
    usage: UsageMetadata | None = None,
    price: Price | None = None,
    model_returned: str | None = None,
) -> int:
    """One row per attempt, in its own short transaction (never inside `apply`)."""
    row = ModelCall(
        message_id=ref.message_id,
        event_type=ref.event_type,
        event_seq=ref.event_seq,
        card_id=ref.card_id,
        eval_run_id=ref.eval_run_id,
        provider=provider,
        model=model,
        model_returned=model_returned,
        role=role,
        attempt=attempt,
        prompt_version=prompt_version,
        request=request,
        response=response,
        outcome=outcome,
        latency_ms=max(latency_ms, 0),
        tokens_in=usage["input_tokens"] if usage else None,
        tokens_out=usage["output_tokens"] if usage else None,
        tokens_cached=cached_tokens(usage) if usage else None,
        cost_usd=cost_usd(usage, price) if usage and price else None,
        started_at=started_at,
    )
    async with session_factory() as session, transaction(session):
        session.add(row)
        await session.flush()
    assert row.id is not None  # noqa: S101 — identity assigned by the flush
    return row.id
