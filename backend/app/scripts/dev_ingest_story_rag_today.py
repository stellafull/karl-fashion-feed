"""Dev-only full pipeline: collect, parse, enrich, analyze images, build story drafts, and insert RAG units."""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

from openai import APIStatusError, APITimeoutError, RateLimitError
from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.core.database import Base, SessionLocal, engine
from backend.app.models import Article, ArticleImage, ensure_article_storage_schema
from backend.app.service.RAG.article_rag_service import ArticleRagService, RagInsertResult
from backend.app.service.article_collection_service import ArticleCollectionService
from backend.app.service.article_parse_service import ArticleParseService
from backend.app.service.image_analysis_service import ImageAnalysisService
from backend.app.service.news_collection_service import NewsCollectionService
from backend.app.service.scheduler_service import SchedulerService


@dataclass(frozen=True)
class ArticleRagRunResult:
    image_candidates: int
    analyzed_images: int
    publishable_articles: int
    text_units: int
    image_units: int
    upserted_units: int


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dev full pipeline: collect, parse, enrich, analyze images, build story drafts, and insert RAG units"
    )
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="Collect only the named source. Can be passed multiple times.",
    )
    parser.add_argument(
        "--limit-sources",
        type=int,
        default=None,
        help="Limit how many configured sources are processed.",
    )
    parser.add_argument(
        "--max-articles-per-source",
        type=int,
        default=None,
        help="Override source max article count for this run.",
    )
    parser.add_argument(
        "--max-pages-per-source",
        type=int,
        default=None,
        help="Override web source max page traversal for this run.",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=int,
        default=12,
        help="Per-request timeout when fetching RSS or web pages.",
    )
    parser.add_argument(
        "--source-concurrency",
        type=int,
        default=4,
        help="How many sources to collect concurrently.",
    )
    parser.add_argument(
        "--http-concurrency",
        type=int,
        default=16,
        help="Global concurrent HTTP request limit.",
    )
    parser.add_argument(
        "--image-analysis-concurrency",
        type=int,
        default=2,
        help="How many image-analysis workers run concurrently.",
    )
    parser.add_argument(
        "--image-analysis-retry-delay-seconds",
        type=int,
        default=15,
        help="Delay before requeueing rate-limited image-analysis tasks.",
    )
    return parser


