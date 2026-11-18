"""Real PostgreSQL email verification and concurrent account/cooldown writes."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from flask import Flask, session
from models import LoginThrottle, Passport, TenantUser, User, db
from passports import groups, invitations, registration
from passports.management import BuilderError

from tests.postgres_schema import postgres_app
from tests.test_passport_editions import extra
from tests.test_passport_groups import consent_values
from tests.test_passport_invitations import invited, raw_token
from tests.test_passport_management import owner
from tests.test_passport_registration import (
    deliveries,
    sent_code,
    test_signup_required_mfa_delays_participation,
    test_verified_signup_is_scoped_and_no_identity_exists_before_proof,
    values,
)

__all__ = [
    "deliveries",
    "invited",
    "owner",
    "test_signup_required_mfa_delays_participation",
    "test_verified_signup_is_scoped_and_no_identity_exists_before_proof",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)


def test_concurrent_mail_claim_sends_once(
    app: Flask, invited: dict[str, int], deliveries: list[tuple[str, str]]
) -> None:
    with app.app_context():
        token = raw_token(db.session.get(Passport, invited["passport"]))
    barrier = Barrier(2)

    def begin() -> int:
        with app.test_request_context():
            barrier.wait(timeout=10)
            try:
                registration.begin(token, values())
                return 200
            except BuilderError as exc:
                db.session.rollback()
                return exc.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: begin(), range(2)))
    assert sorted(results) == [200, 429]
    assert len(deliveries) == 1


def test_same_email_in_different_groups_creates_one_account(
    app: Flask, invited: dict[str, int], deliveries: list[tuple[str, str]]
) -> None:
    contexts: list[tuple[str, str, str]] = []
    with app.test_request_context():
        first = db.session.get(Passport, invited["passport"])
        second = extra(invited, "Another group")
        groups.enable(second, invited["user"], consent_values())
        invitations.replace(second)
        db.session.commit()
        for p in [first, second]:
            LoginThrottle.query.filter(
                LoginThrottle.key.like("passport-mail:%")
            ).delete()
            db.session.commit()
            token = raw_token(p)
            registration.begin(token, values())
            contexts.append((token, sent_code(deliveries), session[registration.KEY]))
            session.pop(
                registration.KEY
            )  # A different browser owns the next challenge.
    barrier = Barrier(2)

    def verify(context: tuple[str, str, str]) -> tuple[int, bool]:
        token, code, cookie = context
        with app.test_request_context():
            session[registration.KEY] = cookie
            barrier.wait(timeout=10)
            user, created, _proof = registration.verify(token, code)
            return user.id, created

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(verify, contexts))
    assert results[0][0] == results[1][0]
    assert sorted(result[1] for result in results) == [False, True]
    with app.app_context():
        user = User.query.filter_by(email="new-friend@example.test").one()
        assert TenantUser.query.filter_by(user_id=user.id).count() == 1
        assert User.query.count() == 3
