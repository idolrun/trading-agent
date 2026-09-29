"""Latency-simulating model fakes. The *real* Jev client, LLM chain, agent, spans verification and
router run on top of them; only the network call is replaced.

- `LatencyJev` stands in for `AsyncTypeSafeClient`: sleeps a sampled Jev latency, then answers the
  questions the real code built (candidate ids, book ids) so `map_answers` assembles the signal.
  When the message is `unsure` (its `path` is "slow", or the run forces the slow path) it answers
  with a low `msg_type` confidence, which the real router sends to the LLM.
- `LatencyChat` stands in for the chat model behind `create_agent`: sleeps a sampled LLM latency,
  reads the message out of the system prompt the real middleware built, and returns the
  structured `LlmParsedSignals` tool call for it.

Latency comes from seeded lognormals (`scenarios.py`; placeholders until F048 bench numbers).
"""

import asyncio
import json
import random
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field
from typesafe_sdk import Question, SystemOneResponse, Usage

from app.interpret.candidates import Candidates, find_candidates
from app.interpret.jev.mapping import Answer
from tests.jev_helpers import c, n, score
from tests.llm_helpers import FakeChat, structured
from tests.load.scenarios import Lognormal
from tests.load.synthetic_day import DayMessage

SCHEMA = "LlmParsedSignals"
NON_BOOK = ("new_position", "ambiguous")
FEED = ("chatter", "commentary_pnl")
UNSURE = 0.55  # below t_field (0.8) and t_chat (0.9): the router sends it to the LLM


def pick_link(item: DayMessage, book: Mapping[str, str]) -> str:
    """The book id whose summary starts with the message's target ("SPY 577C ..."), else
    `ambiguous` (what a careful model says when the position is not there)."""
    link = str(item.signal.get("links_to", "new_position"))
    if link != "position":
        return link
    for position_id, summary in book.items():
        if item.target is not None and summary.startswith(item.target):
            return position_id
    return "ambiguous"


def llm_signal(item: DayMessage, book: Mapping[str, str]) -> dict[str, Any]:
    signal = dict(item.signal)
    if "links_to" in signal:
        signal["links_to"] = pick_link(item, book)
    return signal


def llm_message(item: DayMessage, book: Mapping[str, str]) -> AIMessage:
    return structured(SCHEMA, {"signals": [llm_signal(item, book)]})


def jev_answers(
    item: DayMessage, candidates: Candidates, book: Mapping[str, str], *, unsure: bool
) -> dict[str, Answer]:
    s = item.signal
    msg_type = str(s["msg_type"])
    feed = msg_type in FEED
    role_of: dict[str, str] = {}
    for name in ("strike", "entry_price", "stop_level"):
        if name in s:
            role_of[s[name]["raw"]] = name
    for tp in s.get("tp_levels", []):
        role_of[tp["raw"]] = "take_profit_level"
    default_role = "pnl_or_percent" if feed else "other"
    underlying = s.get("underlying", {}).get("raw", "").lower()
    ticker = next(
        (t.id for t in candidates.tickers if t.text.lstrip("$").lower() == underlying), None
    )
    expiry = s.get("expiry_token", {}).get("raw")
    e_id = next((e.id for e in candidates.expiry_tokens if e.text == expiry), None)
    right_raw = s.get("right", {}).get("raw", "")
    right = "not_stated" if not right_raw else ("call" if right_raw.endswith("c") else "put")
    link = pick_link(item, book) if "links_to" in s else "new_position"
    intent = {"full_exit": 4, "trim": 3}.get(msg_type, 0)
    conf = UNSURE if unsure else (0.95 if feed else 0.97)
    answers: dict[str, Answer] = {
        "msg_type": c(msg_type, conf),
        "signal_count": c("none" if feed else "one"),
        "instrument": c("unclear" if feed else "option"),
        "right": c(right),
        "exit_fraction": c(str(s.get("exit_fraction", "not_stated")), 0.9),
        "stop_basis": c("underlying" if "stop_basis" in s else "not_applicable", 0.9),
        "candle_close": n(0.05),
        "has_prose_condition": n(0.05),
        "is_correction": n(0.02),
        "is_add": n(0.97 if s.get("is_add") else 0.03),
        "breakeven_stop": n(0.02),
        "stop_removed": n(0.02),
        "exit_intent": score(intent),
        "ticker": c(ticker or "not_stated", 0.93),
        "expiry": c(e_id or "not_stated", 0.9),
        "size_tier": c("lotto" if "size_tier" in s else "not_stated", 0.9),
        "correction_target": c("none"),
        "links_to": c(link, 0.93),
    }
    for number in candidates.numbers:
        answers[f"role_{number.id}"] = c(role_of.get(number.text, default_role), 0.95)
    return answers


class LatencyJev:
    """`AsyncTypeSafeClient.system_one` with latency. `force_slow`: every answer is unsure."""

    def __init__(
        self,
        plans: dict[str, DayMessage],
        latency: Lognormal,
        rng: random.Random,
        *,
        force_slow: bool = False,
    ) -> None:
        self.plans, self.latency, self.rng, self.force_slow = plans, latency, rng, force_slow
        self.calls = 0

    async def system_one(
        self, *, state: dict[str, Any], questions: Mapping[str, Question], model: str, **_: Any
    ) -> SystemOneResponse:
        await asyncio.sleep(self.latency.sample(self.rng))
        self.calls += 1
        text = state["message"]["text"]
        item = self.plans[text]
        criteria = getattr(questions["links_to"], "criteria", {})
        book = {k: str(v) for k, v in criteria.items() if k not in NON_BOOK}
        answers = jev_answers(
            item, find_candidates(text), book, unsure=self.force_slow or item.path == "slow"
        )
        return SystemOneResponse.model_construct(
            model=model, usage=Usage(input_tokens=900, output_tokens=40), answers=answers
        )


def prompt_context(messages: list[BaseMessage]) -> dict[str, Any]:
    """The per-message JSON the real `extraction_prompt` middleware appends to the system prompt."""
    for m in messages:
        text = m.text if isinstance(m.text, str) else str(m.content)
        if "## Context\n" in text:
            return dict(json.loads(text.rsplit("## Context\n", 1)[1]))
    raise AssertionError("no '## Context' in the model request")


class LatencyChat(FakeChat):
    """The chat model behind the extract agent, with latency."""

    plans: dict[str, DayMessage] = Field(default_factory=dict)
    latency: Any = None
    rng: Any = None
    calls: int = 0

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        await asyncio.sleep(self.latency.sample(self.rng))
        self.calls += 1
        doc = prompt_context(messages)
        book = {b["id"]: b["summary"] for b in doc["provider_book"]}
        message = llm_message(self.plans[doc["message"]["text"]], book)
        return ChatResult(generations=[ChatGeneration(message=message)])
