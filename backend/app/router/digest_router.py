"""Public digest read APIs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import Article, Digest, DigestArticle
from backend.app.schemas.digest_feed import (
    DigestDetailResponse,
    DigestDetailSource,
    DigestFeedItem,
    DigestFeedResponse,
)

router = APIRouter(prefix="/digests", tags=["digests"])


@router.get("/feed", response_model=DigestFeedResponse)
async def get_digest_feed(db: Session = Depends(get_db)) -> DigestFeedResponse:
    """Return public digest feed cards."""
    return build_digest_feed_response(db)


@router.get("/{digest_key}", response_model=DigestDetailResponse)
async def get_digest_detail(digest_key: str, db: Session = Depends(get_db)) -> DigestDetailResponse:
    """Return one digest detail payload."""
    return build_digest_detail_response(db, digest_key=digest_key)


def build_digest_feed_response(db: Session) -> DigestFeedResponse:
    """Build digest feed cards from persisted digests."""
    digests = list(
        db.scalars(
            select(Digest)
            .where(Digest.generation_status == "done")
            .order_by(Digest.business_date.desc(), Digest.created_at.desc(), Digest.digest_key.asc())
        ).all()
    )
    payload = [
        DigestFeedItem(
            id=digest.digest_key,
            facet=digest.facet,
            title=digest.title_zh,
            dek=digest.dek_zh,
            image=digest.hero_image_url or "",
            published=digest.business_date.isoformat(),
            article_count=digest.source_article_count,
            source_count=len(set(str(item) for item in digest.source_names_json)),
            source_names=[str(item) for item in digest.source_names_json],
        )
        for digest in digests
    ]
    return DigestFeedResponse(digests=payload)


def build_digest_detail_response(db: Session, *, digest_key: str) -> DigestDetailResponse:
    """Build one digest detail response with flattened article sources."""
    digest = db.scalar(
        select(Digest).where(
            Digest.digest_key == digest_key,
            Digest.generation_status == "done",
        )
    )
    if digest is None:
        raise HTTPException(status_code=404, detail="digest not found")

    rows = list(
        db.execute(
            select(Article)
            .join(DigestArticle, DigestArticle.article_id == Article.article_id)
            .where(DigestArticle.digest_key == digest_key)
            .order_by(DigestArticle.rank.asc(), Article.article_id.asc())
        ).scalars()
    )
    sources = [
        DigestDetailSource(
            name=article.source_name,
            title=article.title_raw,
            link=article.original_url,
            lang=article.source_lang,
        )
        for article in rows
    ]
    return DigestDetailResponse(
        id=digest.digest_key,
        facet=digest.facet,
        title=digest.title_zh,
        dek=digest.dek_zh,
        body_markdown=digest.body_markdown,
        hero_image=digest.hero_image_url or "",
        published=digest.business_date.isoformat(),
        sources=sources,
    )
