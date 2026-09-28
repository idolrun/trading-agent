from typing import Any, Self

from pydantic import Field

from app.api.schemas.base import CamelModel
from app.runtime_config.schema import RuntimeConfig, missing_for_entries


class ConfigResponse(CamelModel):
    version: int
    document: dict[str, Any]  # RuntimeConfig as stored (snake_case keys, IDs as strings)
    missing_for_entries: list[str]

    @classmethod
    def of(cls, version: int, config: RuntimeConfig) -> Self:
        return cls(
            version=version,
            document=config.model_dump(mode="json"),
            missing_for_entries=missing_for_entries(config),
        )


class UpdateConfigRequest(CamelModel):
    expected_version: int = Field(ge=0)  # 0 = first initialization
    document: RuntimeConfig
    reason: str = Field(min_length=1)
