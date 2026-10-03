"""Laya state: the same JSON paths the Jev questions point at (`message.text`,
`recent_author_messages`, `provider_book`), cut to fit a 512-token window that right-truncates.
Candidate spans are not repeated: each question option already quotes its own candidate."""

import json
from typing import Any

from app.interpret.context import ProviderContext
from app.interpret.llm_extract.middleware import ScopeViolation, scope_violation

RECENT = 3  # earlier messages of the provider that correction_target may point at
RECENT_CHARS = 120
PARENT_CHARS = 160
MAX_BOOK = 5  # links_to gets one option per candidate position; more cannot fit: Laya abstains


def trim_context(ctx: ProviderContext) -> ProviderContext:
    """The context Laya sees; questions must be built from this same object."""
    return ctx.model_copy(update={"recent_author_messages": ctx.recent_author_messages[-RECENT:]})


def build_laya_state(ctx: ProviderContext, lot_ids: frozenset[str] = frozenset()) -> dict[str, Any]:
    state: dict[str, Any] = {"message": {"text": ctx.message.text}}
    if ctx.reply_parent is not None:
        state["reply_parent"] = {"text": ctx.reply_parent.text[:PARENT_CHARS]}
    if ctx.recent_author_messages:
        state["recent_author_messages"] = [
            {"id": r.id, "text": r.text[:RECENT_CHARS]} for r in ctx.recent_author_messages
        ]
    if ctx.provider_book:  # the resolver-narrowed book (F078)
        state["provider_book"] = [
            {"id": str(b.id), "summary": b.summary} for b in ctx.provider_book
        ]
    if ctx.reentry_candidates:
        state["reentry_candidates"] = [b.summary for b in ctx.reentry_candidates]
    if reason := scope_violation(json.dumps(state), lot_ids):
        raise ScopeViolation(reason)
    return state
