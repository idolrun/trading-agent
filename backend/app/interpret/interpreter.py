"""The interpreter seam: anything that turns a message + context into `ParsedSignals`
(fixture now; Jev / LLM in F032-F035). Output always goes through the one contract."""

from pathlib import Path
from typing import Protocol

from app.interpret.candidates import Candidates
from app.interpret.context import ProviderContext
from app.interpret.signal import ParsedSignals


class InterpreterUnavailable(Exception):
    """No interpreter can answer (FR-9): the caller makes a raw review card + alert."""


class Interpreter(Protocol):
    async def interpret(self, ctx: ProviderContext, candidates: Candidates) -> ParsedSignals: ...


class FixtureInterpreter:
    """`<dir>/<message_id>.json` holds the `ParsedSignals` (dev, demo, tests only; never in
    production). A missing file raises, so the dispatcher retries and then poisons: no event
    is silently dropped."""

    def __init__(self, directory: Path) -> None:
        self._dir = directory

    async def interpret(self, ctx: ProviderContext, candidates: Candidates) -> ParsedSignals:
        path = self._dir / f"{ctx.message.id}.json"
        return ParsedSignals.model_validate_json(path.read_text())
