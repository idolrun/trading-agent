"""F004: platform tables (database-schema.md §5)."""

import uuid

from psycopg import errors
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.models import Alert, AlertAck
from app.evalkit.models import EvalLabel
from app.manual.models import ManualCorrection
from app.runtime_config.models import ConfigChange, ConfigRow
from app.system.models import FlagName, SystemFlag
from tests.integration.schema.helpers import T0, add_message, add_session, expect_error


async def test_config_is_singleton(db_session: AsyncSession) -> None:
    db_session.add(ConfigRow(version=1, document={"tiers": []}))
    await db_session.commit()
    await expect_error(
        db_session,
        errors.CheckViolation,
        "INSERT INTO config (id, version, document) VALUES (2, 1, '{}')",
    )


async def test_five_flag_rows_exist_and_names_are_checked(db_session: AsyncSession) -> None:
    rows = (await db_session.scalars(select(SystemFlag))).all()
    assert {r.flag for r in rows} == set(FlagName)
    assert not any(r.active for r in rows)
    await expect_error(
        db_session,
        errors.CheckViolation,
        "INSERT INTO system_flags (flag, active) VALUES ('panic', true)",
    )
    await db_session.execute(text("UPDATE system_flags SET active = true WHERE flag = 'catch_up'"))
    await db_session.commit()


async def test_audit_tables_insert_only_for_app_role(db_session: AsyncSession) -> None:
    session_id = await add_session(db_session)
    db_session.add_all(
        [
            Alert(severity="urgent", kind="feed_silence", message="no messages for 10 min"),
            ConfigChange(version=1, document={}, diff={}, actor="cli"),
            ManualCorrection(
                target_type="provider_position",
                target_id=str(uuid.uuid4()),
                action="close",
                after={"status": "closed"},
                session_id=session_id,
            ),
        ]
    )
    await db_session.execute(text("INSERT INTO audit_log (action) VALUES ('login_failed')"))
    await db_session.commit()
    updates = {
        "alerts": "UPDATE alerts SET message = 'x'",
        "audit_log": "UPDATE audit_log SET user_email = 'x'",
        "config_changes": "UPDATE config_changes SET reason = 'x'",
        "manual_corrections": "UPDATE manual_corrections SET reason = 'x'",
    }
    for table, update in updates.items():
        await expect_error(db_session, errors.InsufficientPrivilege, update)
        await expect_error(db_session, errors.InsufficientPrivilege, f"DELETE FROM {table}")


async def test_alert_acked_once(db_session: AsyncSession) -> None:
    session_id = await add_session(db_session)
    alert = Alert(severity="warning", kind="poison", message="poison message")
    db_session.add(alert)
    await db_session.commit()
    db_session.add(AlertAck(alert_id=alert.alert_id, session_id=session_id))
    await db_session.commit()
    await expect_error(
        db_session,
        errors.UniqueViolation,
        "INSERT INTO alert_acks (alert_id, session_id) VALUES (:a, :s)",
        {"a": alert.alert_id, "s": session_id},
    )


async def test_alert_dedupe_key_unique(db_session: AsyncSession) -> None:
    db_session.add(Alert(severity="urgent", kind="feed_silence", message="x", dedupe_key="fs:1"))
    await db_session.commit()
    await expect_error(
        db_session,
        errors.UniqueViolation,
        """INSERT INTO alerts (alert_id, severity, kind, message, dedupe_key)
           VALUES (gen_random_uuid(), 'urgent', 'feed_silence', 'x', 'fs:1')""",
    )


async def test_eval_label_split_checked(db_session: AsyncSession) -> None:
    await add_message(db_session)
    db_session.add(
        EvalLabel(
            message_id=1001,
            event_type="create",
            event_seq=0,
            label={"signals": []},
            trading_day=T0.date(),
            split="dev",
            source="manual",
        )
    )
    await db_session.commit()
    await expect_error(
        db_session,
        errors.CheckViolation,
        """INSERT INTO eval_labels (label_id, message_id, event_type, event_seq, label,
               trading_day, split, source)
           VALUES (gen_random_uuid(), 1001, 'create', 0, '{}', '2026-09-28', 'train', 'manual')""",
    )


async def test_auth_session_requires_known_user(db_session: AsyncSession) -> None:
    await expect_error(
        db_session,
        errors.ForeignKeyViolation,
        """INSERT INTO auth_sessions (session_id, token_hash, user_id, expires_at)
           VALUES (gen_random_uuid(), 'h', gen_random_uuid(), now())""",
    )
