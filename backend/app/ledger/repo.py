"""Provider-position and lot queries. No ledger decisions here."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.ledger.models import ProviderPosition


async def recent_underlyings(db: AsyncSession, provider_id: int, since: datetime) -> list[str]:
    """Underlyings this provider opened positions in since `since` (C84 suggestion input)."""
    rows = await db.scalars(
        select(col(ProviderPosition.underlying))
        .where(
            col(ProviderPosition.provider_id) == provider_id,
            col(ProviderPosition.opened_at) >= since,
        )
        .distinct()
        .order_by(col(ProviderPosition.underlying))
    )
    return list(rows.all())
