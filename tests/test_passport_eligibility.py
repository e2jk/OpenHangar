"""Live stamp eligibility and query bounds, independent of UI mutations."""

from datetime import date, time, timedelta
from typing import Any

from flask import Flask
from models import (
    Flight,
    LogbookEntryType,
    Passport,
    PassportAirfield,
    PassportAirfieldAlias,
    PassportAirfieldIdentity,
    PassportArrivalExclusion,
    PassportParticipant,
    PassportVisit,
    db,
)
from passports.catalogue import add_alias, create_custom
from passports.eligibility import evaluate, passport_progress

from tests.test_passport_management import owner

__all__ = ["owner"]


def flight(uid: int, **values: Any) -> Flight:
    defaults = {"date": date(2025, 1, 1), "pic_user_id": uid, "arrival_icao": "TEST"}
    row = Flight(**(defaults | values))
    db.session.add(row)
    return row


def test_live_stamps_dates_counts_aliases_and_mutations(
    app: Flask, owner: dict[str, int]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, owner["passport"])
        part = PassportParticipant.query.filter_by(passport_id=p.id).one()
        p.start_date, p.end_date = date(2025, 1, 1), date(2025, 12, 31)
        early = flight(
            owner["user"], arrival_time=time(10), other_aircraft_registration="OO-TEST"
        )
        late = flight(owner["user"], arrival_time=time(11))
        second = flight(
            owner["other"], second_crew_user_id=owner["user"], date=date(2025, 1, 2)
        )
        for values in [
            {"date": date(2024, 12, 31)},
            {"date": date(2026, 1, 1)},
            {"date": date.today() + timedelta(days=1)},
            {"entry_type": LogbookEntryType.FSTD},
            {"flight_time": 0, "landing_count": 0},
            {"pic_user_id": owner["other"]},
            {"arrival_icao": "ZZZZ"},
            {"arrival_icao": None, "departure_icao": "TEST", "landing_count": 12},
        ]:
            flight(owner["user"], **values)
        db.session.flush()
        visit = PassportVisit(
            passport_id=p.id,
            participant_id=part.id,
            target_id=owner["target"],
            author_user_id=owner["user"],
            visit_date=early.date,
            flight_id=early.id,
            evidence_kind="confirmed_gps",
        )
        independent = PassportVisit(
            passport_id=p.id,
            participant_id=part.id,
            target_id=owner["target"],
            author_user_id=owner["user"],
            visit_date=early.date,
            note="Independent visit",
        )
        db.session.add_all([visit, independent])
        db.session.commit()
        result = passport_progress(p, owner["user"])
        row = result["rows"][0]
        assert (result["visited"], result["total"], row["count"]) == (1, 1, 4)
        assert row["first"]["id"] == early.id and row["first"]["aircraft"] == "OO-TEST"
        assert passport_progress(p, owner["other"])["visited"] == 0
        # No copied automatic rows: edits and reassignment change credit immediately.
        early.arrival_icao = "OTHER"
        db.session.delete(visit)
        second.second_crew_user_id = None
        db.session.commit()
        row = passport_progress(p, owner["user"])["rows"][0]
        assert row["count"] == 2 and row["first"]["id"] == late.id
        db.session.add(
            PassportArrivalExclusion(participant_id=part.id, flight_id=late.id)
        )
        db.session.commit()
        row = passport_progress(p, owner["user"])["rows"][0]
        assert (
            row["count"] == 1
            and row["first"]["kind"] == "manual"
            and row["first"]["time"] is None
        )
        db.session.delete(independent)
        db.session.commit()
        assert passport_progress(p, owner["user"])["visited"] == 0
        # Alias ambiguity outside the selected targets must prevent false credit.
        other_field = create_custom(p.tenant_id, "Reused code")
        add_alias(other_field, "TEST", valid_until=date(2025, 12, 31))
        db.session.commit()
        assert passport_progress(p, owner["user"])["rows"][0]["events"] == []
        other_field_alias = other_field.id
        alias = next(
            a
            for a in PassportAirfieldAlias.query.all()
            if a.airfield_id == other_field_alias
        )
        alias.valid_from, alias.valid_until = date(2026, 1, 1), None
        db.session.commit()
        assert len(passport_progress(p, owner["user"])["rows"][0]["events"]) == 1


