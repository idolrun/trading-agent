"""Guardrail middleware, identical for every provider (TRD §5.5)."""

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    dynamic_prompt,
    wrap_model_call,
)
from langchain_core.messages import AIMessage

from app.interpret.context import FORBIDDEN_KEYS
from app.interpret.llm_extract.tools import ToolScope
from app.llm.chain import ContentRejected
from app.llm.models import CallOutcome

_FORBIDDEN_KEY = re.compile(
    r'\\?"(' + "|".join(sorted(FORBIDDEN_KEYS)) + r')\\?"\s*:', re.IGNORECASE
)


class ScopeViolation(ContentRejected):
    """User-portfolio data in an outgoing model request (FR-8). A bug, not a model failure:
    nothing is sent, the chain stops, the caller alerts (urgent) and makes a raw review card."""

    def __init__(self, reason: str) -> None:
        super().__init__(CallOutcome.SCOPE_BLOCKED, reason)


def scope_violation(payload: str, lot_ids: frozenset[str]) -> str | None:
    if m := _FORBIDDEN_KEY.search(payload):
        return f"forbidden key {m.group(1)!r} in model request"
    if leaked := sorted(i for i in lot_ids if i in payload):
        return f"lot id {leaked[0]} in model request"
    return None


def request_text(request: ModelRequest[Any]) -> str:
    """Everything that would leave the process: system prompt, messages, tool-call args."""
    parts = [request.system_message.text if request.system_message else ""]
    for m in request.messages:
        parts.append(m.text)
        if isinstance(m, AIMessage):
            parts += [json.dumps(tc["args"], default=str) for tc in m.tool_calls]
    return "\n".join(parts)


@wrap_model_call
async def scope_guard(
    request: ModelRequest[ToolScope],
    handler: Callable[[ModelRequest[ToolScope]], Awaitable[ModelResponse]],
) -> ModelResponse:
    """Innermost wrapper, so it sees the final request (after the dynamic prompt)."""
    scope: ToolScope = request.runtime.context
    if reason := scope_violation(request_text(request), scope.lot_ids):
        raise ScopeViolation(reason)
    return await handler(request)


def volatile_context(scope: ToolScope) -> str:
    """Per-message context, byte-stable for equal input (sorted keys, no floats)."""
    ctx = scope.context
    doc: dict[str, Any] = ctx.model_dump(mode="json")
    doc["candidates"] = scope.candidates
    doc["jev_hints"] = scope.hints
    return json.dumps(doc, sort_keys=True, ensure_ascii=False)


def extraction_prompt(static: str) -> AgentMiddleware[Any, Any]:
    """Static prompt + few-shot first (cacheable prefix), volatile context last (rule 7)."""

    @dynamic_prompt
    def prompt(request: ModelRequest[ToolScope]) -> str:
        return f"{static}\n\n## Context\n{volatile_context(request.runtime.context)}"

    return prompt
