"""PostgreSQL-only behaviour: advisory locks and migrating populated data (audit T02).

Set TEST_POSTGRES_URL to a disposable PostgreSQL database (CI runs this
through scripts/test-reservation-concurrency.sh). Every test works in its own
schema and drops it afterwards.

- Advisory locks are exercised across real connections: held, refused to a
  second worker, and taken again after release — also after an error.
- Migrations are run on a schema holding real rows, not only on an empty
  database: values must survive, and the logbook-unification migration's
  "legacy data present" branch must keep both old tables and raise its flag.
- The migrations, run from scratch, build exactly the schema the models
  describe (the rest of the test suite builds it from the models instead).
"""

import datetime as dt
import os
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from flask import Flask
from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.engine import Engine, make_url

from tests.postgres_schema import postgres_app

MIGRATIONS = str(Path(__file__).parent.parent / "app" / "migrations")
UNIFICATION = "893349d2be22"


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)


# ── Advisory locks ────────────────────────────────────────────────────────────


def test_advisory_lock_is_exclusive_and_reacquirable(app: Flask) -> None:
    from models import db
    from services.advisory_lock import advisory_lock_scope

    with app.app_context():
        with advisory_lock_scope(db, 99001) as first:
            assert first
            with advisory_lock_scope(db, 99001) as second:
                assert not second  # another worker skips this run
            with advisory_lock_scope(db, 99002) as other_id:
                assert other_id  # a different job is not blocked
        with advisory_lock_scope(db, 99001) as again:
            assert again  # released at the end of the block

        def failing_job() -> None:
            with advisory_lock_scope(db, 99001) as held:
                assert held
                raise RuntimeError("job failed")

        with pytest.raises(RuntimeError):
            failing_job()
        with advisory_lock_scope(db, 99001) as after_error:
            assert after_error  # an error never leaves the lock held


def test_advisory_lock_survives_commits_in_the_guarded_work(app: Flask) -> None:
    """Held on its own connection: the job's commits do not release it."""
    from models import AppSetting, db
    from services.advisory_lock import advisory_lock_scope

    with app.app_context(), advisory_lock_scope(db, 99003) as acquired:
        assert acquired
        for value in ("one", "two"):
            db.session.merge(AppSetting(key="t02-lock-test", value=value))
            db.session.commit()
            with advisory_lock_scope(db, 99003) as competitor:
                assert not competitor


# ── Migrations on populated data ──────────────────────────────────────────────


@pytest.fixture
def empty_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[Flask, Engine]]:
    """An app on an empty schema, for running the migrations from scratch."""
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("requires disposable PostgreSQL (TEST_POSTGRES_URL)")
    admin = create_engine(url)
    schema = "migr_" + uuid4().hex
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv(
        "OPENHANGAR_DATABASE_URL", scoped.render_as_string(hide_password=False)
    )
    monkeypatch.setenv("OPENHANGAR_ENV", "development")
    monkeypatch.setenv("OPENHANGAR_UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setenv("OPENHANGAR_SKIP_BACKGROUND_THREADS", "1")
    engine = create_engine(scoped)
    application = None
    try:
        from init import create_app

        application = create_app()
        application.config.update(TESTING=True)
        yield application, engine
    finally:
        if application is not None:
            from models import db

            with application.app_context():
                db.session.remove()
                db.engine.dispose()
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _upgrade(app: Flask, revision: str) -> None:
    from flask_migrate import upgrade

    with app.app_context():
        upgrade(directory=MIGRATIONS, revision=revision)


def _insert(engine: Engine, table: str, **values: Any) -> int:
    """Insert one row as the schema stands *now*, filling any other required
    column with a plain value of its type — so the test does not depend on
    every column a past revision happened to have."""
    meta = MetaData()
    meta.reflect(engine, only=[table])
    tbl = meta.tables[table]
    row = dict(values)
    for column in tbl.columns:
        if column.name in row or column.nullable or column.primary_key:
            continue
        if column.server_default is not None:
            continue
        python = column.type.python_type
        row[column.name] = {
            str: "x",
            int: 0,
            bool: False,
            Decimal: Decimal(0),
            float: 0.0,
            dt.date: dt.date(2026, 1, 1),
            dt.datetime: dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        }.get(python, "x")
    with engine.begin() as conn:
        result = conn.execute(tbl.insert().values(**row).returning(tbl.c.id))
        return int(result.scalar_one())


def _populate_before_unification(
    engine: Engine, *, legacy_rows: bool
) -> dict[str, int]:
    tenant = _insert(engine, "tenants", name="Migrated hangar")
    user = _insert(engine, "users", email="pilot@migrated.test", password_hash="x")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tenant_users (user_id, tenant_id, role) VALUES (:u, :t, 'OWNER')"
            ),
            {"u": user, "t": tenant},
        )
    aircraft = _insert(
        engine,
        "aircraft",
        tenant_id=tenant,
        registration="OO-MIG",
        make="Robin",
        model="DR400",
    )
    flight = _insert(
        engine,
        "flight_entries",
        aircraft_id=aircraft,
        date=dt.date(2025, 6, 1),
        departure_icao="EBAW",
        arrival_icao="EBKT",
        flight_time=Decimal("1.3"),
        notes="Kept through the migration",
    )
    ids = {"tenant": tenant, "user": user, "aircraft": aircraft, "flight": flight}
    if legacy_rows:
        ids["legacy"] = _insert(
            engine,
            "pilot_logbook_entries",
            pilot_user_id=user,
            date=dt.date(2024, 3, 2),
        )
    return ids


