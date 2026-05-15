#!/usr/bin/env python3
"""
Docker-specific database initialization script.
Handles first-run table creation and optional dev seed data.
Uses db.create_all() (idempotent) rather than migrations, which are reserved
for evolving the schema once live data exists.
"""

import os

from init import create_app
from models import (
    Aircraft,
    Component,
    FlightCrew,
    FlightEntry,
    MaintenanceRecord,
    MaintenanceTrigger,
    PilotLogbookEntry,
    PilotProfile,
    Tenant,
    TenantUser,
    User,
    db,
)  # noqa: F401 — all must be imported so create_all() sees every table


def init_database():
    app = create_app()

    with app.app_context():
        print("Creating database structure...")
        db.create_all()
        print("Database structure ready.")

        flask_env = os.environ.get("FLASK_ENV", "production")

        if flask_env == "demo":
            print("Demo environment — rebuilding schema and reseeding...")
            db.drop_all()
            db.create_all()
            print("Database structure ready.")
            from demo_seed import seed as demo_seed

            demo_seed()
            print("Demo seed loaded.")
            return

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
                f"{flask_env.title()} environment — database structure ready, no seed data loaded."
            )

        print("Database initialization complete.")


if __name__ == "__main__":
    init_database()
