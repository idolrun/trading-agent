# 1. High-Level System Architecture

[← Index](README.md) · Next: [Component architecture →](02-component-architecture.md)

## 1.1 System context and containers

```mermaid
flowchart TB
    classDef ext fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef model fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c
    classDef human fill:#fff3e0,stroke:#e65100,color:#bf360c
    classDef store fill:#eceff1,stroke:#37474f,color:#263238

    subgraph PEOPLE["People"]
        TRADER["Trader — single user<br/>desktop browser + phone PWA<br/>approves every order action"]:::human
        OPER["Operator<br/>server console only<br/>(password recovery, runbooks)"]:::human
    end

    subgraph UPSTREAM["Upstream signal source"]
        DISCORD["Discord channels<br/>5–20 signal providers<br/>1,000–2,500 msgs / session"]:::ext
        MAPI["User's Message API<br/>push webhook + heartbeat<br/>history API for backfill (OD-1)"]:::ext
    end

    subgraph APP["Copy-Trading Assistant — one Python 3.12 asyncio process"]
        direction TB
        INGEST["Ingest<br/>HMAC webhook · gap detector<br/>feed-health watchdog"]:::code
        SEQ["Inbox + Dispatcher<br/>per-provider ordered workers<br/>staleness · poison handling"]:::code
        PIPE["Signal pipeline — LangGraph StateGraph<br/>load_context → interpret → validate<br/>→ decide → apply → notify → await_approval"]:::code
        INTERP["Interpretation<br/>Jev fast path · LLM slow path<br/>advisory note writer"]:::model
        CARDS["Cards & approvals<br/>card state machine · validity timers"]:::code
        EXEC["Execution<br/>submit_order() · FR-20 invariants<br/>pricing · re-pricing · stops · EOD close"]:::code
        RECON["Reconciliation<br/>startup + every 60 s + after fills"]:::code
        MDA["Market-data adapter<br/>in-process quote & chain cache"]:::code
        BRKA["Broker adapter<br/>thin, client-order-ID aware"]:::code
        WEB["Web tier<br/>FastAPI + HTMX + SSE · PWA<br/>password auth · Web Push · manual ops"]:::code
    end

    PG[("PostgreSQL 16 — managed HA + PITR<br/>ONLY source of truth for system state<br/>inbox queue · ledgers · cards · orders<br/>7-year audit · LangGraph checkpoints/store")]:::store

    subgraph MODELS["Model providers — receive provider text only (FR-8)"]
        JEV["TypeSafe Jev<br/>calibrated Choice / Score answers"]:::model
        LLMS["LLM chain via LangChain<br/>Anthropic → OpenAI → DeepSeek (OD-7)"]:::model
    end

    subgraph MARKET["Brokerage & market — TBD (OD-3, OD-4)"]
        BROKER["Broker<br/>orders · positions · fills<br/>broker-held stops / OCO · paper acct<br/>source of truth for holdings"]:::ext
        MDFEED["Market data (broker feed)<br/>option chains · quotes<br/>underlyings · real-time SPX index"]:::ext
    end

    PUSHSVC["Browser push services<br/>(Web Push / VAPID)"]:::ext
    UPTIME["External uptime monitor<br/>pages phone on /health failure"]:::ext

    DISCORD -->|"messages, edits, deletes"| MAPI
    MAPI -->|"HTTPS POST /ingest (HMAC)<br/>200 only after commit, 503 if DB down"| INGEST
    INGEST -->|"GET history after last snowflake<br/>(startup, reconnect, every 60 s)"| MAPI
    INGEST ==>|"inbox.accept(): INSERT … ON CONFLICT DO NOTHING"| PG
    PG -.->|"NOTIFY new event"| SEQ
    SEQ -->|"one graph run per event,<br/>in post-time order per provider"| PIPE
    PIPE -->|"C71-scoped provider context"| INTERP
    INTERP -->|"one request, all questions"| JEV
    INTERP -->|"slow path, 3 s shared deadline"| LLMS
    PIPE -->|"chain / quote identity checks"| MDA
    PIPE ==>|"apply: ONE transaction<br/>signals + links + ledger + card"| PG
    PIPE -.->|"card created"| CARDS
    CARDS -.->|"SSE + push"| WEB
    WEB -.->|"card / fill / alert push"| PUSHSVC
    PUSHSVC -.-> TRADER
    TRADER -->|"approve / edit / reject<br/>manual ops · kill switch (password step-up)"| WEB
    WEB ==>|"approvals row BEFORE resume"| PG
    WEB -->|"Command(resume=approve)"| PIPE
    PIPE -->|"revalidate → submit_order()"| EXEC
    EXEC ==>|"order intent + client_order_id<br/>committed BEFORE broker call"| PG
    EXEC -->|"place / cancel / replace / status"| BRKA
    BRKA -->|"REST / API"| BROKER
    BROKER -.->|"order & fill events (OD-10)"| BRKA
    MDFEED -.->|"quote stream"| MDA
    MDA -.->|"underlying ticks → system-held stops"| EXEC
    RECON -->|"positions + working orders"| BRKA
    RECON ==>|"lots / orders / mismatch alerts"| PG
    UPTIME -->|"GET /health (heartbeat ≤ 15 s)"| WEB
    OPER -. "console only" .-> APP
```

### What the diagram represents

