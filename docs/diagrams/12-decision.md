# 12. Decision

[← As-built architecture](11-as-built-architecture.md) · [Index](README.md) · Next: [Layers →](13-layers.md)

`decide()` in [backend/app/decide/engine.py](../../backend/app/decide/engine.py) is a pure function. The same inputs always produce the same cards. It does not read the database, call a model, or choose a quantity. The planner in [backend/app/pipeline/plan.py](../../backend/app/pipeline/plan.py) gathers the inputs; `apply` writes the result.

## 12.1 From a parsed signal to a card

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef human fill:#fff3e0,stroke:#e65100,color:#bf360c
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef store fill:#eceff1,stroke:#37474f,color:#263238

    IN["DecisionInputs<br/>signal, validation, link, position, lot<br/>config, risk snapshot, flags, posted_at"]:::code
    PROP["_propose"]:::code
    NONE["no card<br/>ledger may still update"]:::store
    LINKREV["review card<br/>user picks the position"]:::human
    BODY["_body<br/>limit, stop, take-profits<br/>qty stays blank"]:::code
    CLOSE["close card<br/>qty = the whole lot"]:::code
    TYPE{"why is this not a plain card?"}:::code
    REVIEW["review<br/>missing or uncertain fields"]:::human
    INFERRED["inferred<br/>a field came from context"]:::code
    NORMAL["normal"]:::code
    STALE{"stale entry or add?"}:::code
    DROP["drop the card<br/>feed shows it as stale"]:::fail
    LATE["other stale or late card<br/>label: late"]:::code
    GATE{"entry or add gate"}:::code
    DISABLED["disabled<br/>reason on the card, no push"]:::fail
    WARN["warning if an opposite option<br/>is open on the same underlying"]:::code

    IN --> PROP
    PROP -->|"feed-only, not actionable, or unknown type"| NONE
    PROP -->|"follow-up, not linked"| LINKREV
    PROP -->|"follow-up, user holds no lot"| NONE
    PROP -->|"entry, or linked follow-up"| BODY
    BODY -->|"full exit, or trim of all"| CLOSE
    BODY -->|"entry, add, trim, exit, stop, take-profit"| TYPE
    CLOSE --> TYPE
    TYPE -->|"reasons or missing fields"| REVIEW
    TYPE -->|"context-sourced field"| INFERRED
    TYPE -->|"otherwise"| NORMAL
    REVIEW --> STALE
    INFERRED --> STALE
    NORMAL --> STALE
    LINKREV --> STALE
    STALE -->|"yes"| DROP
    STALE -->|"stale or late, not an entry"| LATE
    STALE -->|"fresh"| GATE
    LATE --> GATE
    GATE -->|"config, kill switch, reconciliation, unknown risk, daily loss, max positions, or account unreadable"| DISABLED
    GATE -->|"clear"| WARN
```

An opening message (`entry`, `stock_entry`) is an entry card. It is an add only when it is already linked to a live position that is not a watch or conditional setup. Follow-ups map to add, trim, close, exit, stop, or take-profit. A trim whose fraction is `all` is a close.

Quantity is blank on every buy and sell card. The user types it. A close card is the exception: it sells the lot the user already holds. `decide()` refuses to return if it set a quantity on anything else.

Entry gates run again at approval, after the user has entered a quantity. That second pass is where max exposure and buying power can disable the card. At creation time those two need a quantity, so they do not fire yet. The kill switch and the reconciliation block disable entries and adds only. Exit and stop cards are still created.

## 12.2 Approval

One approval graph per card, thread `card:<card_id>`. The approval row is written before the graph resumes.

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef human fill:#fff3e0,stroke:#e65100,color:#bf360c
    classDef ext fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c

    START(["card pending"]):::code --> WAIT["await_approval<br/>interrupt"]:::human
    WAIT -->|"approve"| REVAL["revalidate<br/>same gates, now with qty"]:::code
    WAIT -->|"reject, expire, invalidate"| STOP["card already closed"]:::fail
    REVAL -->|"still valid"| EXEC["execute<br/>submit_order()"]:::code
    REVAL -->|"band, time, or gate failed"| STOP
    EXEC --> BRK["broker"]:::ext
```
