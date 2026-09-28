# 9. Failure & Recovery Architecture

[← Deployment](08-deployment.md) · [Index](README.md) · Next: [Architecture summary →](10-architecture-summary.md)

Guiding rules (PRD G5, §11; TRD §9):
1. **Never lose a signal silently.** Every message event is durable before it is acknowledged, and every failure leaves a visible record (card or alert).
2. **Never act on stale or unverified state.** When state is uncertain, the system blocks *entries*, not *exits*, and expires open approvals.
3. **The broker is the source of truth for holdings;** Postgres is the source of truth for decisions. Reconciliation reconnects them after any failure.
4. **Every side effect is idempotent:** event PK, card UNIQUE `(signal_id, action)`, deterministic `client_order_id`.

## 9.1 Failure-domain map

```mermaid
flowchart LR
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef ok fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    classDef na fill:#fafafa,stroke:#9e9e9e,color:#757575,stroke-dasharray: 5 5

    F1["Message ingestion fails<br/>(API outage, webhook error, silence)"]:::fail
    F2["Jev / LLM fails"]:::fail
    F3["PostgreSQL unavailable"]:::fail
    F4["Kafka / Redis unavailable"]:::na
    F5["Broker API fails"]:::fail
    F6["Market data unavailable"]:::fail
    F7["App crashes mid-order"]:::fail

    R1["503 → redelivery · gap backfill ·<br/>feed-health alert · catch-up mode ·<br/>stale entries not carded"]:::code
    R2["Jev → slow path · LLM chain fallback ·<br/>circuit breakers · raw review card + alert"]:::code
    R3["intake 503 · dispatch stops · trading halts ·<br/>HA failover ≤ 5 min · startup sequence"]:::code
    R4["NOT APPLICABLE — not deployed (C94)"]:::na
    R5["no blind retry: query by client_order_id ·<br/>unknown state resolved · reconcile"]:::code
    R6["PROPOSED (OD-12): review cards, entries blocked,<br/>urgent alert 'underlying stops blind'"]:::code
    R7["intent-before-call · idempotent re-run ·<br/>adopt working orders · open cards expire"]:::code

    G["Consistency preserved:<br/>no lost event · no duplicate card ·<br/>no duplicate order · lots = broker"]:::ok

    F1 --> R1 --> G
    F2 --> R2 --> G
    F3 --> R3 --> G
    F4 --> R4
    F5 --> R5 --> G
    F6 --> R6 --> G
    F7 --> R7 --> G
```

---

## 9.2 Message ingestion failure

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef ext fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    classDef future fill:#fafafa,stroke:#9e9e9e,color:#757575,stroke-dasharray: 5 5

    W["webhook delivery"]:::ext
    H{"HMAC valid?"}:::code
    REJ["401, logged (possible attack)"]:::fail
    PG{"Postgres commit OK?"}:::code
    S503["503 → Message API retries<br/>(≥ 10 min required, OD-1)"]:::fail
    OK["200 after commit"]:::code

    WD["feed-health watchdog (market hours)"]:::code
    SIL{"no message AND no heartbeat<br/>for N s?"}:::code
    DEGR["URGENT alert · dashboard 'degraded' (FR-4)"]:::fail

    GD["gap detector: startup · reconnect · every 60 s<br/>fetch history after last snowflake per channel"]:::code
    GAP{"missing messages?"}:::code
    BF["accept() each · flag backfilled<br/>duplicates are no-ops"]:::code
    NOHIST["no history API and no redelivery"]:::future
    SPOOL["local append-only spool in front of Postgres<br/>(must be decided before ingest is built)"]:::future

    CU{"backlog > N or oldest > stale_entry_s?"}:::code
    CM["CATCH-UP MODE + alert until drained"]:::fail
    ST["entries older than stale_entry_s:<br/>ledger only, feed 'stale', no card<br/>exits / stops / corrections: carded 'late'"]:::code

    W --> H
    H -->|"no"| REJ
    H -->|"yes"| PG
    PG -->|"no"| S503
    PG -->|"yes"| OK
    S503 -.->|"retry later"| W
    WD --> SIL
    SIL -->|"yes"| DEGR
    DEGR -.->|"on reconnect"| GD
    GD --> GAP
    GAP -->|"yes"| BF --> CU
    GAP -->|"no"| OK
    CU -->|"yes"| CM --> ST
    NOHIST -.-> SPOOL
