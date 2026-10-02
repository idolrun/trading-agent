"""Fake chat models and context builders for LLM tests (no live API calls)."""

import uuid
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.messages.ai import UsageMetadata

from app.interpret.context import BookEntry, ContextMessage, CurrentMessage, ProviderContext
from app.llm.registry import ModelRegistry
from app.runtime_config.schema import ChainEntry, LlmConfig


class FakeChat(FakeMessagesListChatModel):
    """Scripted replies; tool binding is a no-op (the script decides the tool calls)."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "FakeChat":
        return self


class FakeRegistry(ModelRegistry):
    """provider -> scripted model, instead of real API clients."""

    def __init__(self, models: dict[str, BaseChatModel]) -> None:
        self._fakes = models

    def model(self, entry: ChainEntry, cfg: LlmConfig) -> BaseChatModel:
        return self._fakes[entry.provider]


USAGE = UsageMetadata(input_tokens=1200, output_tokens=80, total_tokens=1280)


def structured(name: str, args: dict[str, Any], call_id: str = "s1") -> AIMessage:
    """A ToolStrategy structured answer: a tool call named after the schema."""
    return AIMessage(
        "", tool_calls=[{"name": name, "args": args, "id": call_id}], usage_metadata=USAGE
    )


def tool_call(name: str, args: dict[str, Any], call_id: str) -> BaseMessage:
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": call_id}])


def ctx(
    text: str,
    *,
    message_id: str = "981",
    recent: list[tuple[str, str]] = (),  # type: ignore[assignment]
    book: list[tuple[uuid.UUID, str]] = (),  # type: ignore[assignment]
) -> ProviderContext:
    return ProviderContext(
        now_et="2026-09-28 10:04:12 ET",
        message=CurrentMessage(
            id=message_id,
            author_id="42",
            at_et="2026-09-28 10:04:12 ET",
            text=text,
            event_type="create",
            channel_id="7",
            reply_to=None,
        ),
        reply_parent=None,
        recent_author_messages=[
            ContextMessage(id=i, author_id="42", at_et="2026-09-28 10:01:00 ET", text=t)
            for i, t in recent
        ],
        provider_book=[BookEntry(id=i, summary=s) for i, s in book],
    )
