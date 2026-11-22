"""Email proof precedes limited account creation; existing credentials stay intact."""

import re
from typing import Any
from unittest.mock import patch

import pyotp
import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import (
    AuthChallenge,
    LoginThrottle,
    Passport,
    PassportGroupBlock,
    PassportInvitation,
    PassportParticipant,
    PassportSeriesMember,
    Tenant,
    TenantUser,
    User,
    db,
)
from passports import invitations, registration, tokens
from services import email_service

from tests.auth_helpers import challenge_payload
from tests.test_passport_groups import consent_values
from tests.test_passport_invitation_routes import (
    PASSWORD,
    assert_private,
    credentials,
    friend,
    join_url,
)
from tests.test_passport_invitations import invited, raw_token
from tests.test_passport_management import owner

__all__ = ["friend", "invited", "owner"]
NEW_PASSWORD = "A verified friend's password 123!"


def values(email: str = "new-friend@example.test") -> dict[str, str]:
    return consent_values("New friend") | {
        "email": email,
        "password": NEW_PASSWORD,
        "confirm_password": NEW_PASSWORD,
    }


@pytest.fixture
def deliveries(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    sent: list[tuple[str, str]] = []

    def send(
        to: str,
        subject: str,
        text_body: str,
        html_body: str | None = None,
        locale: str = "en",
    ) -> None:
        sent.append((to, text_body))

    monkeypatch.setattr(email_service, "send_email", send)
    return sent


def sent_code(deliveries: list[tuple[str, str]]) -> str:
    match = re.search(r"\b[0-9]{6}\b", deliveries[-1][1])
    assert match
    return match.group()


def start(
    app: Flask,
    client: FlaskClient,
    ids: dict[str, int],
    deliveries: list[tuple[str, str]],
    email: str = "new-friend@example.test",
) -> tuple[str, str]:
    result = client.post(join_url(app, ids) + "/register", data=values(email))
    assert result.status_code == 302
    return result.location, sent_code(deliveries)


def test_verified_signup_is_scoped_and_no_identity_exists_before_proof(
    app: Flask,
    client: FlaskClient,
    invited: dict[str, int],
    deliveries: list[tuple[str, str]],
) -> None:
    url = join_url(app, invited)
    assert client.get(url + "/register").status_code == 200
    verify_url, code = start(app, client, invited, deliveries)
    assert_private(client.get(verify_url))
    with client.session_transaction() as sess:
        assert registration.KEY in sess and "user_id" not in sess
        assert not any(
            secret in sess.values()
            for secret in [code, NEW_PASSWORD, "new-friend@example.test"]
        )
    with app.app_context():
        assert User.query.count() == 2
        row = AuthChallenge.query.filter_by(purpose=registration.PURPOSE).one()
        assert (
            code not in row.payload.values()
            and NEW_PASSWORD not in row.payload.values()
        )
    result = client.post(verify_url, data={"code": code, "passport_id": "99999"})
    assert f"/passports/{invited['passport']}/" in result.location
    with app.app_context():
        user = User.query.filter_by(email="new-friend@example.test").one()
        assert (
            not user.is_pilot and not user.is_maintenance and not user.is_instance_admin
        )
        membership = db.session.get(TenantUser, (user.id, invited["tenant"]))
        assert membership.role is None and membership.access_scope == "personal"
        assert (
            PassportParticipant.query.filter_by(user_id=user.id).one().passport_id
            == invited["passport"]
        )
        assert AuthChallenge.query.filter_by(purpose=registration.PURPOSE).count() == 0
    assert client.get("/profile").status_code == 200
    assert client.get("/aircraft/").status_code == 403
    assert client.get(url + "/register").status_code == 302
    assert client.get(verify_url).status_code == 302


def test_signup_required_mfa_delays_participation(
    app: Flask,
    client: FlaskClient,
    invited: dict[str, int],
    deliveries: list[tuple[str, str]],
) -> None:
    with app.app_context():
        db.session.get(Tenant, invited["tenant"]).require_totp = True
        db.session.commit()
    verify_url, code = start(app, client, invited, deliveries)
    result = client.post(verify_url, data={"code": code})
    assert result.location.endswith("step=totp-enrol")
    with app.app_context():
        user = User.query.filter_by(email="new-friend@example.test").one()
        assert PassportParticipant.query.filter_by(user_id=user.id).count() == 0
    payload = challenge_payload(client, "login_challenge")
    result = client.post(
        "/login",
        data={"step": "totp-enrol", "totp_code": pyotp.TOTP(payload["secret"]).now()},
    )
    assert f"/passports/{invited['passport']}/" in result.location
    assert client.get(result.location).status_code == 200


@pytest.mark.parametrize("with_totp", [False, True])
def test_existing_address_never_changes_password_or_bypasses_signin(
    app: Flask,
    client: FlaskClient,
    friend: dict[str, int],
    deliveries: list[tuple[str, str]],
    with_totp: bool,
) -> None:
    secret = pyotp.random_base32()
    with app.app_context():
        if with_totp:
            db.session.get(User, friend["other"]).totp_secret = secret
            db.session.commit()
        old_hash = db.session.get(User, friend["other"]).password_hash
    verify_url, code = start(app, client, friend, deliveries, "other@test.example")
    result = client.post(verify_url, data={"code": code})
    assert result.location.endswith("/login")
    with client.session_transaction() as sess:
        assert "user_id" not in sess
    with app.app_context():
        assert User.query.count() == 2
        assert db.session.get(User, friend["other"]).password_hash == old_hash
        assert db.session.get(TenantUser, (friend["other"], friend["tenant"])) is None
        assert PassportParticipant.query.filter_by(user_id=friend["other"]).count() == 0
    assert (
        client.post(
            "/login", data={"email": "other@test.example", "password": NEW_PASSWORD}
        ).status_code
        == 200
    )
    result = credentials(client)
    if with_totp:
        assert result.location.endswith("step=totp")
        result = client.post(
            "/login", data={"step": "totp", "totp_code": pyotp.TOTP(secret).now()}
        )
    assert f"/passports/{friend['passport']}/" in result.location


@pytest.mark.parametrize(
    "field,value",
    [
        ("email", ""),
        ("email", "not-email"),
        ("email", "friend@localhost"),
        ("email", "a\nb@example.test"),
        ("email", "a" * 256 + "@example.test"),
        ("password", "short"),
        ("password", "x" * 1025),
        ("confirm_password", "different"),
        ("share_visits", ""),
    ],
)
def test_invalid_signup_sends_nothing(
    app: Flask,
    client: FlaskClient,
    invited: dict[str, int],
    deliveries: list[tuple[str, str]],
    field: str,
    value: str,
) -> None:
    result = client.post(
        join_url(app, invited) + "/register", data=values() | {field: value}
    )
    assert result.status_code == 400 and not deliveries
    with app.app_context():
        assert User.query.count() == 2


def test_wrong_codes_are_bounded_and_durable(
    app: Flask,
    client: FlaskClient,
    invited: dict[str, int],
    deliveries: list[tuple[str, str]],
) -> None:
    verify_url, code = start(app, client, invited, deliveries)
    for _ in range(5):
        assert client.post(verify_url, data={"code": "wrong"}).status_code == 400
    assert client.post(verify_url, data={"code": code}).status_code == 429
    with app.app_context():
        assert (
            AuthChallenge.query.filter_by(purpose=registration.PURPOSE)
            .one()
            .payload["attempts"]
            == 5
        )
        assert User.query.count() == 2


def test_resend_cooldown_rotation_and_saved_cookie(
    app: Flask,
    client: FlaskClient,
    invited: dict[str, int],
    deliveries: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codes = iter([123456, 654321])
    monkeypatch.setattr(registration.secrets, "randbelow", lambda _: next(codes))
    verify_url, old_code = start(app, client, invited, deliveries)
    cookie = client.get_cookie("session").value
    assert (
        client.post(join_url(app, invited) + "/register", data=values()).status_code
        == 429
    )
    with app.app_context():
        LoginThrottle.query.filter(LoginThrottle.key.like("passport-mail:%")).update(
            {"window_expires_at": 0}
        )
        db.session.commit()
    _, code = start(app, client, invited, deliveries)
    replay = app.test_client()
    replay.set_cookie("session", cookie)
    assert replay.post(verify_url, data={"code": old_code}).status_code == 409
    assert client.post(verify_url, data={"code": old_code}).status_code == 400
    assert client.post(verify_url, data={"code": code}).status_code == 302


@pytest.mark.parametrize("change", ["expired", "rotated", "disabled", "blocked"])
def test_recheck_admission_before_creating_account(
    app: Flask,
    client: FlaskClient,
    invited: dict[str, int],
    deliveries: list[tuple[str, str]],
    change: str,
) -> None:
    verify_url, code = start(app, client, invited, deliveries)
    with app.app_context():
        p = db.session.get(Passport, invited["passport"])
        if change == "expired":
            AuthChallenge.query.filter_by(
                purpose=registration.PURPOSE
            ).one().expires_at = 0
        elif change == "rotated":
            invitations.replace(p)
        elif change == "disabled":
            db.session.get(PassportInvitation, p.id).enabled = False
        else:
            db.session.add(
                PassportGroupBlock(
                    series_id=p.series_id,
                    email_hash=tokens.email_digest("new-friend@example.test"),
                    display_name="Removed",
                )
            )
        db.session.commit()
    assert client.post(verify_url, data={"code": code}).status_code in {403, 404, 409}
    with app.app_context():
        assert User.query.count() == 2


@pytest.mark.parametrize(
    "failure", [email_service.EmailNotConfiguredError, email_service.EmailSendError]
)
def test_mail_failure_discards_pending_secret(
    app: Flask, client: FlaskClient, invited: dict[str, int], failure: type[Exception]
) -> None:
    with patch("services.email_service.send_email", side_effect=failure("SMTP")):
        result = client.post(join_url(app, invited) + "/register", data=values())
    assert result.status_code == 503
    with app.app_context():
        assert AuthChallenge.query.filter_by(purpose=registration.PURPOSE).count() == 0
        assert User.query.count() == 2


def test_demo_signup_does_not_pretend_to_send_mail(
    app: Flask,
    client: FlaskClient,
    invited: dict[str, int],
    deliveries: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENHANGAR_ENV", "demo")
    assert (
        client.post(join_url(app, invited) + "/register", data=values()).status_code
        == 503
    )
    assert not deliveries


def test_no_context_wrong_passport_and_existing_removed_account(
    app: Flask,
    client: FlaskClient,
    invited: dict[str, int],
    deliveries: list[tuple[str, str]],
) -> None:
    url = join_url(app, invited)
    assert client.get(url + "/verify").status_code == 409
    assert client.get("/passports/join/bad/register").status_code == 404
    assert client.get("/passports/join/bad/verify").status_code == 404
    verify_url, code = start(app, client, invited, deliveries, "other@test.example")
    with app.app_context():
        p = db.session.get(Passport, invited["passport"])
        db.session.add(
            PassportSeriesMember(
                series_id=p.series_id,
                tenant_id=p.tenant_id,
                user_id=invited["other"],
                display_name="Removed",
                blocked=True,
            )
        )
        db.session.commit()
    assert client.post(verify_url, data={"code": code}).status_code == 403
    with app.app_context():
        assert db.session.get(User, invited["other"]).password_hash
        assert (
            db.session.get(
                TenantUser, (invited["other"], invited["tenant"])
            ).access_scope
            == "full"
        )


def test_challenge_and_account_races_fail_without_creating_identity(
    app: Flask, invited: dict[str, int], deliveries: list[tuple[str, str]]
) -> None:
    from passports import groups
    from passports.management import BuilderError
    from services import auth_challenges
    from sqlalchemy.exc import NoResultFound, OperationalError
    from sqlalchemy.orm import Query

    class Busy(Exception):
        sqlstate = "55P03"

    busy = OperationalError("lock", {}, Busy())
    with app.test_request_context():
        token = raw_token(db.session.get(Passport, invited["passport"]))
        registration.begin(token, values())
        code = sent_code(deliveries)
        with (
            patch("sqlalchemy.orm.Query.one", side_effect=NoResultFound()),
            pytest.raises(BuilderError),
        ):
            groups.lock_passport(db.session.get(Passport, invited["passport"]))
        original_one = Query.one
        for error in [NoResultFound(), busy]:

            def fail_challenge(query: Any, failure: Exception = error) -> Any:
                if query.column_descriptions[0]["entity"] is AuthChallenge:
                    raise failure
                return original_one(query)

            with (
                patch("sqlalchemy.orm.Query.one", new=fail_challenge),
                pytest.raises(BuilderError),
            ):
                registration.verify(token, code)
            db.session.rollback()
        challenge = auth_challenges.get(registration.KEY, registration.PURPOSE)

        def expired(query: Any) -> Any:
            if query.column_descriptions[0]["entity"] is AuthChallenge:
                challenge.expires_at = 0
                return challenge
            return original_one(query)

        with (
            patch("sqlalchemy.orm.Query.one", new=expired),
            pytest.raises(BuilderError),
        ):
            registration.verify(token, code)
        db.session.rollback()
        with (
            patch("services.auth_challenges.consume", return_value=False),
            pytest.raises(BuilderError),
        ):
            registration.verify(token, code)
        db.session.rollback()
        with (
            patch("sqlalchemy.orm.Query.one_or_none", return_value=None),
            pytest.raises(BuilderError, match="account changed"),
        ):
            registration.verify(token, code)
        db.session.rollback()
        execute = db.session.execute

        def blocked_insert(statement: Any, *args: Any, **kwargs: Any) -> Any:
            if getattr(statement, "is_insert", False):
                raise busy
            return execute(statement, *args, **kwargs)

        with (
            patch.object(db.session, "execute", side_effect=blocked_insert),
            pytest.raises(BuilderError),
        ):
            registration.verify(token, code)
        db.session.rollback()
        assert User.query.count() == 2


def test_wrong_edition_context_and_canonical_address(
    app: Flask, invited: dict[str, int], deliveries: list[tuple[str, str]]
) -> None:
    from passports import groups
    from passports.management import BuilderError

    from tests.test_passport_editions import extra

    with app.test_request_context():
        original = db.session.get(Passport, invited["passport"])
        token = raw_token(original)
        registration.begin(token, values())
        other = extra(invited, "Another group")
        groups.enable(other, invited["user"], consent_values())
        invitations.replace(other)
        db.session.commit()
        with pytest.raises(BuilderError):
            registration.pending(raw_token(other))
        assert registration._email('"Friend"@EXAMPLE.TEST') == "friend@example.test"
        assert User.query.count() == 2


def test_policy_tightened_during_handoff_still_requires_mfa(
    app: Flask,
    client: FlaskClient,
    invited: dict[str, int],
    deliveries: list[tuple[str, str]],
) -> None:
    from passports.management import BuilderError

    verify_url, code = start(app, client, invited, deliveries)

    def tighten(user: User) -> None:
        db.session.get(Tenant, invited["tenant"]).require_totp = True
        db.session.commit()
        raise BuilderError("policy changed", 409)

    with patch("passports.invitations.complete", side_effect=tighten):
        result = client.post(verify_url, data={"code": code})
    assert result.location.endswith("step=totp-enrol")
    with app.app_context():
        user = User.query.filter_by(email="new-friend@example.test").one()
        assert PassportParticipant.query.filter_by(user_id=user.id).count() == 0


def test_verified_existing_account_keeps_passport_context_through_recovery(
    app: Flask,
    client: FlaskClient,
    friend: dict[str, int],
    deliveries: list[tuple[str, str]],
) -> None:
    from datetime import UTC, datetime, timedelta

    from models import PasswordResetToken

    verify_url, code = start(app, client, friend, deliveries, "other@test.example")
    assert client.post(verify_url, data={"code": code}).location.endswith("/login")
    with app.app_context():
        reset = PasswordResetToken(
            user_id=friend["other"],
            generated_by_user_id=friend["user"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.session.add(reset)
        db.session.commit()
        reset_url = "/reset-password/" + reset.token
    assert client.post(
        reset_url, data={"new_password": PASSWORD, "confirm_password": PASSWORD}
    ).location.endswith("/login")
    result = credentials(client)
    assert f"/passports/{friend['passport']}/" in result.location


def test_new_factor_after_email_proof_requires_real_signin(
    app: Flask,
    client: FlaskClient,
    invited: dict[str, int],
    deliveries: list[tuple[str, str]],
) -> None:
    verify_url, code = start(app, client, invited, deliveries)
    verify = registration.verify
    secret = pyotp.random_base32()

    def add_factor(token: str, value: str) -> tuple[User, bool, str | None]:
        user, created, proof = verify(token, value)
        user.totp_secret = secret
        db.session.commit()
        return user, created, proof

    with patch("passports.registration.verify", side_effect=add_factor):
        result = client.post(verify_url, data={"code": code})
    assert result.location.endswith("/login")
    with client.session_transaction() as sess:
        assert "user_id" not in sess and invitations.JOIN_KEY in sess
    with app.app_context():
        user = User.query.filter_by(email="new-friend@example.test").one()
        assert PassportParticipant.query.filter_by(user_id=user.id).count() == 0
    result = client.post(
        "/login", data={"email": "new-friend@example.test", "password": NEW_PASSWORD}
    )
    assert result.location.endswith("step=totp")
    result = client.post(
        "/login", data={"step": "totp", "totp_code": pyotp.TOTP(secret).now()}
    )
    assert f"/passports/{invited['passport']}/" in result.location


def test_credential_refresh_during_join_does_not_authenticate_changed_identity(
    app: Flask, friend: dict[str, int]
) -> None:
    from auth.routes import _finish_login
    from flask import session
    from passports.management import BuilderError

    with app.test_request_context():
        user = db.session.get(User, friend["other"])
        p = db.session.get(Passport, friend["passport"])
        invitations.prepare(raw_token(p), consent_values())
        complete = invitations.complete

        def changed(account: User) -> Passport:
            passport = complete(account)
            account.password_hash = "changed while joining"
            db.session.flush()
            return passport

        with patch("passports.invitations.complete", side_effect=changed):
            assert _finish_login(user).location.endswith("/login")
        assert "user_id" not in session
        assert PassportParticipant.query.filter_by(user_id=user.id).count() == 0

        def rejected(account: User) -> None:
            account.auth_version = "different-after-proof"
            db.session.commit()
            raise BuilderError("changed", 409)

        with patch("passports.invitations.complete", side_effect=rejected):
            assert _finish_login(user).location.endswith("/login")
        assert "user_id" not in session
