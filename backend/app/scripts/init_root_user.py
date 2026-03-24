"""Initialize root user for local authentication."""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import select

from backend.app.core.database import SessionLocal, engine
from backend.app.core.security import PasswordHasher
from backend.app.models import User, ensure_auth_chat_schema


def init_root_user():
    """Create root/root test user if it doesn't exist."""
    # Ensure schema exists
    print("Ensuring auth/chat schema...")
    ensure_auth_chat_schema(engine)

    # Create session
    db = SessionLocal()
    try:
        # Check if root user already exists
        existing = db.execute(
            select(User).where(User.login_name == "root")
        ).scalar_one_or_none()

        if existing:
            print("Root user already exists, skipping creation.")
            return

        # Create root user
        root_user = User(
            login_name="root",
            display_name="Root User",
            password_hash=PasswordHasher.hash_password("root"),
            auth_source="local",
            is_active=True,
            is_admin=True,
        )

        db.add(root_user)
        db.commit()

        print(f"Root user created successfully: {root_user.user_id}")
        print("Login credentials: root / root")

    finally:
        db.close()


if __name__ == "__main__":
    init_root_user()
