# 4. Message Context and Signal Correlation

[← Data flow](03-end-to-end-data-flow.md) · [Index](README.md) · Next: [Signal processing flowchart →](05-signal-processing-flowchart.md)

A provider position belongs to one lane: `(provider_id, opened_channel_id)`. Links never cross lanes. A model sees that lane's messages and that lane's book, narrowed to the resolver's candidates. It does not see user lots, balances, or P&L.

## 4.1 Lane isolation

```mermaid
flowchart LR
    classDef a fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef b fill:#fce4ec,stroke:#ad1457,color:#880e4f
    classDef c fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    classDef shared fill:#eceff1,stroke:#37474f,color:#263238
    classDef model fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c

    subgraph IN["Arrival"]
        direction TB
        A1["A1 · channel 1 · author A"]:::a
        B2["B2 · channel 1 · author B"]:::b
        C3["C3 · channel 2 · author A"]:::c
        A4["A4 · channel 1 · author A"]:::a
    end

    INBOX[("inbox<br/>index provider_id, channel_id, posted_at")]:::shared

    subgraph L1["Lane channel1:A — serial"]
        WA["worker"]:::a
        BOOKA[("book: positions opened in channel 1 by A")]:::a
        WA --> BOOKA
    end

    subgraph L2["Lane channel1:B — serial, parallel with A"]
        WB["worker"]:::b
        BOOKB[("book: positions opened in channel 1 by B")]:::b
        WB --> BOOKB
    end

    subgraph L3["Lane channel2:A — serial, separate from channel 1"]
        WC["worker"]:::c
        BOOKC[("book: positions opened in channel 2 by A")]:::c
        WC --> BOOKC
    end

    MODELS["Jev, Laya, or LLM<br/>one lane's context per request"]:::model

    A1 --> INBOX
    B2 --> INBOX
    C3 --> INBOX
    A4 --> INBOX
    INBOX -->|"A1 then A4"| WA
    INBOX --> WB
    INBOX --> WC
    BOOKA --> MODELS
    BOOKB --> MODELS
    BOOKC --> MODELS
```

The same author in two channels is two lanes and two books. Two authors in one channel are two lanes. Workers run in parallel across lanes, so one lane's model call does not block the others.

Inside a lane the worker is serial from `load_context` through `notify`. A follow-up cannot read the book from before its own entry has been applied.

A row in `provider_positions` with no `opened_channel_id` is in no lane's book.

## 4.2 Identifier model

```mermaid
flowchart TB
    classDef key fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef out fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c
    classDef user fill:#fff3e0,stroke:#e65100,color:#bf360c

    LANE["lane = channel_id + provider_id<br/>sequencing, context, book"]:::key
    MID["message_id snowflake<br/>posted_at derived from it"]:::key
    EV["event key<br/>message_id, event_type, event_seq<br/>create / edit / delete"]:::key
    RTO["reply_to_id"]:::key
    TH["thread_id"]:::key
    SID["signal_id UUID<br/>unique with message_id, event_type, event_seq, idx"]:::out
    LNK["message_links<br/>method: reply, thread, contract, code,<br/>jev, llm, laya, user_correction<br/>latest row is current"]:::out
    PPID["provider_position_id<br/>owned by one provider_id<br/>opened_channel_id = the lane"]:::out
    LOT["lot_id<br/>one open lot per provider position<br/>foreign lots have no position"]:::user
    CARD["card_id = uuid5 of dedupe_key<br/>unique signal_id + action"]:::user
    COID["client_order_id<br/>cta- + uuid5"]:::user

    LANE --> MID --> EV
    MID --> RTO
    MID --> TH
    EV --> SID --> LNK --> PPID
    LANE -->|"owns"| PPID
    PPID --> LOT
    SID --> CARD --> COID
```

Transport keys, known on arrival: `channel_id`, `provider_id`, `message_id`, `event_type`, `event_seq`, `reply_to_id`, `thread_id`.

