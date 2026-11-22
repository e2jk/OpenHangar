"""Phase 42g validation, end to end on one shared collection: members see
and edit the same places, readers cannot edit but record their visits,
non-members get 404 on every place URL (pages, photo, airfields, visits,
downloads, Where next?), and leaving keeps the places and visits."""

from datetime import date
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import Place, PlaceVisit, db
from places.collections import create_collection, remove_member, set_member
from places.management import save_place

from tests.test_place_collections import people
from tests.test_place_management import form, upload
from tests.test_place_sharing import signin

__all__ = ["people"]


@pytest.fixture(autouse=True)
def uploads(app: Flask, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(app.config, "UPLOAD_FOLDER", str(tmp_path))


@pytest.fixture
def shared(app: Flask, people: dict[str, int]) -> dict[str, int]:
    p = people
    with app.app_context():
        ours = create_collection(p["tenant"], p["creator"], "Our places")
        set_member(p["tenant"], p["creator"], ours.id, p["personal"], can_edit=True)
        set_member(p["tenant"], p["creator"], ours.id, p["reader"], can_edit=False)
        castle = save_place(
            p["tenant"],
            p["creator"],
            ours.id,
            form(title="Castle", latitude="50.5", longitude="4.2"),
            photo=upload(),
        )
        db.session.commit()
        return {"collection": ours.id, "castle": castle.id}


def test_members_share_one_collection_and_outsiders_see_nothing(
    app: Flask, client: FlaskClient, people: dict[str, int], shared: dict[str, int]
) -> None:
    pid = shared["castle"]
    # An editor (a Personal account) edits the creator's place.
    signin(client, people, "personal")
    assert client.get(f"/places/{pid}").status_code == 200
    response = client.post(f"/places/{pid}/edit", data=form(title="Castle and park"))
    assert response.status_code == 302
    # The creator sees the change; the reader sees it but cannot edit.
    signin(client, people)
    assert "Castle and park" in client.get(f"/places/{pid}").get_data(as_text=True)
    signin(client, people, "reader")
    assert client.get(f"/places/{pid}").status_code == 200
    assert client.get(f"/places/{pid}/edit").status_code == 404
    assert (
        client.post(f"/places/{pid}/edit", data=form(title="Mine")).status_code == 404
    )
    # ...but records their own visit, shown to every member.
    assert (
        client.post(
            f"/places/{pid}/visits", data={"visit_date": "2026-05-03"}
        ).status_code
        == 302
    )
    signin(client, people)
    assert "by reader" in client.get(f"/places/{pid}").get_data(as_text=True)
    # A non-member gets 404 on every place URL, and nothing in searches.
    signin(client, people, "outsider")
    for method, url in [
        ("get", f"/places/{pid}"),
        ("get", f"/places/{pid}/edit"),
        ("get", f"/places/{pid}/photo"),
        ("get", f"/places/{pid}/airfields"),
        ("post", f"/places/{pid}/visits"),
        ("post", f"/places/{pid}/action"),
        ("get", f"/places/export.gpx?collection={shared['collection']}"),
        ("get", f"/places/collections/{shared['collection']}/settings"),
    ]:
        assert getattr(client, method)(url).status_code == 404, url
    assert b"Castle" not in client.get("/places/export.gpx").data
    where = client.get(
        "/explore/where-next", query_string={"source": "places", "place": pid}
    )
    assert where.status_code == 200 and b"Castle" not in where.data


def test_leaving_keeps_the_places_and_visits(
    app: Flask, client: FlaskClient, people: dict[str, int], shared: dict[str, int]
) -> None:
    p, pid = people, shared["castle"]
    with app.app_context():
        mine = save_place(
            p["tenant"],
            p["personal"],
            shared["collection"],
            form(title="Friend's mill"),
        )
        db.session.add(
            PlaceVisit(place_id=pid, user_id=p["personal"], visit_date=date(2026, 6, 1))
        )
        db.session.commit()
        mill = mine.id
        remove_member(p["tenant"], p["personal"], shared["collection"], p["personal"])
        db.session.commit()
        kept = db.session.get(Place, mill)
        assert kept is not None and kept.owner_user_id == p["creator"]
        assert (
            PlaceVisit.query.filter_by(place_id=pid, user_id=p["personal"]).count() == 1
        )
    signin(client, p, "personal")
    assert client.get(f"/places/{pid}").status_code == 404
    signin(client, p)
    assert "Friend&#39;s mill" in client.get("/places/").get_data(as_text=True)
