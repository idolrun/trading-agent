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


async def test_changes_history_newest_first_and_paged(signed_in: Api) -> None:
    c = signed_in.client
    await c.put("/api/v1/config", json={"expectedVersion": 0, "reason": "init", "document": {}})
    await c.put(
        "/api/v1/config",
        json={"expectedVersion": 1, "reason": "silence", "document": {"feed_silence_s": 600}},
    )
    r = await c.get("/api/v1/config/changes", params={"limit": 1})
    assert r.status_code == 200, r.text
    page = r.json()
    [top] = page["items"]
    assert (top["version"], top["reason"], top["actor"]) == (2, "silence", "user")
    assert top["diff"]["feed_silence_s"]["after"] == 600 and page["nextCursor"]
    rest = (await c.get("/api/v1/config/changes", params={"cursor": page["nextCursor"]})).json()
    assert [i["version"] for i in rest["items"]] == [1] and rest["nextCursor"] is None


async def test_changes_requires_session(api: Api) -> None:
    assert (await api.client.get("/api/v1/config/changes")).status_code == 401
