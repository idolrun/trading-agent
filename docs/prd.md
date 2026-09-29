# PRD — Discord Signal Copy-Trading Assistant (Draft v0.3)

**Status:** Draft for review. Discovery complete except the items in §22.
**Approving this PRD moves the project to architecture and implementation planning. It does not start coding.**
**Changes from v0.1:** calibrated against the user's real realized gain/loss history (Jan 2 – Sep 18, 2026; Appendix B):
- §3 trading profile; §12 scale;
- new rules C82–C87: 1-contract trims, TP split, missing-underlying suggestion, SPX → SPXW, no ticker whitelist, trade-history data handling;
- AC-15 to AC-17; new open item (f).

**Changes from v0.2** (workload now 1,000–2,500 messages per 6–8 h session; transport/queue architecture review):
- §12: new scale, burst specification and p99 target.
- New FR-1a–FR-1e: ordered processing per provider, out-of-order arrivals, staleness and catch-up, delivery and backfill, poison messages.
- New NFR for data durability (RPO/RTO).
- §6: provider-position scope.
- §19: cost estimate.
- New decisions C88–C94 and AC-18 to AC-23; open item (g).

**Traceability:** `[Cxx]` refers to the decision log in Appendix A. Items marked **[CONFIRM]** were proposed during discovery but never explicitly confirmed. They must be accepted or rejected during this review.

---

## 1. Problem statement
The user follows trusted trade-signal providers in several Discord channels. Signals are short, informal, and full of typos. They are often split across several messages (entry, trim, stop, correction, exit) and delivered fast: a 0DTE options trade can open and close within about 7 minutes. Mirroring these signals by hand into a brokerage account is slow and error-prone. Messages get missed, strikes get mistyped, and follow-ups get attributed to the wrong position.

The system turns this message stream into precise, validated, **human-approved** orders at a broker. It tracks each provider's positions and the user's own holdings, so that follow-up messages are applied correctly.

## 2. Goals and non-goals

### Goals
- G1. Convert provider messages into structured trade signals, with the source of every field recorded.
- G2. Track every provider's positions and the user's real lots, and link follow-ups to the right position.
- G3. Present each proposed action as an approval card. The user approves or rejects it within seconds, on desktop or phone.
- G4. Execute approved actions correctly at the broker, under hard deterministic safety invariants and risk limits.
- G5. Never lose a signal silently, never act on an unverified guess, and never auto-correct a number.
- G6. Be provably reliable before real money is used, via a staged go-live with measurable gates.

### Non-goals (MVP) [C68, C70, C30]
- Predicting prices, technical analysis, or generating its own signals.
- Enforcing provider conditions written in prose (e.g. "only enter lottos if green on the day").
- Parsing images or screenshots. Voice channels.
- Multi-leg spreads, selling options to open, short-selling stock. These are future scope.
- Trading in extended hours (pre-market or after-hours).
- Multiple users or multi-tenancy.
- Executing any order without user approval, except the pre-approved automatic actions in §9.
- Integrating with Discord directly. The user's existing message API is the only source [C11].

## 3. Target user
One user, trading their own money, in an in-house application [C1]. The user's local timezone is UTC+5:45. They are available for the full US regular session (19:15–01:45 local) every trading day [C53]. They approve actions from a desktop and a phone [C21].

**Trading profile.** This is taken from the user's real realized gain/loss export: 5,483 closed tax-lot rows over 178 trading days, Jan 2 – Sep 18, 2026 (Appendix B). It is the manual workload this system takes over.
- **Instruments:** 98% options, 2% long stock. 83% of option rows are calls.
- **Expiry:** 45% of option rows close on expiry day (0DTE) and 63% within 2 days; about 10% are held ≥ 30 days. 81 rows expired worthless.
- **Underlyings:** 199 different option underlyings, a long tail; the top 15 cover 78% of rows. The most active are SPY, SPXW, TSLA, QQQ, META, MU and AAPL.
- **SPX:** SPXW (S&P 500 index, PM-settled daily expiries) is 12.5% of option rows.
- **Small lots:** per contract per day, the median exit is 2 contracts, and 49% of exits are exactly 1 contract.
- **Premiums:** median $2.50 per share (p10 $0.86, p90 $9.41); median cost ≈ $254 per contract.
- **Volume:** 14 distinct contracts closed per day at the median, 27 at p90 and 41 at most. Monthly volume roughly tripled from Feb–Apr to Jun–Sep.

## 4. System shape: is this an agent?
**It is a deterministic pipeline with an LLM used in narrow roles, not an autonomous trading agent.**

```
Message API (push) → Ingest → Interpret (LLM) → Validate & normalize (code)
  → Provider ledger update (code) → Decide proposed action (code) → Approval card
  → User approves → Order execution (code) → Broker → Fills → User lot ledger
```

