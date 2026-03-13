import sys
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend import main as backend_main
from backend.app.service.document_ingestion_service import DocumentIngestionStats


class BackendCliTests(unittest.TestCase):
    def test_init_db_command_creates_tables(self):
        with mock.patch("backend.app.core.database.create_all_tables") as create_all_tables:
            with mock.patch("builtins.print") as print_mock:
                exit_code = backend_main.main(["init-db"])

        self.assertEqual(exit_code, 0)
        create_all_tables.assert_called_once_with()
        print_mock.assert_called_once_with("Database tables created.")

    def test_ingest_documents_command_runs_service(self):
        mock_stats = DocumentIngestionStats(collected_count=6, existing_count=2, inserted_count=4)

        with mock.patch(
            "backend.app.service.document_ingestion_service.DocumentIngestionService"
        ) as service_cls:
            service_cls.return_value.collect_and_ingest.return_value = mock_stats
            with mock.patch("builtins.print") as print_mock:
                exit_code = backend_main.main(["ingest-documents", "--sources-file", "custom.yaml"])

        self.assertEqual(exit_code, 0)
        service_cls.assert_called_once_with()
        service_cls.return_value.collect_and_ingest.assert_called_once_with(
            sources_file=Path("custom.yaml")
        )
        print_mock.assert_called_once_with(
            "Document ingestion complete. collected=6 existing=2 inserted=4"
        )

    def test_ingest_retrieval_units_command_runs_contract_service(self):
        mock_stats = SimpleNamespace(documents_scanned=4, units_indexed=11, duplicates_skipped=2)
        service_instance = mock.Mock()
        service_instance.ingest.return_value = mock_stats
        service_cls = mock.Mock(return_value=service_instance)
        writer = mock.Mock()

        with mock.patch("backend.main._load_contract_class", return_value=service_cls) as load_class:
            with mock.patch(
                "backend.app.service.milvus_service.build_text_retrieval_replica_writer",
                return_value=writer,
            ) as writer_builder:
                with mock.patch("builtins.print") as print_mock:
                    exit_code = backend_main.main(["ingest-retrieval-units"])

        self.assertEqual(exit_code, 0)
        load_class.assert_called_once_with(
            "backend.app.service.retrieval_ingestion_service",
            "RetrievalIngestionService",
        )
        writer_builder.assert_called_once_with()
        service_cls.assert_called_once_with(writer=writer)
        service_instance.ingest.assert_called_once_with()
        print_mock.assert_called_once_with(
            "Retrieval unit ingestion complete. documents_scanned=4 units_indexed=11 duplicates_skipped=2 replica_sync=enabled"
        )

    def test_ingest_retrieval_units_command_can_skip_replica_sync(self):
        mock_stats = SimpleNamespace(document_count=2, inserted_count=2)
        service_instance = mock.Mock()
        service_instance.ingest.return_value = mock_stats
        service_cls = mock.Mock(return_value=service_instance)

        with mock.patch("backend.main._load_contract_class", return_value=service_cls):
            with mock.patch(
                "backend.app.service.milvus_service.build_text_retrieval_replica_writer"
            ) as writer_builder:
                with mock.patch("builtins.print") as print_mock:
                    exit_code = backend_main.main(["ingest-retrieval-units", "--skip-replica-sync"])

        self.assertEqual(exit_code, 0)
        writer_builder.assert_not_called()
        service_cls.assert_called_once_with()
        service_instance.ingest.assert_called_once_with()
        print_mock.assert_called_once_with(
            "Retrieval unit ingestion complete. document_count=2 inserted_count=2 replica_sync=skipped"
        )

    def test_search_retrieval_units_command_runs_contract_service(self):
        result_item = SimpleNamespace(unit_id="unit-001", article_id="article-001", score=0.91)
        service_instance = mock.Mock()
        service_instance.search.return_value = [result_item]
        service_cls = mock.Mock(return_value=service_instance)

        with mock.patch("backend.main._load_contract_class", return_value=service_cls) as load_class:
            with mock.patch("builtins.print") as print_mock:
                exit_code = backend_main.main(
                    ["search-retrieval-units", "tailored coats", "--limit", "3"]
                )

        self.assertEqual(exit_code, 0)
        load_class.assert_called_once_with(
            "backend.app.service.retrieval_search_service",
            "RetrievalSearchService",
        )
        service_cls.assert_called_once_with()
        service_instance.search.assert_called_once_with(query="tailored coats", limit=3)
        print_mock.assert_has_calls(
            [
                mock.call(
                    "Retrieval search complete. query='tailored coats' limit=3 results=1"
                ),
                mock.call("1. unit_id='unit-001' article_id='article-001' score=0.91"),
            ]
        )

    def test_missing_retrieval_contract_module_raises_runtime_error(self):
        import_error = ModuleNotFoundError(
            "No module named 'backend.app.service.retrieval_search_service'"
        )
        import_error.name = "backend.app.service.retrieval_search_service"

        with mock.patch(
            "backend.main.importlib.import_module",
            side_effect=import_error,
        ) as import_module:
            with self.assertRaisesRegex(
                RuntimeError,
                "Required service module 'backend.app.service.retrieval_search_service' "
                "is not available",
            ):
                backend_main.main(["search-retrieval-units", "tailored coats"])

        import_module.assert_called_once_with("backend.app.service.retrieval_search_service")
