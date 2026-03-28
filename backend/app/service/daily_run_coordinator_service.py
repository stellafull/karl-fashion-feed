"""Daily control-plane coordinator for runtime rescans and batch triggering."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.config.source_config import load_source_configs
from backend.app.core.database import SessionLocal
from backend.app.models import Article, PipelineRun, SourceRunState, ensure_article_storage_schema
from backend.app.models.runtime import (
    ARTICLE_STAGE_MAX_ATTEMPTS,
    BATCH_STAGE_MAX_ATTEMPTS,
    DEFAULT_STALE_STATE_TIMEOUT,
    SOURCE_RUN_MAX_ATTEMPTS,
    business_day_for_runtime,
    coerce_utc_naive,
    utc_bounds_for_business_day,
)
from backend.app.tasks.aggregation_tasks import generate_digests_for_day, pack_strict_stories_for_day
from backend.app.tasks.content_tasks import collect_source, extract_event_frames, parse_article

RUN_TYPE_DAILY_DIGEST = "digest_daily"
RETRYABLE_STATUSES = {"pending", "failed"}
ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"done", "abandoned"}
STALE_RECLAIM_ERROR = "RuntimeError: stale runtime state reclaimed by coordinator"
Dispatch = Callable[[], None]
RepairDispatch = Callable[[Exception], None]


@dataclass(frozen=True)
class PendingDispatch:
    """Track a post-commit publish and its immediate repair path."""

    publish: Dispatch
    repair: RepairDispatch


class DailyRunCoordinatorService:
    """Drive the current business-day run by rescanning state and enqueueing work."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        stale_after: timedelta = DEFAULT_STALE_STATE_TIMEOUT,
        source_names: Sequence[str] | None = None,
        limit_sources: int | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._stale_after = stale_after
        self._source_names = tuple(source_names) if source_names is not None else None
        self._limit_sources = limit_sources
        if self._limit_sources is not None and self._limit_sources <= 0:
            raise ValueError("limit_sources must be greater than zero")

    def tick(self, *, now: datetime | None = None) -> str:
        """Rescan runtime state, enqueue retryable work, and trigger batch jobs."""
        observed_at = coerce_utc_naive(now or datetime.now(UTC))
        business_day = business_day_for_runtime(observed_at)
        source_names = self._enabled_source_names()
        dispatches: list[PendingDispatch] = []

        with self._session_factory() as session:
            ensure_article_storage_schema(session.get_bind())
            run = self._ensure_run_for_day(session, business_day, observed_at)
            self._reclaim_stale_source_states(session, run.run_id, observed_at)
            self._reclaim_stale_article_states(session, business_day, observed_at)
            self._reclaim_stale_batch_states(run, observed_at)
            self._enqueue_retryable_sources(
                session,
                run.run_id,
                source_names,
                observed_at,
                dispatches=dispatches,
            )
            self._enqueue_retryable_articles(
                session,
                business_day,
                stage="parse",
                observed_at=observed_at,
                dispatches=dispatches,
            )
            self._enqueue_retryable_articles(
                session,
                business_day,
                stage="event_frame",
                observed_at=observed_at,
                dispatches=dispatches,
            )
            self._enqueue_batch_jobs_if_ready(
                session,
                run,
                source_names,
                observed_at,
                dispatches=dispatches,
            )
            self._refresh_run_status(run, observed_at)
            self._refresh_run_metadata(session, run, business_day, source_names)
            session.commit()
            run_id = run.run_id

        self._publish_dispatches(dispatches)

        return run_id

    def drain_until_idle(
        self,
        *,
        run_id: str,
        business_day: date,
        skip_collect: bool = False,
        max_ticks: int = 32,
    ) -> None:
        """Run coordinator ticks until digest run is terminal for the day."""
        for _ in range(max_ticks):
            self.tick()
            with self._session_factory() as session:
                run = session.get(PipelineRun, run_id)
                if run is None:
                    raise RuntimeError(f"pipeline run missing while draining runtime: run_id={run_id}")
                if run.business_date != business_day:
                    raise RuntimeError(
                        "pipeline run business day changed while draining runtime: "
                        f"run_id={run_id} expected={business_day.isoformat()} got={run.business_date.isoformat()}"
                    )
                if run.status in {"done", "failed"}:
                    return
        raise RuntimeError(
            f"coordinator runtime did not drain within {max_ticks} ticks: "
            f"run_id={run_id} business_day={business_day.isoformat()} skip_collect={skip_collect}"
        )

    def _publish_dispatches(self, dispatches: Sequence[PendingDispatch]) -> None:
        first_error: Exception | None = None
        for dispatch in dispatches:
            try:
                dispatch.publish()
            except Exception as exc:
                dispatch.repair(exc)
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def _ensure_run_for_day(
        self,
        session: Session,
        business_day: date,
        observed_at: datetime,
    ) -> PipelineRun:
        runs = list(
            session.scalars(
                select(PipelineRun)
                .where(
                    PipelineRun.business_date == business_day,
                    PipelineRun.run_type == RUN_TYPE_DAILY_DIGEST,
                )
                .order_by(PipelineRun.started_at.asc(), PipelineRun.run_id.asc())
            ).all()
        )
        if len(runs) > 1:
            raise RuntimeError(f"multiple pipeline runs found for business day {business_day.isoformat()}")
        if runs:
            run = runs[0]
            if run.status == "pending":
                run.status = "running"
            return run

        run = PipelineRun(
            business_date=business_day,
            run_type=RUN_TYPE_DAILY_DIGEST,
            status="running",
            started_at=observed_at,
            strict_story_updated_at=observed_at,
            strict_story_token=0,
            digest_updated_at=observed_at,
            digest_token=0,
            metadata_json={},
        )
        session.add(run)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            existing_run = session.scalar(
                select(PipelineRun).where(
                    PipelineRun.business_date == business_day,
                    PipelineRun.run_type == RUN_TYPE_DAILY_DIGEST,
                )
            )
            if existing_run is None:
                raise
            if existing_run.status == "pending":
                existing_run.status = "running"
            return existing_run
        return run

    def _reclaim_stale_source_states(
        self,
        session: Session,
        run_id: str,
        observed_at: datetime,
    ) -> None:
        cutoff = observed_at - self._stale_after
        states = list(
            session.scalars(
                select(SourceRunState)
                .where(
                    SourceRunState.run_id == run_id,
                    SourceRunState.status.in_(tuple(ACTIVE_STATUSES)),
                    SourceRunState.updated_at < cutoff,
                )
                .order_by(SourceRunState.source_name.asc())
            ).all()
        )
        for state in states:
            consume_attempt = state.status == "running"
            if consume_attempt:
                state.attempts += 1
            state.status = "abandoned" if state.attempts >= SOURCE_RUN_MAX_ATTEMPTS else "failed"
            state.error = STALE_RECLAIM_ERROR
            state.updated_at = observed_at

    def _reclaim_stale_article_states(
        self,
        session: Session,
        business_day: date,
        observed_at: datetime,
    ) -> None:
        cutoff = observed_at - self._stale_after
        for stage in ("parse", "event_frame"):
            status_column = getattr(Article, f"{stage}_status")
            updated_at_column = getattr(Article, f"{stage}_updated_at")
            stale_articles = list(
                session.scalars(
                    select(Article)
                    .where(
                        self._article_business_day_clause(business_day),
                        status_column.in_(tuple(ACTIVE_STATUSES)),
                        updated_at_column < cutoff,
                )
                .order_by(Article.article_id.asc())
            ).all()
            )
            for article in stale_articles:
                self._mark_article_failed(
                    article,
                    stage=stage,
                    observed_at=observed_at,
                    consume_attempt=bool(getattr(article, f"{stage}_status") == "running"),
                )

    def _reclaim_stale_batch_states(
        self,
        run: PipelineRun,
        observed_at: datetime,
    ) -> None:
        cutoff = observed_at - self._stale_after
        self._reclaim_stale_batch_stage(
            run,
            stage="strict_story",
            observed_at=observed_at,
            cutoff=cutoff,
        )
        self._reclaim_stale_batch_stage(
            run,
            stage="digest",
            observed_at=observed_at,
            cutoff=cutoff,
        )

    @staticmethod
    def _reclaim_stale_batch_stage(
        run: PipelineRun,
        *,
        stage: str,
        observed_at: datetime,
        cutoff: datetime,
    ) -> None:
        status_field = f"{stage}_status"
        attempts_field = f"{stage}_attempts"
        error_field = f"{stage}_error"
        updated_at_field = f"{stage}_updated_at"
        status = getattr(run, status_field)
        updated_at = getattr(run, updated_at_field)
        if status not in ACTIVE_STATUSES or updated_at >= cutoff:
            return

        attempts = int(getattr(run, attempts_field))
        if status == "running":
            attempts += 1
            setattr(run, attempts_field, attempts)
        setattr(
            run,
            status_field,
            "abandoned" if attempts >= BATCH_STAGE_MAX_ATTEMPTS else "failed",
        )
        setattr(run, error_field, STALE_RECLAIM_ERROR)
        setattr(run, updated_at_field, observed_at)

    def _enqueue_retryable_sources(
        self,
        session: Session,
        run_id: str,
        source_names: Sequence[str],
        observed_at: datetime,
        *,
        dispatches: list[PendingDispatch],
    ) -> None:
        if not source_names:
            return

        state_by_source = {
            state.source_name: state
            for state in session.scalars(
                select(SourceRunState)
                .where(SourceRunState.run_id == run_id)
                .order_by(SourceRunState.source_name.asc())
            ).all()
        }

        for source_name in source_names:
            state = state_by_source.get(source_name)
            if state is None:
                state = SourceRunState(run_id=run_id, source_name=source_name)
                session.add(state)
                session.flush()
                state_by_source[source_name] = state

            if not self._source_is_retryable(state):
                continue

            state.status = "queued"
            state.updated_at = observed_at
            session.flush()
            dispatches.append(
                PendingDispatch(
                    publish=lambda source_name=source_name, run_id=run_id: collect_source.delay(source_name, run_id),
                    repair=lambda exc, run_id=run_id, source_name=source_name: self._repair_source_publish_failure(
                        run_id,
                        source_name,
                        exc,
                    ),
                )
            )

    def _enqueue_retryable_articles(
        self,
        session: Session,
        business_day: date,
        *,
        stage: str,
        observed_at: datetime,
        dispatches: list[PendingDispatch],
    ) -> None:
        if stage not in {"parse", "event_frame"}:
            raise ValueError(f"unsupported article stage: {stage}")

        status_column = getattr(Article, f"{stage}_status")
        attempts_column = getattr(Article, f"{stage}_attempts")
        updated_at_field = f"{stage}_updated_at"
        task = parse_article if stage == "parse" else extract_event_frames
        statement = (
            select(Article)
            .where(
                self._article_business_day_clause(business_day),
                self._retryable_article_clause(stage=stage, status_column=status_column, attempts_column=attempts_column),
            )
            .order_by(Article.article_id.asc())
        )
        if stage == "event_frame":
            statement = statement.where(Article.parse_status == "done")

        for article in session.scalars(statement).all():
            setattr(article, f"{stage}_status", "queued")
            setattr(article, updated_at_field, observed_at)
            session.flush()
            dispatches.append(
                PendingDispatch(
                    publish=lambda article_id=article.article_id, task=task: task.delay(article_id),
                    repair=lambda exc, article_id=article.article_id, stage=stage: self._repair_article_publish_failure(
                        article_id,
                        stage=stage,
                        exc=exc,
                    ),
                )
            )

    def _refresh_run_metadata(
        self,
        session: Session,
        run: PipelineRun,
        business_day: date,
        source_names: Sequence[str],
    ) -> None:
        source_states = list(
            session.scalars(
                select(SourceRunState)
                .where(SourceRunState.run_id == run.run_id)
                .order_by(SourceRunState.source_name.asc())
            ).all()
        )
        articles = self._load_articles_for_business_day(session, business_day)
        parse_counts = Counter(article.parse_status for article in articles)
        event_frame_counts = Counter(article.event_frame_status for article in articles)
        source_counts = Counter(state.status for state in source_states)
        retryable_sources = sum(
            1
            for source_name in source_names
            if self._source_name_is_retryable(source_name, source_states)
        )

        run.metadata_json = {
            "source_status_counts": dict(sorted(source_counts.items())),
            "parse_status_counts": dict(sorted(parse_counts.items())),
            "event_frame_status_counts": dict(sorted(event_frame_counts.items())),
            "configured_source_count": len(source_names),
            "retryable_source_count": retryable_sources,
            "retryable_parse_article_count": self._count_retryable_articles(
                session,
                business_day,
                stage="parse",
            ),
            "retryable_event_frame_article_count": self._count_retryable_articles(
                session,
                business_day,
                stage="event_frame",
            ),
            "batch_status_counts": self._batch_status_counts(run),
            "batch_stage_summary": self._batch_stage_summary(run),
            "failure_summary": self._failure_summary(
                run,
                source_states=source_states,
                articles=articles,
            ),
        }

    def _enqueue_batch_jobs_if_ready(
        self,
        session: Session,
        run: PipelineRun,
        source_names: Sequence[str],
        observed_at: datetime,
        *,
        dispatches: list[PendingDispatch],
    ) -> None:
        front_stages_drained = self._front_stages_drained(
            session,
            run.run_id,
            run.business_date,
            source_names,
        )
        if front_stages_drained:
            self._enqueue_unique_pack(session, run, observed_at, dispatches=dispatches)
        if front_stages_drained and run.strict_story_status == "done" and run.digest_status in {"pending", "failed"}:
            self._enqueue_unique_digest(session, run, observed_at, dispatches=dispatches)

    def _enqueue_unique_pack(
        self,
        session: Session,
        run: PipelineRun,
        observed_at: datetime,
        *,
        dispatches: list[PendingDispatch],
    ) -> None:
        if run.strict_story_status not in RETRYABLE_STATUSES:
            return
        if run.strict_story_attempts >= BATCH_STAGE_MAX_ATTEMPTS:
            run.strict_story_status = "abandoned"
            return

        run.strict_story_status = "queued"
        run.strict_story_updated_at = observed_at
        run.strict_story_token += 1
        ownership_token = run.strict_story_token
        session.flush()
        dispatches.append(
            PendingDispatch(
                publish=lambda business_day_iso=run.business_date.isoformat(),
                run_id=run.run_id,
                ownership_token=ownership_token: pack_strict_stories_for_day.delay(
                    business_day_iso,
                    run_id,
                    ownership_token,
                ),
                repair=lambda exc, run_id=run.run_id: self._repair_batch_publish_failure(
                    run_id,
                    stage="strict_story",
                    exc=exc,
                ),
            )
        )

    def _enqueue_unique_digest(
        self,
        session: Session,
        run: PipelineRun,
        observed_at: datetime,
        *,
        dispatches: list[PendingDispatch],
    ) -> None:
        if run.digest_attempts >= BATCH_STAGE_MAX_ATTEMPTS:
            run.digest_status = "abandoned"
            return

        run.digest_status = "queued"
        run.digest_updated_at = observed_at
        run.digest_token += 1
        ownership_token = run.digest_token
        session.flush()
        dispatches.append(
            PendingDispatch(
                publish=lambda business_day_iso=run.business_date.isoformat(),
                run_id=run.run_id,
                ownership_token=ownership_token: generate_digests_for_day.delay(
                    business_day_iso,
                    run_id,
                    ownership_token,
                ),
                repair=lambda exc, run_id=run.run_id: self._repair_batch_publish_failure(
                    run_id,
                    stage="digest",
                    exc=exc,
                ),
            )
        )

    def _front_stages_drained(
        self,
        session: Session,
        run_id: str,
        business_day: date,
        source_names: Sequence[str],
    ) -> bool:
        if self._count_unfinished_sources(session, run_id, source_names) > 0:
            return False
        if self._count_retryable_articles(session, business_day, stage="parse") > 0:
            return False
        if self._count_active_articles(session, business_day, stage="parse") > 0:
            return False
        if self._count_retryable_articles(session, business_day, stage="event_frame") > 0:
            return False
        if self._count_active_articles(session, business_day, stage="event_frame") > 0:
            return False
        return True

    def _count_unfinished_sources(
        self,
        session: Session,
        run_id: str,
        source_names: Sequence[str],
    ) -> int:
        if not source_names:
            return 0

        state_by_source = {
            state.source_name: state
            for state in session.scalars(
                select(SourceRunState).where(SourceRunState.run_id == run_id)
            ).all()
        }
        unfinished = 0
        for source_name in source_names:
            state = state_by_source.get(source_name)
            if state is None:
                unfinished += 1
                continue
            if state.status in ACTIVE_STATUSES or self._source_is_retryable(state):
                unfinished += 1
        return unfinished

    def _count_retryable_articles(
        self,
        session: Session,
        business_day: date,
        *,
        stage: str,
    ) -> int:
        status_column = getattr(Article, f"{stage}_status")
        attempts_column = getattr(Article, f"{stage}_attempts")
        statement = select(Article.article_id).where(
            self._article_business_day_clause(business_day),
            self._retryable_article_clause(
                stage=stage,
                status_column=status_column,
                attempts_column=attempts_column,
            ),
        )
        if stage == "event_frame":
            statement = statement.where(Article.parse_status == "done")
        return len(session.execute(statement).all())

    def _count_active_articles(
        self,
        session: Session,
        business_day: date,
        *,
        stage: str,
    ) -> int:
        status_column = getattr(Article, f"{stage}_status")
        statement = select(Article.article_id).where(
            self._article_business_day_clause(business_day),
            status_column.in_(tuple(ACTIVE_STATUSES)),
        )
        if stage == "event_frame":
            statement = statement.where(Article.parse_status == "done")
        return len(session.execute(statement).all())

    def _refresh_run_status(
        self,
        run: PipelineRun,
        observed_at: datetime,
    ) -> None:
        if run.digest_status == "done":
            run.status = "done"
            run.finished_at = run.finished_at or observed_at
            return
        if run.strict_story_status == "abandoned" or run.digest_status == "abandoned":
            run.status = "failed"
            run.finished_at = run.finished_at or observed_at
            return
        run.status = "running"
        run.finished_at = None

    def _repair_source_publish_failure(
        self,
        run_id: str,
        source_name: str,
        exc: Exception,
    ) -> None:
        with self._session_factory() as session:
            observed_at = coerce_utc_naive(datetime.now(UTC))
            run = session.get(PipelineRun, run_id)
            if run is None:
                raise RuntimeError(f"missing pipeline run while repairing publish failure: run_id={run_id}")
            state = session.get(SourceRunState, {"run_id": run_id, "source_name": source_name})
            if state is None:
                raise RuntimeError(
                    f"missing source runtime state while repairing publish failure: run={run_id} source={source_name}"
                )
            if state.status == "queued":
                state.status = "failed"
                state.error = self._format_publish_error(exc)
                state.updated_at = observed_at
            elif not self._publish_ownership_moved(state.status):
                raise RuntimeError(
                    f"unexpected source runtime status while repairing publish failure: "
                    f"run={run_id} source={source_name} status={state.status}"
                )
            self._refresh_run_status(run, observed_at)
            self._refresh_run_metadata(
                session,
                run,
                run.business_date,
                self._enabled_source_names(),
            )
            session.commit()

    def _repair_article_publish_failure(
        self,
        article_id: str,
        *,
        stage: str,
        exc: Exception,
    ) -> None:
        with self._session_factory() as session:
            observed_at = coerce_utc_naive(datetime.now(UTC))
            article = session.get(Article, article_id)
            if article is None:
                raise RuntimeError(f"missing article while repairing publish failure: article_id={article_id}")
            run = self._load_run_for_business_day(
                session,
                business_day=business_day_for_runtime(article.ingested_at),
            )

            status_field = f"{stage}_status"
            error_field = f"{stage}_error"
            updated_at_field = f"{stage}_updated_at"
            status = getattr(article, status_field)
            if status == "queued":
                setattr(article, status_field, "failed")
                setattr(article, error_field, self._format_publish_error(exc))
                setattr(article, updated_at_field, observed_at)
            elif not self._publish_ownership_moved(status):
                raise RuntimeError(
                    f"unexpected article runtime status while repairing publish failure: "
                    f"article_id={article_id} stage={stage} status={status}"
                )
            self._refresh_run_status(run, observed_at)
            self._refresh_run_metadata(
                session,
                run,
                run.business_date,
                self._enabled_source_names(),
            )
            session.commit()

    def _repair_batch_publish_failure(
        self,
        run_id: str,
        *,
        stage: str,
        exc: Exception,
    ) -> None:
        with self._session_factory() as session:
            observed_at = coerce_utc_naive(datetime.now(UTC))
            run = session.get(PipelineRun, run_id)
            if run is None:
                raise RuntimeError(f"missing pipeline run while repairing publish failure: run_id={run_id}")

            status_field = f"{stage}_status"
            error_field = f"{stage}_error"
            updated_at_field = f"{stage}_updated_at"
            status = getattr(run, status_field)
            if status == "queued":
                setattr(run, status_field, "failed")
                setattr(run, error_field, self._format_publish_error(exc))
                setattr(run, updated_at_field, observed_at)
            elif not self._publish_ownership_moved(status):
                raise RuntimeError(
                    f"unexpected batch runtime status while repairing publish failure: "
                    f"run_id={run_id} stage={stage} status={status}"
                )
            self._refresh_run_status(run, observed_at)
            self._refresh_run_metadata(
                session,
                run,
                run.business_date,
                self._enabled_source_names(),
            )
            session.commit()

    def _load_articles_for_business_day(self, session: Session, business_day: date) -> list[Article]:
        return list(
            session.scalars(
                select(Article)
                .where(self._article_business_day_clause(business_day))
                .order_by(Article.article_id.asc())
            ).all()
        )

    @staticmethod
    def _retryable_article_clause(*, stage: str, status_column: object, attempts_column: object) -> object:
        del stage
        return or_(
            status_column == "pending",
            and_(status_column == "failed", attempts_column < ARTICLE_STAGE_MAX_ATTEMPTS),
        )

    def _enabled_source_names(self) -> tuple[str, ...]:
        configured = tuple(source.name for source in load_source_configs())
        if self._source_names is not None:
            configured_set = set(configured)
            unknown_sources = sorted(source_name for source_name in self._source_names if source_name not in configured_set)
            if unknown_sources:
                raise ValueError(f"unknown source names requested: {unknown_sources}")
            configured = self._source_names
        if self._limit_sources is not None:
            configured = configured[: self._limit_sources]
        return configured

    @staticmethod
    def _load_run_for_business_day(
        session: Session,
        *,
        business_day: date,
    ) -> PipelineRun:
        run = session.scalar(
            select(PipelineRun).where(
                PipelineRun.business_date == business_day,
                PipelineRun.run_type == RUN_TYPE_DAILY_DIGEST,
            )
        )
        if run is None:
            raise RuntimeError(f"missing pipeline run while repairing publish failure: business_day={business_day}")
        return run

    @staticmethod
    def _source_is_retryable(state: SourceRunState) -> bool:
        if state.status == "pending":
            return True
        if state.status == "failed" and state.attempts < SOURCE_RUN_MAX_ATTEMPTS:
            return True
        return False

    @staticmethod
    def _source_name_is_retryable(source_name: str, states: Sequence[SourceRunState]) -> bool:
        for state in states:
            if state.source_name == source_name:
                return DailyRunCoordinatorService._source_is_retryable(state)
        return True

    @staticmethod
    def _mark_article_failed(
        article: Article,
        *,
        stage: str,
        observed_at: datetime,
        consume_attempt: bool,
    ) -> None:
        attempts_field = f"{stage}_attempts"
        status_field = f"{stage}_status"
        error_field = f"{stage}_error"
        updated_at_field = f"{stage}_updated_at"
        attempts = int(getattr(article, attempts_field))
        if consume_attempt:
            attempts += 1
            setattr(article, attempts_field, attempts)
        setattr(
            article,
            status_field,
            "abandoned" if attempts >= ARTICLE_STAGE_MAX_ATTEMPTS else "failed",
        )
        setattr(article, error_field, STALE_RECLAIM_ERROR)
        setattr(article, updated_at_field, observed_at)

    @staticmethod
    def _format_publish_error(exc: Exception) -> str:
        return f"{exc.__class__.__name__}: {exc}"

    @staticmethod
    def _publish_ownership_moved(status: str) -> bool:
        return status in {"running", "done", "failed", "abandoned"}

    @staticmethod
    def _article_business_day_clause(business_day: date) -> object:
        window_start, window_end = utc_bounds_for_business_day(business_day)
        return and_(Article.ingested_at >= window_start, Article.ingested_at < window_end)

    @staticmethod
    def _batch_status_counts(run: PipelineRun) -> dict[str, int]:
        return dict(sorted(Counter((run.strict_story_status, run.digest_status)).items()))

    @staticmethod
    def _batch_stage_summary(run: PipelineRun) -> dict[str, dict[str, object]]:
        return {
            "strict_story": {
                "status": run.strict_story_status,
                "attempts": run.strict_story_attempts,
                "error": run.strict_story_error,
            },
            "digest": {
                "status": run.digest_status,
                "attempts": run.digest_attempts,
                "error": run.digest_error,
            },
        }

    @staticmethod
    def _failure_summary(
        run: PipelineRun,
        *,
        source_states: Sequence[SourceRunState],
        articles: Sequence[Article],
    ) -> dict[str, object]:
        return {
            "sources": {
                state.source_name: state.error
                for state in source_states
                if state.status in {"failed", "abandoned"}
            },
            "parse": {
                article.article_id: article.parse_error
                for article in articles
                if article.parse_status in {"failed", "abandoned"}
            },
            "event_frame": {
                article.article_id: article.event_frame_error
                for article in articles
                if article.event_frame_status in {"failed", "abandoned"}
            },
            "strict_story": run.strict_story_error,
            "digest": run.digest_error,
        }
