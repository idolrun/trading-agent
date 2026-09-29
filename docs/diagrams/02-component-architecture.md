# 2. Detailed Component Architecture

[← High-level](01-high-level-architecture.md) · [Index](README.md) · Next: [End-to-end data flow →](03-end-to-end-data-flow.md)

The service is a single Python asyncio process split into the modules of TRD §2.2. Module boundaries are code boundaries (packages), not network boundaries.

## 2.1 Module map with communication types

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef model fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c
    classDef ext fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    classDef store fill:#eceff1,stroke:#37474f,color:#263238

    MAPI["Message API"]:::ext
    BROKER["Broker"]:::ext
    MDFEED["Market-data feed"]:::ext
    JEVX["TypeSafe Jev"]:::ext
    LLMX["LLM vendors"]:::ext
    BROWSER["Dashboard / PWA"]:::ext

    subgraph TRANSPORT["Transport"]
        ingest["ingest<br/>webhook · HMAC · watchdog<br/>gap detector + backfill"]:::code
        inbox["inbox<br/>accept() · next_for_provider() · mark()<br/>ONLY transport boundary"]:::code
        dispatcher["dispatcher<br/>per-provider workers · staleness<br/>model semaphores · poison · heartbeat"]:::code
    end

    subgraph ORCH["Orchestration"]
        graphm["graph<br/>LangGraph StateGraph<br/>AsyncPostgresSaver checkpointer"]:::code
    end

    subgraph INTERPRET["Interpretation (no side effects)"]
        cand["interpret.candidates<br/>regex over-find: tickers, numbers,<br/>expiry tokens, fraction words"]:::code
        jev["interpret.jev<br/>state + Appendix A questions<br/>answers → fields w/ provenance"]:::model
        llmx["interpret.llm<br/>create_agent slow path + middleware<br/>note writer"]:::model
        llmreg["llm<br/>model registry · capability table<br/>fallback chain · circuit breakers · cost"]:::code
    end

    subgraph DOMAIN["Deterministic domain core"]
        validate["validate<br/>symbol map · optionable list · chain/quote identity<br/>scale normalisation · adjacent strike · dates"]:::code
        decide["decide<br/>PURE function: action, qty, tier,<br/>card tier, risk limits, TP legs"]:::code
        ledger["ledger<br/>provider-position ledger<br/>user-lot ledger"]:::code
        cards["cards<br/>card state machine · validity (C22)<br/>supersession · notifications"]:::code
    end

    subgraph EXECUTION["Execution"]
        execm["execution<br/>submit_order() · FR-20 invariants<br/>pricing · re-pricing · brackets<br/>system-held stops · stop restore · EOD"]:::code
        timers["timers<br/>persisted deadlines: card expiry,<br/>cutoffs 1/2, off-hours release"]:::code
        reconcile["reconcile<br/>startup + periodic + post-fill"]:::code
    end

    subgraph ADAPTERS["Adapters"]
        brokera["broker adapter"]:::code
        mda["marketdata adapter<br/>in-process quote dict + chain TTL cache"]:::code
    end

    subgraph WEBT["Presentation"]
        web["web<br/>FastAPI · HTMX · SSE · Web Push<br/>password auth · manual ops · labeling"]:::code
        evalkit["evalkit<br/>replay runner · gate metrics"]:::code
    end

    PG[("PostgreSQL")]:::store

    MAPI -->|"sync: POST /ingest"| ingest
    ingest -->|"sync: GET history"| MAPI
    ingest -->|"sync call"| inbox
    inbox -.->|"async: LISTEN/NOTIFY"| dispatcher
    dispatcher -->|"sync: next_for_provider / mark"| inbox
    dispatcher -->|"sync: start run (thread per event)"| graphm
    graphm -->|"sync node calls"| cand
    graphm -->|"sync node calls"| jev
    graphm -->|"sync node calls (timeboxed)"| llmx
    llmx -->|"sync"| llmreg
    llmreg -->|"sync HTTPS"| LLMX
    jev -->|"sync HTTPS, 1 request"| JEVX
    graphm -->|"sync"| validate
    validate -->|"sync: chain, quote"| mda
    graphm -->|"sync"| decide
    graphm -->|"sync: apply txn"| ledger
    graphm -->|"sync: apply txn"| cards
    cards -.->|"async: SSE + push"| web
    graphm -.->|"async: spawn note_writer"| llmx
    web -->|"sync: approval → Command(resume)"| graphm
    timers -.->|"async: expire / cutoff / release"| graphm
    timers -.->|"async: cutoff 2 auto-close"| execm
    graphm -->|"sync: submit_order()"| execm
    execm -->|"sync"| brokera
    brokera -->|"sync REST"| BROKER
    BROKER -.->|"async: order/fill events (OD-10)"| brokera
    brokera -.->|"async: fill events"| execm
    MDFEED -.->|"async: stream"| mda
    mda -.->|"async: underlying ticks"| execm
    execm -->|"sync: fill → lot update"| ledger
    execm -.->|"async: after fill"| reconcile
    reconcile -->|"sync"| brokera
    web -->|"sync: manual ops (same checks)"| execm
    web <-.->|"async: SSE / push"| BROWSER

    inbox --- PG
    graphm --- PG
    ledger --- PG
    cards --- PG
    execm --- PG
    timers --- PG
    reconcile --- PG
    web --- PG
    llmreg --- PG
    evalkit --- PG
