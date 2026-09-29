"""App shutdown stops the services' own background tasks, not only the event loop."""

import asyncio

from app.main import create_app
from tests.integration.api.conftest import api_settings


async def test_shutdown_cancels_the_deferred_approval_resume(owner_engine: object) -> None:
    app = create_app(api_settings())
    async with app.router.lifespan_context(app):
        app.state.approvals.unblock_poll_s = 3600
        app.state.approvals.resume_when_entries_unblocked()
        (deferred,) = [t for t in asyncio.all_tasks() if t.get_name() == "resume-approved-entries"]
        assert not deferred.done()
    assert deferred.cancelled()
