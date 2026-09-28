"""The LLM slow path (TRD §4.6): a `create_agent` run per chain attempt, guardrails in
middleware, verification in code after the agent so it covers every provider."""

import logging
from functools import cache
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.interpret.candidates import Candidates
from app.interpret.context import ProviderContext
from app.interpret.llm_extract.middleware import extraction_prompt, scope_guard
from app.interpret.llm_extract.schema import LlmParsedSignals, to_parsed_signals
from app.interpret.llm_extract.tools import ToolScope, build_tools
from app.interpret.signal import ParsedSignals
from app.llm.capabilities import response_format_for
from app.llm.chain import Attempt, AttemptFailed, ChainResult, ContentRejected, LlmChain
from app.llm.models import CallOutcome
from app.llm.usage import CallRef, total_usage
from app.marketdata.port import MarketData
from app.runtime_config.schema import LlmProvider

logger = logging.getLogger(__name__)

PROMPTS = Path(__file__).parent / "prompts"
TOOL_CALL_LIMIT = 2  # TRD §5.5 latency bound
INSTRUCTION = "Report the signals in the current message."


@cache
def load_prompt(version: str) -> str:
    """`extract@v1` → llm_extract/prompts/extract_v1.md; `note@v1` → interpret/prompts/…"""
    name = f"{version.replace('@', '_')}.md"
    path = PROMPTS / name
    return (path if path.exists() else PROMPTS.parent.parent / "prompts" / name).read_text()


def build_agent(
    model: BaseChatModel, provider: LlmProvider, tools: list[BaseTool], prompt: str
) -> CompiledStateGraph[Any, Any, Any, Any]:
    return create_agent(
        model,
        tools,
        response_format=response_format_for(LlmParsedSignals, provider),
        # Wrappers run outer → inner: the scope guard is last so it sees the final request.
        middleware=[
            ToolCallLimitMiddleware(run_limit=TOOL_CALL_LIMIT),
            extraction_prompt(prompt),
            scope_guard,
        ],
        context_schema=ToolScope,
    )


class LlmInterpreter:
    def __init__(self, chain: LlmChain, marketdata: MarketData, alerts: AlertService) -> None:
        self._chain = chain
        self._tools = build_tools(marketdata)
        self._alerts = alerts
        self._agents: dict[tuple[int, str], CompiledStateGraph[Any, Any, Any, Any]] = {}

    async def extract(
        self,
        ctx: ProviderContext,
        candidates: Candidates,
        *,
        ref: CallRef,
        hints: dict[str, Any] | None = None,
        lot_ids: frozenset[str] = frozenset(),
    ) -> ChainResult[ParsedSignals]:
        """Run the `extract` chain. `ok` → verified `ParsedSignals`; `content_rejected` → review
        card (or raw review + urgent alert when the scope guard fired); `exhausted` → raw
        review card + parser degraded (FR-9). `lot_ids`: the user's open lot ids, for the scope
        guard only."""
        scope = ToolScope.of(ctx, candidates, hints, lot_ids)

        async def attempt(a: Attempt) -> ParsedSignals:
            agent = self._agent(a)
            a.request = {
                "prompt": a.entry.prompt,
                "instruction": INSTRUCTION,
                "scope": _audit(scope),
            }
            result = await agent.ainvoke({"messages": [HumanMessage(INSTRUCTION)]}, context=scope)
            a.usage = total_usage(result["messages"])
            out: LlmParsedSignals | None = result.get("structured_response")
            if out is None or not out.signals:
                raise AttemptFailed(CallOutcome.EMPTY, "no structured response")
            a.response = out.model_dump(mode="json")
            parsed, errors = to_parsed_signals(out, ctx)
            if errors:
                raise ContentRejected(CallOutcome.SPAN_REJECTED, "; ".join(errors))
            return parsed

        result = await self._chain.run("extract", attempt, ref=ref)
        if result.last is not None and result.last.outcome is CallOutcome.SCOPE_BLOCKED:
            await self._alerts.raise_(
                AlertKind.INVARIANT_BLOCK,
                Severity.URGENT,
                "LLM request blocked: it contained user-portfolio data (FR-8)",
                details={"reason": result.reason, "model_call_id": result.last.model_call_id},
                message_id=ref.message_id,
            )
        return result

    def _agent(self, a: Attempt) -> CompiledStateGraph[Any, Any, Any, Any]:
        key = (id(a.model), a.entry.prompt)
        if key not in self._agents:
            prompt = load_prompt(a.entry.prompt)
            self._agents[key] = build_agent(a.model, a.entry.provider, self._tools, prompt)
        return self._agents[key]


def _audit(scope: ToolScope) -> dict[str, Any]:
    """The request as stored in `model_calls` (C71-scoped; lot ids are never stored)."""
    return {
        "context": scope.context.model_dump(mode="json"),
        "candidates": scope.candidates,
        "hints": scope.hints,
    }
