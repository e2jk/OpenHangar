"""Personal accounts (no role) and Viewers get the personal features: what they
can do with a place depends only on collection membership, never on the
organisation, and Places never opens the organisation to them (42h)."""

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import Aircraft, Place, PlaceCollectionMember, Role, TenantUser, db
from places.collections import create_collection, set_member
from places.management import save_place
from services.account_scope import PERSONAL_ENDPOINTS

from tests.test_place_collections import people
from tests.test_place_management import form
from tests.test_place_sharing import signin

__all__ = ["people"]

# The Places pages a Personal account must keep (42b–42c; the other agent's
# entries, preserved through the rename).
PLACES_ENDPOINTS = {
    "places.index",
    "places.new_collection",
    "places.collection_settings",
    "places.join_collection",
    "places.new_place",
    "places.edit_place",
    "places.place_detail",
    "places.place_photo",
    "places.coordinates",
    "places.geocode",
    "places.place_action",
}


def test_personal_allowlist_keeps_every_places_page() -> None:
    assert PLACES_ENDPOINTS <= PERSONAL_ENDPOINTS


@pytest.fixture
def shared(app: Flask, people: dict[str, int]) -> dict[str, int]:
    """A collection where the Personal account and a Viewer are editors, and
    an aircraft of the same organisation."""
    p = people
    with app.app_context():
        collection = create_collection(p["tenant"], p["creator"], "Fly-outs")
        for name in ("personal", "reader"):
            set_member(p["tenant"], p["creator"], collection.id, p[name], can_edit=True)
        place = save_place(
            p["tenant"], p["creator"], collection.id, form(title="Castle")
        )
        aircraft = Aircraft(
            tenant_id=p["tenant"], registration="OO-PER", make="Test", model="T"
        )
        db.session.add(aircraft)
        db.session.commit()
        return {"collection": collection.id, "place": place.id, "aircraft": aircraft.id}


@pytest.mark.parametrize("name", ["personal", "reader"])
def test_an_editor_edits_places_whatever_their_role(
    app: Flask,
    client: FlaskClient,
    people: dict[str, int],
    shared: dict[str, int],
    name: str,
) -> None:
    signin(client, people, name)
    assert client.get(f"/places/{shared['place']}/edit").status_code == 200
    response = client.post(
        f"/places/{shared['place']}/edit",
        data=form(title=f"Castle, edited by {name}"),
    )
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Place, shared["place"]).title == (
            f"Castle, edited by {name}"
        )


def test_a_personal_editor_gains_no_organisation_access(
    client: FlaskClient, people: dict[str, int], shared: dict[str, int]
) -> None:
    signin(client, people, "personal")
    assert client.get("/places/").status_code == 200
    for url in (
        "/aircraft/",
        f"/aircraft/{shared['aircraft']}",
        "/flights/new",
        "/maintenance/",
        "/reservations/",
        "/pilot/logbook",
        "/config/users/",
    ):
        response = client.get(url)
        assert response.status_code in (403, 404), url


def test_a_viewer_editor_stays_read_only_for_the_aircraft(
    client: FlaskClient, people: dict[str, int], shared: dict[str, int]
) -> None:
    signin(client, people, "reader")
    assert client.get(f"/aircraft/{shared['aircraft']}/edit").status_code in (
        403,
        404,
    )
    assert client.get("/flights/new").status_code == 403


def test_starting_a_pilot_logbook_keeps_places_and_collections(
    app: Flask, client: FlaskClient, people: dict[str, int], shared: dict[str, int]
) -> None:
    p = people
    with app.app_context():
        admin = TenantUser.query.filter_by(
            user_id=p["creator"], tenant_id=p["tenant"]
        ).one()
        admin.role = Role.ADMIN
        db.session.commit()
    signin(client, p, "creator")
    response = client.post(
        f"/config/users/{p['personal']}/personal-logbook", data={"enable": "yes"}
    )
    assert response.status_code == 302
    with app.app_context():
        membership = TenantUser.query.filter_by(
            user_id=p["personal"], tenant_id=p["tenant"]
        ).one()
        assert membership.access_scope == "logbook" and membership.role is None
        assert PlaceCollectionMember.query.filter_by(
            collection_id=shared["collection"], user_id=p["personal"], can_edit=True
        ).one()
    signin(client, p, "personal")
    assert client.get(f"/places/{shared['place']}/edit").status_code == 200
    assert client.get("/pilot/logbook").status_code == 200


def test_user_list_names_personal_and_viewer_accounts(
    app: Flask, client: FlaskClient, people: dict[str, int]
) -> None:
    p = people
    with app.app_context():
        TenantUser.query.filter_by(
            user_id=p["creator"], tenant_id=p["tenant"]
        ).one().role = Role.ADMIN
        db.session.commit()
    signin(client, p, "creator")
    page = client.get("/config/users/").get_data(as_text=True)
    assert "Personal account" in page and "Viewer (read-only)" in page
    assert "plus personal features (passports, Places)" in page