@pytest.mark.parametrize("legacy_rows", [False, True])
def test_unification_keeps_flights_and_handles_legacy_tables(
    empty_schema: tuple[Flask, Engine], legacy_rows: bool
) -> None:
    app, engine = empty_schema
    _upgrade(app, "8a3da16e7596")  # the revision just before the unification
    ids = _populate_before_unification(engine, legacy_rows=legacy_rows)

    _upgrade(app, "head")

    with engine.connect() as conn:
        flight = conn.execute(
            text(
                "SELECT aircraft_id, date, departure_icao, arrival_icao, flight_time, notes FROM flights WHERE id = :id"
            ),
            {"id": ids["flight"]},
        ).one()
        assert flight == (
            ids["aircraft"],
            dt.date(2025, 6, 1),
            "EBAW",
            "EBKT",
            Decimal("1.3"),
            "Kept through the migration",
        )
        tables = set(
            conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
                )
            ).scalars()
        )
        flag = conn.execute(
            text(
                "SELECT value FROM app_settings WHERE key = 'legacy_logbook_data_present'"
            )
        ).scalar()
        if legacy_rows:
            assert {"flight_crew", "pilot_logbook_entries"} <= tables
            assert flag == "true"
            assert (
                conn.execute(
                    text("SELECT COUNT(*) FROM pilot_logbook_entries")
                ).scalar()
                == 1
            )
        else:
            assert not {"flight_crew", "pilot_logbook_entries"} & tables
            assert flag is None


def test_this_releases_migrations_apply_to_existing_rows(
    empty_schema: tuple[Flask, Engine],
) -> None:
    """The ledger reversal index and the TOTP step column go onto a database
    that already has users and ledger entries (with a reversal)."""
    app, engine = empty_schema
    _upgrade(app, "d9cb18db220f")
    tenant = _insert(engine, "tenants", name="Ledger hangar")
    user = _insert(
        engine,
        "users",
        email="ledger@migrated.test",
        password_hash="x",
        totp_secret="JBSWY3DPEHPK3PXP",
    )
    account = _insert(
        engine, "billing_accounts", tenant_id=tenant, user_id=user, kind="renter"
    )
    charge = _insert(
        engine,
        "ledger_entries",
        account_id=account,
        entry_type="charge",
        amount=Decimal(100),
        description="c",
        entry_date=dt.date(2026, 1, 1),
    )
    _insert(
        engine,
        "ledger_entries",
        account_id=account,
        entry_type="charge",
        amount=Decimal(-100),
        description="r",
        entry_date=dt.date(2026, 1, 1),
        reverses_id=charge,
    )

    _upgrade(app, "head")

    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT totp_secret, totp_last_step FROM users WHERE id = :u"),
            {"u": user},
        ).one() == ("JBSWY3DPEHPK3PXP", None)
        assert conn.execute(text("SELECT COUNT(*) FROM ledger_entries")).scalar() == 2
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ledger_entries (account_id, entry_type, amount, description, entry_date, reverses_id, created_at) VALUES (:a, 'charge', -100, 'again', '2026-01-02', :c, now())"
            ),
            {"a": account, "c": charge},
        )


# ── Migrations build what the models describe ─────────────────────────────────


