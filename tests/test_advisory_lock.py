"""Tests for app/services/advisory_lock.py."""

from unittest.mock import MagicMock

from services.advisory_lock import advisory_lock_scope  # pyright: ignore[reportMissingImports]


class TestAdvisoryLockScope:
    def test_non_postgresql_yields_true_without_db_call(self):
        mock_db = MagicMock()
        mock_db.engine.dialect.name = "sqlite"

        with advisory_lock_scope(mock_db, 123) as acquired:
            assert acquired is True

        mock_db.engine.connect.assert_not_called()

    def test_postgresql_acquires_and_releases_lock(self):
        mock_db = MagicMock()
        mock_db.engine.dialect.name = "postgresql"
        mock_conn = mock_db.engine.connect.return_value
        mock_conn.execute.return_value.scalar.return_value = True

        with advisory_lock_scope(mock_db, 123) as acquired:
            assert acquired is True

        assert mock_conn.execute.call_count == 2
        mock_conn.close.assert_called_once()

    def test_postgresql_lock_not_acquired_skips_unlock(self):
        mock_db = MagicMock()
        mock_db.engine.dialect.name = "postgresql"
        mock_conn = mock_db.engine.connect.return_value
        mock_conn.execute.return_value.scalar.return_value = False

        with advisory_lock_scope(mock_db, 123) as acquired:
            assert acquired is False

        mock_conn.execute.assert_called_once()
        mock_conn.close.assert_called_once()

    def test_connection_closed_even_if_body_raises(self):
        mock_db = MagicMock()
        mock_db.engine.dialect.name = "postgresql"
        mock_conn = mock_db.engine.connect.return_value
        mock_conn.execute.return_value.scalar.return_value = True

        try:
            with advisory_lock_scope(mock_db, 123):
                raise RuntimeError("boom")
        except RuntimeError:
            pass

        assert mock_conn.execute.call_count == 2  # lock + unlock
        mock_conn.close.assert_called_once()
