# 5. Signal Processing Flowchart

[← Context & correlation](04-context-correlation.md) · [Index](README.md) · Next: [Sequence diagrams →](06-sequence-diagrams.md)

Split into three focused charts: **(5.1)** intake → interpretation hand-off, **(5.2)** deterministic handling per message type, **(5.3)** card tier assignment. Purple = model; blue = code; orange = human; red = error / blocked.

The **model / code boundary** is always the same: models stop after `assemble_signal` (plus the async advisory note). Everything from `validate` onward is deterministic code.

## 5.1 Intake, sequencing and interpretation routing

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef model fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef feed fill:#fafafa,stroke:#757575,color:#424242

    START(["event dequeued for provider P<br/>(post-time order)"]):::code
    MUT{"author muted? (C57)"}:::code
    FEEDM["feed only, label 'muted'"]:::feed
    EVT{"event_type"}:::code
    DEL["delete: find pending cards from this message<br/>→ invalidate + alert (C48)"]:::fail
    EDIT["edit: treated as a correction of the original<br/>(C48) — continues as msg with is_correction"]:::code
    ATT{"attempts ≥ 3 ?"}:::code
    POISON["status failed · RAW REVIEW CARD + alert<br/>P's queue continues (FR-1e)"]:::fail
    STALE{"age > stale_entry_s ?"}:::code
    FLAG["mark message 'stale-eligible' / 'late'<br/>(effect decided per type in 5.2)"]:::code
    HOURS{"within regular hours?"}:::code
    HOLD["action held: timer until 09:30 ET<br/>card built then with fresh validation (C59)"]:::code

    CTX["load_context (C71 scope)"]:::code
    CAND["find_candidates (regex over-find)"]:::code
    JEVOK{"Jev reachable?<br/>(1 retry on 429/529)"}:::code
    JEV["jev_interpret — all questions, one request"]:::model
    R1{"chatter / commentary_pnl / watch<br/>conf ≥ T_chat ?"}:::code
    FEED["NON-ACTIONABLE: feed with label<br/>(FR-10: uncertain defaults here)"]:::feed
    R2{"links_to ambiguous<br/>or conf < T_link ?"}:::code
    REVIEW["REVIEW CARD — candidate positions"]:::fail
    R3{"required field none / not_stated,<br/>conf < T_field, two numbers claim<br/>one role, or signal_count = multiple?"}:::code
    SLOW["llm_extract — provider chain,<br/>3 s shared deadline, ≤ 2 read-only tools"]:::model
    SOK{"result?"}:::code
    DEG["RAW REVIEW CARD + 'parser degraded' alert (FR-9)"]:::fail
    SPAN["literal-span verification (code)"]:::code
    SPOK{"every literal span in text?"}:::code
    HARD["HARD REJECT → review card<br/>no fallback to other vendor"]:::fail
    ASM["assemble_signal<br/>literal / context / resolved per field"]:::code
    NEXT(["→ 5.2 deterministic handling"]):::code
    ERR["unhandled exception<br/>inbox failed attempt, retry later"]:::fail

    START --> MUT
    MUT -->|"yes"| FEEDM
    MUT -->|"no"| EVT
    EVT -->|"delete"| DEL
    EVT -->|"edit"| EDIT --> ATT
    EVT -->|"create"| ATT
    ATT -->|"yes"| POISON
    ATT -->|"no"| STALE
    STALE -->|"yes"| FLAG --> HOURS
    STALE -->|"no"| HOURS
    HOURS -->|"no"| HOLD
    HOURS -->|"yes"| CTX
    HOLD -.->|"at open"| CTX
    CTX --> CAND --> JEVOK
    JEVOK -->|"yes"| JEV --> R1
    JEVOK -->|"no — all to slow path"| SLOW
    R1 -->|"yes"| FEED
    R1 -->|"no"| R2
    R2 -->|"yes"| REVIEW
    R2 -->|"no"| R3
    R3 -->|"yes"| SLOW
    R3 -->|"no"| ASM
    SLOW --> SOK
    SOK -->|"deadline / chain exhausted"| DEG
    SOK -->|"ParsedSignals"| SPAN --> SPOK
    SPOK -->|"no"| HARD
    SPOK -->|"yes"| ASM
    ASM --> NEXT
    CTX -.->|"exception"| ERR
    ERR -.->|"attempts + 1"| ATT