```

### What the diagram represents
Every module, the direction of calls between them, and whether each call is synchronous (solid) or asynchronous (dashed). All modules share one Postgres connection pool; plain lines to `PostgreSQL` mean "reads/writes its own tables" (ownership in §2.2).

### Component responsibilities and interaction rationale

| Module | Owns | Called by | Key rule |
|---|---|---|---|
| `ingest` | webhook, feed-health watchdog, gap detector | Message API (push), its own 60 s loop | Writes only through `inbox.accept()`. |
| `inbox` | `inbox`, `message_events` insert | `ingest`, `dispatcher` | The only transport boundary. A future Kafka consumer would implement the same 3 functions (§2.5). |
| `dispatcher` | worker per provider, staleness check, catch-up mode, model semaphores, poison handling, heartbeat | NOTIFY | Holds the **provider slot** from `load_context` to `notify`; never across `interrupt()`. |
| `graph` | the per-message StateGraph + checkpointer | dispatcher, web (resume), timers (resume) | Orchestration state only; domain truth lives in domain tables (TRD §1.4). |
| `interpret.candidates` | regex finders | graph | Over-find; no whitelist (C86). |
| `interpret.jev` | Jev state builder, question catalog | graph | Jev *chooses among candidates*; it cannot invent digits (C78). |
| `interpret.llm` | `create_agent` slow path, note writer, middleware guardrails, read-only tools | graph | Output passes literal-span verification; no write tools. |
| `llm` | registry, capability table, fallback chain, circuit breakers, cost normalisation | interpret.llm | No provider-specific code outside this module (TRD §4.8). |
| `validate` | identity & normalisation checks | graph | Failures downgrade to review cards; never auto-correct (C78). |
| `decide` | pure decision functions | graph | No I/O; every PRD scenario is a unit test. |
| `ledger` | provider positions, user lots | graph (`apply`), execution (fills), reconcile, web (corrections) | Rows locked `SELECT … FOR UPDATE` inside `apply`. |
| `cards` | card state machine, validity, notifications | graph, timers, web | Card unique on `(signal_id, action)`. |
| `execution` | `submit_order()`, pricing loops, stop watcher, stop restore, EOD | graph, timers, web (manual) | The only module that calls the broker for orders. |
| `timers` | persisted deadlines | own loop | Survive restarts (reloaded at startup step 8). |
| `reconcile` | broker ↔ lot comparison | startup, 60 s loop, post-fill | Mismatch blocks entry cards. |
| `web` | dashboard, auth, manual ops, labeling | browser | Manual orders hit the same `submit_order()` checks (C42). |
| `evalkit` | replay + gates | offline | Replays from `message_events`; never re-triggers live side effects. |

### Consistency considerations
- **Synchronous inside a message run, asynchronous across runs.** A single message's context → apply path is synchronous and serialised per provider. Cross-cutting events (NOTIFY, SSE, timers, broker streams) are asynchronous and always re-read state from Postgres before acting.
- **Resume sources are multiple** (web, timers, supersession, crash recovery) but the graph thread's `await_approval` node accepts exactly one resume; the `cards` state machine rejects transitions out of terminal states, so a late approve after expiry is a no-op.

### Failure points
Blocked event loop (caught by the dispatcher heartbeat, `/health` fails after 15 s) · model semaphores saturated by 429s (counted as queue wait) · note writer failure (off critical path; card shows without note).

---

## 2.2 Data ownership: which module writes which table

```mermaid
flowchart LR
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef audit fill:#fffde7,stroke:#f9a825,color:#5d4037
    classDef state fill:#eceff1,stroke:#37474f,color:#263238
    classDef lg fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c

    subgraph MODS["Writer modules"]
        inbox["inbox"]:::code
        dispatcher["dispatcher"]:::code
        interp["interpret.jev / interpret.llm"]:::code
        apply["graph: apply node<br/>(ledger + cards, ONE txn)"]:::code
        cards["cards"]:::code
        web["web"]:::code
        execm["execution"]:::code
        timers["timers"]:::code
        reconcile["reconcile"]:::code
        ingest["ingest"]:::code
        evalkit["evalkit"]:::code
        graphm["graph runtime"]:::code
    end

    subgraph AUDIT["Append-only audit (INSERT, SELECT only)"]
        me[("message_events")]:::audit
        mc[("model_calls")]:::audit
        sig[("signals")]:::audit
        ml[("message_links")]:::audit
        ce[("card_events")]:::audit
        ap[("approvals")]:::audit
        oe[("order_events")]:::audit
        fi[("fills")]:::audit
        al[("alerts")]:::audit
        cc[("config_changes")]:::audit
        mcor[("manual_corrections")]:::audit
        lo[("logins")]:::audit
        ev[("eval_labels / eval_runs")]:::audit
    end

    subgraph STATE["Current state (mutable)"]
        ib[("inbox")]:::state
        pp[("provider_positions")]:::state
        lots[("lots")]:::state
        cardt[("cards")]:::state
        ord[("orders")]:::state
        st[("stops")]:::state
        tm[("timers")]:::state
        cfg[("config")]:::state
        hb[("dispatcher_heartbeat")]:::state
    end

    subgraph LGT["LangGraph-managed (not audit)"]
        ckpt[("checkpoint tables")]:::lg
        store[("PostgresStore: provider profiles")]:::lg
    end

    inbox ==> me
    inbox ==> ib
    dispatcher ==>|"status, attempts, flags"| ib
    dispatcher ==> hb
    dispatcher ==>|"poison, catch-up"| al
    interp ==> mc
    apply ==> sig
    apply ==> ml
    apply ==> pp
    apply ==> cardt
    apply ==> ce
    apply ==>|"status = done"| ib
    cards ==>|"state transitions"| cardt
    cards ==> ce
    cards ==> tm
    web ==> ap
    web ==> mcor
    web ==> lo
    web ==> cc
    web ==> ev
    web ==>|"provider-position corrections"| pp
    web ==>|"audited profile writes only"| store
    web ==>|"config edits"| cfg
    execm ==> ord
    execm ==> oe
    execm ==> fi
    execm ==> lots
    execm ==> st
    execm ==> al
    timers ==> tm
    reconcile ==>|"adopt orders"| ord
    reconcile ==>|"foreign lots, mismatch flags"| lots
    reconcile ==> al
    ingest ==>|"feed silence"| al
    evalkit ==> ev
    graphm ==> ckpt
