# F088 — Laya off the live path

## Objective
Implements ranked fix #8 from `docs/reviews/signal-bottleneck-2026-10-12.md` §5. Laya, the local model, is the wrong fallback when latency matters:
- it named the right message type 27% of the time (`laya-evaluation-2026-09-30.md`);
- it takes 3–10 s and has one GPU slot, which answers `BUSY` under load;
- it held the lane slot the whole time it ran.

## Decision (2026-10-13)
- `laya.enabled` now defaults to **false**. The graph already routes a Jev model failure to the LLM (or to review, when no LLM is configured) whenever Laya is off or unavailable, so the routing needed no change.
- Laya stays available as an opt-in on the Config page.
- The stored live config still has `laya.enabled: true`. Turn it off in Settings → Config (audited) for the change to take effect live. Config is read live, so no restart is needed.
- `backend/README.md` already said "off by default". It is now true.

## Where
`runtime_config/schema.py`, `config.example.json`, frontend `features/config/types.ts`.

## Tests
- `tests/unit/runtime_config/test_schema.py`: the default is off.
- Frontend `config.test.tsx`: clicking turns Laya on.
- The graph tests that run Laya set it on explicitly.
