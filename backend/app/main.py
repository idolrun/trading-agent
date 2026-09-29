"""FastAPI app factory. Background services are started in `lifespan`."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from sqlalchemy import make_url, text

from app.alerts.service import AlertService
from app.api.errors import ApiError, api_error_handler, validation_error_handler
from app.api.origin_check import origin_check
from app.api.routes import alerts as alerts_routes
from app.api.routes import auth as auth_routes
from app.api.routes import cards as cards_routes
from app.api.routes import config as config_routes
from app.api.routes import events as events_routes
from app.api.routes import feed as feed_routes
from app.api.routes import health as health_routes
from app.api.routes import ingest as ingest_routes
from app.api.routes import lots as lots_routes
from app.api.routes import orders as orders_routes
from app.api.routes import positions as positions_routes
from app.api.routes import push as push_routes
from app.auth.rate_limit import SlidingWindowLimiter
from app.auth.service import AuthService
from app.broker.fake import FakeBroker
from app.broker.port import BrokerClient, BrokerEvents
from app.broker.simulated import SimulatedBroker
from app.cards.approval_service import ApprovalService
from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory
from app.dispatcher.service import Dispatcher
from app.execution.card_executor import CardExecutor
from app.execution.submit import OrderService
from app.graph.checkpointer import open_pool, saver_and_store
from app.graph.message_graph import GraphDeps
from app.graph.processor import GraphProcessor
from app.ingest.feed_health import FeedHealthMonitor
from app.ingest.gaps import GapDetector
from app.interpret.interpreter import FixtureInterpreter, Interpreter
from app.interpret.jev.client import JevInterpreter
from app.interpret.llm_extract.agent import LlmInterpreter
from app.interpret.note import NoteWriter
from app.ledger.relink import RelinkService
from app.llm.chain import LlmChain
from app.llm.registry import alert_missing_keys
from app.logging import configure_logging
from app.marketdata.fake import FakeMarketData
from app.marketdata.port import MarketData
from app.markettime import SystemClock
from app.messageapi.fake import FakeMessageHistory
from app.messageapi.port import MessageHistory
from app.notify.bus import EventBus
from app.notify.push import PushService
from app.pipeline.plan import Planner
from app.pipeline.processor import RawReviewPoisonHandler
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService
from app.system.flags import FlagService
from app.system.heartbeat import run_heartbeat
from app.timers.models import TimerKind
from app.timers.service import TimerService
from app.validate.service import ValidationService

logger = logging.getLogger(__name__)


def libpq_dsn(sqlalchemy_url: str) -> str:
    """`postgresql+psycopg://…` → `postgresql://…` for a raw psycopg connection (LISTEN)."""
    return make_url(sqlalchemy_url).set(drivername="postgresql").render_as_string(False)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    engine = create_engine(settings)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        logger.exception("database unreachable at startup")
        await engine.dispose()
        raise
    session_factory = create_session_factory(engine)
    config_service = ConfigService(
        session_factory,
        libpq_dsn(str(settings.database_url)),
        allow_ungated_models=settings.ungated_models_allowed,
    )
    await config_service.load()
    config_listener = asyncio.create_task(config_service.listen(), name="config-listener")

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.config_service = config_service
    app.state.alerts = AlertService(session_factory)
    app.state.flags = FlagService(session_factory, app.state.alerts)
    llm_config = (config_service.get() or RuntimeConfig()).llm
    await alert_missing_keys(settings, llm_config, app.state.alerts)
    app.state.llm = LlmChain(
        session_factory,
        settings,
        lambda: (config_service.get() or RuntimeConfig()).llm,
        app.state.clock,
    )
    app.state.auth = AuthService(session_factory, settings, app.state.clock, app.state.alerts)
    # Only fakes until F051 (settings.broker_adapter / marketdata_adapter == "fake").
    fake_broker = FakeBroker(app.state.clock)
    broker: BrokerClient = fake_broker
    broker_events: BrokerEvents = fake_broker
    marketdata: MarketData = FakeMarketData(app.state.clock)
    app.state.broker = broker
    app.state.broker_events = broker_events
    app.state.marketdata = marketdata
    # Every order goes through here (TRD §1.5); shadow mode never touches `broker`.
    app.state.orders = OrderService(
        session_factory,
        broker=broker,
        simulated=SimulatedBroker(marketdata, app.state.clock),
        flags=app.state.flags,
        alerts=app.state.alerts,
        config=config_service,
    )
    # LangGraph checkpointer + store (schema langgraph): approval threads live here (F036).
    graph_pool = open_pool(libpq_dsn(str(settings.database_url)))
    await graph_pool.open()
    checkpointer, store = saver_and_store(graph_pool)
    planner = Planner(
        session_factory,
        validation=ValidationService(
            session_factory, marketdata=marketdata, alerts=app.state.alerts, clock=app.state.clock
        ),
        marketdata=marketdata,
        flags=app.state.flags,
    )
    relink = RelinkService(
        session_factory, planner=planner, config=config_service, clock=app.state.clock
    )
    app.state.approvals = ApprovalService(
        session_factory,
        checkpointer=checkpointer,
        executor=CardExecutor(session_factory, orders=app.state.orders, marketdata=marketdata),
        marketdata=marketdata,
        config=config_service,
        flags=app.state.flags,
        clock=app.state.clock,
    )
    app.state.approvals.relink = relink

    feed_health = FeedHealthMonitor(
        session_factory,
        config=config_service,
        flags=app.state.flags,
        alerts=app.state.alerts,
        clock=app.state.clock,
    )
    app.state.feed_health = feed_health
    history: MessageHistory = FakeMessageHistory()  # real client: F050
    gaps = GapDetector(
        session_factory,
        history=history,
        config=config_service,
        alerts=app.state.alerts,
        clock=app.state.clock,
    )
    app.state.gaps = gaps

    async def backfill_on_recovery() -> None:
        await gaps.run_once()

    feed_health.on_recovered.append(backfill_on_recovery)

    # Handlers are registered by their owners; unregistered kinds wait.
    app.state.timers = TimerService(
        session_factory,
        alerts=app.state.alerts,
        clock=app.state.clock,
        listen_dsn=libpq_dsn(str(settings.database_url)),
    )
    app.state.timers.register(TimerKind.CARD_EXPIRY, app.state.approvals.on_card_expiry)
    app.state.bus = EventBus()
    background = [
        config_listener,
        asyncio.create_task(app.state.bus.run(libpq_dsn(str(settings.database_url))), name="bus"),
    ]
    dispatcher = None
    graph_deps: GraphDeps | None = None
    if settings.background_enabled:

        def runtime() -> RuntimeConfig:
            return config_service.get() or RuntimeConfig()

        graph_deps = GraphDeps(
            sessions=session_factory,
            config=config_service,
            clock=app.state.clock,
            planner=planner,
            flags=app.state.flags,
            llm=LlmInterpreter(app.state.llm, marketdata, app.state.alerts),
            jev=JevInterpreter(session_factory, settings, runtime, app.state.clock),
            notes=NoteWriter(session_factory, app.state.llm, app.state.alerts, runtime),
            store=store,
            fixture=_fixture(settings),
            approvals=app.state.approvals,
            relink=relink,
        )
        processor = GraphProcessor(graph_deps, checkpointer)
        relink.recheck = processor.recheck
        dispatcher = Dispatcher(
            session_factory,
            config=config_service,
            processor=processor,
            poison=RawReviewPoisonHandler(session_factory, app.state.alerts),
            clock=app.state.clock,
            listen_dsn=libpq_dsn(str(settings.database_url)),
        )
        await dispatcher.start()
        background.append(
            asyncio.create_task(run_heartbeat(session_factory, app.state.clock), name="heartbeat")
        )
        background.append(asyncio.create_task(feed_health.run(), name="feed-health"))
        background.append(asyncio.create_task(app.state.timers.run(), name="timers"))
        push = PushService(session_factory, app.state.bus, settings)
        background.append(asyncio.create_task(push.run(), name="push"))
        background.append(asyncio.create_task(gaps.run(), name="gap-detector"))
    app.state.dispatcher = dispatcher
    try:
        yield
    finally:
        if dispatcher is not None:
            await dispatcher.stop()
        if graph_deps is not None:
            for note in graph_deps.background:
                note.cancel()
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        await graph_pool.close()
        await engine.dispose()


def _fixture(settings: Settings) -> Interpreter | None:
    """Dev/demo/tests only: recorded interpretations replace Jev + LLM. Never in production."""
    if settings.environment != "production" and settings.interpretation_fixtures_dir:
        return FixtureInterpreter(settings.interpretation_fixtures_dir)
    return None


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Trading Agent", lifespan=lifespan)
    settings = settings or get_settings()
    app.state.settings = settings
    app.state.clock = SystemClock()
    app.state.auth_limiter = SlidingWindowLimiter(
        settings.auth_rate_limit_per_min, timedelta(minutes=1), app.state.clock
    )
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.middleware("http")(origin_check(settings.dashboard_origin))
    app.include_router(auth_routes.router, prefix="/api/v1")
    app.include_router(config_routes.router, prefix="/api/v1")
    app.include_router(events_routes.router, prefix="/api/v1")
    for read in (feed_routes, cards_routes, positions_routes, lots_routes, orders_routes):
        app.include_router(read.router, prefix="/api/v1")
    app.include_router(alerts_routes.router, prefix="/api/v1")
    app.include_router(health_routes.router, prefix="/api/v1")
    app.include_router(push_routes.router, prefix="/api/v1")
    app.include_router(ingest_routes.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