- **The LLM handles:** classifying messages, extracting fields with their source, and resolving references (which provider position a follow-up refers to, and fields implied by context) [C31, C71].
- **Deterministic code handles:** validation, normalization, all sizing, risk limits, safety invariants, order construction and pricing, approval validity, the end-of-day rule, stops, reconciliation.
- **The human handles:** every order action, except the pre-approved automatic actions in §9 [C14].
- The LLM never decides whether to trade, how much, or at what price beyond extracting what the provider wrote. Code proposes the action; the human approves it [C80].
- The LLM also writes an **advisory note** for each card: a plain-language explanation of what the provider did or intends. It is labeled "advisory". It cannot change fields, actions, quantities, or prices. It is generated from the same provider-only context (C71), so it never sees the user's lots or money [C80].
- **[CONFIRM] Note guard:** if the note contains a ticker, strike, expiry, or price that does not appear in the validated fields or source messages, the note is hidden and the event is logged.

## 5. Core use cases
- UC1. A provider posts an option entry. The user approves and it is bought within price limits.
- UC2. A provider posts an entry with SL/TP. The user approves a bracket (entry + stop + take-profits) [C50, C56].
- UC3. A provider trims, exits, or softly suggests taking profit. The user approves a sell of their linked lot [C51, C52, C26].
- UC4. A provider sets, moves, or removes a stop, on the premium or on the underlying. The user approves the stop change [C28, C49, C61].
- UC5. A provider corrects a typo by new message or by edit, before or after the user traded [C16, C48].
- UC6. A provider adds to or averages down a position [C62].
- UC7. A position expiring today approaches the close [C19].
- UC8. A stock signal (long shares) [C55, C60, C61, C76].
- UC9. An ambiguous or incomplete message. The user completes a review card [C31].
- UC10. The user acts manually: exit, stop change, manual entry, kill switch [C40, C77].
- UC11. System failure: feed silence, LLM outage, crash and restart [C41, C45, C46].

## 6. Key concepts
- **Provider:** any author in a configured channel. All authors are parsed [C54]. The user can mute an author [C57].
- **Provider position:** a position a provider has called (entry through exit), tracked whether or not the user took it [C72].
  - It is owned by exactly one provider, and links never cross providers.
  - Its scope defaults to **per provider** (author). Whether it should be per (provider, channel) is open item (g).
- **Signal:** one trade instruction extracted from a message. A message can contain several (FR-6).
  - Each signal is linked to at most one provider position.
  - The link records how it was made (reply-to, model, or user correction) and with what confidence.
  - Signals and links are outputs of interpretation. They cannot be used to route or order messages before interpretation.
- **Message post time:** the time the provider posted the message, taken from the Discord message ID. It drives ordering (FR-1a) and all validity clocks (C22, FR-1c).
- **Lot:** the user's real holding at the broker, linked to exactly one provider position. Two providers on the same contract means two lots [C24]. Positions not opened by the system are "foreign": read-only [C47].
- **Card:** an approval request for one action. Types: normal, inferred, review, disabled (blocked). Non-actionable messages get no card [C31, C75].
- **Field provenance:** each field is *literal* (an exact text span, verified by code), *context* (inferred from a specific earlier message), or *resolved* (computed by code, e.g. "0dte" = today, "friday" = that date) [C31].
- **Tier:** a configured mapping from a size word (default / small / lotto, extensible) to a maximum dollar amount. Code computes the quantity [C15]. Stocks have a separate tier table [C76].

## 7. User workflows

### 7.1 Option entry (happy path)
1. A message arrives by push [C13]. The LLM classifies it as an entry and extracts underlying, expiry, strike, call/put, price or range, size word, and SL/TP, each with its provenance.
2. Code validates and normalizes (§10). If the provider has an open position this adds to, it becomes an add card [C62].
3. Code checks the risk limits [C23]. If any limit is hit, a disabled card is shown with the reason [C75].
4. A card is displayed within 5 s p95 [C38], with a push notification [C58]. It shows the author prominently [C57], the source messages, highlighted inferred fields, the quantity computed from the tier, any bracket legs [C50], and warnings (e.g. an opposite-direction lot [C74]).
5. The user approves with one click (inferred cards included [C32]). Review cards require the missing fields first.
6. The order is sent as a limit at the signal price (top of the range, if a range was given) + X%. Any unfilled remainder is cancelled after M s; partial fills are kept [C22].
7. The lot is created and linked to the provider position.

### 7.2 Entry card becomes invalid [C22]
The entry card expires on the first of:
- N seconds elapsing, **measured from the provider's post time**, not from card creation [C90],
- the live ask rising above signal price + X%,
- a newer provider message on the same position (trim, stop, exit or correction).

The user is notified. No order is placed [C18].

