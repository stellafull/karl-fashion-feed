"""Initialize local authentication users for integration testing."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TypedDict

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal, engine
from backend.app.core.security import PasswordHasher
from backend.app.models import User, ensure_auth_chat_schema


class LocalTestUserSpec(TypedDict):
    """Specification for a local integration-test user."""

    login_name: str
    display_name: str
    password: str
    is_admin: bool


LOCAL_TEST_USERS: tuple[LocalTestUserSpec, ...] = (
    {
        "login_name": "root",
        "display_name": "Root User",
        "password": "root",
        "is_admin": True,
    },
    {
        "login_name": "ROOT1",
        "display_name": "ROOT1",
        "password": "ROOT1",
        "is_admin": True,
    },
    {
        "login_name": "ROOT2",
        "display_name": "ROOT2",
        "password": "ROOT2",
        "is_admin": True,
    },
)


def _validate_existing_user(existing: User, spec: LocalTestUserSpec) -> None:
    """Fail fast when an existing account cannot serve as a local test user."""
    login_name = spec["login_name"]
    if existing.auth_source != "local":
        raise RuntimeError(f"User {login_name} already exists but is not a local account.")
    if not existing.is_active:
        raise RuntimeError(f"User {login_name} already exists but is inactive.")
    if not existing.password_hash:
        raise RuntimeError(f"User {login_name} already exists without a password hash.")
    if not PasswordHasher.verify_password(spec["password"], existing.password_hash):
        raise RuntimeError(
            f"User {login_name} already exists but does not match the expected local test password."
        )
    if existing.is_admin != spec["is_admin"]:
        raise RuntimeError(
            f"User {login_name} already exists but does not match the expected admin role."
        )


def _ensure_local_test_user(db: Session, spec: LocalTestUserSpec) -> bool:
    """Create one local test user when it does not already exist."""
    existing = db.execute(
        select(User).where(User.login_name == spec["login_name"])
    ).scalar_one_or_none()
    if existing:
        _validate_existing_user(existing, spec)
        print(f"{spec['login_name']} already exists, skipping creation.")
        return False

    user = User(
        login_name=spec["login_name"],
        display_name=spec["display_name"],
        password_hash=PasswordHasher.hash_password(spec["password"]),
        auth_source="local",
        is_active=True,
        is_admin=spec["is_admin"],
    )
    db.add(user)
    db.flush()
    print(f"Created local test user: {user.login_name} ({user.user_id})")
    return True


def init_root_user() -> None:
    """Ensure the default local integration-test users exist."""
    # Ensure schema exists
    print("Ensuring auth/chat schema...")
    ensure_auth_chat_schema(engine)

    created_count = 0
    db = SessionLocal()
    try:
        for spec in LOCAL_TEST_USERS:
            created_count += int(_ensure_local_test_user(db, spec))
        db.commit()
    finally:
        db.close()

    if created_count == 0:
        print("All local test users already exist.")
    else:
        print(f"Created {created_count} local test user(s).")

    print("Available login credentials:")
    for spec in LOCAL_TEST_USERS:
        print(f"- {spec['login_name']} / {spec['password']}")


if __name__ == "__main__":
    init_root_user()
