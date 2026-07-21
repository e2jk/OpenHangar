"""
Tests for init._env_or_file (INFRA-05): OPENHANGAR_<NAME>_FILE support for
secret-bearing env vars, so a secret's value never has to sit directly in
`docker inspect` / /proc/<pid>/environ.
"""

import pytest  # pyright: ignore[reportMissingImports]

from init import _env_or_file  # pyright: ignore[reportMissingImports]


class TestEnvOrFile:
    def test_plain_env_var_used_when_file_not_set(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_TESTSECRET", "from-env")
        monkeypatch.delenv("OPENHANGAR_TESTSECRET_FILE", raising=False)
        assert _env_or_file("TESTSECRET") == "from-env"

    def test_unset_returns_empty_string(self, monkeypatch):
        monkeypatch.delenv("OPENHANGAR_TESTSECRET", raising=False)
        monkeypatch.delenv("OPENHANGAR_TESTSECRET_FILE", raising=False)
        assert _env_or_file("TESTSECRET") == ""

    def test_file_value_used_and_stripped(self, monkeypatch, tmp_path):
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("from-file\n")
        monkeypatch.delenv("OPENHANGAR_TESTSECRET", raising=False)
        monkeypatch.setenv("OPENHANGAR_TESTSECRET_FILE", str(secret_file))
        assert _env_or_file("TESTSECRET") == "from-file"

    def test_both_set_raises(self, monkeypatch, tmp_path):
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("from-file")
        monkeypatch.setenv("OPENHANGAR_TESTSECRET", "from-env")
        monkeypatch.setenv("OPENHANGAR_TESTSECRET_FILE", str(secret_file))
        with pytest.raises(RuntimeError, match="Both OPENHANGAR_TESTSECRET and"):
            _env_or_file("TESTSECRET")

    def test_unreadable_file_raises(self, monkeypatch, tmp_path):
        missing_file = tmp_path / "does-not-exist.txt"
        monkeypatch.delenv("OPENHANGAR_TESTSECRET", raising=False)
        monkeypatch.setenv("OPENHANGAR_TESTSECRET_FILE", str(missing_file))
        with pytest.raises(RuntimeError, match="could not be read"):
            _env_or_file("TESTSECRET")
