"""TRD §9 startup sequence (F046): runs in the lifespan before entries can happen, each step
logged and timed. A crash or failover never duplicates cards or orders: intents are found at
the broker by client order id, open cards expire, and entries stay blocked until the broker
and the lots agree."""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cards.approval_service import ApprovalService
from app.dispatcher.service import Dispatcher
from app.execution import repo as orders_repo
from app.execution.exit import ExitPolicy
from app.execution.expiration import ExpirationService
from app.ingest.gaps import GapDetector
from app.reconcile.service import Reconciler, ReconcileReport
from app.runtime_config.models import Actor
from app.system.flags import FlagService
from app.system.models import FlagName
from app.timers.service import TimerService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _step(number: int, name: str) -> AsyncIterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        logger.info(
            "startup step %s",
            name,
            extra={"step": number, "ms": round((time.perf_counter() - started) * 1000)},
        )


async def startup(
    *,
    sessions: async_sessionmaker[AsyncSession],
    flags: FlagService,
    reconciler: Reconciler,
    approvals: ApprovalService,
    gaps: GapDetector,
    timers: TimerService,
    expiration: ExpirationService,
    exits: ExitPolicy,
    dispatcher: Dispatcher,
) -> ReconcileReport:
    async with _step(1, "block entries"):
        await flags.set(FlagName.ENTRIES_BLOCKED, True, reason="startup", actor=Actor.SYSTEM)
    async with _step(2, "reconcile: adopt orders, reconcile lots"):  # steps 2-4
        report = await reconciler.run(release=False)
    async with _step(5, "expire open cards, resume interrupted approvals"):
        expired = await approvals.expire_on_restart()
        resumed = await approvals.resume_approved()
        logger.info("approvals recovered", extra={"expired": expired, "resumed": resumed})
    async with _step(7, "gap detector"):  # step 6 (inbox reset) is Dispatcher.start()
        try:
            await gaps.run_once()
        except Exception:
            logger.exception("startup gap detection failed")  # the periodic run retries
    async with _step(8, "timers and expiration cutoffs"):
        await timers.run_due()
        await expiration.schedule_today()
    async with _step(9, "adopt working exits"):  # the stop watcher reloads itself
        async with sessions() as db:
            roots = await orders_repo.open_exit_roots(db)
        for root_id in roots:
            await exits.adopt(root_id)
    async with _step(10, "dispatcher"):
        await dispatcher.start()
    async with _step(11, "entries gate"):
        if report.clean:
            await reconciler.release()
    return report
