# 9. Failure and Recovery

[← Deployment](08-deployment.md) · [Index](README.md) · Next: [Architecture summary →](10-architecture-summary.md)

Three rules the code follows:

1. A message is durable before it is acknowledged, and a failed run leaves a card or an alert.
2. When state is uncertain, new entries stop. Exits still run. Open approvals from before a restart are expired.
3. In paper and live, Alpaca is the source of truth for holdings. Postgres is the source of truth for decisions. Shadow has no broker account to compare.

Idempotency keys: event primary key, `cards.dedupe_key`, unique `(signal_id, action)`, unique `client_order_id`, unique `broker_fill_id`.

## 9.1 Failure map

```mermaid
flowchart LR
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef ok fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20

    F1["Discord or webhook down"]:::fail
    F2["Jev, Laya, or the LLM chain fails"]:::fail
    F3["PostgreSQL unavailable"]:::fail
    F5["Alpaca order API fails"]:::fail
    F6["Snapshots unavailable"]:::fail
    F7["Process dies mid-order"]:::fail

    R1["webhook 503 · gateway reconnect<br/>gap backfill · feed_degraded<br/>stale entries not carded"]:::code
    R2["Laya after a Jev model failure<br/>LLM after a semantic miss<br/>raw review when the chain is exhausted"]:::code
    R3["webhook 503 · workers stop<br/>startup blocks entries until reconcile"]:::code
    R5["no blind retry<br/>query by client_order_id<br/>reconcile adopts working orders"]:::code
    R6["review card market_data_unavailable<br/>revalidation refuses the approve<br/>underlying_stop alert when the poll is blind"]:::code
    R7["intent row already committed<br/>resume_approved after startup<br/>pending cards expired on restart"]:::code

    G["no lost event · no duplicate card<br/>no duplicate order · lots match the broker<br/>in paper and live"]:::ok

    F1 --> R1 --> G
    F2 --> R2 --> G
    F3 --> R3 --> G
    F5 --> R5 --> G
    F6 --> R6 --> G
    F7 --> R7 --> G
```

Kafka and Redis are not in the process, so they are not failure domains.

## 9.2 Ingestion

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef ext fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20

    W["POST /ingest"]:::ext
    H{"HMAC and ±300 s ok?"}:::code
    REJ["401"]:::fail
    PG{"commit ok?"}:::code
    S503["503, caller retries"]:::fail
    OK["200 after commit"]:::code

    GW["Discord gateway"]:::ext
    DROP["channel not watched: not inserted"]:::code
    ACC["accept, same as the webhook"]:::code
    DISC["on_disconnect → feed health"]:::fail

    WD["FeedHealthMonitor, market hours"]:::code
    SIL{"silence past the hold?"}:::code
    DEGR["flag feed_degraded<br/>alert feed_silence"]:::fail

    GD["GapDetector every 60 s<br/>and once on feed recovery"]:::code
    BF["fetch_after per channel<br/>accept with backfilled true<br/>duplicates no-op"]:::code
    LIM["thread history under a channel<br/>is not paged"]:::fail

    CU{"backlog over the catch-up line<br/>or oldest event past stale_entry_s?"}:::code
    CM["flag catch_up + alert"]:::fail

    W --> H
    H -->|"no"| REJ
    H -->|"yes"| PG
    PG -->|"no"| S503
    PG -->|"yes"| OK
    GW --> DROP --> ACC
    GW --> DISC --> WD
    WD --> SIL
    SIL -->|"yes"| DEGR
    DEGR --> GD --> BF
    BF --> LIM
    ACC --> CU
    CU -->|"yes"| CM
```

The listener's own heartbeat is every 30 s. `POST /ingest/heartbeat` is the same liveness mark for an external Message API.

There is no local spool. If both the gateway and Postgres are down, messages wait in Discord and the gap detector reads history after the database is back. That history call does not page messages that live only inside threads.

Catch-up is a flag and an alert. The lane still processes in `posted_at` order. Entries older than `stale_entry_s` are stored as inbox `stale` and do not become approvable cards.

## 9.3 Models

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef model fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c

    FAST["fast path settled it"]:::code
    JEV["Jev"]:::model
    WHY{"failure kind"}:::code
    LAYA["Laya, if enabled"]:::model
    LLM["LLM chain<br/>skip a provider with no key or an open breaker"]:::model
    NEXT["next chain entry<br/>timeout, 429, 5xx, schema, refusal"]:::code
    SPAN["span not in the text"]:::fail
    RAW["raw review card<br/>parser_degraded when the chain is exhausted"]:::fail
    SCOPE["scope violation<br/>nothing sent, urgent alert"]:::fail
    SPEND["daily llm_spend alert<br/>over the configured cap"]:::fail

    FAST --> JEV
    JEV --> WHY
    WHY -->|"model_failure"| LAYA
    WHY -->|"semantic"| LLM
    WHY -->|"link"| RAW
    LAYA -->|"miss"| LLM
    LAYA -->|"link"| RAW
    LLM --> NEXT --> LLM
    LLM --> SPAN --> RAW
    LLM -->|"deadline or chain done"| RAW
    JEV --> SCOPE
    LLM --> SPEND
```

