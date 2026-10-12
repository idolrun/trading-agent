# 3. End-to-End Data Flow

[← Components](02-component-architecture.md) · [Index](README.md) · Next: [Context & correlation →](04-context-correlation.md)

One message, split where the lane slot is released. The message graph ends at `notify`. The human wait is a second graph, one thread per card.

Legend: cylinders are rows; **⚑** is a state change; thick arrows are durable writes.

## 3.1 Part A — message to pending cards (lane slot held)

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef model fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c
    classDef ext fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    classDef store fill:#eceff1,stroke:#37474f,color:#263238
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c

    SRC["Discord gateway event<br/>or POST /ingest"]:::ext
    HM{"webhook: HMAC<br/>and timestamp ok?"}:::code
    R401["401"]:::fail
    ACC["inbox.accept<br/>INSERT ON CONFLICT DO NOTHING<br/>COMMIT, then 200 or gateway ack"]:::code
    ME[("message_events ⚑")]:::store
    IBX[("inbox ⚑ received")]:::store
    NTF["NOTIFY inbox<br/>payload channel_id:provider_id"]:::code

    WRK["lane worker<br/>next_for_lane by posted_at"]:::code
    SKIP{"muted author<br/>or channel not configured?"}:::code
    DONE0[("inbox ⚑ done + skip_reason<br/>still on the feed")]:::store
    PROC[("inbox ⚑ processing<br/>attempts + 1")]:::store
    POI{"attempts > poison_retry_limit?"}:::code
    RAW0["poison: inbox failed<br/>raw review card + alert"]:::fail

    LC["load_context"]:::code
    DEL{"delete, or edit with<br/>no correction service?"}:::code
    RS["resolve_context"]:::code
    FC["find_candidates"]:::code
    FAST["fast_interpret"]:::code
    JV["jev_interpret"]:::model
    LAY["laya_interpret"]:::model
    LX["llm_extract"]:::model
    MC[("model_calls ⚑")]:::store

    subgraph TX["decide_apply — one transaction"]
        S1[("signals ⚑")]:::store
        S2[("message_links ⚑")]:::store
        S3[("provider_positions ⚑<br/>+ provider_position_events")]:::store
        S4[("cards ⚑ pending or disabled<br/>card_events ⚑ created")]:::store
        S5[("timers ⚑ card_expiry<br/>or offhours_release")]:::store
        S6[("inbox ⚑ done or stale<br/>interpret_outcome set")]:::store
    end

    NT["notify<br/>start approval threads<br/>invalidate superseded threads<br/>spawn note_writer"]:::code
    REL["lane lock released"]:::code

    SRC --> HM
    HM -->|"webhook no"| R401
    HM -->|"gateway, or webhook yes"| ACC
    ACC ==> ME
    ACC ==> IBX
    ACC --> NTF
    NTF -.-> WRK
    WRK --> SKIP
    SKIP -->|"yes"| DONE0
    SKIP -->|"no"| PROC
    PROC --> POI
    POI -->|"yes"| RAW0
    POI -->|"no"| LC
    LC --> DEL
    DEL -->|"yes: mark done"| REL
    DEL -->|"no"| RS --> FC --> FAST
    FAST -->|"not settled"| JV
    JV -->|"model_failure"| LAY
    JV -->|"semantic"| LX
    FAST -->|"settled"| TX
    JV -->|"assemble or feed"| TX
    LAY -->|"settled"| TX
    LAY -->|"semantic"| LX
    LX --> TX
    JV -.-> MC
    LAY -.-> MC
    LX -.-> MC
    TX --> NT --> REL
