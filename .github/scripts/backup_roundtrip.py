#!/usr/bin/env python3
"""Prove a backup restores exact data and files (CI, audit T06).

Runs inside the app image (or a checkout with app/ on the path) against a
throwaway database and upload folder, configured through the usual
OPENHANGAR_* environment variables:

  backup_roundtrip.py populate
      add a representative fleet: aircraft, a flight with two linked
      pilots, a certificate with an uploaded file, a ledger charge and its
      reversal — all fake data
  backup_roundtrip.py fingerprint OUT.json
      write the values a restore must reproduce: per-table row counts,
      selected field values, ledger totals and the SHA-256 of every
      uploaded file
  backup_roundtrip.py compare EXPECTED.json ACTUAL.json
      exit 1, listing every difference, unless they are identical

The backup and restore themselves run through the real `flask backup-now`
and `flask restore-backup` commands (pg_dump, psql, encryption) in between.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

# The app package: the image's working directory (/app), or app/ in a checkout.
for _candidate in (Path(__file__).resolve().parent.parent.parent / "app", Path.cwd()):
    if (_candidate / "init.py").is_file():
        sys.path.insert(0, str(_candidate))


def _app() -> Any:
    from init import create_app

    return create_app()


def populate() -> None:
    from models import (
        Aircraft,
        BillingAccountKind,
        Document,
        Flight,
        LedgerEntryType,
        Role,
        Tenant,
        TenantUser,
        User,
        db,
    )
    from services.billing import BillingService

    app = _app()
    upload_folder = Path(app.config["UPLOAD_FOLDER"])
    with app.app_context():
        tenant = Tenant(name="Round-trip Flying Club", slug="roundtrip")
        db.session.add(tenant)
        db.session.flush()
        users = []
        for i, role in enumerate((Role.ADMIN, Role.PILOT, Role.PILOT)):
            user = User(
                email=f"pilot{i}@roundtrip.test",
                name=f"Round-trip Pilot {i}",
                password_hash="not-a-real-hash",
                is_pilot=True,
            )
            db.session.add(user)
            db.session.flush()
            db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=role))
            users.append(user)
        aircraft = Aircraft(
            tenant_id=tenant.id, registration="OO-RTP", make="Robin", model="DR400-140B"
        )
        db.session.add(aircraft)
        db.session.flush()
        db.session.add(
            Flight(
                aircraft_id=aircraft.id,
                date=date(2026, 3, 14),
                departure_icao="EBAW",
                arrival_icao="EBKT",
                flight_time=Decimal("1.4"),
                flight_time_counter_start=Decimal("1234.5"),
                flight_time_counter_end=Decimal("1235.9"),
                pic_user_id=users[1].id,
                pic_name=users[1].name,
                second_crew_user_id=users[2].id,
                second_crew_name=users[2].name,
                second_crew_role="IP",
                notes="Two-pilot round-trip flight — accents kept: é ü ß",
            )
        )
        relpath = "roundtrip/OO-RTP/registration/2026-01-01 - Certificate.pdf"
        path = upload_folder / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.7\n" + bytes(range(256)) * 64)
        db.session.add(
            Document(
                aircraft_id=aircraft.id,
                filename=relpath,
                original_filename="certificate.pdf",
                category="registration",
                doc_type="arc",
                valid_until=date(2027, 1, 31),
            )
        )
        account = BillingService.get_or_create_account(
            tenant.id, users[1].id, BillingAccountKind.RENTER
        )
        charge = BillingService.post(
            account,
            LedgerEntryType.CHARGE,
            Decimal("187.35"),
            "Rental",
            date(2026, 3, 14),
        )
        db.session.flush()
        BillingService.reverse(charge, users[0], "Wrong rate")
        BillingService.post(
            account,
            LedgerEntryType.CHARGE,
            Decimal("164.50"),
            "Rental, corrected",
            date(2026, 3, 14),
        )
        db.session.commit()
    print("Populated the round-trip fixture.")


def fingerprint(out: str) -> None:
    from models import db
    from sqlalchemy import text

    app = _app()
    upload_folder = Path(app.config["UPLOAD_FOLDER"])
    with app.app_context():
        conn = db.session.connection()
        tables = sorted(t for t in db.metadata.tables if t not in {"alembic_version"})
        counts = {
            t: conn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar()
            for t in tables
        }
        values = {
            "flights": [
                list(map(str, row))
                for row in conn.execute(
                    text(
                        "SELECT date, departure_icao, arrival_icao, flight_time, "
                        "flight_time_counter_start, flight_time_counter_end, pic_name, "
                        "second_crew_name, second_crew_role, notes FROM flights ORDER BY id"
                    )
                )
            ],
            "documents": [
                list(map(str, row))
                for row in conn.execute(
                    text(
                        "SELECT filename, doc_type, valid_until FROM documents ORDER BY id"
                    )
                )
            ],
            "ledger": [
                list(map(str, row))
                for row in conn.execute(
                    text(
                        "SELECT entry_type, amount, description, entry_date, "
                        "reverses_id IS NOT NULL FROM ledger_entries ORDER BY id"
                    )
                )
            ],
            "ledger_balance": str(
                conn.execute(
                    text("SELECT COALESCE(SUM(amount), 0) FROM ledger_entries")
                ).scalar()
            ),
        }
    files = {
        str(path.relative_to(upload_folder)): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(upload_folder.rglob("*"))
        if path.is_file() and "_backups" not in path.parts
    }
    Path(out).write_text(
        json.dumps(
            {
                "counts": counts,
                "values": values,
                "files": files,
                "taken_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
            sort_keys=True,
        )
    )
    print(f"Fingerprint: {sum(counts.values())} rows, {len(files)} file(s) -> {out}")


def compare(expected_path: str, actual_path: str) -> int:
    expected = json.loads(Path(expected_path).read_text())
    actual = json.loads(Path(actual_path).read_text())
    differences = []
    for section in ("counts", "values", "files"):
        exp, act = expected[section], actual[section]
        for key in sorted(set(exp) | set(act)):
            if exp.get(key) != act.get(key):
                differences.append(
                    f"{section}.{key}: expected {exp.get(key)!r}, got {act.get(key)!r}"
                )
    if expected["counts"].get("flights", 0) == 0 or not expected["files"]:
        differences.append("the expected fingerprint is empty: nothing was populated")
    for line in differences:
        print(f"DIFF {line}")
    print(
        "Restore matches the backup exactly."
        if not differences
        else f"{len(differences)} difference(s)."
    )
    return 1 if differences else 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["populate"] and len(argv) == 1:
        populate()
        return 0
    if argv[:1] == ["fingerprint"] and len(argv) == 2:
        fingerprint(argv[1])
        return 0
    if argv[:1] == ["compare"] and len(argv) == 3:
        return compare(argv[1], argv[2])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
