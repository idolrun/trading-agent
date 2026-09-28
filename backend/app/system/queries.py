"""System health for the dashboard (PRD §16; latency and LLM stats come with F049)."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.health import HealthDetails
from app.inbox import repo as inbox
from app.ingest.feed_health import FeedHealthMonitor
from app.system.heartbeat import last_beat
from app.system.models import FlagName, SystemFlag

DEGRADING = (FlagName.FEED_DEGRADED, FlagName.PARSER_DEGRADED, FlagName.ENTRIES_BLOCKED)


async def health_details(
    db: AsyncSession, *, feed: FeedHealthMonitor | None, now: datetime
) -> HealthDetails:
    flags = {f.flag: f.active for f in await db.scalars(select(SystemFlag))}
    backlog = await inbox.backlog(db)
    beat = await last_beat(db)
    return HealthDetails(
        status="degraded" if any(flags.get(f) for f in DEGRADING) else "ok",
        feed_silence_seconds=(now - feed.last_message_at).total_seconds() if feed else None,
        flags=flags,
        catch_up=bool(flags.get(FlagName.CATCH_UP)),
        backlog_depth=backlog.count,
        oldest_unprocessed_seconds=(now - backlog.oldest_posted_at).total_seconds()
        if backlog.oldest_posted_at
        else None,
        heartbeat_age_seconds=(now - beat).total_seconds() if beat else None,
    )
