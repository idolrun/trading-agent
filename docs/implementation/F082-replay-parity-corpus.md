# F082 — Replay parity, a labeled real corpus, and the measurement

## Objective
The replay decides links the way production does, and the 519-message real corpus has labels. That makes the safety numbers measurable: wrong-link rate, false feed-only rate, review rate, latency and path shares, before and after the Jev hardening (F077–F081).

## Before (audit M5)
- The replay used `Resolution.unresolved` over a label "ledger" that kept summaries only. Reply, thread, stated-contract and sole-candidate resolution never ran.
- The F070 follow-up fast path never fired in replay.
- Links were scored with a ticker-only cross-check.
- No labels existed anywhere, so wrong-link and false feed-only rates could not be measured.

## Replay parity (`evalkit/replay.py`)
- **Positions:** the label `Ledger` keeps every labeled position as an in-memory `ProviderPosition`, never added to a session.
  - The status follows labeled follow-ups (trim → trimmed, exit → closed).
  - Setups are opened by `watch` / `conditional` messages that name a ticker.
  - Expiry comes from `markettime.resolve_expiry_token`.
- **History:** the ledger also keeps which message opened or acted on which position (the replay's `message_links`), the reply chain, and thread membership.
- **Production code on that history:**
  - the book through the production `summarize` (same text the model sees live);
  - the lane book filter (live positions + today's setups);
  - `Ledger.resolution(env)`: the production pure `resolve_context` with reply hops, thread positions and `Evidence.of`, plus re-entry priors — what `ledger.repo.load_resolution` does from the database.
- **Wiring:** `GraphDeps.resolution` (replay only) feeds it to `load_context`. The fast path, the Jev link options, the feed guard and every routing rule then see what they would see live.
- **Link scoring:** production `validate.normalize.identify` + `correlation.resolve_link` over the ledger's lane states and the run's resolution.
  - `correct` / `wrong`: the position code links to vs the labeled one.
  - `ambiguous`: no link made — a review card, or the follow-up was not recognized.
- **The one remaining difference from live:** history comes from labels, not from database rows.

## Labels
- `LabelSource.ASSISTANT` (migration `0029`) keeps these labels apart from a person's.
- `evalkit import-labels <file>` (`import_export.import_labels`):
  - validates every label against the API `Label` schema;
  - expands `"provider_position_id": "open:<event key>[:idx]"` to the ledger id of that opening;
  - is idempotent.
- `docs/data/labels-assistant-2026-10.json` holds 519 labels for the `trading_replay` corpus, written by Claude. It is local only: `docs/data/` is gitignored, like the private export it labels; it is also imported into `trading_measure.eval_labels`.
  - message types for rows 1–130 follow the hand-made key `docs/reviews/jev-vs-gold-2026-09-30.csv`;
  - fields are only values the text states literally;
  - 112 follow-ups link to the message that opened their position;
  - 26 judgment calls are listed under `spot_check` for the user.
- **User review (2026-10-08):** of the 26 spot-checks, 18 kept, 6 corrected (imported as source `manual`), 2 unsure (rows 371, 481). The replay ledger now opens a position for an add whose entry predates the corpus (row 16 → row 115). Re-run on the reviewed labels: wrong links 0/115, false feed-only 0/187 (`docs/reviews/jev-hardening-2026-10.md`).
- Metric changes (`evalkit/metrics.py`):
  - `false_feed_only` and `false_feed_only_rate` (a labeled instruction that ended feed-only);
  - outcome counts (`interpret_outcome`);
  - tickers compare as symbols (`$spy` == `SPY`): the interpreter keeps the provider's spelling and validation normalizes it.

## Measurement (`docs/reviews/jev-hardening-2026-10.md`, provisional)

| Metric (519 messages, real Jev `jev-1.13.0`) | Before (b0be248) | After |
|---|---:|---:|
| wrong links | 0 / 112 | 0 / 112 |
| false feed-only | 2 / 185 | 0 / 185 |
| links settled correctly | 0 | 9 |
| settled by code | 9 | 18 |
| review rate | 72.4% | 73.0% |
| Jev ok-call p50 / p95 / p99 | — | 375 / 498 / 574 ms |
| Jev timeouts at 700 ms | — | 1–5% |

## Tests
- `tests/integration/evalkit/test_replay.py`:
  - replay resolves like live: a sole position and a reply are settled by the fast path; two positions with no evidence → review;
  - link scoring separates a wrong code link from an ambiguous one;
  - `false_feed_only` is counted;
  - `import-labels` expands `open:` links and is idempotent.
- `tests/unit/evalkit/test_metrics.py`: symbol comparison; false feed-only and outcomes.