The whole system at container level: one Python asyncio service, one managed PostgreSQL cluster, and the external systems it depends on. The service is a **deterministic pipeline with narrow model roles** (PRD §4), not an autonomous agent.

### Component responsibilities

| Component                              | Responsibility                                                                                                                                                                                                      | Why it exists                                                                                 |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| **Message API** (external, user-owned) | Only source of Discord content (C11). Pushes events; must redeliver or offer backfill (C91).                                                                                                                        | The system never talks to Discord directly (PRD §2 non-goals).                                |
| **Ingest**                             | Verifies HMAC, durably stores each event exactly once, detects gaps and backfills, watches feed health (FR-1, FR-1d, FR-4).                                                                                         | A signal must never be lost (G5).                                                             |
| **Inbox + Dispatcher**                 | Postgres-backed queue; one worker per provider processes that provider's events one at a time in post-time order; providers run in parallel (FR-1a).                                                                | Follow-ups must be processed against a provider book that already contains the entry (AC-18). |
| **Signal pipeline**                    | LangGraph graph, one thread per message event. Builds provider-scoped context, interprets, validates, decides, and commits results in one transaction; pauses for human approval without holding the provider slot. | Orchestration + durable human-in-the-loop pause (TRD §5.1).                                   |
| **Interpretation**                     | Jev answers semantic questions over code-found candidates; an LLM handles hard cases and writes the advisory note. Never decides trade/size/price.                                                                  | Informal, typo-ridden messages need language understanding (PRD §1).                          |
| **Cards & approvals**                  | Card state machine, validity rules (C22), expiry timers, supersession, notifications.                                                                                                                               | Every order action needs approval (C6/C14).                                                   |
| **Execution**                          | The single `submit_order()` gate: FR-20 invariants, order intent, broker call, re-pricing, system-held stops, stop restore, end-of-day close.                                                                       | "Every order goes through one function" (TRD §1.5).                                           |
| **Reconciliation**                     | Compares system lots/orders with the broker; any mismatch blocks new entries.                                                                                                                                       | Broker is the source of truth for holdings (PRD §11).                                         |
| **Web tier**                           | Dashboard, SSE, Web Push, password auth, manual operations, labeling screen.                                                                                                                                        | Approvals within seconds on desktop and phone (G3).                                           |
| **PostgreSQL**                         | Only source of truth for system state, the message queue, audit trail and LangGraph checkpoints. No Kafka, no Redis (C94).                                                                                          | Single transactional boundary; RPO ≈ 0 for trading data (C93).                                |

### Consistency and reliability considerations

- **Two sources of truth, clearly partitioned:** Postgres is authoritative for everything the system decided (messages, signals, ledgers, cards, approvals, order intents). The **broker** is authoritative for what is actually held and working. Reconciliation is the bridge.
- **Durable-before-acknowledge:** the webhook returns `200` only after commit; the order intent is committed before the broker call; the approval row is written before the graph resumes.
- **Models never touch the broker, the ledgers, or user holdings.** They receive provider text and the provider's own book only (FR-8, C71).

### Potential failure points

Message API silence or non-redelivery (OD-1) · Postgres unavailability (trading halts by design) · all LLM vendors down (raw review card) · broker timeouts (query by client order ID) · market-data loss (system-held stops blind, OD-12) · the single app VM (RTO ≤ 5 min target; system-held stops do not fire while it is down). See [09](09-failure-recovery.md).

---

## 1.2 Layered view: transport, correlation, state

TRD §1.7 separates three concerns that are often conflated. This view makes it explicit that **no queue or cache resolves signal context**.

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef model fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c
    classDef store fill:#eceff1,stroke:#37474f,color:#263238
    classDef ext fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20

    subgraph L1["Layer 1 — TRANSPORT: get every event in, once, in provider order"]
        direction LR
        T1["Webhook + HMAC"]:::code --> T2[("inbox + message_events<br/>PK message_id, event_type, event_seq")]:::store
        T3["Gap detector / backfill"]:::code --> T2
        T2 -.->|"NOTIFY"| T4["Dispatcher<br/>per-provider worker, snowflake order"]:::code
    end

    subgraph L2["Layer 2 — CORRELATION: which provider position does this message belong to?"]
        direction LR
        C1["1. reply_to_id → parent's position<br/>(same provider only) — deterministic"]:::code
        C2["2. provider book + Jev links_to<br/>(confidence ≥ T_link)"]:::model
        C3["3. otherwise review card<br/>listing candidate positions"]:::code
        C1 -->|"no reply / parent unlinked"| C2 -->|"ambiguous / low confidence"| C3
    end

    subgraph L3["Layer 3 — AUTHORITATIVE STATE"]
        direction LR
        S1[("Postgres transactions<br/>provider_positions · lots · cards<br/>orders · fills · approvals")]:::store
        S2["Broker positions & orders"]:::ext
        S1 <-->|"reconciliation"| S2
    end

    L1 -->|"envelope: provider_id, message_id,<br/>posted_at, reply_to_id, payload"| L2
    L2 -->|"signal_id → provider_position_id<br/>(method, confidence)"| L3
```

**Why this matters:** `signal_id` and `provider_position_id` are _outputs_ of interpretation (TRD §8.1). Transport can only partition by `provider_id`, which is known on arrival. That is why a Kafka topic keyed by signal or a Redis context cache cannot replace Layer 2, and why TRD §15 rejects both at this scale.
