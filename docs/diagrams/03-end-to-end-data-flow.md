# 3. End-to-End Data Flow

[← Components](02-component-architecture.md) · [Index](README.md) · Next: [Context & correlation →](04-context-correlation.md)

The journey of one message: **ingestion → persistence → context resolution → interpretation → validation → signal/ledger update → decision → approval → order execution → broker → fills → reconciliation.** It is split at the approval pause, because that is where the provider slot is released and the time scale changes from milliseconds to minutes.

Legend for this section: cylinders = persisted rows; **⚑** = a state change; thick arrows = durable writes.

## 3.1 Part A — message to approval card (provider slot held)

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef model fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c
    classDef ext fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    classDef store fill:#eceff1,stroke:#37474f,color:#263238
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c

    MAPI["Message API event<br/>create / edit / delete"]:::ext
    HM{"HMAC + timestamp<br/>window valid?"}:::code
    R401["reject (401)"]:::fail
    ACC["inbox.accept(envelope)<br/>INSERT … ON CONFLICT DO NOTHING<br/>COMMIT → then 200"]:::code
    DUP["duplicate → no-op, 200"]:::code
    ME[("message_events ⚑ new immutable row<br/>raw payload, posted_at (snowflake),<br/>reply_to_id, backfilled flag, t_received")]:::store
    IBX[("inbox ⚑ status = received")]:::store
    NTF["NOTIFY provider channel"]:::code

    WRK["provider worker (1 per provider_id)<br/>next_for_provider(): oldest by posted_at"]:::code
    PROC[("inbox ⚑ status = processing<br/>t_dequeued, attempts + 1")]:::store
    AGE{"age = now − posted_at<br/>> stale_entry_s ?"}:::code
    CATCH["catch-up mode if backlog > N<br/>or oldest > stale_entry_s → alert"]:::fail

    LC["load_context — C71 scope only<br/>message, reply parent, author recent msgs,<br/>@mentions, provider book, provider profile"]:::code
    FC["find_candidates"]:::code
    JV["jev_interpret"]:::model
    RT{"route"}:::code
    LX["llm_extract (slow path)"]:::model
    MC[("model_calls ⚑ per call / attempt")]:::store
    AS["assemble_signal<br/>fields + provenance"]:::code
    VA["validate<br/>symbol map (SPX→SPXW), optionable list,<br/>chain + quote identity, scale, adjacent strike,<br/>dates (XNYS calendar)"]:::code
    MD["marketdata cache"]:::ext
    DC["decide (pure)<br/>action · qty · card tier · risk limits<br/>stale → no approvable card"]:::code

    subgraph TX["apply — ONE Postgres transaction (provider rows FOR UPDATE)"]
        S1[("signals ⚑ (message_id, idx)<br/>fields + provenance")]:::store
        S2[("message_links ⚑ signal → provider_position<br/>method, confidence")]:::store
        S3[("provider_positions ⚑<br/>open / add / trim / closed / stop info")]:::store
        S4[("cards ⚑ pending — UNIQUE (signal_id, action)<br/>card_events ⚑ created")]:::store
        S5[("timers ⚑ card expiry deadline<br/>(from posted_at, C90)")]:::store
        S6[("inbox ⚑ status = done")]:::store
    end

    NT["notify: SSE to dashboard + Web Push<br/>t_card, t_pushed recorded"]:::code
    NW["note_writer (async, off critical path)"]:::model
    REL["⚑ provider slot RELEASED<br/>worker takes provider's next event"]:::code
    AW["await_approval: interrupt()<br/>⚑ checkpoint persisted"]:::code

    MAPI --> HM
    HM -->|"no"| R401
    HM -->|"yes"| ACC
    ACC -->|"conflict"| DUP
    ACC ==> ME
    ACC ==> IBX
    ACC --> NTF
    NTF -.->|"LISTEN"| WRK
    WRK ==> PROC
    PROC --> AGE
    AGE -.->|"backlog signal"| CATCH
    AGE -->|"flag stale / late in state"| LC
    LC --> FC --> JV --> RT
    RT -->|"slow path"| LX --> AS
    RT -->|"fast path"| AS
    JV -.-> MC
    LX -.-> MC
    AS --> VA
    VA <-->|"chain, quote"| MD
    VA --> DC
    DC ==> TX
    TX --> NT
    NT -.-> NW
    NT --> REL
    REL --> AW
