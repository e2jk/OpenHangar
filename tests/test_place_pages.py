"""Destination pages share one filtered map/list and enforce collection boundaries."""

import json
import re
from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import Place, PlaceCollectionInvitation, PlaceTag, PlaceVisit, db
from places.cleanup import purge_retired
from places.collections import create_collection, set_default, set_member
from places.management import photo_path, save_place
from places.sharing import invite_email

from tests.auth_helpers import set_membership_flags
from tests.test_place_collections import people
from tests.test_place_management import form, upload
from tests.test_place_sharing import signin

__all__ = ["people"]


@pytest.fixture(autouse=True)
def uploads(app: Flask, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(app.config, "UPLOAD_FOLDER", str(tmp_path))


@pytest.fixture
def destinations(app: Flask, people: dict[str, int]) -> dict[str, int]:
    p = people
    with app.app_context():
        t, u = p["tenant"], p["creator"]
        c = create_collection(t, u, "Museums")
        d = create_collection(t, u, "Next trip")
        foreign = create_collection(t, p["outsider"], "Private to outsider")
        set_member(t, u, c.id, p["reader"], can_edit=False)
        a = save_place(
            t,
            u,
            c.id,
            form(title="Alpha", latitude="0", longitude="0"),
            tags=["Museum"],
            photo=upload(),
        )
        b = save_place(t, u, c.id, form(title="Bravo"), tags=["Town"])
        z = save_place(t, u, d.id, form(title="Zulu"), tags=["Town"])
        private = save_place(
            t,
            p["outsider"],
            foreign.id,
            form(title="SECRET DESTINATION"),
            photo=upload(),
        )
        db.session.add(
            PlaceVisit(place_id=a.id, user_id=u, visit_date=date(2026, 6, 1))
        )
        # A friend's visit must never count as the viewer's own visit.
        db.session.add(
            PlaceVisit(place_id=b.id, user_id=p["reader"], visit_date=date(2026, 6, 2))
        )
        set_default(t, u, d.id)
        db.session.commit()
        return {
            "collection": c.id,
            "destination": d.id,
            "foreign": foreign.id,
            "alpha": a.id,
            "bravo": b.id,
            "zulu": z.id,
            "private": private.id,
        }


def points(response: object) -> list[dict]:
    match = re.search(r'id="places-map" data-points="([^"]*)"', response.text)
    assert match is not None, response.text[:1000]
    return json.loads(unescape(match[1]))


def test_default_collection_and_validated_photo_forms(
    app: Flask,
    client: FlaskClient,
    people: dict[str, int],
    destinations: dict[str, int],
) -> None:
    p, d = people, destinations
    signin(client, p)
    response = client.get("/places/new")
    assert response.status_code == 200
    assert f'value="{d["destination"]}" selected' in response.text
    response = client.post(
        "/places/new",
        data={
            **form(
                title="Saved from form",
                description="<script>bad</script>",
                address="<b>plain address</b>",
            ),
            "collection": d["collection"],
            "tags": "Museum\nTown",
            "photo": (upload().stream, "museum.png"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    url = response.headers["Location"]
    page = client.get(url)
    assert page.status_code == 200 and "&lt;script&gt;bad&lt;/script&gt;" in page.text
    assert (
        "<b>plain address</b>" not in page.text
        and "&lt;b&gt;plain address&lt;/b&gt;" in page.text
    )
    assert "Last changed by creator" in page.text
    assert "no-store" in page.headers["Cache-Control"]
    assert len(points(page)) == 1
    assert client.get(url + "/edit").status_code == 200
    photo = client.get(url + "/photo")
    assert photo.status_code == 200 and photo.mimetype == "image/jpeg"
    assert (
        photo.data.startswith(b"\xff\xd8")
        and "no-store" in photo.headers["Cache-Control"]
    )
    assert client.head(url + "/photo").status_code == 200
    with app.app_context():
        saved = Place.query.filter_by(title="Saved from form").one()
        assert (
            saved.collection_id == d["collection"]
            and saved.owner_user_id == p["creator"]
        )
        assert saved.description == "<script>bad</script>"
        filename = saved.photo_filename
    assert (
        client.post(
            url + "/edit",
            data={
                **form(title="Edited"),
                "remove_photo": "yes",
                "collection": d["foreign"],
            },
        ).status_code
        == 302
    )
    with app.app_context():
        saved = Place.query.filter_by(title="Edited").one()
        assert saved.collection_id == d["collection"]
        assert not photo_path(filename).exists()


@pytest.mark.parametrize(
    "query, expected",
    [
        ("", ["Alpha", "Bravo", "Zulu"]),
        ("visited=visited", ["Alpha"]),
        ("visited=unvisited", ["Bravo", "Zulu"]),
        ("sort=added", ["Zulu", "Bravo", "Alpha"]),
        ("sort=visited", ["Alpha", "Bravo", "Zulu"]),
        ("visited=garbage&state=garbage&sort=garbage", ["Alpha", "Bravo", "Zulu"]),
    ],
)
def test_filters_drive_identical_list_and_map(
    app: Flask,
    client: FlaskClient,
    people: dict[str, int],
    destinations: dict[str, int],
    query: str,
    expected: list[str],
) -> None:
    signin(client, people)
    response = client.get("/places/?" + query)
    assert response.status_code == 200
    assert [p["name"] for p in points(response)] == expected
    assert "SECRET DESTINATION" not in response.text
    assert "SECRET DESTINATION" not in json.dumps(points(response))
    for name in expected:
        assert name in response.text


def test_collection_and_tag_filters_remain_private(
    app: Flask,
    client: FlaskClient,
    people: dict[str, int],
    destinations: dict[str, int],
) -> None:
    d = destinations
    signin(client, people)
    assert [
        p["name"] for p in points(client.get(f"/places/?collection={d['collection']}"))
    ] == ["Alpha", "Bravo"]
    with app.app_context():
        tag = (
            PlaceTag.query.filter_by(collection_id=d["collection"], name="Museum")
            .one()
            .id
        )
    assert [p["name"] for p in points(client.get(f"/places/?tag={tag}"))] == ["Alpha"]
    assert points(client.get("/places/?tag=99999")) == []
    assert client.get(f"/places/?collection={d['foreign']}").status_code == 404


def test_place_photo_and_forms_respect_readers_nonmembers_and_view_only(
    app: Flask,
    client: FlaskClient,
    people: dict[str, int],
    destinations: dict[str, int],
) -> None:
    p, d = people, destinations
    url = f"/places/{d['alpha']}"
    signin(client, p, "reader")
    assert client.get(url).status_code == client.get(url + "/photo").status_code == 200
    assert points(client.get(url))[0]["visited"] is False
    assert client.get(url + "/edit").status_code == 404
    assert client.post(url + "/action", data={"action": "trash"}).status_code == 404
    assert client.get(f"/places/new?collection={d['collection']}").status_code == 404
    signin(client, p, "outsider")
    for path in (url, url + "/photo", url + "/edit"):
        assert client.get(path).status_code == 404
    signin(client, p, "admin")
    assert client.get(url + "/photo").status_code == 404
    signin(client, p)
    assert points(client.get(url))[0]["visited"] is True
    with app.app_context():
        set_membership_flags(p["creator"], view_only=True)
        db.session.commit()
    assert client.get("/places/new").status_code == 403
    assert client.get(url).status_code == 200
    assert client.get(url + "/edit").status_code == 404


def test_photo_missing_file_missing_row_and_personal_scope(
    app: Flask,
    client: FlaskClient,
    people: dict[str, int],
    destinations: dict[str, int],
) -> None:
    p, d = people, destinations
    signin(client, p)
    assert client.get(f"/places/{d['bravo']}/photo").status_code == 404
    assert client.get("/places/99999/photo").status_code == 404
    assert client.get("/places/99999").status_code == 404
    with app.app_context():
        photo_path(db.session.get(Place, d["alpha"]).photo_filename).unlink()
    assert client.get(f"/places/{d['alpha']}/photo").status_code == 404
    signin(client, p, "personal")
    assert client.get("/places/new").status_code == 200
    response = client.post("/places/new", data={**form(), "collection": "9" * 1000})
    assert response.status_code == 400
    assert client.get("/aircraft/").status_code == 403


def test_invalid_forms_preserve_values_and_coordinate_api_never_fetches(
    app: Flask,
    client: FlaskClient,
    people: dict[str, int],
    destinations: dict[str, int],
) -> None:
    p, d = people, destinations
    signin(client, p)
    for values in (
        {"latitude": "NaN"},
        {"external_url": "javascript:alert(1)"},
    ):
        response = client.post(
            "/places/new",
            data={
                **form(**values),
                "collection": d["collection"],
                "description": "KEEP THIS TEXT",
            },
        )
        assert response.status_code == 400 and "KEEP THIS TEXT" in response.text
        assert response.headers["X-OH-Places-Form-Error"] == "1"
    assert (
        client.post(
            "/places/new", data={**form(), "collection": d["foreign"]}
        ).status_code
        == 400
    )
    with patch("urllib.request.urlopen", side_effect=AssertionError("must not fetch")):
        good = client.post(
            "/places/coordinates", data={"coordinates": "50°30'N 4°12'E"}
        )
        assert good.json == {"latitude": 50.5, "longitude": 4.2}
        bad = client.post(
            "/places/coordinates",
            data={"coordinates": "https://maps.app.goo.gl/example"},
        )
        assert bad.status_code == 400 and "error" in bad.json


def test_edit_form_preserves_zero_coordinates_and_commit_failure_removes_new_photo(
    app: Flask,
    client: FlaskClient,
    people: dict[str, int],
    destinations: dict[str, int],
) -> None:
    p, d = people, destinations
    signin(client, p)
    url = f"/places/{d['alpha']}/edit"
    response = client.get(url)
    assert 'id="place-latitude" value="0.0"' in response.text
    with app.app_context():
        original = photo_path(db.session.get(Place, d["alpha"]).photo_filename)
        before = set(original.parent.iterdir())
    with (
        patch.object(
            db.session, "commit", side_effect=RuntimeError("database unavailable")
        ),
        pytest.raises(RuntimeError),
    ):
        client.post(
            url,
            data={**form(title="Not committed"), "photo": (upload().stream, "new.png")},
            content_type="multipart/form-data",
        )
    assert set(original.parent.iterdir()) == before
    assert original.exists()


def test_actions_copy_move_archive_restore_and_owner_only_purge(
    app: Flask,
    client: FlaskClient,
    people: dict[str, int],
    destinations: dict[str, int],
) -> None:
    p, d = people, destinations
    signin(client, p)
    url = f"/places/{d['alpha']}"
    result = client.post(
        url + "/action", data={"action": "copy", "target": d["destination"]}
    )
    assert result.status_code == 302 and result.headers["Location"] != url
    assert (
        client.post(
            url + "/action", data={"action": "move", "target": d["destination"]}
        ).status_code
        == 302
    )
    for action in ("archive", "unarchive", "trash", "restore"):
        assert client.post(url + "/action", data={"action": action}).status_code == 302
    assert (
        client.post(
            url + "/action", data={"action": "copy", "target": "9" * 1000}
        ).status_code
        == 302
    )
    assert client.post(url + "/action", data={"action": "unknown"}).status_code == 400
    assert client.post(url + "/action", data={"action": "purge"}).status_code == 302
    with app.app_context():
        assert db.session.get(Place, d["alpha"]) is not None
    assert (
        client.post(
            url + "/action", data={"action": "purge", "confirm": "yes"}
        ).status_code
        == 302
    )
    assert client.get(url).status_code == 404


def test_archived_and_bin_filters_and_nonowner_cannot_purge(
    app: Flask,
    client: FlaskClient,
    people: dict[str, int],
    destinations: dict[str, int],
) -> None:
    p, d = people, destinations
    signin(client, p)
    url = f"/places/{d['alpha']}/action"
    client.post(url, data={"action": "archive"})
    assert [x["name"] for x in points(client.get("/places/?state=archived"))] == [
        "Alpha"
    ]
    assert len(points(client.get("/places/?state=all"))) == 3
    client.post(url, data={"action": "trash"})
    assert [x["name"] for x in points(client.get("/places/?state=bin"))] == ["Alpha"]
    assert client.get(f"/places/{d['alpha']}/photo").status_code == 404
    with app.app_context():
        set_member(
            p["tenant"], p["creator"], d["collection"], p["reader"], can_edit=True
        )
        db.session.commit()
    signin(client, p, "reader")
    assert (
        client.post(url, data={"action": "purge", "confirm": "yes"}).status_code == 302
    )
    with app.app_context():
        assert db.session.get(Place, d["alpha"]) is not None
    assert client.post(url, data={"action": "restore"}).status_code == 302


def test_copy_commit_failure_removes_only_new_file(
    app: Flask,
    client: FlaskClient,
    people: dict[str, int],
    destinations: dict[str, int],
) -> None:
    p, d = people, destinations
    signin(client, p)
    client.get(f"/places/{d['alpha']}")
    with app.app_context():
        folder = photo_path("")
        before = set(folder.iterdir())
    with (
        patch.object(
            db.session, "commit", side_effect=RuntimeError("database unavailable")
        ),
        pytest.raises(RuntimeError),
    ):
        client.post(
            f"/places/{d['alpha']}/action",
            data={"action": "copy", "target": d["destination"]},
        )
    assert set(folder.iterdir()) == before


def test_tag_settings_rename_merge_delete_and_bad_ids(
    app: Flask,
    client: FlaskClient,
    people: dict[str, int],
    destinations: dict[str, int],
) -> None:
    p, d = people, destinations
    signin(client, p)
    url = f"/places/collections/{d['collection']}/settings"
    with app.app_context():
        tag = (
            PlaceTag.query.filter_by(collection_id=d["collection"], name="Museum")
            .one()
            .id
        )
    assert (
        client.post(
            url, data={"action": "tag-rename", "tag": tag, "name": "Town"}
        ).status_code
        == 302
    )
    for action in ("tag-rename", "tag-delete"):
        assert client.post(url, data={"action": action}).status_code == 400
    with app.app_context():
        tag = (
            PlaceTag.query.filter_by(collection_id=d["collection"], name="Town")
            .one()
            .id
        )
    with app.app_context():
        unused = PlaceTag(collection_id=d["collection"], name="Unused tag")
        db.session.add(unused)
        db.session.commit()
        unused_id = unused.id
    page = client.get(url).text
    assert "Used by 2 places" in page
    assert "Unused tag" in page
    assert (
        client.post(url, data={"action": "tag-delete", "tag": tag}).status_code == 400
    )
    assert (
        client.post(url, data={"action": "tag-delete", "tag": unused_id}).status_code
        == 302
    )
    assert (
        client.post(
            url, data={"action": "tag-delete", "tag": tag, "confirm": "yes"}
        ).status_code
        == 302
    )
    with app.app_context():
        assert db.session.get(Place, d["alpha"]) is not None
        assert db.session.get(PlaceTag, tag) is None


def test_daily_purge_retains_recent_entries_and_rolls_back_on_failure(
    app: Flask, people: dict[str, int], destinations: dict[str, int]
) -> None:
    p, d = people, destinations
    with app.app_context():
        place = db.session.get(Place, d["alpha"])
        filename = place.photo_filename
        place.deleted_at = datetime.now(UTC) - timedelta(days=31)
        expired = invite_email(
            p["tenant"], p["creator"], d["collection"], "expired@places.test"
        )
        expired.expires_at = datetime.now(UTC) - timedelta(days=1)
        fresh = invite_email(
            p["tenant"], p["creator"], d["collection"], "fresh@places.test"
        )
        db.session.commit()
        fresh_id = fresh.id
        with (
            patch.object(db.session, "commit", side_effect=RuntimeError("fail")),
            pytest.raises(RuntimeError),
        ):
            purge_retired()
        assert photo_path(filename).exists()
        assert db.session.get(Place, d["alpha"]) is not None
        assert purge_retired() == (1, 1)
        assert not photo_path(filename).exists()
        assert db.session.get(PlaceCollectionInvitation, fresh_id) is not None


def test_coordinate_api_uses_a_fixed_public_validation_message(
    client: FlaskClient, people: dict[str, int]
) -> None:
    from places.access import PlaceError

    signin(client, people)
    with patch(
        "places.place_routes.parse_coordinates",
        side_effect=PlaceError("private diagnostic"),
    ):
        response = client.post("/places/coordinates", data={"coordinates": "invalid"})
    assert response.status_code == 400
    assert "Paste decimal or DMS coordinates" in response.json["error"]
    assert "private diagnostic" not in response.text


def test_link_only_prefill_capture_and_later_location(
    app: Flask,
    client: FlaskClient,
    people: dict[str, int],
    destinations: dict[str, int],
) -> None:
    p, d = people, destinations
    signin(client, p)
    page = client.get(
        "/places/new",
        query_string={
            "title": "A shared idea",
            "external_url": "https://example.org/idea",
        },
    )
    assert 'value="A shared idea"' in page.text
    assert 'value="https://example.org/idea"' in page.text
    response = client.post(
        "/places/new",
        data={
            "external_url": "https://example.org/idea?igsh=tracking&utm_source=phone"
        },
    )
    assert response.status_code == 302
    detail_url = response.headers["Location"]
    page = client.get(detail_url)
    assert "Location still to add" in page.text
    assert 'id="places-map"' not in page.text
    with app.app_context():
        saved = Place.query.filter_by(external_url="https://example.org/idea").one()
        assert (
            saved.title is None and saved.latitude is None and saved.longitude is None
        )
        assert saved.collection_id == d["destination"]
        saved_id = saved.id
    page = client.get("/places/?location=missing")
    assert f'href="{detail_url}"' in page.text and points(page) == []
    assert f'href="{detail_url}"' not in client.get("/places/?location=located").text
    edit = client.get(f"/places/{saved_id}/edit")
    assert 'value="None"' not in edit.text
    response = client.post(
        f"/places/{saved_id}/edit",
        data={
            "title": "The museum",
            "external_url": "https://example.org/idea",
            "latitude": "0",
            "longitude": "4",
        },
    )
    assert response.status_code == 302
    assert len(points(client.get(detail_url))) == 1
    assert "The museum" not in client.get("/places/?location=missing").text
    assert (
        client.post(f"/places/{saved_id}/edit", data={"latitude": "0"}).status_code
        == 400
    )
    assert (
        client.post(
            f"/places/{saved_id}/edit", data={"title": "The museum"}
        ).status_code
        == 302
    )
    assert "Location still to add" in client.get(detail_url).text


def test_explore_menu_lists_where_next_places_then_passports(
    app: Flask, client: FlaskClient, people: dict[str, int]
) -> None:
    """The top bar's Explore submenu holds all three, in this order; the
    passports link is no longer a top-level entry."""
    with app.app_context():
        set_membership_flags(people["admin"], is_pilot=True)
        db.session.commit()
    signin(client, people, "admin")
    page = client.get("/places/").text
    menu = page[page.index('<i class="bi bi-compass me-1"></i>') :]
    menu = menu[: menu.index("</ul>")]
    where_next = menu.index("/explore/where-next")
    places = menu.index('href="/places/"')
    passports = menu.index('href="/passports/"')
    assert where_next < places < passports
    assert '<li class="nav-item"><a class="nav-link" href="/passports/"' not in page


def test_bulk_move_is_atomic_and_keeps_existing_move_permissions(
    app: Flask,
    client: FlaskClient,
    people: dict[str, int],
    destinations: dict[str, int],
) -> None:
    d = destinations
    signin(client, people)
    assert "Move to another collection" in client.get("/places/").text
    assert (
        client.post("/places/move", data={"target": d["destination"]}).status_code
        == 302
    )
    for ids in (
        [d["alpha"], "invalid"],
        [d["alpha"], d["bravo"]],
        [d["alpha"], d["private"]],
    ):
        assert (
            client.post(
                "/places/move", data={"target": d["destination"], "place": ids}
            ).location
            == "/places/"
        )
        with app.app_context():
            assert db.session.get(Place, d["alpha"]).collection_id == d["collection"]

    with app.app_context():
        PlaceVisit.query.filter_by(place_id=d["bravo"]).delete()
        db.session.commit()
    response = client.post(
        "/places/move",
        data={"target": d["destination"], "place": [d["alpha"], d["bravo"]]},
    )
    assert response.location == f"/places/?collection={d['destination']}"
    with app.app_context():
        assert db.session.get(Place, d["alpha"]).collection_id == d["destination"]
        assert db.session.get(Place, d["bravo"]).collection_id == d["destination"]


def test_place_tags_accept_commas_and_suggest_used_visible_tags(
    app: Flask,
    client: FlaskClient,
    people: dict[str, int],
    destinations: dict[str, int],
) -> None:
    signin(client, people)
    assert 'value="Museum"' in client.get("/places/new").text
    result = client.post(
        "/places/new",
        data=form(title="Gallery") | {"tags": 'Indoor, "Art, modern"'},
    )
    assert result.status_code == 302
    with app.app_context():
        tags = {
            tag.name
            for tag in PlaceTag.query.filter_by(
                collection_id=destinations["destination"]
            )
        }
        assert {"Indoor", "Art, modern"} <= tags
    assert '"Art, modern",Indoor' in unescape(
        client.get(result.location + "/edit").text
    )
    assert 'value="Indoor"' in client.get("/places/new").text
    assert (
        client.post(
            "/places/new", data=form(title="Too long") | {"tags": "X" * 140_000}
        ).status_code
        == 400
    )


def test_place_cards_distinguish_airfield_status_and_tags_without_showing_address(
    app: Flask,
    client: FlaskClient,
    people: dict[str, int],
    destinations: dict[str, int],
) -> None:
    from passports.catalogue import create_custom
    from places.airfields import add_link

    address = "123 Preview Street"
    with app.app_context():
        place = db.session.get(Place, destinations["alpha"])
        place.address = address
        airfield = create_custom(people["tenant"], "Test Airfield", code="EBTS")
        add_link(people["tenant"], people["creator"], place.id, airfield.id)
        db.session.commit()
    signin(client, people)
    listing = client.get("/places/").text
    assert address not in listing
    assert (
        'bg-primary-subtle text-primary-emphasis text-wrap place-filter-pill" title="Test Airfield"'
        in listing
    )
    assert 'text-warning-emphasis">No linked airfield' in listing
    assert 'text-secondary-emphasis place-filter-pill" href="/places/?tag=' in listing
    assert re.search(r'text-body-secondary">\s*Not yet visited', listing)
    assert "Last visited" in listing
    assert address in client.get(f"/places/{destinations['alpha']}").text


def test_airfield_filter_keeps_collection_and_other_filters(
    app: Flask,
    client: FlaskClient,
    people: dict[str, int],
    destinations: dict[str, int],
) -> None:
    from urllib.parse import parse_qs, urlsplit

    from passports.catalogue import create_custom
    from places.airfields import add_link

    with app.app_context():
        t, u = people["tenant"], people["creator"]
        field = create_custom(t, "Shared airfield", code="EBTS")
        add_link(t, u, destinations["alpha"], field.id)
        add_link(t, u, destinations["zulu"], field.id)
        db.session.commit()
        fid = field.id

    signin(client, people)
    selected = destinations["collection"]
    listing = client.get(f"/places/?collection={selected}&visited=visited").text
    match = re.search(r'title="Shared airfield" href="([^"]+)"[^>]*>EBTS</a>', listing)
    assert match is not None
    href = unescape(match[1])
    query = parse_qs(urlsplit(href).query)
    assert query["collection"] == [str(selected)]
    assert query["visited"] == ["visited"]
    assert query["airfield"] == [str(fid)]

    scoped = client.get(href)
    assert [row["name"] for row in points(scoped)] == ["Alpha"]
    assert f'name="airfield" value="{fid}"' in scoped.text
    assert "Clear airfield filter" in scoped.text
    assert 'aria-current="true">EBTS' in scoped.text

    all_collections = client.get(f"/places/?airfield={fid}")
    assert [row["name"] for row in points(all_collections)] == ["Alpha", "Zulu"]
    export = client.get(f"/places/export.gpx?airfield={fid}&visited=visited")
    assert (
        export.status_code == 200
        and "Alpha" in export.text
        and "Zulu" not in export.text
    )

    signin(client, people, "outsider")
    assert points(client.get(f"/places/?airfield={fid}")) == []