### 7.3 Follow-up exit (trim, exit, soft exit)
1. The LLM links the message to a provider position (reply parent, the author's open positions, recent messages from the author and anyone @mentioned [C71]).
2. If the user holds no lot for that position, the provider ledger is updated and the message goes to the feed only.
3. If the user holds a lot, an exit card is created:
   - With a stated fraction, the quantity is pre-filled as that fraction of the lot, rounded down, editable [C51].
   - If rounding down gives 0 (e.g. "trim half" on a 1-contract lot, which is common per §3), the quantity is left blank. The card is labeled "lot too small to trim: exit all or skip", and no order is placed until the user enters a quantity [C82].
   - With no quantity stated, the field is blank and the user enters it [C26].
   - A soft exit gets a card labeled "suggestion, provider still holding", quantity blank [C52].
4. The exit card expires only on timeout or when the position is already closed [C22].
5. On approval, the exit starts as a limit at the mid price. It re-prices toward the bid every few seconds until it fills or reaches the configured floor. At the floor it stops and notifies the user [C25].
6. Before submitting, working stops and take-profits are reduced or cancelled per C35. If the exit ends unfilled, they are restored.

### 7.4 Stops and take-profits
- Stop on option premium: the card places a stop order held at the broker. After approval it fires with no further approval [C28].
- Stop on the underlying price (e.g. "769 stop" on SPY 770C): the card places a stop held by the system. When the underlying crosses the level, the C25 exit fires automatically. Candle-close conditions are shown as information only [C49].
- Stock stop: stop-market, good-till-cancelled, held at the broker [C61].
- Breakeven stops show both the user's average fill and the provider's entry price; the user picks one [C29].
- Stop moves and removals ("pulled stop") need a new approval card.
- Take-profits are limit orders at the broker, split equally across TP levels (editable), and linked to the stop so one cancels the other [C50, C56].
- When the quantity is smaller than the number of TP levels, the number of TP legs is min(levels, quantity). Contracts are assigned from TP1 onward (1 contract → TP1 only; 2 contracts with 3 levels → TP1 and TP2), and the user can edit this [C83].
- A tier whose dollar amount cannot buy one contract at the signal price produces a disabled card with that reason. It is never rounded up to 1 contract.

### 7.5 Corrections and edits
- Before the user trades: a correction message or a message edit updates the pending card. The update needs fresh approval (it invalidates per C22) [C48].
- After the user trades: alert only. It shows both messages and live P&L, and states that follow-ups now refer to the corrected contract. No action is proposed [C16].
- The original entry message is deleted: the pending card is invalidated and an alert is sent [C48].
- **Numbers are never auto-corrected** [C78].

### 7.6 Expiration management [C19]
- Positions expiring today enter an expiration-management state.
- At cutoff 1, an urgent close card is created.
- If it is not approved by cutoff 2, the system closes the position automatically (C25 exit, working stops and take-profits cancelled first).
- Both cutoffs are configurable.
- This is a core path, not an edge case: 45% of the user's option trades close on expiry day (§3).

### 7.7 Off-hours messages [C59]
Messages calling for action outside regular hours are held until 9:30 ET. The card is then built with fresh validation and the normal validity rules. The system submits orders during regular hours only.

### 7.8 Stock signals [C55, C60, C76, C61]
- Long only.
- Entries with no price are not actionable (feed only).
- Sizing: "X% commons" = X% of account equity, capped by the stock tier maximum. Otherwise the stock tier's dollar amount.
- Stops as in 7.4.
- Multi-week holds are followed until the provider exits [C20].

### 7.9 Manual operations [C40, C42, C77]
- Manual exit of a tracked lot. Manual stop add/move/cancel.
- Manual entry of any contract. Requires a fresh password confirmation (step-up).
- Kill switch: pauses new cards and entries. The optional "flatten" asks which to close (options / stocks / all) and requires a fresh password confirmation.
- Manual orders pass every check that signal orders pass.
- Provider-position correction: the user can add, edit, or close provider positions. Every correction is audited and fed into evaluation data [C73].

## 8. Functional requirements

**Ingestion**
- FR-1. Consume the user's message API via push [C11, C13]. Persist every message event (create, edit, delete) exactly once, keyed by message ID.
- FR-2. Parse every author in configured channels unless muted [C54, C57].
- FR-3. Use reply-to references from the API deterministically when present.
- FR-1a. **Ordered processing per provider.**
  - Messages from one provider are interpreted, linked and applied to the ledgers one at a time, in **post-time order** (not arrival order).
  - Different providers are processed in parallel.
  - A pending approval must never block the provider's next message [C88].
- FR-1b. **Out-of-order arrival.**
  - If a message arrives after a later message from the same provider has already been applied, it is still applied, and flagged "late".
  - That provider's follow-ups that were linked to nothing, or were ambiguous, are re-checked.
  - Any changed result becomes a review card, never an automatic change [C89].
- FR-1c. **Staleness and catch-up.**
  - Message age = processing start − post time.
  - An entry or add older than a configurable threshold (default 30 s) updates the provider ledger but produces **no approvable card**. It appears in the feed labeled "stale".
  - Exits, trims, stops and corrections are always carded, with a "late" badge when old.
  - After an outage or backlog, the system enters **catch-up mode** and raises an alert until the backlog clears [C90].
- FR-1d. **Delivery and backfill.**
  - A message event is accepted only once it is durably stored.
  - The message API must either redeliver unacknowledged events for ≥ 10 minutes, or provide a history API for backfill.
  - The system detects gaps (per channel, by last seen message ID) and backfills them on startup, on reconnect, and periodically during market hours [C91].
- FR-1e. **No silent drops.**
  - A message that fails processing 3 times produces a raw review card and an alert.
  - The provider's later messages continue to be processed.
  - No message is ever skipped without a visible record [C92].
- FR-4. Feed health: during market hours, if there has been no message and no heartbeat for N s, raise an urgent alert and mark the dashboard "degraded" [C41].

**Interpretation (LLM)**
- FR-5. Classify each message into the types: entry, add, trim/partial exit, full exit, soft exit, stop set/move, stop cancel, TP set/update, TP-hit notice, correction, watch, conditional, commentary/P&L, stock entry/exit, out-of-scope strategy, non-signal chatter.
- FR-6. Extract zero or more signals per message. One message can contain several entries.
- FR-7. Every extracted field carries its provenance. Code verifies that each literal span exists in the source text. Any mismatch is a hard reject [C31].
- FR-8. Context sent to the LLM is limited to C71. The LLM never receives the user's lots, balances, or P&L.
- FR-9. If the LLM fails, retry on a second LLM provider. If that also fails, show a raw review card with blank fields and a "parser degraded" alert [C45].
- FR-10. When uncertain, the default classification is non-actionable. Non-actionable messages appear in the feed with their classification label [C33].

**Validation and normalization (deterministic)**
- FR-11. Identity check: the contract must exist in the live option chain, and the signal price must be within a wide, configurable tolerance of the live quote. On failure the card becomes a review card. This is separate from the tighter chase band in C22 [C81].
  - Tickers are checked against the broker's list of optionable symbols and the live chain. There is no fixed ticker whitelist, because the user trades 199 underlyings [C86].
- FR-12. Price notation is normalized ("140/con", "260", "3.2") by choosing the scale that matches the live quote [C81]. Both per-contract dollars ("140/con" = $1.40) and per-share prices occur; typical premiums run from $0.86 to $9.41 per share (§3).
- FR-13. Warn when the signal premium matches an adjacent strike's quote much better than the stated strike's. Warning only; never a correction [C78].
- FR-14. A missing underlying (e.g. "7775c") always produces a review card: the user confirms the underlying [C79].
  - The suggested underlying comes from **live-price proximity**: the strike must be within a configurable percentage of the live price of an underlying the provider recently traded.
  - It never comes from a fixed strike threshold. For example, the user's MU strikes (~900–1,010) would break a "large strike means SPX" rule.
  - The suggestion is never approvable without confirmation [C84].
- FR-15. Relative dates ("0dte", "friday", "1DTE") are resolved by code against the exchange calendar (ET).
- FR-15a. A configurable symbol map translates provider shorthand into broker contracts. The default maps "SPX" to **SPXW** (PM-settled daily expiries) [C85].

**Ledgers and decisions (deterministic)**
- FR-16. Maintain the provider-position ledger and the user-lot ledger [C72]. Lots are per provider [C24]. Adds grow the lot and recompute the average price. A re-entry after a full exit creates a new lot [C62].
- FR-17. Compute proposed actions and quantities from tiers [C15, C76] and fractions [C51].
- FR-18. Enforce the risk limits before creating an approvable entry card: max daily loss (realized + unrealized), max open positions, max total premium/exposure. Foreign positions count toward the limits [C23, C47].
- FR-19. Warn when the user holds an opposite-direction lot on the same underlying [C74].

**Order execution (deterministic)**
- FR-20. Safety invariants, checked before every submission [C35]:
  - (1) never open a short;
  - (2) sell quantity ≤ contracts in that provider's lot, manual exits included;
  - (3) every exit reduces or cancels the working stops and take-profits, and restores them if the exit ends unfilled.
- FR-21. Entry pricing and cancellation per C22. Exit pricing per C25.
- FR-22. Each order has a client order ID generated by the system. On a timeout, query the order status before any retry. Never retry blindly [C46].
- FR-23. System-held stops on underlying prices [C49]. Broker-held premium and stock stops [C28, C61]. Take-profit and stop linked so one cancels the other [C50].
- FR-24. Expiration management per 7.6 [C19].

**Dashboard and notifications**
- FR-25. Responsive web dashboard with push notifications (desktop and phone) [C17, C21].
  - Views: live message feed, cards, provider positions, lots (tracked and foreign), orders and fills, alerts, system health, config, labeling screen [C64].
- FR-26. Push notifications for new cards, fills and automatic actions, alerts, and a risk limit being hit [C58]. Disabled (blocked) cards do not push [C75].
- FR-27. Manual operations and kill switch per 7.9.

## 9. Decision boundaries

| Actor | What it may do |
|---|---|
| LLM | Classify, extract with provenance, link references, write the advisory note. Nothing else. |
| Code only | Validation, normalization, dates, sizing, risk limits, invariants, pricing and re-pricing, validity and expiry, reconciliation, end-of-day forced close. |
| Needs user approval | Every entry, add, exit, trim, stop placement/move/cancel, bracket, manual entry, flatten. |
| Pre-approved automatic actions (the only exceptions) | A broker-held stop or TP firing after approval; a system-held underlying stop firing after approval; exit re-pricing within an approved intent; the end-of-day forced close at cutoff 2; automatic stop restore after an unfilled exit. |

## 10. Uncertainty and hallucination handling
- The system does not rely on the LLM's self-reported confidence [C31].
- Tiers [C31]:
  - **normal card:** every field literal or resolved by code, and validated;
  - **inferred card:** some fields inferred from context, highlighted with the source message;
  - **review card:** a required field is missing or the target position is ambiguous; the user completes it;
  - **non-actionable:** feed only.
- Hallucination detectors:
  - literal-span verification (FR-7);
  - chain and quote validation (FR-11, CONFIRM);
  - adjacent-strike check (FR-13, CONFIRM);
  - schema validation;
  - user corrections to provider positions logged as error signals [C73].

## 11. Failure and recovery [C45, C46, C41]
- LLM outage: second provider, then a raw review card.
- Feed silence: urgent alert, "degraded" state.
- Crash or restart:
  - The broker is the source of truth. Reconcile lots against the broker.
  - On any mismatch, flag it and block new entry cards until it is resolved.
  - Approval cards open at crash time expire.
  - Adopt working broker orders; do not resubmit them.
- Order submit timeout: query by client order ID before any retry.
- Exit reaches its floor unfilled: notify the user and restore stops.
- Unanswered cards expire with no action [C18]. For positions expiring today, the end-of-day forced close is the safety net.
- Reprocessing a message after a crash must never create a second card or a second order.
- **Database unavailable:**
  - message intake is refused, so the message API redelivers (FR-1d);
  - processing stops and trading halts;
  - broker-held stops keep working; system-held underlying stops do not (§21).
- **Message API outage:** on reconnect, gaps are backfilled and catch-up mode applies (FR-1c, FR-1d).
- **Poison message:** FR-1e.

## 12. Non-functional requirements
- Latency: p95 ≤ 5 s and p99 ≤ 8 s from a message being received to its card being displayed on an open dashboard.
  - This is measured over all messages, **including bursts**.
  - Queue wait counts toward it.
  - Phone push is best-effort. Timing is logged per stage [C38].
- Availability: the system must be up during US regular hours every trading day. Hosting is chosen in architecture against this requirement [C37].
- Durability [C93]:
  - Approvals, order intents, fills and ledger changes must survive any single server or disk failure with **no loss** (RPO ≈ 0). Other data may lose up to 5 minutes (RPO ≤ 5 min).
  - Recovery during market hours takes ≤ 5 minutes (RTO).
  - After any failover or restore, positions are reconciled against the broker before new entries are allowed.
- Scale: 5–20 providers, **1,000–2,500 messages per 6–8 h trading session** [C43]. The v0.2 figure was 200–1,000 per day. The average is about 0.1 message/s.
  - Design bursts:
    - at least 50 messages in 10 s across providers;
    - at least 5 messages in 5 s from one provider (entry, stop and targets posted together).
  - The user's real history: 14 distinct option contracts closed per day at the median, 27 at p90 and 41 at most. Up to ~109 lot closes a day.
  - Volume roughly tripled from Feb–Apr to Jun–Sep.
  - Expect **50–150 approval cards per day** at current activity.
- Time: internal clock is exchange time (ET) with an exchange calendar (holidays, half days). The display uses the user's local time [C53].
- Retention: all audit data is kept for 7 years [C63].

## 13. Security, privacy, authentication and authorization [C39, C42]
- Publicly reachable dashboard. Email + password authentication against an allowlist of users; no self-registration (users are added from the console or by a signed-in user; temporary passwords must be changed at first login). Recovery via the server console.
- Short, device-bound sessions. Login audit log. Rate limiting.
- Step-up password confirmation for manual entry and flatten.
- Single user; no roles.
- Broker keys and LLM keys live in secret storage, never in code or logs.
- Message text may be sent to cloud LLM providers [C36]. User holdings and money data are never sent to the LLM (FR-8).
- The user's trade-history exports (e.g. `docs/data/`) contain personal financial data and a partial account number. They are kept out of version control and are never sent to model providers. Only aggregate figures appear in project documents [C87].

## 14. Data requirements
Every record below is persisted with a timestamp, and the whole audit trail is immutable:
- message events (create/edit/delete, raw payload, post time, and "late" / "stale" / "backfilled" flags);
- processing status of every message event (received, processing, done, failed, stale);
- signals and their links to provider positions (how each link was made, confidence), including re-links after corrections;
- LLM calls (input context, output, provider, latency, cost);
- parsed signals with provenance;
- provider positions and their history;
- lots;
- cards (with state transitions and reasons);
- approvals and rejections;
- orders (client ID, broker ID, state history);
- fills;
- stops and TPs;
- alerts;
- config changes;
- manual corrections;
- logins;
- evaluation labels and runs.

## 15. External integrations and dependencies
- **The user's message API.** Push delivery. Needed from it:
  - a sample payload;
  - fields: message, channel and author IDs, timezone-aware timestamp, reply-to ID, edit/delete events, attachments;
  - a heartbeat;
  - historical export. **Export is a hard prerequisite** [C65].
  - v0.3 additions:
    - stable author IDs and Discord message IDs (which carry the post time);
    - **delivery semantics**: redelivery of unacknowledged events for ≥ 10 minutes, **or** a history API that can fetch a channel's messages after a given message ID [C91];
    - whether ordering is guaranteed per channel.
  - If neither redelivery nor backfill exists, the architecture must add a local durable buffer before intake is built.
- **Broker (not chosen) [C12].**
  - The source broker of the trade-history export is unconfirmed. Its format resembles one broker's realized gain/loss report, but this is not established.
  - If the user's current broker has no paper account, §17 stage 3 needs a decision: paper-trade on a second broker, or replace the paper stage.
  - The broker must support:
    - options API, including SPX index options (SPXW is 12.5% of the user's option trades, so this is confirmed as required);
    - stop orders on options;
    - OCO/bracket orders for options;
    - GTC stock stop-market orders;
    - client order IDs and an order-status query;
    - a paper account.
- **Account type (not chosen).** If it is a margin account under $25k, the pattern day trader rule applies to both options and stock round trips. Enforcing it would become a required risk limit. Decided with the broker.
- **Two cloud LLM providers**, chosen on accuracy, latency and cost. Both must pass the same gates [C36, C45].
- **Market data:** option chains, option quotes, underlying prices including the SPX index level (needed for system-held stops and validation). The source is decided in architecture.

## 16. Observability
- Per-stage latency (p50/p95/p99), including queue wait per provider.
- Backlog depth, oldest unprocessed message age, catch-up mode.
- Counts of duplicate, late, stale, backfilled and failed messages.
- Feed health.
- LLM error and fallback rates, and spend.
- Card outcomes (approved, rejected, expired, invalidated, and why).
- Order states, rejects and re-price counts.
- Invariant-check blocks.
- Reconciliation results.
- Alerts are pushed per C58.

## 17. Evaluation and testing
- Labeling: the LLM pre-labels; the user corrects in the dashboard labeling screen [C64].
- Data split: a dev set, plus a held-out test set used only for gate checks, split by trading day. Each check is logged, and the test set is refreshed after repeated failures [C67].
- Staged go-live [C34, C66]:
  1. **Replay:** ≥ 500 actionable + 2,000 non-actionable labeled messages from ≥ 5 providers. Normal-card field accuracy 100%. Zero wrong contracts on normal cards. Actionable recall ≥ 98%.
  2. **Shadow** (live messages, cards, no orders): ≥ 20 trading days.
     - Zero invariant violations in the orders it would have placed.
     - Latency p95 ≤ 5 s and p99 ≤ 8 s, including bursts.
     - Zero undetected feed outages; zero lost or silently dropped messages.
     - Before shadow starts, load and fault tests must pass: a real day replayed at 1×/10×/50× speed, the design bursts (§12), a message-API outage with catch-up, a kill during processing, and a database failover.
  3. **Paper:** ≥ 10 trading days. Every order path exercised at least once: entry, bracket, trim, add, broker stop, underlying stop, stop restore, end-of-day forced close, crash recovery. Zero unexplained reconciliation mismatches.
  4. **Live at minimum sizes:** ≥ 10 trading days before sizes increase.
- Deterministic components (invariants, sizing, validity, re-pricing, reconciliation) get unit and scenario tests using the scenarios in this PRD.

## 18. Deployment and operations
- Hosting is decided in architecture [C37].
- The database must not share a single server or disk with the application. It needs automatic failover and point-in-time recovery, per the durability NFR in §12 [C93].
- Transport and caching: PostgreSQL is the only source of truth and the message queue. Kafka and Redis are not used [C94]. They may be introduced only behind the architecture's defined boundaries, and only when a measured trigger in the TRD is met.
- Configuration: tiers (options and stocks), X, N, M, re-price step and floor, cutoffs 1 and 2, risk limits, feed-silence N, context window size, muted authors, channels, symbol map (C85), missing-underlying proximity % (C84), staleness threshold (FR-1c), retry limit (FR-1e). Initial values are tuned in the shadow and paper stages [C69].
- Seed ranges from the user's history. These are observed ranges only; the user sets the final values:

| Metric | Observed |
|---|---|
| Cost per contract | p10 $88, p50 $254, p90 $971, p99 $2,050 |
| Option cost basis closed per day | p50 ~$10.4k, p90 ~$24.8k, max ~$43.7k |
| Daily realized P&L | p5 ≈ −$2.3k, p10 ≈ −$1.3k, worst day ≈ −$28.1k |

- Tier amounts must buy at least one contract at typical premiums (see §7.4).
- Operator runbooks are required for recovery, reconciliation mismatches, password recovery, and the kill switch.

## 19. Cost
- LLM monthly ceiling: to be decided by the user. Spend is tracked from day one [C44].
- The estimate at 1,000–2,500 messages per session is about $60–180/month for all model providers combined. The TRD suggests a $200/month ceiling.
- Managed database hosting (C93) is an added fixed cost, decided in architecture.

## 20. Scope

### MVP
Everything in §7–§18: options (including 0DTE and SPX) and long stock; single user; web dashboard.

### Future scope
- Multi-leg spreads and selling options.
- Short stock.
- Image parsing.
- Extended hours (listed as out).

## 21. Risks and tradeoffs
- Approving every action plus unanswered-means-expire means exits can be missed. Mitigations: end-of-day forced close, and pre-approved stops.
- System-held underlying stops give no protection during a system outage [C49].
- Stock stop-market orders fill below the stop on gap-downs [C61].
- A public dashboard that allows manual entry has a high attack surface. Mitigated by allowlisted email + password login, step-up confirmation, and identical order checks [C42].
- Parsing every author produces false cards. Mitigated by showing the author, muting, and the recall/precision gates [C54, C57].
- Errors in the provider ledger spread to follow-ups. Mitigated by user correction and evaluation [C73].
- The full-session approval workload (19:15–01:45 local): 50–150 cards per day at the user's current activity (§12). This makes one-tap approval on the phone essential.
- Small lots (49% of exits are 1 contract) make fractional trims and multi-level TPs round badly. Mitigated by C82 and C83.
- Trade-history exports hold sensitive data. Mitigated by C87.
- A provider posting several messages at once, where each needs the slow interpretation path, can push that provider's later messages past the latency target. This is a consequence of ordered processing (C88). It is measured in load tests, and the architecture defines a fix that keeps the order in which messages are applied.
- A backlog after an outage could turn old entries into cards at stale prices. Mitigated by C90: stale entries get no card, and late exits still get one.
- Losing the database loses trading state. Mitigated by C93 (HA, point-in-time recovery, broker reconciliation).
- Depends on the message API: whether it delivers edits and a heartbeat, and whether it can export history. Without edit events, corrections made by editing are lost.
- Provider-quoted prices lag the live market. The price band and validity rules bound how far the system will chase.

## 22. Open items
**Blocking:**
- (a) Message API details and historical export [C65], including delivery semantics and a backfill API (C91).
- (b) Broker and account choice against §15. This includes confirming which broker produced the trade-history export, and the paper-stage approach if that broker has no paper account.
- (c) The note guard (§4, [CONFIRM]).
- (c2) Market-data source for option quotes and the SPX index level (paid feed / entitlement likely). Decided in architecture.
- (g) Provider-position scope: per provider (the default), or per (provider, channel)? This matters if one provider runs separate channels (e.g. 0DTE vs swings) that can hold the same contract. It must be decided before ledgers are built.

**Non-blocking:**
- (d) LLM cost ceiling.
- (e) Initial config values (set during the shadow and paper stages). Seed ranges are in §18. This includes the staleness threshold (default 30 s).
- (f) A transactions/order-history export with open times. The realized report has none, so holding time cannot be measured. That export is needed to validate the "0DTE opens and closes within about 7 minutes" assumption (§1) and the entry/exit validity timings (C22).

## 23. Acceptance criteria
- AC-1. A literal, valid entry message produces a normal card within 5 s p95 over the shadow period.
- AC-2. No order is ever submitted without an approval record, except the pre-approved actions in §9. Checked by an audit query across the paper and live stages.
- AC-3. Scenario tests prove FR-20 (1)–(3), including a stop firing after a manual exit, a cross-lot sell attempt, and an unfilled exit restoring the stop.
- AC-4. An entry card is invalidated by timeout, by a price-band breach, and by a superseding message, each with no order sent.
- AC-5. An edit before the trade updates the card and requires re-approval. A correction or edit after the trade raises an alert with no action.
- AC-6. End of day: cutoff 1 creates a card; if it is unanswered, cutoff 2 closes the position after cancelling its stops and TPs.
- AC-7. Killing the process mid-order and restarting produces no duplicate order. Lots reconcile against the broker, and a mismatch blocks entries.
- AC-8. Feed silence longer than N s during market hours raises an alert and the "degraded" state.
- AC-9. An LLM outage falls back to the second provider, then to a raw review card. No message is lost.
- AC-10 (revised 2026-09-29). Only allowlisted active users can sign in with email + password; there is no self-registration; temporary passwords must be changed at first login. Manual entry and flatten require step-up (password) confirmation. Security-sensitive and user-initiated trading actions are recorded in an append-only audit log.
- AC-11. A literal span that is not present in the source text is always rejected.
- AC-12. All stage gates in §17 are passed in order before real money is used.
- AC-13. The advisory note can never change a card's fields or actions. A note containing a number not present in the validated fields or source messages is hidden (if the note guard is confirmed).
- AC-14. A signal with a missing underlying always produces a review card that requires explicit confirmation of the underlying.
- AC-15. "Trim half" on a 1-contract lot produces an exit card with a blank quantity and the "lot too small to trim" label. No order is sent until the user enters a quantity.
- AC-16. Bracket TP legs never exceed the order quantity. With fewer contracts than TP levels, contracts are assigned from TP1 onward.
- AC-17. "SPX" resolves to SPXW contracts through the symbol map. A missing-underlying suggestion is never approvable without explicit confirmation, even when the strike matches one underlying's live price.
- AC-18. Given the messages A1 "Bought GOOGL 357.5C @ 1.67", B2 "Bought TSLA 400C @ 2.10", A3 "Take half off @ 2.10" and B4 "Stop 1.50", delivered back to back:
  - A3 links to provider A's GOOGL position, and B4 links to provider B's TSLA position;
  - neither provider's context contains the other provider's messages or positions;
  - A3 is never processed against a provider book that does not yet contain A1.
- AC-19. An entry processed more than the staleness threshold after its post time produces no approvable card. An exit processed that late produces a card marked "late".
- AC-20. A message that fails processing 3 times produces a raw review card and an alert, and the same provider's next message is still processed.
- AC-21. After a message-API outage, every message posted during the outage is either redelivered or backfilled. None is lost or duplicated, and catch-up mode is shown until the backlog clears.
- AC-22. Load tests at the §12 design bursts meet p95 ≤ 5 s and p99 ≤ 8 s.
- AC-23. A database failover during market hours loses no approval, order intent, fill or ledger change. The system recovers within 5 minutes and reconciles against the broker before allowing entries.

---

## Appendix A — Decision log
C1 single user, own money · C2 Discord, multiple channels · C3 trusted providers · C4 US market · C5 every trading day, open to close · C6/C14 approval required for every order action · C7 no prediction · C8 messages cross-reference; corrections happen · C9 grouped per provider and company · C10 options (revised by C55) · C11 user's message API · C12 broker not chosen; account not decided · C13 push delivery · C15 tier sizing · C16 late correction = alert only · C17 web dashboard · C18 unanswered = expire · C19 expiration management (cutoff 1 card, cutoff 2 automatic close) · C20 overnight holds allowed · C21 desktop + phone push · C22 asymmetric approval validity · C23 risk limits · C24 separate lots per provider · C25 re-pricing exit with floor · C26 trim quantity entered by user · C27 loss rule dropped · C28 broker-held premium stops · C29 breakeven shows both prices · C30 prose conditions ignored · C31 provenance tiers · C32 one-click inferred cards · C33 non-actionable messages in feed · C34 staged go-live · C35 order invariants · C36 cloud LLM OK · C37 hosting TBD · C38 p95 ≤ 5 s · C39 public + email/password allowlist · C40 manual ops + kill switch · C41 feed health alert · C42 hardening · C43 medium scale · C44 cost ceiling deferred · C45 second LLM provider fallback · C46 recovery policy · C47 foreign positions read-only · C48 edit = correction · C49 system-held underlying stops · C50 bracket at entry · C51 fraction pre-fill · C52 soft exit card · C53 UTC+5:45, full session · C54 all authors parsed · C55 stocks (long) in MVP · C56 equal TP split · C57 author shown + mute · C58 push events · C59 off-hours messages → card at open · C60 no-price stock entry not actionable · C61 stock stop-market GTC at broker · C62 adds join same lot · C63 7-year retention · C64 LLM pre-label + user correct · C65 history export prerequisite · C66 stage gates · C67 dev/test split · C68 non-goals · C69 config tuned in shadow/paper · C70 spreads/short = future; ext-hours/multi-user out · C71 LLM context scope · C72 two ledgers · C73 provider ledger editable · C74 opposite-direction warning · C75 disabled card for limit-blocked entry · C76 stock sizing % of equity else tier · C77 flatten scope chosen at trigger · C78 no auto-correction + adjacent-strike warning · C79 missing underlying → review card.
· C80 LLM interprets only + advisory free-text note (provider-only context) · C81 chain + quote identity check with wide tolerance; scale normalization by quote.
· C82 fraction rounding to 0 → blank qty + "lot too small to trim" label · C83 TP legs = min(levels, qty), assigned from TP1, editable · C84 missing-underlying suggestion by live-price proximity, never a fixed strike threshold · C85 configurable symbol map, "SPX" → SPXW · C86 no ticker whitelist; validate against optionable list + live chain · C87 trade-history exports kept out of git and model context; only aggregates in docs.
· C88 per-provider ordered processing by post time; providers in parallel; approvals never block a provider · C89 out-of-order arrival → "late" flag + re-check → review card · C90 validity clocks start at post time; stale entries get no card, late exits still carded; catch-up mode · C91 message API must redeliver ≥ 10 min or offer backfill; gap detection · C92 no silent drops; poison → raw review card + alert · C93 RPO ≈ 0 for approvals/orders/fills/ledger, RTO ≤ 5 min; DB HA off the app host · C94 no Kafka/Redis; PostgreSQL is sole source of truth and queue; adopt only on measured triggers.

## Appendix B — Evidence
- **Sample messages:** the two channel snippets and the full day were supplied by the user during discovery. Observed patterns drove FR-5, FR-6, FR-12, FR-14, C48, C49, C50 and C51.
- **Realized gain/loss export:** the user's real brokerage export for Jan 1 – Sep 21, 2026. It has 5,483 closed tax-lot rows (closing dates Jan 2 – Sep 18) over 178 trading days, and is stored in `docs/data/` (git-ignored, C87).
  - It records the user's own manual trades, not provider messages. It calibrates scale, sizing and rules, but it is not replay data for §17.
  - Aggregates drove §3, §12, §18, FR-11, FR-12, FR-14, FR-15a and C82–C87. The full breakdown is in the TRD §16.
  - Limits: realized-only, so there are no open times. Rows are split per tax lot. The source broker is unconfirmed.