async def run(args: argparse.Namespace) -> int:
    ensure_article_storage_schema(engine)
    Base.metadata.create_all(bind=engine)

    collector = NewsCollectionService(
        request_timeout_seconds=args.request_timeout_seconds,
        source_concurrency=args.source_concurrency,
        global_http_concurrency=args.http_concurrency,
    )
    collection_service = ArticleCollectionService(collector=collector)
    parse_service = ArticleParseService(collector=collector)
    scheduler_service = SchedulerService()
    rag_service = ArticleRagService()

    collection_result = await collection_service.collect_articles(
        source_names=args.sources,
        limit_sources=args.limit_sources,
        max_articles_per_source=args.max_articles_per_source,
        max_pages_per_source=args.max_pages_per_source,
    )
    print(
        "dev collection completed: "
        f"collected={collection_result.total_collected} "
        f"unique_candidates={collection_result.unique_candidates} "
        f"inserted={collection_result.inserted} "
        f"skipped_existing={collection_result.skipped_existing} "
        f"skipped_in_batch={collection_result.skipped_in_batch}"
    )

    if not collection_result.inserted_article_ids:
        print(
            "dev full pipeline completed: "
            f"inserted={collection_result.inserted} "
            "parsed=0 enriched=0 analyzed_images=0 stories_created=0 rag_units=0"
        )
        return 0

    parse_result = await parse_service.parse_articles(
        article_ids=list(collection_result.inserted_article_ids)
    )
    print(
        "dev parse completed: "
        f"candidates={parse_result.candidates} "
        f"parsed={parse_result.parsed} "
        f"failed={parse_result.failed}"
    )
    if not parse_result.parsed_article_ids:
        print(
            "dev full pipeline completed: "
            f"inserted={collection_result.inserted} "
            "parsed=0 enriched=0 analyzed_images=0 stories_created=0 rag_units=0"
        )
        return 0

    parsed_article_ids = list(parse_result.parsed_article_ids)
    rag_task = asyncio.create_task(
        _process_articles_for_rag(
            parsed_article_ids,
            rag_service=rag_service,
            worker_count=args.image_analysis_concurrency,
            retry_delay_seconds=args.image_analysis_retry_delay_seconds,
        )
    )
    enrichment_task = asyncio.create_task(scheduler_service.enrich_articles(parsed_article_ids))
    enriched_count, skipped_existing_enrichment = await enrichment_task

    story_task = asyncio.create_task(scheduler_service.build_story_drafts(parsed_article_ids))
    story_result, rag_result = await asyncio.gather(story_task, rag_task)

    print(
        "dev full pipeline completed: "
        f"inserted={collection_result.inserted} "
        f"parsed={parse_result.parsed} "
        f"parse_failed={parse_result.failed} "
        f"enriched={enriched_count} "
        f"skipped_existing_enrichment={skipped_existing_enrichment} "
        f"publishable_articles={rag_result.publishable_articles} "
        f"analyzed_images={rag_result.analyzed_images} "
        f"image_candidates={rag_result.image_candidates} "
        f"stories_created={len(story_result.story_drafts)} "
        f"text_units={rag_result.text_units} "
        f"image_units={rag_result.image_units} "
        f"rag_units={rag_result.upserted_units}"
    )
    return 0


async def main() -> int:
    args = build_parser().parse_args()
    return await run(args)


async def _process_articles_for_rag(
    article_ids: list[str],
    *,
    rag_service: ArticleRagService,
    worker_count: int = 2,
    retry_delay_seconds: int = 15,
) -> ArticleRagRunResult:
    if worker_count <= 0:
        raise ValueError("image_analysis_concurrency must be greater than 0")
    if retry_delay_seconds <= 0:
        raise ValueError("image_analysis_retry_delay_seconds must be greater than 0")

    ordered_article_ids = _load_ordered_article_ids(article_ids)
    if not ordered_article_ids:
        return ArticleRagRunResult(
            image_candidates=0,
            analyzed_images=0,
            publishable_articles=0,
            text_units=0,
            image_units=0,
            upserted_units=0,
        )

    queue: asyncio.Queue[str] = asyncio.Queue()
    for article_id in ordered_article_ids:
        await queue.put(article_id)

    image_analysis_service = ImageAnalysisService()
    result_lock = asyncio.Lock()
    image_candidates = 0
    analyzed_images = 0
    publishable_articles = 0
    text_units = 0
    image_units = 0
    upserted_units = 0

    async def worker() -> None:
        nonlocal image_candidates
        nonlocal analyzed_images
        nonlocal publishable_articles
        nonlocal text_units
        nonlocal image_units
        nonlocal upserted_units
        while True:
            article_id = await queue.get()
            try:
                article, images = _load_article_with_images(article_id)
                if article is None:
                    continue

                local_image_candidates = 0
                local_analyzed_images = 0
                for image in images:
                    if image.visual_status == "done":
                        continue
                    local_image_candidates += 1
                    success = await _analyze_image_with_retry(
                        article=article,
                        image=image,
                        service=image_analysis_service,
                        retry_delay_seconds=retry_delay_seconds,
                    )
                    if success:
                        local_analyzed_images += 1

                rag_result = RagInsertResult(
                    publishable_articles=0,
                    text_units=0,
                    image_units=0,
                    upserted_units=0,
                )
                publishable = _load_publishable_articles([article_id])
                if publishable:
                    rag_result = await asyncio.to_thread(rag_service.upsert_articles, publishable)
                    print(
                        "rag upsert done: "
                        f"article_id={article_id} "
                        f"upserted_units={rag_result.upserted_units}",
                        flush=True,
                    )

                async with result_lock:
                    image_candidates += local_image_candidates
                    analyzed_images += local_analyzed_images
                    publishable_articles += rag_result.publishable_articles
                    text_units += rag_result.text_units
                    image_units += rag_result.image_units
                    upserted_units += rag_result.upserted_units
            finally:
                queue.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(worker_count)]
    await queue.join()
    for task in workers:
        task.cancel()
    await asyncio.gather(*workers, return_exceptions=True)

    return ArticleRagRunResult(
        image_candidates=image_candidates,
        analyzed_images=analyzed_images,
        publishable_articles=publishable_articles,
        text_units=text_units,
        image_units=image_units,
        upserted_units=upserted_units,
    )


