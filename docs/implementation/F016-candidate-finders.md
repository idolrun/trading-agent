# F016 — Candidate Finders

## Objective
Deterministically over-find every ticker, number, expiry token and fraction word in a message, with stable IDs and exact character offsets, so that models only **choose** among values copied from the text and never write numbers themselves.

## Requirements covered
- PRD C78 (no invented numbers), FR-7 (literal spans), FR-12 (price notations "140/con", "260", "3.2", ".95"), FR-14 (strike without ticker, e.g. "7775c"), C86 (no whitelist: over-find tickers generically).
- TRD §1.2, §4.1 (code finds candidates, Jev picks), §4.2 `candidates` block, §16.2 #1, #5.

## Dependencies
- Depends on: F015
- Produces: `find_candidates(text) -> Candidates` (pure).
- Used by: F033 (Jev question options), F032 (hints), F017 (state), F048 (eval diagnostics).

## Scope
```python
class Candidate(BaseModel, frozen=True): id: str; text: str; start: int; end: int
class Candidates(BaseModel): tickers: list[Candidate]; numbers: list[Candidate]; expiry_tokens: list[Candidate]; fraction_words: list[Candidate]
```
IDs: `t1…`, `n1…`, `e1…`, `f1…` in text order (deterministic, used by Jev `role_<nX>` questions).
| Kind | Over-find rule (case-insensitive) | Examples |
|---|---|---|
| tickers | `\$?[A-Za-z]{1,5}` word tokens, minus a small stop-list of common English/trading words (`the, and, sl, tp, stop, call, put, calls, puts, dte, con, lotto, half, all, in, out, at, for, …`) kept in code with tests | `spy`, `$TSLA`, `googl` |
| numbers | `\d+(?:\.\d+)?|\.\d+` with optional attached suffix `c|p|C|P|/con|%|k` kept inside the candidate text | `770c`, `.95`, `140/con`, `357.5C`, `50%` |
| expiry_tokens | `\d+\s?dte`, `0dte`, weekday names/abbrevs, `\d{1,2}/\d{1,2}(/\d{2,4})?`, month-name + day | `0dte`, `fri`, `10/16`, `oct 16` |
| fraction_words | `half, third, quarter, all, rest, some, most, 1/2, 1/3, 1/4, \d+%` | `half`, `1/3` |
Numbers that form an expiry token (`10/16`) are reported as expiry tokens, not numbers (overlap resolution: longest match wins, documented and tested).

## Out of scope
Deciding roles (Jev/LLM). Symbol validity (F019).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/interpret/candidates.py` | regexes, stop-list, `find_candidates` | model code, DB |
| `backend/tests/unit/interpret/test_candidates.py` | table tests incl. TRD §4.2 example | — |

## Tests (unit)
- TRD §4.2 example `"spy 770c .95 lotto, sl 769 spy"` → tickers `spy`(0), `spy`(26); numbers `770c`(4), `.95`(9), `769`(22).
- `"7775c 0dte"` → numbers `7775c`, expiry `0dte`, **no** ticker (FR-14 path).
- `"Bought GOOGL 357.5C @ 1.67"`, `"Take half off @ 2.10"`, `"140/con"`, `"3.2"`, `"SPX 7000p 10/16"`.
- Property: every candidate satisfies `text[start:end] == candidate.text`.

## Acceptance criteria
Every numeric value in a test corpus of synthetic messages appears as a candidate; offsets exact; deterministic IDs.

## Implementation steps
1. Regexes + stop-list. 2. Overlap resolution. 3. ID assignment. 4. Tests.
