"""`uv run python -m app.api.export_openapi > ../frontend/openapi.json` — the API contract the
frontend generates its types from (F052). A snapshot test fails on unintended changes."""

import json
import sys
from typing import Any

from app.main import create_app


def spec() -> dict[str, Any]:
    return create_app().openapi()


if __name__ == "__main__":
    json.dump(spec(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
