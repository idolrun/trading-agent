# System Architecture & Design — Discord Signal Copy-Trading Assistant

**Status:** Draft v1.0 · 2026-09-28
**Implements:** `docs/prd.md` (Draft v0.3) and `docs/trd.md` (Draft v0.5). Those two documents are the source of truth. This document visualises and explains them; it does not change them.
**Audience:** engineers building, reviewing, or operating the system.

---

## 1. How to read this document

| # | Document | What it answers |
|---|---|---|
| 1 | [High-level system architecture](01-high-level-architecture.md) | What are the pieces, who talks to whom, where are the trust boundaries? |
| 2 | [Detailed component architecture](02-component-architecture.md) | Which module does what, sync vs async, who owns which table? |
| 3 | [End-to-end data flow](03-end-to-end-data-flow.md) | How does one message become an order, fill and reconciled lot, and where is state persisted? |
| 4 | [Message context & signal correlation](04-context-correlation.md) | How are providers isolated, and how do follow-ups find the right position? |
| 5 | [Signal processing flowchart](05-signal-processing-flowchart.md) | Every decision point, where the model stops and code takes over. |
| 6 | [Sequence diagrams](06-sequence-diagrams.md) | Twelve key workflows, message by message. |
| 7 | [Data model](07-data-model.md) | Entities, relationships, immutable audit vs current state, state machines. |
| 8 | [Deployment architecture](08-deployment.md) | Hosts, network boundaries, external dependencies. |
| 9 | [Failure & recovery architecture](09-failure-recovery.md) | What happens when each dependency fails, and how consistency is restored. |
| 10 | [Architecture summary](10-architecture-summary.md) | Style, source of truth, ordering, concurrency, reliability, LLM vs code, human approval, scaling. |

Traceability tags follow the source documents: `FR-x`, `AC-x`, `[Cxx]` → PRD; `TRD §x` → TRD. **[OD-x]** → an open architectural decision listed in §3 below.

## 2. Diagram conventions

All diagrams are Mermaid. Unless a diagram's own legend says otherwise:

| Notation | Meaning |
|---|---|
| Solid arrow `──▶` | Synchronous call (caller waits for the result) |
| Dashed arrow `┄┄▶` | Asynchronous: `LISTEN/NOTIFY`, SSE, Web Push, timers, background tasks, broker/market streams |
| Thick arrow `══▶` | Durable write inside a PostgreSQL transaction |
| Cylinder node | Persisted in PostgreSQL |
| Blue nodes | Deterministic code |
| Purple nodes | Model (Jev or LLM) — interpretation only |
| Orange nodes | Human decision |
| Green nodes | External system |
| Red nodes | Failure / alert / blocked path |
| Grey dashed nodes | **Not deployed** — future boundary or proposed item, labelled as such |

Terminology is the PRD's: *provider* (a Discord author), *provider position*, *signal*, *lot*, *card*, *field provenance* (literal / context / resolved), *tier*.

## 3. Open architectural decisions register

These are items the PRD/TRD leave undecided or unspecified. Diagrams that touch them mark the relevant element with the OD number. Nothing here is silently assumed; where a diagram must show *something*, the element is labelled **PROPOSED**.

