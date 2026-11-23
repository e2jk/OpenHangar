"""Portable destinations use the current viewer's selection, never private evidence."""

from datetime import date
from html import escape
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import Flight, PassportAirfield, PassportAirfieldIdentity, TenantUser, db
from passports import waypoints
from passports.catalogue import create_custom

from tests.test_passport_builder import signin
from tests.test_passport_groups import group
from tests.test_passport_management import owner

__all__ = ["group", "owner"]

GPX = {"n": "http://www.topografix.com/GPX/1/1"}
KML = {"n": "http://www.opengis.net/kml/2.2"}


def row(**values: Any) -> dict[str, Any]:
    return {
        "name": "Example airfield",
        "ident": "TEST",
        "latitude": 50.5,
        "longitude": 4.5,
        "visited": False,
        "is_closed": False,
        "note": "PRIVATE NOTE",
        "friends": ["PRIVATE FRIEND"],
        "events": [{"aircraft": "PRIVATE AIRCRAFT"}],
        **values,
    }


@pytest.mark.parametrize(
    "fmt,ns,node", [("gpx", GPX, "n:wpt"), ("kml", KML, "n:Document/n:Placemark")]
)
def test_standard_waypoint_fixtures(
    app: Flask, fmt: str, ns: dict[str, str], node: str
) -> None:
    with app.app_context():
        output = waypoints.document(waypoints.select([row()]), fmt)
    actual = ET.fromstring(output)
    fixture = ET.parse(
        Path(__file__).parent / "fixtures" / "passport_waypoints" / ("unvisited." + fmt)
    ).getroot()
    assert actual.tag == fixture.tag and actual.attrib == fixture.attrib
    point = actual.find(node, ns)
    expected = fixture.find(node, ns)
    assert point is not None and expected is not None
    assert ET.tostring(point) == ET.tostring(expected).rstrip()
    assert b"PRIVATE" not in output
    assert len(list(actual.iter())) == len(list(fixture.iter()))


@pytest.mark.parametrize(
    "lat,lon",
    [
        (None, 4),
        (4, None),
        ("bad", 4),
        (float("nan"), 4),
        (4, float("inf")),
        (-91, 4),
        (4, 181),
    ],
)
def test_missing_or_invalid_coordinates_are_listed(lat: Any, lon: Any) -> None:
    result = waypoints.select([row(latitude=lat, longitude=lon)])
    assert result.points == [] and result.omitted == ["TEST — Example airfield"]


def test_own_stamps_closure_and_discovery_omissions() -> None:
    missing = row(name="Code-less field", ident=None, latitude=None)
    result = waypoints.select([row(), row(visited=True), row(is_closed=True), missing])
    assert len(result.points) == 1 and result.omitted == ["Code-less field"]
    selection = waypoints.select(
        [row()], missing_rows=[row(name="Out of range", longitude=140), missing]
    )
    assert len(selection.points) == 1 and selection.omitted == ["Code-less field"]


@pytest.mark.parametrize("fmt", ["gpx", "kml"])
def test_escaped_names_xml_controls_empty_files_and_date_line(
    app: Flask, fmt: str
) -> None:
    name = 'Restaurant <img src=x onerror="alert(1)"> & field\x01\ud800'
    with app.app_context():
        output = waypoints.document(
            waypoints.select(
                [
                    row(name=name, ident="", latitude=90, longitude=180),
                    row(latitude=None, name=name),
                ]
            ),
            fmt,
        )
        empty = waypoints.document(waypoints.select([]), fmt)
        with pytest.raises(ValueError, match="Unsupported"):
            waypoints.document(waypoints.select([]), "zip")
    root = ET.fromstring(output)
    assert b"PRIVATE" not in output and b"\x01" not in output
    if fmt == "gpx":
        point = root.find("n:wpt", GPX)
        assert point is not None and point.attrib == {
            "lat": "90.00000000",
            "lon": "-180.00000000",
        }
        assert point.findtext("n:name", namespaces=GPX) == name[:-2]
        assert "Omitted without usable coordinates" in root.findtext(
            "n:metadata/n:desc", namespaces=GPX
        )
    else:
        assert root.findtext(
            "n:Document/n:Placemark/n:description", namespaces=KML
        ) == escape(name[:-2])
        assert (
            root.findtext(
                "n:Document/n:Placemark/n:Point/n:coordinates", namespaces=KML
            )
            == "180.00000000,90.00000000"
        )
    assert len(list(ET.fromstring(empty).iter())) < len(list(root.iter()))


