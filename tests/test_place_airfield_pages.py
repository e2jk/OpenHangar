"""Explicit nearby/search/manual airfield linking keeps collection privacy."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import Passport, PassportAirfield, Place, PlaceAirfield, db
from passports.catalogue import CatalogueAirfield, add_alias
from places import candidates
from places.access import PlaceError
from places.airfields import add_link, linked_airfields
from places.passport_links import passport_fields

from tests.auth_helpers import set_membership_flags
from tests.test_place_airfields import field, links, people
from tests.test_place_sharing import signin

__all__ = ["links", "people"]


@pytest.fixture(autouse=True)
def catalogue() -> Iterator[list[CatalogueAirfield]]:
    def row(
        key: str,
        name: str,
        kind: str,
        lat: float | None,
        lon: float | None,
        *,
        closed: bool = False,
    ) -> CatalogueAirfield:
        return CatalogueAirfield(
            key, key.upper(), None, name, lat, lon, "BE", "BE-WNA", kind, closed
        )

    rows = [
        row("near", "Near small airfield", "small_airport", 50.501, 4.2),
        row("water", "Near seaplane base", "seaplane_base", 50.502, 4.2),
        row("heli", "Nearby heliport", "heliport", 50.503, 4.2),
        row("closed", "Closed airfield", "small_airport", 50.504, 4.2, closed=True),
        row("far", "Far airport", "large_airport", 55, 4.2),
        row("missing", "No coordinates airport", "medium_airport", None, None),
    ]
    with patch("passports.catalogue.load_catalogue", return_value=tuple(rows)):
        yield rows


def test_nearby_suggestions_are_explicit_and_manual_search_allows_closed_and_heliports(
    app: Flask, client: FlaskClient, people: dict[str, int], links: dict[str, int]
) -> None:
    p, x = people, links
    signin(client, p)
    page = client.get(f"/places/{x['place']}/airfields")
    assert page.status_code == 200
    assert "Near small airfield" in page.text and "Near seaplane base" in page.text
    assert "Nearby heliport" not in page.text and "Closed airfield" not in page.text
    assert "Far airport" not in page.text and "No coordinates airport" not in page.text
    assert "Small airfield" in page.text
    assert (
        "Nearby heliport" in client.get(f"/places/{x['place']}/airfields?q=heli").text
    )
    assert (
        "Closed airfield" in client.get(f"/places/{x['place']}/airfields?q=closed").text
    )
    assert "Far airport" in client.get(f"/places/{x['place']}/airfields?q=far").text
    with app.app_context():
        assert PlaceAirfield.query.count() == 0
    assert (
        client.post(
            f"/places/{x['place']}/airfields",
            data={"action": "add", "key": "catalogue:near"},
        ).status_code
        == 302
    )
    page = client.get(f"/places/{x['place']}/airfields")
    assert "Already linked" in page.text
    with app.app_context():
        row = linked_airfields(p["tenant"], p["creator"])[0]
        fid = row["airfield_id"]
        assert row["public_catalogue"] and row["name"] == "Near small airfield"
    assert (
        client.post(
            f"/places/{x['place']}/airfields",
            data={
                "action": "update",
                "airfield": fid,
                "note": "Taxi then walk",
                "preferred": "yes",
            },
        ).status_code
        == 302
    )
    assert "Taxi then walk" in client.get(f"/places/{x['place']}/airfields").text
    bad = client.post(
        f"/places/{x['place']}/airfields",
        data={"action": "update", "airfield": fid, "note": "x" * 2001},
    )
    assert bad.status_code == 400 and "x" * 2001 in bad.text
    assert (
        client.post(
            f"/places/{x['place']}/airfields",
            data={"action": "remove", "airfield": fid},
        ).status_code
        == 302
    )
    with app.app_context():
        assert PlaceAirfield.query.count() == 0


def test_unlocated_place_can_search_and_link_without_geocoding(
    app: Flask, client: FlaskClient, people: dict[str, int], links: dict[str, int]
) -> None:
    p, x = people, links
    signin(client, p)
    page = client.get(f"/places/{x['unlocated']}/airfields")
    assert page.status_code == 200 and "location to see nearby suggestions" in page.text
    assert "Near small airfield" not in page.text
    page = client.get(f"/places/{x['unlocated']}/airfields?q=near+small")
    assert "Near small airfield" in page.text
    assert (
        client.post(
            f"/places/{x['unlocated']}/airfields",
            data={"action": "add", "key": "catalogue:near"},
        ).status_code
        == 302
    )
    with app.app_context():
        row = linked_airfields(p["tenant"], p["creator"], place_ids=[x["unlocated"]])[0]
        assert row["distance_km"] is None


def test_personal_members_can_link_only_visible_metadata_and_readers_cannot_manage(
    app: Flask, client: FlaskClient, people: dict[str, int], links: dict[str, int]
) -> None:
    p, x = people, links
    for user in ("reader", "outsider", "admin"):
        signin(client, p, user)
        assert client.get(f"/places/{x['place']}/airfields").status_code == 404
        assert (
            client.post(
                f"/places/{x['place']}/airfields",
                data={"action": "add", "key": "catalogue:near"},
            ).status_code
            == 404
        )
    signin(client, p, "personal")
    page = client.get(f"/places/{x['place']}/airfields?q=Hidden")
    assert page.status_code == 200 and "Hidden airfield" not in page.text
    assert (
        client.post(
            f"/places/{x['place']}/airfields",
            data={"action": "add", "key": f"field:{x['hidden']}"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/places/{x['place']}/airfields",
            data={"action": "add", "key": "catalogue:near"},
        ).status_code
        == 302
    )
    with app.app_context():
        choices = candidates.available_airfields(p["tenant"], p["personal"])
        assert choices[f"field:{x['public']}"]["name"] == "Public name"
        add_link(p["tenant"], p["creator"], x["place"], x["field"])
        db.session.commit()
        choices = candidates.available_airfields(p["tenant"], p["personal"])
        assert f"field:{x['field']}" in choices
    assert client.get("/places/2147483647/airfields").status_code == 404


def test_custom_airfield_shares_only_its_explicit_details_and_allows_missing_coordinates(
    app: Flask, client: FlaskClient, people: dict[str, int], links: dict[str, int]
) -> None:
    p, x = people, links
    signin(client, p, "personal")
    url = f"/places/{x['place']}/airfields"
    assert (
        client.post(
            url,
            data={
                "action": "custom",
                "name": "Local microlight field",
                "code": "ULM99",
                "latitude": "50.5",
                "longitude": "4.2",
            },
        ).status_code
        == 302
    )
    assert "Local microlight field" in client.get(url).text
    assert (
        client.post(
            url, data={"action": "custom", "name": "Field to locate"}
        ).status_code
        == 302
    )
    with app.app_context():
        rows = linked_airfields(p["tenant"], p["personal"])
        assert {r["name"] for r in rows} == {
            "Local microlight field",
            "Field to locate",
        }
        assert (
            next(r for r in rows if r["name"] == "Field to locate")["distance_km"]
            is None
        )
    for data in (
        {"name": ""},
        {"name": "x" * 256},
        {"name": "Test", "code": "x" * 65},
        {"name": "Test", "code": "ULM99"},
        {"name": "Test", "latitude": "50"},
    ):
        assert client.post(url, data={"action": "custom", **data}).status_code == 400


@pytest.mark.parametrize(
    "args", ["radius=NaN", "radius=-1", "radius=201", "q=" + ("x" * 201)]
)
def test_invalid_search_inputs_are_refused(
    client: FlaskClient, people: dict[str, int], links: dict[str, int], args: str
) -> None:
    signin(client, people)
    assert client.get(f"/places/{links['place']}/airfields?{args}").status_code == 400


@pytest.mark.parametrize(
    "data",
    [
        {"action": "bad"},
        {"action": "add", "key": "catalogue:no-such-field"},
        {"action": "update", "airfield": "bad"},
        {"action": "remove", "airfield": "9" * 1000},
        {"action": "remove", "airfield": "2147483647"},
    ],
)
def test_invalid_airfield_actions_fail_without_a_server_error(
    client: FlaskClient,
    people: dict[str, int],
    links: dict[str, int],
    data: dict[str, str],
) -> None:
    signin(client, people)
    assert (
        client.post(f"/places/{links['place']}/airfields", data=data).status_code == 400
    )


def test_catalogue_code_conflicts_require_explicit_review(
    app: Flask, client: FlaskClient, people: dict[str, int], links: dict[str, int]
) -> None:
    p, x = people, links
    with app.app_context():
        existing = field(p["tenant"], "Alias owner")
        add_alias(existing, "NEAR")
        db.session.commit()
    signin(client, p)
    page = client.post(
        f"/places/{x['place']}/airfields",
        data={"action": "add", "key": "catalogue:near"},
    )
    assert page.status_code == 400 and "conflicts with an existing code" in page.text


def test_suggestion_radius_service_rejects_bad_limits(
    app: Flask, people: dict[str, int], links: dict[str, int]
) -> None:
    p, x = people, links
    with app.app_context():
        for radius in (0, -1, 201, float("inf")):
            with pytest.raises(PlaceError):
                candidates.suggestions(
                    p["tenant"], p["creator"], x["place"], radius_km=radius
                )


def map_payload(page: Any, key: str) -> list[dict]:
    import json
    import re
    from html import unescape

    match = re.search(rf'data-{key}="([^"]*)"', page.text)
    assert match is not None
    return json.loads(unescape(match[1]))


def test_map_links_filters_and_downloads_share_the_same_places(
    app: Flask, client: FlaskClient, people: dict[str, int], links: dict[str, int]
) -> None:
    from places.management import save_place

    from tests.test_place_management import form

    p, x = people, links
    with app.app_context():
        t, u = p["tenant"], p["creator"]
        b = save_place(t, u, x["other_collection"], form(title="Another museum"))
        unlinked = save_place(t, u, x["collection"], form(title="No runway yet"))
        no_coordinates = field(
            t, "Airfield without coordinates", latitude=None, longitude=None
        )
        for pid in (x["place"], b.id, x["unlocated"]):
            add_link(t, u, pid, x["field"])
        add_link(t, u, b.id, no_coordinates.id)
        db.session.commit()
        b_id, unlinked_id = b.id, unlinked.id
    signin(client, p)
    page = client.get("/places/?linked=linked")
    assert "No runway yet" not in page.text
    markers = map_payload(page, "airfields")
    assert len(markers) == 1 and len(markers[0]["places"]) == 3
    assert len(map_payload(page, "links")) == 2
    assert "Orange markers show linked airfields" in page.text
    assert "No linked airfield" in client.get("/places/?linked=unlinked").text
    assert (
        f'href="/places/{unlinked_id}"' in client.get("/places/?linked=unlinked").text
    )
    assert f'href="/places/{b_id}"' not in client.get("/places/?linked=unlinked").text
    assert len(map_payload(client.get("/places/?linked=unlinked"), "airfields")) == 0
    page = client.get(f"/places/{x['place']}")
    assert "Airfield" in page.text and "Manage airfield links" in page.text
    assert len(map_payload(page, "links")) == 1
    assert "Location still to add" in client.get(f"/places/{x['unlocated']}").text
    assert len(map_payload(client.get(f"/places/{x['unlocated']}"), "airfields")) == 1
    assert "source=places" in page.text
    page = client.get("/places/?linked=linked&location=located")
    assert "Download GPX" in page.text and 'hx-boost="false"' in page.text
    gpx = client.get("/places/export.gpx?linked=linked&location=located")
    assert gpx.status_code == 200 and "Another museum" in gpx.text
    assert "No runway yet" not in gpx.text
    signin(client, p, "reader")
    page = client.get("/places/?linked=linked")
    assert "Another museum" not in page.text
    assert len(map_payload(page, "airfields")[0]["places"]) == 1
    assert "Manage airfield links" not in client.get(f"/places/{x['place']}").text


def test_visit_panel_records_only_own_progress_and_readers_can_visit(
    app: Flask, client: FlaskClient, people: dict[str, int], links: dict[str, int]
) -> None:
    from models import PlaceVisit

    from tests.test_place_pages import points

    p, x = people, links
    signin(client, p)
    url = f"/places/{x['place']}"
    page = client.get(url)
    assert "Nobody has recorded a visit yet" in page.text and "I was here" in page.text
    assert client.post(url + "/visits", data={"note": "Nice cafe"}).status_code == 302
    page = client.get(url)
    assert "Last visited" in page.text and "Nice cafe" in page.text
    assert points(page)[0]["visited"] is True
    signin(client, p, "reader")
    page = client.get(url)
    assert "Nice cafe" in page.text and "I was here" in page.text
    assert "Correct or remove" not in page.text
    assert points(page)[0]["visited"] is False
    assert client.post(url + "/visits", data={}).status_code == 302
    assert points(client.get(url))[0]["visited"] is True
    with app.app_context():
        own = (
            PlaceVisit.query.filter_by(place_id=x["place"], user_id=p["reader"])
            .one()
            .id
        )
    assert client.post(url + f"/visits/{own}/delete", data={}).status_code == 302
    assert points(client.get(url))[0]["visited"] is False


def test_search_results_are_bounded_and_visible_materialised_rows_are_deduplicated(
    app: Flask, people: dict[str, int], links: dict[str, int]
) -> None:
    from dataclasses import replace

    p, x = people, links
    row = CatalogueAirfield(
        "many",
        "MANY",
        None,
        "Many airfields",
        50.501,
        4.2,
        "BE",
        "BE-WNA",
        "small_airport",
        False,
    )
    with (
        app.app_context(),
        patch(
            "passports.catalogue.load_catalogue",
            return_value=tuple(replace(row, source_id=str(i)) for i in range(250)),
        ),
    ):
        rows = candidates.suggestions(
            p["tenant"], p["creator"], x["place"], query="Many"
        )
        assert len(rows) == 200
    with app.app_context():
        candidates.select_airfield(
            p["tenant"], p["creator"], x["place"], "catalogue:near"
        )
        db.session.commit()
        rows = candidates.suggestions(
            p["tenant"], p["creator"], x["place"], query="Near small"
        )
        assert len(rows) == 1 and rows[0]["key"].startswith("field:")


def test_manual_heliports_and_closed_fields_are_searchable_but_never_suggested(
    app: Flask, people: dict[str, int], links: dict[str, int]
) -> None:
    p, x = people, links
    with app.app_context():
        heli = field(p["tenant"], "Manual helicopter site", airfield_type="heliport")
        closed = field(p["tenant"], "Manual retired field", is_closed=True)
        typed_closed = field(p["tenant"], "Manual closed type", airfield_type="closed")
        db.session.commit()
        keys = {f"field:{item.id}" for item in (heli, closed, typed_closed)}
        rows = candidates.suggestions(p["tenant"], p["creator"], x["place"])
        assert not keys.intersection(row["key"] for row in rows)
        searched = candidates.suggestions(
            p["tenant"], p["creator"], x["place"], query="Manual"
        )
        assert keys.issubset({row["key"] for row in searched})


def test_passport_place_suggestions_and_links(
    app: Flask, client: FlaskClient, people: dict[str, int], links: dict[str, int]
) -> None:
    tid, uid = people["tenant"], people["creator"]
    with app.app_context():
        owned = Passport(tenant_id=tid, owner_user_id=uid, title="My airport passport")
        private = Passport(
            tenant_id=tid,
            owner_user_id=people["outsider"],
            title="Private other passport",
        )
        distant = field(tid, "Distant airport", latitude=50.75)
        public = field(
            tid,
            "Public airport",
            source="ourairports",
            source_id="distant-public",
            latitude=54.0,
            overrides={"name": "Private airfield correction"},
        )
        db.session.add_all([owned, private])
        db.session.flush()
        target = PassportAirfield(
            tenant_id=tid, passport_id=owned.id, airfield_id=links["field"]
        )
        db.session.add_all(
            [
                target,
                PassportAirfield(
                    tenant_id=tid,
                    passport_id=owned.id,
                    airfield_id=public.id,
                    public_catalogue=True,
                ),
                PassportAirfield(
                    tenant_id=tid, passport_id=owned.id, airfield_id=distant.id
                ),
                PassportAirfield(
                    tenant_id=tid, passport_id=private.id, airfield_id=links["field"]
                ),
            ]
        )
        db.session.commit()
        passport_id, target_id = owned.id, target.id
        place_name = db.session.get(Place, links["place"]).title
        assert passport_fields(tid, uid)[public.id]["name"] == "Public airport"
    signin(client, people)
    airfields = f"/places/{links['place']}/airfields"
    target_url = f"/passports/{passport_id}/airfields/{target_id}"
    suggestions_url = "/places/passport-suggestions"

    for url in ("/places/", "/passports/"):
        assert suggestions_url in client.get(url).text
    nearby = client.get(airfields)
    assert nearby.status_code == 200
    assert 'id="airfield-candidate-map"' in nearby.text
    assert "In your passports" in nearby.text and "My airport passport" in nearby.text
    assert "Private other passport" not in nearby.text

    shared = client.get(suggestions_url)
    assert shared.status_code == 200 and "My airport passport" in shared.text
    assert 'type="range"' in shared.text and place_name in shared.text
    assert "Distant airport" not in shared.text
    assert "Distant airport" in client.get(suggestions_url + "?radius=30").text
    assert client.get(suggestions_url + "?radius=nan").status_code == 400
    target = client.get(target_url)
    assert target.status_code == 200 and "Places near this airfield" in target.text
    assert place_name in target.text and "Link airfield" in target.text
    assert "Enter a valid search radius" in client.get(target_url + "?radius=300").text
    assert place_name not in client.get(target_url + "?radius=0.1").text

    link = {"action": "add", "key": f"field:{links['field']}"}
    done = client.post(
        airfields + "?radius=30", data={**link, "return_to": "suggestions"}
    )
    assert done.headers["Location"] == suggestions_url + "?radius=30"
    assert "No nearby places to link" in client.get(suggestions_url).text
    assert "Linked places" in client.get(target_url).text
    back = client.post(
        airfields,
        data={
            **link,
            "return_to": "passport",
            "passport_id": passport_id,
            "target_id": target_id,
        },
    )
    assert back.headers["Location"] == target_url

    signin(client, people, "reader")
    assert "No nearby places to link" in client.get(suggestions_url).text
    with app.app_context():
        set_membership_flags(uid, view_only=True)
        db.session.commit()
    signin(client, people)
    assert "No nearby places to link" in client.get(suggestions_url + "?radius=30").text
