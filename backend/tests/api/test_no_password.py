import json

from app.main import create_app


def test_no_route_accepts_a_password() -> None:
    """AC-10: passkey only — nothing in the API contract mentions a password."""
    assert "password" not in json.dumps(create_app().openapi()).lower()