```

```mermaid
sequenceDiagram
    autonumber
    participant API as Message API
    participant ING as ingest
    participant DB as Postgres
    participant WP as workers
    participant W as web
    actor U as Trader

    Note over API,ING: 10-minute Message API outage during market hours
    ING->>ING: watchdog: no message, no heartbeat for N s
    ING->>DB: INSERT alerts feed_silence
    ING--)W: URGENT push, status degraded
    W--)U: 'feed degraded'
    API-->>ING: connection restored
    ING->>API: history after last snowflake, per channel
    API-->>ING: 140 missed messages
    loop each missed message
        ING->>DB: accept() flagged backfilled (ON CONFLICT no-op)
    end
    DB--)WP: NOTIFY
    WP->>WP: backlog over threshold, CATCH-UP MODE on
    WP->>DB: alert catch-up
    WP->>WP: process each provider in post-time order
    Note over WP: old entries → stale feed only · old exits/stops/corrections → cards with 'late' badge
    WP->>DB: backlog drained, catch-up cleared
```

**Consistency:** AC-21 — every message posted during the outage is redelivered or backfilled, none lost or duplicated. **Failure point:** if the Message API offers neither redelivery nor history, a Postgres outage or app outage loses messages; the greyed spool is then mandatory (OD-1).

---

## 9.3 LLM / model failure

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef model fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c

    M["message needs interpretation"]:::code
    J{"Jev call OK?<br/>(429/529 retried once)"}:::model
    JD["route ALL to slow path<br/>(latency rises, nothing lost)"]:::code
    FAST["fast path"]:::code
    SL["slow path, deadline 3,000 ms"]:::code
    CB{"next vendor in chain<br/>circuit closed & key present?"}:::code
    SKIP["skip vendor<br/>(breaker: 5 fails / 60 s → open 30 s → half-open probe)"]:::code
    CALL["call vendor k"]:::model
    RES{"outcome"}:::code
    INFRA["timeout · 429 · 5xx · connection ·<br/>schema-invalid · empty · refusal"]:::fail
    CONT["content rejection:<br/>literal span missing, chain/quote mismatch"]:::fail
    RV["REVIEW CARD (no answer shopping)"]:::fail
    OKX["ParsedSignals from ONE model"]:::code
    EXH{"deadline left and vendors left?"}:::code
    RAW["RAW REVIEW CARD, blank fields<br/>+ 'parser degraded' alert (FR-9, AC-9)"]:::fail
    NOTE["note writer fails → card shown without note<br/>(off critical path)"]:::code

    M --> J
    J -->|"ok"| FAST
    J -->|"down"| JD --> SL
    FAST -->|"needs slow path"| SL
    SL --> CB
    CB -->|"no"| SKIP --> EXH
    CB -->|"yes"| CALL --> RES
    RES -->|"infra failure"| INFRA --> EXH
    RES -->|"content failure"| CONT --> RV
    RES -->|"valid"| OKX
    EXH -->|"yes"| CB
    EXH -->|"no"| RAW
```

**Consistency:** model calls have no side effects, so repeating them after a crash is safe. A missing API key disables that vendor at startup with an alert. Every attempt is recorded in `model_calls`.

---

## 9.4 PostgreSQL unavailable

```mermaid
sequenceDiagram
    autonumber
    participant API as Message API
    participant APP as app
    participant PG as Postgres primary
    participant SB as standby
    participant B as Broker
    participant UPT as uptime monitor
    actor U as Trader

    PG--xAPP: connection lost
    API->>APP: POST /ingest
    APP-->>API: 503 (no commit possible, API will redeliver)
    APP->>APP: dispatcher stops, no new graph steps, trading HALTS
    Note over B: broker-held premium & stock stops and OCO TPs keep working
    Note over APP: system-held underlying stops cannot record fires, treat as not protecting (PRD 11)
    UPT->>APP: GET /health fails (heartbeat not written)
    UPT--)U: page phone
    SB->>SB: automatic failover, promoted (RTO target 5 min)
    APP->>SB: reconnect
    APP->>APP: run full startup sequence (block entries, adopt, reconcile, expire cards, reset inbox, backfill, timers, quotes, dispatcher)
    APP->>B: reconcile before allowing entries (C93)
    API->>APP: redelivered events
    APP->>SB: accept() (duplicates no-op)
```

