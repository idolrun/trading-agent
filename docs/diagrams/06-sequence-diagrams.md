# 6. Sequence Diagrams

[← Signal processing](05-signal-processing-flowchart.md) · [Index](README.md) · Next: [Data model →](07-data-model.md)

Participants use the same names throughout:

| Alias | Participant |
|---|---|
| `API` | User's Message API (external) |
| `ING` | `ingest` (webhook, gap detector) |
| `IN` | `inbox` (Postgres table + NOTIFY) |
| `WP` | dispatcher worker for provider P |
| `G` | LangGraph run (thread `msg:<message_id>:<event_seq>`) |
| `J` / `LLM` | Jev / LLM provider chain |
| `VD` | `validate` + `decide` (pure code) |
| `MD` | market-data adapter |
| `DB` | PostgreSQL |
| `CD` | `cards` module |
| `W` | web tier (dashboard, SSE, push) |
| `U` | Trader |
| `X` | `execution` (`submit_order()`) |
| `B` | Broker |
| `T` | `timers` |
| `R` | `reconcile` |

---

## 6.1 New trade signal (entry with SL/TP → bracket card)

```mermaid
sequenceDiagram
    autonumber
    participant API as Message API
    participant ING as ingest
    participant IN as inbox
    participant WP as worker P
    participant G as graph run
    participant J as Jev
    participant MD as marketdata
    participant VD as validate + decide
    participant DB as Postgres
    participant W as web
    actor U as Trader

    API->>ING: POST /ingest "spy 770c .95 lotto, sl .60 tp 1.40/1.90"
    ING->>ING: verify HMAC + timestamp window
    ING->>IN: accept(envelope)
    IN->>DB: INSERT message_events + inbox ON CONFLICT DO NOTHING
    DB-->>IN: committed
    IN-->>ING: true
    ING-->>API: 200 (only after commit)
    IN--)WP: NOTIFY
    WP->>DB: next_for_provider(P) and mark processing
    WP->>WP: age = now - posted_at (under stale_entry_s)
    WP->>G: start run, provider slot HELD
    G->>DB: load_context (P's msgs, P's book, P's profile)
    G->>G: find_candidates: spy, 770c, .95, .60, 1.40, 1.90
    G->>J: one request with all Appendix A questions
    J-->>G: entry, roles per number, ticker spy, call, lotto, links_to new_position
    G->>G: route: confident and complete, fast path
    G->>G: assemble_signal (literal spans + resolved 0DTE expiry)
    G->>VD: validate
    VD->>MD: chain SPY today, quote 770C
    MD-->>VD: contract exists, ask 0.97
    VD->>VD: scale .95 per share OK, adjacent-strike check OK
    VD->>VD: decide: qty = floor(lotto_max / (0.95 x 100)), risk limits OK, TP legs from TP1
    VD-->>G: NORMAL entry + bracket card proposal
    G->>DB: apply txn: signals, message_links, provider_positions P-new, cards pending, timers expiry, inbox done
    DB-->>G: committed
    G--)W: SSE card + Web Push
    W--)U: card shown (p95 under 5 s from receipt)
    G--)G: spawn note_writer (async, note guard)
    G->>WP: release provider slot
    G->>DB: interrupt() at await_approval, checkpoint saved
    Note over G,DB: Run is paused. P's next message proceeds immediately.
```

**Why:** receipt is acknowledged only once durable; interpretation, validation and decision all complete under the provider slot; the approval wait does not. **Failure points:** Jev unreachable (→ slow path), quote outside tolerance (→ review card), risk limit (→ disabled card), stale (→ no approvable card).

---

## 6.2 Follow-up trim / exit

```mermaid
sequenceDiagram
    autonumber
    participant WP as worker P
    participant G as graph run
    participant J as Jev
    participant VD as validate + decide
    participant DB as Postgres
    participant W as web
    actor U as Trader
    participant X as execution
    participant B as Broker

    WP->>G: "take half off @ 2.10" (P), slot held
    G->>DB: load_context: P's book = P-12 GOOGL 357.5C, recent msgs
    G->>J: questions incl. links_to over P's book
    J-->>G: trim, exit_fraction half, links_to P-12 (0.93)
    G->>VD: decide with provider ledger + user lots (code only)
    VD->>DB: lookup lot by provider_position_id P-12
    alt user holds no lot
        VD-->>G: provider ledger update only
        G->>DB: apply: link + provider position trimmed
        Note over G: feed only, no card
    else lot of 1 contract
        VD-->>G: floor(0.5 x 1) = 0, qty blank, 'lot too small to trim'
        G->>DB: apply: link + ledger + EXIT card (not approvable until qty entered)
    else lot of 4 contracts
        VD-->>G: qty = 2 (editable)
        G->>DB: apply: link + ledger + EXIT card pending
    end
    G--)W: SSE + push
    G->>WP: release slot
    U->>W: approve exit qty 2
    W->>DB: INSERT approvals
    W->>G: Command(resume approve)
    G->>G: revalidate: position not closed, card not expired
    G->>X: submit_order(exit 2)
    X->>B: fresh position read
    X->>X: invariants: sell 2 of lot 4 OK, no short
    X->>B: reduce working stop / TP qty to 2 (FR-20.3)
    X->>DB: orders intent (client_order_id)
    X->>B: limit sell 2 @ mid
    loop every few seconds until filled or floor
        B--)X: working / partial
        X->>B: replace price one step toward bid
    end
    alt filled
        B--)X: fill 2
        X->>DB: fills + lot 4 to 2
    else floor reached unfilled
        X->>B: cancel exit
        X->>B: restore stop / TP to original qty
        X--)W: notify 'exit at floor, unfilled'
    end
```

