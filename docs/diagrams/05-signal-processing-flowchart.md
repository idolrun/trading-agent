# 5. Signal Processing Flowchart

[← Context & correlation](04-context-correlation.md) · [Index](README.md) · Next: [Sequence diagrams →](06-sequence-diagrams.md)

Three charts: intake and routing, what each message type does, and how the card tier is chosen. Purple is a model. Blue is code. Orange would be a person (none of these three charts wait on one). Red is a blocked path.

Models stop when they return `ParsedSignals` (or a feed-only type). `decide_apply` checks literal spans, then `Planner` validates and `decide()` builds cards. The note writer runs after `notify` and cannot change fields.

## 5.1 Intake, sequencing, and interpretation

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef model fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef feed fill:#fafafa,stroke:#757575,color:#424242

    START(["event dequeued for a lane"]):::code
    MUT{"muted author or<br/>channel not in config?"}:::code
    FEEDM["inbox done<br/>skip_reason set, still on the feed"]:::feed
    FEED["feed only, inbox done<br/>no card"]:::feed
    EVT{"event_type"}:::code
    DEL["delete: invalidate pending cards<br/>from this message, alert, inbox done"]:::fail
    ATT{"attempt >= poison_retry_limit<br/>default 3?"}:::code
    POISON["inbox failed<br/>raw review + poison alert<br/>the lane continues"]:::fail
    STALE{"age > stale_entry_s?"}:::code
    FLAG["stale and late flags<br/>carried into the plan"]:::code
    CTX["load_context"]:::code
    RES["resolve_context"]:::code
    CAND["find_candidates"]:::code
    FAST{"fast_parse<br/>matches the whole message?"}:::code
    FASTOK["route = fast"]:::code
    JEV["jev_interpret"]:::model
    KIND{"why not settled?"}:::code
    LAYA["laya_interpret"]:::model
    LLM["llm_extract"]:::model
    RAW["raw review card"]:::fail
    SPAN{"every literal span<br/>is in the text?"}:::code
    HARD["raw_reason literal_span_mismatch"]:::fail
    NEXT(["decide_apply → 5.2"]):::code
    GUARD{"feed-only type but the lane<br/>has a position the text could hit?"}:::code

    START --> MUT
    MUT -->|"yes"| FEEDM
    MUT -->|"no"| EVT
    EVT -->|"delete"| DEL
    EVT -->|"create or edit"| ATT
    ATT -->|"yes"| POISON
    ATT -->|"no"| STALE
    STALE --> FLAG --> CTX --> RES --> CAND --> FAST
    FAST -->|"yes"| FASTOK --> SPAN
    FAST -->|"no"| JEV --> KIND
    KIND -->|"feed and conf ≥ T_chat"| GUARD
    GUARD -->|"yes"| LLM
    GUARD -->|"no"| FEED
    KIND -->|"assemble"| SPAN
    KIND -->|"model_failure"| LAYA
    KIND -->|"semantic"| LLM
    KIND -->|"link"| RAW
    LAYA -->|"settled"| SPAN
    LAYA -->|"semantic"| LLM
    LAYA -->|"link"| RAW
    LLM -->|"ok"| SPAN
    LLM -->|"deadline, chain exhausted, scope"| RAW
    SPAN -->|"yes"| NEXT
    SPAN -->|"no"| HARD --> RAW
