# TRD — Discord Signal Copy-Trading Assistant (Draft v0.6)

**Status:** Draft for review. Implements `docs/prd.md` v0.4. **Synchronized with the codebase on 2026-09-30:** where this document and the code disagreed, the text below now describes the code (see "Changes from v0.5" and §17).
**Scope:** Architecture, tech stack, model choice (Jev + provider-agnostic LLM: Claude, OpenAI, DeepSeek), orchestration (LangGraph / LangChain), decision engine, data model, build order, and as-built status.
**Traceability:** `FR-x`, `AC-x`, `[Cxx]` refer to the PRD. `[VERIFY]` means the claim must be proved in a week-0 spike before we build on it.

**Changes from v0.1:**
- TypeSafe **Jev** is now the primary interpreter. It makes the semantic decisions (§4).
- Claude is the slow path for hard messages and writes the advisory note.
- **LangGraph** orchestrates the pipeline and the human approval step. **LangChain** `create_agent` runs the Claude slow path, with middleware guardrails, tools and memory (§5).
- Appendix A holds the Jev question catalog.

**Changes from v0.2:**
- Calibrated against the user's **real** realized gain/loss history (§16), not synthetic data.
- The decision engine implements PRD C82 (1-contract trims), C83 (TP legs), C84 (missing-underlying proximity), C85 (SPX → SPXW) and C86 (no ticker whitelist) (§6).
- New scenario tests (§12).
- Broker stays **TBD**: the export's source is unconfirmed (§16.3).
- `docs/data/` is git-ignored (C87).

**Changes from v0.3** (Kafka/Redis architecture review; workload of 1,000–2,500 messages per 6–8 h session):
- **Decision:** no Kafka and no Redis now, with boundaries in place so either can be added later (§15.1, §15.2).
- **New §2.4 "Ingest, inbox and sequencing".** It closes gaps found in the review:
  - per-provider ordered processing;
  - out-of-order handling;
  - staleness and catch-up mode;
  - gap backfill;
  - poison messages;
  - model-concurrency limits.
- **Data model:** `inbox`, `signals` and `message_links` tables; a unique key on cards; the identifier model (§8.1).
- **Postgres moves off the app VM** to managed HA with PITR; RPO/RTO targets (§9).
- Latency budget includes queue wait and burst behavior (§2.3). Load and fault tests added (§12). Cost re-estimated (§4.7).
- These changes are adopted in PRD v0.3 (FR-1a–FR-1e, §12 durability and scale, C88–C94, AC-18 to AC-23).

**Changes from v0.4** (LLM provider independence):
- The generative slow path and the note writer no longer depend on Claude. They run through a provider-agnostic **LLM layer** (§4.8) that supports **Anthropic (Claude), OpenAI and DeepSeek**, and optionally any OpenAI-compatible endpoint or Bedrock.
- A model registry in config sets a primary model and an ordered fallback chain for each role. Structured output, prompt caching and request parameters adapt to each provider.
- Every model in the chain must pass the same replay gate.
- Names are made generic: `interpret.claude` → `interpret.llm`, `claude_extract` → `llm_extract`, `t_claude` → `t_llm`.

**Changes from v0.5** (as built; user-confirmed decisions are marked ✔, and the register is `docs/implementation/README.md` §10):
- **Stack (§3):** Python 3.13; **SQLModel + SQLAlchemy 2 async + Alembic + psycopg 3** instead of asyncpg + numbered SQL ✔; **PostgreSQL 17** ✔; the dashboard is a **Next.js 16 app over a FastAPI JSON API**, not Jinja + HTMX; `uv` and `pnpm`.
- **Graphs (§2.1, §5.1):** the message graph ends at `apply → notify`. Each card gets its **own approval graph**, one LangGraph thread per card (`card:<card_id>`) ✔.
- **Discord source (§2.4, §7.1):** a `discord_source` listener (user-account token) feeds the same `inbox.accept()` as the webhook.
- **Auth (§10):** email + password allowlist and an append-only `audit_log` replace passkeys and `logins`.
- **Data model (§8):** the authoritative schema is `docs/implementation/database-schema.md`. It adds users, sessions, audit log, push subscriptions, system flags, `*_events` history tables and `stage_timings`. Migrations are Alembic.
- **Decisions confirmed in the build (§17):** shadow-mode fills, off-hours clock, OCO bracket linkage, exit floor, underlying-stop trigger, kill-switch and flatten scope.
- **Card states (§6):** `disabled` is a terminal card state.
- **Testing (§12):** suites and load results as built. **§17 (new):** implementation status and deviations.

---

## 1. Design principles

1. **Models interpret, code decides.** Jev and the LLM answer "what did the provider mean?". Code answers "what do we do, how much, at what price, and is it allowed?" (PRD §4, §9).
2. **The fast path uses no generative model.** Code finds candidate values and Jev chooses among them. The LLM (any configured provider, §4.8) runs only when Jev is unsure, or for the advisory note.
3. **Model confidence can only downgrade a card, never upgrade it** (C31). High confidence skips nothing that code would otherwise check.
4. **Domain tables are the source of truth.** LangGraph checkpoints hold orchestration state (where a run paused). Ledgers, cards, orders and audit rows live in our own Postgres tables.
5. **Every order goes through one function.** `submit_order()` runs the FR-20 invariants, writes the order intent, then calls the broker. Neither graph nodes nor models call the broker directly.
6. **One process, one database.** A single Python asyncio service plus PostgreSQL.
   - No Kafka and no Redis (§15.1, §15.2).
   - Postgres runs on its own highly available host, not beside the app (§9).
7. **Transport, correlation and state are separate layers.**
   - **Transport** is the inbox and sequencer (§2.4).
   - **Correlation** is reply-to → provider book → Jev `links_to` → review (§4, §8.1).
   - **Authoritative state** is Postgres transactions plus broker reconciliation.
   - No queue or cache resolves signal context.

## 2. Architecture

### 2.1 Per-message graph and per-card approval graph (LangGraph `StateGraph`)

```
Discord listener ──gateway events──▶ inbox.accept()                    §2.4
Message API ──webhook──▶ inbox.accept()  (commit, then 200)            §2.4
gap detector ──backfill (Discord history / history port)──▶ inbox.accept()
                              │ NOTIFY
                              ▼
dispatcher: per-provider worker takes the next event in snowflake order
            + staleness check                                          §2.4
  ═══════════ provider slot HELD from here ═══════════
START
  │
  ▼
load_context ── code: message, reply parent, author's recent msgs, @mentioned msgs,
  │             provider book (author's open positions), provider profile (long-term store)
  ▼
find_candidates ── code: regex over-find tickers, numbers, expiry tokens, fraction words
  │
  ▼
jev_interpret ── ONE Jev request, all Appendix A questions in parallel
  │
  ▼
route (conditional edge, code thresholds)
  ├── chatter (confident) ─────────────────────────────▶ feed_only ──▶ END
  ├── confident & complete ──▶ assemble_signal
  └── low confidence / multi-signal / missing candidate ──▶ llm_extract (create_agent subgraph, §4.8)
                                                              │ (timebox; on failure → raw review card)
                                                              ▼
                                                        assemble_signal
  ▼
validate ── code: chain + quote identity, scale normalization, adjacent-strike, date resolution
  ▼
decide ── code: pure function → proposed action, qty, tier, risk limits
  ▼
apply ── ONE Postgres transaction: signals + message_links + provider ledger + card
  │       (card unique on (signal_id, action)); inbox status = done
  ▼
notify ── SSE + Web Push; spawns note_writer (async, off critical path)
  ═══════════ provider slot RELEASED here (never held across interrupt) ═══════════
  ▼
END  (message graph; thread msg:<message_id>:<event_type>:<event_seq>:<attempt>)

Approval graph — one per card, started by apply/notify (thread card:<card_id>, OD-11):
await_approval ── langgraph interrupt(); resumed by: user approve/edit/reject, expiry timer,
  │                superseding message, or crash-recovery (expire)
  ▼
revalidate ── code: hours, validity, quote + freshness (`quote_max_age_s`), price band, supersession
  │          (later trim/stop/exit/correction, delete or edit of the card's message), broker account,
  │          working orders, positions and risk limits, exit lot qty + broker position (F066)
  ▼
submit_order ── code: FR-20 invariants → broker (idempotent, see §5.6)
  ▼
END  (fills, stops, reprice loops continue in the execution module)
```

One message can produce several cards (one per signal and action), so the approval step is a separate graph per card. This amends v0.5's single per-message graph with a mid-graph `interrupt()`.

Everything outside the graph stays plain async code: ingest, market-data streams, execution loops (re-pricing, stop watcher), timers, reconciliation, web.

### 2.2 Modules

| Module | Responsibility | PRD |
|---|---|---|
| `ingest` | Webhook, signature check, provisional payload adapter, feed-health watchdog, gap detector + backfill. It writes only through `inbox.accept()`. | FR-1–4 |
| `discord_source` | Discord gateway listener (`discord.py-self`, user-account token): maps create/edit/delete events to the provisional payload, ignores bot authors, appends embed text, backfills gaps from channel history, reports status. Same path as the webhook (C95). | FR-1, FR-1d |
| `messageapi` | Port + fake for the user's Message API (history/backfill); the real adapter is not built (F050 blocked). | §7.1 |
| `inbox` | `accept()`, `next_for_provider()`, `mark()`. The only transport boundary; Postgres-backed (§2.4). | FR-1 |
| `dispatcher` | Per-provider workers, snowflake ordering, staleness check, model semaphores, poison handling. Starts one graph run per event. | §2.4 |
| `graph` | The message graph, the per-card approval graph and the Postgres checkpointer/store. | — |
| `pipeline` | `plan` (validate + decide), `apply` (the one transaction), off-hours release. | FR-11–19, §7.7 |
| `interpret.candidates` | Regex candidate finders (tickers, numbers, expiries, fraction words). | FR-7, FR-12 |
| `interpret.jev` | Builds Jev state + questions (Appendix A), calls Jev, maps answers to fields with provenance. | FR-5–7, FR-10 |
| `interpret.llm` | `create_agent` slow path: read-only tools, middleware guardrails, structured output. Note writer. Provider-agnostic; the model comes from the registry (§4.8). | FR-6, FR-9, C80 |
| `llm` | Model registry, provider capability table, fallback chain with a shared deadline, usage/cost normalization (§4.8). | FR-9, C36, C45 |
| `validate` | Chain/quote identity, normalization, adjacent strike, dates. | FR-11–15 |
| `ledger` | Provider-position ledger and user-lot ledger. | FR-16 |
| `decide` | Pure decision functions. | FR-17–19 |
| `cards` | Card state machine, validity, notifications. | §7.1–7.3 |
| `execution` | `submit_order()`, invariants, pricing, re-pricing, brackets, stop restore. | FR-20–23 |
| `broker`, `marketdata` | Thin adapters. | §15 |
| `timers` | Persisted deadlines (card expiry, cutoffs, off-hours release). | 7.6, 7.7 |
| `reconcile` | Startup and periodic broker reconciliation. | §11 |
| `api`, `notify` | FastAPI JSON API under `/api/v1` (OpenAPI is the frontend contract), SSE event bus, Web Push. | FR-25–27 |
| `auth` | Email + password users, sessions, step-up, rate limit, audit log, user CLI. | §13 |
| `manual` | Manual exit/stop/entry, kill switch, flatten, provider-position corrections, provider profile. | 7.9 |
| `runtime_config`, `system`, `alerts` | Database-backed tunables with audit; system flags and heartbeat; alerts. | §18 |
| `observability` | Stage timings, health metrics, spend check, checkpoint pruning. | §16 |
| `evalkit` | Labels, replay runner and gate metrics. | §17 |
| `frontend/` (separate app) | Next.js 16 dashboard (App Router, TanStack Query, shadcn/ui, PWA) generated against the OpenAPI snapshot `frontend/openapi.json`. | FR-25 |

