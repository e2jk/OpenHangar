#!/usr/bin/env python3
"""Prove a backup restores exact data and files (CI, audit T06).

Runs inside the app image (or a checkout with app/ on the path) against a
throwaway database and upload folder, configured through the usual
OPENHANGAR_* environment variables:

  backup_roundtrip.py populate
      add a representative fleet: aircraft, a flight with two linked
      pilots, a certificate with an uploaded file, a ledger charge and its
      reversal — and airfield passports: a group of two editions with a
      passport-only friend, an airfield code alias, manual visits, an
      arrival exclusion, vouchers and a redemption, a group comment and a
      shared stamp photo file — all fake data
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
        _populate_passports(tenant.id, users[1], upload_folder)
        _populate_places(tenant.id, users[1], upload_folder)
    print("Populated the round-trip fixture.")


def _populate_passports(tenant_id: int, pilot: Any, upload_folder: Path) -> None:
    """Every passport table gets rows, and one stamp photo a file (Phase 41)."""
    from models import (
        Flight,
        PassportAirfield,
        PassportAirfieldComment,
        PassportArrivalExclusion,
        PassportParticipant,
        PassportStampPhoto,
        PassportVisit,
        PassportVoucherAllocation,
        PassportVoucherRedemption,
        TenantUser,
        User,
        db,
    )
    from passports import editions, groups, invitations
    from passports.catalogue import add_alias, create_custom
    from passports.management import create_personal, metadata_values

    friend = User(
        email="friend@roundtrip.test",
        name="Round-trip Friend",
        password_hash="not-a-real-hash",
    )
    db.session.add(friend)
    db.session.flush()
    db.session.add(
        TenantUser(
            user_id=friend.id, tenant_id=tenant_id, role=None, access_scope="personal"
        )
    )
    field = create_custom(
        tenant_id, "Round-trip Field", code="EBRT", latitude=50.9, longitude=4.4
    )
    add_alias(field, "EBR1", valid_until=date(2025, 12, 31))

    def edition(label: str, year: int) -> Any:
        passport = create_personal(
            tenant_id, pilot.id, metadata_values({"title": f"Round-trip {label}"})
        )
        passport.start_date, passport.end_date = date(year, 1, 1), date(year, 12, 31)
        passport.edition_label = label
        db.session.add(
            PassportAirfield(
                tenant_id=tenant_id, passport_id=passport.id, airfield_id=field.id
            )
        )
        db.session.flush()
        return passport

    consent = {
        "series_title": "Round-trip group",
        "display_name": "Round-trip Pilot",
        "share_visits": "yes",
        "share_history": "yes",
    }
    first = edition("2025", 2025)
    series = groups.enable(first, pilot.id, consent)
    second = edition("2026", 2026)
    editions.link(second, pilot.id, series_id=series.id)
    groups.enable(second, pilot.id, consent)
    db.session.add(
        PassportParticipant(
            passport_id=second.id,
            user_id=friend.id,
            accepted_at=datetime(2026, 2, 1, tzinfo=UTC),
            display_name="Round-trip Friend",
        )
    )
    db.session.flush()
    groups.consent(second, friend.id, consent | {"display_name": "Round-trip Friend"})
    invitations.replace(second)
    target = PassportAirfield.query.filter_by(passport_id=second.id).one()
    participant = PassportParticipant.query.filter_by(
        passport_id=second.id, user_id=friend.id
    ).one()
    db.session.add(
        PassportVisit(
            passport_id=second.id,
            participant_id=participant.id,
            target_id=target.id,
            author_user_id=friend.id,
            visit_date=date(2026, 4, 2),
            note="Friend's visit — accents kept: é ü ß",
        )
    )
    landing = Flight(date=date(2026, 5, 3), pic_user_id=pilot.id, arrival_icao="EBRT")
    db.session.add(landing)
    db.session.flush()
    pilot_participant = PassportParticipant.query.filter_by(
        passport_id=second.id, user_id=pilot.id
    ).one()
    db.session.add(
        PassportArrivalExclusion(
            participant_id=pilot_participant.id, flight_id=landing.id
        )
    )
    allocation = PassportVoucherAllocation(
        passport_id=second.id,
        target_id=target.id,
        personal_user_id=friend.id,
        quantity=2,
    )
    db.session.add(allocation)
    db.session.flush()
    db.session.add(
        PassportVoucherRedemption(
            passport_id=second.id,
            allocation_id=allocation.id,
            participant_id=participant.id,
            redemption_date=date(2026, 4, 2),
            quantity=1,
            saving_amount=Decimal("12.50"),
            currency="EUR",
            request_key="roundtrip-redemption-0001",
        )
    )
    db.session.add(
        PassportAirfieldComment(
            tenant_id=tenant_id,
            series_id=series.id,
            airfield_id=field.id,
            author_user_id=friend.id,
            author_display_name="Round-trip Friend",
            category="nearby",
            body="Good café next to the apron.",
            created_at=datetime(2026, 4, 2, 12, tzinfo=UTC),
        )
    )
    from models import PassportGroupBlock, PassportOwnershipOffer
    from passports.tokens import email_digest

    # A removed member who may not self-rejoin, and a pending owner handover.
    db.session.add(
        PassportGroupBlock(
            series_id=series.id,
            email_hash=email_digest("removed@roundtrip.test"),
            display_name="Removed friend",
        )
    )
    db.session.add(
        PassportOwnershipOffer(
            series_id=series.id,
            requester_user_id=pilot.id,
            target_user_id=friend.id,
            request_key="roundtrip-offer-000000000000001",
            expires_at=datetime(2026, 12, 31, tzinfo=UTC),
        )
    )
    filename = "roundtrip-stamp.jpg"
    photo_file = upload_folder / "passport_photos" / filename
    photo_file.parent.mkdir(parents=True, exist_ok=True)
    photo_file.write_bytes(b"\xff\xd8\xff\xe0" + bytes(range(256)) * 32)
    db.session.add(
        PassportStampPhoto(
            passport_id=second.id,
            participant_id=participant.id,
            target_id=target.id,
            uploader_user_id=friend.id,
            filename=filename,
            group_shared_at=datetime(2026, 4, 3, tzinfo=UTC),
        )
    )
    db.session.commit()


def _populate_places(tenant_id: int, pilot: Any, upload_folder: Path) -> None:
    """A shared collection with a located place (tag, photo, visits) and a
    link-only place still to locate, shared with the passport friend."""
    from models import (
        Place,
        PlaceCollection,
        PlaceCollectionMember,
        PlaceTag,
        PlaceTagAssignment,
        PlaceVisit,
        User,
        db,
    )

    friend = User.query.filter_by(email="friend@roundtrip.test").one()
    collection = PlaceCollection(
        tenant_id=tenant_id, name="Round-trip places", created_by_user_id=pilot.id
    )
    db.session.add(collection)
    db.session.flush()
    for user, can_edit in ((pilot, True), (friend, False)):
        db.session.add(
            PlaceCollectionMember(
                tenant_id=tenant_id,
                collection_id=collection.id,
                user_id=user.id,
                can_edit=can_edit,
            )
        )
    photo = "0123456789abcdef0123456789abcdef.jpg"  # an app-generated name
    photo_file = upload_folder / "place_photos" / photo
    photo_file.parent.mkdir(parents=True, exist_ok=True)
    photo_file.write_bytes(b"\xff\xd8\xff\xe0" + bytes(range(256)) * 16)
    castle = Place(
        collection_id=collection.id,
        owner_user_id=pilot.id,
        created_by_user_id=pilot.id,
        updated_by_user_id=pilot.id,
        title="Château de Round-trip",
        description="Accents kept: é ü ß",
        latitude=50.4836,
        longitude=4.0,
        external_url="https://example.org/castle",
        photo_filename=photo,
    )
    later = Place(
        collection_id=collection.id,
        owner_user_id=friend.id,
        created_by_user_id=friend.id,
        updated_by_user_id=friend.id,
        external_url="https://www.instagram.com/reel/roundtrip/",
    )
    tag = PlaceTag(collection_id=collection.id, name="Museum")
    db.session.add_all([castle, later, tag])
    db.session.flush()
    db.session.add(
        PlaceTagAssignment(
            place_id=castle.id, tag_id=tag.id, collection_id=collection.id
        )
    )
    # The castle's airfield (with directions), and a dismissed "record a
    # visit?" offer for the round-trip landing there.
    from models import (
        Flight,
        PassportAirfieldIdentity,
        PlaceAirfield,
        PlaceVisitDismissal,
    )

    field = PassportAirfieldIdentity.query.filter_by(
        tenant_id=tenant_id, name="Round-trip Field"
    ).one()
    db.session.add(
        PlaceAirfield(
            tenant_id=tenant_id,
            collection_id=collection.id,
            place_id=castle.id,
            airfield_id=field.id,
            note="Taxi, 10 minutes — é",
            preferred=True,
        )
    )
    landing = Flight.query.filter_by(pic_user_id=pilot.id, arrival_icao="EBRT").first()
    if landing is not None:
        db.session.add(
            PlaceVisitDismissal(
                user_id=pilot.id, flight_id=landing.id, place_id=castle.id
            )
        )
    db.session.add_all(
        [
            PlaceVisit(
                place_id=castle.id, user_id=pilot.id, visit_date=date(2026, 5, 3)
            ),
            PlaceVisit(
                place_id=castle.id,
                user_id=friend.id,
                visit_date=date(2026, 5, 4),
                note="By bike from the field",
            ),
        ]
    )
    db.session.commit()


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
            "passport_visits": [
                list(map(str, row))
                for row in conn.execute(
                    text("SELECT visit_date, note FROM passport_visits ORDER BY id")
                )
            ],
            "passport_vouchers": [
                list(map(str, row))
                for row in conn.execute(
                    text(
                        "SELECT r.redemption_date, r.quantity, r.saving_amount, "
                        "r.currency, a.quantity FROM passport_voucher_redemptions r "
                        "JOIN passport_voucher_allocations a ON a.id = r.allocation_id "
                        "ORDER BY r.id"
                    )
                )
            ],
            "passport_photos": [
                list(map(str, row))
                for row in conn.execute(
                    text(
                        "SELECT filename, group_shared_at FROM passport_stamp_photos "
                        "ORDER BY id"
                    )
                )
            ],
            "places": [
                list(map(str, row))
                for row in conn.execute(
                    text(
                        "SELECT title, description, latitude, longitude, external_url, "
                        "photo_filename FROM places ORDER BY id"
                    )
                )
            ],
            "passport_access": [
                list(map(str, row))
                for row in conn.execute(
                    text(
                        "SELECT access_scope, role IS NULL FROM tenant_users "
                        "ORDER BY user_id, tenant_id"
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
    # Every passport table except transient builder drafts must be proven.
    unproven = sorted(
        table
        for table, count in expected["counts"].items()
        if table.startswith("passport") and table != "passport_drafts" and not count
    )
    if unproven:
        differences.append("passport tables without rows: " + ", ".join(unproven))
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
