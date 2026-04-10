from __future__ import annotations

import os
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret")

from backend.app.core.database import get_db  # noqa: E402
from backend.app.core.security import PasswordHasher  # noqa: E402
from backend.app.models import User, ensure_auth_chat_schema  # noqa: E402
from backend.app.router.auth_router import router as auth_router  # noqa: E402


class AuthRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        ensure_auth_chat_schema(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            future=True,
            expire_on_commit=False,
        )
        self._seed_users()

        app = FastAPI()
        app.include_router(auth_router, prefix="/api/v1")

        def override_get_db():
            db = self.session_factory()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.engine.dispose()

    def _seed_users(self) -> None:
        with self.session_factory() as db:
            db.add_all(
                [
                    User(
                        login_name="ROOT1",
                        display_name="ROOT1",
                        password_hash=PasswordHasher.hash_password("ROOT1"),
                        auth_source="local",
                        is_active=True,
                        is_admin=True,
                    ),
                    User(
                        login_name="ROOT2",
                        display_name="ROOT2",
                        password_hash=PasswordHasher.hash_password("ROOT2"),
                        auth_source="local",
                        is_active=True,
                        is_admin=True,
                    ),
                ]
            )
            db.commit()

    def _login(self, login_name: str, password: str):
        return self.client.post(
            "/api/v1/auth/token",
            data={
                "username": login_name,
                "password": password,
                "grant_type": "password",
            },
        )

    def test_root_accounts_can_log_in(self) -> None:
        for login_name in ("ROOT1", "ROOT2"):
            with self.subTest(login_name=login_name):
                response = self._login(login_name, login_name)

                self.assertEqual(200, response.status_code)
                payload = response.json()
                self.assertEqual("bearer", payload["token_type"])
                self.assertTrue(payload["access_token"])
                self.assertEqual(login_name, payload["user"]["login_name"])
                self.assertTrue(payload["user"]["is_admin"])

    def test_login_updates_last_login_and_auth_me_returns_same_user(self) -> None:
        response = self._login("ROOT1", "ROOT1")

        self.assertEqual(200, response.status_code)
        token = response.json()["access_token"]

        profile_response = self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(200, profile_response.status_code)
        self.assertEqual("ROOT1", profile_response.json()["login_name"])

        with self.session_factory() as db:
            stored_user = db.execute(
                select(User).where(User.login_name == "ROOT1")
            ).scalar_one()
            self.assertIsNotNone(stored_user.last_login_at)

    def test_login_rejects_wrong_password(self) -> None:
        response = self._login("ROOT2", "wrong-password")

        self.assertEqual(401, response.status_code)
        self.assertEqual("Incorrect login name or password", response.json()["detail"])
