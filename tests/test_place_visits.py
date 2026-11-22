"""Visits to a place (42e): every member records, corrects and removes only
their own; the history is for members; "Last visited … by Anna and you"."""

from datetime import date, timedelta

import pytest
from flask import Flask, render_template
from flask.testing import FlaskClient
from models import Flight, PlaceVisit, User, db
from places import visits
from places.access import PlaceError
from places.collections import create_collection, set_member
from places.management import save_place

from tests.test_place_collections import people
from tests.test_place_management import form
from tests.test_place_sharing import signin

__all__ = ["people"]


@pytest.fixture
def place(app: Flask, people: dict[str, int]) -> dict[str, int]:
    p = people
    with app.app_context():
        collection = create_collection(p["tenant"], p["creator"], "Fly-outs")
        set_member(
            p["tenant"], p["creator"], collection.id, p["reader"], can_edit=False
        )
        set_member(p["tenant"], p["creator"], collection.id, p["personal"])
        castle = save_place(
            p["tenant"], p["creator"], collection.id, form(title="Castle")
        )
        db.session.commit()
        return {"place": castle.id, "collection": collection.id}


def _visits(app: Flask, place_id: int) -> list[PlaceVisit]:
    with app.app_context():
        return (
            PlaceVisit.query.filter_by(place_id=place_id).order_by(PlaceVisit.id).all()
        )


@pytest.mark.parametrize("name", ["creator", "reader", "personal"])
def test_every_member_records_a_visit_today_by_default(
    app: Flask,
    client: FlaskClient,
    people: dict[str, int],
    place: dict[str, int],
    name: str,
) -> None:
    signin(client, people, name)
    response = client.post(
        f"/places/{place['place']}/visits", data={"note": "  By  bike  "}
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/places/{place['place']}")
    (visit,) = _visits(app, place["place"])
    assert visit.user_id == people[name] and visit.visit_date == date.today()
    assert visit.note == "By bike"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"visit_date": (date.today() + timedelta(days=1)).isoformat()}, "future"),
        ({"visit_date": "not a date"}, "valid visit date"),
        ({"visit_date": "1850-01-01"}, "valid visit date"),
        ({"note": "x" * 501}, "under 500"),
    ],
)
def test_invalid_visits_are_refused_with_a_message(
    app: Flask,
    client: FlaskClient,
    people: dict[str, int],
    place: dict[str, int],
    data: dict[str, str],
    message: str,
) -> None:
    signin(client, people)
    response = client.post(f"/places/{place['place']}/visits", data=data)
    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert any(message in text for _cat, text in sess.get("_flashes", []))
    assert _visits(app, place["place"]) == []


def test_non_members_and_missing_places_get_404(
    client: FlaskClient, people: dict[str, int], place: dict[str, int]
) -> None:
    signin(client, people, "outsider")
    assert client.post(f"/places/{place['place']}/visits").status_code == 404
    signin(client, people)
    assert client.post("/places/999999/visits").status_code == 404


def test_a_view_only_account_cannot_record(
    app: Flask, client: FlaskClient, people: dict[str, int], place: dict[str, int]
) -> None:
    with app.app_context():
        db.session.get(User, people["reader"]).view_only = True
        db.session.commit()
    signin(client, people, "reader")
    assert client.post(f"/places/{place['place']}/visits").status_code == 302
    assert _visits(app, place["place"]) == []
    with app.app_context():
        panel = visits.panel(
            people["tenant"],
            people["reader"],
            db.session.get(visits.Place, place["place"]),
        )
    assert panel["can_record"] is False


