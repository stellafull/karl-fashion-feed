"""Canonical markdown storage and materialization helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from backend.app.config.storage_config import ARTICLE_MARKDOWN_ROOT
from backend.app.service.article_contracts import MarkdownBlock


class ArticleMarkdownService:
    def __init__(self, root_path: Path | None = None) -> None:
        self.root_path = Path(root_path or ARTICLE_MARKDOWN_ROOT)

    def build_relative_path(
        self,
        *,
        article_id: str,
        reference_time: datetime | None,
    ) -> str:
        dt = reference_time or datetime.now(UTC).replace(tzinfo=None)
        return str(Path(dt.date().isoformat()) / f"{article_id}.md")

    def render_canonical_markdown(
        self,
        *,
        title: str,
        summary: str,
        blocks: Iterable[MarkdownBlock],
    ) -> str:
        lines: list[str] = [f"# {title.strip()}"]
        if summary.strip():
            lines.extend(["", summary.strip()])

        for block in blocks:
            lines.append("")
            if block.kind == "heading":
                lines.append(f"## {block.text.strip()}")
            elif block.kind == "paragraph":
                lines.append(block.text.strip())
            elif block.kind == "list_item":
                lines.append(f"- {block.text.strip()}")
            elif block.kind == "blockquote":
                lines.append(f"> {block.text.strip()}")

        return "\n".join(line for line in lines if line is not None).strip() + "\n"

    def write_markdown(self, *, relative_path: str, content: str) -> Path:
        absolute_path = self.root_path / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_text(content, encoding="utf-8")
        return absolute_path

    def read_markdown(self, *, relative_path: str) -> str:
        return (self.root_path / relative_path).read_text(encoding="utf-8")