### 2.3 Latency budget (p95 ≤ 5 s)

| Stage | Fast path (Jev) | Slow path (LLM) |
|---|---|---|
| Ingest + inbox commit | 20 ms | 20 ms |
| **Queue wait** (per-provider + model semaphore) | **target p95 ≤ 1 s** | **target p95 ≤ 1 s** |
| Context + candidates | 130 ms | 130 ms |
| Jev call | **[VERIFY] target ≤ 300 ms** | 300 ms (it ran first) |
| LLM agent (≤ 2 tool calls), including any fallback attempts | — | ≤ 3,000 ms (hard timebox shared by the whole chain) |
| Validate + decide + apply + push | 550 ms | 550 ms |
| **Total (queue wait at 0)** | **~1 s** | **~4 s** |

The p95 is over all messages, **including bursts**. The slow-path share matters, and so does queue wait.

**Workload** (PRD §12):
- 1,000–2,500 messages per 6–8 h session, averaging about 0.1 message/s.
- Design bursts:
  - ≥ 50 messages in 10 s across providers;
  - ≥ 5 messages in 5 s from one provider (entry + stop + targets posted together).

**Burst analysis:**
- **Cross-provider bursts are cheap.** Providers run in parallel, so the limits are the model semaphores and vendor rate limits (429s), not Postgres.
- **Single-provider bursts on the slow path are the risk.** Serial processing means the third message waits about 8 s, which breaches p95.
- **Upgrade path, only if load tests show a breach (§12):**
  - *parallel interpret, serial apply*: interpret a provider's queued messages concurrently, with the earlier in-flight messages' text included in `recent_author_messages`;
  - then run `apply` strictly in snowflake order;
  - if an earlier message did not create the position a later one linked to, re-interpret the later one.
- **Targets:** p95 ≤ 5 s, p99 ≤ 8 s (PRD §12, AC-22).

### 2.4 Ingest, inbox and sequencing

**Sources.** Two producers call `inbox.accept()`: the Discord listener (`discord_source`) and the webhook. Both map to the same provisional payload (`ingest/adapter.py`), so the inbox, dispatcher and graph do not know which delivered a message.
- The listener is off when `DISCORD_TOKEN` is unset. Watched channels are the runtime-config `channels` list (the dispatcher enforces it too).
- It uses the Discord snowflake for post time, Discord author IDs as `provider_id`, and `reference.message_id` for reply-to.
- Edits and deletes become `message.update` / `message.delete` events. Updates that are not text edits (embed unfurls, pins) are dropped.
- Gap backfill goes through the gap detector using a `MessageHistory` port implemented over the Discord channel history (`after` the last seen snowflake). It recovers **created** messages only: edits and deletes made while the listener was down are not replayed (PRD §21).
- A 30 s listener heartbeat keeps feed health alive while channels are quiet.
- The account is a user account (a "selfbot"), which Discord's terms forbid (PRD open item (h)).

**Transport boundary (the only path in).** The `inbox` module exposes three functions:

```python
accept(envelope) -> bool                         # durable + idempotent; False if duplicate
next_for_provider(provider_id) -> Envelope|None  # oldest unprocessed, by snowflake time
mark(event_id, status, error=None)               # received | processing | done | failed | stale

Envelope = {event_id: (message_id, event_type, event_seq), provider_id, channel_id,
            posted_at (snowflake time), received_at, reply_to_id, payload}
```

Today this is a Postgres table plus `LISTEN/NOTIFY`. A Kafka consumer could implement the same three functions later (key = `provider_id`) with no downstream change (§15.1).

**Webhook contract:**
- Verify the HMAC.
- `INSERT … ON CONFLICT DO NOTHING`, then `COMMIT`, then `NOTIFY`.
- Return `200` only after the commit. If Postgres is unavailable, return `503` so the Message API retries.

**Receipt is exactly once only if the Message API redelivers until it gets a 2xx.** This is [VERIFY] in week 0, and it is a blocking requirement:
- at-least-once delivery with retries for ≥ 10 minutes, **or**
- a history/backfill API.

Without either, a Postgres outage loses messages (PRD FR-1d, C91).

**Gap detector:**
- Keep the last seen snowflake per channel.
- On startup, on reconnect, and every 60 s during market hours, fetch newer messages through the history API and `accept()` them. Duplicates are no-ops.
- Every backfilled event is tagged `backfilled`.

**Per-provider sequencing** (PRD FR-1a, C88; out-of-order FR-1b, C89):
- One asyncio worker per active `provider_id`.
- Workers take events in **snowflake (post-time) order**, not arrival order.
- Different providers run in parallel.
- The provider slot is held from `load_context` through `apply`/`notify`, and **released before `await_approval`**, so a pending approval never blocks the provider's next message.
- If the service later runs as several processes, the slot becomes a **session-level** Postgres advisory lock (`pg_try_advisory_lock(hash(provider_id))`), held on a dedicated connection and released automatically if that connection drops.
  - Not a transaction-scoped lock: the slot spans model calls lasting seconds, and a transaction must not stay open that long.
  - Never a Redis lock (§15.2).
  - Inside `apply`, the provider's ledger rows are still locked with `SELECT … FOR UPDATE`.

**Why the slot matters (race in v0.3):**
- If A1 "Bought GOOGL 357.5C @ 1.67" and A3 "Take half off @ 2.10" are processed at the same time, A3 reads an empty provider book.
- A3 is then linked `none` and goes feed-only, so the exit is silently lost.
- Serializing by provider prevents this.

**Out-of-order arrival:**
- If an event arrives with a snowflake **older** than one already applied for the same provider:
  - apply it;
  - flag it `late`;
  - re-run linking for that provider's messages applied after it that were linked `none` or `ambiguous`, and send any changed result to a review card.
- Count these events. Add a per-provider reorder buffer (≤ 300 ms) only if the count shows it is needed.

**Staleness and catch-up mode** (PRD FR-1c, C90):
- `age = processing_start − posted_at`. The C22 validity clock also starts at `posted_at`, **not** at card creation.
- If `age > stale_entry_s` (config, default 30 s, tuned in shadow): entries and adds are applied to the provider ledger but produce **no approvable card**. They appear in the feed with the label "stale".
- Exits, trims, stops and corrections are still carded, with a **"late"** badge, because missing an exit is worse than acting late.
- **Catch-up mode** turns on automatically when the backlog is more than N events or the oldest unprocessed event is more than `stale_entry_s` old. It raises an alert and clears when the backlog drains.

**Poison messages** (PRD FR-1e, C92):
- After K = 3 failed attempts: status `failed`, a raw review card plus an alert.
- The provider's queue **continues**, so one bad message does not halt a provider.
- A message is never skipped silently (G5).

**Model concurrency:**
- Global semaphores: one for Jev (`N_jev`) and **one per LLM provider** (`N_anthropic`, `N_openai`, `N_deepseek`). Initial values are set from each vendor's rate limits [VERIFY].
- A 429 waits for its backoff inside the semaphore, and the wait is counted as queue wait.

**Watchdog:** the dispatcher writes a heartbeat row every 5 s. `/health` fails if it is older than 15 s, which catches an event loop that is blocked while the process is still alive.

## 3. Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.13, asyncio; `uv` for dependencies | `Decimal` for all money. `ruff` and strict `mypy` gate every change. |
| **Semantic decisions** | **TypeSafe Jev** via `typesafe-sdk` (`AsyncTypeSafeClient`), model `jev-latest` pinned to a dated version once chosen | Choice / Score / Noul (§4, Appendix A). Built; **on by default** (`jev.enabled = true`, model `jev-latest`; needs `TYPESAFE_API_KEY`, without it every message goes to the fallback). Anything Jev does not settle goes to Laya-MLX (local fallback, strict gate, off Apple Silicon it fails to load and alerts), then the optional LLM, then a review card. Bench still pending (PRD (i)). |
| **Generative slow path + note** | **Provider-agnostic** through LangChain `init_chat_model("<provider>:<model>")`. Supported: **Anthropic** (`langchain-anthropic`), **OpenAI** (`langchain-openai`), **DeepSeek** (`langchain-deepseek`). Optional: an OpenAI-compatible endpoint (`ChatOpenAI(base_url=…)`), Bedrock (`langchain-aws`, **not implemented**: the registry accepts `anthropic`, `openai`, `deepseek`, `openai_compat`) | Model registry and fallback chain in config (§4.8). |
| **Orchestration** | **LangGraph** `StateGraph`, `interrupt()` / `Command(resume=…)` | §5. |
| **Agent harness** | **LangChain** `create_agent` + middleware | LLM slow path only. |
| Short-term memory | LangGraph checkpointer: `AsyncPostgresSaver` (same Postgres) | Thread per message run. |
| Long-term memory | LangGraph store: `PostgresStore` (same Postgres) | Provider profiles (§5.3). |
| API | FastAPI, JSON under `/api/v1`, SSE for live updates, Pydantic camelCase schemas; OpenAPI snapshot committed | Discord IDs are serialized as strings. |
| Frontend | **Next.js 16** (App Router), React 19, TanStack Query, shadcn/ui + Tailwind 4, `openapi-typescript`, PWA; `pnpm` | Same origin through Next `rewrites`, so cookies stay `SameSite=Strict`. Tests: Vitest + MSW; E2E: Playwright. |
| Discord | `discord.py-self` listener in the backend | User-account token (ToS risk, PRD (h)). |
| Push | Web Push (VAPID), `pywebpush` | iOS needs the PWA added to the Home Screen. |
| Auth | Email + password (stdlib scrypt), allowlist `users` table, server-side sessions, append-only `audit_log` | §10. |
| Database | **PostgreSQL 17**; **SQLModel + SQLAlchemy 2 async + psycopg 3**; **Alembic** migrations | psycopg 3 is also LangGraph's Postgres driver, so there is one driver. Roles `trading_owner` (migrations) and `trading_app` (DML only). LangGraph tables live in their own schema `langgraph`. |
| Time | `zoneinfo` + `exchange_calendars` (XNYS) | |
| Tests | `pytest` + `pytest-asyncio` + `hypothesis` against real PostgreSQL (never SQLite); markers `integration`, `graph`, `load`, `live` | §12. |
| Tracing | Postgres `model_calls` (required, 7-year retention); LangSmith optional for dev | |
| Hosting | **Target (not built, F064):** app on one US-East VM, Docker Compose (app + Caddy), external uptime monitor. **Postgres: managed HA in the same region** (e.g. RDS/Cloud SQL Multi-AZ, or a primary plus a synchronous standby), with PITR. **Today:** local Docker Compose runs PostgreSQL 17 only; the app and frontend run from `uv` and `pnpm`. | Postgres off the app VM (§9). |
| Queue / transport | Postgres `inbox` + `LISTEN/NOTIFY` | Boundary for a later Kafka (§2.4, §15.1). |
| Caches | In-process only: live quotes (broker stream), option chains (TTL) | Boundary for a later Redis (§15.2). |