Interpretation outputs: `signal_id`, `provider_position_id`, the link row. Two lanes on the same contract are two positions and, if both are traded, two lots. The sell check is per lot.

## 4.3 How a message is linked

Code runs first, in `ledger.resolver`. A model hint never overrides a unique result.

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef model fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef ok fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20

    S["message in lane L"]:::code
    Q0{"opening type<br/>entry or stock_entry?"}:::code
    BOOK{"same contract already<br/>open in this lane?"}:::code
    NEW["new_position or the matching setup<br/>method = code"]:::ok
    SAME["already open on that contract<br/>method = code, rule c62<br/>the card is an add"]:::ok
    R1{"reply chain, depth ≤ 3<br/>one live position on an ancestor<br/>of this provider?"}:::code
    R2{"this provider's earlier messages<br/>in the thread touch exactly one<br/>live position?"}:::code
    R3{"ticker, strike, or call/put<br/>in the text leaves exactly one?"}:::code
    R4{"exactly one live position<br/>left in the lane?"}:::code
    LINK["follow-up link<br/>method = reply, thread, contract,<br/>or code when it is the only one left<br/>cross-checked against the text"]:::ok
    AMB["AMBIGUOUS<br/>candidates kept"]:::fail
    NONE["NONE<br/>empty book"]:::fail
    HINT["model LinkHint stored as<br/>evidence.proposal only<br/>refused if not a candidate,<br/>below T_link, or a contract conflict"]:::model
    REV["review card<br/>user pick → method user_correction"]:::fail
    X{"stated contract contradicts<br/>the code pick?"}:::code

    S --> Q0
    Q0 -->|"yes"| BOOK
    BOOK -->|"yes, same contract"| SAME
    BOOK -->|"no"| NEW
    Q0 -->|"follow-up"| R1
    R1 -->|"yes"| X
    R1 -->|"no"| R2
    R2 -->|"yes"| X
    R2 -->|"no"| R3
    R3 -->|"yes"| X
    R3 -->|"no"| R4
    R4 -->|"yes"| X
    R4 -->|"no, several"| AMB --> HINT --> REV
    R4 -->|"book empty"| NONE --> REV
    X -->|"agrees"| LINK
    X -->|"names another underlying"| AMB
```

`resolve_link` is the function that combines the resolver result with the message type:

- An opening signal creates a position (`method = code`) unless the lane already has that contract, or the text is a re-entry (`back in`, `re-entry`, `reloaded`) onto a position closed today.
- A follow-up takes the resolver's unique pick when the text does not contradict it.
- Otherwise the outcome is `ambiguous` or `none`, and `decide` emits a review card. The model's `links_to` is stored on the link row as a proposal so the card can show it. Confidence does not turn that into a link.
- `watch` and `conditional` update the book (`status` `watch` or `conditional`, optional `condition_text`) and produce no card. A later entry on that setup is an entry, not an add.
- A follow-up with no user lot updates the provider book and creates no card.

## 4.4 Late events and re-links

If a newer event on the lane is already `done` or `stale`, the late event is still applied and flagged `late`. After commit, `RelinkService.recheck_after_late` re-interprets affected follow-ups (`GraphProcessor.recheck` writes nothing) and opens review cards. It does not rewrite those positions by itself.

A user correction, an edit, or a delete goes through `cards.corrections` / `manual.corrections`: pending cards on the old reading are invalidated, a new `message_links` or `manual_corrections` row records the change, and the profile store is touched only on that audited path.

## 4.5 What a model is allowed to see

`load_context` builds the lane packet: the message, the reply parent, recent messages, mentions, the lane book, and the provider profile (`lexicon`, `habits`, `aliases`).

`_model_context` then replaces the book with `link_candidates`, the positions the resolver already narrowed. The model does not see the rest of the lane, and it never sees lots or P&L. A scope-guard hit on the LLM request sends nothing, stores `scope_violation`, and raises an urgent alert.
