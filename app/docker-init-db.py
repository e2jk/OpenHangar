#!/usr/bin/env python3
"""
Docker-specific database initialization script.

Production / development: runs Alembic migrations via Flask-Migrate.
  - Fresh DB:          alembic upgrade head  (creates all tables)
  - Existing create_all DB (no alembic_version table):
                       stamp head, then future upgrades work normally
  - Existing Alembic DB: applies any pending migrations

Demo: drop-and-recreate with db.create_all() then reseed — Alembic is
intentionally skipped because demo always starts from a clean slate.
"""

import os

from flask_migrate import stamp, upgrade
from sqlalchemy import inspect

from init import create_app
from models import User, db


def _run_migrations(app: object) -> None:
    """Apply pending Alembic migrations, stamping first if needed."""
    from flask import Flask

    assert isinstance(app, Flask)
    with app.app_context():
        inspector = inspect(db.engine)
        has_alembic = inspector.has_table("alembic_version")
        has_users = inspector.has_table("users")

        if not has_alembic and has_users:
            # Pre-Alembic instance built with create_all() — stamp at baseline
            # so future migrations apply cleanly without recreating existing tables.
            print("Existing database without Alembic history — stamping at baseline...")
            stamp(revision="head")
            print(
                "Stamp complete. Future schema changes will be applied as migrations."
            )
        else:
            print("Running database migrations...")
            upgrade()
            print("Migrations complete.")


def init_database() -> None:
    app = create_app()
    flask_env = os.environ.get("FLASK_ENV", "production")

    with app.app_context():
        if flask_env == "demo":
            print("Demo environment — rebuilding schema and reseeding...")
            db.drop_all()
            db.create_all()
            print("Database structure ready.")
            from demo_seed import seed as demo_seed

            demo_seed()
            print("Demo seed loaded.")
            return

    _run_migrations(app)

    with app.app_context():
        existing_users = User.query.count()
        if flask_env == "development" and existing_users == 0:
            print("Development environment with empty database — loading seed data...")
            from dev_seed import seed

            seed()
            print("Seed data loaded.")
        elif existing_users > 0:
            print(
                f"{flask_env.title()} database already has {existing_users} user(s) — skipping seed."
            )
        else:
            print(
                f"{flask_env.title()} environment — database ready, no seed data loaded."
            )

    print("Database initialization complete.")


if __name__ == "__main__":
    init_database()
