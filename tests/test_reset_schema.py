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


class TestHoldsNonDemoData:
    """Demo mode's wipe refuses a database that holds a real instance."""

    def test_empty_database_may_be_wiped(self, app):
        from models import db, holds_non_demo_data  # pyright: ignore[reportMissingImports]

        with app.app_context():
            assert holds_non_demo_data(db) is False

    def test_accounts_without_demo_slots_are_a_real_instance(self, app):
        import pw_hash  # pyright: ignore[reportMissingImports]
        from models import User, db, holds_non_demo_data  # pyright: ignore[reportMissingImports]

        with app.app_context():
            db.session.add(
                User(email="real@example.com", password_hash=pw_hash.hash("x"))
            )
            db.session.commit()
            assert holds_non_demo_data(db) is True

    def test_demo_database_may_be_wiped(self, app, monkeypatch):
        from demo_seed import seed  # pyright: ignore[reportMissingImports]
        from models import db, holds_non_demo_data  # pyright: ignore[reportMissingImports]

        monkeypatch.setenv("OPENHANGAR_ENV", "demo")
        with app.app_context():
            seed()
            assert holds_non_demo_data(db) is False

    def test_database_without_tables_may_be_wiped(self):
        from unittest.mock import patch

        from models import holds_non_demo_data  # pyright: ignore[reportMissingImports]

        inspector = MagicMock()
        inspector.has_table.return_value = False
        with patch("models.inspect", return_value=inspector):
            assert holds_non_demo_data(MagicMock()) is False

    def test_accounts_from_before_demo_slots_existed_are_real(self):
        from unittest.mock import patch

        from models import holds_non_demo_data  # pyright: ignore[reportMissingImports]

        db = MagicMock()
        inspector = MagicMock()
        inspector.has_table.side_effect = lambda name: name == "users"
        conn = db.engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.first.return_value = (1,)
        with patch("models.inspect", return_value=inspector):
            assert holds_non_demo_data(db) is True
