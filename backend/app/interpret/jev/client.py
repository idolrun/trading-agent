"""Jev fast path (TRD §4.3, §7.4): one TypeSafe `system_one` request per message, inside the
`typesafe` semaphore, 429/529 retried once, every attempt audited in `model_calls`. Any failure
returns None: the router sends the message to the LLM slow path, nothing is lost."""

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from langchain_core.messages.ai import UsageMetadata
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from typesafe_sdk import (
    AsyncTypeSafeClient,
    Question,
    RetryPolicy,
    SystemOneResponse,
    TypeSafeAPIConnectionError,
    TypeSafeAPIError,
    TypeSafeAPIResponseValidationError,
    TypeSafeAPITimeoutError,
    TypeSafeRateLimitError,
    Usage,
)

from app.config import Settings
from app.interpret.candidates import Candidates
from app.interpret.context import ProviderContext
from app.interpret.jev.mapping import Answer, JevResult, map_answers
from app.interpret.jev.questions import VERSION, build_questions
from app.interpret.jev.state import build_jev_state
from app.llm.limiter import ProviderLimiter
from app.llm.models import CallOutcome, ModelProvider, ModelRole
from app.llm.usage import CallRef, record_model_call
from app.markettime import Clock
from app.runtime_config.schema import RuntimeConfig

logger = logging.getLogger(__name__)

RETRYABLE = frozenset({429, 529})
RETRY_BACKOFF_S = 0.1
ATTEMPTS = 2  # the call + one retry on 429/529


def outcome_of(exc: Exception) -> CallOutcome:
    if isinstance(exc, TypeSafeAPITimeoutError | TimeoutError):
        return CallOutcome.TIMEOUT
    if isinstance(exc, TypeSafeAPIConnectionError):
        return CallOutcome.CONNECTION_ERROR
    if isinstance(exc, TypeSafeAPIResponseValidationError):
        return CallOutcome.SCHEMA_INVALID
    if isinstance(exc, TypeSafeRateLimitError):
        return CallOutcome.RATE_LIMITED
    return CallOutcome.SERVER_ERROR


class JevInterpreter:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        config: Callable[[], RuntimeConfig],
        clock: Clock,
        client: AsyncTypeSafeClient | None = None,
    ) -> None:
        self._sessions = session_factory
        self._config = config
        self._clock = clock
        key = settings.typesafe_api_key
        if client is None and key is not None:
            client = AsyncTypeSafeClient(
                api_key=key.get_secret_value(), retry=RetryPolicy(max_retries=0)
            )
        self._client = client
        self._limiter = ProviderLimiter(lambda p: self._config().llm.concurrency.get(p))

    def available(self) -> bool:
        jev = self._config().jev
        return self._client is not None and jev.enabled and jev.model is not None

    async def interpret(
        self,
        ctx: ProviderContext,
        candidates: Candidates,
        *,
        ref: CallRef,
        lot_ids: frozenset[str] = frozenset(),
    ) -> JevResult | None:
        """None = no Jev answer (disabled, down, rate limited twice): route to the slow path."""
        if not self.available():
            return None
        cfg = self._config()
        state = build_jev_state(ctx, candidates, lot_ids)
        questions = build_questions(ctx, candidates, sorted(cfg.option_tiers or {}))
        answered = await self._ask(cfg, state, questions, ref)
        if answered is None:
            return None
        answers, call_id = answered
        result = map_answers(answers, ctx, candidates, cfg)
        return replace(result, model_call_id=call_id)

    async def _ask(
        self,
        cfg: RuntimeConfig,
        state: dict[str, Any],
        questions: Mapping[str, Question],
        ref: CallRef,
    ) -> tuple[Mapping[str, Answer], int] | None:
        assert self._client is not None and cfg.jev.model is not None  # noqa: S101 — available()
        request = {"questions": VERSION, "question_ids": sorted(questions), "state": state}
        for attempt in range(1, ATTEMPTS + 1):
            started, t0 = self._clock.now(), time.monotonic()
            response: SystemOneResponse | None = None
            try:
                async with asyncio.timeout(cfg.jev.timeout_ms / 1000):
                    async with self._limiter.slot("typesafe"):
                        response = await self._client.system_one(
                            state=state, questions=questions, model=cfg.jev.model
                        )
            except (TypeSafeAPIError, TypeSafeAPIConnectionError, TimeoutError) as exc:
                outcome = outcome_of(exc)
                await self._record(
                    ref, cfg, attempt, request, {"error": type(exc).__name__}, outcome, t0, started
                )
                status = getattr(exc, "status", None)
                if status in RETRYABLE and attempt < ATTEMPTS:
                    await asyncio.sleep(RETRY_BACKOFF_S)
                    continue
                logger.warning("jev unavailable", extra={"outcome": outcome, "status": status})
                return None
            answers = response.answers
            call_id = await self._record(
                ref,
                cfg,
                attempt,
                request,
                {name: a.model_dump(mode="json") for name, a in answers.items()},
                CallOutcome.OK,
                t0,
                started,
                usage=response.usage,
            )
            return answers, call_id
        return None

    async def _record(
        self,
        ref: CallRef,
        cfg: RuntimeConfig,
        attempt: int,
        request: dict[str, Any],
        response: dict[str, Any],
        outcome: CallOutcome,
        t0: float,
        started: datetime,
        usage: Usage | None = None,
    ) -> int:
        tokens = None
        if usage is not None and usage.input_tokens is not None:
            out = usage.output_tokens or 0
            tokens = UsageMetadata(
                input_tokens=usage.input_tokens,
                output_tokens=out,
                total_tokens=usage.input_tokens + out,
            )
        model = cfg.jev.model or ""
        return await record_model_call(
            self._sessions,
            ref=ref,
            provider=ModelProvider.TYPESAFE,
            model=model,
            role=ModelRole.INTERPRET,
            attempt=attempt,
            prompt_version=VERSION,
            request=request,
            response=response,
            outcome=outcome,
            latency_ms=int((time.monotonic() - t0) * 1000),
            started_at=started,
            usage=tokens,
            price=cfg.llm.prices.get(f"typesafe:{model}"),
        )
