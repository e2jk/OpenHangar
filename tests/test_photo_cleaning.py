"""Every stored photo goes through services/images.py: orientation applied,
re-encoded as JPEG without EXIF, GPS or other metadata — aircraft photos
(also on the public showcase), flight counter/fuel photos and photos shared
from the phone. HEIC and GIF are refused with a message."""

import io
import os
from datetime import date
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import AircraftPhoto, Flight, db
from PIL import Image

from tests.test_aircraft_photos import _add_aircraft, _login, _make_user_tenant
from tests.test_passport_stamps import image_bytes


def gps_photo() -> bytes:
    """A JPEG carrying a camera description and a GPS position."""
    stream = io.BytesIO()
    exif = Image.Exif()
    exif[270] = "Private metadata"
    exif[34853] = {1: "N", 2: (50.0, 30.0, 0.0), 3: "E", 4: (4.0, 12.0, 0.0)}
    Image.new("RGB", (40, 30), "red").save(stream, format="JPEG", exif=exif)
    return stream.getvalue()


def _clean(path: Path) -> None:
    data = path.read_bytes()
    assert b"Private metadata" not in data
    with Image.open(path) as img:
        assert img.format == "JPEG"
        assert not img.getexif()


@pytest.fixture
def owner(app: Flask, client: FlaskClient) -> int:
    _uid, tid = _make_user_tenant(app, "clean@x.com", "clean-hangar")
    aid = _add_aircraft(app, tid, "OO-CLN")
    _login(app, client, "clean@x.com")
    return aid


