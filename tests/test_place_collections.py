"""Collection rights, tenant boundaries and retained destinations on departure."""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask
from models import (
    Place,
    PlaceCollection,
    PlaceCollectionMember,
    PlacePreference,
    PlaceTag,
    PlaceTagAssignment,
    PlaceVisit,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)
from places.access import (
    PlaceError,
    actor,
    collection_access,
    collections_for_user,
    place_access,
)
from places.collections import (
    create_collection,
    default_collection,
    delete_collection,
    personal_collection,
    remove_member,
    rename,
    set_default,
    set_member,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query

from tests.auth_helpers import set_membership_flags


@pytest.fixture
def people(app: Flask) -> dict[str, int]:
    with app.app_context():
        tenant = Tenant(name="Places")
        elsewhere = Tenant(name="Elsewhere")
        users = [
            User(email=f"{name}@places.test", password_hash="unused", name=name)
            for name in ("creator", "reader", "outsider", "admin", "personal")
        ]
        db.session.add_all([tenant, elsewhere, *users])
        db.session.flush()
        result = {user.name: user.id for user in users}
        result.update(tenant=tenant.id, elsewhere=elsewhere.id)
        users[3].is_instance_admin = True
        for user in users:
            db.session.add(
                TenantUser(
                    user_id=user.id,
                    tenant_id=tenant.id,
                    role=None if user.name == "personal" else Role.VIEWER,
                    access_scope="personal" if user.name == "personal" else "full",
                )
            )
        db.session.add(
            TenantUser(user_id=users[0].id, tenant_id=elsewhere.id, role=Role.VIEWER)
        )
        db.session.commit()
        return result


def destination(collection_id: int, owner_id: int, **values: Any) -> Place:
    place = Place(
        collection_id=collection_id,
        owner_user_id=owner_id,
        created_by_user_id=owner_id,
        updated_by_user_id=owner_id,
        title="Museum",
        latitude=50.1,
        longitude=4.2,
        **values,
    )
    db.session.add(place)
    db.session.flush()
    return place


def test_personal_defaults_are_tenant_scoped_and_survive_missing_choices(
    app: Flask, people: dict[str, int]
) -> None:
    p = people
    with app.app_context():
        t, u = p["tenant"], p["creator"]
        mine = personal_collection(t, u)
        assert personal_collection(t, u).id == mine.id
        assert default_collection(t, u).id == mine.id
        elsewhere = personal_collection(p["elsewhere"], u)
        assert elsewhere.id != mine.id
        shared = create_collection(t, p["reader"], "  Fly-outs  ")
        assert shared.name == "Fly-outs"
        set_member(t, p["reader"], shared.id, u)
        db.session.flush()
        set_default(t, u, shared.id)
        db.session.flush()
        assert default_collection(t, u).id == shared.id
        set_default(t, u, shared.id)
        member = db.session.get(PlaceCollectionMember, (shared.id, u))
        member.can_edit = False
        db.session.flush()
        assert default_collection(t, u).id == mine.id
        db.session.delete(member)
        db.session.flush()
        assert default_collection(t, u).id == mine.id
        preference = db.session.get(PlacePreference, (u, t))
        preference.default_collection_id = None
        db.session.flush()
        assert default_collection(t, u).id == mine.id
        assert collections_for_user(p["elsewhere"], u).one().id == elsewhere.id
        # Personal accounts and aviation VIEWERs can own Places without fleet rights.
        assert personal_collection(t, p["personal"]).created_by_user_id == p["personal"]


def test_concurrent_first_use_recovers_the_winning_personal_collection(
    app: Flask, people: dict[str, int]
) -> None:
    p = people
    with app.app_context():
        mine = personal_collection(p["tenant"], p["creator"])
        original = Query.first
        missed = False

        def stale_first(query: Query) -> Any:
            nonlocal missed
            if query.column_descriptions[0]["entity"] is PlaceCollection and not missed:
                missed = True
                return None
            return original(query)

        # A racing request already inserted My places. The losing savepoint must
        # not roll back an unrelated change made by this request.
        extra = create_collection(p["tenant"], p["creator"], "Keep this")
        with patch.object(Query, "first", stale_first):
            assert personal_collection(p["tenant"], p["creator"]).id == mine.id
        assert db.session.get(PlaceCollection, extra.id).name == "Keep this"


def test_collection_permissions_never_follow_role_or_admin_visibility(
    app: Flask, people: dict[str, int]
) -> None:
    p = people
    with app.app_context():
        t, u = p["tenant"], p["creator"]
        c = create_collection(t, u, "Private")
        place = destination(c.id, u)
        for uid in (p["outsider"], p["admin"]):
            assert collections_for_user(t, uid).count() == 0
            with pytest.raises(PlaceError):
                collection_access(t, uid, c.id)
        assert collection_access(t, p["admin"], c.id, managing=True).id == c.id
        with pytest.raises(PlaceError):
            collection_access(p["elsewhere"], u, c.id, managing=True)
        with pytest.raises(PlaceError):
            collection_access(t, u, 99999)
        with pytest.raises(PlaceError):
            collection_access(t, p["reader"], c.id, managing=True)
        set_member(t, u, c.id, p["reader"], can_edit=False)
        db.session.flush()
        assert place_access(t, p["reader"], place.id).id == place.id
        with pytest.raises(PlaceError):
            place_access(t, p["reader"], place.id, editing=True)
        set_member(t, u, c.id, p["reader"])
        db.session.flush()
        assert place_access(t, p["reader"], place.id, editing=True).id == place.id
        with pytest.raises(PlaceError):
            set_member(t, u, c.id, u, can_edit=False)
        with pytest.raises(PlaceError):
            remove_member(t, u, c.id, u)
        with pytest.raises(PlaceError):
            place_access(t, u, 99999)
        place.deleted_at = datetime.now(UTC)
        with pytest.raises(PlaceError):
            place_access(t, u, place.id)
        assert place_access(t, u, place.id, include_deleted=True).id == place.id
        rename(t, u, c.id, "New name")
        assert c.name == "New name"
        for value in (" ", "x" * 129):
            with pytest.raises(PlaceError):
                rename(t, u, c.id, value)
        user = db.session.get(User, u)
        set_membership_flags(u, view_only=True)
        assert actor(t, u).id == u
        with pytest.raises(PlaceError):
            actor(t, u, writing=True)
        set_membership_flags(u, view_only=False)
        user.is_active = False
        with pytest.raises(PlaceError):
            actor(t, u)
        with pytest.raises(PlaceError):
            actor(t, 99999)
        with pytest.raises(PlaceError):
            actor(p["elsewhere"], p["reader"])


@pytest.mark.parametrize(
    "departure", ["leave", "remove", "membership", "account", "raw_member"]
)
def test_departure_retains_places_and_visits(
    app: Flask, people: dict[str, int], departure: str
) -> None:
    p = people
    with app.app_context():
        t, u, v = p["tenant"], p["creator"], p["reader"]
        c = create_collection(t, u, "Shared")
        set_member(t, u, c.id, v)
        db.session.flush()
        place = destination(c.id, v)
        visit = PlaceVisit(place_id=place.id, user_id=v, visit_date=date(2026, 10, 1))
        db.session.add(visit)
        set_default(t, v, c.id)
        db.session.commit()
        cid, pid, vid = c.id, place.id, visit.id
        last_edit = place.updated_at
        if departure == "leave":
            remove_member(t, v, cid, v)
        elif departure == "remove":
            remove_member(t, u, cid, v)
            remove_member(t, u, cid, p["outsider"])
        elif departure == "membership":
            db.session.delete(db.session.get(TenantUser, (v, t)))
        elif departure == "raw_member":
            db.session.delete(db.session.get(PlaceCollectionMember, (cid, v)))
        else:
            db.session.delete(db.session.get(User, v))
        db.session.commit()
        db.session.expire_all()
        assert db.session.get(Place, pid).owner_user_id == u
        assert db.session.get(Place, pid).updated_at == last_edit
        assert db.session.get(PlaceVisit, vid).user_id == (
            None if departure == "account" else v
        )
        assert db.session.get(PlaceCollectionMember, (cid, v)) is None
        assert db.session.get(PlaceCollection, cid) is not None
        if departure in {"leave", "remove"}:
            assert db.session.get(PlacePreference, (v, t)).default_collection_id is None


def test_creator_account_removal_retains_content_without_stale_ownership(
    app: Flask, people: dict[str, int]
) -> None:
    p = people
    with app.app_context():
        c = create_collection(p["tenant"], p["creator"], "Retained")
        set_member(p["tenant"], p["creator"], c.id, p["reader"])
        place = destination(c.id, p["creator"])
        db.session.commit()
        cid, pid = c.id, place.id
        db.session.delete(db.session.get(User, p["creator"]))
        db.session.commit()
        db.session.expire_all()
        assert db.session.get(PlaceCollection, cid).created_by_user_id is None
        assert db.session.get(Place, pid).owner_user_id is None
        assert collection_access(p["tenant"], p["reader"], cid).id == cid
        assert collection_access(p["tenant"], p["admin"], cid, managing=True).id == cid


def test_collection_delete_cascades_and_photo_cleanup_obeys_transaction(
    app: Flask, people: dict[str, int], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = people
    monkeypatch.setitem(app.config, "UPLOAD_FOLDER", str(tmp_path))
    with app.app_context():
        c = create_collection(p["tenant"], p["creator"], "Delete")
        name = "a" * 32 + ".jpg"
        photo = tmp_path / "place_photos" / name
        photo.parent.mkdir()
        photo.write_bytes(b"stored")
        place = destination(c.id, p["creator"], photo_filename=name)
        tag = PlaceTag(collection_id=c.id, name="Museum")
        db.session.add(tag)
        db.session.flush()
        db.session.add_all(
            [
                PlaceTagAssignment(
                    collection_id=c.id, place_id=place.id, tag_id=tag.id
                ),
                PlaceVisit(
                    place_id=place.id, user_id=p["creator"], visit_date=date.today()
                ),
            ]
        )
        set_default(p["tenant"], p["creator"], c.id)
        db.session.commit()
        cid = c.id
        delete_collection(p["tenant"], p["creator"], cid)
        db.session.flush()
        assert photo.exists()
        db.session.rollback()
        assert photo.exists()
        delete_collection(p["tenant"], p["creator"], cid)
        db.session.commit()
        assert not photo.exists()
        assert (
            Place.query.count()
            == PlaceTag.query.count()
            == PlaceVisit.query.count()
            == 0
        )
        db.session.expire_all()
        assert (
            db.session.get(
                PlacePreference, (p["creator"], p["tenant"])
            ).default_collection_id
            is None
        )


@pytest.mark.parametrize(
    "values",
    [
        {"latitude": float("nan")},
        {"longitude": float("inf")},
        {"latitude": 91},
        {"longitude": -181},
        {"latitude": None},
        {"latitude": "50"},
        {"photo_filename": "../outside.jpg"},
    ],
)
def test_invalid_place_coordinates_and_photo_names_fail_closed(
    app: Flask, people: dict[str, int], values: dict[str, Any]
) -> None:
    with app.app_context():
        c = create_collection(people["tenant"], people["creator"], "Validate")
        place = destination(c.id, people["creator"])
        for key, value in values.items():
            setattr(place, key, value)
        with pytest.raises(ValueError):
            db.session.flush()
        db.session.rollback()


def test_default_collection_cannot_cross_tenants_even_for_a_member_of_both(
    app: Flask, people: dict[str, int]
) -> None:
    p = people
    with app.app_context():
        foreign = create_collection(p["elsewhere"], p["creator"], "Other tenant")
        db.session.add(
            PlacePreference(
                user_id=p["creator"],
                tenant_id=p["tenant"],
                default_collection_id=foreign.id,
            )
        )
        with pytest.raises(ValueError, match="another tenant"):
            db.session.flush()
        db.session.rollback()
        db.session.add(
            PlacePreference(
                user_id=p["creator"], tenant_id=p["tenant"], default_collection_id=99999
            )
        )
        with pytest.raises(ValueError, match="another tenant"):
            db.session.flush()
        db.session.rollback()


def test_tags_and_members_cannot_reference_another_collection_or_tenant(
    app: Flask, people: dict[str, int]
) -> None:
    p = people
    with app.app_context():
        c = create_collection(p["tenant"], p["creator"], "One")
        d = create_collection(p["elsewhere"], p["creator"], "Two")
        place = destination(c.id, p["creator"])
        tag = PlaceTag(collection_id=d.id, name="Foreign tag")
        db.session.add(tag)
        db.session.commit()
        db.session.add(
            PlaceTagAssignment(collection_id=c.id, place_id=place.id, tag_id=tag.id)
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()
        db.session.add(
            PlaceCollectionMember(
                collection_id=c.id, tenant_id=p["elsewhere"], user_id=p["creator"]
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()
