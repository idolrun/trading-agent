# 6. Sequence Diagrams

[← Signal processing](05-signal-processing-flowchart.md) · [Index](README.md) · Next: [Data model →](07-data-model.md)

Participants keep these names:

| Alias | Participant |
|---|---|
| `DC` | Discord gateway (`DiscordListener`) |
| `IN` | `inbox.accept` |
| `WP` | dispatcher worker for one lane |
| `G` | message graph, thread `msg:{message_id}:{event_type}:{event_seq}:{attempt}` |
| `J` / `Laya` / `LLM` | Jev, local Laya, LLM chain |
| `PL` | `Planner` (validate + link + `decide`) |
| `DB` | PostgreSQL |
| `AG` | approval graph, thread `card:{card_id}` |
| `W` | FastAPI + Next.js desk |
| `U` | signed-in user |
| `X` | `OrderService` / `CardExecutor` |
| `B` | Alpaca, or `SimulatedBroker` when `trading_mode` is `shadow` |
| `T` | `TimerService` |
| `R` | `Reconciler` |

`POST /ingest` replaces `DC` when the event arrives on the webhook. The steps after `accept` are the same.

---

## 6.1 New entry

```mermaid
sequenceDiagram
    autonumber
    participant DC as Discord
    participant IN as inbox
    participant WP as lane worker
    participant G as message graph
    participant J as Jev
    participant PL as planner
    participant DB as Postgres
    participant W as desk

    DC->>IN: create payload
    IN->>DB: INSERT message_events + inbox ON CONFLICT DO NOTHING
    DB-->>IN: committed
    IN--)WP: NOTIFY channel:provider
    WP->>DB: next_for_lane, mark processing
    WP->>G: process, lane lock held
    G->>DB: load_context, lane book
    G->>G: resolve_context, find_candidates
    G->>G: fast_parse misses
    G->>J: one request, lane candidates only
    J-->>G: entry, fields, new position
    G->>G: route assemble
    G->>PL: plan
    PL->>PL: validate chain and quote, decide entry card, qty blank
    G->>DB: apply one txn, signals, link method code, position, card pending, expiry timer, inbox done
    G->>G: notify starts approval thread card:id
    G--)W: NOTIFY cards, SSE
    Note over WP,G: lane lock released, approval graph is paused on its own thread
```

A fast-path match skips Jev. Off-hours still runs this graph, writes the position, and schedules `offhours_release` instead of inserting the card.

---

## 6.2 Follow-up trim

```mermaid
sequenceDiagram
    autonumber
    participant WP as lane worker
    participant G as message graph
    participant DB as Postgres
    participant W as desk

    WP->>G: later message, same lane, after the entry is applied
    G->>G: resolve_context, reply or thread or contract or sole position
    G->>G: fast rule or Jev, type trim
    G->>DB: apply, position trimmed, message_links method from the resolver
    alt user has an open lot for that position
        G->>DB: trim card pending, qty blank, fraction stored as a label
        G--)W: card on the desk
    else no lot
        G->>DB: provider book only, no card
    end
```

A full exit or a trim of `all` is a close card whose quantity is the lot quantity. A soft exit is an exit card labelled `suggestion`.

---

## 6.3 Stops

```mermaid
sequenceDiagram
    autonumber
    participant G as message graph
    participant DB as Postgres
    participant U as User
    participant AG as approval graph
    participant X as execution
    participant B as broker or simulator
    participant SW as underlying-stop watcher

    G->>DB: stop_set applied, stop card pending
    U->>AG: approve
    AG->>X: CardExecutor, BracketService.protect
    alt premium stop
        X->>B: broker-held stop
        X->>DB: stops row, held_by broker
    else underlying stop
        X->>DB: stops row, held_by system
        SW->>SW: poll snapshots
        SW->>X: level crossed, ExitPolicy, auto_reason underlying_stop
    end
```

Breakeven stores both the user's average and the provider's entry on the card. The user picks. Candle-close is a label, not a trigger. A stock stop is a stop-market order at the broker.

---

## 6.4 Edit, correction, delete

```mermaid
sequenceDiagram
    autonumber
    participant DC as Discord
    participant G as message graph
    participant DB as Postgres
    participant AG as approval graph

    alt edit
        DC->>G: edit event, new event_seq
        G->>DB: correction path, pending cards on the old reading invalidated
        G->>AG: resume those threads with invalidate
        G->>DB: book and a new message_links or manual_corrections row
    else delete
        DC->>G: delete
        G->>DB: handle_delete, pending cards invalidated, inbox done
        Note over G: load_context returns skip, no interpret
    end
```

If the user has already traded the corrected signal, the book still changes and an alert is raised. The fill and the lot are not rewritten.

---

## 6.5 Ambiguous link

```mermaid
sequenceDiagram
    autonumber
    participant G as message graph
    participant J as Jev
    participant DB as Postgres
    participant U as User

    G->>G: resolver outcome ambiguous, several candidates
    G->>J: may rank them
    J-->>G: link hint
    G->>DB: message_links outcome ambiguous, hint stored as evidence.proposal
    G->>DB: review card, missing chosen_position_id
    U->>DB: picks a position, method user_correction
    Note over G,J: the hint never becomes the link by confidence alone
```

A link doubt (`route` kind `link`, including re-entry among several closed positions) skips Laya and the LLM and opens the review card directly.

---

