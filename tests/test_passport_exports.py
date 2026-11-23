"""Portable self exports retain private evidence without owner-wide disclosure."""

import io
import json
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import (
    Passport,
    PassportAirfield,
    PassportParticipant,
    PassportStampPhoto,
    PassportVisit,
    TenantUser,
    db,
)
from passports import vouchers
from passports.evidence import photo_path
from passports.exports import account_archive

from tests.test_passport_builder import signin
from tests.test_passport_management import owner
from tests.test_passport_stamps import flight
from tests.test_passport_vouchers import booklet, use

__all__ = ["booklet", "owner"]


@pytest.fixture(autouse=True)
def isolated_photo_folder(
    app: Flask, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(app.config, "UPLOAD_FOLDER", str(tmp_path))


def unpack(response: Any) -> tuple[dict[str, Any], dict[str, bytes]]:
    assert response.status_code == 200
    assert response.mimetype == "application/zip"
    assert "no-store" in response.headers["Cache-Control"]
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    response.close()
    document = json.loads(files.pop("passport-data.json"))
    account = json.loads(files.pop("account-data.json"))
    assert account["format"] == "openhangar-account-data" and account["version"] == 1
    assert account["sections"]["passports"]["data"] == document
    return document, files


def test_export_contains_only_own_evidence_vouchers_and_photo_files(
    app: Flask, client: FlaskClient, booklet: dict[str, int]
) -> None:
    b = booklet
    with app.app_context():
        passport = db.session.get(Passport, b["passport"])
        participants = {
            row.user_id: row
            for row in PassportParticipant.query.filter_by(passport_id=passport.id)
        }
        own_flight = flight(b["user"], date=date(2025, 6, 1))
        db.session.flush()
        for uid, note in ((b["user"], "OWN NOTE"), (b["other"], "FRIEND SECRET")):
            db.session.add(
                PassportVisit(
                    passport_id=passport.id,
                    participant_id=participants[uid].id,
                    target_id=b["target"],
                    visit_date=date(2025, 6, 1),
                    author_user_id=uid,
                    note=note,
                )
            )
            filename = "own.jpg" if uid == b["user"] else "friend.jpg"
            path = photo_path(filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(note.encode())
            db.session.add(
                PassportStampPhoto(
                    passport_id=passport.id,
                    participant_id=participants[uid].id,
                    target_id=b["target"],
                    uploader_user_id=uid,
                    filename=filename,
                )
            )
            vouchers.redeem(
                passport,
                uid,
                b["personal"] if uid == b["user"] else b["shared"],
                use(note=note, saving_amount="12.34", currency="EUR"),
            )
        from models import PassportArrivalExclusion

        db.session.add(
            PassportArrivalExclusion(
                participant_id=participants[b["user"]].id, flight_id=own_flight.id
            )
        )
        db.session.commit()
    signin(client, b["user"])
    index = client.get("/passports/")
    assert b'href="/passports/export" hx-boost="false"' in index.data
    data, files = unpack(
        client.get("/passports/export", query_string={"user_id": b["other"]})
    )
    assert data["format"] == "openhangar-passport-data" and data["version"] == 1
    assert (
        len(data["participations"]) == len(data["visits"]) == len(data["photos"]) == 1
    )
    assert data["visits"][0]["note"] == "OWN NOTE"
    assert data["voucher_allocations"][0]["id"] == b["personal"]
    assert len(data["voucher_allocations"]) == len(data["voucher_events"]) == 1
    assert data["voucher_events"][0]["saving_amount"] == "12.34"
    assert data["arrival_exclusions"] and data["current_completion"]
    assert data["photos"][0]["included"] is True
    # Publication and sharing choices are part of the participant's own data.
    assert {"public_shared_at", "public_frequency_at", "public_name"} <= set(
        data["participations"][0]
    )
    assert {"public_consent", "public_shared_at", "group_shared_at"} <= set(
        data["photos"][0]
    )
    assert list(files.values()) == [b"OWN NOTE"]
    assert "FRIEND SECRET" not in json.dumps(data)
    assert "request_key" not in json.dumps(data)
    assert "classification_provenance" not in json.dumps(data)
    signin(client, b["other"])
    friend, friend_files = unpack(client.get("/passports/export"))
    assert friend["visits"][0]["note"] == "FRIEND SECRET"
    assert list(friend_files.values()) == [b"FRIEND SECRET"]


def test_scoped_export_retains_own_withdrawn_records_without_current_group_data(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    s = owner
    with app.app_context():
        membership = db.session.get(TenantUser, (s["other"], s["tenant"]))
        membership.access_scope, membership.role = "personal", None
        passport = db.session.get(Passport, s["passport"])
        participant = PassportParticipant(
            passport_id=passport.id,
            user_id=s["other"],
            display_name="Former friend",
            accepted_at=datetime.now(UTC),
            is_active=False,
        )
        db.session.add(participant)
        db.session.flush()
        db.session.add(
            PassportVisit(
                passport_id=passport.id,
                participant_id=participant.id,
                target_id=s["target"],
                visit_date=date(2025, 1, 1),
                author_user_id=s["other"],
                note="Retained private note",
            )
        )
        db.session.add(
            PassportStampPhoto(
                passport_id=passport.id,
                participant_id=participant.id,
                target_id=s["target"],
                uploader_user_id=s["other"],
                filename="missing.jpg",
            )
        )
        passport.title = "PRIVATE CURRENT TITLE"
        from passports.catalogue import create_custom

        secret = create_custom(s["tenant"], "NEW PRIVATE TARGET", code="NEWSECRET")
        db.session.add(
            PassportAirfield(
                tenant_id=s["tenant"], passport_id=passport.id, airfield_id=secret.id
            )
        )
        db.session.commit()
    signin(client, s["other"])
    data, files = unpack(client.get("/passports/export"))
    assert data["passports"] == [] and data["current_completion"] == []
    assert data["visits"][0]["note"] == "Retained private note"
    assert len(data["targets"]) == 1 and data["targets"][0] == {
        "target_id": s["target"],
        "passport_id": s["passport"],
        "airfield_id": s["field"],
    }
    assert data["photos"][0]["included"] is False and files == {}
    assert "PRIVATE CURRENT TITLE" not in json.dumps(data)
    assert "NEW PRIVATE TARGET" not in json.dumps(data)
    assert client.get(f"/passports/{s['passport']}/").status_code == 404


def test_export_button_only_when_the_account_has_data(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    from models import Place, PlaceCollection, PlaceCollectionMember
    from passports.exports import account_has_data

    s = owner
    signin(client, s["other"])
    page = client.get("/passports/")
    assert b"No passports yet." in page.data
    assert b"/passports/export" not in page.data
    with app.app_context():
        # Belonging to a collection is not data of its own: the archive would
        # only name it.
        collection = PlaceCollection(
            tenant_id=s["tenant"],
            name="My places",
            created_by_user_id=s["other"],
            is_personal=True,
        )
        db.session.add(collection)
        db.session.flush()
        db.session.add(
            PlaceCollectionMember(
                collection_id=collection.id,
                tenant_id=s["tenant"],
                user_id=s["other"],
                can_edit=True,
            )
        )
        db.session.commit()
        assert not account_has_data(s["tenant"], s["other"], [])
        collection_id = collection.id
    assert b"/passports/export" not in client.get("/passports/").data
    with app.app_context():
        db.session.add(
            Place(
                collection_id=collection_id,
                owner_user_id=s["other"],
                created_by_user_id=s["other"],
                title="Lake",
            )
        )
        db.session.commit()
        assert account_has_data(s["tenant"], s["other"], [])
    page = client.get("/passports/")
    assert b'href="/passports/export" hx-boost="false"' in page.data
    # The passport owner has a passport to export.
    signin(client, s["user"])
    page = client.get("/passports/")
    assert b'href="/passports/export" hx-boost="false"' in page.data


def test_empty_export_and_temporary_file_cleanup(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    s = owner
    signin(client, s["other"])
    data, files = unpack(client.get("/passports/export"))
    assert data["visits"] == data["participations"] == data["targets"] == []
    assert files == {}
    output = io.BytesIO()
    with (
        app.app_context(),
        patch(
            "services.account_exports.tempfile.SpooledTemporaryFile",
            return_value=output,
        ),
        patch(
            "services.account_exports.json.dumps", side_effect=OSError("disk failure")
        ),
        pytest.raises(OSError, match="disk failure"),
    ):
        account_archive(s["tenant"], s["other"], [])
    assert output.closed
    output = io.BytesIO(b"archive")
    with (
        patch("passports.exports.account_archive", return_value=output),
        patch("passports.routes.send_file", side_effect=OSError("response failure")),
        pytest.raises(OSError, match="response failure"),
    ):
        client.get("/passports/export")
    assert output.closed


def test_export_preserves_public_projection_without_private_corrections(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    from passports import catalogue

    s = owner
    with app.app_context():
        row = next(row for row in catalogue.load_catalogue() if row.ident == "EBNM")
        public_name = row.name
        identity = catalogue.materialise(s["tenant"], row)
        catalogue.correct_airfield(identity, {"name": "HIDDEN PRIVATE CORRECTION"})
        db.session.add(
            PassportAirfield(
                tenant_id=s["tenant"],
                passport_id=s["passport"],
                airfield_id=identity.id,
                public_catalogue=True,
                classification_overrides={"ulm": True},
            )
        )
        db.session.commit()
    signin(client, s["user"])
    data, _files = unpack(client.get("/passports/export"))
    field = next(field for field in data["targets"] if field.get("ident") == "EBNM")
    assert field["name"] == public_name and field["ulm"] is True
    assert "HIDDEN PRIVATE CORRECTION" not in json.dumps(data)


def test_export_is_bound_to_authenticated_tenant_even_for_same_person(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    from models import Tenant
    from passports.catalogue import create_custom

    s = owner
    with app.app_context():
        tenant = Tenant(name="Other organisation")
        db.session.add(tenant)
        db.session.flush()
        passport = Passport(
            tenant_id=tenant.id, owner_user_id=s["user"], title="OTHER TENANT TITLE"
        )
        db.session.add(passport)
        db.session.flush()
        identity = create_custom(tenant.id, "OTHER TENANT FIELD")
        target = PassportAirfield(
            tenant_id=tenant.id, passport_id=passport.id, airfield_id=identity.id
        )
        participant = PassportParticipant(
            passport_id=passport.id,
            user_id=s["user"],
            display_name="Me elsewhere",
            accepted_at=datetime.now(UTC),
        )
        db.session.add_all([target, participant])
        db.session.flush()
        db.session.add(
            PassportVisit(
                passport_id=passport.id,
                participant_id=participant.id,
                target_id=target.id,
                author_user_id=s["user"],
                visit_date=date(2025, 1, 1),
                note="OTHER TENANT NOTE",
            )
        )
        db.session.commit()
    signin(client, s["user"])
    data, _files = unpack(client.get("/passports/export"))
    assert "OTHER TENANT" not in json.dumps(data)
    assert len(data["participations"]) == 1
