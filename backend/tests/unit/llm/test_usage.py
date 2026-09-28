from decimal import Decimal

import anthropic
import httpx
import openai
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages.ai import UsageMetadata

from app.llm.chain import AttemptFailed, classify
from app.llm.models import CallOutcome
from app.llm.usage import cost_usd, total_usage
from app.runtime_config.schema import Price

PRICE = Price(
    input_per_mtok=Decimal("2.00"),
    output_per_mtok=Decimal("10.00"),
    cached_input_per_mtok=Decimal("0.20"),
)


def usage(inp: int, out: int, cached: int = 0) -> UsageMetadata:
    return UsageMetadata(
        input_tokens=inp,
        output_tokens=out,
        total_tokens=inp + out,
        input_token_details={"cache_read": cached},
    )


def test_cost_bills_cache_reads_at_the_cached_rate_in_decimal() -> None:
    # 1000 fresh input * 2 + 3000 cached * 0.2 + 500 output * 10, per MTok
    assert cost_usd(usage(4000, 500, cached=3000), PRICE) == Decimal("0.007600")
    assert cost_usd(usage(0, 0), PRICE) == Decimal("0.000000")


def test_total_usage_sums_every_model_turn() -> None:
    msgs = [
        HumanMessage("hi"),
        AIMessage("", usage_metadata=usage(100, 10)),
        AIMessage("", usage_metadata=usage(200, 20, cached=50)),
    ]
    total = total_usage(msgs)
    assert total is not None
    assert (total["input_tokens"], total["output_tokens"]) == (300, 30)
    assert total_usage([HumanMessage("x")]) is None


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "https://api.test/v1"))


def test_classify_maps_provider_errors_to_outcomes() -> None:
    req = httpx.Request("POST", "https://api.test/v1")
    assert classify(TimeoutError()) is CallOutcome.TIMEOUT
    assert classify(openai.APITimeoutError(request=req)) is CallOutcome.TIMEOUT
    rate_limited = anthropic.RateLimitError("slow down", response=_response(429), body=None)
    assert classify(rate_limited) is CallOutcome.RATE_LIMITED
    server = openai.InternalServerError("boom", response=_response(503), body=None)
    assert classify(server) is CallOutcome.SERVER_ERROR
    overloaded = anthropic.APIStatusError("overloaded", response=_response(529), body=None)
    assert classify(overloaded) is CallOutcome.SERVER_ERROR
    assert classify(openai.APIConnectionError(request=req)) is CallOutcome.CONNECTION_ERROR
    assert classify(AttemptFailed(CallOutcome.EMPTY, "no output")) is CallOutcome.EMPTY
    assert classify(KeyError("bug")) is None
