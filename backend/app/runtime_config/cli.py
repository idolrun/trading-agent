"""Console CLI for runtime config (actor `cli`).

uv run python -m app.runtime_config.cli show
uv run python -m app.runtime_config.cli set --file config.json --reason "initial tiers"
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from app.config import get_settings
from app.db import create_engine, create_session_factory
from app.runtime_config.models import Actor
from app.runtime_config.repo import VersionConflict
from app.runtime_config.schema import RuntimeConfig, missing_for_entries
from app.runtime_config.service import ConfigInvalid, ConfigService


async def run(args: argparse.Namespace, new_config: RuntimeConfig | None) -> int:
    settings = get_settings()
    engine = create_engine(settings)
    service = ConfigService(
        create_session_factory(engine), allow_ungated_models=settings.ungated_models_allowed
    )
    try:
        await service.load()
        snapshot = service.snapshot()
        if args.command == "show":
            print(
                json.dumps(
                    {
                        "version": snapshot[0] if snapshot else None,
                        "document": snapshot[1].model_dump(mode="json") if snapshot else None,
                        "missing_for_entries": missing_for_entries(service.get()),
                    },
                    indent=2,
                )
            )
            return 0
        assert new_config is not None  # noqa: S101 — `set` always parses a file first
        try:
            version, config = await service.update(
                new_config,
                expected_version=snapshot[0] if snapshot else 0,
                actor=Actor.CLI,
                reason=args.reason,
            )
        except VersionConflict:
            print("config changed concurrently; run again", file=sys.stderr)
            return 1
        except ConfigInvalid as exc:
            print(f"config rejected: {exc}", file=sys.stderr)
            return 1
        print(f"config version {version}; missing for entries: {missing_for_entries(config)}")
        return 0
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.runtime_config.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show", help="print the current config and what is still missing")
    set_cmd = sub.add_parser("set", help="replace the config with a JSON document")
    set_cmd.add_argument("--file", required=True)
    set_cmd.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    new_config = None
    if args.command == "set":
        try:
            new_config = RuntimeConfig.model_validate_json(Path(args.file).read_text())
        except ValidationError as exc:
            print(exc, file=sys.stderr)
            return 2
    return asyncio.run(run(args, new_config))


if __name__ == "__main__":
    sys.exit(main())