```

### Ownership rules
- **Append-only tables** are granted `INSERT, SELECT` only to the app DB role (TRD §8). No module can rewrite history; corrections are new rows (e.g. a re-link is a new `message_links` row).
- **Provider ledger writers:** `apply` (from messages) and `web` (user corrections, C73). Both lock the provider's rows `FOR UPDATE`.
- **Lot writers:** `execution` (fills) and `reconcile` (foreign lots / mismatch flags). Fills update lots under the **lot's own row lock**, independent of the provider slot (TRD §5.1).
- **Long-term store (provider profiles):** written only by audited code paths from user-approved sources. **Models never write to it** (TRD §5.3) — this prevents memory poisoning by provider messages.
- History of `provider_positions`, `lots`, `stops` is required by PRD §14 but no history table is specified in the TRD → **OD-13** (see [07](07-data-model.md)).

---

## 2.3 Interpretation subsystem internals

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef model fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef store fill:#eceff1,stroke:#37474f,color:#263238

    CTX["load_context (code)<br/>message · reply parent · author recent msgs<br/>@mentioned msgs · provider book · provider profile<br/>NO user lots / balances / P&L"]:::code
    CAND["find_candidates (code)<br/>tickers, numbers, expiry tokens, fraction words<br/>each with id + position"]:::code
    JEV["jev_interpret<br/>ONE request, all Appendix A questions<br/>msg_type · signal_count · role per number<br/>ticker · right · expiry · tier · links_to …"]:::model
    ROUTE{"route (code thresholds)<br/>T_chat 0.9 · T_field 0.8 · T_link 0.8"}:::code

    subgraph SLOW["llm_extract node — shared 3,000 ms deadline"]
        direction TB
        SG["@before_model scope guard<br/>reject if any portfolio field"]:::code
        CHAIN["registry chain attempt k<br/>anthropic → openai → deepseek<br/>per-provider semaphore + circuit breaker"]:::model
        TOOLS["read-only tools (≤ 2 calls)<br/>get_option_chain · get_quote<br/>get_author_messages · get_provider_positions · get_message"]:::code
        SCHEMA["@after_model schema check<br/>ParsedSignals (pydantic)"]:::code
        SPAN["@after_agent literal-span verification"]:::code
        SG --> CHAIN
        CHAIN <--> TOOLS
        CHAIN --> SCHEMA
        SCHEMA -->|"valid"| SPAN
        SCHEMA -->|"invalid → next attempt"| CHAIN
        CHAIN -->|"timeout / 429 / 5xx / refusal → next attempt"| CHAIN
    end

    ASM["assemble_signal (code)<br/>fields tagged literal / context / resolved"]:::code
    FEED["feed_only"]:::code
    REVIEW["review card<br/>(no answer shopping)"]:::fail
    RAW["raw review card<br/>+ 'parser degraded' alert"]:::fail
    MC[("model_calls<br/>every attempt, probabilities,<br/>tokens, cost, latency")]:::store
    NOTE["note_writer (async, after card shown)<br/>advisory text → note guard (OD-6)"]:::model

    CTX --> CAND --> JEV --> ROUTE
    ROUTE -->|"chatter / commentary / watch, conf ≥ T_chat"| FEED
    ROUTE -->|"confident + complete"| ASM
    ROUTE -->|"field none / not_stated / conf < T_field<br/>or signal_count = multiple"| SG
    ROUTE -->|"links_to ambiguous or conf < T_link"| REVIEW
    SPAN -->|"all spans found"| ASM
    SPAN -->|"span missing → hard reject"| REVIEW
    CHAIN -->|"deadline passed / chain exhausted"| RAW
    JEV -.-> MC
    CHAIN -.-> MC
    ASM -.->|"after card"| NOTE
```

