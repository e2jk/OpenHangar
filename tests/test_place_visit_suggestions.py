"""After logging a flight that landed at an airfield linked to one of your
places: "You landed at EBXX, 4 km from X — record a visit?" (42e), once per
flight and place, never recorded by itself."""

from datetime import date, timedelta

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import Flight, PlaceVisit, PlaceVisitDismissal, db
from passports.catalogue import create_custom
from places import visits
from places.airfields import add_link
from places.collections import create_collection, set_member
from places.management import save_place

from tests.auth_helpers import set_membership_flags
from tests.test_place_collections import people
from tests.test_place_sharing import signin

__all__ = ["people"]


@pytest.fixture
def landed(app: Flask, people: dict[str, int]) -> dict[str, int]:
    """Castle (4 km from EBCS) in a collection shared with the reader; a
    flight to EBCS by the creator with the reader as second crew, an older
    one, one elsewhere and one in the future."""
    p = people
    with app.app_context():
        t = p["tenant"]
        trips = create_collection(t, p["creator"], "Trips")
        set_member(t, p["creator"], trips.id, p["reader"], can_edit=False)
        field = create_custom(
            t, "Castle strip", code="EBCS", latitude=50.0, longitude=4.0
        )
        db.session.flush()
        castle = save_place(
            t,
            p["creator"],
            trips.id,
            {"title": "Castle", "latitude": "50.036", "longitude": "4.0"},
        )
        unlocated = save_place(t, p["creator"], trips.id, {"title": "Fair"})
        add_link(t, p["creator"], castle.id, field.id)
        add_link(t, p["creator"], unlocated.id, field.id)
        today = date.today()
        flights = [
            Flight(
                pic_user_id=p["creator"],
                second_crew_user_id=p["reader"],
                date=today - timedelta(days=1),
                arrival_icao="ebcs",
            ),
            Flight(
                pic_user_id=p["creator"],
                date=today - timedelta(days=90),
                arrival_icao="EBCS",
            ),
            Flight(pic_user_id=p["creator"], date=today, arrival_icao="EBXX"),
            Flight(
                pic_user_id=p["creator"],
                date=today + timedelta(days=3),
                arrival_icao="EBCS",
            ),
            Flight(pic_user_id=p["outsider"], date=today, arrival_icao="EBCS"),
        ]
        db.session.add_all(flights)
        db.session.commit()
        return {
            "castle": castle.id,
            "fair": unlocated.id,
            "flight": flights[0].id,
            "other_flight": flights[4].id,
        }


def _suggestions(app: Flask, people: dict[str, int], name: str) -> list[dict]:
    with app.app_context():
        return visits.flight_suggestions(people["tenant"], people[name])


@pytest.mark.parametrize("name", ["creator", "reader"])
def test_a_recent_landing_suggests_each_linked_place_once(
    app: Flask, people: dict[str, int], landed: dict[str, int], name: str
) -> None:
    rows = _suggestions(app, people, name)
    assert [(r["flight_id"], r["place"]) for r in rows] == [
        (landed["flight"], "Castle"),
        (landed["flight"], "Fair"),
    ]
    castle = rows[0]
    assert castle["code"] == "EBCS" and round(castle["distance_km"]) == 4
    assert rows[1]["distance_km"] is None


def _pilot(app: Flask, people: dict[str, int], name: str = "creator") -> None:
    from models import Role, TenantUser

    with app.app_context():
        TenantUser.query.filter_by(
            user_id=people[name], tenant_id=people["tenant"]
        ).one().role = Role.PILOT
        db.session.commit()


