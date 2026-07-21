"""
Tests for startup configuration validation (_validate_config).

Covers the checks added to create_app() / _validate_config():
- SECRET_KEY minimum length
- MAX_UPLOAD_BYTES must be a plain integer and positive
- SYNC_SCAN_INTERVAL must be a plain positive integer when set
- DATABASE_URL must use a PostgreSQL scheme in production
- BACKUP_ENCRYPTION_KEY must not be whitespace-only
- Multiple errors reported together
"""

import pytest

from init import _validate_config  # pyright: ignore[reportMissingImports]


def _app_with(config: dict):
    """Return a minimal mock object that quacks like a Flask app for _validate_config."""

    class _FakeConfig(dict):
        pass

    class _FakeApp:
        def __init__(self):
            self.config = _FakeConfig(config)

    return _FakeApp()


GOOD = {
    "SECRET_KEY": "a" * 32,  # Flask config key (set from OPENHANGAR_SECRET_KEY env var)
    "MAX_CONTENT_LENGTH": 50 * 1024 * 1024,
    "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
}


class TestSecretKey:
    def test_short_key_raises(self, monkeypatch):
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        app = _app_with({**GOOD, "SECRET_KEY": "tooshort"})
        with pytest.raises(RuntimeError, match="SECRET_KEY is too short"):
            _validate_config(app)

    def test_exactly_32_chars_passes(self, monkeypatch):
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        _validate_config(_app_with({**GOOD, "SECRET_KEY": "x" * 32}))

    def test_longer_than_32_passes(self, monkeypatch):
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        _validate_config(_app_with({**GOOD, "SECRET_KEY": "x" * 64}))


