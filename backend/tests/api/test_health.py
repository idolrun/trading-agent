from httpx import AsyncClient


async def test_health_is_503_when_the_app_has_not_started(client: AsyncClient) -> None:
    """The fixture app runs no lifespan, so there is no database to reach."""
    r = await client.get("/health")
    assert r.status_code == 503
    assert r.json() == {"status": "unhealthy", "reason": "not started"}