A content failure does not shop for another model. A vendor that is named in config and has no API key is skipped at startup with a `provider_disabled` warning. Laya `busy` and `queue_timeout` are `model_calls.outcome` values; the message then follows the same fallback as any other Laya miss.

`interpret_outcome` on the inbox row records which of these endings happened, with the trace in `interpret_reasons`.

## 9.4 PostgreSQL

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef ok fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20

    DOWN["database unreachable"]:::fail
    BOOT["lifespan SELECT 1 fails<br/>process does not serve"]:::fail
    LIVE["already up: webhook 503<br/>dispatcher cannot claim<br/>config listener and bus reconnect"]:::code
    BACK["database returns"]:::ok
    START["startup sequence<br/>entries_blocked until reconcile is clean"]:::code

    DOWN --> BOOT
    DOWN --> LIVE
    LIVE --> BACK --> START
```

The local compose file is a single PostgreSQL 17. Failover and PITR are the managed-database target in F064, not a second container in this repo. The app's recovery path is the startup sequence in [06.8](06-sequence-diagrams.md), which blocks entries until lots match the broker.

`BookChanged` during `apply` means the lane book moved after planning. The run fails, the attempt counts, and the worker retries against the new book. It does not commit a stale plan.

## 9.5 Broker

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef store fill:#eceff1,stroke:#37474f,color:#263238

    CALL["place or cancel"]:::code
    OK["order_events submitted or working"]:::store
    TO["timeout"]:::fail
    QRY["get by client_order_id"]:::code
    UNK["status unknown until the query or reconcile"]:::fail
    REJ["broker rejected<br/>card invalidated order_rejected<br/>if every order on the card was refused"]:::fail
    INV["invariant failed before the call<br/>alert invariant_block, no broker request"]:::fail
    MODE["paper or live endpoint<br/>does not match trading_mode"]:::fail
    HALT["process refuses to start"]:::fail

    CALL --> OK
    CALL --> TO --> QRY
    QRY --> OK
    QRY --> UNK
    CALL --> REJ
    CALL --> INV
    MODE --> HALT
```

`check_mode` runs at startup and on every submit. Shadow selects `SimulatedBroker` and does not call Alpaca. A quote-less simulated order stays working, the same as a resting order with no market.

The consumer subscribes to both the Alpaca event source and the simulator, so a mode switch does not require a second process. Fills for the mode that is not selected simply do not arrive.

## 9.6 Market data

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c

    Q["validate or revalidate needs a quote"]:::code
    MISS["no snapshot or an empty side"]:::fail
    REV["review reason market_data_unavailable<br/>alert deduped per hour"]:::fail
    APR["approve revalidation adds quote blocked<br/>card invalidated, no order"]:::fail
    MAN["manual entry raises market_data_unavailable"]:::fail
    STOP["underlying-stop poll fails"]:::fail
    AL["alert underlying_stop or market_data<br/>the stop is not fired on a missing price"]:::fail

    Q --> MISS --> REV
    MISS --> APR
    MISS --> MAN
    STOP --> AL
```

A zero bid or ask from IEX or the indicative feed is treated as "no quote on that side", not as a price of zero. Scale checks that need a quote to choose between `1.40` and `140` become a review instead of a guess.

System-held stops do not fire while the process is down or the snapshot poll is blind. Broker-held stops stay at Alpaca and do not need this process.

## 9.7 Crash during an order

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef store fill:#eceff1,stroke:#37474f,color:#263238
    classDef ok fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20

    A["died before the intent commit"]:::code
    B["died after intent, before the broker ack"]:::code
    C["died after the ack, before the fill row"]:::code

    A1["approval graph still approved<br/>startup resume_approved retries execute<br/>same client_order_id"]:::ok
    B1["startup reconcile sees the broker order<br/>or the status query finds it<br/>no second place"]:::ok
    C1["consumer or reconcile writes the fill<br/>broker_fill_id dedupes"]:::store

    A --> A1
    B --> B1
    C --> C1
```

Pending cards that were never approved are expired with reason `restart` during startup, and their approval threads are resumed with `expire`. Approved cards are not expired. Their execute step runs again after entries are allowed.

`ExitPolicy.adopt` reattaches open exit orders so re-pricing continues. Due timers are fired in the same startup, including a cutoff that elapsed while the process was down.

## 9.8 Consistency summary

| Guarantee | How |
|---|---|
| No lost event once accepted | Primary key, commit before `200`, gateway reconnect plus `GapDetector` |
| No duplicate card | `dedupe_key` and unique `(signal_id, action)` |
| No duplicate order | `client_order_id` checked before the broker call |
| No duplicate fill | unique `broker_fill_id`, forward-only order status |
| Lane order | one asyncio lock per `(channel_id, provider_id)`, claim by `posted_at` |
| One trader process | Postgres advisory lock for the lifetime of the process |
| Entries blocked while books disagree | `entries_blocked` from startup, failover, or `reconcile_mismatch` |
| Exits still possible while entries are blocked | `submit_order` checks the flag for `OrderKind.ENTRY` |
| Audit of an approval | `approvals` plus `card_events`, written before resume |
| Model cannot invent a digit | literal-span check on every interpreter, including fast |
| Model cannot resolve an ambiguous link | `resolve_link` keeps the hint as evidence |
