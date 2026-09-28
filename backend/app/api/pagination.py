"""Keyset pagination: newest first, cursor = opaque base64 of (timestamp, id)."""

import base64
import json
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Annotated, Any

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import Select, tuple_
from sqlalchemy.orm import InstrumentedAttribute

from app.api.errors import ApiError
from app.api.schemas.base import CamelModel

Limit = Annotated[int, Query(ge=1, le=200)]
Cursor = Annotated[str | None, Query()]


class Page[T: BaseModel](CamelModel):
    items: list[T]
    next_cursor: str | None


def encode(at: datetime, key: object) -> str:
    return base64.urlsafe_b64encode(json.dumps([at.isoformat(), str(key)]).encode()).decode()


def keyset[Q: Select[Any]](
    query: Q,
    at: InstrumentedAttribute[Any] | Any,
    key: InstrumentedAttribute[Any] | Any,
    cursor: str | None,
    limit: int,
    parse: Callable[[str], object],
) -> Q:
    """Rows strictly after the cursor, newest first, one extra to know if there is more."""
    if cursor is not None:
        try:
            raw_at, raw_key = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            after = (datetime.fromisoformat(raw_at), parse(raw_key))
        except (ValueError, TypeError) as exc:
            raise ApiError("invalid cursor", "invalid", 422) from exc
        query = query.where(tuple_(at, key) < after)
    return query.order_by(at.desc(), key.desc()).limit(limit + 1)


def cut[R](
    rows: Sequence[R], limit: int, key: Callable[[R], tuple[datetime, object]]
) -> tuple[list[R], str | None]:
    """The page plus the cursor for the next one (None = last page)."""
    items = list(rows[:limit])
    return items, (encode(*key(items[-1])) if len(rows) > limit else None)
