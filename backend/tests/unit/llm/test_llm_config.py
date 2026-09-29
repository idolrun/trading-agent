"""`llm.params` allowlist, required prices, and the replay-gate check (TRD §4.8 rules 5, 8)."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.evalkit.models import EvalRun, EvalRunKind, Split
from app.llm.params import base_url_error, param_errors, safe_params
from app.llm.registry import ModelRegistry
from app.llm.validation import validate_llm_config
from app.runtime_config.schema import ChainEntry, LlmConfig, LlmRoles, Price, RoleConfig

NOW = datetime(2026, 9, 28, tzinfo=UTC)
PRICE = Price(
    input_per_mtok=Decimal(1), output_per_mtok=Decimal(2), cached_input_per_mtok=Decimal("0.1")
)
PASSED_CHECKS = [{"name": "wrong_contracts", "value": 0, "need": "== 0", "passed": True}]


@dataclass
class _Rows:
    rows: list[EvalRun]

    def all(self) -> list[EvalRun]:
        return self.rows


class FakeDb:
    """`validate_llm_config` only reads `eval_runs` by id; every row is returned."""

    def __init__(self, *runs: EvalRun) -> None:
        self.runs = list(runs)

    async def scalars(self, _stmt: Any) -> _Rows:
        return _Rows(self.runs)


def run(ref: str, **overrides: Any) -> EvalRun:
    fields: dict[str, Any] = {
        "kind": EvalRunKind.REPLAY_GATE,
        "model_ref": ref,
        "split": Split.TEST,
        "metrics": {"checks": PASSED_CHECKS},
        "gate_passed": True,
        "started_at": NOW,
        "finished_at": NOW,
    } | overrides
    return EvalRun(**fields)


CLAUDE = ChainEntry(provider="anthropic", model="claude-sonnet-5", prompt="extract@v1")
GPT = ChainEntry(provider="openai", model="gpt-test", prompt="extract@v1")


def gated(*runs: EvalRun, prices: bool = True, **llm: Any) -> tuple[LlmConfig, FakeDb]:
    """A two-vendor extract chain, each entry pointing at the matching run."""
    by_ref = {r.model_ref: r for r in runs}
    chain = [
        e.model_copy(update={"gate_run": by_ref[e.model_ref].eval_run_id})
        if e.model_ref in by_ref
        else e
        for e in (CLAUDE, GPT)
    ]
    cfg = LlmConfig(
        roles=LlmRoles(extract=RoleConfig(chain=chain)),
        prices={e.model_key: PRICE for e in chain} if prices else {},
        **llm,
    )
    return cfg, FakeDb(*runs)


def settings() -> Settings:
    return Settings.model_construct(
        anthropic_api_key=SecretStr("sk-ant-real"),
        openai_api_key=SecretStr("sk-openai-real"),
        deepseek_api_key=None,
        openai_compat_api_key=SecretStr("sk-compat-real"),
    )


# --- llm.params -----------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["anthropic", "openai", "deepseek"])
@pytest.mark.parametrize("key", ["base_url", "api_key", "openai_api_base", "default_headers"])
def test_redirecting_params_are_rejected_for_non_compat_providers(provider: str, key: str) -> None:
    errors = param_errors(f"{provider}:m", {key: "https://evil.example.com"})
    assert errors == [f"llm.params[{provider}:m].{key} is not allowed for {provider}"]


def test_compat_may_not_override_api_key() -> None:
    assert param_errors("openai_compat:m", {"api_key": "sk-x"}) != []


async def test_config_with_redirecting_params_is_rejected_even_when_ungated() -> None:
    cfg = LlmConfig(
        roles=LlmRoles(extract=RoleConfig(chain=[CLAUDE, GPT])),
        params={"openai:gpt-test": {"base_url": "https://evil.example.com", "api_key": "x"}},
    )
    errors = await validate_llm_config(cfg, FakeDb(), allow_ungated=True)  # type: ignore[arg-type]
    assert any("base_url is not allowed" in e for e in errors)
    assert any("api_key is not allowed" in e for e in errors)


def test_registry_strips_redirects_and_passes_the_real_key_last() -> None:
    cfg = LlmConfig(
        params={
            "openai:gpt-test": {
                "base_url": "https://evil.example.com",
                "api_key": "sk-attacker",
                "temperature": 0.2,
            }
        }
    )
    with patch("app.llm.registry.init_chat_model") as init:
        ModelRegistry(settings()).model(GPT, cfg)
    init.assert_called_once_with("openai:gpt-test", temperature=0.2, api_key="sk-openai-real")


def test_registry_drops_temperature_where_unsupported() -> None:
    assert safe_params("anthropic", {"temperature": 0.2, "max_tokens": 512}) == {"max_tokens": 512}


@pytest.mark.parametrize(
    "url",
    [
        "http://llm.example.com/v1",
        "https://127.0.0.1/v1",
        "https://127.8.0.1/v1",
        "https://10.1.2.3/v1",
        "https://172.16.0.1/v1",
        "https://172.31.255.255/v1",
        "https://192.168.1.10/v1",
        "https://169.254.169.254/latest",
        "https://localhost:8000/v1",
        "https://api.localhost/v1",
        "https://metadata.google.internal/",
        "https://[::1]/v1",
        "https://[::ffff:127.0.0.1]/v1",
        "https://[fe80::1]/v1",
        "https://2130706433/v1",
        "https://0x7f.1/v1",
        "https://user:pw@llm.example.com/v1",
        "ftp://llm.example.com",
        "https:///v1",
        42,
    ],
)
def test_compat_base_url_must_be_public_https(url: object) -> None:
    assert base_url_error(url) is not None
    assert param_errors("openai_compat:m", {"base_url": url}) != []


def test_compat_public_https_base_url_is_allowed() -> None:
    params = {"base_url": "https://llm.example.com/v1", "temperature": 0, "max_tokens": 1024}
    assert param_errors("openai_compat:m", params) == []
    assert safe_params("openai_compat", params) == params


def test_registry_refuses_an_unsafe_stored_compat_base_url() -> None:
    entry = ChainEntry(provider="openai_compat", model="oss", prompt="extract@v1")
    cfg = LlmConfig(params={"openai_compat:oss": {"base_url": "https://169.254.169.254/"}})
    with patch("app.llm.registry.init_chat_model") as init, pytest.raises(ValueError):
        ModelRegistry(settings()).model(entry, cfg)
    init.assert_not_called()


# --- prices ---------------------------------------------------------------------------------


async def test_passing_gated_priced_chain_is_accepted() -> None:
    cfg, db = gated(run(CLAUDE.model_ref), run(GPT.model_ref))
    assert await validate_llm_config(cfg, db) == []  # type: ignore[arg-type]


async def test_chain_model_without_price_is_rejected() -> None:
    cfg, db = gated(run(CLAUDE.model_ref), run(GPT.model_ref))
    cfg = cfg.model_copy(update={"prices": {CLAUDE.model_key: PRICE}})
    errors = await validate_llm_config(cfg, db)  # type: ignore[arg-type]
    assert errors == ["llm.roles.extract: openai:gpt-test has no entry in llm.prices"]


async def test_prelabel_model_without_price_is_rejected() -> None:
    cfg, db = gated(run(CLAUDE.model_ref), run(GPT.model_ref))
    prelabel = ChainEntry(provider="deepseek", model="ds", prompt="prelabel@v1")
    roles = cfg.roles.model_copy(update={"prelabel": RoleConfig(chain=[prelabel])})
    errors = await validate_llm_config(cfg.model_copy(update={"roles": roles}), db)  # type: ignore[arg-type]
    assert errors == ["llm.roles.prelabel: deepseek:ds has no entry in llm.prices"]


# --- eval gate ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": EvalRunKind.BENCH},
        {"kind": EvalRunKind.THRESHOLD_SWEEP},
        {"split": Split.DEV},
        {"split": None},
        {"gate_passed": False},
        {"gate_passed": None},
        {"model_ref": "openai:gpt-test:extract@v0"},
        {"metrics": {}},
        {"metrics": {"checks": []}},
        {"metrics": {"checks": [*PASSED_CHECKS, {"name": "providers", "passed": False}]}},
        {"metrics": {"checks": [{"name": "providers"}]}},
    ],
)
async def test_run_that_is_not_a_passing_test_split_replay_gate_is_rejected(
    overrides: dict[str, Any],
) -> None:
    bad = run(GPT.model_ref, **overrides)
    cfg, db = gated(run(CLAUDE.model_ref))
    chain = [cfg.roles.extract.chain[0], GPT.model_copy(update={"gate_run": bad.eval_run_id})]
    cfg = cfg.model_copy(
        update={
            "roles": LlmRoles(extract=RoleConfig(chain=chain)),
            "prices": {e.model_key: PRICE for e in chain},
        }
    )
    db.runs.append(bad)
    errors = await validate_llm_config(cfg, db)  # type: ignore[arg-type]
    assert len(errors) == 1
    assert errors[0].startswith("llm.roles.extract: openai:gpt-test:extract@v1 has no passing gate")


async def test_missing_or_unknown_gate_run_is_rejected() -> None:
    cfg, db = gated(run(CLAUDE.model_ref))
    errors = await validate_llm_config(cfg, db)  # type: ignore[arg-type]
    assert errors == ["llm.roles.extract: openai:gpt-test:extract@v1 has no passing gate run"]
    unknown = uuid.uuid4()
    chain = [cfg.roles.extract.chain[0], GPT.model_copy(update={"gate_run": unknown})]
    cfg = cfg.model_copy(update={"roles": LlmRoles(extract=RoleConfig(chain=chain))})
    errors = await validate_llm_config(cfg, db)  # type: ignore[arg-type]
    assert errors == [
        f"llm.roles.extract: openai:gpt-test:extract@v1 has no passing gate run ({unknown}: "
        "not found)"
    ]
