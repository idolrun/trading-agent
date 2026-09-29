"""Provider-position, link and provider-profile corrections (F047, PRD 7.9, C73, TRD §5.3, T7).

Each correction is one transaction: the change, its `manual_corrections` audit row, its ledger
history and, when it changes how a message was interpreted, an `eval_labels` row (source
`provider_correction`) so the correction feeds evaluation. The store is written only here, from
the user's own action; a model never writes it (TRD §5.3).
"""

import uuid
from dataclasses import replace
from typing import Any

from langgraph.store.base import BaseStore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.cards.approval_service import ApprovalService
from app.db import in_transaction
from app.evalkit.models import EvalLabel, LabelSource
from app.evalkit.split import split_for
from app.graph.message_graph import PROFILE_KEYS
from app.inbox.models import MessageEvent
from app.interpret.models import LinkMethod, Signal
from app.ledger import repo as ledger
from app.ledger.models import PositionChange, PositionStatus, ProviderPosition
from app.ledger.positions import PositionState, PositionUpdate, apply_correction, state_of
from app.ledger.relink import RelinkFailed, RelinkService
from app.manual.models import CorrectionTarget, ManualCorrection
from app.manual.service import ManualInvalid, ManualNotFound
from app.markettime import Clock, to_et
from app.pipeline.apply import BookChanged


