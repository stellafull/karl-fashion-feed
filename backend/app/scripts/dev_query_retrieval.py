"""Dev-only retrieval-core integration runner for deterministic RagTools scenarios."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import select

from backend.app.core.database import SessionLocal
from backend.app.models import Article, ArticleImage
from backend.app.schemas.rag_query import QueryResult
from backend.app.service.RAG.embedding_service import generate_dense_embedding
from backend.app.service.RAG.rag_tools import RagTools


def _print_heading(title: str) -> None:
    print(f"\n=== {title} ===")


def _print_packages(result: QueryResult) -> None:
    print(f"packages={len(result.packages)}")
    for package in result.packages:
        print(
            "  "
            f"article_id={package.article_id} "
            f"combined_score={package.combined_score:.4f} "
            f"text_hits={len(package.text_hits)} "
            f"image_hits={len(package.image_hits)}"
        )


def _print_citations(result: QueryResult) -> None:
    print(f"citation_locators={len(result.citation_locators)}")
    for locator in result.citation_locators:
        print(
            "  "
            f"article_id={locator.article_id} "
            f"article_image_id={locator.article_image_id} "
            f"chunk_index={locator.chunk_index} "
            f"source_name={locator.source_name}"
        )


def _print_result(name: str, result: QueryResult) -> None:
    _print_heading(name)
    print(f"query_plan={result.query_plan.model_dump_json(indent=2)}")
    print(f"text_results={len(result.text_results)}")
    print(f"image_results={len(result.image_results)}")
    _print_packages(result)
    _print_citations(result)


def _load_real_image_url() -> str:
    with SessionLocal() as session:
        image_urls = session.scalars(
            select(ArticleImage.source_url)
            .join(Article, Article.article_id == ArticleImage.article_id)
            .where(
                Article.should_publish.is_(True),
                Article.enrichment_status == "done",
                ArticleImage.visual_status == "done",
            )
            .order_by(Article.ingested_at.desc(), ArticleImage.position.asc(), ArticleImage.image_id.asc())
            .limit(20)
        )
        candidate_urls = [image_url.strip() for image_url in image_urls if isinstance(image_url, str) and image_url.strip()]
    if not candidate_urls:
        raise ValueError("no publishable analyzed image is available for image-to-image retrieval")

    for image_url in candidate_urls:
        try:
            generate_dense_embedding([""], image_urls=[image_url])
            return image_url
        except Exception:
            continue
    raise ValueError("no publishable analyzed image URL produced a valid image embedding for image-to-image retrieval")


def main() -> None:
    tools = RagTools()
    image_url = _load_real_image_url()
    scenarios: list[tuple[str, Callable[[], QueryResult]]] = [
        (
            "text_only_articles",
            lambda: tools.execute_tool(
                "search_fashion_articles",
                {
                    "query": "structured coat",
                    "limit": 3,
                },
            ),
        ),
        (
            "text_to_image",
            lambda: tools.execute_tool(
                "search_fashion_images",
                {
                    "text_query": "red coat",
                    "limit": 3,
                },
            ),
        ),
        (
            "image_to_image",
            lambda: tools.execute_tool(
                "search_fashion_images",
                {
                    "image_url": image_url,
                    "limit": 3,
                },
            ),
        ),
        (
            "fusion_with_time_filter",
            lambda: tools.execute_tool(
                "search_fashion_articles",
                {
                    "query": "couture",
                    "include_images": True,
                    "start_at": "2026-03-18T00:00:00Z",
                    "end_at": "2026-03-19T00:00:00Z",
                    "limit": 3,
                },
            ),
        ),
    ]

    for scenario_name, scenario in scenarios:
        _print_result(scenario_name, scenario())


if __name__ == "__main__":
    main()
