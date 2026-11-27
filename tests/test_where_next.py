"""Where next? on its own page (Explore, 42f): one search over all or chosen
passports, airfields deduplicated with their reasons, the old passport URL
redirecting to it."""

from datetime import UTC, date, datetime
from urllib.parse import parse_qs, urlsplit

from flask import Flask
from flask.testing import FlaskClient
from models import (
    Aircraft,
    PassportAirfield,
    PassportAirfieldIdentity,
    Role,
    TenantUser,
    db,
)
from passports.catalogue import create_custom
from passports.management import create_personal, metadata_values

from tests.test_passport_builder import signin
from tests.test_passport_management import owner

__all__ = ["owner"]

URL = "/explore/where-next"


def _setup(app: Flask, owner: dict[str, int]) -> dict[str, int]:
    """Two passports from the same origin: 'Shared' is a target of both,
    'Single' (as far) of the second only, 'Archived' of an archived one."""
    with app.app_context():
        t, u = owner["tenant"], owner["user"]
        origin = db.session.get(PassportAirfieldIdentity, owner["field"])
        origin.latitude, origin.longitude = 50, 4
        shared = create_custom(t, "Shared", code="SHRD", latitude=50.1, longitude=4.0)
        single = create_custom(t, "Single", code="SNGL", latitude=50.1, longitude=4.0)
        away = create_custom(t, "Archived", code="ARCH", latitude=50.2, longitude=4.0)
        second = create_personal(
            t, u, metadata_values({"title": "<b>Second</b> passport"})
        )
        old = create_personal(t, u, metadata_values({"title": "Old passport"}))
        db.session.flush()
        old.archived_at = datetime.now(UTC)
        for passport_id, field in [
            (owner["passport"], shared),
            (second.id, shared),
            (second.id, single),
            (old.id, away),
        ]:
            db.session.add(
                PassportAirfield(
                    tenant_id=t, passport_id=passport_id, airfield_id=field.id
                )
            )
        db.session.commit()
        return {"second": second.id, "old": old.id}


def _search(client: FlaskClient, owner: dict[str, int], **args: object):
    return client.get(
        URL, query_string={"origin": f"field:{owner['field']}", "limit": 50, **args}
    )


