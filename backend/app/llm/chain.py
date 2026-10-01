"""The fallback chain (TRD §4.8 rules 3, 4, 9): one shared deadline, per-provider breaker and
semaphore, one `model_calls` row per attempt, and no answer shopping."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import anthropic
import openai
import pydantic
from langchain.agents.structured_output import StructuredOutputError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages.ai import UsageMetadata
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.llm.breaker import CircuitBreaker
from app.llm.limiter import ProviderLimiter
from app.llm.models import CallOutcome, ModelProvider, ModelRole
from app.llm.registry import ModelRegistry, enabled_providers
from app.llm.usage import CallRef, record_model_call
from app.markettime import Clock
from app.observability.counters import bump
from app.runtime_config.schema import ChainEntry, LlmConfig

logger = logging.getLogger(__name__)

Role = Literal["extract", "note", "prelabel"]

# Provider-health failures: they feed the breaker. Output failures (schema_invalid, empty,
# refusal) still fall back but say nothing about the provider being down.
TRANSPORT_FAILURES = frozenset(
    {
        CallOutcome.TIMEOUT,
        CallOutcome.RATE_LIMITED,
        CallOutcome.SERVER_ERROR,
        CallOutcome.CONNECTION_ERROR,
    }
)


class AttemptFailed(Exception):
    """Raised by an attempt function for a bad answer that should fall back
    (`schema_invalid`, `empty`, `refusal`)."""

    def __init__(self, outcome: CallOutcome, reason: str) -> None:
        super().__init__(reason)
        self.outcome = outcome


class ContentRejected(Exception):
    """The model answered, but code rejected the content (`span_rejected`, `scope_blocked`).
    Stops the chain: asking another model until one passes is answer shopping (rule 3)."""

    def __init__(self, outcome: CallOutcome, reason: str) -> None:
        super().__init__(reason)
        self.outcome = outcome


@dataclass
class Attempt:
    """Handed to the attempt function. It fills `request`/`response`/`usage` for the audit row."""

    number: int
    entry: ChainEntry
    model: BaseChatModel
    request: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] | None = None
    usage: UsageMetadata | None = None


@dataclass(frozen=True)
class AttemptRecord:
    provider: str
    model: str
    outcome: CallOutcome
    latency_ms: int
    queue_wait_ms: int
    model_call_id: int


@dataclass(frozen=True)
class ChainResult[T]:
    status: Literal["ok", "content_rejected", "exhausted"]
    value: T | None = None
    attempts: list[AttemptRecord] = field(default_factory=list)
    entry: ChainEntry | None = None  # the attempt that answered (ok / content_rejected)
    reason: str | None = None

    @property
    def last(self) -> AttemptRecord | None:
        return self.attempts[-1] if self.attempts else None


def classify(exc: BaseException) -> CallOutcome | None:
    """Map an attempt's exception to an outcome. None = not a model failure (a bug: re-raise)."""
    if isinstance(exc, AttemptFailed):
        return exc.outcome
    if isinstance(exc, TimeoutError | openai.APITimeoutError | anthropic.APITimeoutError):
        return CallOutcome.TIMEOUT
    if isinstance(exc, openai.APIStatusError | anthropic.APIStatusError):
        return CallOutcome.RATE_LIMITED if exc.status_code == 429 else CallOutcome.SERVER_ERROR
    if isinstance(exc, openai.APIConnectionError | anthropic.APIConnectionError):
        return CallOutcome.CONNECTION_ERROR
    if isinstance(exc, StructuredOutputError | pydantic.ValidationError):
        return CallOutcome.SCHEMA_INVALID
    return None


