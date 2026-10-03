"""Evaluation kit CLI (F048). Reads the database and calls models; never a broker.

uv run python -m app.evalkit import history.json
uv run python -m app.evalkit prelabel --limit 200
uv run python -m app.evalkit replay --split dev [--model-ref anthropic:claude-x:extract@v1]
uv run python -m app.evalkit sweep --model-ref anthropic:claude-x:extract@v1
uv run python -m app.evalkit gate --split test --model-ref anthropic:claude-x:extract@v1
"""

import argparse
import asyncio
import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from pydantic import ValidationError

from app.alerts.service import AlertService
from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory
from app.evalkit.gate import run_gate, run_sweep
from app.evalkit.import_export import import_history
from app.evalkit.labels import latest_labels, prelabel_batch
from app.evalkit.models import Split
from app.evalkit.replay import measure, parse_model_ref, pin_extract
from app.graph.message_graph import GraphDeps
from app.interpret.jev.client import JevInterpreter
from app.interpret.llm_extract.agent import LlmInterpreter
from app.llm.chain import LlmChain
from app.logging import configure_logging
from app.marketdata.fake import FakeMarketData
from app.markettime import SystemClock
from app.pipeline.plan import Planner
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService
from app.system.flags import FlagService
from app.validate.service import ValidationService


@asynccontextmanager
async def stack(settings: Settings) -> AsyncIterator[tuple[GraphDeps, LlmChain]]:
    """The interpret side of the app, without approvals, notes, relink or corrections."""
    engine = create_engine(settings)
    try:
        sessions = create_session_factory(engine)
        config = ConfigService(sessions, allow_ungated_models=settings.ungated_models_allowed)
        await config.load()

        def runtime() -> RuntimeConfig:
            return config.get() or RuntimeConfig()

        clock = SystemClock()
        alerts = AlertService(sessions)
        flags = FlagService(sessions, alerts)
        # ponytail: fake market data until F051; only the LLM's optional lookups use it.
        market = FakeMarketData(clock)
        chain = LlmChain(sessions, settings, lambda: runtime().llm, clock)
        deps = GraphDeps(
            sessions=sessions,
            config=config,
            clock=clock,
            planner=Planner(
                sessions,
                validation=ValidationService(
                    sessions, marketdata=market, alerts=alerts, clock=clock
                ),
                marketdata=market,
                flags=flags,
            ),
            flags=flags,
            llm=LlmInterpreter(chain, market, alerts),
            jev=JevInterpreter(sessions, settings, runtime, clock),
        )
        yield deps, chain
    finally:
        await engine.dispose()


async def run(args: argparse.Namespace, payloads: list[object] | None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    async with stack(settings) as (deps, chain):
        match args.command:
            case "import":
                assert payloads is not None  # noqa: S101 — `import` always parses a file first
                result = await import_history(deps.sessions, payloads, deps.clock.now())
                print(result)
            case "prelabel":
                recent_n = deps.cfg().context_recent_messages
                answered = await prelabel_batch(
                    deps.sessions, chain, limit=args.limit, recent_n=recent_n
                )
                print(f"prelabeled {answered} messages")
            case "replay":
                cfg = None
                if args.model_ref:
                    cfg = pin_extract(deps.cfg(), parse_model_ref(args.model_ref))
                async with deps.sessions() as db:
                    events = await latest_labels(db)
                print(json.dumps(await measure(deps, events, Split(args.split), cfg), indent=2))
            case "sweep":
                sweep = await run_sweep(deps, args.model_ref)
                print(f"sweep {sweep.eval_run_id}: best {sweep.thresholds['best']}")
            case "gate":
                gate = await run_gate(deps, args.model_ref)
                for c in gate.checks:
                    print(f"{'ok  ' if c.passed else 'FAIL'} {c.name}: {c.value} (need {c.need})")
                print(f"gate run {gate.run_id}: {'PASSED' if gate.passed else 'FAILED'}")
                if gate.refresh_test_set:
                    print("refresh test set: 3 failed gate runs in a row on this test set (C67)")
                return 0 if gate.passed else 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.evalkit")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("import", help="store a historical export as backfilled events").add_argument(
        "file"
    )
    sub.add_parser("prelabel", help="LLM suggestions for unlabeled messages").add_argument(
        "--limit", type=int, default=100
    )
    replay = sub.add_parser("replay", help="replay a split and print its metrics")
    replay.add_argument("--split", choices=[s.value for s in Split], default="dev")
    replay.add_argument("--model-ref")
    sub.add_parser("sweep", help="t_chat/t_field grid on the dev split").add_argument(
        "--model-ref", required=True
    )
    gate = sub.add_parser("gate", help="PRD §17 stage-1 gate on the test split")
    gate.add_argument("--split", choices=["test"], default="test")
    gate.add_argument("--model-ref", required=True)
    args = parser.parse_args(argv)
    payloads = None
    try:
        if getattr(args, "model_ref", None):
            parse_model_ref(args.model_ref)
        if args.command == "import":
            payloads = json.loads(Path(args.file).read_text())
            if not isinstance(payloads, list):
                raise ValueError("the export must be a JSON list of message payloads")
    except (ValidationError, ValueError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 2
    return asyncio.run(run(args, payloads))


if __name__ == "__main__":
    sys.exit(main())
