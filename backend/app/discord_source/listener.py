"""Discord gateway listener: watched-channel messages go straight into the inbox (FR-1).

Same path as `POST /ingest`: provisional payload -> `to_envelope` -> `inbox.accept`, commit, then
feed-health note. The channel allowlist is `RuntimeConfig.channels` (the dispatcher enforces it
too); filtering here keeps unrelated servers out of the audit table."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import discord
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.db import in_transaction
from app.discord_source import mapping
from app.inbox import repo as inbox
from app.inbox.models import MessageEvent
from app.ingest.adapter import UnmappablePayload, to_envelope
from app.ingest.feed_health import FeedHealthMonitor
from app.markettime import Clock
from app.runtime_config.service import ConfigService

logger = logging.getLogger(__name__)

HEARTBEAT_S = 30.0
State = Literal["not_configured", "connecting", "connected", "login_failed"]


@dataclass(frozen=True)
class ChannelStatus:
    id: int
    name: str | None
    guild: str | None
    visible: bool


@dataclass(frozen=True)
class ListenerStatus:
    state: State
    account: str | None
    last_message_at: datetime | None
    channels: list[ChannelStatus]


class _Client(discord.Client):
    """Forwards gateway events to the listener (discord.py dispatches by method name)."""

    def __init__(self, listener: "DiscordListener") -> None:
        super().__init__()
        self._listener = listener

    async def on_ready(self) -> None:
        self._listener.connected()

    async def on_resumed(self) -> None:
        self._listener.connected()

    async def on_disconnect(self) -> None:
        self._listener.disconnected()

    async def on_message(self, message: discord.Message) -> None:
        await self._listener.handle(mapping.create_payload(message))

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        if payload.message is not None:
            await self._listener.handle(mapping.edit_payload(payload.message))

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        await self._listener.handle_delete(payload)


class DiscordListener:
    def __init__(
        self,
        token: SecretStr | None,
        *,
        sessions: async_sessionmaker[AsyncSession],
        config: ConfigService,
        feed_health: FeedHealthMonitor,
        alerts: AlertService,
        clock: Clock,
    ) -> None:
        self._token = token if token and token.get_secret_value() else None  # blank = off
        self._sessions = sessions
        self._config = config
        self._feed_health = feed_health
        self._alerts = alerts
        self._clock = clock
        self._client: _Client | None = None
        self._state: State = "connecting" if self._token else "not_configured"
        self._ready = asyncio.Event()
        self.last_message_at: datetime | None = None

    # --- lifecycle -------------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._token is not None

    async def run(self) -> None:
        if self._token is None:
            return
        client = self._client = _Client(self)
        heartbeat = asyncio.create_task(self._heartbeat(), name="discord-heartbeat")
        try:
            await client.start(self._token.get_secret_value())
        except discord.LoginFailure:
            self._state = "login_failed"
            self._ready.clear()
            logger.error("discord login failed; listener stopped")
            await self._alerts.raise_(
                AlertKind.MESSAGE_API,
                Severity.URGENT,
                "Discord login failed (bad or revoked DISCORD_TOKEN); no messages are read",
                dedupe_key="discord:login_failed",
            )
        finally:
            heartbeat.cancel()
            await client.close()

    async def ready_client(self, timeout_s: float) -> discord.Client | None:
        """The connected client, waiting up to `timeout_s` (startup backfill races the login)."""
        try:
            await asyncio.wait_for(self._ready.wait(), timeout_s)
        except TimeoutError:
            return None
        return self._client

    def connected(self) -> None:
        self._state = "connected"
        self._ready.set()
        self._feed_health.note_heartbeat(self._clock.now())

    def disconnected(self) -> None:
        if self._state == "connected":
            self._state = "connecting"
            self._ready.clear()

    async def _heartbeat(self) -> None:
        """A quiet channel must not look like a dead feed: liveness is the gateway session."""
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            if self._state == "connected":
                self._feed_health.note_heartbeat(self._clock.now())

    # --- events ----------------------------------------------------------------------------

    def watching(self, channel_id: int) -> bool:
        config = self._config.get()
        return config is not None and channel_id in config.channels

    async def handle(self, payload: dict[str, Any] | None) -> None:
        """`payload["message"]["channel_id"]` is the context channel: a thread's parent."""
        if payload is None:
            return
        channel_id = payload["message"]["channel_id"]
        if not self.watching(channel_id):
            return
        try:
            env = to_envelope(payload, self._clock.now())
        except UnmappablePayload:
            logger.exception(
                "discord message could not be mapped", extra={"channel_id": channel_id}
            )
            return
        try:
            async with in_transaction(self._sessions, None) as db:
                await inbox.accept(db, env)
        except (SQLAlchemyError, OSError):
            # Not redelivered by Discord: the gap detector backfills it from channel history.
            logger.exception(
                "discord message storage failed", extra={"message_id": env.key.message_id}
            )
            return
        now = self._clock.now()
        self.last_message_at = now
        self._feed_health.note_message(now)

    async def handle_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        """A raw delete in a thread names the thread, not its parent: the stored event (or the
        cached message) says which context it belonged to."""
        cached = payload.cached_message
        if cached is not None:
            channel_id, thread_id = mapping.context_channel(cached.channel)
            identity: tuple[int, int, int | None] | None = (cached.author.id, channel_id, thread_id)
        else:
            identity = await self._stored_identity(payload.message_id)
        if identity is None:
            return  # never stored, nothing to correct
        author_id, channel_id, thread_id = identity
        await self.handle(
            mapping.delete_payload(
                payload.message_id, channel_id, author_id, self._clock.now(), thread_id
            )
        )

    async def _stored_identity(self, message_id: int) -> tuple[int, int, int | None] | None:
        async with self._sessions() as db:
            row = (
                await db.execute(
                    select(
                        col(MessageEvent.provider_id),
                        col(MessageEvent.channel_id),
                        col(MessageEvent.thread_id),
                    )
                    .where(col(MessageEvent.message_id) == message_id)
                    .limit(1)
                )
            ).first()
            return None if row is None else (row[0], row[1], row[2])

    # --- read side -------------------------------------------------------------------------

    def status(self) -> ListenerStatus:
        client = self._client
        channels: list[ChannelStatus] = []
        config = self._config.get()
        for channel_id in config.channels if config else []:
            channel = client.get_channel(channel_id) if client is not None else None
            guild = getattr(channel, "guild", None)
            channels.append(
                ChannelStatus(
                    id=channel_id,
                    name=getattr(channel, "name", None),
                    guild=guild.name if guild is not None else None,
                    visible=channel is not None,
                )
            )
        user = client.user if client is not None else None
        return ListenerStatus(
            self._state, str(user) if user is not None else None, self.last_message_at, channels
        )