| ID | Decision | Source | Blocking? | Impact on this design |
|---|---|---|---|---|
| OD-1 | Message API delivery semantics: redelivery ≥ 10 min, **or** a history/backfill API, **or** (if neither) a local append-only spool in front of Postgres. Also: webhook vs websocket transport. | PRD §22(a), FR-1d, C91; TRD §2.4, §7.1 | **Yes** | Diagrams show webhook + history API. If neither guarantee exists, a spool component is added in front of `inbox.accept()` (shown greyed in [08](08-deployment.md), [09](09-failure-recovery.md)). |
| OD-2 | Message API capabilities: edit/delete events, heartbeat, per-channel ordering guarantee, stable author ID, snowflake IDs. | PRD §15; TRD §7.1 | **Yes** | Edit-as-correction ([06 §6.4](06-sequence-diagrams.md)) and feed health depend on it. Without edit events, corrections by edit are lost (PRD §21). |
| OD-3 | Broker and account type (Tradier first / IBKR fallback; margin ≥ $25k or cash; PDT counter if applicable). | PRD §22(b); TRD §7.2, §14.1 | **Yes** | Broker is drawn as a generic adapter. OCO/bracket and stop semantics are broker-specific. |
| OD-4 | Market-data source for option quotes and real-time SPX index. TRD recommends the broker's own feed. | PRD §22(c2); TRD §7.3 | **Yes** | Drawn as "broker feed (TBD)". |
| OD-5 | Provider-position scope: per author (default) or per (author, channel). | PRD §6, §22(g); TRD §14.9 | **Yes** (before ledgers) | All diagrams use **per author**; `channel_id` is metadata only. |
| OD-6 | Note guard (hide advisory note containing unvalidated numbers). TRD recommends accept. | PRD §4 [CONFIRM], §22(c) | **Yes** | Drawn as present. |
| OD-7 | Allowed LLM vendors and chain order; DeepSeek first-party API (operated from China) vs US-hosted open weights vs excluded. | TRD §4.8, §14.12–13 | No | Chain drawn as `anthropic → openai → deepseek` (TRD suggested start). |
| OD-8 | Jev as primary interpreter — go/no-go from week-0 bench (≤ 300 ms p95). Fallback: LLM-only interpreter (same graph without `jev_interpret`). | TRD §13 phase 0, §15.6 | Yes (phase 0) | Diagrams show Jev fast path. |
| OD-9 | Managed Postgres provider and how RPO ≈ 0 is achieved (Multi-AZ sync replication vs primary + synchronous standby). | TRD §9, §14.10 | Yes | Drawn as primary + synchronous standby + PITR. |
| OD-10 | **How fills and order-status changes reach the system** (broker push stream vs polling). TRD specifies idempotent submit and status query by client order ID, but not the fill-intake mechanism. | Not specified | No (phase 4) | Drawn as "broker order/fill events (stream or poll — OD-10)". |
| OD-11 | **How one graph run awaits several cards.** One message can hold several signals (FR-6), each producing a card, but the TRD describes a single `interrupt({card_id,…})` per thread. Options: one interrupt per card via parallel branches, or one card run per card. | FR-6; TRD §2.1, §5.1 (gap) | No (phase 3) | Diagrams show the single-card case. |
| OD-12 | **Behaviour when market data is unavailable.** Not specified beyond FR-11 (validation failure → review card). | Not specified | No | [09 §9.7](09-failure-recovery.md) shows a **PROPOSED** policy. |
| OD-13 | **History of current-state rows.** PRD §14 requires "provider positions and their history"; TRD lists `provider_positions`, `lots`, `stops` as current-state tables and no history table. | PRD §14; TRD §8 (gap) | No | [07](07-data-model.md) shows `*_events` history tables as **PROPOSED**. |
| OD-14 | **Supersession trigger mechanics.** TRD lists "superseding message on the same position" as a resume value, but not who fires it. | TRD §5.1 | No | Diagrams show the `cards` module firing `invalidate` after the new message's `apply` commits (**PROPOSED**). |
| OD-15 | Staleness threshold `stale_entry_s` (default 30 s) and catch-up trigger N. | PRD §22(e); TRD §14.11 | No | Parameters only. |
| OD-16 | Paper-stage approach if the current broker has no paper account. | PRD §22(b); TRD §16.3 | Yes (stage 3) | None on runtime architecture; affects number of broker adapters. |
| OD-17 | **Network path between app VM and managed Postgres** (private network / VPC peering / TLS over public endpoint). | Not specified | No | [08](08-deployment.md) draws a private link, labelled **PROPOSED**. |
| OD-18 | **Card behaviour for some message types:** `add` when the user holds no lot for that position; `tp_hit_notice`; `conditional`. PRD lists the types (FR-5) but not their card behaviour. | FR-5, C62 | No | [05](05-signal-processing-flowchart.md) marks these branches with OD-18. |
| OD-19 | *Parallel interpret, serial apply* upgrade for single-provider slow-path bursts. Adopted only if load test 3 fails. | TRD §2.3, §12 | No | Shown as a conditional upgrade path in [10](10-architecture-summary.md). |

Known accepted risk (not a decision): **system-held underlying stops do not fire while the process is down** (PRD §21, C49; TRD §9). The external uptime monitor pages the phone.