```

Off-hours does not skip interpretation. `decide_apply` still updates the provider book, then schedules `offhours_release` and inserts no cards. At the open, `ReleaseService` re-plans with fresh quotes.

A stale entry or add (`age > stale_entry_s`, default 30 s) is marked inbox `stale`: the book may update, and there is no approvable entry card. Exits, stops, and corrections still card, with `late` when a newer lane event was already applied.

## 3.2 Part B — approval to a reconciled lot (lane slot free)

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef ext fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    classDef store fill:#eceff1,stroke:#37474f,color:#263238
    classDef human fill:#fff3e0,stroke:#e65100,color:#bf360c
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c

    AW["approval graph<br/>thread card:id<br/>interrupt at await_approval"]:::code
    U["User: approve, edit, or reject"]:::human
    TMR["card_expiry timer"]:::code
    SUP["newer message superseded the card"]:::code
    CR["startup expire_on_restart<br/>or resume_approved"]:::code

    AP[("approvals ⚑ before resume<br/>cards ⚑ approved / rejected / expired / invalidated")]:::store
    RES["Command resume"]:::code
    TERM["graph ends<br/>no order"]:::code

    RV{"revalidate<br/>validity + preflight quote"}:::code
    EX["CardExecutor"]:::code
    INV{"submit_order invariants"}:::code
    BLK["invalidated or alert"]:::fail
    OI[("orders ⚑ intent<br/>cards ⚑ submitted")]:::store
    BRK["Alpaca, or SimulatedBroker in shadow"]:::ext
    CONS["BrokerEventConsumer"]:::code
    FL[("fills ⚑")]:::store
    LOT[("lots ⚑ + lot_events<br/>stops updated by brackets.on_fill")]:::store
    RC["reconcile<br/>paper and live"]:::code
    MM[("alerts ⚑ reconcile_mismatch<br/>entries_blocked")]:::fail

    U --> AP
    TMR --> AP
    SUP --> AP
    CR --> AP
    AP --> RES
    AW --- RES
    RES -->|"reject, expire, invalidate"| TERM
    RES -->|"approve"| RV
    RV -->|"failed"| BLK
    RV -->|"ok"| EX --> INV
    INV -->|"violation"| BLK
    INV -->|"pass"| OI
    OI ==> BRK
    BRK -.-> CONS
    CONS ==> FL
    FL ==> LOT
    LOT --> RC
    RC --> MM
```

The decision API writes the approval and the card transition in one transaction, then resumes the graph. Restart recovery calls `resume_approved`. Entry resumes wait while `entries_blocked` is set.

## 3.3 Persistence ledger

| # | Step | Persisted in | Boundary | Idempotency |
|---|---|---|---|---|
| 1 | Gateway or webhook accept | `message_events`, `inbox` | commit before ack | PK `(message_id, event_type, event_seq)` |
| 2 | Dequeue | `inbox` | own update | attempts, `claimed_at` |
| 3 | Stage marks | `stage_timings` | per stage | PK includes `attempt` and `stage` |
| 4 | Model calls | `model_calls` | per attempt | append-only |
| 5 | Message-graph checkpoint | `langgraph.checkpoints` | once, at exit | thread `msg:{message_id}:{event_type}:{event_seq}:{attempt}` |
| 6 | **apply** | `signals`, `message_links`, `provider_positions`, `provider_position_events`, `cards`, `card_events`, `timers`, `inbox` | **one transaction** | `signals` unique on the event key + `idx`; `cards.dedupe_key`; card unique `(signal_id, action)` |
| 7 | Approval | `approvals`, `card_events` | before resume | one `approvals` row per card |
| 8 | Approval checkpoint | `langgraph.checkpoints` | interrupt and resume | thread `card:{card_id}` |
| 9 | Order intent | `orders`, `cards` | before the broker call | `client_order_id` unique |
| 10 | Broker status | `order_events`, `orders` | per event, forward-only | `client_order_id` |
| 11 | Fill | `fills`, `lots`, `lot_events`, `stops` | one transaction | `broker_fill_id` unique |
| 12 | Reconciliation | `lots`, `orders`, `alerts`, `system_flags` | own transaction | mismatch dedupe |

`signal_id` is a UUID. It is not the pair `(message_id, idx)`. That pair is a unique key on `signals` together with `event_type` and `event_seq`.

### Why the split is at notify

Everything that depends on lane order (context, the link, the provider book, the card) commits before the message graph ends. The next event on that lane does not wait for a person. Fills lock the lot and do not take the lane lock.

### Failure points on this path

Postgres down at accept (webhook `503`; the gateway retries on reconnect plus the gap detector) · poison after `poison_retry_limit` (default 3) · model chain exhausted (raw review card) · quote missing at validate (review card) · crash after the intent row (status query by `client_order_id`) · missed fill (the consumer plus periodic reconcile).
