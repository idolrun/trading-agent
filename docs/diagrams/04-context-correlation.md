# 4. Message Context & Signal Correlation

[← Data flow](03-end-to-end-data-flow.md) · [Index](README.md) · Next: [Signal processing flowchart →](05-signal-processing-flowchart.md)

**Invariant for this whole section: provider context is never mixed.** A provider position is owned by exactly one provider; links never cross providers; the context sent to any model contains one provider's messages and one provider's book (PRD §6, C71, AC-18). Position scope is **per author** by default (OD-5).

## 4.1 Provider isolation lanes

```mermaid
flowchart LR
    classDef a fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef b fill:#fce4ec,stroke:#ad1457,color:#880e4f
    classDef shared fill:#eceff1,stroke:#37474f,color:#263238
    classDef model fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c

    subgraph IN["Arrival order (Message API)"]
        direction TB
        A1["A1 · provider A · 10:01:02<br/>'Bought GOOGL 357.5C @ 1.67'"]:::a
        B2["B2 · provider B · 10:01:03<br/>'Bought TSLA 400C @ 2.10'"]:::b
        A3["A3 · provider A · 10:01:05<br/>'Take half off @ 2.10'"]:::a
        B4["B4 · provider B · 10:01:06<br/>'Stop 1.50'"]:::b
    end

    INBOX[("inbox — one table<br/>indexed (provider_id, status, posted_at)")]:::shared

    subgraph LANEA["Lane A — provider_id = A (serial, post-time order)"]
        direction TB
        WA["worker A<br/>slot held: load_context → apply"]:::a
        CXA["context A<br/>A's msgs · A's book · A's profile"]:::a
        BOOKA[("A's provider book<br/>P-A1 GOOGL 357.5C")]:::a
        WA --> CXA --> BOOKA
    end

    subgraph LANEB["Lane B — provider_id = B (serial, post-time order)"]
        direction TB
        WB["worker B<br/>slot held: load_context → apply"]:::b
        CXB["context B<br/>B's msgs · B's book · B's profile"]:::b
        BOOKB[("B's provider book<br/>P-B2 TSLA 400C")]:::b
        WB --> CXB --> BOOKB
    end

    MODELS["Jev / LLM<br/>each request carries ONE provider's context"]:::model

    A1 --> INBOX
    B2 --> INBOX
    A3 --> INBOX
    B4 --> INBOX
    INBOX -->|"A1 then A3"| WA
    INBOX -->|"B2 then B4"| WB
    CXA -->|"A-only state"| MODELS
    CXB -->|"B-only state"| MODELS
    LANEA ~~~ LANEB
```

**Outcome (AC-18):** A3 links to P-A1 (GOOGL); B4 links to P-B2 (TSLA). A3 is never processed before A1 is in A's book, because lane A is serial. Lanes A and B run in parallel, so B never waits on A's slow path.

**Why serial per provider:** if A1 and A3 ran concurrently, A3 could read an empty book, be linked `none`, and go feed-only — a silently lost exit (TRD §2.4 race). **Why parallel across providers:** Kafka-style shared partitions would make one provider's 3 s slow-path call block other providers (head-of-line blocking, TRD §15.1).

---

## 4.2 Identifier model

```mermaid
flowchart TB
    classDef key fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef out fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c
    classDef user fill:#fff3e0,stroke:#e65100,color:#bf360c
    classDef meta fill:#fafafa,stroke:#9e9e9e,color:#616161

    PID["provider_id<br/>= Discord author ID<br/>KEY for sequencing, context scope, ledger scope"]:::key
    CH["channel_id<br/>metadata only — not a scope key (OD-5)"]:::meta
    MID["message_id<br/>Discord snowflake: unique + time-ordered<br/>→ posted_at"]:::key
    EV["message event<br/>(message_id, event_type, event_seq)<br/>create / edit / delete — IDEMPOTENCY KEY"]:::key
    RTO["reply_to_id → message_id<br/>(any author) deterministic link input (FR-3)"]:::key
    SID["signal_id = (message_id, idx)<br/>0..n signals per message (FR-6)<br/>OUTPUT of interpretation"]:::out
    LNK["message_links row<br/>signal_id → provider_position_id<br/>method: reply / jev / llm / user_correction<br/>confidence · append-only"]:::out
    PPID["provider_position_id<br/>created by an entry signal<br/>owned by EXACTLY ONE provider_id"]:::out
    LOT["lot_id — user holding at broker<br/>FK provider_position_id (1 lot per provider position)<br/>foreign lots have none"]:::user
    CARD["card_id<br/>UNIQUE (signal_id, action)"]:::user
    COID["client_order_id<br/>= uuid5(card_id, action_seq)"]:::user

    PID --> MID
    CH -.-> MID
    MID --> EV
    MID --> RTO
    MID --> SID
    SID --> LNK --> PPID
    PID -->|"owns"| PPID
    PPID --> LOT
    SID --> CARD
    CARD --> COID
    LOT -.->|"exit / stop cards reference lot"| CARD
```

