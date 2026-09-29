"""Replay metrics (PRD §17, TRD §12): pure functions over `Case`s, no I/O.

A `Case` pairs what a message was labeled with what the interpreter answered. Signals match by
`idx`. Definitions:
- actionable message: at least one labeled signal of an actionable type;
- recall: labeled actionable signals answered with the same type / all labeled actionable
  signals (a raw review or a missing signal is a miss); precision: the same matches / answered
  actionable signals;
- normal card: an answered actionable signal whose fields are all literal or resolved by code
  (none from context). Validation is `skipped` in replay, so "validated" is not part of it;
- field accuracy: over normal cards paired with a labeled actionable signal, the fields (union
  of labeled and answered non-empty values) that match exactly / all such fields;
- wrong contract: a normal card whose underlying, expiry, strike or right differs from the label.
"""

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.interpret.models import MsgType

NON_ACTIONABLE = frozenset(
    {
        MsgType.CHATTER,
        MsgType.COMMENTARY_PNL,
        MsgType.WATCH,
        MsgType.TP_HIT_NOTICE,
        MsgType.CONDITIONAL,
        MsgType.OUT_OF_SCOPE_STRATEGY,
        MsgType.UNPARSED,
    }
)
CONTRACT_FIELDS = ("underlying", "expiry_token", "strike", "right")
NONE = "none"  # confusion axis: no signal on that side
_NUMBER = re.compile(r"-?\d+(\.\d+)?")


@dataclass(frozen=True)
class Sig:
    msg_type: str
    fields: dict[str, Any] = field(default_factory=dict)  # plain values, unset ones left out
    normal: bool = True  # answers only: no field taken from context


@dataclass(frozen=True)
class Case:
    provider_id: int
    expected: dict[int, Sig]  # by idx
    predicted: dict[int, Sig] | None  # None: no interpretation (raw review)
    path: str  # fixture | feed_only | assemble | slow_path
    latency_ms: float


def is_actionable(msg_type: str) -> bool:
    return msg_type not in NON_ACTIONABLE


def percentile(values: list[float], q: float) -> float:
    """Nearest rank."""
    ordered = sorted(values)
    return ordered[max(math.ceil(q / 100 * len(ordered)) - 1, 0)]


def canon(v: Any) -> Any:
    """Numbers compare by value ("357.50" == 357.5); everything else exactly."""
    if isinstance(v, list):
        return [canon(i) for i in v]
    if isinstance(v, int | float | Decimal) and not isinstance(v, bool):
        return Decimal(str(v)).normalize()
    if isinstance(v, str) and _NUMBER.fullmatch(v):
        return Decimal(v).normalize()
    return v


def _present(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        k: canon(v)
        for k, v in fields.items()
        if v is not None and v is not False and v != [] and v != ""
    }


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def compute(cases: list[Case], *, cost_usd: float = 0.0) -> dict[str, Any]:
    """JSON-ready metrics for `eval_runs.metrics`. Ratios are None when undefined (no data)."""
    actionable = sum(any(is_actionable(s.msg_type) for s in c.expected.values()) for c in cases)
    expected_actionable = answered_actionable = hits = 0
    normal_cards = fields_total = fields_correct = wrong_contracts = 0
    confusion: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for c in cases:
        answered = c.predicted or {}
        for idx in sorted(c.expected.keys() | answered.keys()):
            want, got = c.expected.get(idx), answered.get(idx)
            confusion[want.msg_type if want else NONE][got.msg_type if got else NONE] += 1
            want_a = want is not None and is_actionable(want.msg_type)
            got_a = got is not None and is_actionable(got.msg_type)
            expected_actionable += want_a
            answered_actionable += got_a
            if want is None or got is None or not (want_a and got_a):
                continue
            hits += want.msg_type == got.msg_type
            if not got.normal:
                continue
            normal_cards += 1
            a, b = _present(want.fields), _present(got.fields)
            names = a.keys() | b.keys()
            fields_total += len(names)
            fields_correct += sum(a.get(n) == b.get(n) for n in names)
            wrong_contracts += any(n in b and a.get(n) != b[n] for n in CONTRACT_FIELDS)
    by_path: defaultdict[str, list[float]] = defaultdict(list)
    for c in cases:
        by_path[c.path].append(c.latency_ms)
    return {
        "messages": len(cases),
        "actionable": actionable,
        "non_actionable": len(cases) - actionable,
        "providers": len({c.provider_id for c in cases}),
        "actionable_recall": _ratio(hits, expected_actionable),
        "actionable_precision": _ratio(hits, answered_actionable),
        "normal_cards": normal_cards,
        "field_accuracy": _ratio(fields_correct, fields_total),
        "wrong_contracts": wrong_contracts,
        "confusion": {k: dict(v) for k, v in sorted(confusion.items())},
        "slow_path_share": _ratio(len(by_path.get("slow_path", [])), len(cases)),
        "latency_ms": {
            path: {"n": len(v), "p50": percentile(v, 50), "p95": percentile(v, 95)}
            for path, v in sorted(by_path.items())
        },
        "cost_usd": cost_usd,
    }
