"""Suggestions from a chosen counter/fuel photo (Phase 46): when it was taken
(UTC) and the airfield it was taken at, read in memory and never stored."""

import io
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from PIL import Image
from services import photo_hints

from tests.test_flights import _add_aircraft, _create_user_and_tenant, _login
from tests.test_place_collections import people
from tests.test_place_sharing import signin

__all__ = ["people"]

EBOS = (51.1998, 2.874673)


def _dms(value: float) -> tuple[float, float, float]:
    value = abs(value)
    degrees = int(value)
    minutes = int((value - degrees) * 60)
    seconds = round(((value - degrees) * 60 - minutes) * 60, 4)
    return float(degrees), float(minutes), seconds


def photo(
    taken: str | None = "2026:05:12 14:30:05",
    offset: str | None = "+02:00",
    position: tuple[float, float] | None = EBOS,
    fmt: str = "JPEG",
) -> bytes:
    exif = Image.Exif()
    details: dict[int, Any] = {}
    if taken:
        details[36867] = taken
    if offset:
        details[36881] = offset
    if details:
        exif[0x8769] = details
    if position:
        lat, lon = position
        exif[0x8825] = {
            1: "N" if lat >= 0 else "S",
            2: _dms(lat),
            3: "E" if lon >= 0 else "W",
            4: _dms(lon),
        }
    stream = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(stream, format=fmt, exif=exif)
    return stream.getvalue()


def test_camera_time_and_airfield() -> None:
    hints = photo_hints.read_hints(photo(), "IMG_0001.jpg", 60)
    assert hints["time"] == "12:30" and hints["date"] == "2026-05-12"
    assert hints["time_source"] == "camera"
    assert hints["airfield"]["ident"] == "EBOS"
    assert hints["airfield"]["distance_km"] < 0.1
    assert hints["position"] == {"latitude": 51.1998, "longitude": 2.874673}


def test_without_camera_offset_the_device_offset_is_used() -> None:
    hints = photo_hints.read_hints(photo(offset=None), "x.jpg", 120)
    assert hints["time"] == "12:30" and hints["time_source"] == "device"
    assert photo_hints.read_hints(photo(offset=None), "x.jpg", None)["time"] is None
    # A camera offset beyond any real time zone is ignored.
    assert (
        photo_hints.read_hints(photo(offset="+23:00"), "x.jpg", 0)["time_source"]
        == "device"
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("IMG_20260512_143005.jpg", "14:30"),
        ("PXL_20260512_143005123.jpg", "14:30"),
        ("2026-05-12 14.30.05.jpg", "14:30"),
        ("Photo 2026-05-12 at 14.30.05.png", "14:30"),
        ("IMG_20261340_250000.jpg", None),  # not a real date
        ("holiday.jpg", None),
    ],
)
def test_file_name_time_when_the_photo_has_none(
    name: str, expected: str | None
) -> None:
    hints = photo_hints.read_hints(
        photo(taken=None, offset=None, position=None), name, 0
    )
    assert hints["time"] == expected
    if expected:
        assert hints["time_source"] == "filename"


def test_no_airfield_when_far_or_ambiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    far = photo_hints.read_hints(
        photo(position=(51.25, 2.874673)), "x.jpg", 0
    )  # ~5.6 km north
    assert far["airfield"] is None
    fields = {
        "AAAA": {"lat": 50.0, "lon": 4.0, "name": "A"},
        "BBBB": {"lat": 50.0, "lon": 4.012, "name": "B"},
    }
    monkeypatch.setattr(photo_hints, "_load_airport_geo", lambda: fields)
    between = photo_hints.read_hints(photo(position=(50.0, 4.006)), "x.jpg", 0)
    assert between["airfield"] is None
    near_a = photo_hints.read_hints(photo(position=(50.0, 4.0005)), "x.jpg", 0)
    assert near_a["airfield"]["ident"] == "AAAA"
    monkeypatch.setattr(photo_hints, "_load_airport_geo", dict)
    assert photo_hints.read_hints(photo(), "x.jpg", 0)["airfield"] is None


@pytest.mark.parametrize(
    "data",
    [
        b"not an image",
        b"\xff" * (10 * 1024 * 1024 + 1),
    ],
)
def test_unreadable_or_oversized_photos_give_no_hints(data: bytes) -> None:
    assert photo_hints.read_hints(data, "x.jpg", 0) == {
        "time": None,
        "date": None,
        "time_source": None,
        "airfield": None,
        "position": None,
    }


def test_odd_metadata_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    assert photo_hints._exif_time("not a date") is None
    assert photo_hints._degrees(("a", "b", "c"), "N") is None
    assert photo_hints._degrees((1.0, 2.0, 3.0), "W") < 0
    assert photo_hints._offset("bogus") is None
    monkeypatch.setattr(photo_hints, "MAX_PHOTO_PIXELS", 10)
    assert photo_hints.read_hints(photo(), "x.jpg", 0)["time"] is None


def test_the_endpoint_reads_and_discards(app: Flask, client: FlaskClient) -> None:
    import os

    _uid, tid = _create_user_and_tenant(app)
    _add_aircraft(app, tid)
    _login(app, client)
    before = set(os.listdir(app.config["UPLOAD_FOLDER"]))
    response = client.post(
        "/flights/photo-hints",
        data={"photo": (io.BytesIO(photo()), "hobbs.jpg"), "offset": "60"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.json["airfield"]["ident"] == "EBOS"
    assert response.json["time"] == "12:30"
    assert "no-store" in response.headers["Cache-Control"]
    assert set(os.listdir(app.config["UPLOAD_FOLDER"])) == before
    # An offset outside any time zone is ignored; no file is refused.
    out = client.post(
        "/flights/photo-hints",
        data={"photo": (io.BytesIO(photo(offset=None)), "x.jpg"), "offset": "9999"},
        content_type="multipart/form-data",
    )
    assert out.json["time"] is None
    assert client.post("/flights/photo-hints", data={}).status_code == 400


def test_the_form_offers_the_hints(app: Flask, client: FlaskClient) -> None:
    _uid, tid = _create_user_and_tenant(app)
    _add_aircraft(app, tid)
    _login(app, client)
    page = client.get("/flights/new").get_data(as_text=True)
    assert 'id="photo-hints"' in page and "/flights/photo-hints" in page
    assert "js/photo_hints.js" in page


def test_a_place_photo_offers_its_position(
    app: Flask, client: FlaskClient, people: dict[str, int]
) -> None:
    signin(client, people, "personal")
    response = client.post(
        "/places/photo-position",
        data={"photo": (io.BytesIO(photo()), "castle.jpg")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.json == {"position": {"latitude": 51.1998, "longitude": 2.874673}}
    none = client.post(
        "/places/photo-position",
        data={"photo": (io.BytesIO(photo(position=None)), "x.jpg")},
        content_type="multipart/form-data",
    )
    assert none.json == {"position": None}
    assert client.post("/places/photo-position", data={}).status_code == 400
    form = client.get("/places/new").get_data(as_text=True)
    assert 'id="place-photo-hint"' in form and "/places/photo-position" in form
