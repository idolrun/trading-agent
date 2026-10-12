"""Seeded synthetic trading-day message stream (TRD §12: synthetic until a real export exists).

`generate()` is pure and deterministic: the same arguments always give the same list. Each
`DayMessage` carries what the model fakes need to answer *correctly* for its text:

- `signal`: the `LlmSignal`-shaped dict a correct extraction returns (raw substrings of `text`);
- `path`: "fast" (Jev is confident: assemble in code) or "slow" (Jev is unsure: LLM extract),
  a pure function of the text so every message with the same text takes the same route;
- `target`: for follow-ups, the "SPY 577C" prefix of the book summary the message acts on.

Mix (defaults): ~60% feed (chatter + commentary), the rest position stories: an entry (about a
quarter arrive as a "pack": entry, stop, targets within seconds), then adds / trims / stop moves /
an exit at later times; about 40% of follow-ups are replies to the entry message.
"""

import random
import zlib
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Literal

from tests.load.scenarios import CONTRACTS, SLOW_PCT

Kind = Literal[
    "chatter", "commentary", "entry", "add", "trim", "exit", "stop_move", "stop_set", "tp_set"
]
Path = Literal["fast", "slow"]
PROVIDER_BASE = 710_000_000_000_000_000
CHATTER = (
    "this market is wild today",
    "lunch break, back in a bit",
    "anyone else seeing this chop",
    "good morning everyone",
    "patience pays in this tape",
    "not chasing here",
    "vol is crazy right now",
    "eyes on the fed guys",
    "that was a fun one",
    "quiet tape, staying patient",
    "gm all, coffee first",
    "love the discipline in this room",
    "tough one, moving on",
    "what a bounce",
    "sitting on hands for now",
)
Contract = tuple[str, str, str, str]  # root, strike, right (c/p), ask


@dataclass(frozen=True)
class DayMessage:
    offset_s: float
    provider: int
    text: str
    kind: Kind
    signal: dict[str, Any]
    path: Path = "fast"
    target: str | None = None  # "SPY 577C": prefix of the position's book summary
    reply_to: int | None = None  # index (in the returned list) of the message replied to
    idx: int = 0


def path_for(text: str, kind: Kind, slow_pct: int = SLOW_PCT) -> Path:
    """Stable per text. Chatter is rarely unclear to Jev; actionable text more often."""
    pct = slow_pct / 5 if kind in ("chatter", "commentary") else slow_pct
    return "slow" if zlib.crc32(text.encode()) % 1000 < pct * 10 else "fast"


def _f(raw: str) -> dict[str, str]:
    return {"raw": raw}


def _px(value: float) -> str:
    return f"{value:.2f}"


def _stop(strike: str, right: str) -> str:
    return _px(float(strike) + (-2 if right == "c" else 2)).rstrip("0").rstrip(".")


def _pos(c: Contract, name: str) -> dict[str, Any]:
    """Contract fields shared by every message that names the option."""
    _, strike, right, _ = c
    return {
        "underlying": _f(name),
        "strike": _f(f"{strike}{right}"),
        "right": _f(f"{strike}{right}"),
    }


def _target(c: Contract) -> str:
    return f"{c[0]} {c[1]}{c[2].upper()}"


def _ticker(rng: random.Random, root: str) -> str:
    return root.lower() if rng.random() < 0.6 else root


def _entry(rng: random.Random, c: Contract, with_stop: bool) -> tuple[str, dict[str, Any], str]:
    name = _ticker(rng, c[0])
    signal: dict[str, Any] = {
        "msg_type": "entry",
        "instrument": "option",
        **_pos(c, name),
        "expiry_token": _f("10/2"),
        "entry_price": _f(c[3]),
        "links_to": "new_position",
    }
    if with_stop:
        stop = _stop(c[1], c[2])
        signal |= {"stop_level": _f(stop), "stop_basis": "underlying"}
        return f"{name} {c[1]}{c[2]} 10/2 {c[3]} lotto, sl {stop} {name}", signal, _target(c)
    return f"Bought {name} {c[1]}{c[2]} 10/2 @ {c[3]}", signal, _target(c)


