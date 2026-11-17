"""Live evidence, exact event counts, privacy and safe participant mutations."""

import io
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import (
    Flight,
    Passport,
    PassportParticipant,
    PassportStampPhoto,
    PassportVisit,
    User,
    db,
)
from passports.eligibility import passport_progress
from passports.evidence import MAX_PHOTO_BYTES, photo_path, save_photo
from passports.management import BuilderError
from PIL import Image
from werkzeug.datastructures import FileStorage

from tests.test_passport_builder import signin
from tests.test_passport_management import owner

__all__ = ["owner"]


def flight(uid: int, **values: Any) -> Flight:
    defaults = {"date": date(2025, 1, 1), "pic_user_id": uid, "arrival_icao": "TEST"}
    row = Flight(**(defaults | values))
    db.session.add(row)
    return row


def test_manual_arrival_controls_and_permissions(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    uid, pid, tid = owner["user"], owner["passport"], owner["target"]
    base = f"/passports/{pid}/airfields/{tid}"
    signin(client, uid)
    assert client.get(base).status_code == 200
    for data, message in [
        ({"visit_date": "bad"}, "valid visit date"),
        ({"visit_date": "2999-01-01"}, "not in the future"),
        ({"visit_date": "2025-01-01", "note": "x" * 8001}, "too long"),
    ]:
        response = client.post(base, data={"action": "visit", **data})
        assert response.status_code == 400 and message in response.text
    response = client.post(
        base,
        data={"action": "visit", "visit_date": "2025-01-01", "note": "Nice field"},
        follow_redirects=True,
    )
    assert "Nice field" in response.text and "First visit" in response.text
    with app.app_context():
        visit_id = PassportVisit.query.one().id
        f = flight(uid)
        db.session.commit()
        fid = f.id
    for excluded in ("yes", "yes", "no", "no"):
        assert (
            client.post(
                base, data={"action": "arrival", "flight_id": fid, "exclude": excluded}
            ).status_code
            == 302
        )
    assert (
        client.post(
            base, data={"action": "arrival", "flight_id": 999999999999999999999999}
        ).status_code
        == 404
    )
    assert (
        client.post(
            base, data={"action": "delete_visit", "visit_id": visit_id}
        ).status_code
        == 302
    )
    assert (
        client.post(
            base, data={"action": "delete_visit", "visit_id": visit_id}
        ).status_code
        == 404
    )
    assert client.post(base, data={"action": "invalid"}).status_code == 400
    assert client.get(f"/passports/{pid}/airfields/9999").status_code == 404
    assert (
        client.get(f"/passports/{pid}/?state=unvisited&frequency=1").status_code == 200
    )
    with app.app_context():
        p = db.session.get(Passport, pid)
        p.archived_at = datetime.now(UTC)
        db.session.commit()
    assert client.post(base, data={"action": "visit"}).status_code == 409
    signin(client, owner["other"])
    assert client.post(base, data={"action": "visit"}).status_code == 404
    with app.app_context():
        db.session.add(
            PassportParticipant(
                passport_id=pid,
                user_id=owner["other"],
                display_name="Friend",
                accepted_at=datetime.now(UTC),
            )
        )
        db.session.commit()
    assert client.get(base).status_code == 200
    with app.app_context():
        p = db.session.get(Passport, pid)
        p.archived_at = None
        db.session.get(User, owner["other"]).view_only = True
        db.session.commit()
    assert client.post(base, data={"action": "visit"}).status_code == 403
    signin(client, uid)
    with app.app_context():
        PassportParticipant.query.filter_by(user_id=uid).one().is_active = False
        db.session.commit()
    assert client.post(base, data={"action": "visit"}).status_code == 403


def image_bytes() -> bytes:
    stream = io.BytesIO()
    image = Image.new("RGB", (20, 20), "blue")
    exif = Image.Exif()
    exif[270] = "Private metadata"
    image.save(stream, format="JPEG", exif=exif)
    return stream.getvalue()


def test_private_photo_validation_replace_delete_and_failure(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    signin(client, owner["user"])
    pid, tid = owner["passport"], owner["target"]
    base = f"/passports/{pid}/airfields/{tid}"
    assert client.post(base, data={"action": "photo"}).status_code == 400
    assert (
        client.post(
            base,
            data={"action": "photo", "photo": (io.BytesIO(b"not image"), "bad.png")},
        ).status_code
        == 400
    )
    assert (
        client.post(
            base,
            data={"action": "photo", "photo": (io.BytesIO(image_bytes()), "photo.jpg")},
        ).status_code
        == 302
    )
    with app.app_context():
        photo = PassportStampPhoto.query.one()
        first, photo_id = photo_path(photo.filename), photo.id
        assert not photo.public_consent
        with Image.open(first) as image:
            assert not image.getexif() and image.format == "JPEG"
    response = client.get(f"/passports/{pid}/photos/{photo_id}")
    assert (
        response.status_code == 200 and "no-store" in response.headers["Cache-Control"]
    )
    assert client.get(base).status_code == 200
    assert (
        client.post(
            base,
            data={"action": "photo", "photo": (io.BytesIO(image_bytes()), "new.png")},
        ).status_code
        == 302
    )
    assert not first.exists()
    with app.app_context():
        second = photo_path(PassportStampPhoto.query.one().filename)
    # A DB failure must remove the newly-written file and retain the old photo.
    with (
        patch(
            "passports.routes.evidence.replace_photo",
            side_effect=RuntimeError("db failure"),
        ),
        pytest.raises(RuntimeError),
    ):
        client.post(
            base,
            data={
                "action": "photo",
                "photo": (io.BytesIO(image_bytes()), "new.jpg"),
            },
        )
    assert second.exists() and len(list(second.parent.iterdir())) == 1
    signin(client, owner["other"])
    assert client.get(f"/passports/{pid}/photos/{photo_id}").status_code == 404
    with app.app_context():
        db.session.add(
            PassportParticipant(
                passport_id=pid,
                user_id=owner["other"],
                display_name="Friend",
                accepted_at=datetime.now(UTC),
            )
        )
        db.session.commit()
    assert client.get(f"/passports/{pid}/photos/{photo_id}").status_code == 404
    signin(client, owner["user"])
    assert client.post(base, data={"action": "delete_photo"}).status_code == 302
    assert not second.exists()
    with app.app_context():
        PassportParticipant.query.filter_by(
            user_id=owner["user"]
        ).one().is_active = False
        db.session.commit()
    assert client.get(f"/passports/{pid}/photos/{photo_id}").status_code == 404


def test_photo_bounds(app: Flask) -> None:
    with app.app_context():
        with pytest.raises(BuilderError):
            save_photo(
                FileStorage(
                    stream=io.BytesIO(b"x" * (MAX_PHOTO_BYTES + 1)),
                    filename="large.jpg",
                )
            )
        stream = io.BytesIO()
        Image.new("RGB", (2, 2)).save(stream, format="GIF")
        with pytest.raises(BuilderError):
            save_photo(
                FileStorage(
                    stream=io.BytesIO(stream.getvalue()), filename="animation.gif"
                )
            )
        with (
            patch("passports.evidence.MAX_PHOTO_PIXELS", 1),
            pytest.raises(BuilderError),
        ):
            save_photo(
                FileStorage(stream=io.BytesIO(image_bytes()), filename="large.jpg")
            )


def test_manual_supporting_flight_checks_and_dedup(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    signin(client, owner["user"])
    url = f"/passports/{owner['passport']}/airfields/{owner['target']}"
    with app.app_context():
        own = flight(owner["user"])
        other = flight(owner["other"])
        db.session.commit()
        own_id, other_id = own.id, other.id
    for fid in ("bad", str(10**90), "999999", str(other_id)):
        response = client.post(
            url,
            data={
                "action": "visit",
                "visit_date": "2025-01-01",
                "supporting_flight": fid,
            },
        )
        assert response.status_code == 400 and "own logbook" in response.text
    assert (
        client.post(
            url,
            data={
                "action": "visit",
                "visit_date": "2025-01-02",
                "supporting_flight": own_id,
            },
        ).status_code
        == 400
    )
    assert (
        client.post(
            url,
            data={
                "action": "visit",
                "visit_date": "2025-01-01",
                "supporting_flight": own_id,
            },
            follow_redirects=True,
        ).status_code
        == 200
    )
    with app.app_context():
        p = db.session.get(Passport, owner["passport"])
        assert passport_progress(p, owner["user"])["rows"][0]["count"] == 1
        db.session.get(Flight, own_id).pic_user_id = owner["other"]
        db.session.commit()
        row = passport_progress(p, owner["user"])["rows"][0]
        assert row["count"] == 1 and row["first"]["kind"] == "manual"
        assert not row["first"]["can_open"]
    assert "View flight" not in client.get(url).text
