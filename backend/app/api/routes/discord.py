"""Discord listener status. Watched channels are `RuntimeConfig.channels` (`/config`)."""

from fastapi import APIRouter, Request

from app.api.deps import SessionDep
from app.api.schemas.discord import DiscordChannel, DiscordStatus
from app.discord_source.listener import DiscordListener

router = APIRouter(prefix="/discord", tags=["discord"])


@router.get("/status")
async def status(request: Request, _ctx: SessionDep) -> DiscordStatus:
    listener: DiscordListener = request.app.state.discord
    current = listener.status()
    return DiscordStatus(
        state=current.state,
        account=current.account,
        last_message_at=current.last_message_at,
        channels=[
            DiscordChannel(id=c.id, name=c.name, guild=c.guild, visible=c.visible)
            for c in current.channels
        ],
    )