def test_closed_completion_projected_dates_and_stable_ties(
    app: Flask, owner: dict[str, int]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, owner["passport"])
        field = db.session.get(PassportAirfieldIdentity, owner["field"])
        field.overrides = {"is_closed": True}
        db.session.commit()
        result = passport_progress(p, owner["user"])
        assert (result["total"], result["percent"], result["excluded_closed"]) == (
            0,
            None,
            1,
        )
        f = flight(
            owner["user"], date=date(2025, 12, 31), flight_time=0, landing_count=1
        )
        db.session.commit()
        assert passport_progress(p, owner["user"])["percent"] == 100
        field.overrides = {"is_closed": False}
        db.session.delete(f)
        db.session.commit()
        assert passport_progress(p, owner["user"])["total"] == 1
        fields = [{"key": "new", "ident": "NEW", "is_closed": False}]
        flight(owner["user"], arrival_icao="NEW")
        db.session.commit()
        assert (
            evaluate(
                p.tenant_id, owner["user"], fields, None, None, None, preview=True
            )["visited"]
            == 1
        )
        assert (
            evaluate(p.tenant_id, owner["user"], fields, None, None, None)["visited"]
            == 0
        )
        # Multiple date-only records order by stable IDs without fabricated times.
        part = PassportParticipant.query.one()
        for _index in range(2):
            db.session.add(
                PassportVisit(
                    passport_id=p.id,
                    participant_id=part.id,
                    target_id=owner["target"],
                    author_user_id=owner["user"],
                    visit_date=date(2025, 1, 1),
                )
            )
        db.session.commit()
        row = passport_progress(p, owner["user"])["rows"][0]
        assert row["first"]["id"] == min(e["id"] for e in row["events"])
        p.start_date = date(2025, 2, 1)
        db.session.commit()
        assert passport_progress(p, owner["user"])["visited"] == 0
        assert all(
            e["excluded"]
            for e in passport_progress(p, owner["user"])["rows"][0]["events"]
        )


def test_evidence_queries_do_not_grow_per_marker(
    app: Flask, owner: dict[str, int]
) -> None:
    from sqlalchemy import event

    with app.app_context():
        p = db.session.get(Passport, owner["passport"])
        statements: list[str] = []

        def record(
            _conn: Any,
            _cursor: Any,
            statement: str,
            _parameters: Any,
            _context: Any,
            _many: Any,
        ) -> None:
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", record)
        try:
            passport_progress(p, owner["user"])
            initial = len(statements)
            for index in range(20):
                field = create_custom(p.tenant_id, f"Field {index}", code=f"X{index}")
                db.session.add(
                    PassportAirfield(
                        tenant_id=p.tenant_id, passport_id=p.id, airfield_id=field.id
                    )
                )
                flight(owner["user"], arrival_icao=f"X{index}")
            db.session.commit()
            # Load scalar passport state before measuring, matching the first call.
            assert p.tenant_id == owner["tenant"]
            statements.clear()
            result = passport_progress(p, owner["user"])
            assert len(statements) <= initial + 1
            assert result["visited"] == 20
        finally:
            event.remove(db.engine, "before_cursor_execute", record)


def test_own_logbook_evidence_spans_organisations(
    app: Flask, owner: dict[str, int]
) -> None:
    from models import Aircraft, Tenant

    with app.app_context():
        other_tenant = Tenant(name="Another organisation")
        db.session.add(other_tenant)
        db.session.flush()
        aircraft = Aircraft(
            tenant_id=other_tenant.id,
            registration="OO-OTHER",
            make="Cessna",
            model="172",
        )
        db.session.add(aircraft)
        db.session.flush()
        own = flight(owner["user"], aircraft_id=aircraft.id)
        flight(owner["other"], aircraft_id=aircraft.id)
        db.session.commit()
        passport = db.session.get(Passport, owner["passport"])
        evidence = passport_progress(passport, owner["user"])["rows"][0]
        assert evidence["count"] == 1 and evidence["first"]["aircraft"] == "OO-OTHER"
        db.session.delete(own)
        db.session.commit()
        assert passport_progress(passport, owner["user"])["visited"] == 0
