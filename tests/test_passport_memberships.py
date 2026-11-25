"""Edition withdrawal, group revocation and owner reinstatement without data loss."""

from datetime import date
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import (
    Passport,
    PassportGroupBlock,
    PassportInvitation,
    PassportParticipant,
    PassportSeries,
    PassportSeriesMember,
    PassportVisit,
    PassportVoucherAllocation,
    PassportVoucherRedemption,
    TenantUser,
    User,
    db,
)
from passports import editions, groups, invitations, memberships, vouchers
from passports.management import BuilderError

from tests.auth_helpers import set_membership_flags
from tests.test_passport_builder import signin
from tests.test_passport_editions import extra
from tests.test_passport_groups import consent_values, group
from tests.test_passport_invitations import invited, raw_token
from tests.test_passport_management import owner

__all__ = ["group", "invited", "owner"]


@pytest.fixture
def members(app: Flask, invited: dict[str, int]) -> dict[str, int]:
    with app.app_context():
        p = db.session.get(Passport, invited["passport"])
        link = db.session.get(PassportInvitation, p.id)
        invitations.join(
            invited["other"], link.token_hash, p.id, consent_values("Pilot friend")
        )
        basic = User(email="basic-member@example.test", password_hash="unused")
        db.session.add(basic)
        db.session.flush()
        invitations.join(
            basic.id, link.token_hash, p.id, consent_values("Basic friend")
        )
        participant = PassportParticipant.query.filter_by(
            passport_id=p.id, user_id=invited["other"]
        ).one()
        db.session.add(
            PassportVisit(
                passport_id=p.id,
                participant_id=participant.id,
                target_id=invited["target"],
                author_user_id=invited["other"],
                visit_date=date(2026, 1, 1),
                note="PRIVATE OWN NOTE",
            )
        )
        allocation = PassportVoucherAllocation.query.filter_by(
            passport_id=p.id, personal_user_id=invited["other"]
        ).one()
        vouchers.redeem(
            p,
            invited["other"],
            allocation.id,
            {
                "redemption_date": "2026-01-01",
                "quantity": "1",
                "request_key": "a" * 32,
                "note": "PRIVATE VOUCHER",
            },
        )
        db.session.commit()
        return invited | {"basic": basic.id}


def test_edition_leave_retains_group_history_and_private_records(
    app: Flask, client: FlaskClient, members: dict[str, int]
) -> None:
    signin(client, members["other"])
    path = f"/passports/series/{members['series']}/members"
    page = client.get(path)
    assert page.status_code == 200 and b"Basic friend" in page.data
    assert b"@example" not in page.data and b"PRIVATE" not in page.data
    result = client.post(
        path, data={"action": "leave-edition", "passport_id": str(members["passport"])}
    )
    assert result.status_code == 302
    assert client.get(f"/passports/{members['passport']}/").status_code == 404
    assert client.get(path).status_code == 200
    index = client.get("/passports/")
    assert b"Groups and edition history" in index.data
    with app.app_context():
        member = db.session.get(
            PassportSeriesMember, (members["series"], members["other"])
        )
        assert member.is_active and not member.blocked
        assert (
            PassportVisit.query.filter_by(author_user_id=members["other"]).one().note
            == "PRIVATE OWN NOTE"
        )
        assert PassportVoucherRedemption.query.one().note == "PRIVATE VOUCHER"
        p = db.session.get(Passport, members["passport"])
        invitations.join(
            members["other"],
            invitations.lookup(raw_token(p))[0].token_hash,
            p.id,
            consent_values("Back"),
        )
        db.session.commit()
        assert vouchers.ledger(p, members["other"])["redemptions"]


def test_group_leave_revokes_history_but_can_accept_again(
    app: Flask, client: FlaskClient, members: dict[str, int]
) -> None:
    signin(client, members["basic"])
    path = f"/passports/series/{members['series']}/members"
    assert client.post(path, data={"action": "leave-group"}).status_code == 302
    assert client.get(path).status_code == 404
    assert (
        client.get(
            f"/passports/series/{members['series']}/?history=1&edition=all"
        ).status_code
        == 404
    )
    with app.app_context():
        p = db.session.get(Passport, members["passport"])
        assert not db.session.get(
            PassportSeriesMember, (p.series_id, members["basic"])
        ).is_active
        assert not PassportGroupBlock.query.count()
        invitations.join(
            members["basic"],
            invitations.lookup(raw_token(p))[0].token_hash,
            p.id,
            consent_values("Back basic"),
        )
        db.session.commit()
        assert (
            db.session.get(TenantUser, (members["basic"], members["tenant"])).role
            is None
        )


