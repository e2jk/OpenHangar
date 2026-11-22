"""Private place edits preserve ownership, visitor access and photo lifetimes."""

import io
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask
from models import Place, PlaceTag, PlaceVisit, db
from PIL import Image
from places.access import PlaceError
from places.collections import create_collection, set_member
from places.management import (
    archive_place,
    copy_place,
    move_place,
    photo_path,
    place_values,
    purge_expired,
    purge_place,
    restore_place,
    save_place,
    tag_names,
    trash_place,
)
from places.tags import delete_tag, rename_tag
from werkzeug.datastructures import FileStorage

from tests.test_place_collections import people

__all__ = ["people"]


def form(**values: str) -> dict[str, str]:
    return {"title": " A museum ", "latitude": "50.5", "longitude": "4.2", **values}


def upload() -> FileStorage:
    data = io.BytesIO()
    Image.new("RGB", (4, 4)).save(data, "PNG")
    data.seek(0)
    return FileStorage(data, filename="museum.png")


@pytest.fixture(autouse=True)
def uploads(app: Flask, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(app.config, "UPLOAD_FOLDER", str(tmp_path))


def test_editor_saves_plain_text_clean_photo_and_collection_tags(
    app: Flask, people: dict[str, int]
) -> None:
    p = people
    with app.app_context():
        c = create_collection(p["tenant"], p["creator"], "Days out")
        place = save_place(
            p["tenant"],
            p["creator"],
            c.id,
            form(description="<b>plain</b>", coordinates="50°30'N 4°12'E"),
            tags=[" Museum ", "Museum", "", "Town"],
            photo=upload(),
        )
        db.session.commit()
        pid, path = place.id, photo_path(place.photo_filename)
        assert path.is_file() and path.read_bytes().startswith(b"\xff\xd8")
        assert place.title == "A museum" and place.description == "<b>plain</b>"
        assert (place.latitude, place.longitude) == (50.5, 4.2)
        assert tag_names(pid) == ["Museum", "Town"]
        set_member(p["tenant"], p["creator"], c.id, p["reader"])
        db.session.flush()
        changed = save_place(
            p["tenant"],
            p["reader"],
            c.id,
            form(title="Edited"),
            place_id=pid,
            tags=["Town"],
        )
        assert changed.owner_user_id == p["creator"]
        assert changed.created_by_user_id == p["creator"]
        assert changed.updated_by_user_id == p["reader"]
        assert path.is_file()
        old_time = changed.updated_at
        changed = save_place(
            p["tenant"], p["reader"], c.id, form(), place_id=pid, remove_photo=True
        )
        assert changed.updated_at > old_time and changed.photo_filename is None
        assert path.is_file()
        db.session.commit()
        assert not path.exists()


@pytest.mark.parametrize(
    "values",
    [
        {"title": "x" * 201},
        {"description": "x" * 8001},
        {"address": "x" * 501},
        {"external_url": "x" * 2049},
        {"external_url": "javascript:alert(1)"},
        {"external_url": "https://"},
        {"external_url": "https://user:password@place.test/"},
        {"external_url": "https://place.test/\ninject"},
        {"external_url": "https://["},
    ],
)
def test_place_metadata_refuses_empty_long_or_unsafe_values(
    values: dict[str, str],
) -> None:
    with pytest.raises(PlaceError):
        place_values(form(**values))


def test_external_url_and_photo_save_failure_preserve_existing_file(
    app: Flask, people: dict[str, int]
) -> None:
    p = people
    with app.app_context():
        c = create_collection(p["tenant"], p["creator"], "Photos")
        place = save_place(
            p["tenant"],
            p["creator"],
            c.id,
            form(external_url="https://place.test/visit"),
            photo=upload(),
        )
        db.session.commit()
        pid, cid, old = place.id, c.id, photo_path(place.photo_filename)
        with pytest.raises(PlaceError):
            save_place(
                p["tenant"],
                p["creator"],
                cid,
                form(),
                place_id=pid,
                photo=upload(),
                tags=["x" * 81],
            )
        db.session.rollback()
        assert list(old.parent.iterdir()) == [old]
        assert db.session.get(Place, pid).photo_filename == old.name
        with pytest.raises(PlaceError):
            save_place(
                p["tenant"],
                p["creator"],
                cid,
                form(),
                place_id=pid,
                photo=FileStorage(io.BytesIO(b"bad"), filename="bad.png"),
            )
        db.session.rollback()
        assert old.exists()


def test_copy_owns_independent_photo_tags_and_no_visits(
    app: Flask, people: dict[str, int]
) -> None:
    p = people
    with app.app_context():
        t, u, v = p["tenant"], p["creator"], p["reader"]
        c = create_collection(t, u, "Source")
        d = create_collection(t, v, "Destination")
        set_member(t, u, c.id, v)
        db.session.flush()
        place = save_place(t, u, c.id, form(), tags=["Town"], photo=upload())
        db.session.add(
            PlaceVisit(place_id=place.id, user_id=u, visit_date=date.today())
        )
        db.session.commit()
        copy = copy_place(t, v, place.id, d.id)
        db.session.commit()
        assert copy.owner_user_id == copy.created_by_user_id == v
        assert copy.collection_id == d.id and copy.title == place.title
        assert copy.photo_filename != place.photo_filename
        assert (
            photo_path(copy.photo_filename).read_bytes()
            == photo_path(place.photo_filename).read_bytes()
        )
        assert tag_names(copy.id) == ["Town"]
        assert PlaceVisit.query.filter_by(place_id=copy.id).count() == 0
        purge_place(t, v, copy.id)
        db.session.commit()
        assert photo_path(place.photo_filename).exists()


def test_photo_copy_failure_leaves_no_unowned_file(
    app: Flask, people: dict[str, int]
) -> None:
    p = people
    with app.app_context():
        t, u = p["tenant"], p["creator"]
        c = create_collection(t, u, "Photos")
        place = save_place(t, u, c.id, form(), photo=upload())
        db.session.commit()
        pid, cid, original = place.id, c.id, photo_path(place.photo_filename)
        real_flush = db.session.flush
        count = 0

        def failing_flush(*args: Any, **kwargs: Any) -> Any:
            nonlocal count
            count += 1
            if count == 3:
                raise RuntimeError("database write failed")
            return real_flush(*args, **kwargs)

        with (
            patch.object(db.session, "flush", failing_flush),
            pytest.raises(RuntimeError),
        ):
            copy_place(t, u, pid, cid)
        db.session.rollback()
        assert list(original.parent.iterdir()) == [original]
        original.unlink()
        with pytest.raises(PlaceError, match="photo"):
            copy_place(t, u, pid, cid)
        db.session.rollback()


def test_move_requires_owner_and_every_visitor_and_remaps_tags(
    app: Flask, people: dict[str, int]
) -> None:
    p = people
    with app.app_context():
        t, u, v = p["tenant"], p["creator"], p["reader"]
        c = create_collection(t, u, "Source")
        d = create_collection(t, u, "Destination")
        set_member(t, u, c.id, v)
        set_member(t, u, d.id, v)
        db.session.flush()
        place = save_place(t, u, c.id, form(), tags=["Town"])
        db.session.add(
            PlaceVisit(
                place_id=place.id, user_id=p["outsider"], visit_date=date.today()
            )
        )
        db.session.commit()
        with pytest.raises(PlaceError, match="owner"):
            move_place(t, v, place.id, d.id)
        with pytest.raises(PlaceError, match="Every visitor"):
            move_place(t, u, place.id, d.id)
        with pytest.raises(PlaceError, match="Move"):
            save_place(t, u, d.id, form(), place_id=place.id)
        set_member(t, u, d.id, p["outsider"], can_edit=False)
        db.session.flush()
        assert move_place(t, u, place.id, d.id).collection_id == d.id
        db.session.flush()
        assert tag_names(place.id) == ["Town"]
        assert PlaceVisit.query.filter_by(place_id=place.id).count() == 1
        assert move_place(t, u, place.id, d.id).id == place.id
        db.session.add(
            PlaceVisit(place_id=place.id, user_id=None, visit_date=date.today())
        )
        db.session.flush()
        with pytest.raises(PlaceError, match="Every visitor"):
            move_place(t, u, place.id, c.id)


def test_bin_restore_purge_and_thirty_day_expiry(
    app: Flask, people: dict[str, int]
) -> None:
    p = people
    with app.app_context():
        t, u, v = p["tenant"], p["creator"], p["reader"]
        c = create_collection(t, u, "Bin")
        set_member(t, u, c.id, v)
        db.session.flush()
        place = save_place(t, u, c.id, form(), photo=upload())
        archive_place(t, v, place.id, True)
        assert place.archived
        archive_place(t, v, place.id, False)
        trash_place(t, v, place.id)
        db.session.flush()
        assert place.deleted_by_user_id == v
        with pytest.raises(PlaceError, match="permanently"):
            purge_place(t, v, place.id)
        restore_place(t, v, place.id)
        assert place.deleted_at is None and place.deleted_by_user_id is None
        now = datetime.now(UTC)
        trash_place(t, v, place.id)
        place.deleted_at = now - timedelta(days=29)
        db.session.commit()
        filename = place.photo_filename
        assert purge_expired(now=now) == 0
        assert purge_expired(now=now + timedelta(days=1)) == 1
        db.session.commit()
        assert not photo_path(filename).exists()
        assert Place.query.count() == 0


def test_collection_tag_rename_merge_delete_and_editor_boundary(
    app: Flask, people: dict[str, int]
) -> None:
    p = people
    with app.app_context():
        t, u, v = p["tenant"], p["creator"], p["reader"]
        c = create_collection(t, u, "Tags")
        place = save_place(t, u, c.id, form(), tags=["Museum", "Town"])
        another = save_place(t, u, c.id, form(title="Other"), tags=["Museum"])
        db.session.flush()
        museum = PlaceTag.query.filter_by(name="Museum").one()
        rename_tag(t, u, c.id, museum.id, "Museum")
        for name in (" ", "x" * 81):
            with pytest.raises(PlaceError):
                rename_tag(t, u, c.id, museum.id, name)
        rename_tag(t, u, c.id, museum.id, "Attraction")
        db.session.flush()
        assert tag_names(another.id) == ["Attraction"]
        rename_tag(t, u, c.id, museum.id, "Town")
        db.session.flush()
        assert tag_names(place.id) == tag_names(another.id) == ["Town"]
        town = PlaceTag.query.filter_by(name="Town").one()
        set_member(t, u, c.id, v, can_edit=False)
        db.session.flush()
        with pytest.raises(PlaceError):
            delete_tag(t, v, c.id, town.id)
        delete_tag(t, u, c.id, town.id)
        db.session.flush()
        assert tag_names(place.id) == []
        for operation in (delete_tag,):
            with pytest.raises(PlaceError):
                operation(t, u, c.id, 99999)
        with pytest.raises(PlaceError):
            rename_tag(t, u, c.id, 99999, "Missing")


def test_unlocated_place_can_be_copied_and_enriched_by_another_editor(
    app: Flask, people: dict[str, int]
) -> None:
    from places.presentation import place_label

    p = people
    with app.app_context():
        t, u, v = p["tenant"], p["creator"], p["reader"]
        source = create_collection(t, u, "Shared ideas")
        target = create_collection(t, v, "My ideas")
        set_member(t, u, source.id, v)
        place = save_place(
            t, u, source.id, {"external_url": "https://example.org/idea"}
        )
        db.session.commit()
        assert place_label(place) == "example.org"
        copy = copy_place(t, v, place.id, target.id)
        db.session.commit()
        assert copy.title is None and copy.latitude is None and copy.longitude is None
        assert copy.external_url == place.external_url
        changed = save_place(t, v, source.id, form(title="Found it"), place_id=place.id)
        assert place_label(changed) == "Found it"
        assert changed.latitude == 50.5 and changed.updated_by_user_id == v
        blank = save_place(t, u, source.id, {})
        assert place_label(blank) == "Place to locate"
        # A legacy row may have a non-http URL; the label must still be useful.
        blank.external_url = "not-a-host"
        assert place_label(blank) == "Place to locate"
