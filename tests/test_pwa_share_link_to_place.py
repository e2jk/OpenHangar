"""Sharing a link from the phone (an Instagram reel, a YouTube video…) opens a
new place prefilled with it; nothing is saved until the user saves (42i)."""

from urllib.parse import parse_qs, urlsplit

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import Place, db

from tests.test_place_collections import people
from tests.test_place_sharing import signin

__all__ = ["people"]

REEL = "https://www.instagram.com/reel/C9xYz12AbCd/"


def _share(client: FlaskClient, **fields: str):
    return client.post("/pwa/shared", data=fields, content_type="multipart/form-data")


def _prefill(response) -> dict[str, list[str]]:
    assert response.status_code == 302
    location = urlsplit(response.headers["Location"])
    assert location.path == "/places/new"
    return parse_qs(location.query)


@pytest.mark.parametrize("name", ["creator", "personal"])
def test_an_instagram_share_opens_a_new_place_with_the_clean_link(
    app: Flask, client: FlaskClient, people: dict[str, int], name: str
) -> None:
    signin(client, people, name)
    response = _share(
        client, text=f"Amazing castle near Spa! {REEL}?igsh=MWQ1ZGUxMzBkMA=="
    )
    assert _prefill(response) == {
        "title": ["Amazing castle near Spa!"],
        "external_url": [REEL],
    }
    form = client.get(response.headers["Location"])
    assert form.status_code == 200
    page = form.get_data(as_text=True)
    assert f'value="{REEL}"' in page and 'value="Amazing castle near Spa!"' in page
    with app.app_context():
        assert db.session.query(Place).count() == 0  # nothing saved yet


def test_the_url_field_and_title_are_used_when_sent(
    client: FlaskClient, people: dict[str, int]
) -> None:
    signin(client, people)
    response = _share(
        client,
        title="Castle tour",
        url="https://www.youtube.com/watch?v=abc&si=track",
        text="ignored https://other.example/",
    )
    assert _prefill(response) == {
        "title": ["Castle tour"],
        "external_url": ["https://www.youtube.com/watch?v=abc"],
    }


def test_a_bare_link_gets_no_suggested_title(
    client: FlaskClient, people: dict[str, int]
) -> None:
    signin(client, people)
    assert _prefill(_share(client, text=REEL)) == {"external_url": [REEL]}


@pytest.mark.parametrize(
    "fields",
    [{}, {"text": "no link here"}, {"url": "javascript:alert(1)"}],
)
def test_a_share_without_a_usable_link_or_file_saves_nothing(
    client: FlaskClient, people: dict[str, int], fields: dict[str, str]
) -> None:
    signin(client, people)
    response = _share(client, **fields)
    assert response.status_code == 302
    assert urlsplit(response.headers["Location"]).path == "/"
    with client.session_transaction() as sess:
        flashes = sess.get("_flashes", [])
    assert any("Nothing that OpenHangar can save" in text for _, text in flashes)


def test_a_personal_account_sharing_a_file_is_offered_nothing_and_can_cancel(
    client: FlaskClient, people: dict[str, int]
) -> None:
    import io

    signin(client, people, "personal")
    response = client.post(
        "/pwa/shared",
        data={"files": (io.BytesIO(b"\xff\xd8\xff photo"), "p.jpg", "image/jpeg")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert b"No compatible destination" in response.data
    assert b"dest-" not in response.data
    assert client.get("/pwa/shared").status_code == 200
    assert client.post("/pwa/shared/discard").status_code == 302
    with client.session_transaction() as sess:
        assert "share_pending" not in sess
