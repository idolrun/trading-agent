# 11. As-built architecture

[← Index](README.md) · Next: [Decision →](12-decision.md)

The same pipeline as [01](01-high-level-architecture.md)–[10](10-architecture-summary.md), on one chart: lane isolation, the deterministic fast path, Jev with a Laya fallback, and a decision engine that never chooses a quantity.

## 11.1 One-page view

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef model fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c
    classDef human fill:#fff3e0,stroke:#e65100,color:#bf360c
    classDef ext fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    classDef store fill:#eceff1,stroke:#37474f,color:#263238
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c

    DISCORD["Discord channels"]:::ext --> IN["Discord listener<br/>or POST /ingest"]:::code
    IN ==>|"inbox.accept(), commit then ack"| PG[("PostgreSQL<br/>system state")]:::store
    PG -.->|"NOTIFY"| DISP["dispatcher<br/>one worker per lane<br/>snowflake order"]:::code
    DISP --> LOAD["load_context<br/>resolve_context<br/>find_candidates"]:::code
    LOAD --> FAST{"fast_interpret<br/>whole message, code only"}:::code
    FAST -->|"settled"| APPLY["decide_apply<br/>validate, link, decide<br/>one transaction"]:::code
    FAST -->|"miss"| JEV["jev_interpret<br/>one system_one call"]:::model
    JEV -->|"feed only or assemble"| APPLY
    JEV -->|"model failure"| LAYA["laya_interpret<br/>only after Jev fails"]:::model
    JEV -->|"semantic doubt"| LLM["llm_extract<br/>optional"]:::model
    JEV -->|"link doubt or scope"| RAW["raw_review"]:::fail
    LAYA -->|"settled"| APPLY
    LAYA -->|"still open"| LLM
    LAYA -->|"link doubt or scope"| RAW
    LLM -->|"settled, or Jev draft"| APPLY
    LLM -->|"off, exhausted, rejected"| RAW
    APPLY ==> PG
    RAW ==> PG
    APPLY --> NOTE["notify<br/>one approval graph per card"]:::code
    RAW --> NOTE
    NOTE --> HUMAN["trader<br/>Next.js card inbox"]:::human
    HUMAN -->|"approve"| REVAL["revalidate"]:::code
    HUMAN -->|"reject, expire, invalidate"| DONE["card closed"]:::code
    REVAL -->|"still valid"| EXEC["submit_order()"]:::code
    REVAL -->|"no longer valid"| DONE
    EXEC ==>|"order intent first"| PG
    EXEC --> BRK["broker"]:::ext
    BRK -.->|"fills"| LOTS["lots"]:::code
    LOTS ==> PG
    LOTS --> RECON["reconcile<br/>mismatch blocks entries"]:::code
    RECON ==> PG
```

Models interpret. `decide()` decides prices, stops, and the card type. A person approves. The only automatic orders are the ones that approval already authorised: a broker or system stop, a take-profit, exit re-pricing inside an approved intent, the end-of-day close, and stop restore.

PostgreSQL is the source of truth for messages, signals, the provider book, cards, approvals, and order intents. In paper and live, Alpaca is the source of truth for holdings and working orders. Shadow fills come from `SimulatedBroker` and reconciliation is skipped. LangGraph checkpoints record where an approval thread paused. They do not hold business state.

A lane is `(channel_id, provider_id)`. The worker holds that lane from `load_context` through `notify`, then releases it. The approval wait is a second graph, so a pending card does not block the lane's next message. Different lanes run in parallel.

`decide_apply` is one node on purpose: validation, linking, and `decide()` finish before the transaction, and the decided cards are written in that same transaction. A literal span that does not match the message text becomes a raw review card instead.
