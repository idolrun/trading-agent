"""F048 labeling API: the queue with the LLM's pending suggestion, label writes that supersede."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.db import in_transaction
from app.evalkit.labels import event_key
from app.evalkit.models import EvalLabel
from app.evalkit.split import split_for
from app.inbox import repo as inbox
from app.inbox.envelope import Envelope
from app.ingest.adapter import to_envelope
from app.llm.models import CallOutcome, ModelProvider, ModelRole
from app.llm.usage import CallRef, record_model_call
from tests.integration.api.conftest import Api
from tests.messages import PROVIDER_A, PROVIDER_B, create

T = datetime(2026, 9, 28, 15, 0, tzinfo=UTC)
ENTRY = {
    "idx": 0,
    "msgType": "entry",
    "instrument": "option",
    "fields": {"underlying": "GOOGL", "strike": "357.5", "right": "call", "entry_price": "0.71"},
}


async def store(api: Api, text: str, minute: int, author: int = PROVIDER_A) -> Envelope:
    when = T + timedelta(minutes=minute)
    env = to_envelope(create(when, text, author=author), when)
    async with in_transaction(api.app.state.session_factory, None) as db:
        await inbox.store_event(db, env)
    return env


async def suggest(api: Api, env: Envelope, label: dict[str, Any]) -> None:
    await record_model_call(
        api.app.state.session_factory,
        ref=CallRef(env.key.message_id, env.key.event_type, env.key.event_seq),
        provider=ModelProvider.DEEPSEEK,
        model="ds-test",
        role=ModelRole.PRELABEL,
        attempt=1,
        prompt_version="prelabel@v1",
        request={},
        response=label,
        outcome=CallOutcome.OK,
        latency_ms=5,
        started_at=T,
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/labels/queue"),
        ("GET", "/api/v1/labels/progress"),
        ("GET", "/api/v1/labels/1.create.0"),
        ("PUT", "/api/v1/labels/1.create.0"),
    ],
)
async def test_labels_require_a_session(api: Api, method: str, path: str) -> None:
    r = await api.client.request(method, path, json={"label": {"signals": []}})
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"


async def test_queue_lists_unlabeled_events_with_context_and_suggestion(
    signed_in: Api,
) -> None:
    api = signed_in
    earlier = await store(api, "watching googl", 0)
    target = await store(api, "Bought GOOGL 357.5C 10/2 @ 0.71", 1)
    other = await store(api, "gm", 2, author=PROVIDER_B)
    await suggest(
        api, target, {"signals": [{"idx": 0, "msg_type": "entry", "instrument": "option"}]}
    )

    page = (await api.client.get("/api/v1/labels/queue", params={"limit": 2})).json()
    assert [i["eventKey"] for i in page["items"]] == [event_key(other.key), event_key(target.key)]
    assert page["nextCursor"] is not None
    got = page["items"][1]
    assert got["text"] == "Bought GOOGL 357.5C 10/2 @ 0.71" and got["providerId"] == str(PROVIDER_A)
    assert [m["text"] for m in got["context"]] == ["watching googl"]  # own messages only
    assert got["prelabel"]["signals"][0]["msgType"] == "entry"
    assert page["items"][0]["prelabel"] is None

    rest = (
        await api.client.get(
            "/api/v1/labels/queue", params={"limit": 2, "cursor": page["nextCursor"]}
        )
    ).json()
    assert [i["eventKey"] for i in rest["items"]] == [event_key(earlier.key)]
    assert rest["nextCursor"] is None
    mine = (await api.client.get("/api/v1/labels/queue", params={"providerId": PROVIDER_B})).json()
    assert [i["eventKey"] for i in mine["items"]] == [event_key(other.key)]


async def test_put_writes_a_label_and_leaves_the_queue(signed_in: Api) -> None:
    api = signed_in
    plain = await store(api, "Bought GOOGL 357.5C 10/2 @ 0.71", 1)
    suggested = await store(api, "Bought GOOGL 357.5C 10/2 @ 0.71", 2)
    await suggest(api, suggested, {"signals": []})

    for env in (plain, suggested):
        r = await api.client.put(
            f"/api/v1/labels/{event_key(env.key)}", json={"label": {"signals": [ENTRY]}}
        )
        assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["eventKey"] == event_key(suggested.key) and len(detail["labels"]) == 1
    current = detail["labels"][0]
    assert current["label"]["signals"][0]["fields"]["strike"] == "357.5"
    assert (current["source"], current["split"], current["tradingDay"]) == (
        "prelabel_corrected",
        split_for(date(2026, 9, 28)),
        "2026-09-28",
    )
    async with api.app.state.session_factory() as db:
        sources = {r.message_id: r.source for r in (await db.scalars(select(EvalLabel))).all()}
        stored = (await db.scalars(select(EvalLabel.label))).first()
        session_ids = set(await db.scalars(select(EvalLabel.session_id)))
    assert sources == {
        plain.key.message_id: "manual",
        suggested.key.message_id: "prelabel_corrected",
    }
    assert stored is not None and stored["signals"][0]["msg_type"] == "entry"  # stored snake_case
    assert len(session_ids) == 1 and None not in session_ids  # the labeling session
    assert (await api.client.get("/api/v1/labels/queue")).json()["items"] == []


async def test_a_correction_supersedes_and_a_stale_one_conflicts(signed_in: Api) -> None:
    api = signed_in
    env = await store(api, "gm", 0)
    path = f"/api/v1/labels/{event_key(env.key)}"
    first = (await api.client.put(path, json={"label": {"signals": []}})).json()["labels"][0]
    second = (
        await api.client.put(
            path, json={"label": {"signals": [ENTRY]}, "supersedes": first["labelId"]}
        )
    ).json()
    assert [r["supersedesLabelId"] for r in second["labels"]] == [first["labelId"], None]
    assert second["labels"][1]["labelId"] == first["labelId"]  # history kept, newest first

    stale = await api.client.put(
        path, json={"label": {"signals": []}, "supersedes": first["labelId"]}
    )
    assert stale.status_code == 409 and stale.json()["code"] == "conflict"
    third = await api.client.put(path, json={"label": {"signals": []}})  # default: the current one
    assert third.json()["labels"][0]["supersedesLabelId"] == second["labels"][0]["labelId"]
    assert (await api.client.get(path)).json()["labels"] == third.json()["labels"]


async def test_bad_requests(signed_in: Api) -> None:
    api = signed_in
    env = await store(api, "gm", 0)
    path = f"/api/v1/labels/{event_key(env.key)}"
    unknown = await api.client.put("/api/v1/labels/5.create.0", json={"label": {"signals": []}})
    assert unknown.status_code == 404
    assert (await api.client.get("/api/v1/labels/not-a-key")).status_code == 404
    assert (await api.client.get("/api/v1/labels/5.create.0")).status_code == 404
    bad = {"signals": [{"idx": 0, "msgType": "entry", "fields": {"strke": "1"}}]}
    r = await api.client.put(path, json={"label": bad})
    assert r.status_code == 422 and "unknown signal fields: strke" in r.json()["message"]
    r = await api.client.put(path, json={"label": {"signals": [{"idx": 0, "msgType": "nope"}]}})
    assert r.status_code == 422


async def test_progress_counts_current_labels_toward_the_gate_minimums(signed_in: Api) -> None:
    api = signed_in
    empty = (await api.client.get("/api/v1/labels/progress")).json()
    assert (empty["actionable"], empty["nonActionable"], empty["providers"]) == (0, 0, 0)
    assert empty["minActionable"] > 0 and empty["minProviders"] > 0

    entry = await store(api, "Bought GOOGL 357.5C 10/2 @ 0.71", 1)
    chatter = await store(api, "gm", 2, author=PROVIDER_B)
    for env, signals in ((entry, [ENTRY]), (chatter, [])):
        r = await api.client.put(
            f"/api/v1/labels/{event_key(env.key)}", json={"label": {"signals": signals}}
        )
        assert r.status_code == 200, r.text
    got = (await api.client.get("/api/v1/labels/progress")).json()
    assert (got["actionable"], got["nonActionable"], got["providers"]) == (1, 1, 2)
    # a correction supersedes: the event is still counted once
    await api.client.put(f"/api/v1/labels/{event_key(entry.key)}", json={"label": {"signals": []}})
    got = (await api.client.get("/api/v1/labels/progress")).json()
    assert (got["actionable"], got["nonActionable"]) == (0, 2)
