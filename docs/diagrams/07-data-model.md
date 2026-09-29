# 7. Data Model & Entity Relationships

[← Sequences](06-sequence-diagrams.md) · [Index](README.md) · Next: [Deployment →](08-deployment.md)

PostgreSQL 16 holds all system state (TRD §8). Tables fall into three classes:

| Class | Rule | Tables |
|---|---|---|
| **Immutable audit / event data** | App role has `INSERT, SELECT` only. Corrections are new rows. 7-year retention (C63). | `message_events`, `model_calls`, `signals`, `message_links`, `card_events`, `approvals`, `order_events`, `fills`, `alerts`, `config_changes`, `manual_corrections`, `logins`, `eval_labels`, `eval_runs` |
| **Current state** | Mutable, row-locked, derived from events + broker. | `inbox`, `provider_positions`, `lots`, `cards`, `orders`, `stops`, `timers`, `config`, `dispatcher_heartbeat` |
| **LangGraph-managed** | Orchestration only; not audit; checkpoints pruned after 30 days. | checkpoint tables (`AsyncPostgresSaver`), store tables (`PostgresStore`: provider profiles) |

**Column fidelity:** columns named in TRD §8 are shown as-is. Columns marked `"illustrative"` are the minimum needed to draw a relationship the PRD/TRD describe but whose column name is not specified; final names are an implementation detail. Entities marked **PROPOSED** do not exist in the TRD (see OD-13).

## 7.1 Relationship overview

```mermaid
erDiagram
    PROVIDER ||--o{ message_events : "authors"
    PROVIDER ||--o{ provider_positions : "owns exactly"
    message_events ||--o| inbox : "queued as"
    message_events }o--o| message_events : "reply_to_id"
    message_events ||--o{ model_calls : "interpreted by"
    message_events ||--o{ signals : "yields 0..n"
    signals ||--o{ message_links : "linked by (append-only)"
    provider_positions ||--o{ message_links : "target of"
    provider_positions ||--o{ lots : "held by user as"
    signals ||--o{ cards : "proposes (UNIQUE signal_id, action)"
    lots |o--o{ cards : "exit / stop cards act on"
    cards ||--o{ card_events : "transitions"
    cards ||--o{ approvals : "decided by"
    cards ||--o{ orders : "executes as"
    approvals |o--o{ orders : "authorises"
    lots ||--o{ orders : "trades"
    orders ||--o{ order_events : "state history"
    orders ||--o{ fills : "filled by"
    lots ||--o{ stops : "protected by"
    stops |o--o| orders : "broker-held as"
    cards |o--o{ timers : "expires via"
    lots |o--o{ timers : "EOD cutoffs via"
    alerts }o--o| cards : "may reference"
```

- **`PROVIDER`** is a *logical* entity: `provider_id` = Discord author ID carried on every row; mute list and channel list live in `config` (FR-2, C57). No dedicated `providers` table is specified.
- **`provider_positions ||--o{ lots`**: C24 gives one lot per (provider position, user); "re-entry after full exit creates a new lot" (C62). Whether that re-entry is also a new provider position is implied by PRD §6 ("entry through exit") — the cardinality is drawn permissively.
- **Foreign lots** (positions not opened by the system) have no provider position (C47).

---

## 7.2 Ingestion & interpretation entities

