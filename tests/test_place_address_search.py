"""The Places form submits address searches to the server without logging queries."""

import io
import json
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask
from flask.testing import FlaskClient
from services import geocoding

from tests.test_geocoding import ROWS
from tests.test_place_collections import people
from tests.test_place_sharing import signin

__all__ = ["people"]


@pytest.fixture(autouse=True)
def isolated_geocoder(
    app: Flask, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
    monkeypatch.setitem(app.config, "GEOCODER_URL", geocoding.DEFAULT_URL)
    monkeypatch.setitem(app.config, "UPLOAD_FOLDER", str(tmp_path))
    geocoding._cache.clear()
    yield
    geocoding._cache.clear()


def test_form_never_searches_until_explicit_post(
    app: Flask, client: FlaskClient, people: dict[str, int]
) -> None:
    signin(client, people, "personal")
    with patch("urllib.request.urlopen") as fetch:
        response = client.get("/places/new")
        assert response.status_code == 200
        assert 'id="place-address-query"' in response.text
        assert "OpenStreetMap contributors" in response.text
        fetch.assert_not_called()
    seen: list[urllib.request.Request] = []

    def provider(request: urllib.request.Request, timeout: float) -> io.BytesIO:
        seen.append(request)
        assert timeout == geocoding.REQUEST_SECONDS
        return io.BytesIO(json.dumps(ROWS).encode())

    with patch("urllib.request.urlopen", provider):
        response = client.post("/places/geocode", data={"query": "Charleroi"})
    assert response.status_code == 200 and len(response.json["results"]) == 2
    assert response.json["results"][0] == {
        "name": "Charleroi",
        "address": "Charleroi, Hainaut, Wallonia, Belgium",
        "latitude": 50.4592,
        "longitude": 4.4538,
    }
    assert len(seen) == 1 and "q=Charleroi" in seen[0].full_url
    assert "no-store" in response.headers["Cache-Control"]
    assert client.get("/aircraft/").status_code == 403


@pytest.mark.parametrize(
    "reason,status",
    [("invalid", 400), ("disabled", 503), ("busy", 429), ("unavailable", 503)],
)
def test_provider_failures_are_clear_and_do_not_echo_searches(
    client: FlaskClient,
    people: dict[str, int],
    reason: str,
    status: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    signin(client, people)
    with patch(
        "places.place_routes.geocoding.search",
        side_effect=geocoding.GeocoderError(reason),
    ):
        response = client.post("/places/geocode", data={"query": "SENSITIVE SEARCH"})
    assert response.status_code == status
    assert response.json["reason"] == reason and response.json["error"]
    assert (
        "SENSITIVE SEARCH" not in response.text
        and "SENSITIVE SEARCH" not in caplog.text
    )


def test_disabled_search_keeps_pin_and_coordinate_controls(
    app: Flask,
    client: FlaskClient,
    people: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(app.config, "GEOCODER_URL", "")
    signin(client, people)
    response = client.get("/places/new")
    assert response.status_code == 200 and 'id="place-address-query"' in response.text
    assert "Address search is turned off" in response.text
    assert (
        'id="place-editor-map"' in response.text
        and 'id="place-latitude"' in response.text
    )
    assert (
        client.post("/places/geocode", data={"query": "Charleroi"}).status_code == 503
    )


def test_search_parses_coordinates_locally(
    client: FlaskClient, people: dict[str, int]
) -> None:
    signin(client, people)
    with patch("services.geocoding.search") as search:
        response = client.post("/places/geocode", data={"query": "50.5, 4.2"})
        search.assert_not_called()
    assert response.json == {"position": {"latitude": 50.5, "longitude": 4.2}}