def _follow_up(rng: random.Random, kind: Kind, c: Contract) -> tuple[str, dict[str, Any]]:
    name = _ticker(rng, c[0])
    ask = float(c[3])
    link = {"links_to": "position"}  # resolved against the book by the fake
    match kind:
        case "add":
            px = _px(ask * 1.1)
            return f"Adding {name} {c[1]}{c[2]} @ {px}", {
                "msg_type": "add",
                "instrument": "option",
                **_pos(c, name),
                "entry_price": _f(px),
                "is_add": True,
                **link,
            }
        case "trim":
            return f"Sold half {name} {c[1]}{c[2]} @ {_px(ask * 1.4)}", {
                "msg_type": "trim",
                "instrument": "option",
                **_pos(c, name),
                "exit_fraction": "half",
                **link,
            }
        case "exit":
            return f"Out of {name} {c[1]}{c[2]} @ {_px(ask * 1.2)}", {
                "msg_type": "full_exit",
                "instrument": "option",
                **_pos(c, name),
                "exit_fraction": "all",
                **link,
            }
        case "stop_move":
            stop = _stop(str(float(c[1]) + (1 if c[2] == "c" else -1)), c[2])
            return f"Move stop to {stop} on {name}", {
                "msg_type": "stop_move",
                "underlying": _f(name),
                "stop_level": _f(stop),
                "stop_basis": "underlying",
                **link,
            }
        case "stop_set":
            stop = _stop(c[1], c[2])
            return f"Stop {stop} {name}", {
                "msg_type": "stop_set",
                "underlying": _f(name),
                "stop_level": _f(stop),
                "stop_basis": "underlying",
                **link,
            }
        case "tp_set":
            tp1, tp2 = _px(ask * 1.5), _px(ask * 2)
            return f"TP {tp1} {tp2} {name}", {
                "msg_type": "tp_set",
                "underlying": _f(name),
                "tp_levels": [_f(tp1), _f(tp2)],
                **link,
            }
    raise ValueError(kind)


def _feed(rng: random.Random, commentary: bool) -> tuple[Kind, str, dict[str, Any]]:
    if commentary:
        c = rng.choice(CONTRACTS)
        text = f"{_ticker(rng, c[0])} {rng.choice((20, 35, 50, 80))}% off the lows nice"
        return "commentary", text, {"msg_type": "commentary_pnl"}
    return "chatter", rng.choice(CHATTER), {"msg_type": "chatter"}


def _weights(n: int, skew: float) -> list[float]:
    return [1 / (i + 1) ** skew for i in range(n)]


def _shaped(rng: random.Random, span_s: float, flat: bool) -> float:
    """Intraday shape: heavier open (first 8%) and close (last 12%)."""
    r = rng.random()
    if flat or r < 0.6:
        return rng.uniform(0, span_s)
    if r < 0.85:
        return rng.uniform(0, span_s * 0.08)
    return rng.uniform(span_s * 0.88, span_s)


# Follow-up chain: (kind, probability, per-story). Delays are fractions of the span so a 5 s
# burst and a 20 min day both keep their stories inside the window.
CHAIN: tuple[tuple[Kind, float], ...] = (
    ("add", 0.35),
    ("trim", 0.55),
    ("stop_move", 0.4),
    ("exit", 0.55),
)


def generate(
    seed: int,
    messages: int,
    span_s: float,
    providers: int,
    *,
    feed_frac: float = 0.6,
    flat: bool = False,
    skew: float = 0.7,
    slow_pct: int = SLOW_PCT,
) -> list[DayMessage]:
    """`messages` items over [0, span_s], sorted by offset, `idx` = list position. `skew`:
    how unevenly providers post (0 = evenly)."""
    rng = random.Random(seed)  # noqa: S311 — seeded simulation, not crypto
    ids = [PROVIDER_BASE + i for i in range(providers)]
    weights = _weights(providers, skew)
    budget = messages - round(messages * feed_frac)
    raw: list[tuple[float, DayMessage, int | None]] = []  # (offset, message, entry's raw index)
    stories = 0
    while budget > 0 and stories < messages * 20:
        stories += 1
        provider = rng.choices(ids, weights)[0]
        contract = rng.choice(CONTRACTS)
        t = _shaped(rng, span_s, flat)
        pack = rng.random() < 0.25
        text, signal, target = _entry(rng, contract, with_stop=rng.random() < 0.5)
        base = len(raw)  # the entry's index in `raw`
        story: list[tuple[float, DayMessage, int | None]] = [
            (t, DayMessage(t, provider, text, "entry", signal, target=target), None)
        ]
        clock = t
        if pack:
            for kind in ("stop_set", "tp_set"):
                clock += rng.uniform(0.3, min(3.0, span_s * 0.1))
                text, signal = _follow_up(rng, kind, contract)
                story.append(
                    (clock, DayMessage(clock, provider, text, kind, signal, target=target), None)
                )
        for kind, p in CHAIN:
            if rng.random() >= p:
                continue
            clock += max(0.3, rng.uniform(0.01, 0.15) * span_s)
            text, signal = _follow_up(rng, kind, contract)
            reply = base if rng.random() < 0.4 else None
            story.append(
                (clock, DayMessage(clock, provider, text, kind, signal, target=target), reply)
            )
        story = [s for s in story if s[0] <= span_s][:budget]
        raw += story
        budget -= len(story)
    while len(raw) < messages:
        kind, text, signal = _feed(rng, commentary=rng.random() < 1 / 6)
        provider = rng.choices(ids, weights)[0]
        t = _shaped(rng, span_s, flat)
        raw.append((t, DayMessage(t, provider, text, kind, signal), None))
    order = sorted(range(len(raw)), key=lambda i: (raw[i][0], i))
    new_index = {old: new for new, old in enumerate(order)}
    out: list[DayMessage] = []
    for new, old in enumerate(order):
        offset, m, reply = raw[old]
        out.append(
            replace(
                m,
                offset_s=round(offset, 3),
                idx=new,
                path=path_for(m.text, m.kind, slow_pct),
                reply_to=None if reply is None else new_index[reply],
            )
        )
    return out


