"""Persist text retrieval units for stored documents."""

from __future__ import annotations

from inspect import Parameter, signature
from hashlib import sha256
from pathlib import Path
import re
from typing import Callable, Sequence

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.core.database import get_session_factory
from backend.app.models import Document, RetrievalUnitRef
from backend.app.schema.retrieval import (
    RetrievalIngestionStats,
    TEXT_CHUNK_UNIT_TYPE,
    TextRetrievalUnit,
)


DEFAULT_CHUNK_SIZE = 1200
_BREAK_PATTERNS = ("\n\n", "\n", ". ", " ")

SessionFactory = Callable[[], Session]
TextChunker = Callable[[str], Sequence[str]]
LegacyReplicaWriter = Callable[[Sequence[TextRetrievalUnit]], None]
ReplicaWriter = Callable[[Sequence[TextRetrievalUnit], Sequence[str]], None]


def build_default_text_chunker(*, chunk_size: int = DEFAULT_CHUNK_SIZE) -> TextChunker:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero.")

    def chunk_text(raw_text: str) -> list[str]:
        normalized_text = _normalize_text(raw_text)
        if not normalized_text:
            return []

        if len(normalized_text) <= chunk_size:
            return [normalized_text]

        chunks: list[str] = []
        start = 0
        while start < len(normalized_text):
            end = min(start + chunk_size, len(normalized_text))
            if end >= len(normalized_text):
                chunk = normalized_text[start:].strip()
                if chunk:
                    chunks.append(chunk)
                break

            split_at = _find_split_point(normalized_text, start, end)
            chunk = normalized_text[start:split_at].strip()
            if not chunk:
                split_at = end
                chunk = normalized_text[start:split_at].strip()
            chunks.append(chunk)
            start = split_at

        return chunks

    return chunk_text


def _normalize_text(raw_text: str) -> str:
    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _compact_text(raw_text: str) -> str:
    return " ".join(str(raw_text).split())


