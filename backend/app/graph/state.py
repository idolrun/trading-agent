"""Message graph state (TRD §2.1). JSON values only (pydantic dumps), so a checkpoint never
needs custom deserializers; nodes rebuild the typed objects they need."""

from typing import Any, TypedDict


class MessageState(TypedDict, total=False):
    processing: dict[str, Any]  # ProcessingContext
    skip: bool  # not a create event: feed only until F038
    context: dict[str, Any]  # ProviderContext (C71)
    candidates: dict[str, Any]  # Candidates (F016)
    lot_ids: list[str]  # the user's open lot ids: scope-guard input only, never sent to a model
    route: str  # feed_only | assemble | slow_path | fixture
    hints: dict[str, Any] | None  # Jev answers for the slow path
    parsed: dict[str, Any] | None  # ParsedSignals
    interpreter: str | None  # jev | llm | none
    model_call_id: int | None
    raw_reason: str | None  # set -> raw review card
    card_ids: list[str]
    superseded_ids: list[str]  # cards this event invalidated: their approval graphs resume


def initial_state(processing: dict[str, Any]) -> MessageState:
    """Every key set, so a run never inherits values from an earlier run on the same thread."""
    return MessageState(
        processing=processing,
        skip=False,
        context={},
        candidates={},
        lot_ids=[],
        route="",
        hints=None,
        parsed=None,
        interpreter=None,
        model_call_id=None,
        raw_reason=None,
        card_ids=[],
        superseded_ids=[],
    )
