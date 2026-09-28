# Database Schema — Authoritative Design

**Status:** implemented through migration `0004` (F002–F005, milestone M1); later changes are additive. Implements PRD v0.3 §14 and TRD v0.5 §8, §8.1, §9.
**Engine:** PostgreSQL **17** (confirmed decision; amends TRD §3's 16) for local Docker and for managed HA production.
**Access:** SQLModel on SQLAlchemy 2 async, driver **psycopg 3**, migrations by **Alembic** (confirmed decision; amends TRD §3's asyncpg + numbered SQL).

Every feature that touches the database must use the tables exactly as defined here. If a feature needs a column or table that is not here, update this document **in the same change** and say why.

---

## 1. Conventions

| Topic | Rule | Why |
|---|---|---|
| Discord IDs (`message_id`, `provider_id`, `channel_id`, `reply_to_id`) | `bigint`, `CHECK (> 0)` | Snowflakes are 64-bit integers; `bigint` sorts them correctly (text does not). **API serializes them as strings** (JS numbers lose precision above 2^53). Assumes the Message API gives snowflakes (OD-2). |
| Internal IDs | `uuid`. Deterministic `uuid5` where re-runs must produce the same ID (see §6); otherwise `gen_random_uuid()` | Idempotency without read-then-insert. |
| Money / prices | `numeric(12,4)` per-share prices and levels; `numeric(14,2)` dollar amounts; Python `Decimal` everywhere | TRD §3: `Decimal` for all money. Never `float`. |
| Quantities | `integer`, with `CHECK` bounds | Contracts and shares are whole units (MVP: no fractional shares). |
| Timestamps | `timestamptz NOT NULL DEFAULT now()` for creation; UTC in storage; ET only in domain logic; local (UTC+5:45) only in the UI | coding-practices §20, PRD §12 Time. |
| Enumerations | `text` + `CHECK (col IN (...))`, mirrored by a Python `StrEnum` | Cheaper to evolve than PG `ENUM` types with Alembic. |
| Semi-structured payloads | `jsonb`, always validated by a named Pydantic model with a `schema_version` field before write | Raw payload + model I/O must be kept verbatim (audit). |
| Naming | snake_case tables (plural), PK `<entity>_id` except audit tables with surrogate `id bigint GENERATED ALWAYS AS IDENTITY` | Matches TRD names. |
| Soft delete | None. Current-state rows change status; audit rows never change | 7-year audit (C63). |

### 1.1 Table classes

| Class | Rule | Enforcement |
|---|---|---|
| **Immutable audit / event** | Rows are only inserted. Corrections are new rows. | Runtime role `trading_app` gets `SELECT, INSERT` only (TRD §8). |
| **Current state** | Mutable working set; every change is caused by, and traceable to, an audit row. | `SELECT, INSERT, UPDATE` for `trading_app`; `DELETE` granted nowhere except where stated. |
| **LangGraph-managed** | Orchestration only; not audit. | Separate PG schema `langgraph`; created by `checkpointer.setup()` / `store.setup()` run by the owner role (F035). |

### 1.2 Durability classes (C93, TRD §9)
- **RPO ≈ 0** (synchronous standby / Multi-AZ): `approvals`, `orders`, `order_events`, `fills`, `lots`, `lot_events`, `provider_positions`, `provider_position_events`, `signals`, `message_links`, `cards`, `card_events`, `stops`, `stop_events`, `timers`.
- **RPO ≤ 5 min** (PITR): everything else. `message_events`/`inbox` are additionally protected by durable-before-200 plus Message API redelivery/backfill (FR-1d).

### 1.3 Roles (created by `infra/postgres/init/01-roles.sh` locally; by the managed provider's admin in prod)
| Role | Used by | Grants |
|---|---|---|
| `trading_owner` | Alembic, `graph setup` CLI | Owns schemas `public` and `langgraph`. |
| `trading_app` | Running service | Per-table grants from §1.1, applied by the migration that creates each table (`GRANT … TO trading_app`). `USAGE` + DML on schema `langgraph`. |

---

## 2. Identifier model and correlation (TRD §8.1, PRD §6)

```
provider_id (Discord author ID) ── sequencing key, context scope, ledger scope (OD-5: per author, CONFIRMED)
  └─ message_id (snowflake: unique, time-ordered)
       ├─ event (message_id, event_type, event_seq)       ← transport idempotency key
       ├─ reply_to_id → message_id (any author; deterministic link input, FR-3)
       └─ signal_id = uuid5(message_id:event_type:event_seq:idx)   ← one event can hold several signals (FR-6)
            └─ message_links: signal_id → provider_position_id (method, confidence, outcome)  append-only
provider_position_id ── created by an entry signal (or manual correction); owned by exactly one provider_id
  └─ lot_id (user holding; FK provider_position_id; foreign lots have none)
       └─ card_id (UNIQUE (signal_id, action) and UNIQUE dedupe_key)
            └─ client_order_id = uuid5(card_id, action_seq)  (UNIQUE)
channel_id ── metadata only, never a scope key (OD-5 resolved: per author)
```

**Never-cross-provider is enforced by the database**, not only by code:
- `signals` has `UNIQUE (signal_id, provider_id)`; `provider_positions` has `UNIQUE (provider_position_id, provider_id)`.
- `message_links` carries `provider_id` and has composite FKs `(signal_id, provider_id) → signals` and `(provider_position_id, provider_id) → provider_positions`. A link from provider A's signal to provider B's position is impossible to insert.

### 2.1 Worked example — "take half" (AC-18)
Messages delivered back to back: A1 "Bought GOOGL 357.5C @ 1.67", B2 "Bought TSLA 400C @ 2.10", A3 "Take half off @ 2.10", B4 "Stop 1.50".
1. `inbox.accept()` stores four events. Dispatcher runs **one worker per provider**: worker A takes A1 then A3 (snowflake order); worker B takes B2 then B4, in parallel.
2. A1 `apply` commits provider position `P_A_GOOGL` (owner A) **before** worker A dequeues A3 (provider slot, F012).
3. A3 `load_context` reads only A's book → `[P_A_GOOGL]`; A3 has no `reply_to_id`.
4. Correlation (F021): step 1 reply-to → none; step 2 model `links_to = P_A_GOOGL` with confidence ≥ `T_link` → `message_links(outcome='linked', method='jev'|'llm')`.
5. If A held GOOGL **and** META and `links_to` is `ambiguous` or below `T_link` → `message_links(outcome='ambiguous', candidate_position_ids=[…])` and a **review card** listing both. Nothing is guessed; no model can upgrade it.
6. B4 follows the same path inside B's scope only.

### 2.2 Correlation order (F021)
1. `reply_to_id` whose parent message has a current link to a position **of the same provider** → `method='reply'`, confidence 1.
2. Otherwise the provider's open book + model `links_to` with confidence ≥ `T_link` → `method='jev'|'llm'`.
3. Otherwise `outcome='ambiguous'` (≥ 2 candidates or low confidence) → review card; or `outcome='none'` (book empty / new position for entries).
A reply to **another author's** message is read-only context (C71); it never links across providers.

---

## 3. Ingestion and interpretation tables (F003)

### 3.1 `message_events` — immutable audit
Purpose: every message event exactly once (FR-1), raw payload kept 7 years; replay source for evalkit.

| Column | Type | Null | Notes |
|---|---|---|---|
| `message_id` | bigint | NN | PK part; snowflake |
| `event_type` | text | NN | PK part; `CHECK IN ('create','edit','delete')` |
| `event_seq` | integer | NN | PK part; `CHECK >= 0`; 0 for create/delete; edit sequence from the API (OD-2 — provisional: edit timestamp ms) |
| `provider_id` | bigint | NN | author ID |
| `channel_id` | bigint | NN | metadata |
| `posted_at` | timestamptz | NN | derived from snowflake; drives ordering and validity clocks (C90) |
| `edited_at` | timestamptz | NULL | edit events only |
| `reply_to_id` | bigint | NULL | FR-3 |
| `mentioned_author_ids` | bigint[] | NN | default `'{}'` (C71 @mentions) |
| `text` | text | NULL | NULL for delete events |
| `payload` | jsonb | NN | raw envelope as received |
| `backfilled` | boolean | NN | default false |
| `received_at` | timestamptz | NN | default now() |

- PK `(message_id, event_type, event_seq)`.
- CHECK `(event_type = 'delete') OR (text IS NOT NULL)`.
- Indexes: `(provider_id, posted_at DESC)` — recent author messages (context window); `(channel_id, message_id DESC)` — gap detector's last seen per channel; `(reply_to_id)` — reply parent lookup; `(posted_at)` — feed paging.

### 3.2 `inbox` — current state (transport queue, TRD §2.4)
| Column | Type | Null | Notes |
|---|---|---|---|
| `message_id`, `event_type`, `event_seq` | as above | NN | PK; FK → `message_events` |
| `provider_id` | bigint | NN | denormalized for the sequencer index |
| `posted_at` | timestamptz | NN | denormalized |
| `status` | text | NN | `CHECK IN ('received','processing','done','failed','stale')` |
| `attempts` | smallint | NN | default 0, `CHECK >= 0` |
| `last_error` | text | NULL | redacted error summary |
| `late` | boolean | NN | default false (FR-1b) |
| `backfilled` | boolean | NN | default false |
| `skip_reason` | text | NULL | `CHECK IN ('muted_author','channel_not_configured')` — stored, not interpreted, visible in feed (FR-2, G5 no silent drop) |
| `claimed_at` | timestamptz | NULL | |
| `finished_at` | timestamptz | NULL | |
| `updated_at` | timestamptz | NN | |

- Index `(provider_id, posted_at, message_id) WHERE status IN ('received','processing')` — `next_for_provider()`.
- Index `(posted_at) WHERE status = 'received'` — backlog depth / oldest unprocessed age (catch-up, FR-1c).
- Index `(provider_id, posted_at DESC) WHERE status IN ('done','stale')` — "was a later message already applied?" (FR-1b).
- `status='stale'` = processed, ledger updated, no approvable card (FR-1c).

### 3.3 `stage_timings` — immutable audit (deviation X7)
TRD §8 puts `t_received … t_pushed` columns on `message_events`, but that table is append-only. Stage times are written as rows instead.

| Column | Type | Notes |
|---|---|---|
| `message_id`, `event_type`, `event_seq` | | FK → `message_events` |
| `attempt` | smallint | inbox attempt number |
| `stage` | text | `CHECK IN ('received','dequeued','context','jev','llm','validated','card','pushed')` |
| `at` | timestamptz | |

PK `(message_id, event_type, event_seq, attempt, stage)`. Index `(stage, at)` for percentile queries (F049).

### 3.4 `dispatcher_heartbeat` — current state
`id smallint PK CHECK (id = 1)`, `beat_at timestamptz NN`, `host text NN`, `pid integer NN`. `/health` fails when `now() - beat_at > 15 s` (TRD §2.4).

### 3.5 `model_calls` — immutable audit
Purpose: every Jev and LLM call (PRD §14, TRD §8), 7-year retention, cost tracking (C44).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | bigint identity | NN | PK |
| `message_id`, `event_type`, `event_seq` | | NULL | FK → `message_events` (NULL for non-message calls) |
| `card_id` | uuid | NULL | note-writer calls; FK → `cards` (added in `0004`) |
| `eval_run_id` | uuid | NULL | replay calls; FK → `eval_runs` (added in `0003`) |
| `provider` | text | NN | `CHECK IN ('typesafe','anthropic','openai','deepseek','openai_compat','bedrock')` |
| `model` | text | NN | |
| `role` | text | NN | `CHECK IN ('interpret','extract','note','prelabel')` (`interpret` = Jev) |
| `attempt` | smallint | NN | position in the fallback chain, 1-based |
| `prompt_version` | text | NULL | e.g. `extract@v1` |
| `request` | jsonb | NN | C71-scoped context (never user portfolio, FR-8) |
| `response` | jsonb | NULL | incl. Jev per-question probabilities |
| `outcome` | text | NN | `CHECK IN ('ok','timeout','rate_limited','server_error','connection_error','schema_invalid','empty','refusal','scope_blocked','span_rejected','circuit_open')` |
| `latency_ms` | integer | NN | |
| `tokens_in`, `tokens_out`, `tokens_cached` | integer | NULL | from `usage_metadata` |
| `cost_usd` | numeric(12,6) | NULL | from registry price table |
| `started_at` | timestamptz | NN | |

Indexes: `(message_id, event_type, event_seq)`; `(started_at)` — daily spend; `(provider, model, started_at)` — per-provider latency/error stats (F049).

### 3.6 `signals` — immutable audit
Purpose: parsed signals with provenance (FR-6, FR-7). Replaces TRD's `parsed_signals`.

| Column | Type | Null | Notes |
|---|---|---|---|
| `signal_id` | uuid | NN | PK; `uuid5(NAMESPACE, f"{message_id}:{event_type}:{event_seq}:{idx}")` |
| `message_id`, `event_type`, `event_seq` | | NN | FK → `message_events` |
| `idx` | smallint | NN | `CHECK >= 0` |
| `provider_id` | bigint | NN | |
| `msg_type` | text | NN | FR-5 list: `entry, add, trim, full_exit, soft_exit, stop_set, stop_move, stop_cancel, tp_set, tp_hit_notice, correction, watch, conditional, commentary_pnl, stock_entry, stock_exit, out_of_scope_strategy, chatter` + `unparsed` (raw review card: poison / parser degraded) |
| `instrument` | text | NULL | `CHECK IN ('option','stock','unclear')` |
| `fields` | jsonb | NN | Pydantic `SignalFields` (F015); every field carries provenance `literal`(candidate id + span) / `context`(source message id) / `resolved` |
| `interpreter` | text | NN | `CHECK IN ('jev','llm','none')` |
| `model_call_id` | bigint | NULL | FK → `model_calls` |
| `stale` | boolean | NN | FR-1c |
| `late` | boolean | NN | FR-1b / FR-1c badge |
| `schema_version` | smallint | NN | |
| `created_at` | timestamptz | NN | |

UNIQUE `(message_id, event_type, event_seq, idx)`; UNIQUE `(signal_id, provider_id)` (composite FK target). Index `(provider_id, created_at DESC)`.

### 3.7 `message_links` — immutable audit
| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | bigint identity | NN | PK |
| `signal_id` | uuid | NN | |
| `provider_id` | bigint | NN | |
| `provider_position_id` | uuid | NULL | NULL when outcome is `none`/`ambiguous` |
| `outcome` | text | NN | `CHECK IN ('linked','new_position','none','ambiguous')` |
| `method` | text | NN | `CHECK IN ('reply','jev','llm','user_correction','code')` (`code` = entry opened a new position) |
| `confidence` | numeric(5,4) | NULL | `CHECK BETWEEN 0 AND 1` |
| `candidate_position_ids` | uuid[] | NN | default `'{}'`; review card choices |
| `supersedes_link_id` | bigint | NULL | FK → `message_links(id)` (re-link after correction / FR-1b re-check) |
| `created_at` | timestamptz | NN | |

- FK `(signal_id, provider_id) → signals(signal_id, provider_id)`; FK `(provider_position_id, provider_id) → provider_positions(provider_position_id, provider_id)`.
- CHECK `(outcome IN ('linked','new_position')) = (provider_position_id IS NOT NULL)`.
- Index `(signal_id, id DESC)` — current link = latest row; `(provider_position_id)`.

---

## 4. Trading state and trading audit tables (F005)

### 4.1 `provider_positions` — current state
| Column | Type | Null | Notes |
|---|---|---|---|
| `provider_position_id` | uuid | NN | PK |
| `provider_id` | bigint | NN | owner (never crosses providers) |
| `opened_channel_id` | bigint | NULL | metadata only |
| `instrument` | text | NN | `CHECK IN ('option','stock')` |
| `underlying` | text | NN | after symbol map (e.g. `SPXW`) |
| `expiry` | date | NULL | |
| `strike` | numeric(12,4) | NULL | |
| `option_right` | text | NULL | `CHECK IN ('call','put')` |
| `provider_entry_price` | numeric(12,4) | NULL | first stated entry price (breakeven option, C29). No provider average: provider sizes are unknown, so an average would be invented. Adds are recorded in `provider_position_events`. |
| `remaining_fraction` | numeric(5,4) | NN | 1 at open; trims reduce (provider's book only) |
| `stop_level` | numeric(12,4) | NULL | provider's last stated stop |
| `stop_basis` | text | NULL | `CHECK IN ('premium','underlying')` |
| `tp_levels` | numeric(12,4)[] | NN | default `'{}'` |
| `status` | text | NN | `CHECK IN ('open','trimmed','closed')` |
| `is_corrected` | boolean | NN | default false (TRD "+ corrected") |
| `opened_by_signal_id` | uuid | NULL | FK → `signals` (NULL for manual correction) |
| `opened_at`, `closed_at`, `updated_at` | timestamptz | | |

- CHECK option shape: `instrument='option'` ⇒ `expiry, strike, option_right` NOT NULL; `instrument='stock'` ⇒ all three NULL.
- UNIQUE `(provider_position_id, provider_id)`.
- Index `(provider_id, status) WHERE status <> 'closed'` — provider book (C71 context, correlation).
- Index `(provider_id, underlying, opened_at DESC)` — "underlyings the provider traded in the last N days" (C84).

### 4.2 `provider_position_events` — immutable audit (OD-13 proposal: history of current-state rows)
`id identity PK`, `provider_position_id FK`, `change CHECK IN ('open','add','trim','exit','stop_set','stop_move','stop_cancel','tp_set','correct','close')`, `signal_id uuid NULL FK`, `manual_correction_id bigint NULL FK`, `before jsonb NULL`, `after jsonb NN`, `at timestamptz`. CHECK exactly one of `signal_id`, `manual_correction_id` is set. Index `(provider_position_id, id)`.

### 4.3 `lots` — current state (user holdings)
| Column | Type | Null | Notes |
|---|---|---|---|
| `lot_id` | uuid | NN | PK |
| `provider_position_id` | uuid | NULL | FK; NULL ⇔ foreign (C47) |
| `provider_id` | bigint | NULL | denormalized; NULL ⇔ foreign |
| `is_foreign` | boolean | NN | read-only in UI; counts toward risk limits |
| `instrument` | text | NN | option / stock |
| `broker_symbol` | text | NN | OCC option symbol or stock ticker |
| `underlying`, `expiry`, `strike`, `option_right` | | | same shape CHECK as §4.1 |
| `qty` | integer | NN | `CHECK >= 0` |
| `avg_price` | numeric(12,4) | NULL | system average (may differ from broker FIFO; UI labels it, TRD §16.2 #10) |
| `status` | text | NN | `CHECK IN ('open','closed')` |
| `opened_at`, `closed_at`, `updated_at` | timestamptz | | |

- CHECK `is_foreign = (provider_position_id IS NULL)`; CHECK `(status='closed') = (qty = 0)`.
- **Partial UNIQUE** `(provider_position_id) WHERE status = 'open'` — one open lot per provider position (C24); a re-entry after full exit creates a new lot (C62).
- Index `(broker_symbol, status)` — reconciliation; `(underlying, status)` — opposite-direction warning (C74); `(expiry) WHERE status='open'` — expiration management.

### 4.4 `lot_events` — immutable audit (OD-13)
`id identity PK`, `lot_id FK`, `change CHECK IN ('open','fill_add','fill_reduce','close','reconcile_adjust','adopt_foreign')`, `fill_id uuid NULL FK`, `qty_before`, `qty_after`, `avg_before`, `avg_after`, `reason text NULL`, `at`. Index `(lot_id, id)`.

### 4.5 `cards` — current state
| Column | Type | Null | Notes |
|---|---|---|---|
| `card_id` | uuid | NN | PK; `uuid5(NAMESPACE, dedupe_key)` |
| `dedupe_key` | text | NN | UNIQUE. Signal cards: `sig:{signal_id}:{action}`; system: `cutoff1:{lot_id}:{date}`, `relink:{signal_id}:{link_id}`; manual: `manual:{idempotency_key}` |
| `source` | text | NN | `CHECK IN ('signal','system','manual')` |
| `signal_id` | uuid | NULL | FK; required when `source='signal'` |
| `action` | text | NN | `CHECK IN ('entry','add','trim','exit','stop','tp','close','review')` (`review` = raw review / re-link review) |
| `card_type` | text | NN | `CHECK IN ('normal','inferred','review','disabled')` |
| `state` | text | NN | `CHECK IN ('pending','approved','submitted','rejected','expired','invalidated','disabled')` (X3) |
| `provider_id` | bigint | NULL | |
| `provider_position_id` | uuid | NULL | FK |
| `lot_id` | uuid | NULL | FK (exit/stop/close cards) |
| `proposal` | jsonb | NN | Pydantic `CardProposal`: qty (nullable), limit/stop prices, bracket legs, tp split, warnings, labels (`stale`,`late`,`suggestion`,`lot_too_small`, `backfilled`), missing fields, suggested underlying, candidate positions, breakeven options |
| `posted_at` | timestamptz | NN | validity clock origin (C90) |
| `valid_until` | timestamptz | NULL | entry: posted_at + N s; exit: timeout |
| `disabled_reason` | text | NULL | required when `state='disabled'` |
| `note` | text | NULL | advisory (C80) |
| `note_hidden` | boolean | NN | note guard (OD-6) |
| `version` | integer | NN | optimistic check for decisions/edits |
| `created_at`, `updated_at` | timestamptz | | |

- UNIQUE `(signal_id, action)` (TRD §8; NULL `signal_id` rows are distinct) and UNIQUE `(dedupe_key)`.
- CHECK `(card_type = 'disabled') = (state = 'disabled')`; CHECK `state <> 'disabled' OR disabled_reason IS NOT NULL`; CHECK `source <> 'signal' OR signal_id IS NOT NULL`.
- Index `(state, created_at DESC) WHERE state = 'pending'` — inbox; `(provider_position_id, state)` — supersession (C22 third rule); `(lot_id, state)`.

### 4.6 `card_events` — immutable audit
`id identity PK`, `card_id FK`, `from_state NULL`, `to_state NN`, `reason_code text NN` (`created, approved, rejected, timeout, price_band, superseded, correction, deleted, restart, revalidate_failed, edited, review_completed, submitted`), `detail jsonb`, `actor CHECK IN ('user','system','timer')`, `proposal_snapshot jsonb NULL`, `at`. Index `(card_id, id)`.

### 4.7 `approvals` — immutable audit
| Column | Type | Null | Notes |
|---|---|---|---|
| `approval_id` | uuid | NN | PK |
| `card_id` | uuid | NN | FK; **UNIQUE** (a card is decided once) |
| `decision` | text | NN | `CHECK IN ('approve','reject')` |
| `edits` | jsonb | NN | user edits (qty, price, TP split, chosen breakeven, confirmed underlying, chosen position) |
| `approved_proposal` | jsonb | NULL | exact proposal approved (NULL on reject) |
| `card_version` | integer | NN | must equal `cards.version` at decision time |
| `session_id` | uuid | NN | FK → `auth_sessions` |
| `step_up` | boolean | NN | manual entry / flatten require true |
| `decided_at` | timestamptz | NN | |

### 4.8 `orders` — current state
| Column | Type | Null | Notes |
|---|---|---|---|
| `order_id` | uuid | NN | PK |
| `client_order_id` | text | NN | UNIQUE; `uuid5(card_id, action_seq)` or `uuid5(parent, reprice_seq)`; prefix `cta-` for broker-side recovery (TRD §9) |
| `card_id` | uuid | NULL | FK |
| `approval_id` | uuid | NULL | FK |
| `auto_reason` | text | NULL | `CHECK IN ('reprice','eod_forced_close','stop_restore','underlying_stop')` (PRD §9 list) |
| `parent_order_id` | uuid | NULL | FK (reprice chain, bracket legs) |
| `oco_group_id` | uuid | NULL | stop/TP linkage (C50) |
| `provider_position_id` | uuid | NULL | FK |
| `lot_id` | uuid | NULL | FK; set on first fill for entries |
| `kind` | text | NN | `CHECK IN ('entry','exit','stop','tp')` |
| `side` | text | NN | `CHECK IN ('buy','sell')` |
| `instrument`, `broker_symbol` | text | NN | |
| `qty` | integer | NN | `CHECK > 0` |
| `order_type` | text | NN | `CHECK IN ('limit','stop','stop_limit','stop_market')` |
| `limit_price`, `stop_price` | numeric(12,4) | NULL | |
| `tif` | text | NN | `CHECK IN ('day','gtc')` |
| `status` | text | NN | `CHECK IN ('intent','submitted','working','partially_filled','filled','cancelled','rejected','unknown')` |
| `mode` | text | NN | `CHECK IN ('shadow','paper','live')` |
| `broker_order_id` | text | NULL | UNIQUE |
| `filled_qty` | integer | NN | default 0, `CHECK 0 <= filled_qty <= qty` |
| `avg_fill_price` | numeric(12,4) | NULL | |
| `created_at`, `updated_at` | timestamptz | | |

- **AC-2 in the schema:** CHECK `approval_id IS NOT NULL OR auto_reason IS NOT NULL`.
- **Invariant (1) shape:** CHECK `(kind = 'entry') = (side = 'buy')` — every sell is an exit/stop/TP, every buy is an entry; no short can be *expressed*. Quantity checks (invariant 2) run in `submit_order()` under the lot row lock.
- Index `(status) WHERE status IN ('intent','submitted','working','partially_filled','unknown')`; `(lot_id, status)`; `(card_id)`.

### 4.9 `order_events` — immutable audit
`id identity PK`, `order_id FK`, `status`, `source CHECK IN ('submit','broker_stream','poll','reconcile','timeout_query','cancel')`, `broker_payload jsonb NULL`, `at`. Index `(order_id, id)`.

### 4.10 `fills` — immutable audit
`fill_id uuid PK`, `order_id FK NN`, `broker_fill_id text NN UNIQUE` (dedupes stream + poll), `qty integer CHECK > 0`, `price numeric(12,4) NN`, `filled_at`, `received_at`. Index `(order_id)`.

### 4.11 `stops` — current state (stops and take-profits)
| Column | Type | Null | Notes |
|---|---|---|---|
| `stop_id` | uuid | NN | PK |
| `lot_id` | uuid | NN | FK |
| `kind` | text | NN | `CHECK IN ('stop','tp')` |
| `basis` | text | NN | `CHECK IN ('premium','underlying','stock')` |
| `held_by` | text | NN | `CHECK IN ('broker','system')` |
| `level` | numeric(12,4) | NN | trigger / limit level |
| `qty` | integer | NN | `CHECK > 0` |
| `tp_index` | smallint | NULL | 1-based (C83) |
| `order_id` | uuid | NULL | FK; broker-held only |
| `card_id` | uuid | NN | FK (approved by) |
| `candle_close` | boolean | NN | info only (C49) |
| `status` | text | NN | `CHECK IN ('pending','working','suspended','cancelled','fired')` |
| `created_at`, `updated_at` | | | |

CHECK `(held_by = 'system') = (basis = 'underlying')`; CHECK `kind <> 'tp' OR tp_index IS NOT NULL`. Index `(lot_id, status)`; `(status) WHERE held_by='system' AND status='working'` — underlying-stop watcher.

### 4.12 `stop_events` — immutable audit (OD-13)
`id identity PK`, `stop_id FK`, `change CHECK IN ('create','working','suspend','restore','move','cancel','fire','reduce')`, `order_id NULL`, `detail jsonb`, `at`.

### 4.13 `timers` — current state (persisted deadlines)
`timer_id uuid PK`, `kind CHECK IN ('card_expiry','entry_cancel','cutoff_1','cutoff_2','offhours_release')`, `due_at timestamptz NN`, `card_id NULL FK`, `lot_id NULL FK`, `order_id NULL FK`, `message_id/event_type/event_seq NULL`, `dedupe_key text NN UNIQUE`, `status CHECK IN ('scheduled','fired','cancelled')`, `fired_at NULL`, `created_at`. Index `(due_at) WHERE status='scheduled'`.

---

## 5. Platform tables (F004)

| Table | Class | Columns (key ones) | Constraints / indexes | Source |
|---|---|---|---|---|
| `alerts` | audit | `alert_id uuid PK`, `severity CHECK IN ('info','warning','urgent')`, `kind text` (feed_silence, parser_degraded, poison, reconcile_mismatch, catch_up, late_correction, message_deleted, risk_limit, exit_floor, provider_disabled, market_data, invariant_block, note_hidden, system_flag, auth_anomaly), `message text`, `card_id/lot_id/order_id NULL`, `message_id NULL`, `details jsonb`, `dedupe_key text NULL UNIQUE`, `created_at` | idx `(created_at DESC)` | PRD §14, FR-26 |
| `alert_acks` | audit | `alert_id PK FK`, `session_id FK`, `acked_at` | | derived (X5) |
| `config` | current | `id smallint PK CHECK (id=1)`, `version integer`, `document jsonb` (Pydantic `RuntimeConfig`), `updated_at` | | PRD §18 |
| `config_changes` | audit | `id identity`, `version integer UNIQUE`, `document jsonb`, `diff jsonb`, `session_id NULL`, `actor CHECK IN ('user','cli','system')`, `reason text`, `at` | | PRD §14 |
| `system_flags` | current | `flag text PK CHECK IN ('entries_blocked','kill_switch','catch_up','feed_degraded','parser_degraded')`, `active boolean`, `reason text`, `since timestamptz`, `updated_at` | | derived (X5): TRD §9 entry block, PRD 7.9, FR-1c, FR-4, FR-9 |
| `system_flag_events` | audit | `id identity`, `flag`, `active`, `reason`, `actor`, `session_id NULL`, `at` | | derived |
| `manual_corrections` | audit | `id identity`, `target_type CHECK IN ('provider_position','message_link','provider_profile')`, `target_id text`, `action text`, `before jsonb`, `after jsonb`, `reason text`, `session_id`, `at` | idx `(target_type, target_id)` | C73, PRD §14 |
| `logins` | audit | `id identity`, `outcome CHECK IN ('success','failure','step_up_success','step_up_failure','logout','rate_limited','enrolled')`, `credential_id bytea NULL`, `session_id NULL`, `ip inet`, `user_agent text`, `at` | idx `(at DESC)` | PRD §13 |
| `passkey_credentials` | current | `credential_id bytea PK`, `public_key bytea`, `sign_count bigint`, `transports text[]`, `label text`, `created_at`, `last_used_at`, `revoked_at NULL` | | derived (X5) |
| `enrollment_tokens` | current | `token_hash bytea PK`, `expires_at`, `used_at NULL`, `created_at` | | derived: "recovery only via server console" |
| `auth_sessions` | current | `session_id uuid PK`, `token_hash bytea UNIQUE`, `credential_id FK`, `created_at`, `expires_at`, `last_seen_at`, `step_up_at NULL`, `ip inet`, `user_agent text`, `revoked_at NULL` | idx `(expires_at) WHERE revoked_at IS NULL` | derived |
| `push_subscriptions` | current | `subscription_id uuid PK`, `endpoint text UNIQUE`, `p256dh text`, `auth text`, `label text`, `created_at`, `revoked_at NULL` | | derived (FR-26) |
| `eval_labels` | audit | `label_id uuid PK`, `message_id/event_type/event_seq FK`, `label jsonb` (expected `ParsedSignals` + link), `trading_day date`, `split CHECK IN ('dev','test')`, `source CHECK IN ('prelabel_corrected','manual','provider_correction')`, `supersedes_label_id NULL`, `session_id`, `labeled_at` | idx `(split, trading_day)` | C64, C67 |
| `eval_runs` | audit | `eval_run_id uuid PK`, `kind CHECK IN ('replay_gate','threshold_sweep','bench')`, `model_ref text` (`provider:model:prompt`), `split`, `thresholds jsonb`, `metrics jsonb`, `gate_passed boolean NULL`, `started_at`, `finished_at` | idx `(model_ref, started_at DESC)` | C45, C67, TRD §4.8 rule 5 |

---

## 6. Idempotency keys

| Operation | Key | Mechanism |
|---|---|---|
| Webhook / backfill delivery | `(message_id, event_type, event_seq)` | `INSERT … ON CONFLICT DO NOTHING` on `message_events` and `inbox` |
| Re-processing after crash | `signal_id = uuid5(event key, idx)`; `cards.dedupe_key`; UNIQUE `(signal_id, action)` | `apply` is one transaction; re-run inserts conflict → no second card |
| Order submission | `client_order_id = uuid5(card_id, action_seq)` | UNIQUE; intent committed before broker call; timeout ⇒ status query (FR-22) |
| Broker fill events | `broker_fill_id` | UNIQUE on `fills` |
| Timers | `timers.dedupe_key` | UNIQUE |
| Card decision | `approvals.card_id` UNIQUE + `cards.version` | double approve / stale edit impossible |
| Alerts | `alerts.dedupe_key` (e.g. `feed_silence:{episode}`) | avoids storms |

## 7. Transaction boundaries

| # | Transaction | Contents | Must not include |
|---|---|---|---|
| T1 | `inbox.accept` | insert `message_events` + `inbox` + `stage_timings(received)` + `NOTIFY inbox, '<provider_id>'` | any model or broker call |
| T2 | claim | `UPDATE inbox SET status='processing', attempts=attempts+1` | |
| T3 | **`apply`** (F026) | lock provider's open `provider_positions` rows `FOR UPDATE`; insert `signals`, `message_links`; update/insert `provider_positions` + `provider_position_events`; read lots (`FOR SHARE`); insert `cards` (+`card_events`) with `ON CONFLICT (dedupe_key) DO NOTHING`; insert `timers`; invalidate superseded cards (OD-14); set inbox `done`/`stale`; `NOTIFY cards` | model calls, broker calls (quotes are fetched **before** the transaction and passed in) |
| T4 | decision (F036) | `SELECT cards … FOR UPDATE`; check `state='pending'` and `version`; insert `approvals`; update card; `card_events`; commit **then** `Command(resume)` | broker calls |
| T5a | order intent (F028) | lock lot `FOR UPDATE` (exits); run invariants; insert `orders(status='intent')` + `order_events`; commit | — |
| T5b | order result | update `orders.status`, `broker_order_id`; `order_events` | |
| T6 | fill (F040) | insert `fills ON CONFLICT DO NOTHING`; lock order + lot `FOR UPDATE`; update order, lot (+ create lot on first entry fill); `lot_events` | |
| T7 | manual correction (F047) | update `provider_positions` + `provider_position_events` + `manual_corrections` (+ `message_links` re-link rows) | |
| T8 | config change (F006) | update `config` (version+1) + insert `config_changes` | |

**Never hold a transaction open across a model or broker call** (coding-practices §9).

## 8. Concurrency

| Concern | Mechanism |
|---|---|
| Per-provider ordering (FR-1a) | One asyncio worker + `asyncio.Lock` per `provider_id` (single process). Multi-process later: session-level `pg_try_advisory_lock(hashtextextended(provider_id::text, 0))` on a dedicated connection (TRD §2.4). |
| Provider ledger writes | `SELECT … FOR UPDATE` on the provider's open positions inside T3/T7. |
| Lot quantity (invariant 2) | `SELECT lots … FOR UPDATE` in T5a and T6; sell qty checked against locked `qty` minus working exit orders. |
| Card decision vs expiry vs supersession | `SELECT cards … FOR UPDATE`; state transition validated by the card state machine; losers get 409. |
| Inbox claim | single dispatcher; no `SKIP LOCKED` needed. Multi-process: `FOR UPDATE SKIP LOCKED` inside the provider lock. |
| Global flags | read inside T5a (`kill_switch`, `entries_blocked`) so a flag set concurrently blocks the next submit. |

## 9. Expected query patterns → indexes

| Query | Feature | Index |
|---|---|---|
| next event for provider in snowflake order | F012 | `inbox (provider_id, posted_at, message_id) WHERE status IN (received, processing)` |
| backlog depth / oldest unprocessed | F013 | `inbox (posted_at) WHERE status='received'` |
| last seen per channel | F014 | `message_events (channel_id, message_id DESC)` |
| author's recent N messages | F017 | `message_events (provider_id, posted_at DESC)` |
| provider's open book | F017, F021 | `provider_positions (provider_id, status) WHERE status<>'closed'` |
| current link of a signal | F021, F037 | `message_links (signal_id, id DESC)` |
| open lot for provider position | F022, F023 | partial UNIQUE `lots (provider_position_id) WHERE status='open'` |
| pending cards | F030, F055 | `cards (state, created_at DESC) WHERE state='pending'` |
| pending cards on a position (supersession) | F025 | `cards (provider_position_id, state)` |
| due timers | F027 | `timers (due_at) WHERE status='scheduled'` |
| working orders | F040, F046 | `orders (status) WHERE status IN (...)` |
| system-held stops | F044 | `stops (status) WHERE held_by='system' AND status='working'` |
| per-stage p95 | F049 | `stage_timings (stage, at)` |
| LLM spend today | F049 | `model_calls (started_at)` |

## 10. Retention and growth
- 7-year retention for all audit tables (C63). Volume estimate: ≤ 2,500 message events/session × ~250 sessions/year ≈ 0.6 M events/year; `model_calls` is the largest (jsonb request/response). Partitioning is **not** planned now (YAGNI); add range partitioning by month on `model_calls`/`stage_timings` when a table exceeds ~50 GB or query p95 degrades (measure first).
- LangGraph checkpoints for finished threads pruned after 30 days (TRD §5.2) by a job in F049.

## 11. Migration grouping
| Migration | Feature | Tables |
|---|---|---|
| `0001_roles_and_schemas` | F002 | schema `langgraph`, default privileges (in `langgraph` only, §12) |
| `0002_ingestion_interpretation` | F003 | §3 |
| `0003_platform` | F004 | §5 (before trading state: `approvals` → `auth_sessions`, `provider_position_events` → `manual_corrections`) |
| `0004_trading_state` | F005 | §4, plus FK `model_calls.card_id → cards` |
| `0005_alert_kind_system_flag` | F008 | `alerts.kind` gains `system_flag` |
| `0006_alert_kind_auth_anomaly` | F009 | `alerts.kind` gains `auth_anomaly` |
| later | owning feature | only additive changes, each updating this document |

## 12. Implementation notes (decisions made while implementing F002–F005)
Recorded here per the rule at the top of this document. None changes a column's meaning.

| # | Topic | Decision | Why |
|---|---|---|---|
| 1 | Init script | `infra/postgres/init/01-roles.sh` (not `.sql`) | Needs role passwords from env. |
| 2 | Default privileges | `ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner` only **in schema `langgraph`** (DML + sequence usage). `public` gets explicit per-table grants in each migration. | LangGraph tables are created later by `setup()`; audit tables in `public` must stay insert-only. |
| 3 | `text` columns | SQLModel `str` fields are compiled as `TEXT` on PostgreSQL (`@compiles(AutoString)` in `app/db.py`); migrations render `sa.Text()` (`render_item` in `migrations/env.py`) and never import sqlmodel. | Matches §1 convention; keeps migrations self-contained. |
| 4 | Enumerations | `StrEnum` in each `models.py`; `CHECK (col IN …)` generated from it by `app.db.check_in`. Never a PG `ENUM`. | One source of truth for Python and DB lists. |
| 5 | Constraint names | Naming convention on `SQLModel.metadata`: `pk_<table>`, `uq_<table>_<cols>`, `fk_<table>_<cols>`, `ck_<table>_<name>`; indexes named explicitly (`ix_<table>_<purpose>`). | Stable names for Alembic downgrades and error mapping. |
| 6 | Discord ID `> 0` checks | Applied on every Discord ID column that is not already covered by an FK to a checked column. | §1 convention. |
| 7 | Extra sanity checks | `model_calls.attempt >= 1` (1-based), `model_calls.latency_ms >= 0`. | Stated semantics, made explicit. |
| 8 | Timestamps | Creation-like columns (`received_at`, `at`, `created_at`) and `updated_at` are `NOT NULL DEFAULT now()`, also defaulted in Python so ORM inserts never send NULL. `updated_at` is set by the application on update (no triggers). Event-time columns (`posted_at`, `started_at`, `due_at` …) have no default. | §1 convention; no hidden trigger logic. |
| 9 | Identity sequences | `GRANT USAGE ON SEQUENCE <table>_id_seq TO trading_app` for every identity column. | F003 migration rule. |
| 10 | `session_id` FKs | Every `session_id` column references `auth_sessions`. NOT NULL where only a logged-in user can act (`alert_acks`, `manual_corrections`, `approvals`); nullable where CLI/system can act (`config_changes`, `system_flag_events`, `logins`, `eval_labels`). | Integrity of the audit trail. |
| 11 | Other FKs made explicit | `system_flag_events.flag → system_flags`; `eval_labels` message key → `message_events`; `eval_labels.supersedes_label_id → eval_labels`; `auth_sessions.credential_id → passkey_credentials`. | Stated as references in §5. |
| 12 | No FK | `alerts.card_id/lot_id/order_id/message_id` (alerts precede those tables and are audit references); `logins.credential_id` (failed attempts may carry unknown IDs). | Keeps alert/login writes unconditional. |
| 13 | §5 nullability not stated in the table | `alerts.details` and `eval_runs.thresholds` = `jsonb NOT NULL DEFAULT '{}'`; `eval_runs.metrics`, `finished_at` NOT NULL (audit row is written once, after the run); `eval_runs.split`, `gate_passed`, `manual_corrections.before/reason`, `config_changes.reason`, `logins.ip/user_agent`, `passkey_credentials.label`, `push_subscriptions.label` nullable. `passkey_credentials.sign_count` default 0; `auth_sessions.last_seen_at` default now(). | Audit tables are insert-only, so values known only at the end must be written then. |
| 14 | Actor enums | `config_changes.actor` and `system_flag_events.actor` share `('user','cli','system')`. | §5 lists it for config; flags are set by the same actors. |
| 15 | Structural rows | Migration `0003` inserts the five `system_flags` rows (inactive). Test truncation keeps them and resets `active=false`. | F004. |
| 16 | Provider consistency beyond `message_links` | `lots` and `cards` also use the composite FK `(provider_position_id, provider_id) → provider_positions`; `lots` adds CHECK `(provider_id IS NULL) = (provider_position_id IS NULL)`. | A lot or card cannot claim another provider's position (AC-18), for free. |
| 17 | `approvals` shape | CHECK `(decision = 'approve') = (approved_proposal IS NOT NULL)`; `edits` defaults to `'{}'`, `step_up` to false. | §4.7 "NULL on reject", made enforceable. |
| 18 | Extra sanity checks | `provider_positions.remaining_fraction BETWEEN 0 AND 1` (default 1); `cards.version >= 1` (default 1); `stops.tp_index >= 1`; `lots/orders.instrument IN ('option','stock')`. | Stated semantics. |
| 19 | FK cycle | `signals → model_calls → cards → signals`: the `model_calls.card_id` FK is declared `use_alter=True` (created by `ALTER TABLE` in `0004`). | Lets SQLAlchemy/Alembic order table creation. |
| 20 | §4 abbreviated nullability | `lot_events.qty_before/avg_before/avg_after` nullable (an `open` has no "before"); `card_events.detail`, `stop_events.detail` = `jsonb NOT NULL DEFAULT '{}'`; `stops.candle_close` default false; `provider_positions.opened_at` default now(). | Values not always known. |
| 21 | `stop_events` index | `(stop_id, id)`, same as the other `*_events` tables. | History lookup per stop. |
| 22 | `provider_position_events` cause | CHECK `num_nonnulls(signal_id, manual_correction_id) = 1`. | "Exactly one of" in §4.2. |
| 23 | `alerts.kind` `system_flag` (migration `0005`, F008) | Every system-flag transition raises one alert of this kind (details: flag, active, reason, actor). | Kill switch and entries-blocked had no alert kind; one uniform kind for all flags. |
| 24 | `alerts.kind` `auth_anomaly` (migration `0006`, F009) | Raised (urgent) on a passkey sign-count regression. | F009 security requirement "cloned authenticator → urgent alert"; no existing kind fit. |
