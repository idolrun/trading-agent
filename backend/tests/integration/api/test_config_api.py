from tests.integration.api.conftest import Api


async def test_get_before_init_is_404(signed_in: Api) -> None:
    r = await signed_in.client.get("/api/v1/config")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


async def test_put_then_get(signed_in: Api) -> None:
    body = {
        "expectedVersion": 0,
        "reason": "bootstrap",
        "document": {"channels": ["1234567890123456789"], "feed_silence_s": 600},
    }
    r = await signed_in.client.put("/api/v1/config", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["version"] == 1
    assert data["document"]["channels"] == ["1234567890123456789"]
    assert "feed_silence_s" not in data["missingForEntries"]
    assert "option_tiers" in data["missingForEntries"]

    r = await signed_in.client.get("/api/v1/config")
    assert r.status_code == 200
    assert r.json() == data


async def test_stale_version_is_409(signed_in: Api) -> None:
    body = {"expectedVersion": 0, "reason": "init", "document": {}}
    assert (await signed_in.client.put("/api/v1/config", json=body)).status_code == 200
    r = await signed_in.client.put("/api/v1/config", json=body)
    assert r.status_code == 409
    assert r.json()["code"] == "conflict"


async def test_invalid_document_is_422(signed_in: Api) -> None:
    body = {"expectedVersion": 0, "reason": "x", "document": {"option_tiers": {"default": "0"}}}
    r = await signed_in.client.put("/api/v1/config", json=body)
    assert r.status_code == 422
    assert r.json()["code"] == "invalid"
    assert "option_tiers" in r.json()["message"]


async def test_requires_session(api: Api) -> None:
    r = await api.client.get("/api/v1/config")
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"
