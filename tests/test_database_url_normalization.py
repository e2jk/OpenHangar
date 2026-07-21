"""
Tests for the psycopg2-binary -> psycopg 3 migration (IMG-04):
- init._normalize_database_url: plain postgresql:// (or explicit
  +psycopg2) gets rewritten to the psycopg 3 dialect.
- utils.to_libpq_url: the reverse direction, for libpq CLI tools
  (pg_dump, psql) that don't understand a +driver suffix.
"""

from init import _normalize_database_url  # pyright: ignore[reportMissingImports]
from utils import to_libpq_url  # pyright: ignore[reportMissingImports]


class TestNormalizeDatabaseUrl:
    def test_plain_postgresql_rewritten_to_psycopg(self):
        assert (
            _normalize_database_url("postgresql://u:p@h/db")
            == "postgresql+psycopg://u:p@h/db"
        )

    def test_explicit_psycopg2_rewritten_to_psycopg(self):
        assert (
            _normalize_database_url("postgresql+psycopg2://u:p@h/db")
            == "postgresql+psycopg://u:p@h/db"
        )

    def test_already_psycopg_left_unchanged(self):
        assert (
            _normalize_database_url("postgresql+psycopg://u:p@h/db")
            == "postgresql+psycopg://u:p@h/db"
        )

    def test_sqlite_left_unchanged(self):
        assert _normalize_database_url("sqlite:///:memory:") == "sqlite:///:memory:"


class TestToLibpqUrl:
    def test_strips_psycopg_suffix(self):
        assert to_libpq_url("postgresql+psycopg://u:p@h/db") == "postgresql://u:p@h/db"

    def test_plain_postgresql_left_unchanged(self):
        assert to_libpq_url("postgresql://u:p@h/db") == "postgresql://u:p@h/db"

    def test_sqlite_left_unchanged(self):
        assert to_libpq_url("sqlite:///:memory:") == "sqlite:///:memory:"
