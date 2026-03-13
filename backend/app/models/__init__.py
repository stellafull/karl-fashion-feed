"""ORM model registry."""

from backend.app.models.article import Article, ArticleImage, ensure_article_storage_schema

__all__ = ["Article", "ArticleImage", "ensure_article_storage_schema"]
