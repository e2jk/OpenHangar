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

import bcrypt  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]
from flask import template_rendered  # pyright: ignore[reportMissingImports]
from init import create_app  # pyright: ignore[reportMissingImports]
from models import db as _db  # pyright: ignore[reportMissingImports]
from sqlalchemy.pool import StaticPool  # pyright: ignore[reportMissingImports]

# Reduce bcrypt work factor to the minimum (4) for the entire test session.
# Default is 12 (~670 ms/hash on this server); rounds=4 is ~3 ms.
# bcrypt.checkpw works correctly regardless of the rounds used to create the hash.
# This patch applies to all callers — test helpers AND the app's own auth routes.
_real_gensalt = bcrypt.gensalt
bcrypt.gensalt = lambda rounds=4, prefix=b"2b": _real_gensalt(4, prefix=prefix)


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
    """Truncate all tables after every test so each starts with a clean DB."""
    yield
    with app.app_context():
        _db.session.remove()
        for table in reversed(_db.metadata.sorted_tables):
            _db.session.execute(table.delete())
        _db.session.commit()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def captured_templates(app):
    """Collects (template, context) pairs rendered during a request."""
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template, context))

    template_rendered.connect(record, app)
    yield recorded
    template_rendered.disconnect(record, app)
