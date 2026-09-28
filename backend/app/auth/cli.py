"""Console-only passkey management (recovery = server console, PRD §13).

uv run python -m app.auth.cli enroll     # prints a one-time enrolment token (15 min)
uv run python -m app.auth.cli list
uv run python -m app.auth.cli revoke <credential-id-base64url>
"""

import argparse
import asyncio
import sys

from webauthn import base64url_to_bytes
from webauthn.helpers import bytes_to_base64url

from app.alerts.service import AlertService
from app.auth.service import AuthService
from app.config import get_settings
from app.db import create_engine, create_session_factory
from app.markettime import SystemClock


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    auth = AuthService(factory, settings, SystemClock(), AlertService(factory))
    try:
        if args.command == "enroll":
            token, expires_at = await auth.create_enrollment_token()
            print(f"Enrolment token (single use, expires {expires_at:%Y-%m-%d %H:%M} UTC):")
            print(token)
        elif args.command == "list":
            for c in await auth.credentials():
                state = f"revoked {c.revoked_at:%Y-%m-%d}" if c.revoked_at else "active"
                print(f"{bytes_to_base64url(c.credential_id)}  {c.label or '-'}  {state}")
        else:
            if not await auth.revoke(base64url_to_bytes(args.credential_id)):
                print("no active credential with that id", file=sys.stderr)
                return 1
            print("revoked (and its sessions)")
        return 0
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.auth.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("enroll", help="create a one-time token to register a new passkey")
    sub.add_parser("list", help="list registered passkeys")
    revoke = sub.add_parser("revoke", help="revoke a passkey and its sessions")
    revoke.add_argument("credential_id")
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