def test_only_ones_own_visit_can_be_corrected_or_removed(
    app: Flask, client: FlaskClient, people: dict[str, int], place: dict[str, int]
) -> None:
    pid = place["place"]
    signin(client, people, "reader")
    client.post(f"/places/{pid}/visits", data={"visit_date": "2026-05-01"})
    (theirs,) = _visits(app, pid)
    signin(client, people)
    assert client.post(f"/places/{pid}/visits/{theirs.id}", data={}).status_code == 404
    assert client.post(f"/places/{pid}/visits/{theirs.id}/delete").status_code == 404
    assert client.post(f"/places/{pid}/visits/999999/delete").status_code == 404
    signin(client, people, "reader")
    response = client.post(
        f"/places/{pid}/visits/{theirs.id}",
        data={"visit_date": "2026-05-02", "note": "Picnic"},
    )
    assert response.status_code == 302
    (edited,) = _visits(app, pid)
    assert edited.visit_date == date(2026, 5, 2) and edited.note == "Picnic"
    assert (
        client.post(
            f"/places/{pid}/visits/{theirs.id}", data={"visit_date": "x"}
        ).status_code
        == 302
    )
    assert _visits(app, pid)[0].visit_date == date(2026, 5, 2)
    assert client.post(f"/places/{pid}/visits/{theirs.id}/delete").status_code == 302
    assert _visits(app, pid) == []


def test_panel_summary_history_and_former_members(
    app: Flask, people: dict[str, int], place: dict[str, int]
) -> None:
    p, pid = people, place["place"]
    with app.app_context():
        flight = Flight(pic_user_id=p["creator"], date=date(2026, 5, 3))
        db.session.add(flight)
        db.session.flush()
        db.session.add_all(
            [
                PlaceVisit(
                    place_id=pid, user_id=p["reader"], visit_date=date(2026, 5, 3)
                ),
                PlaceVisit(
                    place_id=pid,
                    user_id=p["creator"],
                    visit_date=date(2026, 5, 3),
                    flight_id=flight.id,
                ),
                PlaceVisit(place_id=pid, user_id=None, visit_date=date(2026, 5, 3)),
                PlaceVisit(
                    place_id=pid, user_id=p["reader"], visit_date=date(2026, 1, 9)
                ),
            ]
        )
        db.session.commit()
        castle = db.session.get(visits.Place, pid)
        with app.test_request_context():
            mine = visits.panel(p["tenant"], p["creator"], castle)
            theirs = visits.panel(p["tenant"], p["reader"], castle)
            page = render_template(
                "places/_visits.html", place=castle, visit_panel=mine
            )
    assert mine["summary"] == (
        "Last visited May 3, 2026 by a former member, reader and you"
    )
    assert [v["date"] for v in mine["history"]] == [date(2026, 5, 3)] * 3 + [
        date(2026, 1, 9)
    ]
    assert mine["visited"] is True and mine["can_record"] is True
    # A flight link only on one's own visit.
    assert [v["flight_id"] is not None for v in mine["history"] if v["mine"]] == [True]
    assert all(v["flight_id"] is None for v in theirs["history"])
    assert "Correct or remove" in page and "I was here" in page
    assert "/pilot/logbook/" in page


def test_panel_for_a_place_nobody_visited(
    app: Flask, people: dict[str, int], place: dict[str, int]
) -> None:
    with app.app_context(), app.test_request_context():
        castle = db.session.get(visits.Place, place["place"])
        panel = visits.panel(people["tenant"], people["reader"], castle)
        page = render_template("places/_visits.html", place=castle, visit_panel=panel)
    assert panel["summary"] is None and panel["history"] == []
    assert panel["visited"] is False
    assert "Nobody has recorded a visit yet." in page


def test_service_errors_are_place_errors(
    app: Flask, people: dict[str, int], place: dict[str, int]
) -> None:
    with app.app_context(), pytest.raises(PlaceError):
        visits.record(people["tenant"], people["outsider"], place["place"], {})


def test_a_single_visitor_is_named_alone(
    app: Flask, client: FlaskClient, people: dict[str, int], place: dict[str, int]
) -> None:
    signin(client, people, "reader")
    client.post(f"/places/{place['place']}/visits", data={"visit_date": "2026-05-03"})
    with app.app_context(), app.test_request_context():
        castle = db.session.get(visits.Place, place["place"])
        panel = visits.panel(people["tenant"], people["creator"], castle)
    assert panel["summary"] == "Last visited May 3, 2026 by reader"
    assert panel["visited"] is False
