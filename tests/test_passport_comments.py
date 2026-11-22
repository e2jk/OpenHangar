"""A group's deliberate discussion is separate from private visits and flights."""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from flask import Flask
from models import (
    Passport,
    PassportAirfieldComment,
    PassportParticipant,
    PassportSeries,
    PassportSeriesMember,
    Tenant,
    TenantUser,
    User,
    db,
)
from passports import comments
from passports.eligibility import passport_progress
from passports.management import BuilderError
from sqlalchemy.exc import IntegrityError

from tests.test_passport_groups import group
from tests.test_passport_management import owner

__all__ = ["group", "owner"]


def post(
    s: dict[str, int], uid: int | None = None, **values: str
) -> PassportAirfieldComment:
    return comments.add(
        s["tenant"],
        uid or s["user"],
        s["series"],
        s["field"],
        {"body": "A useful group tip", "category": "airfield", **values},
    )


def test_no_stamp_required_and_same_thread_survives_edition_archive(
    app: Flask, group: dict[str, int]
) -> None:
    s = group
    with app.app_context():
        new = db.session.get(Passport, s["new"])
        before = passport_progress(new, s["user"])["visited"]
        old = db.session.get(Passport, s["old"])
        old.archived_at = datetime.now(UTC)
        membership = db.session.get(TenantUser, (s["other"], s["tenant"]))
        membership.role, membership.access_scope = None, "personal"
        first = post(
            s, category="nearby", experience_date=datetime.now(UTC).date().isoformat()
        )
        second = post(
            s, s["other"], body="Question before visiting", category="question"
        )
        db.session.commit()
        assert first.series_id == second.series_id == s["series"]
        assert first.author_display_name == "Original pilot" and first.edited_at is None
        assert passport_progress(new, s["user"])["visited"] == before
        assert not PassportParticipant.query.filter_by(
            passport_id=old.id, user_id=s["other"]
        ).first()
        assert (
            comments.context(
                s["tenant"], s["other"], s["series"], s["field"]
            ).airfield.id
            == s["field"]
        )
        comments.change(
            s["tenant"],
            s["other"],
            s["series"],
            s["field"],
            second.id,
            {"body": "<script>literal text</script>", "category": "nearby"},
        )
        db.session.commit()
        assert (
            second.body == "<script>literal text</script>"
            and second.edited_at is not None
        )


def test_authors_edit_owners_moderate_without_rewriting_other_people(
    app: Flask, group: dict[str, int]
) -> None:
    s = group
    with app.app_context():
        row = post(s, s["other"])
        db.session.commit()
        with pytest.raises(BuilderError) as denied:
            comments.change(
                s["tenant"],
                s["user"],
                s["series"],
                s["field"],
                row.id,
                {"body": "Owner cannot rewrite", "category": "airfield"},
            )
        assert denied.value.status == 403
        db.session.rollback()
        assert (
            db.session.get(PassportAirfieldComment, row.id).body == "A useful group tip"
        )
        comments.change(
            s["tenant"], s["user"], s["series"], s["field"], row.id, {}, delete=True
        )
        db.session.commit()
        assert PassportAirfieldComment.query.count() == 0
        with pytest.raises(BuilderError) as missing:
            comments.change(
                s["tenant"], s["user"], s["series"], s["field"], 999999, {}, delete=True
            )
        assert missing.value.status == 404
        own = post(s)
        db.session.commit()
        with pytest.raises(BuilderError):
            comments.change(
                s["tenant"],
                s["other"],
                s["series"],
                s["field"],
                own.id,
                {},
                delete=True,
            )


@pytest.mark.parametrize(
    "values",
    [
        {"body": " "},
        {"body": "x" * 8001},
        {"category": "other"},
        {"experience_date": "bad"},
        {"experience_date": "2999-01-01"},
    ],
)
def test_comment_validation(
    app: Flask, group: dict[str, int], values: dict[str, str]
) -> None:
    with app.app_context(), pytest.raises(BuilderError) as invalid:
        post(group, **values)
    assert invalid.value.status == 400


def test_leaving_revokes_access_but_keeps_the_stated_group_audience(
    app: Flask, group: dict[str, int]
) -> None:
    s = group
    with app.app_context():
        row = post(s, s["other"])
        db.session.commit()
        member = db.session.get(PassportSeriesMember, (s["series"], s["other"]))
        member.is_active = False
        db.session.commit()
        for writing in (False, True):
            with pytest.raises(BuilderError) as denied:
                comments.context(
                    s["tenant"], s["other"], s["series"], s["field"], writing=writing
                )
            assert denied.value.status == 404
        assert (
            db.session.get(PassportAirfieldComment, row.id).body == "A useful group tip"
        )
        assert (
            comments.context(s["tenant"], s["user"], s["series"], s["field"]).series.id
            == s["series"]
        )


