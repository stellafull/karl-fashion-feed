from __future__ import annotations

import os
import unittest
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret")
os.environ.setdefault("FEISHU_APP_ID", "cli_test_app")
os.environ.setdefault("FEISHU_APP_SECRET", "test-feishu-secret")
os.environ.setdefault(
    "FEISHU_BROWSER_REDIRECT_URI",
    "https://backend.example.com/api/v1/auth/feishu/browser/callback",
)
os.environ.setdefault(
    "FEISHU_FRONTEND_AUTH_COMPLETE_URL",
    "https://frontend.example.com/auth/complete",
)
os.environ.setdefault("FEISHU_OAUTH_SCOPE", "contact:contact.base:readonly")

from backend.app.core.database import get_db  # noqa: E402
from backend.app.core.security import PasswordHasher  # noqa: E402
from backend.app.models import User, ensure_auth_chat_schema  # noqa: E402
from backend.app.router.auth_router import (  # noqa: E402
    get_feishu_auth_service,
    router as auth_router,
)
from backend.app.service.feishu_auth_service import FeishuUserIdentity  # noqa: E402


@dataclass(slots=True)
class FakeFeishuAuthService:
    """Deterministic fake for auth-router tests."""

    def build_browser_authorize_url(self, *, state: str) -> str:
        return (
            "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
            "?client_id=cli_test_app&response_type=code"
            "&redirect_uri=https%3A%2F%2Fbackend.example.com%2Fapi%2Fv1%2Fauth%2Ffeishu%2Fbrowser%2Fcallback"
            f"&state={state}&scope=contact%3Acontact.base%3Areadonly"
        )

    async def exchange_client_code(self, code: str) -> FeishuUserIdentity:
        if code == "bad-client-code":
            raise ValueError("invalid feishu code")
        return FeishuUserIdentity(
            feishu_user_id="ou_user_client",
            display_name="Client User",
            email="client@example.com",
            avatar_url="https://example.com/avatar-client.png",
            open_id="open-client",
            union_id="union-shared",
        )

    async def exchange_browser_code(self, code: str) -> FeishuUserIdentity:
        if code == "bad-browser-code":
            raise ValueError("invalid browser auth code")
        if code == "browser-same-user":
            return FeishuUserIdentity(
                feishu_user_id="ou_user_client",
                display_name="Client User",
                email="client@example.com",
                avatar_url="https://example.com/avatar-client.png",
                open_id="open-client-browser",
                union_id="union-shared",
            )
        return FeishuUserIdentity(
            feishu_user_id="ou_user_browser",
            display_name="Browser User",
            email="browser@example.com",
            avatar_url="https://example.com/avatar-browser.png",
            open_id="open-browser",
            union_id="union-browser",
        )


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
        app.dependency_overrides[get_feishu_auth_service] = lambda: FakeFeishuAuthService()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.engine.dispose()

    def _seed_users(self) -> None:
        with self.session_factory() as db:
            db.add_all(
                [
                    User(
                        login_name="dev-root",
                        display_name="Dev Root",
                        password_hash=PasswordHasher.hash_password("dev-root"),
                        auth_source="local",
                        is_active=True,
                        is_admin=True,
                    ),
                    User(
                        login_name="other-local",
                        display_name="Other Local",
                        password_hash=PasswordHasher.hash_password("other-local"),
                        auth_source="local",
                        is_active=True,
                        is_admin=False,
                    ),
                ]
            )
            db.commit()

    def _dev_login(self, login_name: str, password: str):
        return self.client.post(
            "/api/v1/auth/dev/token",
            data={
                "username": login_name,
                "password": password,
                "grant_type": "password",
            },
        )

    def test_dev_root_can_log_in_via_dev_endpoint(self) -> None:
        response = self._dev_login("dev-root", "dev-root")

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("bearer", payload["token_type"])
        self.assertTrue(payload["access_token"])
        self.assertEqual("dev-root", payload["user"]["login_name"])
        self.assertTrue(payload["user"]["is_admin"])
        self.assertIsNone(payload["user"]["avatar_url"])

    def test_dev_endpoint_rejects_non_dev_root_local_account(self) -> None:
        response = self._dev_login("other-local", "other-local")

        self.assertEqual(403, response.status_code)
        self.assertEqual("Only dev-root may use dev login", response.json()["detail"])

    def test_legacy_local_password_route_is_not_available(self) -> None:
        response = self.client.post(
            "/api/v1/auth/token",
            data={"username": "dev-root", "password": "dev-root", "grant_type": "password"},
        )

        self.assertEqual(404, response.status_code)

    def test_feishu_client_exchange_auto_provisions_user_and_auth_me_works(self) -> None:
        response = self.client.post(
            "/api/v1/auth/feishu/client/exchange",
            json={"code": "client-good"},
        )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("feishu", payload["user"]["auth_source"])
        self.assertEqual("https://example.com/avatar-client.png", payload["user"]["avatar_url"])
        token = payload["access_token"]

        profile_response = self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(200, profile_response.status_code)
        self.assertEqual("Client User", profile_response.json()["display_name"])
        self.assertEqual("https://example.com/avatar-client.png", profile_response.json()["avatar_url"])

        with self.session_factory() as db:
            stored_user = db.execute(
                select(User).where(User.feishu_user_id == "ou_user_client")
            ).scalar_one()
            self.assertEqual("feishu", stored_user.auth_source)
            self.assertEqual("Client User", stored_user.display_name)
            self.assertEqual("client@example.com", stored_user.email)
            self.assertIsNotNone(stored_user.last_login_at)

    def test_browser_start_redirects_to_feishu_authorize_url(self) -> None:
        response = self.client.get(
            "/api/v1/auth/feishu/browser/start",
            follow_redirects=False,
        )

        self.assertEqual(307, response.status_code)
        location = response.headers["location"]
        parsed = urlparse(location)
        self.assertEqual("accounts.feishu.cn", parsed.netloc)
        self.assertEqual("/open-apis/authen/v1/authorize", parsed.path)
        params = parse_qs(parsed.query)
        self.assertEqual(["cli_test_app"], params["client_id"])
        self.assertEqual(["code"], params["response_type"])
        self.assertEqual(
            ["https://backend.example.com/api/v1/auth/feishu/browser/callback"],
            params["redirect_uri"],
        )
        self.assertIn("state", params)
        self.assertEqual(["contact:contact.base:readonly"], params["scope"])

    def test_browser_callback_rejects_invalid_state(self) -> None:
        response = self.client.get(
            "/api/v1/auth/feishu/browser/callback",
            params={"code": "browser-good", "state": "invalid-state"},
            follow_redirects=False,
        )

        self.assertEqual(401, response.status_code)
        self.assertEqual("Invalid browser auth state", response.json()["detail"])

    def test_browser_callback_reuses_existing_feishu_user_without_duplication(self) -> None:
        client_exchange = self.client.post(
            "/api/v1/auth/feishu/client/exchange",
            json={"code": "client-good"},
        )
        self.assertEqual(200, client_exchange.status_code)

        start_response = self.client.get(
            "/api/v1/auth/feishu/browser/start",
            follow_redirects=False,
        )
        state = parse_qs(urlparse(start_response.headers["location"]).query)["state"][0]

        callback_response = self.client.get(
            "/api/v1/auth/feishu/browser/callback",
            params={"code": "browser-same-user", "state": state},
            follow_redirects=False,
        )

        self.assertEqual(307, callback_response.status_code)
        location = callback_response.headers["location"]
        self.assertTrue(location.startswith("https://frontend.example.com/auth/complete"))
        token = parse_qs(urlparse(location).query)["token"][0]

        profile_response = self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(200, profile_response.status_code)
        self.assertEqual("Client User", profile_response.json()["display_name"])
        self.assertEqual("https://example.com/avatar-client.png", profile_response.json()["avatar_url"])

        with self.session_factory() as db:
            users = db.execute(
                select(User).where(User.feishu_user_id == "ou_user_client")
            ).scalars().all()
            self.assertEqual(1, len(users))
