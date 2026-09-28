"""Dispatcher contracts: what a processor receives and must do."""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from app.inbox.envelope import Envelope


class ProcessingContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    envelope: Envelope
    attempt: int  # 1-based inbox attempt
    started_at: datetime
    age_s: float  # processing start - post time (FR-1c)
    stale: bool  # age_s > stale_entry_s; the processor decides what stale means per type
    late: bool  # a later-posted event of this provider was already applied (FR-1b)


class Processor(Protocol):
    async def process(self, ctx: ProcessingContext) -> None:
        """Handle one event. Must mark the inbox row done/stale itself, inside its apply tx."""
        ...


class PoisonHandler(Protocol):
    async def on_poison(self, ctx: ProcessingContext, error: str) -> None:
        """Called once an event has failed `poison_retry_limit` times (FR-1e)."""
        ...
