"""Jev fast path (TRD §4.3, §7.4): one TypeSafe `system_one` request per message, inside the
`typesafe` semaphore, 429/529 retried once, every attempt audited in `model_calls`.

F078: the result says *why* there is no answer (`JevFailed.kind`), because a timeout, an invalid
answer and a scope violation lead to different places (see `interpret/route.py`). Time is split
into the limiter wait (`queue_timeout_ms`), one request (`timeout_ms`) and the whole step
(`budget_ms`). Nothing here raises for a model problem: the message graph never poisons on Jev.
"""

import asyncio
import logging
import math
import time
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal

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
from app.interpret.jev.mapping import Answer, JevResult, map_answers, validate_answers
from app.interpret.jev.questions import VERSION, build_questions
from app.interpret.jev.state import build_jev_state
from app.interpret.llm_extract.middleware import ScopeViolation
from app.llm.limiter import ProviderLimiter
from app.llm.models import CallOutcome, ModelProvider, ModelRole
from app.llm.usage import CallRef, record_model_call
from app.markettime import Clock
from app.observability.counters import bump
from app.runtime_config.schema import RuntimeConfig

logger = logging.getLogger(__name__)

RETRYABLE = frozenset({429, 529})
RETRY_BACKOFF_S = 0.1
ATTEMPTS = 2  # the call + one retry on 429/529, when the budget still holds a full request

FailKind = Literal[
    "disabled",
    "timeout",
    "queue_timeout",
    "rate_limited",
    "api_error",
    "connection",
    "invalid",
    "scope_violation",
]


@dataclass(frozen=True)
class JevAnswered:
    result: JevResult


@dataclass(frozen=True)
class JevFailed:
    """No usable Jev answer. `scope_violation` is a safety failure (no model may be asked);
    every other kind is a model failure (another model may answer)."""

    kind: FailKind
    detail: str = ""
    model_call_id: int | None = None


JevOutcome = JevAnswered | JevFailed

_KIND: dict[CallOutcome, FailKind] = {
    CallOutcome.TIMEOUT: "timeout",
    CallOutcome.QUEUE_TIMEOUT: "queue_timeout",
    CallOutcome.RATE_LIMITED: "rate_limited",
    CallOutcome.CONNECTION_ERROR: "connection",
    CallOutcome.SCHEMA_INVALID: "invalid",
}


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


class _QueueTimeout(Exception):
    pass


