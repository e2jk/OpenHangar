"""Stable airfields, schema integrity, scoped read access and file lifetimes."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import (
    Flight,
    Passport,
    PassportAirfield,
    PassportAirfieldAlias,
    PassportAirfieldIdentity,
    PassportArrivalExclusion,
    PassportParticipant,
    PassportSeries,
    PassportStampPhoto,
    PassportVisit,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)
from passports import catalogue as cat
from passports.eligibility import date_eligible
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def world(app: Flask) -> Iterator[dict[str, int]]:
    with app.app_context():
        tenant = Tenant(name="Passport club")
        owner = User(email="owner@passport.test", password_hash="unused")
        member = User(email="member@passport.test", password_hash="unused")
        stranger = User(email="stranger@passport.test", password_hash="unused")
        db.session.add_all([tenant, owner, member, stranger])
        db.session.flush()
        for user in (owner, member, stranger):
            db.session.add(
                TenantUser(tenant_id=tenant.id, user_id=user.id, role=Role.VIEWER)
            )
        series = PassportSeries(
            tenant_id=tenant.id, owner_user_id=owner.id, title="Voucher friends"
        )
        db.session.add(series)
        db.session.flush()
        passport = Passport(
            tenant_id=tenant.id,
            owner_user_id=owner.id,
            series_id=series.id,
            title="Belgium 2027",
        )
        field = cat.create_custom(
            tenant.id, "Namur", code="EBNM", latitude=50.48, longitude=4.77
        )
        db.session.add(passport)
        db.session.flush()
        target = PassportAirfield(
            tenant_id=tenant.id, passport_id=passport.id, airfield_id=field.id
        )
        participant = PassportParticipant(
            passport_id=passport.id,
            user_id=member.id,
            display_name="A friend",
            accepted_at=datetime.now(UTC),
        )
        flight = Flight(
            date=date(2027, 5, 1), pic_user_id=member.id, arrival_icao="EBNM"
        )
        db.session.add_all([target, participant, flight])
        db.session.flush()
        visit = PassportVisit(
            passport_id=passport.id,
            participant_id=participant.id,
            target_id=target.id,
            visit_date=flight.date,
            flight_id=flight.id,
            author_user_id=member.id,
        )
        photo = PassportStampPhoto(
            passport_id=passport.id,
            participant_id=participant.id,
            target_id=target.id,
            uploader_user_id=member.id,
            filename="stamp.png",
        )
        exclusion = PassportArrivalExclusion(
            participant_id=participant.id, flight_id=flight.id
        )
        db.session.add_all([visit, photo, exclusion])
        db.session.commit()
        result = {
            "tenant": tenant.id,
            "owner": owner.id,
            "member": member.id,
            "stranger": stranger.id,
            "series": series.id,
            "passport": passport.id,
            "field": field.id,
            "target": target.id,
            "participant": participant.id,
            "flight": flight.id,
            "visit": visit.id,
            "photo": photo.id,
        }
    yield result


@pytest.fixture
def catalogue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[cat.CatalogueAirfield, ...]]:
    path = tmp_path / "airports.csv"
    path.write_text(
        "id,ident,icao_code,name,latitude_deg,longitude_deg,iso_country,iso_region,type\n1,EBNM,EBNM,Namur,50.4,4.7,BE,BE-WAL,small_airport\n2,BE-123,,ULM field,,,BE,BE-VLG,small_airport\n3,OLD,OLD,Closed field,NaN,99,BE,BE-VLG,closed\n4,HEL,HEL,Heliport,1,2,FR,FR-HDF,heliport\n,,BAD,Incomplete,0,0,,,\n"
    )
    monkeypatch.setattr(cat, "CATALOGUE_PATH", path)
    cat.load_catalogue.cache_clear()
    yield cat.load_catalogue()
    cat.load_catalogue.cache_clear()


def test_catalogue_keeps_non_icao_and_missing_coordinates(
    app: Flask, catalogue: tuple[cat.CatalogueAirfield, ...]
) -> None:
    assert len(catalogue) == 4
    assert catalogue[1].icao_code is None and catalogue[1].latitude is None
    assert catalogue[2].latitude is None and catalogue[2].is_closed
    assert cat.catalogue_choices() == {
        "country": ["BE", "FR"],
        "region": ["BE-VLG", "BE-WAL", "FR-HDF"],
        "airfield_type": ["closed", "heliport", "small_airport"],
    }
    assert len(cat.find_airfields(include_closed=True)) == 4
    assert len(cat.find_airfields(country="BE")) == 2
    assert cat.find_airfields("ULM", region="BE-VLG", types={"small_airport"}) == [
        catalogue[1]
    ]
    assert cat.find_airfields("HEL", country="BE") == []
    with app.app_context():
        assert PassportAirfieldIdentity.query.count() == 0


def test_catalogue_io_failure_is_not_silently_empty(
    catalogue: tuple[cat.CatalogueAirfield, ...],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cat, "CATALOGUE_PATH", tmp_path / "missing")
    cat.load_catalogue.cache_clear()
    with pytest.raises(FileNotFoundError):
        cat.load_catalogue()


@pytest.mark.parametrize(
    "coords", [(91, 0), (0, 181), (float("inf"), 0), (None, 4), (0, None)]
)
def test_bad_coordinates_rejected(coords: tuple[float | None, float | None]) -> None:
    with pytest.raises((TypeError, ValueError)):
        cat._coordinates(*coords)


def test_materialise_refresh_preserves_codes_overrides_and_identity(
    app: Flask, catalogue: tuple[cat.CatalogueAirfield, ...], world: dict[str, int]
) -> None:
    with app.app_context():
        field = cat.materialise(
            world["tenant"], replace(catalogue[1], ident="LOCAL", icao_code="CODE")
        )
        fid = field.id
        cat.correct_airfield(
            field,
            {
                "name": "My corrected name",
                "military": False,
                "latitude": 50,
                "longitude": 4,
            },
        )
        cat.materialise(
            world["tenant"],
            replace(catalogue[1], ident="NEW", name="New source name", is_closed=True),
        )
        db.session.commit()
        assert field.id == fid and field.name == "New source name" and field.is_closed
        assert cat.effective_airfield(field)["name"] == "My corrected name"
        assert cat.effective_airfield(field)["ulm"] is None
        for code in ("LOCAL", "CODE", "NEW"):
            assert cat.resolve_code(world["tenant"], code).id == fid
        assert cat.resolve_code(world["tenant"] + 1, "NEW") is None
        assert cat.resolve_code(world["tenant"], "ZZZZ") is None
        assert cat.refresh_referenced(world["tenant"]) == []
        assert field.ident == "BE-123"  # source id, not renamed code, drives refresh
        # Missing rows do not remove fields or imply closure.
        field.source_id = "missing"
        db.session.commit()
        assert cat.refresh_referenced(world["tenant"]) == []
        assert field.id == fid


def test_source_reuse_and_ambiguous_alias_need_review(
    app: Flask, world: dict[str, int], catalogue: tuple[cat.CatalogueAirfield, ...]
) -> None:
    with app.app_context():
        original = cat.materialise(
            world["tenant"], replace(catalogue[1], ident="LOCAL")
        )
        db.session.commit()
        with pytest.raises(cat.AirfieldConflict):
            cat.materialise(
                world["tenant"], replace(catalogue[1], source_id="99", ident="LOCAL")
            )
        assert PassportAirfieldIdentity.query.count() == 2
        assert cat.refresh_referenced(world["tenant"]) == []
        # An existing source identity cannot take over another field's code.
        conflicting = replace(catalogue[1], ident="EBNM")
        with pytest.raises(cat.AirfieldConflict):
            cat.materialise(world["tenant"], conflicting)
        with patch.object(cat, "load_catalogue", return_value=(conflicting,)):
            assert cat.refresh_referenced(world["tenant"]) == ["2"]
        assert original.ident == "BE-123"
        other = cat.create_custom(world["tenant"], "Different site")
        db.session.flush()
        old_alias = PassportAirfieldAlias.query.filter_by(
            airfield_id=original.id, code="LOCAL"
        ).one()
        old_alias.valid_until = date(2026, 12, 31)
        cat.add_alias(other, "LOCAL", valid_from=date(2027, 1, 1))
        db.session.flush()
        assert (
            cat.resolve_code(world["tenant"], "local", date(2026, 12, 31)).id
            == original.id
        )
        assert (
            cat.resolve_code(world["tenant"], "local", date(2027, 1, 1)).id == other.id
        )
        with pytest.raises(cat.AirfieldConflict):
            cat.resolve_code(world["tenant"], "LOCAL")


@pytest.mark.parametrize(
    "values",
    [
        {"unknown": "x"},
        {"name": ""},
        {"name": 42},
        {"name": "x" * 256},
        {"latitude": 100, "longitude": 4},
    ],
)
def test_bad_correction(
    app: Flask, world: dict[str, int], values: dict[str, Any]
) -> None:
    with app.app_context(), pytest.raises((ValueError, TypeError)):
        cat.correct_airfield(
            db.session.get(PassportAirfieldIdentity, world["field"]), values
        )


def test_custom_validation_and_reconciliation(
    app: Flask, world: dict[str, int], catalogue: tuple[cat.CatalogueAirfield, ...]
) -> None:
    with app.app_context():
        with pytest.raises(ValueError):
            cat.create_custom(world["tenant"], " ")
        with pytest.raises(cat.AirfieldConflict):
            cat.create_custom(world["tenant"], "Duplicate", code="EBNM")
        with pytest.raises(cat.AirfieldConflict):
            cat.create_custom(world["tenant"], "Too long code", code="X" * 65)
        for code, start, end in [
            ("", None, None),
            ("X" * 65, None, None),
            ("OK", date(2027, 1, 1), date(2026, 1, 1)),
        ]:
            with pytest.raises(ValueError):
                cat.add_alias(
                    db.session.get(PassportAirfieldIdentity, world["field"]),
                    code,
                    valid_from=start,
                    valid_until=end,
                )
        field = cat.create_custom(
            world["tenant"], "Custom", code="MYFIELD", latitude=51, longitude=3
        )
        fid = field.id
        cat.correct_airfield(field, {"country": "BE"})
        cat.reconcile_custom(field, catalogue[1])
        db.session.commit()
        assert field.id == fid and field.source == "ourairports"
        assert cat.effective_airfield(field)["name"] == "Custom"
        assert cat.effective_airfield(field)["latitude"] == 51
        assert cat.resolve_code(world["tenant"], "MYFIELD").id == fid
        with pytest.raises(cat.AirfieldConflict):
            cat.reconcile_custom(field, catalogue[1])
        second = cat.create_custom(world["tenant"], "Second")
        with pytest.raises(cat.AirfieldConflict):
            cat.reconcile_custom(second, catalogue[1])
        with pytest.raises(cat.AirfieldConflict):
            cat.reconcile_custom(second, catalogue[0])  # code already held by Namur
        assert second.source == "manual"


@pytest.mark.parametrize(
    "model,key",
    [
        (Passport, "passport"),
        (PassportParticipant, "participant"),
        (PassportAirfield, "target"),
        (User, "member"),
        (User, "owner"),
        (Tenant, "tenant"),
        (PassportStampPhoto, "photo"),
    ],
)
def test_cascade_removes_photo_only_after_commit(
    app: Flask, world: dict[str, int], model: type[Any], key: str, tmp_path: Path
) -> None:
    with app.app_context(), patch.dict(app.config, UPLOAD_FOLDER=str(tmp_path)):
        root = tmp_path / "passport_photos"
        root.mkdir()
        photo = root / "stamp.png"
        photo.write_bytes(b"photo")
        db.session.delete(db.session.get(model, world[key]))
        db.session.flush()
        assert photo.exists()
        db.session.commit()
        assert not photo.exists()
        assert db.session.get(PassportStampPhoto, world["photo"]) is None
        if model != Tenant:
            assert db.session.get(PassportAirfieldIdentity, world["field"]) is not None


def test_photo_rollback_replacement_and_nested_transactions(
    app: Flask, world: dict[str, int], tmp_path: Path
) -> None:
    with app.app_context(), patch.dict(app.config, UPLOAD_FOLDER=str(tmp_path)):
        root = tmp_path / "passport_photos"
        root.mkdir()
        path = root / "stamp.png"
        path.write_bytes(b"photo")
        obj = db.session.get(PassportStampPhoto, world["photo"])
        db.session.delete(obj)
        db.session.flush()
        db.session.rollback()
        assert path.exists()
        obj = db.session.get(PassportStampPhoto, world["photo"])
        # A committed savepoint must not delete a file before the outer commit.
        db.session.connection().exec_driver_sql("BEGIN")
        nested = db.session.begin_nested()
        obj.filename = "replacement.png"
        db.session.flush()
        nested.commit()
        assert path.exists()
        db.session.rollback()
        assert path.exists()
        obj = db.session.get(PassportStampPhoto, world["photo"])
        obj.filename = "final.png"
        db.session.commit()
        assert not path.exists()


def test_photo_nested_rollback_and_cleanup_error(
    app: Flask, world: dict[str, int], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with app.app_context(), patch.dict(app.config, UPLOAD_FOLDER=str(tmp_path)):
        obj = db.session.get(PassportStampPhoto, world["photo"])
        nested = db.session.begin_nested()
        db.session.delete(obj)
        db.session.flush()
        nested.rollback()
        db.session.commit()
        assert db.session.get(PassportStampPhoto, world["photo"]) is not None
        db.session.delete(db.session.get(PassportStampPhoto, world["photo"]))
        with patch.object(Path, "unlink", side_effect=OSError("disk unavailable")):
            db.session.commit()
        assert "Could not delete passport photo" in caplog.text


def test_flight_deletion_preserves_manual_visit_and_independent_photo(
    app: Flask, world: dict[str, int]
) -> None:
    with app.app_context():
        db.session.delete(db.session.get(Flight, world["flight"]))
        db.session.commit()
        assert db.session.get(PassportVisit, world["visit"]).flight_id is None
        assert PassportArrivalExclusion.query.count() == 0
        assert db.session.get(PassportStampPhoto, world["photo"]) is not None
        db.session.delete(db.session.get(PassportSeries, world["series"]))
        db.session.commit()
        assert db.session.get(Passport, world["passport"]).series_id is None


def test_uniqueness_dates_and_same_passport_constraints(
    app: Flask, world: dict[str, int]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, world["passport"])
        p.start_date, p.end_date = date(2027, 2, 1), date(2027, 1, 1)
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()
        other = Passport(
            tenant_id=world["tenant"], owner_user_id=world["owner"], title="Other"
        )
        db.session.add(other)
        db.session.flush()
        db.session.add(
            PassportVisit(
                passport_id=other.id,
                participant_id=world["participant"],
                target_id=world["target"],
                visit_date=date(2027, 1, 1),
                author_user_id=world["member"],
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()
        db.session.add(
            PassportParticipant(
                passport_id=world["passport"],
                user_id=world["member"],
                display_name="Duplicate",
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()
        other_tenant = Tenant(name="Other")
        db.session.add(other_tenant)
        db.session.flush()
        db.session.add(
            PassportAirfield(
                tenant_id=other_tenant.id,
                passport_id=world["passport"],
                airfield_id=world["field"],
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()


def test_photo_and_series_validation(app: Flask, world: dict[str, int]) -> None:
    with app.app_context():
        photo = db.session.get(PassportStampPhoto, world["photo"])
        photo.filename = "../outside.png"
        with pytest.raises(ValueError):
            db.session.flush()
        db.session.rollback()
        passport = db.session.get(Passport, world["passport"])
        passport.series_id = 999999
        with pytest.raises(ValueError):
            db.session.flush()
        db.session.rollback()
        tenant = Tenant(name="Foreign series owner")
        db.session.add(tenant)
        db.session.flush()
        series = PassportSeries(
            tenant_id=tenant.id, owner_user_id=world["owner"], title="Foreign series"
        )
        db.session.add(series)
        db.session.flush()
        passport = db.session.get(Passport, world["passport"])
        passport.series_id = series.id
        with pytest.raises(ValueError):
            db.session.flush()
        db.session.rollback()


def test_date_boundaries() -> None:
    p = Passport(start_date=date(2027, 1, 1), end_date=date(2027, 12, 31))
    today = date(2028, 1, 1)
    assert date_eligible(p, p.start_date, today=today)
    assert date_eligible(p, p.end_date, today=today)
    assert not date_eligible(p, date(2026, 12, 31), today=today)
    assert not date_eligible(p, date(2028, 1, 1), today=today)
    assert not date_eligible(Passport(), date(2999, 1, 1))
    assert date_eligible(Passport(), date(2000, 1, 1))


def _login(client: FlaskClient, uid: int) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = uid


def test_owner_member_stranger_read_boundaries(
    app: Flask, client: FlaskClient, world: dict[str, int]
) -> None:
    assert client.get("/passports/").status_code == 302
    assert client.get(f"/passports/{world['passport']}/").status_code == 302
    _login(client, world["stranger"])
    assert "Belgium 2027" not in client.get("/passports/").text
    assert client.get(f"/passports/{world['passport']}/").status_code == 404
    _login(client, world["owner"])
    assert "Belgium 2027" in client.get("/passports/").text
    assert "Namur" in client.get(f"/passports/{world['passport']}/").text
    _login(client, world["member"])
    response = client.get(f"/passports/{world['passport']}/")
    assert response.status_code == 200 and 'hx-history="false"' in response.text
    assert (
        "Airfield passports" in response.text
    )  # VIEWER without is_pilot has membership access
    assert client.get("/passports/9999999999999999999999999999/").status_code == 404
    with app.app_context():
        participant = db.session.get(PassportParticipant, world["participant"])
        participant.is_active = False
        db.session.commit()
    assert client.get(f"/passports/{world['passport']}/").status_code == 404
    with app.app_context():
        participant = db.session.get(PassportParticipant, world["participant"])
        participant.is_active, participant.accepted_at = True, None
        db.session.commit()
    assert client.get(f"/passports/{world['passport']}/").status_code == 404


def test_detail_empty_archived_and_cross_tenant(
    app: Flask, client: FlaskClient, world: dict[str, int]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, world["passport"])
        p.archived_at, p.description = datetime.now(UTC), "Private description"
        p.start_date, p.end_date = date(2027, 1, 1), date(2027, 12, 31)
        db.session.delete(db.session.get(PassportAirfield, world["target"]))
        db.session.commit()
    _login(client, world["member"])
    response = client.get(f"/passports/{world['passport']}/")
    assert (
        "Archived" in response.text and "No airfields selected yet." in response.text
    ), response.text[response.text.find("<main") : response.text.find("</main>")]
    with app.app_context():
        newtenant = Tenant(name="Foreign")
        db.session.add(newtenant)
        db.session.flush()
        p = db.session.get(Passport, world["passport"])
        p.series_id, p.tenant_id = None, newtenant.id
        db.session.commit()
    assert client.get(f"/passports/{world['passport']}/").status_code == 404
