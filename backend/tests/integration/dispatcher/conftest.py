from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import create_session_factory
from tests.integration.conftest import _url


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()