def test_one_row_per_airfield_with_every_passport_as_a_reason(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    ids = _setup(app, owner)
    signin(client, owner["user"])
    page = _search(client, owner).get_data(as_text=True)
    assert 'value="time" selected' in page
    assert (
        'id="discovery-speed" step="0.01" min="0.01" max="9999.99" value="100"' in page
    )
    assert page.count(">Shared</a>") == 1
    assert "Not yet stamped in Local fields" in page
    # Passport titles are escaped, never markup.
    assert "Not yet stamped in &lt;b&gt;Second&lt;/b&gt; passport" in page
    # At equal distance, the airfield with more reasons comes first.
    assert page.index(">Shared</a>") < page.index(">Single</a>")
    # An archived passport is neither offered nor searched.
    assert "Old passport" not in page and "Archived" not in page
    assert f'value="{ids["second"]}"' in page


def test_chosen_passports_limit_the_search(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    ids = _setup(app, owner)
    signin(client, owner["user"])
    page = _search(client, owner, stamp_book=owner["passport"]).get_data(as_text=True)
    assert ">Shared</a>" in page and ">Single</a>" not in page
    assert "Second</b> passport" not in page.split("<tbody>")[1]
    # Bookmarks made with the former parameter name still choose it.
    legacy = _search(client, owner, passport=owner["passport"]).get_data(as_text=True)
    assert ">Shared</a>" in legacy and ">Single</a>" not in legacy
    # A passport the user cannot open, or an archived one, is ignored: the
    # search falls back to every active passport.
    for foreign in (999999, ids["old"]):
        page = _search(client, owner, stamp_book=foreign).get_data(as_text=True)
        assert ">Shared</a>" in page and ">Single</a>" in page


def test_no_link_names_a_parameter_after_a_password(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    """URL scanners report any parameter whose name contains "pass" as a
    password in the URL (ZAP rule 10024): the page, its form and its
    download links never use one."""
    _setup(app, owner)
    signin(client, owner["user"])
    page = _search(client, owner, stamp_book=owner["passport"]).get_data(as_text=True)
    assert "passport=" not in page
    assert 'name="passport"' not in page
    assert 'name="stamp_book"' in page


def test_downloads_cover_the_chosen_passports(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    _setup(app, owner)
    signin(client, owner["user"])
    page = _search(client, owner, stamp_book=owner["passport"]).get_data(as_text=True)
    assert "download=gpx" in page and f"stamp_book={owner['passport']}" in page
    download = _search(client, owner, stamp_book=owner["passport"], download="gpx")
    assert download.status_code == 200
    assert (
        'filename="where-next-unvisited.gpx"' in download.headers["Content-Disposition"]
    )
    assert b"SHRD" in download.data and b"SNGL" not in download.data
    assert _search(client, owner, download="zip").status_code == 400
    assert client.get(URL, query_string={"download": "gpx"}).status_code == 400


def test_no_download_is_offered_when_no_airfield_matches(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    _setup(app, owner)
    signin(client, owner["user"])
    # Only places, and the account has none.
    page = _search(client, owner, source="places").get_data(as_text=True)
    assert ">Shared</a>" not in page
    assert "download=gpx" not in page and "download=kml" not in page


def test_the_old_passport_url_redirects_with_its_inputs(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    signin(client, owner["user"])
    response = client.get(
        f"/passports/{owner['passport']}/where-next",
        query_string={"limit": "20", "download": "kml", "unknown": "dropped"},
    )
    assert response.status_code == 302
    location = urlsplit(response.headers["Location"])
    assert location.path == URL
    assert parse_qs(location.query) == {
        "stamp_book": [str(owner["passport"])],
        "limit": ["20"],
        "download": ["kml"],
    }
    assert client.get("/passports/999999/where-next").status_code == 404
    # The passport page links straight to the new page.
    detail = client.get(f"/passports/{owner['passport']}/").get_data(as_text=True)
    assert f"/explore/where-next?stamp_book={owner['passport']}" in detail


def test_without_passports_the_page_says_how_to_start(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    signin(client, owner["other"])
    page = client.get(URL).get_data(as_text=True)
    assert "Add places or create an airfield passport" in page


def test_the_explore_menu_and_a_signed_out_or_membershipless_user(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    assert client.get(URL).status_code == 302  # to the login page
    signin(client, owner["user"])
    page = client.get("/places/").get_data(as_text=True)
    assert "Explore" in page and 'href="/explore/where-next"' in page
    from unittest.mock import patch

    with patch("explore.routes.current_membership", return_value=None):
        assert client.get(URL).status_code == 403


def _places(app: Flask, owner: dict[str, int]) -> dict[str, int]:
    """Places of the owner: Castle (linked to Shared, also a passport target,
    and to Lakeside), a visited Mill (linked to Millfield), an unlocated idea
    linked to Lakeside, a closed field and a place with no airfield."""
    from models import PlaceVisit
    from places.airfields import add_link
    from places.collections import create_collection
    from places.management import save_place

    ids = _setup(app, owner)
    with app.app_context():
        t, u = owner["tenant"], owner["user"]
        trips = create_collection(t, u, "Trips")
        other = create_collection(t, u, "Other")
        shared = PassportAirfieldIdentity.query.filter_by(
            tenant_id=t, name="Shared"
        ).one()
        lakeside = create_custom(
            t, "Lakeside", code="LAKE", latitude=50.05, longitude=4.0
        )
        millfield = create_custom(
            t, "Millfield", code="MILL", latitude=50.06, longitude=4.0
        )
        closed = create_custom(
            t, "Closed strip", code="SHUT", latitude=50.07, longitude=4.0
        )
        closed.is_closed = True
        castle = save_place(
            t,
            u,
            trips.id,
            {"title": "Castle", "latitude": "50.1", "longitude": "4.01"},
            tags=["Museum"],
        )
        mill = save_place(
            t, u, trips.id, {"title": "Mill", "latitude": "50.06", "longitude": "4.01"}
        )
        idea = save_place(
            t, u, other.id, {"title": "Idea", "external_url": "https://example.org/x"}
        )
        save_place(
            t, u, other.id, {"title": "Lonely", "latitude": "50", "longitude": "4"}
        )
        add_link(t, u, castle.id, shared.id, note="taxi")
        add_link(t, u, castle.id, lakeside.id)
        add_link(t, u, mill.id, millfield.id)
        add_link(t, u, idea.id, lakeside.id)
        add_link(t, u, mill.id, closed.id)
        db.session.add(
            PlaceVisit(place_id=mill.id, user_id=u, visit_date=date(2026, 1, 1))
        )
        db.session.commit()
        return {
            **ids,
            "trips": trips.id,
            "other": other.id,
            "castle": castle.id,
            "idea": idea.id,
        }


def test_places_and_passports_together_with_their_reasons(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    _places(app, owner)
    signin(client, owner["user"])
    page = _search(client, owner).get_data(as_text=True)
    assert 'name="source"' in page  # both sources have content
    assert page.count(">Shared</a>") == 1
    shared_row = page.split(">Shared</a>")[1].split("</tr>")[0]
    assert "Not yet stamped in Local fields" in shared_row
    assert "km from Castle" in shared_row and "(taxi)" in shared_row
    lakeside_row = page.split(">Lakeside</a>")[1].split("</tr>")[0]
    assert "km from Castle" in lakeside_row and "Linked to Idea" in lakeside_row
    # A visited place and a closed field contribute nothing by default.
    assert ">Millfield</a>" not in page and "Closed strip" not in page
    assert "One place has no airfield yet." in page
    assert 'href="/places/?linked=unlinked"' in page


def test_choosing_the_source_collections_and_visited_places(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    ids = _places(app, owner)
    signin(client, owner["user"])
    places_only = _search(client, owner, source="places").get_data(as_text=True)
    assert ">Single</a>" not in places_only and ">Lakeside</a>" in places_only
    assert "Not yet stamped" not in places_only
    passports_only = _search(client, owner, source="passports").get_data(as_text=True)
    assert ">Lakeside</a>" not in passports_only and ">Single</a>" in passports_only
    assert "no airfield yet" not in passports_only
    visited = _search(client, owner, source="places", visited="1").get_data(
        as_text=True
    )
    assert ">Millfield</a>" in visited
    expanded = _search(client, owner, source="places", unvisited_places="0").text
    assert ">Millfield</a>" in expanded
    assert 'id="discovery-visited" checked' not in expanded
    assert 'id="discovery-visited" checked' in places_only
    other = _search(client, owner, source="places", collection=ids["other"]).get_data(
        as_text=True
    )
    assert ">Lakeside</a>" in other and "km from Castle" not in other
    assert "Linked to Idea" in other and "One place has no airfield yet." in other
    assert f'href="/places/?linked=unlinked&amp;collection={ids["other"]}"' in other
    assert _search(client, owner, source="nonsense").status_code == 200


def test_one_place_preselected_from_its_page(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    ids = _places(app, owner)
    signin(client, owner["user"])
    page = _search(client, owner, source="places", place=ids["castle"]).get_data(
        as_text=True
    )
    assert ">Shared</a>" in page and ">Lakeside</a>" in page
    assert "Linked to Idea" not in page and "no airfield yet" not in page
    assert f'name="place" value="{ids["castle"]}"' in page
    links = _search(
        client, owner, source="places", place=ids["castle"], collection=ids["trips"]
    )
    assert "collection=" in links.get_data(as_text=True)


def test_places_alone_need_no_source_choice(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    from places.collections import create_collection

    with app.app_context():
        create_collection(owner["tenant"], owner["other"], "Only places")
        db.session.commit()
    signin(client, owner["other"])
    page = client.get(URL).get_data(as_text=True)
    assert 'name="source"' not in page and "Only places" in page
    assert "Add places or create" not in page


def test_places_are_skipped_when_the_account_cannot_use_them(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    from unittest.mock import patch

    from places.access import PlaceError

    _setup(app, owner)
    signin(client, owner["user"])
    with patch("explore.routes.collections_for_user", side_effect=PlaceError("x")):
        page = _search(client, owner).get_data(as_text=True)
    assert ">Shared</a>" in page and 'name="source"' not in page


def test_tag_and_never_landed_filters_and_the_place_pins(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    import json
    import re
    from html import unescape

    from models import Flight

    _places(app, owner)
    signin(client, owner["user"])
    page = _search(client, owner, source="places", tag="museum").get_data(as_text=True)
    assert ">Shared</a>" in page and ">Lakeside</a>" in page
    assert "Linked to Idea" not in page and "no airfield yet" not in page
    assert '<option value="Museum" selected' in page
    nothing = _search(client, owner, source="places", tag="Nope").get_data(as_text=True)
    assert "km from" not in nothing
    data = json.loads(
        unescape(re.search(r'data-map="([^"]*)"', page).group(1))  # type: ignore[union-attr]
    )
    (castle,) = data["places"]
    assert castle["name"] == "Castle" and len(castle["airfields"]) == 2
    assert castle["url"].startswith("/places/")
    with app.app_context():
        db.session.add(
            Flight(
                pic_user_id=owner["user"], date=date(2025, 3, 1), arrival_icao="lake"
            )
        )
        db.session.commit()
    fresh = _search(client, owner).text
    assert ">Lakeside</a>" not in fresh and ">Shared</a>" in fresh
    assert 'id="discovery-never-landed" checked' in fresh
    expanded = _search(client, owner, never_landed="0").text
    assert ">Lakeside</a>" in expanded
    assert "never_landed=0" in expanded


def test_a_passport_or_place_link_preselects_its_source(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    ids = _places(app, owner)
    signin(client, owner["user"])
    from_passport = _search(client, owner, passport=owner["passport"]).get_data(
        as_text=True
    )
    assert 'value="passports" id="discovery-source-passports" checked' in from_passport
    assert "km from Castle" not in from_passport
    assert 'data-discovery-source="places" hidden' in from_passport
    assert 'class="card-body" disabled' in from_passport
    assert '<option value="Museum"' in from_passport
    from_place = client.get(URL, query_string={"place": ids["castle"]}).get_data(
        as_text=True
    )
    assert 'value="places" id="discovery-source-places" checked' in from_place
    assert 'data-discovery-source="passports" hidden' in from_place


def test_first_load_uses_a_single_aircraft_home_and_searches_both_sources(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    _places(app, owner)
    with app.app_context():
        db.session.get(TenantUser, (owner["user"], owner["tenant"])).role = Role.ADMIN
        db.session.add(
            Aircraft(
                tenant_id=owner["tenant"],
                registration="OO-UX",
                make="Cessna",
                model="172",
                home_airfield_id=owner["field"],
                cruise_speed_knots=125,
            )
        )
        db.session.commit()
    signin(client, owner["user"])
    page = client.get(URL).get_data(as_text=True)
    assert "Starting from" in page
    assert 'value="both" id="discovery-source-both" checked' in page
    assert 'value="125.00"' in page
    assert "Not yet stamped in Local fields" in page
    assert "km from Castle" in page