def _resolution_summary(resolution: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not resolution:
        return None
    keys = ("outcome", "position_id", "candidates", "method", "evidence")
    return {k: resolution.get(k) for k in keys}


def _json_safe(value: Any) -> Any:
    """jsonb has no NaN / Infinity: an invalid answer is still audited, as text."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


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
        underlying_optional: bool = False,
        resolution: Mapping[str, Any] | None = None,
    ) -> JevOutcome:
        """`resolution`: the context resolver's verdict, kept in the audit row (F080) so a
        historical call shows which candidates code had narrowed to and why."""
        if not self.available():
            return JevFailed("disabled")
        cfg = self._config()
        bump("jev:requests")
        questions = build_questions(ctx, candidates)
        try:
            state = build_jev_state(ctx, candidates, lot_ids)
        except ScopeViolation as exc:  # a bug upstream: nothing leaves the process
            bump("jev:scope_violation")
            reason = str(exc)
            logger.error("jev request blocked by the scope guard", extra={"reason": reason})
            call_id = await self._record(
                ref,
                cfg,
                1,
                {"questions": VERSION, "blocked": reason},
                {"error": "scope_blocked"},
                CallOutcome.SCOPE_BLOCKED,
                time.monotonic(),
                self._clock.now(),
            )
            return JevFailed("scope_violation", reason, call_id)
        request = {
            "questions": VERSION,
            "question_ids": sorted(questions),
            "state": state,
            # F080: what a replay of this call needs besides the state
            "model_requested": cfg.jev.model,
            "link_candidates": [str(b.id) for b in ctx.link_candidates],
            "resolution": _resolution_summary(resolution),
            "thresholds": {
                k: str(getattr(cfg, k)) for k in ("t_chat", "t_type", "t_field", "t_link")
            },
            "budget_ms": {
                "queue": cfg.jev.queue_timeout_ms,
                "request": cfg.jev.timeout_ms,
                "step": cfg.jev.budget_ms,
            },
        }
        asked = await self._ask(cfg, state, questions, request, ref)
        if isinstance(asked, JevFailed):
            bump(f"jev:{asked.kind}")
            return asked
        answers, response, t0, started, attempt = asked
        raw = _json_safe({name: a.model_dump(mode="json") for name, a in answers.items()})
        problem = validate_answers(answers, questions)
        result: JevResult | None = None
        if problem is None:
            try:
                result = map_answers(
                    answers, ctx, candidates, cfg, underlying_optional=underlying_optional
                )
            except (KeyError, StopIteration, ValueError) as exc:  # a mapper gap: never poison
                logger.exception("jev answers could not be mapped")
                problem = f"mapping: {type(exc).__name__}: {exc}"[:300]
        outcome = CallOutcome.OK if problem is None else CallOutcome.SCHEMA_INVALID
        call_id = await self._record(
            ref,
            cfg,
            attempt,
            request,
            raw if problem is None else {"answers": raw, "invalid": problem},
            outcome,
            t0,
            started,
            usage=response.usage,
            model_returned=response.model,
        )
        if response.model and response.model != cfg.jev.model:
            bump("jev:model_mismatch")  # an alias moved, or the API substituted a model
            logger.warning(
                "jev answered with another model",
                extra={"requested": cfg.jev.model, "returned": response.model},
            )
        if result is None:
            bump("jev:invalid")
            logger.warning("jev answer invalid", extra={"problem": problem})
            return JevFailed("invalid", problem or "", call_id)
        bump("jev:ok")
        return JevAnswered(replace(result, model_call_id=call_id))

    async def _ask(
        self,
        cfg: RuntimeConfig,
        state: dict[str, Any],
        questions: Mapping[str, Question],
        request: dict[str, Any],
        ref: CallRef,
    ) -> tuple[Mapping[str, Answer], SystemOneResponse, float, datetime, int] | JevFailed:
        assert self._client is not None and cfg.jev.model is not None  # noqa: S101 — available()
        jev = cfg.jev
        step_started = time.monotonic()
        for attempt in range(1, ATTEMPTS + 1):
            started, t0 = self._clock.now(), time.monotonic()
            try:
                response = await self._one(cfg, state, questions)
            except (
                TypeSafeAPIError,
                TypeSafeAPIConnectionError,
                TimeoutError,
                _QueueTimeout,
            ) as exc:
                outcome = (
                    CallOutcome.QUEUE_TIMEOUT if isinstance(exc, _QueueTimeout) else outcome_of(exc)
                )
                call_id = await self._record(
                    ref, cfg, attempt, request, {"error": type(exc).__name__}, outcome, t0, started
                )
                status = getattr(exc, "status", None)
                spent_ms = (time.monotonic() - step_started) * 1000
                # a retry only when its worst case (backoff, wait, request) fits the budget
                worst = RETRY_BACKOFF_S * 1000 + jev.queue_timeout_ms + jev.timeout_ms
                fits = spent_ms + worst <= jev.budget_ms
                if status in RETRYABLE and attempt < ATTEMPTS and fits:
                    await asyncio.sleep(RETRY_BACKOFF_S)
                    continue
                logger.warning("jev unavailable", extra={"outcome": outcome, "status": status})
                return JevFailed(_KIND.get(outcome, "api_error"), str(outcome), call_id)
            return response.answers, response, t0, started, attempt
        raise AssertionError("unreachable: the last attempt returns")  # pragma: no cover

    async def _one(
        self, cfg: RuntimeConfig, state: dict[str, Any], questions: Mapping[str, Question]
    ) -> SystemOneResponse:
        """One request: the limiter wait and the request each have their own clock."""
        assert self._client is not None  # noqa: S101
        jev = cfg.jev
        async with AsyncExitStack() as held:
            try:
                async with asyncio.timeout(jev.queue_timeout_ms / 1000):
                    await held.enter_async_context(self._limiter.slot("typesafe"))
            except TimeoutError:
                raise _QueueTimeout from None
            async with asyncio.timeout(jev.timeout_ms / 1000):
                return await self._client.system_one(
                    state=state,
                    questions=questions,
                    model=jev.model,
                    timeout=jev.timeout_ms / 1000,
                )

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
        model_returned: str | None = None,
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
            model_returned=model_returned,
        )
