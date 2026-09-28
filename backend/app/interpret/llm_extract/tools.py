"""Read-only tools for the extraction agent (TRD §5.6). Scoped by `ToolRuntime.context` to the
message author's C71 context: no tool can reach another provider, the user's lots, or write."""

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from app.interpret.candidates import Candidates
from app.interpret.context import ProviderContext
from app.interpret.llm_extract.schema import context_texts
from app.marketdata.port import MarketData, Quote, QuoteUnavailable

CHAIN_ROWS = 40  # nearest strikes returned by get_option_chain


@dataclass(frozen=True)
class ToolScope:
    """Agent runtime context: never shown to the model as-is; read by tools and middleware."""

    message_id: str
    author_id: str
    context: ProviderContext
    candidates: dict[str, Any] = field(default_factory=dict)  # F016, for the prompt
    hints: dict[str, Any] | None = None  # Jev answers (F033)
    lot_ids: frozenset[str] = frozenset()  # for the scope guard only, never in a prompt

    @classmethod
    def of(
        cls,
        ctx: ProviderContext,
        candidates: Candidates,
        hints: dict[str, Any] | None = None,
        lot_ids: frozenset[str] = frozenset(),
    ) -> "ToolScope":
        return cls(
            ctx.message.id, ctx.message.author_id, ctx, candidates.model_dump(), hints, lot_ids
        )


def _quote(q: Quote) -> dict[str, str | None]:
    return {
        k: None if v is None else str(v)
        for k, v in (("bid", q.bid), ("ask", q.ask), ("last", q.last))
    }


def build_tools(marketdata: MarketData) -> list[BaseTool]:
    @tool
    async def get_option_chain(underlying: str, expiry: str) -> list[dict[str, Any]] | str:
        """Listed option contracts (strike, right, bid, ask) for an underlying and an expiry
        date (YYYY-MM-DD), nearest the underlying price first."""
        try:
            day = date.fromisoformat(expiry)
            spot = await marketdata.underlying_price(underlying.upper())
            rows = await marketdata.chain(underlying.upper(), day)
        except (ValueError, QuoteUnavailable) as exc:
            return f"unavailable: {exc}"
        mid = spot.last or spot.bid or spot.ask
        if mid is not None:
            rows.sort(key=lambda r: abs(r[0].strike - mid))
        return [
            {"strike": str(c.strike), "right": c.right, **_quote(q)} for c, q in rows[:CHAIN_ROWS]
        ]

    @tool
    async def get_quote(symbol: str) -> dict[str, str | None] | str:
        """Current bid/ask/last of a stock or index symbol."""
        try:
            return _quote(await marketdata.underlying_price(symbol.upper()))
        except QuoteUnavailable as exc:
            return f"unavailable: {exc}"

    @tool
    def get_author_messages(n: int, runtime: ToolRuntime[ToolScope]) -> list[dict[str, str]]:
        """The author's last `n` messages before this one, oldest first."""
        recent = runtime.context.context.recent_author_messages
        return [m.model_dump() for m in recent[-n:]] if n > 0 else []  # window = config cap

    @tool
    def get_provider_positions(runtime: ToolRuntime[ToolScope]) -> list[dict[str, str]]:
        """This author's open positions (their own calls, not the user's holdings)."""
        return [
            {"id": str(b.id), "summary": b.summary} for b in runtime.context.context.provider_book
        ]

    @tool
    def get_message(message_id: str, runtime: ToolRuntime[ToolScope]) -> str:
        """Text of an earlier message, only if it is in this message's context."""
        return context_texts(runtime.context.context).get(message_id, "not in context")

    return [get_option_chain, get_quote, get_author_messages, get_provider_positions, get_message]
