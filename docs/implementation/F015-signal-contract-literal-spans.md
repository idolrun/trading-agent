# F015 — Signal Contract & Literal-Span Verification

## Objective
Define the single output contract every interpreter (stub, Jev, any LLM provider) must produce — `ParsedSignals` with per-field provenance — and the deterministic literal-span verifier that hard-rejects any literal value not present in the message text.

## Requirements covered
- PRD G1 (source of every field recorded), FR-5 (types), FR-6 (0..n signals per message), FR-7 (literal spans verified by code; mismatch = hard reject), §6 field provenance (literal / context / resolved), §10, AC-11, C31, C78 (no auto-correction).
- TRD §4.8 rule 1 (one output contract), §5.5 (`@after_agent` literal-span verification), Appendix A.3.

## Dependencies
- Depends on: F001
- Produces: `app/interpret/signal.py` types; `verify_literal_spans()`; JSON schema for LLM structured output.
- Used by: F016, F018, F023, F026, F032, F033, F048.

## Scope
```python
class Literal_(BaseModel):  kind: Literal["literal"]; raw: str; start: int | None = None; end: int | None = None; candidate_id: str | None = None
class Context_(BaseModel):  kind: Literal["context"]; source_message_id: int; raw: str | None = None
class Resolved(BaseModel):  kind: Literal["resolved"]; rule: str; inputs: dict[str, str]
Provenance = Annotated[Literal_ | Context_ | Resolved, Field(discriminator="kind")]
class F[T](BaseModel): value: T; source: Provenance            # generic field wrapper (verify Pydantic accepts PEP 695 syntax; else `class F(BaseModel, Generic[T])`)

class SignalFields(BaseModel):
    underlying: F[str] | None; expiry_token: F[str] | None; strike: F[Decimal] | None; right: F[Literal["call","put"]] | None
    entry_price: F[Decimal] | None; entry_price_low: F[Decimal] | None; entry_price_high: F[Decimal] | None
    price_unit_hint: Literal["per_share","per_contract"] | None      # "/con" seen (FR-12)
    size_tier: F[str] | None; qty_stated: F[int] | None
    stop_level: F[Decimal] | None; stop_basis: Literal["premium","underlying"] | None; candle_close: bool = False
    tp_levels: list[F[Decimal]] = []
    exit_fraction: Literal["all","half","third","quarter","other_stated","not_stated"] | None
    breakeven_stop: bool = False; stop_removed: bool = False; is_add: bool = False
    prose_condition: str | None                                      # shown, never enforced (C30)
    correction_target_message_id: int | None
class LinkHint(BaseModel): target: UUID | Literal["new_position","ambiguous"] | None; confidence: Decimal | None
class ParsedSignal(BaseModel):
    msg_type: MsgType; instrument: Literal["option","stock","unclear"] | None
    fields: SignalFields; link_hint: LinkHint; field_confidence: dict[str, Decimal] = {}
class ParsedSignals(BaseModel): signals: list[ParsedSignal]; interpreter: Literal["jev","llm","stub","none"]; schema_version: int = 1

def verify_literal_spans(parsed: ParsedSignals, text: str) -> list[SpanError]
```
Rules of `verify_literal_spans`:
- For every `Literal_`: if `start/end` given → `text[start:end] == raw` exactly; else `raw` must occur in `text` (case-sensitive exact substring) and code fills `start/end` with the first occurrence.
- `value` must be derivable from `raw` by the field's parser (e.g. `raw=".95"` → `Decimal("0.95")`; `raw="770c"` → strike 770 + right call); a value that is not the parse of `raw` is a mismatch (prevents "raw 112, value 115" — C78).
- Any `SpanError` ⇒ the whole message is a **hard reject** → review card path (FR-7). No partial acceptance, no fallback to another model (TRD §4.8 rule 3, "no answer shopping").
- `Context_` must reference a message present in the C71 context set (checked in F032/F033 where the context is known).

## Out of scope
Scale normalization (F018), confidence routing (F033).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/interpret/signal.py` | contract types + JSON schema export | model calls |
| `backend/app/interpret/spans.py` | `verify_literal_spans`, field raw parsers | DB |
| `backend/tests/unit/interpret/test_spans.py` | unit + hypothesis tests | — |

## Tests
- Unit: exact span OK; span offsets off by one → error; raw absent from text → error; `raw="112"`, `value=115` → error; `.95` → 0.95 ok; `140/con` raw with value 140 ok (scale later).
- Property (hypothesis): for random text and random substring chosen as raw, verification passes; for any raw not in text, it fails.

## Acceptance criteria
AC-11: a literal span not present in the source text is always rejected.

## Implementation steps
1. Types. 2. Raw parsers per field kind. 3. Verifier. 4. Tests. 5. Export JSON schema used by F032.

## Implementation notes (2026-09-28)
- Provenance classes are named `LiteralSpan`, `ContextSource`, `ResolvedSource` (doc: `Literal_`, `Context_`, `Resolved`) to avoid shadowing `typing.Literal`; discriminator `kind` unchanged. `F[T]` uses PEP 695 syntax (verified on Pydantic 2.13).
- All contract models use `extra="forbid"`: unknown keys from a model are schema errors.
- Raw parsers (`spans.parse_raw`): text fields (`underlying`, `expiry_token`, `size_tier`) compare after trimming, dropping a leading `$` and casefolding; price/level fields need exactly one number in the raw (thousands commas allowed; `140/con` → 140, scale is F018); `right` reads `call(s)`/`put(s)`/`c`/`p`/`770c`; `qty_stated` needs one integer.
- The span search itself is exact and case-sensitive; only the value comparison is normalized.
- `interpreter="stub"` is allowed in the contract; the `signals.interpreter` column (jev/llm/none) mapping is F026's.
