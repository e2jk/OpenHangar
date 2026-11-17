"""Discovery defaults, distances, explicit fuel assumptions and permission boundaries."""

from datetime import date, timedelta
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import (
    Aircraft,
    Flight,
    PassportAirfieldIdentity,
    PassportArrivalExclusion,
    PassportParticipant,
    PersonalMinimumsItem,
    PersonalMinimumsRevision,
    PersonalMinimumsSection,
    PersonalMinimumsTag,
    PilotProfile,
    Role,
    TenantUser,
    db,
)
from passports import discovery as d
from passports.catalogue import add_alias, create_custom
from passports.management import BuilderError

from tests.test_passport_builder import signin
from tests.test_passport_foundation import catalogue
from tests.test_passport_management import owner

__all__ = ["catalogue", "owner"]


def test_home_resolution_and_numeric_bounds(
    app: Flask, owner: dict[str, int], catalogue: Any
) -> None:
    with app.app_context():
        t = owner["tenant"]
        assert d.resolve_home(t, "") is None
        assert d.store_home(None, t) is None
        field = d.resolve_home(t, " test ")
        assert field.id == owner["field"]
        assert d.store_home(field, t) == field.id
        assert d.resolve_home(t, f"field:{field.id}").id == field.id
        row = d.resolve_home(t, "EBNM")
        fid = d.store_home(row, t)
        assert db.session.get(PassportAirfieldIdentity, fid).source == "ourairports"
        for value in (
            "field:9999",
            "field:" + "9" * 80,
            "UNKNOWN",
            "ZZZZ",
            "field:bad",
        ):
            with pytest.raises(BuilderError):
                d.resolve_home(t, value)
        other = create_custom(t, "Other")
        add_alias(other, "TEST")
        db.session.flush()
        with pytest.raises(BuilderError):
            d.resolve_home(t, "TEST")
        assert d.positive("", "speed") is None
        assert d.positive("0", "reserve", zero=True) == 0
        for value in ("nan", "inf", "bad", "-1", "0", "10000"):
            with pytest.raises(BuilderError):
                d.positive(value, "speed")
        assert abs(d.distance_nm(0, 0, 0, 1) - 60.04) < 0.1
        assert abs(d.distance_nm(0, 179.5, 0, -179.5) - 60.04) < 0.1
        assert d.distance_nm(90, 0, -90, 0) > 10800
        assert d.origin_choices(t, "x") == []
        assert any(f["name"] == "Namur" for f in d.origin_choices(t, "Nam"))


def test_filtering_range_and_reserve_preference(
    app: Flask, owner: dict[str, int]
) -> None:
    origin = {"latitude": 0, "longitude": 0}
    fields = [
        {
            "name": "Near",
            "target_id": 1,
            "latitude": 0,
            "longitude": 1,
            "visited": False,
        },
        {
            "name": "Far",
            "target_id": 2,
            "latitude": 0,
            "longitude": 10,
            "visited": False,
        },
        {
            "name": "Visited",
            "target_id": 3,
            "latitude": 0,
            "longitude": 1,
            "visited": True,
        },
        {
            "name": "Closed",
            "target_id": 4,
            "latitude": 0,
            "longitude": 1,
            "visited": False,
            "is_closed": True,
        },
        {
            "name": "Unknown",
            "target_id": 5,
            "latitude": None,
            "longitude": None,
            "visited": False,
        },
    ]
    rows, missing = d.nearby({"rows": fields}, origin, 120, 100, "distance", None)
    assert [r["name"] for r in rows] == ["Near"] and missing == 1
    assert 30 < rows[0]["minutes"] < 31
    assert d.nearby({"rows": fields}, None, 120, 100, "distance", None)[0] == []
    assert d.nearby({"rows": fields}, origin, None, 100, "time", None)[0] == []
    assert d.nearby({"rows": fields}, origin, 120, 20, "time", None)[0] == []
    assert d.nearby({"rows": fields}, origin, 120, 100, "distance", 20)[0] == []
    assert d.theoretical_range(120, 80, 20, 60, False) == 360
    assert d.theoretical_range(120, 80, 20, 60, True) == 180
    assert d.theoretical_range(None, 80, 20, 60, False) is None
    assert d.theoretical_range(120, 80, 0, 60, False) is None
    assert d.theoretical_range(-1, 80, 20, 60, False) is None
    assert d.theoretical_range(float("nan"), 80, 20, 60, False) is None
    assert d.theoretical_range(120, 20, 20, 60, False) is None
    with app.app_context():
        assert d.reserve_preference(owner["user"]) is None
        rev = PersonalMinimumsRevision(
            user_id=owner["user"], revision_number=1, status="active"
        )
        db.session.add(rev)
        db.session.flush()
        section = PersonalMinimumsSection(revision_id=rev.id, title="Fuel")
        db.session.add(section)
        db.session.flush()
        for value in (30, 45, None, -1):
            db.session.add(
                PersonalMinimumsItem(
                    section_id=section.id,
                    label="Reserve",
                    semantic_tag=PersonalMinimumsTag.MIN_FUEL_RESERVE_MINUTES,
                    numeric_value=value,
                )
            )
        db.session.commit()
        assert d.reserve_preference(owner["user"]) == 45