---

## 6.3 Stop-loss update (premium vs underlying) and trigger

```mermaid
sequenceDiagram
    autonumber
    participant G as graph run
    participant VD as validate + decide
    participant DB as Postgres
    participant W as web
    actor U as Trader
    participant X as execution
    participant MD as marketdata
    participant B as Broker

    G->>VD: signal stop_move, links_to P-3 (SPY 770C), stop_basis ?
    alt stop_basis = premium ("stop .60")
        VD-->>G: STOP card: broker-held stop 0.60 on lot qty
    else stop_basis = underlying ("769 stop")
        VD-->>G: STOP card: system-held stop at SPY 769, candle-close info only
    else breakeven ("stop to BE")
        VD-->>G: STOP card showing user avg fill AND provider entry, user picks
    else stop_cancel ("pulled stop")
        VD-->>G: STOP-CANCEL card
    end
    G->>DB: apply: link + provider stop info + card pending
    G--)W: SSE + push
    U->>W: approve
    W->>DB: INSERT approvals
    W->>G: Command(resume approve)
    G->>X: submit_order(stop change)
    alt premium stop
        X->>DB: orders intent
        X->>B: cancel/replace stop order (OCO with TPs kept linked)
        B-->>X: working
        Note over B: Fires at broker later with no further approval (C28)
    else underlying stop
        X->>DB: stops row: system-held, level 769, lot, approval ref
        loop underlying ticks
            MD--)X: SPY last
        end
        MD--)X: SPY crosses 769
        X->>DB: auto action, auto_reason = underlying_stop
        X->>B: cancel TPs then C25 exit (limit mid, re-price to floor)
        X--)W: push 'underlying stop fired'
    end
```

**Reliability note:** the broker-held stop survives an app outage; the system-held underlying stop **does not** (PRD §21) — uptime monitor pages the phone.

---

## 6.4 Correction message (before and after the trade) · edit · delete

```mermaid
sequenceDiagram
    autonumber
    participant API as Message API
    participant G as graph run
    participant J as Jev
    participant DB as Postgres
    participant CD as cards
    participant OG as original card's paused run
    participant W as web
    actor U as Trader

    API->>G: "sorry 360C not 357.5" (create) or edit event of A1
    G->>J: is_correction, correction_target over P's recent msgs
    J-->>G: correction of A1
    G->>DB: lookup: card for A1 signal, lot for P-A1
    alt user has NOT traded (card pending)
        G->>DB: apply: new signal + message_links (re-link) + NEW card with corrected fields
        G->>CD: original card superseded
        CD->>OG: Command(resume invalidate, by = correction msg)
        OG->>DB: card_events invalidated, no order
        G--)W: new card needs fresh approval (C48)
    else user HAS traded (lot exists)
        G->>DB: apply: new signal + re-link, provider position marked corrected
        G->>DB: INSERT alerts (both messages, live P&L)
        G--)W: ALERT only, no action proposed (C16)
        Note over W: Follow-ups now refer to the corrected contract
    end
    Note over G: Numbers are NEVER auto-corrected (C78). Corrected values come only from the provider's text.

    API->>G: delete event for A1
    G->>CD: pending card for A1?
    CD->>OG: Command(resume invalidate, reason = deleted)
    G->>DB: INSERT alerts 'entry message deleted'
    G--)W: alert
```

---

## 6.5 Ambiguous signal (review card)