```mermaid
erDiagram
    message_events {
        text message_id PK "Discord snowflake"
        text event_type PK "create / edit / delete"
        int event_seq PK "idempotency key part"
        text provider_id "Discord author ID"
        text channel_id "metadata, not scope key"
        timestamptz posted_at "from snowflake, drives ordering"
        text reply_to_id "nullable"
        jsonb payload "raw, immutable"
        bool backfilled
        timestamptz t_received
        timestamptz t_dequeued
        timestamptz t_jev
        timestamptz t_llm
        timestamptz t_validated
        timestamptz t_card
        timestamptz t_pushed
    }
    inbox {
        text event_id FK "to message_events"
        text provider_id "index provider_id, status, posted_at"
        timestamptz posted_at
        text status "received processing done failed stale"
        int attempts "poison after 3"
        text last_error
        text flags "late, backfilled"
    }
    model_calls {
        bigint id PK "illustrative"
        text message_id FK "illustrative"
        text provider "typesafe anthropic openai deepseek openai_compat bedrock"
        text model
        text role "extract note prelabel"
        int attempt "position in fallback chain"
        text prompt_version
        jsonb request "C71-scoped context"
        jsonb response "incl. Jev probabilities per question"
        int latency_ms
        int tokens_in
        int tokens_out
        int tokens_cached
        numeric cost
        text outcome "ok timeout 429 5xx schema refusal"
    }
    signals {
        text message_id PK "signal_id part 1"
        int idx PK "signal_id part 2, FR-6"
        text msg_type
        jsonb fields "underlying, expiry, strike, right, price, tier, SL, TPs"
        jsonb provenance "literal+candidate+span / context+source msg / resolved"
    }
    message_links {
        bigint id PK "illustrative"
        text message_id FK "signal_id part 1"
        int idx FK "signal_id part 2"
        text provider_position_id FK
        text method "reply jev llm user_correction"
        numeric confidence
        timestamptz created_at "re-link = new row"
    }
    eval_labels {
        bigint id PK "illustrative"
        text message_id FK
        jsonb label "user-corrected"
        text split "dev / test by trading day"
    }
    eval_runs {
        bigint id PK "illustrative"
        text model_ref "provider:model:prompt"
        jsonb metrics "accuracy, recall, latency"
        bool gate_passed "gate_run id used by registry"
    }
    message_events ||--o| inbox : "queued as"
    message_events ||--o{ model_calls : "interpreted by"
    message_events ||--o{ signals : "yields"
    signals ||--o{ message_links : "linked by"
    message_events ||--o{ eval_labels : "labelled as"
```

---

## 7.3 Trading state & trading audit entities

```mermaid
erDiagram
    provider_positions {
        text provider_position_id PK
        text provider_id "owner, never crosses providers"
        text instrument "option / stock"
        text underlying "after symbol map, e.g. SPXW"
        date expiry
        numeric strike
        text option_right "call / put"
        numeric provider_entry_price "illustrative"
        text status "open trimmed closed (+corrected)"
    }
    lots {
        text lot_id PK
        text provider_position_id FK "null for foreign lots"
        text contract "illustrative"
        int qty
        numeric avg_price "system avg, may differ from broker FIFO"
        bool is_foreign "read-only, counts for risk limits"
        text status "illustrative: open / closed"
    }
    cards {
        text card_id PK
        text message_id FK "signal_id, UNIQUE with action"
        int idx FK "signal_id"
        text action "entry add exit stop tp close ..."
        text card_type "normal inferred review disabled"
        text state "pending approved submitted rejected expired invalidated"
        text lot_id FK "exit/stop cards"
        jsonb proposal "qty, price, legs, warnings"
        timestamptz valid_until "clock from posted_at"
    }
    card_events {
        text card_id FK
        text state
        text reason "timeout, price band, superseded, restart ..."
        timestamptz at
    }
    approvals {
        text approval_id PK
        text card_id FK
        text decision "approve edit reject"
        jsonb edits
        timestamptz at
        text session_id "illustrative: user session"
    }
    orders {
        text order_id PK "illustrative"
        text client_order_id UK "uuid5 of card_id, action_seq"
        text card_id FK "null for auto actions"
        text approval_id FK "null only with auto_reason"
        text auto_reason "PRD section 9 list only"
        text lot_id FK
        text kind "entry exit stop tp"
        text status "intent submitted working partially_filled filled cancelled rejected unknown"
        text broker_order_id
    }
    order_events {
        text order_id FK
        text status
        jsonb broker_payload
        timestamptz at
    }
    fills {
        text fill_id PK "illustrative"
        text order_id FK
        text broker_fill_id UK "illustrative, dedupe"
        int qty
        numeric price
        timestamptz at
    }
    stops {
        text stop_id PK "illustrative"
        text lot_id FK
        text basis "premium underlying stock"
        text held_by "broker / system"
        numeric level
        text order_id FK "broker-held only"
        text status "illustrative: working suspended restored cancelled fired"
    }
    timers {
        text timer_id PK "illustrative"
        text kind "card_expiry cutoff_1 cutoff_2 offhours_release entry_cancel"
        timestamptz due_at
        text ref "card / lot / message"
        text status
    }
    provider_position_events {
        text provider_position_id FK "PROPOSED OD-13"
        text change "open add trim stop correct close"
        text source_message_id "or manual correction"
        timestamptz at
    }
    provider_positions ||--o{ lots : "held as"
    provider_positions ||--o{ provider_position_events : "PROPOSED history"
    lots |o--o{ cards : "acted on by"
    cards ||--o{ card_events : "transitions"
    cards ||--o{ approvals : "decided by"
    cards ||--o{ orders : "executes as"
    approvals |o--o{ orders : "authorises"
    lots ||--o{ orders : "trades"
    orders ||--o{ order_events : "history"
    orders ||--o{ fills : "filled by"
    lots ||--o{ stops : "protected by"
    stops |o--o| orders : "broker order"
    cards |o--o{ timers : "expiry"
```

