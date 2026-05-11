import shutil
import tempfile

import pytest  # pyright: ignore[reportMissingImports]
from flask import template_rendered  # pyright: ignore[reportMissingImports]
from init import create_app  # pyright: ignore[reportMissingImports]
from models import db as _db  # pyright: ignore[reportMissingImports]
from sqlalchemy.pool import StaticPool  # pyright: ignore[reportMissingImports]


# Session-scoped: create_app() and db.create_all() run once per worker process.
# StaticPool ensures the same in-memory SQLite connection is reused throughout.
@pytest.fixture(scope="session")
def app():
    upload_dir = tempfile.mkdtemp()
    app = create_app()
    app.config["TESTING"] = True
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
        _db.drop_all()
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