## 6.6 Approve to an order

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant W as desk
    participant DB as Postgres
    participant AG as approval graph
    participant X as OrderService
    participant B as Alpaca or simulator

    U->>W: POST decide approve, optional edits and qty
    W->>DB: approvals row, card approved, same transaction
    W->>AG: Command resume
    AG->>AG: revalidate, quote and validity
    AG->>X: execute
    X->>DB: orders intent, client_order_id, card submitted
    alt shadow
        X->>B: SimulatedBroker, no Alpaca call
    else paper or live
        X->>B: place
        B-->>X: ack or timeout
        X->>B: on timeout, query by client_order_id
    end
```

Reject, expiry, and invalidate are written on the card before resume. The approval graph sees a non-approve decision and ends. A failed revalidation moves `approved` to `invalidated` with `revalidate_failed`. If every placed order is `rejected`, the reason is `order_rejected`.

---

## 6.7 Fill to the lot

```mermaid
sequenceDiagram
    autonumber
    participant B as Alpaca or simulator
    participant C as BrokerEventConsumer
    participant DB as Postgres
    participant BR as BracketService
    participant R as Reconciler

    B--)C: status or fill, client_order_id
    C->>DB: one txn, fill by broker_fill_id, lot open or adjust, order filled_qty
    C--)DB: NOTIFY fills, orders, lots
    C->>BR: on_fill, place or resize protective legs
    C->>R: on_fill
```

An unknown `client_order_id` is ignored. Status only moves forward. Reconciliation can synthesize the same events with source `reconcile` when the stream missed one.

---

## 6.8 Process start

```mermaid
sequenceDiagram
    autonumber
    participant APP as lifespan
    participant DB as Postgres
    participant R as Reconciler
    participant AG as approvals
    participant GAPS as GapDetector
    participant T as timers
    participant D as Dispatcher

    APP->>DB: entries_blocked, reason startup
    APP->>DB: advisory lock, second process exits
    APP->>R: run, release false, sync orders, compare lots
    APP->>AG: expire pending cards, reason restart
    APP->>GAPS: run_once
    APP->>T: fire due timers, schedule today's cutoffs
    APP->>D: reset in-flight inbox rows, start workers
    alt reconcile clean, or shadow skip
        APP->>R: release entries_blocked
        APP->>AG: resume_approved, including entries
    else mismatch
        APP->>AG: resume_approved, entries stay deferred
    end
```

Failover recovery (`recover_after_failover`) is the shorter list: it does not expire cards and does not reset the dispatcher. It runs when the listener reconnects the worker after the lock was lost.

---

## 6.9 Reconciliation

```mermaid
sequenceDiagram
    autonumber
    participant R as Reconciler
    participant B as Alpaca
    participant C as consumer
    participant DB as Postgres

    Note over R: periodic loop, and after every fill, paper and live only
    R->>B: positions and working orders
    R->>C: missing order events, source reconcile
    R->>DB: lot qty vs broker qty, foreign lots adopted
    alt not clean
        R->>DB: entries_blocked reason reconciliation, reconcile_mismatch alerts
    else clean
        R->>DB: clear the block when the reason was startup, failover, or reconciliation
    end
```

Shadow mode returns clean immediately (`skipped=shadow`) because there is no broker account to compare.

---

## 6.10 End of day

```mermaid
sequenceDiagram
    autonumber
    participant T as timers
    participant E as ExpirationService
    participant DB as Postgres
    participant U as User
    participant X as ExitPolicy

    T->>E: cutoff_1
    E->>DB: close card pending, supersede an older close card on the lot
    U->>X: approve, normal exit path
    T->>E: cutoff_2
    E->>X: Auto EOD_FORCED_CLOSE, stops and TPs cancelled first
```

`schedule_today` arms both timers from the runtime config at startup. A fill that closes the lot cancels them via `expiration.on_fill`.

---

## 6.11 Model failure and the slow path

```mermaid
sequenceDiagram
    autonumber
    participant G as message graph
    participant J as Jev
    participant Laya as Laya
    participant LLM as LLM chain
    participant DB as Postgres

    G->>J: interpret
    alt Jev off, timeout, API error, invalid answer
        J-->>G: model_failure
        G->>Laya: local model
        alt Laya settles it
            Laya-->>G: assemble or feed
        else Laya still unsure, chain configured
            G->>LLM: semantic extract, shared deadline
            LLM-->>G: next provider on timeout, 429, 5xx, schema failure
        else link doubt
            G->>DB: raw review, no further model
        end
    else Jev answered but a field or the type is unsure
        J-->>G: semantic
        G->>LLM: Laya is skipped
    end
    alt chain exhausted or span missing
        G->>DB: raw review, interpret_outcome review or validation_failure
    end
```

Content rejection (a span that is not in the text) does not try the next vendor. Infrastructure failure does, inside the same deadline. One message keeps one interpreter's output.

---

## 6.12 Poison message

```mermaid
sequenceDiagram
    autonumber
    participant WP as lane worker
    participant G as message graph
    participant DB as Postgres
    participant P as poison handler

    WP->>G: attempt 1
    G-->>WP: exception
    WP->>DB: inbox received, attempts kept, last_error set
    Note over WP: connection errors do not count
    WP->>G: attempt >= poison_retry_limit (default 3)
    G-->>WP: exception
    WP->>DB: inbox failed
    WP->>P: RawReviewPoisonHandler
    P->>DB: raw review card + poison alert
    Note over WP: the lane worker takes the next event
```