### Immutable vs current state — why it is split this way
- **Audit rows record what was known and decided at the time** (a message, a model answer, a link, an approval, a fill). They are the evidence for AC-2 ("no order without an approval") and the replay source for `evalkit`. Because they are never updated, a re-link after a correction is a second `message_links` row, and a card transition is a `card_events` row.
- **Current-state rows are the working set** the pipeline locks and updates. Every change to them is caused by (and traceable to) an audit row: a `signals` + `message_links` insert moves a provider position; a `fills` insert moves a lot; an `approvals` insert moves a card.
- **Uniqueness does the idempotency work:** `message_events` PK (duplicate delivery), `cards` UNIQUE `(signal_id, action)` (re-processing after crash), `orders.client_order_id` UNIQUE (resubmission), broker fill id (duplicate fill events).
- **History gap (OD-13):** PRD §14 requires provider positions "and their history", and stops/lots history is needed for audit. The TRD's audit tables capture the *causes* but not a row-per-change of `provider_positions`, `lots`, `stops`. The PROPOSED `provider_position_events` (and equivalents for `lots`, `stops`) closes that gap; the alternative is deriving history by replaying `message_links` + `fills` + `manual_corrections`.

### Durability classes (C93)
| RPO ≈ 0 (synchronous commit to standby) | RPO ≤ 5 min (PITR) |
|---|---|
| `approvals`, `orders`, `order_events`, `fills`, `lots`, `provider_positions`, `message_links`, `signals`, `cards` | `model_calls`, `alerts`, `logins`, eval tables, checkpoints |

`message_events`/`inbox` are protected by *durable-before-200* plus Message API redelivery/backfill, so even a PITR rollback can be refilled by the gap detector (OD-1).

---

## 7.4 State machines

### Card

```mermaid
stateDiagram-v2
    [*] --> pending: apply txn (normal / inferred / review)
    [*] --> disabled: risk limit, tier too small (not approvable, no push)
    pending --> pending: review fields completed / qty entered
    pending --> approved: user approves (approvals row first)
    pending --> rejected: user rejects
    pending --> expired: timeout from posted_at / restart
    pending --> invalidated: superseding msg, correction, delete, ask beyond band
    approved --> invalidated: revalidate fails (C22)
    approved --> submitted: submit_order() wrote order intent
    submitted --> [*]
    rejected --> [*]
    expired --> [*]
    invalidated --> [*]
    disabled --> [*]
```

### Order

```mermaid
stateDiagram-v2
    [*] --> intent: committed before broker call
    intent --> submitted: broker accepted
    intent --> unknown: timeout
    unknown --> submitted: status query finds it
    unknown --> rejected: status query finds nothing, intent void
    submitted --> working
    working --> partially_filled
    partially_filled --> partially_filled: more fills / re-price
    working --> filled
    partially_filled --> filled
    working --> cancelled: M s elapsed, floor reached, user / EOD cancel
    partially_filled --> cancelled: remainder cancelled, partial kept
    submitted --> rejected: broker rejects
    filled --> [*]
    cancelled --> [*]
    rejected --> [*]
```

The TRD lists `unknown` as a terminal-looking state; resolving it through the status query is the FR-22 rule ("query before any retry").

### Provider position

```mermaid
stateDiagram-v2
    [*] --> open: entry signal applied
    open --> open: add (C62) / stop or TP change
    open --> trimmed: trim
    trimmed --> trimmed: further trim / add / stop change
    open --> closed: full exit
    trimmed --> closed: full exit
    open --> corrected: correction (C16) or user correction (C73)
    trimmed --> corrected
    corrected --> open
    corrected --> trimmed
    corrected --> closed
    closed --> [*]
```

### Inbox event

```mermaid
stateDiagram-v2
    [*] --> received: accept() committed
    received --> processing: worker dequeues (post-time order)
    processing --> done: apply committed
    processing --> received: failure, attempts under 3, or restart reset
    processing --> failed: 3rd failure, raw review card + alert
    processing --> stale: stale entry recorded, no approvable card
    done --> [*]
    failed --> [*]
    stale --> [*]
```
