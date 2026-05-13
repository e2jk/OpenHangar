"""
Shared seed helpers — fleet data used by both dev_seed and demo_seed.

Add richer data here as new phases are implemented; both seeds pick it up
automatically without any further changes.

All hardcoded dates are expressed relative to _SEED_REF_DATE (the "today"
the data was designed around). At runtime, _shift() maps them to real dates
so the data always looks recent regardless of when the seed is executed.
"""

import mimetypes
import os
import shutil
import uuid
from datetime import date, datetime, time, timedelta, timezone

from models import (
    Aircraft, BackupRecord, Component, ComponentType, CrewRole,
    Document,
    Expense, ExpenseType,
    FlightCrew, FlightEntry, MaintenanceTrigger, PilotLogbookEntry, PilotProfile,
    Snag, ShareToken, TriggerType, WeightBalanceConfig, WeightBalanceEntry, WeightBalanceStation, db,
)

# Placeholder seed documents bundled in the repo
_SEED_DOCS_DIR = os.path.join(os.path.dirname(__file__), "dev_seed_docs")

# The calendar "today" assumed when writing the seed data.
# All dates are shifted at runtime so they stay relative to the actual date.
_SEED_REF_DATE = date(2026, 5, 9)


def seed_fleet(tenant_id: int) -> list:
    """
    Populate one tenant with the standard sample fleet.

    Called once by dev_seed and N times (once per slot) by demo_seed.
    Extend this function as each phase adds new data — both environments
    stay in sync automatically.
    """
    _s = date.today() - _SEED_REF_DATE          # date shift (timedelta)

    def _d(d: date) -> date:
        return d + _s

    def _dt(dt: datetime) -> datetime:
        return dt + _s

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
        installed_at=_d(date(2019, 6, 15)),
        extras={"tbo_hours": 2000},
    ))
    db.session.add(Component(
        aircraft_id=c172.id,
        type=ComponentType.PROPELLER,
        make="McCauley",
        model="1C160/DTM7557",
        serial_number="MC-880342",
        time_at_install=312.0,
        installed_at=_d(date(2019, 6, 15)),
        extras={"blade_count": 2, "diameter_in": 76},
    ))

    for flight_date, dep, arr, hs, he, ts, te, pilot, notes, nature, dep_t, arr_t, ldg, copilot in [
        (_d(date(2020, 3, 14)), "EBOS", "EBBR", 312.0, 313.5, 1820.0, 1821.3, "J. Klein",  "Smooth flight, VFR",                          "Local flight",  time(9, 15),  time(10, 45), 1,  None),
        (_d(date(2020, 5,  2)), "EBBR", "ELLX", 313.5, 315.2, 1821.3, 1822.8, "J. Klein",  None,                                          "Navigation",    time(13, 0),  time(14, 42), 1,  None),
        (_d(date(2020, 7, 19)), "ELLX", "EDDM", 315.2, 318.7, 1822.8, 1826.0, "J. Klein",  "Cross-country, light turbulence over Vosges", "Cross-country", time(8, 30),  time(12, 0),  1,  "M. Dupont"),
        (_d(date(2020, 9,  5)), "EDDM", "EBOS", 318.7, 322.1, 1826.0, 1829.1, "M. Dupont", None,                                          "Navigation",    time(10, 0),  time(13, 24), 1,  None),
        (_d(date(2021, 1, 12)), "EBOS", "EHAM", 322.1, 323.9, 1829.1, 1830.7, "J. Klein",  "IFR return, vectors to ILS 18R",              "IFR practice",  time(14, 45), time(16, 33), 1,  None),
        (_d(date(2021, 4,  3)), "EHAM", "EBKT", 323.9, 324.8, 1830.7, 1831.5, "J. Klein",  None,                                          "Navigation",    None,         None,         1,  None),
        (_d(date(2021, 8, 27)), "EBKT", "LFQQ", 324.8, 325.6, 1831.5, 1832.2, "S. Martin", "Training flight, touch-and-go practice",      "Training",      time(10, 0),  time(10, 48), 6,  None),
        (_d(date(2026, 5,  6)), "EBOS", "EHRD", 325.6, 327.1, 1832.2, 1833.5, "J. Klein",  "Local VFR to EHRD",                           "Navigation",    time(9, 30),  time(11, 0),  1,  None),
    ]:
        fe = FlightEntry(
            aircraft_id=c172.id, date=flight_date,
            departure_icao=dep, arrival_icao=arr,
            flight_time_counter_start=hs, flight_time_counter_end=he,
            engine_time_counter_start=ts, engine_time_counter_end=te,
            flight_time=round(he - hs, 1),
            nature_of_flight=nature, departure_time=dep_t, arrival_time=arr_t,
            landing_count=ldg, notes=notes,
        )
        db.session.add(fe)
        db.session.flush()
        db.session.add(FlightCrew(flight_id=fe.id, name=pilot, role=CrewRole.PIC, sort_order=0))
        if copilot:
            db.session.add(FlightCrew(flight_id=fe.id, name=copilot, role=CrewRole.COPILOT, sort_order=1))

    # OK — annual inspection
    db.session.add(MaintenanceTrigger(
        aircraft_id=c172.id,
        name="Annual inspection (ARC)",
        trigger_type=TriggerType.CALENDAR,
        due_date=_d(date(2026, 6, 15)),
        interval_days=365,
        notes="EASA Form 1 required",
    ))
    # DUE SOON — 50 h oil change (4.4 h remaining < 5.0 h warn threshold)
    db.session.add(MaintenanceTrigger(
        aircraft_id=c172.id,
        name="50 h oil & filter change",
        trigger_type=TriggerType.HOURS,
        due_engine_hours=330.0,
        interval_hours=50,
    ))
    # OVERDUE — transponder biennial check past due
    db.session.add(MaintenanceTrigger(
        aircraft_id=c172.id,
        name="Transponder biennial check",
        trigger_type=TriggerType.CALENDAR,
        due_date=_d(date(2025, 12, 1)),
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
            installed_at=_d(date(2015, 3, 1)),
            extras={"tbo_hours": 2000},
        ))
    for position, serial in [("left", "P-11001"), ("right", "P-11002")]:
        db.session.add(Component(
            aircraft_id=seminole.id, type=ComponentType.PROPELLER,
            position=position, make="Hartzell", model="HC-C2YK-1BF",
            serial_number=serial, time_at_install=780.0,
            installed_at=_d(date(2015, 3, 1)),
            extras={"blade_count": 2, "variable_pitch": True},
        ))

    for flight_date, dep, arr, hs, he, ts, te, pilot, notes, nature, ldg in [
        (_d(date(2020, 4, 10)), "EBOS", "EHRD", 780.0, 781.4, 780.0, 781.2, "J. Klein",  "First flight after annual", "Air test",   1),
        (_d(date(2020, 6, 22)), "EHRD", "EBBR", 781.4, 782.2, 781.2, 781.9, "J. Klein",  None,                        "Navigation", 1),
        (_d(date(2020, 11, 15)), "EBBR", "ELLX", 782.2, 783.5, 781.9, 783.1, "M. Dupont", "Night rating exercise",    "Night flight", 1),
        (_d(date(2021, 2,  8)), "ELLX", "EBOS", 783.5, 784.8, 783.1, 784.3, "J. Klein",  None,                        "Navigation", 1),
    ]:
        fe = FlightEntry(
            aircraft_id=seminole.id, date=flight_date,
            departure_icao=dep, arrival_icao=arr,
            flight_time_counter_start=hs, flight_time_counter_end=he,
            engine_time_counter_start=ts, engine_time_counter_end=te,
            flight_time=round(he - hs, 1),
            nature_of_flight=nature, landing_count=ldg, notes=notes,
        )
        db.session.add(fe)
        db.session.flush()
        db.session.add(FlightCrew(flight_id=fe.id, name=pilot, role=CrewRole.PIC, sort_order=0))

    db.session.add(MaintenanceTrigger(
        aircraft_id=seminole.id,
        name="Annual inspection (ARC)",
        trigger_type=TriggerType.CALENDAR,
        due_date=_d(date(2027, 3, 1)),
        interval_days=365,
    ))
    db.session.add(MaintenanceTrigger(
        aircraft_id=seminole.id,
        name="Left engine 50 h oil change",
        trigger_type=TriggerType.HOURS,
        due_engine_hours=789.0,
        interval_hours=50,
    ))
    db.session.add(MaintenanceTrigger(
        aircraft_id=seminole.id,
        name="Right propeller 500 h inspection",
        trigger_type=TriggerType.HOURS,
        due_engine_hours=1280.0,
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
        installed_at=_d(date(2020, 3, 12)),
        extras={"tbo_hours": 2400, "fuel_type": "Jet-A1", "displacement_cc": 1991},
    ))
    db.session.add(Component(
        aircraft_id=robin.id,
        type=ComponentType.PROPELLER,
        make="MT-Propeller",
        model="MTV-6-A-C/C190-59",
        serial_number="MTV6-20187",
        time_at_install=0.0,
        installed_at=_d(date(2020, 3, 12)),
        extras={
            "blade_count": 3,
            "diameter_cm": 190,
            "variable_pitch": True,
            "material": "laminated wood / composite",
            "tbo_hours": 2400,
        },
    ))
    for flight_date, dep, arr, hs, he, ts, te, pilot, notes, nature in [
        (_d(date(2023, 6,  5)), "EBGT", "EBOS", 200.0, 201.2, 200.0, 201.1, "J. Klein", "Delivery flight from overhaul shop", "Ferry flight"),
        (_d(date(2023, 9, 17)), "EBOS", "EBGT", 201.2, 202.0, 201.1, 201.8, "J. Klein", None,                                 "Local flight"),
    ]:
        fe = FlightEntry(
            aircraft_id=robin.id, date=flight_date,
            departure_icao=dep, arrival_icao=arr,
            flight_time_counter_start=hs, flight_time_counter_end=he,
            engine_time_counter_start=ts, engine_time_counter_end=te,
            flight_time=round(he - hs, 1),
            nature_of_flight=nature, landing_count=1, notes=notes,
        )
        db.session.add(fe)
        db.session.flush()
        db.session.add(FlightCrew(flight_id=fe.id, name=pilot, role=CrewRole.PIC, sort_order=0))
    db.session.add(MaintenanceTrigger(
        aircraft_id=robin.id,
        name="Annual inspection (ARC)",
        trigger_type=TriggerType.CALENDAR,
        due_date=_d(date(2027, 3, 12)),
        interval_days=365,
    ))
    db.session.add(MaintenanceTrigger(
        aircraft_id=robin.id,
        name="100 h engine inspection",
        trigger_type=TriggerType.HOURS,
        due_engine_hours=300.0,
        interval_hours=100,
        notes="Continental CD-155 mandatory 100 h check",
    ))

    # ── Aircraft 4: Jodel DR-1050 — tach-only aircraft (no flight time counter) ─
    jodel = Aircraft(
        tenant_id=tenant_id,
        registration="OO-TCH",
        make="Jodel",
        model="DR-1050 Ambassadeur",
        year=1962,
        has_flight_counter=False,
        flight_counter_offset=0.0,
    )
    db.session.add(jodel)
    db.session.flush()

    db.session.add(Component(
        aircraft_id=jodel.id,
        type=ComponentType.ENGINE,
        make="Continental",
        model="C90-14F",
        serial_number="C90-12345",
        time_at_install=1450.0,
        installed_at=_d(date(2010, 4, 1)),
        extras={"tbo_hours": 1800},
    ))
    for flight_date, dep, arr, ts, te, notes, nature in [
        (_d(date(2024, 3, 10)), "EBGT", "EBOS", 1500.0, 1501.2, "Spring flying", "Local flight"),
        (_d(date(2024, 5, 18)), "EBOS", "EBGT", 1501.2, 1502.0, None,            "Local flight"),
    ]:
        fe = FlightEntry(
            aircraft_id=jodel.id, date=flight_date,
            departure_icao=dep, arrival_icao=arr,
            flight_time_counter_start=None, flight_time_counter_end=None,
            engine_time_counter_start=ts, engine_time_counter_end=te,
            flight_time=round((te - ts) - jodel.flight_counter_offset, 1),
            nature_of_flight=nature, landing_count=1, notes=notes,
        )
        db.session.add(fe)
        db.session.flush()
        db.session.add(FlightCrew(flight_id=fe.id, name="J. Klein", role=CrewRole.PIC, sort_order=0))
    db.session.add(MaintenanceTrigger(
        aircraft_id=jodel.id,
        name="Annual inspection (ARC)",
        trigger_type=TriggerType.CALENDAR,
        due_date=_d(date(2026, 4, 1)),
        interval_days=365,
    ))
    db.session.add(MaintenanceTrigger(
        aircraft_id=jodel.id,
        name="100 h engine inspection",
        trigger_type=TriggerType.HOURS,
        due_engine_hours=1550.0,
        interval_hours=100,
        notes="Continental C90 mandatory 100 h check",
    ))

    # ── Phase 8: Expenses ─────────────────────────────────────────────────────

    # Cessna 172S — fuel, parts, insurance over the last year
    for exp_date, etype, desc, amount, currency, qty, unit in [
        (_d(date(2024, 1, 15)), ExpenseType.INSURANCE, "Annual hull & liability — Allianz",  2840.00, "EUR", None, None),
        (_d(date(2024, 3,  2)), ExpenseType.PARTS,     "50 h oil change — Aeroshell 15W-50",   85.00, "EUR", None, None),
        (_d(date(2024, 3,  2)), ExpenseType.PARTS,     "Oil filter Lycoming LW-13624",          22.50, "EUR", None, None),
        (_d(date(2024, 5, 18)), ExpenseType.FUEL,      "Shell 100LL at EBOS",                  186.00, "EUR", 60.0,  "L"),
        (_d(date(2024, 7,  9)), ExpenseType.FUEL,      "Shell 100LL at EBBR",                  155.00, "EUR", 50.0,  "L"),
        (_d(date(2024, 9, 22)), ExpenseType.FUEL,      "Total 100LL at EDDM",                  210.00, "EUR", 65.0,  "L"),
        (_d(date(2024, 11,  5)), ExpenseType.PARTS,    "Magneto inspection — Slick 4351",      320.00, "EUR", None, None),
        (_d(date(2025, 1, 20)), ExpenseType.FUEL,      "Q8 100LL at EBOS",                     162.00, "EUR", 52.0,  "L"),
        (_d(date(2025, 3, 10)), ExpenseType.OTHER,     "Landing fees EBBR (4× approach)",       48.00, "EUR", None, None),
    ]:
        db.session.add(Expense(
            aircraft_id=c172.id, date=exp_date,
            expense_type=etype, description=desc,
            amount=amount, currency=currency,
            quantity=qty, unit=unit,
        ))

    # Piper Seminole — higher operating costs (twin)
    for exp_date, etype, desc, amount, currency, qty, unit in [
        (_d(date(2024, 1,  8)), ExpenseType.INSURANCE, "Annual hull & liability — AXA",       5200.00, "EUR", None, None),
        (_d(date(2024, 2, 14)), ExpenseType.PARTS,     "Left engine 50 h oil change",           140.00, "EUR", None, None),
        (_d(date(2024, 2, 14)), ExpenseType.PARTS,     "Right engine 50 h oil change",          140.00, "EUR", None, None),
        (_d(date(2024, 4, 20)), ExpenseType.FUEL,      "Total 100LL at EHRD",                   285.00, "EUR", 90.0,  "L"),
        (_d(date(2024, 8, 31)), ExpenseType.FUEL,      "Shell 100LL at EBBR",                   312.00, "EUR", 98.0,  "L"),
        (_d(date(2024, 10,  3)), ExpenseType.PARTS,    "Left propeller governor overhaul",      1450.00, "EUR", None, None),
        (_d(date(2025, 2,  5)), ExpenseType.FUEL,      "Q8 100LL at EBOS",                      290.00, "EUR", 92.0,  "L"),
    ]:
        db.session.add(Expense(
            aircraft_id=seminole.id, date=exp_date,
            expense_type=etype, description=desc,
            amount=amount, currency=currency,
            quantity=qty, unit=unit,
        ))

    # Robin DR-401 — diesel, lower fuel cost per litre
    for exp_date, etype, desc, amount, currency, qty, unit in [
        (_d(date(2024, 1, 12)), ExpenseType.INSURANCE, "Annual hull & liability — Generali",  1950.00, "EUR", None, None),
        (_d(date(2024, 3, 15)), ExpenseType.PARTS,     "Annual inspection — EBGT MRO",         880.00, "EUR", None, None),
        (_d(date(2024, 6,  5)), ExpenseType.FUEL,      "Jet-A1 at EBGT",                        82.00, "EUR", 60.0,  "L"),
        (_d(date(2024, 9, 17)), ExpenseType.FUEL,      "Jet-A1 at EBOS",                         70.00, "EUR", 52.0,  "L"),
        (_d(date(2025, 1, 30)), ExpenseType.OTHER,     "Avionics software update — Garmin",    240.00, "EUR", None, None),
    ]:
        db.session.add(Expense(
            aircraft_id=robin.id, date=exp_date,
            expense_type=etype, description=desc,
            amount=amount, currency=currency,
            quantity=qty, unit=unit,
        ))

    # ── Phase 9: Documents ────────────────────────────────────────────────────
    _seed_documents(c172, seminole, robin)

    # ── Phase 10: Backup records ──────────────────────────────────────────────
    _seed_backup_records(_dt)

    # ── Phase 11: Share tokens ────────────────────────────────────────────────
    # Tokens must be globally unique across all demo slots, so generate them
    # rather than using hardcoded strings.
    import secrets as _secrets
    def _unique_token():
        while True:
            t = _secrets.token_urlsafe(6)[:8]
            if not ShareToken.query.filter_by(token=t).first():
                return t

    db.session.add(ShareToken(aircraft_id=c172.id,
                              token=_unique_token(), access_level="summary"))
    db.session.add(ShareToken(aircraft_id=seminole.id,
                              token=_unique_token(), access_level="full"))

    # ── Phase 12: Snags ───────────────────────────────────────────────────────
    # OO-PNH: one grounding snag (simulates a grounded aircraft)
    db.session.add(Snag(
        aircraft_id=c172.id,
        title="Left main gear door does not latch securely",
        description="Door pops open during rollout. Possible broken latch mechanism. Observed on last 3 landings.",
        reporter="J. Klein",
        is_grounding=True,
        reported_at=_dt(datetime(2026, 4, 10, 14, 32, 0, tzinfo=timezone.utc)),
    ))
    # OO-ABC: one non-grounding cosmetic snag
    db.session.add(Snag(
        aircraft_id=seminole.id,
        title="Right cabin door seal leaking — wind noise above 100 kt",
        description="Seal visibly worn near upper hinge. Annoying but not safety-critical.",
        reporter="M. Dupont",
        is_grounding=False,
        reported_at=_dt(datetime(2026, 3, 25, 9, 15, 0, tzinfo=timezone.utc)),
    ))
    # OO-GRN: no open snags (clean aircraft)

    # ── Phase 20: Mass & Balance ──────────────────────────────────────────────
    _seed_wb(c172, robin, jodel, _d)

    return [c172, seminole, robin, jodel]


