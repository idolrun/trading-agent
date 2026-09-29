"""Global safety flags. Reads are uncached so a flag set is visible to the next order submit."""

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.db import in_transaction, notify
from app.runtime_config.models import Actor
from app.system.models import FlagName, SystemFlag, SystemFlagEvent

ACTIVATION_SEVERITY = {
    FlagName.KILL_SWITCH: Severity.URGENT,
    FlagName.ENTRIES_BLOCKED: Severity.URGENT,
    FlagName.FEED_DEGRADED: Severity.URGENT,
    FlagName.PARSER_DEGRADED: Severity.WARNING,
    FlagName.CATCH_UP: Severity.WARNING,
}
ENTRY_BLOCKERS = (FlagName.KILL_SWITCH, FlagName.ENTRIES_BLOCKED)


class FlagService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], alerts: AlertService
    ) -> None:
        self._sessions = session_factory
        self._alerts = alerts

    async def set(
        self,
        flag: FlagName,
        active: bool,
        reason: str,
        actor: Actor,
        *,
        session_id: uuid.UUID | None = None,
        db: AsyncSession | None = None,
    ) -> bool:
        """Set a flag. Idempotent: setting the current value records nothing (returns False)."""
        async with in_transaction(self._sessions, db) as session:
            changed = await session.scalar(
                update(SystemFlag)
                .where(col(SystemFlag.flag) == flag, col(SystemFlag.active) != active)
                .values(active=active, reason=reason, since=func.now(), updated_at=func.now())
                .returning(col(SystemFlag.flag))
            )
            if changed is None:
                return False
            session.add(
                SystemFlagEvent(
                    flag=flag, active=active, reason=reason, actor=actor, session_id=session_id
                )
            )
            await notify(session, "flags", flag)
            state = "activated" if active else "cleared"
            await self._alerts.raise_(
                AlertKind.SYSTEM_FLAG,
                ACTIVATION_SEVERITY[flag] if active else Severity.INFO,
                f"{flag} {state}: {reason}",
                details={"flag": flag, "active": active, "reason": reason, "actor": actor},
                db=session,
            )
        return True

    async def is_active(self, flag: FlagName, *, db: AsyncSession | None = None) -> bool:
        async with in_transaction(self._sessions, db) as session:
            active = await session.scalar(
                select(col(SystemFlag.active)).where(col(SystemFlag.flag) == flag)
            )
        return bool(active)

    async def active_reason(self, flag: FlagName, *, db: AsyncSession | None = None) -> str | None:
        """Why the flag is active now; None when it is not active."""
        async with in_transaction(self._sessions, db) as session:
            return await session.scalar(
                select(col(SystemFlag.reason)).where(
                    col(SystemFlag.flag) == flag, col(SystemFlag.active)
                )
            )

    async def entries_allowed(self, *, db: AsyncSession | None = None) -> tuple[bool, str | None]:
        """False (with reason for the disabled-card text) if kill switch or entries-blocked."""
        async with in_transaction(self._sessions, db) as session:
            rows = (
                await session.execute(
                    select(col(SystemFlag.flag), col(SystemFlag.reason))
                    .where(col(SystemFlag.flag).in_(ENTRY_BLOCKERS), col(SystemFlag.active))
                    .order_by(col(SystemFlag.flag).desc())  # kill_switch first
                )
            ).all()
        if not rows:
            return True, None
        return False, "; ".join(f"{flag}: {reason}" for flag, reason in rows)
