import json
import logging
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings, get_settings
from app.logging import JsonFormatter
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return get_settings().model_copy(update={"environment": "test"})


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def log_lines() -> Iterator[list[dict[str, Any]]]:
    """Every INFO+ log line as the production JSON formatter renders it (correlation IDs)."""
    lines: list[dict[str, Any]] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(json.loads(self.format(record)))

    handler, root = Capture(), logging.getLogger()
    handler.setFormatter(JsonFormatter())
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    yield lines
    root.removeHandler(handler)
    root.setLevel(level)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" in item.path.parts:
            item.add_marker(pytest.mark.integration)
