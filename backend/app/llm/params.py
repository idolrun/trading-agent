"""Allowlist for `llm.params` (the chat-model constructor kwargs a config may set).

`llm.params` is editable by any desk session, so it must never redirect a provider's API key
to another host: only `openai_compat` may set `base_url`, and only to a public HTTPS host."""

import ipaddress
import socket
from typing import Any
from urllib.parse import urlsplit

from app.llm.capabilities import CAPABILITIES
from app.runtime_config.schema import LlmProvider

COMMON_PARAMS = frozenset({"temperature", "max_tokens"})
# Latency knobs: reasoning models think by default, which blows the extract deadline.
PROVIDER_PARAMS = {
    "openai_compat": frozenset({"base_url"}),
    "openai": frozenset({"reasoning_effort"}),
    "deepseek": frozenset({"extra_body"}),  # only {"thinking": {"type": ...}}, see _value_error
}
REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high"})
BLOCKED_HOSTS = frozenset({"localhost", "metadata.google.internal"})


def allowed_params(provider: str) -> frozenset[str]:
    return COMMON_PARAMS | PROVIDER_PARAMS.get(provider, frozenset())


def _ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:  # legacy IPv4 forms the resolver accepts: "2130706433", "127.1", "0x7f.1"
            ip = ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def base_url_error(url: object) -> str | None:
    """None = safe. Names are not resolved (a public name pointing inward is not caught)."""
    if not isinstance(url, str):
        return "must be a string"
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 — raises on a malformed port
    except ValueError:
        return "is not a valid URL"
    if parts.scheme != "https":
        return "must use https"
    if parts.username is not None or parts.password is not None:
        return "must not carry credentials"
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        return "has no host"
    if host in BLOCKED_HOSTS or host.endswith((".localhost", ".internal")):
        return f"host {host} is internal"
    ip = _ip(host)
    if ip is not None and not ip.is_global:
        return f"host {host} is not a public address"
    return None


def _value_error(name: str, value: Any) -> str | None:
    if name == "base_url":
        return base_url_error(value)
    if name == "temperature":
        ok = isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= 2
        return None if ok else "must be a number in [0, 2]"
    if name == "max_tokens":
        ok = isinstance(value, int) and not isinstance(value, bool) and value > 0
        return None if ok else "must be a positive integer"
    if name == "reasoning_effort":
        return None if value in REASONING_EFFORTS else f"must be one of {sorted(REASONING_EFFORTS)}"
    if name == "extra_body":
        ok = value in ({"thinking": {"type": "enabled"}}, {"thinking": {"type": "disabled"}})
        return None if ok else 'must be {"thinking": {"type": "enabled" | "disabled"}}'
    return None


def param_errors(model_key: str, params: dict[str, Any]) -> list[str]:
    """Config validation of one `llm.params["provider:model"]` entry."""
    provider = model_key.split(":", 1)[0]
    if provider not in CAPABILITIES:
        return [f"llm.params[{model_key}]: unknown provider {provider}"]
    allowed = allowed_params(provider)
    errors: list[str] = []
    for name, value in params.items():
        if name not in allowed:
            errors.append(f"llm.params[{model_key}].{name} is not allowed for {provider}")
        elif (why := _value_error(name, value)) is not None:
            errors.append(f"llm.params[{model_key}].{name} {why}")
    return errors


def safe_params(provider: LlmProvider, params: dict[str, Any]) -> dict[str, Any]:
    """Constructor kwargs from `llm.params`: unknown keys (`api_key` included) are dropped.
    Raises ValueError on an unsafe `base_url`, which config validation already rejects."""
    allowed = allowed_params(provider)
    if not CAPABILITIES[provider].temperature:
        allowed -= {"temperature"}
    kept = {k: v for k, v in params.items() if k in allowed}
    if "base_url" in kept and (why := base_url_error(kept["base_url"])) is not None:
        raise ValueError(f"llm.params base_url for {provider} {why}")
    return kept
