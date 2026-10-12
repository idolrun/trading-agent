"""Laya fallback: answers the Jev questions locally (MLX, Apple Silicon) when Jev gave no answer.

The model is small and its answers are weak, so the result goes through the same mapper and
router as Jev's with the strict thresholds of `RuntimeConfig.laya`. Blocking MLX calls run on one
dedicated thread; the slot is released when the thread finishes, not when the caller gives up,
so a timeout can never leave two calls on the GPU."""

import asyncio
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from functools import partial
from typing import Any, Literal

from langchain_core.messages.ai import UsageMetadata
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.config import Settings
from app.interpret.candidates import Candidates
from app.interpret.context import ProviderContext
from app.interpret.jev import questions as jev_questions
from app.interpret.jev.mapping import JevResult, map_answers
from app.interpret.laya.convert import to_answers, to_laya_questions
from app.interpret.laya.state import MAX_BOOK, build_laya_state, trim_context
from app.llm.models import CallOutcome, ModelProvider, ModelRole
from app.llm.usage import CallRef, record_model_call
from app.markettime import Clock
from app.runtime_config.schema import RuntimeConfig

logger = logging.getLogger(__name__)
VERSION = f"{jev_questions.VERSION}+laya_state@v1"
State = Literal["idle", "loading", "ready", "failed"]


def gate(cfg: RuntimeConfig) -> RuntimeConfig:
    """The config the mapper and router use for Laya's answers: its own, stricter thresholds."""
    lc = cfg.laya
    return cfg.model_copy(
        update={
            "t_chat": lc.t_chat,
            "t_field": lc.t_field,
            "t_type": lc.t_type,
            "t_link": lc.t_link,
        }
    )


def load_agent(model: str) -> Any:
    import laya_mlx  # lazy: needs mlx, which exists only on Apple Silicon

    return laya_mlx.load(model)


class LayaInterpreter:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        config: Callable[[], RuntimeConfig],
        clock: Clock,
        alerts: AlertService,
        loader: Callable[[str], Any] = load_agent,
    ) -> None:
        self._sessions = session_factory
        self._model = settings.laya_model
        self._config = config
        self._clock = clock
        self._alerts = alerts
        self._loader = loader
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="laya")
        self._slot = asyncio.Semaphore(1)
        self._agent: Any = None
        self._warming: asyncio.Task[None] | None = None
        self.state: State = "idle"

    def available(self) -> bool:
        return self._config().laya.enabled and self.state == "ready"

    def start(self) -> None:
        """Load the weights in the background once Laya is enabled (first use downloads them)."""
        if self._config().laya.enabled and self.state == "idle":
            self._warming = asyncio.create_task(self.warm(), name="laya-warm")

    async def warm(self) -> None:
        if self.state != "idle":
            return
        self.state = "loading"
        try:
            self._agent = await self._run(partial(self._loader, self._model))
        except Exception as exc:  # ImportError off Apple Silicon, download or weight errors
            self.state = "failed"
            logger.exception("laya could not load")
            await self._alerts.raise_(
                AlertKind.PROVIDER_DISABLED,
                Severity.WARNING,
                "Laya fallback is enabled but could not load; a Jev outage goes to the LLM/review",
                details={"error": f"{type(exc).__name__}: {exc}"[:300]},
                dedupe_key="laya:load_failed",
            )
            return
        self.state = "ready"
        logger.info("laya ready", extra={"model": self._model})

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def _run[T](self, fn: Callable[[], T]) -> T:
        await self._slot.acquire()
        fut = asyncio.get_running_loop().run_in_executor(self._executor, fn)
        fut.add_done_callback(self._done)  # on the loop, when the thread ends (even if abandoned)
        return await asyncio.shield(fut)

    def _done(self, fut: asyncio.Future[Any]) -> None:
        self._slot.release()
        if not fut.cancelled():
            fut.exception()  # mark retrieved: a caller that timed out never awaits it

    async def interpret(
        self,
        ctx: ProviderContext,
        candidates: Candidates,
        *,
        ref: CallRef,
        lot_ids: frozenset[str] = frozenset(),
        underlying_optional: bool = False,
    ) -> JevResult | None:
        """None = no Laya answer (off, still loading, too many open positions, timeout)."""
        self.start()
        if not self.available():
            return None
        if len(ctx.link_candidates) > MAX_BOOK:  # F078: the resolver-narrowed candidates
            logger.info("laya skipped: book too large for its window")
            return None
        cfg = self._config()
        small = trim_context(ctx)
        state = build_laya_state(small, lot_ids)
        questions = jev_questions.build_questions(small, candidates)
        request = {"questions": VERSION, "question_ids": sorted(questions), "state": state}
        started, t0 = self._clock.now(), time.monotonic()
        if self._slot.locked():
            # F074: one GPU slot for every lane. Waiting for another lane's 3-10 s call would
            # stretch this lane's queue for nothing: skip now, the message goes on to the LLM or
            # review, and the skip is audited.
            logger.info("laya busy: skipped")
            await self._record(ref, request, {"error": "busy"}, CallOutcome.BUSY, t0, started)
            return None
        try:
            async with asyncio.timeout(cfg.laya.timeout_ms / 1000):
                raw = await self._run(
                    partial(self._agent.system_one, state, to_laya_questions(questions))
                )
        except Exception as exc:
            outcome = (
                CallOutcome.TIMEOUT if isinstance(exc, TimeoutError) else CallOutcome.SERVER_ERROR
            )
            logger.warning(
                "laya gave no answer", extra={"outcome": outcome, "error": type(exc).__name__}
            )
            await self._record(ref, request, {"error": type(exc).__name__}, outcome, t0, started)
            return None
        answers = to_answers(raw["answers"])
        call_id = await self._record(
            ref, request, raw["answers"], CallOutcome.OK, t0, started, raw["usage"]
        )
        result = map_answers(
            answers,
            small,
            candidates,
            gate(cfg),
            underlying_optional=underlying_optional,
            interpreter="laya",
        )
        return replace(result, model_call_id=call_id)

    async def _record(
        self,
        ref: CallRef,
        request: dict[str, Any],
        response: dict[str, Any],
        outcome: CallOutcome,
        t0: float,
        started: datetime,
        usage: dict[str, int] | None = None,
    ) -> int:
        tokens = None
        if usage is not None:
            tokens = UsageMetadata(
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                total_tokens=usage["input_tokens"] + usage["output_tokens"],
            )
        return await record_model_call(
            self._sessions,
            ref=ref,
            provider=ModelProvider.LAYA,
            model=self._model,
            role=ModelRole.INTERPRET,
            attempt=1,
            prompt_version=VERSION,
            request=request,
            response=response,
            outcome=outcome,
            latency_ms=int((time.monotonic() - t0) * 1000),
            started_at=started,
            usage=tokens,
        )
