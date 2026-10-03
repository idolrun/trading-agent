"""Jev state (TRD §4.2): the C71 provider context plus code-found candidates. Never the user's
portfolio (FR-8): same forbidden-key/lot-id check as the LLM scope guard."""

import json
from typing import Any

from app.interpret.candidates import Candidate, Candidates
from app.interpret.context import ProviderContext
from app.interpret.llm_extract.middleware import ScopeViolation, scope_violation


def _candidate(c: Candidate) -> dict[str, Any]:
    return {"id": c.id, "text": c.text, "pos": c.start}


def build_jev_state(
    ctx: ProviderContext, candidates: Candidates, lot_ids: frozenset[str] = frozenset()
) -> dict[str, Any]:
    m = ctx.message
    state: dict[str, Any] = {
        "now_et": ctx.now_et,
        "message": {
            "id": m.id,
            "author": m.author_id,
            "channel": m.channel_id,
            "text": m.text,
            "reply_to": m.reply_to,
            "thread": m.thread_id,
        },
        "reply_parent": None if ctx.reply_parent is None else ctx.reply_parent.model_dump(),
        "recent_author_messages": [
            {"id": r.id, "at": r.at_et, "text": r.text} for r in ctx.recent_author_messages
        ],
        # F078: the resolver-narrowed book only (the caller passes link candidates as the book)
        "provider_book": [{"id": str(b.id), "summary": b.summary} for b in ctx.provider_book],
        "reentry_candidates": [
            {"id": str(b.id), "summary": b.summary} for b in ctx.reentry_candidates
        ],
        "recent_signal_events": [
            {"position": str(e.position_id), "at": e.at_et, "what": e.summary}
            for e in ctx.recent_signal_events
        ],
        "provider_profile": ctx.provider_profile,
        "candidates": {
            "tickers": [_candidate(c) for c in candidates.tickers],
            "numbers": [_candidate(c) for c in candidates.numbers],
            "expiry_tokens": [_candidate(c) for c in candidates.expiry_tokens],
            "fraction_words": [_candidate(c) for c in candidates.fraction_words],
        },
    }
    if reason := scope_violation(json.dumps(state), lot_ids):
        raise ScopeViolation(reason)
    return state
