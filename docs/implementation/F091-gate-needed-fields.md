# F091 — Gate only the fields the message type reads

## Objective
Implements ranked fix #1 from `docs/reviews/signal-bottleneck-2026-10-12.md` §4.1. Before this change, a message settled only if every answer the mapper used cleared `t_field`, including every `role_nN` and the instrument, even when the decision never read that answer. Two of the top doubt reasons were:
- `instrument: confidence < t_field` (545);
- `role_nN` (367).

## Decisions (2026-10-13)
- **Number roles** (`READ_ROLES`). Each type lists the roles it reads downstream:
  - entry/add/stock_entry/conditional and other unlisted types read every role except `share_or_contract_qty`, which C105 says is never read;
  - exits and `tp_hit_notice` read `strike`;
  - stops read `strike` + `stop_level`;
  - `tp_set` reads `strike` + `take_profit_level`.

  `strike` stays on every follow-up because `correlation.inconsistency` uses it to catch a wrong link. A number is ignored when it is either:
  - surely a role the type doesn't read;
  - unsure, but its combined probability of not being a read role is at least `t_field`.

  This generalizes the old "surely junk" rule.
- **`instrument`** blocks only entry/add/stock_entry/conditional. On a follow-up, an unsure instrument is dropped; an unstated one contradicts nothing.
- **`exit_fraction`** blocks only a trim, because "all" turns it into a close of the lot. Elsewhere an unsure fraction is dropped.
- **Unchanged on purpose:**
  - On follow-ups, ticker, expiry and right stay gated. The stated contract is the only guard against a wrong link when one candidate remains (F077). The resolver never narrows to nothing, so a code-only replacement would need the position's underlying inside the mapper. That is out of scope here.
  - `stop_basis` with a stop, the stop flags, the condition gate and the correction gate are unchanged.
- `ROUTING_VERSION` 3 → 4, so the question-set fingerprint is now `jev_questions@b3e2aed87d20`. Replay before re-pinning.

## Where
`interpret/jev/mapping.py` (`READ_ROLES`, `INSTRUMENT_GATED`, the role loop and the field loop).

## Tests
`tests/unit/interpret/test_jev_mapping.py`:
- an unsure price on a trim is noise, but on an entry it still blocks;
- a maybe-strike blocks on any follow-up;
- a trim's unsure fraction blocks; a full exit's doesn't;
- instrument blocks an opening, not a follow-up;
- a follow-up's unsure ticker still blocks.

## To measure (Milestone A gate)
Run the F082 replay on `trading_measure`. Required: wrong links 0 and false feed-only 0. Also report the change in review rate and in the `instrument` / `role_nN` doubt counts.
