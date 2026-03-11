import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend import main as backend_main
from backend.app.service.document_ingestion_service import DocumentIngestionStats


class BackendCliTests(unittest.TestCase):
    def test_init_db_command_creates_tables(self):
        with mock.patch("backend.app.db.create_all_tables") as create_all_tables:
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
