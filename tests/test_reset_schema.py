"""Tests for models.reset_schema (demo database schema reset helper)."""

from unittest.mock import MagicMock

from models import reset_schema  # pyright: ignore[reportMissingImports]


class TestResetSchema:
    def test_sqlite_skips_schema_drop(self):
        mock_db = MagicMock()
        mock_db.engine.dialect.name = "sqlite"

        reset_schema(mock_db)

        mock_db.engine.begin.assert_not_called()
        mock_db.create_all.assert_called_once()

    def test_postgresql_drops_and_recreates_public_schema(self):
        mock_db = MagicMock()
        mock_db.engine.dialect.name = "postgresql"
        mock_conn = mock_db.engine.begin.return_value.__enter__.return_value

        reset_schema(mock_db)

        assert mock_conn.execute.call_count == 2
        statements = [call.args[0].text for call in mock_conn.execute.call_args_list]
        assert statements == ["DROP SCHEMA public CASCADE", "CREATE SCHEMA public"]
        mock_db.create_all.assert_called_once()
