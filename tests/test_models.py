"""
Tests for Phase 1 domain models: Aircraft and Component.

These tests exercise the ORM layer directly (no HTTP), using the same
in-memory SQLite database that the route tests use.
"""
import pytest # pyright: ignore[reportMissingImports]
from datetime import date, datetime, timedelta
from sqlalchemy.exc import IntegrityError # pyright: ignore[reportMissingImports]
from models import Aircraft, Component, ComponentType, Role, Tenant, UserInvitation, db # pyright: ignore[reportMissingImports]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_tenant(name="Test Hangar"):
    tenant = Tenant(name=name)
    db.session.add(tenant)
    db.session.flush()
    return tenant


def _make_aircraft(tenant, registration="OO-PNH", make="Cessna", model="172S", year=2005):
    ac = Aircraft(
        tenant_id=tenant.id,
        registration=registration,
        make=make,
        model=model,
        year=year,
    )
    db.session.add(ac)
    db.session.flush()
    return ac


def _make_component(aircraft, type=ComponentType.ENGINE, make="Lycoming",
                    model="IO-360", position=None, serial="L-12345",
                    time_at_install=1200.5):
    comp = Component(
        aircraft_id=aircraft.id,
        type=type,
        make=make,
        model=model,
        position=position,
        serial_number=serial,
        time_at_install=time_at_install,
    )
    db.session.add(comp)
    db.session.flush()
    return comp


# ── Aircraft ──────────────────────────────────────────────────────────────────