def test_migrations_build_the_schema_the_models_describe(
    empty_schema: tuple[Flask, Engine],
) -> None:
    """The test suite builds its tables from the models (``create_all``);
    every real install builds them by running the migrations. Any difference
    — a missing table, column, index or unique constraint, another column
    type or nullability, a foreign key without its ``ondelete`` — would pass
    every other test and only show in production. Column server defaults are
    not compared: the models set their defaults in Python."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from models import db

    app, engine = empty_schema
    _upgrade(app, "head")

    with engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        differences = compare_metadata(context, db.metadata)

    assert differences == [], (
        "migrations at head differ from app/models.py; add a migration "
        "(or fix the model):\n" + "\n".join(repr(d) for d in differences)
    )


def test_passport_migration_round_trip(empty_schema: tuple[Flask, Engine]) -> None:
    from flask_migrate import downgrade
    from models import Passport, PassportSeries, Tenant, User, db
    from sqlalchemy import inspect

    app, engine = empty_schema
    previous = "febde4fa83c5"
    _upgrade(app, previous)
    tid = _insert(engine, "tenants", name="Existing organisation")
    uid = _insert(
        engine, "users", email="existing@migration.test", password_hash="unused"
    )
    _upgrade(app, "head")
    with app.app_context():
        assert db.session.get(Tenant, tid).name == "Existing organisation"
        series = PassportSeries(
            tenant_id=tid, owner_user_id=uid, title="Linked editions"
        )
        db.session.add(series)
        db.session.flush()
        passport = Passport(
            tenant_id=tid,
            owner_user_id=uid,
            series_id=series.id,
            title="2027",
            start_date=dt.date(2027, 1, 1),
            end_date=dt.date(2027, 12, 31),
        )
        db.session.add(passport)
        db.session.commit()
        pid = passport.id
        db.session.delete(series)
        db.session.commit()
        assert db.session.get(Passport, pid).series_id is None
        assert db.session.get(User, uid) is not None
        db.session.remove()
        downgrade(directory=MIGRATIONS, revision=previous)
    assert "passports" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT name FROM tenants WHERE id = :id"), {"id": tid}
            ).scalar_one()
            == "Existing organisation"
        )
    _upgrade(app, "head")
    assert "passport_stamp_photos" in inspect(engine).get_table_names()


def test_passport_builder_round_trip_on_postgres(app: Flask) -> None:
    """Exercise draft consumption and edit fingerprints on the production DB type."""
    import re

    from models import Passport, PassportParticipant, Role, Tenant, TenantUser, User, db
    from werkzeug.test import TestResponse

    with app.app_context():
        tenant = Tenant(name="Passport PostgreSQL club")
        user = User(email="passport-builder@postgres.test", password_hash="unused")
        db.session.add_all([tenant, user])
        db.session.flush()
        db.session.add(
            TenantUser(tenant_id=tenant.id, user_id=user.id, role=Role.PILOT)
        )
        db.session.commit()
        uid = user.id
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = uid

    def form(response: TestResponse, action: str, **values: str) -> dict[str, str]:
        result = {"action": action, **values}
        for name in ("draft", "revision"):
            match = re.search(r'name="' + name + r'" value="([^"]*)"', response.text)
            assert match is not None
            result[name] = match[1]
        return result

    response = client.get("/passports/new")
    response = client.post(
        "/passports/new",
        data=form(response, "add_codes", title="PostgreSQL passport", codes="EBNM"),
    )
    assert response.status_code == 200
    response = client.post("/passports/new", data=form(response, "review"))
    final_form = form(response, "save")
    saved = client.post("/passports/new", data=final_form)
    assert saved.status_code == 302
    assert client.post("/passports/new", data=final_form).status_code == 404
    with app.app_context():
        passport = Passport.query.filter_by(title="PostgreSQL passport").one()
        pid = passport.id
        assert (
            PassportParticipant.query.filter_by(passport_id=pid, user_id=uid)
            .one()
            .accepted_at
            is not None
        )
    path = f"/passports/{pid}/edit"
    response = client.get(path)
    response = client.post(path, data=form(response, "review", end_date="2027-12-31"))
    assert response.status_code == 200
    assert client.post(path, data=form(response, "save")).status_code == 302
    with app.app_context():
        assert db.session.get(Passport, pid).end_date == dt.date(2027, 12, 31)


def test_discovery_home_migration_round_trip(
    empty_schema: tuple[Flask, Engine],
) -> None:
    from flask_migrate import downgrade
    from sqlalchemy import inspect

    app, engine = empty_schema
    previous = "8e862ec0a689"
    _upgrade(app, previous)
    tid = _insert(engine, "tenants", name="Existing home defaults")
    uid = _insert(engine, "users", email="home@migration.test", password_hash="unused")
    aid = _insert(
        engine,
        "aircraft",
        tenant_id=tid,
        registration="OO-HOME",
        make="Cessna",
        model="172",
    )
    profile_id = _insert(engine, "pilot_profiles", user_id=uid)
    _upgrade(app, "head")
    fid = _insert(
        engine,
        "passport_airfield_identities",
        tenant_id=tid,
        source="manual",
        name="Home",
    )
    with engine.begin() as conn:
        assert conn.execute(
            text(
                "SELECT home_airfield_id, cruise_speed_knots FROM aircraft WHERE id=:id"
            ),
            {"id": aid},
        ).one() == (None, None)
        conn.execute(
            text(
                "UPDATE aircraft SET home_airfield_id=:field, cruise_speed_knots=120 WHERE id=:id"
            ),
            {"field": fid, "id": aid},
        )
        conn.execute(
            text("UPDATE pilot_profiles SET home_airfield_id=:field WHERE id=:id"),
            {"field": fid, "id": profile_id},
        )
        conn.execute(
            text("DELETE FROM passport_airfield_identities WHERE id=:id"), {"id": fid}
        )
        assert (
            conn.execute(
                text("SELECT home_airfield_id FROM aircraft WHERE id=:id"), {"id": aid}
            ).scalar_one()
            is None
        )
        assert (
            conn.execute(
                text("SELECT home_airfield_id FROM pilot_profiles WHERE id=:id"),
                {"id": profile_id},
            ).scalar_one()
            is None
        )
    with app.app_context():
        downgrade(directory=MIGRATIONS, revision=previous)
    assert "home_airfield_id" not in {
        column["name"] for column in inspect(engine).get_columns("aircraft")
    }
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT registration FROM aircraft WHERE id=:id"), {"id": aid}
            ).scalar_one()
            == "OO-HOME"
        )
    _upgrade(app, "head")
