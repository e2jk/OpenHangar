"""Passport owner checks, draft bounds and preview data, independent of the UI."""

from collections.abc import Iterator
from datetime import date
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask
from models import (
    Flight,
    Passport,
    PassportAirfield,
    PassportArrivalExclusion,
    PassportDraft,
    PassportParticipant,
    PassportStampPhoto,
    PassportVisit,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)
from passports.catalogue import add_alias, create_custom
from passports.drafts import load_draft, new_draft, save_draft
from passports.management import (
    BuilderError,
    completion_preview,
    create_personal,
    fingerprint,
    metadata_values,
    owned_passport,
    protected_targets,
    require_actor,
    set_archived,
)


@pytest.fixture
def owner(app: Flask) -> Iterator[dict[str, int]]:
    with app.app_context():
        t = Tenant(name="Builder club")
        u = User(email="builder@test.example", password_hash="unused", name="Builder")
        v = User(email="other@test.example", password_hash="unused")
        db.session.add_all([t, u, v])
        db.session.flush()
        db.session.add_all(
            [
                TenantUser(tenant_id=t.id, user_id=u.id, role=Role.PILOT),
                TenantUser(tenant_id=t.id, user_id=v.id, role=Role.VIEWER),
            ]
        )
        p = create_personal(t.id, u.id, metadata_values({"title": "Local fields"}))
        f = create_custom(t.id, "First field", code="TEST")
        db.session.flush()
        target = PassportAirfield(tenant_id=t.id, passport_id=p.id, airfield_id=f.id)
        db.session.add(target)
        db.session.flush()
        db.session.commit()
        result = {
            "tenant": t.id,
            "user": u.id,
            "other": v.id,
            "passport": p.id,
            "field": f.id,
            "target": target.id,
        }
    yield result


def test_actor_and_owner_permissions(app: Flask, owner: dict[str, int]) -> None:
    with app.app_context():
        t, u = owner["tenant"], owner["user"]
        assert require_actor(t, u, creating=True).id == u
        with pytest.raises(BuilderError):
            require_actor(t, 99999)
        with pytest.raises(BuilderError):
            require_actor(t + 1, u)
        with pytest.raises(BuilderError):
            require_actor(t, owner["other"], creating=True)
        other = db.session.get(User, owner["other"])
        other.is_pilot = True
        assert require_actor(t, other.id, creating=True) == other
        user = db.session.get(User, u)
        user.view_only = True
        with pytest.raises(BuilderError):
            owned_passport(t, u, owner["passport"])
        user.view_only = False
        user.is_active = False
        with pytest.raises(BuilderError):
            require_actor(t, u)
        user.is_active = True
        with pytest.raises(BuilderError):
            owned_passport(t, owner["other"], owner["passport"])
        p = owned_passport(t, u, owner["passport"])
        set_archived(p, True)
        db.session.flush()
        with pytest.raises(BuilderError):
            owned_passport(t, u, p.id)
        assert owned_passport(t, u, p.id, archived_ok=True) == p
        set_archived(p, False)
        db.session.flush()
        assert p.archived_at is None


@pytest.mark.parametrize(
    "values",
    [
        {"title": ""},
        {"title": "x" * 129},
        {"title": "x", "description": "x" * 8001},
        {"title": "x", "start_date": "bad"},
        {"title": "x", "start_date": "2027-12-31", "end_date": "2027-01-01"},
    ],
)
def test_metadata_errors(values: dict[str, str]) -> None:
    with pytest.raises(BuilderError):
        metadata_values(values)