### Key points
- **Jev chooses, code finds.** Every number in a literal field is a span copied from the text, so a model cannot transpose a digit (C78, FR-7).
- **Confidence only downgrades** (TRD §1.3): a low `links_to` goes to a review card and *no model can upgrade it*.
- **Fallback vs no-fallback:** infrastructure failures (timeout, 429/5xx, schema-invalid, refusal) fall to the next vendor within the same deadline. **Content failures** (span not in text, chain/quote mismatch) go to review — re-asking other models until one "passes" would reward a matching hallucination (TRD §4.8 rule 3).
- **One message = one model's output.** Outputs are never merged across providers (rule 4).
- **Jev outage:** 429/529 retried once, then the message takes the slow path (latency rises, nothing is lost). If Jev fails the week-0 bench, the graph runs without `jev_interpret` (OD-8).

---

## 2.4 Execution subsystem internals

```mermaid
flowchart TB
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef ext fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    classDef fail fill:#ffebee,stroke:#c62828,color:#b71c1c
    classDef store fill:#eceff1,stroke:#37474f,color:#263238
    classDef human fill:#fff3e0,stroke:#e65100,color:#bf360c

    subgraph CALLERS["Callers (the ONLY paths to an order)"]
        G["graph: submit_order node<br/>(after approval + revalidate)"]:::code
        M["web: manual ops<br/>(password login; step-up for entry / flatten)"]:::human
        T["timers: cutoff 2 EOD auto-close"]:::code
        SW["stop watcher: underlying crossed<br/>(pre-approved)"]:::code
        RP["exit re-price loop<br/>(within approved intent)"]:::code
        SR["stop restore after unfilled exit"]:::code
    end

    SUB["submit_order(intent)"]:::code
    INV{"FR-20 invariants<br/>fresh broker position read<br/>1 never open a short<br/>2 sell qty ≤ this provider's lot<br/>3 exits reduce/cancel stops & TPs first"}:::code
    BLOCK["blocked → alert + invariant-block metric"]:::fail
    MODE{"mode?"}:::code
    SHADOW["shadow: record only, no broker call"]:::code
    INTENT[("orders row: status intent<br/>client_order_id = uuid5(card_id, action_seq)<br/>COMMIT before broker call")]:::store
    CALL["broker adapter: place order<br/>limit / stop / OCO bracket / stop-market GTC"]:::code
    BRK["Broker"]:::ext
    TO{"timeout?"}:::code
    QRY["query status by client_order_id<br/>NEVER blind retry"]:::code
    WORK[("order_events: submitted → working<br/>→ partially_filled → filled / cancelled / rejected / unknown")]:::store
    FILLS[("fills + lots update<br/>(lot row lock)")]:::store

    G --> SUB
    M --> SUB
    T --> SUB
    SW --> SUB
    RP --> SUB
    SR --> SUB
    SUB --> INV
    INV -->|"fail"| BLOCK
    INV -->|"pass"| MODE
    MODE -->|"shadow"| SHADOW
    MODE -->|"paper / live"| INTENT --> CALL --> BRK
    CALL --> TO
    TO -->|"yes"| QRY --> WORK
    TO -->|"no"| WORK
    BRK -.->|"fills (OD-10)"| FILLS
```