```

## 3.2 Part B — approval to reconciled lot (provider slot NOT held)

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef ext fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    classDef store fill:#eceff1,stroke:#37474f,color:#263238
    classDef human fill:#fff3e0,stroke:#e65100,color:#bf360c
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c

    AW["await_approval (paused thread)"]:::code
    U["Trader: approve / edit / reject<br/>(dashboard or phone)"]:::human
    TMR["timers: expiry deadline"]:::code
    SUP["newer message on same position<br/>(supersession, OD-14)"]:::code
    CR["crash recovery"]:::code

    AP[("approvals ⚑ written BEFORE resume<br/>card_events ⚑ approved")]:::store
    RES["Command(resume=decision)"]:::code
    TERM[("cards ⚑ rejected / expired / invalidated<br/>card_events ⚑ + reason · no order (C18)")]:::store

    RV{"revalidate (C22)<br/>entry: within N s of posted_at,<br/>ask ≤ signal + X%, not superseded<br/>exit: position not already closed"}:::code
    INV{"submit_order()<br/>FR-20 invariants on fresh broker read"}:::code
    BLK["blocked → alert"]:::fail
    OI[("orders ⚑ status = intent<br/>client_order_id = uuid5(card_id, action_seq)<br/>cards ⚑ submitted")]:::store
    BRK["Broker"]:::ext
    OE[("order_events ⚑ submitted → working<br/>→ partially_filled → filled / cancelled")]:::store
    LOOP["execution loops<br/>entry: cancel remainder after M s<br/>exit: re-price mid → bid until floor"]:::code
    FL[("fills ⚑ immutable")]:::store
    LOT[("lots ⚑ create / grow (avg price) /<br/>reduce / close — lot row lock<br/>stops ⚑ bracket legs, restored if exit unfilled")]:::store
    NP["push: fill notification"]:::code
    RC["reconcile (after every fill + every 60 s)<br/>lots & working orders vs broker"]:::code
    OK["consistent"]:::code
    MM[("alerts ⚑ mismatch<br/>⚑ entry cards BLOCKED until resolved")]:::fail

    U --> AP --> RES
    TMR --> RES
    SUP --> RES
    CR --> RES
    AW --- RES
    RES -->|"reject / expire / invalidate"| TERM
    RES -->|"approve (+ edits)"| RV
    RV -->|"invalid"| TERM
    RV -->|"valid"| INV
    INV -->|"violation"| BLK
    INV -->|"pass"| OI
    OI ==> BRK
    BRK -.->|"status / fill events (OD-10)"| OE
    OE --> LOOP
    LOOP -->|"replace / cancel"| BRK
    BRK -.-> FL
    FL ==> LOT
    LOT --> NP
    LOT --> RC
    RC <-->|"positions, orders"| BRK
    RC --> OK
    RC --> MM
```

## 3.3 Persistence and state-change ledger

| # | Step | Persisted in | Transaction boundary | State change | Idempotency key |
|---|---|---|---|---|---|
| 1 | Webhook / backfill accept | `message_events`, `inbox` | own txn, commit before `200` | inbox `received` | `(message_id, event_type, event_seq)` |
| 2 | Dequeue | `inbox` | own txn | `processing`, attempts+1 | event_id |
| 3 | Interpretation | `model_calls` | per call | none (audit only) | — (repeatable, no side effects) |
| 4 | Graph progress | LangGraph checkpoints | per node | orchestration only | `thread_id = msg:<message_id>:<event_seq>` |
| 5 | **apply** | `signals`, `message_links`, `provider_positions`, `cards`, `card_events`, `timers`, `inbox` | **one txn** | provider ledger + card `pending` + inbox `done` | card UNIQUE `(signal_id, action)` |
| 6 | Approval | `approvals`, `card_events` | own txn, **before** resume | card `approved` | approval_id |
| 7 | Order intent | `orders`, `cards` | own txn, **before** broker call | order `intent`, card `submitted` | `client_order_id` UNIQUE |
| 8 | Broker status | `order_events` | per event | order state machine | client_order_id + broker id |
| 9 | Fill | `fills`, `lots`, `stops` | one txn per fill, lot row lock | lot created / grown / reduced / closed | broker fill id (**OD-10** defines source) |
| 10 | Reconciliation | `lots`, `orders`, `alerts` | own txn | adopt orders; foreign lots; entry gate | client_order_id prefix |

### Explanation
- **Why the split at `apply`:** everything that depends on per-provider order (context, linking, ledger, card) is committed atomically before the run pauses. After resume, nothing touches the provider ledger, so the next message from the same provider never waits on a human (FR-1a, C88).
- **Why the approval row precedes resume:** AC-2 ("no order without an approval record") is checked against domain tables, not against checkpoint internals (TRD §5.1).
- **Why the order intent precedes the broker call:** if the process dies between the two, restart finds the intent and queries the broker by `client_order_id` instead of resubmitting (AC-7).
- **Validity clocks start at `posted_at`**, not at card creation (C90); a backlog therefore cannot create a fresh-looking card for an old price.

### Failure points on this path
Postgres down at step 1 (→ `503`, redelivery, OD-1) · poison at steps 2–5 (3 attempts → raw review card) · LLM chain exhausted at step 3 (raw review card) · market data missing at validate (review card, OD-12) · crash between 7 and broker call (resolved by status query) · missing fill event (periodic reconciliation catches it).
