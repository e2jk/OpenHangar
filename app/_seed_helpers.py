"""
Shared seed helpers — fleet data used by both dev_seed and demo_seed.

Add richer data here as new phases are implemented; both seeds pick it up
automatically without any further changes.
"""

import mimetypes
import os
import shutil
import uuid
from datetime import date, datetime, timezone

from models import (
    Aircraft, BackupRecord, Component, ComponentType,
    Document,
    Expense, ExpenseType,
    FlightEntry, MaintenanceTrigger, Snag, ShareToken, TriggerType, db,
)

# Placeholder seed documents bundled in the repo
_SEED_DOCS_DIR = os.path.join(os.path.dirname(__file__), "dev_seed_docs")


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
            flight_time_counter_start=hs, flight_time_counter_end=he,
            engine_time_counter_start=ts, engine_time_counter_end=te,
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
        due_engine_hours=330.0,
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
            flight_time_counter_start=hs, flight_time_counter_end=he,
            engine_time_counter_start=ts, engine_time_counter_end=te,
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
            flight_time_counter_start=hs, flight_time_counter_end=he,
            engine_time_counter_start=ts, engine_time_counter_end=te,
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
        installed_at=date(2010, 4, 1),
        extras={"tbo_hours": 1800},
    ))
    for flight_date, dep, arr, ts, te, notes in [
        (date(2024, 3, 10), "EBGT", "EBOS", 1500.0, 1501.2, "Spring flying"),
        (date(2024, 5, 18), "EBOS", "EBGT", 1501.2, 1502.0, None),
    ]:
        db.session.add(FlightEntry(
            aircraft_id=jodel.id, date=flight_date,
            departure_icao=dep, arrival_icao=arr,
            flight_time_counter_start=None, flight_time_counter_end=None,
            engine_time_counter_start=ts, engine_time_counter_end=te,
            notes=notes,
        ))
    db.session.add(MaintenanceTrigger(
        aircraft_id=jodel.id,
        name="Annual inspection (ARC)",
        trigger_type=TriggerType.CALENDAR,
        due_date=date(2026, 4, 1),
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
        (date(2024, 1, 15), ExpenseType.INSURANCE, "Annual hull & liability — Allianz",  2840.00, "EUR", None, None),
        (date(2024, 3,  2), ExpenseType.PARTS,     "50 h oil change — Aeroshell 15W-50",   85.00, "EUR", None, None),
        (date(2024, 3,  2), ExpenseType.PARTS,     "Oil filter Lycoming LW-13624",          22.50, "EUR", None, None),
        (date(2024, 5, 18), ExpenseType.FUEL,      "Shell 100LL at EBOS",                  186.00, "EUR", 60.0,  "L"),
        (date(2024, 7,  9), ExpenseType.FUEL,      "Shell 100LL at EBBR",                  155.00, "EUR", 50.0,  "L"),
        (date(2024, 9, 22), ExpenseType.FUEL,      "Total 100LL at EDDM",                  210.00, "EUR", 65.0,  "L"),
        (date(2024, 11,  5), ExpenseType.PARTS,    "Magneto inspection — Slick 4351",      320.00, "EUR", None, None),
        (date(2025, 1, 20), ExpenseType.FUEL,      "Q8 100LL at EBOS",                     162.00, "EUR", 52.0,  "L"),
        (date(2025, 3, 10), ExpenseType.OTHER,     "Landing fees EBBR (4× approach)",       48.00, "EUR", None, None),
    ]:
        db.session.add(Expense(
            aircraft_id=c172.id, date=exp_date,
            expense_type=etype, description=desc,
            amount=amount, currency=currency,
            quantity=qty, unit=unit,
        ))

    # Piper Seminole — higher operating costs (twin)
    for exp_date, etype, desc, amount, currency, qty, unit in [
        (date(2024, 1,  8), ExpenseType.INSURANCE, "Annual hull & liability — AXA",       5200.00, "EUR", None, None),
        (date(2024, 2, 14), ExpenseType.PARTS,     "Left engine 50 h oil change",           140.00, "EUR", None, None),
        (date(2024, 2, 14), ExpenseType.PARTS,     "Right engine 50 h oil change",          140.00, "EUR", None, None),
        (date(2024, 4, 20), ExpenseType.FUEL,      "Total 100LL at EHRD",                   285.00, "EUR", 90.0,  "L"),
        (date(2024, 8, 31), ExpenseType.FUEL,      "Shell 100LL at EBBR",                   312.00, "EUR", 98.0,  "L"),
        (date(2024, 10,  3), ExpenseType.PARTS,    "Left propeller governor overhaul",      1450.00, "EUR", None, None),
        (date(2025, 2,  5), ExpenseType.FUEL,      "Q8 100LL at EBOS",                      290.00, "EUR", 92.0,  "L"),
    ]:
        db.session.add(Expense(
            aircraft_id=seminole.id, date=exp_date,
            expense_type=etype, description=desc,
            amount=amount, currency=currency,
            quantity=qty, unit=unit,
        ))

    # Robin DR-401 — diesel, lower fuel cost per litre
    for exp_date, etype, desc, amount, currency, qty, unit in [
        (date(2024, 1, 12), ExpenseType.INSURANCE, "Annual hull & liability — Generali",  1950.00, "EUR", None, None),
        (date(2024, 3, 15), ExpenseType.PARTS,     "Annual inspection — EBGT MRO",         880.00, "EUR", None, None),
        (date(2024, 6,  5), ExpenseType.FUEL,      "Jet-A1 at EBGT",                        82.00, "EUR", 60.0,  "L"),
        (date(2024, 9, 17), ExpenseType.FUEL,      "Jet-A1 at EBOS",                         70.00, "EUR", 52.0,  "L"),
        (date(2025, 1, 30), ExpenseType.OTHER,     "Avionics software update — Garmin",    240.00, "EUR", None, None),
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
    _seed_backup_records()

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
        reported_at=datetime(2026, 4, 10, 14, 32, 0, tzinfo=timezone.utc),
    ))
    # OO-ABC: one non-grounding cosmetic snag
    db.session.add(Snag(
        aircraft_id=seminole.id,
        title="Right cabin door seal leaking — wind noise above 100 kt",
        description="Seal visibly worn near upper hinge. Annoying but not safety-critical.",
        reporter="M. Dupont",
        is_grounding=False,
        reported_at=datetime(2026, 3, 25, 9, 15, 0, tzinfo=timezone.utc),
    ))
    # OO-GRN: no open snags (clean aircraft)


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


def _seed_backup_records() -> None:
    from flask import current_app  # pyright: ignore[reportMissingImports]
    try:
        backup_folder = current_app.config.get("BACKUP_FOLDER", "/data/backups")
    except RuntimeError:
        backup_folder = "/data/backups"

    seed_backups = [
        ("openhangar_backup_20260115T020000Z.zip.enc", 204800,  "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2", datetime(2026, 1, 15,  2, 0, 0, tzinfo=timezone.utc), "ok"),
        ("openhangar_backup_20260214T020000Z.zip.enc", 207360,  "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3", datetime(2026, 2, 14,  2, 0, 0, tzinfo=timezone.utc), "ok"),
        ("openhangar_backup_20260315T020000Z.zip.enc", 209920,  "c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4", datetime(2026, 3, 15,  2, 0, 0, tzinfo=timezone.utc), "ok"),
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
