"""Invitation capabilities, bounded join state and individual entitlements."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from flask import Flask, session
from models import (
    AuthChallenge,
    Passport,
    PassportGroupBlock,
    PassportInvitation,
    PassportParticipant,
    PassportSeries,
    PassportSeriesMember,
    PassportVoucherAllocation,
    Tenant,
    TenantUser,
    User,
    db,
)
from passports import groups, invitations, tokens
from passports.management import BuilderError

from tests.test_passport_groups import consent_values
from tests.test_passport_management import owner

__all__ = ["owner"]


@pytest.fixture
def invited(app: Flask, owner: dict[str, int]) -> dict[str, int]:
    with app.app_context():
        p = db.session.get(Passport, owner["passport"])
        p.default_voucher_quantity = 1
        series = groups.enable(p, owner["user"], consent_values("Owner"))
        invitations.replace(p)
        db.session.commit()
        return owner | {"series": series.id}


def raw_token(passport: Passport) -> str:
    row = db.session.get(PassportInvitation, passport.id)
    token = tokens.recover("invite", passport.id, row.key_nonce, row.token_hash)
    assert token is not None
    return token


def test_capabilities_are_copyable_scoped_and_not_stored(app: Flask) -> None:
    with app.app_context():
        nonce, token, hashed = tokens.create("invite", 7)
        assert tokens.valid_format(token)
        assert tokens.recover("invite", 7, nonce, hashed) == token
        assert tokens.recover("public", 7, nonce, hashed) is None
        assert tokens.recover("invite", 8, nonce, hashed) is None
        assert tokens.email_digest(" FRIEND@Example.test ") == tokens.email_digest(
            "friend@example.test"
        )
        assert tokens.email_digest("friend@example.test") != tokens.digest(
            "friend@example.test"
        )
        assert not any(
            tokens.valid_format(t) for t in ["", "x" * 42, "x" * 44, "!" * 43]
        )
        with patch.dict(app.config, SECRET_KEY="replacement"):
            assert tokens.recover("invite", 7, nonce, hashed) is None


def test_invitation_rotation_expiry_and_scopes(
    app: Flask, invited: dict[str, int]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, invited["passport"])
        old = raw_token(p)
        assert invitations.lookup(old)[1] == p
        assert invitations.owner_passport(p.tenant_id, p.owner_user_id, p.id) == p
        for uid, pid in [(invited["other"], p.id), (p.owner_user_id, 99999)]:
            with pytest.raises(BuilderError):
                invitations.owner_passport(p.tenant_id, uid, pid)
        row = invitations.replace(p, date.today().isoformat())
        db.session.flush()
        assert old not in repr(row.__dict__)
        with pytest.raises(BuilderError):
            invitations.lookup(old)
        token = raw_token(p)
        for value in ["", "!" * 43, "x" * 43]:
            with pytest.raises(BuilderError):
                invitations.lookup(value)
        with pytest.raises(BuilderError):
            invitations.lookup_hash(row.token_hash, passport_id=p.id + 1)
        for expiry in ["garbage", (date.today() - timedelta(days=1)).isoformat()]:
            with pytest.raises(BuilderError):
                invitations.replace(p, expiry)
        for column, value in [
            ("enabled", False),
            ("expires_at", datetime.now(UTC) - timedelta(seconds=1)),
        ]:
            previous = getattr(row, column)
            setattr(row, column, value)
            assert not invitations.available(row, p)
            with pytest.raises(BuilderError):
                invitations.lookup(token)
            setattr(row, column, previous)
        row.expires_at = datetime.now() + timedelta(
            hours=1
        )  # SQLite returns naive timestamps.
        assert invitations.available(row, p)
        for column, value in [
            ("archived_at", datetime.now(UTC)),
            ("end_date", date.today() - timedelta(days=1)),
        ]:
            previous = getattr(p, column)
            setattr(p, column, value)
            with pytest.raises(BuilderError):
                invitations.replace(p)
            assert not invitations.available(row, p)
            setattr(p, column, previous)
        tenant = db.session.get(Tenant, p.tenant_id)
        tenant.is_active = False
        with pytest.raises(BuilderError):
            invitations.lookup(token)
        tenant.is_active = True
        with patch.dict(app.config, SECRET_KEY="rotated"):
            assert not invitations.available(row, p)
        p.is_group = False
        assert not invitations.available(row, p)
        with pytest.raises(BuilderError):
            invitations.owner_passport(p.tenant_id, p.owner_user_id, p.id)


def test_join_preserves_roles_and_personal_vouchers(
    app: Flask, invited: dict[str, int]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, invited["passport"])
        row = db.session.get(PassportInvitation, p.id)
        user = User(email="new-friend@example.test", password_hash="unused")
        db.session.add(user)
        db.session.flush()
        for uid in [invited["other"], user.id]:
            previous = db.session.get(TenantUser, (uid, p.tenant_id))
            old_role = previous.role if previous else None
            assert (
                invitations.join(uid, row.token_hash, p.id, consent_values("Friend"))
                == p
            )
            db.session.flush()
            membership = db.session.get(TenantUser, (uid, p.tenant_id))
            assert membership.role == old_role
            assert membership.access_scope == ("full" if previous else "personal")
            participant = PassportParticipant.query.filter_by(
                passport_id=p.id, user_id=uid
            ).one()
            assert participant.share_visits and participant.share_history
            allocations = PassportVoucherAllocation.query.filter_by(
                passport_id=p.id, personal_user_id=uid
            ).all()
            assert len(allocations) == 1 and allocations[0].quantity == 1
            allocations[0].quantity = 0
            accepted = participant.accepted_at
            invitations.join(
                uid, row.token_hash, p.id, {}
            )  # Reopening neither resets balance nor consent.
            db.session.flush()
            assert allocations[0].quantity == 0 and participant.accepted_at == accepted
        assert PassportVoucherAllocation.query.filter_by(passport_id=p.id).count() == 2
        assert invitations.enrolled(p, user.id)
        assert not invitations.enrolled(p, 999999)
        private = Passport(
            tenant_id=p.tenant_id, owner_user_id=p.owner_user_id, title="Private"
        )
        db.session.add(private)
        db.session.flush()
        assert not invitations.enrolled(private, user.id)
        invitations.replace(p)
        db.session.flush()
        assert invitations.enrolled(p, user.id)


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"display_name": "x" * 129, "share_visits": "yes"},
        {"display_name": "Friend"},
    ],
)
def test_join_requires_explicit_consent(
    app: Flask, invited: dict[str, int], values: dict[str, str]
) -> None:
    with app.app_context():
        row = db.session.get(PassportInvitation, invited["passport"])
        with pytest.raises(BuilderError):
            invitations.join(invited["other"], row.token_hash, row.passport_id, values)
        assert (
            PassportParticipant.query.filter_by(user_id=invited["other"]).count() == 0
        )


def test_removed_and_inactive_users_cannot_join(
    app: Flask, invited: dict[str, int]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, invited["passport"])
        row = db.session.get(PassportInvitation, p.id)
        user = db.session.get(User, invited["other"])
        for column in ["is_active", "view_only"]:
            setattr(user, column, column == "view_only")
            with pytest.raises(BuilderError):
                invitations.join(user.id, row.token_hash, p.id, consent_values())
            setattr(user, column, column != "view_only")
        with pytest.raises(BuilderError):
            invitations.join(999999, row.token_hash, p.id, consent_values())
        block = PassportGroupBlock(
            series_id=p.series_id,
            user_id=user.id,
            email_hash=tokens.email_digest(user.email),
            display_name="Friend",
        )
        db.session.add(block)
        db.session.flush()
        assert invitations.blocked(p, user)
        with pytest.raises(BuilderError):
            invitations.join(user.id, row.token_hash, p.id, consent_values())
        block.user_id = None
        assert invitations.blocked(
            p, user
        )  # Verified-email fingerprint survives account deletion.
        db.session.delete(block)
        db.session.flush()
        invitations.join(user.id, row.token_hash, p.id, consent_values())
        db.session.flush()
        member = db.session.get(PassportSeriesMember, (p.series_id, user.id))
        member.blocked = True
        with pytest.raises(BuilderError):
            invitations.join(user.id, row.token_hash, p.id, consent_values())
        member.blocked = False
        participant = PassportParticipant.query.filter_by(
            passport_id=p.id, user_id=user.id
        ).one()
        participant.blocked = True
        db.session.flush()
        assert not invitations.enrolled(p, user.id)
        with pytest.raises(BuilderError):
            invitations.join(user.id, row.token_hash, p.id, consent_values())
        with pytest.raises(BuilderError):
            groups.consent(p, user.id, consent_values())
        assert (
            groups.visible_editions(
                db.session.get(PassportSeries, p.series_id), user.id
            )[1]
            == set()
        )


def test_pending_context_revalidates_and_completion_is_identity_bound(
    app: Flask, invited: dict[str, int]
) -> None:
    with app.test_request_context():
        p = db.session.get(Passport, invited["passport"])
        user = db.session.get(User, invited["other"])
        assert invitations.pending() is None and invitations.complete(user) is None
        invitations.prepare(raw_token(p), consent_values())
        challenge, current = invitations.pending()
        assert current == p and challenge.payload["passport_title"] == p.title
        cookie = session[invitations.JOIN_KEY]
        assert cookie not in repr(challenge.__dict__)
        assert PassportParticipant.query.filter_by(user_id=user.id).count() == 0
        assert invitations.complete(user) == p
        db.session.commit()
        assert invitations.JOIN_KEY not in session
        session[invitations.JOIN_KEY] = cookie
        assert invitations.complete(user) == p
        session[invitations.JOIN_KEY] = cookie
        with pytest.raises(BuilderError):
            invitations.complete(db.session.get(User, invited["user"]))
        db.session.get(PassportSeriesMember, (p.series_id, user.id)).is_active = False
        with pytest.raises(BuilderError):
            invitations.complete(user)
        db.session.rollback()
        session[invitations.JOIN_KEY] = cookie
        challenge = AuthChallenge.query.filter_by(
            purpose=invitations.JOIN_PURPOSE
        ).one()
        challenge.expires_at = 0
        db.session.commit()
        assert invitations.pending() is None
        with pytest.raises(BuilderError):
            invitations.complete(user)


def test_pending_context_revocation_and_registration_binding(
    app: Flask, invited: dict[str, int]
) -> None:
    with app.test_request_context():
        p = db.session.get(Passport, invited["passport"])
        user = db.session.get(User, invited["other"])
        invitations.prepare(raw_token(p), consent_values())
        challenge, _ = invitations.pending()
        challenge.payload = {**challenge.payload, "expected_user_id": invited["user"]}
        db.session.commit()
        with pytest.raises(BuilderError):
            invitations.complete(user)
        challenge.payload = {**challenge.payload, "expected_user_id": user.id}
        db.session.commit()
        row = db.session.get(PassportInvitation, p.id)
        row.enabled = False
        db.session.commit()
        assert invitations.pending() is None
        with pytest.raises(BuilderError):
            invitations.complete(user)
        assert PassportParticipant.query.filter_by(user_id=user.id).count() == 0


def test_group_lock_errors_and_changed_parent(
    app: Flask, invited: dict[str, int]
) -> None:
    from sqlalchemy.exc import OperationalError

    class Busy(Exception):
        sqlstate = "55P03"

    busy = OperationalError("lock", {}, Busy())
    unexpected = OperationalError("lock", {}, RuntimeError("unexpected"))
    with app.app_context():
        p = db.session.get(Passport, invited["passport"])
        with patch.object(db.engine.dialect, "name", "postgresql"):
            with patch.object(db.session, "execute") as execute:
                groups.lock_series(invited["series"])
                assert execute.call_count == 2
            with (
                patch.object(db.session, "execute", side_effect=busy),
                pytest.raises(BuilderError, match="being updated"),
            ):
                groups.lock_series(invited["series"])
        with pytest.raises(OperationalError):
            groups.lock_failure(unexpected)
        with (
            patch("sqlalchemy.orm.Query.one", side_effect=busy),
            pytest.raises(BuilderError),
        ):
            groups.lock_passport(p)
        with (
            patch("passports.invitations.owned_passport", side_effect=busy),
            pytest.raises(BuilderError),
        ):
            invitations.owner_passport(p.tenant_id, p.owner_user_id, p.id)
        with (
            patch("passports.groups.owned_passport", side_effect=busy),
            pytest.raises(BuilderError),
        ):
            groups.enable(p, p.owner_user_id, consent_values())

        def moved(*args: object, **kwargs: object) -> Passport:
            p.series_id = None
            return p

        with (
            patch("sqlalchemy.orm.Query.one", side_effect=moved),
            pytest.raises(BuilderError, match="changed"),
        ):
            groups.lock_passport(p)
        db.session.rollback()
        with (
            patch("passports.invitations.owned_passport", side_effect=moved),
            pytest.raises(BuilderError, match="changed"),
        ):
            invitations.owner_passport(p.tenant_id, p.owner_user_id, p.id)
        db.session.rollback()
        with (
            patch(
                "passports.groups.owned_passport",
                side_effect=lambda *args: setattr(p, "series_id", None),
            ),
            pytest.raises(BuilderError, match="changed"),
        ):
            groups.enable(p, p.owner_user_id, consent_values())
        db.session.rollback()


def test_context_expiry_during_completion_and_deleted_group(
    app: Flask, invited: dict[str, int]
) -> None:
    with app.test_request_context():
        p = db.session.get(Passport, invited["passport"])
        user = db.session.get(User, invited["other"])
        invitations.prepare(raw_token(p), consent_values())
        challenge, _ = invitations.pending()
        with (
            patch(
                "passports.groups.lock_series",
                side_effect=lambda _: setattr(challenge, "expires_at", 0),
            ),
            pytest.raises(BuilderError),
        ):
            invitations.complete(user)
        db.session.rollback()
        invitations.complete(user)
        db.session.commit()
        challenge.payload = {**challenge.payload, "completed_by": user.id}
        db.session.commit()
        # A retained result never recreates a deleted passport/group.
        from services import auth_challenges

        cookie = "saved-result"
        challenge.token_hash = tokens.digest(cookie)
        db.session.commit()
        session[invitations.JOIN_KEY] = cookie
        assert (
            auth_challenges.get(invitations.JOIN_KEY, invitations.JOIN_PURPOSE)
            is not None
        )
        db.session.delete(db.session.get(PassportSeries, p.series_id))
        db.session.commit()
        with pytest.raises(BuilderError, match="Group not found"):
            invitations.complete(user)
        db.session.delete(p)
        db.session.commit()
        assert invitations.pending() is None