def _copy_seed_doc(src_name: str, label: str, upload_folder: str) -> tuple[str, str, int | None]:
    """Copy a seed doc to the upload folder, return (stored_name, mime_type, size_bytes)."""
    src = os.path.join(_SEED_DOCS_DIR, src_name)
    ext = os.path.splitext(src_name)[1]
    stored = f"doc_{label}_{uuid.uuid4().hex[:12]}{ext}"
    dest = os.path.join(upload_folder, stored)
    mime = mimetypes.guess_type(src_name)[0] or "text/plain"
    size = None
    if os.path.exists(src):
        try:
            os.makedirs(upload_folder, exist_ok=True)
            shutil.copy2(src, dest)
            size = os.path.getsize(dest)
        except OSError:
            pass
    return stored, mime, size


def _seed_documents(c172: Aircraft, seminole: Aircraft, robin: Aircraft) -> None:
    from flask import current_app  # pyright: ignore[reportMissingImports]
    try:
        upload_folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
    except RuntimeError:
        upload_folder = "/data/uploads"

    # (source_file, title, is_sensitive, aircraft, component)
    seed_entries = [
        ("oo-pnh_arc_2025.txt",      "Annual Review Certificate 2025", False, c172,     None),
        ("oo-pnh_weight_balance.txt", "Weight & Balance Sheet",         False, c172,     None),
        ("oo-pnh_insurance_2025.txt", "Insurance Certificate 2025",     True,  c172,     None),
        ("oo-abc_arc_2026.txt",       "Annual Review Certificate 2026", False, seminole, None),
        ("oo-grn_arc_2027.txt",       "Annual Review Certificate 2027", False, robin,    None),
        ("oo-grn_engine_logbook.txt", "Continental CD-155 Engine Logbook", False, robin,
         robin.components[0] if robin.components else None),
    ]

    for src_name, title, sensitive, aircraft, comp in seed_entries:
        label = f"comp{comp.id}" if comp else f"ac{aircraft.id}"
        stored, mime, size = _copy_seed_doc(src_name, label, upload_folder)
        db.session.add(Document(
            aircraft_id=aircraft.id,
            component_id=comp.id if comp else None,
            filename=stored,
            original_filename=src_name,
            mime_type=mime,
            size_bytes=size,
            title=title,
            is_sensitive=sensitive,
        ))


