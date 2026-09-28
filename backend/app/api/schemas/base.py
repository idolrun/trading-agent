"""Base schema for API responses: camelCase JSON, Discord snowflakes as strings."""

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer
from pydantic.alias_generators import to_camel

# JS numbers lose precision above 2^53; snowflakes are 64-bit.
DiscordId = Annotated[int, PlainSerializer(str, return_type=str)]
# Prices/fractions as exact strings without the column's trailing zeros ("357.5", not "357.5000").
Num = Annotated[
    Decimal,
    PlainSerializer(lambda d: format(d.normalize(), "f"), return_type=str, when_used="json"),
]


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, serialize_by_alias=True
    )
