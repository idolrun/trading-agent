# F017 — Provider Context Loader (C71 scope)

## Objective
Build the exact, provider-scoped context an interpreter may see for one message — and make it structurally impossible to include the user's lots, balances or P&L.

## Requirements covered
- PRD C71 (context = reply parent, author's open positions, recent messages from the author and anyone @mentioned), FR-8 (LLM never receives the user's lots, balances, or P&L), §6 (positions owned by one provider; links never cross providers), AC-18 (neither provider's context contains the other provider's messages or positions).
- TRD §2.1 `load_context`, §4.2 Jev state, §4.5, §5.2 (no free-running chat history; rebuilt from Postgres each run), §15.2 (`context.load` accessor — Redis boundary).

## Dependencies
- Depends on: F003, F005
- Produces: `ProviderContext` model, `load_context(session, envelope, *, profile) -> ProviderContext`.
- Used by: F021, F032, F033, F034, F035.

## Scope
```python
class ContextMessage(BaseModel): id: str; author_id: str; at_et: str; text: str
class BookEntry(BaseModel): id: UUID; summary: str       # e.g. "GOOGL 357.5C exp 2026-09-28, entered 1.67 at 09:48, trimmed 50%"
class ProviderContext(BaseModel, extra="forbid"):
    now_et: str
    message: ContextMessage & {channel_id, reply_to}
    reply_parent: ContextMessage | None          # any author; read-only context
    recent_author_messages: list[ContextMessage] # same provider, posted_at < message.posted_at, newest N (config.context_recent_messages)
    mentioned_messages: list[ContextMessage]     # newest N from each @mentioned author, before this post time
    provider_book: list[BookEntry]               # this provider's open/trimmed positions only
    provider_profile: dict[str, list[str]]       # lexicon/habits/aliases from LangGraph store (F035 passes it; {} before)
```
- "Recent" is by **post time**, never arrival order, and excludes messages posted after the current one (keeps replay deterministic).
- The model has `extra="forbid"` and no field capable of carrying portfolio data; `FORBIDDEN_KEYS = {"lot","lots","balance","pnl","equity","avg_price","qty_held"}` checked by a unit test over the JSON schema and by the LLM scope guard (F032).
- Book summaries are rendered by code (deterministic format, `Decimal` → plain string), byte-stable for prompt caching (TRD §4.8 rule 7).

## Out of scope
Store reads (F035 wires `PostgresStore` and passes `profile`). User portfolio (never).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/interpret/context.py` | `ProviderContext`, `load_context`, book summary renderer | lots/portfolio queries |
| `backend/app/interpret/context_repo.py` | the three read queries (recent by provider, mentioned, open book) | business rules |
| `backend/tests/unit/interpret/test_context_schema.py`, `backend/tests/integration/interpret/test_load_context.py` | tests | — |

## Tests
- Unit: schema contains no forbidden key; `extra` fields rejected.
- Integration (AC-18 fixture): after A1 and B2 applied, context for A3 contains only A's messages and A's GOOGL position; context for B4 only B's. Reply from B to A1 → B's context includes A1 as `reply_parent` only, B's book unaffected.
- Recent messages respect post-time cut-off with out-of-order inserts.

## Acceptance criteria
AC-18 context-isolation clause proven; FR-8 structurally enforced.

## Implementation steps
1. Models + forbidden-key test. 2. Queries. 3. Summary renderer. 4. `load_context`. 5. Tests.
