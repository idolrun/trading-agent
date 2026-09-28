"""F017: FR-8 is structural — the context schema cannot carry portfolio data."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.interpret.context import FORBIDDEN_KEYS, ContextMessage, ProviderContext


def property_names(schema: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(schema, dict):
        names |= set(schema.get("properties", {}))
        for value in schema.values():
            names |= property_names(value)
    elif isinstance(schema, list):
        for value in schema:
            names |= property_names(value)
    return names


def test_no_forbidden_key_anywhere_in_the_schema() -> None:
    names = property_names(ProviderContext.model_json_schema())
    assert names and not names & FORBIDDEN_KEYS


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        ContextMessage.model_validate(
            {"id": "1", "author_id": "2", "at_et": "x", "text": "y", "lots": []}
        )