class LlmChain:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        config: Callable[[], LlmConfig],
        clock: Clock,
        models: ModelRegistry | None = None,
    ) -> None:
        self._sessions = session_factory
        self._config = config
        self._clock = clock
        self._models = models or ModelRegistry(settings)
        self._enabled = enabled_providers(settings)
        self._breakers: dict[str, CircuitBreaker] = {}
        self._limiter = ProviderLimiter(lambda p: self._config().concurrency.get(p))

    def configured(self, role: Role) -> bool:
        """At least one chain entry for `role` has a provider with an API key. False = the role
        is optional-and-off: callers skip it instead of reporting a degraded parser."""
        return any(e.provider in self._enabled for e in getattr(self._config().roles, role).chain)

    def breaker(self, provider: str) -> CircuitBreaker:
        return self._breakers.setdefault(provider, CircuitBreaker(self._clock))

    def breakers_open(self) -> dict[str, bool]:
        """Providers seen so far -> circuit open (or half-open probing)."""
        return {p: b.is_open for p, b in self._breakers.items()}

    async def run[T](
        self,
        role: Role,
        attempt_fn: Callable[[Attempt], Awaitable[T]],
        *,
        ref: CallRef,
        deadline_ms: int | None = None,
    ) -> ChainResult[T]:
        """Try the role's chain in order until one attempt answers. `exhausted` = every
        attempt failed or the deadline passed: the caller makes a raw review card (FR-9)."""
        cfg = self._config()
        role_cfg = getattr(cfg.roles, role)
        deadline_ms = deadline_ms or role_cfg.deadline_ms
        loop = asyncio.get_running_loop()
        end = None if deadline_ms is None else loop.time() + deadline_ms / 1000
        cap = None if role_cfg.per_attempt_cap_ms is None else role_cfg.per_attempt_cap_ms / 1000
        records: list[AttemptRecord] = []

        async def record(
            attempt: Attempt, outcome: CallOutcome, latency_ms: int, wait_ms: int, started: datetime
        ) -> None:
            entry = attempt.entry
            call_id = await record_model_call(
                self._sessions,
                ref=ref,
                provider=ModelProvider(entry.provider),
                model=entry.model,
                role=ModelRole(role),
                attempt=attempt.number,
                prompt_version=entry.prompt,
                request=attempt.request,
                response=attempt.response,
                outcome=outcome,
                latency_ms=latency_ms,
                started_at=started,
                usage=attempt.usage,
                price=cfg.prices.get(entry.model_key),
            )
            records.append(
                AttemptRecord(entry.provider, entry.model, outcome, latency_ms, wait_ms, call_id)
            )

        for number, entry in enumerate(role_cfg.chain, start=1):
            if entry.provider not in self._enabled:
                continue
            remaining = None if end is None else end - loop.time()
            if remaining is not None and remaining <= 0:
                break
            started = self._clock.now()
            attempt = Attempt(number, entry, self._models.model(entry, cfg))
            breaker = self.breaker(entry.provider)
            if not breaker.allow():
                await record(attempt, CallOutcome.CIRCUIT_OPEN, 0, 0, started)
                continue
            budgets = [b for b in (remaining, cap) if b is not None]
            wait_ms = 0
            t0 = time.monotonic()
            try:
                async with asyncio.timeout(min(budgets) if budgets else None):
                    async with self._limiter.slot(entry.provider) as wait_ms:
                        value = await attempt_fn(attempt)
            except ContentRejected as exc:
                breaker.success()
                latency = _ms_since(t0) - wait_ms
                attempt.response = attempt.response or {"rejected": str(exc)}
                await record(attempt, exc.outcome, latency, wait_ms, started)
                return ChainResult("content_rejected", None, records, entry, str(exc))
            except Exception as exc:
                outcome = classify(exc)
                if outcome is None:
                    breaker.failure()  # never leave a half-open probe dangling
                    raise
                if outcome == CallOutcome.RATE_LIMITED:
                    bump(f"rate_limited:{entry.provider}")
                if outcome in TRANSPORT_FAILURES:
                    breaker.failure()
                else:
                    breaker.success()
                attempt.response = attempt.response or {
                    "error": type(exc).__name__,
                    "detail": str(exc)[:500],
                }
                await record(attempt, outcome, _ms_since(t0) - wait_ms, wait_ms, started)
                logger.warning(
                    "llm attempt failed",
                    extra={"role": role, "provider": entry.provider, "outcome": outcome},
                )
                continue
            breaker.success()
            await record(attempt, CallOutcome.OK, _ms_since(t0) - wait_ms, wait_ms, started)
            return ChainResult("ok", value, records, entry)
        return ChainResult("exhausted", None, records, reason="chain exhausted or deadline passed")


def _ms_since(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)