```mermaid
sequenceDiagram
    autonumber
    participant G as graph run
    participant J as Jev
    participant DB as Postgres
    participant W as web
    actor U as Trader
    participant X as execution

    G->>DB: load_context: P's book = P-4 GOOGL 357.5C, P-5 META 780C
    G->>J: "take half off @ 2.10"
    J-->>G: trim, links_to ambiguous (P-4 0.48, P-5 0.44)
    Note over G: Confidence below T_link. No model may upgrade this.
    G->>DB: apply: signal + REVIEW card listing P-4 and P-5 (no link row yet)
    G--)W: review card + push
    U->>W: pick P-5, enter qty
    W->>DB: message_links row method = user_correction
    W->>DB: manual_corrections + eval data (C73)
    W->>DB: INSERT approvals
    W->>G: Command(resume approve, edits = position P-5, qty)
    G->>G: revalidate
    G->>X: submit_order(exit against P-5's lot)
```

Variants that also produce review cards: missing underlying ("7775c" → suggestion by live-price proximity, never approvable unconfirmed), identity/quote mismatch, literal-span failure, two numbers claiming one role that the slow path cannot resolve.

---

## 6.6 Human approval → broker order (entry with bracket)

```mermaid
sequenceDiagram
    autonumber
    actor U as Trader
    participant W as web
    participant DB as Postgres
    participant G as graph run
    participant MD as marketdata
    participant X as execution
    participant B as Broker
    participant T as timers

    U->>W: approve card C (signed-in session)
    W->>DB: card still pending? (row lock)
    alt card expired / invalidated
        W--)U: 'card no longer valid'
    else pending
        W->>DB: INSERT approvals, card_events approved
        W->>G: Command(resume approve, approval_id)
        G->>MD: live ask
        G->>G: revalidate C22: now - posted_at under N s, ask under signal + X pct, not superseded
        alt invalid
            G->>DB: card expired / invalidated, reason
            G--)W: notify, no order (C18)
        else valid
            G->>X: submit_order(entry + bracket)
            X->>B: fresh positions read
            X->>X: FR-20 invariants (no short, lot bounds)
            X->>DB: orders intent, client_order_id = uuid5(card_id, action_seq), card submitted
            X->>B: limit buy @ signal(top of range) + X pct, OCO stop + TP legs (broker-specific, OD-3)
            alt response OK
                B-->>X: accepted, broker id
                X->>DB: order_events submitted
            else timeout
                X->>B: query by client_order_id
                B-->>X: found / not found
                X->>DB: order_events per real status (never blind retry)
            end
            X->>T: timer: cancel unfilled remainder after M s
        end
    end
```

**AC-2:** every `orders` row joins to an `approvals` row, or carries an `auto_reason` from the PRD §9 list (broker stop/TP firing, underlying stop firing, exit re-pricing, EOD cutoff 2, stop restore).

---

## 6.7 Order fill → lot / position update

```mermaid
sequenceDiagram
    autonumber
    participant B as Broker
    participant X as execution
    participant DB as Postgres
    participant R as reconcile
    participant W as web
    actor U as Trader

    B--)X: fill event (stream or poll, OD-10): client_order_id, qty, price
    X->>DB: BEGIN
    X->>DB: INSERT fills (immutable, dedupe on broker fill id)
    X->>DB: SELECT lot FOR UPDATE (lot row lock, not provider slot)
    alt entry fill, no lot yet
        X->>DB: INSERT lots (FK provider_position_id, qty, avg price)
    else add fill
        X->>DB: UPDATE lots qty + recomputed avg price (C62)
    else exit / stop / TP fill
        X->>DB: UPDATE lots qty down, close at 0
        X->>DB: adjust sibling stop / TP legs (OCO)
    end
    X->>DB: order_events filled / partially_filled
    X->>DB: COMMIT
    X--)W: push fill notification (C58)
    W--)U: 'filled 2 SPY 770C @ 0.96'
    X--)R: reconcile after fill
    R->>B: positions + working orders
    R->>DB: compare with lots / orders
    alt match
        R->>DB: reconciliation result ok
    else mismatch
        R->>DB: INSERT alerts, entry gate BLOCKED
        R--)W: urgent alert
    end
    Note over DB: Entry partial fill kept, remainder cancelled at M s (C22). A re-entry after a full exit creates a NEW lot.
```

---

## 6.8 Service restart / recovery (TRD §9 startup sequence)

```mermaid
sequenceDiagram
    autonumber
    participant APP as app process
    participant DB as Postgres
    participant B as Broker
    participant G as graph threads
    participant ING as ingest
    participant API as Message API
    participant T as timers
    participant MD as marketdata
    participant WP as dispatcher

    APP->>DB: 1 set entry gate BLOCKED
    APP->>B: 2 load positions + orders
    APP->>DB: 3 adopt working broker orders by client_order_id prefix (never resubmit)
    APP->>DB: 4 reconcile lots vs broker positions
    alt mismatch
        APP->>DB: alert, gate stays BLOCKED until user resolves
    end
    APP->>G: 5 resume every interrupted thread with expire (reason restart)
    G->>DB: open cards expired, no orders
    APP->>DB: 6 inbox rows in processing reset to received
    APP->>ING: 7 run gap detector
    ING->>API: history after last snowflake per channel
    API-->>ING: missing messages
    ING->>DB: accept() each, backfilled flag, duplicates no-op
    APP->>T: 8 reload persisted timers (expiry, cutoffs, off-hours)
    APP->>MD: 9 resubscribe quotes (positions + system-held stops)
    APP->>WP: 10 start dispatcher, catch-up mode if backlog
    APP->>DB: 11 unblock entries (only if reconciled)
    Note over APP,WP: Re-processed messages cannot duplicate: card UNIQUE (signal_id, action), client_order_id deterministic, apply is one txn.
```

