"""
Development seed data.
Creates a default admin user + tenant and a sample fleet so the app is
usable immediately after a fresh dev install without going through the
setup wizard.

Each phase of the implementation plan adds to this seed so the dev
environment always reflects the full feature set built so far.

Credentials printed to container logs on first run.
Never loaded in production.
"""

import bcrypt # pyright: ignore[reportMissingImports]
import pyotp # pyright: ignore[reportMissingImports]
from datetime import date

from models import Aircraft, Component, ComponentType, FlightEntry, MaintenanceTrigger, Role, Tenant, TenantUser, TriggerType, User, db

# Fixed TOTP secret for the dev seed user — add this once to your
# authenticator app and it will always work across DB resets.
_DEV_TOTP_SECRET = "JBSWY3DPEHPK3PXP"
_DEV_EMAIL = "admin@openhangar.dev"
_DEV_PASSWORD = "openhangar-dev-1"


def seed():
    # ── Tenant & user ─────────────────────────────────────────────────────────
    tenant = Tenant(name="Dev Hangar")
    db.session.add(tenant)
    db.session.flush()

    user = User(
        email=_DEV_EMAIL,
        password_hash=bcrypt.hashpw(_DEV_PASSWORD.encode(), bcrypt.gensalt()).decode(),
        totp_secret=_DEV_TOTP_SECRET,
        is_active=True,
    )
    db.session.add(user)
    db.session.flush()

    db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN))

    # ── Phase 1: sample fleet ─────────────────────────────────────────────────

    # Single-engine: Cessna 172S
    c172 = Aircraft(
        tenant_id=tenant.id,
        registration="OO-PNH",
        make="Cessna",
        model="172S Skyhawk",
        year=2004,
    )
    db.session.add(c172)
    db.session.flush()

    db.session.add(Component(
        aircraft_id=c172.id,
        type=ComponentType.ENGINE,
        make="Lycoming",
        model="IO-360-L2A",
        serial_number="L-23456-51A",
        time_at_install=312.0,
        installed_at=date(2019, 6, 15),
        extras={"tbo_hours": 2000},
    ))
    db.session.add(Component(
        aircraft_id=c172.id,
        type=ComponentType.PROPELLER,
        make="McCauley",
        model="1C160/DTM7557",
        serial_number="MC-880342",
        time_at_install=312.0,
        installed_at=date(2019, 6, 15),
        extras={"blade_count": 2, "diameter_in": 76},
    ))

    # Multi-engine: Piper PA-44 Seminole
    seminole = Aircraft(
        tenant_id=tenant.id,
        registration="OO-ABC",
        make="Piper",
        model="PA-44-180 Seminole",
        year=1998,
    )
    db.session.add(seminole)
    db.session.flush()

    for position, model, serial in [
        ("left",  "O-360-E1A6D",  "L-54321-27A"),
        ("right", "LO-360-E1A6D", "L-54322-27A"),
    ]:
        db.session.add(Component(
            aircraft_id=seminole.id,
            type=ComponentType.ENGINE,
            position=position,
            make="Lycoming",
            model=model,
            serial_number=serial,
            time_at_install=780.0,
            installed_at=date(2015, 3, 1),
            extras={"tbo_hours": 2000},
        ))

    for position, serial in [("left", "P-11001"), ("right", "P-11002")]:
        db.session.add(Component(
            aircraft_id=seminole.id,
            type=ComponentType.PROPELLER,
            position=position,
            make="Hartzell",
            model="HC-C2YK-1BF",
            serial_number=serial,
            time_at_install=780.0,
            installed_at=date(2015, 3, 1),
            extras={"blade_count": 2, "variable_pitch": True},
        ))

    # ── Phase 3: flight history ───────────────────────────────────────────────

    # OO-PNH — 7 Belgian/European hops starting from hobbs 312.0
    for flight_date, dep, arr, hs, he in [
        (date(2020, 3, 14), "EBOS", "EBBR", 312.0, 313.5),
        (date(2020, 5,  2), "EBBR", "ELLX", 313.5, 315.2),
        (date(2020, 7, 19), "ELLX", "EDDM", 315.2, 318.7),
        (date(2020, 9,  5), "EDDM", "EBOS", 318.7, 322.1),
        (date(2021, 1, 12), "EBOS", "EHAM", 322.1, 323.9),
        (date(2021, 4,  3), "EHAM", "EBKT", 323.9, 324.8),
        (date(2021, 8, 27), "EBKT", "LFQQ", 324.8, 325.6),
    ]:
        db.session.add(FlightEntry(
            aircraft_id=c172.id,
            date=flight_date,
            departure_icao=dep,
            arrival_icao=arr,
            hobbs_start=hs,
            hobbs_end=he,
        ))

    # OO-ABC — 4 hops starting from hobbs 780.0
    for flight_date, dep, arr, hs, he in [
        (date(2020, 4, 10), "EBOS", "EHRD", 780.0, 781.4),
        (date(2020, 6, 22), "EHRD", "EBBR", 781.4, 782.2),
        (date(2020, 11, 15), "EBBR", "ELLX", 782.2, 783.5),
        (date(2021, 2,  8), "ELLX", "EBOS", 783.5, 784.8),
    ]:
        db.session.add(FlightEntry(
            aircraft_id=seminole.id,
            date=flight_date,
            departure_icao=dep,
            arrival_icao=arr,
            hobbs_start=hs,
            hobbs_end=he,
        ))

    # ── Phase 4: maintenance triggers ─────────────────────────────────────────
    # OO-PNH current hobbs ≈ 325.6 h  →  OK / due_soon / overdue demonstrated

    # OK  — annual inspection, due 2026-06-15 (58 days away > 30-day threshold)
    db.session.add(MaintenanceTrigger(
        aircraft_id=c172.id,
        name="Annual inspection (ARC)",
        trigger_type=TriggerType.CALENDAR,
        due_date=date(2026, 6, 15),
        interval_days=365,
        notes="EASA Form 1 required",
    ))
    # DUE SOON — 50 h oil change, due at 330.0 h (4.4 h remaining < 5.0 h warn threshold)
    db.session.add(MaintenanceTrigger(
        aircraft_id=c172.id,
        name="50 h oil & filter change",
        trigger_type=TriggerType.HOURS,
        due_hobbs=330.0,
        interval_hours=50,
    ))
    # OVERDUE — transponder biennial check, due date in the past
    db.session.add(MaintenanceTrigger(
        aircraft_id=c172.id,
        name="Transponder biennial check",
        trigger_type=TriggerType.CALENDAR,
        due_date=date(2025, 12, 1),
        interval_days=730,
    ))

    # OO-ABC current hobbs ≈ 784.8 h  →  yellow (due_soon, no overdue)

    # OK  — annual inspection, far in the future
    db.session.add(MaintenanceTrigger(
        aircraft_id=seminole.id,
        name="Annual inspection (ARC)",
        trigger_type=TriggerType.CALENDAR,
        due_date=date(2027, 3, 1),
        interval_days=365,
    ))
    # DUE SOON — left engine oil change, due at 789.0 h (4.2 h remaining < 5.0 h warn threshold)
    db.session.add(MaintenanceTrigger(
        aircraft_id=seminole.id,
        name="Left engine 50 h oil change",
        trigger_type=TriggerType.HOURS,
        due_hobbs=789.0,
        interval_hours=50,
    ))
    # OK — right prop 500 h inspection, well in the future
    db.session.add(MaintenanceTrigger(
        aircraft_id=seminole.id,
        name="Right propeller 500 h inspection",
        trigger_type=TriggerType.HOURS,
        due_hobbs=1280.0,
        interval_hours=500,
        notes="Hartzell SB HC-SB-61-253 compliance",
    ))

    # ── Phase 5: green aircraft ───────────────────────────────────────────────
    # OO-GRN — all maintenance OK → dashboard green status

    robin = Aircraft(
        tenant_id=tenant.id,
        registration="OO-GRN",
        make="Robin",
        model="DR-401/155CDI",
        year=2020,
    )
    db.session.add(robin)
    db.session.flush()

    # Continental CD-155: 4-cylinder turbodiesel, Jet-A1, 155 hp, TBO 2400 h
    db.session.add(Component(
        aircraft_id=robin.id,
        type=ComponentType.ENGINE,
        make="Continental",
        model="CD-155",
        serial_number="CD155-20341",
        time_at_install=0.0,
        installed_at=date(2020, 3, 12),
        extras={"tbo_hours": 2400, "fuel_type": "Jet-A1", "displacement_cc": 1991},
    ))
    # MT-Propeller MTV-6-A-C: 3-blade constant-speed, laminated wood/composite,
    # diameter 190 cm (74.8 in), hydraulic pitch control, TBO 2400 h (shared with engine)
    db.session.add(Component(
        aircraft_id=robin.id,
        type=ComponentType.PROPELLER,
        make="MT-Propeller",
        model="MTV-6-A-C/C190-59",
        serial_number="MTV6-20187",
        time_at_install=0.0,
        installed_at=date(2020, 3, 12),
        extras={
            "blade_count": 3,
            "diameter_cm": 190,
            "variable_pitch": True,
            "material": "laminated wood / composite",
            "tbo_hours": 2400,
        },
    ))
    # 2 flights so total_hobbs is defined (OK hobbs-trigger evaluation)
    for flight_date, dep, arr, hs, he in [
        (date(2023, 6,  5), "EBGT", "EBOS", 200.0, 201.2),
        (date(2023, 9, 17), "EBOS", "EBGT", 201.2, 202.0),
    ]:
        db.session.add(FlightEntry(
            aircraft_id=robin.id,
            date=flight_date,
            departure_icao=dep,
            arrival_icao=arr,
            hobbs_start=hs,
            hobbs_end=he,
        ))
    # OK — annual inspection due well in the future
    db.session.add(MaintenanceTrigger(
        aircraft_id=robin.id,
        name="Annual inspection (ARC)",
        trigger_type=TriggerType.CALENDAR,
        due_date=date(2027, 3, 12),
        interval_days=365,
    ))
    # OK — 100 h diesel engine inspection, well above current hobbs (202.0 h)
    db.session.add(MaintenanceTrigger(
        aircraft_id=robin.id,
        name="100 h engine inspection",
        trigger_type=TriggerType.HOURS,
        due_hobbs=300.0,
        interval_hours=100,
        notes="Continental CD-155 mandatory 100 h check",
    ))

    db.session.commit()

    # ── Log credentials ───────────────────────────────────────────────────────
    totp_uri = pyotp.TOTP(_DEV_TOTP_SECRET).provisioning_uri(
        name=_DEV_EMAIL, issuer_name="OpenHangar"
    )

    print("=" * 60)
    print("  DEV SEED CREDENTIALS")
    print(f"  Email    : {_DEV_EMAIL}")
    print(f"  Password : {_DEV_PASSWORD}")
    print(f"  TOTP key : {_DEV_TOTP_SECRET}")
    print(f"  TOTP URI : {totp_uri}")
    print("=" * 60)
    print("  SAMPLE FLEET")
    print("  OO-PNH  Cessna 172S        — status: OVERDUE  (7 flights, 3 mx)")
    print("  OO-ABC  Piper PA-44        — status: DUE SOON (4 flights, 3 mx)")
    print("  OO-GRN  Robin DR-401/155CDI — status: OK       (2 flights, 2 mx)")
    print("=" * 60)
