"""Where next? on its own page (Explore, 42f): one search over all or chosen
passports, airfields deduplicated with their reasons, the old passport URL
redirecting to it."""

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

from flask import Flask
from flask.testing import FlaskClient
from models import PassportAirfield, PassportAirfieldIdentity, db
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
    page = _search(client, owner, passport=owner["passport"]).get_data(as_text=True)
    assert ">Shared</a>" in page and ">Single</a>" not in page
    assert "Second</b> passport" not in page.split("<tbody>")[1]
    # A passport the user cannot open, or an archived one, is ignored: the
    # search falls back to every active passport.
    for foreign in (999999, ids["old"]):
        page = _search(client, owner, passport=foreign).get_data(as_text=True)
        assert ">Shared</a>" in page and ">Single</a>" in page


def test_downloads_cover_the_chosen_passports(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    _setup(app, owner)
    signin(client, owner["user"])
    page = _search(client, owner, passport=owner["passport"]).get_data(as_text=True)
    assert "download=gpx" in page and f"passport={owner['passport']}" in page
    download = _search(client, owner, passport=owner["passport"], download="gpx")
    assert download.status_code == 200
    assert 'filename="where-next.gpx"' in download.headers["Content-Disposition"]
    assert b"SHRD" in download.data and b"SNGL" not in download.data
    assert _search(client, owner, download="zip").status_code == 400
    assert client.get(URL, query_string={"download": "gpx"}).status_code == 400


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
        "passport": [str(owner["passport"])],
        "limit": ["20"],
        "download": ["kml"],
    }
    assert client.get("/passports/999999/where-next").status_code == 404
    # The passport page links straight to the new page.
    detail = client.get(f"/passports/{owner['passport']}/").get_data(as_text=True)
    assert f"/explore/where-next?passport={owner['passport']}" in detail


def test_without_passports_the_page_says_how_to_start(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    signin(client, owner["other"])
    page = client.get(URL).get_data(as_text=True)
    assert "Create or join an airfield passport" in page


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