---

## 6.9 Reconciliation (periodic, every 60 s in market hours + after every fill)

```mermaid
sequenceDiagram
    autonumber
    participant R as reconcile
    participant B as Broker
    participant DB as Postgres
    participant W as web
    actor U as Trader

    R->>B: positions, working orders
    R->>DB: lots (tracked + foreign), orders not terminal
    R->>R: diff per contract: broker qty vs sum of lots
    alt broker position with no lot and no system order
        R->>DB: foreign lot (read-only, counts toward risk limits, C47)
    else working broker order matching client_order_id prefix unknown to DB
        R->>DB: adopt order (intent row lost, e.g. after PITR restore)
    else order in DB 'unknown' / 'submitted' but terminal at broker
        R->>DB: order_events to real state (and fills)
    else qty mismatch on tracked lot
        R->>DB: INSERT alerts, entry gate BLOCKED
        R--)W: urgent alert
        U->>W: resolve via runbook (manual correction, audited)
        W->>DB: manual_corrections, gate reopened
    end
    R->>DB: reconciliation result row for health page
```

---

## 6.10 End-of-day expiration management (0DTE, C19)

```mermaid
sequenceDiagram
    autonumber
    participant T as timers
    participant CD as cards
    participant DB as Postgres
    participant W as web
    actor U as Trader
    participant X as execution
    participant B as Broker

    Note over T: lots expiring today are in expiration-management state
    T->>CD: cutoff 1 reached
    CD->>DB: URGENT close card per expiring lot
    CD--)W: push urgent
    alt user approves before cutoff 2
        U->>W: approve close
        W->>DB: approvals
        W->>X: submit_order(C25 exit)
    else unanswered at cutoff 2
        T->>X: auto close, auto_reason = eod_cutoff_2
        X->>B: cancel working stops + TPs first
        X->>DB: orders intent
        X->>B: C25 exit: limit mid, re-price toward bid to floor
        X--)W: push 'auto-closed at cutoff 2'
    end
```

---

## 6.11 Failure: LLM slow path fails over, then degrades

```mermaid
sequenceDiagram
    autonumber
    participant G as llm_extract node
    participant REG as llm registry
    participant A1 as Anthropic
    participant O2 as OpenAI
    participant D3 as DeepSeek
    participant DB as Postgres
    participant W as web

    Note over G: shared deadline 3000 ms for the whole chain
    G->>REG: attempt 1 (extract role)
    REG->>A1: ParsedSignals request (scope-guarded)
    A1-->>REG: 529 overloaded
    REG->>DB: model_calls attempt 1 error, breaker count + 1
    G->>REG: attempt 2 with remaining deadline
    REG->>O2: request
    alt schema-valid and spans verified
        O2-->>REG: ParsedSignals
        REG->>DB: model_calls attempt 2 ok
        REG-->>G: fields (one model's output only)
    else output fails literal-span check
        O2-->>REG: ParsedSignals with span not in text
        REG-->>G: HARD REJECT, NO further fallback
        G->>DB: REVIEW card
    else timeout
        REG->>D3: attempt 3 if deadline left and circuit closed
        D3-->>REG: timeout
        REG-->>G: chain exhausted
        G->>DB: RAW review card (blank fields) + alert 'parser degraded'
        G--)W: push alert
    end
```

---

## 6.12 Failure: poison message

```mermaid
sequenceDiagram
    autonumber
    participant WP as worker P
    participant DB as Postgres
    participant G as graph run
    participant W as web

    loop attempt 1..3
        WP->>DB: mark processing, attempts + 1
        WP->>G: run
        G-->>WP: exception (e.g. malformed payload)
        WP->>DB: mark received, last_error
    end
    WP->>DB: mark failed
    WP->>DB: RAW review card + alerts
    WP--)W: alert push
    WP->>DB: next_for_provider(P)
    Note over WP: P's later messages continue (FR-1e, AC-20). Nothing skipped silently.
```

Broker submit timeout and crash-during-order sequences are in [09 §9.8](09-failure-recovery.md).