def _find_split_point(text: str, start: int, end: int) -> int:
    search_floor = start + max((end - start) // 2, 1)
    for separator in _BREAK_PATTERNS:
        position = text.rfind(separator, search_floor, end)
        if position == -1:
            continue
        return position + len(separator)
    return end


class RetrievalIngestionService:
    def __init__(
        self,
        session_factory: SessionFactory | None = None,
        *,
        writer: ReplicaWriter | LegacyReplicaWriter | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunker: TextChunker | None = None,
    ):
        self._session_factory = session_factory or get_session_factory()
        self._writer = writer
        self._chunker = chunker or build_default_text_chunker(chunk_size=chunk_size)

    def ingest(
        self,
        *,
        article_ids: Sequence[str] | None = None,
    ) -> RetrievalIngestionStats:
        return self.ingest_documents(article_ids=article_ids)

    def ingest_documents(
        self,
        *,
        article_ids: Sequence[str] | None = None,
    ) -> RetrievalIngestionStats:
        if article_ids is not None and not article_ids:
            return RetrievalIngestionStats(
                document_count=0,
                skipped_count=0,
                chunk_count=0,
                existing_count=0,
                inserted_count=0,
            )

        with self._session_factory() as session:
            documents = self._fetch_documents(session, article_ids=article_ids)
            if not documents:
                return RetrievalIngestionStats(
                    document_count=0,
                    skipped_count=0,
                    chunk_count=0,
                    existing_count=0,
                    inserted_count=0,
                )

            existing_refs = self._fetch_existing_refs(
                session,
                article_ids=[document.article_id for document in documents],
            )

            skipped_count = 0
            chunk_count = 0
            existing_count = 0
            article_ids_to_sync = tuple(dict.fromkeys(document.article_id for document in documents))
            new_refs: list[RetrievalUnitRef] = []
            stale_refs: dict[str, RetrievalUnitRef] = {}
            units_to_write: list[TextRetrievalUnit] = []

            try:
                for document in documents:
                    article_ref_map = existing_refs.get(document.article_id, {})
                    raw_text = self._read_document_text(document)
                    if raw_text is None:
                        for stale_ref in self._stale_text_refs(article_ref_map, keep_count=0):
                            stale_refs[stale_ref.unit_id] = stale_ref
                        skipped_count += 1
                        continue

                    chunks = [
                        chunk
                        for raw_chunk in self._chunker(raw_text)
                        if (chunk := _compact_text(raw_chunk))
                    ]
                    if not chunks:
                        for stale_ref in self._stale_text_refs(article_ref_map, keep_count=0):
                            stale_refs[stale_ref.unit_id] = stale_ref
                        skipped_count += 1
                        continue

                    chunk_count += len(chunks)
                    content_version_hash = self._build_content_version_hash(document, raw_text)
                    for chunk_index, chunk_text in enumerate(chunks):
                        existing_ref = article_ref_map.get(chunk_index)
                        unit_id = (
                            existing_ref.unit_id
                            if existing_ref is not None
                            else self._build_unit_id(
                                article_id=document.article_id,
                                chunk_index=chunk_index,
                            )
                        )
                        unit = TextRetrievalUnit(
                            unit_id=unit_id,
                            article_id=document.article_id,
                            chunk_index=chunk_index,
                            text=chunk_text,
                            source_url=document.canonical_url,
                            content_version_hash=content_version_hash,
                            title=document.title,
                            source_id=document.source_id,
                        )
                        units_to_write.append(unit)

                        if existing_ref is not None:
                            existing_count += 1
                            self._refresh_existing_ref(
                                existing_ref,
                                source_url=document.canonical_url,
                                content_version_hash=content_version_hash,
                            )
                            continue

                        new_refs.append(
                            RetrievalUnitRef(
                                unit_id=unit.unit_id,
                                article_id=document.article_id,
                                unit_type=TEXT_CHUNK_UNIT_TYPE,
                                chunk_index=chunk_index,
                                source_url=document.canonical_url,
                                content_version_hash=content_version_hash,
                            )
                        )

                    for stale_ref in self._stale_text_refs(article_ref_map, keep_count=len(chunks)):
                        stale_refs[stale_ref.unit_id] = stale_ref

                for stale_ref in stale_refs.values():
                    session.delete(stale_ref)

                if new_refs:
                    session.add_all(new_refs)

                session.commit()
            except Exception:
                session.rollback()
                raise

        if self._writer:
            # Replay after SQL commit so PostgreSQL stays the source of truth.
            self._write_replica_units(
                tuple(units_to_write),
                article_ids=article_ids_to_sync,
            )

        return RetrievalIngestionStats(
            document_count=len(documents),
            skipped_count=skipped_count,
            chunk_count=chunk_count,
            existing_count=existing_count,
            inserted_count=len(new_refs),
        )

    @staticmethod
    def _fetch_documents(
        session: Session,
        *,
        article_ids: Sequence[str] | None = None,
    ) -> list[Document]:
        statement: Select[tuple[Document]] = select(Document).order_by(Document.article_id)
        if article_ids is not None:
            statement = statement.where(Document.article_id.in_(article_ids))
        return list(session.scalars(statement).all())

    @staticmethod
    def _fetch_existing_refs(
        session: Session,
        *,
        article_ids: Sequence[str],
    ) -> dict[str, dict[int, RetrievalUnitRef]]:
        if not article_ids:
            return {}

        refs = session.scalars(
            select(RetrievalUnitRef).where(
                RetrievalUnitRef.article_id.in_(article_ids),
                RetrievalUnitRef.unit_type == TEXT_CHUNK_UNIT_TYPE,
            )
        ).all()
        grouped_refs: dict[str, dict[int, RetrievalUnitRef]] = {}
        for ref in refs:
            if ref.chunk_index is None:
                continue
            grouped_refs.setdefault(ref.article_id, {})[ref.chunk_index] = ref
        return grouped_refs

    @staticmethod
    def _read_document_text(document: Document) -> str | None:
        if document.content_md_path:
            path = Path(document.content_md_path)
            if path.exists():
                text = path.read_text(encoding="utf-8")
                if text.strip():
                    return text

        body_sections = [
            str(document.source_payload.get("content_text") or "").strip(),
            str(document.source_payload.get("content_snippet") or "").strip(),
            (document.summary_zh or "").strip(),
        ]
        fallback_sections = [section for section in body_sections if section]
        if fallback_sections and document.title.strip():
            fallback_sections.insert(0, document.title.strip())
        fallback_text = "\n\n".join(fallback_sections)
        return fallback_text or None

    @staticmethod
    def _build_unit_id(*, article_id: str, chunk_index: int) -> str:
        raw_identifier = f"{article_id}:{TEXT_CHUNK_UNIT_TYPE}:{chunk_index}"
        return sha256(raw_identifier.encode("utf-8")).hexdigest()

    @staticmethod
    def _build_content_version_hash(document: Document, raw_text: str) -> str:
        if document.content_hash and document.content_hash.strip():
            return document.content_hash.strip()
        return sha256(_normalize_text(raw_text).encode("utf-8")).hexdigest()

    @staticmethod
    def _refresh_existing_ref(
        ref: RetrievalUnitRef,
        *,
        source_url: str,
        content_version_hash: str,
    ) -> None:
        ref.source_url = source_url
        ref.content_version_hash = content_version_hash

    @staticmethod
    def _stale_text_refs(
        article_ref_map: dict[int, RetrievalUnitRef],
        *,
        keep_count: int,
    ) -> list[RetrievalUnitRef]:
        return [
            ref
            for chunk_index, ref in article_ref_map.items()
            if chunk_index >= keep_count
        ]

    def _write_replica_units(
        self,
        units: Sequence[TextRetrievalUnit],
        *,
        article_ids: Sequence[str],
    ) -> None:
        if self._writer is None:
            return

        writer_mode = self._resolve_writer_mode(self._writer)
        if writer_mode == "positional_article_ids":
            self._writer(units, article_ids)
            return

        if writer_mode == "keyword_article_ids":
            self._writer(units, article_ids=article_ids)
            return

        self._writer(units)

    @staticmethod
    def _resolve_writer_mode(
        writer: ReplicaWriter | LegacyReplicaWriter,
    ) -> str:
        writer_signature = getattr(writer, "_spec_signature", None)

        if writer_signature is None:
            wrapped_writer = getattr(writer, "_mock_wraps", None)
            if callable(wrapped_writer):
                writer = wrapped_writer
            else:
                side_effect = getattr(writer, "side_effect", None)
                if callable(side_effect):
                    writer = side_effect

            try:
                writer_signature = signature(writer)
            except (TypeError, ValueError):
                return "positional_article_ids"

        parameters = tuple(writer_signature.parameters.values())
        if any(parameter.kind == Parameter.VAR_POSITIONAL for parameter in parameters):
            return "positional_article_ids"

        positional_count = 0
        keyword_only_article_ids = False
        accepts_kwargs = False
        for parameter in parameters:
            if parameter.kind in (
                Parameter.POSITIONAL_ONLY,
                Parameter.POSITIONAL_OR_KEYWORD,
            ):
                positional_count += 1
                continue
            if parameter.kind == Parameter.KEYWORD_ONLY and parameter.name == "article_ids":
                keyword_only_article_ids = True
                continue
            if parameter.kind == Parameter.VAR_KEYWORD:
                accepts_kwargs = True

        if positional_count >= 2:
            return "positional_article_ids"
        if keyword_only_article_ids or accepts_kwargs:
            return "keyword_article_ids"
        return "legacy"
