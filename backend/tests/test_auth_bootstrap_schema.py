from __future__ import annotations

import unittest

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from backend.app.models import ensure_auth_chat_schema


class AuthBootstrapSchemaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_bootstrap_repairs_legacy_user_table_with_feishu_columns(self) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    '''
                    CREATE TABLE "user" (
                        user_id VARCHAR(36) PRIMARY KEY,
                        login_name VARCHAR(64) NOT NULL,
                        display_name VARCHAR(128) NOT NULL,
                        email VARCHAR(255),
                        password_hash VARCHAR(255),
                        auth_source VARCHAR(16) NOT NULL,
                        is_active BOOLEAN NOT NULL,
                        is_admin BOOLEAN NOT NULL,
                        last_login_at TIMESTAMP,
                        created_at TIMESTAMP NOT NULL,
                        updated_at TIMESTAMP NOT NULL
                    )
                    '''
                )
            )

        ensure_auth_chat_schema(self.engine)

        inspector = inspect(self.engine)
        columns = {column["name"] for column in inspector.get_columns("user")}
        self.assertIn("feishu_user_id", columns)
        self.assertIn("feishu_open_id", columns)
        self.assertIn("feishu_union_id", columns)
        self.assertIn("feishu_avatar_url", columns)
        indexes = {index["name"] for index in inspector.get_indexes("user")}
        self.assertIn("ix_user_feishu_user_id", indexes)

