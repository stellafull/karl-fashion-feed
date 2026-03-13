"""Backfill canonical markdown files and image assets for legacy article rows."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.core.database import SessionLocal, engine
from backend.app.models import Article, ArticleImage, ensure_article_storage_schema
from backend.app.service.article_markdown_service import ArticleMarkdownService
from backend.app.service.article_contracts import MarkdownBlock


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill markdown files and image assets")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit how many legacy article rows are processed",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    ensure_article_storage_schema(engine)

    markdown_service = ArticleMarkdownService()
    with SessionLocal() as session:
        articles_query = (
            select(Article)
            .where((Article.markdown_rel_path.is_(None)) | (Article.markdown_rel_path == ""))
            .order_by(Article.ingested_at.asc())
        )
        if args.limit is not None:
            articles_query = articles_query.limit(args.limit)

        articles = session.scalars(articles_query).all()
        written_paths: list[Path] = []
        try:
            for article in articles:
                image_rows = session.scalars(
                    select(ArticleImage).where(ArticleImage.article_id == article.article_id)
                ).all()

                if not image_rows and article.image_url:
                    hero_image = ArticleImage(
                        image_id=str(uuid4()),
                        article_id=article.article_id,
                        source_url=article.image_url,
                        normalized_url=article.image_url,
                        role="hero",
                        source_kind="legacy",
                        source_selector="legacy:image_url",
                        visual_status="pending",
                    )
                    session.add(hero_image)
                    image_rows = [hero_image]

                hero_image_id = article.hero_image_id or (image_rows[0].image_id if image_rows else None)
                blocks = _legacy_blocks(article, hero_image_id)
                image_id_map = {0: hero_image_id} if hero_image_id else {}
                markdown = markdown_service.render_canonical_markdown(
                    title=article.title_raw,
                    summary=article.summary_raw,
                    blocks=blocks,
                    image_ids_by_index=image_id_map,
                )
                relative_path = markdown_service.build_relative_path(
                    article_id=article.article_id,
                    reference_time=article.published_at or article.discovered_at,
                )
                written_paths.append(
                    markdown_service.write_markdown(
                        relative_path=relative_path,
                        content=markdown,
                    )
                )
                article.markdown_rel_path = relative_path
                article.hero_image_id = hero_image_id

            session.commit()
            print(f"backfilled {len(articles)} articles")
            return 0
        except Exception:
            session.rollback()
            for path in written_paths:
                if path.exists():
                    path.unlink()
            raise


def _legacy_blocks(article: Article, hero_image_id: str | None) -> list[MarkdownBlock]:
    blocks: list[MarkdownBlock] = []
    if hero_image_id:
        blocks.append(MarkdownBlock(kind="image", image_index=0))

    legacy_text = (article.content_raw or "").strip() or (article.summary_raw or "").strip()
    if not legacy_text:
        legacy_text = article.title_raw

    paragraphs = [segment.strip() for segment in legacy_text.splitlines() if segment.strip()]
    if not paragraphs:
        paragraphs = [legacy_text]

    blocks.extend(MarkdownBlock(kind="paragraph", text=paragraph) for paragraph in paragraphs)
    return blocks


if __name__ == "__main__":
    raise SystemExit(main())
