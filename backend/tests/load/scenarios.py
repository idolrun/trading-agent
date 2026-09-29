"""Load-suite constants: PRD §12 thresholds, simulated model latency, scenario sizes, env knobs.

Everything a reader might want to tune lives here. Environment variables (all optional):

    LOAD_DAY_MESSAGES     messages in the synthetic day            (default 400)
    LOAD_DAY_SPAN_S       length of the synthetic day, in seconds  (default 1200)
    LOAD_1X_SLICE_S       seconds of the day replayed at 1x        (default 90)
    LOAD_SPEEDS           replay speeds, comma separated           (default "1,10,50")
    LOAD_SEED             generator + latency seed                 (default 20260928)
    LOAD_SLOW_PCT         % of actionable messages Jev is unsure of (default 20)
    LOAD_LATENCY_SCALE    multiplies every simulated model latency (default 1.0)
    LOAD_JEV_MEDIAN_MS / LOAD_JEV_P95_MS / LOAD_LLM_MEDIAN_MS / LOAD_LLM_P95_MS
    LOAD_REPORT_DIR       where load-<date>.md is written          (default <repo>/docs/reviews)
    LOAD_FAILOVER_DOWN_S  test 6: seconds Postgres stays stopped   (default 10)
"""

import math
import os
import random
from dataclasses import dataclass

# --- Pass criteria (PRD §12, TRD §12) ---
E2E_P95_S = 5.0
E2E_P99_S = 8.0
QUEUE_WAIT_P95_S = 1.0
INSERT_P99_MS = 50.0

Z95 = 1.6448536269514722  # standard normal 95th percentile


def env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Lognormal:
    """Seconds-valued lognormal fitted to a median and a p95, clamped to `cap_s`."""

    median_ms: float
    p95_ms: float
    cap_ms: float

    def sample(self, rng: random.Random) -> float:
        sigma = math.log(self.p95_ms / self.median_ms) / Z95
        ms = math.exp(math.log(self.median_ms) + sigma * rng.gauss(0, 1))
        return min(ms, self.cap_ms) / 1000


# PLACEHOLDERS until the F048 bench numbers are exported from `model_calls`
# (`SELECT provider, model, role, percentile_cont(...) ... latency_ms`; see README).
# Jev: TRD §22 design target is p95 <= 300 ms, and `jev.timeout_ms` is 300 ms by default, so a
# sample above that would just time out into the slow path; the cap keeps samples served (under
# load, event-loop scheduling still pushes a few past 300 ms: `typesafe:timeout` in the report).
# LLM extract: capped below the default `per_attempt_cap_ms` (2000) so a sample never times out
# (a timed-out attempt is a different scenario: fallback / raw review + parser_degraded).
JEV_DEFAULT = Lognormal(median_ms=120, p95_ms=260, cap_ms=290)
LLM_DEFAULT = Lognormal(median_ms=900, p95_ms=1800, cap_ms=1950)


def latencies() -> tuple[Lognormal, Lognormal]:
    """(jev, llm) with env overrides and LOAD_LATENCY_SCALE applied."""
    scale = env_float("LOAD_LATENCY_SCALE", 1.0)
    jev = Lognormal(
        env_float("LOAD_JEV_MEDIAN_MS", JEV_DEFAULT.median_ms) * scale,
        env_float("LOAD_JEV_P95_MS", JEV_DEFAULT.p95_ms) * scale,
        JEV_DEFAULT.cap_ms * max(scale, 1.0),
    )
    llm = Lognormal(
        env_float("LOAD_LLM_MEDIAN_MS", LLM_DEFAULT.median_ms) * scale,
        env_float("LOAD_LLM_P95_MS", LLM_DEFAULT.p95_ms) * scale,
        LLM_DEFAULT.cap_ms * max(scale, 1.0),
    )
    return jev, llm


# --- Scenario sizes ---
SEED = env_int("LOAD_SEED", 20260928)
DAY_MESSAGES = env_int("LOAD_DAY_MESSAGES", 400)
DAY_SPAN_S = env_float("LOAD_DAY_SPAN_S", 1200.0)
SLICE_1X_S = env_float("LOAD_1X_SLICE_S", 90.0)
SPEEDS = tuple(float(s) for s in os.environ.get("LOAD_SPEEDS", "1,10,50").split(","))
SLOW_PCT = env_int("LOAD_SLOW_PCT", 20)
DAY_PROVIDERS = 12

# TRD §12 test 2: 50 messages in 5 s across 10 providers
CROSS_MESSAGES, CROSS_WINDOW_S, CROSS_PROVIDERS = 50, 5.0, 10
# TRD §12 test 3: 5 messages in 3 s from one provider
SINGLE_MESSAGES, SINGLE_WINDOW_S = 5, 3.0

# Option contracts that exist in tests/fixtures/market (expiry 10/2): root, strike, right, ask.
CONTRACTS: tuple[tuple[str, str, str, str], ...] = (
    ("SPY", "577", "c", "5.04"),
    ("SPY", "578", "c", "4.61"),
    ("SPY", "577", "p", "1.75"),
    ("QQQ", "493", "c", "4.13"),
    ("QQQ", "494", "c", "3.61"),
    ("QQQ", "493", "p", "1.51"),
    ("GOOGL", "350", "c", "5.63"),
    ("META", "695", "c", "18.34"),
    ("MU", "116", "c", "2.85"),
    ("TSLA", "395", "c", "7.77"),
    ("TSLA", "397.5", "c", "5.39"),
)

# TRD §12 test 4: a 10-minute Message API outage, then backlog delivery
OUTAGE_S, OUTAGE_PROVIDERS = 600.0, 8
# TRD §12 test 6: Postgres stopped this long mid-burst (the DB is shared: keep it short)
FAILOVER_DOWN_S = env_float("LOAD_FAILOVER_DOWN_S", 10.0)
RTO_S = 300.0  # PRD §12 / AC-23: recovery within 5 minutes
