"""`GraphProcessor`: the F012 `Processor` that runs the message graph for one event."""

from typing import Any

from langgraph.graph.state import CompiledStateGraph

from app.dispatcher.types import ProcessingContext
from app.graph.message_graph import GraphDeps, build_message_graph, thread_id
from app.graph.state import initial_state


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
