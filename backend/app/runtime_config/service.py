"""Runtime config use cases: cached read, audited update, LISTEN-driven cache refresh."""

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import listen_forever, transaction
from app.runtime_config import repo
from app.runtime_config.models import Actor
from app.runtime_config.schema import RuntimeConfig

logger = logging.getLogger(__name__)

Snapshot = tuple[int, RuntimeConfig]


class ConfigService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], listen_dsn: str | None = None
    ) -> None:
        self._sessions = session_factory
        self._listen_dsn = listen_dsn
        self._snapshot: Snapshot | None = None

    def get(self) -> RuntimeConfig | None:
        """Current config, or None if never initialized. No I/O."""
        return self._snapshot[1] if self._snapshot else None

    def snapshot(self) -> Snapshot | None:
        return self._snapshot

    async def load(self) -> None:
        async with self._sessions() as session:
            row = await repo.load(session)
        self._snapshot = (
            None if row is None else (row.version, RuntimeConfig.model_validate(row.document))
        )

    async def update(
        self,
        config: RuntimeConfig,
        *,
        expected_version: int,
        actor: Actor,
        reason: str,
        session_id: uuid.UUID | None = None,
    ) -> Snapshot:
        """Raises `repo.VersionConflict` when `expected_version` is stale."""
        document = config.model_dump(mode="json")
        async with self._sessions() as session, transaction(session):
            before = await repo.load(session)
            version = await repo.save(
                session,
                document=document,
                diff=_diff(before.document if before else {}, document),
                expected_version=expected_version,
                actor=actor,
                reason=reason,
                session_id=session_id,
            )
        self._snapshot = (version, config)
        logger.info("config updated", extra={"config_version": version, "actor": actor})
        return self._snapshot

    async def listen(self) -> None:
        """Reload on every `NOTIFY config`; reconnects forever. Run as a background task."""
        assert self._listen_dsn is not None  # noqa: S101 — constructed with a DSN by lifespan

        async def reload(_payload: str) -> None:
            await self.load()

        await listen_forever(self._listen_dsn, "config", reload, self.load)


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Top-level keys that changed: `{key: {"before": …, "after": …}}`."""
    return {
        key: {"before": before.get(key), "after": after.get(key)}
        for key in before.keys() | after.keys()
        if before.get(key) != after.get(key)
    }
