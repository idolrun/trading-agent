"""trading state

Tables of database-schema.md §4 (F005) with per-class grants, the no-cross-provider
composite FK message_links -> provider_positions, and FK model_calls.card_id -> cards.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-28 20:58:14.750378
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUDIT = [
    "provider_position_events",
    "lot_events",
    "card_events",
    "approvals",
    "order_events",
    "fills",
    "stop_events",
]
CURRENT = ["provider_positions", "lots", "cards", "orders", "stops", "timers"]
SEQUENCES = [f"{table}_id_seq" for table in AUDIT if table not in ("approvals", "fills")]


def upgrade() -> None:
    op.create_table(
        "provider_positions",
        sa.Column("provider_position_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.BigInteger(), nullable=False),
        sa.Column("opened_channel_id", sa.BigInteger(), nullable=True),
        sa.Column("instrument", sa.Text(), nullable=False),
        sa.Column("underlying", sa.Text(), nullable=False),
        sa.Column("expiry", sa.Date(), nullable=True),
        sa.Column("strike", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("option_right", sa.Text(), nullable=True),
        sa.Column("provider_entry_price", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column(
            "remaining_fraction",
            sa.Numeric(precision=5, scale=4),
            server_default="1",
            nullable=False,
        ),
        sa.Column("stop_level", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("stop_basis", sa.Text(), nullable=True),
        sa.Column(
            "tp_levels",
            sa.ARRAY(sa.Numeric(precision=12, scale=4)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("is_corrected", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("opened_by_signal_id", sa.Uuid(), nullable=True),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(instrument = 'option' AND expiry IS NOT NULL AND strike IS NOT NULL AND option_right IS NOT NULL) OR (instrument = 'stock' AND expiry IS NULL AND strike IS NULL AND option_right IS NULL)",
            name=op.f("ck_provider_positions_contract_shape"),
        ),
        sa.CheckConstraint(
            "instrument IN ('option', 'stock')", name=op.f("ck_provider_positions_instrument")
        ),
        sa.CheckConstraint(
            "option_right IN ('call', 'put')", name=op.f("ck_provider_positions_option_right")
        ),
        sa.CheckConstraint(
            "status IN ('open', 'trimmed', 'closed')", name=op.f("ck_provider_positions_status")
        ),
        sa.CheckConstraint(
            "stop_basis IN ('premium', 'underlying')", name=op.f("ck_provider_positions_stop_basis")
        ),
        sa.CheckConstraint(
            "opened_channel_id > 0", name=op.f("ck_provider_positions_opened_channel_id_positive")
        ),
        sa.CheckConstraint(
            "provider_id > 0", name=op.f("ck_provider_positions_provider_id_positive")
        ),
        sa.CheckConstraint(
            "remaining_fraction BETWEEN 0 AND 1",
            name=op.f("ck_provider_positions_remaining_fraction_range"),
        ),
        sa.ForeignKeyConstraint(
            ["opened_by_signal_id"],
            ["signals.signal_id"],
            name=op.f("fk_provider_positions_opened_by_signal_id"),
        ),
        sa.PrimaryKeyConstraint("provider_position_id", name=op.f("pk_provider_positions")),
        sa.UniqueConstraint(
            "provider_position_id",
            "provider_id",
            name=op.f("uq_provider_positions_provider_position_id_provider_id"),
        ),
    )
    op.create_index(
        "ix_provider_positions_open_book",
        "provider_positions",
        ["provider_id", "status"],
        unique=False,
        postgresql_where=sa.text("status <> 'closed'"),
    )
    op.create_index(
        "ix_provider_positions_recent_underlyings",
        "provider_positions",
        ["provider_id", "underlying", sa.literal_column("opened_at DESC")],
        unique=False,
    )
    op.create_table(
        "lots",
        sa.Column("lot_id", sa.Uuid(), nullable=False),
        sa.Column("provider_position_id", sa.Uuid(), nullable=True),
        sa.Column("provider_id", sa.BigInteger(), nullable=True),
        sa.Column("is_foreign", sa.Boolean(), nullable=False),
        sa.Column("instrument", sa.Text(), nullable=False),
        sa.Column("broker_symbol", sa.Text(), nullable=False),
        sa.Column("underlying", sa.Text(), nullable=False),
        sa.Column("expiry", sa.Date(), nullable=True),
        sa.Column("strike", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("option_right", sa.Text(), nullable=True),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("avg_price", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(instrument = 'option' AND expiry IS NOT NULL AND strike IS NOT NULL AND option_right IS NOT NULL) OR (instrument = 'stock' AND expiry IS NULL AND strike IS NULL AND option_right IS NULL)",
            name=op.f("ck_lots_contract_shape"),
        ),
        sa.CheckConstraint(
            "(status = 'closed') = (qty = 0)", name=op.f("ck_lots_closed_iff_empty")
        ),
        sa.CheckConstraint("instrument IN ('option', 'stock')", name=op.f("ck_lots_instrument")),
        sa.CheckConstraint("option_right IN ('call', 'put')", name=op.f("ck_lots_option_right")),
        sa.CheckConstraint("status IN ('open', 'closed')", name=op.f("ck_lots_status")),
        sa.CheckConstraint(
            "(provider_id IS NULL) = (provider_position_id IS NULL)",
            name=op.f("ck_lots_provider_with_position"),
        ),
        sa.CheckConstraint(
            "is_foreign = (provider_position_id IS NULL)",
            name=op.f("ck_lots_foreign_iff_no_position"),
        ),
        sa.CheckConstraint("qty >= 0", name=op.f("ck_lots_qty_non_negative")),
        sa.ForeignKeyConstraint(
            ["provider_position_id", "provider_id"],
            ["provider_positions.provider_position_id", "provider_positions.provider_id"],
            name=op.f("fk_lots_provider_position_id_provider_id"),
        ),
        sa.PrimaryKeyConstraint("lot_id", name=op.f("pk_lots")),
    )
    op.create_index(
        "ix_lots_broker_symbol_status", "lots", ["broker_symbol", "status"], unique=False
    )
    op.create_index(
        "ix_lots_open_expiry",
        "lots",
        ["expiry"],
        unique=False,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index("ix_lots_underlying_status", "lots", ["underlying", "status"], unique=False)
    op.create_index(
        "uq_lots_one_open_per_position",
        "lots",
        ["provider_position_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_table(
        "provider_position_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("provider_position_id", sa.Uuid(), nullable=False),
        sa.Column("change", sa.Text(), nullable=False),
        sa.Column("signal_id", sa.Uuid(), nullable=True),
        sa.Column("manual_correction_id", sa.BigInteger(), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "change IN ('open', 'add', 'trim', 'exit', 'stop_set', 'stop_move', 'stop_cancel', 'tp_set', 'correct', 'close')",
            name=op.f("ck_provider_position_events_change"),
        ),
        sa.CheckConstraint(
            "num_nonnulls(signal_id, manual_correction_id) = 1",
            name=op.f("ck_provider_position_events_exactly_one_cause"),
        ),
        sa.ForeignKeyConstraint(
            ["manual_correction_id"],
            ["manual_corrections.id"],
            name=op.f("fk_provider_position_events_manual_correction_id"),
        ),
        sa.ForeignKeyConstraint(
            ["provider_position_id"],
            ["provider_positions.provider_position_id"],
            name=op.f("fk_provider_position_events_provider_position_id"),
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"], ["signals.signal_id"], name=op.f("fk_provider_position_events_signal_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_position_events")),
    )
    op.create_index(
        "ix_provider_position_events_position",
        "provider_position_events",
        ["provider_position_id", "id"],
        unique=False,
    )
    op.create_table(
        "cards",
        sa.Column("card_id", sa.Uuid(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("signal_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("card_type", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("provider_id", sa.BigInteger(), nullable=True),
        sa.Column("provider_position_id", sa.Uuid(), nullable=True),
        sa.Column("lot_id", sa.Uuid(), nullable=True),
        sa.Column("proposal", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_reason", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("note_hidden", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(card_type = 'disabled') = (state = 'disabled')", name=op.f("ck_cards_disabled_type")
        ),
        sa.CheckConstraint(
            "action IN ('entry', 'add', 'trim', 'exit', 'stop', 'tp', 'close', 'review')",
            name=op.f("ck_cards_action"),
        ),
        sa.CheckConstraint(
            "card_type IN ('normal', 'inferred', 'review', 'disabled')",
            name=op.f("ck_cards_card_type"),
        ),
        sa.CheckConstraint(
            "source <> 'signal' OR signal_id IS NOT NULL", name=op.f("ck_cards_signal_card_signal")
        ),
        sa.CheckConstraint(
            "source IN ('signal', 'system', 'manual')", name=op.f("ck_cards_source")
        ),
        sa.CheckConstraint(
            "state <> 'disabled' OR disabled_reason IS NOT NULL",
            name=op.f("ck_cards_disabled_has_reason"),
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'approved', 'submitted', 'rejected', 'expired', 'invalidated', 'disabled')",
            name=op.f("ck_cards_state"),
        ),
        sa.CheckConstraint("provider_id > 0", name=op.f("ck_cards_provider_id_positive")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_cards_version_positive")),
        sa.ForeignKeyConstraint(["lot_id"], ["lots.lot_id"], name=op.f("fk_cards_lot_id")),
        sa.ForeignKeyConstraint(
            ["provider_position_id", "provider_id"],
            ["provider_positions.provider_position_id", "provider_positions.provider_id"],
            name=op.f("fk_cards_provider_position_id_provider_id"),
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"], ["signals.signal_id"], name=op.f("fk_cards_signal_id")
        ),
        sa.PrimaryKeyConstraint("card_id", name=op.f("pk_cards")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_cards_dedupe_key")),
        sa.UniqueConstraint("signal_id", "action", name=op.f("uq_cards_signal_id_action")),
    )
    op.create_index("ix_cards_lot_state", "cards", ["lot_id", "state"], unique=False)
    op.create_index(
        "ix_cards_pending",
        "cards",
        ["state", sa.literal_column("created_at DESC")],
        unique=False,
        postgresql_where=sa.text("state = 'pending'"),
    )
    op.create_index(
        "ix_cards_position_state", "cards", ["provider_position_id", "state"], unique=False
    )
    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("card_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column(
            "edits",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("approved_proposal", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("card_version", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("step_up", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(decision = 'approve') = (approved_proposal IS NOT NULL)",
            name=op.f("ck_approvals_approved_proposal_iff_approve"),
        ),
        sa.CheckConstraint("decision IN ('approve', 'reject')", name=op.f("ck_approvals_decision")),
        sa.ForeignKeyConstraint(["card_id"], ["cards.card_id"], name=op.f("fk_approvals_card_id")),
        sa.ForeignKeyConstraint(
            ["session_id"], ["auth_sessions.session_id"], name=op.f("fk_approvals_session_id")
        ),
        sa.PrimaryKeyConstraint("approval_id", name=op.f("pk_approvals")),
        sa.UniqueConstraint("card_id", name=op.f("uq_approvals_card_id")),
    )
    op.create_table(
        "card_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("card_id", sa.Uuid(), nullable=False),
        sa.Column("from_state", sa.Text(), nullable=True),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("proposal_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "actor IN ('user', 'system', 'timer')", name=op.f("ck_card_events_actor")
        ),
        sa.CheckConstraint(
            "from_state IN ('pending', 'approved', 'submitted', 'rejected', 'expired', 'invalidated', 'disabled')",
            name=op.f("ck_card_events_from_state"),
        ),
        sa.CheckConstraint(
            "reason_code IN ('created', 'approved', 'rejected', 'timeout', 'price_band', 'superseded', 'correction', 'deleted', 'restart', 'revalidate_failed', 'edited', 'review_completed', 'submitted')",
            name=op.f("ck_card_events_reason_code"),
        ),
        sa.CheckConstraint(
            "to_state IN ('pending', 'approved', 'submitted', 'rejected', 'expired', 'invalidated', 'disabled')",
            name=op.f("ck_card_events_to_state"),
        ),
        sa.ForeignKeyConstraint(
            ["card_id"], ["cards.card_id"], name=op.f("fk_card_events_card_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_card_events")),
    )
    op.create_index("ix_card_events_card", "card_events", ["card_id", "id"], unique=False)
    op.create_table(
        "orders",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("client_order_id", sa.Text(), nullable=False),
        sa.Column("card_id", sa.Uuid(), nullable=True),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        sa.Column("auto_reason", sa.Text(), nullable=True),
        sa.Column("parent_order_id", sa.Uuid(), nullable=True),
        sa.Column("oco_group_id", sa.Uuid(), nullable=True),
        sa.Column("provider_position_id", sa.Uuid(), nullable=True),
        sa.Column("lot_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("instrument", sa.Text(), nullable=False),
        sa.Column("broker_symbol", sa.Text(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("order_type", sa.Text(), nullable=False),
        sa.Column("limit_price", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("stop_price", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("tif", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("broker_order_id", sa.Text(), nullable=True),
        sa.Column("filled_qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("avg_fill_price", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'entry') = (side = 'buy')", name=op.f("ck_orders_no_short_shape")
        ),
        sa.CheckConstraint(
            "auto_reason IN ('reprice', 'eod_forced_close', 'stop_restore', 'underlying_stop')",
            name=op.f("ck_orders_auto_reason"),
        ),
        sa.CheckConstraint("instrument IN ('option', 'stock')", name=op.f("ck_orders_instrument")),
        sa.CheckConstraint("kind IN ('entry', 'exit', 'stop', 'tp')", name=op.f("ck_orders_kind")),
        sa.CheckConstraint("mode IN ('shadow', 'paper', 'live')", name=op.f("ck_orders_mode")),
        sa.CheckConstraint(
            "order_type IN ('limit', 'stop', 'stop_limit', 'stop_market')",
            name=op.f("ck_orders_order_type"),
        ),
        sa.CheckConstraint("side IN ('buy', 'sell')", name=op.f("ck_orders_side")),
        sa.CheckConstraint(
            "status IN ('intent', 'submitted', 'working', 'partially_filled', 'filled', 'cancelled', 'rejected', 'unknown')",
            name=op.f("ck_orders_status"),
        ),
        sa.CheckConstraint("tif IN ('day', 'gtc')", name=op.f("ck_orders_tif")),
        sa.CheckConstraint(
            "approval_id IS NOT NULL OR auto_reason IS NOT NULL",
            name=op.f("ck_orders_approved_or_auto"),
        ),
        sa.CheckConstraint("filled_qty BETWEEN 0 AND qty", name=op.f("ck_orders_filled_qty_range")),
        sa.CheckConstraint("qty > 0", name=op.f("ck_orders_qty_positive")),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["approvals.approval_id"], name=op.f("fk_orders_approval_id")
        ),
        sa.ForeignKeyConstraint(["card_id"], ["cards.card_id"], name=op.f("fk_orders_card_id")),
        sa.ForeignKeyConstraint(["lot_id"], ["lots.lot_id"], name=op.f("fk_orders_lot_id")),
        sa.ForeignKeyConstraint(
            ["parent_order_id"], ["orders.order_id"], name=op.f("fk_orders_parent_order_id")
        ),
        sa.ForeignKeyConstraint(
            ["provider_position_id"],
            ["provider_positions.provider_position_id"],
            name=op.f("fk_orders_provider_position_id"),
        ),
        sa.PrimaryKeyConstraint("order_id", name=op.f("pk_orders")),
        sa.UniqueConstraint("broker_order_id", name=op.f("uq_orders_broker_order_id")),
        sa.UniqueConstraint("client_order_id", name=op.f("uq_orders_client_order_id")),
    )
    op.create_index("ix_orders_card_id", "orders", ["card_id"], unique=False)
    op.create_index("ix_orders_lot_status", "orders", ["lot_id", "status"], unique=False)
    op.create_index(
        "ix_orders_open_status",
        "orders",
        ["status"],
        unique=False,
        postgresql_where=sa.text(
            "status IN ('intent', 'submitted', 'working', 'partially_filled', 'unknown')"
        ),
    )
    op.create_table(
        "fills",
        sa.Column("fill_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("broker_fill_id", sa.Text(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("qty > 0", name=op.f("ck_fills_qty_positive")),
        sa.ForeignKeyConstraint(["order_id"], ["orders.order_id"], name=op.f("fk_fills_order_id")),
        sa.PrimaryKeyConstraint("fill_id", name=op.f("pk_fills")),
        sa.UniqueConstraint("broker_fill_id", name=op.f("uq_fills_broker_fill_id")),
    )
    op.create_index("ix_fills_order_id", "fills", ["order_id"], unique=False)
    op.create_table(
        "order_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("broker_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "source IN ('submit', 'broker_stream', 'poll', 'reconcile', 'timeout_query', 'cancel')",
            name=op.f("ck_order_events_source"),
        ),
        sa.CheckConstraint(
            "status IN ('intent', 'submitted', 'working', 'partially_filled', 'filled', 'cancelled', 'rejected', 'unknown')",
            name=op.f("ck_order_events_status"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.order_id"], name=op.f("fk_order_events_order_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_events")),
    )
    op.create_index("ix_order_events_order", "order_events", ["order_id", "id"], unique=False)
    op.create_table(
        "stops",
        sa.Column("stop_id", sa.Uuid(), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.Column("held_by", sa.Text(), nullable=False),
        sa.Column("level", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("tp_index", sa.SmallInteger(), nullable=True),
        sa.Column("order_id", sa.Uuid(), nullable=True),
        sa.Column("card_id", sa.Uuid(), nullable=False),
        sa.Column("candle_close", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(held_by = 'system') = (basis = 'underlying')",
            name=op.f("ck_stops_system_iff_underlying"),
        ),
        sa.CheckConstraint(
            "basis IN ('premium', 'underlying', 'stock')", name=op.f("ck_stops_basis")
        ),
        sa.CheckConstraint("held_by IN ('broker', 'system')", name=op.f("ck_stops_held_by")),
        sa.CheckConstraint(
            "kind <> 'tp' OR tp_index IS NOT NULL", name=op.f("ck_stops_tp_has_index")
        ),
        sa.CheckConstraint("kind IN ('stop', 'tp')", name=op.f("ck_stops_kind")),
        sa.CheckConstraint(
            "status IN ('pending', 'working', 'suspended', 'cancelled', 'fired')",
            name=op.f("ck_stops_status"),
        ),
        sa.CheckConstraint("qty > 0", name=op.f("ck_stops_qty_positive")),
        sa.CheckConstraint("tp_index >= 1", name=op.f("ck_stops_tp_index_positive")),
        sa.ForeignKeyConstraint(["card_id"], ["cards.card_id"], name=op.f("fk_stops_card_id")),
        sa.ForeignKeyConstraint(["lot_id"], ["lots.lot_id"], name=op.f("fk_stops_lot_id")),
        sa.ForeignKeyConstraint(["order_id"], ["orders.order_id"], name=op.f("fk_stops_order_id")),
        sa.PrimaryKeyConstraint("stop_id", name=op.f("pk_stops")),
    )
    op.create_index("ix_stops_lot_status", "stops", ["lot_id", "status"], unique=False)
    op.create_index(
        "ix_stops_system_working",
        "stops",
        ["status"],
        unique=False,
        postgresql_where=sa.text("held_by = 'system' AND status = 'working'"),
    )
    op.create_table(
        "timers",
        sa.Column("timer_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("card_id", sa.Uuid(), nullable=True),
        sa.Column("lot_id", sa.Uuid(), nullable=True),
        sa.Column("order_id", sa.Uuid(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=True),
        sa.Column("event_seq", sa.Integer(), nullable=True),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('card_expiry', 'entry_cancel', 'cutoff_1', 'cutoff_2', 'offhours_release')",
            name=op.f("ck_timers_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('scheduled', 'fired', 'cancelled')", name=op.f("ck_timers_status")
        ),
        sa.ForeignKeyConstraint(["card_id"], ["cards.card_id"], name=op.f("fk_timers_card_id")),
        sa.ForeignKeyConstraint(["lot_id"], ["lots.lot_id"], name=op.f("fk_timers_lot_id")),
        sa.ForeignKeyConstraint(
            ["message_id", "event_type", "event_seq"],
            ["message_events.message_id", "message_events.event_type", "message_events.event_seq"],
            name=op.f("fk_timers_message_id_event_type_event_seq"),
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.order_id"], name=op.f("fk_timers_order_id")),
        sa.PrimaryKeyConstraint("timer_id", name=op.f("pk_timers")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_timers_dedupe_key")),
    )
    op.create_index(
        "ix_timers_due",
        "timers",
        ["due_at"],
        unique=False,
        postgresql_where=sa.text("status = 'scheduled'"),
    )
    op.create_table(
        "lot_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=False),
        sa.Column("change", sa.Text(), nullable=False),
        sa.Column("fill_id", sa.Uuid(), nullable=True),
        sa.Column("qty_before", sa.Integer(), nullable=True),
        sa.Column("qty_after", sa.Integer(), nullable=False),
        sa.Column("avg_before", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("avg_after", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "change IN ('open', 'fill_add', 'fill_reduce', 'close', 'reconcile_adjust', 'adopt_foreign')",
            name=op.f("ck_lot_events_change"),
        ),
        sa.ForeignKeyConstraint(["fill_id"], ["fills.fill_id"], name=op.f("fk_lot_events_fill_id")),
        sa.ForeignKeyConstraint(["lot_id"], ["lots.lot_id"], name=op.f("fk_lot_events_lot_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lot_events")),
    )
    op.create_index("ix_lot_events_lot", "lot_events", ["lot_id", "id"], unique=False)
    op.create_table(
        "stop_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("stop_id", sa.Uuid(), nullable=False),
        sa.Column("change", sa.Text(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=True),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "change IN ('create', 'working', 'suspend', 'restore', 'move', 'cancel', 'fire', 'reduce')",
            name=op.f("ck_stop_events_change"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.order_id"], name=op.f("fk_stop_events_order_id")
        ),
        sa.ForeignKeyConstraint(
            ["stop_id"], ["stops.stop_id"], name=op.f("fk_stop_events_stop_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stop_events")),
    )
    op.create_index("ix_stop_events_stop", "stop_events", ["stop_id", "id"], unique=False)
    op.create_foreign_key(
        op.f("fk_message_links_provider_position_id_provider_id"),
        "message_links",
        "provider_positions",
        ["provider_position_id", "provider_id"],
        ["provider_position_id", "provider_id"],
    )
    op.create_foreign_key(
        op.f("fk_model_calls_card_id"),
        "model_calls",
        "cards",
        ["card_id"],
        ["card_id"],
        use_alter=True,
    )

    for table in AUDIT:
        op.execute(f"GRANT SELECT, INSERT ON {table} TO trading_app")
    for table in CURRENT:
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO trading_app")
    for seq in SEQUENCES:
        op.execute(f"GRANT USAGE ON SEQUENCE {seq} TO trading_app")


def downgrade() -> None:
    op.drop_constraint(op.f("fk_model_calls_card_id"), "model_calls", type_="foreignkey")
    op.drop_constraint(
        op.f("fk_message_links_provider_position_id_provider_id"),
        "message_links",
        type_="foreignkey",
    )
    op.drop_index("ix_stop_events_stop", table_name="stop_events")
    op.drop_table("stop_events")
    op.drop_index("ix_lot_events_lot", table_name="lot_events")
    op.drop_table("lot_events")
    op.drop_index(
        "ix_timers_due", table_name="timers", postgresql_where=sa.text("status = 'scheduled'")
    )
    op.drop_table("timers")
    op.drop_index(
        "ix_stops_system_working",
        table_name="stops",
        postgresql_where=sa.text("held_by = 'system' AND status = 'working'"),
    )
    op.drop_index("ix_stops_lot_status", table_name="stops")
    op.drop_table("stops")
    op.drop_index("ix_order_events_order", table_name="order_events")
    op.drop_table("order_events")
    op.drop_index("ix_fills_order_id", table_name="fills")
    op.drop_table("fills")
    op.drop_index(
        "ix_orders_open_status",
        table_name="orders",
        postgresql_where=sa.text(
            "status IN ('intent', 'submitted', 'working', 'partially_filled', 'unknown')"
        ),
    )
    op.drop_index("ix_orders_lot_status", table_name="orders")
    op.drop_index("ix_orders_card_id", table_name="orders")
    op.drop_table("orders")
    op.drop_index("ix_card_events_card", table_name="card_events")
    op.drop_table("card_events")
    op.drop_table("approvals")
    op.drop_index("ix_cards_position_state", table_name="cards")
    op.drop_index(
        "ix_cards_pending", table_name="cards", postgresql_where=sa.text("state = 'pending'")
    )
    op.drop_index("ix_cards_lot_state", table_name="cards")
    op.drop_table("cards")
    op.drop_index("ix_provider_position_events_position", table_name="provider_position_events")
    op.drop_table("provider_position_events")
    op.drop_index(
        "uq_lots_one_open_per_position",
        table_name="lots",
        postgresql_where=sa.text("status = 'open'"),
    )
    op.drop_index("ix_lots_underlying_status", table_name="lots")
    op.drop_index(
        "ix_lots_open_expiry", table_name="lots", postgresql_where=sa.text("status = 'open'")
    )
    op.drop_index("ix_lots_broker_symbol_status", table_name="lots")
    op.drop_table("lots")
    op.drop_index("ix_provider_positions_recent_underlyings", table_name="provider_positions")
    op.drop_index(
        "ix_provider_positions_open_book",
        table_name="provider_positions",
        postgresql_where=sa.text("status <> 'closed'"),
    )
    op.drop_table("provider_positions")
