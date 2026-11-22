"""Places in the account data export and as GPX/KML waypoints (42g)."""

import io
import json
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import PlaceVisit, db
from places.access import PlaceError
from places.collections import create_collection, set_member
from places.management import save_place

from tests.test_place_collections import people
from tests.test_place_management import form, upload
from tests.test_place_sharing import signin

__all__ = ["people"]

GPX = {"n": "http://www.topografix.com/GPX/1/1"}


@pytest.fixture(autouse=True)
def uploads(app: Flask, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(app.config, "UPLOAD_FOLDER", str(tmp_path))


@pytest.fixture
def shared(app: Flask, people: dict[str, int]) -> dict[str, int]:
    p = people
    with app.app_context():
        t = p["tenant"]
        ours = create_collection(t, p["creator"], "Our places")
        set_member(t, p["creator"], ours.id, p["reader"], can_edit=True)
        mine = save_place(
            t,
            p["creator"],
            ours.id,
            form(title="Castle", latitude="50.5", longitude="4.2"),
            tags=["Museum"],
            photo=upload(),
        )
        theirs = save_place(
            t,
            p["reader"],
            ours.id,
            form(title="Their lake", latitude="51", longitude="5"),
        )
        link_only = save_place(
            t,
            p["creator"],
            ours.id,
            {"external_url": "https://example.org/reel", "title": ""},
        )
        private = create_collection(t, p["outsider"], "Outsider only")
        save_place(t, p["outsider"], private.id, form(title="SECRET spot"))
        db.session.add_all(
            [
                PlaceVisit(
                    place_id=theirs.id,
                    user_id=p["creator"],
                    visit_date=date(2026, 6, 1),
                    note="Swim",
                ),
                PlaceVisit(
                    place_id=mine.id, user_id=p["reader"], visit_date=date(2026, 6, 2)
                ),
            ]
        )
        db.session.commit()
        return {
            "collection": ours.id,
            "mine": mine.id,
            "theirs": theirs.id,
            "link_only": link_only.id,
            "private": private.id,
        }


def _account_section(client: FlaskClient) -> tuple[dict, dict[str, bytes]]:
    response = client.get("/passports/export")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    account = json.loads(files["account-data.json"])
    return account["sections"]["places"]["data"], files


def test_export_holds_own_places_and_visits_only(
    client: FlaskClient, people: dict[str, int], shared: dict[str, int]
) -> None:
    signin(client, people)
    data, files = _account_section(client)
    titles = {place["title"] for place in data["places"]}
    assert titles == {"Castle", None}
    castle = next(p for p in data["places"] if p["title"] == "Castle")
    assert castle["tags"] == ["Museum"] and castle["latitude"] == 50.5
    assert files[castle["photo"]]
    assert castle["included"] is True
    link_only = next(p for p in data["places"] if p["title"] is None)
    assert link_only["external_url"] == "https://example.org/reel"
    assert link_only["latitude"] is None and "photo" not in link_only
    # Own visits, also to another member's place; never theirs.
    assert data["visits"] == [
        {
            "place_id": shared["theirs"],
            "place": "Their lake",
            "date": "2026-06-01",
            "note": "Swim",
            "flight_id": None,
        }
    ]
    names = {c["name"] for c in data["collections"]}
    assert "Our places" in names and "Outsider only" not in names
    assert "SECRET" not in json.dumps(data)


def test_a_personal_account_exports_its_own_visits(
    client: FlaskClient, people: dict[str, int], shared: dict[str, int]
) -> None:
    signin(client, people, "reader")
    data, _files = _account_section(client)
    assert [p["title"] for p in data["places"]] == ["Their lake"]
    assert [v["place"] for v in data["visits"]] == ["Castle"]


def test_gpx_and_kml_hold_the_located_places_of_the_filtered_list(
    client: FlaskClient, people: dict[str, int], shared: dict[str, int]
) -> None:
    signin(client, people)
    response = client.get("/places/export.gpx")
    assert response.status_code == 200 and response.mimetype == "application/gpx+xml"
    assert 'filename="places.gpx"' in response.headers["Content-Disposition"]
    names = {
        n.findtext("n:name", namespaces=GPX)
        for n in ET.fromstring(response.data).findall("n:wpt", GPX)
    }
    assert names == {"Castle", "Their lake"}  # no location, no waypoint
    tagged = client.get(
        "/places/export.kml",
        query_string={"collection": shared["collection"], "visited": "visited"},
    )
    assert tagged.status_code == 200 and b"Their lake" in tagged.data
    assert b"Castle" not in tagged.data
    assert client.get("/places/export.zip").status_code == 404
    # Another member's private collection, or a missing one, stays hidden.
    for collection in (shared["private"], 999999):
        assert (
            client.get(
                "/places/export.gpx", query_string={"collection": collection}
            ).status_code
            == 404
        )


def test_unnamed_located_places_get_a_neutral_waypoint_name(
    app: Flask, client: FlaskClient, people: dict[str, int], shared: dict[str, int]
) -> None:
    with app.app_context():
        save_place(
            people["tenant"],
            people["creator"],
            shared["collection"],
            form(title="", latitude="49", longitude="3"),
        )
        db.session.commit()
    signin(client, people)
    assert b"Unnamed place" in client.get("/places/export.gpx").data


def test_export_requires_a_membership(
    client: FlaskClient, people: dict[str, int]
) -> None:
    from unittest.mock import patch

    signin(client, people)
    with patch("places.exports.current_membership", return_value=None):
        assert client.get("/places/export.gpx").status_code == 403
    with patch("places.exports.list_places", side_effect=PlaceError("x")):
        assert client.get("/places/export.gpx").status_code == 404
