"""Provider capability table (TRD §4.8). Every cell is [VERIFY]'d by the S5 bench."""

from dataclasses import dataclass
from typing import Literal

from langchain.agents.structured_output import ProviderStrategy, ToolStrategy
from pydantic import BaseModel

from app.runtime_config.schema import LlmProvider


@dataclass(frozen=True)
class Capabilities:
    structured: Literal["provider", "tool"]
    tools: bool
    caching: Literal["explicit", "automatic"]
    temperature: bool  # False: never send `temperature` (newest Anthropic models reject it)


CAPABILITIES: dict[LlmProvider, Capabilities] = {
    "anthropic": Capabilities("provider", tools=True, caching="explicit", temperature=False),
    "openai": Capabilities("provider", tools=True, caching="automatic", temperature=True),
    "deepseek": Capabilities("tool", tools=True, caching="automatic", temperature=True),
    # Open weights behind an OpenAI-compatible server: tool calling is the portable path.
    "openai_compat": Capabilities("tool", tools=True, caching="automatic", temperature=True),
}


def response_format_for[S: BaseModel](
    schema: type[S], provider: LlmProvider
) -> ProviderStrategy[S] | ToolStrategy[S]:
    """Schema-invalid output must fail the attempt (so the chain can fall back), never be
    retried inside the agent: `ToolStrategy(handle_errors=False)`."""
    if CAPABILITIES[provider].structured == "provider":
        return ProviderStrategy(schema)
    return ToolStrategy(schema, handle_errors=False)
