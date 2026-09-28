"""F030: the committed OpenAPI contract matches the app. On an intended change, regenerate:
`uv run python -m app.api.export_openapi > ../frontend/openapi.json`."""

import json
from pathlib import Path

from app.api.export_openapi import spec

SNAPSHOT = Path(__file__).parents[3] / "frontend" / "openapi.json"


def test_openapi_matches_snapshot() -> None:
    assert json.loads(json.dumps(spec())) == json.loads(SNAPSHOT.read_text())
