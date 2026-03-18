"""Dev-only full pipeline: collect, parse, enrich, analyze images, build story drafts, and insert RAG units."""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

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
from backend.app.service.story_workflow_service import StoryWorkflowService


@dataclass(frozen=True)
class ImageAnalysisRunResult:
    candidates: int
    analyzed: int


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
    story_workflow_service = StoryWorkflowService()
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
    image_analysis_task = asyncio.create_task(_analyze_new_images(parsed_article_ids))
    enrichment_task = asyncio.create_task(story_workflow_service.enrich_articles(parsed_article_ids))
    enriched_count, skipped_existing_enrichment = await enrichment_task

    story_task = asyncio.create_task(story_workflow_service.build_story_drafts(parsed_article_ids))
    rag_task = asyncio.create_task(
        _insert_rag_after_image_analysis(parsed_article_ids, image_analysis_task, rag_service)
    )

    story_result, rag_result = await asyncio.gather(story_task, rag_task)
    image_analysis_result = await image_analysis_task

    print(
        "dev full pipeline completed: "
        f"inserted={collection_result.inserted} "
        f"parsed={parse_result.parsed} "
        f"parse_failed={parse_result.failed} "
        f"enriched={enriched_count} "
        f"skipped_existing_enrichment={skipped_existing_enrichment} "
        f"publishable_articles={len(story_result.publishable_records)} "
        f"analyzed_images={image_analysis_result.analyzed} "
        f"image_candidates={image_analysis_result.candidates} "
        f"stories_created={len(story_result.story_drafts)} "
        f"text_units={rag_result.text_units} "
        f"image_units={rag_result.image_units} "
        f"rag_units={rag_result.inserted_units}"
    )
    return 0


async def main() -> int:
    args = build_parser().parse_args()
    return await run(args)


async def _analyze_new_images(article_ids: list[str]) -> ImageAnalysisRunResult:
    image_analysis_service = ImageAnalysisService()
    with SessionLocal() as session:
        rows = session.execute(
            select(Article, ArticleImage)
            .join(ArticleImage, ArticleImage.article_id == Article.article_id)
            .where(
                Article.article_id.in_(article_ids),
                ArticleImage.visual_status != "done",
            )
            .order_by(
                Article.ingested_at.asc(),
                Article.article_id.asc(),
                ArticleImage.position.asc(),
                ArticleImage.image_id.asc(),
            )
        ).all()

    if not rows:
        return ImageAnalysisRunResult(candidates=0, analyzed=0)

    payloads = [
        (article.article_id, image.image_id, image_analysis_service.build_input(article=article, image=image))
        for article, image in rows
    ]
    results = await asyncio.gather(
        *(image_analysis_service.infer_payload(payload) for _, _, payload in payloads),
        return_exceptions=True,
    )

    analyzed = 0
    with SessionLocal() as session:
        for (article_id, image_id, _), result in zip(payloads, results, strict=True):
            del article_id
            stored_image = session.get(ArticleImage, image_id)
            if stored_image is None:
                continue
            if isinstance(result, Exception):
                image_analysis_service.apply_failure(image=stored_image, error=result)
            else:
                image_analysis_service.apply_result(image=stored_image, result=result)
                analyzed += 1
        session.commit()

    return ImageAnalysisRunResult(candidates=len(rows), analyzed=analyzed)


async def _insert_rag_after_image_analysis(
    article_ids: list[str],
    image_analysis_task: asyncio.Task[ImageAnalysisRunResult],
    rag_service: ArticleRagService,
) -> RagInsertResult:
    await image_analysis_task
    articles = _load_publishable_articles(article_ids)
    return await asyncio.to_thread(rag_service.insert_articles, articles)


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


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