def _load_publishable_articles(article_ids: list[str]) -> list[Article]:
    with SessionLocal() as session:
        return session.scalars(
            select(Article)
            .where(
                Article.article_id.in_(article_ids),
                Article.should_publish.is_(True),
                Article.enrichment_status == "done",
            )
            .order_by(Article.ingested_at.asc(), Article.article_id.asc())
        ).all()


def _load_ordered_article_ids(article_ids: list[str]) -> list[str]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(Article.article_id)
                .where(Article.article_id.in_(article_ids))
                .order_by(Article.ingested_at.asc(), Article.article_id.asc())
            ).all()
        )


def _load_article_with_images(article_id: str) -> tuple[Article | None, list[ArticleImage]]:
    with SessionLocal() as session:
        article = session.get(Article, article_id)
        if article is None:
            return None, []
        images = list(
            session.scalars(
                select(ArticleImage)
                .where(ArticleImage.article_id == article_id)
                .order_by(ArticleImage.position.asc(), ArticleImage.image_id.asc())
            ).all()
        )
    return article, images


async def _analyze_image_with_retry(
    *,
    article: Article,
    image: ArticleImage,
    service: ImageAnalysisService,
    retry_delay_seconds: int,
) -> bool:
    attempt = 0
    payload = service.build_input(article=article, image=image)
    while True:
        try:
            result = await service.infer_payload(payload)
        except Exception as exc:
            if _should_retry_image_analysis(exc):
                attempt += 1
                print(
                    "image analysis retry queued: "
                    f"article_id={article.article_id} "
                    f"image_id={image.image_id} "
                    f"attempt={attempt} "
                    f"error={exc.__class__.__name__}",
                    flush=True,
                )
                await asyncio.sleep(retry_delay_seconds)
                continue

            _apply_image_failure(image.image_id, exc, service)
            print(
                "image analysis failed: "
                f"article_id={article.article_id} "
                f"image_id={image.image_id} "
                f"error={exc.__class__.__name__}",
                flush=True,
            )
            return False

        _apply_image_result(image.image_id, result, service)
        print(
            "image analysis done: "
            f"article_id={article.article_id} "
            f"image_id={image.image_id}",
            flush=True,
        )
        return True


def _should_retry_image_analysis(error: Exception) -> bool:
    if isinstance(error, (RateLimitError, APITimeoutError)):
        return True
    return isinstance(error, APIStatusError) and error.status_code in {429, 500, 502, 503, 504}


def _apply_image_result(image_id: str, result: object, service: ImageAnalysisService) -> None:
    with SessionLocal() as session:
        stored_image = session.get(ArticleImage, image_id)
        if stored_image is None:
            return
        service.apply_result(image=stored_image, result=result)
        session.commit()


def _apply_image_failure(image_id: str, error: Exception, service: ImageAnalysisService) -> None:
    with SessionLocal() as session:
        stored_image = session.get(ArticleImage, image_id)
        if stored_image is None:
            return
        service.apply_failure(image=stored_image, error=error)
        session.commit()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
