"""Airfield links preserve collection boundaries and public-source metadata."""

from datetime import date
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import PassportAirfieldIdentity, Place, PlaceAirfield, PlaceVisit, db
from places.access import PlaceError
from places.airfields import add_link, linked_airfields, remove_link, update_link
from places.collections import create_collection, set_member
from places.management import copy_place, move_place, save_place
from sqlalchemy.exc import IntegrityError

from tests.auth_helpers import set_membership_flags
from tests.test_place_collections import people
from tests.test_place_management import form
from tests.test_place_sharing import signin

__all__ = ["people"]


def field(
    tenant_id: int, name: str = "Airfield", **values: Any
) -> PassportAirfieldIdentity:
    row = PassportAirfieldIdentity(
        tenant_id=tenant_id,
        **{
            "source": "manual",
            "name": name,
            "ident": "TEST",
            "latitude": 50.51,
            "longitude": 4.2,
            **values,
        },
    )
    db.session.add(row)
    db.session.flush()
    return row


@pytest.fixture
def links(app: Flask, people: dict[str, int]) -> dict[str, int]:
    p = people
    with app.app_context():
        t, u = p["tenant"], p["creator"]
        c = create_collection(t, u, "Shared destinations")
        d = create_collection(t, u, "Another collection")
        foreign = create_collection(p["elsewhere"], u, "Other tenant")
        set_member(t, u, c.id, p["reader"], can_edit=False)
        set_member(t, u, c.id, p["personal"])
        a = save_place(t, u, c.id, form())
        b = save_place(t, u, d.id, {"external_url": "https://example.org/idea"})
        f = field(t)
        public = field(
            t,
            "Public name",
            source="ourairports",
            source_id="test-public",
            overrides={"name": "Private correction", "latitude": 52.0},
        )
        private = field(t, "Hidden airfield")
        elsewhere = field(p["elsewhere"], "Other tenant airfield")
        db.session.commit()
        return {
            "collection": c.id,
            "other_collection": d.id,
            "foreign_collection": foreign.id,
            "place": a.id,
            "unlocated": b.id,
            "field": f.id,
            "public": public.id,
            "hidden": private.id,
            "elsewhere": elsewhere.id,
        }


def test_link_notes_preference_distance_audit_and_idempotence(
    app: Flask, people: dict[str, int], links: dict[str, int]
) -> None:
    p, x = people, links
    with app.app_context():
        t, u = p["tenant"], p["creator"]
        place = db.session.get(Place, x["place"])
        before = place.updated_at
        link = add_link(
            t, u, place.id, x["field"], note="  Walk 10 minutes  ", preferred=True
        )
        assert link.note == "Walk 10 minutes" and link.preferred
        assert add_link(t, u, place.id, x["field"]) is link
        assert link.note == "Walk 10 minutes"
        db.session.commit()
        assert place.updated_by_user_id == u and place.updated_at > before
        rows = linked_airfields(t, u)
        assert len(rows) == 1
        assert rows[0]["place_name"] == "A museum"
        assert 1 < rows[0]["distance_km"] < 1.2
        assert rows[0]["note"] == "Walk 10 minutes" and not rows[0]["visited"]
        update_link(t, u, place.id, x["field"], note="  ")
        db.session.commit()
        assert link.note is None and not link.preferred
        remove_link(t, u, place.id, x["field"])
        db.session.commit()
        assert linked_airfields(t, u) == []
        assert db.session.get(PassportAirfieldIdentity, x["field"]) is not None


def test_readers_can_view_links_but_cannot_change_them(
    app: Flask, people: dict[str, int], links: dict[str, int]
) -> None:
    p, x = people, links
    with app.app_context():
        t, u, v = p["tenant"], p["creator"], p["reader"]
        add_link(t, u, x["place"], x["field"])
        db.session.commit()
        assert len(linked_airfields(t, v)) == 1
        for change in (add_link, update_link, remove_link):
            with pytest.raises(PlaceError):
                change(t, v, x["place"], x["field"])
        assert linked_airfields(t, p["outsider"]) == []
        assert linked_airfields(t, p["admin"], [x["collection"]]) == []
        assert linked_airfields(t, v, [x["other_collection"]]) == []
        assert linked_airfields(p["elsewhere"], u) == []


