# F073 — One context packet, production/evaluation parity

## Objective
Every interpretation path builds the same small, lane-scoped context packet with the same window. That makes evaluation results describe production.

## Before (audit)
- `context_recent_messages` had no default.
- The live graph read `cfg.context_recent_messages or 0`, so a model saw **0** earlier messages.
- Evalkit prelabel used `or 5` and the labeling API used `RECENT_DEFAULT = 5`.

## What changed
| Area | Change |
|---|---|
| `runtime_config/schema.py` | `context_recent_messages: PositiveInt = 5`, explicit (null is rejected). Migration `0025` rewrites a stored null to 5, with an audit row. `config.example.json` uses 5. |
| Callers | The fallbacks are removed from `graph/message_graph.py` (`or 0`), `evalkit/cli.py` (`or 5`) and `api/routes/labels.py` (`RECENT_DEFAULT`). All read the configured value through `load_context`. Replay runs the same message graph, so it gets the same context; only the book differs, and it comes from the label ledger. |
| Context packet (`interpret/context.py`) | `ProviderContext` = current message (+ `thread_id`), reply parent (read-only), the lane author's last N messages, the lane book (live + today's setups, with status), `link_candidates` (the resolver's narrowing, F069), and **`recent_signal_events`**: the lane's last N position events caused by earlier messages, e.g. "trim SPY 580P". No other channel, no other author, no user portfolio. |
| Jev state | Adds `message.thread` and `recent_signal_events`. The scope guard (`scope_violation`) still runs on every state. |

## Tests
- `tests/unit/runtime_config/test_schema.py`: default 5; null rejected.
- `tests/integration/interpret/test_context_packet.py`: the packet holds only the lane's messages, book and events (newest first); another channel's QQQ and another author's TSLA are absent; labeling reads the live window.
- Existing: `test_paper_flow.py::test_no_model_ever_sees_the_account` (Jev/Laya requests carry no account data) and the context schema forbidden-key check.