def test_owner_removal_cannot_be_bypassed_and_reinstatement_requires_fresh_acceptance(
    app: Flask, client: FlaskClient, members: dict[str, int]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, members["passport"])
        memberships.remove(p.tenant_id, members["user"], p.series_id, members["other"])
        db.session.commit()
        with pytest.raises(BuilderError):
            invitations.join(
                members["other"],
                invitations.lookup(raw_token(p))[0].token_hash,
                p.id,
                consent_values(),
            )
        block = PassportGroupBlock.query.one()
        memberships.reinstate(p.tenant_id, members["user"], p.series_id, block.id)
        db.session.commit()
        assert not db.session.get(
            PassportSeriesMember, (p.series_id, members["other"])
        ).is_active
        pp = PassportParticipant.query.filter_by(
            passport_id=p.id, user_id=members["other"]
        ).one()
        assert not pp.is_active and not pp.blocked and not pp.share_visits
        invitations.join(
            members["other"],
            invitations.lookup(raw_token(p))[0].token_hash,
            p.id,
            consent_values("Explicit return"),
        )
        db.session.commit()
        assert vouchers.ledger(p, members["other"])["redemptions"]
        assert (
            PassportVisit.query.filter_by(author_user_id=members["other"]).count() == 1
        )


def test_edition_ban_survives_group_reinstatement(
    app: Flask, members: dict[str, int]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, members["passport"])
        memberships.remove(
            p.tenant_id,
            members["user"],
            p.series_id,
            members["other"],
            passport_id=p.id,
        )
        db.session.commit()
        memberships.remove(p.tenant_id, members["user"], p.series_id, members["other"])
        db.session.commit()
        memberships.remove(
            p.tenant_id, members["user"], p.series_id, members["other"]
        )  # no duplicate removal row
        db.session.commit()
        global_block = PassportGroupBlock.query.filter_by(passport_id=None).one()
        memberships.reinstate(
            p.tenant_id, members["user"], p.series_id, global_block.id
        )
        db.session.commit()
        assert (
            PassportParticipant.query.filter_by(
                passport_id=p.id, user_id=members["other"]
            )
            .one()
            .blocked
        )
        memberships.reinstate(
            p.tenant_id, members["user"], p.series_id, PassportGroupBlock.query.one().id
        )
        db.session.commit()
        assert (
            not PassportParticipant.query.filter_by(
                passport_id=p.id, user_id=members["other"]
            )
            .one()
            .blocked
        )
        assert not PassportGroupBlock.query.count()


def test_owner_and_foreign_scope_checks(
    app: Flask, client: FlaskClient, members: dict[str, int]
) -> None:
    path = f"/passports/series/{members['series']}/members"
    signin(client, members["other"])
    assert (
        client.post(
            path, data={"action": "remove", "user_id": str(members["basic"])}
        ).status_code
        == 403
    )
    signin(client, members["user"])
    for data in [
        {"action": "leave-group"},
        {"action": "remove", "user_id": str(members["user"])},
        {"action": "remove", "user_id": "99999"},
        {"action": "remove", "user_id": str(members["other"]), "passport_id": "99999"},
        {"action": "reinstate", "block_id": "99999"},
    ]:
        assert client.post(path, data=data).status_code in {404, 409}
    for data in [
        {"action": "unknown"},
        {"action": "remove"},
        {"action": "reinstate"},
        {"action": "leave-edition"},
        {"action": "remove", "passport_id": "9" * 100},
    ]:
        assert client.post(path, data=data).status_code == 400
    assert client.get("/passports/series/99999/members").status_code == 404
    with app.app_context():
        with pytest.raises(BuilderError):
            memberships.locked(
                members["tenant"] + 100, members["user"], members["series"], owner=True
            )
        with pytest.raises(BuilderError):
            memberships.remove(
                members["tenant"],
                members["user"],
                members["series"],
                members["basic"],
                passport_id=members["passport"] + 500,
            )


