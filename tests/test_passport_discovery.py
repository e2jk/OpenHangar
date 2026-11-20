"""Discovery defaults, distances, explicit fuel assumptions and permission boundaries."""

import re
from datetime import date
from typing import Any

from flask import Flask
from flask.testing import FlaskClient
from models import (
    Aircraft,
    Flight,
    PassportAirfield,
    PassportAirfieldIdentity,
    PersonalMinimumsItem,
    PersonalMinimumsRevision,
    PersonalMinimumsSection,
    PersonalMinimumsTag,
    PilotProfile,
    Role,
    TenantUser,
    db,
)
from passports.catalogue import create_custom

from tests.test_passport_builder import signin
from tests.test_passport_foundation import catalogue
from tests.test_passport_management import owner

__all__ = ["catalogue", "owner"]


def test_discovery_page_and_aircraft_access(
    app: Flask, client: FlaskClient, owner: dict[str, int], catalogue: Any
) -> None:
    uid, tid, pid = owner["user"], owner["tenant"], owner["passport"]
    signin(client, uid)
    path = f"/passports/{pid}/where-next"
    assert "Choose a reference" in client.get(path).text
    with app.app_context():
        ac = Aircraft(
            tenant_id=tid,
            registration="OO-NEXT",
            make="Cessna",
            model="172",
            home_airfield_id=owner["field"],
            cruise_speed_knots=100,
            fuel_flow=20,
        )
        field = db.session.get(PassportAirfieldIdentity, owner["field"])
        field.latitude, field.longitude = 50, 4
        db.session.add_all([ac, PilotProfile(user_id=uid, home_airfield_id=field.id)])
        db.session.flush()
        aid = ac.id
        near = create_custom(
            tid, "Near destination", code="NEAR", latitude=50.1, longitude=4.1
        )
        db.session.add(
            PassportAirfield(tenant_id=tid, passport_id=pid, airfield_id=near.id)
        )
        flight = Flight(pic_user_id=uid, date=date(2025, 1, 1), arrival_icao="NEAR")
        db.session.add(flight)
        db.session.commit()
    assert client.get(path, query_string={"aircraft": aid}).status_code == 404
    with app.app_context():
        db.session.get(TenantUser, (uid, tid)).role = Role.ADMIN
        db.session.commit()
    automatic = client.get(
        path,
        query_string={
            "aircraft": aid,
            "origin": "",
            "speed": "",
            "flow": "",
            "usable_fuel": "80",
            "reserve": "60",
            "range": "1",
            "trip": "return",
        },
    )
    assert (
        "150.0 nm" in automatic.text
        and "Choose a reference airfield to find destinations." not in automatic.text
    )
    response = client.get(
        path,
        query_string={
            "aircraft": aid,
            "q": "Nam",
            "start": "latest",
            "mode": "time",
            "speed": 100,
            "limit": 120,
            "usable_fuel": 80,
            "flow": 20,
            "reserve": 60,
            "range": 1,
            "trip": "return",
        },
    )
    assert (
        response.status_code == 200
        and "150.0 nm" in response.text
        and "Near destination" in response.text
    )
    assert "No open, unvisited" not in response.text
    assert "unknown" in response.text.lower()
    for args, message in [
        ({"origin": "field:999999"}, "Choose a reference"),
        ({"origin": "catalogue:2"}, "no coordinates"),
        ({"mode": "time", "speed": ""}, "Enter a cruise speed"),
        ({"range": 1}, "range is unknown"),
        ({"speed": "NaN"}, "valid cruise"),
    ]:
        result = client.get(path, query_string=args)
        assert result.status_code == 200 and message in result.text
    assert (
        client.get(
            path,
            query_string={
                "mode": "invalid",
                "start": "home",
                "origin": f"field:{owner['field']}",
            },
        ).status_code
        == 200
    )
    assert client.get(path, query_string={"aircraft": "9" * 80}).status_code == 404
    signin(client, owner["other"])
    assert client.get(path).status_code == 404


def test_discovery_uses_published_reserve_and_new_aircraft_home(
    app: Flask, client: FlaskClient, owner: dict[str, int], catalogue: Any
) -> None:
    signin(client, owner["user"])
    with app.app_context():
        db.session.get(TenantUser, (owner["user"], owner["tenant"])).role = Role.ADMIN
        rev = PersonalMinimumsRevision(
            user_id=owner["user"], revision_number=1, status="active"
        )
        db.session.add(rev)
        db.session.flush()
        section = PersonalMinimumsSection(revision_id=rev.id, title="Fuel")
        db.session.add(section)
        db.session.flush()
        db.session.add(
            PersonalMinimumsItem(
                section_id=section.id,
                label="Reserve",
                semantic_tag=PersonalMinimumsTag.MIN_FUEL_RESERVE_MINUTES,
                numeric_value=45,
            )
        )
        db.session.commit()
    # Materialise a catalogue home while creating an aircraft with required fields.
    response = client.post(
        "/aircraft/new",
        data={
            "registration": "OO-NEW",
            "make": "Cessna",
            "model": "172",
            "home_airfield": "HEL",
            "cruise_speed_knots": "100",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        ac = Aircraft.query.filter_by(registration="OO-NEW").one()
        aid = ac.id
        assert ac.home_airfield.source_id == "4"
    response = client.get(
        f"/passports/{owner['passport']}/where-next", query_string={"aircraft": aid}
    )
    assert response.status_code == 200 and "published personal minimum" in response.text


def test_discovery_slider_range_follows_mode(
    client: FlaskClient, owner: dict[str, int]
) -> None:
    """The slider covers a practical range for each mode (500 nm or 300
    minutes) rather than the 21,600-minute server cap, which pinned 100 nm
    to its far left. The number field keeps the server's wider limit."""
    signin(client, owner["user"])
    path = f"/passports/{owner['passport']}/where-next"
    for mode, maximum in [("distance", "500"), ("time", "300")]:
        html = client.get(path, query_string={"mode": mode}).text
        slider = re.search(r'<input[^>]*id="discovery-slider"[^>]*>', html)
        assert slider is not None
        assert f'max="{maximum}"' in slider.group(0)
        assert 'data-max-distance="500"' in slider.group(0)
        assert 'data-max-time="300"' in slider.group(0)
        limit = re.search(r'<input[^>]*id="discovery-limit"[^>]*>', html)
        assert limit is not None and 'max="21600"' in limit.group(0)