**Not used, on purpose:** Kafka (§15.1), Redis (§15.2), Kubernetes, vector DB, LangChain multi-agent supervisors (§5.7).

## 4. Interpretation: Jev fast path + LLM slow path

### 4.1 Why this split works
- **Jev chooses, it never writes.** Its docs: "System One models do not write replies, produce code, or generate explanations". That makes it fit PRD rules we already have:
  - **No invented numbers (C78, FR-7).** Code finds every number in the message and Jev only picks which one is the strike, price or stop. The value is copied from the text, so it cannot invent or transpose a digit (TypeSafe's value-extraction pattern).
  - **Calibrated probabilities.** Every answer carries a probability, so code can route on it instead of trusting a model's self-report.
  - **Parallel, isolated questions.** All questions run in one request against the same state, so adding questions barely changes latency.
- **The LLM covers what Jev cannot do** (any provider in the registry, §4.8):
  - messages where the needed value is not among the candidates (e.g. only implied by context);
  - several signals in one message that need grouping (FR-6);
  - the advisory note (C80).

### 4.2 Jev state (what Jev sees)

```json
{
  "now_et": "2026-09-28T10:04:12-04:00",
  "message": {
    "id": "m_981", "author": "trader_x", "channel": "0dte-calls",
    "text": "spy 770c .95 lotto, sl 769 spy",
    "reply_to": null
  },
  "recent_author_messages": [
    {"id": "m_975", "at": "10:01", "text": "watching spy 770c"}
  ],
  "mentioned_messages": [],
  "provider_book": [
    {"id": "P12", "summary": "QQQ 480P exp 2026-09-28, entered 1.20 at 09:48, trimmed 50%"}
  ],
  "provider_profile": {
    "notes": ["writes cents without a leading zero ('.95' = $0.95)", "'lotto' = lotto tier"]
  },
  "candidates": {
    "tickers": [{"id": "t1", "text": "spy", "pos": 0}, {"id": "t2", "text": "spy", "pos": 26}],
    "numbers": [
      {"id": "n1", "text": "770c", "pos": 4},
      {"id": "n2", "text": ".95",  "pos": 9},
      {"id": "n3", "text": "769",  "pos": 22}
    ],
    "expiry_tokens": [],
    "fraction_words": []
  }
}
```

**Provider context only.** The state contains the provider's messages and the **provider's** book (allowed by C71). It does **not** contain the user's lots, balances or P&L (FR-8). See §4.5 for the portfolio questions.

### 4.3 Questions
The full catalog is in Appendix A. In summary:
- **Signal context:** message type, instrument kind, number roles (one Choice per candidate number), ticker, call/put, expiry, size tier, exit fraction, stop basis, conditions, correction target.
- **Provider-portfolio context:** which provider position this refers to, whether it is an add, strength of exit intent (Score), breakeven stop.

### 4.4 Routing rules (in `route`, code)

| Condition | Action |
|---|---|
| `msg_type = chatter`, and `commentary`/`watch` types, with confidence ≥ T_chat | Feed only. |
| Any required field whose Choice is `none`/`not_stated`, or confidence < T_field | Slow path (LLM). |
| `signal_count = multiple` | Slow path. |
| `links_to = ambiguous`, or confidence < T_link | Review card (C31). No model upgrades this. |
| Everything confident and complete | `assemble_signal`: fields are **literal** (a span chosen by Jev) or **resolved** (dates by code). |
| Slow path fails or exceeds its timebox | Raw review card + "parser degraded" alert (FR-9). |

The thresholds (`T_chat`, `T_field`, `T_link`) are config values, tuned on the replay dev set to keep actionable recall ≥ 98% (PRD §17). Start conservative: T_chat = 0.9, T_field = 0.8, T_link = 0.8.

### 4.5 Portfolio context: what Jev does and does not get
- **Provider portfolio (allowed):** the provider book goes into Jev state, which is how `links_to` resolves "trim half" to the right provider position.
- **User portfolio (not sent):** every user-portfolio decision is exact arithmetic or a lookup, so code does it faster and exactly:

| Decision | Done by |
|---|---|
| Does the user hold a lot for the linked provider position? | Code: lookup by `provider_position_id`. |
| Sell quantity = fraction × lot | Code: `floor(fraction × lot_qty)` (C51). A result of 0 gives a blank qty + "lot too small to trim" (C82). |
| Opposite-direction warning | Code: same underlying, opposite right (C74). |
| Risk limits, exposure, daily loss | Code (FR-18). |
| Breakeven price choice | Code shows both prices; the user picks (C29). |

Sending user holdings to Jev would break FR-8/C80. It would also let holdings sway how a message is read (the same message must mean the same thing whatever the user holds). If you still want it, it needs a PRD change first.

### 4.6 LLM slow path
- `create_agent(model=registry.model("extract", attempt), tools=[…read-only…], response_format=registry.response_format(ParsedSignals, provider), middleware=[…])`. §4.8 covers the registry and §5 the middleware.
- It gets the same state as Jev plus Jev's answers as hints. Its output still goes through literal-span verification (FR-7).
- The note writer is a separate single call made after the card is shown. Its output passes the note guard.

### 4.7 Cost estimate
These estimates use the updated workload: up to 2,500 messages per session, 21 sessions a month.
- **Jev:** $0.042 per MTok input, output free (TypeSafe cookbook, Sept 2026). Worst case, if the ~2k-token state is billed once per question (20 questions): 40k tokens × 2,500 msgs ≈ $4.2/day ≈ **$90/month**. It is likely much less [VERIFY billing model].
- **LLM:** a 20% slow-path share (~500 calls/day) plus notes on ~150 actionable messages a day. With Claude Sonnet 5 as the primary, this is ≈ **$60–90/month**. OpenAI and DeepSeek costs are computed from the registry's price table and measured in the week-0 bench; DeepSeek is expected to be cheaper [VERIFY current prices]. Fallback calls are rare, so they add little.
- **Total:** ≈ $60–180/month depending on Jev billing. Raise the proposed ceiling to **$200/month** (§14).

### 4.8 LLM provider layer (Claude, OpenAI, DeepSeek)

**Goal:** any LLM role can run on any supported provider, chosen by config. No provider-specific code exists outside the `llm` module. Supported providers:
- **Anthropic (Claude)**, **OpenAI** and **DeepSeek**, all through LangChain `init_chat_model("<provider>:<model>")`;
- optionally, any **OpenAI-compatible endpoint** (e.g. DeepSeek open weights on a US host) through `ChatOpenAI(base_url=…)`, and **Bedrock**.

**Model registry** (config, every change audited in `config_changes`):

```yaml
llm:
  roles:
    extract:                 # slow path: structured output + read-only tools
      deadline_ms: 3000      # one deadline shared by every attempt in the chain
      chain:
        - {provider: anthropic, model: claude-sonnet-5,  prompt: extract@v3, gate_run: <id>}
        - {provider: openai,    model: <openai-model>,   prompt: extract@v3, gate_run: <id>}
        - {provider: deepseek,  model: <deepseek-model>, prompt: extract@v3, gate_run: <id>}
    note:                    # advisory note, off the critical path
      deadline_ms: 4000
      chain: [...]
    prelabel:                # offline labeling (PRD C64); batch-friendly, no deadline
      chain: [...]
  prices:                    # per provider:model, used for cost in model_calls
    "anthropic:claude-sonnet-5": {input_per_mtok: 2.00, output_per_mtok: 10.00, cached_input_per_mtok: 0.20}   # cached rate [VERIFY]
  params:                    # passed through to the chat-model constructor, per provider:model
    "anthropic:claude-sonnet-5": {...}
```

- The OpenAI and DeepSeek model IDs are placeholders. They are chosen by the week-0 LLM bench [VERIFY current model names, prices, limits].
- The chain order is whatever passes the gate with the best latency and cost. Claude first is only the starting assumption.

**Provider capability table** (drives code paths; [VERIFY] each cell in the bench):

| Capability | Anthropic | OpenAI | DeepSeek |
|---|---|---|---|
| Structured output | Native (`ProviderStrategy`) | Native (`ProviderStrategy`) | `ToolStrategy` (tool calling) or JSON mode [VERIFY] |
| Tool calling | Yes | Yes | Yes for chat models [VERIFY]; reasoning variants may not support tools |
| Prompt caching | Explicit cache breakpoints | Automatic prefix caching | Automatic prefix caching [VERIFY] |
| Sampling params (`temperature`) | Rejected by newest models; don't send | Model-dependent | Accepted |
| Reasoning control | `effort` | Reasoning effort on reasoning models | Model choice (chat vs reasoning) |
| Data handling | Vendor terms | Vendor terms | **Needs a user decision (§14):** the first-party API is operated from China [VERIFY terms]. The alternative is open weights on a US-hosted OpenAI-compatible endpoint. |

**Rules:**
1. **One output contract.** Every provider returns the same pydantic `ParsedSignals`. Literal-span verification (FR-7), the scope guard (FR-8) and the note guard (C80) run identically after every provider, so no provider can weaken a guarantee.
2. **Structured-output strategy per provider.** `registry.response_format()` picks `ProviderStrategy` or `ToolStrategy` from the capability table. Output that fails the schema counts as a failed attempt.
3. **Fallback inside the `llm_extract` node, not in middleware.**
   - Try the chain in order. Each attempt gets `min(remaining deadline, per-attempt cap)`.
   - **Falls back on:** timeout, 429/5xx, connection error, schema-invalid output, empty output or refusal.
   - **Does not fall back on content rejections** (a literal span missing from the text, or a failed chain/quote check). Those go to a review card. Re-asking other models until one passes would favor a hallucination that happens to match. This is the "no answer shopping" rule.
   - Deadline exceeded or chain exhausted: raw review card + "parser degraded" alert (FR-9).
4. **No mixing within a message.** One message's extraction comes from exactly one model call. Outputs are never merged across providers.
5. **Gate per model** (PRD C45). Each `(provider, model, prompt version)` must pass the replay gate on its own, and its `gate_run` ID is recorded in the registry. Config validation rejects any live chain entry without a passing gate run.
   - The chain must include **at least two independent vendors**. The same model on Bedrock does not count as a second vendor.
6. **Prompts.** One shared prompt per role. A per-provider override is allowed only if the gate requires it. The prompt version is stored in `model_calls`.
7. **Caching-friendly prompts.** The static system prompt and few-shot examples come first; volatile context comes last. The prefix is byte-stable (no timestamps or unsorted JSON in it), so all three providers' caches can hit.
8. **Normalized usage.** Token counts come from LangChain `usage_metadata` (input, output, cached). Cost is computed from the `prices` table and written per call to `model_calls`.
9. **Per-provider isolation.**
   - Each provider has its own semaphore (§2.4) and a circuit breaker: after 5 consecutive failures within 60 s it is skipped for 30 s, then probed half-open. This stops a dead provider from eating the deadline.
   - A missing API key disables that provider at startup and raises an alert.
10. **Same context everywhere.** The C71-scoped state is identical for every provider. User portfolio data is never sent to any of them (FR-8).

## 5. Orchestration and agent features (LangGraph + LangChain)

### 5.1 Graph and human-in-the-loop (PRD G3, §9)
- The per-message pipeline (§2.1) is a LangGraph `StateGraph`, compiled with `AsyncPostgresSaver`. `thread_id = "msg:<message_id>:<event_type>:<event_seq>:<attempt>"`. It ends at `apply → notify`.
- **Approval** is a second graph, **one thread per card** (`thread_id = "card:<card_id>"`), because one message can carry several signals and so several cards. Its `await_approval` node calls `interrupt({card_id, …})`. Resume paths:

| Trigger | Resume value |
|---|---|
| Dashboard approve / edit / reject | `Command(resume={"decision": "approve", "edits": {...}, "approval_id": ...})` |
| Card expiry timer (C22) | `{"decision": "expire"}` |
| Superseding message on the same position | `{"decision": "invalidate", "by": message_id}` |
| Crash recovery (PRD §11: open cards expire) | `{"decision": "expire", "reason": "restart"}` |

- The approval record is written to the `approvals` table **before** resuming, so AC-2 is checked against domain tables and not against checkpoints.
- **The provider slot (§2.4) is never held during `interrupt()`.** Everything that needs per-provider ordering (context, linking, ledger, card) finishes in `apply` before the run pauses. After it resumes, `revalidate` and `submit_order` do not touch the provider ledger. Fills update lots in the execution module under the lot's own row lock.
- `HumanInTheLoopMiddleware` is **not** used for orders, because order actions are not model tool calls: the model never proposes orders (PRD §4). It is available if a future agent tool needs approval.

### 5.2 Short-term memory
- **The checkpointer** holds graph state across the approval interrupt. That covers minutes for entry cards, and up to the provider's exit for exit cards.
- **No free-running chat history.** Each run rebuilds provider context from Postgres (`load_context`), limited to what C71 allows. That keeps context scope explicit and testable, rather than accumulating in a thread.
- Checkpoints for finished runs are pruned after 30 days by a daily job (`observability`). The audit trail lives in domain tables.

### 5.3 Long-term memory (LangGraph store)
- Namespace `("provider", author_id)`, with keys `lexicon`, `habits` and `aliases`. Examples: "'.95' means $0.95", "'con' prices are per-contract dollars (÷100)", "'spx' means SPXW for 0DTE".
- **Read:** into Jev state (`provider_profile`) and into the LLM prompt through `@dynamic_prompt`.
- **Write:** only by code, and only from user-approved sources: `PUT /api/v1/providers/{id}/profile` (provider-position corrections, C73) and the labeling screen. Each write happens in the same transaction as its audit row in `manual_corrections` (target `provider_profile`, with a reason). **Models never write to the store**, which guards against memory poisoning from provider messages.
- The store is `AsyncPostgresStore` on the shared pool.

### 5.4 Context engineering
- **Model context:** `@dynamic_prompt` injects `now_et`, the provider profile and the C71-scoped context. The system prompt is static and comes first, with volatile content last, so each provider's prefix cache can reuse it. Caching is per provider (§4.8): explicit cache breakpoints for Anthropic, automatic prefix caching for OpenAI and DeepSeek [VERIFY].
- **Tool context:** tools receive `ToolRuntime` with a `context_schema` of `{message_id, author_id}` only.
- **Lifecycle context:** middleware (§5.5) runs before and after each model call.

### 5.5 Guardrails (middleware on the LLM agent, identical for every provider)

| Hook | Guardrail | PRD |
|---|---|---|
| `@before_model` | **Scope guard:** reject (`jump_to: "end"`) if the model request contains any user-portfolio field (`lot`, `balance`, `pnl`, `equity` keys, or known lot IDs). | FR-8 |
| `@after_model` | Structured output is schema-valid (`ProviderStrategy`, pydantic). | §10 |
| `@after_agent` | **Literal-span verification:** every literal field must be an exact substring of the message text, else a hard reject. | FR-7, AC-11 |
| `@after_agent` (note writer) | **Note guard:** any ticker/strike/expiry/price not in the validated fields or sources hides the note and logs it. | C80, AC-13 |
| Fallback chain in the `llm_extract` node (§4.8), not in middleware, so one deadline covers every attempt | Outage handling. | FR-9 |
| Tool-call limit (≤ 2 calls) [VERIFY middleware name] + wall-clock timebox in the graph node | Latency bound. | NFR p95 |

**Trading guardrails stay in code, not middleware.** The FR-20 invariants, risk limits and approval validity live in `submit_order()` / `decide`. Manual orders never pass through the agent, and they must hit the same checks (C42).

### 5.6 Tools (LLM agent, read-only)

| Tool | Returns |
|---|---|
| `get_option_chain(underlying, expiry)` | Strikes and quotes (for identity questions). |
| `get_quote(symbol)` | Bid/ask/last. |
| `get_author_messages(n)` | The author's last n messages (C71 scope, from `runtime.context`). |
| `get_provider_positions()` | The author's provider book. |
| `get_message(message_id)` | One message, only if it is in the C71 set. |

**No write tools.** No tool places, changes or cancels orders, or edits ledgers.

**Idempotency on resume:** LangGraph re-runs an interrupted node from its start. So:
- nodes before `interrupt()` in `await_approval` are side-effect-free;
- `submit_order` derives `client_order_id = uuid5(card_id, action_seq)`, so a re-executed node finds the existing order at the broker and does not duplicate it (AC-7).

### 5.7 Multi-agent
- **Router pattern, implemented deterministically:** Jev's `msg_type` Choice is the router, and conditional edges pick the branch. This adds no LLM call, whereas LangChain's LLM router costs one extra model call.
- **One** LLM agent (the slow path) plus one note-writer call. The provider can differ per role (§4.8).
- No supervisor or sub-agent hierarchy. LangChain's own docs put subagents at ~4 model calls for a simple task, which does not fit a 5 s budget. Revisit only for an offline feature (e.g. a weekly provider-performance analyst).

## 6. Decision engine (code)

```
decide(signal, provider_ledger, user_lots, config, quotes, now_et) -> list[ProposedAction]
```

- **Pure function**, no I/O. Every PRD scenario is a unit test, and every live decision can be replayed.
- **Card tier:** all literal/resolved and valid → normal; any context field → inferred; missing field or ambiguous link → review. Jev/LLM confidence can only lower the tier.
- **Sizing:** `qty = floor(tier_max_usd / (limit_price × 100))` for options; stocks per C76. If `qty = 0` (the tier cannot buy one contract), the result is a **disabled card** with that reason. It is never rounded up.
- **Fractional exits (C51, C82):** `qty = floor(fraction × lot_qty)`. If that is 0 (e.g. "trim half" on a 1-lot, which is 49% of the user's exits), the card has qty = blank and the label "lot too small to trim: exit all or skip", and it is not approvable until the user enters a qty.
- **TP legs (C56, C83):**
  - `legs = min(len(tp_levels), qty)`;
  - split `qty` equally over the legs, then assign the remainder from TP1 onward;
  - so 1 contract → TP1; 2 contracts with 3 levels → TP1, TP2; 5 contracts with 3 levels → 2/2/1.
  - The user can edit this on the card.
- **Symbol map (C85):** before chain lookup, provider shorthand is mapped through the config table (default "SPX" → SPXW). A provider's long-term profile (§5.3) can add aliases, which must be approved by the user.
- **Missing-underlying suggestion (C84):**
  - candidates = underlyings the provider traded in the last N days;
  - suggest those whose live price is within ±P% of the strike (P configurable, default 10%);
  - zero or several matches give no suggestion;
  - always a review card requiring confirmation (C79).
- **Risk limits** (FR-18): daily loss, open positions, exposure. Foreign positions included. A breach produces a disabled card.
- **Incomplete config:** money values have no defaults. Until they are all set, every entry becomes a disabled card "config incomplete".
- **State machines:** Card `pending → approved → submitted | rejected | expired | invalidated`, plus the terminal `disabled` (card type `disabled`); Order `intent → submitted → working → partially_filled → filled | cancelled | rejected | unknown`; Provider position `open → trimmed → closed` (+ `corrected`).
- **Invariants** (FR-20) are checked inside `submit_order()` with a fresh broker position read:
  1. never open a short;
  2. sell qty ≤ that provider's lot;
  3. exits reduce/cancel stops and TPs first, and restore them if the exit ends unfilled.

## 7. External integrations

### 7.1 Message API (blocking for the webhook path; the Discord listener covers the source meanwhile)
- The **Discord listener** (§2.4) is built and needs no spike: it has snowflakes, author IDs, edit/delete events and channel-history backfill. The Message API is now the alternative source, for a user who does not want to run a user-account token. Everything below is still open for it, and the real adapter (F050) is not built.
- HTTPS webhook `POST /ingest` with an HMAC signature (or a stream client if the API is websocket-based). All writes go through `inbox.accept()` (§2.4).
- Idempotent insert: `ON CONFLICT (message_id, event_type, event_seq) DO NOTHING`.
- **Week-0 spike [VERIFY]:**
  - payload, edit/delete events, reply-to, heartbeat, historical export;
  - **delivery semantics:** does it retry on non-2xx, and for how long? Is order guaranteed per channel?
  - **a history API usable for gap backfill**, by channel and after a given message ID;
  - a stable author ID, and Discord snowflake message IDs.
- **Blocking:** at least one of redelivery (≥ 10 min) or a backfill API must exist. Otherwise, add a local append-only spool in front of Postgres, and decide that before building ingest.

### 7.2 Broker (blocking)

| Broker | SPX options | Stops on options | OCO/bracket options | GTC stock stop | Client order ID | Paper | Notes |
|---|---|---|---|---|---|---|---|
| **Tradier** | yes [VERIFY] | yes [VERIFY] | OCO/OTOCO [VERIFY] | yes | `tag` [VERIFY] | sandbox (delayed data) | Simplest REST + streaming. |
| **Interactive Brokers** | yes | yes | OCA / brackets | yes | `orderRef` | yes | Most complete; IB Gateway + re-auth; `ib_async`. |
| tastytrade | yes | [VERIFY] | OTOCO [VERIFY] | yes | [VERIFY] | cert sandbox | Third option. |
| Schwab | yes | yes | yes | yes | — | **no paper** | Fails PRD §15. |
| Alpaca | [VERIFY] | [VERIFY] | [VERIFY] | yes | yes | yes | Likely fails SPX/OCO on options. |

- **Recommendation:** spike Tradier first and fall back to IBKR.
- **Account type:** a margin account under $25k triggers PDT limits. Use margin ≥ $25k or a cash account (settled funds, T+1). If PDT applies, a day-trade counter becomes a required risk limit.

### 7.3 Market data (blocking)
- Use the broker's feed for chains, quotes, underlyings and the SPX index.
- A real-time SPX index entitlement is **required**:
  - it drives system-held underlying stops (C49);
  - SPXW is 12.5% of the user's option trades (§16).
  - Verify only that the chosen broker offers it.
- The optionable-symbol list (C86) comes from the broker/market-data API and is refreshed daily.

### 7.4 Model providers
- **TypeSafe Jev.** Docs publish no latency SLA, rate limits, data retention or regions [VERIFY with vendor]. Errors `429`/`529` are retried once with backoff, then the message goes to the slow path.
- **LLM providers:** **Anthropic, OpenAI and DeepSeek**, and optionally an OpenAI-compatible endpoint or Bedrock. The chain order is config (§4.8). At least two independent vendors must be in the chain (FR-9, C45).
- **If Jev is down**, every message takes the LLM slow path. Latency rises but nothing is lost.
- **If every LLM provider is down**, the result is a raw review card plus a "parser degraded" alert (FR-9).

## 8. Data model (PostgreSQL)

The authoritative schema is [`docs/implementation/database-schema.md`](implementation/database-schema.md) (Alembic migrations `0001`–`0015`). This section lists the tables the design needs; the schema document wins where they differ. The app DB role (`trading_app`) has `INSERT, SELECT` only on append-only tables.

**As-built differences from the lists below:**
- Stage timestamps are **not** columns on `message_events` (that table is append-only). They are `stage_timings` audit rows.
- `logins` is replaced by the append-only `audit_log`, which also records user management and user-initiated trading actions.
- Tables added because a requirement forced them: `users`, `sessions`, `push_subscriptions`, `system_flags`, alert acknowledgements, and `*_events` history tables for the current-state rows (`lot_events`, `provider_position_events`, `stop_events`, …).
- Discord IDs are `bigint` in storage and strings in the API. Signal IDs are deterministic `uuid5` values so a re-run yields the same ID.
- LangGraph checkpoint and store tables live in the separate schema `langgraph`.

**Append-only (audit):**
- `message_events`: PK `(message_id, event_type, event_seq)`; also `provider_id`, `channel_id`, `posted_at`, `reply_to_id`, raw payload, `backfilled` flag.
  - Stage timestamps (`t_received`, `t_dequeued`, `t_jev`, `t_llm`, `t_validated`, `t_card`, `t_pushed`) are kept in `stage_timings`, not here.
- `model_calls`: provider (`typesafe` / `anthropic` / `openai` / `deepseek` / `openai_compat` / `bedrock`), model, role (`extract` / `note` / `prelabel`), attempt number in the chain, request, response, latency, tokens, cost, outcome. Jev calls store every question's full probabilities and confidence.
- `signals`: `signal_id = (message_id, idx)` (FR-6), with fields and provenance: `literal` + chosen candidate ID + span, `context` + source message ID, or `resolved`. This replaces `parsed_signals`.
- `message_links`: `signal_id → provider_position_id`, plus `method` (`reply` | `jev` | `llm` | `user_correction`), `confidence`, `created_at`. A re-link after a correction is a new row; rows are never updated.
- `card_events`, `approvals`, `order_events`, `fills`, `alerts`, `config_changes`, `manual_corrections`, `audit_log`, `eval_labels`, `eval_runs`

**Current state:**
- `inbox`: `event_id` (FK → `message_events`), `provider_id`, `posted_at`, `status` (`received | processing | done | failed | stale`), `attempts`, `last_error`, `flags` (`late`, `backfilled`). Indexed on `(provider_id, status, posted_at)`.
- `provider_positions`, `lots`, `stops`, `timers`, `config`.
- `cards`: **UNIQUE `(signal_id, action)`**, so a re-run after a crash cannot create a second card.
- `orders`: `client_order_id` UNIQUE.
- `dispatcher_heartbeat`.

### 8.1 Identifier model

```
provider_id (Discord author ID) ── key for sequencing, context scope and ledger scope
  └─ message_id (snowflake: unique, time-ordered)
       ├─ event (create | edit | delete, event_seq)  ← idempotency key
       ├─ reply_to_id → message_id (any author; deterministic link input, FR-3)
       └─ signal_id = (message_id, idx)               ← one message can hold several signals
            └─ message_links: signal_id → provider_position_id (method, confidence)
provider_position_id ── created by an entry signal; owned by exactly one provider_id
  └─ lot_id (user holding; FK provider_position_id; foreign lots have none)
       └─ card_id (UNIQUE signal_id + action) → client_order_id = uuid5(card_id, action_seq)
channel_id ── metadata; not a scope key (see §14: position scope)
```

- A link never crosses providers. A reply to another author's message makes that message read-only context. The replying author gets their own provider position.
- **Correlation order:**
  1. `reply_to_id` whose parent is linked to a position of the same provider (deterministic);
  2. the author's provider book plus Jev `links_to` (confidence ≥ T_link);
  3. otherwise a review card listing the candidate positions.
- `signal_id` and `provider_position_id` are **outputs of interpretation**, not transport keys. They cannot be used for partitioning or routing before interpretation.

**LangGraph-managed:** checkpoint tables (`AsyncPostgresSaver`) and store tables (`PostgresStore`). These are not audit data.

**AC-2 query:** every `orders` row has an `approvals` row, or an `auto_reason` from the PRD §9 list.

## 9. Reliability and recovery
- **Order idempotency:** `client_order_id` is deterministic per card action and written before the broker call. On timeout, query by client ID; never retry blindly.
- **Startup sequence:**
  1. block entry cards;
  2. load broker positions and orders;
  3. adopt working orders;
  4. reconcile lots (a mismatch keeps entries blocked);
  5. resume every interrupted graph thread with `expire`;
  6. reset inbox rows in `processing` to `received`;
  7. run the gap detector (backfill);
  8. reload timers;
  9. resubscribe quotes;
  10. start the dispatcher. Catch-up mode applies if a backlog exists.
  11. unblock.
- **Failover without a restart:** on a database connection error the dispatcher holds its workers. When the database answers, it runs `recover_after_failover` once: block entries, reconcile, backfill, run due timers, release entries if clean, resume approvals. A failed event goes back to `received` with retries, never left `processing`. Proven in load test 6 (§12).
- **Shadow mode:** the `SimulatedBroker` is in memory and fills at the live quote, so it is lost on restart. Reconciliation in shadow is skipped and treated as clean, and the database is the truth (proposed, awaiting confirmation: OD-33).
- **Re-execution safety:** after a crash, a message is re-processed from `load_context`.
  - Model calls repeat; they have no side effects.
  - `apply` is one transaction.
  - Cards are unique on `(signal_id, action)`, and `client_order_id` is deterministic.
  - So re-processing produces no duplicate card and no duplicate order.
- **Periodic reconciliation:** every 60 s during market hours, plus after every fill.
- **Database:**
  - **Hosting:** managed Postgres with HA (automatic failover) and PITR, on a separate host from the app. The v0.3 design (Postgres in Docker Compose on the app VM) was unsafe: one disk or VM loss lost the trading state.
  - **RPO** (PRD §12 durability, C93): ≈ 0 for approvals, order intents, fills and ledger rows (synchronous commit to the standby, or managed Multi-AZ); ≤ 5 min for everything else via PITR.
  - **RTO:** ≤ 5 min during market hours.
  - **After a failover or restore:** run the full startup sequence. Reconciliation recovers orders at the broker by `client_order_id` prefix, even if their intent rows were lost.
- **Postgres unavailable:** the webhook returns 503, dispatching stops, and trading halts. This is correct: there is no state to trade against. Protective stops held at the broker keep working. System-held underlying stops do not.
- **Known gap:** system-held underlying stops do not fire while the process is down. The external uptime monitor pages the phone.

## 10. Security
- Email + password login for allowlisted users. Passkeys were removed on 2026-09-29.
  - No self-registration and no public register endpoint. The first user and any recovery come from the console (`python -m app.auth.cli create-user | reset-password | list`); a signed-in user can add, deactivate, reactivate or reset others (`/api/v1/users`).
  - Temporary passwords must be changed at first login (`POST /api/v1/auth/change-password`); every other endpoint answers 403 `password_change_required` until then.
  - Server-side sessions in an HttpOnly, Secure, `SameSite=Strict` cookie: 60 min idle, 12 h absolute, bound to the User-Agent (a change revokes it).
  - State-changing `/api/*` calls must carry `Origin: DASHBOARD_ORIGIN`. Login is rate limited.
  - Step-up = re-entering the password (120 s, single use) for manual entry, flatten and lot adjust. Approving a signal-sourced card does not need it.
  - Append-only `audit_log` (DB grant SELECT + INSERT only). Route handlers call `audit.by(...)` for user-initiated trading actions.
  - `seed-dev-user` creates a fixed local login and refuses to run unless `ENVIRONMENT=development`.
- The Discord account token, like every API key, lives in the env file / secret storage and is never logged.
- `ENVIRONMENT=test` mounts a control plane (`/api/v1/test/*`: clock, market quotes, interpretation fixtures, reset) for the browser E2E suite only. It is hidden from OpenAPI, and reset refuses any database except the app's own. Never set it elsewhere.
- `/ingest`: HMAC + timestamp window.
- API keys (TypeSafe, Anthropic, OpenAI, DeepSeek, AWS if used, broker) live in a root-only env file and are redacted from logs.
- Only provider-side text and context leave the system (FR-8). This is enforced by the Jev state builder's types and by the LLM scope-guard middleware, which is the same for every provider.
- The long-term store is written only by audited code paths (§5.3).

## 11. Observability
Dashboard health page (`GET /api/v1/health/details`), all as SQL over the tables above; `GET /health` is a public liveness check that also fails on a stale dispatcher heartbeat. A daily job checks LLM spend against `llm_monthly_budget_usd` and prunes checkpoints.
- per-stage p50/p95/p99, split by fast and slow path;
- **inbox:**
  - insert p99;
  - queue wait per provider (p95, max);
  - backlog depth;
  - oldest unprocessed age;
  - catch-up mode on/off;
- **counts:** duplicates, `late`, `stale`, `backfilled` and `failed` (poison) events;
- model semaphore saturation and 429 rate;
- Postgres CPU, connections and lock waits;
- dispatcher heartbeat age;
- slow-path share;
- Jev confidence distributions per question;
- fallback rate and spend;
- **per LLM provider and model:** latency p50/p95, error rate by type (timeout / 429 / 5xx / schema), circuit-breaker state, share of slow-path calls served, spend;
- card outcomes;
- order states;
- invariant blocks;
- reconciliation results;
- feed health.

## 12. Testing and evaluation

**As built:** `backend/tests/` has `unit`, `integration` (real PostgreSQL 17, database `trading_test`), `api`, `graph`, `scenarios`, `load` and `live` levels; `frontend/` has Vitest + MSW component tests and a Playwright suite (`pnpm e2e`, its own `trading_e2e` database and ports 8100/3100) covering AC-18, trim on a 1-lot, TP legs, entry invalidation, edit/correction, missing underlying, 0DTE cutoffs, manual-entry step-up, feed silence and password sign-in. `live` tests call real model APIs and are excluded from default runs.

**Load and fault results** (`docs/reviews/load-2026-09-29.md`, model latency **simulated** from seeded lognormals until real bench numbers exist):

| Test | Result |
|---|---|
| 1. Day at 1× and 10× | Pass (p95 ≈ 1.3 s, p99 ≈ 2.0–2.3 s) |
| 1. Day at 50× | **Miss:** p95 5.8 s, inbox insert p99 50.3 ms; sits at the limit on a busy laptop |
| 2. 50 messages in 5 s, 10 providers | **Miss:** per-provider queue wait p95 1.03 s (limit 1 s); end-to-end passes |
| 3. 5 messages in 3 s from one provider, slow path forced | Pass (spread p95 2.7 s; packed p95 4.8 s). The §2.3 upgrade path is **not** needed yet |
| 4. 10-minute Message API outage, then backlog | Pass, zero lost, zero duplicate cards |
| 5. `kill -9` in every graph node, then restart | Pass, 23 of 23 cases, no duplicate card or order |
| 6. Postgres stop/start during a burst | Pass, RTO ≈ 28 s, no lost event, entries blocked until reconciled |

The 50× and test 2 misses must be re-measured with real model latency and on the target hardware before they count.

- **Unit and scenario tests** for `decide`, invariants, validity, re-pricing and reconciliation. Property tests (`hypothesis`) cover no-short and no-oversell.
- **Graph tests:**
  - interrupt/resume for each resume value;
  - crash mid-`submit_order` then resume → no duplicate order (AC-7);
  - scope-guard middleware blocks portfolio fields.
- **Replay harness:**
  - runs the full interpret path;
  - measures field accuracy, actionable recall ≥ 98% and zero wrong contracts on normal cards;
  - reports slow-path share and per-path latency;
  - sweeps T_chat/T_field/T_link on the dev set only, then checks once on the test set.
- **Scenario tests from real trade patterns** (§16):

| Scenario | Expected |
|---|---|
| "trim half" on a 1-contract lot | Blank-qty card with the "lot too small" label; no order until the user enters a qty (AC-15). |
| Entry of 2 contracts with 3 TPs | TP legs TP1 = 1, TP2 = 1; no TP3 (AC-16). |
| Entry of 5 contracts with 3 TPs | Split 2/2/1. |
| Tier $100 at a $2.50 premium | Disabled card: "tier cannot buy one contract". |
| "spx 7775c 0dte" | Resolves to SPXW, today's expiry (AC-17). |
| "1000c" with no ticker, provider recently traded MU near $1,000 | Review card with MU suggested; not approvable without confirmation. |
| "1000c", two underlyings within ±10% | Review card with no suggestion. |
| 0DTE position unanswered at cutoff 1 → cutoff 2 | Close card, then automatic close after stops/TPs are cancelled (AC-6). |
| Ticker outside the top-15 underlyings | Accepted if it is optionable and in the live chain (C86). |

- **Sequencing and context tests:**

| Scenario | Expected |
|---|---|
| A1 "Bought GOOGL 357.5C @ 1.67", B2 "Bought TSLA 400C @ 2.10", A3 "Take half off @ 2.10", B4 "Stop 1.50", delivered back to back | A3 links to A's GOOGL position; B4 links to B's TSLA position; neither context contains the other provider's messages |
| A3 delivered before A1 | Both applied in snowflake order, or A1 is flagged `late` and A3 re-linked to review; no silent feed-only |
| A holds GOOGL and META; "take half off @ 2.10" with low `links_to` confidence | Review card listing both positions |
| B replies "same" to A1 | B gets its own provider position; A's ledger is unchanged |
| Entry processed 45 s after post | Ledger updated; no approvable card; labeled "stale" |
| Exit processed 45 s after post | Exit card with a "late" badge |
| Message fails 3 times | `failed`, raw review card + alert; the provider's next message still processes |
| Duplicate webhook delivery | `accept()` returns False; one card |

- **Load and fault tests** (replay real message-API history when available; synthetic until then):
  1. one full real day at 1×, 10× and 50× speed;
  2. cross-provider burst: 50 messages in 5 s across 10 providers;
  3. single-provider burst: 5 messages in 3 s, with the slow path forced on all of them;
  4. catch-up: a 10-minute Message API outage, then backlog delivery;
  5. kill -9 in each graph node, then restart: no duplicate card or order;
  6. Postgres failover during a burst: no lost accepted event; RTO measured.

  **Pass criteria:** end-to-end p95 ≤ 5 s and p99 ≤ 8 s including bursts; per-provider queue wait p95 ≤ 1 s; inbox insert p99 < 50 ms; zero lost or duplicated effects.
  If only test 3 fails, apply the §2.3 upgrade path (parallel interpret, serial apply). **Do not** add Kafka or Redis for it.
- **LLM provider tests** (§4.8):

| Scenario | Expected |
|---|---|
| The same 50 fixture messages on each configured provider:model | Schema-valid `ParsedSignals` from every provider; field accuracy is reported per model |
| Primary times out | Second vendor answers within the shared deadline; `model_calls` shows attempts 1 and 2 |
| Primary returns schema-invalid output | Falls back to the next model |
| Primary output fails literal-span verification | **No fallback**; review card (no answer shopping) |
| Every provider fails, or the deadline passes | Raw review card + "parser degraded" alert |
| Provider fails 5 times within 60 s | Circuit opens; the provider is skipped for 30 s; a half-open probe follows |
| Live chain entry without a passing `gate_run` | Config validation rejects it at startup |
| Chain with only one vendor | Config validation rejects it |
| Request to any provider containing a user-portfolio field | Blocked by the scope guard |

- **Jev question tuning:** when a question fails, inspect the state, candidates, probabilities and the label, and fix the question wording or the candidate regex. Never lower a threshold without re-running the gate.
- **Shadow mode** (runtime-config `trading_mode = shadow`, the default) runs the graphs end to end. `submit_order()` writes the intent and never calls a broker; a `SimulatedBroker` fills marketable orders at the live quote so lots, exits and stops are exercised (C97). **Paper mode** uses the paper account; **live** uses the real one. Only the fake and simulated brokers exist today.

## 13. Build order

The original plan below was executed as the feature roadmap in `docs/implementation/README.md` (F001–F064). Status is in §17.

| Phase | Deliverable | Exit criterion |
|---|---|---|
| 0. Spikes (1 week) | Message API + export + **delivery semantics and backfill API**; broker paper spike; SPX data; **Jev bench** (latency p50/p95, Appendix A accuracy on 100 labeled messages, billing, rate limits); **LLM bench** (latency, schema validity, and field accuracy on the same labeled set for each candidate Claude / OpenAI / DeepSeek model); managed Postgres provisioned | §22 blockers resolved; Jev meets ≤ 300 ms p95 or the design falls back to Claude-first |
| 1. Ingest + inbox + sequencing | Webhook, `inbox`, gap detector, dispatcher (per-provider workers, staleness, poison), heartbeat, feed health | Exactly-once receipt; sequencing tests (§12) pass; load tests 1–2, 4 and 6 pass with stub interpretation |
| 2. Interpret + eval | Candidates, Jev node, routing, `llm` registry + LLM agent + middleware, labeling screen, replay harness | **Replay gate** |
| 3. Graph + ledgers + decide + cards + dashboard | Full graph with `apply` transaction, `message_links`, interrupt approval, checkpointer, store, SSE/push, password auth; load tests 3 and 5 | **Shadow gate** (20 days) |
| 4. Execution | `submit_order()`, brackets, re-pricing, stops, EOD, reconciliation | **Paper gate** (10 days) |
| 5. Live | Minimum sizes | **Live gate** (10 days) |

## 14. Decisions needed from the user
1. **Broker and account type** (Tradier first / IBKR fallback; margin ≥ $25k or cash).
2. **Jev as the primary interpreter**, with the week-0 bench as the go/no-go.
3. **User portfolio stays out of model context** (§4.5). Changing that needs a PRD revision of FR-8/C80.
4. **Note guard:** recommend accept.
5. **LLM + Jev cost ceiling:** suggest $200/month (updated for 2,500 msgs/session, §4.7).
6. **Confirm the export's broker** (§16.3). If your current broker has no paper account, choose the paper-stage approach.
7. **Seed config values** (PRD §18): tiers, max exposure and max daily loss are yours to set. The history only gives observed ranges.
8. **Transactions/order-history export** (PRD §22 f): needed to measure holding time.

9. ~~**Provider position scope**~~ **Resolved 2026-09-28: per author.** `channel_id` is metadata only (PRD C96).
10. **Managed Postgres provider** and the RPO/RTO targets (§9).
11. **Staleness threshold** `stale_entry_s` (default 30 s) and the catch-up trigger (§2.4).
12. **Allowed LLM vendors and chain order** (§4.8):
    - Is **DeepSeek's first-party API** acceptable for provider message text? Its API is operated from China [VERIFY terms].
    - If not, use DeepSeek open weights on a US-hosted OpenAI-compatible endpoint, or leave DeepSeek out.
    - PRD C36 allows cloud LLM providers in general; vendor choice is yours.
13. **Initial chain:** suggested `anthropic → openai → deepseek` for `extract`. The final order comes from the LLM bench. The chain is empty in the shipped config, and a chain entry without a passing gate run is rejected unless `ALLOW_UNGATED_MODELS` is set in development.
14. **Discord listener:** keep the user-account token (ToS risk, throwaway account), or wait for the Message API (PRD open item (h)).

*Resolved in v0.3:* 1-contract trims (C82: blank qty + warning) and the TP split (C83: fill from TP1).

**Adopted in PRD v0.3:**

| TRD item | PRD v0.3 |
|---|---|
| Scale, bursts, p95 including bursts, p99 ≤ 8 s | §12, AC-22 |
| Per-provider ordered processing | FR-1a, C88, AC-18 |
| Out-of-order arrival | FR-1b, C89 |
| Staleness, catch-up, validity from post time | FR-1c, C90, §7.2, AC-19 |
| Redelivery or backfill, gap detection | FR-1d, C91, §15, AC-21 |
| Poison messages | FR-1e, C92, AC-20 |
| RPO/RTO, DB off the app host | §12 durability, §18, C93, AC-23 |
| No Kafka/Redis | §18, C94 |
| Provider-position scope | §6, open item (g) = decision 9 above |
| Cost | §19 |

## 15. Alternatives considered

### 15.1 Kafka: not now

**Decision:** not used now. The system is designed for future adoption behind the `inbox` boundary (§2.4).

**Why not:**
- **Scale:** 2,500 messages / 6.5 h ≈ 0.1 msg/s, and even a 100× burst is ~10/s. Postgres commits thousands of small rows per second.
- **Latency:** dominated by Jev and the LLM. Kafka adds a hop, and rebalances pause consumption for seconds.
- **Ordering:** Kafka keeps order only within a partition, and only in producer (API delivery) order, not Discord post order. Several providers would share a partition, so one provider's slow-path message blocks the others (head-of-line blocking). The per-provider sequencer does this better.
- **Correlation:** none. Kafka cannot know that "take half off" refers to GOOGL 357.5C.
- **Replay:** worse than what we have. `message_events` keeps 7 years of raw payloads, and `evalkit` replays from it. Replaying a live consumer re-triggers side effects.
- **Exactly once:** consumers are at-least-once, so full idempotency is needed anyway (we already have it). Offsets split processing state between Kafka and Postgres.

**Dangerous failure modes Kafka would add:**
1. consumer lag, then acting on stale signals;
2. dead-lettering an exit or stop message, so a lot is never sold;
3. an offset reset or replay re-creating cards for old signals;
4. Postgres restored to T−5 min while offsets stay at T, so trims and stops are never re-processed;
5. retention expiring during a long outage.

**If adopted later:**
- key = `provider_id` (not channel, signal or position ID; §8.1);
- consumers implement `next_for_provider`/`mark` with per-key concurrency;
- the offset is committed only after `apply` commits;
- no dead-letter skip: a poison message follows §2.4;
- a staleness check on every event.

**Adopt only when one of these is measured:**
- sustained ingest above ~100 events/s, or inbox insert/claim contention (insert p99 > 50 ms after tuning);
- two or more independently deployed consumers that need the raw stream with separate replay positions;
- multi-user fan-out beyond ~1,000 users (a PRD non-goal today);
- multi-region ingest;
- a need to accept messages while Postgres is down **and** the Message API cannot redeliver. Try a local spool or SQS first.

**Multi-user note (unchanged from v0.2):**
- Multi-user is a PRD non-goal (§2, C1), and copy-trading other people's accounts is likely regulated.
- Isolation comes from the data model (`user_id` on every row, row-level security, per-user credentials and limits), not from topics.
- Interpret each message once, then fan out per user.

### 15.2 Redis: not now

**Decision:** not used now. Hot reads go through accessors (`context.load`, `marketdata.quote`, `marketdata.chain`, `provider_lock`) so Redis can be added behind them later.

**Candidate uses and verdicts:**

| Use | Verdict |
|---|---|
| Hot provider context | No. An indexed Postgres read is 1–5 ms. A stale cached provider book mis-links follow-ups, which is **dangerous**. |
| Active signal state, orders, fills, positions, lots, approvals, audit | **Never.** Redis persistence can lose about 1 s of writes, and async-replica failover can lose acknowledged writes. |
| Market data | No. With a single process, an in-memory dict fed by the broker stream is faster. |
| Option-chain cache | No. An in-process cache with a TTL is enough. |
| Distributed locks | No. Single process: `asyncio.Lock`. Multi-process: Postgres advisory locks, which are transactional with the state they protect. |
| Rate limiting | No. Caddy or in-process for logins; semaphores for model APIs. |
| Temporary processing state | No. LangGraph checkpoints must be durable, because approvals wait. |
| Deduplication | No. Only a Postgres unique constraint dedupes atomically with the insert. |

- **p95 impact:** saves about 5–20 ms of the ~550 ms non-model path, under 1% of the budget.
- **Consistency risks if added:**
  - non-atomic dual writes;
  - stale reads after a ledger commit;
  - invalidation races;
  - lost writes on failover;
  - a TTL expiring while a position is still open;
  - reconciliation facing three sources of truth.
- **If adopted later:** Redis holds only derived, rebuildable data. If Redis is down, the system falls back to Postgres or the broker with no loss of correctness.
- **Adopt only when one of these is measured:**
  - two or more app processes or hosts need shared live quotes, and duplicate broker streams or broker connection limits are a problem;
  - `context.load` p95 > 50 ms after indexing;
  - vendor rate limits must be shared across processes;
  - `LISTEN/NOTIFY` fan-out to a horizontally scaled web tier is measured as insufficient.

### 15.3 Architecture options compared

| | A: service → PG | B: Kafka → service → PG | C: service → Redis + PG | D: Kafka → service → Redis → PG |
|---|---|---|---|---|
| Fits ~0.1 msg/s | ✔ | Oversized | ✔ | Oversized |
| p95 | Baseline | + a hop, rebalance pauses | ≈ 0 gain | Worse |
| Exactly-once effects | One transaction boundary | Offsets + PG split | Dual-write risk | Both |
| Per-provider order | Sequencer | Partition, with head-of-line blocking | Sequencer | Partition |
| Correlation | App logic | App logic | App logic | App logic |
| Sources of truth | PG + broker | + Kafka | + Redis | + both |

**Chosen:** **A+**, which is option A plus:
- the inbox, sequencer, staleness, backfill and poison handling (§2.4);
- managed HA Postgres (§9);
- the transport and cache boundaries described above.

### 15.4 Jev making trading decisions (size, limits, price)
- Rejected. These are arithmetic and rule checks with exact answers, and code runs them in microseconds (faster than any model round trip).
- A model that is right 99% of the time on sizing gives about one wrong-size order every 3 days at 30 trades/day. PRD §9 and AC-2/AC-3 require deterministic, provable checks.

### 15.5 Single-vendor LLM (v0.4: Claude + Claude on Bedrock)
Replaced by the provider layer (§4.8). Claude on Bedrock gave infrastructure redundancy but not **vendor** independence: one vendor's pricing, policy or model retirement affected both halves. It also locked the prompt and the eval to one model family.

**Direct vendor SDKs** instead of LangChain `init_chat_model` were considered too. With three vendors, the unified interface earns its place. The trade-off is that LangChain can lag behind vendor features (e.g. new caching or effort parameters). Mitigation: per-model `params` pass-through, and the capability table is re-verified whenever a model is added.

### 15.6 LLM-only interpreter (v0.1 design)
This is the fallback if the Jev bench fails its latency or accuracy targets. The graph is the same, with `jev_interpret` removed.

## 16. Reference: the user's real trade history (Jan 2 – Sep 18, 2026)

**Source:** `docs/data/GainLoss_Realized_20260921-022733.csv` (git-ignored, C87). This is the user's **real** brokerage realized gain/loss export, not dummy data. It has 5,483 closed tax-lot rows over 178 trading days. These are the user's own manual trades, which is the workload the system will take over. Only aggregates are quoted here.

**Limits of this file:**
- It is a realized-only report: there are no open times, so holding time cannot be measured.
- Rows are split per tax lot, so one exit can appear as several rows.
- A **transactions/order-history export** is needed to measure open-to-close time and fill behavior.

### 16.1 Observed profile

| Metric | Value |
|---|---|
| Instrument mix | 98% options (5,373 rows), 2% stock (110 rows, 41 symbols) |
| Calls / puts | 83% / 17% |
| Distinct option underlyings | 199. The top 15 cover 78% of rows: SPY 750, SPXW 672, TSLA 460, QQQ 409, META 397, MU 376, AAPL 318, NVDA 168, IWM 129, … |
| Index options | SPXW 672 rows (12.5%), strikes 6,500–7,855. XSP 3 rows. No other index products. |
| Days to expiry at close | 0 DTE 45%; ≤ 2 DTE 63%; ≥ 30 DTE ~10%. 81 rows closed at $0.00 (expired worthless). |
| Contracts closed per contract per day | p50 2, p75 3, p90 4, p99 11, max 23. **49% are exactly 1 contract.** |
| Entry premium (per share) | p10 $0.86, p50 $2.50, p90 $9.41 |
| Cost per contract | p10 $88, p50 $254, p90 $971, p99 $2,050 |
| Distinct contracts closed per day | p50 14, p90 27, max 41 (lot rows/day: p50 26, p90 60, max 109) |
| Option cost basis closed per day | p50 ~$10.4k, p90 ~$24.8k, max ~$43.7k |
| Daily realized P&L | p5 ≈ −$2.3k, p10 ≈ −$1.3k, median ≈ +$0.2k, worst day ≈ −$28.1k |
| Trend | 220–300 option rows/month (Feb–Apr) → 700–1,040/month (Jun–Sep) |
| Wash-sale rows | 990 (18%): frequent re-entry into the same contract after a loss |

### 16.2 What this changes in the design

| # | Finding | Change |
|---|---|---|
| 1 | 199 underlyings, a long tail | **C86:** no ticker whitelist. The Jev candidate finder over-finds tickers, and `validate` checks them against the broker's optionable-symbol list and live chain. |
| 2 | SPXW is 12.5% of trades | **C85:** "SPX" maps to **SPXW** (PM-settled, daily expiries) through a config symbol map. Provider profiles can add aliases with user approval (§5.3). The SPX real-time index entitlement (§7.3) is confirmed as required. |
| 3 | MU strikes ~900–1,010, SPXW 6,500–7,855 | **C84:** the missing-underlying suggestion uses **live-price proximity** (§6), not a fixed "strike > N means SPX" rule. It stays a suggestion that needs confirmation (C79). |
| 4 | 45% 0DTE, 81 expired at $0 | Expiration management (cutoffs 1/2) is a core path and is tested first in the paper stage. |
| 5 | Premium p10 $0.86 – p90 $9.41 | FR-12 scale normalization must handle "140/con" (per-contract $) vs "1.40" (per share), using the live quote. Add both forms to the Jev `role_<nX>` examples. |
| 6 | **49% of exits are 1 contract** | **Decided, C82:** when `floor(fraction × lot)` = 0, the qty is left blank and the card is labeled "lot too small to trim: exit all or skip" (§6). |
| 7 | Lots of 1–4 contracts | **Decided, C83:** TP legs = min(levels, qty), filled from TP1, editable (§6). A tier that cannot buy one contract gives a disabled card, never a round-up. |
| 8 | Up to 41 contracts/day, rows/day up to 109, volume tripling | Approval workload can reach 50–150 cards/day, so one-tap approve on the phone and card batching per position matter. PRD §12 has been updated to this level. |
| 9 | Daily cost basis p90 ~$25k; daily P&L p5 ≈ −$2.3k | Observed ranges to **seed** the shadow-stage config for max exposure and max daily loss. The user sets the final values (C69). |
| 10 | 18% wash-sale rows | Out of scope: the broker reports tax. C62 (a re-entry after a full exit is a new lot) matches this pattern. The system does not compute tax lots; its lot average price can differ from the broker's FIFO cost basis, and the UI labels which one it shows. |

### 16.3 Broker identification: TBD
- The export's source broker is **unconfirmed**. The column set ("Cost Basis Method", "Wash Sale?", "Disallowed Loss", "Transaction Closed Date") resembles Charles Schwab's realized gain/loss export, but that is a guess, not a fact. The broker stays TBD (PRD §22 b).
- If the current broker turns out to have **no paper account** (Schwab, for example), the PRD §17 paper stage needs one of:
  - **(a)** Run the paper stage on a second broker's paper account (Tradier/IBKR) and go live on the current broker. Cost: two broker adapters.
  - **(b)** Use the current broker only, and replace the paper stage with a longer shadow stage + live at minimum size. Cost: a PRD §17 change; order paths get their first real exercise with real money.
  - **(c)** Move live trading to a broker that has a paper account.

### 16.4 Data handling (C87)
- The file holds real personal financial data and a partial account number.
- `docs/data/` is listed in `.gitignore`.
- The file is never sent to model providers, and only aggregates appear in docs.

## 17. Implementation status and as-built decisions (2026-09-30)

Detail and per-feature specs: `docs/implementation/README.md` (roadmap, decision register), `database-schema.md`, `backend/coding-practices.md`.

### 17.1 Status

| Area | Features | Status |
|---|---|---|
| Infrastructure, schema, config, time, alerts, auth, ports + fakes | F001–F010 | Done |
| Ingest, inbox, dispatcher, feed health, gap backfill, signal contract, candidates, context, normalization, validation, ledgers, decide, cards, apply, timers, `submit_order`, SSE/push, read API | F011–F030 | Done |
| LLM provider layer, extraction agent, Jev fast path, advisory note | F031–F034 | Done (Jev on by default with Laya fallback, F066; no bench yet) |
| Message graph, approval graph, out-of-order, corrections, off-hours | F035–F039 | Done |
| Fill intake, entry, brackets, exit re-pricing, underlying stops, expiration, reconciliation, manual ops, evalkit, observability | F040–F049 | Done, on the fake and simulated broker |
| Real Message API adapter | F050 | **Not built** (blocked: spike S1) |
| Real broker + market-data adapters | F051 | **Not built** (blocked: spikes S2, S3) |
| Next.js dashboard (all FR-25 views) | F052–F061 | Done |
| Browser E2E suite, load and fault suite | F062–F063 | Done (§12) |
| Discord listener + dashboard Discord page | post-F063 | Done |
| Deployment, runbooks, stage-gate audit | F064 | **Not started** |

Backend completion checklist: all boxes ticked except "Jev bench result recorded".

### 17.2 Decisions confirmed during the build

| ID | Decision | PRD |
|---|---|---|
| R-DB | SQLModel + SQLAlchemy 2 async + Alembic + psycopg 3 (amends §3) | — |
| R-PG | PostgreSQL 17 (amends §3) | — |
| OD-5 | Provider-position scope = per author | C96 |
| OD-11 | Approval graph per card (amends §2.1, §5.1) | — |
| OD-28 | Shadow mode simulates fills at the live quote | C97 |
| R-INTERP | Until the model interpreters were wired, production had no interpreter: every message became a raw review card + `parser_degraded` alert. The fixture interpreter is dev/test only | — |
| OD-29 | Off-hours held messages start validity/staleness at the 9:30 ET release | C98 |
| OD-30 | Bracket legs share an OCO group; invariant counts max(stop qty, Σ TP qty); TP fill resizes the stop, stop fill cancels TPs. The real adapter should use native OCO to close the simultaneous-fill race | C99 |
| OD-20 | Exit re-price floor = `exit_floor_pct` below the mid at start, never below one tick | C100 |
| OD-32 | Underlying stops trigger on the last trade | C101 |
| OD-27 | Kill switch disables entry/add cards and entries; exit and stop cards remain | C102 |
| OD-31 | Flatten exits tracked lots only | C103 |
| — | Passwords instead of passkeys; no roles, no signup | C39 |
| — | Discord listener as a message source | C95 |
| X1 | Next.js frontend over a FastAPI JSON API; same origin through `rewrites` (amends §3) | — |
| X3 | `disabled` is a card state and a card type (amends §6) | — |
| X7 | Stage timings as rows, not columns on `message_events` | — |

### 17.3 Still open (do not assume)

| ID | Question | Default in the code |
|---|---|---|
| OD-1, OD-2 | Message API delivery, fields, backfill | Provisional payload; webhook + history port with a fake |
| OD-3, OD-4, OD-16 | Broker, account type (PDT), market data + SPX entitlement, paper stage | Fake broker and market data; `pdt_day_trade_limit` absent from config |
| OD-6 | Note guard accepted? | Built behind `note_guard_enabled`, default on |
| OD-7 | LLM vendors and chain order | Registry supports all four providers; chains empty |
| OD-8 | Jev go/no-go | Both paths built; Jev off |
| OD-9, OD-17 | Managed Postgres provider, network path | Local PostgreSQL 17 |
| OD-12 | Market data unavailable | Validation failure → review card + `market_data` alert; entries not approvable |
| OD-18 | `add` with no lot, `tp_hit_notice`, `conditional` | `add` without a lot → ledger + feed only; `tp_hit_notice` → ledger + feed; `conditional` → feed with label |
| OD-26 | "X% commons" sizing extraction | Not actionable → review card |
| OD-33 | Reconciliation in shadow mode | Skipped, treated as clean |
| (h) | Discord user-account token (ToS) | Listener on only when `DISCORD_TOKEN` is set |

---

## Appendix A — Jev question catalog

All questions go in **one** request per message. `{…}` marks an option set built by code from the state. Every Choice includes a no-match option, as the TypeSafe docs recommend. Question IDs are for code only; each question is written to be self-contained.

### A.1 Signal context (Discord message)

| ID | Type | Instructions (abridged) | Options / levels | Used for |
|---|---|---|---|---|
| `msg_type` | Choice | What is `message.text` doing, given `recent_author_messages` and `provider_book`? | `entry`, `add`, `trim`, `full_exit`, `soft_exit`, `stop_set`, `stop_move`, `stop_cancel`, `tp_set`, `tp_hit_notice`, `correction`, `watch`, `conditional`, `commentary_pnl`, `stock_entry`, `stock_exit`, `out_of_scope_strategy`, `chatter` (each with `what` / `not_for` / examples) | FR-5, routing |
| `signal_count` | Choice | How many distinct trade instructions does `message.text` contain? | `none`, `one`, `multiple` | FR-6 → slow path |
| `instrument` | Choice | Is the instruction about an option or shares? | `option`, `stock`, `unclear` | 7.8 |
| `role_<nX>` (one per candidate number) | Choice | In `message.text`, what does the number `candidates.numbers[X].text` at position `pos` represent? | `strike`, `entry_price`, `entry_price_range_low`, `entry_price_range_high`, `stop_level`, `take_profit_level`, `underlying_level`, `exit_fraction`, `share_or_contract_qty`, `pnl_or_percent`, `date_or_time`, `other` | Strike, price, stop, TPs (literal spans). The `entry_price` examples cover both per-share ("1.40", ".95") and per-contract ("140/con") forms; scale is fixed later by FR-12. |
| `ticker` | Choice | Which candidate is the traded underlying? | `{candidates.tickers}` + `not_stated` | FR-14 (`not_stated` → review). Candidates are generic, not whitelisted; the choice is validated against the optionable list (C86) after the symbol map (C85). |
| `right` | Choice | Is the option a call or a put? | `call`, `put`, `not_stated` | |
| `expiry` | Choice | Which token states the expiry? | `{candidates.expiry_tokens}` + `not_stated` | FR-15 (code resolves) |
| `size_tier` | Choice | Which size did the provider signal? | `{config tier names}` + `not_stated` | C15 |
| `exit_fraction` | Choice | How much of the position is being exited? | `all`, `half`, `third`, `quarter`, `other_stated`, `not_stated` | C51, C26 |
| `stop_basis` | Choice | Is the stop on the option premium or on the underlying price? | `premium`, `underlying`, `not_applicable` | C49 |
| `candle_close` | Noul | Does the stop depend on a candle closing beyond a level? | — | C49 (info only) |
| `has_prose_condition` | Noul | Does the instruction carry a condition in prose (e.g. "only if green on the day")? | — | C30 (shown, not enforced) |
| `is_correction` | Noul | Does `message.text` correct a detail of an earlier message? | — | 7.5 |
| `correction_target` | Choice | Which earlier message is being corrected? | `{recent_author_messages ids}` + `none` | 7.5 |

### A.2 Provider-portfolio context (provider book)

| ID | Type | Instructions (abridged) | Options / levels | Used for |
|---|---|---|---|---|
| `links_to` | Choice | Which of the author's open positions in `provider_book` does `message.text` refer to? | `{provider_book ids}` + `new_position` + `ambiguous` | 7.3, FR-3 fallback |
| `is_add` | Noul | Is the author adding to or averaging an existing open position? | — | C62 |
| `exit_intent` | Score | How strongly does the author signal leaving the position? | 0 no exit intent · 1 mentions profit, no action · 2 suggests taking some off ("could trim here") · 3 states trimming · 4 states fully out | C52 soft-exit label |
| `breakeven_stop` | Noul | Is the author moving the stop to their entry price (breakeven)? | — | C29 |
| `stop_removed` | Noul | Is the author removing or no longer using a stop ("pulled stop")? | — | 7.4 |

### A.3 How answers become fields
1. A field is **literal** only when Jev picks a candidate whose confidence ≥ T_field. The value is the candidate's exact span, and code normalizes scale against the live quote (FR-12).
2. If two numbers both claim the same single-valued role (e.g. two `strike`s), the message goes to the slow path.
3. `not_stated` on a required field goes to the slow path (context inference). If it is still missing, the result is a review card.
4. Probabilities for all questions are stored in `model_calls` for evaluation and threshold tuning.
