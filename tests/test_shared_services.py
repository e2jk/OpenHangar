"""Shared services accept source adapters without leaking feature policy."""

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from models import Tenant, User, db
from services import account_exports, destination_search, file_cleanup, waypoints
from services.account_exports import ExportContext, ExportFile, ExportSection

from tests.test_passport_management import owner

__all__ = ["owner"]


def test_destination_and_waypoint_sources_keep_their_own_selection_policy() -> None:
    candidate = {
        "key": "place:1",
        "name": "Town",
        "ident": "",
        "latitude": 50.5,
        "longitude": 4.5,
        "visited": True,
    }
    assert (
        destination_search.resolve_origin(" home ", lambda value: candidate)
        is candidate
    )
    assert destination_search.origin_choices([candidate], "T") == []
    assert destination_search.origin_choices([candidate], "Town") == [candidate]
    rows, missing = destination_search.nearby(
        [candidate], candidate, 100, 10, "distance", None
    )
    assert rows[0]["distance_nm"] == 0 and missing == 0
    # Neither generic service decides whether a source wants visited places.
    assert waypoints.select([candidate]).points[0].name == "Town"


def test_registered_model_cleanup_is_folder_scoped_and_transactional(
    app: Flask, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(file_cleanup, "_registry", dict(file_cleanup._registry))
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    file_cleanup.register(User, "name", lambda: first)
    file_cleanup.register(Tenant, "name", lambda: second)
    with app.app_context():
        user = User(
            email="file-owner@example.test", password_hash="unused", name="same.jpg"
        )
        tenant = Tenant(name="same.jpg")
        db.session.add_all([user, tenant])
        db.session.commit()
        (first / "same.jpg").write_bytes(b"one")
        (second / "same.jpg").write_bytes(b"two")
        db.session.delete(user)
        db.session.flush()
        assert (first / "same.jpg").exists()
        db.session.commit()
        assert not (first / "same.jpg").exists()
        assert (second / "same.jpg").exists()
        db.session.connection().exec_driver_sql("BEGIN")
        savepoint = db.session.begin_nested()
        db.session.delete(tenant)
        db.session.flush()
        savepoint.commit()
        assert (second / "same.jpg").exists()
        db.session.rollback()
        assert (second / "same.jpg").exists()
        db.session.delete(tenant)
        db.session.commit()
        assert not (second / "same.jpg").exists()
        unnamed = User(email="no-file@example.test", password_hash="unused")
        db.session.add(unnamed)
        db.session.commit()
        db.session.delete(unnamed)
        db.session.commit()


def test_registered_cleanup_refuses_path_traversal(
    app: Flask, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(file_cleanup, "_registry", dict(file_cleanup._registry))
    file_cleanup.register(User, "name", lambda: tmp_path / "registered")
    outside = tmp_path / "private.jpg"
    outside.write_bytes(b"private")
    with app.app_context():
        user = User(
            email="unsafe-file@example.test",
            password_hash="unused",
            name="../private.jpg",
        )
        db.session.add(user)
        db.session.commit()
        db.session.delete(user)
        with pytest.raises(ValueError, match="Invalid stored filename"):
            db.session.flush()
        db.session.rollback()
        assert outside.read_bytes() == b"private"


def test_account_archive_combines_scoped_providers_and_legacy_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(account_exports, "_providers", {})
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")
    calls = []

    def provider(context: ExportContext) -> ExportSection:
        calls.append((context.tenant_id, context.user_id))
        meta: dict[str, Any] = {"file": "places/photos/1.jpg"}
        return ExportSection(
            {"visits": [context.user_id], "photos": [meta]},
            [ExportFile(meta["file"], photo, meta)],
            legacy_document="places-data.json",
        )

    account_exports.register("places", provider)
    account_exports.register(
        "other", lambda context: ExportSection({"user": context.user_id})
    )
    with (
        account_exports.account_archive(4, 7) as output,
        zipfile.ZipFile(output) as archive,
    ):
        manifest = json.loads(archive.read("account-data.json"))
        assert (
            manifest["format"] == "openhangar-account-data" and manifest["version"] == 1
        )
        assert manifest["sections"]["places"]["data"] == json.loads(
            archive.read("places-data.json")
        )
        assert manifest["sections"]["places"]["data"]["photos"][0]["included"]
        assert archive.read("places/photos/1.jpg") == b"photo"
        assert calls == [(4, 7)]
    with pytest.raises(ValueError, match="section name"):
        account_exports.register("../bad", provider)


@pytest.mark.parametrize(
    "name",
    ["../secret", "/absolute", "a/../b", "a\\b", "a/./b", "account-data.json", ".", ""],
)
def test_account_archive_rejects_unsafe_and_reserved_names(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(account_exports, "_providers", {})
    output = io.BytesIO()
    monkeypatch.setattr(
        account_exports.tempfile, "SpooledTemporaryFile", lambda **kwargs: output
    )
    account_exports.register(
        "places",
        lambda context: ExportSection(
            {}, [ExportFile(name, tmp_path / "not-present", {})]
        ),
    )
    with pytest.raises(ValueError, match="archive entry"):
        account_exports.account_archive(1, 2)
    assert output.closed


def test_generic_account_export_uses_passport_provider_without_request_identity(
    app: Flask, owner: dict[str, int]
) -> None:
    from passports.access import passports_for_user

    with (
        app.app_context(),
        account_exports.account_archive(owner["tenant"], owner["user"]) as output,
        zipfile.ZipFile(output) as archive,
    ):
        manifest = json.loads(archive.read("account-data.json"))
        assert (
            manifest["sections"]["passports"]["data"]["passports"][0]["id"]
            == owner["passport"]
        )
        assert passports_for_user(owner["tenant"], 999999).all() == []