class Corrections:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        approvals: ApprovalService,
        relink: RelinkService,
        store: BaseStore,
        clock: Clock,
    ) -> None:
        self._sessions = sessions
        self._approvals = approvals
        self._relink = relink
        self._store = store
        self._clock = clock

    # --- provider positions (C73) --------------------------------------------------------

    async def add_position(
        self, provider_id: int, changes: dict[str, Any], reason: str, session_id: uuid.UUID
    ) -> uuid.UUID:
        update = _corrected(None, changes)
        position_id = uuid.uuid4()
        async with in_transaction(self._sessions, None) as db:
            audit = await _audit(
                db,
                CorrectionTarget.PROVIDER_POSITION,
                str(position_id),
                "add",
                None,
                update.after.model_dump(mode="json"),
                reason,
                session_id,
            )
            await ledger.record(
                db,
                provider_id=provider_id,
                row=None,
                update=update,
                manual_correction_id=audit.id,
                position_id=position_id,
                at=self._clock.now(),
            )
        return position_id

    async def edit_position(
        self, position_id: uuid.UUID, changes: dict[str, Any], reason: str, session_id: uuid.UUID
    ) -> None:
        """Edit fields and/or `status` (closing a position is `status: closed`)."""
        async with in_transaction(self._sessions, None) as db:
            row = await db.scalar(
                select(ProviderPosition)
                .where(col(ProviderPosition.provider_position_id) == position_id)
                .with_for_update()
            )
            if row is None:
                raise ManualNotFound("position not found")
            if row.status == PositionStatus.CLOSED:
                raise ManualInvalid("position is closed")
            before = state_of(row)
            update = _corrected(before, changes)
            closing = update.after.status == PositionStatus.CLOSED
            if closing:
                update = replace(update, change=PositionChange.CLOSE)
            after = update.after.model_dump(mode="json")
            audit = await _audit(
                db,
                CorrectionTarget.PROVIDER_POSITION,
                str(position_id),
                "close" if closing else "edit",
                before.model_dump(mode="json"),
                after,
                reason,
                session_id,
            )
            await ledger.record(
                db,
                provider_id=row.provider_id,
                row=row,
                update=update,
                manual_correction_id=audit.id,
                at=self._clock.now(),
            )
            sig = await db.get(Signal, row.opened_by_signal_id) if row.opened_by_signal_id else None
            if sig is not None and changes.keys() - {"status"}:  # the opening message was misread
                corrected = {k: after[k] for k in changes if k != "status"}
                await _label(
                    db,
                    sig,
                    {"signals": [_signal(sig) | {"corrected": corrected}], "link": None},
                    session_id,
                )

    # --- links ---------------------------------------------------------------------------

    async def link(
        self, signal_id: uuid.UUID, position_id: uuid.UUID, reason: str, session_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """Re-link a signal (new `user_correction` link, ledger effect, follow-up cards);
        returns the new card ids. A position of another provider is refused."""
        async with self._sessions() as db:
            sig = await db.get(Signal, signal_id)
            position = await db.get(ProviderPosition, position_id)
        if sig is None or position is None:
            raise ManualNotFound("signal or position not found")
        if position.provider_id != sig.provider_id:
            raise ManualInvalid("a signal can only link to a position of its own provider")

        async def audit(db: AsyncSession) -> None:
            latest = await ledger.latest_link(db, signal_id)
            before = (
                {
                    "provider_position_id": str(latest.provider_position_id)
                    if latest.provider_position_id
                    else None,
                    "method": latest.method,
                }
                if latest
                else None
            )
            await _audit(
                db,
                CorrectionTarget.MESSAGE_LINK,
                str(signal_id),
                "relink",
                before,
                {"provider_position_id": str(position_id), "method": LinkMethod.USER_CORRECTION},
                reason,
                session_id,
            )
            link = {"idx": sig.idx, "provider_position_id": str(position_id)}
            await _label(db, sig, {"signals": [_signal(sig)], "link": link}, session_id)

        try:
            created, superseded = await self._relink.apply_approved(
                sig.provider_id, position_id, signal_id, audit=audit
            )
        except (RelinkFailed, BookChanged) as exc:
            raise ManualInvalid(str(exc)) from None
        await self._approvals.invalidated(superseded, None)
        await self._approvals.start_many(created)
        return created

    # --- provider profile (TRD §5.3) -----------------------------------------------------

    async def set_profile(
        self,
        provider_id: int,
        profile: dict[str, list[str]],
        reason: str,
        session_id: uuid.UUID,
    ) -> dict[str, list[str]]:
        """Write the given keys of the provider's store namespace; returns the whole profile."""
        if not profile:
            raise ManualInvalid("nothing to change")
        async with in_transaction(self._sessions, None) as db:
            before = await self.profile(provider_id)
            await _audit(
                db,
                CorrectionTarget.PROVIDER_PROFILE,
                str(provider_id),
                "update",
                {key: before.get(key) for key in profile},
                dict(profile),
                reason,
                session_id,
            )
            for key, items in profile.items():  # inside the transaction: a failed put rolls
                await self._store.aput(_namespace(provider_id), key, {"items": items})  # back
        return await self.profile(provider_id)

    async def profile(self, provider_id: int) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for key in PROFILE_KEYS:
            item = await self._store.aget(_namespace(provider_id), key)
            if item is not None:
                found[key] = [str(v) for v in item.value.get("items", [])]
        return found


def _namespace(provider_id: int) -> tuple[str, str]:
    return ("provider", str(provider_id))


def _corrected(before: PositionState | None, changes: dict[str, Any]) -> PositionUpdate:
    update = apply_correction(before, changes)
    s = update.after
    contract = (s.expiry, s.strike, s.option_right)
    is_option = s.instrument == "option"
    if (is_option and None in contract) or (not is_option and any(v is not None for v in contract)):
        raise ManualInvalid("options need expiry, strike and right; stocks none of them")
    return update


def _signal(sig: Signal) -> dict[str, Any]:
    return {"idx": sig.idx, "msg_type": sig.msg_type}


async def _audit(
    db: AsyncSession,
    target: CorrectionTarget,
    target_id: str,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
    reason: str,
    session_id: uuid.UUID,
) -> ManualCorrection:
    row = ManualCorrection(
        target_type=target,
        target_id=target_id,
        action=action,
        before=before,
        after=after,
        reason=reason,
        session_id=session_id,
    )
    db.add(row)
    await db.flush()
    return row


async def _label(
    db: AsyncSession, sig: Signal, label: dict[str, Any], session_id: uuid.UUID
) -> None:
    """The corrected interpretation of the signal's message event; supersedes an earlier label."""
    event = await db.get(MessageEvent, (sig.message_id, sig.event_type, sig.event_seq))
    assert event is not None  # noqa: S101 — FK
    day = to_et(event.posted_at).date()
    previous = await db.scalar(
        select(col(EvalLabel.label_id))
        .where(
            col(EvalLabel.message_id) == sig.message_id,
            col(EvalLabel.event_type) == sig.event_type,
            col(EvalLabel.event_seq) == sig.event_seq,
        )
        .order_by(col(EvalLabel.labeled_at).desc())
        .limit(1)
    )
    db.add(
        EvalLabel(
            message_id=sig.message_id,
            event_type=sig.event_type,
            event_seq=sig.event_seq,
            label=label,
            trading_day=day,
            split=split_for(day),
            source=LabelSource.PROVIDER_CORRECTION,
            supersedes_label_id=previous,
            session_id=session_id,
        )
    )
    await db.flush()