- **Transport keys** (blue) are known on arrival and may be used to route: `provider_id`, `message_id`, `event_seq`, `reply_to_id`.
- **Interpretation outputs** (purple) — `signal_id`, `provider_position_id`, links — cannot be used to partition or route before interpretation (PRD §6; TRD §8.1).
- **Two providers on the same contract → two provider positions → two lots** (C24). The sell invariant (FR-20.2) is per provider lot.

---

## 4.3 Correlation decision ladder

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef model fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef ok fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20

    S["signal from message M<br/>(author = provider P)"]:::code
    Q0{"msg_type opens a position?<br/>(entry / stock_entry, not is_add)"}:::code
    NEW["create new provider position<br/>owned by P"]:::ok
    Q1{"M.reply_to_id present?"}:::code
    Q2{"parent authored by P<br/>and linked to one of P's positions?"}:::code
    Q2B["parent by another provider Q:<br/>parent = READ-ONLY context only<br/>never links into Q's book"]:::code
    L1["link method = reply<br/>(deterministic)"]:::ok
    Q3{"Jev links_to over P's provider book<br/>= a position id, confidence ≥ T_link?"}:::model
    L2["link method = jev<br/>(or llm on slow path)"]:::ok
    Q4{"links_to = ambiguous<br/>or confidence < T_link?"}:::code
    RV["REVIEW CARD<br/>lists P's candidate positions<br/>user picks → message_links row<br/>method = user_correction (C73)"]:::fail
    NONE["linked to nothing<br/>(e.g. P has no open position)<br/>feed; re-checked if a late<br/>earlier message arrives (FR-1b)"]:::code

    S --> Q0
    Q0 -->|"yes"| NEW
    Q0 -->|"no — follow-up"| Q1
    Q1 -->|"yes"| Q2
    Q1 -->|"no"| Q3
    Q2 -->|"yes"| L1
    Q2 -->|"parent by other provider"| Q2B --> Q3
    Q2 -->|"parent unlinked"| Q3
    Q3 -->|"yes"| L2
    Q3 -->|"no"| Q4
    Q4 -->|"yes"| RV
    Q4 -->|"new_position / none"| NONE
```

**Why this order:** the reply reference is exact and cheap, so it wins (FR-3). The model is only asked to choose among *this provider's* open positions; it cannot link across providers because the other providers' positions are never in its options. Low confidence downgrades to human review; it never upgrades (C31).

**Cross-provider replies:** "B replies 'same' to A1" → B gets its own provider position; A's ledger is unchanged (TRD §12 scenario).

---

## 4.4 Many messages → one provider position → one lot

```mermaid
flowchart LR
    classDef msg fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef pos fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c
    classDef lot fill:#fff3e0,stroke:#e65100,color:#bf360c
    classDef card fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20

    M1["A1 10:01 entry<br/>'Bought GOOGL 357.5C @ 1.67 sl 1.2'"]:::msg
    M2["A5 10:03 add<br/>'adding more 1.40' (reply to A1)"]:::msg
    M3["A7 10:04 correction<br/>'meant 360C not 357.5' "]:::msg
    M4["A9 10:05 stop_move<br/>'stop to BE'"]:::msg
    M5["A11 10:06 trim<br/>'take half off @ 2.10'"]:::msg
    M6["A13 10:08 full_exit<br/>'all out'"]:::msg

    PP(("P-A1<br/>provider position<br/>owner = A<br/>open → trimmed → closed")):::pos

    L["Lot L-7<br/>user holding, FK P-A1<br/>created by fill of card C1"]:::lot

    C1["C1 entry + bracket card"]:::card
    C2["C2 add card (joins L-7, C62)"]:::card
    C3["C3 correction: before trade → C1 invalidated,<br/>re-carded · after trade → ALERT only (C16)"]:::card
    C4["C4 stop-move card<br/>shows avg fill AND provider entry (C29)"]:::card
    C5["C5 exit card qty = floor(½ × lot)<br/>0 → blank + 'lot too small' (C82)"]:::card
    C6["C6 exit-all card"]:::card

    M1 -->|"new position"| PP
    M2 -->|"reply link"| PP
    M3 -->|"correction_target = A1"| PP
    M4 -->|"jev links_to"| PP
    M5 -->|"jev links_to"| PP
    M6 -->|"jev links_to"| PP
    PP --- L
    M1 -.-> C1
    M2 -.-> C2
    M3 -.-> C3
    M4 -.-> C4
    M5 -.-> C5
    M6 -.-> C6
    C1 -.->|"fill"| L
