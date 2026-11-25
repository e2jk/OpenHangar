"""Month and year maintenance intervals never run past the calendar deadline."""

import importlib.util
from datetime import date
from pathlib import Path

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    MaintenanceRecord,
    MaintenanceTrigger,
    Tenant,
    db,
    next_due_date,
)


def test_months_follow_the_calendar_and_never_land_later():
    assert next_due_date(date(2026, 1, 31), 30) == date(2026, 2, 28)
    assert next_due_date(date(2028, 1, 31), 30) == date(2028, 2, 29)
    # July has 31 days: the 30-day count is the earlier date.
    assert next_due_date(date(2026, 7, 1), 30) == date(2026, 7, 31)
    assert next_due_date(date(2026, 11, 30), 90) == date(2027, 2, 28)
    # Years: a leap day is not added.
    assert next_due_date(date(2027, 3, 1), 365) == date(2028, 2, 29)
    assert next_due_date(date(2028, 2, 29), 365) == date(2029, 2, 28)
    # Other day counts are plain days.
    assert next_due_date(date(2026, 1, 31), 45) == date(2026, 3, 17)


def _migration():
    path = next(
        Path("app/migrations/versions").glob("c6ab03391954_*.py"),
    )
    spec = importlib.util.spec_from_file_location("calendar_due_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_migration_moves_derived_due_dates_earlier_with_a_note(app):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with app.app_context():
        tenant = Tenant(name="T")
        db.session.add(tenant)
        db.session.flush()
        ac = Aircraft(tenant_id=tenant.id, registration="OO-CAL", make="X", model="Y")
        db.session.add(ac)
        db.session.flush()
        derived = MaintenanceTrigger(
            aircraft_id=ac.id,
            name="Monthly",
            trigger_type="calendar",
            interval_days=30,
            due_date=date(2026, 3, 2),
        )
        by_hand = MaintenanceTrigger(
            aircraft_id=ac.id,
            name="Set by hand",
            trigger_type="calendar",
            interval_days=30,
            due_date=date(2026, 3, 10),
            notes="Keep",
        )
        no_service = MaintenanceTrigger(
            aircraft_id=ac.id,
            name="Never done",
            trigger_type="calendar",
            interval_days=30,
            due_date=date(2026, 3, 2),
        )
        days_only = MaintenanceTrigger(
            aircraft_id=ac.id,
            name="Every 45 days",
            trigger_type="calendar",
            interval_days=45,
            due_date=date(2026, 3, 17),
        )
        db.session.add_all([derived, by_hand, no_service, days_only])
        db.session.flush()
        for t in (derived, by_hand, days_only):
            db.session.add(
                MaintenanceRecord(trigger_id=t.id, performed_at=date(2026, 1, 31))
            )
        db.session.commit()
        ids = [t.id for t in (derived, by_hand, no_service, days_only)]

        with (
            db.engine.begin() as conn,
            Operations.context(MigrationContext.configure(conn)),
        ):
            _migration().upgrade()
        db.session.expire_all()
        moved, kept, untouched, plain = (
            db.session.get(MaintenanceTrigger, i) for i in ids
        )
        assert moved.due_date == date(2026, 2, 28)
        assert "moved from 2026-03-02 to 2026-02-28" in moved.notes
        assert kept.due_date == date(2026, 3, 10) and kept.notes == "Keep"
        assert untouched.due_date == date(2026, 3, 2) and untouched.notes is None
        assert plain.due_date == date(2026, 3, 17) and plain.notes is None
        _migration().downgrade()