class TestMaxUploadBytes:
    def test_non_integer_raises(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_MAX_UPLOAD_BYTES", "50MB")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        with pytest.raises(
            RuntimeError, match="MAX_UPLOAD_BYTES must be a plain integer"
        ):
            _validate_config(_app_with(GOOD))

    def test_negative_value_raises(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_MAX_UPLOAD_BYTES", "-1")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        with pytest.raises(
            RuntimeError, match="MAX_UPLOAD_BYTES must be a positive integer"
        ):
            _validate_config(_app_with(GOOD))

    def test_zero_raises(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_MAX_UPLOAD_BYTES", "0")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        with pytest.raises(
            RuntimeError, match="MAX_UPLOAD_BYTES must be a positive integer"
        ):
            _validate_config(_app_with(GOOD))

    def test_positive_passes_and_updates_config(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_MAX_UPLOAD_BYTES", "1024")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        app = _app_with(GOOD)
        _validate_config(app)
        assert app.config["MAX_CONTENT_LENGTH"] == 1024

    def test_unset_leaves_default(self, monkeypatch):
        monkeypatch.delenv("OPENHANGAR_MAX_UPLOAD_BYTES", raising=False)
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        app = _app_with(GOOD)
        _validate_config(app)
        assert app.config["MAX_CONTENT_LENGTH"] == 50 * 1024 * 1024


class TestSyncScanInterval:
    def test_non_integer_raises(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_SYNC_SCAN_INTERVAL", "2min")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        with pytest.raises(
            RuntimeError, match="SYNC_SCAN_INTERVAL must be a plain integer"
        ):
            _validate_config(_app_with(GOOD))

    def test_negative_value_raises(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_SYNC_SCAN_INTERVAL", "-10")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        with pytest.raises(
            RuntimeError, match="SYNC_SCAN_INTERVAL must be a positive integer"
        ):
            _validate_config(_app_with(GOOD))

    def test_zero_raises(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_SYNC_SCAN_INTERVAL", "0")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        with pytest.raises(
            RuntimeError, match="SYNC_SCAN_INTERVAL must be a positive integer"
        ):
            _validate_config(_app_with(GOOD))

    def test_positive_passes(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_SYNC_SCAN_INTERVAL", "30")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        _validate_config(_app_with(GOOD))

    def test_unset_passes(self, monkeypatch):
        monkeypatch.delenv("OPENHANGAR_SYNC_SCAN_INTERVAL", raising=False)
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        _validate_config(_app_with(GOOD))


class TestDatabaseUrl:
    def test_non_postgres_scheme_raises_in_production(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ENV", "production")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        app = _app_with({**GOOD, "SQLALCHEMY_DATABASE_URI": "mysql://user:pw@host/db"})
        with pytest.raises(RuntimeError, match="DATABASE_URL scheme"):
            _validate_config(app)

    def test_postgres_scheme_passes_in_production(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ENV", "production")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        _validate_config(
            _app_with({**GOOD, "SQLALCHEMY_DATABASE_URI": "postgresql://u:p@h/db"})
        )

    def test_psycopg_scheme_passes_in_production(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ENV", "production")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        _validate_config(
            _app_with(
                {**GOOD, "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg://u:p@h/db"}
            )
        )

    def test_psycopg2_scheme_raises_in_production(self, monkeypatch):
        # psycopg2 was replaced by psycopg 3 (IMG-04); create_app() normalises
        # a plain postgresql:// URL to +psycopg before this check ever runs, so
        # an explicit +psycopg2 scheme reaching here means the driver it names
        # isn't installed and would fail to connect — reject it.
        monkeypatch.setenv("OPENHANGAR_ENV", "production")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        app = _app_with(
            {**GOOD, "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg2://u:p@h/db"}
        )
        with pytest.raises(RuntimeError, match="DATABASE_URL scheme"):
            _validate_config(app)

    def test_non_postgres_allowed_in_development(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ENV", "development")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        _validate_config(
            _app_with({**GOOD, "SQLALCHEMY_DATABASE_URI": "mysql://u:p@h/db"})
        )

    def test_sqlite_always_allowed(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ENV", "production")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        _validate_config(
            _app_with({**GOOD, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
        )


class TestOpenhangareEnv:
    def test_invalid_value_raises(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ENV", "staging")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        with pytest.raises(RuntimeError, match="OPENHANGAR_ENV must be one of"):
            _validate_config(_app_with(GOOD))

    def test_valid_values_pass(self, monkeypatch):
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        for val in ("production", "development", "test", "demo"):
            monkeypatch.setenv("OPENHANGAR_ENV", val)
            _validate_config(_app_with(GOOD))

    def test_unset_passes(self, monkeypatch):
        monkeypatch.delenv("OPENHANGAR_ENV", raising=False)
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        _validate_config(_app_with(GOOD))


class TestSmtpPort:
    def test_non_integer_raises(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_SMTP_PORT", "abc")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        with pytest.raises(
            RuntimeError, match="OPENHANGAR_SMTP_PORT must be an integer"
        ):
            _validate_config(_app_with(GOOD))

    def test_out_of_range_raises(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_SMTP_PORT", "99999")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        with pytest.raises(RuntimeError, match="OPENHANGAR_SMTP_PORT must be between"):
            _validate_config(_app_with(GOOD))

    def test_zero_raises(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_SMTP_PORT", "0")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        with pytest.raises(RuntimeError, match="OPENHANGAR_SMTP_PORT must be between"):
            _validate_config(_app_with(GOOD))

    def test_valid_port_passes(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_SMTP_PORT", "587")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        _validate_config(_app_with(GOOD))

    def test_unset_passes(self, monkeypatch):
        monkeypatch.delenv("OPENHANGAR_SMTP_PORT", raising=False)
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        _validate_config(_app_with(GOOD))


class TestDemoBusyWindowMinutes:
    def test_non_integer_raises(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_DEMO_BUSY_WINDOW_MINUTES", "abc")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        with pytest.raises(RuntimeError, match="OPENHANGAR_DEMO_BUSY_WINDOW_MINUTES"):
            _validate_config(_app_with(GOOD))

    def test_zero_raises(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_DEMO_BUSY_WINDOW_MINUTES", "0")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        with pytest.raises(
            RuntimeError, match="OPENHANGAR_DEMO_BUSY_WINDOW_MINUTES must be a positive"
        ):
            _validate_config(_app_with(GOOD))

    def test_valid_value_passes(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_DEMO_BUSY_WINDOW_MINUTES", "30")
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        _validate_config(_app_with(GOOD))

    def test_unset_passes(self, monkeypatch):
        monkeypatch.delenv("OPENHANGAR_DEMO_BUSY_WINDOW_MINUTES", raising=False)
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        _validate_config(_app_with(GOOD))


class TestBackupEncryptionKey:
    def test_whitespace_only_raises(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", "   ")
        with pytest.raises(RuntimeError, match="BACKUP_ENCRYPTION_KEY.*whitespace"):
            _validate_config(_app_with(GOOD))

    def test_real_key_passes(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", "realkey")
        _validate_config(_app_with(GOOD))

    def test_unset_passes(self, monkeypatch):
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        _validate_config(_app_with(GOOD))


class TestRestoreEncryptionKey:
    def test_whitespace_only_raises(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_RESTORE_ENCRYPTION_KEY", "   ")
        with pytest.raises(RuntimeError, match="RESTORE_ENCRYPTION_KEY.*whitespace"):
            _validate_config(_app_with(GOOD))

    def test_real_key_passes(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_RESTORE_ENCRYPTION_KEY", "realkey")
        _validate_config(_app_with(GOOD))

    def test_unset_passes(self, monkeypatch):
        monkeypatch.delenv("OPENHANGAR_RESTORE_ENCRYPTION_KEY", raising=False)
        _validate_config(_app_with(GOOD))


class TestMultipleErrors:
    def test_all_errors_reported_together(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ENV", "production")
        monkeypatch.setenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", "   ")
        monkeypatch.setenv("OPENHANGAR_MAX_UPLOAD_BYTES", "notanumber")
        app = _app_with(
            {
                "SECRET_KEY": "short",
                "MAX_CONTENT_LENGTH": 50 * 1024 * 1024,
                "SQLALCHEMY_DATABASE_URI": "mysql://u:p@h/db",
            }
        )
        with pytest.raises(RuntimeError) as exc_info:
            _validate_config(app)
        msg = str(exc_info.value)
        assert "SECRET_KEY is too short" in msg
        assert "MAX_UPLOAD_BYTES" in msg
        assert "DATABASE_URL scheme" in msg
        assert "OPENHANGAR_BACKUP_ENCRYPTION_KEY" in msg