def test_skipped_middle_year_and_left_editions_are_readonly_history(
    app: Flask, group: dict[str, int]
) -> None:
    with app.app_context():
        series = db.session.get(PassportSeries, group["series"])
        middle = extra(group, "Skipped middle", date(2024, 7, 1), date(2024, 12, 31))
        editions.link(middle, group["user"], series_id=series.id)
        groups.enable(middle, group["user"], consent_values("Owner"))
        db.session.commit()
        visible, accepted = groups.visible_editions(
            series, group["other"], include_history=True
        )
        assert middle.id in {p.id for p in visible} and middle.id not in accepted
        memberships.leave(
            group["tenant"], group["other"], series.id, passport_id=group["new"]
        )
        db.session.commit()
        visible, accepted = groups.visible_editions(
            series, group["other"], include_history=True
        )
        assert not accepted and {p.id for p in visible} == {
            group["old"],
            group["new"],
            middle.id,
        }
        progress = groups.combined(series, visible, group["other"])
        assert all(item["visited"] == 0 for item in progress["progress"].values())
        assert PassportVisit.query.filter_by(author_user_id=group["user"]).count() == 1


def test_deleted_identity_stays_blocked_until_owner_clears_record(
    app: Flask, members: dict[str, int]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, members["passport"])
        old_email = db.session.get(User, members["other"]).email
        memberships.remove(p.tenant_id, members["user"], p.series_id, members["other"])
        db.session.commit()
        db.session.execute(db.delete(User).where(User.id == members["other"]))
        db.session.commit()
        block = PassportGroupBlock.query.one()
        assert block.user_id is None
        replacement = User(email=old_email, password_hash="unused")
        db.session.add(replacement)
        db.session.commit()
        token_hash = invitations.lookup(raw_token(p))[0].token_hash
        with pytest.raises(BuilderError):
            invitations.join(replacement.id, token_hash, p.id, consent_values())
        db.session.rollback()
        memberships.reinstate(p.tenant_id, members["user"], p.series_id, block.id)
        db.session.commit()
        invitations.join(
            replacement.id, token_hash, p.id, consent_values("Recreated friend")
        )
        db.session.commit()
        assert invitations.enrolled(p, replacement.id)


def test_readonly_owner_cannot_remove_but_member_can_withdraw(
    app: Flask, client: FlaskClient, members: dict[str, int]
) -> None:
    with app.app_context():
        set_membership_flags(members["user"], view_only=True)
        set_membership_flags(members["other"], view_only=True)
        db.session.commit()
    path = f"/passports/series/{members['series']}/members"
    signin(client, members["user"])
    page = client.get(path)
    assert page.status_code == 200 and b"Remove member</button>" not in page.data
    assert (
        client.post(
            path, data={"action": "remove", "user_id": str(members["basic"])}
        ).status_code
        == 403
    )
    signin(client, members["other"])
    assert client.post(path, data={"action": "leave-group"}).status_code == 302


def test_membership_races_and_lost_access_are_controlled(
    app: Flask, client: FlaskClient, members: dict[str, int]
) -> None:
    from sqlalchemy.exc import OperationalError

    class Busy(Exception):
        sqlstate = "55P03"

    busy = OperationalError("lock", {}, Busy())
    with app.app_context():
        with (
            patch("sqlalchemy.orm.Query.all", side_effect=busy),
            pytest.raises(BuilderError),
        ):
            memberships.locked(
                members["tenant"], members["user"], members["series"], owner=True
            )
        db.session.rollback()
        series = db.session.get(PassportSeries, members["series"])

        def changed(_: int) -> None:
            series.is_group = False
            db.session.flush()

        with (
            patch("passports.groups.lock_series", side_effect=changed),
            pytest.raises(BuilderError),
        ):
            memberships.locked(members["tenant"], members["user"], series.id)
        db.session.rollback()
    signin(client, members["user"])
    path = f"/passports/series/{members['series']}/members"
    roster = memberships.roster

    def missing_after_error(series: PassportSeries, uid: int) -> Any:
        view = roster(series, uid)
        if not hasattr(missing_after_error, "seen"):
            missing_after_error.seen = True
            return view
        raise BuilderError("gone", 404)

    with patch("passports.memberships.roster", side_effect=missing_after_error):
        assert client.post(path, data={"action": "bad"}).status_code == 404