**Pricing policies** executed inside this subsystem (all code, PRD §9):

| Action | Policy | Source |
|---|---|---|
| Entry / add | Limit at signal price (top of range) + X%; cancel unfilled remainder after M s; keep partial fills. | C22, FR-21 |
| Exit / trim | Limit at mid; re-price toward bid every few seconds until filled or floor reached; at floor stop + notify + restore stops. | C25 |
| Premium stop | Broker-held stop order; fires with no further approval. | C28 |
| Underlying stop | System-held; stop watcher triggers the C25 exit when the underlying crosses. Candle-close shown as info only. | C49 |
| Stock stop | Stop-market GTC at broker. | C61 |
| Take-profits | Broker limit orders, OCO-linked with stop; legs = min(levels, qty) from TP1. | C50, C56, C83 |
| EOD | Cutoff 1 → urgent close card; cutoff 2 → automatic C25 exit, stops/TPs cancelled first. | C19 |

---

## 2.5 Extension boundaries (Kafka / Redis — **not deployed**)

PRD C94 and TRD §15 rule out Kafka and Redis now but require boundaries so either can be added **only on a measured trigger**.

```mermaid
flowchart LR
    classDef code fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef future fill:#fafafa,stroke:#9e9e9e,color:#757575,stroke-dasharray: 5 5
    classDef store fill:#eceff1,stroke:#37474f,color:#263238

    subgraph NOW["Deployed today"]
        IB["inbox boundary<br/>accept · next_for_provider · mark"]:::code
        PGQ[("Postgres inbox + LISTEN/NOTIFY")]:::store
        ACC["hot-read accessors<br/>context.load · marketdata.quote<br/>marketdata.chain · provider_lock"]:::code
        MEM["in-process dict / TTL cache<br/>asyncio.Lock"]:::code
        IB --> PGQ
        ACC --> MEM
    end

    K["Kafka consumer — NOT DEPLOYED<br/>key = provider_id · commit offset after apply<br/>no dead-letter skip · staleness per event"]:::future
    R["Redis — NOT DEPLOYED<br/>derived, rebuildable data only<br/>never ledgers / orders / approvals"]:::future
    AL["Postgres advisory lock<br/>(if multi-process)"]:::future

    IB -. "same 3 functions,<br/>no downstream change" .-> K
    ACC -. "behind accessor,<br/>falls back to PG / broker" .-> R
    ACC -. "session-level pg_try_advisory_lock" .-> AL
```

| Adopt Kafka only if measured | Adopt Redis only if measured |
|---|---|
| Sustained ingest > ~100 events/s, or inbox insert p99 > 50 ms after tuning | ≥ 2 app processes need shared live quotes and broker connection limits bite |
| ≥ 2 independently deployed consumers need separate replay positions | `context.load` p95 > 50 ms after indexing |
| Multi-user fan-out > ~1,000 users (PRD non-goal) | Vendor rate limits must be shared across processes |
| Multi-region ingest | `LISTEN/NOTIFY` fan-out to a scaled web tier measured insufficient |
| Must accept while PG is down **and** Message API cannot redeliver (try local spool / SQS first) | — |