def test_no_foreign_group_private_linked_field_or_tenant_access(
    app: Flask, group: dict[str, int]
) -> None:
    from passports.catalogue import create_custom

    s = group
    with app.app_context():
        private_field = create_custom(s["tenant"], "Private unshared field")
        for tid, sid, fid in (
            (s["tenant"] + 999, s["series"], s["field"]),
            (s["tenant"], s["series"] + 999, s["field"]),
            (s["tenant"], s["series"], private_field.id),
            (s["tenant"], s["series"], 999999),
        ):
            with pytest.raises(BuilderError) as missing:
                comments.context(tid, s["user"], sid, fid)
            assert missing.value.status == 404
        db.session.get(User, s["other"]).view_only = True
        db.session.commit()
        with pytest.raises(BuilderError) as readonly:
            post(s, s["other"])
        assert readonly.value.status == 403


def test_database_guards_scope_body_and_category(
    app: Flask, group: dict[str, int]
) -> None:
    s = group
    with app.app_context():
        tenant = Tenant(name="Other tenant")
        db.session.add(tenant)
        db.session.flush()
        wrong_tenant = tenant.id
        db.session.commit()
        for changes in (
            {"tenant_id": wrong_tenant},
            {"category": "invalid"},
            {"body": ""},
            {"body": "x" * 8001},
        ):
            values = {
                "tenant_id": s["tenant"],
                "series_id": s["series"],
                "airfield_id": s["field"],
                "author_user_id": s["user"],
                "author_display_name": "Me",
                "category": "airfield",
                "body": "Tip",
            } | changes
            db.session.add(PassportAirfieldComment(**values))
            with pytest.raises(IntegrityError):
                db.session.flush()
            db.session.rollback()


def test_integrity_race_is_a_retryable_conflict(
    app: Flask, group: dict[str, int]
) -> None:
    with app.app_context():
        with (
            patch(
                "passports.comments.db.session.flush",
                side_effect=IntegrityError("write", {}, Exception("race")),
            ),
            pytest.raises(BuilderError) as conflict,
        ):
            post(group)
        assert conflict.value.status == 409
        db.session.rollback()
        assert PassportAirfieldComment.query.count() == 0


def test_account_and_group_deletion_remove_comments(
    app: Flask, group: dict[str, int]
) -> None:
    s = group
    with app.app_context():
        post(s, s["other"])
        db.session.commit()
        db.session.delete(db.session.get(User, s["other"]))
        db.session.commit()
        assert PassportAirfieldComment.query.count() == 0
        post(s)
        db.session.commit()
        # Existing series integrity requires explicitly removing its editions
        # before deleting the parent. The discussion outlives those editions.
        for passport in Passport.query.filter_by(series_id=s["series"]).all():
            db.session.delete(passport)
        db.session.flush()
        assert PassportAirfieldComment.query.count() == 1
        db.session.delete(db.session.get(PassportSeries, s["series"]))
        db.session.commit()
        assert PassportAirfieldComment.query.count() == 0


def test_membership_and_group_are_rechecked_under_the_mutation_lock(
    app: Flask, group: dict[str, int]
) -> None:
    s = group
    with app.app_context():

        def revoke_member(_series_id: int) -> None:
            db.session.get(
                PassportSeriesMember, (s["series"], s["other"])
            ).is_active = False
            db.session.flush()

        with (
            patch("passports.comments.groups.lock_series", side_effect=revoke_member),
            pytest.raises(BuilderError) as revoked,
        ):
            post(s, s["other"])
        assert revoked.value.status == 404
        db.session.rollback()

        def remove_group(_series_id: int) -> None:
            db.session.get(PassportSeries, s["series"]).is_group = False
            db.session.flush()

        with (
            patch("passports.comments.groups.lock_series", side_effect=remove_group),
            pytest.raises(BuilderError) as removed,
        ):
            post(s)
        assert removed.value.status == 404
        db.session.rollback()
        with (
            patch(
                "passports.comments.groups.lock_series",
                side_effect=AssertionError("foreign lock"),
            ),
            pytest.raises(BuilderError) as foreign,
        ):
            comments.context(s["tenant"], s["user"], 999999, s["field"], writing=True)
        assert foreign.value.status == 404


def test_existing_discussion_outlives_edition_target_removal(
    app: Flask, group: dict[str, int]
) -> None:
    from models import PassportAirfield

    s = group
    with app.app_context():
        row = post(s, body="x" * 8000)
        db.session.commit()
        PassportAirfield.query.filter(
            PassportAirfield.passport_id.in_([s["old"], s["new"]]),
            PassportAirfield.airfield_id == s["field"],
        ).delete(synchronize_session=False)
        db.session.commit()
        assert (
            comments.context(
                s["tenant"], s["other"], s["series"], s["field"]
            ).airfield.id
            == s["field"]
        )
        assert db.session.get(PassportAirfieldComment, row.id).body == "x" * 8000
