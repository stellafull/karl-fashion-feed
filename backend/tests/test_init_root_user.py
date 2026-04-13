from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.security import PasswordHasher
from backend.app.models import User, ensure_auth_chat_schema
from backend.app.scripts.init_root_user import LOCAL_TEST_USERS, init_root_user


class InitRootUserScriptTest(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.engine.dispose()

    def _load_login_names(self) -> list[str | None]:
        with self.session_factory() as db:
            return list(
                db.execute(select(User.login_name).order_by(User.login_name.asc()))
                .scalars()
                .all()
            )

    def test_init_root_user_creates_the_dev_root_account(self) -> None:
        with (
            patch("backend.app.scripts.init_root_user.engine", self.engine),
            patch("backend.app.scripts.init_root_user.SessionLocal", self.session_factory),
        ):
            init_root_user()

        self.assertEqual(["dev-root"], self._load_login_names())

        with self.session_factory() as db:
            created_user = db.execute(select(User)).scalar_one()

        self.assertEqual("local", created_user.auth_source)
        self.assertTrue(created_user.is_active)
        self.assertTrue(created_user.is_admin)
        self.assertTrue(created_user.password_hash)
        self.assertTrue(
            PasswordHasher.verify_password(
                LOCAL_TEST_USERS[0]["password"],
                created_user.password_hash,
            )
        )

    def test_init_root_user_is_idempotent(self) -> None:
        with (
            patch("backend.app.scripts.init_root_user.engine", self.engine),
            patch("backend.app.scripts.init_root_user.SessionLocal", self.session_factory),
        ):
            init_root_user()
            init_root_user()

        with self.session_factory() as db:
            user_count = db.query(User).count()

        self.assertEqual(1, user_count)

    def test_init_root_user_fails_when_existing_account_is_not_local(self) -> None:
        with self.session_factory() as db:
            db.add(
                User(
                    login_name="dev-root",
                    display_name="Dev Root",
                    password_hash="hash",
                    auth_source="feishu",
                    is_active=True,
                    is_admin=True,
                )
            )
            db.commit()

        with (
            patch("backend.app.scripts.init_root_user.engine", self.engine),
            patch("backend.app.scripts.init_root_user.SessionLocal", self.session_factory),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "User dev-root already exists but is not a local account.",
            ):
                init_root_user()

    def test_init_root_user_fails_when_existing_password_does_not_match(self) -> None:
        with self.session_factory() as db:
            db.add(
                User(
                    login_name="dev-root",
                    display_name="Dev Root",
                    password_hash=PasswordHasher.hash_password("different-password"),
                    auth_source="local",
                    is_active=True,
                    is_admin=True,
                )
            )
            db.commit()

        with (
            patch("backend.app.scripts.init_root_user.engine", self.engine),
            patch("backend.app.scripts.init_root_user.SessionLocal", self.session_factory),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "User dev-root already exists but does not match the expected local test password.",
            ):
                init_root_user()
