"""A disposable PostgreSQL schema per test, for the real-connection race lanes.

Set TEST_POSTGRES_URL to a disposable PostgreSQL database (CI runs these
lanes through scripts/test-reservation-concurrency.sh). Without it, tests
using this app are skipped.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from flask import Flask
from init import create_app
from models import db
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def postgres_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("requires disposable PostgreSQL (TEST_POSTGRES_URL)")
    admin = create_engine(url)
    schema = "race_" + uuid4().hex
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv(
        "OPENHANGAR_DATABASE_URL", scoped_url.render_as_string(hide_password=False)
    )
    monkeypatch.setenv("OPENHANGAR_ENV", "development")
    monkeypatch.setenv("OPENHANGAR_UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setenv("OPENHANGAR_SKIP_BACKGROUND_THREADS", "1")
    scoped_engine = create_engine(scoped_url)
    application = None
    try:
        # Build tables before app startup reads settings/tenant slugs.
        db.metadata.create_all(scoped_engine)
        application = create_app()
        application.config.update(
            TESTING=True, WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False
        )
        yield application
    finally:
        if application is not None:
            with application.app_context():
                db.session.remove()
                db.engine.dispose()
        scoped_engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