def test_aircraft_photos_lose_their_metadata(
    app: Flask, client: FlaskClient, owner: int
) -> None:
    response = client.post(
        f"/aircraft/{owner}/photos/upload",
        data={"photos": (io.BytesIO(gps_photo()), "apron.png", "image/png")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    with app.app_context():
        (photo,) = AircraftPhoto.query.filter_by(aircraft_id=owner).all()
        assert photo.filename.endswith(".jpg")
        assert photo.original_filename == "apron.png"
        _clean(Path(app.config["UPLOAD_FOLDER"]) / photo.filename)


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("phone.heic", b"\x00\x00\x00\x18ftypheic", b"unsupported format"),
        ("broken.jpg", b"\xff\xd8\xff not a photo", b"valid JPEG, PNG or WebP"),
    ],
)
def test_aircraft_photos_that_cannot_be_cleaned_are_refused(
    app: Flask, client: FlaskClient, owner: int, name: str, data: bytes, message: bytes
) -> None:
    response = client.post(
        f"/aircraft/{owner}/photos/upload",
        data={"photos": (io.BytesIO(data), name, "application/octet-stream")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert message in response.data
    with app.app_context():
        assert AircraftPhoto.query.filter_by(aircraft_id=owner).count() == 0


def _flight(owner: int, **photos: tuple) -> dict:
    return {
        "aircraft_id": str(owner),
        "date": "2026-06-01",
        "departure_icao": "EBOS",
        "arrival_icao": "EBBR",
        "flight_time_counter_start": "100.0",
        "flight_time_counter_end": "101.5",
        "crew_name_0": "Test Pilot",
        "crew_role_0": "PIC",
        **photos,
    }


def test_counter_photos_lose_their_metadata(
    app: Flask, client: FlaskClient, owner: int
) -> None:
    response = client.post(
        "/flights/new",
        data=_flight(
            owner,
            flight_counter_photo=(io.BytesIO(gps_photo()), "hobbs.webp"),
            fuel_photo=(io.BytesIO(b"GIF89a"), "fuel.gif"),
        ),
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"fuel.gif: Upload a JPEG, PNG or WebP photo." in response.data
    with app.app_context():
        flight = Flight.query.filter_by(aircraft_id=owner).one()
        assert flight.fuel_photo is None
        assert flight.flight_counter_photo.endswith(".jpg")
        _clean(Path(app.config["UPLOAD_FOLDER"]) / flight.flight_counter_photo)


def test_a_broken_counter_photo_is_refused_with_its_name(
    app: Flask, client: FlaskClient, owner: int
) -> None:
    response = client.post(
        "/flights/new",
        data=_flight(owner, engine_counter_photo=(io.BytesIO(b"\xff\xd8"), "x.jpg")),
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"x.jpg: Upload a valid JPEG, PNG or WebP photo" in response.data
    with app.app_context():
        assert (
            Flight.query.filter_by(aircraft_id=owner).one().engine_counter_photo is None
        )
    assert not [
        name
        for name in os.listdir(app.config["UPLOAD_FOLDER"])
        if name.startswith("flight_") and "engine" in name
    ]


def test_valid_photos_still_upload(app: Flask, client: FlaskClient, owner: int) -> None:
    client.post(
        f"/aircraft/{owner}/photos/upload",
        data={"photos": (io.BytesIO(image_bytes()), "a.jpg", "image/jpeg")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        assert AircraftPhoto.query.filter_by(aircraft_id=owner).count() == 1


# ── Re-encoding photos stored before cleaning (flask clean-photos) ──────────


@pytest.fixture
def stored(app: Flask, owner: int) -> dict[str, str]:
    """Photos as older versions stored them: as uploaded."""
    folder = Path(app.config["UPLOAD_FOLDER"])
    (folder / "club" / "OO-CLN" / "photos").mkdir(parents=True, exist_ok=True)
    files = {
        "gps": "club/OO-CLN/photos/01-aaaaaa.jpg",
        "png": "flight_1_flight_bbbb.png",
        "heic": "club/OO-CLN/photos/02-cccccc.heic",
        "clean": "club/OO-CLN/photos/03-dddddd.jpg",
        "gone": "club/OO-CLN/photos/04-eeeeee.jpg",
    }
    (folder / files["gps"]).write_bytes(gps_photo())
    png = io.BytesIO()
    Image.new("RGB", (10, 10)).save(png, format="PNG")
    (folder / files["png"]).write_bytes(png.getvalue())
    (folder / files["heic"]).write_bytes(b"\x00\x00\x00\x18ftypheic")
    plain = io.BytesIO()
    Image.new("RGB", (10, 10)).save(plain, format="JPEG")
    (folder / files["clean"]).write_bytes(plain.getvalue())
    with app.app_context():
        for key in ("gps", "heic", "clean", "gone"):
            db.session.add(
                AircraftPhoto(
                    aircraft_id=owner, filename=files[key], original_filename="p.jpg"
                )
            )
        db.session.add(
            Flight(
                aircraft_id=owner,
                date=date(2026, 1, 1),
                flight_counter_photo=files["png"],
            )
        )
        db.session.commit()
    return files


def test_a_dry_run_only_reports(app: Flask, stored: dict[str, str]) -> None:
    from services.photo_reencode import reencode

    with app.app_context():
        report = reencode(app.config["UPLOAD_FOLDER"])
        assert sorted(report.to_clean) == sorted([stored["gps"], stored["png"]])
        assert report.unreadable == [stored["heic"]]
        assert report.clean == [stored["clean"]] and report.missing == [stored["gone"]]
        assert report.cleaned == []
    assert (
        b"Private metadata"
        in (Path(app.config["UPLOAD_FOLDER"]) / stored["gps"]).read_bytes()
    )


@pytest.mark.parametrize("keep", [False, True])
def test_apply_reencodes_and_retires_the_originals(
    app: Flask, stored: dict[str, str], keep: bool
) -> None:
    from aircraft.routes import thumb_path_for
    from services.photo_reencode import reencode

    folder = Path(app.config["UPLOAD_FOLDER"])
    with app.app_context():
        report = reencode(str(folder), apply=True, keep_originals=keep)
        assert sorted(report.cleaned) == sorted([stored["gps"], stored["png"]])
        photo = AircraftPhoto.query.filter(
            AircraftPhoto.filename.like("%01-aaaaaa-clean%")
        ).one()
        flight = Flight.query.filter(Flight.flight_counter_photo.isnot(None)).one()
        for relpath in (photo.filename, flight.flight_counter_photo):
            assert relpath.endswith(".jpg")
            _clean(folder / relpath)
        assert (folder / thumb_path_for(photo.filename)).is_file()
        # Run again: everything is clean now.
        again = reencode(str(folder))
        assert again.to_clean == [] and len(again.clean) == 3
    assert not (folder / stored["gps"]).exists()
    trashed = folder / "_trash" / "photos" / stored["gps"]
    assert trashed.exists() is keep


def test_unreadable_photos_are_left_alone_on_apply(
    app: Flask, stored: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import services.photo_reencode as module

    monkeypatch.setattr(module, "_cleaned_bytes", lambda path: None)
    with app.app_context():
        report = module.reencode(app.config["UPLOAD_FOLDER"], apply=True)
        assert report.cleaned == [] and len(report.unreadable) == 3


def test_the_cli_command(app: Flask, stored: dict[str, str]) -> None:
    runner = app.test_cli_runner()
    with app.app_context():
        dry = runner.invoke(args=["clean-photos"])
        done = runner.invoke(args=["clean-photos", "--apply", "--keep-originals"])
    assert "To re-encode (run with --apply): 2" in dry.output, dry
    assert "Cannot be decoded (HEIC, GIF or damaged)" in dry.output
    assert "Missing from disk: 1" in dry.output
    assert "Re-encoded: 2" in done.output and "Already clean: 1" in done.output


def test_stored_gifs_and_refused_files_are_reported_not_changed(
    app: Flask, owner: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    import services.photo_reencode as module
    from services.images import PhotoError

    folder = Path(app.config["UPLOAD_FOLDER"])
    gif = io.BytesIO()
    Image.new("RGB", (5, 5)).save(gif, format="GIF")
    (folder / "flight_9_fuel_gif.gif").write_bytes(gif.getvalue())
    (folder / "flight_9_fuel_big.jpg").write_bytes(gps_photo())
    with app.app_context():
        db.session.add_all(
            [
                Flight(date=date(2026, 1, 1), fuel_photo="flight_9_fuel_gif.gif"),
                Flight(date=date(2026, 1, 2), fuel_photo="flight_9_fuel_big.jpg"),
            ]
        )
        db.session.commit()

        def refuse(upload):
            raise PhotoError("too big")

        monkeypatch.setattr(module, "clean_photo", refuse)
        report = module.reencode(str(folder), apply=True)
    assert sorted(report.unreadable) == [
        "flight_9_fuel_big.jpg",
        "flight_9_fuel_gif.gif",
    ]
    assert report.cleaned == []