def test_home_forms_defaults_and_permissions(
    app: Flask, client: FlaskClient, owner: dict[str, int], catalogue: Any
) -> None:
    uid, tid = owner["user"], owner["tenant"]
    signin(client, uid)
    assert (
        client.post("/pilot/profile", data={"home_airfield": "EBNM"}).status_code == 302
    )
    assert "field:" in client.get("/pilot/profile").text
    assert (
        client.post("/pilot/profile", data={"home_airfield": "BAD"}).status_code == 422
    )
    with app.app_context():
        db.session.get(TenantUser, (uid, tid)).role = Role.ADMIN
        db.session.commit()
    data = {
        "registration": "OO-HOME",
        "make": "Cessna",
        "model": "172",
        "home_airfield": "EBNM",
        "cruise_speed_knots": "110",
    }
    assert client.post("/aircraft/new", data=data).status_code == 302
    with app.app_context():
        ac = Aircraft.query.filter_by(registration="OO-HOME").one()
        aid = ac.id
        assert (
            ac.home_airfield_id
            == PilotProfile.query.filter_by(user_id=uid).one().home_airfield_id
        )
        assert ac.cruise_speed_knots == 110
        # A global pilot profile must not leak another tenant's home.
        assert d.home_descriptor(PilotProfile.query.one(), tid + 1) is None
    path = f"/aircraft/{aid}/edit"
    assert client.get(path).status_code == 200
    assert (
        client.post(path, data=data | {"cruise_speed_knots": "nan"}).status_code == 200
    )
    assert client.post(path, data=data | {"home_airfield": "BAD"}).status_code == 200
    assert (
        client.post(
            path, data=data | {"cruise_speed_knots": "", "home_airfield": ""}
        ).status_code
        == 302
    )
    with app.app_context():
        ac = db.session.get(Aircraft, aid)
        assert ac.cruise_speed_knots is None and ac.home_airfield_id is None
    signin(client, owner["other"])
    assert client.post(path, data=data).status_code == 403


def test_latest_arrival_scoping_aliases_and_exclusion(
    app: Flask, owner: dict[str, int], catalogue: Any
) -> None:
    with app.app_context():
        uid, tid = owner["user"], owner["tenant"]
        assert d.latest_arrival(tid, uid, None) is None
        part = PassportParticipant.query.filter_by(user_id=uid).one()
        rows = [
            Flight(
                date=date(2025, 1, 1) + timedelta(days=i),
                pic_user_id=uid,
                arrival_icao=code,
                flight_time=duration,
            )
            for i, (code, duration) in enumerate(
                [
                    ("EBNM", None),
                    ("TEST", None),
                    ("ZZZZ", None),
                    ("", None),
                    ("TEST", 0),
                    ("MISSING", None),
                ]
            )
        ]
        db.session.add_all(rows)
        db.session.commit()
        assert d.latest_arrival(tid, uid, part.id)["key"] == f"field:{owner['field']}"
        db.session.add(
            PassportArrivalExclusion(participant_id=part.id, flight_id=rows[1].id)
        )
        db.session.commit()
        assert d.latest_arrival(tid, uid, part.id)["key"] == "catalogue:1"
        field = create_custom(tid, "Ambiguous")
        add_alias(field, "EBNM", valid_from=date(2026, 1, 1))
        db.session.commit()
        assert d.latest_arrival(tid, uid, part.id) is None
        db.session.get(Flight, rows[0].id).pic_user_id = owner["other"]
        db.session.commit()
        assert d.latest_arrival(tid, uid, part.id) is None