def test_metadata_fingerprint_and_draft_scopes(
    app: Flask, owner: dict[str, int]
) -> None:
    with app.app_context():
        p = owned_passport(owner["tenant"], owner["user"], owner["passport"])
        assert (
            metadata_values({"title": " Passport ", "start_date": "2027-01-01"})[
                "title"
            ]
            == "Passport"
        )
        token, draft = new_draft(owner["tenant"], owner["user"], p)
        assert draft.payload["selected"] == [f"field:{owner['field']}"]
        assert len(token) == 32 and token != draft.token_hash
        assert load_draft(token, owner["tenant"], owner["user"], p.id, 0) == draft
        for tok, tid, uid, pid, rev in [
            ("bad", owner["tenant"], owner["user"], p.id, 0),
            (token, owner["tenant"], owner["other"], p.id, 0),
            (token, owner["tenant"] + 1, owner["user"], p.id, 0),
            (token, owner["tenant"], owner["user"], None, 0),
            (token, owner["tenant"], owner["user"], p.id, 9),
        ]:
            with pytest.raises(BuilderError):
                load_draft(tok, tid, uid, pid, rev)
        previous = fingerprint(p)
        p.description = "Changed"
        db.session.flush()
        assert previous != fingerprint(p) and fingerprint(None) == ""
        save_draft(draft, {**draft.payload, "codes": "EBNM"})
        db.session.flush()
        assert draft.revision == 1
        with patch("passports.drafts.MAX_TARGETS", 0), pytest.raises(BuilderError):
            save_draft(draft, draft.payload)
        with (
            patch("passports.drafts.MAX_PAYLOAD_BYTES", 1),
            pytest.raises(BuilderError),
        ):
            save_draft(draft, draft.payload)
        draft.expires_at = 1
        db.session.flush()
        with pytest.raises(BuilderError):
            load_draft(token, owner["tenant"], owner["user"], p.id)
        with patch("passports.drafts.MAX_DRAFTS", 2):
            for _i in range(4):
                new_draft(owner["tenant"], owner["user"])
        assert PassportDraft.query.count() == 2
        assert all(d.payload["selected"] == [] for d in PassportDraft.query.all())


def test_preview_and_removal_protection(app: Flask, owner: dict[str, int]) -> None:
    with app.app_context():
        p = db.session.get(Passport, owner["passport"])
        participant = PassportParticipant.query.filter_by(
            passport_id=p.id, user_id=owner["user"]
        ).one()
        assert (
            participant.accepted_at is not None
            and participant.display_name == "Builder"
        )
        flights = [
            Flight(
                date=date(2025, 1, 1), pic_user_id=owner["user"], arrival_icao="TEST"
            ),
            Flight(
                date=date(2025, 2, 1),
                second_crew_user_id=owner["user"],
                arrival_icao="NEW",
            ),
            Flight(
                date=date(2025, 3, 1),
                pic_user_id=owner["user"],
                arrival_icao="ZERO",
                flight_time=0,
            ),
            Flight(
                date=date(2025, 4, 1), pic_user_id=owner["user"], arrival_icao="AMB"
            ),
        ]
        db.session.add_all(flights)
        db.session.flush()
        db.session.add(
            PassportArrivalExclusion(
                participant_id=participant.id, flight_id=flights[0].id
            )
        )
        fields: list[dict[str, Any]] = [
            {"id": owner["field"], "key": "first", "is_closed": False},
            {"key": "new", "ident": "NEW", "is_closed": True},
            {"key": "zero", "ident": "ZERO"},
            {"key": "amb1", "ident": "AMB"},
            {"key": "amb2", "ident": "AMB"},
        ]
        assert completion_preview(p, owner["user"], fields, {}) == {
            "visited": 1,
            "total": 5,
            "percent": 20,
        }
        assert (
            completion_preview(
                p, owner["user"], fields, {"start_date": date(2026, 1, 1)}
            )["visited"]
            == 0
        )
        assert protected_targets(p.id, set()) == set()
        assert protected_targets(p.id, {owner["target"]}) == set()
        db.session.add(
            PassportVisit(
                passport_id=p.id,
                participant_id=participant.id,
                target_id=owner["target"],
                author_user_id=owner["user"],
                visit_date=date(2025, 1, 1),
            )
        )
        db.session.flush()
        assert completion_preview(p, owner["user"], fields, {})["visited"] == 2
        assert protected_targets(p.id, {owner["target"]}) == {owner["target"]}
        db.session.query(PassportVisit).delete()
        db.session.add(
            PassportStampPhoto(
                passport_id=p.id,
                participant_id=participant.id,
                target_id=owner["target"],
                uploader_user_id=owner["user"],
                filename="first.png",
            )
        )
        db.session.flush()
        assert protected_targets(p.id, {owner["target"]}) == {owner["target"]}
        assert (
            completion_preview(
                None,
                owner["user"],
                [{"id": owner["field"], "key": "first", "ident": "TEST"}],
                {},
            )["visited"]
            == 1
        )
        assert completion_preview(p, owner["other"], [], {}) == {
            "visited": 0,
            "total": 0,
            "percent": None,
        }
        field = create_custom(owner["tenant"], "Other alias field")
        db.session.flush()
        add_alias(field, "TEST", valid_from=date(2026, 1, 1))
        assert (
            completion_preview(
                None, owner["user"], [{"id": field.id, "key": "dated"}], {}
            )["visited"]
            == 0
        )
