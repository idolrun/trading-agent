"""Message graph state (TRD §2.1). JSON values only (pydantic dumps), so a checkpoint never
needs custom deserializers; nodes rebuild the typed objects they need."""

from typing import Any, TypedDict


class MessageState(TypedDict, total=False):
    processing: dict[str, Any]  # ProcessingContext
    recheck: bool  # F037: interpret only, END before decide_apply (no writes)
    replay: bool  # F048: recheck plus no writes at all (evalkit); context book from GraphDeps.book
    skip: bool  # a delete event (F038): straight to notify
    context: dict[str, Any]  # ProviderContext (C71)
    resolution: dict[str, Any] | None  # Resolution (F069): the code's verdict on the target
    candidates: dict[str, Any]  # Candidates (F016)
    lot_ids: list[str]  # the user's open lot ids: scope-guard input only, never sent to a model
    route: str  # fast | feed_only | assemble | slow_path | fixture
    route_reasons: list[str]  # why the fast path did not settle the message
    jev_answered: bool  # Jev returned an answer (False: down, timed out, disabled)
    hints: dict[str, Any] | None  # Jev answers for the slow path
    parsed: dict[str, Any] | None  # ParsedSignals
    interpreter: str | None  # fast | jev | laya | llm | none
    model_call_id: int | None
    raw_reason: str | None  # set -> raw review card
    card_ids: list[str]
    superseded_ids: list[str]  # cards this event invalidated: their approval graphs resume


def initial_state(
    processing: dict[str, Any], *, recheck: bool = False, replay: bool = False
) -> MessageState:
    """Every key set, so a run never inherits values from an earlier run on the same thread."""
    return MessageState(
        processing=processing,
        recheck=recheck,
        replay=replay,
        skip=False,
        context={},
        resolution=None,
        candidates={},
        lot_ids=[],
        route="",
        route_reasons=[],
        jev_answered=False,
        hints=None,
        parsed=None,
        interpreter=None,
        model_call_id=None,
        raw_reason=None,
        card_ids=[],
        superseded_ids=[],
    )