def test_join_offers_require_acceptance_and_reuse_group_alias(
    app: Flask, client: FlaskClient, members: dict[str, int]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, members["passport"])
        series = db.session.get(PassportSeries, p.series_id)
        assert memberships.joinable_editions(series, members["basic"]) == []
        memberships.leave(p.tenant_id, members["basic"], series.id, passport_id=p.id)
        db.session.commit()
        offers = memberships.joinable_editions(series, members["basic"])
        assert [offer["passport"].id for offer in offers] == [p.id]
        token = offers[0]["token"]
        assert token == raw_token(p)
        assert (
            not PassportParticipant.query.filter_by(
                passport_id=p.id, user_id=members["basic"]
            )
            .one()
            .is_active
        )
    signin(client, members["basic"])
    page = client.get(f"/passports/join/{token}")
    assert page.status_code == 200
    assert b'value="Basic friend"' in page.data
    page = client.get(f"/passports/series/{members['series']}/")
    assert page.status_code == 200 and token.encode() in page.data


@pytest.mark.parametrize(
    "reason",
    ["disabled", "expired", "archived", "ended", "blocked", "readonly", "group-block"],
)
def test_join_offers_never_bypass_invitation_or_member_restrictions(
    app: Flask, members: dict[str, int], reason: str
) -> None:
    from datetime import UTC, datetime, timedelta

    with app.app_context():
        p = db.session.get(Passport, members["passport"])
        series = db.session.get(PassportSeries, p.series_id)
        memberships.leave(p.tenant_id, members["basic"], series.id, passport_id=p.id)
        link = db.session.get(PassportInvitation, p.id)
        user = db.session.get(User, members["basic"])
        if reason == "disabled":
            link.enabled = False
        elif reason == "expired":
            link.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif reason == "archived":
            p.archived_at = datetime.now(UTC)
        elif reason == "ended":
            p.end_date = date.today() - timedelta(days=1)
        elif reason == "blocked":
            memberships.remove(
                p.tenant_id, members["user"], series.id, user.id, passport_id=p.id
            )
        elif reason == "readonly":
            set_membership_flags(user.id, view_only=True)
        else:
            from passports import tokens

            db.session.add(
                PassportGroupBlock(
                    series_id=series.id,
                    user_id=user.id,
                    email_hash=tokens.email_digest(user.email),
                    display_name="Blocked",
                )
            )
        db.session.flush()
        assert memberships.joinable_editions(series, user.id) == []


def test_withdrawal_rejects_already_left_edition(
    app: Flask, members: dict[str, int]
) -> None:
    with app.app_context():
        memberships.leave(
            members["tenant"],
            members["basic"],
            members["series"],
            passport_id=members["passport"],
        )
        db.session.commit()
        with pytest.raises(BuilderError) as error:
            memberships.leave(
                members["tenant"],
                members["basic"],
                members["series"],
                passport_id=members["passport"],
            )
        assert error.value.status == 404


def test_account_disappearing_during_member_control_is_safe(
    app: Flask, members: dict[str, int]
) -> None:
    with app.app_context():
        get = db.session.get

        def missing_user(model: Any, identity: Any, **kwargs: Any) -> Any:
            if model is User and identity == members["basic"]:
                return None
            return get(model, identity, **kwargs)

        with (
            patch.object(db.session, "get", side_effect=missing_user),
            pytest.raises(BuilderError) as error,
        ):
            memberships.remove(
                members["tenant"], members["user"], members["series"], members["basic"]
            )
        assert error.value.status == 404
        db.session.rollback()
        memberships.remove(
            members["tenant"], members["user"], members["series"], members["basic"]
        )
        db.session.commit()
        block_id = PassportGroupBlock.query.one().id
        with patch.object(db.session, "get", side_effect=missing_user):
            memberships.reinstate(
                members["tenant"], members["user"], members["series"], block_id
            )
        db.session.commit()
        assert not PassportGroupBlock.query.count()
        # Lost identity never silently activates the old membership.
        assert not db.session.get(
            PassportSeriesMember, (members["series"], members["basic"])
        ).is_active


def test_group_disappears_during_failed_membership_request(
    app: Flask, client: FlaskClient, members: dict[str, int]
) -> None:
    signin(client, members["user"])
    first = memberships.roster

    def deleted_group(series: PassportSeries, uid: int) -> Any:
        view = first(series, uid)
        series.is_group = False
        db.session.commit()  # Models a concurrent committed change before revalidation.
        return view

    with patch("passports.memberships.roster", side_effect=deleted_group):
        assert (
            client.post(
                f"/passports/series/{members['series']}/members", data={"action": "bad"}
            ).status_code
            == 404
        )