def test_shown_on_the_logbook_and_recorded_with_one_click(
    app: Flask, client: FlaskClient, people: dict[str, int], landed: dict[str, int]
) -> None:
    _pilot(app, people)
    signin(client, people)
    page = client.get("/pilot/logbook").get_data(as_text=True)
    assert "You landed at EBCS" in page and "km from Castle" in page
    assert "the airfield for Fair" in page
    response = client.post(
        f"/places/{landed['castle']}/visits/from-flight/{landed['flight']}",
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/pilot/logbook")
    with app.app_context():
        visit = PlaceVisit.query.filter_by(place_id=landed["castle"]).one()
        flight = db.session.get(Flight, landed["flight"])
        assert visit.flight_id == flight.id and visit.visit_date == flight.date
    assert [r["place"] for r in _suggestions(app, people, "creator")] == ["Fair"]
    # The second crew member still gets their own suggestion.
    assert len(_suggestions(app, people, "reader")) == 2


def test_dismissed_once_and_for_all(
    app: Flask, client: FlaskClient, people: dict[str, int], landed: dict[str, int]
) -> None:
    signin(client, people)
    url = f"/places/{landed['fair']}/visits/from-flight/{landed['flight']}/dismiss"
    for _attempt in range(2):
        assert client.post(url, data={"aircraft": "not a number"}).status_code == 302
    with app.app_context():
        assert PlaceVisitDismissal.query.count() == 1
    assert [r["place"] for r in _suggestions(app, people, "creator")] == ["Castle"]
    response = client.post(url, data={"aircraft": "7"})
    assert response.headers["Location"].endswith("/flights")


def test_never_for_someone_elses_flight_or_place(
    app: Flask, client: FlaskClient, people: dict[str, int], landed: dict[str, int]
) -> None:
    signin(client, people)
    for url in (
        f"/places/{landed['castle']}/visits/from-flight/{landed['other_flight']}",
        f"/places/{landed['castle']}/visits/from-flight/999999/dismiss",
    ):
        assert client.post(url).status_code == 404
    signin(client, people, "outsider")
    assert _suggestions(app, people, "outsider") == []
    assert (
        client.post(
            f"/places/{landed['castle']}/visits/from-flight/{landed['other_flight']}"
        ).status_code
        == 404
    )


def test_a_view_only_account_is_refused_with_a_message(
    app: Flask, client: FlaskClient, people: dict[str, int], landed: dict[str, int]
) -> None:

    with app.app_context():
        set_membership_flags(people["reader"], view_only=True)
        db.session.commit()
    signin(client, people, "reader")
    for suffix in ("", "/dismiss"):
        response = client.post(
            f"/places/{landed['castle']}/visits/from-flight/{landed['flight']}{suffix}"
        )
        assert response.status_code == 302
    with app.app_context():
        assert PlaceVisit.query.count() == 0 and PlaceVisitDismissal.query.count() == 0


def test_nothing_to_suggest_without_links_or_membership(
    app: Flask, client: FlaskClient, people: dict[str, int]
) -> None:
    from unittest.mock import patch

    assert _suggestions(app, people, "creator") == []
    _pilot(app, people)
    signin(client, people)
    assert "You landed at" not in client.get("/pilot/logbook").get_data(as_text=True)
    with (
        app.app_context(),
        patch("places.airfields.linked_airfields", side_effect=visits.PlaceError("x")),
    ):
        assert visits.flight_suggestions(people["tenant"], people["creator"]) == []
    with patch("places.visit_routes.current_membership", return_value=None):
        page = client.get("/pilot/logbook")
        assert page.status_code == 200 and "You landed at" not in page.get_data(
            as_text=True
        )


def test_the_aircraft_logbook_shows_them_too(
    app: Flask, client: FlaskClient, people: dict[str, int], landed: dict[str, int]
) -> None:
    from models import Aircraft, Role, TenantUser

    with app.app_context():
        TenantUser.query.filter_by(
            user_id=people["creator"], tenant_id=people["tenant"]
        ).one().role = Role.ADMIN
        aircraft = Aircraft(
            tenant_id=people["tenant"], registration="OO-VIS", make="T", model="M"
        )
        other = Aircraft(
            tenant_id=people["tenant"], registration="OO-OTH", make="T", model="M"
        )
        db.session.add_all([aircraft, other])
        db.session.flush()
        db.session.get(Flight, landed["flight"]).aircraft_id = aircraft.id
        db.session.commit()
        aid, oid = aircraft.id, other.id
    signin(client, people)
    page = client.get(f"/aircraft/{aid}/flights").get_data(as_text=True)
    assert "You landed at EBCS" in page
    assert f'name="aircraft" value="{aid}"' in page
    # Another aircraft's logbook offers only that aircraft's flights.
    other_page = client.get(f"/aircraft/{oid}/flights").get_data(as_text=True)
    assert "You landed at" not in other_page


def test_at_most_three_suggestions_at_once(
    app: Flask, people: dict[str, int], landed: dict[str, int]
) -> None:
    with app.app_context():
        db.session.add(
            Flight(
                pic_user_id=people["creator"],
                date=date.today() - timedelta(days=2),
                arrival_icao="EBCS",
            )
        )
        db.session.commit()
    assert len(_suggestions(app, people, "creator")) == visits.MAX_SUGGESTIONS


def test_an_airfields_former_code_matches_flights_of_its_time(
    app: Flask, people: dict[str, int], landed: dict[str, int]
) -> None:
    from models import PassportAirfieldAlias, PassportAirfieldIdentity

    today = date.today()
    with app.app_context():
        field = PassportAirfieldIdentity.query.filter_by(name="Castle strip").one()
        db.session.add_all(
            [
                PassportAirfieldAlias(
                    airfield_id=field.id,
                    code="EBOLD",
                    valid_until=today - timedelta(days=5),
                ),
                Flight(
                    pic_user_id=people["creator"],
                    date=today - timedelta(days=10),
                    arrival_icao="EBOLD",
                ),
                Flight(pic_user_id=people["creator"], date=today, arrival_icao="EBOLD"),
            ]
        )
        db.session.query(Flight).filter(
            db.func.upper(Flight.arrival_icao) == "EBCS"
        ).update({"arrival_icao": "EBXX"}, synchronize_session=False)
        db.session.commit()
    rows = _suggestions(app, people, "creator")
    # Only the flight from when EBOLD was this airfield's code.
    assert {r["date"] for r in rows} == {today - timedelta(days=10)}
    assert {r["code"] for r in rows} == {"EBOLD"}


def test_manual_visit_suppresses_only_its_own_place_date_and_author(
    app: Flask, client: FlaskClient, people: dict[str, int], landed: dict[str, int]
) -> None:
    with app.app_context():
        flight = db.session.get(Flight, landed["flight"])
        visit = visits.record(
            people["tenant"],
            people["creator"],
            landed["castle"],
            {"visit_date": flight.date.isoformat()},
        )
        visits.record(
            people["tenant"],
            people["creator"],
            landed["fair"],
            {"visit_date": (flight.date - timedelta(days=1)).isoformat()},
        )
        db.session.commit()
        visit_id = visit.id

    assert [row["place"] for row in _suggestions(app, people, "creator")] == ["Fair"]
    assert len(_suggestions(app, people, "reader")) == 2
    signin(client, people)
    # A suggestion already open in another tab must leave the manual visit intact.
    assert (
        client.post(
            f"/places/{landed['castle']}/visits/from-flight/{landed['flight']}"
        ).status_code
        == 302
    )
    with app.app_context():
        visit = PlaceVisit.query.filter_by(place_id=landed["castle"]).one()
        assert visit.id == visit_id and visit.flight_id is None