def test_public_source_link_cannot_be_upgraded_to_private_metadata_by_id(
    app: Flask, people: dict[str, int], links: dict[str, int]
) -> None:
    p, x = people, links
    with app.app_context():
        t, v = p["tenant"], p["personal"]
        for fid in (x["hidden"], x["public"], x["elsewhere"], 2147483647):
            with pytest.raises(PlaceError, match="Airfield not found"):
                add_link(t, v, x["place"], fid)
        for fid in (x["hidden"], x["elsewhere"]):
            with pytest.raises(PlaceError):
                add_link(t, v, x["place"], fid, public_catalogue=True)
        link = add_link(t, v, x["place"], x["public"], public_catalogue=True)
        db.session.commit()
        assert link.public_catalogue
        rows = linked_airfields(t, v)
        assert rows[0]["name"] == "Public name"
        assert rows[0]["latitude"] == 50.51
        assert "Private correction" not in str(rows)
        with pytest.raises(PlaceError):
            add_link(t, v, x["place"], x["public"], public_catalogue=False)


def test_explicit_shared_private_link_grants_metadata_only_to_collection_members(
    app: Flask, people: dict[str, int], links: dict[str, int]
) -> None:
    p, x = people, links
    with app.app_context():
        t, u, v = p["tenant"], p["creator"], p["personal"]
        add_link(t, u, x["place"], x["public"])
        db.session.commit()
        c = create_collection(t, v, "My fly-outs")
        b = save_place(t, v, c.id, form())
        link = add_link(t, v, b.id, x["public"])
        db.session.commit()
        assert not link.public_catalogue
        assert linked_airfields(t, v, [c.id])[0]["name"] == "Private correction"
        with pytest.raises(PlaceError):
            add_link(t, p["outsider"], x["place"], x["public"])
        assert linked_airfields(t, p["outsider"], [c.id]) == []


def test_provider_filters_only_the_viewers_visits_and_handles_missing_locations(
    app: Flask, people: dict[str, int], links: dict[str, int]
) -> None:
    p, x = people, links
    with app.app_context():
        t, u = p["tenant"], p["creator"]
        add_link(t, u, x["place"], x["field"])
        add_link(t, u, x["unlocated"], x["field"])
        unknown = field(t, "No coordinates", latitude=None, longitude=None)
        add_link(t, u, x["place"], unknown.id)
        db.session.add(
            PlaceVisit(
                place_id=x["place"],
                user_id=p["reader"],
                visit_date=date.today(),
                note="private visit note",
            )
        )
        db.session.commit()
        assert len(linked_airfields(t, u, include_visited=False)) == 3
        db.session.add(
            PlaceVisit(
                place_id=x["place"],
                user_id=u,
                visit_date=date.today(),
                note="my visit note",
            )
        )
        db.session.commit()
        rows = linked_airfields(t, u, include_visited=False)
        assert len(rows) == 1 and rows[0]["place_id"] == x["unlocated"]
        assert rows[0]["distance_km"] is None
        all_rows = linked_airfields(t, u, place_ids=[x["place"]])
        assert all(row["visited"] for row in all_rows)
        assert all_rows[-1]["distance_km"] is None
        assert "visit note" not in str(all_rows)
        assert linked_airfields(t, u, []) == []
        assert linked_airfields(t, u, place_ids=[]) == []
        place = db.session.get(Place, x["place"])
        place.archived = True
        db.session.commit()
        assert len(linked_airfields(t, u)) == 1
        assert len(linked_airfields(t, u, include_archived=True)) == 3
        place.deleted_at = place.created_at
        db.session.commit()
        assert len(linked_airfields(t, u, include_archived=True)) == 1


