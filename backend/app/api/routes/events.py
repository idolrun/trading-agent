from collections.abc import AsyncIterable

from fastapi import APIRouter, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.api.deps import SessionDep
from app.notify.bus import EventBus

router = APIRouter(tags=["events"])


@router.get("/events", response_class=EventSourceResponse)
async def events(request: Request, _ctx: SessionDep) -> AsyncIterable[ServerSentEvent]:
    """Live updates: `event` = card.created, alert.created, ..., `data` = {"id"}. `resync`
    means events may have been missed: refetch. Keep-alive pings every 15 s (FastAPI)."""
    bus: EventBus = request.app.state.bus
    # Node proxies (Next rewrites) hold the response headers until the first body byte; without
    # this the browser's `open` would wait for the 15 s keep-alive.
    yield ServerSentEvent(comment="connected")
    async for event in bus.stream():
        yield ServerSentEvent(event=event.type, data={"id": event.id})
