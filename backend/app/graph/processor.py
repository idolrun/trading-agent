"""`GraphProcessor`: the F012 `Processor` that runs the message graph for one event."""

import uuid
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from app.dispatcher.types import ProcessingContext
from app.graph.message_graph import GraphDeps, build_message_graph, thread_id
from app.graph.state import initial_state
from app.interpret.signal import ParsedSignals


class GraphProcessor:
    def __init__(self, deps: GraphDeps, checkpointer: Any = None) -> None:
        self.deps = deps
        self._durable = checkpointer is not None
        self._graph: CompiledStateGraph[Any, Any, Any, Any] = build_message_graph(
            deps, checkpointer
        )

    async def process(self, ctx: ProcessingContext) -> None:
        # durability "exit": one checkpoint at the end; per-node writes stay off the latency path
        await self._graph.ainvoke(
            initial_state(ctx.model_dump(mode="json")),
            config={"configurable": {"thread_id": thread_id(ctx)}},
            durability="exit" if self._durable else None,
        )

    async def recheck(self, ctx: ProcessingContext) -> ParsedSignals | None:
        """F037: re-interpret an applied event against today's context; writes nothing.
        None = no interpretation (interpreter down / raw review)."""
        key = ctx.envelope.key
        state = await self._graph.ainvoke(
            initial_state(ctx.model_dump(mode="json"), recheck=True),
            config={"configurable": {"thread_id": f"recheck:{key.message_id}:{uuid.uuid4()}"}},
            durability="exit" if self._durable else None,
        )
        if state.get("raw_reason") or state.get("parsed") is None:
            return None
        return ParsedSignals.model_validate(state["parsed"])
