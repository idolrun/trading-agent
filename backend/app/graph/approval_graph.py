"""The approval graph (TRD §5.1, OD-11, F036): one thread per card, `card:<card_id>`.

    START -> await_approval --interrupt--> resume value
               approve -> revalidate -> ok   -> execute -> END
                                     -> fail -> END  (card invalidated by the step)
               reject | expire | invalidate -> END   (card state already set by the resumer)

`await_approval` holds nothing before `interrupt()`: LangGraph re-runs a node from its start on
resume. Nodes are adapters over `ApprovalSteps` (F036 `ApprovalService`); no rules here.
"""

import uuid
from typing import Any, Protocol, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt


class ApprovalState(TypedDict, total=False):
    card_id: str
    decision: dict[str, Any]  # the resume value (TRD §5.1 table)
    revalidation: str | None  # failure reason, None = still valid
    order_ids: list[str]


class ApprovalSteps(Protocol):
    async def revalidate(self, card_id: uuid.UUID) -> str | None: ...

    async def execute(self, card_id: uuid.UUID) -> list[uuid.UUID]: ...


def thread_config(card_id: uuid.UUID) -> RunnableConfig:
    return {"configurable": {"thread_id": f"card:{card_id}"}}


def build_approval_graph(
    steps: ApprovalSteps, checkpointer: Any
) -> CompiledStateGraph[Any, Any, Any, Any]:
    async def await_approval(state: ApprovalState) -> dict[str, Any]:
        return {"decision": interrupt({"card_id": state["card_id"]})}

    async def revalidate(state: ApprovalState) -> dict[str, Any]:
        return {"revalidation": await steps.revalidate(uuid.UUID(state["card_id"]))}

    async def execute(state: ApprovalState) -> dict[str, Any]:
        ids = await steps.execute(uuid.UUID(state["card_id"]))
        return {"order_ids": [str(i) for i in ids]}

    g = StateGraph(ApprovalState)
    g.add_node("await_approval", await_approval)
    g.add_node("revalidate", revalidate)
    g.add_node("execute", execute)
    g.add_edge(START, "await_approval")
    g.add_conditional_edges(
        "await_approval",
        lambda s: "revalidate" if s["decision"].get("decision") == "approve" else END,
    )
    g.add_conditional_edges("revalidate", lambda s: END if s["revalidation"] else "execute")
    g.add_edge("execute", END)
    return g.compile(checkpointer=checkpointer)