```

**Notes**
- **Off-hours hold (C59):** PRD says actions *are held until 09:30 ET* and then built with fresh validation. Whether interpretation runs immediately and only the card is deferred, or the whole event is deferred, is not stated; the chart shows the conservative reading (card built at open with fresh validation) — both satisfy C59.
- **Edit events:** without edit delivery from the Message API, this branch never fires and edit-corrections are lost (OD-2, PRD §21).
- **Where the LLM stops:** after `ParsedSignals` passes schema + literal-span checks. It never sees the user's lots and never chooses an action, quantity or price.

---

## 5.2 Deterministic handling per message type

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef feed fill:#fafafa,stroke:#757575,color:#424242
    classDef card fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    classDef od fill:#fafafa,stroke:#9e9e9e,color:#757575,stroke-dasharray: 5 5

    IN(["signal assembled"]):::code
    VAL["validate: symbol map (SPX→SPXW) · optionable list<br/>chain exists · quote within wide tolerance<br/>scale normalise ('140/con' vs '1.40') · adjacent-strike warning<br/>relative dates via XNYS calendar"]:::code
    VOK{"identity valid?"}:::code
    MU{"underlying missing?<br/>(e.g. '7775c')"}:::code
    SUGG["REVIEW CARD — suggest underlying by live-price<br/>proximity ±P% (C84) · never approvable unconfirmed"]:::fail
    VRV["REVIEW CARD — identity mismatch (FR-11)<br/>numbers never auto-corrected (C78)"]:::fail
    TYPE{"msg_type"}:::code

    ENT["entry / stock_entry"]:::code
    ADDQ{"is_add and P has open position?"}:::code
    STKP{"stock entry without price?"}:::code
    STKF["feed only (C60)"]:::feed
    LEDG1["ledger: create / grow provider position"]:::code
    STL{"stale (age > stale_entry_s)?"}:::code
    STF["ledger updated · NO approvable card<br/>feed label 'stale' (FR-1c)"]:::feed
    SIZE["size: floor(tier_max / (price × 100))<br/>stocks: X% equity capped by stock tier (C76)"]:::code
    Q0{"qty = 0?"}:::code
    DIS1["DISABLED CARD 'tier cannot buy one contract'<br/>never rounded up"]:::fail
    RISK{"risk limits OK?<br/>daily loss · open positions · exposure<br/>(foreign positions count) · kill switch off<br/>· entry gate open (reconciled)"}:::code
    DIS2["DISABLED CARD with reason (C75)<br/>no push"]:::fail
    BRK["bracket legs if SL/TP:<br/>TP legs = min(levels, qty) from TP1 (C83)"]:::code
    WARN["warnings: opposite-direction lot (C74)<br/>adjacent strike (FR-13) · prose condition (C30)"]:::code
    ECARD["ENTRY / ADD CARD"]:::card
    ADDNL["add but user holds no lot → OD-18"]:::od

    EXIT["trim / full_exit / soft_exit / stock_exit"]:::code
    LEDG2["ledger: update provider position"]:::code
    HOLDQ{"user holds lot for this<br/>provider position?"}:::code
    FEEDX["feed only (ledger updated)"]:::feed
    FRQ{"fraction stated?"}:::code
    FRC["qty = floor(fraction × lot)"]:::code
    FR0{"qty = 0?"}:::code
    SMALL["qty blank · label 'lot too small to trim:<br/>exit all or skip' (C82)"]:::code
    BLANK["qty blank, user enters (C26)<br/>soft exit: 'suggestion, provider still holding' (C52)"]:::code
    XCARD["EXIT CARD (+ 'late' badge if old)<br/>expires only on timeout or position closed"]:::card

    STOP["stop_set / stop_move / stop_cancel / tp_set"]:::code
    LEDG3["ledger: provider stop / TP info"]:::code
    HOLD2{"user holds lot?"}:::code
    BASIS{"stop_basis"}:::code
    PREM["premium → broker-held stop (C28)"]:::code
    UND["underlying → system-held stop (C49)<br/>candle-close shown as info only"]:::code
    STK["stock → stop-market GTC (C61)"]:::code
    BE["breakeven: show user avg fill AND<br/>provider entry, user picks (C29)"]:::code
    SCARD["STOP / TP CARD (+ 'late' badge if old)"]:::card

    CORR["correction (incl. edit)"]:::code
    TRADED{"has the user traded the<br/>corrected signal?"}:::code
    UPD["pending card invalidated → re-carded<br/>with corrected fields, fresh approval (C48)"]:::card
    ALERT["ALERT only: both messages + live P&L<br/>follow-ups now refer to corrected contract (C16)"]:::fail
    RELINK["new message_links / provider-position<br/>correction rows (append-only)"]:::code

    OTH["tp_hit_notice · conditional"]:::od
    OTHF["ledger annotation / feed — card behaviour<br/>not specified (OD-18)"]:::od
    OOS["out_of_scope_strategy (spreads, short, etc.)"]:::code
    OOSF["feed only, label 'out of scope'"]:::feed

    IN --> VAL --> MU
    MU -->|"yes"| SUGG
    MU -->|"no"| VOK
    VOK -->|"no"| VRV
    VOK -->|"yes"| TYPE

    TYPE --> ENT --> STKP
    STKP -->|"yes"| STKF
    STKP -->|"no"| ADDQ
    ADDQ -->|"add"| LEDG1
    ADDQ -->|"new"| LEDG1
    LEDG1 --> STL
    STL -->|"yes"| STF
    STL -->|"no"| SIZE --> Q0
    Q0 -->|"yes"| DIS1
    Q0 -->|"no"| RISK
    RISK -->|"breach"| DIS2
    RISK -->|"ok"| BRK --> WARN --> ECARD
    ADDQ -.-> ADDNL

    TYPE --> EXIT --> LEDG2 --> HOLDQ
    HOLDQ -->|"no"| FEEDX
    HOLDQ -->|"yes"| FRQ
    FRQ -->|"yes"| FRC --> FR0
    FR0 -->|"yes"| SMALL --> XCARD
    FR0 -->|"no"| XCARD
    FRQ -->|"no / soft"| BLANK --> XCARD

    TYPE --> STOP --> LEDG3 --> HOLD2
    HOLD2 -->|"no"| FEEDX
    HOLD2 -->|"yes"| BASIS
    BASIS --> PREM --> SCARD
    BASIS --> UND --> SCARD
    BASIS --> STK --> SCARD
    BASIS --> BE --> SCARD

    TYPE --> CORR --> TRADED
    TRADED -->|"no"| UPD
    TRADED -->|"yes"| ALERT
    CORR --> RELINK

    TYPE --> OTH --> OTHF
    TYPE --> OOS --> OOSF
```

