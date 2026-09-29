"""Chat-model construction from the config registry, and API-key → enabled providers.

The only place a provider name turns into a LangChain model (TRD §4.8)."""

import json
import logging

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.config import Settings
from app.llm.params import safe_params
from app.runtime_config.schema import ChainEntry, LlmConfig, LlmProvider

logger = logging.getLogger(__name__)

PROVIDERS: tuple[LlmProvider, ...] = ("anthropic", "openai", "deepseek", "openai_compat")


def api_key(settings: Settings, provider: LlmProvider) -> SecretStr | None:
    key: SecretStr | None = getattr(settings, f"{provider}_api_key")
    return key


def enabled_providers(settings: Settings) -> frozenset[LlmProvider]:
    """Providers with an API key. The others are skipped by every chain."""
    return frozenset(p for p in PROVIDERS if api_key(settings, p) is not None)


def configured_providers(cfg: LlmConfig) -> set[LlmProvider]:
    roles = (cfg.roles.extract, cfg.roles.note, cfg.roles.prelabel)
    return {entry.provider for role in roles for entry in role.chain}


async def alert_missing_keys(settings: Settings, cfg: LlmConfig, alerts: AlertService) -> None:
    """Startup: a provider named in a chain but without a key is disabled + warning alert."""
    for provider in sorted(configured_providers(cfg) - enabled_providers(settings)):
        await alerts.raise_(
            AlertKind.PROVIDER_DISABLED,
            Severity.WARNING,
            f"LLM provider {provider} has no API key; it is skipped in every chain",
            details={"provider": provider},
        )


class ModelRegistry:
    """Builds (and reuses) one chat model per provider:model + params."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._models: dict[str, BaseChatModel] = {}

    def model(self, entry: ChainEntry, cfg: LlmConfig) -> BaseChatModel:
        params = safe_params(entry.provider, cfg.params.get(entry.model_key, {}))
        cache_key = f"{entry.model_key}|{json.dumps(params, sort_keys=True, default=str)}"
        if cache_key not in self._models:
            key = api_key(self._settings, entry.provider)
            if key is None:
                raise LookupError(f"provider {entry.provider} is disabled (no API key)")
            # An OpenAI-compatible endpoint is ChatOpenAI with `base_url` from params.
            provider = "openai" if entry.provider == "openai_compat" else entry.provider
            self._models[cache_key] = init_chat_model(
                f"{provider}:{entry.model}", **params, api_key=key.get_secret_value()
            )
        return self._models[cache_key]