class TestAircraftModel:
    def test_create_aircraft(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            db.session.commit()
            assert ac.id is not None

    def test_aircraft_defaults(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            db.session.commit()
            assert ac.is_placeholder is False
            assert ac.created_at is not None

    def test_aircraft_placeholder_flag(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = Aircraft(
                tenant_id=tenant.id,
                registration="PLACEHOLDER",
                make="Unknown",
                model="Unknown",
                is_placeholder=True,
            )
            db.session.add(ac)
            db.session.commit()
            assert ac.is_placeholder is True

    def test_aircraft_year_optional(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = Aircraft(
                tenant_id=tenant.id,
                registration="OO-XYZ",
                make="Piper",
                model="PA-28",
                year=None,
            )
            db.session.add(ac)
            db.session.commit()
            assert ac.year is None

    def test_aircraft_requires_registration(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = Aircraft(tenant_id=tenant.id, make="Cessna", model="172S")
            db.session.add(ac)
            with pytest.raises(IntegrityError):
                db.session.commit()

    def test_aircraft_requires_make(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = Aircraft(tenant_id=tenant.id, registration="OO-ABC", model="172S")
            db.session.add(ac)
            with pytest.raises(IntegrityError):
                db.session.commit()

    def test_aircraft_requires_model(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = Aircraft(tenant_id=tenant.id, registration="OO-ABC", make="Cessna")
            db.session.add(ac)
            with pytest.raises(IntegrityError):
                db.session.commit()

    def test_aircraft_requires_tenant(self, app):
        with app.app_context():
            ac = Aircraft(registration="OO-ABC", make="Cessna", model="172S")
            db.session.add(ac)
            with pytest.raises(IntegrityError):
                db.session.commit()

    def test_aircraft_tenant_relationship(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            db.session.commit()
            fetched = db.session.get(Aircraft, ac.id)
            assert fetched.tenant.name == "Test Hangar"

    def test_tenant_aircraft_backref(self, app):
        with app.app_context():
            tenant = _make_tenant()
            _make_aircraft(tenant, registration="OO-AAA")
            _make_aircraft(tenant, registration="OO-BBB")
            db.session.commit()
            assert len(db.session.get(Tenant, tenant.id).aircraft) == 2

    def test_aircraft_cascade_delete_from_tenant(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            ac_id = ac.id
            db.session.commit()
            db.session.delete(tenant)
            db.session.commit()
            assert db.session.get(Aircraft, ac_id) is None

    def test_multiple_tenants_isolated(self, app):
        with app.app_context():
            t1 = _make_tenant("Hangar A")
            t2 = _make_tenant("Hangar B")
            _make_aircraft(t1, registration="OO-AAA")
            _make_aircraft(t2, registration="OO-BBB")
            db.session.commit()
            assert len(db.session.get(Tenant, t1.id).aircraft) == 1
            assert len(db.session.get(Tenant, t2.id).aircraft) == 1


# ── Component (generic) ───────────────────────────────────────────────────────

class TestComponentModel:
    def test_create_engine_component(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            comp = _make_component(ac, type=ComponentType.ENGINE)
            db.session.commit()
            assert comp.id is not None
            assert comp.type == ComponentType.ENGINE

    def test_create_propeller_component(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            comp = _make_component(ac, type=ComponentType.PROPELLER,
                                   make="Hartzell", model="HC-C2YK", serial="P-001")
            db.session.commit()
            assert comp.type == ComponentType.PROPELLER

    def test_create_avionics_component(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            comp = _make_component(ac, type=ComponentType.AVIONICS,
                                   make="Garmin", model="G1000", serial=None,
                                   time_at_install=None)
            db.session.commit()
            assert comp.type == ComponentType.AVIONICS

    def test_custom_type_string(self, app):
        """Any string is valid as a type — no DB enum constraint."""
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            comp = Component(aircraft_id=ac.id, type="battery",
                             make="Concorde", model="RG-35AXC")
            db.session.add(comp)
            db.session.commit()
            assert comp.type == "battery"

    def test_component_requires_type(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            db.session.commit()
            comp = Component(aircraft_id=ac.id, make="Lycoming", model="IO-360")
            db.session.add(comp)
            with pytest.raises(IntegrityError):
                db.session.commit()

    def test_component_requires_make(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            db.session.commit()
            comp = Component(aircraft_id=ac.id, type=ComponentType.ENGINE, model="IO-360")
            db.session.add(comp)
            with pytest.raises(IntegrityError):
                db.session.commit()

    def test_component_requires_model(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            db.session.commit()
            comp = Component(aircraft_id=ac.id, type=ComponentType.ENGINE, make="Lycoming")
            db.session.add(comp)
            with pytest.raises(IntegrityError):
                db.session.commit()

    def test_serial_optional(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            comp = Component(aircraft_id=ac.id, type=ComponentType.ENGINE,
                             make="Continental", model="O-200", serial_number=None)
            db.session.add(comp)
            db.session.commit()
            assert comp.serial_number is None

    def test_time_at_install_optional(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            comp = Component(aircraft_id=ac.id, type=ComponentType.ENGINE,
                             make="Continental", model="O-200", time_at_install=None)
            db.session.add(comp)
            db.session.commit()
            assert comp.time_at_install is None

    def test_time_at_install_decimal(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            comp = _make_component(ac, time_at_install=1234.5)
            db.session.commit()
            assert float(db.session.get(Component, comp.id).time_at_install) == 1234.5

    def test_position_field(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant, make="Piper", model="PA-44 Seminole")
            left  = _make_component(ac, model="O-360-E1A6D",  serial="E-L", position="left")
            right = _make_component(ac, model="LO-360-E1A6D", serial="E-R", position="right")
            db.session.commit()
            assert db.session.get(Component, left.id).position == "left"
            assert db.session.get(Component, right.id).position == "right"

    def test_position_optional(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            comp = _make_component(ac, position=None)
            db.session.commit()
            assert comp.position is None

    def test_installed_at_and_removed_at(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            comp = Component(
                aircraft_id=ac.id,
                type=ComponentType.ENGINE,
                make="Lycoming", model="IO-360",
                installed_at=date(2020, 1, 15),
                removed_at=date(2024, 6, 1),
            )
            db.session.add(comp)
            db.session.commit()
            fetched = db.session.get(Component, comp.id)
            assert fetched.installed_at == date(2020, 1, 15)
            assert fetched.removed_at == date(2024, 6, 1)

    def test_removed_at_null_means_installed(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            comp = _make_component(ac)
            db.session.commit()
            assert db.session.get(Component, comp.id).removed_at is None

    def test_extras_json(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            comp = Component(
                aircraft_id=ac.id,
                type=ComponentType.PROPELLER,
                make="Hartzell", model="HC-C2YK",
                extras={"blade_count": 2, "diameter_in": 76, "variable_pitch": True},
            )
            db.session.add(comp)
            db.session.commit()
            fetched = db.session.get(Component, comp.id)
            assert fetched.extras["blade_count"] == 2
            assert fetched.extras["variable_pitch"] is True

    def test_extras_null_by_default(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            comp = _make_component(ac)
            db.session.commit()
            assert comp.extras is None

    def test_component_aircraft_relationship(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            comp = _make_component(ac)
            db.session.commit()
            assert db.session.get(Component, comp.id).aircraft.registration == "OO-PNH"

    def test_aircraft_components_backref(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            _make_component(ac, type=ComponentType.ENGINE,    serial="E-001")
            _make_component(ac, type=ComponentType.PROPELLER, serial="P-001",
                            make="Hartzell", model="HC-C2YK")
            db.session.commit()
            assert len(db.session.get(Aircraft, ac.id).components) == 2

    def test_component_cascade_delete_from_aircraft(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            comp = _make_component(ac)
            comp_id = comp.id
            db.session.commit()
            db.session.delete(ac)
            db.session.commit()
            assert db.session.get(Component, comp_id) is None

    def test_created_at_set(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            comp = _make_component(ac)
            db.session.commit()
            assert comp.created_at is not None


# ── Cross-model ───────────────────────────────────────────────────────────────

class TestAircraftWithComponents:
    def test_full_aircraft_with_engine_and_propeller(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            _make_component(ac, type=ComponentType.ENGINE)
            _make_component(ac, type=ComponentType.PROPELLER,
                            make="Hartzell", model="HC-C2YK", serial="P-001")
            db.session.commit()
            assert len(db.session.get(Aircraft, ac.id).components) == 2

    def test_multi_engine_aircraft(self, app):
        """A twin-engine aircraft has two engine components with distinct positions."""
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant, make="Piper", model="PA-44 Seminole")
            _make_component(ac, model="O-360-E1A6D",  serial="E-L", position="left")
            _make_component(ac, model="LO-360-E1A6D", serial="E-R", position="right")
            db.session.commit()
            engines = [c for c in db.session.get(Aircraft, ac.id).components
                       if c.type == ComponentType.ENGINE]
            assert len(engines) == 2
            assert {e.position for e in engines} == {"left", "right"}

    def test_component_history_same_aircraft(self, app):
        """Replacing an engine keeps the old record (removed_at set) and adds a new one."""
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            old_eng = Component(
                aircraft_id=ac.id, type=ComponentType.ENGINE,
                make="Lycoming", model="IO-360", serial_number="OLD-001",
                installed_at=date(2018, 3, 1), removed_at=date(2023, 9, 1),
            )
            new_eng = Component(
                aircraft_id=ac.id, type=ComponentType.ENGINE,
                make="Lycoming", model="IO-360", serial_number="NEW-002",
                installed_at=date(2023, 9, 1),
            )
            db.session.add_all([old_eng, new_eng])
            db.session.commit()

            all_engines = [c for c in db.session.get(Aircraft, ac.id).components
                           if c.type == ComponentType.ENGINE]
            assert len(all_engines) == 2
            current    = [e for e in all_engines if e.removed_at is None]
            historical = [e for e in all_engines if e.removed_at is not None]
            assert len(current) == 1
            assert len(historical) == 1
            assert current[0].serial_number == "NEW-002"

    def test_deleting_aircraft_cascades_to_all_components(self, app):
        with app.app_context():
            tenant = _make_tenant()
            ac = _make_aircraft(tenant)
            eng  = _make_component(ac, type=ComponentType.ENGINE)
            prop = _make_component(ac, type=ComponentType.PROPELLER,
                                   make="Hartzell", model="HC-C2YK", serial="P-001")
            ac_id, eng_id, prop_id = ac.id, eng.id, prop.id
            db.session.commit()

            db.session.delete(ac)
            db.session.commit()

            assert db.session.get(Aircraft, ac_id) is None
            assert db.session.get(Component, eng_id) is None
            assert db.session.get(Component, prop_id) is None

    def test_component_type_all_set(self, app):
        """ComponentType.ALL contains the expected built-in types."""
        with app.app_context():
            assert ComponentType.ENGINE    in ComponentType.ALL
            assert ComponentType.PROPELLER in ComponentType.ALL
            assert ComponentType.AVIONICS  in ComponentType.ALL


# ── UserInvitation.is_expired — naive datetime branch ─────────────────────────

class TestUserInvitationIsExpired:
    def test_is_expired_with_naive_past_datetime(self, app):
        """SQLite returns naive datetimes after a DB round-trip; covers models.py:103."""
        with app.app_context():
            tenant = _make_tenant()
            inv = UserInvitation(
                tenant_id=tenant.id,
                role=Role.PILOT,
                expires_at=datetime.utcnow() - timedelta(hours=1),
            )
            db.session.add(inv)
            db.session.commit()
            # After commit, SQLAlchemy reloads from SQLite → naive datetime
            assert inv.is_expired is True

    def test_is_not_expired_with_naive_future_datetime(self, app):
        with app.app_context():
            tenant = _make_tenant()
            inv = UserInvitation(
                tenant_id=tenant.id,
                role=Role.PILOT,
                expires_at=datetime.utcnow() + timedelta(days=1),
            )
            db.session.add(inv)
            db.session.commit()
            assert inv.is_expired is False

    def test_is_expired_with_tz_aware_past_datetime(self, app):
        """In-memory object with tz-aware datetime covers models.py:104."""
        from datetime import timezone
        with app.app_context():
            tenant = _make_tenant()
            inv = UserInvitation(
                tenant_id=tenant.id,
                role=Role.PILOT,
                expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            )
            # Check is_expired before commit so expires_at is still tz-aware in memory
            assert inv.is_expired is True