**Notes**
- **Risk-limit check** applies to *approvable entry cards* (FR-18). Exits are never blocked by risk limits — missing an exit is worse than acting late (TRD §2.4).
- **Supersession:** after this message's `apply` commits, any still-pending **entry** card on the same provider position is invalidated (C22). Firing mechanism: OD-14.
- **Exit card side effects** are deferred to execution: on approval, working stops/TPs are reduced/cancelled first and restored if the exit ends unfilled (FR-20.3).
- **Dashed grey branches** are not specified by PRD/TRD (OD-18) and must be decided before phase 3.

---

## 5.3 Card tier assignment (code; confidence can only downgrade)

```mermaid
flowchart LR
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef card fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    classDef feed fill:#fafafa,stroke:#757575,color:#424242

    A{"actionable type?"}:::code
    NA["no card — feed with label"]:::feed
    B{"required field missing<br/>or link ambiguous<br/>or identity failed<br/>or underlying missing?"}:::code
    RV["REVIEW card<br/>user completes fields first"]:::fail
    C{"risk limit breached<br/>or tier can't buy 1 contract<br/>or stale entry?"}:::code
    DS["DISABLED card (reason shown, no push)<br/>stale entry: feed only, no card"]:::fail
    D{"any field with provenance<br/>= context (inferred from<br/>an earlier message)?"}:::code
    IF["INFERRED card<br/>inferred fields highlighted with source msg<br/>one-click approve (C32)"]:::card
    NM["NORMAL card<br/>all fields literal or resolved, validated"]:::card

    A -->|"no"| NA
    A -->|"yes"| B
    B -->|"yes"| RV
    B -->|"no"| C
    C -->|"yes"| DS
    C -->|"no"| D
    D -->|"yes"| IF
    D -->|"no"| NM
```

Every card shows the **author prominently** (C57), the source messages, inferred-field highlights, computed quantity, bracket legs and warnings, plus the async **advisory note** labelled "advisory" (C80) — hidden by the note guard if it mentions any ticker/strike/expiry/price not present in validated fields or sources (OD-6).