```

Edits continue as messages. `CorrectionService` treats an edit, or a `correction` signal, as a correction of the original position. Deletes do not enter interpretation when that service is wired (it is, in the lifespan).

`T_chat` defaults to 0.9. `T_type`, `T_field`, and `T_link` default to 0.8. Laya has its own higher gates (`t_chat` 0.95, the others 0.9) inside `LayaConfig`.

An exception during the run increments `attempts` and leaves the row for another try until the poison limit. Database connection errors retry and do not count as poison.

## 5.2 What each message type does

`Planner.plan` validates, links, and calls `decide()`. `apply_event` writes the ledger update and the cards in one transaction. `decide()` returns no cards for the feed-only types; the ledger update can still be written.

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef feed fill:#fafafa,stroke:#757575,color:#424242
    classDef card fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20

    IN(["ParsedSignals"]):::code
    VAL["validate<br/>symbol map, optionable list, chain, quote,<br/>scale, adjacent strike, XNYS dates"]:::code
    BAD{"identity ok?"}:::code
    MISS{"underlying missing?"}:::code
    SUG["review card, suggested underlying<br/>not approvable until confirmed"]:::fail
    ID["review card, numbers left as stated"]:::fail
    MD["quote missing → review<br/>market_data_unavailable"]:::fail
    TYPE{"msg_type"}:::code

    SETUP["watch / conditional"]:::code
    SETU["provider position status watch or conditional<br/>condition_text stored · no card · no lot"]:::feed
    QUIET["chatter, commentary_pnl,<br/>out_of_scope_strategy, tp_hit_notice"]:::feed

    ENT["entry / stock_entry"]:::code
    ON{"linked to a live position<br/>that is not a setup?"}:::code
    ADD["action add"]:::code
    NEW["action entry, new position<br/>or open the setup"]:::code
    STL{"stale?"}:::code
    STF["inbox stale · no approvable entry card"]:::feed
    HOLD{"regular hours?"}:::code
    HLD["ledger written · offhours_release timer<br/>cards planned again at the open"]:::code
    ECARD["entry or add card<br/>qty blank · limit = signal × chase, on the tick<br/>stop and TP levels stored, legs after the user qty"]:::card

    EXIT["trim / full_exit / soft_exit / stock_exit<br/>stop_* / tp_set"]:::code
    LINK{"resolver unique<br/>and contract agrees?"}:::code
    REV["review card, candidate positions"]:::fail
    LOT{"user holds a lot<br/>for that position?"}:::code
    FEEDX["provider book updated · no card"]:::feed
    XCARD["trim or exit card, qty blank<br/>soft exit labelled suggestion<br/>full_exit and trim-all are close cards<br/>with the lot qty"]:::card
    SCARD["stop or tp card<br/>breakeven shows user avg and provider entry"]:::card

    CORR["edit or correction signal"]:::code
    CUPD["pending cards on the old reading invalidated<br/>book corrected · new link or manual_corrections row<br/>if the user already traded: alert, book still corrected"]:::card

    IN --> VAL --> MISS
    MISS -->|"yes"| SUG
    MISS -->|"no"| BAD
    BAD -->|"quote down"| MD
    BAD -->|"mismatch"| ID
    BAD -->|"ok"| TYPE
    TYPE --> SETUP --> SETU
    TYPE --> QUIET
    TYPE --> ENT --> ON
    ON -->|"yes"| ADD --> STL
    ON -->|"no"| NEW --> STL
    STL -->|"yes"| STF
    STL -->|"no"| HOLD
    HOLD -->|"no"| HLD
    HOLD -->|"yes"| ECARD
    TYPE --> EXIT --> LINK
    LINK -->|"no"| REV
    LINK -->|"yes"| LOT
    LOT -->|"no"| FEEDX
    LOT -->|"trim or exit"| XCARD
    LOT -->|"stop or tp"| SCARD
    TYPE --> CORR --> CUPD
```

Risk limits, the kill switch, and `entries_blocked` apply to entry and add cards. A breach becomes a disabled card with a reason. Exits are not blocked by those gates.

After the transaction commits, a pending entry or add on that same position is `invalidated` / `superseded`, and `notify` ends its approval thread.

A disabled card is inserted in state `disabled` (the type and the state match). It is not pushed as something to approve.

## 5.3 Card tier

```mermaid
flowchart LR
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef card fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    classDef feed fill:#fafafa,stroke:#757575,color:#424242

    A{"actionable type<br/>with a lot when required?"}:::code
    NA["no card"]:::feed
    B{"review reasons or<br/>missing fields?"}:::code
    RV["REVIEW<br/>user completes fields first"]:::fail
    C{"risk, kill switch,<br/>entries blocked,<br/>or config gap?"}:::code
    DS["DISABLED<br/>reason stored"]:::fail
    D{"any field source<br/>is context?"}:::code
    INF["INFERRED"]:::card
    NM["NORMAL<br/>literal or resolved fields"]:::card

    A -->|"no"| NA
    A -->|"yes"| B
    B -->|"yes"| RV
    B -->|"no"| C
    C -->|"yes"| DS
    C -->|"no"| D
    D -->|"yes"| INF
    D -->|"no"| NM
```

Low field confidence (under `T_field`) is a review reason. It does not pick a different number. Stale entries produce no card rather than a disabled one.

The card stores the proposal: action, limit, signal price, stop, TP levels, warnings, candidate positions, and `valid_until` measured from `posted_at`. The advisory note is written afterward onto `cards.note`. If the note guard finds a ticker, strike, expiry, or price that is not in the validated fields, `note_hidden` is set and a `note_hidden` alert is raised.
