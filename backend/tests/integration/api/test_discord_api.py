from tests.integration.api.conftest import Api


async def test_status_needs_a_session(api: Api) -> None:
    assert (await api.client.get("/api/v1/discord/status")).status_code == 401


async def test_status_without_token_is_not_configured(signed_in: Api) -> None:
    r = await signed_in.client.get("/api/v1/discord/status")
    assert r.status_code == 200
    body = r.json()
    assert (body["state"], body["account"], body["lastMessageAt"]) == ("not_configured", None, None)
    assert body["channels"] == []
