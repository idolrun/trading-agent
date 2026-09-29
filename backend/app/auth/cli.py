"""Console-only user provisioning and recovery (there is no public registration).

uv run python -m app.auth.cli create-user <email>      # prints a one-time temporary password
uv run python -m app.auth.cli reset-password <email>   # same, for a locked-out user
uv run python -m app.auth.cli list
uv run python -m app.auth.cli seed-dev-user            # development only: the local desk login
"""

import argparse
import asyncio
import sys

from app.auth.audit import AuditService
from app.auth.service import AuthService, UserExists
from app.config import get_settings
from app.db import create_engine, create_session_factory
from app.markettime import SystemClock

# ponytail: fixed local login for development; refused in any other environment.
DEV_EMAIL = "dev@idolrun.com"
DEV_PASSWORD = "dev@123123"  # noqa: S105 — development seed, never usable outside development


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    auth = AuthService(factory, settings, SystemClock(), AuditService(factory))
    try:
        if args.command == "seed-dev-user":
            if settings.environment != "development":
                print("seed-dev-user runs only with ENVIRONMENT=development", file=sys.stderr)
                return 1
            user = await auth.find_user(DEV_EMAIL)
            if user is None:
                user, _ = await auth.create_user(DEV_EMAIL, None, None)
            await auth.set_password(user.user_id, DEV_PASSWORD)
            print(f"{DEV_EMAIL} ready (development password, no forced change)")
        elif args.command == "list":
            for u in await auth.users():
                state = "active" if u.is_active else "inactive"
                print(f"{u.email}  {state}{'  must-change' if u.must_change_password else ''}")
        elif args.command == "create-user":
            try:
                _, temp = await auth.create_user(args.email, None, None)
            except UserExists:
                print("a user with that email already exists", file=sys.stderr)
                return 1
            print(f"Temporary password for {args.email} (shown once, must be changed at login):")
            print(temp)
        else:
            user = await auth.find_user(args.email)
            if user is None:
                print("no user with that email", file=sys.stderr)
                return 1
            _, temp = await auth.reset_password(user.user_id, None, None)
            print(f"New temporary password for {user.email} (shown once):")
            print(temp)
        return 0
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.auth.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list users")
    sub.add_parser("seed-dev-user", help="development only: create/reset the local desk login")
    for name, text in (
        ("create-user", "add an allowed email and print its temporary password"),
        ("reset-password", "issue a new temporary password and end the user's sessions"),
    ):
        sub.add_parser(name, help=text).add_argument("email")
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