**PITR restore (worse case):** data after the restore point may be lost for RPO-5-min tables. Orders placed after that point are recovered by reconciliation, which **adopts broker orders by `client_order_id` prefix** even if their intent rows were lost (TRD §9); missed messages are refilled by the gap detector. Trading data (approvals, intents, fills, ledger) is on the synchronous path so its RPO is ≈ 0.

**Why halting is correct:** there is no state to trade against, and a queue in front of Postgres (Kafka) would create exactly the dangerous mode TRD §15.1 lists: offsets ahead of a restored database, so trims/stops never re-process.

---

## 9.5 Kafka / Redis unavailable — not applicable

Neither is deployed (PRD C94, TRD §15). Their would-be failure modes were a reason for that decision:

| Would-be component | Failure mode avoided |
|---|---|
| Kafka | consumer lag → acting on stale signals; dead-lettered exit → lot never sold; offset reset → duplicate cards; PG restore behind offsets; retention expiry during outage |
| Redis | lost ~1 s of writes on failover; stale cached provider book → mis-linked follow-ups; dual-write races; a third source of truth for reconciliation |

If either is adopted on a measured trigger, it holds only rebuildable data (Redis) or sits behind the `inbox` boundary with offsets committed after `apply` (Kafka); its outage must then degrade to Postgres/broker with no correctness loss.

---

## 9.6 Broker API failure

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef ext fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20

    SUB["submit_order(): intent committed"]:::code
    CALL["place order at broker"]:::ext
    R{"response"}:::code
    ACK["accepted → submitted"]:::code
    REJ["broker reject → order rejected<br/>card notified · stops restored if exit"]:::fail
    TO["timeout / connection error"]:::fail
    Q["query by client_order_id (never blind retry)"]:::code
    QR{"found?"}:::code
    ADOPT["record real status"]:::code
    NF{"safe to resubmit?<br/>(same client_order_id, still valid)"}:::code
    RESUB["resubmit with SAME client_order_id<br/>(broker dedupes if supported, OD-3)"]:::code
    UNK["status unknown → alert<br/>entry gate BLOCKED until reconciled"]:::fail
    DOWN["broker unreachable for a period"]:::fail
    DOWNR["no new orders · cards cannot submit<br/>exit re-price loops pause + alert<br/>broker-held stops/TPs still active at broker"]:::fail
    REC["on recovery: reconcile positions + orders<br/>adopt · update · flag mismatches"]:::code
    STREAM["fill / order stream disconnect (OD-10)"]:::fail
    POLL["periodic reconcile (60 s) catches missed fills"]:::code

    SUB --> CALL --> R
    R -->|"ok"| ACK
    R -->|"reject"| REJ
    R -->|"timeout"| TO --> Q --> QR
    QR -->|"yes"| ADOPT
    QR -->|"no"| NF
    NF -->|"yes"| RESUB
    NF -->|"no / cannot tell"| UNK
    DOWN --> DOWNR --> REC
    STREAM --> POLL --> REC
```

The TRD requires "query before any retry" but does not define when a not-found order may be resubmitted; the "safe to resubmit" branch reuses the same `client_order_id` so the worst case is a broker-side duplicate rejection, never a second order. Exact semantics depend on the chosen broker's client-ID dedupe (OD-3).

---

## 9.7 Market data unavailable — **PROPOSED policy (OD-12)**

PRD/TRD only specify that a failed identity check yields a review card (FR-11) and that the SPX index entitlement is required for system-held stops (C49). The following policy is **proposed** and must be confirmed.

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef prop fill:#fafafa,stroke:#9e9e9e,color:#616161,stroke-dasharray: 5 5

    MD["quote stream stale or chain API failing<br/>(staleness threshold — PROPOSED)"]:::fail
    A1["validate: chain / quote unavailable →<br/>REVIEW card 'market data unavailable' (FR-11)"]:::code
    A2["revalidate entry: price band cannot be checked →<br/>card cannot be approved (PROPOSED)"]:::prop
    A3["exit pricing (mid → bid) impossible →<br/>exit submission paused + URGENT alert (PROPOSED)"]:::prop
    A4["system-held underlying stops BLIND →<br/>URGENT alert 'underlying stops unprotected' (PROPOSED)"]:::prop
    A5["broker-held premium / stock stops and TPs<br/>unaffected (live at broker)"]:::code
    A6["dashboard health: market data degraded"]:::fail
    R["on recovery: resubscribe · re-evaluate<br/>system-held stops against current price immediately"]:::prop

    MD --> A1
    MD --> A2
    MD --> A3
    MD --> A4
    MD --> A5
    MD --> A6
    A4 --> R
```