def test_refused_note_and_missing_link_leave_existing_data_unchanged(
    app: Flask, people: dict[str, int], links: dict[str, int]
) -> None:
    p, x = people, links
    with app.app_context():
        t, u = p["tenant"], p["creator"]
        with pytest.raises(PlaceError, match="2,000"):
            add_link(t, u, x["place"], x["field"], note="x" * 2001)
        add_link(t, u, x["place"], x["field"], note="Keep this")
        with pytest.raises(PlaceError):
            update_link(t, u, x["place"], x["field"], note="x" * 2001)
        for change in (update_link, remove_link):
            with pytest.raises(PlaceError, match="Airfield not found"):
                change(t, u, x["place"], x["hidden"])
        db.session.commit()
        assert linked_airfields(t, u)[0]["note"] == "Keep this"


def test_copy_and_move_preserve_links_and_source_metadata_choice(
    app: Flask, people: dict[str, int], links: dict[str, int]
) -> None:
    p, x = people, links
    with app.app_context():
        t, u = p["tenant"], p["creator"]
        add_link(
            t,
            u,
            x["place"],
            x["public"],
            public_catalogue=True,
            note="Taxi",
            preferred=True,
        )
        db.session.commit()
        copy = copy_place(t, u, x["place"], x["other_collection"])
        db.session.commit()
        original = db.session.get(PlaceAirfield, (x["place"], x["public"]))
        copied = db.session.get(PlaceAirfield, (copy.id, x["public"]))
        assert (
            copied.note == original.note
            and copied.preferred
            and copied.public_catalogue
        )
        move_place(t, u, x["place"], x["other_collection"])
        db.session.commit()
        db.session.expire_all()
        assert original.collection_id == x["other_collection"]
        db.session.delete(db.session.get(Place, x["place"]))
        db.session.commit()
        assert db.session.get(PlaceAirfield, (x["place"], x["public"])) is None
        assert db.session.get(PlaceAirfield, (copy.id, x["public"])) is not None


def test_composite_constraints_reject_foreign_airfields_and_wrong_collections(
    app: Flask, people: dict[str, int], links: dict[str, int]
) -> None:
    p, x = people, links
    with app.app_context():
        for collection, fid in (
            (x["collection"], x["elsewhere"]),
            (x["other_collection"], x["field"]),
            (x["foreign_collection"], x["field"]),
        ):
            with pytest.raises(IntegrityError), db.session.begin_nested():
                db.session.add(
                    PlaceAirfield(
                        tenant_id=p["tenant"],
                        collection_id=collection,
                        place_id=x["place"],
                        airfield_id=fid,
                    )
                )
                db.session.flush()


def test_new_place_can_search_and_link_an_airfield_atomically(
    app: Flask, client: FlaskClient, people: dict[str, int], links: dict[str, int]
) -> None:
    signin(client, people)
    lookup = "/places/new/airfields"
    response = client.post(lookup, data={"query": "TEST"})
    key = f"field:{links['field']}"
    assert response.status_code == 200
    assert any(row["key"] == key for row in response.json["results"])
    assert all(
        row["key"] != f"field:{links['elsewhere']}" for row in response.json["results"]
    )
    assert client.post(lookup, data={"query": ""}).json == {"results": []}
    assert client.post(lookup, data={"query": "x" * 201}).status_code == 400
    base = form(title="New destination") | {"collection": links["collection"]}
    assert (
        client.post(
            "/places/new", data=base | {"airfield_key": f"field:{links['elsewhere']}"}
        ).status_code
        == 400
    )
    with app.app_context():
        assert Place.query.filter_by(title="New destination").count() == 0
    response = client.post("/places/new", data=base | {"airfield_key": key})
    assert response.status_code == 302
    with app.app_context():
        place = Place.query.filter_by(title="New destination").one()
        assert db.session.get(PlaceAirfield, (place.id, links["field"])) is not None
        set_membership_flags(people["creator"], view_only=True)
        db.session.commit()
    assert client.post(lookup, data={"query": "TEST"}).status_code == 403
