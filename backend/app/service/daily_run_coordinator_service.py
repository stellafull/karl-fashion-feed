"""Daily control-plane coordinator for runtime rescans and batch triggering."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, or_, select
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


class DailyRunCoordinatorService:
    """Drive the current business-day run by rescanning state and enqueueing work."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        stale_after: timedelta = DEFAULT_STALE_STATE_TIMEOUT,
    ) -> None:
        self._session_factory = session_factory
        self._stale_after = stale_after

    def tick(self, *, now: datetime | None = None) -> str:
        """Rescan runtime state, enqueue retryable work, and trigger batch jobs."""
        observed_at = coerce_utc_naive(now or datetime.now(UTC))
        business_day = business_day_for_runtime(observed_at)
        source_names = self._enabled_source_names()
        dispatches: list[Dispatch] = []

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
            self._refresh_run_metadata(session, run, business_day, source_names)
            self._enqueue_batch_jobs_if_ready(
                session,
                run,
                source_names,
                observed_at,
                dispatches=dispatches,
            )
            self._refresh_run_status(run, observed_at)
            session.commit()
            run_id = run.run_id

        for dispatch in dispatches:
            dispatch()

        return run_id

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
            digest_updated_at=observed_at,
            metadata_json={},
        )
        session.add(run)
        session.flush()
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
        dispatches: list[Dispatch],
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
            dispatches.append(lambda source_name=source_name, run_id=run_id: collect_source.delay(source_name, run_id))

    def _enqueue_retryable_articles(
        self,
        session: Session,
        business_day: date,
        *,
        stage: str,
        observed_at: datetime,
        dispatches: list[Dispatch],
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
            dispatches.append(lambda article_id=article.article_id, task=task: task.delay(article_id))

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
        }

    def _enqueue_batch_jobs_if_ready(
        self,
        session: Session,
        run: PipelineRun,
        source_names: Sequence[str],
        observed_at: datetime,
        *,
        dispatches: list[Dispatch],
    ) -> None:
        if self._front_stages_drained(session, run.run_id, run.business_date, source_names):
            self._enqueue_unique_pack(session, run, observed_at, dispatches=dispatches)
        if run.strict_story_status == "done" and run.digest_status in {"pending", "failed"}:
            self._enqueue_unique_digest(session, run, observed_at, dispatches=dispatches)

    def _enqueue_unique_pack(
        self,
        session: Session,
        run: PipelineRun,
        observed_at: datetime,
        *,
        dispatches: list[Dispatch],
    ) -> None:
        if run.strict_story_status not in RETRYABLE_STATUSES:
            return
        if run.strict_story_attempts >= BATCH_STAGE_MAX_ATTEMPTS:
            run.strict_story_status = "abandoned"
            return

        run.strict_story_status = "queued"
        run.strict_story_updated_at = observed_at
        session.flush()
        dispatches.append(
            lambda business_day_iso=run.business_date.isoformat(), run_id=run.run_id: pack_strict_stories_for_day.delay(
                business_day_iso,
                run_id,
            )
        )

    def _enqueue_unique_digest(
        self,
        session: Session,
        run: PipelineRun,
        observed_at: datetime,
        *,
        dispatches: list[Dispatch],
    ) -> None:
        if run.digest_attempts >= BATCH_STAGE_MAX_ATTEMPTS:
            run.digest_status = "abandoned"
            return

        run.digest_status = "queued"
        run.digest_updated_at = observed_at
        session.flush()
        dispatches.append(
            lambda business_day_iso=run.business_date.isoformat(), run_id=run.run_id: generate_digests_for_day.delay(
                business_day_iso,
                run_id,
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
            run.finished_at = observed_at
            return
        if run.strict_story_status == "abandoned" or run.digest_status == "abandoned":
            run.status = "failed"
            run.finished_at = observed_at
            return
        run.status = "running"
        run.finished_at = None

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

    @staticmethod
    def _enabled_source_names() -> tuple[str, ...]:
        return tuple(source.name for source in load_source_configs())

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
    def _article_business_day_clause(business_day: date) -> object:
        window_start, window_end = utc_bounds_for_business_day(business_day)
        return and_(Article.ingested_at >= window_start, Article.ingested_at < window_end)