def cross_burst(seed: int, messages: int, window_s: float, providers: int) -> list[DayMessage]:
    """TRD §12 test 2: mostly actionable messages, flat arrival, `messages / providers` per
    provider (50 / 10 = 5 each: every provider at the single-provider design burst at once).
    Drawing providers at random instead piles a dozen messages on one provider, which tests a
    single-provider burst far beyond PRD §12, not a cross-provider one."""
    per = [messages // providers + (i < messages % providers) for i in range(providers)]
    tagged: list[tuple[DayMessage, int]] = []  # (message, provider index)
    for i, n in enumerate(per):
        own = generate(seed + i, n, window_s, 1, feed_frac=0.3, flat=True)
        tagged += [(replace(m, provider=PROVIDER_BASE + i), i) for m in own]
    tagged.sort(key=lambda t: (t[0].offset_s, t[1], t[0].idx))
    new_index = {(i, m.idx): new for new, (m, i) in enumerate(tagged)}
    return [
        replace(m, idx=new, reply_to=None if m.reply_to is None else new_index[(i, m.reply_to)])
        for new, (m, i) in enumerate(tagged)
    ]


def single_burst(
    seed: int, messages: int, window_s: float, *, tight: bool = False
) -> list[DayMessage]:
    """TRD §12 test 3: one provider posts an entry pack then follow-ups inside `window_s`.

    Offsets are evenly spread over the window (`tight`: all inside its first 15%: the worst
    case). Text comes from the same builders, so Jev (forced unsure) sends each to the LLM."""
    rng = random.Random(seed)  # noqa: S311 — seeded simulation, not crypto
    provider, contract = PROVIDER_BASE, CONTRACTS[0]
    text, signal, target = _entry(rng, contract, with_stop=False)
    kinds: tuple[Kind, ...] = ("stop_set", "tp_set", "add", "trim", "stop_move", "exit")
    items = [DayMessage(0.0, provider, text, "entry", signal, target=target)]
    for kind in kinds[: messages - 1]:
        text, signal = _follow_up(rng, kind, contract)
        items.append(DayMessage(0.0, provider, text, kind, signal, target=target))
    span = window_s * (0.15 if tight else 1.0)
    return [
        replace(m, offset_s=round(i * span / max(len(items) - 1, 1), 3), idx=i, path="slow")
        for i, m in enumerate(items)
    ]


def story_message(
    kind: Kind,
    provider: int,
    contract: Contract,
    offset_s: float = 0.0,
    *,
    path: Path = "fast",
    seed: int = 0,
) -> DayMessage:
    """One scripted message for the fault tests: an entry, a follow-up on `contract`, or a
    feed message (chatter / commentary)."""
    rng = random.Random(seed)  # noqa: S311 — seeded simulation, not crypto
    target: str | None = _target(contract)
    if kind == "entry":
        text, signal, target = _entry(rng, contract, with_stop=False)
    elif kind in ("chatter", "commentary"):
        kind, text, signal = _feed(rng, commentary=kind == "commentary")
        target = None
    else:
        text, signal = _follow_up(rng, kind, contract)
    return DayMessage(offset_s, provider, text, kind, signal, path=path, target=target)


def take_slice(items: list[DayMessage], seconds: float) -> list[DayMessage]:
    """The first `seconds` of a day (the 1x replay). Replies to dropped items cannot occur:
    a reply always follows its entry."""
    return [m for m in items if m.offset_s <= seconds]


def mix(items: list[DayMessage]) -> Counter[str]:
    return Counter(m.kind for m in items)


def by_text(items: list[DayMessage]) -> dict[str, DayMessage]:
    return {m.text: m for m in items}