---

## 9.8 Application crash during an order

Crash points on the order path and why each is safe:

```mermaid
sequenceDiagram
    autonumber
    participant W as web
    participant DB as Postgres
    participant G as graph run
    participant X as execution
    participant B as Broker
    participant RS as restart sequence

    W->>DB: approvals row committed
    Note over W,DB: CRASH POINT 1 — after approval, before resume
    W->>G: Command(resume approve)
    G->>X: submit_order
    X->>DB: orders intent + client_order_id committed
    Note over X,DB: CRASH POINT 2 — intent committed, broker not called
    X->>B: place order (client_order_id)
    Note over X,B: CRASH POINT 3 — broker accepted, response not recorded
    B-->>X: accepted
    X->>DB: order_events submitted
    Note over X,B: CRASH POINT 4 — during re-price loop or awaiting fills

    RS->>DB: 1 block entries
    RS->>B: 2 load positions + working orders
    RS->>DB: 3 adopt working orders by client_order_id prefix (points 3, 4)
    RS->>DB: 4 reconcile lots vs broker (fills during downtime recorded)
    RS->>G: 5 resume open threads with expire
    alt crash point 1 (approved, never submitted)
        G->>DB: card expired on restart (PRD 11: open cards expire), no order
    else crash point 2 (intent, no broker order)
        RS->>B: query client_order_id → not found
        RS->>DB: intent closed (exact status not specified in TRD), card closed, alert
    else crash point 3 or 4 (order at broker)
        RS->>DB: adopted, NOT resubmitted, exit loops re-armed or user alerted
    end
    Note over RS: A re-executed submit_order node derives the SAME client_order_id = uuid5(card_id, action_seq), finds the existing order, and does not duplicate it (AC-7)
```

**Crash point 1 note:** PRD §11 says approval cards open at crash time expire. Whether an *approved-but-not-yet-submitted* card should expire or resume submission after restart is not explicitly stated; the diagram follows the stricter reading (expire, user re-acts) because prices will have moved. Flagged for confirmation alongside OD-14.

**Crash mid-message (before approval):** the event is re-processed from `load_context`; model calls repeat (no side effects); `apply` is one transaction; card uniqueness prevents a second card.

---

## 9.9 Consistency guarantees summary

| Failure | Data at risk | Guarantee | Mechanism | Test |
|---|---|---|---|---|
| Duplicate webhook | message | exactly-once receipt | PK `(message_id, event_type, event_seq)` | TRD §12 "duplicate delivery" |
| Crash during processing | card | no duplicate card | apply txn + UNIQUE `(signal_id, action)` | load test 5 (kill -9 each node) |
| Crash during order | order | no duplicate order | intent-before-call, deterministic `client_order_id`, adopt | AC-7 |
| Broker timeout | order | no blind retry | status query by client ID | FR-22 |
| Poison message | provider queue | no halt, no silent drop | 3 attempts → raw review card | AC-20 |
| LLM outage | signal | no loss | chain → raw review card | AC-9 |
| Message API outage | messages | no loss | redelivery / backfill, catch-up | AC-21 |
| Postgres failover | approvals / intents / fills / ledger | RPO ≈ 0, RTO ≤ 5 min | sync standby, startup sequence, reconcile | AC-23, load test 6 |
| Lot / broker drift | holdings | detected, entries blocked | reconcile startup + 60 s + post-fill | paper gate |
| App down | system-held stops | **not protected** (accepted risk) | uptime monitor pages phone | PRD §21 |
