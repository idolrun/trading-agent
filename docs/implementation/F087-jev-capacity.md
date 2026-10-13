# F087 — Jev capacity

## Objective
Implements ranked fix #7 from `docs/reviews/signal-bottleneck-2026-10-12.md` §6.1. Two problems are measured there:
- Jev's 700 ms timeout sat on the vendor's p99, so about 11 of every 100 live calls timed out.
- The limiter's size was fixed at boot, so raising it needed a restart.

## Decisions (2026-10-13)
- Defaults change from `jev.timeout_ms` 700 / `budget_ms` 1200 to **900 / 1500**.
  - The 1,500 ms budget still allows the single 429/529 retry, as long as the first attempt failed within about 250 ms. The rule is: `spent + 100 + queue_timeout_ms + timeout_ms ≤ budget_ms`.
  - With 900/1200, the retry could never happen.
- `ProviderLimiter` now reads `llm.concurrency[provider]` on every call and builds a new semaphore when the size changes. Calls already holding a slot finish on the old semaphore, so for a moment up to old + new calls can run.
- **No hedged second request.** Jev's rate limit and price are still unknown (TRD §7.4 [VERIFY]), and a hedge would double both.
- The stored live config keeps whatever values it holds now (700 / 1200 at the time of writing). The new defaults apply only to keys that are unset. To take effect live, change them in Settings → Config (audited).

## Where
| Area | Change |
|---|---|
| `llm/limiter.py` | size per call; new semaphore on change |
| `runtime_config/schema.py`, `config.example.json` | Jev defaults 900 / 1500 |
| Frontend `features/config/types.ts` | defaults for a new document |

## Tests
- `tests/unit/llm/test_limiter.py`: unset size uses the default; a bigger size applies on the next call.
- `tests/integration/interpret/test_jev_client.py`: the budget recorded on each call uses the new defaults.
- Frontend `config.test.tsx`.

## Not measured yet
Still to measure: the live timeout rate at 900 ms, and the queue-timeout rate during bursts after raising `llm.concurrency.typesafe`. The vendor's real rate limit is still needed before raising it far.