def test_private_routes_discovery_filters_and_safe_download_links(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    s = owner
    with app.app_context():
        field = db.session.get(PassportAirfieldIdentity, s["field"])
        field.latitude, field.longitude = 50.5, 4.5
        near = create_custom(
            s["tenant"],
            "Near & private label",
            code="NEAR",
            latitude=50.51,
            longitude=4.51,
        )
        missing = create_custom(s["tenant"], "No coordinates", code="MISS")
        far = create_custom(
            s["tenant"], "Far away", code="FAR", latitude=60, longitude=4
        )
        for f in (near, missing, far):
            db.session.add(
                PassportAirfield(
                    tenant_id=s["tenant"], passport_id=s["passport"], airfield_id=f.id
                )
            )
        db.session.commit()
    signin(client, s["user"])
    path = f"/passports/{s['passport']}/"
    page = client.get(
        path,
        query_string={"state": "unvisited", "_external": "1", "_scheme": "javascript"},
    )
    assert 'hx-boost="false">Download GPX waypoints' in page.text
    assert "javascript:" not in page.text
    assert "MISS — No coordinates" in page.text
    response = client.get(path, query_string={"download": "gpx", "state": "unvisited"})
    assert response.status_code == 200 and response.mimetype == "application/gpx+xml"
    assert response.cache_control.private and response.cache_control.no_store
    assert (
        response.headers["Content-Disposition"]
        == f'attachment; filename="passport-{s["passport"]}-unvisited.gpx"'
    )
    root = ET.fromstring(response.data)
    assert {
        n.findtext("n:name", namespaces=GPX) for n in root.findall("n:wpt", GPX)
    } == {"FAR", "TEST", "NEAR"}
    assert client.get(path, query_string={"download": "zip"}).status_code == 400
    assert not ET.fromstring(
        client.get(path, query_string={"download": "gpx", "state": "visited"}).data
    ).findall("n:wpt", GPX)
    # Nothing to download in that selection: no download button either.
    visited = client.get(path, query_string={"state": "visited"}).text
    assert "Download GPX waypoints" not in visited
    assert "Download KML waypoints" not in visited
    discovery = path + "where-next"
    assert (
        client.get(
            discovery, follow_redirects=True, query_string={"download": "gpx"}
        ).status_code
        == 400
    )
    result = client.get(
        discovery,
        follow_redirects=True,
        query_string={
            "download": "kml",
            "origin": f"field:{s['field']}",
            "limit": "10",
        },
    )
    assert (
        result.status_code == 200
        and result.mimetype == "application/vnd.google-earth.kml+xml"
    )
    assert (
        b"Far away" not in result.data
        and b"NEAR" in result.data
        and b"No coordinates" in result.data
    )
    assert (
        client.get(
            discovery,
            follow_redirects=True,
            query_string={
                "download": "gpx",
                "origin": f"field:{s['field']}",
                "speed": "nan",
            },
        ).status_code
        == 400
    )
    signin(client, s["other"])
    assert client.get(path, query_string={"download": "gpx"}).status_code == 404


def test_group_filters_keep_the_viewers_own_unvisited_state(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    s = group
    with app.app_context():
        f = db.session.get(PassportAirfieldIdentity, s["field"])
        f.latitude, f.longitude = 50.5, 4.5
        member = db.session.get(TenantUser, (s["other"], s["tenant"]))
        member.role = None
        member.access_scope = "personal"
        db.session.commit()
    signin(client, s["other"])
    with app.app_context():
        field = create_custom(
            s["tenant"],
            "Visited only by the organiser",
            code="NEAR",
            latitude=50.51,
            longitude=4.51,
        )
        db.session.add(
            PassportAirfield(
                tenant_id=s["tenant"], passport_id=s["new"], airfield_id=field.id
            )
        )
        db.session.add(
            Flight(pic_user_id=s["user"], date=date(2025, 6, 1), arrival_icao="NEAR")
        )
        db.session.commit()
    path = f"/passports/{s['new']}/"
    response = client.get(
        path, query_string={"download": "gpx", "state": "anyone", "participant": "2"}
    )
    assert response.status_code == 200
    root = ET.fromstring(response.data)
    assert [
        n.findtext("n:name", namespaces=GPX) for n in root.findall("n:wpt", GPX)
    ] == ["NEAR"]
    assert b"PRIVATE" not in response.data and b"Original pilot" not in response.data
    # Selecting the friend's own visited target still does not change who "you" is.
    empty = client.get(path, query_string={"download": "gpx", "participant": "1"})
    assert not ET.fromstring(empty.data).findall("n:wpt", GPX)
    signin(client, s["user"])
    other = client.get(
        path, query_string={"download": "gpx", "state": "anyone", "participant": "1"}
    )
    assert [
        n.findtext("n:name", namespaces=GPX)
        for n in ET.fromstring(other.data).findall("n:wpt", GPX)
    ] == ["TEST"]
