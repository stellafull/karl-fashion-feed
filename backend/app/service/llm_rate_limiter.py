"""Redis-backed lease limiter for shared LLM stages."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import redis

from backend.app.config.celery_config import build_celery_broker_url


class LlmRateLimiter:
    """Coordinate one shared Redis lease per LLM bucket across workers."""

    def __init__(
        self,
        *,
        redis_client: redis.Redis | None = None,
        lease_ttl_seconds: int | None = None,
        poll_interval_seconds: float | None = None,
        key_prefix: str = "llm-rate-limit",
    ) -> None:
        self._redis = redis_client or redis.Redis.from_url(
            build_celery_broker_url(),
            decode_responses=True,
        )
        self._lease_ttl_seconds = lease_ttl_seconds or int(
            os.getenv("LLM_RATE_LIMIT_LEASE_TTL_SECONDS", "600")
        )
        self._poll_interval_seconds = poll_interval_seconds or float(
            os.getenv("LLM_RATE_LIMIT_POLL_INTERVAL_SECONDS", "1")
        )
        self._key_prefix = key_prefix

    @contextmanager
    def lease(self, bucket: str) -> Iterator[None]:
        """Acquire a shared Redis lease for one LLM bucket."""
        token = self._acquire(bucket)
        try:
            yield
        finally:
            self._release(bucket, token)

    def _acquire(self, bucket: str) -> str:
        key = self._build_key(bucket)
        token = str(uuid.uuid4())
        while True:
            acquired = self._redis.set(
                key,
                token,
                nx=True,
                ex=self._lease_ttl_seconds,
            )
            if acquired:
                return token
            time.sleep(self._poll_interval_seconds)

    def _release(self, bucket: str, token: str) -> None:
        key = self._build_key(bucket)
        release_script = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""
        self._redis.eval(release_script, 1, key, token)

    def _build_key(self, bucket: str) -> str:
        normalized_bucket = bucket.strip()
        if not normalized_bucket:
            raise ValueError("bucket cannot be blank")
        return f"{self._key_prefix}:{normalized_bucket}"
