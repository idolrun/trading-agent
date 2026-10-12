# System Architecture — Discord Signal Copy-Trading Assistant

**Status:** Current with the code · 2026-10-12
**Drawn from:** `backend/app` and `frontend/app`. `docs/prd.md` and `docs/trd.md` remain the product spec. Where a diagram and those documents disagree, the diagram follows what the process actually does.
**Audience:** engineers building, reviewing, or operating the system.

---

## 1. How to read this document

| # | Document | What it answers |
|---|---|---|
| 1 | [High-level system architecture](01-high-level-architecture.md) | What are the pieces, who talks to whom, where are the trust boundaries? |
| 2 | [Detailed component architecture](02-component-architecture.md) | Which module does what, sync vs async, who owns which table? |
| 3 | [End-to-end data flow](03-end-to-end-data-flow.md) | How does one message become an order, fill and reconciled lot, and where is state persisted? |
| 4 | [Message context & signal correlation](04-context-correlation.md) | How are lanes isolated, and how does a follow-up find the right position? |
| 5 | [Signal processing flowchart](05-signal-processing-flowchart.md) | Every decision point, where models stop and code takes over. |
| 6 | [Sequence diagrams](06-sequence-diagrams.md) | Twelve key workflows, message by message. |
| 7 | [Data model](07-data-model.md) | Tables, relationships, immutable audit vs current state, state machines. |
| 8 | [Deployment architecture](08-deployment.md) | Processes, network boundaries, external dependencies. |
| 9 | [Failure & recovery architecture](09-failure-recovery.md) | What happens when each dependency fails, and how consistency is restored. |
| 10 | [Architecture summary](10-architecture-summary.md) | Style, source of truth, ordering, concurrency, reliability, models vs code, human approval. |
| 11 | [As-built one-pager](11-as-built-architecture.md) | The same pipeline on a single chart. |
| 12 | [Decision](12-decision.md) | How `decide()` turns a parsed signal into a card, and how approval resumes. |
| 13 | [Layers](13-layers.md) | Transport, correlation, interpretation, decision, state, approval. |
| 14 | [Jev](14-jev.md) | The TypeSafe call, the failure kinds, and where each kind goes. |

## 2. Diagram conventions

All diagrams are Mermaid. Unless a diagram's own legend says otherwise:

| Notation | Meaning |
|---|---|
| Solid arrow `──▶` | Synchronous call (caller waits for the result) |
| Dashed arrow `┄┄▶` | Asynchronous: `LISTEN/NOTIFY`, SSE, Web Push, timers, background tasks, broker or market polls |
| Thick arrow `══▶` | Durable write inside a PostgreSQL transaction |
| Cylinder node | Persisted in PostgreSQL |
| Blue nodes | Deterministic code |
| Purple nodes | Model (fast-path rules are blue; Jev, Laya, and the LLM chain are purple) |
| Orange nodes | Human decision |
| Green nodes | External system |
| Red nodes | Failure, alert, or blocked path |
| Grey dashed nodes | Specified as a production target and not present as files in this repo |

Terminology in the code: *provider* (a Discord author), *lane* `(channel_id, provider_id)`, *provider position*, *signal*, *lot*, *card*, *field source* (literal / context / resolved), *trading mode* (`shadow`, `paper`, `live`).

## 3. Decisions the code has settled

These were open in the original design notes. The running process behaves as follows.

| Topic | What the code does |
|---|---|
| Message delivery | With `DISCORD_TOKEN` set, `DiscordListener` (discord.py gateway) is the live feed: create, edit, delete, and a 30 s heartbeat. `POST /ingest` (HMAC, ±300 s) is the same `inbox.accept()` path for an external Message API or tests. Gap backfill uses Discord channel history when the listener is on. |
| Broker | Alpaca, selected by `broker_adapter` and `broker_kind` (`alpaca-paper` or `alpaca-live`). `trading_mode=shadow` routes orders to `SimulatedBroker` and never calls Alpaca. Tests use `FakeBroker`. |
| Market data | Alpaca REST snapshots (IEX stocks, indicative options by default). Underlying stops poll those snapshots on `alpaca_poll_interval_s` (default 5 s). |
| Position scope | A **lane** is `(channel_id, provider_id)`. The inbox, the worker, and the provider book are all scoped to the lane. `channel_id` is a scope key. |
| Interpretation order | Whole-message rules (`interpret.fast`) first. Otherwise Jev. A Jev **model failure** falls to local Laya, then the optional LLM. A **semantic** doubt goes to the LLM and skips Laya. A **link** doubt goes straight to a review card. |
| One message, many cards | The message graph ends at `notify`. Each pending card gets its own approval graph, thread `card:<card_id>`. |
| Fills | `BrokerEventConsumer` applies Alpaca events and simulated fills. Reconciliation re-syncs orders on its own loop and after fills. |
| Missing quotes | Validation returns a review card (`market_data_unavailable`). Revalidation and manual entry refuse to guess a price. The underlying-stop watcher raises a `market_data` alert when the feed is blind. |
| Ledger history | `provider_position_events`, `lot_events`, and `stop_events` are append-only history next to the current-state rows. |
| Supersession | `apply_event` invalidates a pending entry or add on the same position. `notify` resumes that card's approval graph with `invalidate`. |
| Setups | `watch` and `conditional` update the provider book and create no card and no lot. A later entry on that setup opens it. `tp_hit_notice` is feed-only. |
| Quantity | `decide()` never chooses a size. Entry, add, trim, and exit cards leave `qty` blank for the user. A full close sells the lot quantity. |

## 4. Still outside this repo

| Topic | State |
|---|---|
| Managed Postgres HA, synchronous standby, PITR, private network | Production target in `docs/implementation/F064-deployment-runbooks.md`. Local `compose.yaml` runs a single PostgreSQL 17 on `127.0.0.1`. |
| Docker images, Caddy, `deploy/compose.prod.yaml` | Named in F064. Those files are not in the repo. The process that runs is one uvicorn worker plus the Next.js app. |
| Parallel interpret, serial apply | Not built. A lane worker runs the whole message graph, then releases the slot. |
| Kafka, Redis | Not used. The inbox boundary is `accept` / `next_for_lane` / `mark`. Hot reads stay in process. |
