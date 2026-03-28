"""Shared Celery application for runtime tasks."""

from __future__ import annotations

from celery import Celery

from backend.app.config.celery_config import build_celery_settings


celery_app = Celery("karl-fashion-feed")
celery_app.conf.update(build_celery_settings())