def _seed_backup_records(_dt) -> None:
    from flask import current_app  # pyright: ignore[reportMissingImports]
    try:
        backup_folder = current_app.config.get("BACKUP_FOLDER", "/data/backups")
    except RuntimeError:
        backup_folder = "/data/backups"

    seed_backups = [
        ("openhangar_backup_20260115T020000Z.zip.enc", 204800,  "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2", _dt(datetime(2026, 1, 15,  2, 0, 0, tzinfo=timezone.utc)), "ok"),
        ("openhangar_backup_20260214T020000Z.zip.enc", 207360,  "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3", _dt(datetime(2026, 2, 14,  2, 0, 0, tzinfo=timezone.utc)), "ok"),
        ("openhangar_backup_20260315T020000Z.zip.enc", 209920,  "c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4", _dt(datetime(2026, 3, 15,  2, 0, 0, tzinfo=timezone.utc)), "ok"),
    ]
    for filename, size, sha256, created_at, status in seed_backups:
        db.session.add(BackupRecord(
            filename=filename,
            path=os.path.join(backup_folder, filename),
            size_bytes=size,
            sha256=sha256,
            created_at=created_at,
            status=status,
        ))


def _seed_wb(c172: Aircraft, robin: Aircraft, jodel: Aircraft, _d) -> None:
    """Seed W&B configurations and sample calculations for OO-PNH, OO-GRN, and OO-TCH."""

    # ── OO-PNH (Cessna 172S Skyhawk, Avgas) ──────────────────────────────────
    # Arms from C172S POH (firewall datum), approximate values
    c172.fuel_type = "avgas"
    cfg_c172 = WeightBalanceConfig(
        aircraft_id=c172.id,
        empty_weight=760.0,
        empty_cg_arm=1.003,
        max_takeoff_weight=1111.0,
        forward_cg_limit=0.889,
        aft_cg_limit=1.219,
        fuel_unit="L",
        datum_note="Firewall",
    )
    db.session.add(cfg_c172)
    db.session.flush()

    # (label, arm, limit, is_fuel, position) — limit is capacity (L) for fuel, max_weight (kg) for non-fuel
    # Main wing tank + optional aux belly tank at a noticeably different arm.
    stations_c172 = [
        ("Pilot + Co-pilot",   1.016, 190.0,  False, 0),
        ("Rear passengers",    1.854, 190.0,  False, 1),
        ("Baggage area 1",     2.540,  54.4,  False, 2),
        ("Fuel tank (main)",   1.219, 262.5,  True,  3),   # 262.5 L main wing tank
        ("Fuel tank (aux)",    0.946,  60.0,  True,  4),   # 60 L aux belly tank, more forward
    ]
    for label, arm, limit, is_fuel, pos in stations_c172:
        cap = limit if is_fuel else None
        st = WeightBalanceStation(
            config_id=cfg_c172.id, label=label, arm=arm,
            max_weight=None if is_fuel else limit,
            capacity=cap,
            is_fuel=is_fuel, position=pos,
        )
        db.session.add(st)
    db.session.flush()

    # Look up stations by position so we can reference their IDs
    c172_sts = {st.position: st for st in cfg_c172.stations}

    # Sample: pilot + copilot, main tank half-full, aux tank empty
    # station_weights stores: volume (L) for fuel stations, kg for non-fuel
    fuel_vol_main = 131.25  # L — main tank half full
    fuel_vol_aux  = 0.0     # L — aux tank empty
    fuel_kg_main  = fuel_vol_main * 0.72
    fuel_kg_aux   = fuel_vol_aux  * 0.72
    sw_normal = {
        str(c172_sts[0].id): 160.0,         # kg — pilot + copilot
        str(c172_sts[1].id): 0.0,           # kg — no rear pax
        str(c172_sts[2].id): 10.0,          # kg — small bag
        str(c172_sts[3].id): fuel_vol_main, # L — main wing
        str(c172_sts[4].id): fuel_vol_aux,  # L — aux belly
    }
    ew, ea = 760.0, 1.003
    total_m = (ew * ea + 160.0 * 1.016 + 0.0 + 10.0 * 2.540
               + fuel_kg_main * 1.219 + fuel_kg_aux * 0.946)
    total_w = ew + 160.0 + 0.0 + 10.0 + fuel_kg_main + fuel_kg_aux
    cg_normal = total_m / total_w
    db.session.add(WeightBalanceEntry(
        config_id=cfg_c172.id,
        date=_d(date(2026, 5, 6)),
        label="Pre-flight EBOS–EHRD",
        total_weight=round(total_w, 2),
        loaded_cg=round(cg_normal, 3),
        is_in_envelope=True,
        station_weights=sw_normal,
    ))

    # ── OO-GRN (Robin DR-401/155CDI, Jet-A1) ─────────────────────────────────
    # Arms from DR-401 POH (nose datum), approximate values
    robin.fuel_type = "jet_a1"
    cfg_robin = WeightBalanceConfig(
        aircraft_id=robin.id,
        empty_weight=650.0,
        empty_cg_arm=0.268,
        max_takeoff_weight=900.0,
        forward_cg_limit=0.180,
        aft_cg_limit=0.380,
        fuel_unit="L",
        datum_note="Nose tip",
    )
    db.session.add(cfg_robin)
    db.session.flush()

    stations_robin = [
        ("Pilot + Co-pilot", 0.300, 170.0, False, 0),
        ("Rear passengers",  0.830, 160.0, False, 1),
        ("Baggage",          1.250,  40.0, False, 2),
        ("Fuel tank",        0.400, 100.0, True,  3),   # 100 L sample load
    ]
    for label, arm, limit, is_fuel, pos in stations_robin:
        st = WeightBalanceStation(
            config_id=cfg_robin.id, label=label, arm=arm,
            max_weight=None if is_fuel else limit,
            capacity=160.0 if is_fuel else None,   # 160 L total usable capacity
            is_fuel=is_fuel, position=pos,
        )
        db.session.add(st)
    db.session.flush()

    robin_sts = {st.position: st for st in cfg_robin.stations}

    fuel_vol_robin = 100.0  # L
    fuel_kg_robin  = fuel_vol_robin * 0.81  # jet_a1 density
    sw_robin = {
        str(robin_sts[0].id): 150.0,        # kg
        str(robin_sts[1].id): 0.0,          # kg
        str(robin_sts[2].id): 5.0,          # kg
        str(robin_sts[3].id): fuel_vol_robin,  # L
    }
    ew2, ea2 = 650.0, 0.268
    total_m2 = ew2 * ea2 + 150.0 * 0.300 + 0.0 + 5.0 * 1.250 + fuel_kg_robin * 0.400
    total_w2 = ew2 + 150.0 + 0.0 + 5.0 + fuel_kg_robin
    cg_robin = total_m2 / total_w2
    db.session.add(WeightBalanceEntry(
        config_id=cfg_robin.id,
        date=_d(date(2026, 5, 6)),
        label="Pre-flight EBGT–EBOS",
        total_weight=round(total_w2, 2),
        loaded_cg=round(cg_robin, 3),
        is_in_envelope=True,
        station_weights=sw_robin,
    ))

    # ── OO-TCH (Jodel DR-1050 Ambassadeur, Avgas) — polygon envelope ─────────
    # Classic 2-seat vintage aircraft; non-rectangular CG envelope (dogleg
    # forward limit) from the POH expressed originally in inches / lb.
    # Polygon vertices converted: 1 in = 0.0254 m, 1 lb = 0.453592 kg.
    # Original points (arm in, weight lb):
    #   (11.5, 1000), (11.5, 1200), (14, 1500), (21, 1500), (21, 1000)
    jodel.fuel_type = "avgas"
    jodel_poly = [
        [0.292, 453.6],   # fwd / light   (11.5 in / 1000 lb)
        [0.292, 544.3],   # fwd / medium  (11.5 in / 1200 lb) — dogleg knee
        [0.356, 680.4],   # fwd / heavy   (14 in  / 1500 lb)
        [0.533, 680.4],   # aft / heavy   (21 in  / 1500 lb)
        [0.533, 453.6],   # aft / light   (21 in  / 1000 lb)
    ]
    cfg_jodel = WeightBalanceConfig(
        aircraft_id=jodel.id,
        empty_weight=448.0,
        empty_cg_arm=0.393,
        # Scalar limits match polygon extremes (used as fallback display only)
        max_takeoff_weight=680.4,
        forward_cg_limit=0.292,
        aft_cg_limit=0.533,
        fuel_unit="L",
        datum_note="Nose tip",
        envelope_points=jodel_poly,
    )
    db.session.add(cfg_jodel)
    db.session.flush()

    stations_jodel = [
        ("Pilot + passenger", 0.348, 170.0, False, 0),
        ("Baggage",           0.850,  15.0, False, 1),
        ("Fuel tank",         0.405,  80.0, True,  2),   # 80 L usable
    ]
    for label, arm, limit, is_fuel, pos in stations_jodel:
        st = WeightBalanceStation(
            config_id=cfg_jodel.id, label=label, arm=arm,
            max_weight=None if is_fuel else limit,
            capacity=80.0 if is_fuel else None,
            is_fuel=is_fuel, position=pos,
        )
        db.session.add(st)
    db.session.flush()

    jodel_sts = {st.position: st for st in cfg_jodel.stations}

    # Sample: solo flight, 30 L fuel — weight 551.6 kg, CG 0.389 m (inside polygon)
    fuel_vol_jodel = 30.0   # L
    fuel_kg_jodel  = fuel_vol_jodel * 0.72  # avgas
    sw_jodel = {
        str(jodel_sts[0].id): 80.0,           # kg — pilot only
        str(jodel_sts[1].id): 2.0,            # kg — light bag
        str(jodel_sts[2].id): fuel_vol_jodel, # L
    }
    ew3, ea3 = 448.0, 0.393
    total_m3 = ew3*ea3 + 80.0*0.348 + 2.0*0.850 + fuel_kg_jodel*0.405
    total_w3 = ew3 + 80.0 + 2.0 + fuel_kg_jodel
    cg_jodel = total_m3 / total_w3
    db.session.add(WeightBalanceEntry(
        config_id=cfg_jodel.id,
        date=_d(date(2026, 5, 1)),
        label="Solo local — EBGT",
        total_weight=round(total_w3, 2),
        loaded_cg=round(cg_jodel, 3),
        is_in_envelope=True,
        station_weights=sw_jodel,
    ))


