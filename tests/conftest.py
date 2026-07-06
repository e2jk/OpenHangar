import os
import shutil
import socket
import tempfile

# Cap all socket operations (DNS, TCP, SMTP, HTTP) at 5 s so tests that
# accidentally make real network calls time out quickly instead of hanging
# for minutes.  Tests that need the network must mock it.
socket.setdefaulttimeout(5)


def pytest_addoption(parser):
    parser.addoption(
        "--e2e",
        action="store_true",
        default=False,
        help="Run Playwright end-to-end browser tests (slow, requires live server)",
    )


# Set OPENHANGAR_SECRET_KEY before any create_app() call — required since the app raises
# RuntimeError if it is absent. os.environ.setdefault leaves any value already set
# by the caller (e.g. CI) intact.
os.environ.setdefault("OPENHANGAR_SECRET_KEY", "test-secret-key-not-for-production")

import argon2 as _argon2  # pyright: ignore[reportMissingImports]
import bcrypt  # pyright: ignore[reportMissingImports]
import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]
from flask import template_rendered  # pyright: ignore[reportMissingImports]
from init import create_app  # pyright: ignore[reportMissingImports]
from models import db as _db  # pyright: ignore[reportMissingImports]
from sqlalchemy.pool import StaticPool  # pyright: ignore[reportMissingImports]

# Keep bcrypt work factor at the minimum (4) so test_auth_logging.py's legacy-hash
# tests (bcrypt→Argon2id upgrade path) stay fast. All other tests use pw_hash.hash().
_real_gensalt = bcrypt.gensalt
bcrypt.gensalt = lambda rounds=4, prefix=b"2b": _real_gensalt(4, prefix=prefix)

# Reduce Argon2id work factors to the minimum for the entire test session.
# Production settings (time_cost=2, memory_cost=65536, parallelism=2) allocate
# 64 MiB per hash call.  Under pytest-xdist every worker hashes in parallel,
# causing memory pressure that inflates login-path tests to 5-10 seconds.
# This patch must run before create_app() so that auth/routes.py (imported
# inside create_app()) copies the already-fast DUMMY_HASH at its module level.
_pw_hash._ph = _argon2.PasswordHasher(
    time_cost=1,
    memory_cost=8,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=_argon2.Type.ID,
)
_pw_hash.DUMMY_HASH = _pw_hash._ph.hash("dummy-timing-equalization-placeholder")


# Session-scoped: create_app() and db.create_all() run once per worker process.
# StaticPool ensures the same in-memory SQLite connection is reused throughout.
@pytest.fixture(scope="session")
def app():
    upload_dir = tempfile.mkdtemp()
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["RATELIMIT_ENABLED"] = False
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }
    app.config["UPLOAD_FOLDER"] = upload_dir

    with app.app_context():
        _db.create_all()

    yield app

    with app.app_context():
        _db.session.remove()
        _db.drop_all()
        _db.engine.dispose()
    shutil.rmtree(upload_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_db(app):
    """Truncate all tables and clear the in-memory cache after every test."""
    yield
    with app.app_context():
        _db.session.remove()
        for table in reversed(_db.metadata.sorted_tables):
            _db.session.execute(table.delete())
        _db.session.commit()
        from extensions import cache as _cache  # pyright: ignore[reportMissingImports]

        _cache.clear()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _block_tile_fetches():
    """Prevent real HTTP tile fetches in every test.

    GIF/image generation calls urllib.request.urlopen to download map tiles from
    a.basemaps.cartocdn.com.  When the local DNS resolver is slow or unreachable
    each tile request hangs for the full socket timeout, turning a fast test run
    into a 49-minute one.

    Raising OSError immediately is safe: _make_tile_background wraps every tile
    fetch in ``except Exception`` and falls back to a plain background, so all
    GIF/PNG assertions still pass.

    Tests that need controlled tile responses (tile-cache, OpenAIP overlay, …)
    already patch urllib.request.urlopen themselves; their patch takes precedence
    over this one for the duration of their ``with patch(...)`` block.

    unittest.mock.patch is used instead of monkeypatch to avoid changing the
    fixture finalization order (monkeypatch as a dependency of an autouse fixture
    causes it to outlive clean_db teardown, breaking tests that patch db.session).
    """
    from unittest.mock import patch

    def _no_network(*args, **kwargs):
        raise OSError("no network in tests: patch urllib.request.urlopen")

    with patch("urllib.request.urlopen", _no_network):
        yield


@pytest.fixture()
def captured_templates(app):
    """Collects (template, context) pairs rendered during a request."""
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template, context))

    template_rendered.connect(record, app)
    yield recorded
    template_rendered.disconnect(record, app)
