import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest import mock

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app.core.database import Base, create_engine_from_url
from backend.app.models import Document, RetrievalUnitRef
from backend.app.schema.retrieval import TEXT_CHUNK_UNIT_TYPE
from backend.app.service.milvus_service import build_text_retrieval_replica_writer
from backend.app.service.retrieval_ingestion_service import (
    RetrievalIngestionService,
)


class RetrievalIngestionServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine_from_url("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
            future=True,
        )
        self.storage_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.storage_dir.cleanup()

    @staticmethod
    def _capture_writer_call(
        storage: list[dict[str, tuple]],
    ):
        def writer(units, article_ids) -> None:
            storage.append(
                {
                    "units": tuple(units),
                    "article_ids": tuple(article_ids),
                }
            )

        return writer

    def test_ingest_documents_reads_markdown_and_persists_text_chunk_refs(self):
        markdown_path = self._write_markdown(
            "article_001.md",
            "\n\n".join(
                [
                    "# Retrieval Title",
                    "First paragraph with enough text to force chunk splitting on a small chunk size.",
                    "Second paragraph keeps the chunking deterministic across repeated ingestion runs.",
                    "Third paragraph closes the document with additional retrieval context.",
                ]
            ),
        )
        self._insert_document(
            article_id="article_001",
            content_md_path=str(markdown_path),
            content_hash="doc-hash-001",
        )

        written_units: list[tuple] = []
        service = RetrievalIngestionService(
            session_factory=self.session_factory,
            writer=self._capture_writer_call(written_units),
            chunk_size=90,
        )

        stats = service.ingest_documents()

        with self.session_factory() as session:
            stored_refs = session.scalars(
                select(RetrievalUnitRef).order_by(RetrievalUnitRef.chunk_index)
            ).all()

        self.assertEqual(stats.document_count, 1)
        self.assertEqual(stats.skipped_count, 0)
        self.assertEqual(stats.existing_count, 0)
        self.assertEqual(stats.chunk_count, len(stored_refs))
        self.assertEqual(stats.inserted_count, len(stored_refs))
        self.assertGreater(len(stored_refs), 1)
        self.assertEqual(len(written_units), 1)
        self.assertEqual(len(written_units[0]["units"]), len(stored_refs))
        self.assertEqual(written_units[0]["article_ids"], ("article_001",))
        self.assertEqual(
            [ref.chunk_index for ref in stored_refs],
            list(range(len(stored_refs))),
        )
        self.assertTrue(all(ref.unit_type == TEXT_CHUNK_UNIT_TYPE for ref in stored_refs))
        self.assertTrue(all(ref.source_url == "https://example.com/article_001" for ref in stored_refs))
        self.assertTrue(all(ref.content_version_hash == "doc-hash-001" for ref in stored_refs))
        self.assertEqual(
            [unit.unit_id for unit in written_units[0]["units"]],
            [ref.unit_id for ref in stored_refs],
        )

    def test_ingest_documents_is_idempotent_for_existing_chunk_indexes(self):
        markdown_path = self._write_markdown(
            "article_002.md",
            "Paragraph one.\n\nParagraph two has enough content to produce more than one chunk when needed.",
        )
        self._insert_document(article_id="article_002", content_md_path=str(markdown_path))

        write_calls: list[tuple] = []
        service = RetrievalIngestionService(
            session_factory=self.session_factory,
            writer=self._capture_writer_call(write_calls),
            chunk_size=50,
        )

        first_run = service.ingest_documents()
        second_run = service.ingest_documents()

        with self.session_factory() as session:
            stored_count = session.scalar(select(func.count()).select_from(RetrievalUnitRef))

        self.assertEqual(first_run.inserted_count, first_run.chunk_count)
        self.assertEqual(second_run.inserted_count, 0)
        self.assertEqual(second_run.existing_count, first_run.chunk_count)
        self.assertEqual(stored_count, first_run.chunk_count)
        self.assertEqual(len(write_calls), 2)
        self.assertEqual(len(write_calls[0]["units"]), first_run.chunk_count)
        self.assertEqual(len(write_calls[1]["units"]), second_run.chunk_count)
        self.assertEqual(write_calls[1]["article_ids"], ("article_002",))

    def test_ingest_documents_supports_legacy_single_argument_writers(self):
        markdown_path = self._write_markdown(
            "article_002_legacy_writer.md",
            "Legacy writer compatibility should remain stable for injected callbacks.",
        )
        self._insert_document(
            article_id="article_002_legacy_writer",
            content_md_path=str(markdown_path),
        )

        write_calls: list[tuple] = []
        service = RetrievalIngestionService(
            session_factory=self.session_factory,
            writer=lambda units: write_calls.append(tuple(units)),
            chunk_size=200,
        )

        stats = service.ingest_documents(article_ids=["article_002_legacy_writer"])

        self.assertEqual(stats.document_count, 1)
        self.assertEqual(stats.chunk_count, 1)
        self.assertEqual(len(write_calls), 1)
        self.assertEqual(len(write_calls[0]), 1)

    def test_ingest_documents_refreshes_existing_refs_from_current_markdown(self):
        markdown_path = self._write_markdown(
            "article_002_refresh.md",
            "Original retrieval body that produces exactly one chunk.",
        )
        self._insert_document(article_id="article_002_refresh", content_md_path=str(markdown_path))

        service = RetrievalIngestionService(
            session_factory=self.session_factory,
            chunk_size=200,
        )
        first_run = service.ingest_documents()

        refreshed_body = "Updated retrieval body that should be replayed into the replica."
        markdown_path.write_text(refreshed_body, encoding="utf-8")
        write_calls: list[tuple] = []
        replay_service = RetrievalIngestionService(
            session_factory=self.session_factory,
            writer=self._capture_writer_call(write_calls),
            chunk_size=200,
        )

        second_run = replay_service.ingest_documents(article_ids=["article_002_refresh"])

        with self.session_factory() as session:
            stored_ref = session.scalar(
                select(RetrievalUnitRef).where(RetrievalUnitRef.article_id == "article_002_refresh")
            )

        self.assertEqual(first_run.inserted_count, 1)
        self.assertEqual(second_run.inserted_count, 0)
        self.assertEqual(second_run.existing_count, 1)
        self.assertIsNotNone(stored_ref)
        self.assertEqual(stored_ref.content_version_hash, sha256(refreshed_body.encode("utf-8")).hexdigest())
        self.assertEqual(len(write_calls), 1)
        self.assertEqual(len(write_calls[0]["units"]), 1)
        self.assertEqual(write_calls[0]["article_ids"], ("article_002_refresh",))
        self.assertEqual(write_calls[0]["units"][0].text, refreshed_body)
        self.assertEqual(
            write_calls[0]["units"][0].content_version_hash,
            stored_ref.content_version_hash,
        )

    def test_ingest_documents_recomputes_current_chunks_and_replays_all_current_units(self):
        markdown_path = self._write_markdown("article_007.md", "initial truth")
        self._insert_document(article_id="article_007", content_md_path=str(markdown_path))

        observed_inputs: list[str] = []
        write_calls: list[tuple] = []

        def versioned_chunker(raw_text: str) -> list[str]:
            observed_inputs.append(raw_text)
            if raw_text == "expanded truth":
                return ["expanded chunk 0", "expanded chunk 1", "expanded chunk 2"]
            return ["initial chunk 0", "initial chunk 1"]

        service = RetrievalIngestionService(
            session_factory=self.session_factory,
            writer=self._capture_writer_call(write_calls),
            chunker=versioned_chunker,
        )

        first_run = service.ingest_documents(article_ids=["article_007"])

        with self.session_factory() as session:
            first_refs = session.scalars(
                select(RetrievalUnitRef)
                .where(RetrievalUnitRef.article_id == "article_007")
                .order_by(RetrievalUnitRef.chunk_index)
            ).all()

        markdown_path.write_text("expanded truth", encoding="utf-8")
        second_run = service.ingest_documents(article_ids=["article_007"])

        with self.session_factory() as session:
            second_refs = session.scalars(
                select(RetrievalUnitRef)
                .where(RetrievalUnitRef.article_id == "article_007")
                .order_by(RetrievalUnitRef.chunk_index)
            ).all()

        self.assertEqual(observed_inputs, ["initial truth", "expanded truth"])
        self.assertEqual(first_run.chunk_count, 2)
        self.assertEqual(first_run.inserted_count, 2)
        self.assertEqual(second_run.document_count, 1)
        self.assertEqual(second_run.chunk_count, 3)
        self.assertEqual(second_run.existing_count, 2)
        self.assertEqual(second_run.inserted_count, 1)
        self.assertEqual([ref.chunk_index for ref in second_refs], [0, 1, 2])
        self.assertEqual(len(write_calls), 2)
        self.assertEqual(
            [unit.text for unit in write_calls[0]["units"]],
            ["initial chunk 0", "initial chunk 1"],
        )
        self.assertEqual(
            [unit.text for unit in write_calls[1]["units"]],
            ["expanded chunk 0", "expanded chunk 1", "expanded chunk 2"],
        )
        self.assertEqual(write_calls[1]["article_ids"], ("article_007",))
        self.assertEqual(
            [unit.unit_id for unit in write_calls[1]["units"]],
            [ref.unit_id for ref in second_refs],
        )
        self.assertNotEqual(
            {ref.content_version_hash for ref in first_refs},
            {ref.content_version_hash for ref in second_refs},
        )

    def test_ingest_documents_removes_stale_trailing_refs_when_document_shrinks(self):
        markdown_path = self._write_markdown("article_008.md", "initial truth")
        self._insert_document(article_id="article_008", content_md_path=str(markdown_path))

        write_calls: list[dict[str, tuple]] = []

        def shrinking_chunker(raw_text: str) -> list[str]:
            if raw_text == "initial truth":
                return ["initial chunk 0", "initial chunk 1", "initial chunk 2"]
            return ["shrunk chunk 0"]

        service = RetrievalIngestionService(
            session_factory=self.session_factory,
            writer=self._capture_writer_call(write_calls),
            chunker=shrinking_chunker,
        )

        first_run = service.ingest_documents(article_ids=["article_008"])
        markdown_path.write_text("shrunk truth", encoding="utf-8")
        second_run = service.ingest_documents(article_ids=["article_008"])

        with self.session_factory() as session:
            stored_refs = session.scalars(
                select(RetrievalUnitRef)
                .where(RetrievalUnitRef.article_id == "article_008")
                .order_by(RetrievalUnitRef.chunk_index)
            ).all()

        self.assertEqual(first_run.chunk_count, 3)
        self.assertEqual(second_run.chunk_count, 1)
        self.assertEqual(second_run.existing_count, 1)
        self.assertEqual(second_run.inserted_count, 0)
        self.assertEqual([ref.chunk_index for ref in stored_refs], [0])
        self.assertEqual(len(stored_refs), 1)
        self.assertEqual(write_calls[1]["article_ids"], ("article_008",))
        self.assertEqual(
            [unit.text for unit in write_calls[1]["units"]],
            ["shrunk chunk 0"],
        )

    def test_ingest_documents_removes_stale_trailing_refs_and_deactivates_replica_units(self):
        class FakeReplicaGateway:
            def __init__(self):
                self.records_by_article: dict[str, dict[str, dict]] = {}
                self.query_calls: list[dict[str, object]] = []
                self.upsert_calls: list[list[dict]] = []

            def upsert_records(self, *, collection_name, records):
                self.upsert_calls.append([dict(record) for record in records])
                for record in records:
                    article_records = self.records_by_article.setdefault(record["article_id"], {})
                    article_records[record["unit_id"]] = dict(record)

            def query_records(self, *, collection_name, filter_expression=None, output_fields=None):
                self.query_calls.append(
                    {
                        "collection_name": collection_name,
                        "filter_expression": filter_expression,
                        "output_fields": tuple(output_fields or ()),
                    }
                )
                if not filter_expression:
                    return []
                article_id = filter_expression.split('article_id == "', 1)[1].rsplit('"', 1)[0]
                return [
                    dict(record)
                    for record in self.records_by_article.get(article_id, {}).values()
                    if record.get("is_active", True)
                ]

        markdown_path = self._write_markdown("article_008_replica.md", "initial truth")
        self._insert_document(article_id="article_008_replica", content_md_path=str(markdown_path))

        def shrinking_chunker(raw_text: str) -> list[str]:
            if raw_text == "initial truth":
                return ["initial chunk 0", "initial chunk 1", "initial chunk 2"]
            return ["shrunk chunk 0"]

        gateway = FakeReplicaGateway()
        service = RetrievalIngestionService(
            session_factory=self.session_factory,
            writer=build_text_retrieval_replica_writer(gateway),
            chunker=shrinking_chunker,
        )

        first_run = service.ingest_documents(article_ids=["article_008_replica"])
        markdown_path.write_text("shrunk truth", encoding="utf-8")
        second_run = service.ingest_documents(article_ids=["article_008_replica"])

        with self.session_factory() as session:
            stored_refs = session.scalars(
                select(RetrievalUnitRef)
                .where(RetrievalUnitRef.article_id == "article_008_replica")
                .order_by(RetrievalUnitRef.chunk_index)
            ).all()

        active_replica_records = sorted(
            (record["chunk_index"], record["is_active"])
            for record in gateway.records_by_article["article_008_replica"].values()
            if record.get("is_active", True)
        )
        all_replica_records = sorted(
            (record["chunk_index"], record["is_active"])
            for record in gateway.records_by_article["article_008_replica"].values()
        )

        self.assertEqual(first_run.chunk_count, 3)
        self.assertEqual(second_run.chunk_count, 1)
        self.assertEqual([ref.chunk_index for ref in stored_refs], [0])
        self.assertEqual(active_replica_records, [(0, True)])
        self.assertEqual(all_replica_records, [(0, True), (1, False), (2, False)])
        self.assertEqual(len(gateway.query_calls), 2)

    def test_ingest_documents_deactivates_replica_units_when_document_now_has_no_chunks(self):
        class FakeReplicaGateway:
            def __init__(self):
                self.records_by_article: dict[str, dict[str, dict]] = {}
                self.upsert_calls: list[list[dict]] = []

            def upsert_records(self, *, collection_name, records):
                self.upsert_calls.append([dict(record) for record in records])
                for record in records:
                    article_records = self.records_by_article.setdefault(record["article_id"], {})
                    article_records[record["unit_id"]] = dict(record)

            def query_records(self, *, collection_name, filter_expression=None, output_fields=None):
                if not filter_expression:
                    return []
                article_id = filter_expression.split('article_id == "', 1)[1].rsplit('"', 1)[0]
                return [
                    dict(record)
                    for record in self.records_by_article.get(article_id, {}).values()
                    if record.get("is_active", True)
                ]

        markdown_path = self._write_markdown("article_008_zero.md", "initial truth")
        self._insert_document(article_id="article_008_zero", content_md_path=str(markdown_path))

        def shrinking_to_zero_chunker(raw_text: str) -> list[str]:
            if raw_text == "initial truth":
                return ["initial chunk 0", "initial chunk 1", "initial chunk 2"]
            return []

        gateway = FakeReplicaGateway()
        service = RetrievalIngestionService(
            session_factory=self.session_factory,
            writer=build_text_retrieval_replica_writer(gateway),
            chunker=shrinking_to_zero_chunker,
        )

        first_run = service.ingest_documents(article_ids=["article_008_zero"])
        markdown_path.write_text("shrunk truth", encoding="utf-8")
        second_run = service.ingest_documents(article_ids=["article_008_zero"])

        with self.session_factory() as session:
            stored_refs = session.scalars(
                select(RetrievalUnitRef)
                .where(RetrievalUnitRef.article_id == "article_008_zero")
                .order_by(RetrievalUnitRef.chunk_index)
            ).all()

        active_replica_records = [
            record
            for record in gateway.records_by_article["article_008_zero"].values()
            if record.get("is_active", True)
        ]
        all_replica_records = sorted(
            (record["chunk_index"], record["is_active"])
            for record in gateway.records_by_article["article_008_zero"].values()
        )

        self.assertEqual(first_run.chunk_count, 3)
        self.assertEqual(second_run.chunk_count, 0)
        self.assertEqual(second_run.skipped_count, 1)
        self.assertEqual(stored_refs, [])
        self.assertEqual(active_replica_records, [])
        self.assertEqual(all_replica_records, [(0, False), (1, False), (2, False)])

    def test_ingest_documents_uses_injected_chunker(self):
        markdown_path = self._write_markdown(
            "article_003.md",
            "\n\n".join(
                [
                    "# Custom Chunking Title",
                    "Alpha paragraph that would split into many pieces with the default chunker.",
                    "Beta paragraph that proves the ingestion flow can delegate chunking.",
                    "Gamma paragraph that keeps the input text long enough to ignore chunk_size.",
                ]
            ),
        )
        self._insert_document(article_id="article_003", content_md_path=str(markdown_path))

        observed_inputs: list[str] = []
        written_units: list[tuple] = []

        def custom_chunker(raw_text: str) -> list[str]:
            observed_inputs.append(raw_text)
            return ["  custom\nchunk one  ", "custom   chunk two"]

        service = RetrievalIngestionService(
            session_factory=self.session_factory,
            writer=self._capture_writer_call(written_units),
            chunk_size=10,
            chunker=custom_chunker,
        )

        stats = service.ingest_documents(article_ids=["article_003"])

        with self.session_factory() as session:
            stored_refs = session.scalars(
                select(RetrievalUnitRef).where(RetrievalUnitRef.article_id == "article_003").order_by(
                    RetrievalUnitRef.chunk_index
                )
            ).all()

        self.assertEqual(observed_inputs, [markdown_path.read_text(encoding="utf-8")])
        self.assertEqual(stats.document_count, 1)
        self.assertEqual(stats.skipped_count, 0)
        self.assertEqual(stats.chunk_count, 2)
        self.assertEqual(stats.inserted_count, 2)
        self.assertEqual([ref.chunk_index for ref in stored_refs], [0, 1])
        self.assertEqual(len(written_units), 1)
        self.assertEqual(written_units[0]["article_ids"], ("article_003",))
        self.assertEqual(
            [unit.text for unit in written_units[0]["units"]],
            ["custom chunk one", "custom chunk two"],
        )

    def test_ingest_documents_uses_payload_fallback_and_skips_empty_documents(self):
        self._insert_document(
            article_id="article_004",
            source_payload={"content_snippet": "Persisted fallback snippet for retrieval."},
            summary_zh="摘要内容",
        )
        self._insert_document(article_id="article_005")

        service = RetrievalIngestionService(
            session_factory=self.session_factory,
            chunk_size=200,
        )

        stats = service.ingest_documents()

        with self.session_factory() as session:
            stored_refs = session.scalars(
                select(RetrievalUnitRef).order_by(RetrievalUnitRef.article_id, RetrievalUnitRef.chunk_index)
            ).all()

        self.assertEqual(stats.document_count, 2)
        self.assertEqual(stats.skipped_count, 1)
        self.assertEqual(stats.chunk_count, 1)
        self.assertEqual(stats.inserted_count, 1)
        self.assertEqual(len(stored_refs), 1)
        self.assertEqual(stored_refs[0].article_id, "article_004")

    def test_ingest_documents_supports_legacy_single_argument_mock_writers(self):
        markdown_path = self._write_markdown(
            "article_legacy_writer.md",
            "Legacy writer contract should still receive a single positional argument.",
        )
        self._insert_document(
            article_id="article_legacy_writer",
            content_md_path=str(markdown_path),
        )

        captured_units: list[tuple] = []
        writer = mock.Mock(
            side_effect=lambda units: captured_units.append(tuple(units)),
        )
        service = RetrievalIngestionService(
            session_factory=self.session_factory,
            writer=writer,
            chunk_size=200,
        )

        stats = service.ingest_documents(article_ids=["article_legacy_writer"])

        with self.session_factory() as session:
            stored_refs = session.scalars(
                select(RetrievalUnitRef)
                .where(RetrievalUnitRef.article_id == "article_legacy_writer")
                .order_by(RetrievalUnitRef.chunk_index)
            ).all()

        self.assertEqual(stats.document_count, 1)
        self.assertEqual(stats.chunk_count, 1)
        writer.assert_called_once()
        args, kwargs = writer.call_args
        self.assertEqual(kwargs, {})
        self.assertEqual(len(args), 1)
        self.assertEqual(len(captured_units), 1)
        self.assertEqual(
            [unit.unit_id for unit in args[0]],
            [ref.unit_id for ref in stored_refs],
        )
        self.assertEqual(captured_units[0], tuple(args[0]))

    def test_writer_failure_does_not_roll_back_committed_retrieval_unit_refs(self):
        markdown_path = self._write_markdown(
            "article_006.md",
            "Retrieval content that should not persist when the injected writer fails.",
        )
        self._insert_document(article_id="article_006", content_md_path=str(markdown_path))

        observed_ref_counts: list[int] = []

        def failing_writer(_units: tuple) -> None:
            with self.session_factory() as session:
                observed_ref_counts.append(
                    session.scalar(select(func.count()).select_from(RetrievalUnitRef))
                )
            raise RuntimeError("writer failed")

        service = RetrievalIngestionService(
            session_factory=self.session_factory,
            writer=failing_writer,
            chunk_size=200,
        )

        with self.assertRaisesRegex(RuntimeError, "writer failed"):
            service.ingest_documents()

        with self.session_factory() as session:
            stored_refs = session.scalars(select(RetrievalUnitRef)).all()

        self.assertEqual(observed_ref_counts, [1])
        self.assertEqual(len(stored_refs), 1)
        self.assertEqual(stored_refs[0].article_id, "article_006")

    def _insert_document(
        self,
        *,
        article_id: str,
        content_md_path: str | None = None,
        content_hash: str | None = None,
        source_payload: dict | None = None,
        summary_zh: str | None = None,
    ) -> None:
        with self.session_factory() as session:
            session.add(
                Document(
                    article_id=article_id,
                    source_id="source-001",
                    canonical_url=f"https://example.com/{article_id}",
                    title=f"Title for {article_id}",
                    content_md_path=content_md_path,
                    content_hash=content_hash,
                    summary_zh=summary_zh,
                    source_payload=source_payload or {},
                )
            )
            session.commit()

    def _write_markdown(self, file_name: str, content: str) -> Path:
        path = Path(self.storage_dir.name) / file_name
        path.write_text(content, encoding="utf-8")
        return path