def seed_pilot_profiles(
    user_id: int,
    date_offset_days=0,  # int or callable() -> int, evaluated per logbook entry
    license_number: str = "BE.PPL(A).20341",
) -> None:
    """Create a pilot profile + ~200 h logbook for the dev/demo user (J. Klein).

    Training (2017–2018) on PA-28 at EBCI/EBNM; post-PPL cross-countries
    and currency on C172S and PA-44 Seminole.

    date_offset_days shifts every logbook date forward so a second pilot's
    entries look slightly different without duplicating the data.  Pass a
    callable (e.g. ``lambda: random.randint(1, 4)``) to get a fresh random
    offset per entry.
    """
    _base = date.today() - _SEED_REF_DATE

    def _d(d: date) -> date:
        offset = date_offset_days() if callable(date_offset_days) else date_offset_days
        return d + _base + timedelta(days=offset)

    db.session.add(PilotProfile(
        user_id=user_id,
        license_number=license_number,
        medical_expiry=_d(date(2026, 6, 20)),   # ~42 days out → warning on dashboard
        sep_expiry=_d(date(2026, 9, 30)),
    ))
    db.session.flush()

    # Compact row: (date, ac_type, reg, dep, arr, h_se, h_me, fn,
    #               ldg_d, ldg_n, night, instr, pic_name, remark)
    # fn: "P"=PIC, "D"=dual; h_se/h_me: single-pilot SE or ME time
    ip = "P. Laurent"   # main PPL instructor
    ic = "M. Charlier"  # secondary instructor
    jk = "J. Klein"

    rows = [
        # ── PPL training 2017 — PA-28-161 at EBCI/EBNM ───────────────────────
        # Circuits, stalls, PFL, pre-solo  (5 dual sessions)
        (_d(date(2017, 4,  8)),"PA-28-161 Warrior II","OO-HAW","EBCI","EBCI", 0.8,None,"D",6,0,None,None,ip, f"Intro — effects of controls, {ip}"),
        (_d(date(2017, 4, 22)),"PA-28-161 Warrior II","OO-HAW","EBCI","EBCI", 0.9,None,"D",5,0,None,None,ip, f"Stalls, slow flight, PFL — {ip}"),
        (_d(date(2017, 5, 13)),"PA-28-161 Warrior II","OO-HAW","EBCI","EBCI", 0.9,None,"D",4,0,None,None,ip, f"Circuits — pre-solo check — {ip}"),
        (_d(date(2017, 5, 27)),"PA-28-161 Warrior II","OO-HAW","EBCI","EBCI", 0.5,None,"P",3,0,None,None,jk, "First solo!"),
        # Navigation exercises dual + solo XC
        (_d(date(2017, 7,  1)),"PA-28-161 Warrior II","OO-HAW","EBCI","EBNM", 1.2,None,"D",1,0,None,None,ip, f"First nav EBCI-EBNM — {ip}"),
        (_d(date(2017, 7,  8)),"PA-28-161 Warrior II","OO-HAW","EBNM","EBCI", 1.0,None,"D",1,0,None,None,ip, f"Return nav EBNM-EBCI — {ip}"),
        (_d(date(2017, 8, 19)),"PA-28-161 Warrior II","OO-HAW","EBCI","ELLX", 1.8,None,"D",1,0,None,None,ip, f"Long nav EBCI-ELLX — {ip}"),
        (_d(date(2017, 8, 26)),"PA-28-161 Warrior II","OO-HAW","ELLX","EBCI", 1.8,None,"D",1,0,None,None,ip, f"Return ELLX-EBCI — {ip}"),
        (_d(date(2017, 9, 16)),"PA-28-161 Warrior II","OO-HAW","EBCI","LFQQ", 1.3,None,"D",1,0,None,None,ip, f"International nav EBCI-LFQQ — {ip}"),
        (_d(date(2017, 9, 23)),"PA-28-161 Warrior II","OO-HAW","LFQQ","EBCI", 1.3,None,"D",1,0,None,None,ip, f"Return LFQQ-EBCI — {ip}"),
        (_d(date(2017,10,  7)),"PA-28-161 Warrior II","OO-HAW","EBCI","EBNM", 1.1,None,"P",1,0,None,None,jk, "First solo cross-country EBCI-EBNM"),
        (_d(date(2017,10, 14)),"PA-28-161 Warrior II","OO-HAW","EBNM","EBCI", 1.0,None,"P",1,0,None,None,jk, "Solo return EBNM-EBCI"),
        # Skills test
        (_d(date(2017,11,  4)),"PA-28-161 Warrior II","OO-HAW","EBCI","EBCI", 1.5,None,"P",1,0,None,None,jk, "PPL Skills Test — examiner R. Pieters — PASS"),
        # ── Post-PPL 2017–2018 — building hours on PA-28-181 ─────────────────
        (_d(date(2017,11, 25)),"PA-28-181 Archer III","OO-TOM","EBCI","EHRD", 1.5,None,"P",1,0,None,None,jk, "First post-PPL XC EBCI-EHRD"),
        (_d(date(2017,11, 25)),"PA-28-181 Archer III","OO-TOM","EHRD","EBCI", 1.4,None,"P",1,0,None,None,jk, "Return EHRD-EBCI"),
        (_d(date(2018, 1, 27)),"PA-28-181 Archer III","OO-TOM","EBCI","LFQQ", 1.3,None,"P",1,0,None,None,jk, "EBCI-LFQQ"),
        (_d(date(2018, 1, 27)),"PA-28-181 Archer III","OO-TOM","LFQQ","EBCI", 1.3,None,"P",1,0,None,None,jk, "Return LFQQ-EBCI"),
        (_d(date(2018, 4,  7)),"PA-28-181 Archer III","OO-TOM","EBCI","ELLX", 1.8,None,"P",1,0,None,None,jk, "EBCI-ELLX"),
        (_d(date(2018, 4,  7)),"PA-28-181 Archer III","OO-TOM","ELLX","EBCI", 1.8,None,"P",1,0,None,None,jk, "Return ELLX-EBCI"),
        (_d(date(2018, 5,  5)),"PA-28-181 Archer III","OO-TOM","EBCI","EBOS", 1.5,None,"P",1,0,None,None,jk, "EBCI-EBOS"),
        (_d(date(2018, 5,  5)),"PA-28-181 Archer III","OO-TOM","EBOS","EBCI", 1.5,None,"P",1,0,None,None,jk, "Return EBOS-EBCI"),
        (_d(date(2018, 6, 16)),"PA-28-181 Archer III","OO-TOM","EBCI","EDDM", 4.5,None,"P",1,0,None,None,jk, "Long XC EBCI-EDDM"),
        (_d(date(2018, 7,  7)),"PA-28-181 Archer III","OO-TOM","EDDM","EHRD", 3.5,None,"P",1,0,None,None,jk, "EDDM-EHRD"),
        (_d(date(2018, 7, 14)),"PA-28-181 Archer III","OO-TOM","EHRD","EBCI", 1.5,None,"P",1,0,None,None,jk, "Return EHRD-EBCI"),
        # ── Night rating 2018 ─────────────────────────────────────────────────
        (_d(date(2018, 9,  1)),"PA-28-161 Warrior II","OO-HAW","EBCI","EBCI", 1.2,None,"D",4,0, 1.2,None,ip, f"Night rating — circuits — {ip}"),
        (_d(date(2018, 9,  8)),"PA-28-161 Warrior II","OO-HAW","EBCI","EBCI", 1.0,None,"D",5,0, 1.0,None,ip, f"Night circuits — {ip}"),
        (_d(date(2018, 9, 15)),"PA-28-161 Warrior II","OO-HAW","EBCI","EBCI", 0.8,None,"P",3,0, 0.8,None,jk, "Solo night circuits"),
        (_d(date(2018, 9, 22)),"PA-28-161 Warrior II","OO-HAW","EBCI","EBNM", 1.3,None,"D",0,1, 1.3,None,ip, f"Night nav EBCI-EBNM — {ip}"),
        (_d(date(2018, 9, 29)),"PA-28-161 Warrior II","OO-HAW","EBNM","EBCI", 1.2,None,"P",0,1, 1.2,None,jk, "Solo night return EBNM-EBCI"),
        # ── Building hours 2019 ───────────────────────────────────────────────
        (_d(date(2019, 5,  4)),"PA-28-181 Archer III","OO-TOM","EBCI","EDDM", 4.5,None,"P",1,0,None,None,jk, "Long XC EBCI-EDDM"),
        (_d(date(2019, 5, 11)),"PA-28-181 Archer III","OO-TOM","EDDM","ELLX", 2.5,None,"P",1,0,None,None,jk, "EDDM-ELLX"),
        (_d(date(2019, 5, 11)),"PA-28-181 Archer III","OO-TOM","ELLX","EBCI", 1.8,None,"P",1,0,None,None,jk, "ELLX-EBCI"),
        (_d(date(2019, 8,  3)),"PA-28-181 Archer III","OO-TOM","EBCI","EHRD", 1.5,None,"P",1,0,None,None,jk, "EBCI-EHRD"),
        (_d(date(2019, 8,  3)),"PA-28-181 Archer III","OO-TOM","EHRD","EDDM", 3.2,None,"P",1,0,None,None,jk, "EHRD-EDDM"),
        (_d(date(2019, 8, 10)),"PA-28-181 Archer III","OO-TOM","EDDM","EBCI", 4.3,None,"P",1,0,None,None,jk, "Return EDDM-EBCI"),
        (_d(date(2019, 9,  7)),"PA-28-161 Warrior II","OO-HAW","EBCI","EBCI", 1.0,None,"D",3,0,None, 1.0,ic, f"Instrument approach practice under foggles — {ic}"),
        # ── C172S currency + cross-countries 2020–2021 (OO-PNH) ──────────────
        (_d(date(2020, 3, 14)),"C172S Skyhawk","OO-PNH","EBOS","EBBR",  1.5,None,"P",1,0,None,None,jk, "Local VFR EBOS-EBBR"),
        (_d(date(2020, 5,  2)),"C172S Skyhawk","OO-PNH","EBBR","ELLX",  1.7,None,"P",1,0,None,None,jk, "Navigation EBBR-ELLX"),
        (_d(date(2020, 7, 19)),"C172S Skyhawk","OO-PNH","ELLX","EDDM",  3.5,None,"P",1,0,None,None,jk, "ELLX-EDDM — light turbulence over Vosges"),
        (_d(date(2020, 9,  5)),"C172S Skyhawk","OO-PNH","EDDM","EBOS",  3.4,None,"P",1,0,None,None,jk, "Return EDDM-EBOS"),
        # ── PA-44 Seminole checkout + currency (OO-ABC) ───────────────────────
        (_d(date(2020, 4, 10)),"PA-44 Seminole","OO-ABC","EBOS","EHRD",  None,1.4,"D",1,0,None,None,ic, f"PA-44 checkout — {ic}"),
        (_d(date(2020, 6, 22)),"PA-44 Seminole","OO-ABC","EHRD","EBBR",  None,0.8,"P",1,0,None,None,jk, "Twin currency EHRD-EBBR"),
        (_d(date(2020,11, 15)),"PA-44 Seminole","OO-ABC","EBBR","ELLX",  None,1.3,"P",0,1, 1.3,None,jk, "Night flight on twin EBBR-ELLX"),
        (_d(date(2021, 2,  8)),"PA-44 Seminole","OO-ABC","ELLX","EBOS",  None,1.3,"P",1,0,None,None,jk, "Return ELLX-EBOS"),
        # ── Mixed fleet 2021–2024 ─────────────────────────────────────────────
        (_d(date(2021, 1, 12)),"C172S Skyhawk","OO-PNH","EBOS","EHAM",  1.8,None,"P",1,0,None, 0.5,jk, "IFR practice — vectors to ILS 18R"),
        (_d(date(2021, 5, 15)),"C172S Skyhawk","OO-PNH","EBOS","EDDM",  4.5,None,"P",1,0,None,None,jk, "Summer cross-country EBOS-EDDM"),
        (_d(date(2021, 5, 22)),"C172S Skyhawk","OO-PNH","EDDM","EBOS",  4.3,None,"P",1,0,None,None,jk, "Return EDDM-EBOS"),
        (_d(date(2022, 5, 14)),"PA-44 Seminole","OO-ABC","EBOS","ELLX",  None,1.7,"P",1,0,None,None,jk, "Twin XC EBOS-ELLX"),
        (_d(date(2022, 5, 14)),"PA-44 Seminole","OO-ABC","ELLX","EBOS",  None,1.7,"P",1,0,None,None,jk, "Return ELLX-EBOS"),
        (_d(date(2022, 7,  2)),"C172S Skyhawk","OO-PNH","EBOS","EDDM",  4.5,None,"P",1,0,None,None,jk, "Summer holiday EBOS-EDDM"),
        (_d(date(2022, 7,  9)),"C172S Skyhawk","OO-PNH","EDDM","EBOS",  4.3,None,"P",1,0,None,None,jk, "Return EDDM-EBOS"),
        (_d(date(2023, 6,  5)),"Robin DR-401/155CDI","OO-GRN","EBGT","EBOS", 1.2,None,"P",1,0,None,None,jk, "Delivery flight from overhaul shop"),
        (_d(date(2023, 7, 15)),"C172S Skyhawk","OO-PNH","EBOS","ELLX",  1.8,None,"P",1,0,None,None,jk, "EBOS-ELLX-EHRD triangle pt.1"),
        (_d(date(2023, 7, 15)),"C172S Skyhawk","OO-PNH","ELLX","EHRD",  1.7,None,"P",1,0,None,None,jk, "pt.2"),
        (_d(date(2023, 7, 22)),"C172S Skyhawk","OO-PNH","EHRD","EBOS",  1.4,None,"P",1,0,None,None,jk, "Return EHRD-EBOS"),
        (_d(date(2023, 9, 17)),"Robin DR-401/155CDI","OO-GRN","EBOS","EBGT", 0.8,None,"P",1,0,None,None,jk, "Local flight EBOS-EBGT"),
        (_d(date(2024, 2, 10)),"C172S Skyhawk","OO-PNH","EBOS","EBBR",  1.5,None,"P",1,0,None,None,jk, "Local VFR flight"),
        (_d(date(2024, 4, 20)),"C172S Skyhawk","OO-PNH","EBBR","ELLX",  1.7,None,"P",1,0,None,None,jk, "Cross-country to Luxembourg"),
        (_d(date(2024, 7,  5)),"C172S Skyhawk","OO-PNH","ELLX","EBOS",  1.7,None,"P",0,1, 0.8,None,jk, "Night return — partial night"),
        # ── 2026: currency flights + most recent (3 days ago) ────────────────
        (_d(date(2026, 2, 15)),"C172S Skyhawk","OO-PNH","EBBR","EBOS",  1.4,None,"P",1,0,None,None,jk, "Currency EBBR-EBOS"),
        (_d(date(2026, 2, 22)),"C172S Skyhawk","OO-PNH","EBOS","EHRD",  1.3,None,"P",1,0,None,None,jk, "Currency EBOS-EHRD"),
        (_d(date(2026, 3,  1)),"C172S Skyhawk","OO-PNH","EHRD","EBOS",  1.5,None,"P",1,0,None,None,jk, "Currency return EHRD-EBOS"),
        (_d(date(2026, 5,  6)),"C172S Skyhawk","OO-PNH","EBOS","EHRD",  1.5,None,"P",1,0,None,None,jk, "Local VFR EBOS-EHRD"),
    ]

    for (dt, ac_type, reg, dep, arr, h_se, h_me, fn,
         ldg_d, ldg_n, night, instr, pic_name, remark) in rows:
        hours = h_se if h_se is not None else h_me
        db.session.add(PilotLogbookEntry(
            pilot_user_id=user_id,
            date=dt,
            aircraft_type=ac_type,
            aircraft_registration=reg,
            departure_place=dep,
            arrival_place=arr,
            pic_name=pic_name,
            single_pilot_se=h_se,
            single_pilot_me=h_me,
            landings_day=ldg_d if ldg_d else None,
            landings_night=ldg_n if ldg_n else None,
            night_time=night,
            instrument_time=instr,
            function_pic=hours if fn == "P" else None,
            function_dual=hours if fn == "D" else None,
            remarks=remark,
        ))