```

Each message is its own signal (or signals) with its own `message_links` row; the position accumulates history; the user's lot is touched only through approved cards and fills. A newer message on the same position **invalidates a still-pending entry card** (C22) — e.g. if C1 is still pending when A5 arrives, C1 expires and the user sees the newer state.

---

## 4.5 Ambiguity and out-of-order handling

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef human fill:#fff3e0,stroke:#e65100,color:#bf360c

    E["event E for provider P arrives"]:::code
    O{"E.posted_at older than an event<br/>already APPLIED for P?"}:::code
    NORM["process normally in post-time order"]:::code
    LATE["apply E · flag 'late'<br/>count metric (reorder buffer ≤ 300 ms<br/>added only if counts justify it)"]:::code
    RECHK["re-run linking for P's messages applied<br/>after E that were linked none / ambiguous"]:::code
    CHG{"any link result changed?"}:::code
    RC["REVIEW CARD per changed message<br/>never an automatic change (C89)"]:::fail
    DONE["no change → nothing to do"]:::code
    AMB{"linking ambiguous?"}:::code
    RVC["REVIEW CARD: candidate positions listed<br/>user chooses or dismisses"]:::human
    UC["message_links row method = user_correction<br/>+ manual_corrections audit + eval data (C73)"]:::code

    E --> O
    O -->|"no"| NORM --> AMB
    O -->|"yes"| LATE --> RECHK --> CHG
    CHG -->|"yes"| RC
    CHG -->|"no"| DONE
    AMB -->|"yes"| RVC --> UC
```

---

## 4.6 AC-18 as a sequence

```mermaid
sequenceDiagram
    autonumber
    participant API as Message API
    participant IN as inbox
    participant WA as worker A
    participant WB as worker B
    participant J as Jev
    participant DB as Postgres (ledgers)

    API->>IN: A1 GOOGL entry (A)
    API->>IN: B2 TSLA entry (B)
    API->>IN: A3 take half off (A)
    API->>IN: B4 stop 1.50 (B)
    par lane A
        IN-->>WA: A1 (oldest for A)
        WA->>J: state with A's msgs, A's book = empty
        J-->>WA: msg_type entry
        WA->>DB: apply: create P-A1 GOOGL 357.5C
        IN-->>WA: A3 (next for A)
        WA->>J: state with A1 in recent msgs, A's book = P-A1
        J-->>WA: msg_type trim, links_to P-A1
        WA->>DB: apply: link A3 to P-A1, trim, exit card if lot held
    and lane B
        IN-->>WB: B2 (oldest for B)
        WB->>J: state with B's msgs, B's book = empty
        J-->>WB: msg_type entry
        WB->>DB: apply: create P-B2 TSLA 400C
        IN-->>WB: B4
        WB->>J: state with B's book = P-B2
        J-->>WB: msg_type stop_set, links_to P-B2
        WB->>DB: apply: link B4 to P-B2, stop card if lot held
    end
    Note over WA,WB: Neither state ever contains the other provider's messages or positions
```

### Consistency and failure considerations
- **Ordering source:** snowflake post time, not arrival order (C88). Late arrivals are applied and flagged, never dropped.
- **Slot mechanics:** single process → `asyncio` per-provider worker; multi-process (future) → session-level `pg_try_advisory_lock(hash(provider_id))` on a dedicated connection (never a Redis lock). Inside `apply`, provider rows are additionally locked `FOR UPDATE`.
- **Failure points:** a provider-ledger error propagates to later follow-ups (PRD §21) — mitigated by user correction + review cards; a poison message in lane P does **not** stop lane P (FR-1e).
- **OD-5:** if position scope becomes (author, channel), the lane key stays `provider_id` but the book and `links_to` options are filtered by channel.
