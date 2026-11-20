"""Manual visit corrections keep the participant, target and logbook boundaries."""

from datetime import UTC, date, datetime
from typing import Any

from flask import Flask
from flask.testing import FlaskClient
from models import Flight, Passport, PassportParticipant, PassportVisit, db

from tests.test_passport_account_scope import scoped
from tests.test_passport_builder import signin
from tests.test_passport_management import owner

__all__ = ["owner", "scoped"]


def test_passport_only_owner_can_correct_manual_visit_without_editing_flight(
    app: Flask, client: FlaskClient, scoped: dict[str, int]
) -> None:
    s = scoped
    signin(client, s["user"])
    path = f"/passports/{s['passport']}/airfields/{s['target']}"
    with app.app_context():
        flight = Flight(
            pic_user_id=s["user"],
            date=date(2025, 1, 1),
            arrival_icao="TEST",
            flight_time=1,
        )
        db.session.add(flight)
        db.session.commit()
        flight_id = flight.id
    assert (
        client.post(
            path,
            data={
                "action": "visit",
                "visit_date": "2025-01-01",
                "supporting_flight": str(flight_id),
                "note": "Original",
            },
        ).status_code
        == 302
    )
    with app.app_context():
        visit_id = PassportVisit.query.one().id
    response = client.get(path)
    assert (
        b"Edit manual visit" in response.data
        and b"Current supporting flight" in response.data
    )
    values: dict[str, Any] = {
        "action": "edit_visit",
        "visit_id": str(visit_id),
        "visit_date": "2025-01-01",
        "supporting_flight": str(flight_id),
        "note": "Corrected <tip>",
    }
    assert client.post(path, data=values).status_code == 302
    assert b"Corrected &lt;tip&gt;" in client.get(path).data
    for bad, message in (
        ({"visit_date": "bad"}, "valid visit date"),
        ({"visit_date": "2999-01-01"}, "not in the future"),
        ({"note": "x" * 8001}, "too long"),
        ({"visit_date": "2025-01-02"}, "own logbook on the visit date"),
    ):
        response = client.post(path, data=values | bad)
        assert response.status_code == 400 and message in response.text
    assert (
        client.post(
            path,
            data=values
            | {"visit_date": "2025-01-02", "supporting_flight": "", "note": ""},
        ).status_code
        == 302
    )
    with app.app_context():
        visit = db.session.get(PassportVisit, visit_id)
        assert (
            visit.visit_date == date(2025, 1, 2)
            and visit.note is None
            and visit.flight_id is None
        )
        flight = db.session.get(Flight, flight_id)
        assert flight.date == date(2025, 1, 1) and flight.arrival_icao == "TEST"
        passport = db.session.get(Passport, s["passport"])
        passport.archived_at = datetime.now(UTC)
        db.session.commit()
    assert client.post(path, data=values).status_code == 409


def test_manual_edit_rejects_other_participants_targets_and_gps_evidence(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    s = owner
    signin(client, s["user"])
    path = f"/passports/{s['passport']}/airfields/{s['target']}"
    with app.app_context():
        participant = PassportParticipant(
            passport_id=s["passport"],
            user_id=s["other"],
            display_name="Friend",
            accepted_at=datetime.now(UTC),
        )
        db.session.add(participant)
        db.session.flush()
        foreign = PassportVisit(
            passport_id=s["passport"],
            participant_id=participant.id,
            target_id=s["target"],
            visit_date=date(2025, 1, 1),
            author_user_id=s["other"],
            note="Private friend note",
        )
        own = PassportParticipant.query.filter_by(
            passport_id=s["passport"], user_id=s["user"]
        ).one()
        gps = PassportVisit(
            passport_id=s["passport"],
            participant_id=own.id,
            target_id=s["target"],
            visit_date=date(2025, 1, 1),
            author_user_id=s["user"],
            evidence_kind="confirmed_gps",
        )
        db.session.add_all([foreign, gps])
        db.session.commit()
        foreign_id, gps_id = foreign.id, gps.id
    assert b"Private friend note" not in client.get(path).data
    assert b"Edit manual visit" not in client.get(path).data
    for visit_id in (foreign_id, gps_id, 999999999999999999999999):
        assert (
            client.post(
                path,
                data={
                    "action": "edit_visit",
                    "visit_id": str(visit_id),
                    "visit_date": "2025-01-02",
                },
            ).status_code
            == 404
        )
    with app.app_context():
        assert db.session.get(PassportVisit, foreign_id).note == "Private friend note"
        assert db.session.get(PassportVisit, gps_id).visit_date == date(2025, 1, 1)
