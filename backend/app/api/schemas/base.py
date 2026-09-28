"""Base schema for API responses: camelCase JSON, Discord snowflakes as strings."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer
from pydantic.alias_generators import to_camel

# JS numbers lose precision above 2^53; snowflakes are 64-bit.
DiscordId = Annotated[int, PlainSerializer(str, return_type=str)]


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, serialize_by_alias=True
    )
