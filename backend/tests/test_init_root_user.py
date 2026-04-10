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

    def _load_login_names(self) -> list[str]:
        with self.session_factory() as db:
            return list(
                db.execute(select(User.login_name).order_by(User.login_name.asc()))
                .scalars()
                .all()
            )

    def test_init_root_user_creates_all_default_local_accounts(self) -> None:
        with (
            patch("backend.app.scripts.init_root_user.engine", self.engine),
            patch("backend.app.scripts.init_root_user.SessionLocal", self.session_factory),
        ):
            init_root_user()

        self.assertEqual(
            sorted(spec["login_name"] for spec in LOCAL_TEST_USERS),
            self._load_login_names(),
        )

        with self.session_factory() as db:
            created_users = db.execute(select(User).order_by(User.login_name.asc())).scalars().all()

        expected_passwords = {
            spec["login_name"]: spec["password"] for spec in LOCAL_TEST_USERS
        }
        for user in created_users:
            self.assertEqual("local", user.auth_source)
            self.assertTrue(user.is_active)
            self.assertTrue(user.is_admin)
            self.assertTrue(user.password_hash)
            self.assertTrue(
                PasswordHasher.verify_password(
                    expected_passwords[user.login_name],
                    user.password_hash,
                )
            )

    def test_init_root_user_creates_missing_accounts_when_root_already_exists(self) -> None:
        with self.session_factory() as db:
            db.add(
                User(
                    login_name="root",
                    display_name="Root User",
                    password_hash=PasswordHasher.hash_password("root"),
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
            init_root_user()

        self.assertEqual(
            sorted(spec["login_name"] for spec in LOCAL_TEST_USERS),
            self._load_login_names(),
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

        self.assertEqual(len(LOCAL_TEST_USERS), user_count)

    def test_init_root_user_fails_when_existing_account_is_not_local(self) -> None:
        with self.session_factory() as db:
            db.add(
                User(
                    login_name="ROOT1",
                    display_name="ROOT1",
                    password_hash="hash",
                    auth_source="sso",
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
                "User ROOT1 already exists but is not a local account.",
            ):
                init_root_user()

    def test_init_root_user_fails_when_existing_password_does_not_match(self) -> None:
        with self.session_factory() as db:
            db.add(
                User(
                    login_name="ROOT2",
                    display_name="ROOT2",
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
                "User ROOT2 already exists but does not match the expected local test password.",
            ):
                init_root_user()
