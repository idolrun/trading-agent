"""Load suite: runs only when selected with `-m load`; reuses the integration DB fixtures."""

from pathlib import Path

import pytest

from tests.integration.conftest import _clean_tables, db_session, owner_engine

__all__ = ["_clean_tables", "db_session", "owner_engine"]

HERE = Path(__file__).parent


def _selects_load(markexpr: str) -> bool:
    return "load" in markexpr and "not load" not in markexpr


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _selects_load(config.getoption("markexpr") or ""):
        # test 6 stops the shared Postgres: run it after everything else (stable sort)
        items.sort(key=lambda item: item.path.name == "test_failover.py")
        return
    skip = pytest.mark.skip(reason="load suite: run with -m load")
    for item in items:
        if HERE in item.path.parents:
            item.add_marker(skip)
