"""Printouts contain only the viewer's live stamps and explicitly selected own photos."""

import io
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import (
    Passport,
    PassportParticipant,
    PassportStampPhoto,
    PassportVisit,
    TenantUser,
    db,
)
from passports import printing
from passports.drafts import MAX_TARGETS

from tests.test_passport_builder import signin
from tests.test_passport_groups import group
from tests.test_passport_management import owner
from tests.test_passport_stamps import image_bytes

__all__ = ["group", "owner"]


@pytest.fixture(autouse=True)
def own_upload_directory(
    app: Flask, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(app.config, "UPLOAD_FOLDER", str(tmp_path))


def upload(client: FlaskClient, s: dict[str, int]) -> int:
    response = client.post(
        f"/passports/{s['passport']}/airfields/{s['target']}",
        data={"action": "photo", "photo": (io.BytesIO(image_bytes()), "photo.jpg")},
    )
    assert response.status_code == 302
    with client.application.app_context():
        return (
            PassportStampPhoto.query.filter_by(
                passport_id=s["passport"], uploader_user_id=s["user"]
            )
            .one()
            .id
        )


def test_selected_own_photo_live_dates_no_private_evidence_and_archived_print(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    s = owner
    signin(client, s["user"])
    photo = upload(client, s)
    with app.test_request_context():
        p = db.session.get(Passport, s["passport"])
        p.start_date, p.end_date, p.edition_label = (
            date(2025, 1, 1),
            date(2025, 12, 31),
            "2025",
        )
        part = PassportParticipant.query.filter_by(
            passport_id=p.id, user_id=s["user"]
        ).one()
        db.session.add(
            PassportVisit(
                passport_id=p.id,
                participant_id=part.id,
                target_id=s["target"],
                author_user_id=s["user"],
                visit_date=date(2025, 6, 1),
                note="PRIVATE VISIT NOTE",
            )
        )
        db.session.commit()
    url = f"/passports/{s['passport']}/print"
    page = client.get(url)
    assert page.status_code == 200 and b"PRIVATE VISIT NOTE" not in page.data
    assert page.cache_control.private and page.cache_control.no_store
    assert b'hx-history="false"' in page.data
    assert 'id="passport-print-document"' in page.text
    assert 'class="passport-print-photo"' not in page.text
    assert 'name="photo"' in page.text
    selected = client.get(url, query_string={"photo": photo})
    assert 'class="passport-print-photo"' in selected.text and "1 / 1" in selected.text
    with app.test_request_context():
        view = printing.personal(
            db.session.get(Passport, s["passport"]), s["user"], [str(photo)]
        )
        assert view["targets"][0]["date"] == date(2025, 6, 1)
        assert set(view["targets"][0]) == {
            "name",
            "ident",
            "visited",
            "is_closed",
            "date",
            "photo",
        }
        assert "events" not in view and "notes" not in view
        visit = PassportVisit.query.one()
        visit.visit_date = date(2025, 7, 1)
        p = db.session.get(Passport, s["passport"])
        p.archived_at = datetime.now(UTC)
        db.session.commit()
        assert printing.personal(p, s["user"], [])["targets"][0]["date"] == date(
            2025, 7, 1
        )
    assert b"Archived" in client.get(url).data


@pytest.mark.parametrize("value", ["-1", "bad", "9" * 100])
def test_bad_photo_numbers_are_bounded(
    client: FlaskClient, owner: dict[str, int], value: str
) -> None:
    signin(client, owner["user"])
    assert (
        client.get(
            f"/passports/{owner['passport']}/print", query_string={"photo": value}
        ).status_code
        == 400
    )


def test_photo_selection_bounds_missing_files_and_no_achievable_targets(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    s = owner
    signin(client, s["user"])
    photo = upload(client, s)
    url = f"/passports/{s['passport']}/print"
    assert client.get(url, query_string={"photo": photo + 1}).status_code == 404
    assert (
        client.get(url, query_string=[("photo", "1")] * (MAX_TARGETS + 1)).status_code
        == 400
    )
    with app.app_context():
        from passports.evidence import photo_path

        photo_path(db.session.get(PassportStampPhoto, photo).filename).unlink()
    missing = client.get(url, query_string={"photo": photo})
    assert missing.status_code == 200 and b"Photo file unavailable" in missing.data
    assert 'class="passport-print-photo"' not in missing.text
    with app.app_context():
        from models import PassportAirfield, PassportAirfieldIdentity

        db.session.get(PassportAirfieldIdentity, s["field"]).is_closed = True
        db.session.commit()
    assert b"No achievable targets yet." in client.get(url).data
    with app.app_context():
        db.session.query(PassportStampPhoto).delete()
        db.session.query(PassportAirfield).delete()
        db.session.commit()
    assert b"No airfields selected yet." in client.get(url).data


def test_roleless_group_viewer_cannot_select_another_participants_photo(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    s = group
    signin(client, s["user"])
    photo = upload(client, s)
    with app.app_context():
        from models import PassportAirfield

        target = (
            PassportAirfield.query.filter_by(
                passport_id=s["new"], airfield_id=s["field"]
            )
            .one()
            .id
        )
    same_edition = {**s, "passport": s["new"], "target": target}
    other_photo = upload(client, same_edition)
    with app.app_context():
        member = db.session.get(TenantUser, (s["other"], s["tenant"]))
        member.role, member.access_scope = None, "passport"
        db.session.commit()
    signin(client, s["other"])
    own = f"/passports/{s['new']}/print"
    assert client.get(own).status_code == 200
    assert client.get(own, query_string={"photo": photo}).status_code == 404
    assert client.get(own, query_string={"photo": other_photo}).status_code == 404
    friend_photo = upload(client, {**same_edition, "user": s["other"]})
    assert client.get(own, query_string={"photo": friend_photo}).status_code == 200
    signin(client, s["user"])
    assert client.get(own, query_string={"photo": friend_photo}).status_code == 404
    signin(client, s["other"])
    assert client.get(f"/passports/{s['old']}/print").status_code == 404
    with app.app_context():
        db.session.query(PassportParticipant).filter_by(
            passport_id=s["new"], user_id=s["other"]
        ).update({"is_active": False})
        db.session.commit()
    assert client.get(own).status_code == 404


def test_owner_without_participant_can_print_targets_but_not_old_private_photos(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    s = owner
    with app.app_context():
        db.session.query(PassportParticipant).delete()
        db.session.commit()
    signin(client, s["user"])
    result = client.get(f"/passports/{s['passport']}/print")
    assert result.status_code == 200 and b"First field" in result.data
    signin(client, s["other"])
    assert client.get(f"/passports/{s['passport']}/print").status_code == 404
