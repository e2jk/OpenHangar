"""
Shared seed helpers — fleet data used by both dev_seed and demo_seed.

Add richer data here as new phases are implemented; both seeds pick it up
automatically without any further changes.
"""

from datetime import date

from models import (
    Aircraft, Component, ComponentType,
    FlightEntry, MaintenanceTrigger, TriggerType, db,
)


def seed_fleet(tenant_id: int) -> None:
    """
    Populate one tenant with the standard sample fleet.

    Called once by dev_seed and N times (once per slot) by demo_seed.
    Extend this function as each phase adds new data — both environments
    stay in sync automatically.
    """

    # ── Aircraft 1: Cessna 172S — status OVERDUE ──────────────────────────────
    c172 = Aircraft(
        tenant_id=tenant_id,
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

    for flight_date, dep, arr, hs, he, ts, te, pilot, notes in [
        (date(2020, 3, 14), "EBOS", "EBBR", 312.0, 313.5, 1820.0, 1821.3, "J. Klein",   "Smooth flight, VFR"),
        (date(2020, 5,  2), "EBBR", "ELLX", 313.5, 315.2, 1821.3, 1822.8, "J. Klein",   None),
        (date(2020, 7, 19), "ELLX", "EDDM", 315.2, 318.7, 1822.8, 1826.0, "M. Dupont",  "Cross-country, light turbulence over Vosges"),
        (date(2020, 9,  5), "EDDM", "EBOS", 318.7, 322.1, 1826.0, 1829.1, "M. Dupont",  None),
        (date(2021, 1, 12), "EBOS", "EHAM", 322.1, 323.9, 1829.1, 1830.7, "J. Klein",   "IFR return, vectors to ILS 18R"),
        (date(2021, 4,  3), "EHAM", "EBKT", 323.9, 324.8, 1830.7, 1831.5, "J. Klein",   None),
        (date(2021, 8, 27), "EBKT", "LFQQ", 324.8, 325.6, 1831.5, 1832.2, "S. Martin",  "Training flight, touch-and-go practice"),
    ]:
        db.session.add(FlightEntry(
            aircraft_id=c172.id, date=flight_date,
            departure_icao=dep, arrival_icao=arr,
            hobbs_start=hs, hobbs_end=he,
            tach_start=ts, tach_end=te,
            pilot=pilot, notes=notes,
        ))

    # OK — annual inspection
    db.session.add(MaintenanceTrigger(
        aircraft_id=c172.id,
        name="Annual inspection (ARC)",
        trigger_type=TriggerType.CALENDAR,
        due_date=date(2026, 6, 15),
        interval_days=365,
        notes="EASA Form 1 required",
    ))
    # DUE SOON — 50 h oil change (4.4 h remaining < 5.0 h warn threshold)
    db.session.add(MaintenanceTrigger(
        aircraft_id=c172.id,
        name="50 h oil & filter change",
        trigger_type=TriggerType.HOURS,
        due_hobbs=330.0,
        interval_hours=50,
    ))
    # OVERDUE — transponder biennial check past due
    db.session.add(MaintenanceTrigger(
        aircraft_id=c172.id,
        name="Transponder biennial check",
        trigger_type=TriggerType.CALENDAR,
        due_date=date(2025, 12, 1),
        interval_days=730,
    ))

    # ── Aircraft 2: Piper PA-44 Seminole — status DUE SOON ────────────────────
    seminole = Aircraft(
        tenant_id=tenant_id,
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
            aircraft_id=seminole.id, type=ComponentType.ENGINE,
            position=position, make="Lycoming", model=model,
            serial_number=serial, time_at_install=780.0,
            installed_at=date(2015, 3, 1),
            extras={"tbo_hours": 2000},
        ))
    for position, serial in [("left", "P-11001"), ("right", "P-11002")]:
        db.session.add(Component(
            aircraft_id=seminole.id, type=ComponentType.PROPELLER,
            position=position, make="Hartzell", model="HC-C2YK-1BF",
            serial_number=serial, time_at_install=780.0,
            installed_at=date(2015, 3, 1),
            extras={"blade_count": 2, "variable_pitch": True},
        ))

    for flight_date, dep, arr, hs, he, ts, te, pilot, notes in [
        (date(2020, 4, 10), "EBOS", "EHRD", 780.0, 781.4, 780.0, 781.2, "J. Klein",  "First flight after annual"),
        (date(2020, 6, 22), "EHRD", "EBBR", 781.4, 782.2, 781.2, 781.9, "J. Klein",  None),
        (date(2020, 11, 15), "EBBR", "ELLX", 782.2, 783.5, 781.9, 783.1, "M. Dupont", "Night rating exercise"),
        (date(2021, 2,  8), "ELLX", "EBOS", 783.5, 784.8, 783.1, 784.3, "J. Klein",  None),
    ]:
        db.session.add(FlightEntry(
            aircraft_id=seminole.id, date=flight_date,
            departure_icao=dep, arrival_icao=arr,
            hobbs_start=hs, hobbs_end=he,
            tach_start=ts, tach_end=te,
            pilot=pilot, notes=notes,
        ))

    db.session.add(MaintenanceTrigger(
        aircraft_id=seminole.id,
        name="Annual inspection (ARC)",
        trigger_type=TriggerType.CALENDAR,
        due_date=date(2027, 3, 1),
        interval_days=365,
    ))
    db.session.add(MaintenanceTrigger(
        aircraft_id=seminole.id,
        name="Left engine 50 h oil change",
        trigger_type=TriggerType.HOURS,
        due_hobbs=789.0,
        interval_hours=50,
    ))
    db.session.add(MaintenanceTrigger(
        aircraft_id=seminole.id,
        name="Right propeller 500 h inspection",
        trigger_type=TriggerType.HOURS,
        due_hobbs=1280.0,
        interval_hours=500,
        notes="Hartzell SB HC-SB-61-253 compliance",
    ))

    # ── Aircraft 3: Robin DR-401 — status OK (green) ──────────────────────────
    robin = Aircraft(
        tenant_id=tenant_id,
        registration="OO-GRN",
        make="Robin",
        model="DR-401/155CDI",
        year=2020,
    )
    db.session.add(robin)
    db.session.flush()

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
    for flight_date, dep, arr, hs, he, ts, te, pilot, notes in [
        (date(2023, 6,  5), "EBGT", "EBOS", 200.0, 201.2, 200.0, 201.1, "J. Klein",  "Delivery flight from overhaul shop"),
        (date(2023, 9, 17), "EBOS", "EBGT", 201.2, 202.0, 201.1, 201.8, "J. Klein",  None),
    ]:
        db.session.add(FlightEntry(
            aircraft_id=robin.id, date=flight_date,
            departure_icao=dep, arrival_icao=arr,
            hobbs_start=hs, hobbs_end=he,
            tach_start=ts, tach_end=te,
            pilot=pilot, notes=notes,
        ))
    db.session.add(MaintenanceTrigger(
        aircraft_id=robin.id,
        name="Annual inspection (ARC)",
        trigger_type=TriggerType.CALENDAR,
        due_date=date(2027, 3, 12),
        interval_days=365,
    ))
    db.session.add(MaintenanceTrigger(
        aircraft_id=robin.id,
        name="100 h engine inspection",
        trigger_type=TriggerType.HOURS,
        due_hobbs=300.0,
        interval_hours=100,
        notes="Continental CD-155 mandatory 100 h check",
    ))
