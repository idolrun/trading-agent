"""F076: the background services run in exactly one process per database."""

import pytest

from app.main import libpq_dsn
from app.system.singleton import SecondInstance, hold_instance_lock
from tests.integration.conftest import _url


async def test_a_second_instance_is_refused_until_the_first_lets_go(owner_engine: object) -> None:
    dsn = libpq_dsn(_url("test_database_url"))
    first = await hold_instance_lock(dsn)
    try:
        with pytest.raises(SecondInstance, match="exactly one"):
            await hold_instance_lock(dsn)
    finally:
        await first.close()  # a crash drops the connection the same way
    again = await hold_instance_lock(dsn)
    await again.close()
